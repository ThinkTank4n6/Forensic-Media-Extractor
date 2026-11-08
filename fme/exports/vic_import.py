"""
vic_import.py -- read Project VIC (VICS) JSON files.

Two jobs, because a VICS file turns up in two very different roles.

1. As an export with its media alongside it. Cellebrite and friends write
   <name>.json next to a <name>Files folder holding one file per record,
   named by MD5. This module reads the JSON, resolves every RelativeFilePath,
   verifies each file against the hash recorded for it, and writes the media
   out in the same layout media_core produces -- so a VICS export can be
   folded into the same manifest, carved output and C4All/VICS reports as
   anything else.

       python3 vic_import.py -i case.json -o ./out

2. As a hash set with no media at all. The national Project VIC sets are
   exactly this: hundreds of thousands of records, Category filled in, no
   files. Pointed at a manifest from a previous run, this matches recovered
   files against the set and reports which ones are known.

       python3 vic_import.py -i VIC_US.json --match out/manifest.json

   Matching is on MD5 and SHA-1. Nothing is classified here: the Category
   comes from the set, and files not in the set are reported as unknown, not
   as anything else. What that means for a case is the examiner's call.

The files are large -- the sets run to hundreds of megabytes -- so records
are streamed one object at a time rather than loaded whole.
"""

from __future__ import annotations

# Allow this file to be run directly -- "python fme/engines/media_core.py" --
# as well as imported. Run as a script there is no package context, so the
# project root goes on the path before the package imports below.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import csv
import hashlib
import json
import os
import sys
from typing import Iterator, Optional
from fme.exports.report import spreadsheet_safe

__version__ = "1.0.0"

CHUNK = 4 * 1024 * 1024
# The most one record may take. A VICS record is a few kilobytes; one that
# will not decode within this much is damaged, not cut off by a chunk edge.
MAX_RECORD = 4 * CHUNK


# Members of a top-level object that may hold the records. A Project VIC
# document keeps them in "value"; other distributions of the same data use
# one of the rest.
RECORD_KEYS = ('"value"', '"Media"', '"media"', '"objects"', '"data"')


def _record_array_start(buf: str, fh) -> tuple:
    """Find the array the records live in, reading more of the file as needed.

    The national download and a single case export are not the same shape.
    A case file wraps its media in a Case object, so the records sit under
    "Media"; the full set has no cases in it at all and its records sit
    directly in "value". Insisting on "Media" found nothing in the second,
    which is why a 70-million-hash import came back with no categories: the
    structured read failed and the fallback scan kept only the digests.
    """
    while True:
        found = [(buf.index(key), key) for key in RECORD_KEYS if key in buf]
        if found:
            at, _ = min(found)
            bracket = buf.find("[", at)
            if bracket >= 0:
                return buf[bracket + 1:], True
        data = fh.read(CHUNK)
        if not data:
            return buf, False
        buf += data
        if len(buf) > 64 * CHUNK:        # no array anywhere near the start
            return buf, False


def stream_media(path: str) -> Iterator[dict]:
    """Yield each media record from a VICS file without loading it all.

    A national set runs to gigabytes and tens of millions of records, so it
    is neither loaded whole nor walked character by character in Python. The
    file is read in chunks and json.JSONDecoder().raw_decode -- which is C --
    decodes one object at a time from the buffer, which is topped up whenever
    an object straddles a chunk boundary.

    Records are yielded from whichever shape the file is in:

    * ``value`` holding the records themselves (the full download);
    * ``value`` holding cases, each with a ``Media`` array (a case export);
    * a bare top-level array of records.

    A case object is recognized by having a ``Media`` array rather than by
    its other fields, so a record that happens to carry a case number is
    still read as a record.
    """
    decoder = json.JSONDecoder()
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        buf = fh.read(CHUNK)
        if not buf:
            return
        stripped = buf.lstrip()
        if stripped.startswith("["):
            buf = stripped[1:]
        else:
            buf, ok = _record_array_start(buf, fh)
            if not ok:
                return

        pos = 0
        seen = 0
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n,":
                pos += 1
            if pos < len(buf) and buf[pos] == "]":
                return
            if pos >= len(buf) or buf[pos] != "{":
                data = fh.read(CHUNK)
                if not data:
                    return
                buf = buf[pos:] + data
                pos = 0
                continue
            try:
                obj, offset = decoder.raw_decode(buf, pos)
            except ValueError as exc:
                # Cut off at a chunk boundary -- or broken. A broken record
                # was read as cut off: the rest of the file went into memory
                # and the stream ended quietly at the end of it, so one bad
                # record made a national set of tens of millions a set of a
                # few, with no error. Past MAX_RECORD, or at the end of the
                # file, it is broken, and said so.
                if len(buf) - pos > MAX_RECORD:
                    raise ValueError(
                        f"{path}: record {seen + 1:,} is not valid JSON "
                        f"({exc}); nothing after it can be read") from exc
                data = fh.read(CHUNK)
                if not data:
                    raise ValueError(
                        f"{path}: the file ends inside record {seen + 1:,} "
                        f"({exc}): it is cut off or damaged") from exc
                buf = buf[pos:] + data
                pos = 0
                continue
            pos = offset
            seen += 1

            media = obj.get("Media") if isinstance(obj, dict) else None
            if isinstance(media, list):
                # A case object: the records are inside it.
                for record in media:
                    if isinstance(record, dict):
                        yield record
            elif isinstance(obj, dict):
                yield obj


def case_header(path: str) -> dict:
    """The case block, read from the front of the file only."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        head = fh.read(8192)
    at = head.find('"Media"')
    if at > 0:
        head = head[:at].rstrip().rstrip(",")
        head += "}]}"
    try:
        doc = json.loads(head)
        return doc.get("value", [{}])[0]
    except json.JSONDecodeError:
        return {}


def _hash_file(path: str) -> tuple:
    md5, sha1, sha256, size = (hashlib.md5(), hashlib.sha1(),
                               hashlib.sha256(), 0)
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            md5.update(chunk); sha1.update(chunk); sha256.update(chunk)
            size += len(chunk)
    return md5.hexdigest(), sha1.hexdigest(), sha256.hexdigest(), size


def _mismatched(entry: dict, md5: str, sha1: str, sha256: str) -> str:
    """Which of the record's hashes the file fails, or ""."""
    got = {"md5": md5, "sha1": sha1, "sha256": sha256}
    claimed = {"md5": entry.get("MD5"), "sha1": entry.get("SHA1")}
    for alt in entry.get("AlternativeHashes") or []:
        kind = str(alt.get("HashName") or "").lower().replace("-", "")
        if kind in got:
            claimed.setdefault(kind, alt.get("HashValue"))
            claimed[kind] = claimed[kind] or alt.get("HashValue")
    wrong = [k.upper() for k, v in claimed.items()
             if v and str(v).lower() != got[k]]
    return ", ".join(wrong)


def _category_of(entry: dict):
    value = entry.get("Category")
    return value if value not in ("", None) else None


# --------------------------------------------------------------------------
# 1. Import a VICS export together with its media
# --------------------------------------------------------------------------
def import_media(vic_path: str, out_dir: str, *, files_root: str = "",
                 verify: bool = True, signatures=None) -> dict:
    """Copy every referenced file into a media_core-style output tree."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fme.engines import media_core

    base = files_root or os.path.dirname(os.path.abspath(vic_path))
    header = case_header(vic_path)
    out = media_core.OutputManager(out_dir, dedupe=True)

    stats = {"records": 0, "found": 0, "missing": 0, "hash_mismatch": 0,
             "not_media": 0}
    started = __import__("time").time()

    for entry in stream_media(vic_path):
        stats["records"] += 1
        rel = (entry.get("RelativeFilePath") or "").replace("\\", os.sep)
        if not rel:
            stats["missing"] += 1
            continue
        # Kept inside the export. The path is the JSON's, and joined bare an
        # absolute path or ..\ read any file on the examining machine into
        # the case as evidence, under whatever path and category the JSON gave.
        full = os.path.abspath(os.path.join(base, rel))
        if not _inside(os.path.abspath(base), full):
            stats["outside_export"] = stats.get("outside_export", 0) + 1
            continue
        if not os.path.isfile(full):
            stats["missing"] += 1
            continue

        with open(full, "rb") as fh:
            head = fh.read(media_core.MAX_SIG_SPAN)
        name = (entry.get("MediaFiles") or [{}])[0].get("FileName", "")
        category, ext = media_core.sniff_category(head, name, signatures)
        if category is None:
            # The record says it is media, but the bytes do not. Report it
            # rather than silently exporting something unidentifiable.
            stats["not_media"] += 1
            continue

        md5, sha1, sha256, size = _hash_file(full) if verify else ("", "", "", 0)
        # Every hash the record gives, not only its MD5: a record with only a
        # SHA-1 was never checked. A file that does not match is not the one
        # the record describes, so the record's category is not carried onto
        # it -- it was, and wrong bytes entered the case graded CAM.
        mismatch = verify and _mismatched(entry, md5, sha1, sha256)
        if mismatch:
            stats["hash_mismatch"] += 1

        # Every place the record lists, not only the first. A file found in
        # three places is one Media with three MediaFiles -- which is how
        # this program's own export writes one since v2026.11.283 -- and
        # reading only MediaFiles[0] dropped the other two. The first stores
        # the file; the rest are the same bytes, which the case records as
        # further places it was found rather than as files of their own.
        places = entry.get("MediaFiles") or [{}]
        cat = None if mismatch else _category_of(entry)
        stored_once = False
        for mf in places:
            clean = os.path.basename(rel).rsplit(".", 1)[0][:50]
            rec = out.store(
                _file_chunks(full), category=category, ext=ext or "bin",
                stem=f"recovered_{out.next_index():04d}_vic_{clean}",
                engine="projectvic",
                source_path=mf.get("FilePath") or rel,
                inode="", fs_type="", partition="",
                deleted=bool(mf.get("Deleted")),
                signature=f"vic:category={cat if cat is not None else 'none'}",
                mtime=_vic_time(mf.get("Written")),
                atime=_vic_time(mf.get("Accessed")),
                crtime=_vic_time(mf.get("Created")),
                note=(f"imported from Project VIC"
                      + (f", category {cat}" if cat is not None else "")
                      + (f"; HASH MISMATCH ({mismatch}): this is not the file "
                         f"the record describes, and its category "
                         f"({_category_of(entry)}) was not applied"
                         if mismatch else "")
                      + ("" if verify else "; not verified against the record")),
            )
            if rec:
                stored_once = True
        if stored_once:
            stats["found"] += 1

    summary = {
        "image": os.path.abspath(vic_path),
        "image_size": os.path.getsize(vic_path),
        "image_size_human": media_core.human(os.path.getsize(vic_path)),
        "mode": "projectvic",
        "started_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "elapsed_seconds": round(__import__("time").time() - started, 1),
        "tool": f"vic_import.py {__version__}",
        "engine": media_core._engine_description(),
        "case_number": header.get("CaseNumber", ""),
        "source_application": header.get("SourceApplicationName", ""),
        "duplicates_suppressed": out.dupes,
        "canceled": False,
        "workers": 1,
        **stats,
        "total_files": len(out.records),
        "output_dir": out.outdir,
    }
    out.close(summary)
    return summary


def _file_chunks(path: str) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while True:
            data = fh.read(CHUNK)
            if not data:
                return
            yield data


def _vic_time(value: Optional[str]) -> str:
    if not value or value.startswith("0001-01-01"):
        return ""
    return value


# --------------------------------------------------------------------------
# 2. Use a VICS file as a hash set
# --------------------------------------------------------------------------
def load_hash_set(vic_path: str) -> dict:
    """Build {hash: category} for MD5 and SHA-1, lowercase."""
    from fme.review.casedb import severity_rank

    lookup = {}

    def keep(value, cat):
        # A hash listed twice keeps the more severe category, and an entry
        # with none never erases one: the last entry used to win, so an
        # uncategorized duplicate wiped out a category 1.
        if value not in lookup or lookup[value] is None:
            lookup[value] = cat
        elif isinstance(cat, int) and isinstance(lookup[value], int) \
                and severity_rank(cat) < severity_rank(lookup[value]):
            lookup[value] = cat

    for entry in stream_media(vic_path):
        cat = _category_of(entry)
        for key in ("MD5", "SHA1"):
            value = (entry.get(key) or "").lower()
            if value:
                keep(value, cat)
        for alt in entry.get("AlternativeHashes") or []:
            value = (alt.get("HashValue") or "").lower()
            if value:
                keep(value, cat)
    return lookup


def match_manifest(vic_path: str, manifest_path: str,
                   csv_path: str = "") -> dict:
    """Flag which recovered files appear in a Project VIC set.

    The set decides the category; this only reports what it says. A file that
    is not in the set is unknown -- which is not the same as benign, and the
    output says so rather than implying otherwise.
    """
    lookup = load_hash_set(vic_path)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    rows, matched = [], 0
    for rec in manifest.get("files", []):
        hit, known = None, False
        for key in ("md5", "sha1", "sha256"):
            value = (rec.get(key) or "").lower()
            if value and value in lookup:
                # In the set whatever its category; a SHA-256 match from
                # AlternativeHashes on an uncategorized record was "no".
                known = True
                if hit is None:
                    hit = lookup[value]
        if known:
            matched += 1
        rows.append({
            "out_path": rec.get("out_path", ""),
            "source_path": rec.get("source_path", ""),
            "size": rec.get("size", 0),
            "md5": rec.get("md5", ""),
            "sha1": rec.get("sha1", ""),
            "in_project_vic": "yes" if known else "no",
            "category": "" if hit is None else hit,
        })

    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows
                                    else ["out_path"])
            writer.writeheader()
            # Paths come from the evidence; one beginning with = is a
            # formula when this list is opened in Excel.
            writer.writerows({key: spreadsheet_safe(value)
                              for key, value in row.items()} for row in rows)

    return {"hashes_in_set": len(lookup), "files_checked": len(rows),
            "matched": matched, "unmatched": len(rows) - matched,
            "csv": os.path.abspath(csv_path) if csv_path else "",
            "rows": rows}


def _inside(root: str, full: str) -> bool:
    """Is `full` inside the folder `root`? Both absolute.

    os.path.commonpath raises ValueError for paths on two Windows drives, so
    a record whose RelativeFilePath was "D:\\x.jpg" in an export on C: ended
    the whole import with a traceback instead of being counted as outside
    the export like any other path that leaves it. (The same guard as
    fme.web.request_guard.contained, which exports/ does not import.)
    """
    try:
        return (os.path.normcase(os.path.commonpath([root, full]))
                == os.path.normcase(root))
    except ValueError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read Project VIC JSON: import its media, or use it as a "
                    "hash set to flag known files.")
    ap.add_argument("-i", "--input", required=True, help="VICS .json file")
    ap.add_argument("-o", "--output", help="output directory (import mode)")
    ap.add_argument("--files-root", default="",
                    help="where the referenced files live, if not beside the "
                         "JSON")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip re-hashing each file against its record")
    ap.add_argument("--match", metavar="MANIFEST",
                    help="hash-set mode: flag which files in this manifest.json "
                         "appear in the set")
    ap.add_argument("--csv", default="", help="write match results here")
    args = ap.parse_args(argv)

    if args.match:
        result = match_manifest(args.input, args.match, args.csv)
        print(f"Hash set: {result['hashes_in_set']:,} hash(es)")
        print(f"Checked : {result['files_checked']} recovered file(s)")
        print(f"  in the set  : {result['matched']}")
        print(f"  not in it   : {result['unmatched']}  (unknown, which is not "
              f"the same as cleared)")
        by_cat = {}
        for row in result["rows"]:
            if row["in_project_vic"] == "yes":
                by_cat[row["category"]] = by_cat.get(row["category"], 0) + 1
        for cat, count in sorted(by_cat.items(), key=lambda x: str(x[0])):
            print(f"    category {cat if cat != '' else '(none recorded)'}: {count}")
        if result["csv"]:
            print(f"Wrote {result['csv']}")
        return 0

    if not args.output:
        ap.error("-o/--output is required unless --match is used")
    summary = import_media(args.input, args.output,
                           files_root=args.files_root,
                           verify=not args.no_verify)
    print(f"Records in the file : {summary['records']}")
    print(f"  media exported    : {summary['found']}")
    print(f"  file not found    : {summary['missing']}")
    print(f"  not a media file  : {summary['not_media']}")
    print(f"  hash mismatches   : {summary['hash_mismatch']}")
    print(f"  duplicates merged : {summary['duplicates_suppressed']}")
    print(f"Output: {summary['output_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
