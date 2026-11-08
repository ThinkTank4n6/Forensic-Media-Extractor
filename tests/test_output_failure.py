"""A find that cannot be stored is counted, named, and -- when the output
itself has failed -- stops the run loudly.

OutputManager.store caught every OSError with one log warning and returned
None. Nothing counted it. A run onto a full drive dropped every file after
the drive filled, kept going, and reported "Finished". A read error from the
evidence went down the same path and was logged as "write failed".
"""
import errno
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
sys.path.insert(0, HERE)
from fme.engines import media_core, output  # noqa: E402
import _engine  # noqa: E402 -- replaces a name wherever the engine reads it

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_output_failure-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good):
    global ok
    ok &= bool(good)
    print(f"{label} -- {'ok' if good else 'WRONG'}")


def jpeg(n):
    return b"\xff\xd8\xff\xe0" + bytes([n % 251 + 1]) * 40_000 + b"\xff\xd9"


real_open = open
full = {"after": None, "stored": 0}


def open_that_fills(path, mode="r", *a, **kw):
    """The output drive fills once `after` files have gone onto it."""
    if "w" in mode and os.path.basename(str(path)).startswith(".tmp_") \
            and full["after"] is not None:
        if full["stored"] >= full["after"]:
            raise OSError(errno.ENOSPC, "No space left on device")
        full["stored"] += 1
    return real_open(path, mode, *a, **kw)


# 1. An evidence read error is a read failure, counted, and does not stop
#    anything: the next file stores normally.
out = media_core.OutputManager(os.path.join(ROOT, "read"))


def gives_out():
    yield jpeg(1)[:1000]
    raise OSError(errno.EIO, "Input/output error")


rec = out.store(gives_out(), category="image", ext=".jpg", stem="a",
                engine="carve", offset=4096)
after = out.store(iter([jpeg(2)]), category="image", ext=".jpg", stem="b",
                  engine="carve", offset=8192)
check("a bad read is counted as a read failure, not a write failure",
      rec is None and out.read_failed == 1 and out.write_failed == 0)
check("a bad read does not mark the output failed; the next file is kept",
      out.output_failure is None and after is not None)
check("the lost file is named by where it was",
      out.failures and out.failures[0]["where"] == "offset 4096"
      and out.failures[0]["kind"] == "read")
check("no temporary file is left behind",
      not [n for n in os.listdir(out.outdir) if n.startswith(".tmp_")])
summary = {}
out.close(summary)
check("the summary carries the counts",
      summary.get("read_failed") == 1 and summary.get("write_failed") == 0)

# 2. A full drive: counted, the output marked failed, later files fail fast
#    and are counted too, and the run's cancel check now says stop.
out = media_core.OutputManager(os.path.join(ROOT, "full"))
output.open = open_that_fills
full.update(after=1, stored=0)
try:
    kept = out.store(iter([jpeg(3)]), category="image", ext=".jpg", stem="c",
                     engine="carve", offset=0)
    lost = [out.store(iter([jpeg(4 + i)]), category="image", ext=".jpg",
                      stem=f"d{i}", engine="carve", offset=i) for i in range(3)]
finally:
    del output.open
    full["after"] = None
check("the file before the drive filled is kept", kept is not None)
check("every file after it is counted as a write failure",
      all(r is None for r in lost) and out.write_failed == 3)
check("the output is marked failed with the reason",
      out.output_failure and "No space" in out.output_failure)
stop = media_core.stop_when_output_fails(out, None)
check("the run's cancel check now asks it to stop", stop() is True)
check("a healthy output does not stop a run",
      media_core.stop_when_output_fails(
          media_core.OutputManager(os.path.join(ROOT, "fine")), None)() is False)
summary = {}
out.close(summary)
check("the summary says the output failed", summary.get("output_failure"))

# 3. End to end: a run onto a drive that fills stops early, keeps what it
#    had, says so in its summary, and the command line exits non-zero.
image = os.path.join(ROOT, "volume.dd")
with real_open(image, "wb") as fh:
    fh.write(os.urandom(1 << 20))
attempted = {"n": 0}


def carve_ten(reader, out, args, stats, progress_cb=None, cancel_cb=None):
    for i in range(10):
        media_core._check_cancel(cancel_cb)
        attempted["n"] += 1
        out.store(iter([jpeg(20 + i)]), category="image", ext="jpg",
                  stem=f"recovered_{out.next_index():04d}_0", engine="carve",
                  offset=i * 512)


real_carve = media_core.run_carve_engine
saved = _engine.replace("run_carve_engine", carve_ten)
output.open = open_that_fills
full.update(after=4, stored=0)
try:
    summary = media_core.run_extraction(image, os.path.join(ROOT, "case"),
                                        mode="carve", workers=1)
finally:
    _engine.restore(saved)
    del output.open
    full["after"] = None
check("the run keeps the four files written before the drive filled",
      summary.get("total_files") == 4)
check("it stops at the next cancel check instead of trying the other five",
      attempted["n"] == 5 and summary.get("write_failed") == 1)
check("its summary says it stopped because the output failed",
      summary.get("canceled") and summary.get("output_failure"))
check("the command line reports it with exit status 3",
      media_core._report_losses(summary) == media_core.EXIT_FILES_LOST)
check("a clean run reports nothing lost",
      media_core._report_losses({"read_failed": 0, "write_failed": 0}) == 0)

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
