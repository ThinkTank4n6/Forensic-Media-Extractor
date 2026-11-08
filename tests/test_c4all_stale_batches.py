"""A C4All re-export with fewer files leaves none of the earlier export's.

The batch folders (DVD01(CASE)/01/00000001.JPG ...) were never cleared, so
a re-export after grading -- fewer files, fewer discs -- left the earlier
export's DVD02, DVD03 ... in the folder. A disc burned from it, or a review
tool pointed at it, carried files the new index no longer listed. Now a
re-export removes what it no longer lists, but only what removing cannot
lose: a link or copy of a file still in the case. A file an earlier export
*moved* is the case's only copy, and it stays and is reported.
"""
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.exports import c4all_export  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "c4all_stale-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
ok = True


def check(label, condition):
    global ok
    ok &= bool(condition)
    print(("ok   " if condition else "FAIL ") + label)


def make_case(name: str, count: int) -> str:
    case = os.path.join(ROOT, name)
    os.makedirs(os.path.join(case, "image"))
    files = []
    for i in range(count):
        data = b"\xff\xd8\xff\xe0" + bytes([i]) * 600 + b"\xff\xd9"
        rel = f"image/recovered_{i:04d}.jpg"
        with open(os.path.join(case, rel), "wb") as fh:
            fh.write(data)
        files.append({"out_path": rel, "size": len(data), "ext": "jpg",
                      "category": "image", "engine": "filesystem",
                      "md5": hashlib.md5(data).hexdigest(),
                      "sha1": hashlib.sha1(data).hexdigest(),
                      "sha256": hashlib.sha256(data).hexdigest(),
                      "source_path": f"/DCIM/IMG_{i:04d}.JPG"})
    return case, files


def write_manifest(case: str, files: list) -> str:
    path = os.path.join(case, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"files": files, "duplicates": [], "summary": {}}, fh)
    return path


def batch_files(export_dir: str) -> list:
    return sorted(os.path.relpath(os.path.join(r, f), export_dir)
                  for r, _, fs in os.walk(export_dir) for f in fs
                  if os.path.basename(r).isdigit())


# Four files, a "disc" one file wide: four DVD folders.
case, files = make_case("case", 4)
manifest = write_manifest(case, files)
export_dir = os.path.join(ROOT, "export")
for mode in ("hardlink", "copy"):
    shutil.rmtree(export_dir, ignore_errors=True)
    c4all_export.export(manifest, export_dir, case="C1", mode=mode, media_bytes=700)
    check(f"{mode}: the first export places four files on four discs",
          len(batch_files(export_dir)) == 4)
    # Graded down to one file, exported again into the same folder.
    write_manifest(case, files[:1])
    result = c4all_export.export(manifest, export_dir, case="C1", mode=mode,
                                 media_bytes=700)
    left = batch_files(export_dir)
    check(f"{mode}: the re-export leaves only its own file ({left})",
          left == [os.path.join("DVD01(C1)", "01", "00000001.JPG")])
    check(f"{mode}: and the empty DVD folders are gone",
          not any(d.startswith("DVD0") and d != "DVD01(C1)"
                  for d in os.listdir(export_dir)))
    check(f"{mode}: the result says three were removed, none kept",
          result["stale_removed"] == 3 and result["stale_kept"] == [])
    check(f"{mode}: the case's own recovered files are untouched",
          all(os.path.isfile(os.path.join(case, f["out_path"])) for f in files))
    write_manifest(case, files)

# A moved export: the batch file is the only copy, and must survive.
moved_case, moved_files = make_case("moved_case", 2)
moved_manifest = write_manifest(moved_case, moved_files)
moved_dir = os.path.join(ROOT, "moved_export")
c4all_export.export(moved_manifest, moved_dir, case="M1", mode="move",
                    media_bytes=700)
write_manifest(moved_case, moved_files[:0] + [moved_files[0]])
result = c4all_export.export(moved_manifest, moved_dir, case="M1", mode="move",
                             media_bytes=700)
second = os.path.join(moved_dir, "DVD02(M1)", "01", "00000002.JPG")
check("move: a moved file the re-export no longer lists is kept",
      os.path.isfile(second))
check("move: and the result names it as left in place, removing nothing",
      "DVD02(M1)/01/00000002.JPG" in result["stale_kept"]
      and result["stale_removed"] == 0)

# A folder this program did not export into is not touched.
foreign = os.path.join(ROOT, "foreign")
os.makedirs(os.path.join(foreign, "DVD02(C1)", "01"))
with open(os.path.join(foreign, "DVD02(C1)", "01", "00000009.JPG"), "wb") as fh:
    fh.write(b"someone else's")
os.link(os.path.join(foreign, "DVD02(C1)", "01", "00000009.JPG"),
        os.path.join(foreign, "other_name.jpg"))
c4all_export.export(manifest, foreign, case="C1", mode="copy", media_bytes=700)
check("a folder with no export of ours keeps its files",
      os.path.isfile(os.path.join(foreign, "DVD02(C1)", "01", "00000009.JPG")))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
