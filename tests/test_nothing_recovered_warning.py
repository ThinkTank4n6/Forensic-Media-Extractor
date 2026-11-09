"""A run that kept nothing says what kind of volume it was, even when the
carve took names for candidates it then dropped.

The check was out.counter == 0. Every engine takes a name before store()
decides whether to keep the file, so one undersized or blank candidate --
which a volume full of unreadable structure produces -- put counter above
zero and the "this is a ReFS volume, not an empty disk" warning never came.
"""
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
sys.path.insert(0, HERE)
from fme.engines import media_core, vendor_fs  # noqa: E402
import _engine  # noqa: E402 -- replaces a name wherever the engine reads it

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_nothing_recovered-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
image = os.path.join(ROOT, "volume.dd")
with open(image, "wb") as fh:
    fh.write(os.urandom(1 << 20))

dropped = {"n": 0}


def carve_that_keeps_nothing(reader, out, args, stats, *rest, **kw):
    # Names taken, nothing kept: an undersized candidate, then a blank one.
    for data in (b"\xff\xd8\xff" + b"x" * 20, b"\x00" * (1 << 20)):
        rec = out.store(iter([data]), category="image", ext="jpg",
                        stem=f"recovered_{out.next_index():04d}_0",
                        engine="carve", offset=0)
        dropped["n"] += rec is None


real_describe = vendor_fs.describe
saved = _engine.replace("run_carve_engine", carve_that_keeps_nothing)
vendor_fs.describe = lambda path: {"kind": "refs", "lines": ["superblock checksum"]}
try:
    summary = media_core.run_extraction(image, os.path.join(ROOT, "case"),
                                        mode="carve", workers=1)
finally:
    _engine.restore(saved)
    vendor_fs.describe = real_describe

good = dropped["n"] == 2 and summary.get("total_files") == 0 \
    and summary.get("unreadable_volume") == "refs"
ok &= good
print(f"two candidates named and dropped, nothing kept: the run says it was a "
      f"{summary.get('unreadable_volume')!r} volume -- {'ok' if good else 'WRONG'}")

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
