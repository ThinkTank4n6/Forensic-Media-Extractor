"""
signatures.py -- Section 2 -- signature definitions: what a file's first bytes look like.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable, Iterator, Optional, Sequence



# ==========================================================================
# SECTION 2 -- Signature definitions
# ==========================================================================
IMAGE_EXTS = {
    "jpg", "jpeg", "jpe", "jfif", "png", "gif", "bmp", "dib", "tif", "tiff",
    "webp", "heic", "heif", "avif", "jp2", "j2k", "jpf", "jpx", "psd", "ico",
    "cur", "tga", "pcx", "svg", "cr2", "cr3", "nef", "arw", "dng", "orf",
    "rw2", "raf", "srw", "pef", "3fr", "erf", "mrw", "x3f", "hdr", "exr",
    "wmf", "emf", "eps", "jxl", "qoi", "mng", "icns", "jxr", "pcx", "cr3",
    # GPU textures: iOS app-switcher snapshots (SplashBoard/Snapshots) are
    # .ktx files, and are pictures of what was on screen. Kept by name only:
    # there is no carving signature, because the carve has one judge of a
    # file's length (mediacarve) and it does not know KTX.
    "ktx",
}

AUDIO_EXTS = {
    "wav", "mp3", "m4a", "aac", "flac", "ogg", "oga", "opus", "amr", "wma",
    "aiff", "aif", "caf", "3ga",
}

VIDEO_EXTS = {
    "mp4", "m4v", "mov", "qt", "3gp", "3g2", "avi", "mkv", "webm", "wmv",
    "asf", "flv", "f4v", "mpg", "mpeg", "mpe", "m1v", "m2v", "mp2", "mts",
    "m2ts", "ts", "tod", "vob", "ogv", "ogm", "rm", "rmvb", "divx", "mxf",
    "swf", "amv", "mjpg", "mjpeg", "dv", "m4p", "mpv", "yuv", "gifv", "insv",
}


@dataclass(frozen=True)
class Signature:
    key: str
    magic: bytes
    offset: int          # where magic sits relative to file start
    category: str        # "image" | "video" | "other"
    ext: str
    parser: str          # key into PARSERS
    regex: Optional[bytes] = None   # structural pattern instead of a literal
    encoding: Optional[str] = None  # payload is encoded; decode before writing


SIGNATURES: list[Signature] = [
    # ---- images -----------------------------------------------------------
    Signature("jpeg", b"\xff\xd8\xff", 0, "image", "jpg", "jpeg"),
    Signature("png", b"\x89PNG\r\n\x1a\n", 0, "image", "png", "png"),
    Signature("gif87a", b"GIF87a", 0, "image", "gif", "gif"),
    Signature("gif89a", b"GIF89a", 0, "image", "gif", "gif"),
    Signature("bmp", b"BM", 0, "image", "bmp", "bmp"),
    Signature("tiff_le", b"II\x2a\x00", 0, "image", "tif", "tiff"),
    Signature("tiff_be", b"MM\x00\x2a", 0, "image", "tif", "tiff"),
    Signature("bigtiff_le", b"II\x2b\x00", 0, "image", "tif", "tiff"),
    Signature("bigtiff_be", b"MM\x00\x2b", 0, "image", "tif", "tiff"),
    Signature("webp", b"WEBP", 8, "image", "webp", "riff"),
    Signature("jp2", b"\x00\x00\x00\x0cjP  \r\n\x87\n", 0, "image", "jp2", "isobmff"),
    Signature("psd", b"8BPS", 0, "image", "psd", "psd"),
    Signature("ico", b"\x00\x00\x01\x00", 0, "image", "ico", "ico"),
    Signature("cur", b"\x00\x00\x02\x00", 0, "image", "cur", "ico"),
    Signature("qoi", b"qoif", 0, "image", "qoi", "generic"),
    Signature("jxl", b"\x00\x00\x00\x0cJXL \r\n\x87\n", 0, "image", "jxl", "isobmff"),
    Signature("exr", b"\x76\x2f\x31\x01", 0, "image", "exr", "exr"),
    Signature("dds", b"DDS ", 0, "image", "dds", "dds"),
    # An EMF is identified by dSignature " EMF" at offset 40, not by the
    # iType field before it. Keying off iType (01 00 00 00) matched 22,847
    # times in a single 7.6 MB binary, because those four bytes are
    # everywhere in compiled code.
    Signature("emf", b" EMF", 40, "image", "emf", "emf"),
    Signature("mng", b"\x8aMNG\r\n\x1a\n", 0, "image", "mng", "png"),
    Signature("icns", b"icns", 0, "image", "icns", "icns"),
    # JXR/HD Photo. The three-byte form (II BC) matched random data seven
    # times in 200 MB and carved megabytes each time; the version byte makes
    # it a four-byte magic, and the parser bounds it properly.
    Signature("jxr", b"II\xbc\x01", 0, "image", "jxr", "jxr"),
    Signature("pcx", b"\x0a\x05\x01", 0, "image", "pcx", "pcx"),

    # Base64-encoded images. These do not look like images on disk at all --
    # they are text, in HTML, email bodies, JSON, app caches and web storage --
    # so no byte-level signature for JPEG or PNG will ever match them. A
    # forensic suite with dedicated base64 plugins finds tens of thousands of
    # pictures this way that a conventional carver cannot see.
    Signature("jpeg_b64", b"/9j/", 0, "image", "jpg", "base64", encoding="base64"),
    Signature("png_b64", b"iVBORw0KGgo", 0, "image", "png", "base64",
              encoding="base64"),
    Signature("gif_b64_87", b"R0lGODdh", 0, "image", "gif", "base64",
              encoding="base64"),
    Signature("gif_b64_89", b"R0lGODlh", 0, "image", "gif", "base64",
              encoding="base64"),

    # Windows metafiles, including the hex-encoded form embedded in RTF.
    Signature("wmf", b"\xd7\xcd\xc6\x9a", 0, "image", "wmf", "wmf"),
    Signature("wmf_rtf", b"d7cdc69a", 0, "image", "wmf", "hex", encoding="hex"),
    Signature("jpeg_rtf", b"ffd8ff", 0, "image", "jpg", "hex", encoding="hex"),

    # AOL ART: still turns up in long-lived collections and old profiles.
    Signature("art", b"\x4a\x47\x04\x0e", 0, "image", "art", "generic"),
    Signature("art2", b"\x4a\x47\x03\x0e", 0, "image", "art", "generic"),

    # ---- audio ------------------------------------------------------------
    Signature("wav", b"WAVE", 8, "audio", "wav", "riff"),
    # Audio a phone actually produces: voice notes, call recordings, music.
    Signature("aiff", b"FORM", 0, "audio", "aiff", "aiff"),
    Signature("caf", b"caff", 0, "audio", "caf", "caf"),
    Signature("amr", b"#!AMR\n", 0, "audio", "amr", "amr"),
    Signature("amr_wb", b"#!AMR-WB\n", 0, "audio", "amr", "amr"),
    Signature("silk", b"#!SILK_V3", 0, "audio", "silk", "silk"),
    Signature("mp3_id3", b"ID3", 0, "audio", "mp3", "mp3"),
    Signature("mp3_frame", b"\xff\xfb", 0, "audio", "mp3", "mp3"),
    Signature("mp3_frame2", b"\xff\xf3", 0, "audio", "mp3", "mp3"),
    Signature("mp3_frame3", b"\xff\xf2", 0, "audio", "mp3", "mp3"),

    # ---- video ------------------------------------------------------------
    # ISO base media: mp4/mov/3gp/heic/avif all start with a 'ftyp' box.
    Signature("isobmff", b"ftyp", 4, "video", "mp4", "isobmff"),
    # Standard MIDI: a header chunk of a fixed length, then track chunks.
    Signature("midi", b"MThd\x00\x00\x00\x06", 0, "audio", "mid", "midi"),
    Signature("mov_moov", b"moov", 4, "video", "mov", "isobmff"),
    # Plenty of QuickTime files put mdat, wide, free, skip or pnot first and
    # carry no ftyp at all -- camera and screen-capture output especially.
    # Matching only ftyp and moov silently skipped every one of them.
    Signature("mov_mdat", b"mdat", 4, "video", "mov", "isobmff_qt"),
    Signature("mov_wide", b"wide", 4, "video", "mov", "isobmff_qt"),
    Signature("mov_skip", b"skip", 4, "video", "mov", "isobmff_qt"),
    Signature("mov_pnot", b"pnot", 4, "video", "mov", "isobmff_qt"),
    Signature("avi", b"AVI ", 8, "video", "avi", "riff"),
    Signature("matroska", b"\x1a\x45\xdf\xa3", 0, "video", "mkv", "ebml"),
    Signature(
        "asf",
        b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c",
        0, "video", "wmv", "asf",
    ),
    Signature("flv", b"FLV\x01", 0, "video", "flv", "flv"),
    Signature("mpeg_ps", b"\x00\x00\x01\xba", 0, "video", "mpg", "mpegps"),
    Signature("mpeg_es", b"\x00\x00\x01\xb3", 0, "video", "m2v", "mpegps"),
    Signature("ogg", b"OggS", 0, "video", "ogv", "ogg"),
    Signature("realmedia", b".RMF", 0, "video", "rm", "generic"),
    Signature(
        "mxf",
        b"\x06\x0e\x2b\x34\x02\x05\x01\x01\x0d\x01\x02\x01\x01\x02",
        0, "video", "mxf", "generic",
    ),
    Signature("swf", b"FWS", 0, "video", "swf", "swf"),
    Signature("swf_c", b"CWS", 0, "video", "swf", "swf"),
    Signature("dv", b"\x1f\x07\x00\x3f", 0, "video", "dv", "generic"),
    # MPEG-TS: 0x47 sync bytes every 188 octets -- verify three in a row.
    Signature(
        "mpegts", b"", 0, "video", "ts", "mpegts",
        regex=rb"\x47[\s\S]{187}\x47[\s\S]{187}\x47",
    ),
    # AVCHD camcorder output: the same transport stream with a four-byte
    # timestamp on every packet, so the stride is 192 rather than 188.
    Signature(
        "m2ts", b"", 4, "video", "m2ts", "m2ts",
        regex=rb"\x47[\s\S]{191}\x47[\s\S]{191}\x47",
    ),
]

SIG_BY_KEY = {s.key: s for s in SIGNATURES}


MAX_SIG_SPAN = 512  # longest pattern (MPEG-TS span) + slack; chunk overlap
_TS_STRIDE = 188 * 8  # after a transport-stream hit, resume this far ahead
# The packet size of each structural (regex) signature. Both were tested at
# 188 whatever the signature said, so an AVCHD .m2ts -- 192-byte packets, a
# four-byte timestamp before each sync byte -- was never found by the carve
# at all: its sync bytes are 192 apart, and the 188 test missed every one.
_STREAM_PACKET = {"mpegts": 188, "m2ts": 192}


def _ts_positions(buf: bytes, first_only: bool = False, packet: int = 188):
    """Where a transport stream starts, without a Python loop: three sync
    bytes `packet` apart (188 for MPEG-TS, 192 for M2TS).

    Returns None when NumPy is absent, so the caller falls back rather
    than losing the signature altogether.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    if len(buf) < 2 * packet + 1:
        return []

    whole = np.frombuffer(buf, dtype=np.uint8)
    # The three sync bytes a packet needs, tested for every position at
    # once instead of one position at a time.
    body = whole[:len(whole) - 2 * packet]
    matched = ((body == 0x47)
               & (whole[packet:len(whole) - packet] == 0x47)
               & (whole[2 * packet:] == 0x47))
    at = np.flatnonzero(matched)
    if at.size == 0:
        return []
    if first_only:
        return [int(at[0])]

    # Keep one start per stream rather than one per packet: consecutive
    # packets are the same file, and the old walk skipped a stride ahead
    # for the same reason.
    skip = packet * 8
    kept, last = [], -skip
    for position in at.tolist():
        if position - last >= skip:
            kept.append(position)
            last = position
    return kept


def scan_buffer(buf: bytes, first_only: bool = False,
                signatures: Optional[list] = None) -> list[tuple[int, Signature]]:
    """Find every signature occurrence in a buffer, ordered by position.

    Uses one bytes.find pass per signature rather than a regex alternation.
    CPython's find is a memchr/two-way search and runs roughly fifty times
    faster than a compiled alternation of the same literals -- the difference
    between scanning a terabyte image in hours instead of days.
    """
    hits: list[tuple[int, Signature]] = []
    for sig in (SIGNATURES if signatures is None else signatures):
        if sig.regex is not None:          # structural pattern (MPEG-TS)
            # A transport stream is recognized by one byte repeating every
            # 188. One byte matches once every 256 in anything, so walking
            # the matches in Python means millions of iterations over a
            # disk image -- measured at 77% of a whole run. The same test
            # done with array operations is one pass and no Python loop.
            packet = _STREAM_PACKET.get(sig.parser, 188)
            found = _ts_positions(buf, first_only, packet)
            if found is None:                # NumPy absent: the old walk
                i = buf.find(b"\x47")
                while i >= 0:
                    if (i + 2 * packet + 1 <= len(buf)
                            and buf[i + packet] == 0x47
                            and buf[i + 2 * packet] == 0x47):
                        hits.append((i, sig))
                        if first_only:
                            break
                        i += packet * 8
                        i = buf.find(b"\x47", i)
                        continue
                    i = buf.find(b"\x47", i + 1)
            else:
                hits.extend((at, sig) for at in found)
            continue
        magic = sig.magic
        if not magic:
            continue
        i = buf.find(magic)
        while i >= 0:
            hits.append((i, sig))
            if first_only:
                break
            i = buf.find(magic, i + 1)
    hits.sort(key=lambda h: h[0])
    return hits
