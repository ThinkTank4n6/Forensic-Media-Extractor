"""A file whose name is not UTF-8 is recovered, and the run still finishes.

A folder from another machine holds names in that machine's encoding: a
Windows-1252 "café" is the byte E9, which Python hands back as a lone
surrogate. manifest.csv could not write it, the file was kept without its row,
and the manifest rewrite at the end failed: the run ended with no
manifest.json, so the two good files beside it were lost to the case as well.
Names are now written with each undecodable byte as \\xNN.
"""
import contextlib
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, PROJECT)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core          # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_undecodable-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


check("an escaped byte is shown as \\xNN", media_core.writable_text(os.fsdecode(b"caf\xe9")) == "caf\\xe9")
check("and its bytes can be had back from that",
      media_core.writable_text(os.fsdecode(b"caf\xe9")).encode().decode("unicode_escape")
      .encode("latin-1") == b"caf\xe9")
check("an unpaired UTF-16 surrogate (NTFS allows one) is shown, not refused",
      media_core.writable_text("a\ud83db") == "a\\ud83db")
check("text that was fine is left alone", media_core.writable_text("café ü 写真") == "café ü 写真")

if os.name == "nt" or sys.platform == "darwin":
    # Windows and macOS refuse such a byte in a name, so the folder cannot be
    # built here; the function above is what such a name meets.
    print("(a folder with a non-UTF-8 name cannot be made on this system; the run is not checked)")
else:
    source = os.fsencode(os.path.join(ROOT, "src"))
    os.makedirs(source)
    picture = fixtures.make_jpeg(6)
    for index, name in enumerate([b"ok.jpg", b"caf\xe9.jpg", b"zzz.jpg"]):
        note = bytes([0xFF, 0xFE, 0x00, 0x04, 0x30 + index, 0x30])
        with open(os.path.join(source, name), "wb") as fh:
            fh.write(picture[:2] + note + picture[2:])
    out = os.path.join(ROOT, "out")
    finished = subprocess.run(
        [sys.executable, "-m", "fme", "extract", "-i", os.fsdecode(source), "-o", out],
        cwd=PROJECT, capture_output=True, text=True, errors="replace")
    check("the run finishes", finished.returncode == 0, f"exit {finished.returncode}")
    path = os.path.join(out, "manifest.json")
    files = json.load(open(path, encoding="utf-8"))["files"] if os.path.isfile(path) else []
    names = sorted(os.path.basename(f["source_path"]) for f in files)
    check("every file is in manifest.json", names == ["caf\\xe9.jpg", "ok.jpg", "zzz.jpg"], str(names))
    with open(os.path.join(out, "manifest.csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    check("and manifest.csv has the same rows", len(rows) == len(files) == 3, str(len(rows)))
    check("no scratch file is left", not [n for n in os.listdir(out) if n.endswith(".tmp")])

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
