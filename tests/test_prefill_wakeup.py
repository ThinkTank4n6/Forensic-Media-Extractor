"""A tile asked for while the tile maker is finishing is still made
(v2026.11.336).

A worker that found nothing left to make released the queue's lock and only
afterwards counted itself out. A tile asked for in that moment was queued
while the worker still counted as running, so nothing started another, and
the worker left without it: a video tile with no poster -- or a picture with
no thumbnail -- until something asked again. Seen as test_video_tiles
failing under load, where the moment is long; on an examiner's machine it is
a tile that stays blank.

Here the moment is made long on purpose: the worker is held for a second
just after it finds the queue empty, and a second tile is asked for then.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["FME_PAGED_THRESHOLD"] = "0"

import make_media_test_image as fixtures  # noqa: E402
from fme.engines import output  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "prefill_wakeup-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
case = os.path.join(ROOT, "case")
out = output.OutputManager(case, metadata=False)
for n in range(3):
    out.store(iter([fixtures.make_jpeg(6 + n)]), category="image", ext="jpg",
              stem=f"p{n}", engine="filesystem", source_path=f"/DCIM/p{n}.jpg", inode="",
              fs_type="", partition="", signature="test")
out.close({"image": "/evidence"})

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
job = client.post("/api/cases/open", json={"out_dir": case}, headers=H).get_json()["job_id"]
with gui.JOBS_LOCK:
    rels = [f["rel_path"] for f in gui.JOBS[job]["files"]]


def thumb(rel):
    return gui._thumb_path(case, rel)


for _ in range(300):                       # the sweep made when the case opened
    if all(os.path.isfile(thumb(r)) for r in rels):
        break
    time.sleep(0.1)
check("the case's thumbnails are made when it opens",
      all(os.path.isfile(thumb(r)) for r in rels))
for _ in range(100):                       # and its workers have gone
    with gui._PREFILLS_LOCK:
        if job not in gui._PREFILLS:
            break
    time.sleep(0.1)

# The moment a worker finds the queue empty, made a second long.
real_next = gui._Prefill.next_path
held = []


def slow_next(self, kind="image", *args, **kwargs):
    rel = real_next(self, kind, *args, **kwargs)
    if rel is None:
        held.append(time.time())
        time.sleep(1.0)
    return rel


gui._Prefill.next_path = slow_next
try:
    first, second = rels[0], rels[1]
    os.remove(thumb(first))
    gui._prefill_ahead(job, [first], replace=False)
    for _ in range(100):                   # made, and its worker now in the moment
        if os.path.isfile(thumb(first)) and held:
            break
        time.sleep(0.05)
    check("the first tile asked for is made", os.path.isfile(thumb(first)))
    os.remove(thumb(second))
    gui._prefill_ahead(job, [second], replace=False)     # asked for in that moment
    for _ in range(100):
        if os.path.isfile(thumb(second)):
            break
        time.sleep(0.05)
    check("one asked for while the worker was finishing is made too",
          os.path.isfile(thumb(second)))
finally:
    gui._Prefill.next_path = real_next

# A tile the running sweep made once, gone, and asked for again before the
# sweep ends. The sweep's "taken" list refused it: on a large case, where
# the sweep runs for hours, a tile that failed once or was removed stayed
# blank until it ended. This is what test_video_tiles met under load.
print("a tile asked for again while the sweep that made it is still going")
real_thumb = gui._make_thumb


def slow_thumb(out_dir, full, rel):
    time.sleep(0.6)                        # a sweep that is still going
    return real_thumb(out_dir, full, rel)


gui._make_thumb = slow_thumb
try:
    for rel in rels:
        os.remove(thumb(rel))
    with gui._PREFILLS_LOCK:
        gui._PREFILLS.pop(job, None)
    gui._start_prefill(job, force=True)
    for _ in range(100):
        if os.path.isfile(thumb(rels[0])):
            break
        time.sleep(0.05)
    with gui._PREFILLS_LOCK:
        sweeping = gui._PREFILLS.get(job)
    check("the sweep has made the first and is still going",
          os.path.isfile(thumb(rels[0])) and sweeping is not None and sweeping.running > 0)
    os.remove(thumb(rels[0]))
    gui._prefill_ahead(job, [rels[0]], replace=False)
    for _ in range(200):
        with gui._PREFILLS_LOCK:
            if job not in gui._PREFILLS:
                break
        time.sleep(0.05)
    check("asked for again, it is made again", os.path.isfile(thumb(rels[0])))
    check("and the rest of the sweep too", all(os.path.isfile(thumb(r)) for r in rels))
finally:
    gui._make_thumb = real_thumb

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
