"""What a run stores, and what a case records, is what happened (v2026.11.320).

From a review of v2026.11.319, each reproduced before it was fixed:

- a zip/UFDR entry or a folder file whose read failed part way was stored
  truncated and unmarked, with no error counted;
- a case folder deleted or replaced while the program ran kept receiving
  gradings in the old, deleted case.db;
- a reviewer's gradings were credited to whoever ran the extraction;
- importing a basemap deleted the hash-store token the examiner kept;
- a failed duplicate write still changed the kept file's record;
- --resume took any progress note for this run's;
- under an unlocked BitLocker/LUKS volume the carve could not see the
  device's bad sectors.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import contextlib
import errno
import io
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402
    from fme.web import media_gui as gui  # noqa: E402
from fme.engines import media_core, output, sources, unlock  # noqa: E402
from fme.review import settings  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_honesty320-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
H = {"X-FME-Request": "1"}
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def quiet(**kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.run_extraction(workers=1, metadata=False, kml=False, **kw)


print("a read that fails part way is stored marked, not as whole")
picture = fixtures.make_jpeg(9000)
package = os.path.join(ROOT, "phone.zip")
with zipfile.ZipFile(package, "w", zipfile.ZIP_STORED) as z:
    z.writestr("DCIM/a.jpg", picture)
raw = bytearray(open(package, "rb").read())
raw[raw.find(picture[2000:2016]) + 9_000_000] ^= 0xFF
open(package, "wb").write(bytes(raw))
summary = quiet(image_path=package, out_dir=os.path.join(ROOT, "pkg"), mode="fs")
rows = json.load(open(os.path.join(ROOT, "pkg", "manifest.json"), encoding="utf-8"))["files"]
check("a package entry with a bad CRC: stored as far as it read, marked short, with why",
      rows and rows[0]["bounded"] == "short" and "CRC" in rows[0]["note"]
      and summary.get("read_short") == 1, rows and (rows[0]["size"], rows[0]["note"][:60]))

real_chunks = sources._file_chunks


def failing(path, chunk=4 << 20, strict=False):
    with open(path, "rb") as fh:
        yield fh.read(chunk)
        raise OSError(errno.EIO, "Input/output error")


folder = os.path.join(ROOT, "usb")
os.makedirs(folder)
open(os.path.join(folder, "big.jpg"), "wb").write(picture)
import _engine  # noqa: E402
saved = _engine.replace("_file_chunks", failing)
try:
    summary = quiet(image_path=folder, out_dir=os.path.join(ROOT, "fold"))
finally:
    _engine.restore(saved)
rows = json.load(open(os.path.join(ROOT, "fold", "manifest.json"), encoding="utf-8"))["files"]
big = [r for r in rows if r["source_path"].endswith("big.jpg")]
check("a folder file with an I/O error part way: marked short, with why",
      big and big[0]["bounded"] == "short" and "Input/output" in big[0]["note"],
      big and big[0]["note"][:80])

print("\na failed duplicate write leaves the kept file's record alone")
out = output.OutputManager(os.path.join(ROOT, "dup"), metadata=False)
carved = out.store(iter([picture]), category="image", ext="jpg", stem="c",
                   engine="carve", source_path="", inode="", fs_type="",
                   partition="", signature="jpg", offset=4096)
real_add = out._db.add_duplicate
out._db.add_duplicate = lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O"))
out.store(iter([picture]), category="image", ext="jpg", stem="m", engine="mft",
          source_path="/Users/x/secret.jpg", inode="5", fs_type="NTFS",
          partition="p1", signature="mft", deleted=True)
out._db.add_duplicate = real_add
check("still a carved file with no path, one instance",
      carved.engine == "carve" and not carved.source_path and carved.instances == 1
      and not carved.deleted, (carved.engine, carved.source_path, carved.instances))
out.close({})

print("\na resume belongs to its run")
image = os.path.join(HERE, "media_test.img")
case = os.path.join(ROOT, "resume")
os.makedirs(case)
media_core._update_progress(case, lambda old: {
    "phase": "carve", "offset": 12 << 20, "total": 64 << 30,
    "run": {"source": "/evidence/other.E01", "source_size": 68719476736}})
try:
    quiet(image_path=image, out_dir=case, resume=True)
    said = ""
except ValueError as exc:
    said = str(exc)
check("another run's note is refused, saying what differs",
      "different run" in said and "source" in said, said[:90])

print("\nthe carve sees bad sectors under an unlocked volume")
inner = types.SimpleNamespace(name="dev", size=1 << 20, bad_ranges=[[4096, 512]],
                              close=lambda: None)
wrapped = unlock.UnlockedReader(inner, io.BytesIO(bytes(1 << 20)))
check("the device's bad ranges show through the decrypting reader",
      wrapped.bad_ranges == [[4096, 512]])

print("\nthe case on screen is the case on disk")
client = gui.app.test_client()
cdir = os.path.join(ROOT, "case")
out = output.OutputManager(cdir, metadata=False)
rec = out.store(iter([picture]), category="image", ext="jpg", stem="a", engine="folder",
                source_path="/a.jpg", inode="", fs_type="", partition="",
                signature="folder-file")
out.close({"image": "/evidence"})
settings.save({"examiner": "Det. Reviewer"})
job = client.post("/api/cases/open", json={"out_dir": cdir}, headers=H).get_json()["job_id"]
client.post("/api/case/categorize", headers=H,
            json={"job": job, "sha256": [rec.sha256], "category": 1})
con = sqlite3.connect(os.path.join(cdir, "case.db"))
who = con.execute("SELECT categorized_by FROM decisions").fetchone()
con.close()
check("a grading is credited to the examiner this machine names", who == ("Det. Reviewer",), who)
os.replace(os.path.join(cdir, "case.db"), os.path.join(ROOT, "case.db.moved"))
for side in ("-wal", "-shm"):
    if os.path.exists(os.path.join(cdir, "case.db" + side)):
        os.replace(os.path.join(cdir, "case.db" + side), os.path.join(ROOT, "moved" + side))
client.post("/api/case/categorize", headers=H,
            json={"job": job, "sha256": [rec.sha256], "category": 2})
check("a case.db replaced under it is reopened, and the grading lands there",
      os.path.exists(os.path.join(cdir, "case.db")) and sqlite3.connect(
          os.path.join(cdir, "case.db")).execute(
          "SELECT category FROM decisions").fetchone() == (2,))
r = client.post("/api/cases/close", json={"job": job}, headers=H).get_json()
check("Close lets go of the case's database", r.get("released") is True
      and cdir not in gui.CASES)

print("\nimporting a basemap keeps the store token")
settings.save({"store_token": "kept-token"}, remember_token=True)
from fme.engines import basemaps  # noqa: E402
try:
    basemaps.set_active("")
except Exception:  # noqa: BLE001 -- clearing the choice is enough to save
    settings.save({"basemap": ""})
check("the token is still remembered", settings.load().get("store_token") == "kept-token")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
