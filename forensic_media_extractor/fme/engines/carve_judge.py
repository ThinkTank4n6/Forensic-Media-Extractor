"""
carve_judge.py -- one opinion on where a carved file starts and ends.

The carve finds a header and has to say how many bytes after it belong to
the file. Get that wrong long and the file swallows whatever follows it; get
it wrong short and a picture loses its bottom half. Measured on v2026.11.273:
a JPEG cut off before its end marker was taken to the *last* FFD9 within
8 MiB, which ran 63 KB through a fake header and over the RIFF header of an
intact AVI behind it. The AVI was never reported as a video; its frames came
out as twenty loose JPEGs.

The judge is mediacarve (``fme/vendor/mediacarve.py``, MIT, vendored
verbatim with its provenance in ``vendored.json``). It walks each format's
own structure before it claims a byte, and every length it gives says how it
was arrived at:

    header    the file's own header recorded the length
    parsed    the length came from walking the file's structure to its end
    capped    the end was not confirmed: the length is a ceiling, not a fact

And one the carve adds when it stores the file, not one this module returns:

    short     the length was judged one of the ways above, but the source
              could not be read to the end of it, so less was stored; the
              row's note says how much, and the offset where reading stopped

That label goes into the manifest's ``bounded`` column, because it is what
tells an examiner how far to trust a carved file's length -- and so its
hashes.

**How a hit is judged** (``judge``):

1. mediacarve confirms the length (header or parsed): that length is used,
   whatever FME's own parser thought. One judge, not two.
2. mediacarve cannot confirm the end, but the header is real -- FME's parser
   accepted it, or mediacarve validated the header and capped the length.
   This is a partial file: truncated, or overwritten part-way. An examiner
   wants those, so it is kept, but cut off at the next header mediacarve
   validates, and labelled capped. A partial can therefore never hide a file
   that mediacarve can see.
3. Neither accepts it: not a file.

**Where the cut is looked for.** Not from the first byte: a truncated camera
photograph nearly always carries a complete EXIF thumbnail inside its APP1
segment, and cutting there would throw away the main picture's scan data.
The search starts after the file's own declared header segments -- for a
JPEG, after its start-of-scan header, which the segment lengths step over
the thumbnail to reach. And a file that runs off the end of the image is not
cut at all: nothing lies after it to protect, and cutting a truncated AVI at
its first MJPEG frame would leave only its header.

**What is not judged here.** Signatures mediacarve has no reader for --
QuickTime files without an ftyp, MPEG streams, base64 and hex runs, PSD,
custom signatures -- keep FME's own parsers, and their labels come from those
parsers (``parsed``, or ``capped`` where the length was a guess). The
``JUDGED`` table below is the whole list, and ``tests/test_carve_judge.py``
checks it.

Called from ``media_core._carve_candidate`` only, which the single-worker
carve, the parallel workers and the parent's re-scans all share.
"""

from __future__ import annotations

import os
import struct
import sys
from typing import Optional

__version__ = "1.0.0"

from fme import resources

VENDOR_DIR = resources.vendor_dir()

# FME signature key -> the mediacarve header family that reads it. Anything
# not listed stays with FME's own parser.
JUDGED = {
    "jpeg": "jpeg",
    "png": "png",
    "gif87a": "gif", "gif89a": "gif",
    "bmp": "bmp",
    "tiff_le": "tiff", "tiff_be": "tiff",
    "ico": "ico", "cur": "ico",
    "webp": "riff", "wav": "riff", "avi": "riff",
    "isobmff": "bmff",
    "matroska": "mkv",
    "ogg": "ogg",
    "amr": "amr", "amr_wb": "amr",
    "silk": "silk",
    "aiff": "iff",
    "caf": "caf",
    "mp3_id3": "mp3", "mp3_frame": "mp3", "mp3_frame2": "mp3",
    "mp3_frame3": "mp3",
}

# Bytes read to find the header at a candidate's own offset. Longer than any
# mediacarve signature, BMP's 26-byte pattern included.
_WINDOW = 64

# The furthest a JPEG's header segments are walked looking for its scan.
_JPEG_HEADER_LIMIT = 4 << 20

_module = None
_error = ""


def module():
    """mediacarve, imported once.

    Not optional: the carve's lengths depend on it, and a carve that quietly
    fell back to the old judgment would give different files from the same
    image depending on what happened to be installed. A build without it
    fails loudly, here, rather than carving.
    """
    global _module, _error
    if _module is not None:
        return _module
    if VENDOR_DIR and VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    try:
        import mediacarve  # noqa: PLC0415 - vendored, imported by name
    except Exception as exc:  # noqa: BLE001 - said, then raised
        _error = f"{type(exc).__name__}: {exc}"
        raise RuntimeError(
            "the carve's length judge (fme/vendor/mediacarve.py) could not be "
            f"loaded ({_error}); this build is incomplete and will not carve"
        ) from exc
    _module = mediacarve
    return _module


def status() -> dict:
    """What `fme doctor` and the tests report."""
    try:
        mc = module()
        return {"available": True, "version": mc.__version__,
                "families": sorted(set(JUDGED.values()))}
    except RuntimeError:
        return {"available": False, "version": "", "error": _error,
                "families": []}


class _Stream:
    """An FME ImageReader seen as the seekable stream mediacarve reads.

    Read-only, like everything that touches evidence. Reads past the end
    come back short, which mediacarve treats as the end of the stream.
    """

    def __init__(self, reader):
        self._reader = reader
        self._size = reader.size
        self._pos = 0

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            self._pos = offset
        elif whence == os.SEEK_CUR:
            self._pos += offset
        else:
            self._pos = self._size + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if self._pos >= self._size or self._pos < 0:
            return b""
        if size is None or size < 0:
            size = self._size - self._pos
        data = self._reader.read_clamped(self._pos, size)
        self._pos += len(data)
        return data


def _caps(mc, max_size: int) -> dict:
    return {kind: min(cap, max_size) for kind, cap in mc.DEFAULT_CAPS.items()}


def measure(reader, start: int, max_size: int):
    """mediacarve's reading of a header at exactly `start`, or None.

    Uses the public scan over a window that begins at `start`: every header
    in the window is measured, and only the one at `start` is answered.
    ``nested`` is set so an earlier hit in the window cannot shadow it.
    """
    mc = module()
    stream = _Stream(reader)
    end = min(start + _WINDOW, reader.size)
    if end <= start:
        return None
    found = None
    for hit in mc.carve(stream, kinds=mc.MEDIA_KINDS, caps=_caps(mc, max_size),
                        start=start, end=end, nested=True):
        if hit.offset == start:
            found = hit
            break
    return found


def next_header(reader, lo: int, hi: int, max_size: int) -> Optional[int]:
    """The first offset in [lo, hi) where mediacarve validates a media header."""
    if hi <= lo:
        return None
    mc = module()
    for hit in mc.carve(_Stream(reader), kinds=mc.MEDIA_KINDS,
                        caps=_caps(mc, max_size), start=lo, end=hi, nested=True):
        return hit.offset
    return None


# Start-of-frame markers, ITU-T T.81 Table B.1: SOF0-SOF15 less DHT, JPG, DAC.
_JPEG_SOF = frozenset(set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC})


def jpeg_header(reader, start: int) -> tuple:
    """Walk a JPEG's header segments: (where its scan data begins, whole).

    Each marker segment carries its own length, so this steps over APP1 --
    and the EXIF thumbnail inside it -- without looking at what it holds.
    `whole` is True only when the walk met a frame header and then a scan
    header of the length T.81 fixes (6 + 2 x Ns, Ns from 1 to 4) without
    once leaving the segment chain. A photograph cut off part-way still has
    that: what is lost is scan data, not the first few hundred bytes. Random
    data that happens to begin FF D8 FF does not.
    """
    pos = start + 2
    limit = start + _JPEG_HEADER_LIMIT
    saw_frame = False
    while pos < limit:
        head = reader.read_clamped(pos, 5)
        if len(head) < 2 or head[0] != 0xFF:
            return pos, False
        marker = head[1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xD8, 0xD9) or len(head) < 4:
            return pos, False
        length = struct.unpack(">H", head[2:4])[0]
        if length < 2:
            return pos, False
        if marker in _JPEG_SOF:
            saw_frame = True
        elif marker == 0xDA:
            whole = (saw_frame and len(head) == 5 and 1 <= head[4] <= 4
                     and length == 6 + 2 * head[4])
            return pos + 2 + length, whole
        pos += 2 + length
    return pos, False


def own_header_end(reader, start: int, family: str) -> int:
    """The end of the part of a file that is certainly its own header.

    A header found before this point belongs to the file (a thumbnail in a
    JPEG's APP1), so it is no reason to cut. After it, any header mediacarve
    validates is the start of something else.
    """
    if family == "jpeg":
        return jpeg_header(reader, start)[0]
    # Every other family's own signature sits in its first 16 bytes; FME's
    # weak-parser truncation has always skipped the same span.
    return start + 16


def judge(reader, sig_key: str, start: int, total: int, max_size: int,
          fme_length: Optional[int], fme_ext: Optional[str]):
    """Decide one hit of a signature in JUDGED: (length, ext, bounded), or
    None when it is not a file.

    `fme_length` / `fme_ext` are what FME's own parser made of the hit (None
    if it rejected it), already within FME's caps. The extension is FME's
    where it had one, so folders and names do not change under examiners;
    mediacarve's otherwise.
    """
    family = JUDGED[sig_key]
    hit = measure(reader, start, max_size)
    if hit is not None and hit.bounded != "capped":
        return hit.length, fme_ext or hit.ext.lstrip("."), hit.bounded

    # The end was not confirmed. A partial, if anything at all -- and only
    # if its header is real. For a JPEG that means a header that walks to a
    # valid scan: measured on 256 MiB of random data, FME's parser, which
    # resyncs past broken segments, accepted four "JPEGs" of 3.9 to 12 MB.
    if family == "jpeg" and not jpeg_header(reader, start)[1]:
        return None
    if fme_length:
        length = fme_length
    elif hit is not None:
        length = hit.length
    else:
        return None          # neither reader accepts it: not a file

    ext = fme_ext or (hit.ext.lstrip(".") if hit is not None else None)
    runs_off_end = start + length >= total
    if not runs_off_end:
        lo = max(own_header_end(reader, start, family), start + 1)
        cut = next_header(reader, lo, start + length, max_size)
        if cut is not None:
            length = cut - start
    return length, ext, "capped"
