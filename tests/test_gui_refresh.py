"""The GUI's live file list costs the same at any size of case.

While a run is going, the GUI refreshes its file list from manifest.csv
every few seconds so the gallery fills as files are found. It re-read the
whole manifest each time, on the extraction thread, so the carve stopped
for longer with every file found: measured on v245 at 0.5 s a refresh with
25,000 files and 3.0 s with 200,000, which is 43% of a large run.

A refresh now reads only the rows written since the last one. This checks
it returns exactly what the manifest holds -- rows caught half-written
included -- and that its cost does not grow with the case.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import csv
import dataclasses
import io
import logging
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
logging.disable(logging.CRITICAL)

from fme.engines import media_core
from fme.web import media_gui

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_gui_refresh-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
FIELDS = [f.name for f in dataclasses.fields(media_core.Record)]
path = os.path.join(ROOT, "manifest.csv")


def row(i: int, **extra) -> dict:
    base = {"engine": "carve", "category": "image", "size": 20000 + i,
            "out_path": f"images/jpg/recovered_{i:07d}.jpg", "sha256": f"{i:064x}",
            "ext": "jpg", "instances": 1, "deleted": "False",
            "note": "unallocated or fs-independent"}
    base.update(extra)
    return base


print("a refresh returns what was written since the last one, and nothing twice")
tail = media_gui._ManifestTail(path)
seen = []
with open(path, "w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, FIELDS, restval="")
    writer.writeheader()
    for i in range(3):
        writer.writerow(row(i))
    fh.flush()
    seen += tail.read_new()
    # Awkward rows: a comma, a quote and a line break inside fields, and
    # a name in another script.
    writer.writerow(row(3, source_path='/DCIM/a, "b"\nc.jpg', note="ünïcødé 写真"))
    fh.flush()
    seen += tail.read_new()
    # A row caught half-written, as the writer is part way through it.
    text = io.StringIO()
    csv.DictWriter(text, FIELDS, restval="").writerow(row(4, source_path="/x\ny.jpg"))
    whole = text.getvalue()
    split = whole.index("\n") + 1               # past the quoted line break only
    fh.write(whole[:split])
    fh.flush()
    half = tail.read_new()
    fh.write(whole[split:])
    fh.flush()
    seen += half + tail.read_new()
with open(path, newline="", encoding="utf-8") as fh:
    expected = list(csv.DictReader(fh))
right = seen == expected
ok &= right
print(f"   every row once, exactly as written: {right} ({len(seen)} of {len(expected)})")
ok &= half == []
print(f"   a half-written row waits for the rest: {half == []}")
ok &= tail.read_new() == []
print(f"   a look with nothing new returns nothing: {tail.read_new() == []}")

with open(path, "w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, FIELDS, restval="")
    writer.writeheader()
    writer.writerow(row(9))
again = tail.read_new()
right = tail.restarted and [r["sha256"] for r in again] == [f"{9:064x}"]
ok &= right
print(f"   a manifest written afresh is read afresh: {right}")

print("\nthe cost of a refresh as the case grows")
tail = media_gui._ManifestTail(path)
costs = {}
with open(path, "w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, FIELDS, restval="")
    writer.writeheader()
    written = 0
    for size in (25_000, 200_000):
        while written < size:
            writer.writerow(row(written))
            written += 1
        fh.flush()
        tail.read_new()                       # what the earlier looks took
        for i in range(500):                  # four seconds of a fast carve
            writer.writerow(row(written + i))
        written += 500
        fh.flush()
        started = time.time()
        fresh = [media_gui._job_file_entry(media_gui._live_row(r)) for r in tail.read_new()]
        costs[size] = (time.time() - started, len(fresh))
for size, (cost, count) in costs.items():
    print(f"   {size:>7,} files in the case: {count} new rows read in {cost * 1000:.0f} ms")
flat = costs[200_000][0] < 0.25 and costs[200_000][1] == 500
ok &= flat
print(f"   no slower at 200,000 files than the new rows need: {flat}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
