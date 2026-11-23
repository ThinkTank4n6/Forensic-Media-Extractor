"""Ctrl-Z puts a page back the way it was, with the hash store running
(v2026.11.337).

Reported: grading a page of 500 from 8 (Uncategorized) and pressing 2 by
mistake, Ctrl-Z did not bring them back to 8 -- they came back 0,
Non-Pertinent. The undo itself was right; two things around it were not:

  * the undo put the files back to "nobody has graded this", and the next
    hash store pass -- every 15 seconds during a run, and at its end --
    graded them from the store, where the same phone's pictures were mostly
    Non-Pertinent. To the examiner the undo had made them 0;
  * every file a hash set or the store graded was an undo step of its own,
    and Undo keeps 50: one pass pushed the examiner's gradings out, and
    Ctrl-Z took back one store-graded file instead of the page.

Checked through the routes the page uses:

  * a store pass is one undo step, and never the one Ctrl-Z takes;
  * Ctrl-Z after a store pass undoes the examiner's page;
  * the page comes back 8, and stays 8 through the next store pass;
  * a file in it the examiner then grades is theirs as usual, and a file
    the examiner never touched is still graded by the store;
  * a hundred store passes do not push the examiner's gradings out.
"""
import _sandbox  # noqa: F401
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import make_media_test_image as fixtures  # noqa: E402
from fme.engines import output  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "undo_with_store-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
out = output.OutputManager(os.path.join(ROOT, "case"), metadata=False)
for n in range(10):
    out.store(iter([fixtures.make_jpeg(80 + n)]), category="image", ext="jpg",
              stem=f"IMG_{n:04d}", engine="filesystem", source_path=f"/DCIM/IMG_{n:04d}.jpg",
              inode="", fs_type="", partition="", signature="test")
out.close({"image": "/evidence"})

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
job = client.post("/api/cases/open", json={"out_dir": os.path.join(ROOT, "case")},
                  headers=H).get_json()["job_id"]
case = gui._case_for(job)
digests = sorted(f["sha256"] for f in gui.JOBS[job]["files"])
page, others = digests[:6], digests[6:]
U, NP = case.uncategorized, case.non_pertinent


def store_says_non_pertinent(which):
    """A store pass: every file named is Non-Pertinent in the store, as the
    same phone's pictures were in the examiner's earlier case."""
    lookup = {d: [{"kind": "case", "category": NP, "set": "Phone A, Graykey",
                   "via": "store", "region": case.region}] for d in which}
    return gui._apply_hash_categories(job, lookup)


def grade(which, code):
    return client.post("/api/case/categorize", headers=H,
                       json={"job": job, "sha256": which, "category": code}).get_json()


def now(which):
    decided = case.decisions()
    return [(decided.get(d) or {}).get("category", U) for d in which]


print("The page: two already Uncategorized by hand, four never looked at")
grade(page[:2], U)
check("all six show 8", now(page) == [U] * 6, now(page))

print("Graded 1, then 2 by mistake, while the store works on the other files")
grade(page, 1)
answer = store_says_non_pertinent(others)
check("the store graded the files nobody had", answer["categorized"] == 4
      and now(others) == [NP] * 4, answer)
grade(page, 2)
store_says_non_pertinent(others)              # nothing left for it: all graded
steps = client.get(f"/api/case/undo?job={job}").get_json()["batches"]
check("Undo offers the examiner's gradings, not the store's",
      steps and all("hash set" not in b["detail"] for b in steps),
      [b["detail"] for b in steps])
check("the store's pass of four files was one step, not four",
      sum(1 for b in case._con.execute(
          "SELECT detail FROM undo_batches WHERE detail LIKE '%Phone A%'")) == 1)

print("Ctrl-Z")
undone = client.post("/api/case/undo", json={"job": job}, headers=H).get_json()
check("it takes back the 2", undone.get("undone") == 6 and now(page) == [1] * 6,
      (undone.get("undone"), now(page)))
undone = client.post("/api/case/undo", json={"job": job}, headers=H).get_json()
check("and again, the 1: the page is 8 again", now(page) == [U] * 6, now(page))
check("the store's grades of the other files are left alone", now(others) == [NP] * 4)

print("The next store pass, which knows the page's files as Non-Pertinent")
answer = store_says_non_pertinent(page)
check("the page stays 8: the examiner put it there", now(page) == [U] * 6,
      (now(page), answer))
grade(page[:1], 1)
check("a file of it graded again is graded", now(page[:1]) == [1])
undone = client.post("/api/case/undo", json={"job": job}, headers=H).get_json()
check("and undone again, it is 8 again", now(page[:1]) == [U], now(page[:1]))

print("A hundred store passes after the examiner's grading")
grade(page, 3)
fresh = [d for d in others]
for n in range(100):
    case.set_category(fresh[n % 4], NP if n % 2 else U, by="hash set: Phone A",
                      only_if_undecided=True)
steps = client.get(f"/api/case/undo?job={job}").get_json()["batches"]
check("the examiner's grading is still the first thing Undo offers",
      steps and steps[0]["detail"] == "category 3", steps[:1])
undone = client.post("/api/case/undo", json={"job": job}, headers=H).get_json()
check("and Ctrl-Z takes it back", now(page) == [U] * 6, now(page))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
