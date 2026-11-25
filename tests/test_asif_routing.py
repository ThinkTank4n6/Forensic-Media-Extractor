"""An Apple Sparse Image (.asif) goes to the vendored reader (v2026.11.338).

ewfprobe 0.17 reads ASIF, the disk image format of macOS 26 and later. FME
chooses a reader by extension, and .asif was on no list: the image was opened
as raw bytes and carved as one, with no file system in it walked.

What is checked here is the routing only. No ASIF image is in this
repository -- none could be written here, and the other tool's tests have
none either -- so the reader's decoding of a real one is unproven by FME;
docs/EXAMINER_TASKS.md says so.
"""
import _sandbox  # noqa: F401
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from fme.engines import image_readers, media_core  # noqa: E402
from fme.web import media_gui  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "asif_routing-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
path = os.path.join(ROOT, "mac.asif")
with open(path, "wb") as fh:
    fh.write(b"shdw" + b"\0" * 508)

opened = {}


class StandIn(image_readers.ImageReader):
    def __init__(self, source, credentials=None):
        opened["path"] = source
        self.size, self.name = 0, os.path.basename(source)


real = image_readers.VendorImageReader
image_readers.VendorImageReader = StandIn
try:
    reader = image_readers.open_image(path)
finally:
    image_readers.VendorImageReader = real
check("an .asif is opened by the vendored reader, not as raw bytes",
      opened.get("path") == path and isinstance(reader, StandIn), type(reader).__name__)
check("it is an acquisition to the folder check", ".asif" in media_core.ACQUISITION_EXTENSIONS)
check("and picked out in the file chooser", ".asif" in media_gui.IMAGE_EXTENSIONS)
check("the vendored reader says it reads ASIF",
      hasattr(__import__("fme.engines.vendor_fs", fromlist=["x"]).ewfprobe(), "ASIF_SIGNATURE"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
