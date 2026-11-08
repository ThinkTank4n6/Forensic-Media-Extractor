"""The progress note never fails a run, never leaves scratch files, and a
new run in a folder whose last run just finished writes its note at once.
"""
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_progress_errors-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

try:
    media_core._save_progress(ROOT, {"phase": "carve", "seen": {1, 2}}, every=0)
    raised = ""
except Exception as exc:  # noqa: BLE001 - what the old code did
    raised = f"{type(exc).__name__}: {exc}"
left = [n for n in os.listdir(ROOT) if n.endswith(".tmp")]
good = not raised and not left
ok &= good
print(f"a value json cannot write: raised {raised or 'nothing'}, scratch files "
      f"left {left} -- {'ok' if good else 'WRONG'}")

media_core._save_progress(ROOT, {"phase": "carve", "offset": 1}, every=0)
media_core.clear_progress(ROOT)
media_core._save_progress(ROOT, {"phase": "carve", "offset": 2})   # throttled call
note = media_core.read_progress(ROOT)
good = note.get("offset") == 2
ok &= good
print(f"a new run's first note, straight after the last run cleared its own: "
      f"written {good} -- {'ok' if good else 'WRONG'}")

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
