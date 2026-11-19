"""Opening a case again lets go of the copy opened before (v2026.11.335).

Every open of a case made a new job holding the case's whole file list --
about 200 MB for a case of 221,292 files -- and none was ever let go, so a
day of reopening one large case held gigabytes nobody could reach. Opening a
case again now releases its earlier copies; a request still naming one (a
second window left on it) is told so, rather than answered from an empty
list as if the case had no files; and another case stays open.

Through the routes, as the page opens a case.
"""
import _sandbox  # noqa: F401
import gc
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
from fme.review import galleryquery  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "reopen_releases-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)


def make_case(name, count):
    case = os.path.join(ROOT, name)
    out = output.OutputManager(case, metadata=False)
    for n in range(count):
        out.store(iter([b"\xff\xd8\xff\xe0" + f"{name}{n}".encode() * 50 + b"\xff\xd9"]),
                  category="image", ext="jpg", stem=f"{name}{n}", engine="filesystem",
                  source_path=f"/DCIM/{name}_{n}.JPG", inode="", fs_type="",
                  partition="", signature="test")
    out.close({"image": "/evidence"})
    return case


case_a, case_b = make_case("a", 40), make_case("b", 25)
client = gui.app.test_client()
H = {"X-FME-Request": "1"}


def open_case(folder):
    return client.post("/api/cases/open", json={"out_dir": folder}, headers=H).get_json()["job_id"]


def page_of(job):
    return client.post("/api/gallery/query", headers=H, json={
        "job": job, "state": {"filter": "all"}, "counts": True, "limit": 100})


first = open_case(case_a)
page_of(first)                                     # cached, as the page would
other = open_case(case_b)
second = open_case(case_a)
third = open_case(case_a)

print("Case A opened three times, case B once between")
with gui.JOBS_LOCK:
    held = {j: len(gui.JOBS[j]["files"]) for j in (first, second, third, other)}
check("the first two copies of case A are let go", held[first] == 0 and held[second] == 0, held)
check("the copy open now has every file", held[third] == 40, held[third])
check("case B, opened in between, is still open", held[other] == 25, held[other])

answer = page_of(first)
check("a request for a copy let go is told so",
      answer.status_code == 410 and "opened again" in answer.get_json().get("error", ""),
      f"{answer.status_code} {answer.get_json().get('error', '')[:70]}")
check("and so is one naming it as job_id",
      client.get(f"/api/status?job_id={second}").status_code == 410)
fresh = page_of(third).get_json()
check("the copy open now answers", fresh.get("total") == 40, fresh.get("total"))
check("case B answers too", page_of(other).get_json().get("total") == 25)

gc.collect()
lists = [v[0] for v in galleryquery.CACHE._items.values() if v]
check("nothing the gallery keeps holds a list that was let go",
      all(len(x) in (25, 40) for x in lists), [len(x) for x in lists])

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
