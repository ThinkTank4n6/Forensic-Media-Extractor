"""A picture the gallery can show is one every picture feature can read
(v2026.11.341).

GitHub's button sweep failed on Python 3.11: it selected the first tile,
which there was snapshot.ktx -- an Apple GPU texture the gallery draws
through FME's texture decoder -- and "Others like this" said the picture
"could not be described by the model", "Find edited copies" that it "would
not decode". Those two, face search and screening each read pictures with
OpenCV's own decoder, which reads no texture, reads a CgBI PNG as black,
and reads no HEIC -- every photograph an iPhone takes. They now read
through imaging.read_array, which asks OpenCV first and then goes the
gallery's way.

  * a KTX texture, a CgBI PNG and a JPEG are each read, in color and gray;
  * the CgBI PNG is read as its own pixels, not black;
  * edit matching fingerprints the texture and finds its copy;
  * none of the four features decodes with OpenCV directly any more.
"""
import _sandbox  # noqa: F401
import io
import os
import re
import shutil
import struct
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
from fme.engines import edit_match, imaging, textures  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "decode_like_gallery-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

# A picture with enough detail for edit matching's feature check: the
# recipe tests/test_edit_match.py uses. A smooth gradient has too few
# features to match even its own PNG copy.
import cv2  # noqa: E402
rng = np.random.default_rng(11)
bgr = np.zeros((600, 800, 3), np.uint8)
for _ in range(240):
    colour = tuple(int(v) for v in rng.integers(40, 235, 3))
    cv2.circle(bgr, tuple(int(v) for v in rng.integers(0, 800, 2)),
               int(rng.integers(8, 60)), colour, -1)
for _ in range(60):
    cv2.line(bgr, tuple(int(v) for v in rng.integers(0, 800, 2)),
             tuple(int(v) for v in rng.integers(0, 600, 2)), (255, 255, 255), 2)
cv2.putText(bgr, "EVIDENCE", (60, 320), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 6)
bgr[40:120, 300:500] = (20, 40, 250)                 # a red block, BGR
h, w = bgr.shape[:2]
rgba = np.dstack([bgr[..., ::-1], np.full((h, w), 255, np.uint8)])


def make_ktx(picture):
    ph, pw, _ = picture.shape
    data = picture.tobytes()
    head = struct.pack("<13I", 0x04030201, 0x1401, 1, 0x1908, 0x8058,
                       0x1908, pw, ph, 0, 0, 1, 1, 0)
    return textures.KTX1_MAGIC + head + struct.pack("<I", len(data)) + data


def chunk(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def make_cgbi(picture):
    """CgBI as Xcode writes it: BGRA, premultiplied (opaque here), raw
    DEFLATE, every row filter 0."""
    ph, pw, _ = picture.shape
    bgra = picture[:, :, [2, 1, 0, 3]]
    raw = b"".join(b"\x00" + bgra[y].tobytes() for y in range(ph))
    c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    idat = c.compress(raw) + c.flush()
    ihdr = struct.pack(">IIBBBBB", pw, ph, 8, 6, 0, 0, 0)
    return (textures.PNG_SIG + chunk(b"CgBI", b"\x50\x00\x20\x06")
            + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


from PIL import Image  # noqa: E402

files = {"snapshot.ktx": make_ktx(rgba), "AppIcon@2x.png": make_cgbi(rgba)}
buf = io.BytesIO()
Image.fromarray(rgba[..., :3]).save(buf, "JPEG", quality=95)
files["photo.jpg"] = buf.getvalue()
paths = {}
for name, body in files.items():
    paths[name] = os.path.join(ROOT, name)
    with open(paths[name], "wb") as fh:
        fh.write(body)

print("Each read in color and in gray")
for name, path in paths.items():
    color = imaging.read_array(path)
    gray = imaging.read_array(path, gray=True)
    check(f"{name}", color is not None and color.shape[:2] == (h, w)
          and color.shape[2] == 3 and gray is not None and gray.shape == (h, w),
          None if color is None else color.shape)

cgbi = imaging.read_array(paths["AppIcon@2x.png"])
red = cgbi[80, 400] if cgbi is not None else None
check("the CgBI PNG is its own pixels, not black (BGR at the red square)",
      red is not None and int(red[2]) > 200 and int(red[0]) < 60, None if red is None else red.tolist())

print("Find edited copies, on the texture")
ready, why = edit_match.available()
if not ready:
    print(f"  (not checked: {why})")
else:
    target = edit_match.fingerprints(paths["snapshot.ktx"])
    check("the texture is fingerprinted, not 'would not decode'", bool(target))
    library = {name: edit_match.fingerprints(p) for name, p in paths.items()
               if name != "snapshot.ktx"}
    found = edit_match.find_copies(paths["snapshot.ktx"], library,
                                   {n: p for n, p in paths.items() if n in library})
    check("and its copies are looked for", found.get("ok"), found.get("error"))
    check("the JPEG of the same picture is found",
          "photo.jpg" in {m.get("sha256") for m in found.get("matches", [])},
          [m.get("sha256") for m in found.get("matches", [])])

print("No picture feature decodes with OpenCV directly")
engines = os.path.join(os.path.dirname(HERE), "fme", "engines")
direct = []
for name in ("edit_match.py", "content_search.py", "face_match.py", "screening.py"):
    with open(os.path.join(engines, name), encoding="utf-8") as fh:
        if re.search(r"cv2\.(imdecode|imread)\(", fh.read()):
            direct.append(name)
check("all four read through imaging.read_array", not direct, direct)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
