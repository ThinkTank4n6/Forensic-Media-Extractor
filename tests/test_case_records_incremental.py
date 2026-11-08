"""Details during a run reads only what the manifest gained since last time.

case_records() kept one whole parse per case, keyed on the file's mtime and
size. During a run manifest.csv grows between any two clicks, so every click
missed and parsed the whole manifest again: 10.9 s at 200,000 rows, with the
GIL held, stalling the status poll and the extraction thread. Each case now
keeps a tail and reads only appended rows; a rewritten file (close() renames
one into place) is read from the start, and manifest.json wins once it exists.
"""
import csv
import dataclasses
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.engines import media_core          # noqa: E402
from fme.web import media_gui as gui        # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_case_records-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


FIELDS = [f.name for f in dataclasses.fields(media_core.Record)]
ROW = {f: "" for f in FIELDS}
ROW.update(ext="jpg", category="image", engine="carve", size="12345", deleted="False",
           sha256="a" * 64, md5="b" * 32, offset="4096", container="False",
           animated="False", instances="1")


def write(path, names, mode="w"):
    with open(path, mode, newline="", encoding="utf-8") as fh:
        out = csv.DictWriter(fh, fieldnames=FIELDS)
        if mode == "w":
            out.writeheader()
        for name in names:
            out.writerow(dict(ROW, out_path=name))


manifest = os.path.join(ROOT, "manifest.csv")
write(manifest, [f"images/recovered_{i:06d}.jpg" for i in range(50_000)])
write(os.path.join(ROOT, "duplicates.csv"), [])
files, _ = gui.case_records(ROOT)
check("every row is read", len(files) == 50_000, str(len(files)))

write(manifest, ["images/late.jpg"], mode="a")
started = time.perf_counter()
files, _ = gui.case_records(ROOT)
spent = time.perf_counter() - started
check("a row written since is there", files[-1]["out_path"] == "images/late.jpg")
check("and finding it did not read the case again", spent < 0.25, f"{spent:.3f} s")

replacement = manifest + ".new"
write(replacement, ["images/after_close.jpg"])
os.replace(replacement, manifest)
files, _ = gui.case_records(ROOT)
check("a manifest renamed into place is read from its start",
      [f["out_path"] for f in files] == ["images/after_close.jpg"])

with open(os.path.join(ROOT, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"files": [{"out_path": "images/final.jpg"}], "duplicates": []}, fh)
files, _ = gui.case_records(ROOT)
check("manifest.json wins once the run has written it",
      [f["out_path"] for f in files] == ["images/final.jpg"])

# The run hands Details the rows it has parsed for the gallery, so the first
# click during a run has nothing left to parse.
fed = os.path.join(ROOT, "fed")
os.makedirs(fed)
write(os.path.join(fed, "manifest.csv"), [f"images/r_{i:06d}.jpg" for i in range(50_000)])
write(os.path.join(fed, "duplicates.csv"), [])
run_tail = gui._ManifestTail(os.path.join(fed, "manifest.csv"))
before = run_tail.pos
parsed = [r for r in map(gui._live_row, run_tail.read_new()) if r is not None]
gui._feed_case_records(fed, run_tail, before, parsed)
started = time.perf_counter()
files, _ = gui.case_records(fed)
spent = time.perf_counter() - started
check("the first click after the run has read is instant", spent < 0.25, f"{spent:.3f} s")
check("and lists every row once", len(files) == 50_000, str(len(files)))

# A click that reads ahead of the run: the run's next hand-off overlaps it
# and must be refused, not added.
write(os.path.join(fed, "manifest.csv"), ["images/next.jpg"], mode="a")
gui.case_records(fed)                       # Details reads the new row itself
before = run_tail.pos
parsed = [r for r in map(gui._live_row, run_tail.read_new()) if r is not None]
gui._feed_case_records(fed, run_tail, before, parsed)
files, _ = gui.case_records(fed)
check("a row read by both is listed once", len(files) == 50_001, str(len(files)))
write(os.path.join(fed, "manifest.csv"), ["images/later.jpg"], mode="a")
files, _ = gui.case_records(fed)
check("and rows after that are still found", files[-1]["out_path"] == "images/later.jpg"
      and len(files) == 50_002, str(len(files)))

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
