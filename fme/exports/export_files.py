"""
export_files.py -- handing over the files themselves.

A report describes what was found. Sometimes what is wanted is the files:
a folder of the category 1 material for a prosecutor, an evidence disc for
the court, a set to send to another unit. That is a different thing from a
report and should not have to be done through one.

Three things this does that copying by hand does not:

**It verifies every copy.** Each file is hashed as it is written and the
result compared with the hash recorded at carve time. A copy that does not
match is reported rather than quietly handed over, and the run says so.

**It writes a manifest beside the files**: name, category, size, MD5,
SHA-1, SHA-256 and where the file came from. A folder of pictures with no
record of what they are is not evidence anybody can work with.

**It never overwrites.** An export into a folder that already holds files
stops before writing anything, and within an export every file and every
thumbnail is written to a name it has claimed by creating it exclusively,
so no two can land on one name whatever the rows hold.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
from fme.exports.report import spreadsheet_safe

__version__ = "1.1.0"

# What Windows actually refuses in a folder name is < > : " / \ | ? *, so
# everything else is kept. Replacing more than that turned "Child Abuse
# Material (CAM)" into "Child Abuse Material _CAM_", which is harder to
# read and harder to match against the report beside it.
SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
CHUNK = 1024 * 1024


def _folder_name(name: str) -> str:
    """A category name as a folder name, readable and legal on Windows."""
    cleaned = SAFE.sub("_", name).strip(" .") or "category"
    return cleaned[:60]


def _claim(folder: str, name: str, digest: str = ""):
    """Create a new file in `folder` for `name`, never one that exists.

    Returns (path, open binary handle). The name is claimed by creating it
    exclusively ("xb"), so the check and the write cannot be split.

    This used to test os.path.exists once, fall back to
    "<stem>_<first 8 of SHA-256>", and then open that with "wb" without
    looking: a third file of the same name and hash prefix -- identical
    copies, or rows with no recorded hash, where the suffix is empty --
    replaced the second. Three rows went into exported_files.csv, each
    "hash_verified", over a folder that held two files.
    """
    stem, extension = os.path.splitext(name)
    tag = (digest or "")[:8]
    attempt = 0
    while True:
        if attempt == 0:
            candidate = name
        elif tag:
            candidate = f"{stem}_{tag}{'' if attempt == 1 else f'_{attempt}'}{extension}"
        else:
            candidate = f"{stem}_{attempt + 1}{extension}"
        path = os.path.join(folder, candidate)
        try:
            return path, open(path, "xb")
        except FileExistsError:
            attempt += 1


def _hash_copy(source: str, writing) -> dict:
    """Copy a file into an open handle, hashing it on the way, so the copy
    can be checked."""
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    size = 0
    with open(source, "rb") as reading, writing:
        while True:
            chunk = reading.read(CHUNK)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
            writing.write(chunk)
            size += len(chunk)
    return {"md5": md5.hexdigest(), "sha1": sha1.hexdigest(),
            "sha256": sha256.hexdigest(), "size": size}


KIND_FOLDERS = {"image": "Images", "video": "Videos", "audio": "Audio"}


def _kind_of(row) -> str:
    """Images, Videos, Audio or Other -- the split a submission asks for."""
    kind = (row.get("kind") or row.get("category") or "").lower()
    if kind in KIND_FOLDERS:
        return KIND_FOLDERS[kind]
    extension = str(row.get("ext", "")).lower()
    if extension in VIDEO_EXTENSIONS:
        return "Videos"
    if extension in AUDIO_EXTENSIONS:
        return "Audio"
    if extension in IMAGE_EXTENSIONS:
        return "Images"
    return "Other"


VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v", "3gp", "wmv",
                    "flv", "mpg", "mpeg", "ts", "m2ts", "mts", "ogv", "rm",
                    "asf", "dv", "mxf", "swf"}
AUDIO_EXTENSIONS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "wma",
                    "aiff", "caf", "amr", "silk", "mid"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp",
                    "heic", "heif", "avif", "jp2", "jxl", "ico", "psd", "dng",
                    "cr2", "cr3", "nef", "arw", "qoi", "pcx", "dds"}


def export(rows: list, out_dir: str, dest_dir: str, *, thumbnails=None,
           by_category: bool = True, by_kind: bool = True,
           progress=None, cancel=None) -> dict:
    """Copy these files into a folder, by category, and verify each one.

    `rows` are the rows a report would cover, so the same scope controls
    both: whatever categories were chosen are what gets exported.
    """
    dest_dir = os.path.abspath(dest_dir)
    if os.path.isdir(dest_dir) and os.listdir(dest_dir):
        raise ValueError(
            f"{dest_dir} is not empty. Choose a folder that does not exist "
            f"yet, or an empty one: an export that writes into an existing "
            f"set of files cannot be told apart from it afterwards.")
    os.makedirs(dest_dir, exist_ok=True)

    written, failed, mismatched, unverified = [], [], [], []
    for index, row in enumerate(rows, start=1):
        if cancel is not None and cancel():
            break
        source = os.path.join(out_dir, row.get("out_path", ""))
        if not os.path.isfile(source):
            failed.append({"name": row.get("name", ""),
                           "why": "the recovered file is no longer there"})
            continue

        # Images and videos in separate folders, which is how a submission
        # to NCMEC is asked for, and which makes the counts checkable: a
        # folder of 703 files should be the 703 the report says.
        folder = dest_dir
        if by_category or by_kind:
            parts = []
            if by_category:
                parts.append(row.get("category_name", "") or "Uncategorized")
            if by_kind:
                parts.append(_kind_of(row))
            folder = os.path.join(dest_dir, _folder_name(" ".join(parts)))
            os.makedirs(folder, exist_ok=True)

        name = os.path.basename(row.get("out_path", "")) or "file"
        recorded = (row.get("sha256") or "").lower()
        # Two recovered files can share a name; neither may overwrite the
        # other.
        try:
            target, writing = _claim(folder, name, recorded)
        except OSError as exc:
            failed.append({"name": name, "why": str(exc)})
            continue
        try:
            copied = _hash_copy(source, writing)
        except OSError as exc:
            # A copy that failed part way is not left behind looking like
            # one of the exported files.
            try:
                os.unlink(target)
            except OSError:
                pass
            failed.append({"name": name, "why": str(exc)})
            continue

        if not recorded:
            # Nothing to check the copy against. This was written down as
            # verified -- hash_verified was "no recorded hash, or a match"
            # -- and the page said every copy matched.
            unverified.append({"name": name,
                               "exported_as": os.path.relpath(target, dest_dir)})
        elif copied["sha256"] != recorded:
            # The copy is not the file. Said plainly, and kept, so somebody
            # can look at what was actually written.
            mismatched.append({"name": name, "expected": recorded,
                               "got": copied["sha256"]})

        written.append({
            "exported_as": os.path.relpath(target, dest_dir),
            "name": name,
            "category": row.get("category_name", ""),
            "flags": row.get("flag_names", ""),
            "size": copied["size"],
            "md5": copied["md5"], "sha1": copied["sha1"],
            "sha256": copied["sha256"],
            # True or False against the hash recorded at carve time; empty
            # where none was recorded, which is not the same as verified.
            "hash_verified": (copied["sha256"] == recorded) if recorded else "",
            "source_path": row.get("source_path", ""),
            "recovered_by": row.get("engine", ""),
            "deleted": row.get("deleted", False),
        })

        written[-1]["thumbnail"] = ""
        if thumbnails is not None:
            picture = thumbnails(row)
            if picture:
                # One _thumbnails folder serves every category folder, and a
                # thumbnail was named by its file's stem alone: two files of
                # one name in two categories left one thumbnail, showing
                # whichever came last. Claimed like the files, and named in
                # the manifest so each thumbnail is tied to its file.
                thumb_dir = os.path.join(dest_dir, "_thumbnails")
                os.makedirs(thumb_dir, exist_ok=True)
                stem = os.path.splitext(os.path.basename(target))[0]
                thumb, fh = _claim(thumb_dir, f"{stem}.jpg", recorded)
                with fh:
                    fh.write(picture)
                written[-1]["thumbnail"] = os.path.relpath(thumb, dest_dir)

        if progress and index % 25 == 0:
            progress(index, len(rows))

    manifest = os.path.join(dest_dir, "exported_files.csv")
    fields = ["exported_as", "name", "category", "flags", "size", "md5",
              "sha1", "sha256", "hash_verified", "source_path",
              "recovered_by", "deleted", "thumbnail"]
    with open(manifest, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        # Names and paths come from the evidence; one beginning with = is a
        # formula when this list is opened in Excel.
        writer.writerows({key: spreadsheet_safe(value)
                          for key, value in row.items()} for row in written)

    if progress:
        progress(len(rows), len(rows))
    folders = sorted({os.path.dirname(row["exported_as"])
                      for row in written if os.path.dirname(row["exported_as"])})
    return {"folder": dest_dir, "written": len(written),
            "failed": failed, "mismatched": mismatched,
            "unverified": unverified,
            "manifest": manifest,
            "folders": folders,
            "categories": sorted({row["category"] for row in written})}
