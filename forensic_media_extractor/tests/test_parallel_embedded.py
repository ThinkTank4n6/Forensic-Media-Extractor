"""The parallel carve finds what one worker finds, embedded media included.

After the single-worker carve stores a file, it opens it and pulls out the
media inside -- a JPEG's thumbnail, the pictures in a document. The carve
skips signatures nested inside a file on purpose, so this is the only pass
that reaches them. The parallel carve stored its files and never looked
inside, and the GUI defaults to several workers, so most runs dropped
every embedded file. An embedded thumbnail can be the picture as it was
before it was cropped or edited.

The existing worker-count check in test_resume.py compares only what the
carve itself finds, on an image with nothing embedded, so it could not see
this. Here the image holds a JPEG with a second JPEG inside it, and the
same carve is run with one worker and with four.
"""
import contextlib
import glob
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_parallel_embedded-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

inner = fixtures.make_jpeg(3)
outer = fixtures.make_jpeg(12)
# The inner picture rides in an APP1 segment, as an EXIF thumbnail does.
photo = outer[:2] + b"\xff\xe1" + struct.pack(">H", len(inner) + 2) + inner + outer[2:]
image_bytes = bytearray(8 << 20)              # enough to split into stripes
image_bytes[3 << 20:(3 << 20) + len(photo)] = photo
image = os.path.join(ROOT, "embedded.dd")
with open(image, "wb") as fh:
    fh.write(bytes(image_bytes))


def carve(workers: int, *extra) -> list:
    out = os.path.join(ROOT, f"w{workers}" + "".join(extra).replace("-", "_"))
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image, "-o", out,
                    "--mode", "carve", "--workers", str(workers), *extra],
                   capture_output=True, cwd=PROJECT, timeout=900)
    found = glob.glob(os.path.join(out, "**", "manifest.json"), recursive=True)
    if not found:
        return []
    with open(found[0], encoding="utf-8") as fh:
        return json.load(fh)["files"]


def engines(files) -> list:
    return sorted((f["engine"], f["size"]) for f in files)


one = carve(1)
four = carve(4)
print("the carved photo and the picture inside it")
print(f"   one worker : {engines(one)}")
print(f"   four workers: {engines(four)}")
has_inner = any(f["engine"] == "embedded" and f["size"] == len(inner) for f in one)
ok &= has_inner
print(f"   one worker finds the embedded picture: {has_inner}")
same = sorted(f["sha256"] for f in one) == sorted(f["sha256"] for f in four)
ok &= same
print(f"   four workers find exactly the same files: {same}")

print("\nturning the look inside off turns it off for both")
one_off = carve(1, "--no-scan-inside")
four_off = carve(4, "--no-scan-inside")
off = engines(one_off) == engines(four_off) == [("carve", len(photo))]
ok &= off
print(f"   only the carved photo, either way: {off}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
