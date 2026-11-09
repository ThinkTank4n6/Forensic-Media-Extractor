"""An E01 libewf will not read falls back the same way whatever it is named,
and the handle libewf opened is closed first.

The .e01 branch closed nothing when libewf opened the file and reported no
media: the handle stayed open while the vendored reader opened the same
segments, and on Windows an open handle keeps an evidence drive from
detaching. An E01 with the wrong extension, recognized by its header, had
no fallback at all.

libewf is stood in for here, so this runs where libewf-python is absent.
"""
import logging
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_ewf_fallback-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
closed, vendored = [], []


class NoMediaHandle:
    def open(self, filenames):
        pass

    def get_media_size(self):
        return 0

    def close(self):
        closed.append(True)


class StandInVendorReader(media_core.ImageReader):
    def __init__(self, path, credentials=None):
        vendored.append(path)
        self.size = 1

    def read(self, offset, length):
        return b""


real = (_engine.replace("pyewf", types.SimpleNamespace(glob=lambda p: [p],
                                                       handle=NoMediaHandle)),
        _engine.replace("VendorImageReader", StandInVendorReader))
try:
    for name in ("named.E01", "misnamed.bin"):
        path = os.path.join(ROOT, name)
        with open(path, "wb") as fh:
            fh.write(b"EVF\x09\x0d\x0a\xff\x00" + b"\0" * 100)
        closed.clear()
        vendored.clear()
        try:
            reader = media_core.open_image(path)
            fell_back = isinstance(reader, StandInVendorReader) and vendored == [path]
        except Exception as exc:  # noqa: BLE001 - what the old code did here
            fell_back = False
            print(f"   {name}: raised {type(exc).__name__}: {exc}")
        good = fell_back and closed == [True]
        ok &= good
        print(f"{name}: libewf's handle closed {closed == [True]}, read by the "
              f"vendored reader {fell_back} -- {'ok' if good else 'WRONG'}")
finally:
    for saved in real:
        _engine.restore(saved)

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
