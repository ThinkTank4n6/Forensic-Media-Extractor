"""
db_extract.py -- pull images and videos out of SQLite BLOB columns.

Carving cannot recover media stored in a database. SQLite splits any payload
larger than about one page across a chain of overflow pages, each carrying a
four-byte pointer to the next, so the bytes of a 500 KB photo are not
contiguous on disk: a carver finds the JPEG header, follows it into a page
pointer, and either truncates at the first page boundary or rejects the file
outright. Thumbnail caches, messaging app databases, Photos.sqlite, browser
caches and EXIF sidecar tables all store media this way.

This module reads the database properly:

  * walks the schema, then every table b-tree (rowid and WITHOUT ROWID)
  * decodes the record format and pulls out BLOB columns
  * reassembles overflow chains, so large blobs come back byte-exact
  * optionally sweeps free space -- the freelist pages, per-page gaps and
    freeblocks -- where the records of deleted rows survive

The blob's own length is authoritative, so unlike carving there is no
guessing at where a file ends.

    python3 db_extract.py -i mmssms.db -o ./out
    python3 db_extract.py -i Photos.sqlite -o ./out --no-freespace

media_core calls this automatically for every SQLite file it finds while
walking a file system; pass --no-databases to turn that off.

Not supported: encrypted databases (SQLCipher, WAL-encrypted stores), ESE
(.edb) and Thumbs.db/thumbcache. WAL and journal side files are not merged --
point this at them separately if they matter.
"""

from __future__ import annotations

# Allow this file to be run directly -- "python fme/engines/media_core.py" --
# as well as imported. Run as a script there is no package context, so the
# project root goes on the path before the package imports below.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import os
import re
import struct
import sys
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

SQLITE_MAGIC = b"SQLite format 3\x00"

# B-tree page types
PAGE_INTERIOR_INDEX = 0x02
PAGE_INTERIOR_TABLE = 0x05
PAGE_LEAF_INDEX = 0x0A
PAGE_LEAF_TABLE = 0x0D

MAX_PAGES_VISITED = 5_000_000     # loop guard on malformed page chains
MIN_BLOB = 64                     # below this nothing worth recovering


@dataclass
class BlobHit:
    table: str
    column: str
    rowid: Optional[int]
    data: bytes
    source: str          # "record" or "freespace"
    page: int


def varint(buf: bytes, pos: int) -> tuple[int, int]:
    """SQLite's big-endian base-128 varint: up to nine bytes, the ninth
    contributing all eight of its bits."""
    value = 0
    for i in range(8):
        if pos + i >= len(buf):
            return 0, pos + i
        byte = buf[pos + i]
        if i == 8:
            break
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos + i + 1
    if pos + 8 < len(buf):
        value = (value << 8) | buf[pos + 8]
    return value, pos + 9


class SqliteFile:
    """Minimal read-only SQLite pager. Reads pages on demand, so a multi-
    gigabyte database costs no more memory than the pages in flight."""

    def __init__(self, read: Callable[[int, int], bytes], size: int):
        self._read = read
        self.size = size
        header = read(0, 100)
        if len(header) < 100 or header[:16] != SQLITE_MAGIC:
            raise ValueError("Not a SQLite database")
        page_size = struct.unpack(">H", header[16:18])[0]
        self.page_size = 65536 if page_size == 1 else page_size
        if self.page_size < 512 or self.page_size & (self.page_size - 1):
            raise ValueError(f"Implausible page size: {self.page_size}")
        self.reserved = header[20]
        self.usable = self.page_size - self.reserved
        if self.usable < 480:
            raise ValueError(f"Implausible usable page size: {self.usable}")
        self.page_count = struct.unpack(">I", header[28:32])[0] or \
            max(1, size // self.page_size)
        self.freelist_trunk = struct.unpack(">I", header[32:36])[0]
        self.freelist_count = struct.unpack(">I", header[36:40])[0]
        self.encoding = struct.unpack(">I", header[56:60])[0] or 1

    def page(self, number: int) -> bytes:
        if number < 1 or number > self.page_count:
            return b""
        return self._read((number - 1) * self.page_size, self.page_size)

    # -- overflow ---------------------------------------------------------
    def _local_size(self, payload: int, leaf_table: bool) -> int:
        """How much of a payload lives on the page itself. Straight out of
        the file format spec; getting this wrong silently corrupts blobs."""
        max_local = self.usable - 35 if leaf_table else \
            ((self.usable - 12) * 64 // 255) - 23
        if payload <= max_local:
            return payload
        min_local = ((self.usable - 12) * 32 // 255) - 23
        surplus = min_local + (payload - min_local) % (self.usable - 4)
        return surplus if surplus <= max_local else min_local

    def read_payload(self, page_data: bytes, offset: int, payload_len: int,
                     leaf_table: bool) -> bytes:
        local = self._local_size(payload_len, leaf_table)
        out = bytearray(page_data[offset:offset + local])
        if local >= payload_len:
            return bytes(out)
        ptr_at = offset + local
        if ptr_at + 4 > len(page_data):
            return bytes(out)
        next_page = struct.unpack(">I", page_data[ptr_at:ptr_at + 4])[0]
        seen = set()
        while next_page and len(out) < payload_len and len(seen) < MAX_PAGES_VISITED:
            if next_page in seen:
                break                      # cyclic chain in a damaged file
            seen.add(next_page)
            data = self.page(next_page)
            if len(data) < 4:
                break
            following = struct.unpack(">I", data[0:4])[0]
            out += data[4:self.usable][:payload_len - len(out)]
            next_page = following
        return bytes(out)

    # -- b-tree walk ------------------------------------------------------
    def walk_table(self, root: int) -> Iterator[tuple[int, Optional[int], bytes]]:
        """Yield (page number, rowid, record payload) for every row."""
        stack, seen = [root], set()
        while stack:
            number = stack.pop()
            if number in seen or len(seen) > MAX_PAGES_VISITED:
                continue
            seen.add(number)
            data = self.page(number)
            if not data:
                continue
            base = 100 if number == 1 else 0
            if base + 12 > len(data):
                continue
            page_type = data[base]
            if page_type not in (PAGE_INTERIOR_INDEX, PAGE_INTERIOR_TABLE,
                                 PAGE_LEAF_INDEX, PAGE_LEAF_TABLE):
                continue
            cell_count = struct.unpack(">H", data[base + 3:base + 5])[0]
            interior = page_type in (PAGE_INTERIOR_TABLE, PAGE_INTERIOR_INDEX)
            header_len = 12 if interior else 8
            if interior:
                right = struct.unpack(">I", data[base + 8:base + 12])[0]
                if right:
                    stack.append(right)

            pointers = base + header_len
            for i in range(cell_count):
                at = pointers + i * 2
                if at + 2 > len(data):
                    break
                cell = struct.unpack(">H", data[at:at + 2])[0]
                if cell >= len(data):
                    continue
                try:
                    if page_type == PAGE_INTERIOR_TABLE:
                        child = struct.unpack(">I", data[cell:cell + 4])[0]
                        if child:
                            stack.append(child)
                    elif page_type == PAGE_INTERIOR_INDEX:
                        child = struct.unpack(">I", data[cell:cell + 4])[0]
                        if child:
                            stack.append(child)
                        payload_len, pos = varint(data, cell + 4)
                        yield number, None, self.read_payload(
                            data, pos, payload_len, False)
                    elif page_type == PAGE_LEAF_TABLE:
                        payload_len, pos = varint(data, cell)
                        rowid, pos = varint(data, pos)
                        yield number, rowid, self.read_payload(
                            data, pos, payload_len, True)
                    else:  # leaf index: WITHOUT ROWID tables live here
                        payload_len, pos = varint(data, cell)
                        yield number, None, self.read_payload(
                            data, pos, payload_len, False)
                except (struct.error, IndexError, ValueError):
                    continue

    def freelist_pages(self) -> set:
        """Every page currently on the freelist. Used to sanity-check
        candidate overflow pointers found in deleted cells."""
        pages, trunk = set(), self.freelist_trunk
        while trunk and trunk not in pages and len(pages) < MAX_PAGES_VISITED:
            pages.add(trunk)
            data = self.page(trunk)
            if len(data) < 8:
                break
            nxt, count = struct.unpack(">II", data[0:8])
            for i in range(min(count, (self.usable - 8) // 4)):
                at = 8 + i * 4
                leaf = struct.unpack(">I", data[at:at + 4])[0]
                if leaf:
                    pages.add(leaf)
            trunk = nxt
        return pages

    # -- free space -------------------------------------------------------
    def free_regions(self) -> Iterator[tuple[int, bytes]]:
        """Yield (page number, bytes) for space that no longer belongs to a
        live row: whole freelist pages, plus the gap and freeblocks on each
        b-tree page. Deleted rows survive here until the space is reused."""
        # Freelist pages, via the trunk chain.
        trunk, seen = self.freelist_trunk, set()
        while trunk and trunk not in seen and len(seen) < MAX_PAGES_VISITED:
            seen.add(trunk)
            data = self.page(trunk)
            if len(data) < 8:
                break
            nxt, count = struct.unpack(">II", data[0:8])
            yield trunk, data[8:]
            for i in range(min(count, (self.usable - 8) // 4)):
                at = 8 + i * 4
                leaf = struct.unpack(">I", data[at:at + 4])[0]
                if leaf:
                    leaf_data = self.page(leaf)
                    if leaf_data:
                        yield leaf, leaf_data
            trunk = nxt

        # The unallocated gap on each b-tree page, and its freeblock chain.
        for number in range(1, self.page_count + 1):
            data = self.page(number)
            if not data:
                continue
            base = 100 if number == 1 else 0
            if base + 8 > len(data) or data[base] not in (
                    PAGE_INTERIOR_INDEX, PAGE_INTERIOR_TABLE,
                    PAGE_LEAF_INDEX, PAGE_LEAF_TABLE):
                continue
            interior = data[base] in (PAGE_INTERIOR_TABLE, PAGE_INTERIOR_INDEX)
            cell_count = struct.unpack(">H", data[base + 3:base + 5])[0]
            content_start = struct.unpack(">H", data[base + 5:base + 7])[0] or 65536
            gap_start = base + (12 if interior else 8) + cell_count * 2
            if content_start > gap_start:
                yield number, data[gap_start:content_start]

            freeblock = struct.unpack(">H", data[base + 1:base + 3])[0]
            guard = 0
            while freeblock and guard < 4096:
                guard += 1
                if freeblock + 4 > len(data):
                    break
                nxt, size = struct.unpack(">HH", data[freeblock:freeblock + 4])
                if size > 4:
                    yield number, data[freeblock:freeblock + size]
                if nxt <= freeblock:
                    break
                freeblock = nxt


# --------------------------------------------------------------------------
# Records and schema
# --------------------------------------------------------------------------
class ShortBlob(bytes):
    """A blob of which the record held less than its stated length."""
    expected = 0


def decode_record(payload: bytes) -> list:
    """Decode SQLite's record format into a list of Python values, keeping
    blobs as bytes and everything else as a marker we do not care about."""
    header_len, pos = varint(payload, 0)
    if header_len <= 0 or header_len > len(payload):
        return []
    serials, at = [], pos
    while at < header_len:
        serial, at = varint(payload, at)
        serials.append(serial)
    values, body = [], header_len
    for serial in serials:
        if serial == 0:
            values.append(None)
        elif serial in (1, 2, 3, 4, 5, 6):
            size = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8}[serial]
            values.append(int.from_bytes(payload[body:body + size], "big", signed=True))
            body += size
        elif serial == 7:
            values.append(None)
            body += 8
        elif serial in (8, 9):
            values.append(serial - 8)
        elif serial >= 12:
            size = (serial - 12) // 2
            chunk = payload[body:body + size]
            if serial % 2 == 0 and len(chunk) < size:
                # The record's own header gives the blob's length, and the
                # payload -- its overflow chain broken by a damaged or cut-off
                # file -- holds less. It was handed on as a whole blob from a
                # live row; it is marked as the part it is.
                chunk = ShortBlob(chunk)
                chunk.expected = size
            values.append(chunk if serial % 2 == 0 else None)  # blob vs text
            body += size
        else:
            values.append(None)
    return values


def parse_columns(sql: str) -> list:
    """Best-effort column names from CREATE TABLE, for labeling only."""
    if not sql:
        return []
    match = re.search(r"\((.*)\)\s*(?:WITHOUT\s+ROWID)?\s*$", sql, re.S | re.I)
    if not match:
        return []
    body, depth, current, parts = match.group(1), 0, [], []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    names = []
    for part in parts:
        token = part.strip().split()
        if not token:
            continue
        name = token[0].strip('"`[]')
        # Table constraints start with these; a *column* may legitimately be
        # named "key", so that one must not be in the list.
        if name.upper() in ("PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"):
            continue
        names.append(name)
    return names


def schema(db: SqliteFile) -> list:
    """Tables from sqlite_master: (name, rootpage, [column names])."""
    tables = []
    for _, _, payload in db.walk_table(1):
        values = decode_record(payload)
        if len(values) < 5:
            continue
        # sqlite_master is (type, name, tbl_name, rootpage, sql); type and the
        # names are text, which decode_record deliberately does not decode, so
        # re-read them here.
        header_len, pos = varint(payload, 0)
        serials, at = [], pos
        while at < header_len:
            serial, at = varint(payload, at)
            serials.append(serial)
        body, texts = header_len, {}
        for idx, serial in enumerate(serials):
            if serial >= 12:
                size = (serial - 12) // 2
                if serial % 2:
                    texts[idx] = payload[body:body + size].decode("utf-8", "replace")
                body += size
            elif serial in (1, 2, 3, 4, 5, 6):
                body += {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8}[serial]
            elif serial == 7:
                body += 8
        if texts.get(0) != "table":
            continue
        name = texts.get(1, "")
        root = values[3] if isinstance(values[3], int) else 0
        if name and root:
            tables.append((name, root, parse_columns(texts.get(4, ""))))
    return tables


# --------------------------------------------------------------------------
# Media detection
# --------------------------------------------------------------------------
def looks_like_media(blob: bytes, signatures=None) -> Optional[tuple]:
    """Return (category, ext) when a blob starts with a known media header.

    The blob's stored length ends the file, so nothing has to be parsed to
    find the end -- the usual carving problem does not arise here.
    """
    if len(blob) < MIN_BLOB:
        return None
    try:
        from fme.engines import media_core
    except ImportError:
        return None
    head = blob[:media_core.MAX_SIG_SPAN]
    for pos, sig in media_core.scan_buffer(head, signatures=signatures):
        if pos == sig.offset:
            return sig.category, sig.ext
        if pos > 64:
            break
    return None


def carve_buffer(buf: bytes, signatures=None) -> Iterator[tuple]:
    """Signature-scan a lump of free space. Yields (category, ext, bytes)."""
    try:
        from fme.engines import media_core
    except ImportError:
        return
    read = lambda off, length: buf[off:off + length] if 0 <= off < len(buf) else b""
    next_allowed = 0
    for pos, sig in media_core.scan_buffer(buf, signatures=signatures):
        start = pos - sig.offset
        if start < next_allowed or start < 0:
            continue
        cap = min(len(buf) - start,
                  media_core.MAX_IMAGE_DEFAULT if sig.category == "image"
                  else media_core.MAX_VIDEO_DEFAULT)
        parser = media_core.PARSERS.get(sig.parser, media_core._p_generic)
        try:
            result = parser(read, start, cap)
        except Exception:  # noqa: BLE001 - malformed free space is expected
            continue
        if not result:
            continue
        length = min(result[0] or 0, len(buf) - start)
        if length < MIN_BLOB:
            continue
        next_allowed = start + length
        yield sig.category, result[1] or sig.ext, buf[start:start + length]


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def extract(read: Callable[[int, int], bytes], size: int, *,
            freespace: bool = True, signatures=None,
            max_blob: int = 512 * 1024 * 1024) -> Iterator[BlobHit]:
    """Yield every media blob in a SQLite database."""
    db = SqliteFile(read, size)
    seen_offsets = set()

    for name, root, columns in schema(db):
        for page, rowid, payload in db.walk_table(root):
            values = decode_record(payload)
            for index, value in enumerate(values):
                if not isinstance(value, (bytes, bytearray)):
                    continue
                if len(value) > max_blob:
                    continue
                found = looks_like_media(bytes(value), signatures)
                if not found:
                    continue
                column = columns[index] if index < len(columns) else f"col{index}"
                yield BlobHit(name, column, rowid, bytes(value),
                              "record-short" if isinstance(value, ShortBlob)
                              else "record", page)

    if freespace:
        freelist = db.freelist_pages()
        for page, chunk in db.free_regions():
            if not chunk or (page, len(chunk)) in seen_offsets:
                continue
            seen_offsets.add((page, len(chunk)))
            for ext, data, how in recover_free_region(db, bytes(chunk),
                                                      freelist, signatures):
                yield BlobHit("(deleted row)", ext, None, data,
                              "freespace" if how == "local" else "freespace+overflow",
                              page)


def recover_free_region(db: SqliteFile, chunk: bytes, freelist: set,
                        signatures=None) -> Iterator[tuple]:
    """Recover media from one region of free space.

    Two cases, in order. A blob small enough to have lived entirely on the
    page is simply parsed out of the region. A larger one left only its first
    few KB here, with the rest still sitting on freed overflow pages; the
    four-byte pointer to the head of that chain follows the local part, so
    the region is scanned for a value that names a page currently on the
    freelist, the chain is walked and stripped of its pointers, and the
    result is handed to a format parser. Only what the parser accepts is
    returned, which is what keeps a wrong guess from producing garbage.

    Yields (extension, data, how) where how is "local" or "overflow".
    """
    try:
        from fme.engines import media_core
    except ImportError:
        return
    # Unlike carving a whole image, overlapping results are not suppressed
    # here. A deleted cell whose blob overflowed leaves a header on the page
    # whose data continues elsewhere; parsing it can stop at a terminator
    # belonging to a *different* deleted file further down the same region.
    # Skipping everything inside that span would lose the second file, which
    # is often intact. Duplicates are collapsed by hash upstream instead.
    for pos, sig in media_core.scan_buffer(chunk, signatures=signatures):
        start = pos - sig.offset
        if start < 0:
            continue
        parser = media_core.PARSERS.get(sig.parser, media_core._p_generic)
        limit = (media_core.MAX_IMAGE_DEFAULT if sig.category == "image"
                 else media_core.MAX_VIDEO_DEFAULT)

        # Two readings of the same header: everything on this page, or a
        # local part plus a freed overflow chain. Both are parsed, and the
        # longer validated result wins -- a header whose data continued into
        # overflow can still parse "successfully" against a stray terminator
        # further down the page, and that shorter answer is a truncation, not
        # a file.
        local = chunk[start:]
        best = None   # (length, ext, data, how, consumed)

        read_local = lambda off, length: local[off:off + length]
        try:
            result = parser(read_local, 0, len(local))
        except Exception:  # noqa: BLE001
            result = None
        if result and result[0] and MIN_BLOB <= result[0] <= len(local):
            best = (result[0], result[1] or sig.ext,
                    bytes(local[:result[0]]), "local", result[0])

        for cut in _pointer_candidates(local, freelist, db.page_count):
            buf = bytearray(local[:cut])
            next_page = struct.unpack(">I", local[cut:cut + 4])[0]
            seen = set()
            while (next_page and next_page not in seen and len(buf) < limit
                   and len(seen) < MAX_PAGES_VISITED):
                seen.add(next_page)
                data = db.page(next_page)
                if len(data) < 8:
                    break
                following = struct.unpack(">I", data[0:4])[0]
                buf += data[4:db.usable]
                if following > db.page_count:
                    break
                next_page = following
            if len(buf) <= len(local):
                continue
            read_buf = lambda off, length: bytes(buf[off:off + length])
            try:
                result = parser(read_buf, 0, len(buf))
            except Exception:  # noqa: BLE001
                continue
            if result and result[0] and MIN_BLOB <= result[0] <= len(buf):
                if best is None or result[0] > best[0]:
                    best = (result[0], result[1] or sig.ext,
                            bytes(buf[:result[0]]), "overflow", cut + 4)

        if best:
            yield best[1], best[2], best[3]


def _pointer_candidates(local: bytes, freelist: set, page_count: int,
                        limit: int = 12) -> list:
    """Positions in a deleted cell where a four-byte overflow pointer could
    sit: any big-endian value naming a page that is on the freelist."""
    out = []
    for q in range(MIN_BLOB, len(local) - 4):
        value = struct.unpack(">I", local[q:q + 4])[0]
        if value and value <= page_count and value in freelist:
            out.append(q)
            if len(out) >= limit:
                break
    return out


def extract_file(path: str, **kwargs) -> Iterator[BlobHit]:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        def read(offset: int, length: int) -> bytes:
            fh.seek(offset)
            return fh.read(length)
        yield from extract(read, size, **kwargs)


def is_sqlite(head: bytes) -> bool:
    return head[:16] == SQLITE_MAGIC


def main(argv=None) -> int:
    import hashlib
    ap = argparse.ArgumentParser(
        description="Extract images and videos from SQLite BLOB columns.")
    ap.add_argument("-i", "--input", required=True, help="SQLite database file")
    ap.add_argument("-o", "--output", required=True, help="output directory")
    ap.add_argument("--no-freespace", action="store_true",
                    help="skip the free-space sweep for deleted rows")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(args.output, exist_ok=True)
    seen, count = set(), 0
    try:
        for hit in extract_file(args.input, freespace=not args.no_freespace):
            digest = hashlib.sha256(hit.data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            found = looks_like_media(hit.data)
            ext = (found[1] if found else hit.column) or "bin"
            count += 1
            label = f"{hit.table}_{hit.rowid}" if hit.rowid is not None else f"page{hit.page}"
            name = re.sub(r"[^A-Za-z0-9._-]", "_", f"{count:04d}_{label}")[:80]
            with open(os.path.join(args.output, f"{name}.{ext}"), "wb") as fh:
                fh.write(hit.data)
            print(f"  {hit.source:9} {hit.table}.{hit.column} "
                  f"rowid={hit.rowid} {len(hit.data)} bytes -> {name}.{ext}")
    except ValueError as exc:
        print(f"Cannot read database: {exc}", file=sys.stderr)
        return 2
    print(f"\n{count} media blob(s) written to {os.path.abspath(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
