"""FME_NO_TSK=1 switches The Sleuth Kit off everywhere, not in two places.

The switch is how the independent reader's answer is got to set against
TSK's. It was honored by the partition listing and by inspect only: the
file-system walk still walked with TSK whenever pytsk3 was installed, and
the vendored reader was not asked for NTFS, exFAT, HFS+ or APFS. On every
lab machine -- the ones with pytsk3 -- tools/verify.py and the known-answer
test compared TSK with itself and reported agreement.
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
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core, vendor_fs  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_tsk_switch-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good):
    global ok
    ok &= bool(good)
    print(f"{label} -- {'ok' if good else 'WRONG'}")


if media_core.pytsk3 is None:
    print("not checked: pytsk3 is not installed, so there is nothing to switch off")
    sys.exit(0)

card = os.path.join(ROOT, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(card)

os.environ.pop("FME_NO_TSK", None)
check("with pytsk3 installed, TSK reads by default", media_core.tsk_enabled())
os.environ["FME_NO_TSK"] = "1"
check("FME_NO_TSK=1 switches it off", not media_core.tsk_enabled())

# The walk must not touch TSK at all: every way into it raises.
touched = []


def refuse(*a, **kw):
    touched.append(a)
    raise AssertionError("TSK was used with FME_NO_TSK=1")


real = (media_core.pytsk3.Volume_Info, media_core.pytsk3.FS_Info)
media_core.pytsk3.Volume_Info = refuse
media_core.pytsk3.FS_Info = refuse
try:
    reader = media_core.open_image(card)
    out = media_core.OutputManager(os.path.join(ROOT, "out"), metadata=False)
    stats = collections.defaultdict(int, volumes=[])
    args = types.SimpleNamespace(image=card, max_file_size=1 << 32, no_deleted=False,
                                 include_system=False, databases=False,
                                 signatures=None, volumes=None)
    media_core.run_fs_engine(reader, out, args, stats)
    out.close({})
    reader.close()
finally:
    media_core.pytsk3.Volume_Info, media_core.pytsk3.FS_Info = real
check("the file-system walk does not use TSK", not touched)
check("and still reads the card", len(out.records) > 0)

wanted = vendor_fs.kinds_to_read(include_mainstream=not media_core.tsk_enabled())
check("the vendored reader is asked for the mainstream file systems",
      {"ntfs", "exfat", "apfs", "hfsplus"} <= set(wanted))
check("and every run's summary says TSK was switched off",
      "switched off" in media_core._engine_description())

os.environ.pop("FME_NO_TSK", None)
check("unset again, TSK is back", media_core.tsk_enabled()
      and "switched off" not in media_core._engine_description())

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
