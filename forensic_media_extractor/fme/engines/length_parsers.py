"""
length_parsers.py -- Section 3 -- structure-aware length parsers: how long a carved file is.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import functools
import re
import struct
from typing import Callable, Iterator, Optional, Sequence
from fme.engines.output import (  # noqa: E402,F401
    MIN_IMAGE_SIZE,
    MIN_VIDEO_SIZE,
    _FLOORS,
    _floor,
    _set_floors,
    writable_text,
    _writable_record,
    Record,
    _record_from_row,
    BOUNDED_VALUES,
    SIZED_ENGINES,
    _CSV_FIELD_LIMIT,
    ManifestUnreadable,
    CaseFolderInUse,
    refuse_existing_case,
    _OUTPUT_FATAL_ERRNOS,
    _output_is_fatal,
    stop_when_output_fails,
    ORPHAN_ENGINE,
    ORPHAN_NOTE,
    OutputManager,
    _csv_rows,
    _write_replacing,
    _silent_unlink,
    is_animated,
)
from fme.engines.engine_common import (  # noqa: F401
    GIB, KIB, MAX_VIDEO_DEFAULT, MIB,
)
from fme.engines.signatures import (  # noqa: F401
    AUDIO_EXTS, IMAGE_EXTS, MAX_SIG_SPAN, Signature,
    VIDEO_EXTS, scan_buffer,
)



# ==========================================================================
# SECTION 3 -- Structure-aware length parsers
# ==========================================================================
Reader = Callable[[int, int], bytes]
# Parser contract: (read, start, cap) -> (length, ext_override) or None


B64_ALPHABET = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
B64_SKIP = frozenset(b" \t\r\n\\\"'")     # wrapping in HTML, JSON, RTF
HEX_ALPHABET = frozenset(b"0123456789abcdefABCDEF")
HEX_SKIP = frozenset(b" \t\r\n")


def decode_payload(raw: bytes, encoding: str) -> Optional[bytes]:
    """Turn an encoded run into the bytes it represents."""
    if encoding == "base64":
        import base64 as _b64
        cleaned = bytes(c for c in raw if c in B64_ALPHABET)
        cleaned = cleaned[:len(cleaned) - len(cleaned) % 4]
        if len(cleaned) < 32:
            return None
        try:
            return _b64.b64decode(cleaned, validate=False)
        except Exception:  # noqa: BLE001
            return None
    if encoding == "hex":
        cleaned = bytes(c for c in raw if c in HEX_ALPHABET)
        cleaned = cleaned[:len(cleaned) - len(cleaned) % 2]
        if len(cleaned) < 64:
            return None
        try:
            return bytes.fromhex(cleaned.decode("ascii"))
        except ValueError:
            return None
    return None


def _encoded_run(read: Reader, start: int, cap: int, alphabet, skip) -> int:
    """Length of the encoded text run beginning at start."""
    length = 0
    pos = start
    limit = min(cap, 64 * MIB)
    while length < limit:
        chunk = read(pos, min(1 * MIB, limit - length))
        if not chunk:
            break
        for i, byte in enumerate(chunk):
            if byte in alphabet or byte in skip:
                continue
            return length + i
        length += len(chunk)
        pos += len(chunk)
    return length


def _validate_decoded(data: bytes, signatures=None):
    """Confirm decoded bytes really are media, and say what they are."""
    if not data:
        return None
    for pos, sig in scan_buffer(data[:MAX_SIG_SPAN], signatures=signatures):
        if pos != sig.offset or sig.encoding:
            continue
        parser = PARSERS.get(sig.parser, _p_generic)
        try:
            result = parser(lambda o, n: data[o:o + n], 0, len(data))
        except (struct.error, ValueError, OverflowError, IndexError):
            return None
        if not result or not result[0]:
            return None
        return sig.category, (result[1] or sig.ext), min(result[0], len(data))
    return None


def _p_base64(read: Reader, start: int, cap: int):
    """Measure a base64 run, decode it, and only accept it if the result is
    genuinely an image. The length returned is the length of the *encoded*
    text; the caller decodes before writing."""
    run = _encoded_run(read, start, cap, B64_ALPHABET, B64_SKIP)
    if run < 64:
        return None
    decoded = decode_payload(read(start, run), "base64")
    checked = _validate_decoded(decoded)
    if not checked:
        return None
    return run, checked[1]


def _p_hex(read: Reader, start: int, cap: int):
    """The same, for hex-encoded images -- the form a metafile or a picture
    takes when embedded in an RTF document."""
    run = _encoded_run(read, start, cap, HEX_ALPHABET, HEX_SKIP)
    if run < 128:
        return None
    decoded = decode_payload(read(start, run), "hex")
    checked = _validate_decoded(decoded)
    if not checked:
        return None
    return run, checked[1]


def _p_wmf(read: Reader, start: int, cap: int):
    """Windows metafile: the placeable header records the total size in
    16-bit words."""
    hdr = read(start, 22)
    if len(hdr) < 22:
        return None
    size_words = struct.unpack("<I", hdr[6:10])[0]
    total = size_words * 2
    if not 22 <= total <= min(cap, 64 * MIB):
        return None
    return total, None


def _p_generic(read: Reader, start: int, cap: int, default: int = 8 * MIB):
    return min(cap, default), None


def _p_dds(read: Reader, start: int, cap: int):
    """A DirectDraw Surface: "DDS " then a header whose own size is fixed.

    Carved on its four magic bytes alone, random data passed: a stray DDS
    came out of pure noise about once in six 160 MiB runs. The format fixes
    dwSize at 124 and the pixel format's size at 32; noise meets both about
    once in 2^64. The length is still a guess, as it was.
    """
    head = read(start, 80)
    if len(head) < 80:
        return None
    if int.from_bytes(head[4:8], "little") != 124:
        return None
    if int.from_bytes(head[76:80], "little") != 32:
        return None
    return _p_generic(read, start, cap)


def _p_exr(read: Reader, start: int, cap: int):
    """OpenEXR: the magic, then a version field and named header attributes.

    As with DDS, four magic bytes let noise through. The version is 2 in its
    low byte with only the four defined flag bits above it, and every EXR
    header names a "channels" attribute near its start.
    """
    head = read(start, 4096)
    if len(head) < 16:
        return None
    version = int.from_bytes(head[4:8], "little")
    if version & 0xFF != 2 or version & ~0x1EFF:
        return None
    if b"channels\x00" not in head:
        return None
    return _p_generic(read, start, cap)


# Markers that legitimately follow SOI in a real JPEG. Random data matching
# FFD8FF passes the three-byte signature but almost never a valid marker
# here, which is what keeps the carver from wandering through noise.
_JPEG_FIRST_MARKERS = (
    set(range(0xE0, 0xF0))          # APP0-APP15 (JFIF, Exif, ICC, ...)
    | set(range(0xC0, 0xD0)) - {0xC8}  # SOF0-SOF15, DHT
    | {0xDB, 0xDD, 0xFE}            # DQT, DRI, COM
)


def _p_jpeg(read: Reader, start: int, cap: int):
    """Walk JPEG markers to the EOI so we never truncate at an embedded FFD9."""
    lead = read(start, 4)
    if len(lead) < 4 or lead[3] not in _JPEG_FIRST_MARKERS:
        return None
    off = start + 2
    limit = start + cap
    saw_sos = False
    segments = 0
    while off < limit:
        buf = read(off, 4)
        if len(buf) < 2:
            break
        if buf[0] != 0xFF:
            # Resync: find the next marker.
            window = read(off, 64 * KIB)
            if not window:
                break
            idx = window.find(b"\xff")
            if idx < 0:
                off += len(window)
                continue
            off += idx
            continue
        marker = buf[1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            off += 2
            continue
        if marker == 0xFF:
            off += 1
            continue
        if marker == 0xD9:  # EOI
            length = off + 2 - start
            return (length, None) if length >= _floor("image") else None
        if len(buf) < 4:
            break
        seglen = struct.unpack(">H", buf[2:4])[0]
        if seglen < 2:
            break
        off += 2 + seglen
        segments += 1
        if marker == 0xDA:  # start of scan: skip entropy-coded data
            saw_sos = True
            off = _skip_entropy(read, off, limit)
            if off is None:
                break

    # The walk did not reach an EOI. Only fall back to a trailer search if
    # the file really looked like a JPEG (valid segments and a scan) --
    # otherwise this candidate was noise and must be rejected, or a stray
    # FFD9 far downstream would turn megabytes of unrelated data into a
    # bogus "recovered photo".
    if not saw_sos or segments < 2:
        return None
    window = read(start, min(cap, 8 * MIB))
    idx = window.rfind(b"\xff\xd9")
    if idx > 0:
        return idx + 2, None
    return None


def _skip_entropy(read: Reader, off: int, limit: int) -> Optional[int]:
    """Advance past entropy-coded data to the next real marker."""
    while off < limit:
        chunk = read(off, 256 * KIB)
        n = len(chunk)
        if n < 2:  # truncated file: no further marker can exist
            return None
        i = chunk.find(b"\xff")
        while 0 <= i < n - 1:
            nxt = chunk[i + 1]
            # FF00 is a stuffed byte, FFD0-FFD7 are restart markers: both are
            # part of the entropy-coded stream, anything else is a real marker.
            if nxt != 0x00 and not (0xD0 <= nxt <= 0xD7) and nxt != 0xFF:
                return off + i
            i = chunk.find(b"\xff", i + 1)
        off += n - 1
    return None


def _p_png(read: Reader, start: int, cap: int):
    off = start + 8
    limit = start + cap
    seen_ihdr = False
    while off < limit:
        hdr = read(off, 8)
        if len(hdr) < 8:
            return None
        length = struct.unpack(">I", hdr[0:4])[0]
        ctype = hdr[4:8]
        if not ctype.isalpha():
            return None
        if ctype == b"IHDR":
            seen_ihdr = True
        if length > 512 * MIB:
            return None
        off += 12 + length
        if ctype == b"IEND":
            return (off - start, None) if seen_ihdr else None
    return None


def _p_gif(read: Reader, start: int, cap: int):
    head = read(start, 13)
    if len(head) < 13:
        return None
    flags = head[10]
    off = start + 13
    if flags & 0x80:
        off += 3 * (2 ** ((flags & 0x07) + 1))
    limit = start + cap
    frames = 0
    while off < limit:
        b = read(off, 1)
        if not b:
            return None
        block = b[0]
        if block == 0x3B:  # trailer
            length = off + 1 - start
            return (length, None) if frames else None
        if block == 0x21:  # extension
            off += 2
            off = _gif_subblocks(read, off, limit)
        elif block == 0x2C:  # image descriptor
            desc = read(off + 1, 9)
            if len(desc) < 9:
                return None
            lflags = desc[8]
            off += 10
            if lflags & 0x80:
                off += 3 * (2 ** ((lflags & 0x07) + 1))
            off += 1  # LZW minimum code size
            off = _gif_subblocks(read, off, limit)
            frames += 1
        else:
            return None
        if off is None:
            return None
    return None


def _gif_subblocks(read: Reader, off: int, limit: int) -> Optional[int]:
    while off < limit:
        b = read(off, 1)
        if not b:
            return None
        size = b[0]
        off += 1
        if size == 0:
            return off
        off += size
    return None


def _p_riff(read: Reader, start: int, cap: int):
    hdr = read(start, 12)
    if len(hdr) < 12 or hdr[0:4] != b"RIFF":
        return None
    size = struct.unpack("<I", hdr[4:8])[0]
    total = size + 8
    if total < 32 or total > cap:
        return None
    form = hdr[8:12]
    ext = {b"AVI ": "avi", b"WEBP": "webp", b"WAVE": "wav"}.get(form)
    if ext is None:
        return None
    return total, ext


# Top-level boxes that actually occur in ISO base media files. Accepting any
# four printable bytes as a box type let roughly 2% of random data through,
# which on a 128 GB image is dozens of multi-gigabyte false positives.
ISOBMFF_TOP_BOXES = {
    b"ftyp", b"styp", b"moov", b"moof", b"mdat", b"free", b"skip", b"wide",
    b"pnot", b"uuid", b"meta", b"mfra", b"sidx", b"ssix", b"prft", b"pdin",
    b"bloc", b"junk", b"idat", b"emsg", b"jP  ", b"jp2h", b"jp2c", b"cslg",
}

# A candidate is only a file if it carries content, not just headers.
ISOBMFF_CONTENT_BOXES = {b"mdat", b"moov", b"moof", b"jp2c", b"meta"}


def _p_isobmff(read: Reader, start: int, cap: int):
    off = start
    total = 0
    boxes = 0
    brand = b""
    seen = set()
    first_type = b""

    while total < cap:
        hdr = read(off, 16)
        if len(hdr) < 8:
            break
        size = struct.unpack(">I", hdr[0:4])[0]
        btype = hdr[4:8]
        if btype not in ISOBMFF_TOP_BOXES:
            break
        if size == 1:
            if len(hdr) < 16:
                break
            size = struct.unpack(">Q", hdr[8:16])[0]
        elif size == 0:
            # "extends to end of file" is only meaningful as the final box of
            # something that has already proved itself.
            if boxes < 2 or not (seen & ISOBMFF_CONTENT_BOXES):
                return None
            size = cap - total
        if size < 8 or size > cap - total:
            break

        if boxes == 0:
            first_type = btype
            if btype not in (b"ftyp", b"styp", b"jP  ", b"moov"):
                return None
            if btype == b"ftyp":
                brand = read(off + 8, 4)
            elif btype == b"moov":
                # Any four bytes spelling "moov" on the disk started a
                # candidate before this check; a real one names a movie header.
                probe = read(off + 8, 16)
                if b"mvhd" not in probe and b"cmov" not in probe:
                    return None
        seen.add(btype)
        off += size
        total += size
        boxes += 1
        if boxes > 4096:
            break

    if boxes < 2 or total < 1024:
        return None
    if not seen & ISOBMFF_CONTENT_BOXES:
        return None
    # A large result must be more than two headers agreeing with each other.
    if total > 64 * MIB and (boxes < 3 or b"mdat" not in seen):
        return None
    ext = _brand_to_ext(brand) if first_type in (b"ftyp", b"styp") else "mov"
    if first_type == b"jP  ":
        ext = "jp2"
    return min(total, cap), ext


def _p_isobmff_qt(read: Reader, start: int, cap: int):
    """A QuickTime file whose first box is not ftyp.

    Walking boxes alone is too weak here -- "mdat" occurs constantly inside
    other video -- so this additionally insists on finding a moov box, which
    is what makes the run an actual movie rather than a fragment of one.
    """
    off = start
    total = 0
    boxes = 0
    seen = set()
    while total < cap:
        hdr = read(off, 16)
        if len(hdr) < 8:
            break
        size = struct.unpack(">I", hdr[0:4])[0]
        btype = hdr[4:8]
        if btype not in ISOBMFF_TOP_BOXES:
            break
        if size == 1:
            if len(hdr) < 16:
                break
            size = struct.unpack(">Q", hdr[8:16])[0]
        elif size == 0:
            if boxes < 2 or b"moov" not in seen:
                return None
            size = cap - total
        if size < 8 or size > cap - total:
            break
        seen.add(btype)
        off += size
        total += size
        boxes += 1
        if boxes > 4096:
            break
    if boxes < 2 or total < 4096 or b"moov" not in seen:
        return None
    return min(total, cap), "mov"


def _p_jxr(read: Reader, start: int, cap: int):
    """JXR carries a TIFF-like directory offset; use it to bound the file."""
    hdr = read(start, 8)
    if len(hdr) < 8:
        return None
    ifd = struct.unpack("<I", hdr[4:8])[0]
    if not 8 <= ifd <= min(cap, 64 * MIB):
        return None
    return min(ifd + 64 * KIB, cap), None


def _p_icns(read: Reader, start: int, cap: int):
    hdr = read(start, 8)
    if len(hdr) < 8:
        return None
    total = struct.unpack(">I", hdr[4:8])[0]
    if not 16 <= total <= min(cap, 64 * MIB):
        return None
    return total, None


def _p_pcx(read: Reader, start: int, cap: int):
    """PCX has no length field, so bound it by its own image geometry."""
    hdr = read(start, 128)
    if len(hdr) < 128 or hdr[0] != 0x0A or hdr[2] != 1:
        return None
    bpp = hdr[3]
    xmin, ymin, xmax, ymax = struct.unpack("<HHHH", hdr[4:12])
    planes = hdr[65]
    bytes_per_line = struct.unpack("<H", hdr[66:68])[0]
    if bpp not in (1, 2, 4, 8) or planes not in (1, 3, 4) or not bytes_per_line:
        return None
    if xmax < xmin or ymax < ymin or (xmax - xmin) > 30000 or (ymax - ymin) > 30000:
        return None
    raw = bytes_per_line * planes * (ymax - ymin + 1)
    # Run-length encoded, so the stored size is bounded by the raw size.
    return min(128 + raw + 769, cap), None


def _p_m2ts(read: Reader, start: int, cap: int):
    """AVCHD transport stream: 192-byte packets, sync byte at offset 4."""
    off = start
    total = 0
    packets = 0
    while total < cap:
        chunk = read(off, 192 * 512)
        if len(chunk) < 192:
            break
        i = 0
        good = 0
        while i + 192 <= len(chunk) and chunk[i + 4] == 0x47:
            i += 192
            good += 1
        if good == 0:
            break
        off += i
        total += i
        packets += good
        if i < 192 * 512:
            break
    if packets < 64:
        return None
    return total, None


def _brand_to_ext(brand: bytes) -> str:
    b = brand.decode("ascii", "ignore").strip().lower()
    if b in ("qt  ", "qt"):
        return "mov"
    if b.startswith("hei") or b in ("mif1", "msf1", "heic", "heix", "hevc"):
        return "heic"
    if b.startswith("avif") or b == "avis":
        return "avif"
    if b.startswith("3g"):
        return "3gp"
    if b.startswith("jp2") or b == "jpx":
        return "jp2"
    if b in ("m4a ", "m4a"):
        return "m4a"
    if b in ("m4v", "m4v "):
        return "m4v"
    if b.startswith("crx"):
        return "cr3"            # Canon raw video/still container
    if b.startswith("mj2") or b.startswith("mjp2"):
        return "mj2"
    if b.startswith("f4v"):
        return "f4v"
    return "mp4"


def _read_vint(data: bytes, pos: int, mask_len: bool):
    """EBML variable-size integer. Returns (value, bytes_consumed)."""
    if pos >= len(data):
        return None, 0
    first = data[pos]
    if first == 0:
        return None, 0
    n = 1
    mask = 0x80
    while not (first & mask):
        mask >>= 1
        n += 1
        if n > 8:
            return None, 0
    if pos + n > len(data):
        return None, 0
    value = first & (mask - 1) if mask_len else first
    for i in range(1, n):
        value = (value << 8) | data[pos + i]
    if mask_len and value == (1 << (7 * n)) - 1:
        return -1, n  # unknown size
    return value, n


def _p_ebml(read: Reader, start: int, cap: int):
    head = read(start, 4 * KIB)
    if len(head) < 16:
        return None
    pos = 4
    hsize, n = _read_vint(head, pos, True)
    if hsize is None or hsize < 0:
        return None
    pos += n + hsize
    seg = head[pos:pos + 4]
    if seg[:4] != b"\x18\x53\x80\x67":
        return None
    pos += 4
    ssize, n = _read_vint(head, pos, True)
    pos += n
    doctype = "webm" if b"webm" in head[:1024] else "mkv"
    if ssize is None:
        return None
    if ssize < 0:  # unknown length -- live stream style
        return min(cap, MAX_VIDEO_DEFAULT), doctype
    total = pos + ssize
    if total < _floor("video") or total > cap:
        return min(cap, MAX_VIDEO_DEFAULT), doctype
    return total, doctype


def _p_asf(read: Reader, start: int, cap: int):
    off = start
    total = 0
    objects = 0
    while total < cap:
        hdr = read(off, 24)
        if len(hdr) < 24:
            break
        size = struct.unpack("<Q", hdr[16:24])[0]
        if size < 24 or size > cap:
            break
        off += size
        total += size
        objects += 1
        if objects > 4096:
            break
    if objects < 2 or total < _floor("video"):
        return None
    return min(total, cap), None


def _p_flv(read: Reader, start: int, cap: int):
    hdr = read(start, 9)
    if len(hdr) < 9:
        return None
    data_off = struct.unpack(">I", hdr[5:9])[0]
    if data_off < 9 or data_off > 1024:
        return None
    off = start + data_off
    total = data_off
    tags = 0
    last_tag_len = 0
    while total < cap:
        rec = read(off, 15)
        if len(rec) < 15:
            break
        tag_type = rec[4]
        if tag_type not in (8, 9, 18):
            break
        data_size = int.from_bytes(rec[5:8], "big")
        step = 4 + 11 + data_size
        off += step
        total += step
        tags += 1
        last_tag_len = 11 + data_size
    if tags < 2:
        return None
    # The stream ends with one final PreviousTagSize field.
    trailer = read(off, 4)
    if len(trailer) == 4 and int.from_bytes(trailer, "big") == last_tag_len:
        total += 4
    return min(total, cap), None


def _p_ogg(read: Reader, start: int, cap: int):
    off = start
    total = 0
    pages = 0
    while total < cap:
        hdr = read(off, 27)
        if len(hdr) < 27 or hdr[0:4] != b"OggS":
            break
        nsegs = hdr[26]
        table = read(off + 27, nsegs)
        if len(table) < nsegs:
            break
        page = 27 + nsegs + sum(table)
        off += page
        total += page
        pages += 1
        if pages > 2_000_000:
            break
    if pages < 2:
        return None
    ext = "ogv" if pages > 8 else "ogg"
    return min(total, cap), ext


def _p_mpegts(read: Reader, start: int, cap: int):
    off = start
    total = 0
    packets = 0
    while total < cap:
        chunk = read(off, 188 * 512)
        if len(chunk) < 188:
            break
        i = 0
        good = 0
        while i + 188 <= len(chunk) and chunk[i] == 0x47:
            i += 188
            good += 1
        if good == 0:
            break
        off += i
        total += i
        packets += good
        if i < 188 * 512:
            break
    if packets < 64:
        return None
    return total, None


def _p_mpegps(read: Reader, start: int, cap: int):
    """MPEG program/elementary stream: follow pack headers as far as they run."""
    off = start
    total = 0
    packs = 0
    while total < cap:
        chunk = read(off, 1 * MIB)
        if len(chunk) < 4:
            break
        consumed = 0
        while consumed + 4 <= len(chunk):
            if chunk[consumed:consumed + 3] != b"\x00\x00\x01":
                break
            sid = chunk[consumed + 3]
            if sid == 0xBA:      # pack header
                consumed += 14
                if consumed <= len(chunk):
                    stuffing = chunk[consumed - 1] & 0x07 if consumed <= len(chunk) else 0
                    consumed += stuffing
                packs += 1
            elif sid == 0xB9:    # end code
                consumed += 4
                total += consumed
                return (total, None) if packs > 2 else None
            elif sid in (0xB3, 0xB8, 0x00, 0xB5, 0xB2):  # video ES headers
                consumed += 4
                packs += 1
            elif 0xBB <= sid <= 0xFE:
                if consumed + 6 > len(chunk):
                    break
                plen = struct.unpack(">H", chunk[consumed + 4:consumed + 6])[0]
                consumed += 6 + plen
            else:
                break
        if consumed == 0:
            break
        off += consumed
        total += consumed
        if consumed < 4:
            break
    if packs < 8 or total < _floor("video"):
        return None
    return min(total, cap), None


def _p_bmp(read: Reader, start: int, cap: int):
    hdr = read(start, 30)
    if len(hdr) < 30:
        return None
    size = struct.unpack("<I", hdr[2:6])[0]
    dib = struct.unpack("<I", hdr[14:18])[0]
    if dib not in (12, 40, 52, 56, 64, 108, 124):
        return None
    if size < 30 or size > min(cap, 512 * MIB):
        return None
    if struct.unpack("<I", hdr[6:10])[0] != 0:  # reserved must be zero
        return None
    return size, None


def _p_ico(read: Reader, start: int, cap: int):
    """An icon directory, validated by following it.

    The four-byte header (00 00 01 00) is far too common in compiled code to
    trust on its own -- it matched thousands of times per binary. What makes
    an icon an icon is that its directory entries point at real image data,
    so this follows the first one and insists on finding a PNG header or a
    plausible DIB header there.
    """
    hdr = read(start, 6)
    if len(hdr) < 6:
        return None
    count = struct.unpack("<H", hdr[4:6])[0]
    if not 1 <= count <= 64:
        return None
    entries = read(start + 6, 16 * count)
    if len(entries) < 16 * count:
        return None
    end = 6 + 16 * count
    first_offset = None
    for i in range(count):
        e = entries[i * 16:(i + 1) * 16]
        width, height, colors = e[0], e[1], e[2]
        planes = struct.unpack("<H", e[4:6])[0]
        size = struct.unpack("<I", e[8:12])[0]
        offset = struct.unpack("<I", e[12:16])[0]
        if size == 0 or size > 16 * MIB or offset < end:
            return None
        # width/height of 0 means 256; colors is a palette count, and planes
        # is 0 or 1 for an icon (a cursor stores a hotspot here instead).
        if colors not in (0, 2, 4, 8, 16, 16 + 240) and colors < 2:
            return None
        if planes > 4 and e[4:6] != b"\x00\x00":
            pass          # cursors put the hotspot here, so do not reject
        if first_offset is None:
            first_offset = offset
        end = max(end, offset + size)
    if end > cap:
        return None

    # Follow the first entry: PNG-compressed icons start with the PNG magic,
    # everything else with a DIB header whose size is one of a known set.
    data = read(start + first_offset, 8)
    if len(data) < 8:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        dib = struct.unpack("<I", data[0:4])[0]
        if dib not in (12, 40, 52, 56, 64, 108, 124):
            return None
    return end, None


def _p_psd(read: Reader, start: int, cap: int):
    hdr = read(start, 26)
    if len(hdr) < 26 or hdr[4:6] != b"\x00\x01":
        return None
    return min(cap, 256 * MIB), None


def _p_swf(read: Reader, start: int, cap: int):
    hdr = read(start, 8)
    if len(hdr) < 8:
        return None
    version = hdr[3]
    if not 1 <= version <= 50:      # no SWF version has ever been outside this
        return None
    size = struct.unpack("<I", hdr[4:8])[0]
    if size < 32 or size > min(cap, 64 * MIB):
        return None
    return size, None


def _p_emf(read: Reader, start: int, cap: int):
    hdr = read(start, 48)
    if len(hdr) < 48:
        return None
    if struct.unpack("<I", hdr[0:4])[0] != 1:          # iType: EMR_HEADER
        return None
    if hdr[40:44] != b" EMF":                          # dSignature
        return None
    header_size = struct.unpack("<I", hdr[4:8])[0]
    size = struct.unpack("<I", hdr[8:12])[0]           # nBytes: whole file
    if header_size < 80 or header_size > size:
        return None
    if size < 88 or size > min(cap, 64 * MIB):
        return None
    return size, None


_TIFF_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4,
                   10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}


def _p_tiff(read: Reader, start: int, cap: int):
    # A TIFF header six bytes after "Exif\0\0" is the metadata block inside a
    # JPEG, not an image. Carving it produces a tiny .tif that is really the
    # camera tags of the photograph it sits in.
    if start >= 6 and read(start - 6, 6) == b"Exif\x00\x00":
        return None
    """Walk IFDs and strip/tile offsets to find the true extent."""
    hdr = read(start, 8)
    if len(hdr) < 8:
        return None
    endian = "<" if hdr[0:2] == b"II" else ">" if hdr[0:2] == b"MM" else None
    if endian is None:
        return None
    version = struct.unpack(endian + "H", hdr[2:4])[0]
    if version not in (42, 43):
        return None
    if version == 43:  # BigTIFF -- extent parsing not implemented
        return min(cap, 256 * MIB), None

    ifd_off = struct.unpack(endian + "I", hdr[4:8])[0]
    end = 8
    seen: set[int] = set()
    raw_ext = None
    guard = 0
    while ifd_off and ifd_off not in seen and guard < 64:
        guard += 1
        seen.add(ifd_off)
        if ifd_off >= cap:
            break
        cnt_raw = read(start + ifd_off, 2)
        if len(cnt_raw) < 2:
            break
        count = struct.unpack(endian + "H", cnt_raw)[0]
        if count == 0 or count > 4096:
            break
        block = read(start + ifd_off + 2, count * 12 + 4)
        if len(block) < count * 12 + 4:
            break
        end = max(end, ifd_off + 2 + count * 12 + 4)
        strip_offsets: list[int] = []
        strip_counts: list[int] = []
        for i in range(count):
            e = block[i * 12:(i + 1) * 12]
            tag, ftype, n = struct.unpack(endian + "HHI", e[0:8])
            tsize = _TIFF_TYPE_SIZE.get(ftype, 0)
            payload = tsize * n
            if payload > 4:
                voff = struct.unpack(endian + "I", e[8:12])[0]
                end = max(end, voff + payload)
                values = _tiff_values(read, start, voff, ftype, n, endian)
            else:
                values = _tiff_inline(e[8:12], ftype, n, endian)
            if tag in (273, 324):      # StripOffsets / TileOffsets
                strip_offsets = values
            elif tag in (279, 325):    # StripByteCounts / TileByteCounts
                strip_counts = values
            elif tag == 330:           # SubIFDs (raw formats)
                for sub in values:
                    if sub and sub not in seen and len(seen) < 64:
                        seen.add(sub)
            elif tag == 271 and raw_ext is None:  # Make
                pass
        for o, c in zip(strip_offsets, strip_counts):
            end = max(end, o + c)
        nxt = struct.unpack(endian + "I", block[count * 12:count * 12 + 4])[0]
        ifd_off = nxt
    if end < 64 or end > cap:
        return None
    # Distinguish common raw formats by their leading bytes.
    lead = read(start, 16)
    if lead[8:10] == b"CR":
        raw_ext = "cr2"
    elif b"NIKON" in read(start, 128):
        raw_ext = "nef"
    return end, raw_ext


def _tiff_values(read: Reader, start, voff, ftype, n, endian) -> list[int]:
    if ftype not in (3, 4):
        return []
    n = min(n, 4096)
    size = 2 if ftype == 3 else 4
    data = read(start + voff, size * n)
    if len(data) < size * n:
        return []
    fmt = endian + ("H" if ftype == 3 else "I") * n
    return list(struct.unpack(fmt, data))


def _tiff_inline(raw: bytes, ftype, n, endian) -> list[int]:
    try:
        if ftype == 3 and n <= 2:
            return list(struct.unpack(endian + "H" * n, raw[: 2 * n]))
        if ftype == 4 and n == 1:
            return list(struct.unpack(endian + "I", raw))
    except struct.error:
        return []
    return []


def _p_footer(read: Reader, start: int, cap: int, *, footer: bytes,
              max_size: int, header_len: int):
    """Length parser for a user-supplied header/footer signature -- the same
    rule the FAT32 browser's carve used (retired in v2026.11.319): run to the first footer, else to
    the signature's own max_size."""
    cap = min(cap, max_size)
    if not footer:
        return cap, None
    pos = start + header_len
    end = start + cap
    window = 4 * MIB
    while pos < end:
        buf = read(pos, min(window + len(footer), end - pos))
        if not buf:
            break
        idx = buf.find(footer)
        if idx >= 0:
            return (pos + idx + len(footer)) - start, None
        pos += window
    return cap, None


def signatures_from_fat32(extra_signatures, category: str = "other") -> list:
    """Convert fat32_core.FileSignature objects -- including the ones the GUI
    Custom Signatures tab builds via signature_from_hex() -- into carver
    signatures, registering a footer parser for each."""
    out = []
    for i, fsig in enumerate(extra_signatures or []):
        header = getattr(fsig, "header", b"")
        if not header:
            continue
        ext = (getattr(fsig, "ext", "") or "bin").lstrip(".").lower()
        cat = "image" if ext in IMAGE_EXTS else "video" if ext in VIDEO_EXTS else category
        key = f"custom_{i}_{ext}"
        PARSERS[key] = functools.partial(
            _p_footer,
            footer=getattr(fsig, "footer", None) or b"",
            max_size=getattr(fsig, "max_size", 64 * MIB),
            header_len=len(header),
        )
        out.append(Signature(key, header, 0, cat, ext, key))
    return out


# Artifacts rather than media: carried only when the run asks for them,
# because a disk holds far more of these than it holds pictures and an
# examiner looking for media does not want a hundred thousand of them.
ARTIFACT_SIGNATURES = [
    Signature("sqlite", b"SQLite format 3\x00", 0, "other", "sqlite", "sqlite"),
    Signature("bplist", b"bplist00", 0, "other", "plist", "bplist"),
    Signature("zip_artifact", b"PK\x03\x04", 0, "other", "zip", "zip"),
    Signature("pdf_artifact", b"%PDF-", 0, "other", "pdf", "pdf"),
]

WEAK_PARSERS = {"generic", "psd", "dds", "exr"}  # length is a guess, not parsed structure
# Parsers whose length is the extent of units each checked in turn (a sync
# byte every packet, a start code every pack): where they stop, the stream
# stops, including at the end of the image.
STREAM_PARSERS = {"mpegts", "m2ts", "mpegps"}

PARSERS: dict[str, Callable] = {
    "isobmff_qt": _p_isobmff_qt,
    "icns": _p_icns,
    "jxr": _p_jxr,
    "pcx": _p_pcx,
    "m2ts": _p_m2ts,
    "base64": _p_base64,
    "hex": _p_hex,
    "wmf": _p_wmf,
    "jpeg": _p_jpeg,
    "png": _p_png,
    "gif": _p_gif,
    "riff": _p_riff,
    "isobmff": _p_isobmff,
    "ebml": _p_ebml,
    "asf": _p_asf,
    "flv": _p_flv,
    "ogg": _p_ogg,
    "mpegts": _p_mpegts,
    "mpegps": _p_mpegps,
    "bmp": _p_bmp,
    "ico": _p_ico,
    "psd": _p_psd,
    "swf": _p_swf,
    "emf": _p_emf,
    "tiff": _p_tiff,
    "generic": _p_generic,
    "dds": _p_dds,
    "exr": _p_exr,
}


def parse_size(text: str) -> int:
    m = re.fullmatch(r"(?i)\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*", text)
    if not m:
        raise argparse.ArgumentTypeError(f"bad size: {text}")
    mult = {"": 1, "k": KIB, "m": MIB, "g": GIB, "t": 1024 * GIB}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def ts(unix: Optional[int]) -> str:
    if not unix:
        return ""
    try:
        return _dt.datetime.fromtimestamp(unix, _dt.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def magic_matched(head: bytes, signatures: Optional[list] = None) -> bool:
    """Whether the header itself identifies a known format, as opposed to the
    file name merely claiming one."""
    for pos, sig in scan_buffer(head[:MAX_SIG_SPAN], signatures=signatures):
        if pos == sig.offset:
            return True
        if pos > 64:
            break
    return False


def _p_aiff(read: Reader, start: int, cap: int):
    """FORM....AIFF / AIFC: a size in the header, like RIFF but big-endian."""
    head = read(start, 12)
    if len(head) < 12 or head[:4] != b"FORM" or head[8:12] not in (b"AIFF", b"AIFC"):
        return None
    size = struct.unpack(">I", head[4:8])[0] + 8
    return (size, None) if 32 <= size <= min(cap, 2 * GIB) else None


def _p_caf(read: Reader, start: int, cap: int):
    """Apple Core Audio, walked: a CAF carries no total length, only chunks."""
    if read(start, 4) != b"caff":
        return None
    pos, end = 8, 8
    while pos + 12 <= cap:
        chunk = read(start + pos, 12)
        if len(chunk) < 12 or not all(32 <= b < 127 for b in chunk[:4]):
            break
        size = struct.unpack(">q", chunk[4:12])[0]
        if size == -1:
            return cap, None
        if size < 0 or size > cap:
            break
        pos += 12 + size
        end = pos
    return (end, None) if end > 8 else None


# Total bytes per frame, header byte included -- which is how the standard
# tabulates them. Adding one for the header counts it twice and walks the
# file at the wrong stride.
AMR_FRAME_BYTES = (13, 14, 16, 18, 20, 21, 27, 32, 6, 0, 0, 0, 0, 0, 0, 1)
AMR_WB_FRAME_BYTES = (18, 24, 33, 37, 41, 47, 51, 59, 61, 6, 0, 0, 0, 0, 1, 1)


def _p_amr(read: Reader, start: int, cap: int):
    """AMR and AMR-WB: phones record calls and voice notes in this.

    There is no length field anywhere, so the end is found by walking frames
    whose size comes from the mode in each frame header, until one does not
    make sense.
    """
    head = read(start, 9)
    if head[:6] == b"#!AMR\n":
        table, pos = AMR_FRAME_BYTES, 6
    elif head[:9] == b"#!AMR-WB\n":
        table, pos = AMR_WB_FRAME_BYTES, 9
    else:
        return None
    frames = 0
    first_mode = None
    while pos < cap:
        byte = read(start + pos, 1)
        if not byte:
            break
        # Bit 7 and the low two bits of a frame header are reserved and zero.
        if byte[0] & 0x83:
            break
        mode = (byte[0] >> 3) & 0x0F
        size = table[mode]
        if size == 0:
            break
        if first_mode is None:
            first_mode = mode
        elif mode != first_mode and mode not in (8, 15):
            # A recording keeps its mode, apart from comfort-noise (8) and
            # no-data (15) frames. A random byte after the end of the file
            # passes the reserved-bit test often enough to swallow one more
            # frame; requiring the mode to match stops that, and stops the
            # carved file differing from the original by a frame -- which
            # would change its hash and lose a hash-set match.
            break
        pos += size
        frames += 1
    return (min(pos, cap), None) if frames >= 5 else None


def _p_silk(read: Reader, start: int, cap: int):
    """SILK, which is what Skype and some messengers record voice in."""
    if read(start, 9) != b"#!SILK_V3":
        return None
    pos, blocks = 9, 0
    while pos + 2 <= cap:
        raw = read(start + pos, 2)
        if len(raw) < 2:
            break
        length = struct.unpack("<h", raw)[0]
        if length == -1:
            pos += 2
            break
        if not 0 < length <= 1024:
            break
        pos += 2 + length
        blocks += 1
    return (min(pos, cap), None) if blocks >= 5 else None


MP3_BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192,
                      224, 256, 320, 0)
MP3_BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128,
                      144, 160, 0)
MP3_RATES = {0: (11025, 12000, 8000), 2: (22050, 24000, 16000),
             3: (44100, 48000, 32000)}


def _mp3_frame_length(header: bytes):
    """One MPEG audio frame's length, or None if this is not a frame header."""
    if len(header) < 4 or header[0] != 0xFF or (header[1] & 0xE0) != 0xE0:
        return None
    version = (header[1] >> 3) & 0x03
    layer = (header[1] >> 1) & 0x03
    if version == 1 or layer == 0:
        return None
    bitrate_index = (header[2] >> 4) & 0x0F
    sample_index = (header[2] >> 2) & 0x03
    if bitrate_index in (0, 15) or sample_index == 3:
        return None
    table = MP3_BITRATES_V1_L3 if version == 3 else MP3_BITRATES_V2_L3
    bitrate = table[bitrate_index] * 1000
    rate = MP3_RATES.get(version, MP3_RATES[3])[sample_index]
    if not bitrate or not rate:
        return None
    samples = 1152 if (version == 3 and layer == 1) else 576
    return int(samples / 8 * bitrate / rate) + ((header[2] >> 1) & 0x01)


def _p_mp3(read: Reader, start: int, cap: int):
    """MP3, by walking its frames: nothing in the file says how long it is.

    An ID3v2 tag at the front is skipped by its own declared size; an ID3v1
    tag at the end is taken with the file.
    """
    head = read(start, 10)
    pos = 0
    if head[:3] == b"ID3" and len(head) >= 10:
        pos = 10 + ((head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14
                    | (head[8] & 0x7F) << 7 | (head[9] & 0x7F))
    elif len(head) < 2 or head[0] != 0xFF or (head[1] & 0xE0) != 0xE0:
        return None
    frames = 0
    while pos < cap:
        header = read(start + pos, 4)
        if len(header) < 4:
            break
        length = _mp3_frame_length(header)
        if length is None or length < 24:
            if read(start + pos, 3) == b"TAG":
                pos += 128
            break
        pos += length
        frames += 1
        if frames > 2_000_000:
            break
    return (min(pos, cap), None) if frames >= 10 else None


def _p_sqlite(read: Reader, start: int, cap: int):
    """A SQLite database, sized from its own header.

    Page size and page count are both in the first 32 bytes, so the file's
    length is exact rather than guessed.
    """
    head = read(start, 32)
    if len(head) < 32 or head[:16] != b"SQLite format 3\x00":
        return None
    raw = struct.unpack(">H", head[16:18])[0]
    page_size = 65536 if raw == 1 else raw
    if page_size < 512 or page_size & (page_size - 1):
        return None
    pages = struct.unpack(">I", head[28:32])[0]
    if not 1 <= pages <= 1 << 28:
        return None
    size = page_size * pages
    return (size, None) if 512 <= size <= min(cap, 64 * GIB) else None


def _p_bplist(read: Reader, start: int, cap: int):
    """A binary property list, sized from its trailer.

    The trailer is the last 32 bytes and records where the offset table
    starts and how many entries it has, which gives the end exactly.
    """
    if read(start, 8) != b"bplist00":
        return None
    # The structure is only readable from the end, so the end has to be
    # found first: scan for a trailer whose numbers describe this file.
    for size in _plist_candidates(read, start, cap):
        return size, None
    return None


def _plist_candidates(read: Reader, start: int, cap: int):
    limit = min(cap, 64 * MIB)
    window = read(start, limit)
    if len(window) < 40:
        return
    # A trailer sits at the very end; try the plausible ends in order.
    for end in range(min(len(window), limit), 39, -1):
        trailer = window[end - 32:end]
        offset_size, ref_size = trailer[6], trailer[7]
        if not (1 <= offset_size <= 8 and 1 <= ref_size <= 8):
            continue
        count = struct.unpack(">Q", trailer[8:16])[0]
        table_at = struct.unpack(">Q", trailer[24:32])[0]
        if count == 0 or count > 1 << 24:
            continue
        if table_at + count * offset_size != end - 32:
            continue
        yield end
        return


def _p_zip(read: Reader, start: int, cap: int):
    """A zip, sized from its end-of-central-directory record."""
    if read(start, 4) != b"PK\x03\x04":
        return None
    limit = min(cap, 512 * MIB)
    window = read(start, limit)
    at = window.rfind(b"PK\x05\x06")
    if at < 0 or at + 22 > len(window):
        return None
    comment = struct.unpack("<H", window[at + 20:at + 22])[0]
    end = at + 22 + comment
    return (end, None) if 30 <= end <= limit else None


def _p_pdf(read: Reader, start: int, cap: int):
    """A PDF, ending at its last end-of-file marker."""
    if read(start, 5) != b"%PDF-":
        return None
    limit = min(cap, 512 * MIB)
    window = read(start, limit)
    at = window.rfind(b"%%EOF")
    if at < 0:
        return None
    end = at + 5
    while end < len(window) and window[end] in (0x0D, 0x0A):
        end += 1
    return (end, None) if end > 100 else None


# Registered here rather than in the dictionary above, which is defined
# before these functions exist.
PARSERS.update({
    "aiff": _p_aiff, "caf": _p_caf, "amr": _p_amr, "silk": _p_silk,
    "mp3": _p_mp3,
    "sqlite": _p_sqlite, "bplist": _p_bplist, "zip": _p_zip, "pdf": _p_pdf,
})


def header_is_blank(head: bytes) -> bool:
    """True when a header carries no information: empty, or one byte value
    repeated. A run of 0x00 is what a sparse or unallocated extent reads as,
    and it is never the start of a real media file."""
    if not head or len(head) < 16:
        return True
    return len(set(head[:64])) <= 1


# What the brand in an ftyp box means for where the file belongs.
ISOBMFF_IMAGE_EXTS = {"heic", "heif", "avif", "jp2", "jxl", "mj2", "cr3"}
ISOBMFF_AUDIO_EXTS = {"m4a", "m4b", "m4p"}


def _isobmff_category(head: bytes) -> tuple:
    """Read the brand and say what kind of file this actually is."""
    brand = head[8:12] if len(head) >= 12 else b""
    ext = _brand_to_ext(brand) if brand else "mp4"
    if ext in ISOBMFF_IMAGE_EXTS:
        return "image", ext
    if ext in ISOBMFF_AUDIO_EXTS:
        return "audio", ext
    return "video", ext


def sniff_category(head: bytes, name: str,
                   signatures: Optional[list] = None) -> tuple[Optional[str], Optional[str]]:
    """Classify a file by its bytes first, then by its extension.

    Content takes precedence deliberately. Classifying on the extension alone
    let a deleted directory entry named SOMETHING.MOV, whose recorded size was
    corrupt and whose clusters read as zeros, be extracted as a multi-gigabyte
    "video" made entirely of 0x00. A name is a claim; the header is evidence.
    """
    for pos, sig in scan_buffer(head[:MAX_SIG_SPAN], signatures=signatures):
        if pos == sig.offset:
            if sig.parser == "isobmff" and sig.magic == b"ftyp":
                # mp4, HEIC, AVIF and M4A all start with the same ftyp box.
                # The brand inside it says which, and getting it wrong puts
                # every iPhone photograph in the videos folder, where it is
                # filtered as a video, scanned for frames and reported as a
                # clip. The carver already read the brand; everything else
                # that classifies a file -- the file system engine, the
                # folder engine, a zip's members, a deleted MFT record --
                # came through here and did not.
                return _isobmff_category(head)
            return sig.category, sig.ext
        if pos > 64:
            break

    # No signature matched. The extension may still be right -- a truncated or
    # partly overwritten file keeps its name -- but not if the header says
    # nothing at all.
    if header_is_blank(head):
        return None, None
    # A Mac writes "._name" beside every file it copies to a volume that has
    # no place for its extended attributes -- every SD card and USB stick a
    # Mac has touched. It is an AppleDouble (or AppleSingle) header, never
    # the picture its name ends in, and was stored as one by extension.
    if head[:4] in (b"\x00\x05\x16\x07", b"\x00\x05\x16\x00"):
        return None, None
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in IMAGE_EXTS:
        return "image", ext
    if ext in VIDEO_EXTS:
        return "video", ext
    if ext in AUDIO_EXTS:
        return "audio", ext
    return None, None
