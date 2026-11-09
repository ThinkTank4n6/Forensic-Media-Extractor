"""A carved file that runs into a stretch the source cannot read says so.

_carve_chunks stopped at the first empty read without a word. A video
judged 4 MiB by its own header was stored as the 2 MiB before the bad
stretch, its row still said bounded = "header", and nothing listed the
stretch: the manifest claimed a whole file the case did not hold.
"""
import logging
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
from fme.exports import report  # noqa: E402

ok = True
MIB = 1 << 20
ROOT = os.path.join(tempfile.gettempdir(), "fme_carve_short-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)


class BadStretch(media_core.ImageReader):
    """16 MiB of distinct bytes; 6-7 MiB gives nothing back."""
    size = 16 * MIB

    def read(self, offset, length):
        if offset < 0 or offset >= self.size or 6 * MIB <= offset < 7 * MIB:
            return b""
        end = min(offset + length, self.size)
        if offset < 6 * MIB:
            end = min(end, 6 * MIB)
        return bytes((i * 7 + (i >> 9)) & 0xFF for i in range(offset, end))


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def carve(name, start, length, bounded="header"):
    out = media_core.OutputManager(os.path.join(ROOT, name), metadata=False)
    stats = {"carved_files": 0, "carved_bytes": 0}
    rec = {"start": start, "length": length, "ext": "mov", "category": "video",
           "signature": "mov", "bounded": bounded, "encoding": None}
    media_core._store_carved(BadStretch(), out, types.SimpleNamespace(scan_inside=False),
                             stats, media_core.SIGNATURES, rec, 16 * MIB)
    return out, stats


# A file crossing the bad stretch.
out, stats = carve("crosses", 4 * MIB, 4 * MIB)
row = out.records[0]
check("stored up to the stretch, no further, nothing invented",
      row.size == 2 * MIB, f"{row.size:,} bytes")
check('marked bounded "short"', row.bounded == "short", repr(row.bounded))
check("its note gives where reading stopped, how much, and how it was judged",
      "6,291,456" in row.note and "2,097,152 of the 4,194,304" in row.note
      and "(header)" in row.note, row.note)
check("the stretch is listed as unread",
      stats.get("unreadable_ranges") == [[6 * MIB, 2 * MIB]],
      str(stats.get("unreadable_ranges")))
check("and counted", stats.get("carved_short") == 1)

# The row is written once, by store(): the CSV says the same.
out.close({})
with open(os.path.join(out.outdir, "manifest.csv"), newline="", encoding="utf-8") as fh:
    import csv
    written = next(csv.DictReader(fh))
check('the manifest row on disk says "short" too', written["bounded"] == "short")

# A resume reads the row back rather than refusing it as damaged.
again = media_core.OutputManager(out.outdir, resume=True)
again._csv_fh.close()
again._dup_csv_fh.close()
check('a resume keeps the "short" row', len(again.records) == 1
      and again.records[0].bounded == "short")

# A file clear of the stretch is unchanged.
out, stats = carve("clear", 8 * MIB, 2 * MIB)
row = out.records[0]
check("a file the source gives whole is stored whole, as it was",
      row.size == 2 * MIB and row.bounded == "header"
      and "SHORT" not in row.note and not stats.get("unreadable_ranges"))

# The report says so among its limits.
limits = report.coverage_limits({"summary": {"carved_short": 1}, "files": []})
check("the report's limits mention the short file",
      any('bounded "short"' in line for line in limits))

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
