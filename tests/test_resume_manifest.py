"""A resumed case keeps everything it already had in manifest.json.

manifest.json is written at the end of a run from the files that run holds
in memory, and every report, export and gallery reads it. A resume used to
reload only the digests of what the earlier run found -- enough to stop a
file being recovered twice, not enough to list it. The resumed case's
manifest.json then named only the files found after the resume, while
manifest.csv beside it named them all. Nothing failed and nothing warned:
the case simply reported less than it held.

The same gap dropped evidence of a second kind. A file found again after
the resume was counted as a duplicate, but its location was never written,
because the copy it duplicated was not in memory to be recorded against.

This drives OutputManager directly, so it needs no image and runs in a
second or two.
"""
import csv
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.engines import media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_resume_manifest-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def picture(seed: int) -> bytes:
    """Enough random bytes to clear the minimum size, framed as a JPEG."""
    rng = random.Random(seed)
    body = bytes(rng.getrandbits(8) for _ in range(media_core.MIN_IMAGE_SIZE + 4096))
    return b"\xff\xd8\xff\xe0" + body + b"\xff\xd9"


def store(out, data: bytes, stem: str, **meta):
    meta.setdefault("engine", "carve")
    return out.store(iter([data]), category="image", ext="jpg", stem=stem, **meta)


def case(name: str) -> str:
    folder = os.path.join(ROOT, name)
    os.makedirs(folder)
    return folder


def manifest(folder: str) -> dict:
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def rows(folder: str) -> list:
    with open(os.path.join(folder, "manifest.csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


A, B = picture(1), picture(2)

print("a resumed case lists the files from before the resume")
folder = case("before_and_after")
out = media_core.OutputManager(folder, metadata=False)
store(out, A, "recovered_0000_4096", offset=4096)
out.close({})
out = media_core.OutputManager(folder, metadata=False, resume=True)
store(out, B, "recovered_0001_8192", offset=8192)
out.close({})
listed = sorted(f["sha256"] for f in manifest(folder)["files"])
in_rows = sorted(r["sha256"] for r in rows(folder))
expected = sorted(hashlib.sha256(x).hexdigest() for x in (A, B))
ok &= listed == expected
print(f"   manifest.json lists both files: {listed == expected}")
ok &= listed == in_rows
print(f"   manifest.json agrees with manifest.csv: {listed == in_rows}")

# Read back as text, the earlier row would carry "False" for deleted --
# which is true -- and a string where the offset should be a number.
first = next((f for f in manifest(folder)["files"]
             if f["sha256"] == hashlib.sha256(A).hexdigest()), None)
typed = first is not None and first["offset"] == 4096 and first["deleted"] is False \
    and first["latitude"] is None and isinstance(first["size"], int)
ok &= typed
print(f"   the earlier file's fields keep their types: {typed}")

print("\na duplicate of an earlier file is recorded with its location")
folder = case("duplicate_after_resume")
out = media_core.OutputManager(folder, metadata=False)
store(out, A, "recovered_0000_4096")
out.close({})
out = media_core.OutputManager(folder, metadata=False, resume=True)
again = store(out, A, "x", engine="filesystem", source_path="/DCIM/a.jpg")
out.close({})
where = [d["source_path"] for d in manifest(folder)["duplicates"]]
ok &= again is None and len(rows(folder)) == 1
print(f"   not stored a second time: {again is None and len(rows(folder)) == 1}")
ok &= where == ["/DCIM/a.jpg"]
print(f"   its location is in manifest.json: {where == ['/DCIM/a.jpg']}")
ok &= len(manifest(folder)["files"]) == 1
print(f"   the file itself is still listed: {len(manifest(folder)['files']) == 1}")

print("\nduplicates found before the resume survive it")
folder = case("earlier_duplicates")
out = media_core.OutputManager(folder, metadata=False)
store(out, A, "recovered_0000_4096")
store(out, A, "y", engine="filesystem", source_path="/Pictures/a.jpg")
out.close({})
out = media_core.OutputManager(folder, metadata=False, resume=True)
store(out, B, "recovered_0001_8192")
out.close({})
where = [d["source_path"] for d in manifest(folder)["duplicates"]]
ok &= where == ["/Pictures/a.jpg"]
print(f"   the earlier duplicate is still listed: {where == ['/Pictures/a.jpg']}")

print("\na half-written last row is left out, not half-read")
folder = case("torn_row")
out = media_core.OutputManager(folder, metadata=False)
store(out, A, "recovered_0000_4096")
out.close({})
# What a crash in the middle of writing a row leaves behind.
with open(os.path.join(folder, "manifest.csv"), "a", encoding="utf-8") as fh:
    fh.write("carve,image,images/jpg/torn.jpg,not-a-size")
out = media_core.OutputManager(folder, metadata=False, resume=True)
store(out, B, "recovered_0001_8192")
out.close({})
paths = sorted(os.path.basename(f["out_path"]) for f in manifest(folder)["files"])
clean = "torn.jpg" not in paths and len(paths) == 2
ok &= clean
print(f"   the torn row is skipped and the rest kept: {clean}")

print("\na resume with no earlier manifest starts a case as usual")
folder = case("nothing_to_resume")
out = media_core.OutputManager(folder, metadata=False, resume=True)
store(out, A, "recovered_0000_4096")
out.close({})
fresh = len(manifest(folder)["files"]) == 1 and len(rows(folder)) == 1
ok &= fresh
print(f"   one file, one row: {fresh}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
