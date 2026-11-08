"""The lower-severity findings of the v2026.11.319 review (v2026.11.320).

- a resumed folder or package run wrote every earlier file down again as a
  duplicate of itself;
- a failed frame decode removed every decode of that video in progress;
- duplicates.csv was not rewritten at close, so a row that failed to append
  stayed missing;
- a model replaced at the same path stayed loaded;
- an Android backup's decompressed copy outlived the archive;
- one CaseDB method read the shared connection outside its guard.
"""
import ast
import contextlib
import io
import json
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
from fme.engines import media_core, output, sources, video_frames  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_small320-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


print("a resumed folder run")
folder = os.path.join(ROOT, "usb")
os.makedirs(folder)
for n in range(3):
    open(os.path.join(folder, f"{n}.jpg"), "wb").write(fixtures.make_jpeg(5 + n))
case = os.path.join(ROOT, "case")
with contextlib.redirect_stdout(io.StringIO()):
    media_core.run_extraction(folder, case, workers=1, metadata=False, kml=False)
    media_core._update_progress(case, lambda old: {**old, "phase": "folder"})
    media_core.run_extraction(folder, case, workers=1, metadata=False, kml=False,
                              resume=True)
done = json.load(open(os.path.join(case, "manifest.json"), encoding="utf-8"))
check("does not record its own files as duplicates of themselves",
      len(done["files"]) == 3 and not done.get("duplicates")
      and all(f.get("instances", 1) == 1 for f in done["files"]),
      (len(done["files"]), len(done.get("duplicates") or [])))
check("package and folder finds are resume-keyed",
      {"package", "folder"} <= output.RESUME_KEYED_ENGINES)

print("\na failed frame decode")
out_dir = os.path.join(ROOT, "frames")
sha = "f" * 64
other = video_frames.frames_dir(out_dir, sha, True) + ".partOTHER"
os.makedirs(other)
from fme.engines import isolated  # noqa: E402
real_run, real_available = isolated.run, video_frames.available
isolated.run = lambda *a, **k: type("O", (), {"status": "crashed", "ok": False,
                                              "detail": "stand-in", "stdout": b""})()
video_frames.available = lambda: True
try:
    video_frames.extract("/nowhere.mp4", out_dir, sha, board=True)
finally:
    isolated.run, video_frames.available = real_run, real_available
check("leaves another request's decode of the same video alone", os.path.isdir(other))

print("\nduplicates.csv at close")
out = output.OutputManager(os.path.join(ROOT, "dups"), metadata=False)
picture = fixtures.make_jpeg(7)
out.store(iter([picture]), category="image", ext="jpg", stem="a", engine="folder",
          source_path="/a.jpg", inode="", fs_type="", partition="",
          signature="folder-file")
real_append = out._dup_csv.writerow
out._dup_csv.writerow = lambda row: (_ for _ in ()).throw(OSError("disk full"))
out.store(iter([picture]), category="image", ext="jpg", stem="b", engine="folder",
          source_path="/b.jpg", inode="", fs_type="", partition="",
          signature="folder-file")
out._dup_csv.writerow = real_append
out.close({})
with open(os.path.join(ROOT, "dups", "duplicates.csv"), encoding="utf-8") as fh:
    listed = fh.read()
check("a duplicate whose row failed to append is in it after close", "/b.jpg" in listed)

print("\na model replaced at the same path")
from fme.engines import content_search  # noqa: E402
model = os.path.join(ROOT, "m.onnx")
open(model, "wb").write(b"one")
first = content_search._file_key(model)
os.utime(model, ns=(1, 1))
open(model, "wb").write(b"two!")
check("is a different loaded model", content_search._file_key(model) != first)

print("\nan Android backup's working copy")
tar = io.BytesIO()
with tarfile.open(fileobj=tar, mode="w") as t:
    info = tarfile.TarInfo("apps/x/f.jpg")
    info.size = len(picture)
    t.addfile(info, io.BytesIO(picture))
ab = os.path.join(ROOT, "phone.ab")
open(ab, "wb").write(b"ANDROID BACKUP\n5\n1\nnone\n" + zlib.compress(tar.getvalue()))
archive, members = sources._open_android_backup(ab)
spool = archive._fme_spool if hasattr(archive, "_fme_spool") else None
archive.close()
closed = getattr(getattr(archive, "fileobj", None), "closed", None)
check("is closed with the archive", closed is True, closed)

print("\nthe case database's rule")
tree = ast.parse(open(os.path.join(PROJECT, "fme", "review", "casedb.py"),
                      encoding="utf-8").read())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CaseDB")
loose = [f.name for f in cls.body if isinstance(f, ast.FunctionDef)
         and not f.name.startswith("_") and "self._con" in ast.unparse(f)
         and "self._guard()" not in ast.unparse(f)]
check("every public CaseDB method that touches the connection holds the guard",
      not loose, loose)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
