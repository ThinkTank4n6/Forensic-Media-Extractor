"""The gallery's live view of a manifest starts again when the file is
replaced, not only when it gets shorter.

OutputManager rewrites manifest.csv whole and renames it into place. The
tail looked only for a shorter file, so after a rewrite of the same length
or longer it read the new file from the old one's byte offset.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.web import media_gui  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_tail_replaced-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
path = os.path.join(ROOT, "manifest.csv")

with open(path, "w", newline="") as fh:
    fh.write("out_path,size\r\na.jpg,1\r\nb.jpg,2\r\n")
tail = media_gui._ManifestTail(path)
first = [r["out_path"] for r in tail.read_new()]

# Rewritten whole, longer, and renamed into place, as close() does.
scratch = path + ".tmp"
with open(scratch, "w", newline="") as fh:
    fh.write("out_path,size\r\na.jpg,1\r\nb.jpg,2\r\nc.jpg,3\r\nd.jpg,4\r\n")
os.replace(scratch, path)
second = [r["out_path"] for r in tail.read_new()]

good = first == ["a.jpg", "b.jpg"] and tail.restarted \
    and second == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
ok &= good
print(f"before: {first}; after a longer rewrite: restarted {tail.restarted}, "
      f"rows {second} -- {'ok' if good else 'WRONG'}")

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
