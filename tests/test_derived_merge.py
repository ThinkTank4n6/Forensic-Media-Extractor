"""Two hash checks at once both keep their matches in the case.

The store check and the hash-set check each read the case's matches, merged
their own in and saved -- under the case's lock for the read and again for the
save, but not between. Whichever saved second erased the other's matches from
case.db, and so from every report, while the page (merged separately) still
showed them. CaseDB.merge_derived reads, merges and saves under one lock.
"""
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.review import casedb, hashsets      # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_derived_merge-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

case = casedb.open_case(ROOT, "examiner", "case")
store = {"a" * 64: [{"set": "store", "via": "store"}]}
session = {"b" * 64: [{"set": "loaded.json", "via": "session"}]}


def slowly(saved, new, origins):
    time.sleep(0.2)        # a large case's merge takes this long, or longer
    return hashsets.merge_matches(saved, new, origins)


checks = [threading.Thread(target=case.merge_derived, args=(
              "hash_matches", lambda saved: slowly(saved, store, {"store", "stash"}))),
          threading.Thread(target=case.merge_derived, args=(
              "hash_matches", lambda saved: slowly(saved, session, {"session"})))]
for thread in checks:
    thread.start()
for thread in checks:
    thread.join()
kept = case.derived("hash_matches")
case.close()
good = sorted(kept) == sorted(list(store) + list(session))
print(f"both checks' matches are in the case: {len(kept)} of 2 -- {'ok' if good else 'WRONG'}")
ok &= good

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
