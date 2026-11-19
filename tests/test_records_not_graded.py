"""SQLite databases and property lists are not graded (v2026.11.335).

With "also recover artifacts" on, a run keeps every SQLite database and
binary property list it finds, and each one sat in the gallery, the category
bar and Not yet reviewed as a file waiting for a category no examiner gives
a database. They are now set aside as a container is: off the bar and out of
Not yet reviewed, still in the manifest, and back with Show containers. A
PDF is still for grading.

Opened through the route, as the examiner opens a case.
"""
import _sandbox  # noqa: F401
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

ROOT = os.path.join(tempfile.gettempdir(), "records_not_graded-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
case = os.path.join(ROOT, "case")
out = output.OutputManager(case, metadata=False)


def put(name, body, category, ext, source):
    return out.store(iter([body]), category=category, ext=ext, stem=name,
                     engine="carve", source_path=source, inode="",
                     fs_type="", partition="", signature="test")


put("photo", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x11" * 3000 + b"\xff\xd9",
    "image", "jpg", "/DCIM/IMG_0001.JPG")
put("db", b"SQLite format 3\x00" + b"\x00" * 4080, "other", "sqlite", "/data/sms.db")
put("plist", b"bplist00" + b"\x08" * 200, "other", "plist", "/prefs/a.plist")
put("doc", b"%PDF-1.4\n" + b"%" * 900 + b"\n%%EOF\n", "other", "pdf", "/docs/a.pdf")
out.close({"image": "/evidence"})

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
job = client.post("/api/cases/open", json={"out_dir": case}, headers=H).get_json()["job_id"]


def query(state):
    return client.post("/api/gallery/query", headers=H, json={
        "job": job, "state": state, "counts": True, "limit": 50}).get_json()


answer = query({"filter": "all"})
shown = {f["source_path"] for f in answer["files"]}
print("A case with a photo, a database, a property list and a PDF")
check("the database is not in the gallery", "/data/sms.db" not in shown, sorted(shown))
check("nor the property list", "/prefs/a.plist" not in shown)
check("the photo and the PDF are", {"/DCIM/IMG_0001.JPG", "/docs/a.pdf"} <= shown)
check("the bar counts the two to grade",
      sum(answer["counts"]["by_category"].values()) == 2, answer["counts"]["by_category"])
check("and says it is leaving two out", answer["counts"]["containers"] == 2,
      answer["counts"]["containers"])

digests = [f["sha256"] for f in answer["files"]]
client.post("/api/case/categorize", headers=H,
            json={"job": job, "sha256": digests, "category": 1})
left = query({"filter": "needs-review"})
check("once the photo and the PDF are graded, nothing is left to review",
      left["total"] == 0, left["total"])

opened = query({"filter": "all", "showContainers": True})
check("Show containers brings the database and the property list back",
      {"/data/sms.db", "/prefs/a.plist"} <= {f["source_path"] for f in opened["files"]})

import json  # noqa: E402
kept = {f["source_path"] for f in json.load(open(os.path.join(case, "manifest.json"),
                                                  encoding="utf-8"))["files"]}
check("both are still in the manifest", {"/data/sms.db", "/prefs/a.plist"} <= kept)

# The report lists the database and the property list like every other file
# -- they are in the manifest -- but says how many of its Uncategorized are
# files nobody grades, so it does not read "2 Uncategorized" against a
# gallery whose bar reads 0 without a word about the difference.
import time  # noqa: E402
started = client.post("/api/report/build", headers=H, json={
    "job": job, "formats": ["html"], "thumbnails": False,
    "dest": os.path.join(ROOT, "report")}).get_json()
state = {}
for _ in range(200):
    state = client.get(f"/api/report/status?id={started['id']}").get_json()
    if state.get("status") != "running":
        break
    time.sleep(0.1)
check("the report is written", state.get("status") == "done",
      state.get("error") or state.get("status"))
html_files = [w["path"] for w in state.get("written") or []
              if str(w.get("path", "")).endswith(".html")]
page = open(html_files[0], encoding="utf-8").read() if html_files else ""
from fme.exports import report_contents  # noqa: E402
check("its summary says how many of the Uncategorized are not for grading",
      report_contents.SET_ASIDE_NOTE in page)
at = page.find(report_contents.SET_ASIDE_NOTE)
check("and the number is the two", at > 0 and '<td class="num">2</td>' in page[at:at + 300],
      page[at:at + 200].split("</tr>")[0][-80:] if at > 0 else "")
check("both are still listed in it", "sms.db" in page or "db.sqlite" in page or
      page.count(".sqlite") >= 1)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
