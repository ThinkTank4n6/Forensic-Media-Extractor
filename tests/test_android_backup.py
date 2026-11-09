"""An Android backup (adb backup, .ab) is read as the phone's files (v2026.11.318).

The README and the manual listed `.ab` among the phone extractions this
program reads, and nothing read it: is_package refused the file and the run
carved its compressed bytes as if they were a disk, finding nothing. The
format is four header lines and a tar, usually zlib-compressed. An encrypted
backup needs the device's backup password, and is refused with a reason.
"""
import contextlib
import io
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402
from fme.engines import media_core  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_android_backup-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


picture = fixtures.make_jpeg(12)
member = "apps/com.whatsapp/r/Media/WhatsApp Images/IMG-20240101-WA0001.jpg"
tar = io.BytesIO()
with tarfile.open(fileobj=tar, mode="w") as t:
    info = tarfile.TarInfo(member)
    info.size, info.mtime = len(picture), 1704067200
    t.addfile(info, io.BytesIO(picture))


def backup(name, compressed=True, encryption="none"):
    path = os.path.join(ROOT, name)
    body = zlib.compress(tar.getvalue()) if compressed else tar.getvalue()
    with open(path, "wb") as fh:
        fh.write(b"ANDROID BACKUP\n5\n" + (b"1" if compressed else b"0") + b"\n"
                 + encryption.encode() + b"\n" + body)
    return path


for name, compressed in (("phone.ab", True), ("plain.ab", False)):
    path = backup(name, compressed)
    print(f"{'compressed' if compressed else 'uncompressed'} backup")
    check("is taken for a package", media_core.is_package(path))
    out = os.path.join(ROOT, "case_" + name)
    with contextlib.redirect_stdout(io.StringIO()):
        summary = media_core.run_extraction(path, out, mode="fs", workers=1,
                                            metadata=False, kml=False)
    import json
    files = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))["files"]
    import hashlib
    got = [f for f in files if f["sha256"] == hashlib.sha256(picture).hexdigest()]
    check("its picture is recovered under the app's own path",
          got and "WhatsApp Images/IMG-20240101-WA0001.jpg" in got[0]["source_path"],
          [f["source_path"] for f in files])

print("encrypted backup")
path = backup("locked.ab", True, "AES-256")
out = os.path.join(ROOT, "case_locked")
try:
    with contextlib.redirect_stdout(io.StringIO()):
        summary = media_core.run_extraction(path, out, mode="fs", workers=1,
                                            metadata=False, kml=False)
    said = " ".join(str(v) for v in (summary.get("package_error"),
                                      summary.get("errors_text"), summary) if v)
except Exception as exc:  # noqa: BLE001
    said = str(exc)
check("is refused, saying it needs the backup password",
      "encrypted Android backup" in said, said[:120])

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
