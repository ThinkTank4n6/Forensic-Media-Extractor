"""A damaged manifest row is skipped; it does not stop the live view.

The live gallery and Details read manifest.csv as the run writes it, and took
nothing from a look if any row had the wrong number of fields -- right for a
last row still being written, wrong for a damaged one with rows after it,
which never changes: the gallery would stay at that row for the rest of the
run. Only a wrong last row is waited for now; others are dropped and counted.
"""
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.web import media_gui as gui        # noqa: E402

logging.disable(logging.WARNING)
ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_tail_damaged-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
path = os.path.join(ROOT, "manifest.csv")


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def add(text):
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(text)


add("a,b,c\n1,2,3\n4,5\n6,7,8\n")
tail = gui._ManifestTail(path)
got = [row["a"] for row in tail.read_new()]
check("a damaged row between whole ones is skipped, the rest read", got == ["1", "6"], str(got))
check("and counted", tail.skipped == 1)
add("9,1")
check("a last row still being written is waited for", tail.read_new() == [])
add("0,2\n")
got = [row["a"] for row in tail.read_new()]
check("and read once it is whole", got == ["9"], str(got))
add("bad,row\n")
check("a damaged last row is waited for too -- it may be one being written",
      tail.read_new() == [])
add("5,5,5\n")
got = [row["a"] for row in tail.read_new()]
check("and dropped once a row follows it", got == ["5"] and tail.skipped == 2,
      f"{got}, {tail.skipped} skipped")

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
