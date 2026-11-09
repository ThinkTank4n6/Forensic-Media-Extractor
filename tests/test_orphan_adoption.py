"""A file a crash left in the case folder with no manifest row is listed by its
hashes, and gets its provenance back when the resumed run finds it again.

manifest.csv is written a row at a time after the file is renamed into
place, so a crash between the two leaves a whole recovered file with no row.
It used to be named in orphaned_files and nowhere else: absent from
manifest.json and so from every export, and absent from the dedupe table, so
the resumed run stored the same bytes a second time beside it.
"""
import csv
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
ROOT = os.path.join(tempfile.gettempdir(), "fme_orphan_adoption-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)


def check(label, good):
    global ok
    ok &= bool(good)
    print(f"{label} -- {'ok' if good else 'WRONG'}")


def picture(n):
    return b"\xff\xd8\xff\xe0" + bytes([n]) * 30_000 + b"\xff\xd9"


def store(out, data, at):
    return out.store(iter([data]), category="image", ext="jpg",
                     stem=f"recovered_{out.next_index():04d}_{at}",
                     engine="carve", offset=at, source_path=f"/DCIM/{at}.jpg")


def rows(case):
    with open(os.path.join(case, "manifest.csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def files_on_disk(case):
    return sorted(os.path.relpath(os.path.join(d, n), case)
                  for d, _, names in os.walk(os.path.join(case, "images"))
                  for n in names)


def crash_with_orphan(case):
    """Two files stored; the second one's row lost to a crash."""
    out = media_core.OutputManager(case, metadata=False)
    store(out, picture(1), 100)
    second = store(out, picture(2), 200)
    out._csv_fh.close()
    out._dup_csv_fh.close()
    # The crash came after the rename and before the record's commit.
    out._db.remove_file(second.out_path)
    out._db.close()
    path = os.path.join(case, "manifest.csv")
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(lines[:-1])           # the second row never reached disk


# 1. Resumed, the orphan is listed by its hashes, from the start.
case = os.path.join(ROOT, "listed")
crash_with_orphan(case)
out = media_core.OutputManager(case, metadata=False, resume=True)
listed = rows(case)
orphan = [r for r in listed if r["engine"] == media_core.ORPHAN_ENGINE]
check("the unlisted file is in manifest.csv as soon as the case reopens",
      len(listed) == 2 and len(orphan) == 1)
import hashlib  # noqa: E402
check("its row carries the file's own SHA-256",
      orphan and orphan[0]["sha256"] == hashlib.sha256(picture(2)).hexdigest())
check("and says its provenance was lost", orphan and "crash" in orphan[0]["note"])
summary = {}
out.close(summary)
check("the summary still names it as orphaned",
      summary.get("orphaned_files") == [orphan[0]["out_path"]])

# 2. Resumed and found again: one file, one row, with its provenance back.
case = os.path.join(ROOT, "adopted")
crash_with_orphan(case)
before = files_on_disk(case)
out = media_core.OutputManager(case, metadata=False, resume=True)
rec = store(out, picture(2), 200)
summary = {}
out.close(summary)
after = files_on_disk(case)
got = rows(case)
check("the re-found bytes are not stored a second time", before == after)
check("the record is the orphan's file", rec and rec.out_path in before)
check("and has back where it was found",
      [(r["offset"], r["source_path"], r["engine"]) for r in got]
      == [("100", "/DCIM/100.jpg", "carve"), ("200", "/DCIM/200.jpg", "carve")])
check("the summary counts it restored, not orphaned",
      summary.get("orphans_restored") == 1 and not summary.get("orphaned_files"))

# 3. A crash after adopting: the second resume keeps the full row.
case = os.path.join(ROOT, "adopted-then-crash")
crash_with_orphan(case)
out = media_core.OutputManager(case, metadata=False, resume=True)
store(out, picture(2), 200)
out._csv_fh.close()
out._dup_csv_fh.close()
out = media_core.OutputManager(case, metadata=False, resume=True)
out.close({})
got = rows(case)
check("after a second crash the adopted row wins over the orphan row",
      [(r["offset"], r["engine"]) for r in got] == [("100", "carve"), ("200", "carve")])

# 4. The exports do not invent an origin for it.
from fme.exports import c4all_export  # noqa: E402
check("the C4All exporter's copy of the engine name matches",
      c4all_export.ORPHAN_ENGINE == media_core.ORPHAN_ENGINE)
check("C4All does not call it unallocated space",
      "Unallocated" not in c4all_export.evidence_path(
          {"engine": media_core.ORPHAN_ENGINE}, "CASE", "EV"))

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
