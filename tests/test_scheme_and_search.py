"""Two things carried between cases: the examiner's scheme, and provenance.

A saved scheme is the examiner's own categories and flags, so they do not
get re-created by hand -- and spelled slightly differently -- in every case.
The Windows Search join turns a cached thumbnail, which has no name of its
own, into the path it was indexed under.
"""
import tempfile
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_media_test_image as g
from fme.engines import winsearch
from fme.review import casedb, savedset

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "scheme_search_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ok = True

print("the examiner's saved scheme")
first = os.path.join(ROOT, "case1")
second = os.path.join(ROOT, "case2")
os.makedirs(first)
os.makedirs(second)

case = casedb.open_case(first, examiner="Det. C. T.", case_number="DC26-253")
case.add_category("Series: Blue", "#2563eb")
case.add_flag("Local series 14", "#a21caf", "l")
case.close()

scheme_path = os.path.join(ROOT, "scheme.json")
scheme = savedset.save(first, scheme_path)
ok &= len(scheme["categories"]) == 1
print(f"  saved {len(scheme['categories'])} category(ies), "
      f"{len(scheme['flags'])} flag(s)")

# The locked scheme is deliberately not carried: category 1 means the same
# thing in every case, and the case database refuses to rename it anyway.
ok &= all(entry["code"] >= casedb.FIRST_CUSTOM_CODE
          for entry in scheme["categories"])
print(f"  locked categories not carried: "
      f"{all(e['code'] >= casedb.FIRST_CUSTOM_CODE for e in scheme['categories'])}")

case = casedb.open_case(second, examiner="Det. C. T.", case_number="DC26-301")
case.close()
result = savedset.apply(second, scheme_path)
case = casedb.CaseDB(os.path.join(second, "case.db"))
names = [c["name"] for c in case.categories()]
flags = [f["name"] for f in case.flags()]
ok &= "Series: Blue" in names and "Local series 14" in flags
print(f"  applied to a new case: +{result['categories_added']} category, "
      f"+{result['flags_added']} flag")

# Applying twice must not duplicate, and must not disturb what is there.
before = (len(names), len(flags))
savedset.apply(second, scheme_path)
case2 = casedb.CaseDB(os.path.join(second, "case.db"))
after = (len(case2.categories()), len(case2.flags()))
case2.close()
case.close()
ok &= before == after
print(f"  applying twice changes nothing: {before == after}")

print("\nnaming a cached thumbnail")
source = os.path.join(ROOT, "src")
os.makedirs(source)
known, unknown = g.make_jpeg(13), g.make_png()


def entry(cache_id, payload):
    header = 56
    size = header + len(payload)
    return (b"CMMM" + struct.pack("<I", size) + struct.pack("<Q", cache_id)
            + struct.pack("<I", 0) + struct.pack("<I", 0)
            + struct.pack("<I", len(payload)) + bytes(header - 28) + payload)


with open(os.path.join(source, "thumbcache_256.db"), "wb") as fh:
    fh.write(b"CMMM" + struct.pack("<I", 0x1A) + struct.pack("<I", 0)
             + struct.pack("<I", 24) + bytes(8)
             + entry(0x1122334455667788, known)
             + entry(0x99AABBCCDDEEFF00, unknown))

run = os.path.join(ROOT, "run")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", source,
                "-o", run], capture_output=True, cwd=PROJECT)

index = os.path.join(ROOT, "Windows.db")
connection = sqlite3.connect(index)
connection.execute("CREATE TABLE SystemIndex_1_PropertyStore_Metadata "
                   "(Id INTEGER, Name TEXT)")
connection.executemany(
    "INSERT INTO SystemIndex_1_PropertyStore_Metadata VALUES (?,?)",
    [(11, "System.ThumbnailCacheId"), (12, "System.ItemPathDisplay"),
     (13, "System.ItemNameDisplay")])
connection.execute("CREATE TABLE SystemIndex_1_PropertyStore "
                   "(WorkId INTEGER, ColumnId INTEGER, Value)")
connection.executemany(
    "INSERT INTO SystemIndex_1_PropertyStore VALUES (?,?,?)",
    [(1, 11, struct.pack("<Q", 0x1122334455667788)),
     (1, 12, "C:\\Users\\X\\Pictures\\holiday.jpg".encode("utf-16-le")),
     (1, 13, "holiday.jpg".encode("utf-16-le"))])
connection.commit()
connection.close()

report = winsearch.resolve(run, index)
ok &= report["named"] == 1 and report["cached_thumbnails"] == 2
print(f"  {report['cached_thumbnails']} cached thumbnail(s), "
      f"{report['named']} named from {report['indexed_items']} indexed item(s)")

with open(os.path.join(run, "thumbnail_sources.csv"), encoding="utf-8") as fh:
    rows = list(__import__("csv").DictReader(fh))
ok &= rows and rows[0]["indexed_path"].endswith("holiday.jpg")
print(f"  resolved to: {rows[0]['indexed_path'] if rows else 'nothing'}")

# The one the index does not know stays unnamed rather than being guessed at.
ok &= len(rows) == 1
print(f"  the thumbnail the index does not know stays unnamed: {len(rows) == 1}")

# Windows 10 and earlier: an ESE index, read in a child process.
#
# A real Windows.edb can only be written by Windows' own ESE engine, so the
# reader's decoding is checked against a stand-in that hands back rows in the
# shape impacket_ese does (names as bytes, binary values hex-encoded, text as
# str). What a real index does is EXAMINER_TASKS.md's job.
import binascii as _bx
import io as _io
import json as _json
import contextlib as _ctx

CACHE = 0x0123456789ABCDEF


class _StandIn:
    tables = {}
    fail_at = set()

    def __init__(self, path):
        self.path = path

    def mountDB(self):
        pass

    def openTable(self, name):
        rows = type(self).tables.get(name)
        return None if rows is None else {"rows": iter(rows), "n": 0}

    def getNextRow(self, cursor):
        cursor["n"] += 1
        if cursor["n"] in type(self).fail_at:
            raise ValueError("damaged page")
        return next(cursor["rows"], None)

    def close(self):
        pass


_real_reader = winsearch._ese_reader
winsearch._ese_reader = lambda: type("M", (), {"ESENT_DB": _StandIn})
try:
    # Windows 8 and later: tagged column names, little-endian id.
    _StandIn.tables = {"SystemIndex_PropertyStore": [
        {b"4454-System_ThumbnailCacheId": _bx.hexlify(struct.pack("<Q", CACHE)),
         b"4447-System_ItemPathDisplay": "C:\\Users\\u\\Pictures\\b.jpg",
         b"4391-System_ItemNameDisplay": "b.jpg"}]}
    _StandIn.fail_at = set()
    got, st = winsearch.edb_map("x.edb")
    _w8 = got == {CACHE: ("C:\\Users\\u\\Pictures\\b.jpg", "b.jpg")}
    ok &= _w8
    print(f"  ESE, Windows 8+ table, little-endian id read: {_w8}")

    # Windows 7: SystemIndex_0A, plain names, big-endian id. Read
    # little-endian, the id would match no thumbnail at all.
    _StandIn.tables = {"SystemIndex_0A": [
        {b"System_ThumbnailCacheId": _bx.hexlify(struct.pack(">Q", CACHE)),
         b"System_ItemPathDisplay": "C:\\a.jpg", b"System_ItemNameDisplay": "a.jpg"}]}
    got, st = winsearch.edb_map("x.edb")
    _w7 = list(got) == [CACHE] and st["table"] == "SystemIndex_0A"
    ok &= _w7
    print(f"  ESE, Windows 7 table, big-endian id read: {_w7}")

    # A row the reader cannot decode is counted, not silently dropped.
    _StandIn.tables = {"SystemIndex_PropertyStore": [
        {b"1-System_ThumbnailCacheId": _bx.hexlify(struct.pack("<Q", n)),
         b"2-System_ItemPathDisplay": f"p{n}", b"3-System_ItemNameDisplay": f"n{n}"}
        for n in range(1, 6)]}
    _StandIn.fail_at = {2, 4}
    got, st = winsearch.edb_map("x.edb")
    _counted = st["row_errors"] == 2 and len(got) == 5
    ok &= _counted
    print(f"  undecodable rows are counted ({st['row_errors']}), the rest read "
          f"({len(got)}): {_counted}")

    # A cursor that fails at every position stops, and says why, instead of
    # retrying five million times.
    _StandIn.fail_at = set(range(1, 10 ** 7))
    _old_run, winsearch.EDB_ERROR_RUN = winsearch.EDB_ERROR_RUN, 50
    got, st = winsearch.edb_map("x.edb")
    winsearch.EDB_ERROR_RUN = _old_run
    _stops = st["row_errors"] == 50 and "unreadable rows" in st["stopped"]
    ok &= _stops
    print(f"  a cursor stuck on errors stops after a run of them: {_stops}")

    # The child's answer is plain JSON the parent can read back.
    _StandIn.fail_at = set()
    _StandIn.tables = {"SystemIndex_PropertyStore": [
        {b"1-System_ThumbnailCacheId": _bx.hexlify(struct.pack("<Q", 0x1122334455667788)),
         b"2-System_ItemPathDisplay": "C:\\Users\\X\\Pictures\\holiday.jpg",
         b"3-System_ItemNameDisplay": "holiday.jpg"}]}
    _out = _io.StringIO()
    with _ctx.redirect_stdout(_out):
        _code = winsearch.edb_worker(["x.edb"])
    _answer = _json.loads(_out.getvalue())
    _wire = _code == 0 and "1122334455667788" in _answer["ids"]
    ok &= _wire
    print(f"  the worker answers as JSON: {_wire}")
finally:
    winsearch._ese_reader = _real_reader

# The real reader on a damaged file: this one spins forever in-process (a
# short read at end of file is retried with no exit). Isolated, the request
# ends at the deadline and the examiner is told the reader failed -- not that
# the index named nothing, which would read as a finding about the exhibit.
ese = os.path.join(ROOT, "Windows.edb")
with open(ese, "wb") as fh:
    fh.write(bytes(4) + b"\xef\xcd\xab\x89" + bytes(200))
import time as _time
winsearch.EDB_TIME_LIMIT, _limit = 6, winsearch.EDB_TIME_LIMIT
_t0 = _time.monotonic()
try:
    winsearch.resolve(run, ese)
    _said = ""
except ValueError as exc:
    _said = str(exc)
_took = _time.monotonic() - _t0
winsearch.EDB_TIME_LIMIT = _limit
_bounded = _took < 30 and "could not be read" in _said and "did not finish" in _said
ok &= _bounded
print(f"  a damaged Windows.edb ends in {_took:.0f} s with a reason, not a "
      f"hang: {_bounded}")
print(f"    ({_said[:110]}...)")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
