"""A record's instants and its zone-less readings are kept apart, and read
only through timeutil.

Three releases running (v2026.11.295, .296, .297) found code that read a time
field directly and cut its string, and in doing so either lost the zone of a
UTC time or dated it in UTC: the contact sheet, the report's summary, the
gallery's year filters. The fourth, found while doing this, was the report's
DOCX and PDF writers printing the stored text unconverted. Fixing each one
fixed one. This is the change that stops a fifth: a record's time fields hold
instants only, readings go to `recorded_times`, `timeutil.settle()` is the one
gate in, and timeutil's accessors the one way out -- which this test enforces
by reading every reader's source.
"""
import ast
import json
import logging
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
logging.disable(logging.CRITICAL)
from fme.engines import media_core, timeutil  # noqa: E402
from fme.exports import c4all_export, report  # noqa: E402

FIELDS = set(timeutil.TIME_FIELDS)
ok = True


def check(what, passed):
    global ok
    ok &= bool(passed)
    print(f"  {what}: {bool(passed)}")


print("what store() writes")
root = os.path.join(tempfile.gettempdir(), "fme_time_model-") + str(os.getpid())
shutil.rmtree(root, ignore_errors=True)
out = media_core.OutputManager(root, metadata=False)
fat = out.store(iter([b"\xff\xd8\xff" + b"f" * 200]), category="image", ext="jpg",
                stem="FAT", engine="filesystem", fs_type="FAT32",
                mtime="2024-07-04T18:00:00", crtime="2024-07-04T17:59:58")
ntfs = out.store(iter([b"\xff\xd8\xff" + b"n" * 200]), category="image", ext="jpg",
                 stem="NTFS", engine="filesystem", fs_type="NTFS",
                 mtime="2024-07-04T18:00:00+00:00", atime="2024-07-04T18:00:00Z")
summary = {}
out.close(summary)
with open(os.path.join(root, "manifest.json"), encoding="utf-8") as fh:
    files = {os.path.basename(f["out_path"]): f for f in json.load(fh)["files"]}
fat_row, ntfs_row = files["FAT.jpg"], files["NTFS.jpg"]
check("a FAT time is not in the mtime field", fat_row["mtime"] == "")
check("it is kept, as recorded, in recorded_times",
      json.loads(fat_row["recorded_times"]) == {
          "crtime": "2024-07-04T17:59:58", "mtime": "2024-07-04T18:00:00"})
check("an NTFS time stays in its field with its offset",
      ntfs_row["mtime"] == "2024-07-04T18:00:00+00:00")
check("a Z time is written with +00:00", ntfs_row["atime"] == "2024-07-04T18:00:00+00:00")
check("no instant field holds a zone-less time",
      all(not f[name] or timeutil.zoned(f[name]) for f in files.values() for name in FIELDS))
with open(os.path.join(root, "manifest.csv"), encoding="utf-8") as fh:
    header = fh.readline()
check("manifest.csv carries recorded_times too", "recorded_times" in header)
shutil.rmtree(root, ignore_errors=True)

print("\nreading, new and old manifests alike")
check("stored() gives the FAT reading back", timeutil.stored(fat_row, "mtime") == "2024-07-04T18:00:00")
check("instant() refuses a reading", timeutil.instant(fat_row, "mtime") is None)
check("reading() finds it", timeutil.reading(fat_row, "mtime") is not None)
old = {"mtime": "2024-07-04T18:00:00", "captured": "2025-01-01T02:00:00+00:00"}
check("an old manifest's zone-less field reads as a reading",
      timeutil.instant(old, "mtime") is None and timeutil.reading(old, "mtime") is not None)
check("and its zoned field as an instant", timeutil.instant(old, "captured") is not None)
check("C4All gives a FAT time no epoch", c4all_export.epoch(timeutil.stored(fat_row, "mtime")) == "")
check("and an NTFS time one", c4all_export.epoch(timeutil.stored(ntfs_row, "mtime")) != "")

print("\nevery report format shows a FAT time, and converts an NTFS time")
report._SHOWN_IN["zone"] = ""
check("the report CSV keeps the reading", report._field_value(fat_row, "mtime") == "2024-07-04T18:00:00")
shown = report._time_shown(report._field_value(fat_row, "mtime"))
check(f"the HTML/DOCX/PDF text marks it as the device clock ({shown})", timeutil.AS_RECORDED in shown)
shown = report._time_shown(report._field_value(ntfs_row, "mtime"))
check(f"and states the zone of an instant ({shown})", "+00:00" in shown)

print("\nno reader goes around timeutil")
READERS = ("fme/exports", "fme/review", "fme/web")
found = []
for folder in READERS:
    for name in sorted(os.listdir(os.path.join(PROJECT, folder))):
        if not name.endswith(".py"):
            continue
        path = os.path.join(folder, name)
        tree = ast.parse(open(os.path.join(PROJECT, path), encoding="utf-8").read())
        for node in ast.walk(tree):
            key = None
            # rec["mtime"], rec.get("mtime") -- the two ways a field was read
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                key = node.slice.value
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "get" and node.args
                  and isinstance(node.args[0], ast.Constant)):
                key = node.args[0].value
            if key in FIELDS:
                found.append(f"{path}:{node.lineno}")
check(f"direct reads of a time field in {', '.join(READERS)}: {found or 'none'}", not found)

script = open(os.path.join(PROJECT, "fme/web/ui/app.js"), encoding="utf-8").read()
cut = re.findall(r"\.(?:captured|mtime|atime|crtime|ctime)\s*(?:\|\|[^)]*\))?\s*\.(?:slice|substr|substring)\(",
                 script)
check(f"the page cuts no time as text: {cut or 'none'}", not cut)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
