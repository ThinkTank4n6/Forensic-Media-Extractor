"""A resume retries what failed, and a finished run leaves nothing to resume.

Two faults in the bookkeeping --resume reads:

- A phase was recorded as done whether or not all of it worked. The
  file-system phase logged the extra reader's failure (F2FS, QNX, the
  flash file systems) and recorded itself finished anyway, so a resume
  skipped it and that partition was never read again.
- clear_progress() existed and nothing called it, so run_state.json
  outlived every finished run, and a later --resume on the case skipped
  what the stale note said was done.

This drives run_extraction over the FAT32 test card with the extra reader
made to fail, the run stopped part way through the carve, and resumed.
"""
import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_resume_phases-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
image = os.path.join(ROOT, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(image)

calls = {"vendor": 0, "walk": 0}
real_vendor, real_walk = media_core.run_vendor_fs, media_core.run_fs_engine


def failing_vendor(*args, **kwargs):
    calls["vendor"] += 1
    raise OSError("F2FS reader stopped on a bad block")


def counting_vendor(*args, **kwargs):
    calls["vendor"] += 1
    return real_vendor(*args, **kwargs)


def counting_walk(*args, **kwargs):
    calls["walk"] += 1
    return real_walk(*args, **kwargs)


media_core.run_fs_engine = counting_walk


def run(out: str, *, vendor_fails: bool, stop_in_carve: bool = False,
        resume: bool = False) -> dict:
    media_core.run_vendor_fs = failing_vendor if vendor_fails else counting_vendor
    state = {"carving": False}

    def progress(done, total, found, phase):
        state["carving"] |= phase == "carve"

    calls["vendor"] = calls["walk"] = 0
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.run_extraction(
            image, out, mode="both", resume=resume, progress_cb=progress,
            cancel_cb=(lambda: state["carving"]) if stop_in_carve else None)


def note(out: str) -> dict:
    path = os.path.join(out, media_core.PROGRESS_FILE)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


print("the extra reader fails, and the run is stopped during the carve")
case = os.path.join(ROOT, "stopped")
first = run(case, vendor_fails=True, stop_in_carve=True)
done = note(case).get("phases_done", [])
right = "filesystem" in done and "other-filesystems" not in done
ok &= right
print(f"   the failed reader is not recorded as done: {right} ({done})")
said = first.get("failed_phases") == ["other-filesystems"]
ok &= said
print(f"   and the summary names it: {said}")

print("\n--resume, with the reader working again")
resumed = run(case, vendor_fails=False, resume=True)
retried = calls["vendor"] == 1
ok &= retried
print(f"   the extra reader is run again: {retried}")
skipped = calls["walk"] == 0
ok &= skipped
print(f"   the main walk, which had finished, is not: {skipped}")
gone = not note(case) and not resumed.get("canceled")
ok &= gone
print(f"   the finished case leaves no progress note: {gone}")

print("\na run that finishes cleanly")
clean = os.path.join(ROOT, "clean")
run(clean, vendor_fails=False)
ok &= not note(clean)
print(f"   leaves no progress note: {not note(clean)}")
again = run(clean, vendor_fails=False, resume=True)
full = calls["walk"] == 1 and calls["vendor"] == 1 and not again.get("canceled")
ok &= full
print(f"   and a later --resume on it runs in full: {full}")

print("\na run that finishes with a phase failed")
partial = os.path.join(ROOT, "partial")
run(partial, vendor_fails=True)
kept = note(partial)
right = bool(kept) and "carve" in kept.get("phases_done", []) \
    and "other-filesystems" not in kept.get("phases_done", [])
ok &= right
print(f"   keeps the note, with the failed phase left to do: {right}")
run(partial, vendor_fails=False, resume=True)
right = calls["vendor"] == 1 and calls["walk"] == 0 and not note(partial)
ok &= right
print(f"   --resume retries only that phase, then clears it: {right}")

print("\na note left by v244, which marked the whole file-system phase done")
old = os.path.join(ROOT, "old_note")
run(old, vendor_fails=True, stop_in_carve=True)
with open(os.path.join(old, media_core.PROGRESS_FILE), "w", encoding="utf-8") as fh:
    json.dump({"phases_done": ["fat-deleted", "filesystem", "mft"], "offset": 0}, fh)
run(old, vendor_fails=False, resume=True)
right = calls["vendor"] == 1 and calls["walk"] == 0
ok &= right
print(f"   --resume now runs the extra reader it had skipped: {right}")

media_core.run_vendor_fs, media_core.run_fs_engine = real_vendor, real_walk
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
