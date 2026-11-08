"""The hand-over report, and exporting the files themselves.

Two different acts. A report describes what was found; an export hands over
the material. Doing the second should not require doing the first.
"""
import tempfile
import csv
import json
import os
import shutil
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fme.exports import docx_parts, export_files, report
from fme.review import casedb

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "protocol_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ok = True

try:
    from PIL import Image
except ImportError:
    print("Pillow is not installed; skipping")
    sys.exit(0)

# Real images, because the appendix embeds them and a fixture that cannot
# be decoded would pass a test that proves nothing.
source = os.path.join(ROOT, "src")
os.makedirs(source)
for index in range(5):
    picture = Image.new("RGB", (320, 240))
    pixels = picture.load()
    for y in range(240):
        for x in range(320):
            pixels[x, y] = ((x * 2 + index * 30) % 256, (y * 3) % 256,
                            (x + y + index * 20) % 256)
    picture.save(os.path.join(source, f"photo{index}.jpg"), quality=85)

run = os.path.join(ROOT, "run")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", source,
                "-o", run], capture_output=True, cwd=PROJECT)
with open(os.path.join(run, "manifest.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)

case = casedb.open_case(run, examiner="Det. C. T.", case_number="DC25-450")
flags = case.flags()
for index, record in enumerate(manifest["files"]):
    case.set_category(record["sha256"], 1 if index < 3 else 5)
    if index < 2:
        case.set_file_flag(record["sha256"], flags[1]["id"], True)
case.close()

print("the protocol report")
dest = os.path.join(ROOT, "report")
built = report.build(run, dest, formats=["protocol"], thumbnails=True,
                     header={"agency": "Delaware County DA — CID",
                             "case_number": "DC25-450",
                             "examiner": "Det. C. T.",
                             "place": "Media, Pennsylvania"},
                     scope={"categories": [1],
                            "include_uncategorized": False})
written = built["written"][0]
ok &= os.path.isfile(written["path"])
print(f"  written: {os.path.basename(written['path'])} "
      f"({written['bytes']:,} bytes, {written['images']} picture(s))")

# It has to be a real Word document, not merely a zip. A report that opens
# on the examiner's machine and not on the prosecutor's is worse than none.
ok &= zipfile.is_zipfile(written["path"])
try:
    import docx

    document = docx.Document(written["path"])
    headings = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    tables = len(document.tables)
    pictures = len(document.inline_shapes)
    print(f"  opens in a Word reader: {tables} table(s), "
          f"{pictures} embedded picture(s)")
    for wanted in ("Conclusion", "Appendix 2. Definitions",
                   "Appendix 3. File list"):
        there = any(wanted in line for line in headings)
        ok &= there
        print(f"    {wanted:32} {'present' if there else 'MISSING'}")
    # Three thumbnails and the contents ring. The ring is counted
    # explicitly rather than folded into the number, so a change to
    # either is visible here.
    ok &= pictures == 4
    with zipfile.ZipFile(written["path"]) as archive:
        has_ring = any("contents" in name for name in archive.namelist())
    ok &= has_ring
    print(f"    {'the contents ring is embedded':32} "
          f"{'present' if has_ring else 'MISSING'}")
except ImportError:
    print("  (python-docx not installed; structure checked only)")

# The scope has to hold: a report of one category must not name another.
with zipfile.ZipFile(written["path"]) as archive:
    body = archive.read("word/document.xml").decode("utf-8", "replace")
leaked = [name for name in ("Adult", "Non-Pertinent") if name in body]
ok &= not leaked
print(f"  no other category appears in it: {not leaked}")

# Counts in the document, not just in the summary object.
ok &= "Unique" in body and "Images" in body
print(f"  the conclusion table is in the document: "
      f"{'Unique' in body and 'Images' in body}")

print("\nexporting the files themselves")
rows = report.gather(run, categories=[1], include_uncategorized=False)
out = os.path.join(ROOT, "export")
result = export_files.export(rows["rows"], rows["out_dir"], out)
ok &= result["written"] == 3
print(f"  wrote {result['written']} file(s) into "
      f"{len(result['categories'])} folder(s): {result['categories']}")

# Every copy is hashed as it is written and checked against the hash taken
# at carve time. A copy that is not the file must be reported, not handed
# over quietly.
ok &= not result["mismatched"] and not result["failed"]
print(f"  every copy matched its recorded hash: {not result['mismatched']}")

with open(result["manifest"], encoding="utf-8") as fh:
    listed = list(csv.DictReader(fh))
ok &= len(listed) == 3
ok &= all(row["hash_verified"] == "True" for row in listed)
ok &= all(row["sha256"] and row["md5"] and row["category"] for row in listed)
print(f"  the manifest records {len(listed)} file(s) with their hashes and "
      f"categories")

# An export into a folder that already holds files stops before writing
# anything: two exports mixed together cannot be told apart afterwards.
try:
    export_files.export(rows["rows"], rows["out_dir"], out)
    refused = False
except ValueError:
    refused = True
ok &= refused
print(f"  it refuses to write into a folder that is not empty: {refused}")

# A table has to carry its column grid, or readers other than Word refuse
# the whole document.
grid = docx_parts.table([["a", "b"], ["1", "2"]])
ok &= "<w:tblGrid>" in grid
print(f"\n  tables carry the grid other readers require: "
      f"{'<w:tblGrid>' in grid}")

# A submission to NCMEC asks for images and videos separately, and separate
# folders make the counts checkable besides: a folder of 703 files should be
# the 703 the report says.
print("\nimages and videos in separate folders")
mixed = report.gather(run)
split_dir = os.path.join(ROOT, "split")
shutil.rmtree(split_dir, ignore_errors=True)
split = export_files.export(mixed["rows"], mixed["out_dir"], split_dir)
print(f"  wrote {split['written']} file(s) into:")
for folder in split["folders"]:
    print(f"     {folder}")

images_folders = [f for f in split["folders"] if f.endswith("Images")]
ok &= bool(images_folders)
print(f"  category and kind are both in the folder name: {bool(images_folders)}")

# The name has to stay readable: a folder an examiner cannot match against
# the report beside it is a folder they will mis-file.
readable = any("(CAM)" in folder for folder in split["folders"])
ok &= readable
print(f"  punctuation Windows allows is kept, so \"(CAM)\" survives: "
      f"{readable}")

# Everything Windows actually refuses must still go.
cleaned = export_files._folder_name('bad: "name" | here')
ok &= not any(ch in cleaned for ch in '<>:"/\\|?*')
print(f"  and what Windows refuses is removed: {cleaned!r}")

# Turning the split off puts a category back in one folder.
flat_dir = os.path.join(ROOT, "flat")
shutil.rmtree(flat_dir, ignore_errors=True)
flat = export_files.export(mixed["rows"], mixed["out_dir"], flat_dir,
                           by_kind=False)
ok &= all(not f.endswith(("Images", "Videos")) for f in flat["folders"])
print(f"  with the split off: {flat['folders']}")

# The unit's logo on the report header. A prosecutor should see whose work
# they are reading without hunting for the agency line.
print("\nthe agency logo")
logo = os.path.join(ROOT, "logo.png")
Image.new("RGB", (240, 80), (12, 60, 120)).save(logo)
with_logo = os.path.join(ROOT, "report_logo")
shutil.rmtree(with_logo, ignore_errors=True)
built = report.build(run, with_logo, formats=["protocol"],
                     header={"agency": "County DA", "case_number": "DC25-450",
                             "logo": logo})
with zipfile.ZipFile(built["written"][0]["path"]) as archive:
    embedded = [name for name in archive.namelist()
                if name.startswith("word/media/")]
has_logo = any("logo" in name for name in embedded)
ok &= has_logo
print(f"  the logo is embedded in the document: {has_logo}")

# A missing or unreadable logo must not cost the report. The header is
# where a report is least able to afford an exception.
missing = os.path.join(ROOT, "report_nologo")
shutil.rmtree(missing, ignore_errors=True)
survived = report.build(run, missing, formats=["protocol"],
                        header={"agency": "County DA", "logo": "/nope.png"})
ok &= os.path.isfile(survived["written"][0]["path"])
print(f"  a logo that is not there does not stop the report: "
      f"{os.path.isfile(survived['written'][0]['path'])}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
