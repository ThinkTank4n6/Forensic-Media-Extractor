"""A carve worker that is reading counts as alive, whatever part of the carve
is doing the reading.

The heartbeat the parent watches for stuck workers beat once per chunk of
the scan. Judging one header -- a video's frames walked to their end, the
cut at the next header -- reads up to max_carve_size without coming back to
the scan, and on a slow source that ran past STRIPE_STALL_SECONDS: healthy
workers were killed as stuck, and the run's error blamed memory.
"""
import logging
import multiprocessing
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_heartbeat-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
image = os.path.join(ROOT, "card.dd")
with open(image, "wb") as fh:
    fh.write(os.urandom(4 << 20))

heartbeat = multiprocessing.get_context("spawn").RawValue("q", 0)
media_core._worker_init(image, [], False, False, False, None, heartbeat)
reader = media_core._WORKER["reader"]

opened = heartbeat.value
good = opened >= 1
ok &= good
print(f"opening the image beats: {opened} -- {'ok' if good else 'WRONG'}")

for label, read in (
        ("a direct read", lambda: reader.read(0, 4096)),
        ("a clamped read, as the parsers make", lambda: reader.read_clamped(100, 4096)),
        ("the cut at the next header, outside the scan loop",
         lambda: media_core._truncate_at_next_signature(reader, 0, 3 << 20))):
    before = heartbeat.value
    read()
    good = heartbeat.value > before
    ok &= good
    print(f"{label} beats: {heartbeat.value - before} -- {'ok' if good else 'WRONG'}")

data = reader.read(1000, 64)
with open(image, "rb") as fh:
    fh.seek(1000)
    same = fh.read(64) == data
ok &= same
print(f"and the bytes read are the image's: {same}")

reader.close()
shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
