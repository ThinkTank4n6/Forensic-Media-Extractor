"""A Project VIC match keeps its series and flags, wherever it was made, and
one kind of check never erases another's matches.

- Checking a case against a set loaded for the session saved its matches
  over the case's whole list: every match the shared store had made --
  MediaID, Series, Tags and flags -- was gone from the case and from every
  report written after.
- A VICS file loaded for the session kept only "category N" per hash. Its
  matches had no MediaID, Series, Tags or flags for the details pane, and
  no category auto-categorize could use, so it categorized nothing.
- Neither match CSV had a column for any of them.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import csv
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.review import hashsets  # noqa: E402
from fme.web import media_gui  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_vic_detail-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
A, B, C = "a" * 64, "b" * 64, "c" * 64


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


files = [{"out_path": "images/jpg/a.jpg", "sha256": A, "md5": "1" * 32, "size": 1},
         {"out_path": "images/jpg/b.jpg", "sha256": B, "md5": "2" * 32, "size": 1},
         {"out_path": "images/jpg/c.jpg", "sha256": C, "md5": "3" * 32, "size": 1}]
with open(os.path.join(ROOT, "manifest.json"), "w") as fh:
    json.dump({"summary": {}, "files": files, "duplicates": []}, fh)
vics = os.path.join(ROOT, "vics.json")
with open(vics, "w") as fh:
    json.dump({"@odata.context": "http://github.com/ICMEC/ProjectVic/$metadata#Media",
               "value": [
                   {"MediaID": 77, "Category": 1, "MD5": "2" * 32, "Series": "SERIES-B",
                    "Tags": ["tagX", "tagY"], "VictimIdentified": True,
                    "IsDistributed": True},
                   # Only a SHA256, which the session loader did not read.
                   {"MediaID": 78, "Category": 2, "SHA256": C, "Series": "SERIES-C"}]},
              fh)

job = "e" * 32
media_gui.JOBS[job] = {"out_dir": ROOT, "status": "done",
                       "files": [dict(f) for f in files]}
case = media_gui._case_for(job)
store_match = {"set": "unit store", "kind": "projectvic", "category": 1, "via": "store",
               "note": "category 1", "record": {"MediaID": 5, "Series": "SERIES-A"}}
case.save_derived("hash_matches", {A: [store_match]})
media_gui.JOBS[job]["hash_matches"] = {A: [store_match]}

media_gui.HASH_SETS[:] = [hashsets.load(vics)]
client = media_gui.app.test_client()
HEADERS = {"X-FME-Request": "1", "Content-Type": "application/json"}


def scan():
    return client.post("/api/hashsets/scan", headers=HEADERS,
                       data=json.dumps({"job": job, "autocategorize": True}))


answer = scan()
check("the session scan runs", answer.status_code == 200, str(answer.status_code))
saved = case.derived("hash_matches")

print("\nthe store's match, made before")
check("is still saved in the case", [m.get("record", {}).get("Series")
                                     for m in saved.get(A, [])] == ["SERIES-A"])
check("and still in the running job", A in media_gui.JOBS[job]["hash_matches"])

print("\nthe session set's matches")
b = (saved.get(B) or [{}])[0]
record = b.get("record") or {}
check("carry the series, tags and flags",
      record.get("Series") == "SERIES-B" and record.get("Tags") == "tagX, tagY"
      and record.get("VictimIdentified") is True and record.get("IsDistributed") is True
      and record.get("MediaID") == 77, json.dumps(record))
check("carry the category as a number", b.get("category") == 1)
check("match an entry that has only a SHA256",
      [m.get("record", {}).get("Series") for m in saved.get(C, [])] == ["SERIES-C"])
decision = case.decision(B)
check("and auto-categorize uses them", decision.get("category") == 1
      and "vics.json" in (decision.get("categorized_by") or ""), str(decision))
details = client.get(f"/api/details?job={job}&file=images/jpg/b.jpg")
if details.status_code == 200:
    shown = (details.get_json().get("hash_sets") or [{}])[0].get("record") or {}
    check("the details pane is given them", shown.get("Series") == "SERIES-B")

print("\nchecking again")
scan()
again = case.derived("hash_matches")
check("replaces the session's own matches rather than adding copies",
      len(again.get(B, [])) == 1 and len(again.get(A, [])) == 1)

print("\nthe store checked again afterwards")
merged = hashsets.merge_matches(again, {A: [dict(store_match)]}, {"store", "stash"})
check("keeps the session's matches", len(merged.get(B, [])) == 1
      and len(merged.get(A, [])) == 1)
legacy = {A: [{"set": "s", "kind": "projectvic", "note": "", "matched_hash": "x"}],
          B: [{"set": "u", "kind": "projectvic", "note": "", "record": {}}]}
merged = hashsets.merge_matches(legacy, {}, {"session"})
check("matches saved before origins were recorded are told apart",
      A not in merged and B in merged)

print("\nthe match CSV")
with open(os.path.join(ROOT, "hash_matches.csv"), newline="", encoding="utf-8") as fh:
    rows = {r["sha256"]: r for r in csv.DictReader(fh)}
header = list(next(iter(rows.values())).keys())
check("keeps its first nine columns where they were",
      header[:9] == ["out_path", "source_path", "size", "md5", "sha1", "sha256",
                     "in_sets", "kinds", "notes"])
row = rows[B]
check("has the series, tags and flags",
      row.get("vic_series") == "SERIES-B" and row.get("vic_tags") == "tagX, tagY"
      and row.get("vic_victim_identified") == "True"
      and row.get("vic_media_id") == "77", str({k: v for k, v in row.items()
                                                if k.startswith("vic_")}))

media_gui.HASH_SETS[:] = []
with media_gui.JOBS_LOCK:
    media_gui.JOBS.pop(job, None)
shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
