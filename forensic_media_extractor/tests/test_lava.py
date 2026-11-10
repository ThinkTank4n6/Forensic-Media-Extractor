"""A LAVA project has to be laid out the way the viewer reads it.

The viewer resolves a media item's recorded path against the report folder
and comes up with a path no report contains, then falls through to
``_HTML/<extraction_path>`` -- which is the copy it actually serves. A
project without that copy shows every image as a broken icon while every
path inside it is correct, which is close to undiagnosable from the viewer.
"""
import tempfile
import json
import os
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fme.exports import lava_export

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "lava_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

image = os.path.join(HERE, "media_test.img")
if not os.path.isfile(image):
    subprocess.run([sys.executable, os.path.join(HERE, "make_media_test_image.py")],
                   cwd=HERE, capture_output=True)

out_dir = os.path.join(ROOT, "run")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image,
                "-o", out_dir, "--lava", "DC26-253"],
               capture_output=True, cwd=PROJECT)
lava_dir = os.path.join(out_dir, "lava")

ok = os.path.isdir(lava_dir)
print(f"wrote {lava_dir}: {ok}")

# Every media cell has to resolve twice: where the item says it is, and
# under _HTML where the viewer reads it.
con = sqlite3.connect(os.path.join(lava_dir, "_lava_artifacts.db"))
con.row_factory = sqlite3.Row
items = con.execute("SELECT id, extraction_path FROM _lava_media_items").fetchall()
con.close()
print(f"\n{len(items)} media item(s)")
missing_plain, missing_html = [], []
for row in items:
    relative = row["extraction_path"]
    if not os.path.isfile(os.path.join(lava_dir, relative.replace("/", os.sep))):
        missing_plain.append(relative)
    if not os.path.isfile(os.path.join(lava_dir, "_HTML",
                                       relative.replace("/", os.sep))):
        missing_html.append(relative)
ok &= not missing_plain and not missing_html
print(f"  present at media/      : {len(items) - len(missing_plain)}/{len(items)}")
print(f"  present under _HTML/   : {len(items) - len(missing_html)}/{len(items)}"
      f"   <- the copy the viewer serves")

# The two pages LAVA shows as tabs.
logs = os.path.join(lava_dir, "_HTML", "_Script_Logs")
for name in ("DeviceInfo.html", "Screen_Output.html"):
    present = os.path.isfile(os.path.join(logs, name))
    ok &= present
    print(f"  _HTML/_Script_Logs/{name:20} {present}")

report = lava_export.validate(lava_dir)
ok &= report["ok"]
print(f"\nvalidator: {'valid' if report['ok'] else 'NOT loadable'}")
for problem in report["problems"]:
    print(f"  - {problem}")

# And it must notice when the served copy is gone, because that is the fault
# that looks like nothing from inside the viewer.
broken = os.path.join(ROOT, "broken")
shutil.copytree(lava_dir, broken)
shutil.rmtree(os.path.join(broken, "_HTML"))
index_path = os.path.join(broken, "_lava_data.lava")
with open(index_path, encoding="utf-8") as fh:
    index = json.load(fh)
index["param_output"] = broken
with open(index_path, "w", encoding="utf-8") as fh:
    json.dump(index, fh, indent=1)

caught = lava_export.validate(broken)
ok &= not caught["ok"]
mentions_html = any("_HTML" in p for p in caught["problems"])
ok &= mentions_html
print(f"\nwith _HTML removed: {'rejected' if not caught['ok'] else 'ACCEPTED'}, "
      f"and says why: {mentions_html}")
for problem in caught["problems"][:2]:
    print(f"  - {problem[:118]}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
