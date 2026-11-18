"""The gallery's counts follow a similarity run, a store check and a
screening, not only a grading (v2026.11.332).

The gallery's caches recognize a job's list of files by the list itself,
because a list is replaced rather than edited. Similar images, the hash store
check and screening edit the entries of the same list. Since v2026.11.332 the
counts after a grading are adjusted for the decisions that changed, and those
runs change none: without a new list (media_gui._entries_changed) the count
asked for before the run was carried over, "0 groups" after it found two --
checked by disabling it. Driven through the route, not the function under it.
"""
import _sandbox  # noqa: F401
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["FME_PAGED_THRESHOLD"] = "0"

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


try:
    from PIL import Image, ImageDraw
except ImportError:
    print("(not checked: Pillow is not installed)")
    sys.exit(0)

from fme.engines import output  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "gallery_inplace-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
case = os.path.join(ROOT, "case")
out = output.OutputManager(case, metadata=False)
for scene in range(2):
    base = Image.new("RGB", (320, 240), (40 + 150 * scene, 90, 200 - 150 * scene))
    draw = ImageDraw.Draw(base)
    for k in range(8):
        draw.rectangle((20 + 35 * k, 30 + 15 * ((k + scene) % 3), 45 + 35 * k, 200),
                       fill=((60 * k) % 256, (90 * scene + 30 * k) % 256, 120))
    for copy, size in enumerate(((320, 240), (300, 225), (280, 210))):
        path = os.path.join(ROOT, f"s{scene}_{copy}.jpg")
        base.resize(size).save(path, quality=90 - 20 * copy)
        with open(path, "rb") as fh:
            out.store(iter([fh.read()]), category="image", ext="jpg",
                      stem=f"s{scene}_{copy}", engine="folder", source_path=path,
                      inode="", fs_type="", partition="", signature="folder-file")
out.close({"image": "/evidence"})

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
job = client.post("/api/cases/open", json={"out_dir": case}, headers=H).get_json()["job_id"]


def groups():
    answer = client.post("/api/gallery/query", headers=H, json={
        "job": job, "state": {"filter": "all"}, "counts": True, "limit": 50}).get_json()
    return answer["counts"]["groups"]


print("Similar images")
check("no groups before the run", groups() == 0)
scan = client.post("/api/similarity/scan", json={"job": job}, headers=H).get_json()
check("the run finds the two scenes", scan.get("groups") == 2, scan.get("groups"))
check("and the gallery's count says so, though it was counted before",
      groups() == 2, groups())
stacked = client.post("/api/gallery/query", headers=H, json={
    "job": job, "state": {"filter": "all", "stackSimilar": True}, "limit": 50}).get_json()
check("stacked, a tile per scene", stacked.get("total") == 2, stacked.get("total"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
