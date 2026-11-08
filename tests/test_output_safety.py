"""What the case record does when a run is resumed, repeated or interrupted.

1. A resume made every file the file-system, MFT and FAT deleted-entry
   passes had already stored a "duplicate of itself": those phases have no
   resume point, so an unfinished one runs again from its start, and
   store() had no way to know the same file from the same place. A carved
   file the case held only as a duplicate was written down again the same
   way. Now a resumed run passes over what the run it resumes stored.
2. A run without --resume into a folder holding a case emptied manifest.db
   and manifest.csv and left the recovered files beside them, for a later
   --resume to adopt as orphans. It is refused now, with a clear error,
   from the API and the command line; --overwrite still allows it.
3. A recovered file is made durable (fsync) before its row commits, so a
   power cut cannot leave a committed row for bytes that never reached
   the disk.
4. A CSV line that failed after the row committed raised out of store()
   before the record was kept in memory, and close() -- which rebuilds
   manifest.db from memory -- then deleted the committed row.
"""
import contextlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402
from fme.engines import manifestdb, media_core, output  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_output_safety-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
IMAGE = os.path.join(HERE, "media_test.img")


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def run(out_dir, **options):
    return media_core.run_extraction(IMAGE, out_dir, workers=1, metadata=False,
                                     kml=False, **options)


# --------------------------------------------------------------------------
print("a resumed run does not record its own files again")
case = os.path.join(ROOT, "case")
first = run(case, mode="fs")
named = [f for f in first["files"] if f["engine"] in ("filesystem", "fat-deleted")]
check("the first run stores files from the file system", len(named) >= 3, len(named))
again = run(case, mode="fs", resume=True)
check("a resume that walks the same volumes again adds no duplicate rows",
      again["duplicate_instances"] == first["duplicate_instances"],
      (first["duplicate_instances"], again["duplicate_instances"]))
check("and no instances", [f["instances"] for f in again["files"]]
      == [f["instances"] for f in first["files"]])
check("it says how many it passed over",
      again.get("resume_skipped", 0) >= len(named), again.get("resume_skipped"))
check("and the case still lists every file once",
      sorted(f["out_path"] for f in again["files"])
      == sorted(f["out_path"] for f in first["files"]))

print("\na carved file the case holds only as a duplicate is known on resume")
out = media_core.OutputManager(os.path.join(ROOT, "carved"), metadata=False)
picture = fixtures.make_jpeg(7)
for offset in (1000, 9000):
    out.store(iter([picture]), category="image", ext="jpg",
              stem=f"recovered_{out.next_index():04d}_{offset}", engine="carve",
              offset=offset, signature="jpeg")
held = media_core._already_carved(out)
check("both offsets count as carved", held == {1000, 9000}, held)
out.close({})

# --------------------------------------------------------------------------
print("\na new run into a folder that holds a case is refused")
before = manifestdb.read(case)
try:
    run(case, mode="fs")
    refused = False
except output.CaseFolderInUse as exc:
    refused = "--resume" in str(exc) and "--overwrite" in str(exc)
check("refused, saying what to do instead", refused)
check("and the case's record is untouched", manifestdb.read(case) == before)
cli = subprocess.run([sys.executable, "-m", "fme", "extract", "-i", IMAGE,
                      "-o", case, "--mode", "fs"], cwd=PROJECT,
                     capture_output=True, text=True, timeout=600)
check("the command line refuses it too, with exit code 2",
      cli.returncode == 2 and "already holds a case" in cli.stderr,
      (cli.returncode, cli.stderr.strip()[-200:]))
empty = os.path.join(ROOT, "empty")
os.makedirs(empty)
check("an empty folder is not refused", run(empty, mode="fs")["total_files"] > 0)
redo = run(case, mode="fs", overwrite=True)
check("--overwrite starts it again", redo["total_files"] > 0)

# --------------------------------------------------------------------------
print("\na recovered file is on disk before its row commits")
order = []
real_fsync = output._fsync_file
output._fsync_file = lambda path: (order.append("fsync"), real_fsync(path))
out = media_core.OutputManager(os.path.join(ROOT, "durable"), metadata=False)
real_put = out._db.put_file
out._db.put_file = lambda rec: (order.append("commit"), real_put(rec))
try:
    out.store(iter([picture]), category="image", ext="jpg", stem="durable",
              engine="carve", offset=0, signature="jpeg")
finally:
    output._fsync_file = real_fsync
check("fsync, then the commit", order == ["fsync", "commit"], order)
out.close({})

# --------------------------------------------------------------------------
print("\na CSV line that fails after the commit does not lose the record")
out = media_core.OutputManager(os.path.join(ROOT, "csvfail"), metadata=False)
real_row = out._csv.writerow


def failing_row(row):
    out._csv.writerow = real_row
    raise OSError(5, "the CSV could not be written")


out._csv.writerow = failing_row
rec = None
try:
    rec = out.store(iter([picture]), category="image", ext="jpg", stem="kept",
                    engine="carve", offset=0, signature="jpeg")
    raised = False
except OSError:
    raised = True
check("store() does not raise", not raised)
check("the record is kept in memory", rec is not None and rec in out.records)
check("and counted", out.csv_failed == 1, out.csv_failed)
summary = {}
out.close(summary)
files, _ = manifestdb.read(out.outdir)
check("manifest.db still holds it after close", [f["out_path"] for f in files]
      == [rec.out_path] if rec else False)
with open(os.path.join(out.outdir, "manifest.csv"), encoding="utf-8") as fh:
    check("and manifest.csv is rewritten with it", rec and rec.out_path in fh.read())
check("the summary says a CSV row failed", summary.get("csv_rows_failed") == 1,
      summary.get("csv_rows_failed"))
with open(os.path.join(out.outdir, "manifest.json"), encoding="utf-8") as fh:
    check("as does manifest.json", json.load(fh)["summary"].get("csv_rows_failed") == 1)

# --------------------------------------------------------------------------
print("\na name taken from the evidence cannot leave the case folder")
# A DASH manifest's Representation id once went into a stem unchanged, and
# an id of "/../../x" wrote the joined video outside the case (v2026.11.312).
case_dir = os.path.join(ROOT, "escape", "case")
os.makedirs(os.path.join(case_dir, "videos", "mp4"), exist_ok=True)
out = media_core.OutputManager(case_dir, metadata=False)
hostile = ["recovered_0001_stream_/../../../../ESCAPED",
           "recovered_0002_stream_..\\..\\..\\ESCAPED2",
           "recovered_0003_stream_a:ads", "..", ""]
stored = []
for i, stem in enumerate(hostile):
    rec = out.store(iter([fixtures.make_jpeg(20 + i)]), category="image",
                    ext="jpg", stem=stem, engine="embedded", offset=0,
                    signature="jpeg")
    stored.append(rec)
root_real = os.path.realpath(case_dir)
inside = all(r and os.path.realpath(os.path.join(case_dir, r.out_path))
             .startswith(root_real + os.sep) for r in stored)
check("every hostile stem is stored inside the case", inside,
      [r.out_path if r else None for r in stored])
escaped = [p for p in ("ESCAPED.jpg", "ESCAPED2.jpg")
           if os.path.exists(os.path.join(ROOT, "escape", p))
           or os.path.exists(os.path.join(ROOT, p))]
check("and nothing was written beside it", not escaped, escaped)
check("no name holds a colon (an NTFS alternate data stream)",
      all(":" not in r.out_path for r in stored if r))
weird_ext = out.store(iter([fixtures.make_jpeg(40)]), category="image",
                      ext="../../jpg", stem="ext_test", engine="embedded",
                      offset=0, signature="jpeg")
check("an extension cannot carry a path either",
      weird_ext and os.path.realpath(os.path.join(case_dir, weird_ext.out_path))
      .startswith(root_real + os.sep), weird_ext.out_path if weird_ext else None)
out.close({})

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
