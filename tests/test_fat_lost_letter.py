"""A deleted FAT name's lost first letter is shown as "?", by every path.

FAT overwrites the first byte of a deleted name. The deleted-entry pass and a
deleted folder showed the gap as "?"; the file-system walk showed it as "_",
which is a letter a FAT name can really begin with -- "_MG_0009.JPG" could
not be told from a deleted IMG_0009.JPG. "?" cannot appear in a FAT name.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_lost_letter-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
card = os.path.join(ROOT, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(card)

out = os.path.join(ROOT, "out")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", card, "-o", out],
               cwd=PROJECT, capture_output=True, timeout=900,
               env=dict(os.environ, FME_NO_TSK="1"))
with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
    files = json.load(fh)["files"]
deleted = sorted(f["source_path"] for f in files if f.get("deleted") and f.get("source_path"))
print(f"deleted files, by name: {deleted}")
good = any(p.endswith("/?MG_0009.JPG") for p in deleted)
ok &= good
print(f"  the deleted IMG_0009.JPG is shown as ?MG_0009.JPG -- {'ok' if good else 'WRONG'}")
underscored = [p for p in deleted if os.path.basename(p).startswith("_")]
ok &= not underscored
print(f"  no deleted name begins with an underscore standing for a lost letter: "
      f"{underscored or 'none'} -- {'ok' if not underscored else 'WRONG'}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
