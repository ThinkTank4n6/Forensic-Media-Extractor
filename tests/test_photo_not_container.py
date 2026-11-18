"""A photo is not a container because its thumbnail came out of it
(v2026.11.334).

The examiner graded every tile of a 221,292-file case and the category bar
still said 4,441 Uncategorized. Every photo whose EXIF thumbnail was pulled
out of it (a video with its cover art, the same) had been marked a
container: hidden from the gallery, its thumbnail the only tile, and counted
Uncategorized with nothing to grade it from. A report then listed the full
picture as ungraded while its thumbnail carried the grade.

This builds a case the way a release before .334 left it -- the photo marked
-- and opens it through the route: the photo is a tile again, a true
container (the pieces a clip was rebuilt from) stays hidden, and the bar
counts what the gallery can show, so a case graded to the end reads 0.
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


from fme.engines import output  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "photo_container-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
case = os.path.join(ROOT, "case")
out = output.OutputManager(case, metadata=False)


def put(name, body, category, ext, source):
    return out.store(iter([body]), category=category, ext=ext, stem=name,
                     engine="filesystem", source_path=source, inode="",
                     fs_type="", partition="", signature="test")


JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x11" * 3000 + b"\xff\xd9"
put("photo", JPEG + b"photo", "image", "jpg", "/DCIM/IMG_0001.JPG")
put("thumb", JPEG + b"thumb", "image", "jpg", "/DCIM/IMG_0001.JPG::+6")
put("plain", JPEG + b"plain", "image", "jpg", "/DCIM/IMG_0002.JPG")
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x22" * 4000
put("segment", MP4 + b"seg", "video", "mp4", "/cache/seg_000.mp4")
put("clip", MP4 + b"clip", "video", "mp4", "/cache/stream.json::7")
out.close({"image": "/evidence"})

# What .333 and before wrote: the photo, as well as the segment, marked.
manifest_path = os.path.join(case, "manifest.json")
manifest = json.load(open(manifest_path, encoding="utf-8"))
for f in manifest["files"]:
    if f["source_path"] in ("/DCIM/IMG_0001.JPG", "/cache/seg_000.mp4"):
        f["container"] = True
json.dump(manifest, open(manifest_path, "w", encoding="utf-8"))

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
job = client.post("/api/cases/open", json={"out_dir": case}, headers=H).get_json()["job_id"]


def query(state):
    return client.post("/api/gallery/query", headers=H, json={
        "job": job, "state": state, "counts": True, "limit": 50}).get_json()


shown = {f["source_path"] for f in query({"filter": "all"})["files"]}
print("An old case, opened")
check("the photo its thumbnail came out of is a tile again",
      "/DCIM/IMG_0001.JPG" in shown, sorted(shown))
check("and so is the thumbnail", "/DCIM/IMG_0001.JPG::+6" in shown)
check("a true container -- the piece a clip was rebuilt from -- stays hidden",
      "/cache/seg_000.mp4" not in shown)

uncategorized = (gui._case_for(job).uncategorized)
answer = query({"filter": "all"})
bar = answer["counts"]["by_category"]
check("the bar counts the four the gallery shows, not the hidden one",
      sum(bar.values()) == 4, bar)
check("and says how many containers it is leaving out",
      answer["counts"]["containers"] == 1, answer["counts"]["containers"])

digests = [f["sha256"] for f in answer["files"]]
graded = client.post("/api/case/categorize", headers=H,
                     json={"job": job, "sha256": digests, "category": 1})
check("every tile graded", graded.status_code == 200, graded.status_code)
bar = query({"filter": "all"})["counts"]["by_category"]
check("a case graded to the end reads 0 Uncategorized",
      bar.get(uncategorized, bar.get(str(uncategorized), 0)) == 0, bar)
left = query({"filter": "needs-review"})
check("and Not yet reviewed is empty", left["total"] == 0, left["total"])

opened = query({"filter": "all", "showContainers": True})
check("with containers shown, the hidden one is there, and counted",
      "/cache/seg_000.mp4" in {f["source_path"] for f in opened["files"]}
      and sum(opened["counts"]["by_category"].values()) == 5,
      opened["counts"]["by_category"])

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
