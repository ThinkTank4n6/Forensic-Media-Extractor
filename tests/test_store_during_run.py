"""A run is checked against the hash store as it goes, and when it ends --
with or without a page (v2026.11.336).

Reported: a phone extracted with Graykey was graded in full and contributed
to the store; the same phone's pictures run again came in "Uncategorized",
129,230 of them, though the case was being run against the store. The store
was asked only once a run had finished, and only by the page pressing its own
button: nothing during the run, and nothing at all if the page was closed,
reloaded or on another case when the run ended.

  * case A graded and contributed; case B holds the same pictures (renamed)
    and new ones, run with no page: when it ends every known picture has
    A's category, set by a hash set, and the new ones are Uncategorized;
  * a picture graded by hand in B before the check is left as graded;
  * checked in batches while a run goes (the matcher itself, fed as the
    run feeds it): each batch is graded as it is checked, and a later batch
    does not erase the matches an earlier one made;
  * with no store set up, nothing is made by asking.
"""
import _sandbox  # noqa: F401
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import make_media_test_image as fixtures  # noqa: E402
from fme.review import hashstore  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "store_during_run-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
pictures = [fixtures.make_jpeg(6 + n) for n in range(12)]
os.makedirs(os.path.join(ROOT, "phone_a"))
os.makedirs(os.path.join(ROOT, "phone_b"))
for n, body in enumerate(pictures[:8]):
    with open(os.path.join(ROOT, "phone_a", f"IMG_{n:04d}.jpg"), "wb") as fh:
        fh.write(body)
for n, body in enumerate(pictures):          # the same eight, renamed, and four new
    with open(os.path.join(ROOT, "phone_b", f"export_{n:04d}.jpg"), "wb") as fh:
        fh.write(body)

client = gui.app.test_client()
H = {"X-FME-Request": "1"}


def run(folder):
    client.post("/api/use", json={"path": folder}, headers=H)
    job = client.post("/api/start", json={"out_dir": os.path.join(ROOT, "results")},
                      headers=H).get_json()["job_id"]
    for _ in range(600):
        state = client.get(f"/api/status?job_id={job}").get_json()
        if state["status"] in ("done", "error", "canceled"):
            break
        time.sleep(0.1)
    client.post("/api/release", json={}, headers=H)
    return job, state


print("With no store set up")
gui.STORE_PATH = os.path.join(ROOT, "no_store_here.db")
check("asking makes none", gui._store_if_set_up() is None
      and not os.path.exists(gui.STORE_PATH))

def manifest_rows(state):
    with open(os.path.join(state["out_dir"], "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)["files"]


job_a, state_a = run(os.path.join(ROOT, "phone_a"))
digests_a = [f["sha256"] for f in manifest_rows(state_a)]
client.post("/api/case/categorize", headers=H,
            json={"job": job_a, "sha256": digests_a, "category": 1})
store_path = os.path.join(ROOT, "store.db")
store = hashstore.HashStore(store_path)
case_a = gui._case_for(job_a)
with open(os.path.join(state_a["out_dir"], "manifest.json"), encoding="utf-8") as fh:
    rows_a = json.load(fh)["files"]
store.contribute_rows([{"md5": f["md5"], "sha256": f["sha256"], "category": 1,
                        "label": "graded"} for f in rows_a], "Phone A, Graykey")
store.close()
answer = client.post("/api/store", json={"path": store_path}, headers=H).get_json()
print("Case A graded and contributed:", answer.get("total"), "hashes in the store")

print("Case B, the same phone's pictures and four new, run with no page open")
job_b, state_b = run(os.path.join(ROOT, "phone_b"))
for _ in range(300):                          # the server's own check, on its thread
    scan = client.get(f"/api/store/scan?job={job_b}").get_json()
    if scan.get("state") in ("done", "failed"):
        break
    time.sleep(0.1)
check("the server checked it when it ended, with nobody asking",
      scan.get("state") == "done", scan.get("state") or scan.get("error"))
decided = gui._case_for(job_b).decisions()
rows_b = manifest_rows(state_b)
known = [f["sha256"] for f in rows_b if f["sha256"] in set(digests_a)]
new = [f["sha256"] for f in rows_b if f["sha256"] not in set(digests_a)]
check("every picture case A graded has its category",
      len(known) == 8 and all(decided.get(d, {}).get("category") == 1 for d in known),
      f"{sum(1 for d in known if decided.get(d, {}).get('category') == 1)} of {len(known)}")
check("set by a hash set, not by hand", all(decided[d].get("auto") for d in known))
check("the four new pictures are left to grade",
      len(new) == 4 and not any(decided.get(d, {}).get("category") == 1 for d in new))

print("Checked in batches, as a run feeds it")
# Case B again, fresh: no categories yet, one picture graded by hand.
shutil.rmtree(os.path.join(ROOT, "results"), ignore_errors=True)
job_c, state_c = run(os.path.join(ROOT, "phone_b"))
for _ in range(300):                          # its end-of-run check, then wipe it
    scan = client.get(f"/api/store/scan?job={job_c}").get_json()
    if scan.get("state") in ("done", "failed"):
        break
    time.sleep(0.1)
case_c = gui._case_for(job_c)
with open(os.path.join(state_c["out_dir"], "manifest.json"), encoding="utf-8") as fh:
    known_c = [f for f in json.load(fh)["files"] if f["sha256"] in set(digests_a)]
case_c.set_category([f["sha256"] for f in manifest_rows(state_c)], case_c.uncategorized)
case_c.merge_derived("hash_matches", lambda saved: {})
with gui.JOBS_LOCK:
    gui.JOBS[job_c]["hash_matches"] = {}
by_hand = known_c[0]["sha256"]
client.post("/api/case/categorize", headers=H,
            json={"job": job_c, "sha256": [by_hand], "category": 3})
gui._LiveStoreCheck.EVERY = 0.2
checker = gui._LiveStoreCheck(job_c)
checker.add(known_c[:4])
for _ in range(100):
    if checker.counts["checked"] >= 4:
        break
    time.sleep(0.05)
mid = gui._case_for(job_c).decisions()
check("the first batch is graded while the run is still going",
      all(mid.get(f["sha256"], {}).get("category") == 1 for f in known_c[1:4]),
      checker.counts)
checker.add(known_c[4:])
checker.end()
after = gui._case_for(job_c).decisions()
check("and the next batch too",
      all(after.get(f["sha256"], {}).get("category") == 1 for f in known_c[4:]))
check("the picture graded by hand is left as graded",
      after.get(by_hand, {}).get("category") == 3, after.get(by_hand))
saved = gui._case_for(job_c).derived("hash_matches") or {}
check("the first batch's matches are still kept after the second",
      all(saved.get(f["sha256"]) for f in known_c[:4]),
      sum(1 for f in known_c[:4] if saved.get(f["sha256"])))
check("the page is told how far it has got",
      (gui.JOBS[job_c].get("live_store") or {}).get("categorized", 0) >= 6,
      gui.JOBS[job_c].get("live_store"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
