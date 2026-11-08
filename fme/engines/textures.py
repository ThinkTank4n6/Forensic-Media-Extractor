"""
textures.py -- two Apple picture formats ordinary decoders cannot show.

**CgBI PNG.** Xcode rewrites the PNGs in an iOS app bundle into a variant
with a `CgBI` chunk before `IHDR`, raw DEFLATE with no zlib header, BGRA
byte order and premultiplied alpha. It keeps the `.png` name and signature,
so it is recovered and hashed like any PNG -- and then Pillow and every
browser show it as black or not at all. Decoded here in pure Python.

**KTX 1 and Apple `AAPL` textures.** GPU textures: app-switcher snapshots
under `SplashBoard/Snapshots`, and textures inside apps. Usually ASTC block
data, often LZFSE-compressed first. The block decoder (`texture2ddecoder`)
is a Rust extension that can crash the process on malformed data, so a
texture is only ever decoded in a child process (`fme/engines/isolated.py`,
worker "texture"); a crash costs that one file its thumbnail.

Either way the result is a *rendering* for review -- a thumbnail, a preview,
a perceptual hash. The recovered file itself is never altered and its
hashes are the file's own.

Adapted from GLEAPP's imaging.py (MIT; fme/vendor/LICENSE-gleapp). Two
changes from it, both about files from a seized device being hostile:

* **Sizes are bounded before anything is allocated.** GLEAPP inflated the
  whole IDAT stream without limit and built the image with
  `Image.fromarray`, which skips Pillow's decompression-bomb check. A
  few-kilobyte PNG claiming 50,000 x 50,000 pixels would exhaust memory in
  the examiner's interface. Here the header's dimensions are checked against
  `MAX_PIXELS`, and inflation stops at exactly the bytes the header implies.
* **Every failure is a reason, not None.** `TextureError` says what was
  wrong, so a tile that cannot be drawn can say why.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import zlib
from typing import Optional

__version__ = "1.0.0"

PNG_SIG = b"\x89PNG\r\n\x1a\n"
KTX1_MAGIC = b"\xabKTX 11\xbb\r\n\x1a\n"
AAPL_MAGIC = b"AAPL\r\n\x1a\n"

# The same ceiling Pillow applies to anything it opens, so a picture too big
# for Pillow is too big here too. 89 million pixels is Pillow's warning level;
# its hard stop is twice that.
MAX_PIXELS = 89_478_485
TEXTURE_TIME_LIMIT = 60          # seconds a child may spend on one texture


class TextureError(ValueError):
    """A file this module recognized but could not render, and why."""


def kind_of(head: bytes) -> Optional[str]:
    """'cgbi', 'ktx' or 'aapl' from a file's first 16 bytes, else None."""
    if head[:8] == PNG_SIG and head[12:16] == b"CgBI":
        return "cgbi"
    if head[:12] == KTX1_MAGIC:
        return "ktx"
    if head[:8] == AAPL_MAGIC:
        return "aapl"
    return None


def kind_of_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return kind_of(fh.read(16))
    except OSError:
        return None


# ---------------------------------------------------------------------------
# CgBI PNG
# ---------------------------------------------------------------------------
# Adam7 interlace passes: (x0, y0, x-step, y-step)
_ADAM7 = [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
          (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]


def _unfilter(np, buf, start, rows, stride, bpp):
    """Reverse PNG filtering for `rows` scanlines from `buf[start]`."""
    if start + rows * (stride + 1) > len(buf):
        raise TextureError("the PNG's pixel data is truncated")
    out = np.zeros((rows, stride), np.uint8)
    prior = np.zeros(stride, np.int64)
    off = start
    for y in range(rows):
        ftype = buf[off]
        cur = np.frombuffer(buf, np.uint8, count=stride, offset=off + 1).astype(np.int64)
        off += 1 + stride
        if ftype == 0:
            pass
        elif ftype == 1:                                   # Sub
            cur = np.cumsum(cur.reshape((-1, bpp)), axis=0).reshape(-1) & 0xFF
        elif ftype == 2:                                   # Up
            cur = (cur + prior) & 0xFF
        elif ftype in (3, 4):                              # Average, Paeth
            c = cur.tolist()
            p = prior.tolist()
            for x in range(stride):
                left = c[x - bpp] if x >= bpp else 0
                if ftype == 3:
                    c[x] = (c[x] + ((left + p[x]) >> 1)) & 0xFF
                else:
                    up = p[x]
                    ul = p[x - bpp] if x >= bpp else 0
                    est = left + up - ul
                    pa, pb, pc = abs(est - left), abs(est - up), abs(est - ul)
                    pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                    c[x] = (c[x] + pred) & 0xFF
            cur = np.array(c, np.int64)
        else:
            raise TextureError(f"unknown PNG filter type {ftype}")
        out[y] = cur
        prior = cur
    return out


def _inflate_bounded(stream: bytes, limit: int) -> bytes:
    """Raw DEFLATE, possibly several streams back to back (Apple's iDOT
    chunk splits it), stopping at `limit` bytes of output."""
    out = bytearray()
    while stream and len(out) < limit:
        dec = zlib.decompressobj(-zlib.MAX_WBITS)
        out += dec.decompress(stream, limit - len(out))
        if len(out) >= limit or not dec.eof:
            break
        stream = dec.unused_data
    return bytes(out)


def decode_cgbi(path: str):
    """A CgBI PNG as an RGB or RGBA PIL image. Raises TextureError."""
    import numpy as np
    from PIL import Image

    with open(path, "rb") as fh:
        data = fh.read()
    if kind_of(data[:16]) != "cgbi":
        raise TextureError("not an Apple CgBI PNG")
    pos = 8
    width = height = depth = color = interlace = -1
    idat = bytearray()
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR" and len(body) >= 13:
            width, height, depth, color, _c, _f, interlace = struct.unpack(
                ">IIBBBBB", body[:13])
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
    if width <= 0 or height <= 0:
        raise TextureError("the PNG has no usable IHDR")
    if width * height > MAX_PIXELS:
        raise TextureError(f"{width} x {height} is larger than this will "
                           f"decode ({MAX_PIXELS:,} pixels)")
    if depth != 8 or color not in (2, 6) or interlace not in (0, 1):
        raise TextureError(f"unsupported CgBI PNG (depth {depth}, color type "
                           f"{color}, interlace {interlace})")
    channels = 4 if color == 6 else 3

    if interlace == 0:
        expected = height * (width * channels + 1)
    else:
        expected = 0
        for x0, y0, dx, dy in _ADAM7:
            pw = (width - x0 + dx - 1) // dx
            ph = (height - y0 + dy - 1) // dy
            if pw > 0 and ph > 0:
                expected += ph * (pw * channels + 1)
    try:
        raw = _inflate_bounded(bytes(idat), expected)
    except zlib.error as exc:
        raise TextureError(f"the PNG's compressed data is damaged ({exc})") from None

    if interlace == 0:
        px = _unfilter(np, raw, 0, height, width * channels, channels).reshape(
            (height, width, channels)).copy()
    else:
        px = np.zeros((height, width, channels), np.uint8)
        off = 0
        for x0, y0, dx, dy in _ADAM7:
            pw = (width - x0 + dx - 1) // dx
            ph = (height - y0 + dy - 1) // dy
            if pw <= 0 or ph <= 0:
                continue
            sub = _unfilter(np, raw, off, ph, pw * channels, channels)
            off += ph * (pw * channels + 1)
            px[y0:height:dy, x0:width:dx] = sub.reshape((ph, pw, channels))

    px[:, :, [0, 2]] = px[:, :, [2, 0]]               # BGR(A) -> RGB(A)
    if channels == 4:
        # Un-premultiply: stored color is color * alpha / 255.
        alpha = px[:, :, 3].astype(np.uint32)
        nz = alpha > 0
        for ch in range(3):
            c = px[:, :, ch].astype(np.uint32)
            c[nz] = np.minimum(255, (c[nz] * 255 + alpha[nz] // 2) // alpha[nz])
            px[:, :, ch] = c.astype(np.uint8)
        return Image.fromarray(px, "RGBA")
    return Image.fromarray(px, "RGB")


# ---------------------------------------------------------------------------
# KTX 1 / AAPL GPU textures
# ---------------------------------------------------------------------------
# glInternalFormat -> (texture2ddecoder function suffix, extra arguments)
_FORMATS = {
    0x8C00: ("pvrtc", (False,)), 0x8C02: ("pvrtc", (False,)),
    0x8C01: ("pvrtc", (True,)), 0x8C03: ("pvrtc", (True,)),
    0x9274: ("etc2", ()), 0x9275: ("etc2", ()),
    0x9278: ("etc2a8", ()), 0x9279: ("etc2a8", ()),
    0x9270: ("eacr", ()), 0x9272: ("eacrg", ()),
    0x8D64: ("etc1", ()),
    0x83F0: ("bc1", ()), 0x83F1: ("bc1", ()), 0x83F3: ("bc3", ()),
    0x8E8C: ("bc7", ()), 0x8E8D: ("bc7", ()),
}
_ASTC = [(4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6), (8, 8),
         (10, 5), (10, 6), (10, 8), (10, 10), (12, 10), (12, 12)]
for _i, _bs in enumerate(_ASTC):
    _FORMATS[0x93B0 + _i] = ("astc", _bs)     # COMPRESSED_RGBA_ASTC_*
    _FORMATS[0x93D0 + _i] = ("astc", _bs)     # COMPRESSED_SRGB8_ALPHA8_ASTC_*

_RGBA8 = (0x8058, 0x1908)
_RGB8 = (0x8051, 0x1907)


def _min_bytes(codec: str, w: int, h: int, extra) -> int:
    """The least a level-0 payload can be. Shorter data is rejected before the
    block decoder sees it: handed too little, it reads past the end."""
    def blocks(bw, bh):
        return ((w + bw - 1) // bw) * ((h + bh - 1) // bh)
    if codec == "astc":
        return blocks(*extra) * 16
    if codec == "pvrtc":
        return max(32, (w * h) // (4 if extra and extra[0] else 2) // 8 * 8)
    if codec in ("etc1", "etc2", "eacr", "bc1"):
        return blocks(4, 4) * 8
    if codec in ("etc2a8", "eacrg", "bc3", "bc7"):
        return blocks(4, 4) * 16
    return 0


def _lzfse(buf: bytes) -> bytes:
    try:
        import liblzfse
    except ImportError:
        raise TextureError("this texture is LZFSE-compressed, which needs the "
                           "pyliblzfse package (pip install pyliblzfse)") from None
    try:
        return liblzfse.decompress(buf)
    except Exception as exc:  # noqa: BLE001 - a C extension's own errors
        raise TextureError(f"its LZFSE data is damaged ({exc})") from None


def _parse_aapl(b: bytes):
    """Apple's chunked texture: (glInternalFormat, width, height, payload)."""
    pos, n = 8, len(b)
    glinternal = w = h = 0
    dpos = dsize = 0
    compressed = False
    while pos + 8 <= n:
        size = struct.unpack_from("<I", b, pos)[0]
        ident = b[pos + 4:pos + 8]
        body = pos + 8
        if size <= 0 or body + size > n + 8:
            break
        if ident == b"HEAD" and body + 44 <= n:
            vals = struct.unpack_from("<11I", b, body)
            glinternal, w, h = vals[4], vals[6], vals[7]
        elif ident == b"LZFS":
            dpos, dsize, compressed = body + 4, size - 4, True
        elif ident in (b"astc", b"ASTC"):
            dpos, dsize, compressed = body + 4, size - 4, False
        pos = body + size
    if not (glinternal and w and h and dsize > 0):
        raise TextureError("the AAPL texture has no usable header or data")
    raw = bytes(b[dpos:dpos + dsize])
    return glinternal, w, h, (_lzfse(raw) if compressed else raw)


def _parse_ktx1(b: bytes):
    """KTX 1: (glInternalFormat, width, height, level-0 payload)."""
    if len(b) < 64:
        raise TextureError("the KTX header is truncated")
    (endian, _t, _ts, _f, glinternal, _b, w, h, _d, _a, faces, _l,
     kvbytes) = struct.unpack_from("<13I", b, 12)
    if endian != 0x04030201:
        raise TextureError("big-endian KTX is not handled")
    if not w or not h or faces > 6:
        raise TextureError(f"implausible KTX dimensions ({w} x {h}, {faces} faces)")
    start = 64 + kvbytes
    kv = b[64:start]
    payload = bytes(b[start:])
    if b"Compression_APPLE" in kv:
        if payload[12:15] == b"bvx":
            return glinternal, w, h, _lzfse(payload[12:])
        return glinternal, w, h, payload[4:]
    size = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    return glinternal, w, h, (payload[4:4 + size] if size else payload[4:])


def decode_texture_here(path: str):
    """A KTX or AAPL texture as an RGB PIL image, in this process.

    **Only the isolated worker calls this.** The block decoder can crash the
    process; everything else goes through `render_texture`.
    """
    from PIL import Image

    with open(path, "rb") as fh:
        b = fh.read()
    kind = kind_of(b[:16])
    if kind == "aapl":
        glinternal, w, h, data = _parse_aapl(b)
    elif kind == "ktx":
        glinternal, w, h, data = _parse_ktx1(b)
    else:
        raise TextureError("not a KTX or AAPL texture")
    if w * h > MAX_PIXELS:
        raise TextureError(f"{w} x {h} is larger than this will decode")

    if glinternal in _RGBA8 or glinternal in _RGB8:
        channels = 4 if glinternal in _RGBA8 else 3
        if len(data) < w * h * channels:
            raise TextureError("the texture's pixel data is truncated")
        mode = "RGBA" if channels == 4 else "RGB"
        return Image.frombytes(mode, (w, h), data[:w * h * channels]).convert("RGB")

    spec = _FORMATS.get(glinternal)
    if spec is None:
        raise TextureError(f"GPU texture format 0x{glinternal:04X} is not handled")
    name, extra = spec
    need = _min_bytes(name, w, h, extra)
    if need and len(data) < need:
        raise TextureError(f"the texture is truncated ({len(data):,} of "
                           f"{need:,} bytes)")
    try:
        import texture2ddecoder as t2d
    except ImportError:
        raise TextureError("decoding this texture needs the texture2ddecoder "
                           "package (pip install texture2ddecoder)") from None
    decode = getattr(t2d, f"decode_{name}", None)
    if decode is None:
        raise TextureError(f"texture2ddecoder has no {name} decoder")
    rgba = decode(data, w, h, *extra)
    if not rgba or len(rgba) < w * h * 4:
        raise TextureError("the block decoder returned too little data")
    return Image.frombytes("RGBA", (w, h), rgba, "raw", "BGRA").convert("RGB")


def texture_worker(args: list) -> int:
    """The child's side: decode args[0], write a PNG to args[1]."""
    try:
        decode_texture_here(args[0]).save(args[1], "PNG")
    except TextureError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    return 0


def render_texture(path: str):
    """A KTX or AAPL texture as a loaded PIL image, decoded in a child process.

    Raises TextureError with the child's reason, or with "crashed" when the
    decoder took the child down -- the case isolation exists for.
    """
    from PIL import Image

    from fme.engines import isolated

    handle, out = tempfile.mkstemp(suffix=".png", prefix="fme-texture-")
    os.close(handle)
    try:
        outcome = isolated.run("texture", [os.path.abspath(path), out],
                               TEXTURE_TIME_LIMIT)
        if not outcome.ok:
            reason = outcome.detail
            if outcome.status == "failed" and ": " in reason:
                reason = reason.split(": ", 1)[1]
            raise TextureError(f"could not render this texture: {reason}")
        with Image.open(out) as img:
            img.load()
            return img.copy()
    finally:
        try:
            os.remove(out)
        except OSError:
            pass
