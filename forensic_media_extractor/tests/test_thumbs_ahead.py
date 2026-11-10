"""Tiles are made ahead of the examiner, on several workers, next page first
(v2026.11.326).

One thread made a case's tiles in order, and only after a run: a case opened
from the list made none ahead, so each page of a 221,592-file case made its
500 tiles while the examiner waited for it.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import contextlib
import io
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402
    from fme.web import media_gui as gui  # noqa: E402
from fme.engines import output  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_thumbs_ahead-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
H = {"X-FME-Request": "1"}
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


case_dir = os.path.join(ROOT, "case")
out = output.OutputManager(case_dir, metadata=False)
for n in range(80):
    out.store(iter([fixtures.make_jpeg(4100 + n)]), category="image", ext="jpg",
              stem=f"p{n:03d}", engine="folder", source_path=f"/p{n:03d}.jpg",
              inode="", fs_type="", partition="", signature="folder-file")
out.close({"image": "/evidence"})

made, threads = [], set()
gate = threading.Event()
real_make = gui._make_thumb


def slow_make(out_dir, full, rel):
    gate.wait(10)
    threads.add(threading.current_thread().name)
    made.append(rel)
    time.sleep(0.02)
    return real_make(out_dir, full, rel)


gui._make_thumb = slow_make
client = gui.app.test_client()
print("a case opened from the list")
job = client.post("/api/cases/open", json={"out_dir": case_dir}, headers=H).get_json()["job_id"]
check("starts making its tiles", job in gui._PREFILLS)
with gui.JOBS_LOCK:
    rels = [f["rel_path"] for f in gui.JOBS[job]["files"]]
# The examiner goes to the fifth page of ten; the server is asked for it.
answer = client.post("/api/gallery/query", headers=H, json={
    "job": job, "state": {"filter": "all"}, "limit": 10, "offset": 40}).get_json()
shown = [f["rel_path"] for f in answer["files"]]
following = set(rels[50:60])
gate.set()
# Waited for on disk, not on the count of calls: a call is counted as it
# starts, and under load the last tile was still being written.
for _ in range(1200):
    if all(os.path.isfile(gui._thumb_path(case_dir, r)) for r in rels):
        break
    time.sleep(0.05)
first = set(made[:10 + gui._prefill_workers()])
check("the page after the one asked for is made first",
      following <= first, f"{len(following & first)} of 10 in the first {len(first)}")
check("on more than one worker at once", len(threads) > 1, sorted(threads))
check("and every tile is made in the end",
      all(os.path.isfile(gui._thumb_path(case_dir, r)) for r in rels))
gui._make_thumb = real_make

print("\na later page, once the sweep is over")
for r in rels[:10]:
    os.remove(gui._thumb_path(case_dir, r))
for _ in range(100):
    if job not in gui._PREFILLS:
        break
    time.sleep(0.05)
client.post("/api/thumbs/ahead", headers=H, json={"job": job, "files": rels[:10]})
for _ in range(200):
    if all(os.path.isfile(gui._thumb_path(case_dir, r)) for r in rels[:10]):
        break
    time.sleep(0.05)
check("asking ahead makes those tiles again",
      all(os.path.isfile(gui._thumb_path(case_dir, r)) for r in rels[:10]))
client.post("/api/cases/close", json={"job": job}, headers=H)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
