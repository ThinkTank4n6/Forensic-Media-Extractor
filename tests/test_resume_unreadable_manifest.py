"""Resuming a case never rewrites its manifest from a partial read, and a
row torn in its last column is not taken as whole.

Two ways a resume damaged the case it was adding to:

- A manifest that could not be read to the end -- any csv.Error, such as a
  field over csv's 128 KiB default limit -- was read as far as the error.
  The constructor then rewrote manifest.csv from those rows and
  duplicates.csv from nothing. The rows after the error were gone.
- A crash that tore the last row inside its last column, `bounded`, left a
  row csv parses as complete: "capped" cut to "cap", or to "", which is
  what a file sized by a file system carries.
"""
import csv
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_resume_unreadable-") + str(os.getpid())
FIELDS = [f.name for f in media_core.Record.__dataclass_fields__.values()]


def case(name: str, records, dupes=()) -> str:
    folder = os.path.join(ROOT, name)
    os.makedirs(folder)
    for path, rows in (("manifest.csv", records), ("duplicates.csv", dupes)):
        with open(os.path.join(folder, path), "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            for rec in rows:
                writer.writerow(asdict(rec))
    return folder


def record(i: int, **extra) -> media_core.Record:
    return media_core.Record(engine="carve", category="image", size=10, md5="m",
                             out_path=f"images/jpg/recovered_{i:04d}_{i}.jpg",
                             sha256=f"{i:064x}", ext="jpg", offset=i,
                             bounded="capped", **extra)


def rows(folder: str, name: str) -> int:
    with open(os.path.join(folder, name), newline="", encoding="utf-8") as fh:
        return sum(1 for _ in csv.DictReader(fh))


shutil.rmtree(ROOT, ignore_errors=True)

# 1. A field longer than csv's default limit, in the middle of the manifest.
records = [record(i) for i in range(5)]
records[2].also_seen_at = "; ".join(f"/storage/emulated/0/DCIM/p{k}" for k in range(5000))
folder = case("long-field", records, dupes=[record(0)])
out = media_core.OutputManager(folder, resume=True)
out._csv_fh.close()
out._dup_csv_fh.close()
good = (len(out.records) == 5 and out.counter == 5
        and rows(folder, "manifest.csv") == 5 and rows(folder, "duplicates.csv") == 1)
ok &= good
print(f"a 140 KiB field: {len(out.records)} of 5 rows read, manifest keeps "
      f"{rows(folder, 'manifest.csv')}, duplicates keeps "
      f"{rows(folder, 'duplicates.csv')} of 1 -- {'ok' if good else 'WRONG'}")

# 2. A manifest that cannot be read at all is refused, and left as it was.
folder = case("unreadable", [record(i) for i in range(3)], dupes=[record(0)])
with open(os.path.join(folder, "manifest.csv"), "a", encoding="utf-8") as fh:
    fh.write('"' + "x" * 10 + "\n")   # opened quote, never closed: csv.Error in strict
before = open(os.path.join(folder, "manifest.csv"), "rb").read()
real_reader = csv.DictReader
csv.DictReader = lambda *a, **k: (_ for _ in ()).throw(csv.Error("simulated"))
try:
    media_core.OutputManager(folder, resume=True)
    refused = False
except media_core.ManifestUnreadable:
    refused = True
finally:
    csv.DictReader = real_reader
after = open(os.path.join(folder, "manifest.csv"), "rb").read()
good = refused and before == after and rows(folder, "duplicates.csv") == 1
ok &= good
print(f"a manifest that will not read: refused {refused}, manifest unchanged "
      f"{before == after}, duplicates kept {rows(folder, 'duplicates.csv') == 1} "
      f"-- {'ok' if good else 'WRONG'}")

# 3. The last row torn inside `bounded`, the last column.
for cut, label in ((3, '"capped" cut to "cap"'), (6, '"capped" cut to ""')):
    folder = case(f"torn-{cut}", [record(i) for i in range(3)])
    path = os.path.join(folder, "manifest.csv")
    data = open(path, "rb").read()
    open(path, "wb").write(data[:len(data) - len(b"\r\n") - cut])
    out = media_core.OutputManager(folder, resume=True)
    out._csv_fh.close()
    out._dup_csv_fh.close()
    kept = [r.bounded for r in out.records]
    good = len(out.records) == 2 and kept == ["capped", "capped"] and out.torn_rows == 1
    ok &= good
    print(f"last row torn, {label}: {len(out.records)} rows kept, bounded {kept} "
          f"-- {'ok' if good else 'WRONG'}")

# 4. An unknown `bounded` value on an otherwise whole line is refused too.
whole = {k: "" if v is None else str(v) for k, v in asdict(record(9)).items()}
assert media_core._record_from_row(whole) is not None, "the control row must read"
bad = media_core._record_from_row({**whole, "bounded": "cap"})
ok &= bad is None
print(f"bounded 'cap' read back as a record: {bad is not None} -- "
      f"{'ok' if bad is None else 'WRONG'}")

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
