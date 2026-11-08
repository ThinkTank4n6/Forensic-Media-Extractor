"""manifest.db is the case's record: each stored file, and each duplicate with
what it taught the kept file, is one transaction, and a resume and a rebuild
read it rather than re-deriving the truth from the CSVs.
"""
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import manifestdb, media_core  # noqa: E402
from fme.review import recover  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_manifestdb-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)


def check(label, good):
    global ok
    ok &= bool(good)
    print(f"{label} -- {'ok' if good else 'WRONG'}")


def picture(n):
    return b"\xff\xd8\xff\xe0" + bytes([n]) * 30_000 + b"\xff\xd9"


def crash(out):
    """close() never runs: the handles just go."""
    out._csv_fh.close()
    out._dup_csv_fh.close()
    out._db.close()


# 1. A stored file is in the record before store() returns.
case = os.path.join(ROOT, "live")
out = media_core.OutputManager(case, metadata=False)
carved = out.store(iter([picture(1)]), category="image", ext="jpg",
                   stem="recovered_0000_0", engine="carve", offset=4096)
files, _ = manifestdb.read(case)
check("a stored file is in manifest.db at once",
      len(files) == 1 and files[0]["sha256"] == carved.sha256
      and files[0]["offset"] == 4096)

# 2. A duplicate and what it taught the kept file land together.
out.store(iter([picture(1)]), category="image", ext="jpg",
          stem="recovered_0001_0", engine="filesystem",
          source_path="/DCIM/IMG_0001.JPG", deleted=True)
files, dups = manifestdb.read(case)
check("the duplicate is recorded", len(dups) == 1
      and dups[0]["source_path"] == "/DCIM/IMG_0001.JPG")
check("and the kept file's row already has its name, deleted flag and count",
      files[0]["source_path"] == "/DCIM/IMG_0001.JPG" and files[0]["deleted"]
      and files[0]["instances"] == 2)
crash(out)

# 3. A resume after that crash reads it back whole, with nothing replayed.
out = media_core.OutputManager(case, metadata=False, resume=True)
rec = out.records[0]
check("a resume reads the record, merged, from manifest.db",
      len(out.records) == 1 and rec.deleted and rec.instances == 2
      and rec.source_path == "/DCIM/IMG_0001.JPG" and len(out.duplicates) == 1)
check("and carries on numbering after it", out.counter >= 1)
crash(out)

# 4. A rebuild of the interrupted case uses it too.
state = recover.unfinished(case)
check("an interrupted case with only manifest.db is recoverable",
      state.get("recoverable") and state["rows"] == 1)

# 5. close() makes the record agree with manifest.json, including what the
#    run changed after a file was stored.
case = os.path.join(ROOT, "closed")
out = media_core.OutputManager(case, metadata=False)
kept = out.store(iter([picture(2)]), category="image", ext="jpg",
                 stem="recovered_0000_0", engine="carve", offset=0)
kept.container = True                     # as mark_containers does after
out.close({})
files, _ = manifestdb.read(case)
with open(os.path.join(case, "manifest.json"), encoding="utf-8") as fh:
    js = json.load(fh)["files"]
check("after close manifest.db and manifest.json say the same",
      files == js and files[0]["container"] is True)

# 6. A new run into the folder starts the record empty, like manifest.csv.
out = media_core.OutputManager(case, metadata=False)
check("a fresh run does not inherit the old record",
      not manifestdb.read(case)[0] and not out.records)
out.close({})

# 7. A record written by a newer release is refused, not misread.
con = sqlite3.connect(manifestdb.path_in(case))
con.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
con.commit()
con.close()
try:
    media_core.OutputManager(case, metadata=False, resume=True)
    refused = False
except media_core.ManifestUnreadable:
    refused = True
check("a case from a newer schema is refused", refused)

# 8. Reading a folder with no record does not create one.
empty = os.path.join(ROOT, "empty")
os.makedirs(empty)
try:
    manifestdb.read(empty)
    raised = False
except manifestdb.ManifestDBError:
    raised = True
check("reading where there is no manifest.db creates nothing",
      raised and not os.listdir(empty))

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
