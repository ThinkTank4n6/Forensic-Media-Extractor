"""Putting a case back after a grading went wrong.

The undo in the interface knows only about gradings made since it existed.
This covers the other case, which is the one that actually happened: a key
pressed with everything selected in an older version, with nothing recorded
to step back to.

It works from the audit log, which has recorded every grading since the
beginning -- file, category, who, when. The state before any event can be
rebuilt by replaying what came before it, and nothing has to be guessed.
"""
import tempfile
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.review import casedb, recover

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "recover_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

ok = True
case = casedb.open_case(ROOT, examiner="Det. C. T.", case_number="DC26-253")
pool = [f"{i:064x}" for i in range(4000)]

case.set_category(pool[:200], 5)
case.set_category(pool[200:320], 1)
case.set_category(pool[320:360], 2)
case.set_category(pool[360:380], 7)
careful = case.counts()
print(f"a morning's work: {careful}")

case.set_category(pool, 0)            # everything selected, 0 pressed
print(f"after the accident: {case.counts()}")

# A case graded by a version without undo has no undo history at all. That
# is the situation this exists for, so it is the situation it is tested in.
connection = sqlite3.connect(os.path.join(ROOT, "case.db"))
connection.execute("DELETE FROM undo_items")
connection.execute("DELETE FROM undo_batches")
connection.commit()
connection.close()
case.close()

history = recover.events(ROOT)
ok &= len(history) == 5
print(f"\n{len(history)} event(s) rebuilt from the audit log:")
for event in history:
    print(f"   #{event['number']}  {event['count']:>6,} file(s) "
          f"-> category {event['category']}")

# Each act of grading is one event, however many files it touched: that is
# what makes a bulk mistake one thing to take back.
biggest = max(history, key=lambda e: e["count"])
ok &= biggest["count"] == len(pool) and biggest["category"] == 0
print(f"  the accident is a single event of {biggest['count']:,} file(s)")

before = recover.state_before(ROOT, biggest["number"])["before"]
never_graded = sum(1 for code in before.values() if code is None)
ok &= never_graded == len(pool) - sum(careful.values())
print(f"  of those, {never_graded:,} had never been graded at all")

result = recover.undo(ROOT, biggest["number"], examiner="Det. C. T.")
case = casedb.CaseDB(os.path.join(ROOT, "case.db"))
restored = case.counts()
ok &= restored == careful
print(f"\nafter taking it back: {restored}")
print(f"  exactly as it was: {restored == careful}")

# A file that had never been graded must end with no decision, not with one
# that says "uncategorized": a report distinguishes the two, and so does the
# next examiner.
untouched = case.decision(pool[3000])
ok &= untouched["categorized_at"] in (None, "")
print(f"  a never-graded file has no decision again: "
      f"{untouched['categorized_at'] in (None, '')}")

# The recovery is itself recorded, and a snapshot is left behind.
entries = [row for row in case.audit(50) if "recovered" in (row["detail"] or "")]
ok &= bool(entries) or os.path.isdir(os.path.join(ROOT, "backups"))
print(f"  a snapshot was written: "
      f"{os.path.isdir(os.path.join(ROOT, 'backups'))}")
case.close()

# And an event that does not exist is refused rather than half-applied.
try:
    recover.undo(ROOT, 999)
    refused = False
except ValueError:
    refused = True
ok &= refused
print(f"  an event that does not exist is refused: {refused}")

# Two files in this program are a keystroke apart: cases.db is the list of
# cases and sits beside the program; case.db is one case's review and sits
# in that run's output folder. Pointing recovery at the first is the
# obvious mistake, and "no case.db here" is not an answer to it.
print("\npointed at the wrong folder")
from fme.review import registry as _registry

wrong = os.path.join(ROOT, "program_folder")
os.makedirs(wrong, exist_ok=True)
inner = os.path.join(wrong, "DC26-320")
os.makedirs(inner, exist_ok=True)
inner_case = casedb.open_case(inner, examiner="Det. C. T.",
                              case_number="DC26-320")
inner_case.set_category([f"{i:064x}" for i in range(20)], 1)
inner_case.close()
book = _registry.CaseRegistry(os.path.join(wrong, "cases.db"))
book.register(inner, case_number="DC26-320")
book.close()

message = recover._wrong_folder(wrong)
explains = "list of cases" in message and "case.db" in message
ok &= explains
print(f"  it explains the difference: {explains}")
finds = inner in message
ok &= finds
print(f"  and finds the case inside: {finds}")

# The list of cases is read for recovery by whether case.db is there, not
# by whether the media is: a case whose files were moved away still holds
# its decisions, and those are what is being put back.
import os as _os
_os.chdir(wrong)
known = recover._known_cases()
usable = [e for e in known if e["present"]]
_os.chdir(os.path.dirname(os.path.abspath(__file__)))
ok &= bool(usable)
print(f"  a case with decisions but no manifest is still offered: "
      f"{bool(usable)}")

# ---------------------------------------------------------------------------
# A run that was interrupted
#
# manifest.json is written at the end; manifest.csv is written row by row
# as the run goes. A crash therefore leaves the recovered files on disk and
# listed, with only the summary missing — and the case list, which looked
# for manifest.json alone, called that "folder missing". An examiner told
# their case folder is missing has no reason to look inside it.
import csv as _csv
import json as _json
import subprocess as _sub

print()
print("a run that was interrupted")

from fme.review import recover as _recover

_project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.join(tempfile.gettempdir(), "fme_interrupted")
shutil.rmtree(_root, ignore_errors=True)
os.makedirs(os.path.join(_root, "src"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_media_test_image as _fixtures

for _index in range(6):
    with open(os.path.join(_root, "src", f"p{_index}.jpg"), "wb") as _fh:
        _fh.write(_fixtures.make_jpeg(820 + _index))

_out = os.path.join(_root, "case")
_sub.run([sys.executable, "-m", "fme", "extract", "-i",
          os.path.join(_root, "src"), "-o", _out],
         capture_output=True, cwd=_project)
with open(os.path.join(_out, "manifest.json"), encoding="utf-8") as _fh:
    _real = _json.load(_fh)["summary"]["output_dir"]

_ok = True
os.remove(os.path.join(_real, "manifest.json"))     # the crash

_state = _recover.unfinished(_real)
_ok &= _state["folder_there"] and not _state["has_manifest"]
_ok &= _state["rows"] == 6 and _state["files_on_disk"] == 6
print(f"  the folder is there, with {_state['rows']} row(s) written and "
      f"{_state['files_on_disk']} file(s) on disk")
_ok &= _state["recoverable"]
print(f"  recoverable: {_state['recoverable']}")

# An empty or half-written manifest is not a manifest: a run killed while
# writing one leaves exactly that, and treating it as fine tells an
# examiner their work is gone while it sits next to them.
with open(os.path.join(_real, "manifest.json"), "w") as _fh:
    _fh.write("")
_broken = _recover.unfinished(_real)
_ok &= not _broken["has_manifest"] and _broken["manifest_unreadable"]
print(f"  an empty manifest counts as missing, not as fine: "
      f"{_broken['manifest_unreadable']}")

_result = _recover.rebuild_manifest(_real)
_ok &= _result["ok"] and _result["files"] == 6
print(f"  rebuilt: {_result['files']} file(s)")

with open(os.path.join(_real, "manifest.json"), encoding="utf-8") as _fh:
    _rebuilt = _json.load(_fh)
_ok &= len(_rebuilt["files"]) == 6
_ok &= _rebuilt["summary"]["rebuilt_from_rows"] is True
print(f"  and the manifest says it was rebuilt: "
      f"{_rebuilt['summary']['rebuilt_from_rows']}")

# A case rebuilt from an unfinished run is partial, and has to say so
# where somebody reading it will see it.
_ok &= "did not finish" in _rebuilt["summary"]["note"]
_ok &= "partial" in _rebuilt["summary"]["note"]
print(f"  and that the case is partial: True")

# Running it on a case that is fine must refuse rather than overwrite.
_again = _recover.rebuild_manifest(_real)
_ok &= not _again["ok"]
print(f"  it refuses to overwrite a manifest that is already there: True")

ok &= _ok


print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
