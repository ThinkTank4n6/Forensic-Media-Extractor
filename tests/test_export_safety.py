"""What the exports write is well formed, safe to open, and their own.

Each check is a defect an outside review found:

- C4All index: a file name holding a control character XML 1.0 forbids
  (\\x01 on a FAT volume) made C4P_Index.xml a document no parser accepts;
  CDATA escapes markup, not characters. It is now written visibly as \\x01.
- C4All placing: the scratch name was the same for every thread of the
  process, so two exports at once dropped each other's half-copied file.
- C4All re-export: an --all-as-movies export into a folder that held an
  earlier picture index left that C4P_Index.xml beside the new movie index.
- CSV: a path or name from the evidence beginning with = reached Excel as a
  formula in exported_files.csv and the Project VIC match list.
- VICS import: a RelativeFilePath on another Windows drive ended the whole
  import with ValueError instead of being counted as outside the export.
"""
import tempfile
import csv
import json
import ntpath
import os
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fme.exports import c4all_export, export_files, report, vic_import

ROOT = os.path.join(tempfile.gettempdir(), "export_safety-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

failures = []


def check(label, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + label
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


from PIL import Image

source = os.path.join(ROOT, "source")
os.makedirs(source)
for index, color in enumerate(((200, 10, 10), (10, 200, 10))):
    image = Image.new("RGB", (64, 48), color)
    for x in range(0, 64, 3):
        image.putpixel((x, index * 7 % 48), (x * 3 % 255, 0, 255 - x))
    image.save(os.path.join(source, f"p{index}.jpg"), quality=90)
out_dir = os.path.join(ROOT, "case")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", source,
                "-o", out_dir], capture_output=True, cwd=os.path.dirname(HERE))
manifest_path = os.path.join(out_dir, "manifest.json")
with open(manifest_path, encoding="utf-8") as fh:
    manifest = json.load(fh)
check("the fixture run recovered two pictures", len(manifest["files"]) == 2,
      len(manifest["files"]))

# What the evidence might hold: a control character, a CDATA terminator, a
# formula.
manifest["files"][0]["source_path"] = "/DCIM/bad\x01name]]>.jpg"
manifest["files"][1]["source_path"] = "=HYPERLINK(\"http://x\",\"y\")"
with open(manifest_path, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh)

# ------------------------------------------------------------- C4All index
export_dir = os.path.join(ROOT, "export")
result = c4all_export.export(manifest_path, export_dir, case="SAFE-1",
                             mode="copy", include_duplicates=False)
index_path = os.path.join(export_dir, "C4P_Index.xml")
try:
    tree = ET.parse(index_path)
    paths = [e.text for e in tree.iter("fullpath")]
    parsed = True
except ET.ParseError as exc:
    paths, parsed = [], False
    print("   ", exc)
check("C4P_Index.xml parses with a control character in a path", parsed)
check("the character is shown, not dropped, and ]]> survives the CDATA",
      any("bad\\x01name]]>.jpg" in (p or "") for p in paths), paths)

# Re-export with every file as a movie: the old picture index must go.
result = c4all_export.export(manifest_path, export_dir, case="SAFE-1",
                             mode="copy", include_duplicates=False,
                             all_as_movies=True)
check("a re-export removes the picture index it no longer writes",
      not os.path.exists(index_path)
      and os.path.isfile(os.path.join(export_dir, "C4M_Index.xml")),
      sorted(os.listdir(export_dir)))

# A folder with an index this program did not write keeps it.
foreign = os.path.join(ROOT, "foreign")
os.makedirs(foreign)
for name in ("C4P_Index.xml", "Case_Report.xml"):
    with open(os.path.join(foreign, name), "w", encoding="utf-8") as fh:
        fh.write("<from another tool/>")
c4all_export.export(manifest_path, foreign, case="SAFE-1", mode="copy",
                    include_duplicates=False, all_as_movies=True)
with open(os.path.join(foreign, "C4P_Index.xml"), encoding="utf-8") as fh:
    check("an index another tool wrote is not deleted",
          fh.read() == "<from another tool/>")

# --------------------------------------------------------- concurrent place
src = os.path.join(out_dir, manifest["files"][0]["out_path"])
dest = os.path.join(ROOT, "placed", "same.jpg")
errors = []


def place_many():
    for _ in range(40):
        try:
            c4all_export.place_file(src, dest, "copy")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))


threads = [threading.Thread(target=place_many) for _ in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()
leftovers = [n for n in os.listdir(os.path.dirname(dest)) if ".placing-" in n]
check("six threads placing one file at once all succeed", not errors,
      errors[:2])
check("and leave no scratch files behind", not leftovers, leftovers)
with open(src, "rb") as a, open(dest, "rb") as b:
    check("the placed file is whole", a.read() == b.read())

# ----------------------------------------------------------------- CSV
rows = report.gather(out_dir)["rows"]
copied = export_files.export(rows, out_dir, os.path.join(ROOT, "copied"))
with open(copied["manifest"], newline="", encoding="utf-8") as fh:
    cells = [cell for row in csv.reader(fh) for cell in row]
check("exported_files.csv never starts a cell with = from the evidence",
      not any(c.startswith("=") for c in cells)
      and "'=HYPERLINK(\"http://x\",\"y\")" in cells, [c for c in cells if "HYPER" in c])

vics = os.path.join(ROOT, "set.json")
with open(vics, "w", encoding="utf-8") as fh:
    json.dump({"value": [{"CaseID": "x", "Media": [
        {"MD5": manifest["files"][1]["md5"], "Category": 1,
         "MediaFiles": [{"FileName": "a.jpg"}]}]}]}, fh)
listed = os.path.join(ROOT, "matched.csv")
vic_import.match_manifest(vics, manifest_path, listed)
with open(listed, newline="", encoding="utf-8") as fh:
    cells = [cell for row in csv.reader(fh) for cell in row]
check("the Project VIC match list never starts a cell with = either",
      not any(c.startswith("=") for c in cells), [c for c in cells if "HYPER" in c])

# ------------------------------------------------------ other-drive paths
real = os.path.commonpath
os.path.commonpath = ntpath.commonpath     # as on Windows
try:
    check("a path on another drive is outside the export, not an exception",
          vic_import._inside("C:\\export", "D:\\x.jpg") is False)
    check("a path inside is still inside",
          vic_import._inside("C:\\export", "C:\\export\\a\\x.jpg") is True)
finally:
    os.path.commonpath = real

shutil.rmtree(ROOT, ignore_errors=True)
print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
