"""Review state: decisions survive, the VIC scheme is locked, VIC export
carries what an examiner decided.
"""
import tempfile
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fme.review import casedb

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "casedb_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

A, B, C = "a" * 64, "b" * 64, "c" * 64
ok = True

db = casedb.open_case(ROOT, examiner="J. Smith", case_number="DC26-253")

locked = [c for c in db.categories() if c["locked"]]
print(f"grading scheme seeded and locked: {len(locked)} categories")
ok &= len(locked) == len(casedb.CATEGORIES)
# Every scheme category carries the key that applies it, including the three
# that are not digits.
keys = {c["key"] for c in locked}
ok &= {"0", "9"} <= keys
print(f"  keys: {''.join(sorted(k for k in keys if k))}")
# Uncategorized must not be 0: 0 is Non-Pertinent, which is a finding.
ok &= casedb.UNCATEGORIZED != 0
print(f"  uncategorized is {casedb.UNCATEGORIZED}, "
      f"and 0 is {[c['name'] for c in locked if c['code'] == 0][0]}")

seeded = [f["name"] for f in db.flags()]
print(f"flags seeded in a new case: {seeded}")
ok &= seeded == [name for name, _, _ in casedb.DEFAULT_FLAGS]
flag_keys = {f["key"]: f["name"] for f in db.flags() if f["key"]}
print(f"  flag keys: {flag_keys}")
ok &= set(flag_keys) == {"-", ".", "*"}
# Whether something was AI-generated is a property of the material, not a
# grade, so it must not also exist as a category.
ok &= not any("AI" in c["name"] for c in db.categories())

db.set_category(A, 1)
db.set_category([B, C], 5)
db.set_note(A, "matches DC24-118")
flag_id = db.add_flag("For review")
db.set_file_flag(A, flag_id, True)
# A seeded flag an examiner deletes must not come back on the next open.
removed = next(f for f in db.flags() if f["name"] == "Bestiality")
db.remove_flag(removed["id"])
own = db.add_category("Device owner")
print(f"examiner category starts at {own}, above the scheme's own codes")
ok &= own == casedb.FIRST_CUSTOM_CODE

print("\nthe locked scheme cannot be edited:")
for action, call in (("rename", lambda: db.rename_category(1, "Other")),
                     ("delete", lambda: db.remove_category(2))):
    try:
        call()
        print(f"  {action}: ALLOWED  <-- should have been refused")
        ok = False
    except ValueError as exc:
        print(f"  {action}: refused — {str(exc)[:58]}…")

db.close()

db = casedb.open_case(ROOT)
still_gone = "Bestiality" not in [f["name"] for f in db.flags()]
print(f"a deleted flag stays deleted after reopening: {still_gone}")
ok &= still_gone

decision = db.decision(A)
print(f"\nafter closing and reopening: category={decision['category']}, "
      f"note={decision['note']!r}, flags={decision['flags']}")
ok &= decision["category"] == 1 and decision["note"] == "matches DC24-118"
ok &= decision["flags"] == [flag_id]

print(f"counts: {db.counts()}")
ok &= db.counts().get(5) == 2

# deleting an examiner category returns its files, it does not orphan them
db.set_category(C, own)
returned = db.remove_category(own)
print(f"\ndeleting an examiner category returned {returned} file(s) to uncategorized")
ok &= returned == 1 and db.decision(C)["category"] == casedb.UNCATEGORIZED

entries = db.audit(100)
print(f"audit log holds {len(entries)} entries, most recent: {entries[0]['action']}")
ok &= len(entries) >= 8

snapshot = db.snapshot("test")
ok &= os.path.isfile(snapshot)
print(f"snapshot: {os.path.basename(snapshot)}")
db.close()

# decisions key on content, so a second run into a new folder keeps them
second = os.path.join(ROOT, "run2")
os.makedirs(second)
shutil.copy(os.path.join(ROOT, "case.db"), os.path.join(second, "case.db"))
db2 = casedb.open_case(second)
print(f"\ncarried to another output folder: category={db2.decision(A)['category']}")
ok &= db2.decision(A)["category"] == 1
db2.close()

# A case graded under the previous numbering must be moved, not reinterpreted:
# 0 meant Uncategorized and 5 meant Non-pertinent there.
import sqlite3
old_root = os.path.join(ROOT, "old_scheme")
os.makedirs(old_root)
con = sqlite3.connect(os.path.join(old_root, "case.db"))
con.executescript(casedb.SCHEMA.replace("DEFAULT 8", "DEFAULT 0")
                  .replace(",\n    key TEXT NOT NULL DEFAULT ''", ""))
con.execute("INSERT INTO meta (key, value) VALUES ('scheme_version','1')")
for sha, category in ((A, 0), (B, 5), (C, 1)):
    con.execute("INSERT INTO decisions (sha256, category, updated_at) "
                "VALUES (?,?,'x')", (sha, category))
con.commit()
con.close()

migrated = casedb.open_case(old_root)
moved = {"A": migrated.decision(A)["category"],
         "B": migrated.decision(B)["category"],
         "C": migrated.decision(C)["category"]}
print(f"\nmigrated from the previous scheme: {moved}")
expected = {"A": casedb.UNCATEGORIZED, "B": 0, "C": 1}
ok &= moved == expected
if moved != expected:
    print(f"  expected {expected}")
logged = any(r["action"] == "scheme-migrated" for r in migrated.audit(20))
ok &= logged
print(f"  recorded in the audit log: {logged}")
migrated.close()

# A case graded while the AI gradings were categories becomes a grading plus
# a flag, losing neither half.
ai_root = os.path.join(ROOT, "ai_scheme")
os.makedirs(ai_root)
con = sqlite3.connect(os.path.join(ai_root, "case.db"))
con.executescript(casedb.SCHEMA)
con.execute("INSERT INTO meta (key, value) VALUES ('scheme_version','2')")
con.execute("INSERT INTO meta (key, value) VALUES ('flags_seeded','x')")
for code, name in ((10, "AI CSAM"), (11, "AI Age Difficult"), (12, "AI Adult")):
    con.execute("INSERT INTO categories (code, name, color, locked, position, key) "
                "VALUES (?,?,'#000',1,?,'')", (code, name, code))
for sha, code in ((A, 10), (B, 11), (C, 12)):
    con.execute("INSERT INTO decisions (sha256, category, updated_at) "
                "VALUES (?,?,'x')", (sha, code))
con.commit()
con.close()

ai = casedb.open_case(ai_root)
flag_names = {f["id"]: f["name"] for f in ai.flags()}
print("\nAI categories became a grading plus a flag:")
for sha, label, want_code, want_flag in (
        (A, "AI CSAM", 1, "AI CSAM"),
        (B, "AI Age Difficult", 2, "AI Age Difficult"),
        (C, "AI Adult", 5, "AI Adult")):
    decided = ai.decision(sha)
    carried = [flag_names[i] for i in decided["flags"]]
    good = decided["category"] == want_code and want_flag in carried
    ok &= good
    print(f"  {label:18} -> category {decided['category']}, flags {carried}"
          f"{'' if good else '   <-- expected ' + str(want_code)}")
ok &= not any(c["code"] in (10, 11, 12) for c in ai.categories())
ai.close()

# A category a hash set asserted and one an examiner chose are different
# claims, and everything downstream has to be able to tell them apart.
auto_root = os.path.join(ROOT, "auto")
os.makedirs(auto_root)
auto = casedb.open_case(auto_root, examiner="Det. C. T.")
auto.set_category(A, 1)                                   # a person
auto.set_category(B, 0, by="hash set: NSRL (known software)")
auto.set_category(C, 2, by="hash set: VIC_US_2026")
decisions = auto.decisions()
print("\nwho decided what:")
for label, digest, expect_auto in (("examiner", A, False),
                                   ("NSRL", B, True),
                                   ("Project VIC", C, True)):
    decided = decisions[digest]
    good = decided["auto"] is expect_auto
    ok &= good
    print(f"  {label:12} category {decided['category']}  auto={decided['auto']}  "
          f"by {decided['by']!r}{'' if good else '  <-- wrong'}")
# The audit log has to show it too, or a later reader cannot reconstruct it.
logged = [r for r in auto.audit(20) if "hash set" in r["detail"]]
ok &= len(logged) == 2
print(f"  recorded in the audit log: {len(logged)} entr(y/ies)")
auto.close()

# Grading is the one thing that happens thousands of times in a case, so
# what it costs matters more than what anything else costs. The work that
# used to ride along with it -- re-reading a sixty-megabyte manifest to
# refresh a count, and sending every decision in the case back to the page
# -- is why a keystroke took half a second on a large exhibit.
import time as _time

speed = casedb.open_case(os.path.join(ROOT, "speed"), examiner="Det. C. T.")
digests = [f"{i:064x}" for i in range(5000)]
started = _time.time()
speed.set_category(digests, 0)
bulk = _time.time() - started

started = _time.time()
for digest in digests[:200]:
    speed.set_category(digest, 5)
singles = (_time.time() - started) / 200
speed.close()

print(f"\ngrading speed")
print(f"  5,000 files in one call : {bulk * 1000:.0f} ms "
      f"({bulk / 5000 * 1000:.2f} ms each)")
print(f"  one file at a time      : {singles * 1000:.2f} ms each")
# A tenth of a second per keystroke would be felt; this is far below it.
ok &= singles < 0.05
print(f"  fast enough to keep up with typing: {singles < 0.05}")

# The server reads the case from several request threads while an
# occasional snapshot runs on another. One SQLite connection shared between
# them, with reads taken outside the lock, fails as "bad parameter or other
# API misuse" -- an error that says nothing about what actually went wrong,
# and which shows up as the interface quietly refusing to categorize.
import threading as _threading

concurrent = casedb.open_case(os.path.join(ROOT, "concurrent"),
                              examiner="Det. C. T.")
many = [f"{i:064x}" for i in range(20000)]
concurrent.set_category(many, 1)

faults, stop = [], _threading.Event()


def _read():
    while not stop.is_set():
        try:
            concurrent.decision(many[7])
            concurrent.counts()
            concurrent.decisions()
        except Exception as exc:  # noqa: BLE001 - any failure is the point
            faults.append(f"read: {type(exc).__name__}: {exc}")
            return


def _write():
    index = 0
    while not stop.is_set():
        try:
            concurrent.set_category(many[index % len(many)], 5)
        except Exception as exc:  # noqa: BLE001
            faults.append(f"write: {type(exc).__name__}: {exc}")
            return
        index += 1


def _snapshot():
    for _ in range(4):
        try:
            concurrent.snapshot("auto")
        except Exception as exc:  # noqa: BLE001
            faults.append(f"snapshot: {type(exc).__name__}: {exc}")
    stop.set()


workers = ([_threading.Thread(target=_read) for _ in range(3)]
           + [_threading.Thread(target=_write), _threading.Thread(target=_snapshot)])
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
concurrent.close()

ok &= not faults
print(f"\nreading and writing while snapshots run: "
      f"{faults[:1] if faults else 'no faults'}")

# Grading the wrong files is the mistake with the worst consequences and no
# natural recovery: a keystroke with everything selected overwrites a
# morning's careful decisions in one go. Every grading records what it
# replaced, so it can be put back.
print("\nundo")
undo_case = casedb.open_case(os.path.join(ROOT, "undo"), examiner="Det. C. T.")
pool = [f"{i:064x}" for i in range(2000)]
undo_case.set_category(pool[:300], 5)
undo_case.set_category(pool[300:450], 1)
undo_case.set_category(pool[450:500], 2)
careful = undo_case.counts()
print(f"  a morning's work: {careful}")

undo_case.set_category(pool, 0)          # everything selected, 0 pressed
print(f"  after the accident: {undo_case.counts()}")

offered = undo_case.undoable(3)
ok &= bool(offered) and offered[0]["files"] == len(pool)
print(f"  offered to undo: {offered[0]['files']:,} file(s) — "
      f"{offered[0]['detail']}")

result = undo_case.undo()
ok &= result["undone"] == len(pool)
ok &= undo_case.counts() == careful
print(f"  undone: {result['undone']:,}; counts restored: "
      f"{undo_case.counts() == careful}")

# A file that had never been graded must end with no decision at all, not
# with a row saying "uncategorized" -- the two are different claims.
after = undo_case.decision(pool[1500])
ok &= after["category"] == casedb.UNCATEGORIZED
ok &= after["categorized_at"] in (None, "")
print(f"  a file that was never graded has no decision again: "
      f"{after['categorized_at'] in (None, '')}")

# Undo goes back more than one step.
undo_case.undo()
ok &= undo_case.counts() == {5: 300, 1: 150}
print(f"  a second undo steps further back: {undo_case.counts()}")

# And it refuses politely when there is nothing left.
for _ in range(10):
    undo_case.undo()
nothing = undo_case.undo()
ok &= nothing["undone"] == 0 and "nothing to undo" in nothing["reason"].lower()
print(f"  with nothing left: {nothing['reason']}")
undo_case.close()

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
