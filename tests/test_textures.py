"""Apple pictures a browser cannot draw are rendered, not shown black.

A CgBI PNG -- what Xcode turns every PNG in an iOS app bundle into -- keeps
the .png name and signature, so it is recovered and hashed like any PNG and
then drawn as black or nothing. A KTX texture (app-switcher snapshots) is
not drawn at all. Both are built here byte by byte, the way the device
writes them, and checked three ways: the decoder gets the pixels right, a
hostile file cannot exhaust memory, and the interface an examiner uses
actually shows them -- which, for a PNG, means not trusting the extension.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

try:
    import numpy as np
    from PIL import Image
except ImportError:
    print("(not checked: Pillow and NumPy are needed)")
    sys.exit(0)

from fme.engines import imaging, textures

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "textures_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "src"))


def chunk(kind: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def make_cgbi(rgba: np.ndarray, split: bool = False) -> bytes:
    """A CgBI PNG as Xcode writes one: premultiplied, BGRA, every filter
    type in turn, raw DEFLATE -- optionally split into two streams (iDOT)."""
    h, w, _ = rgba.shape
    px = rgba.astype(np.uint32)
    a = px[:, :, 3:4]
    px[:, :, :3] = (px[:, :, :3] * a + 127) // 255           # premultiply
    px = px.astype(np.uint8)[:, :, [2, 1, 0, 3]]              # RGBA -> BGRA
    stride, bpp = w * 4, 4
    raw = bytearray()
    prior = [0] * stride
    for y in range(h):
        line = px[y].reshape(-1).tolist()
        ftype = y % 5
        out = []
        for x in range(stride):
            left = line[x - bpp] if x >= bpp else 0
            up = prior[x]
            ul = prior[x - bpp] if x >= bpp else 0
            pred = (0, left, up, (left + up) >> 1, paeth(left, up, ul))[ftype]
            out.append((line[x] - pred) & 0xFF)
        raw.append(ftype)
        raw += bytes(out)
        prior = line
    def deflate(data):
        c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
        return c.compress(data) + c.flush()
    if split:
        half = (h // 2) * (stride + 1)
        idat = deflate(bytes(raw[:half])) + deflate(bytes(raw[half:]))
    else:
        idat = deflate(bytes(raw))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (textures.PNG_SIG + chunk(b"CgBI", b"\x50\x00\x20\x06")
            + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


rng = np.random.default_rng(20261106)
picture = rng.integers(0, 256, size=(37, 53, 4), dtype=np.uint8)
picture[:, :, 3] = 255                    # opaque: exact round trip
translucent = picture.copy()
translucent[:, :, 3] = rng.integers(40, 256, size=(37, 53), dtype=np.uint8)

print("a CgBI PNG decodes to its own pixels")
for label, source, split in (("opaque, one stream", picture, False),
                             ("opaque, split by iDOT", picture, True),
                             ("translucent", translucent, False)):
    path = os.path.join(ROOT, f"cgbi-{label.split(',')[0]}-{split}.png")
    with open(path, "wb") as fh:
        fh.write(make_cgbi(source, split))
    got = np.asarray(textures.decode_cgbi(path))
    if source is picture:
        right = np.array_equal(got, source)
    else:
        # Premultiplying loses precision where alpha is low; the decoder
        # cannot get it back, and a match within that loss is correct.
        err = np.abs(got.astype(int) - source.astype(int))[:, :, :3]
        right = bool((err <= (255 // source[:, :, 3:4].astype(int)) + 1).all())
    ok &= right
    print(f"  {label:24} exact{'' if source is picture else ' within premultiply loss'}: {right}")

# Pillow on its own opens it without complaint and draws the wrong thing:
# this is why the bytes, not the extension, decide.
plain = os.path.join(ROOT, "plain-view.png")
with open(plain, "wb") as fh:
    fh.write(make_cgbi(picture))
try:
    with Image.open(plain) as im:
        im.load()
        pillow_right = np.array_equal(np.asarray(im.convert("RGBA")), picture)
except Exception:  # noqa: BLE001
    pillow_right = False
print(f"  (Pillow alone gets it right: {pillow_right} -- the reason this exists)")
with imaging.open_image(plain) as im:
    through = np.array_equal(np.asarray(im), picture)
ok &= through and imaging.needs_rendering(plain)
print(f"  imaging.open_image, which every thumbnail and hash uses, gets it right: {through}")

print("\na hostile PNG cannot exhaust memory")
bomb = os.path.join(ROOT, "bomb.png")
ihdr = struct.pack(">IIBBBBB", 50000, 50000, 8, 6, 0, 0, 0)
zeros = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
with open(bomb, "wb") as fh:
    fh.write(textures.PNG_SIG + chunk(b"CgBI", bytes(4)) + chunk(b"IHDR", ihdr)
             + chunk(b"IDAT", zeros.compress(bytes(1 << 20)) + zeros.flush())
             + chunk(b"IEND", b""))
t0 = time.monotonic()
try:
    textures.decode_cgbi(bomb)
    said = ""
except textures.TextureError as exc:
    said = str(exc)
refused = "larger than" in said and time.monotonic() - t0 < 2
ok &= refused
print(f"  a {os.path.getsize(bomb):,}-byte file claiming 50,000 x 50,000 is refused "
      f"before anything is allocated: {refused}")

# Inflation stops at what the header promises, however much follows.
lying = os.path.join(ROOT, "lying.png")
ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0)
z = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
with open(lying, "wb") as fh:
    fh.write(textures.PNG_SIG + chunk(b"CgBI", bytes(4)) + chunk(b"IHDR", ihdr)
             + chunk(b"IDAT", z.compress(bytes(200 << 20)) + z.flush())
             + chunk(b"IEND", b""))
bounded = len(textures._inflate_bounded(
    open(lying, "rb").read()[8 + 16 + 25 + 8:-12 - 4], 4 * 17)) == 68
ok &= bounded
print(f"  200 MB of compressed padding inflates only the 68 bytes the header "
      f"implies: {bounded}")

truncated = os.path.join(ROOT, "truncated.png")
with open(truncated, "wb") as fh:
    fh.write(make_cgbi(picture)[:-200])
try:
    textures.decode_cgbi(truncated)
    named = False
except textures.TextureError as exc:
    named = bool(str(exc))
ok &= named
print(f"  a truncated one is refused with a reason: {named}")


def make_ktx(rgba: np.ndarray, glinternal=0x8058, payload=None) -> bytes:
    h, w, _ = rgba.shape
    data = rgba.tobytes() if payload is None else payload
    head = struct.pack("<13I", 0x04030201, 0x1401, 1, 0x1908, glinternal,
                       0x1908, w, h, 0, 0, 1, 1, 0)
    return textures.KTX1_MAGIC + head + struct.pack("<I", len(data)) + data


print("\na KTX texture is decoded, in a separate process")
ktx = os.path.join(ROOT, "src", "snapshot.ktx")
with open(ktx, "wb") as fh:
    fh.write(make_ktx(picture))
with imaging.open_image(ktx) as im:
    got = np.asarray(im)
right = np.array_equal(got, picture[:, :, :3])
ok &= right
print(f"  an uncompressed RGBA texture, through open_image: {right}")

astc = os.path.join(ROOT, "astc.ktx")
with open(astc, "wb") as fh:
    fh.write(make_ktx(picture, glinternal=0x93B0,
                      payload=bytes(((53 + 3) // 4) * ((37 + 3) // 4) * 16)))
try:
    import texture2ddecoder  # noqa: F401
    have_t2d = True
except ImportError:
    have_t2d = False
try:
    with imaging.open_image(astc) as im:
        decoded_astc = im.size == (53, 37)
    said = ""
except textures.TextureError as exc:
    decoded_astc, said = False, str(exc)
if have_t2d:
    ok &= decoded_astc
    print(f"  an ASTC texture decodes: {decoded_astc}")
else:
    named = "texture2ddecoder" in said
    ok &= named
    # Two lines, not one: the runner lists "not checked" lines in its summary
    # but skips any ending in ": True", which it reads as a result -- so a
    # combined line hid this gap from a green run.
    print("  not checked: texture2ddecoder is not installed, so no ASTC "
          "texture was decoded here")
    print(f"  without it, the reason given names the package: {named}")

short = os.path.join(ROOT, "short.ktx")
with open(short, "wb") as fh:
    fh.write(make_ktx(picture, glinternal=0x93B0, payload=bytes(10)))
try:
    textures.decode_texture_here(short)
    caught = False
except textures.TextureError as exc:
    caught = "truncated" in str(exc)
ok &= caught
print(f"  a texture too short for its size is refused before the block decoder "
      f"sees it: {caught}")

print("\nthrough the program, as an examiner sees it")
cgbi_src = os.path.join(ROOT, "src", "AppIcon60x60@2x.png")
with open(cgbi_src, "wb") as fh:
    fh.write(make_cgbi(picture))
run = os.path.join(ROOT, "run")
done = subprocess.run([sys.executable, "-m", "fme", "extract", "-i",
                       os.path.join(ROOT, "src"), "-o", run],
                      capture_output=True, text=True, cwd=PROJECT, timeout=600)
with open(os.path.join(run, "manifest.json"), encoding="utf-8") as fh:
    files = json.load(fh)["files"]
by_ext = {f.get("ext"): f for f in files}
import hashlib


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


recovered = "png" in by_ext and "ktx" in by_ext
ok &= recovered
print(f"  both recovered from a folder (the .ktx kept by its name): {recovered}")
if recovered:
    untouched = (by_ext["png"]["sha256"] == sha(cgbi_src)
                 and by_ext["ktx"]["sha256"] == sha(ktx))
    ok &= untouched
    print(f"  their hashes are the files' own -- rendering alters nothing: {untouched}")

    from fme.web import media_gui as gui
    client = gui.app.test_client()
    headers = {"X-FME-Request": "1"}
    opened = client.post("/api/cases/open", json={"out_dir": run},
                         headers=headers).get_json()
    job = opened.get("job_id") or gui.LAST_JOB.get("id")
    png_rel, ktx_rel = by_ext["png"]["out_path"], by_ext["ktx"]["out_path"]
    detail = client.get(f"/api/details?job={job}&file={png_rel}").get_json()
    asks = detail.get("convertible") and not detail.get("previewable")
    ok &= bool(asks)
    print(f"  the details pane asks for a rendering of the CgBI .png rather than "
          f"showing it raw: {bool(asks)}")
    for label, rel in (("CgBI PNG", png_rel), ("KTX texture", ktx_rel)):
        for route in ("thumb", "preview"):
            reply = client.get(f"/api/{route}?job={job}&file={rel}")
            drawn = reply.status_code == 200 and reply.mimetype == "image/jpeg"
            if drawn:
                import io
                with Image.open(io.BytesIO(reply.data)) as im:
                    # A black square would pass a status check. The picture's
                    # mean brightness is that of random bytes, about 127.
                    mean = float(np.asarray(im.convert("L")).mean())
                drawn = 90 < mean < 165
            ok &= drawn
            print(f"  {label:12} /api/{route:8} draws the picture, not a black "
                  f"square: {drawn}")
else:
    print(done.stderr[-800:])

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
