"""Each case's progress note is throttled on its own.

_save_progress wrote at most once every few seconds across the whole
process, so with two GUI jobs running side by side, one job's save held back
the other's: a crash could cost the second far more than a few seconds.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.engines import media_core

root = tempfile.mkdtemp(prefix="fme_throttle-")
a, b = os.path.join(root, "a"), os.path.join(root, "b")
os.makedirs(a)
os.makedirs(b)
media_core._save_progress(a, {"offset": 1})
media_core._save_progress(b, {"offset": 2})
both = media_core.read_progress(a).get("offset") == 1 and media_core.read_progress(b).get("offset") == 2
print(f"two cases saved a moment apart are both written: {both}")
media_core._save_progress(a, {"offset": 3})
held = media_core.read_progress(a).get("offset") == 1
print(f"a second save of the same case within the interval waits: {held}")
shutil.rmtree(root, ignore_errors=True)
ok = both and held
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
