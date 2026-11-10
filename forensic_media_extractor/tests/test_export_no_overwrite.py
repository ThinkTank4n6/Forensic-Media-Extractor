"""Exporting files never overwrites one with another, and never calls a copy
verified when there was nothing to verify it against.

export_files.py says it never overwrites. A name collision fell back once,
to "<stem>_<first 8 of SHA-256>", and then wrote with "wb": a third file of
the same name and hash prefix replaced the second. Reproduced with three
IMG_0001.jpg: exported_files.csv listed three, each "hash_verified", over a
folder holding two. Thumbnails, one folder for every category, were named by
stem alone, so two files of one name in two categories left one thumbnail.
And a row with no recorded hash was written down as verified.
"""
import csv
import hashlib
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.exports import export_files  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_export_overwrite-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def case(name, contents, category=lambda i: "Cat 1", recorded=True):
    folder = os.path.join(ROOT, name, "case")
    rows = []
    for i, data in enumerate(contents):
        sub = os.path.join(folder, f"d{i}")
        os.makedirs(sub)
        with open(os.path.join(sub, "IMG_0001.jpg"), "wb") as fh:
            fh.write(data)
        rows.append({"out_path": f"d{i}/IMG_0001.jpg", "ext": "jpg",
                     "category_name": category(i),
                     "sha256": hashlib.sha256(data).hexdigest() if recorded else ""})
    return rows, folder, os.path.join(ROOT, name, "out")


def on_disk(dest):
    found = {}
    for here, _, files in os.walk(dest):
        for name in files:
            if name != "exported_files.csv":
                path = os.path.join(here, name)
                found[os.path.relpath(path, dest)] = open(path, "rb").read()
    return found


def manifest(result):
    with open(result["manifest"], newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# 1. Three files of one name with no recorded hash: the reported case.
rows, src, dest = case("no-hash", [b"first", b"second", b"third"], recorded=False)
result = export_files.export(rows, src, dest)
files, listed = on_disk(dest), manifest(result)
check("three rows, three files, each row naming its own",
      len(listed) == 3 and len(files) == 3
      and sorted(r["exported_as"] for r in listed) == sorted(files),
      f"{len(listed)} rows, {len(files)} files")
check("and every file's bytes survived",
      sorted(files.values()) == [b"first", b"second", b"third"])
check("a copy with no recorded hash is not called verified",
      all(r["hash_verified"] == "" for r in listed)
      and len(result["unverified"]) == 3)

# 2. Three identical copies: same name, same hash, same prefix.
rows, src, dest = case("same-bytes", [b"same"] * 3)
result = export_files.export(rows, src, dest)
files, listed = on_disk(dest), manifest(result)
check("identical copies of one name each get a file",
      len(files) == 3 and len({r["exported_as"] for r in listed}) == 3,
      ", ".join(sorted(files)))
check("and a copy checked against its recorded hash is verified",
      all(r["hash_verified"] == "True" for r in listed) and not result["unverified"])

# 3. Thumbnails: one name in two categories.
rows, src, dest = case("thumbs", [b"cat one", b"cat two"],
                       category=lambda i: f"Cat {i + 1}")
result = export_files.export(rows, src, dest,
                             thumbnails=lambda row: row["category_name"].encode())
files, listed = on_disk(dest), manifest(result)
thumbs = {r["exported_as"]: r["thumbnail"] for r in listed}
check("each file has its own thumbnail, named in the manifest",
      len(set(thumbs.values())) == 2 and all(thumbs.values())
      and all(files[t] == (b"Cat 1" if "Cat 1" in f else b"Cat 2")
              for f, t in thumbs.items()), str(thumbs))

# 4. A copy that fails part way leaves nothing behind.
rows, src, dest = case("fails", [b"fine", b"breaks"])
real = export_files._hash_copy


def breaks_on_second(source, writing):
    if "d1" in source:
        writing.write(b"half")
        writing.close()
        raise OSError("device removed (simulated)")
    return real(source, writing)


export_files._hash_copy = breaks_on_second
try:
    result = export_files.export(rows, src, dest)
finally:
    export_files._hash_copy = real
files = on_disk(dest)
check("a failed copy is reported and not left in the export",
      len(result["failed"]) == 1 and list(files.values()) == [b"fine"], str(files))

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
