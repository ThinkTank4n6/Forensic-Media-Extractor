"""The deleted-file passes run to the end when the GUI is watching.

The MFT, deleted-FAT and extra-file-system passes reported progress as
progress_cb(0, 0, out.count, ...), and OutputManager has no `count`. The
command line passes no callback, so every test and every command-line run
was fine. The GUI always passes one, and there the call raised the first
time it was reached: on the first file that came back a duplicate while
nothing new had been stored yet -- which is the usual case, because the
file-system walk recovers some deleted files before these passes run --
or on the 25th file. run_extraction caught it as "recovery stopped", so in
the GUI each pass gave up after at most a handful of files, and the run
finished as if nothing were wrong.

Each pass is run here with a progress callback, over a case that already
holds one of its files, as the GUI runs it.
"""
import collections
import contextlib
import io
import logging
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

from fme.engines import media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_progress_cb-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

saved_argv = sys.argv
sys.argv = [saved_argv[0], os.path.join(ROOT, "ntfs.dd")]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        import make_ntfs_test_image as ntfs
        import make_media_test_image as fat
        fat.main(os.path.join(ROOT, "fat.img"))
finally:
    sys.argv = saved_argv

seen = []


def progress(done, total, recovered, phase):
    seen.append((phase, recovered))


def run(name, image, already, pass_fn):
    reader = media_core.open_image(image)
    out = media_core.OutputManager(os.path.join(ROOT, name), metadata=False)
    out.store(iter([already]), category="image", ext="jpg", stem="walk",
              engine="filesystem")             # the file-system walk got there first
    stats = collections.defaultdict(int)
    error = ""
    try:
        pass_fn(reader, out, types.SimpleNamespace(image=image, max_file_size=1 << 32),
                stats, media_core.SIGNATURES, progress_cb=progress)
    except Exception as exc:  # noqa: BLE001 - reported below
        error = f"{type(exc).__name__}: {exc}"
    finally:
        out.close({})
        reader.close()
    return len(out.records), error


print("the MFT pass, watched, over a case holding one of its files")
held, error = run("mft", os.path.join(ROOT, "ntfs.dd"), ntfs.jpeg_resident,
                  media_core.run_mft_recovery)
right = not error and held == 2
ok &= right
print(f"   runs to the end and recovers the other file: {right}" + (f" ({error})" if error else ""))

print("\nthe deleted-FAT pass, the same way")
held, error = run("fat", os.path.join(ROOT, "fat.img"), fat.make_jpeg(9),
                  media_core.run_fat_deleted_recovery)
right = not error
ok &= right
print(f"   runs to the end: {right}" + (f" ({error})" if error else ""))
reported = any(phase in ("mft", "fat-deleted") for phase, _ in seen)
ok &= reported
print(f"   and progress was reported to the callback: {reported}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
