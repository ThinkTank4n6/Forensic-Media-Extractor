"""A page of a job's files is a query, and it sees the examiner's gradings.

/api/files once filtered a copy of the job's list in Python, and its
category filter read a `grade` field the rows never had -- so "show me
category 1" answered nothing, on every case, without an error. Gradings live
in case.db. This checks the page against the case database an examiner
actually grades into, and that the answer changes the moment they grade.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.review import casedb, fileindex
from fme.web import media_gui as gui

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fileindex_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

N = 200_000
rows = [{"rel_path": f"images/jpg/f{i}.jpg", "category": "image" if i % 3 else "video",
         "ext": "jpg" if i % 3 else "mp4", "deleted": i % 5 == 0,
         "sha256": f"{i:064x}", "size": 1000 + (i * 7919) % 100000,
         "source_path": f"/DCIM/100APPLE/IMG_{i:05d}.JPG" if i % 2 else ""}
        for i in range(N)]

case = casedb.CaseDB(os.path.join(ROOT, "case.db"), examiner="Det. C. T.")
graded = [rows[i]["sha256"] for i in range(0, N, 1000)]       # 200 files
case.set_category(graded, 1, by="Det. C. T.")

with gui.JOBS_LOCK:
    gui.JOBS["__index_probe__"] = {"status": "done", "files": rows, "out_dir": ROOT,
                                   "recovered": N, "summary": {}, "error": None,
                                   "done": 1, "total": 1}
client = gui.app.test_client()


def page(query: str) -> dict:
    reply = client.get(f"/api/files?job_id=__index_probe__&{query}")
    body = json.loads(reply.get_data())
    body["_status"] = reply.status_code
    return body


print("a category filter sees the examiner's gradings")
got = page("category=1&limit=5000")
right = got["total"] == len(graded) and \
    {f["sha256"] for f in got["files"]} == set(graded)
ok &= right
print(f"  category 1: {got['total']} of {len(graded)} graded files, the same ones: "
      f"{right}  (the old filter answered 0)")

uncat = page("category=8&limit=1")
ok &= uncat["total"] == N - len(graded)
print(f"  ungraded files count as Uncategorized: {uncat['total']:,}")

# Grade a few more in case.db, exactly as the interface does. The count is
# cached, so this checks the cache notices another connection's commit.
case.set_category([rows[i]["sha256"] for i in (1, 2, 3)], 1, by="Det. C. T.")
after = page("category=1&limit=1")
ok &= after["total"] == len(graded) + 3
print(f"  three more graded: {after['total']} -- the cached count noticed: "
      f"{after['total'] == len(graded) + 3}")

both = page("category=1&kind=video&deleted=1&limit=5000")
want = sum(1 for r in rows if r["sha256"] in set(graded) | {rows[i]['sha256'] for i in (1, 2, 3)}
           and r["category"] == "video" and r["deleted"])
ok &= both["total"] == want
print(f"  combined with kind and deleted: {both['total']} (expected {want})")

print("\nthe page is sorted where asked, and only by known columns")
big = page("sort=size&dir=desc&limit=50")
sizes = [f["size"] for f in big["files"]]
ok &= sizes == sorted(sizes, reverse=True) and sizes[0] == max(r["size"] for r in rows)
print(f"  largest first: {sizes[:3]}... {sizes == sorted(sizes, reverse=True)}")
for attempt in ("rel_path;DROP TABLE files", "size) --", "(SELECT 1)"):
    refused = page(f"sort={attempt}")
    ok &= refused["_status"] == 400
    print(f"  sort={attempt!r}: refused ({refused['_status']})")
ok &= page("sort=order&limit=1")["total"] == N
print("  and the table is still there afterwards")

literal = page("q=100%25&limit=5")      # a percent sign, URL-encoded
ok &= literal["total"] == 0
print(f"  a % in the search is a character, not a wildcard: {literal['total'] == 0}")

print("\nkeeping up with a run that is still going")
index = gui.JOBS["__index_probe__"]["_index"]
before = index.rebuilds
grown = rows + [dict(rows[0], rel_path=f"late/x{i}.jpg", sha256=f"late{i}")
                for i in range(100)]
with gui.JOBS_LOCK:
    gui.JOBS["__index_probe__"]["files"] = grown
ok &= page("limit=1")["total"] == N + 100 and index.rebuilds == before
print(f"  100 more files: indexed as a tail, not rebuilt: {index.rebuilds == before}")
restarted = [dict(r, rel_path="re/" + r["rel_path"]) for r in rows[:N + 100]]
with gui.JOBS_LOCK:
    gui.JOBS["__index_probe__"]["files"] = restarted
again = page("q=re/&limit=1")
ok &= index.rebuilds == before + 1 and again["total"] == N
print(f"  a restarted run of the same length is rebuilt, not mistaken for the "
      f"old one: {index.rebuilds == before + 1}")

print("\nwhat a page costs on a case this size")
times = []
for offset in (0, 50000, 150000, 199500):
    t0 = time.perf_counter()
    page(f"category=8&kind=image&sort=name&limit=500&offset={offset}")
    times.append((time.perf_counter() - t0) * 1000)
print(f"  pages of 500 at four offsets, filtered and sorted by name: "
      f"{', '.join(f'{t:.0f} ms' for t in times)}")
ok &= max(times) < 2000

with gui.JOBS_LOCK:
    del gui.JOBS["__index_probe__"]
case.close()
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
