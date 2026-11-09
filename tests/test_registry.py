"""The case list: register, survive a restart, find by scanning, reopen.

The registry holds no evidence and no decisions -- it is a table of contents
over output folders. These checks are mostly about that boundary: losing it
must cost nothing but the list, and it must never claim a case is there when
the folder has gone.
"""
import tempfile
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.review import casedb, registry

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "registry_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

ok = True


def make_case(name, case_number, graded):
    """An output folder shaped like one a run leaves behind."""
    folder = os.path.join(ROOT, name)
    os.makedirs(folder)
    records = [{"out_path": f"images/jpg/{i}.jpg",
                "category": "image" if i % 3 else "video", "ext": "jpg",
                "size": 1000 + i, "md5": f"{i:032x}", "sha1": f"{i:040x}",
                "sha256": f"{i:064x}", "engine": "carve", "source_path": "",
                "signature": "jpeg", "offset": i, "deleted": False,
                "note": "", "instances": 1} for i in range(9)]
    json.dump({"summary": {"image": f"/evidence/{name}.E01"},
               "files": records, "duplicates": []},
              open(os.path.join(folder, "manifest.json"), "w"))
    case = casedb.open_case(folder, examiner="J. Smith", case_number=case_number)
    for record in records[:graded]:
        case.set_category(record["sha256"], 1)
    case.close()
    return folder


first = make_case("run_a", "DC26-253", 3)
second = make_case("run_b", "DC26-301", 0)

book = registry.CaseRegistry(os.path.join(ROOT, "cases.db"))
book.register(first, case_number="DC26-253", evidence="1TB WD",
              examiner="J. Smith")
listed = book.cases()
print(f"registered one by hand: {len(listed)} case(s)")
ok &= len(listed) == 1 and listed[0]["files"] == 9
print(f"  counts read from the manifest: {listed[0]['files']} files, "
      f"{listed[0]['images']} images, {listed[0]['categorized']} graded")
ok &= listed[0]["categorized"] == 3

# Scanning finds the other one and reads its details from its own files.
found = book.scan(ROOT)
numbers = sorted(c["case_number"] for c in book.cases())
print(f"\nscanning found {len(found)}; the list now holds {numbers}")
ok &= numbers == ["DC26-253", "DC26-301"]
discovered = [c for c in book.cases() if c["case_number"] == "DC26-301"][0]
ok &= discovered["examiner"] == "J. Smith"
print(f"  the discovered case brought its examiner: {discovered['examiner']!r}")

# A case number typed by a person must not be blanked by a later register().
book.register(first, source="/evidence/run_a.E01")
kept = book.get(first)["case_number"]
ok &= kept == "DC26-253"
print(f"\nre-registering without a case number keeps it: {kept!r}")

# Grading more files changes what the list reports, because it is read back
# from the case rather than remembered.
case = casedb.CaseDB(os.path.join(first, "case.db"))
case.set_category("4" * 64, 5)
case.close()
book.refresh(first)
print(f"after grading one more: {book.get(first)['categorized']} graded")
ok &= book.get(first)["categorized"] == 4

# A folder that has moved is reported missing, not offered.
shutil.move(second, os.path.join(ROOT, "moved_away"))
gone = [c for c in book.cases() if c["case_number"] == "DC26-301"][0]
ok &= gone["present"] is False
print(f"\nfolder moved away -> present={gone['present']}")

# Losing the registry costs the list and nothing else.
book.close()
os.remove(os.path.join(ROOT, "cases.db"))
for suffix in ("-wal", "-shm"):
    side = os.path.join(ROOT, "cases.db" + suffix)
    if os.path.exists(side):
        os.remove(side)
rebuilt = registry.CaseRegistry(os.path.join(ROOT, "cases.db"))
again = rebuilt.scan(ROOT)
print(f"registry deleted and rebuilt by scanning: {len(again)} case(s) back")
ok &= len(again) == 2
case = casedb.CaseDB(os.path.join(first, "case.db"))
ok &= case.decision("0" * 64)["category"] == 1
print(f"  and the decisions were never in it: category "
      f"{case.decision('0' * 64)['category']} still on the file")
case.close()

ok &= rebuilt.forget(first)
ok &= os.path.isfile(os.path.join(first, "manifest.json"))
print(f"\nforgetting a case leaves its output alone: "
      f"{os.path.isfile(os.path.join(first, 'manifest.json'))}")
rebuilt.close()

# Moving a case to a share is ordinary housekeeping. The list records where
# output was, not a copy of it, so a move has to be recognized rather than
# producing a second entry for the same case -- and everything the list
# knows, the case number somebody typed included, has to travel with it.
print("\nmoving a case")
moved_from = os.path.join(ROOT, "run_a")
central = os.path.join(ROOT, "central")
os.makedirs(central, exist_ok=True)

fresh = registry.CaseRegistry(os.path.join(ROOT, "moving.db"))
fresh.register(moved_from, case_number="DC26-253", evidence="1TB WD",
               examiner="Det. C. T.")
before = fresh.cases()[0]
print(f"  before: {before['case_number']} at {os.path.basename(before['out_dir'])}, "
      f"{before['files']} files, {before['categorized']} graded")

moved_to = os.path.join(central, "DC26-253")
shutil.move(moved_from, moved_to)
gone = fresh.cases()[0]
ok &= gone["present"] is False
print(f"  after the move it shows as missing: {gone['present'] is False}")

found = fresh.scan(central)
relinked = [entry for entry in found if entry.get("relinked_from")]
ok &= len(relinked) == 1
print(f"  scanning the new place relinked {len(relinked)} case(s)")

listed = fresh.cases()
ok &= len(listed) == 1
print(f"  the list holds {len(listed)} entry, not two")
survivor = listed[0]
ok &= survivor["case_number"] == "DC26-253"
ok &= survivor["examiner"] == "Det. C. T."
ok &= survivor["categorized"] == before["categorized"]
ok &= survivor["present"] is True
print(f"  and it kept its number, examiner and {survivor['categorized']} "
      f"grading(s)")

# A fingerprint is what identifies a case, so a different case in the same
# place must not be mistaken for it.
other = os.path.join(central, "DC26-999")
os.makedirs(other, exist_ok=True)
with open(os.path.join(other, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "/evidence/other.E01"},
               "files": [{"out_path": "images/jpg/x.jpg", "category": "image",
                          "sha256": "f" * 64, "md5": "f" * 32, "size": 1}],
               "duplicates": []}, fh)
fresh.scan(central)
ok &= len(fresh.cases()) == 2
print(f"  a different case is added, not mistaken for it: "
      f"{len(fresh.cases()) == 2}")

# And relinking by hand, which is what someone does when the folder was
# moved somewhere a scan will not reach.
by_hand = os.path.join(ROOT, "elsewhere")
shutil.move(moved_to, by_hand)
fresh.relink(moved_to, by_hand)
pointed = [c for c in fresh.cases() if c["case_number"] == "DC26-253"][0]
ok &= os.path.abspath(pointed["out_dir"]) == os.path.abspath(by_hand)
ok &= pointed["present"] is True
print(f"  relinking by hand points it at {os.path.basename(by_hand)}: "
      f"{pointed['present']}")
fresh.close()

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
