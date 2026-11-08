"""A run that was interrupted, continued.

A crash on a 298 GiB image used to cost the whole run: the recovered
files were on disk but there was no record of where the scan had reached,
so the only way forward was to start again. On a case that takes hours
that is most of a working day.

The run writes where it has got to every few seconds. Resuming reopens the
same case — so nothing already recovered is recovered twice — and starts
the scan from that point, backed off by one signature's span in case a
file straddled the moment it died.

The check that matters is not that a resume finishes. It is that a
resumed run finds **the same files** as one that was never interrupted,
because a resume that quietly skipped evidence would be worse than no
resume at all.
"""
import tempfile
import csv
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

import make_media_test_image as fixtures
from fme.engines import media_core

ok = True
# One directory per process. Twenty-seven tests used a
# fixed path here and each wipes it on startup, so two
# runs overlapping -- which happens whenever a batch is
# cut short and its child keeps going -- had the second
# deleting the first's working files mid-run. That is
# the only mechanism found for this suite's intermittent
# failures.
ROOT = os.path.join(tempfile.gettempdir(), "fme_resume_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

try:
    import numpy as np
except ImportError:
    print("NumPy is not installed; skipping")
    sys.exit(0)

# Big enough that a run can be killed part way through it.
rng = np.random.default_rng(57)
blob = bytearray(rng.integers(0, 255, 320 * 1024 * 1024,
                              dtype=np.uint8).tobytes())
at = 2_000_000
planted = 0
for index in range(48):
    picture = fixtures.make_jpeg(1100 + index)
    blob[at:at + len(picture)] = picture
    at += len(picture) + 6_000_000
    planted += 1
image = os.path.join(ROOT, "disk.dd")
with open(image, "wb") as fh:
    fh.write(bytes(blob))
print(f"a {os.path.getsize(image) / 1048576:.0f} MiB image with "
      f"{planted} pictures in it")


def rows_of(folder):
    path = os.path.join(folder, "manifest.csv")
    if not os.path.isfile(path):
        return set()
    with open(path, newline="", encoding="utf-8") as fh:
        return {row["sha256"] for row in csv.DictReader(fh)
                if row.get("sha256")}


def case_folder(parent):
    if os.path.isfile(os.path.join(parent, "manifest.csv")):
        return parent
    found = glob.glob(os.path.join(parent, "media_out", "*"))
    return found[0] if found else parent


def kill_when(process, parent, ready, limit=240.0):
    """Kill the run as soon as its case shows `ready`, and say what happened.

    This slept a fixed 6 or 8 seconds and killed. On a busy machine the run
    had recorded nothing yet, and the checks below failed -- one of them while
    printing "True", which was a fixed string. On a fast one it could finish
    first, and "killed part way" would test nothing. Waiting for the state
    each check needs makes the test mean the same thing on any machine; a
    run that finishes before it can be interrupted fails the test, loudly.
    """
    started = time.monotonic()
    while time.monotonic() - started < limit:
        if process.poll() is not None:
            return f"finished in {time.monotonic() - started:.0f} s before it could be killed"
        folder = case_folder(parent)
        if ready(folder, media_core.read_progress(folder) or {}):
            process.kill()
            process.wait()
            return ""
        time.sleep(0.2)
    process.kill()
    process.wait()
    return f"showed nothing to interrupt after {limit:.0f} s"


print("\nkilling a run part way")
interrupted = os.path.join(ROOT, "interrupted")
process = subprocess.Popen(
    [sys.executable, "-m", "fme", "extract", "-i", image, "-o", interrupted,
     "--mode", "carve", "--workers", "1"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=PROJECT)
# Part way: some files kept, and the carve's position recorded.
problem = kill_when(process, interrupted,
                    lambda folder, note: rows_of(folder) and note.get("offset", 0) > 0)
ok &= not problem
if problem:
    print(f"  the run {problem}")

folder = case_folder(interrupted)
part = rows_of(folder)
ok &= bool(part)
print(f"  {len(part)} file(s) recovered before it died")

note = media_core.read_progress(folder)
ok &= bool(note) and note.get("offset", 0) > 0
print(f"  it recorded reaching {note.get('offset', 0) / 1048576:.0f} MiB "
      f"of {note.get('total', 0) / 1048576:.0f} MiB")

print("\nresuming it")
subprocess.run(
    [sys.executable, "-m", "fme", "extract", "-i", image, "-o", folder,
     "--mode", "carve", "--workers", "1", "--resume"],
    capture_output=True, cwd=PROJECT, timeout=900)
resumed = rows_of(folder)
ok &= len(resumed) > len(part)
print(f"  {len(resumed)} file(s) after resuming "
      f"(it had {len(part)})")

# Nothing recovered twice: the rows already written are read back when the
# case is reopened, so the second pass knows what the first one found.
with open(os.path.join(folder, "manifest.csv"), newline="",
          encoding="utf-8") as fh:
    all_rows = [row["sha256"] for row in csv.DictReader(fh)
                if row.get("sha256")]
ok &= len(all_rows) == len(set(all_rows))
print(f"  nothing recovered twice: "
      f"{len(all_rows) == len(set(all_rows))}")

print("\nagainst a run that was never interrupted")
whole = os.path.join(ROOT, "whole")
subprocess.run(
    [sys.executable, "-m", "fme", "extract", "-i", image, "-o", whole,
     "--mode", "carve", "--workers", "1"],
    capture_output=True, cwd=PROJECT, timeout=900)
complete = rows_of(case_folder(whole))

missed = complete - resumed
ok &= not missed
print(f"  the full run found {len(complete)}; the resumed run found "
      f"{len(resumed)}")
print(f"  found by the full run and missed by the resume: {len(missed)}")

# The other direction is not a failure -- a resume re-scans a little
# either side of where it stopped -- but it is worth seeing.
print(f"  found by the resume and not the full run: "
      f"{len(resumed - complete)}")

# The carve is the long phase but not the only one. A run that died
# during the databases pass used to redo the file-system walk, the MFT
# recovery and the whole carve to reach the same place. A phase that
# finished is recorded, and a resume skips it.
print("\nphases, not just the carve")
phased = os.path.join(ROOT, "phased")
process = subprocess.Popen(
    [sys.executable, "-m", "fme", "extract", "-i", image, "-o", phased,
     "--workers", "1"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=PROJECT)
# What this part is about: a finished phase and the carve's position, both
# in the note at the moment the run dies.
problem = kill_when(process, phased,
                    lambda folder, note: "filesystem" in (note.get("phases_done") or [])
                    and note.get("offset", 0) > 0)
ok &= not problem
if problem:
    print(f"  the run {problem}")

folder = case_folder(phased)
state = media_core.read_progress(folder)
finished = state.get("phases_done") or []
ok &= "filesystem" in finished
print(f"  the run recorded finishing: {finished}")
print(f"  and the carve reaching {state.get('offset', 0) / 1048576:.0f} MiB")

# The record of finished phases and the carve's position share one note,
# and writing one must not throw the other away.
both = state.get("offset", 0) > 0 and bool(finished)
ok &= both
print(f"  both survive in the same note: {both}")

subprocess.run(
    [sys.executable, "-m", "fme", "extract", "-i", image, "-o", folder,
     "--workers", "1", "--resume"],
    capture_output=True, cwd=PROJECT, timeout=900)

whole_again = os.path.join(ROOT, "whole_again")
subprocess.run(
    [sys.executable, "-m", "fme", "extract", "-i", image, "-o", whole_again,
     "--workers", "1"],
    capture_output=True, cwd=PROJECT, timeout=900)

after = rows_of(folder)
reference = rows_of(case_folder(whole_again))
ok &= not (reference - after)
print(f"  after resuming: {len(after)} file(s); a full run finds "
      f"{len(reference)}; missed {len(reference - after)}")

# A real machine carves in parallel -- the default is sixteen workers --
# and that path took a different route through the code, which ignored
# the resume entirely and re-read the whole image. No file came back
# twice, because the digests are known, but the hours a resume exists to
# save were spent anyway.
print("\nresuming a parallel carve")
size = 300 * 1024 * 1024
every = media_core._stripe_tasks(size, 4, 1, 4 << 30, 16 << 20)
part = media_core._stripe_tasks(size, 4, 1, 4 << 30, 16 << 20,
                                resume_from=200 * 1024 * 1024)
ok &= len(part) < len(every)
print(f"  from the start: {len(every)} stripe(s); resuming at 200 MiB: "
      f"{len(part)}")

# It must not start after the point it died, or a file straddling that
# stripe is lost.
ok &= bool(part) and part[0][0] <= 200 * 1024 * 1024
print(f"  the first stripe kept begins at "
      f"{part[0][0] / 1048576:.0f} MiB, before the 200 it reached")

# And the end of the image is still covered.
ok &= part[-1][1] == size
print(f"  the last stripe still reaches the end: {part[-1][1] == size}")


# The parallel carve must actually recover something.
#
# A worker initializer whose signature did not match its call made
# every worker process die the moment the pool started. The run did
# not fail -- it reported no carved files, which looks exactly like an
# image with nothing in it, and the default is sixteen workers, so
# this is the path nearly every real run takes. It went unnoticed
# because every test here that checks carving uses one worker.
print("\nthe parallel carve recovers what one worker does")
alone = os.path.join(ROOT, "one_worker")
shutil.rmtree(alone, ignore_errors=True)
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image,
                "-o", alone, "--mode", "carve", "--workers", "1"],
               capture_output=True, cwd=PROJECT, timeout=900)
many = os.path.join(ROOT, "four_workers")
shutil.rmtree(many, ignore_errors=True)
result = subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image,
                         "-o", many, "--mode", "carve", "--workers", "4"],
                        capture_output=True, text=True, cwd=PROJECT,
                        timeout=900)

one_found = rows_of(case_folder(alone))
many_found = rows_of(case_folder(many))
ok &= bool(many_found)
print(f"   one worker found {len(one_found)}, four found {len(many_found)}")
ok &= not (one_found - many_found)
print(f"   nothing the single worker found was missed: "
      f"{not (one_found - many_found)}")

# And no worker died on the way. A pool whose workers crash still
# returns, with nothing in it.
died = "_worker_init" in (result.stdout + result.stderr)
ok &= not died
print(f"   no worker process crashed: {not died}")

# Its working folder holds a 320 MiB image and several carves of it --
# about 640 MB a run. Left behind, it was 640 MB more in the temp folder
# every time the suite ran, enough to fill a disk over a few dozen runs.
shutil.rmtree(ROOT, ignore_errors=True)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
