"""A file-system walk that fails does not take the rest of the run with it.

Every phase of a run but the first was caught: a failure was logged,
recorded in failed_phases, named in the report and retried by --resume.
The file-system walk was not, so one exception in it ended the run before
the deleted-file passes and the carve -- which do not need the walk -- had
started. The walk is stood in for here by one that raises.
"""
import io
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
from fme.exports import report  # noqa: E402


def main() -> int:
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image as fixtures
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_fs_walk_failure-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    # Fixed bytes, not os.urandom: random padding passes as a second media
    # file about once in eighty runs (AMR and MP3 are found by walking
    # frames, and noise sometimes walks -- docs/VALIDATION.md 5.11), and
    # this test then failed for a reason unrelated to what it checks. Seeded
    # bytes are the same every run; this seed was checked to hold no match.
    import random
    blob = bytearray(random.Random(20261103).randbytes(4 << 20))
    pic = fixtures.make_jpeg(11)
    blob[1 << 20:(1 << 20) + len(pic)] = pic
    image = os.path.join(root, "disk.dd")
    with open(image, "wb") as fh:
        fh.write(bytes(blob))

    def broken(*_args, **_kwargs):
        raise RuntimeError("a file system the walk could not handle")

    real = media_core.run_fs_engine
    media_core.run_fs_engine = broken
    try:
        summary = media_core.run_extraction(image, os.path.join(root, "out"),
                                            mode="both", workers=1, progress=False)
    except Exception as exc:  # noqa: BLE001 - what this checks against
        summary = {"raised": repr(exc)}
    finally:
        media_core.run_fs_engine = real
    went_on = "raised" not in summary and summary.get("carved_files") == 1
    ok &= went_on
    print(f"the run goes on to the carve and finds the picture: {went_on} "
          f"({summary.get('raised') or summary.get('carved_files')})")
    named = "filesystem" in (summary.get("failed_phases") or [])
    ok &= named
    print(f"the walk is recorded as a failed phase: {named}")
    told = any("file systems stopped" in line
               for line in report.coverage_limits({"summary": summary}))
    ok &= told
    print(f"and the report says so: {told}")
    kept = os.path.isfile(os.path.join(root, "out", media_core.PROGRESS_FILE))
    ok &= kept
    print(f"and the progress note is kept so --resume retries it: {kept}")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
