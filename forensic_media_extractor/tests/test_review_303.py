"""Defects found by the review of v2026.11.303. Each check failed there.

The first is the one that mattered most: auto-grading took "lowest code is
most severe", and code 0 is Non-Pertinent -- so a file one source graded
CAM and another Non-Pertinent was graded Non-Pertinent, by a machine, and
left the examiner's backlog.
"""
import _sandbox  # noqa: F401
import hashlib
import io
import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from fme.engines import (credentials, db_extract, exif_read, fat32_core,  # noqa: E402
                         media_core, similarity)
from fme.exports import (c4all_export, c4all_import, docx_parts, protocol,  # noqa: E402
                         report, vic_import)
from fme.review import casedb, recover, registry, savedset, settings  # noqa: E402
from fme.web import media_gui as g  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "review303-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
H = {"X-FME-Request": "1"}
client = g.app.test_client()
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}: {'ok' if good else 'WRONG'} {detail}".rstrip())


def folder(name):
    path = os.path.join(ROOT, name)
    os.makedirs(path, exist_ok=True)
    return path


# 1. Severity: CAM outranks Non-Pertinent; disagreements are held.
case = folder("case")
media_core.OutputManager(case).close({"image": "/e.E01"})
jid = client.post("/api/cases/open", json={"out_dir": case},
                  headers=H).get_json()["job_id"]
a, b = "a" * 64, "b" * 64
# Each with the scheme it was graded under, as the store and (since
# v2026.11.318) the stash say; a code with none is never applied.
lookup = {a: [{"set": "DC25-100", "kind": "case", "category": 0, "region": "US"},
              {"set": "your stash", "kind": "stash", "category": 1, "region": "US"}],
          b: [{"set": "DC25-100", "kind": "case", "category": 0, "region": "US"},
              {"set": "your stash", "kind": "stash", "category": 1, "region": "US"}]}
g._apply_hash_categories(jid, lookup, hold=[b])
decided = casedb.CaseDB(os.path.join(case, "case.db")).decisions()
check("CAM and Non-Pertinent from two sources: graded CAM",
      decided.get(a, {}).get("category") == 1, str(decided.get(a)))
check("a stash/store disagreement is left for the examiner", b not in decided)
check("severity: 1 before 7 before 0",
      sorted([0, 7, 1], key=casedb.severity_rank) == [1, 7, 0])

# 2. C4All placement: a failed move keeps the case's file; copy is a copy.
src = os.path.join(folder("place"), "evidence.jpg")
with open(src, "wb") as fh:
    fh.write(b"EVIDENCE")
blocked = os.path.join(ROOT, "place", "out", "x.jpg")
os.makedirs(os.path.join(blocked, "inner"))
try:
    c4all_export.place_file(src, blocked, "move")
except OSError:
    pass
check("a move that cannot land leaves the case's file", os.path.isfile(src))
linked = os.path.join(ROOT, "place", "out", "y.jpg")
c4all_export.place_file(src, linked, "hardlink")
c4all_export.place_file(src, linked, "copy")
check("copy after an earlier hard link is a separate file",
      not os.path.samefile(src, linked))

# 3. Rebuild carries what duplicates taught the kept file.
with __import__("contextlib").redirect_stdout(io.StringIO()):
    import make_media_test_image as fx
crashed = folder("crashed")
out = media_core.OutputManager(crashed, metadata=False)
jpeg = fx.make_jpeg(1)
out.store(iter([jpeg]), category="image", ext="jpg", stem="recovered_0001_100",
          engine="carve", offset=100, bounded="parsed")
out.store(iter([jpeg]), category="image", ext="jpg", stem="x",
          engine="fat-deleted", source_path="/DCIM/IMG_1.JPG", deleted=True)
out._csv_fh.close()
out._dup_csv_fh.close()
recover.rebuild_manifest(crashed)
with open(os.path.join(crashed, "manifest.json")) as fh:
    kept = json.load(fh)["files"][0]
check("a rebuilt case keeps instances, name and deleted flag",
      kept["instances"] == 2 and kept["deleted"] is True
      and kept["source_path"] == "/DCIM/IMG_1.JPG", str(kept["instances"]))

# 4. Project VIC import and hash set.
vic = folder("vic")
outside = os.path.join(ROOT, "secret.jpg")
with open(outside, "wb") as fh:
    fh.write(jpeg)
inside = os.path.join(vic, "in.jpg")
with open(inside, "wb") as fh:
    fh.write(jpeg)
records = [{"RelativeFilePath": "../secret.jpg", "Category": 1,
            "MediaFiles": [{"FileName": "s.jpg"}]},
           {"RelativeFilePath": "in.jpg", "Category": 1, "MD5": "0" * 32,
            "MediaFiles": [{"FileName": "in.jpg"}]}]
with open(os.path.join(vic, "set.json"), "w") as fh:
    json.dump({"value": records}, fh)
summary = vic_import.import_media(os.path.join(vic, "set.json"), folder("vicout"))
with open(os.path.join(ROOT, "vicout", "manifest.json")) as fh:
    files = json.load(fh)["files"]
check("a VICS path outside the export is not read",
      summary.get("outside_export") == 1 and len(files) == 1)
check("a file failing its hash does not carry the record's category",
      files and "category=none" in files[0]["signature"]
      and summary["hash_mismatch"] == 1, files[0]["signature"] if files else "")
broken = os.path.join(vic, "broken.json")
with open(broken, "w") as fh:
    fh.write('{"value": [' + ",".join(['{"MD5": "%032x"}' % i for i in range(5)])
             + ', {"MD5": oops}, {"MD5": "' + "f" * 32 + '"}]}')
try:
    list(vic_import.stream_media(broken))
    loud = False
except ValueError:
    loud = True
check("a damaged VICS record stops the read with an error", loud)
twice = os.path.join(vic, "twice.json")
with open(twice, "w") as fh:
    json.dump({"value": [{"MD5": "c" * 32, "Category": 1},
                         {"MD5": "c" * 32}]}, fh)
check("a hash listed twice keeps its category",
      vic_import.load_hash_set(twice).get("c" * 32) == 1)

# 5. Report counts.
rows = [{"sha256": "aa", "category_name": "CAM", "category": 1, "kind": "image",
         "partition": "PhoneA"}]
data = {"rows": rows, "manifest": {"duplicates": [
    {"sha256": "aa", "partition": "PhoneB"}, {"sha256": "aa", "partition": "PhoneB"}]}}
per = {where: s["totals"]["images"]
       for where, s in protocol.by_source(data, {}, report.conclusion)}
check("each copy counted on its own device", per == {"PhoneA": 1, "PhoneB": 2},
      str(per))
check("a cell that starts with = is not a formula",
      report.spreadsheet_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
      and report.spreadsheet_safe("-3.5") == "-3.5")
check("XML-forbidden characters are written visibly",
      docx_parts.xml_text("a\x01b") == "a\\x01b")

# 6. Engines.
payload = bytes([3, 12 + 2 * 10, 1]) + b"1234"     # a 10-byte blob, 4 present
value = db_extract.decode_record(payload)[0]
check("a SQLite blob shorter than its record says is marked short",
      isinstance(value, db_extract.ShortBlob) and value.expected == 10)
real = credentials.reader
credentials.reader = lambda: None
result = credentials.scan(ROOT, [{"out_path": "secret.jpg", "sha256": "s"}])
credentials.reader = real
check("no credentials reader is counted as not established",
      result["not_established"] == 1)
from PIL import Image  # noqa: E402
eight = Image.linear_gradient("L").resize((64, 64))
sixteen = Image.frombytes(
        "I;16", (64, 64), b"".join(struct.pack("<H", p * 257)
                                   for p in eight.tobytes()))
check("16-bit grayscale hashes like its 8-bit self",
      list(similarity.gray(sixteen).tobytes()) == list(eight.tobytes()))
big = os.path.join(ROOT, "late_moov.mp4")
with open(big, "wb") as fh:
    fh.write(struct.pack(">I", 16) + b"ftypisom" + b"\0" * 4)
    fh.write(struct.pack(">I", 80 * 1024 * 1024 + 8) + b"mdat")
    fh.seek(80 * 1024 * 1024, 1)
    moov = b"moov"
    fh.write(struct.pack(">I", 8) + moov)
with open(big, "rb") as fh:
    found = exif_read.moov_box(fh, os.path.getsize(big))
check("a moov after 80 MB of mdat is found", found[4:8] == b"moov")
os.remove(big)
bs = fat32_core.BootSector(512, 1, 1, 1, 1, 1, 2, "", "FAT32")
table = fat32_core.FatTable.__new__(fat32_core.FatTable)
table.bs, table.entries = bs, [0] * 64
table.entries[2], table.entries[3] = 3, 0x0FFFFFFF
check("a size field smaller than the FATs does not empty the volume",
      list(table.chain(2)) == [2, 3])

# 7. Review state.
cdb = casedb.CaseDB(os.path.join(folder("undo"), "case.db"))
cdb.set_category("d" * 64, 1)
cdb.set_note("d" * 64, "victim identified")
cdb.undo()
check("undo keeps a note written after the grading",
      cdb.decision("d" * 64)["note"] == "victim identified")
code = cdb.add_category("Series X", code=12)
check("a saved category keeps its code where it is free", code == 12)
cdb.close()
book = registry.CaseRegistry(os.path.join(ROOT, "cases.db"))
book.register(case)
book.relink(case, case)
check("relinking a case to itself keeps it", bool(book.get(case)))
settings.save({"examiner": "Det. T", "agency": "CID"})
client.post("/api/settings", json={"timezone": "UTC"}, headers=H)
kept = settings.load()
check("saving one setting leaves the others", kept.get("agency") == "CID",
      str({k: kept.get(k) for k in ("agency", "timezone")}))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASSED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
