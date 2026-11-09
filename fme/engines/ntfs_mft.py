"""
ntfs_mft.py -- deleted files out of the NTFS master file table.

Carving finds content and loses everything else: a carved picture has no
name, no path, no dates and no parent folder, because the file system no
longer has a record of it. The MFT does. Every file on an NTFS volume has a
record there, and deleting a file marks its record unused rather than
erasing it -- so until that record is reused, the name, the path, the
timestamps and the size are all still readable, and so is the content, for
as long as its clusters stay free.

Two things here that carving cannot do at all:

**Resident data.** A file small enough to fit inside its own MFT record --
up to roughly 700 bytes, which is a thumbnail, an icon, a short note --
never occupies a cluster. It lives in the record. A carver scanning free
clusters will never see it, because it was never there. Reading it out of
the record is the only way to get it.

**Orphaned records.** A record whose parent folder has itself been deleted
and reused is unreachable by walking directories, but the record is still
in the table with its name intact. Walking the table rather than the tree
finds it.

And one caution the output carries, because it matters more than the
recovery: an unused MFT record names clusters that the file system has
released. Anything may have been written there since. A deleted file whose
content still decodes is probably intact; one that does not is not evidence
of anything except that its clusters were reused. Every file recovered this
way is labeled with which it is.
"""

from __future__ import annotations

import datetime
import struct
from typing import Iterator, Optional

__version__ = "1.0.0"

RECORD_SIGNATURE = b"FILE"
ATTR_STANDARD_INFORMATION = 0x10
ATTR_ATTRIBUTE_LIST = 0x20
ATTR_FILE_NAME = 0x30
ATTR_DATA = 0x80
ATTR_REPARSE_POINT = 0xC0
ATTR_END = 0xFFFFFFFF

# An attribute's own flags, at offset 12 of its header. They change what the
# clusters hold: compressed clusters are LZNT1, encrypted ones are EFS
# ciphertext. Read as plain content, either is stored and hashed as a file
# it never was -- and a JPEG's first cluster often survives compression
# well enough to be classed as a picture by its name.
DATA_COMPRESSED = 0x0001
DATA_ENCRYPTED = 0x4000
DATA_SPARSE = 0x8000
# FILE_ATTRIBUTE_ENCRYPTED in $STANDARD_INFORMATION: a second word for it,
# kept in a record whose $DATA has moved to an extension that is gone.
FILE_ATTRIBUTE_ENCRYPTED = 0x4000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000

# Reparse tags whose unnamed $DATA is not the file. The Windows Overlay
# Filter (compact.exe, CompactOS) leaves $DATA an all-sparse hole and keeps
# the content compressed in a named stream; a cloud provider's online-only
# placeholder keeps it with the provider. Read as $DATA, both are zeros of
# the right length -- a file stored and hashed as nothing but zeros.
IO_REPARSE_TAG_WOF = 0x80000017
IO_REPARSE_TAG_CLOUD = 0x9000001A
IO_REPARSE_TAG_CLOUD_MASK = 0xFFFF0FFF  # the flags nibble varies

# Larger than this is not read into memory by content(); content_stream()
# reads any size.
IN_MEMORY_LIMIT = 2 * 1024 * 1024 * 1024
# How much content_stream() reads from the volume at a time.
STREAM_STEP = 4 * 1024 * 1024

# Namespaces an NTFS name can be in. A file usually has two records, the
# short 8.3 one and the long one; the long one is what a person recognizes.
NAME_POSIX, NAME_WIN32, NAME_DOS, NAME_WIN32_DOS = 0, 1, 2, 3

MAX_RECORD = 4096
MAX_NAME = 255
# 1601-01-01 to 1970-01-01 in 100-nanosecond units, which is how NTFS counts.
WINDOWS_EPOCH_OFFSET = 116444736000000000


# Where a FILETIME counts from. Zero is "never set", which Windows writes
# for a time it has no value for.
_FILETIME_ZERO = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)


def _filetime(value: int) -> str:
    """An NTFS timestamp as ISO text, or empty if it says nothing.

    Counted from 1601 directly, never through the 1970 epoch. Going through
    the epoch dropped every time before 1970 as empty -- a clock reset to
    1601 or 1980 on a camera card copied across is evidence, not nothing --
    and dividing to float seconds and printing whole seconds threw away the
    fraction NTFS keeps to 100 ns, which is what orders two files written in
    the same second. Kept here to the microsecond, as far as a datetime
    goes; the last 100-ns digit is the only part not carried.
    """
    if not value:
        return ""
    try:
        moment = _FILETIME_ZERO + datetime.timedelta(microseconds=value // 10)
    except (OverflowError, ValueError):
        return ""                   # past year 9999: not a time
    # NTFS records UTC, so the time says so. It was written with the zone
    # stripped, and a time with no zone cannot be told from a FAT or EXIF
    # time, which is local to a device nobody knows the zone of. isoformat()
    # leaves the fraction off when there is none, so a whole-second time
    # reads exactly as it always did.
    return moment.isoformat()


def read_boot(data: bytes) -> dict:
    """The volume geometry, from the boot sector.

    Returns {} for anything that is not NTFS, rather than guessing: the
    numbers here decide where every record is read from, and a wrong guess
    reads noise confidently.
    """
    if len(data) < 512 or data[3:11] != b"NTFS    ":
        return {}
    try:
        bytes_per_sector = struct.unpack("<H", data[11:13])[0]
        sectors_per_cluster = data[13]
        total_sectors = struct.unpack("<Q", data[40:48])[0]
        mft_cluster = struct.unpack("<Q", data[48:56])[0]
        raw_record = struct.unpack("<b", data[64:65])[0]
    except struct.error:
        return {}

    if bytes_per_sector not in (512, 1024, 2048, 4096):
        return {}
    if sectors_per_cluster == 0 or sectors_per_cluster > 128:
        # Above 128 the field is a power of two held as a negative byte.
        shift = struct.unpack("<b", data[13:14])[0]
        if not -12 <= shift < 0:
            return {}
        sectors_per_cluster = 1 << (-shift)

    cluster_size = bytes_per_sector * sectors_per_cluster
    record_size = (raw_record * cluster_size if raw_record > 0
                   else 1 << (-raw_record))
    if not 256 <= record_size <= MAX_RECORD:
        return {}

    return {"bytes_per_sector": bytes_per_sector,
            "sectors_per_cluster": sectors_per_cluster,
            "cluster_size": cluster_size,
            "mft_offset": mft_cluster * cluster_size,
            "record_size": record_size,
            "total_bytes": total_sectors * bytes_per_sector}


# The update sequence covers a record in 512-byte blocks whatever the
# device's sector size: ntfs-3g and the Linux ntfs3 driver both fix it at
# 512, and Windows writes it that way on 4Kn volumes too.
FIXUP_BLOCK = 512


def apply_fixups(record: bytearray, bytes_per_sector: int = FIXUP_BLOCK) -> bool:
    """Undo the update sequence NTFS writes across a record.

    The last two bytes of every 512-byte block in a record are replaced by a
    counter when it is written, and the real values kept in an array at the
    front. Putting them back is not optional: without it the last two bytes
    of each block are wrong, which corrupts whatever attribute happens to
    span the boundary -- usually resident data, exactly what we came for.

    The blocks are 512 bytes whatever the sector size. Stepping by the
    volume's sector size instead, as this once did, rejected every record on
    a 4Kn volume -- the table's own record included -- so MFT recovery found
    nothing there and said nothing. bytes_per_sector is accepted so callers
    need not change, and does not decide the stride.
    """
    try:
        offset = struct.unpack("<H", record[4:6])[0]
        count = struct.unpack("<H", record[6:8])[0]
    except struct.error:
        return False
    # One entry for the marker, then one per block -- exactly as many as
    # the record has blocks, or it was not written as a record this size.
    if len(record) % FIXUP_BLOCK or count != len(record) // FIXUP_BLOCK + 1:
        return False
    if offset < 8 or offset + count * 2 > len(record):
        return False

    marker = record[offset:offset + 2]
    for index in range(1, count):
        end = index * FIXUP_BLOCK - 2
        if bytes(record[end:end + 2]) != marker:
            return False            # not a record written by this volume
        source = offset + index * 2
        record[end:end + 2] = record[source:source + 2]
    return True


def _attributes(record: bytes) -> Iterator[tuple]:
    """Each attribute in a record as (type, resident, header, body)."""
    try:
        first = struct.unpack("<H", record[20:22])[0]
    except struct.error:
        return
    position = first
    while position + 8 <= len(record):
        kind = struct.unpack("<I", record[position:position + 4])[0]
        if kind == ATTR_END:
            return
        length = struct.unpack("<I", record[position + 4:position + 8])[0]
        if length < 24 or position + length > len(record):
            return
        body = record[position:position + length]
        resident = body[8] == 0
        yield kind, resident, body
        position += length


def _resident_value(body: bytes) -> bytes:
    try:
        length = struct.unpack("<I", body[16:20])[0]
        offset = struct.unpack("<H", body[20:22])[0]
    except struct.error:
        return b""
    if offset + length > len(body):
        return b""
    return body[offset:offset + length]


def _runs(body: bytes) -> list:
    """Decode a non-resident attribute's data runs into (lcn, clusters).

    A run with no cluster number is sparse -- a hole, which reads as zeros
    and is recorded as such rather than skipped, because its length still
    counts toward where the next run sits.
    """
    try:
        start = struct.unpack("<H", body[32:34])[0]
    except struct.error:
        return []
    runs, position, cluster = [], start, 0
    while position < len(body):
        header = body[position]
        if header == 0:
            break
        length_size = header & 0x0F
        offset_size = (header >> 4) & 0x0F
        position += 1
        if length_size == 0 or position + length_size + offset_size > len(body):
            break
        count = int.from_bytes(body[position:position + length_size], "little")
        position += length_size
        if offset_size:
            delta = int.from_bytes(body[position:position + offset_size],
                                   "little", signed=True)
            position += offset_size
            cluster += delta
            runs.append((cluster, count))
        else:
            runs.append((None, count))      # sparse
    return runs


def parse_record(raw: bytes, bytes_per_sector: int, number: int = -1) -> dict:
    """One MFT record: what it was, and where its content is.

    Returns {} for anything that is not a record. A record that fails its
    fixups is reported rather than silently half-read, because a record
    written by a different volume looks plausible until its sector
    boundaries are wrong.
    """
    if len(raw) < 48 or raw[:4] != RECORD_SIGNATURE:
        return {}
    record = bytearray(raw)
    if not apply_fixups(record, bytes_per_sector):
        return {}
    record = bytes(record)

    try:
        sequence = struct.unpack("<H", record[16:18])[0]
        # A record that is an extension of another names its base here;
        # zero for a base record. NTFS moves attributes out to extension
        # records when they no longer fit -- most often the $DATA run list
        # of a heavily fragmented file.
        base_reference = struct.unpack("<Q", record[32:40])[0]
        flags = struct.unpack("<H", record[22:24])[0]
        used = struct.unpack("<I", record[24:28])[0]
    except struct.error:
        return {}
    if used > len(record):
        return {}

    in_use = bool(flags & 0x01)
    is_directory = bool(flags & 0x02)

    best_name, parent, parent_sequence, names = "", None, 0, []
    has_attribute_list = False
    data_segments = None
    data_vcn = 0
    initialized = None
    data_flags = 0
    compression_unit = 0
    file_attributes = 0
    reparse_tag = None
    created = modified = accessed = ""
    # Two sizes, kept apart. $FILE_NAME carries a copy that Windows updates
    # only when the name changes, so it is often stale; $DATA's real size
    # is the file's. Taking whichever came first -- and $FILE_NAME always
    # comes first, attributes being stored in type order -- cut a file that
    # had grown since it was named short, ran one that had shrunk on into
    # its slack, and hashed neither as it was.
    name_size = 0
    data_size: Optional[int] = None
    resident_data: Optional[bytes] = None
    runs: list = []
    allocated = 0

    for kind, resident, body in _attributes(record[:used]):
        if kind == ATTR_STANDARD_INFORMATION and resident:
            value = _resident_value(body)
            if len(value) >= 32:
                created = _filetime(struct.unpack("<Q", value[0:8])[0])
                modified = _filetime(struct.unpack("<Q", value[8:16])[0])
                accessed = _filetime(struct.unpack("<Q", value[24:32])[0])
            if len(value) >= 36:
                file_attributes = struct.unpack("<I", value[32:36])[0]
        elif kind == ATTR_FILE_NAME and resident:
            value = _resident_value(body)
            if len(value) < 66:
                continue
            reference = struct.unpack("<Q", value[0:8])[0]
            name_length = value[64]
            namespace = value[65]
            text = value[66:66 + name_length * 2].decode("utf-16-le", "replace")
            if not text or name_length > MAX_NAME:
                continue
            names.append((namespace, text))
            parent = reference & 0x0000FFFFFFFFFFFF
            parent_sequence = reference >> 48
            if not name_size:
                name_size = struct.unpack("<Q", value[48:56])[0]
        elif kind == ATTR_REPARSE_POINT and resident:
            value = _resident_value(body)
            if len(value) >= 4:
                reparse_tag = struct.unpack("<I", value[0:4])[0]
        elif kind == ATTR_ATTRIBUTE_LIST:
            has_attribute_list = True
            if resident:
                data_segments = _data_segments(_resident_value(body))
        elif kind == ATTR_DATA:
            # The unnamed $DATA attribute is the file's content; a named one
            # is an alternate stream and is left alone.
            name_length = body[9]
            if name_length:
                continue
            data_flags = struct.unpack("<H", body[12:14])[0]
            if resident:
                resident_data = _resident_value(body)
                data_size = len(resident_data)
            else:
                runs = _runs(body)
                try:
                    # Where in the file this stretch of runs starts. Only
                    # the stretch at VCN 0 carries the file's sizes.
                    data_vcn = struct.unpack("<Q", body[16:24])[0]
                except struct.error:
                    data_vcn = 0
                # Clusters per compression unit, as a power of two; NTFS
                # writes 4 (16 clusters). Meaningful only with the flag.
                compression_unit = body[34] if len(body) > 34 else 0
                try:
                    data_size = struct.unpack("<Q", body[48:56])[0]
                    allocated = struct.unpack("<Q", body[40:48])[0]
                    initialized = struct.unpack("<Q", body[56:64])[0]
                except struct.error:
                    pass

    for namespace in (NAME_WIN32, NAME_WIN32_DOS, NAME_POSIX, NAME_DOS):
        for found_namespace, text in names:
            if found_namespace == namespace:
                best_name = text
                break
        if best_name:
            break

    # $FILE_NAME's copy is used only when the record has no $DATA size at
    # all, and "size_from" says which one the size is.
    size = data_size if data_size is not None else name_size
    return {"number": number, "in_use": in_use, "directory": is_directory,
            "name": best_name, "parent": parent, "size": size,
            "sequence": sequence, "parent_sequence": parent_sequence,
            "base": base_reference & 0x0000FFFFFFFFFFFF,
            "base_sequence": base_reference >> 48,
            "has_attribute_list": has_attribute_list, "data_vcn": data_vcn,
            "data_segments": data_segments,
            "initialized": initialized,
            "data_flags": data_flags, "compression_unit": compression_unit,
            "file_attributes": file_attributes, "reparse_tag": reparse_tag,
            "size_from": "data" if data_size is not None else "name",
            "created": created, "modified": modified, "accessed": accessed,
            "resident": resident_data, "runs": runs, "allocated": allocated}


def _mft_runs(read, boot: dict) -> list:
    """Where the table itself lives, from its own record."""
    raw = read(boot["mft_offset"], boot["record_size"])
    record = parse_record(raw, boot["bytes_per_sector"], 0)
    if record and record.get("runs"):
        return record["runs"]
    # Without its own record, assume the table is contiguous from where the
    # boot sector says it starts. That is true of most volumes and is the
    # only guess worth making.
    # None, not 0, says "to the end of the volume": a run of 0 clusters in a
    # damaged record read the whole rest of the volume as MFT records.
    return [(boot["mft_offset"] // boot["cluster_size"], None)]


def records(read, boot: dict, limit: int = 0) -> Iterator[dict]:
    """Every record in the table, in order, used and unused alike."""
    record_size = boot["record_size"]
    cluster_size = boot["cluster_size"]
    seen = 0
    for lcn, count in _mft_runs(read, boot):
        if lcn is None:
            # A sparse stretch of the table still holds record numbers.
            # Skipping it without counting them renumbered every record
            # after it, and parent references then named the wrong folders.
            seen += (count or 0) * cluster_size // record_size
            if limit and seen >= limit:
                return
            continue
        if count == 0 or lcn < 0:
            continue
        base = lcn * cluster_size
        span = (count * cluster_size if count is not None
                else boot.get("total_bytes", 0) - base)
        position = 0
        while position + record_size <= max(span, record_size):
            raw = read(base + position, record_size)
            if len(raw) < record_size:
                return
            if raw[:4] == RECORD_SIGNATURE:
                found = parse_record(raw, boot["bytes_per_sector"], seen)
                if found:
                    yield found
            seen += 1
            position += record_size
            if limit and seen >= limit:
                return


def deleted_files(read, boot: dict, limit: int = 0) -> Iterator[dict]:
    """Records that are not in use and describe a file with content.

    A file whose run list outgrew its record keeps its name, dates and an
    $ATTRIBUTE_LIST in its base record, and its $DATA in extension records
    that point back to it. Each record used to be judged alone: the named
    base record had no $DATA of its own and was dropped, and the first
    extension came back as a nameless, undated file cut off where the next
    extension took over -- usually a large video, usually the one that
    mattered. Extension records are now joined to their base record, in
    the order of the file they hold, and never yielded on their own.
    """
    bases, extensions = {}, {}
    for record in records(read, boot, limit):
        if record.get("base"):
            if not record["in_use"]:
                extensions.setdefault(record["base"], []).append(record)
            continue
        if record["in_use"] or record["directory"]:
            continue
        if record.get("has_attribute_list"):
            bases[record["number"]] = record
            continue
        if record["resident"] is None and not record["runs"]:
            continue
        yield record
    for number, base in bases.items():
        joined = _joined(base, extensions.get(number, []), boot["cluster_size"])
        if joined is not None:
            yield joined


def _data_segments(value: bytes) -> list:
    """Where an $ATTRIBUTE_LIST says the unnamed $DATA lives.

    Each entry names an attribute, the VCN its stretch starts at and the
    record holding it. Returned as [(vcn, record number, sequence)] for the
    unnamed $DATA only.
    """
    found, at = [], 0
    while at + 26 <= len(value):
        kind, length, name_length = struct.unpack_from("<IHB", value, at)
        if not length or kind == 0 or kind == ATTR_END:
            break
        vcn, reference = struct.unpack_from("<QQ", value, at + 8)
        if kind == ATTR_DATA and name_length == 0:
            found.append((vcn, reference & 0x0000FFFFFFFFFFFF, reference >> 48))
        at += length
    return found


def _joined(base: dict, extensions: list, cluster_size: int) -> Optional[dict]:
    """A base record with its extensions' $DATA put back in file order.

    An extension belongs to this file only if it still names the base by
    the sequence number the base had -- or one behind it, since Windows
    advances a record's number when it frees it. Stretches are joined while
    each starts where the last ended; at the first gap the file stops, and
    the record says it is partial rather than filling the gap with guesses.
    """
    pieces = []
    if base["resident"] is not None:
        return base
    # When the base record's own attribute list can be read, it says which
    # records hold the file's $DATA: only those count, however many others
    # still point back at the base, and one it names that is gone makes the
    # file partial. Without it, the back-pointers are all there is.
    listed = base.get("data_segments") or []
    missing = False
    if listed:
        named = {number for _, number, _ in listed if number != base["number"]}
        extensions = [e for e in extensions if e["number"] in named]
        missing = bool(named - {e["number"] for e in extensions})
    if base["runs"]:
        pieces.append(base)
    for extension in extensions:
        wanted = extension.get("base_sequence", 0)
        have = base.get("sequence", 0)
        if wanted and have not in (wanted, (wanted + 1) & 0xFFFF):
            continue                      # reused for another file since
        if extension["resident"] is not None:
            return dict(base, resident=extension["resident"],
                        size=len(extension["resident"]), size_from="data",
                        data_flags=extension.get("data_flags", 0))
        if extension["runs"]:
            pieces.append(extension)
    if not pieces:
        return None
    pieces.sort(key=lambda piece: piece.get("data_vcn", 0))
    first = pieces[0]
    if first.get("data_vcn", 0) != 0:
        return None                       # the start of the file is gone
    runs, next_vcn, partial = [], 0, False
    for piece in pieces:
        if piece.get("data_vcn", 0) != next_vcn:
            partial = True
            break
        runs += piece["runs"]
        next_vcn += sum(count for _, count in piece["runs"])
    size = first["size"] if first.get("size_from") == "data" else base["size"]
    # A stretch missing from the end leaves no gap to see -- only a run
    # list that stops short of the size the file records.
    if next_vcn * cluster_size < size or missing:
        partial = True
    return dict(base, runs=runs, size=size, size_from="data",
                allocated=first.get("allocated", 0),
                initialized=first.get("initialized"), pieces=len(pieces),
                # The flags that decide how the clusters are read are the
                # ones on the stretch at VCN 0, not the base record's, which
                # may have no $DATA at all.
                data_flags=first.get("data_flags", 0),
                compression_unit=first.get("compression_unit", 0),
                partial=partial)


class Undecodable(ValueError):
    """Compressed clusters that are not an LZNT1 stream."""


def lznt1_decompress(src: bytes, limit: int) -> bytes:
    """Inflate one NTFS compression unit's stored bytes to `limit` bytes.

    LZNT1 is a series of chunks, each a 16-bit header -- the stored length
    less 3 in the low 12 bits, "compressed" in the top bit -- then either
    4096 literal bytes or groups of eight items behind a flag byte, each item
    a literal (bit clear) or a 16-bit back reference (bit set). A back
    reference splits into displacement and length at a point that moves as
    the chunk's output grows: 4 displacement bits until 16 bytes are out, one
    more at each doubling, up to 12. That moving split is the part most
    implementations get wrong, and getting it wrong decodes the first 16
    bytes of every chunk correctly and the rest into plausible garbage.

    Whatever is short of `limit` reads as zeros: a compressed chunk that
    inflates to less than 4096 bytes is filled to 4096 so the next starts
    where it belongs, and a unit that ends (a zero header, or the stored
    bytes running out) is filled to its end. That is what ntfs-3g's
    ntfs_decompress() does, and what Windows shows.

    Raises Undecodable for bytes that cannot be LZNT1 -- a chunk running
    past the stored bytes, a stored chunk that is not 4096 bytes, a back
    reference before its chunk. On released clusters that means the unit was
    overwritten, and the caller says so rather than storing what it got.
    """
    out = bytearray()
    at = 0
    while at + 2 <= len(src) and len(out) < limit:
        header = src[at] | (src[at + 1] << 8)
        at += 2
        if header == 0:
            break
        size = (header & 0x0FFF) + 1
        if at + size > len(src):
            raise Undecodable("a chunk runs past the bytes stored for its unit")
        chunk = src[at:at + size]
        at += size
        if not header & 0x8000:
            if size != 4096:
                raise Undecodable(f"a stored chunk is {size} bytes, not 4096")
            out += chunk
            continue
        start = len(out)
        i = 0
        while i < len(chunk) and len(out) - start < 4096:
            flags = chunk[i]
            i += 1
            for bit in range(8):
                if i >= len(chunk) or len(out) - start >= 4096:
                    break
                if not flags & (1 << bit):
                    out.append(chunk[i])
                    i += 1
                    continue
                if i + 2 > len(chunk):
                    raise Undecodable("a back reference is cut off by the "
                                      "end of its chunk")
                token = chunk[i] | (chunk[i + 1] << 8)
                i += 2
                done = len(out) - start
                shift, span = 12, done - 1
                while span >= 0x10:
                    shift -= 1
                    span >>= 1
                length = (token & ((1 << shift) - 1)) + 3
                back = (token >> shift) + 1
                if back > done:
                    raise Undecodable("a back reference reaches before the "
                                      "start of its chunk")
                # Byte by byte: a reference may overlap what it is writing,
                # which is how a run of one repeated byte is stored.
                source = len(out) - back
                for k in range(min(length, 4096 - done)):
                    out.append(out[source + k])
        filled = len(out) - start
        if filled < 4096:
            out += bytes(4096 - filled)
    if len(out) < limit:
        out += bytes(limit - len(out))
    return bytes(out[:limit])


def refusal(record: dict) -> str:
    """Why a record's content cannot be read as the file, or "".

    Said rather than returned as bytes: an EFS file's clusters are
    ciphertext, and stored as a picture by its name they would be hashed and
    graded as one.
    """
    flags = record.get("data_flags", 0) or 0
    if flags & DATA_ENCRYPTED or \
            (record.get("file_attributes", 0) or 0) & FILE_ATTRIBUTE_ENCRYPTED:
        return ("encrypted with EFS: its clusters hold ciphertext, and the "
                "key is not on the volume")
    tag = record.get("reparse_tag")
    if tag == IO_REPARSE_TAG_WOF:
        return ("compressed by the Windows Overlay Filter: its $DATA is an "
                "empty hole and the content is in a WofCompressedData stream "
                "this pass does not read")
    # A cloud file is a placeholder when Windows would fetch it on reading
    # (RECALL_ON_DATA_ACCESS), or when it records a size and stores nothing.
    # One that is all there -- hydrated -- keeps its tag and reads as usual.
    if tag is not None and (tag & IO_REPARSE_TAG_CLOUD_MASK) == \
            IO_REPARSE_TAG_CLOUD and record.get("resident") is None and (
                (record.get("file_attributes", 0) or 0)
                & FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
                or (record.get("size") and all(
                    lcn is None for lcn, _ in record.get("runs") or []))):
        return ("an online-only cloud placeholder: its content was with the "
                "provider, not on the volume")
    if flags & DATA_COMPRESSED:
        if record.get("resident") is not None:
            return ("its data is resident and flagged compressed, which NTFS "
                    "does not write; not read rather than guessed at")
        unit = record.get("compression_unit", 0) or 0
        if not 1 <= unit <= 8:
            return (f"compressed with a compression unit of 2^{unit} "
                    f"clusters, which NTFS does not write")
    return ""


def _plain_pieces(runs, read, cluster_size, wanted, step):
    """Uncompressed content from a run list, at most `step` bytes a time."""
    got = 0
    for lcn, count in runs:
        if got >= wanted:
            return
        length = min(count * cluster_size, wanted - got)
        if lcn is not None and lcn < 0:
            return                        # a damaged run points before the volume
        done = 0
        while done < length:
            take = min(step, length - done)
            if lcn is None:
                piece = bytes(take)       # a hole, which is real
            else:
                piece = read(lcn * cluster_size + done, take)
            if piece:
                yield piece
            done += len(piece)
            if len(piece) < take:
                # Stop at the first read that does not come back in full.
                # Carrying on appended the next run straight after the gap,
                # so every later byte sat at the wrong offset in a file that
                # was stored and hashed, while the note said only that its
                # end was missing.
                return
        got += length


def _unit_extents(runs, first_vcn, clusters):
    """The (lcn or None, count) stretches covering VCNs [first, first+n)."""
    found, vcn = [], 0
    for lcn, count in runs:
        lo, hi = max(vcn, first_vcn), min(vcn + count, first_vcn + clusters)
        if lo < hi:
            found.append((None if lcn is None else lcn + (lo - vcn), hi - lo))
        vcn += count
        if vcn >= first_vcn + clusters:
            break
    return found


def _compressed_pieces(runs, read, cluster_size, unit_clusters, wanted):
    """LZNT1-compressed content, one compression unit at a time.

    A unit whose clusters are all stored was not worth compressing and is
    plain; one with some clusters stored and the rest a hole is compressed,
    the stored ones holding its LZNT1 stream; one that is all hole is zeros.
    """
    unit = unit_clusters * cluster_size
    total = sum(count for _, count in runs)
    produced, first = 0, 0
    while produced < wanted and first < total:
        extents = _unit_extents(runs, first, unit_clusters)
        stored = [(lcn, n) for lcn, n in extents if lcn is not None]
        stored_clusters = sum(n for _, n in stored)
        if any(lcn < 0 for lcn, _ in stored):
            return
        raw = bytearray()
        for lcn, n in stored:
            piece = read(lcn * cluster_size, n * cluster_size)
            raw += piece
            if len(piece) < n * cluster_size:
                if stored_clusters >= unit_clusters:
                    # A plain unit read short: what came back is still at
                    # the right offset, and the file stops there.
                    yield bytes(raw)[:wanted - produced]
                return
        if not stored:
            out = bytes(unit)
        elif stored_clusters >= unit_clusters:
            out = bytes(raw[:unit])
        else:
            try:
                out = lznt1_decompress(bytes(raw), unit)
            except Undecodable as exc:
                raise Undecodable(f"the compression unit at byte {produced:,} "
                                  f"does not decode: {exc}") from None
        out = out[:wanted - produced]
        yield out
        produced += len(out)
        first += unit_clusters


def content_stream(record: dict, read, boot: dict,
                   step: int = STREAM_STEP) -> tuple:
    """A deleted file's bytes as an iterator of pieces, and how far to trust
    them -- for a file of any size, where content() stops at 2 GiB.

    `how` is "resident", "from released clusters", "refused" (record["refused"]
    says why: encrypted, or compressed in a way NTFS does not write) or
    "unreadable" (nothing to read). The iterator is empty for the last two.

    What only the reading can find out is written into the record when the
    iterator is exhausted, not before: "short_by" (bytes the run list or the
    volume did not give), "zeroed_from" (past the initialized size, read as
    zeros) and "undecodable" (a compression unit that is not LZNT1, so the
    clusters were overwritten; the file stops there and short_by counts the
    rest). A caller that builds its note before draining the iterator will
    miss them.
    """
    record.pop("refused", None)
    record.pop("undecodable", None)
    record.pop("zeroed_from", None)
    reason = refusal(record)
    if reason:
        record["refused"] = reason
        return iter(()), "refused"
    if record["resident"] is not None:
        return iter([record["resident"]]), "resident"

    cluster_size = boot["cluster_size"]
    if record.get("size_from") == "data":
        # The file's own size, even when it is zero: an empty file has
        # nothing to recover, and its allocation is not its content.
        wanted = record["size"]
    else:
        wanted = record["size"] or record["allocated"]
    if not wanted:
        return iter(()), "unreadable"

    # Only as far as the file was ever written. NTFS records that point as
    # the initialized size (the valid data length); Windows shows what lies
    # past it as zeros, whatever the clusters hold -- a file preallocated
    # and part-written, a recording cut off. Reading those clusters as the
    # file gave bytes it never held, and a hash nothing else would match.
    # For a compressed file it counts the inflated bytes.
    valid = record.get("initialized")
    readable = wanted if valid is None else min(wanted, valid)
    compressed = bool((record.get("data_flags", 0) or 0) & DATA_COMPRESSED)
    record["short_by"] = 0

    def pieces():
        got = 0
        if compressed:
            source = _compressed_pieces(record["runs"], read, cluster_size,
                                        1 << record["compression_unit"],
                                        readable)
        else:
            source = _plain_pieces(record["runs"], read, cluster_size,
                                   readable, step)
        try:
            for piece in source:
                piece = piece[:readable - got]
                if piece:
                    got += len(piece)
                    yield piece
                if got >= readable:
                    break
        except Undecodable as exc:
            record["undecodable"] = str(exc)
        if got < readable:
            # The run list stops before the file does, or a unit did not
            # decode. Said, rather than handed back as though it were the
            # whole file.
            record["short_by"] = readable - got
            return
        if readable < wanted:
            record["zeroed_from"] = readable
            left = wanted - readable
            while left:
                take = min(step, left)
                yield bytes(take)
                left -= take

    return pieces(), "from released clusters"


def content(record: dict, read, boot: dict) -> tuple:
    """A deleted file's bytes, and how much to trust them.

    Resident data is exact: it was inside the record and nothing else can
    have been written over it. Non-resident data is read from clusters the
    file system has released, so it is what is there now, which may or may
    not still be the file. The caller is told which it has.

    Read into memory, so a file larger than IN_MEMORY_LIMIT comes back
    empty with how "too large" and record["refused"] saying so; it used to
    come back as b"" and "unreadable", and the caller dropped the file --
    usually a video, usually the one that mattered -- without a word.
    content_stream() reads it. "refused" is an encrypted or impossibly
    compressed file, with record["refused"] saying which.
    """
    if record["resident"] is None and not refusal(record):
        wanted = (record["size"] if record.get("size_from") == "data"
                  else record["size"] or record["allocated"])
        if wanted > IN_MEMORY_LIMIT:
            record["refused"] = (f"{wanted:,} bytes, too large to read into "
                                 f"memory; read with content_stream()")
            return b"", "too large"
    chunks, how = content_stream(record, read, boot)
    return b"".join(chunks), how


def path_of(record: dict, directories: dict, depth: int = 32) -> str:
    """The file's path, by walking parent references back to the root.

    A parent reference names a record and the sequence number that record
    had when the reference was written. NTFS reuses records, so the record
    a reference names may now be a different folder; only the sequence
    number tells them apart. Each step checks it, and a folder that is not
    the one the reference meant is reported as "[deleted folder]", the same
    as one that is gone -- because the original is, and the folder now in
    its record is somewhere the file never was.
    """
    parts, steps = [record["name"]], 0
    parent, wanted = record.get("parent"), record.get("parent_sequence", 0)
    while parent is not None and parent != 5 and steps < depth:
        found = directories.get(parent)
        if not found or not found.get("name") or not _same_folder(found, wanted):
            parts.append("[deleted folder]")
            break
        parts.append(found["name"])
        parent, wanted = found.get("parent"), found.get("parent_sequence", 0)
        steps += 1
    return "/".join(reversed([p for p in parts if p]))


def _same_folder(folder: dict, wanted: int) -> bool:
    """Whether a folder record is still the one a reference was written to.

    A sequence number of 0 in the reference carries no check. A live folder
    must match exactly. A deleted folder may also be one ahead, because
    Windows advances a record's sequence number when it frees the record --
    the folder is still the one the file was in, freed after it.
    """
    if not wanted or "sequence" not in folder:
        return True
    have = folder["sequence"]
    if have == wanted:
        return True
    return not folder.get("in_use", True) and have == (wanted + 1) & 0xFFFF


def directory_map(read, boot: dict, limit: int = 0) -> dict:
    """Folder records by number, so a path can be reconstructed."""
    found = {}
    for record in records(read, boot, limit):
        if record["directory"] and record["name"]:
            found[record["number"]] = record
    return found
