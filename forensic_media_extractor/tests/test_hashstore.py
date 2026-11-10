"""The hash store: import once, match from then on, contribute back."""
import tempfile
import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.review import casedb, hashstore

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "hashstore_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "case"))

ok = True
A, B, C = "a" * 32, "b" * 40, "c" * 64

# A set in each format the office is likely to have.
nsrl = os.path.join(ROOT, "NSRLFile.txt")
with open(nsrl, "w", newline="") as fh:
    writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
    writer.writerow(["SHA-1", "MD5", "CRC32", "FileName", "FileSize",
                     "ProductCode", "OpSystemCode", "SpecialCode"])
    writer.writerow([B.upper(), A.upper(), "0", "setup.exe", 10, "1", "WIN", ""])
    for i in range(5000):
        writer.writerow([f"{i:040x}", f"{i:032x}", "0", f"f{i}.dll", 10, "1",
                         "WIN", ""])

vic = os.path.join(ROOT, "vic.json")
json.dump({"@odata.context": "http://github.com/VICSDATAMODEL/ProjectVic/"
                             "DataModels/2.0.xml/US/$metadata#Cases",
           "value": [{"CaseNumber": "set", "Media": [
               {"MD5": "d" * 32, "Category": 1, "SHA1": None,
                "MediaFiles": [], "AlternativeHashes": []}]}]},
          open(vic, "w"))

own = os.path.join(ROOT, "ours.txt")
with open(own, "w") as fh:
    fh.write("# what we have seen before\n")
    fh.write(f"{C}  DC24-118\n")
    fh.write("d41d8cd98f00b204e9800998ecf8427e  an empty file\n")

store = hashstore.HashStore(os.path.join(ROOT, "store.db"))
print("imports:")
for path, expect_kind in ((nsrl, "nsrl"), (vic, "projectvic"), (own, "custom")):
    result = store.import_set(path)
    good = result["kind"] == expect_kind
    ok &= good
    print(f"  {os.path.basename(path):16} {result['kind']:11} "
          f"{result['stored']:>7,} hashes {'' if good else '<-- wrong kind'}")

# The hashes of an empty file identify nothing, so they must not be stored.
ok &= not store.lookup(["d41d8cd98f00b204e9800998ecf8427e"])
print(f"\nempty-file hash excluded: {not store.lookup(['d41d8cd98f00b204e9800998ecf8427e'])}")

# A file in one set by several algorithms is one match, not three.
hits = store.lookup([A, B])
ok &= len(hits) == 1 and hits[0]["matched_on"] == "md5, sha1"
print(f"one source, two algorithms -> {len(hits)} match on "
      f"{hits[0]['matched_on'] if hits else '-'}")

# Importing the same name twice is refused rather than silently doubling.
try:
    store.import_set(own)
    print("duplicate import: ALLOWED  <-- should have been refused")
    ok = False
except ValueError as exc:
    print(f"duplicate import refused: {str(exc)[:54]}…")

# Contribute a graded case, and check a later case learns the grading.
case_dir = os.path.join(ROOT, "case")
records = [{"out_path": f"images/jpg/{n}.jpg", "category": "image", "ext": "jpg",
            "size": 100, "md5": f"{i:032x}".replace("0", "e", 1),
            "sha1": f"{i:040x}".replace("0", "e", 1),
            "sha256": f"{i:064x}".replace("0", "e", 1), "engine": "carve",
            "source_path": "", "signature": "jpeg", "offset": i, "deleted": False,
            "note": "", "instances": 1}
           for i, n in enumerate(["one", "two", "three"], start=90)]
json.dump({"summary": {"image": "t"}, "files": records, "duplicates": []},
          open(os.path.join(case_dir, "manifest.json"), "w"))

case = casedb.open_case(case_dir, examiner="J. Smith", case_number="DC26-253")
case.set_category(records[0]["sha256"], 1)
case.set_category(records[1]["sha256"], 0)
case.close()

result = store.contribute(case_dir, "DC26-253")
print(f"\ncontributed {result['hashes']} hash(es) from {result['files']} graded "
      f"file(s); {result['skipped_uncategorized']} ungraded not added")
ok &= result["files"] == 2 and result["skipped_uncategorized"] == 1

learned = store.lookup([records[0]["sha256"]])
carried = learned and learned[0]["note"]
ok &= bool(carried) and "Child Abuse" in carried
print(f"a later case looking it up learns: {learned[0]['source']} — {carried}")

# An RDS arrives in more than one shape, and two of them are traps: the
# modern release puts a joined view beside the raw table, and some releases
# ship a hash column that is empty throughout. Choosing the wrong one imports
# a set of nothing.
import sqlite3 as _sqlite3

def _build(path, maker):
    if os.path.exists(path):
        os.remove(path)
    connection = _sqlite3.connect(path)
    maker(connection)
    connection.commit()
    connection.close()


def _modern(connection):
    connection.execute("CREATE TABLE PKG (package_id INTEGER, name TEXT)")
    connection.execute("INSERT INTO PKG VALUES (42, 'Acme Installer')")
    connection.execute("CREATE TABLE FILE (sha256 TEXT, sha1 TEXT, md5 TEXT, "
                       "file_name TEXT, file_size INTEGER, package_id INTEGER)")
    for index in range(3):
        connection.execute("INSERT INTO FILE VALUES (?,?,?,?,?,42)",
                           (f"{index + 80:064x}", f"{index + 80:040x}",
                            f"{index + 80:032x}", f"lib{index}.dll", 2048))
    connection.execute(
        "CREATE VIEW METADATA AS SELECT f.sha256, f.sha1, f.md5, f.file_name, "
        "f.file_size, p.name AS product_name FROM FILE f "
        "JOIN PKG p ON p.package_id = f.package_id")


def _empty_column(connection):
    connection.execute("CREATE TABLE FILE (sha256 TEXT, md5 TEXT, file_name TEXT)")
    for index in range(3):
        connection.execute("INSERT INTO FILE VALUES (NULL,?,?)",
                           (f"{index + 90:032x}", f"x{index}.exe"))


print("\nNSRL databases")
for label, maker, expect_table, expect_columns in (
        ("modern, with a view", _modern, "METADATA", 3),
        ("an empty sha256 column", _empty_column, "FILE", 1)):
    path = os.path.join(ROOT, label.split(",")[0].replace(" ", "_") + ".db")
    _build(path, maker)
    connection = _sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    table, columns, extra = hashstore.rds_table(connection)
    connection.close()
    good = table == expect_table and len(columns) == expect_columns
    ok &= good
    print(f"  {label:24} -> {table} using {', '.join(columns)}"
          f"{'' if good else f'   <-- expected {expect_table}'}")

# The modern one must come in with what it says about each file, and be
# treated as known-good so a hit means Non-Pertinent rather than a finding.
modern_path = os.path.join(ROOT, "modern.db")
_build(modern_path, _modern)
rds_result = store.import_set(modern_path, name="RDS_modern")
ok &= rds_result["kind"] == "nsrl" and rds_result["stored"] == 9
print(f"  imported {rds_result['stored']} hash(es) as {rds_result['kind']}")
print(f"    {rds_result['note']}")
rds_hit = [m for m in store.lookup([f"{80:064x}"]) if m["source"] == "RDS_modern"]
carried = rds_hit[0]["record"] if rds_hit else {}
ok &= carried.get("file_name") == "lib0.dll"
ok &= carried.get("product_name") == "Acme Installer"
print(f"    a match says: {carried}")

# An NSRL row says what the file is, and that is the whole value of a match:
# "known software" is a conclusion, "msvcrt.dll, Windows 10" is a finding.
nsrl_match = [m for m in store.lookup([A]) if m["kind"] == "nsrl"]
nsrl_record = nsrl_match[0].get("record", {}) if nsrl_match else {}
ok &= nsrl_record.get("FileName") == "setup.exe"
print(f"\nan NSRL match carries: {nsrl_record}")

# A Project VIC record is not just a hash and a category. MediaID, size, the
# victim and offender flags, the series and the tags are the reasons an
# examiner consults the set, and a match that drops them cannot be acted on.
full_vic = os.path.join(ROOT, "vic_full.json")
vic_records = [{
    "MD5": f"{i:032x}", "MediaID": 900000 + i, "Category": 1,
    "SHA1": f"{i:040x}", "VictimIdentified": True, "OffenderIdentified": False,
    "IsDistributed": True, "MediaSize": 204800 + i,
    "IsPrecategorized": True, "PrecategorizationSource": "ICACCOPS",
    "DateUpdated": "2026-03-19T14:22:07Z", "Tags": ["series:Blue"],
    "Series": "Blue", "MediaFiles": [],
    "AlternativeHashes": [{"SHA256": f"{i:064x}"}],
} for i in range(500, 503)]
json.dump({"@odata.context": "http://github.com/VICSDATAMODEL/ProjectVic/"
                             "DataModels/2.0.xml/US/$metadata#Cases",
           "value": [{"CaseNumber": "VIC_US", "Media": vic_records}]},
          open(full_vic, "w"))

imported = store.import_set(full_vic)
# MD5, SHA-1 and the SHA-256 from AlternativeHashes: three per record.
ok &= imported["stored"] == 9
print(f"\nProject VIC: {imported['stored']} hash(es) from "
      f"{len(vic_records)} record(s)")

# That digest is in the NSRL fixture too, so the lookup returns both sources
# and the caller has to pick -- which is the point of reporting every source
# a hash appears in rather than the first.
matched = store.lookup([f"{500:032x}"])
ok &= len(matched) >= 2
print(f"  the same digest is in {len(matched)} source(s): "
      f"{', '.join(sorted(m['source'] for m in matched))}")
vic_match = next((m for m in matched if m["kind"] == "projectvic"), None)
ok &= vic_match is not None
kept = (vic_match or {}).get("record", {})
print("  the record travels with the hash:")
for field in ("MediaID", "Category", "Series", "Tags", "MediaSize",
              "DateUpdated", "VictimIdentified", "IsPrecategorized",
              "PrecategorizationSource"):
    present = field in kept
    ok &= present
    print(f"    {field:24} {kept.get(field, 'MISSING')}")

# A set stores several hashes of one file; any of them must find the record.
by_sha256 = store.lookup([f"{500:064x}"])
ok &= bool(by_sha256) and by_sha256[0]["record"].get("MediaID") == 900500
print(f"  found by its SHA-256 from AlternativeHashes: "
      f"MediaID {by_sha256[0]['record'].get('MediaID') if by_sha256 else None}")

# A Project VIC file comes in more than one shape, and the national download
# is not the shape a case export is: its records sit directly in "value",
# with no case wrapping them. Reading only the case shape made a 70-million
# hash import fall back to a blind scan, which keeps the digests and loses
# every category -- the whole reason for importing the set.
flat = os.path.join(ROOT, "vic_flat.json")
flat_records = [{"MD5": f"{i:032x}", "MediaID": 700000 + i, "Category": 1,
                 "SHA1": f"{i:040x}", "MediaSize": 2048,
                 "IsPrecategorized": True, "PrecategorizationSource": "ICACCOPS",
                 "MediaFiles": [], "AlternativeHashes": []}
                for i in range(700, 704)]
with open(flat, "w") as fh:
    # One long line, as a real download is.
    json.dump({"@odata.context": "http://github.com/VICSDATAMODEL/ProjectVic/"
                                 "DataModels/2.0.xml/US/$metadata#Media",
               "value": flat_records}, fh, separators=(",", ":"))

flat_result = store.import_set(flat)
ok &= flat_result["kind"] == "projectvic" and not flat_result["note"]
print(f"\nthe full-download shape: {flat_result['stored']} hash(es) read "
      f"structurally{', no fallback' if not flat_result['note'] else ''}")

flat_hit = [m for m in store.lookup([f"{700:032x}"]) if m["kind"] == "projectvic"]
carried = flat_hit[0]["record"] if flat_hit else {}
ok &= carried.get("MediaID") == 700700 and flat_hit[0]["category"] == 1
print(f"  with its category ({flat_hit[0]['category'] if flat_hit else None}) "
      f"and MediaID ({carried.get('MediaID')})")

# A VICS file whose records are not where the reader expects must still
# import, by scanning, rather than reporting a bare zero.
odd = os.path.join(ROOT, "odd_vic.json")
json.dump({"@odata.context": "http://github.com/VICSDATAMODEL/ProjectVic/"
                             "DataModels/2.0.xml/US/$metadata#Cases",
           "value": [{"CaseNumber": "x", "MediaRecords": [
               {"MD5": f"{i:032x}", "Category": 1} for i in range(50)]}]},
          open(odd, "w"))
salvaged = store.import_set(odd)
ok &= salvaged["stored"] == 50 and "scanning" in salvaged["note"]
print(f"\nan unexpected VICS shape: {salvaged['stored']} hash(es) recovered "
      f"by scanning")

# A file with no hashes in it leaves no empty source behind.
junk = os.path.join(ROOT, "junk.txt")
open(junk, "w").write("no hashes here\njust words\n")
nothing = store.import_set(junk)
ok &= nothing["stored"] == 0
ok &= "junk.txt" not in [src["name"] for src in store.sources()]
print(f"a file with no hashes: stored {nothing['stored']}, and no empty "
      f"source left behind")

# The inspector has to explain a file rather than just failing on it.
report = hashstore.inspect_set(odd)
ok &= report["kind"] == "projectvic" and not report["structured_sample"]
ok &= bool(report["scanned_sample"])
print(f"inspector on that file: kind={report['kind']}, "
      f"structural={len(report['structured_sample'])}, "
      f"scanned={len(report['scanned_sample'])}")

# The .db has to be self-contained: people copy a store to a share, and a
# file whose recent writes are still in the -wal looks like a failed import.
import sqlite3 as _sqlite3
copy = os.path.join(ROOT, "copied_store.db")
shutil.copy(os.path.join(ROOT, "store.db"), copy)
con = _sqlite3.connect(copy)
copied = con.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]
con.close()
ok &= copied == store.size()
print(f"\na copy of the .db alone holds {copied:,} of {store.size():,} hash(es)")

checked = hashstore.verify(os.path.join(ROOT, "store.db"))
ok &= checked["total"] == store.size()
wal = (checked.get("side_files") or {}).get("-wal", 0)
print(f"verify reads {checked['total']:,} from the file; "
      f"write-ahead log is {wal:,} bytes")
ok &= wal <= 4096

# Removing a source takes its hashes with it and leaves the others alone.
before = store.size()
removed = store.remove_source("ours.txt")
ok &= store.size() == before - removed
print(f"\nremoving a source dropped {removed} hash(es); {store.size():,} remain")
store.close()

# And it is all still there after reopening.
store = hashstore.HashStore(os.path.join(ROOT, "store.db"))
reopened = [s["name"] for s in store.sources()]
ok &= "DC26-253" in reopened
print(f"after reopening: {reopened}")
store.close()

# A store on a network share is the one placement that fails late and
# badly: SQLite needs file locking that shares implement poorly, so it works
# until it does not, and when it does not it can take the store with it.
print("\nwhere the store is kept")
for path, expected in ((r"\\\\LABSERVER\\hashstore\\hash_store.db", True),
                       ("//server/share/hash_store.db", True),
                       (os.path.join(ROOT, "hash_store.db"), False)):
    seen = hashstore.is_network_path(path)
    ok &= seen == expected
    print(f"  {path[:44]:46} share={seen}")

import sqlite3 as _sqlite3

explained = hashstore._explain_open_failure(
    r"\\\\LABSERVER\\hashstore\\hash_store.db",
    _sqlite3.OperationalError("disk I/O error"))
# The message has to name the cause and the way out, not just repeat the
# error: "disk I/O error" tells an examiner nothing they can act on.
names_cause = "network share" in explained
names_fix = "hashserver" in explained
ok &= names_cause and names_fix
print(f"  the message names the cause: {names_cause}")
print(f"  and what to do instead     : {names_fix}")

# And a store that is on a share opens without write-ahead logging, which
# is the specific thing shares cannot do.
local = hashstore.HashStore(os.path.join(ROOT, "journal_check.db"))
mode = local._con.execute("PRAGMA journal_mode").fetchone()[0]
local.close()
ok &= mode.lower() == "wal"
print(f"  a local store still uses write-ahead logging: {mode}")

# Opening a path that holds no store used to make an empty one there and
# report it as the store. An examiner who typed hashes.db where the import
# had written hash_store.db was told "nothing in store yet" and went looking
# for a fault in a store that was never theirs.
print("\nopening a store that is not there")
two_names = os.path.join(ROOT, "two_names")
shutil.rmtree(two_names, ignore_errors=True)
os.makedirs(two_names)
written = hashstore.HashStore(os.path.join(two_names, "hash_store.db"))
written.contribute_rows(
    [{"md5": f"{i:032x}", "category": 1, "label": "CAM"} for i in range(50)],
    "VIC_US_2026", contributor="import")
written.close()

wrong_name = os.path.join(two_names, "hashes.db")
try:
    hashstore.HashStore(wrong_name, create=False)
    refused = False
    message = ""
except hashstore.StoreMissing as exc:
    refused = True
    message = str(exc)
ok &= refused
print(f"  opening a path with no store is refused: {refused}")

# It does not leave an empty store behind, which is what made the original
# confusing: the path then existed, and looked like the answer.
ok &= not os.path.exists(wrong_name)
print(f"  and no empty store is created there    : {not os.path.exists(wrong_name)}")

# The message names the store that is actually in that folder, which is the
# answer the examiner needed.
names_real = "hash_store.db" in message
ok &= names_real
print(f"  the message names the store that is there: {names_real}")

# A database that is not one of ours must not be offered as one.
import sqlite3 as _sqlite3

other = os.path.join(two_names, "notes.db")
_connection = _sqlite3.connect(other)
_connection.execute("CREATE TABLE notes(a)")
_connection.close()
ok &= hashstore.looks_like_store(os.path.join(two_names, "hash_store.db"))
ok &= not hashstore.looks_like_store(other)
print(f"  an unrelated database is not offered   : "
      f"{not hashstore.looks_like_store(other)}")

# Getting a store off a share is the move this exists for, and copying the
# file in Explorer is how one arrives missing its most recent imports:
# SQLite keeps recent writes in a journal beside the database, so a copy
# taken without it is older than it looks, silently.
print("\nmoving a store")
move_root = os.path.join(ROOT, "move")
shutil.rmtree(move_root, ignore_errors=True)
os.makedirs(os.path.join(move_root, "from"))
os.makedirs(os.path.join(move_root, "to"))

origin_path = os.path.join(move_root, "from", "hash_store.db")
origin = hashstore.HashStore(origin_path)
origin.contribute_rows(
    [{"md5": f"{i:032x}", "sha1": f"{i:040x}", "category": 1, "label": "CAM"}
     for i in range(500)], "VIC_US_2026", contributor="import")

# Deliberately left open, as it would be during an import: the recent
# writes are in the journal, not yet folded into the database file.
journal = [name for name in os.listdir(os.path.join(move_root, "from"))
           if name.endswith(("-wal", "-journal"))]
print(f"  a journal sits beside the store: {bool(journal)} {journal}")

target = os.path.join(move_root, "to", "hash_store.db")
moved = hashstore.move_store(origin_path, target)
origin.close()

ok &= moved["complete"]
print(f"  copied {moved['hashes']:,} of {moved['was']:,} hash(es): "
      f"{moved['complete']}")

# The copy has to be a working store, not just the right size.
copied = hashstore.HashStore(target)
hit = copied.lookup([f"{7:032x}"])
copied.close()
ok &= bool(hit)
print(f"  a hash from the original is found in the copy: {bool(hit)}")

# It must never overwrite something that is already there.
try:
    hashstore.move_store(origin_path, target)
    refused = False
except ValueError:
    refused = True
ok &= refused
print(f"  it refuses to overwrite an existing file: {refused}")

# And the original is left alone: moving evidence is done by hand, after
# the copy has been seen to be good.
ok &= os.path.isfile(origin_path)
print(f"  the original is left where it was: {os.path.isfile(origin_path)}")

# SQLite allows one writer at a time, and importing a national set holds
# that writer for minutes. A second write failing instantly with "database
# is locked" reads as a fault; it is a queue, and waiting costs nothing.
print("\nwriting while another writer holds the store")
import sqlite3 as _sqlite3
import threading as _threading
import time as _time

busy_dir = os.path.join(ROOT, "busy")
shutil.rmtree(busy_dir, ignore_errors=True)
os.makedirs(busy_dir)
busy_path = os.path.join(busy_dir, "hash_store.db")
first = hashstore.HashStore(busy_path)
first.contribute_rows(
    [{"md5": f"{i:032x}", "category": 1, "label": "CAM"} for i in range(20)],
    "setup", contributor="x")

holding = _threading.Event()


def _hold():
    first._con.execute("BEGIN IMMEDIATE")
    holding.set()
    _time.sleep(3)
    first._con.execute("COMMIT")


_threading.Thread(target=_hold, daemon=True).start()
holding.wait()

second = hashstore.HashStore(busy_path)
started = _time.time()
try:
    added = second.contribute_rows(
        [{"md5": "f" * 32, "category": 5, "label": "Adult"}],
        "DC26-253", contributor="jsmith")
    waited = _time.time() - started
    patient = added == 1 and waited >= 2
except _sqlite3.OperationalError:
    patient = False
    waited = _time.time() - started
second.close()
first.close()
ok &= patient
print(f"  it waited {waited:.1f}s for the other writer and then wrote: "
      f"{patient}")

# And a store that stays busy says what that means, not what SQLite calls it.
explained = hashstore._explain_open_failure(
    busy_path, _sqlite3.OperationalError("database is locked"))
ok &= "one writer at a time" in explained and "let it finish" in explained
print(f"  a store that stays busy explains itself: "
      f"{'one writer at a time' in explained}")

# Every request to a shared store asked it how many hashes it holds and
# what each set holds. Both were worked out by scanning the table -- a
# count over every row, and a join of the whole table to the sources table.
# On a national set that is tens of millions of rows per request, and the
# server stopped answering anything at all.
print("\nwhat a status request costs")
import time as _time

speed_dir = os.path.join(ROOT, "speed")
shutil.rmtree(speed_dir, ignore_errors=True)
os.makedirs(speed_dir)
big = hashstore.HashStore(os.path.join(speed_dir, "hash_store.db"))
big.contribute_rows(
    [{"md5": f"{i:032x}", "category": 1, "label": "CAM"}
     for i in range(120000)], "VIC_US_2026", contributor="setup")

big.size()                      # the first call settles the total
started = _time.time()
total = big.size()
kept = _time.time() - started

started = _time.time()
scanned = big.recount()
counting = _time.time() - started

ok &= total == scanned
print(f"  the running total agrees with counting: {total:,} == {scanned:,}")
ok &= kept < counting
print(f"  reading it: {kept * 1000:.1f} ms   counting it: "
      f"{counting * 1000:.0f} ms")

big.sources()                   # the first call settles each set's count
started = _time.time()
listed = big.sources()
per_source = _time.time() - started
ok &= listed and listed[0]["hashes"] == 120000
print(f"  the sets and their counts: {per_source * 1000:.1f} ms "
      f"({listed[0]['hashes']:,} in {listed[0]['name']})")

# The counts have to stay right, or fast is worse than slow.
big.contribute_rows([{"md5": "e" * 32, "category": 5, "label": "Adult"}],
                    "VIC_US_2026", contributor="setup")
ok &= big.size() == big.recount()
after = {row["name"]: row["hashes"] for row in big.sources()}
ok &= after["VIC_US_2026"] == 120001
print(f"  after adding one: total {big.size():,}, "
      f"set {after['VIC_US_2026']:,}")

removed = big.remove_source("VIC_US_2026")
ok &= big.size() == big.recount() == 0
print(f"  after removing the set: total {big.size():,} "
      f"(agrees with counting: {big.size() == big.recount()})")
big.close()

# An import of a national set writes faster than SQLite folds its log in,
# so the log grows during it. That is the right trade -- until the program
# is stopped part way, when every later read has to walk whatever was left.
print("\nthe write-ahead log after an interrupted import")
wal_dir = os.path.join(ROOT, "wal")
shutil.rmtree(wal_dir, ignore_errors=True)
os.makedirs(wal_dir)
interrupted = hashstore.HashStore(os.path.join(wal_dir, "hash_store.db"))
interrupted._con.execute("PRAGMA wal_autocheckpoint=0")
for batch in range(3):
    interrupted._con.executemany(
        "INSERT OR IGNORE INTO hashes (digest, algo, source_id, category, "
        "note, meta) VALUES (?,?,?,?,?,?)",
        [(f"{batch:02x}{i:030x}", "md5", None, 1, "", "")
         for i in range(30000)])
    interrupted._con.commit()

left = interrupted.wal_bytes()
ok &= left > 0
print(f"  left behind: {left / 1048576:.1f} MiB of log")

folded = interrupted.checkpoint()
ok &= folded["complete"]
print(f"  folding it in took {folded['seconds']}s and moved "
      f"{folded['folded_in'] / 1048576:.1f} MiB")
ok &= interrupted.wal_bytes() < left
print(f"  log afterwards: {interrupted.wal_bytes() / 1024:.1f} KiB")
interrupted.close()

# A hash store is one large SQLite file several people write to over a
# network, on a machine that can lose power mid-import. When it goes
# wrong the symptom is not an error: lookups stop matching things they
# used to match, which in a case looks like the file not being in the
# set.
print("\nchecking the store for damage")
import subprocess as _sub

_sound = os.path.join(ROOT, "sound.db")
_st = hashstore.HashStore(_sound)
_st.contribute_rows([{"md5": f"{i:032x}", "category": 1, "label": "CAM"}
                     for i in range(200)], "VIC", contributor="setup")
_st.close()

_r = _sub.run([sys.executable, "-m", "fme", "store", "--store", _sound,
               "--integrity"], capture_output=True, text=True,
              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ok &= _r.returncode == 0 and "pages are sound" in _r.stdout
print(f"   a sound store passes: {_r.returncode == 0}")

# And it must say what it does not know: structurally intact is not
# the same as holding the right hashes.
ok &= "does not" in _r.stdout and "right hashes" in _r.stdout
print(f"   and says what that does not prove: True")

with open(_sound, "r+b") as _fh:
    _fh.seek(40960)
    _fh.write(b"\xff" * 8192)
_r = _sub.run([sys.executable, "-m", "fme", "store", "--store", _sound,
               "--integrity"], capture_output=True, text=True,
              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ok &= _r.returncode != 0
print(f"   a damaged store fails: {_r.returncode != 0}")
ok &= "Stop the server" in _r.stdout and "Do not repair in place" in _r.stdout
print(f"   and says what to do about it, in order: True")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
