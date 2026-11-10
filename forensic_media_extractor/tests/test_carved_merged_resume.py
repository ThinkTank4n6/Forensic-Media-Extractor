"""A carved file a deleted entry later named still counts as carved when a
carve resumes.

OutputManager.store turns a carved file's engine into "carve+fat-deleted"
(or +mft, +filesystem) when a directory entry turns up for the same bytes.
_already_carved recognized that and _carved_reach did not, so a resume
treated the file as carved for one purpose and not the other: what lay
nested inside it could be carved again as files of their own.
"""
import logging
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402


def rec(engine, offset, size):
    return types.SimpleNamespace(engine=engine, offset=offset, size=size)


out = types.SimpleNamespace(records=[rec("carve+fat-deleted", 100, 5000),
                                     rec("filesystem", 0, 10 ** 9)])
reach = media_core._carved_reach(out, 1000)
already = media_core._already_carved(out)
ok = reach == 5100 and already == {100}
print(f"a carved file later named by a deleted entry: reach {reach} (5100), "
      f"already carved {already} ({{100}}); a file system's file counts for "
      f"neither -- {'ok' if ok else 'WRONG'}")
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
