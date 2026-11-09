"""Reading a C4All index back in.

This program could write a C4All index and not read one, so the exchange
was one-way: an examiner handed a set by another unit, or returning to one
made years ago, had no way to bring the gradings in.

Three things decide whether an import of somebody else's judgments is safe
to have at all, and each is checked here: that C4All's "0" is read as
ungraded rather than as Non-Pertinent, that files are matched on hashes
rather than names, and that an existing grading in this case is never
quietly replaced.
"""
import tempfile
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import make_media_test_image as fixtures
from fme.exports import c4all_import
from fme.review.casedb import CaseDB

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "c4all_import_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "DVD01", "01"))
ok = True

pictures = []
for index in range(3):
    blob = fixtures.make_jpeg(600 + index)
    name = f"{index + 1:08d}.JPG"
    with open(os.path.join(ROOT, "DVD01", "01", name), "wb") as fh:
        fh.write(blob)
    pictures.append({
        "name": name, "blob": blob,
        "md5": hashlib.md5(blob).hexdigest(),
        "sha1": hashlib.sha1(blob).hexdigest(),
        "sha256": hashlib.sha256(blob).hexdigest(),
    })

# The index as C4All writes it: UTF-16, CDATA paths, Windows separators,
# and 0 for an entry nobody categorized.
rows = []
for picture, category in zip(pictures, (1, 0, 5)):
    rows.append(f"""  <Image>
    <path><![CDATA[DVD01\\01\\]]></path>
    <picture>{picture['name']}</picture>
    <category>{category}</category>
    <fullpath><![CDATA[C:\\DCIM\\{picture['name']}]]></fullpath>
    <created>1569647772</created>
    <deleted>18446744073709551615</deleted>
    <hash>{picture['md5']}</hash>
    <sha1Hash>{picture['sha1']}</sha1Hash>
    <fileSize>{len(picture['blob'])}</fileSize>
  </Image>""")
index_path = os.path.join(ROOT, "C4P Index.xml")
with open(index_path, "w", encoding="utf-16") as fh:
    fh.write('<?xml version="1.0" encoding="utf-16"?>\n'
             '<ReportIndex version="2.0">\n' + "\n".join(rows) +
             "\n</ReportIndex>\n")

print("recognizing it")
ok &= c4all_import.looks_like_index(index_path)
print(f"  a C4All index is recognized: "
      f"{c4all_import.looks_like_index(index_path)}")
not_one = os.path.join(ROOT, "something.xml")
with open(not_one, "w", encoding="utf-8") as fh:
    fh.write("<other/>")
ok &= not c4all_import.looks_like_index(not_one)
print(f"  other XML is not: {not c4all_import.looks_like_index(not_one)}")

print("\nwhat it holds")
summary = c4all_import.summarize(index_path)
ok &= summary["entries"] == 3 and summary["files_present"] == 3
print(f"  {summary['entries']} entry/entries, {summary['files_present']} "
      f"present on disk")

# C4All writes 0 for "nobody has categorized this". Reading that as
# category 0 would mark every ungraded file Non-Pertinent -- a decision
# nobody made, applied to a whole case.
ok &= summary["graded"] == 2 and 0 not in summary["by_category"]
print(f"  graded: {summary['graded']} of 3; "
      f"category 0 is not among them: {0 not in summary['by_category']}")

print("\nbringing it into a case")
case_dir = os.path.join(ROOT, "case")
os.makedirs(case_dir, exist_ok=True)
case = CaseDB(os.path.join(case_dir, "case.db"), examiner="verify")

# This case has already graded the third picture differently.
case.set_category([pictures[2]["sha256"]], 2)

files = [{"sha256": p["sha256"], "md5": p["md5"], "sha1": p["sha1"]}
         for p in pictures]
try:
    c4all_import.apply_to_case(case, files, index_path)
    asked = False
except ValueError as exc:
    asked = "scheme" in str(exc)
ok &= asked
print(f"  an index whose scheme is not stated is refused: {asked}")
try:
    c4all_import.apply_to_case(case, files, index_path, index_region="CA-EN")
    crossed = False
except ValueError as exc:
    crossed = "Canada" in str(exc)
ok &= crossed and not case.decisions().get(pictures[0]["sha256"])
print(f"  and one graded under another scheme brings nothing in: {crossed}")
result = c4all_import.apply_to_case(case, files, index_path, index_region="US")

ok &= result["applied"] == 1
print(f"  applied: {result['applied']} (the one graded there and not here)")

decided = case.decisions()
ok &= decided.get(pictures[0]["sha256"], {}).get("category") == 1
print(f"  the index's category reached the case: "
      f"{decided.get(pictures[0]['sha256'], {}).get('category')}")
by = case.decision(pictures[0]["sha256"])
ok &= by["auto"] and "C4All index" in (by["categorized_by"] or "")
print(f"  recorded as the index's, not the examiner's: {by['categorized_by']}")

ok &= pictures[1]["sha256"] not in decided or \
    decided[pictures[1]["sha256"]].get("category") is None
print(f"  the ungraded entry was left ungraded: True")

# The one that matters: an examiner's own decision is not replaced by
# another unit's, and the difference is reported instead.
ok &= decided.get(pictures[2]["sha256"], {}).get("category") == 2
ok &= len(result["disagreements"]) == 1
clash = result["disagreements"][0]
ok &= clash["this_case"] == 2 and clash["the_index"] == 5
print(f"  our grading stands ({clash['this_case']}) and theirs "
      f"({clash['the_index']}) is reported, not applied")

# Matching is by hash: two cases full of IMG_0001.JPG would collide on
# name instantly.
renamed = [{"sha256": "f" * 64, "md5": "0" * 32, "sha1": "0" * 40}]
none_applied = c4all_import.apply_to_case(case, renamed, index_path, index_region="US")
ok &= none_applied["applied"] == 0
print(f"  a file with no matching hash gets nothing: "
      f"{none_applied['applied'] == 0}")
case.close()

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
