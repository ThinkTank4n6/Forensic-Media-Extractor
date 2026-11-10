"""
containers.py -- places media hides that a byte scan cannot reach.

Two formats that a carver walks straight past, both of which matter on real
exhibits:

**Windows thumbnail caches** (`thumbcache_*.db`). Explorer keeps a copy of
every thumbnail it has drawn, in per-size files under each user's
`AppData\\Local\\Microsoft\\Windows\\Explorer\\`. Those copies survive the
originals: a picture deleted and wiped from the disk is often still in the
cache, which makes these the strongest single source of "what was once on
this machine". The records wrap complete JPEGs, PNGs, GIFs or headerless
BMPs. Layout per libyal's `libwtcdb` documentation.

**Documents**: PDF and the saved-page formats. A PDF carries its pictures as
image XObjects; a JPEG inside one is the file the producer wrote, byte for
byte, so its hashes can match a hash set. Saved web pages (`.mht`, `.mhtml`)
carry their images as MIME parts.

Both read as bytes, with no dependency. A thumbnail extracted here carries no
reliable path of its own -- the cache records an identifier that needs the
Windows Search index to resolve -- so it is the same evidentiary shape as a
carved file: real content, uncertain provenance.
"""

from __future__ import annotations

import base64
import logging
import re
import struct
import zlib
from typing import Iterator, Optional, Tuple

__version__ = "1.0.0"

LOG = logging.getLogger("fme")

# --------------------------------------------------------------------------
# Windows thumbnail cache
# --------------------------------------------------------------------------
THUMBCACHE_MAGIC = b"CMMM"

# The layouts, per libyal's libwtcdb documentation and Thumbcache Viewer.
# Each entry header starts "CMMM", its size and an 8-byte hash; then:
#   Vista (0x14)  an 8-byte extension (UTF-16, 4 chars), then identifier
#                 size, padding size, data size, unknown, two checksums: 56
#   7 (0x15)      identifier size, padding size, data size, unknown, two
#                 checksums: 48
#   8+ (0x1A..)   identifier size, padding size, data size, width, height,
#                 unknown, two checksums: 56
# Vista was read with the Windows 7 layout: the extension's characters were
# taken as the identifier and data sizes, and the walk stopped at the first
# entry with nothing said. The file header moved too: from Windows 8's
# second format (0x1C) a field is inserted before the first entry's offset.
_LAYOUTS = {
    # version: (entry header size, offset of identifier/padding/data sizes)
    "vista": (56, 24),
    "7": (48, 16),
    "8": (56, 16),
}
_VERSIONS = {0x14: "vista", 0x15: "7", 0x1A: "8", 0x1C: "8", 0x1E: "8",
             0x1F: "8", 0x20: "8"}
_FIRST_ENTRY_MOVED = 0x1C
_MAX_IDENTIFIER = 2048
_MAX_ENTRY = 32 * 1024 * 1024

_THUMB_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\x0a", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF8", "gif"),
    (b"BM", "bmp"),
)


def is_thumbcache(head: bytes) -> bool:
    return head[:4] == THUMBCACHE_MAGIC


def thumbcache_entries(data: bytes,
                       report: Optional[dict] = None) -> Iterator[Tuple[str, bytes, dict]]:
    """Walk a thumbcache database, yielding (identifier, bytes, details).

    The walk follows the offsets in the header rather than scanning for the
    `CMMM` signature. A thumbnail's own compressed bytes can contain those
    four characters, and a blind scan reads such a coincidence as the start
    of a record -- producing entries that are fragments of the previous
    image. The cost of following offsets is that a corrupt record ends the
    walk instead of resynchronizing past it, which is the trade real readers
    make too: a lost tail beats a confident misread.

    But a walk that ends early is said, not hidden: `report`, if given, is
    filled with "version", "layout", "entries" (how many were walked) and,
    when the walk stopped before the end of the entries, "stopped" (why) and
    "stopped_at" (the offset); and it is logged. The free space after the
    last entry -- zeros, or past the header's next-free offset -- is the
    end, not a stop.
    """
    if report is None:
        report = {}
    report.update(entries=0)
    if len(data) < 24 or data[:4] != THUMBCACHE_MAGIC:
        return
    version = struct.unpack("<I", data[4:8])[0]
    layout = _VERSIONS.get(version)
    report["version"] = version
    if layout is None:
        # Not a version anyone has documented. Read with the layout of the
        # nearest, and said, since a wrong layout reads nothing or nonsense.
        layout = "8" if version >= 0x1A else "7"
        report["layout_guessed"] = True
        LOG.warning("thumbcache version 0x%X is not one this program knows; "
                    "read with the Windows %s layout", version, layout)
    report["layout"] = layout
    header_size, sizes_at = _LAYOUTS[layout]
    at = 16 if version >= _FIRST_ENTRY_MOVED else 12
    try:
        first_entry, next_free = struct.unpack("<II", data[at:at + 8])
    except struct.error:
        return

    offset = first_entry if 24 <= first_entry < len(data) else 24
    seen = 0

    def stop(why):
        if offset >= len(data) or (next_free and offset >= next_free) \
                or not data[offset:].strip(b"\x00"):
            return                       # the end of the entries, not a stop
        report.update(stopped=why, stopped_at=offset)
        LOG.warning("thumbcache walk stopped at offset %d after %d entries: "
                    "%s", offset, seen, why)

    while seen < 200000:
        if offset + header_size > len(data):
            stop("an entry header runs past the end of the file")
            return
        if data[offset:offset + 4] != THUMBCACHE_MAGIC:
            stop("no entry signature where the last entry said the next is")
            return
        try:
            entry_size = struct.unpack("<I", data[offset + 4:offset + 8])[0]
            cache_id = struct.unpack("<Q", data[offset + 8:offset + 16])[0]
            identifier_size, padding_size, data_size = struct.unpack(
                "<III", data[offset + sizes_at:offset + sizes_at + 12])
        except struct.error:
            stop("an entry header could not be read")
            return
        if not header_size <= entry_size <= _MAX_ENTRY:
            stop(f"an entry gives its size as {entry_size:,} bytes")
            return
        if identifier_size > _MAX_IDENTIFIER or data_size > _MAX_ENTRY:
            stop("an entry's identifier or data size is not credible")
            return

        identifier = ""
        start = offset + header_size
        if identifier_size:
            raw = data[start:start + identifier_size]
            identifier = raw.decode("utf-16-le", "replace").strip("\x00").strip()
        payload_at = start + identifier_size + padding_size
        if payload_at + data_size > len(data):
            # The record claims more than the file holds: the database was
            # truncated mid-entry. Slicing would yield a part of a
            # thumbnail, which is a corrupt image presented as a recovered
            # one -- worse than stopping here.
            stop("the database ends inside an entry (truncated)")
            return
        payload = data[payload_at:payload_at + data_size]

        if data_size and payload:
            kind = "bin"
            for signature, ext in _THUMB_SIGNATURES:
                if payload.startswith(signature):
                    kind = ext
                    break
            yield (identifier or f"{cache_id:016x}", payload,
                   {"cache_id": f"{cache_id:016x}", "ext": kind,
                    "version": version})
        seen += 1
        report["entries"] = seen
        offset += entry_size


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
_PDF_STREAM = re.compile(rb"<<(?P<dict>.{0,3000}?)>>\s*stream\r?\n", re.S)


def is_pdf(head: bytes) -> bool:
    return head[:5] == b"%PDF-"


def _pdf_dict_value(raw: bytes, key: bytes) -> Optional[bytes]:
    match = re.search(key + rb"\s*(/[A-Za-z0-9#]+|\d+|\[[^\]]*\])", raw)
    return match.group(1) if match else None


def _own_dict(header: bytes) -> bytes:
    """The stream's own dictionary. _PDF_STREAM matches from the first "<<"
    it meets, which can be an earlier object's, and that object's keys --
    its /Filter, its /Type -- were then read as this stream's."""
    cut = header.rfind(b"obj")
    return header[cut + 3:] if cut >= 0 else header


def _pdf_filters(header: bytes) -> list:
    value = _pdf_dict_value(header, rb"/Filter") or b""
    return re.findall(rb"/([A-Za-z0-9]+)", value)


def _stream_payload(data: bytes, start: int, header: bytes) -> Optional[bytes]:
    """A stream's bytes: by its /Length where that is a number that lands on
    "endstream", otherwise up to "endstream" less the one end-of-line the
    format puts before it. Every trailing CR and LF used to be stripped,
    which cut real bytes off a payload that ended in them, and a payload
    containing the bytes "endstream" was cut there when /Length said
    otherwise."""
    length = _pdf_dict_value(header, rb"/Length")
    if length and length.isdigit():
        end = start + int(length)
        tail = data[end:end + 12].lstrip(b"\r\n")
        if end <= len(data) and tail.startswith(b"endstream"):
            return data[start:end]
    end = data.find(b"endstream", start)
    if end < 0:
        return None
    payload = data[start:end]
    for eol in (b"\r\n", b"\n", b"\r"):
        if payload.endswith(eol):
            return payload[:-len(eol)]
    return payload


def pdf_images(data: bytes, max_images: int = 2000) -> Iterator[Tuple[str, bytes, dict]]:
    """Image XObjects out of a PDF, without a PDF library.

    A JPEG (`DCTDecode`) or JPEG 2000 (`JPXDecode`) stream is the image file
    as the producer wrote it, so it comes out verbatim and its hashes can
    match a hash set. Anything stored as raw pixels -- Flate, LZW, CCITT --
    is *not* a file that ever existed on its own, so it is not reconstructed
    here: rebuilding it would produce bytes whose hashes belong to this tool
    rather than to any evidence. Those are reported, not written.
    """
    found = 0
    skipped_raster = 0
    for match in _PDF_STREAM.finditer(data):
        if found >= max_images:
            break
        header = _own_dict(match.group("dict"))
        if b"/Image" not in header:
            continue
        payload = _stream_payload(data, match.end(), header)
        if not payload:
            continue

        # The image file is what the last filter decodes; anything before
        # it in the list is transport encoding over that file. A stream
        # with Flate in front of DCT was written out as zlib bytes named
        # .jpg, whose hashes no set can ever hold.
        filters = _pdf_filters(header)
        last = filters[-1] if filters else b""
        if last == b"DCTDecode":
            ext = "jpg"
        elif last == b"JPXDecode":
            ext = "jp2"
        else:
            last = None
        if last is not None and filters[:-1]:
            if filters[:-1] != [b"FlateDecode"]:
                last = None
            else:
                try:
                    payload = zlib.decompress(payload)
                except zlib.error:
                    last = None
        if last is None:
            # Pixels, not a file. Counted so the caller can say how many
            # images were seen but not extractable as their own bytes.
            skipped_raster += 1
            continue

        width = _pdf_dict_value(header, rb"/Width")
        height = _pdf_dict_value(header, rb"/Height")
        found += 1
        yield (f"image_{found:04d}.{ext}", payload,
               {"ext": ext, "width": width, "height": height,
                "verbatim": True})
    if skipped_raster:
        yield ("", b"", {"raster_only": skipped_raster})


def pdf_embedded_files(data: bytes, max_files: int = 200) -> Iterator[Tuple[str, bytes, dict]]:
    """Attachments carried inside a PDF, which are whole files verbatim."""
    found = 0
    # The whole stream dictionary, wherever /Type sits in it: only the text
    # after "/Type /EmbeddedFile" was read, so a /Filter written before it
    # was missed and zlib bytes came out marked verbatim.
    for match in _PDF_STREAM.finditer(data):
        if found >= max_files:
            break
        header = _own_dict(match.group("dict"))
        if not re.search(rb"/Type\s*/EmbeddedFile\b", header):
            continue
        payload = _stream_payload(data, match.end(), header)
        if payload is None:
            continue
        filters = _pdf_filters(header)
        if filters == [b"FlateDecode"]:
            try:
                payload = zlib.decompress(payload)
            except zlib.error:
                continue
        elif filters:
            continue    # an encoding this does not undo: not the file
        if payload:
            found += 1
            yield (f"attachment_{found:03d}", payload, {"verbatim": True})


# --------------------------------------------------------------------------
# Saved web pages
# --------------------------------------------------------------------------
def is_mhtml(head: bytes) -> bool:
    lowered = head[:2048].lower()
    return (b"mime-version:" in lowered
            and (b"multipart/related" in lowered or b"content-location:" in lowered))


def mhtml_parts(data: bytes) -> Iterator[Tuple[str, bytes, dict]]:
    """Image and video parts of a saved page, verbatim.

    A saved page keeps each resource as a MIME part, so what comes out is
    what the browser downloaded -- unlike an `<img src="photo.jpg">`, which
    is a link to a file sitting beside the page and ingested on its own.
    """
    import email
    import email.policy

    try:
        message = email.message_from_bytes(data, policy=email.policy.default)
    except Exception:  # noqa: BLE001 - malformed saved pages are common
        return
    index = 0
    for part in message.walk():
        kind = (part.get_content_type() or "").lower()
        if not (kind.startswith("image/") or kind.startswith("video/")):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            continue
        if not payload:
            continue
        index += 1
        location = part.get("Content-Location") or f"part_{index:03d}"
        ext = kind.split("/", 1)[-1].split(";")[0].strip()
        yield (location, payload, {"ext": {"jpeg": "jpg"}.get(ext, ext),
                                   "verbatim": True})


def html_data_uris(data: bytes, max_items: int = 2000) -> Iterator[Tuple[str, bytes, dict]]:
    """Media held in `data:` URIs inside an HTML page.

    These are content rather than links, so they are worth pulling out. The
    carver already finds base64 images anywhere in a file; this names them by
    their declared type instead of guessing from the decoded bytes.
    """
    pattern = re.compile(
        rb"data:(image|video)/([A-Za-z0-9.+-]{1,20});base64,([A-Za-z0-9+/=\s]{64,})")
    found = 0
    for match in pattern.finditer(data):
        if found >= max_items:
            break
        subtype = match.group(2).decode("ascii", "replace").lower()
        blob = re.sub(rb"\s", b"", match.group(3))
        try:
            payload = base64.b64decode(blob, validate=False)
        except Exception:  # noqa: BLE001
            continue
        if len(payload) < 64:
            continue
        found += 1
        yield (f"data_uri_{found:04d}.{subtype}", payload,
               {"ext": {"jpeg": "jpg", "svg+xml": "svg"}.get(subtype, subtype),
                "verbatim": True})


def document_kind(head: bytes, name: str = "") -> Optional[str]:
    """Which of these a file is, by its bytes rather than its name."""
    if is_thumbcache(head):
        return "thumbcache"
    if is_pdf(head):
        return "pdf"
    if is_mhtml(head):
        return "mhtml"
    lowered = head[:1024].lower()
    if b"<html" in lowered or b"<!doctype html" in lowered:
        return "html"
    if name.lower().endswith((".mht", ".mhtml")):
        return "mhtml"
    return None
