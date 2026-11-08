"""A gallery tile is never visible half-written.

_make_thumb saved straight to the tile's name, and /api/thumb serves any tile
that exists: while the encoder ran, the tile existed truncated -- from zero
bytes -- and a request then got a broken picture the browser kept for a day.
A save that failed part way left that file to be served for good. Tiles are
now written beside their name and renamed into place.
"""
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
try:
    import numpy
    from PIL import Image
except ImportError:
    print("Pillow and numpy are needed; not checked")
    sys.exit(0)
from fme.web import media_gui as gui        # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_thumb_atomic-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


# Noise encodes slowly, which holds the window open long enough to look in.
source = os.path.join(ROOT, "a.png")
Image.fromarray((numpy.random.default_rng(1).random((3000, 3000, 3)) * 255)
                .astype("uint8")).save(source)
gui.THUMB_EDGE = 3000
tile = gui._thumb_path(ROOT, "a.png")
seen, done = [], threading.Event()


def watch():
    while not done.is_set():
        if os.path.isfile(tile):
            seen.append(os.path.getsize(tile))
        time.sleep(0.0005)


watcher = threading.Thread(target=watch)
watcher.start()
made = gui._make_thumb(ROOT, source, "a.png")
done.set()
watcher.join()
final = os.path.getsize(tile)
check("the tile was made", made and final > 0)
check("no look ever found it shorter than it ended", all(size == final for size in seen),
      f"{sum(size < final for size in seen)} of {len(seen)} looks were short")
check("nothing is left beside it",
      not [n for n in os.listdir(os.path.dirname(tile)) if n.endswith(".tmp")])


class Breaks:
    def save(self, path, *args, **kwargs):
        with open(path, "wb") as fh:
            fh.write(b"\xff\xd8 half")
        raise OSError("disk full")


broken = os.path.join(ROOT, "broken.jpg")
try:
    gui.save_jpeg_atomically(Breaks(), broken)
except OSError:
    pass
check("a save that fails leaves no tile to be served", not os.path.exists(broken))
check("and no scratch file", not [n for n in os.listdir(ROOT) if n.endswith(".tmp")])

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
