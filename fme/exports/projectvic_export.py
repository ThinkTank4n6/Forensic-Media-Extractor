"""
projectvic_export.py -- write a Project VIC (VICS 2.0) JSON file for a
media_core run, for import into Griffeye and other Project VIC tooling.

The C4All XML export gets results into Griffeye through the C4All path;
this produces the interchange format itself, matching the US 2.0 data model:

    {
      "@odata.context": ".../DataModels/2.0.xml/US/$metadata#Cases",
      "value": [ { case fields..., "Media": [ { per-file records } ] } ]
    }

Schema and conventions were taken from a real Project VIC US 2.0 file:
timestamps as ISO 8601 with a UTC offset and 0001-01-01T00:00:00+00:00 for
unknown, MimeType as "Image" or "Video", MediaSize in bytes, Windows-style
backslashes in path fields, CRLF line endings, ASCII-escaped output.

Nothing here categorises anything. Category is written as null and
IsPrecategorized as false on every record: this tool recovers and hashes
files, it does not grade them. Categorization stays with the examiner in
Griffeye, which is where the decision belongs.

    python3 projectvic_export.py -m out/manifest.json -o case.json \\
        -c DC23-282 --org "Anytown PD" --contact "J. Smith"
"""

from __future__ import annotations

# Allow this file to be run directly as well as imported. Run as a script
# there is no package context, so the project root goes on the path first.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import sys
import threading
import uuid
from typing import Optional
from fme.sqlite_uri import ro_uri
from fme.engines import timeutil

__version__ = "1.0.0"

ODATA_CONTEXT = ("http://github.com/VICSDATAMODEL/ProjectVic/DataModels/"
                 "2.0.xml/US/$metadata#Cases")

# Project VIC writes this when a timestamp is not known, rather than omitting
# the field or using a zero epoch.
UNKNOWN_TIME = "0001-01-01T00:00:00+00:00"

MIME_BY_CATEGORY = {"image": "Image", "video": "Video"}


# Times written as UNKNOWN_TIME because the evidence recorded no zone for
# them; reset and read by export(), which reports the count. Per thread: the
# interface builds reports concurrently, one thread each, and a shared count
# was reset by one export while another was halfway through counting.
_ZONELESS = threading.local()


def vic_time(iso: str) -> str:
    """ISO 8601 with an explicit offset, or the Project VIC unknown value.

    A time recorded with no zone -- a FAT file time, a camera's capture time
    without an offset -- was given +00:00 here, so a reader of the export
    placed a camera's 14:00 at 14:00 UTC: 10:00 in New York. VICS has no way
    to write a time whose zone is unknown, so it is written as unknown, and
    counted. The time itself is in the case's manifest and its reports,
    shown as the device recorded it.
    """
    if not iso:
        return UNKNOWN_TIME

    when = timeutil.aware(iso)
    if when is None:
        if timeutil.parse(iso) is not None:
            _ZONELESS.count = getattr(_ZONELESS, "count", 0) + 1
        return UNKNOWN_TIME
    return when.isoformat()


def windows_path(path: str) -> str:
    return (path or "").replace("/", "\\")


def basename(path: str) -> str:
    """os.path.basename only splits on the host separator, so a Windows-style
    path handed to it on Linux comes back whole. Split on both."""
    return (path or "").replace("\\", "/").rstrip("/").rpartition("/")[2]


def join_evidence(evidence: str, tail: str) -> str:
    """Join without doubling separators: source paths from the file system
    engine are absolute within the volume and start with a separator."""
    parts = [p.strip("\\/") for p in (evidence, tail) if p and p.strip("\\/")]
    return "\\".join(parts)


def media_file(rec: dict, evidence: str) -> dict:
    """One place a file was found: a MediaFiles entry."""
    source = rec.get("source_path") or ""
    file_name = basename(source) if source else basename(rec.get("out_path", ""))
    if source:
        file_path = join_evidence(evidence, windows_path(source))
    else:
        offset = rec.get("offset")
        located = f"Unallocated Clusters (offset {offset})" if offset is not None \
            else "Unallocated Clusters"
        file_path = join_evidence(evidence, located)
    return {
        "MD5": (rec.get("md5") or "").lower(),
        "FileName": file_name,
        "FilePath": file_path,
        "Created": vic_time(timeutil.stored(rec, "crtime")),
        "Written": vic_time(timeutil.stored(rec, "mtime")),
        "Accessed": vic_time(timeutil.stored(rec, "atime")),
        "SourceID": "",
        "Deleted": bool(rec.get("deleted")),
    }


def media_record(rec: dict, media_id: int, evidence: str,
                 relative_root: str, rel_override: str = "",
                 places: Optional[list] = None) -> dict:
    """One Media entry: one distinct file, with every place it was found.

    `places` are the other records of the same file -- the manifest's
    duplicates -- and each becomes a MediaFiles entry under this one Media.
    Field order follows the reference file so a diff against a known-good
    export stays readable."""
    rel = rel_override or windows_path(
        os.path.join(relative_root, rec.get("out_path", ""))
        if relative_root else rec.get("out_path", ""))

    return {
        "MD5": (rec.get("md5") or "").lower(),
        "MediaID": media_id,
        # Null unless an examiner categorized the file in this case. The tool
        # still classifies nothing itself; this carries a person's decision
        # back out in the format Project VIC defines for it.
        "Category": rec.get("vic_category"),
        "SHA1": (rec.get("sha1") or "").lower() or None,
        "VictimIdentified": False,
        "OffenderIdentified": False,
        "IsDistributed": False,
        "MediaSize": int(rec.get("size") or 0),
        "RelativeFilePath": rel,
        "IsPrecategorized": rec.get("vic_category") is not None,
        "PrecategorizationSource": (rec.get("vic_source", "examiner")
                                    if rec.get("vic_category") is not None
                                    else ""),
        "MimeType": MIME_BY_CATEGORY.get(rec.get("category"), "Other"),
        # Present for schema parity; PhotoDNA hashing is licensed and not
        # something this pipeline computes, so the list stays empty.
        "AlternativeHashes": [],
        "MediaFiles": [media_file(r, evidence) for r in [rec] + list(places or [])],
    }


def group_records(records: list) -> list:
    """The records of each distinct file together, in first-seen order.

    By SHA-256, which is what the manifest deduplicates on. Every duplicate
    used to become a Media entry of its own: one picture found in a camera
    folder, a copy and unallocated space was three Media with one MD5, and
    TotalMediaFiles, and the export's image count, counted places rather
    than files -- a different number from the case's own report. VICS models
    a file found in several places as one Media with several MediaFiles.
    A record with no hash at all cannot be matched to another and stays
    on its own.
    """
    groups, order = {}, []
    for index, rec in enumerate(records):
        key = (rec.get("sha256") or "").lower() or (rec.get("md5") or "").lower() \
            or f"#unhashed-{index}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rec)
    return [groups[key] for key in order]


def build(manifest: dict, *, case_number: str, organization: str = "",
          contact_name: str = "", contact_phone: str = "",
          contact_email: str = "", contact_title: str = "",
          evidence: str = "", relative_root: str = "",
          source_app: str = "", source_version: str = "",
          case_id: str = "", media_id: str = "sequential",
          rel_paths: Optional[dict] = None) -> dict:
    records = manifest.get("files", [])
    rel_paths = rel_paths or {}
    media = [
        media_record(group[0], 0 if media_id == "zero" else i, evidence,
                     relative_root, rel_paths.get(group[0].get("out_path", ""), ""),
                     places=group[1:])
        for i, group in enumerate(group_records(records), start=1)
    ]
    return {
        "@odata.context": ODATA_CONTEXT,
        "value": [{
            "CaseID": case_id or str(uuid.uuid4()),
            "CaseNumber": case_number,
            "ContactOrganization": organization,
            "ContactName": contact_name,
            "ContactPhone": contact_phone,
            "ContactEmail": contact_email,
            "ContactTitle": contact_title,
            # Distinct files: the Media entries, as the case's own report
            # counts them. The places each was found are its MediaFiles.
            "TotalMediaFiles": len(media),
            # Counted from the records. It was written as 0 "by
            # construction" -- true until examiner categories were exported,
            # after which the header said none were precategorized over
            # records that said they were.
            "TotalPrecategorized": sum(1 for entry in media
                                       if entry["IsPrecategorized"]),
            # Griffeye shows these, and a defence expert can ask what
            # produced the set. The default used to be this module's own
            # __version__ -- "1.0.0", a number this program has never
            # been released under.
            "SourceApplicationName": source_app or _tool_name(),
            "SourceApplicationVersion": source_version or _tool_version(),
            "Media": media,
        }],
    }


def _provenance() -> dict:
    try:
        from fme.engines.media_core import tool_provenance

        return tool_provenance()
    except Exception:  # noqa: BLE001 - an export is not lost over this
        return {}


def _tool_name() -> str:
    return _provenance().get("tool") or "Forensic Media Extractor"


def _tool_version() -> str:
    """The build, and the fingerprint that identifies it exactly."""
    where = _provenance()
    version = where.get("tool_version") or __version__
    mark = (where.get("code_fingerprint") or "")[:12]
    return f"{version} ({mark})" if mark else version


def stage_hash_layout(manifest: dict, manifest_path: str, out_path: str) -> dict:
    """Reproduce the layout Cellebrite ships: a sibling folder named
    <json stem>Files holding one file per record, named by its MD5 with no
    extension. Returns out_path -> RelativeFilePath for each staged file.

    Files are hard-linked where the filesystem allows it, so staging a large
    case costs no extra disk.
    """
    source_dir = os.path.dirname(os.path.abspath(manifest_path))
    stem = os.path.splitext(os.path.basename(out_path))[0]
    folder = os.path.join(os.path.dirname(os.path.abspath(out_path)), stem + "Files")
    os.makedirs(folder, exist_ok=True)

    mapping = {}
    held: dict = {}     # staged name -> the SHA-256 of what it holds
    for rec in manifest.get("files", []):
        md5 = (rec.get("md5") or "").lower()
        if not md5:
            continue
        src = os.path.join(source_dir, rec.get("out_path", ""))
        if not os.path.isfile(src):
            continue
        sha = (rec.get("sha256") or "").lower()
        # Named by MD5, as Cellebrite does -- but an existing file of that
        # name was reused unexamined, so two files sharing an MD5 (collision
        # pairs are easy to make) both pointed at the first one's bytes and
        # the second Media entry's hashes described a file it did not have.
        # A name already holding other bytes gets the SHA-256 added to it.
        name = md5
        if _staged_sha(os.path.join(folder, name), held, name) not in ("", sha):
            name = f"{md5}_{sha[:16] or 'x'}"
        dest = os.path.join(folder, name)
        if not os.path.exists(dest):
            try:
                os.link(src, dest)
            except (OSError, NotImplementedError):
                shutil.copy2(src, dest)
            held[name] = sha
        mapping[rec["out_path"]] = f"{stem}Files\\{name}"
    return mapping


def _staged_sha(path: str, held: dict, name: str) -> str:
    """The SHA-256 of what a staged name holds ("" when nothing does)."""
    if name in held:
        return held[name]
    if not os.path.exists(path):
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    held[name] = digest.hexdigest()
    return held[name]


def export(manifest_path: str, out_path: str, files_layout: str = "inplace",
           **kwargs) -> dict:
    """Write the VICS JSON and return a small summary.

    files_layout="hash" also stages the recovered files next to the JSON the
    way Cellebrite does, so the export is self-contained.

    manifest=, when given, replaces reading `manifest_path` (which still
    locates the case folder and case.db): a scoped report passes only the
    records in its scope, so the exchange file does not hand Griffeye files
    the report beside it left out.
    """
    manifest = kwargs.pop("manifest", None)
    if manifest is None:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    else:
        # The records are annotated below (vic_category); copies keep that
        # from reaching the caller's rows.
        manifest = dict(manifest,
                        files=[dict(r) for r in manifest.get("files", [])],
                        duplicates=[dict(r) for r in manifest.get("duplicates", [])])

    # Categories an examiner assigned live in case.db beside the manifest.
    # Until now this always wrote null: the tool had nowhere to read a
    # decision from. Where a review has happened, the export should carry it.
    decisions = {}
    if kwargs.pop("use_categories", True):
        case_path = os.path.join(os.path.dirname(os.path.abspath(manifest_path)),
                                 "case.db")
        if os.path.isfile(case_path):
            try:
                import sqlite3
                con = sqlite3.connect(ro_uri(case_path), uri=True)
                from fme.review import casedb
                decisions = {}
                sources = {}
                omitted = 0
                # The case's own scheme: only the US one is VICS's, and
                # another region's maps no more than its Non-Pertinent.
                row = con.execute("SELECT value FROM meta WHERE "
                                  "key='category_region'").fetchone()
                from fme.review import regions
                scheme = regions.get(row[0] if row else "US")
                for sha256, code, by in con.execute(
                        "SELECT sha256, category, categorized_by FROM decisions"):
                    if code == scheme.uncategorized:
                        continue
                    mapped = scheme.vic_out.get(code)
                    if mapped is None:
                        # A local category VICS does not define. Exporting a
                        # number it does not know, or the nearest one that
                        # looks similar, would misstate the finding.
                        omitted += 1
                        continue
                    decisions[sha256] = mapped
                    # A category a hash set asserted is not an examiner's
                    # decision, and VICS has a field that says which it was.
                    sources[sha256] = ((by or "")[len("hash set: "):]
                                       if (by or "").startswith("hash set: ")
                                       else "examiner")
                if omitted:
                    print(f"{omitted} file(s) carry a category Project VIC does "
                          f"not define; exported with no category rather than a "
                          f"guess. Their grading is in case.db and in this "
                          f"tool's own reports.")
                con.close()
            except sqlite3.Error as exc:
                # Refused, not written without them: an export with every
                # Category null, because the case was locked for a moment,
                # went to other agencies with nothing said (v2026.11.317).
                raise RuntimeError(
                    f"The case's gradings could not be read ({exc}), so no "
                    f"Project VIC file was written. Try again; if it persists, "
                    f"export with categories turned off to send hashes only."
                ) from exc

    if kwargs.pop("include_duplicates", True) and manifest.get("duplicates"):
        manifest = dict(manifest,
                        files=list(manifest["files"]) + manifest["duplicates"])
    if decisions:
        for record in manifest["files"]:
            code = decisions.get(record.get("sha256"))
            if code is not None:        # 0 is Non-Pertinent, a real decision
                record["vic_category"] = code
                record["vic_source"] = sources.get(record.get("sha256"), "examiner")

    if not manifest.get("files"):
        raise ValueError("The manifest contains no recovered files to export.")

    if files_layout == "hash":
        kwargs["rel_paths"] = stage_hash_layout(manifest, manifest_path, out_path)

    _ZONELESS.count = 0
    doc = build(manifest, **kwargs)
    text = json.dumps(doc, indent=2, ensure_ascii=True)
    # The reference file is CRLF throughout; match it rather than leaving
    # mixed endings for whatever reads this next.
    with open(out_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(text)

    case = doc["value"][0]
    counts = {"Image": 0, "Video": 0, "Other": 0}
    for entry in case["Media"]:
        counts[entry["MimeType"]] = counts.get(entry["MimeType"], 0) + 1
    return {
        "output": os.path.abspath(out_path),
        "case_id": case["CaseID"],
        "case_number": case["CaseNumber"],
        "total": case["TotalMediaFiles"],
        "places": sum(len(entry["MediaFiles"]) for entry in case["Media"]),
        "precategorized": case["TotalPrecategorized"],
        "images": counts.get("Image", 0),
        "videos": counts.get("Video", 0),
        "other": counts.get("Other", 0),
        "bytes": os.path.getsize(out_path),
        "files_layout": files_layout,
        # Times VICS could not carry: recorded with no zone, written as the
        # format's "unknown" rather than with a zone the evidence did not give.
        "times_without_zone": _ZONELESS.count,
    }


def validate(path: str) -> dict:
    """Say whether a file is a Project VIC file a tool like Griffeye will
    accept, and if not, what it actually is.

    Written because the obvious mistake is to hand Griffeye the run's
    manifest.json -- which is this tool's internal working record, not an
    interchange format. Griffeye's message for that is "Could not determine
    format of 0 data", which does not point anywhere useful.
    """
    result = {"path": os.path.abspath(path), "is_vics": False,
              "problems": [], "records": 0, "identified_as": "unknown"}
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError as exc:
        result["problems"].append(str(exc))
        return result

    text = head.decode("utf-8-sig", "replace")
    if '"@odata.context"' in text and "ProjectVic" in text:
        result["is_vics"] = True
        result["identified_as"] = "Project VIC (VICS)"
    elif '"summary"' in text and '"out_path"' in text:
        result["identified_as"] = "media_core manifest.json"
        result["problems"].append(
            "This is the extraction's own manifest, not a Project VIC file. "
            "It records what was recovered and where it came from, for your "
            "own use. Load ProjectVIC.json instead -- write one with the "
            "Export panel, with 'media_core.py --vic CASE', or with "
            "projectvic_export.py.")
        return result
    else:
        result["problems"].append(
            "No @odata.context naming the Project VIC data model. Griffeye "
            "identifies a VICS file by that, and reports 'could not determine "
            "format' when it is absent.")

    try:
        doc = json.loads(open(path, "r", encoding="utf-8-sig").read())
    except (OSError, json.JSONDecodeError) as exc:
        result["problems"].append(f"Not valid JSON: {exc}")
        return result

    value = doc.get("value")
    if not isinstance(value, list) or not value:
        result["problems"].append("No 'value' array holding the case block.")
        return result
    case = value[0]
    for field in ("CaseNumber", "TotalMediaFiles", "Media"):
        if field not in case:
            result["problems"].append(f"Case block is missing {field}.")
    media = case.get("Media") or []
    result["records"] = len(media)
    # Consistency a reader will check by counting. Warnings rather than
    # problems: another tool's export may count differently and still load.
    # This program's own export wrote both of these wrong before
    # v2026.11.283.
    result["warnings"] = []
    if isinstance(case.get("TotalMediaFiles"), int) \
            and case["TotalMediaFiles"] != len(media):
        result["warnings"].append(
            f"TotalMediaFiles says {case['TotalMediaFiles']}; the Media array "
            f"holds {len(media)}.")
    marked = sum(1 for entry in media if entry.get("IsPrecategorized"))
    if isinstance(case.get("TotalPrecategorized"), int) \
            and case["TotalPrecategorized"] != marked:
        result["warnings"].append(
            f"TotalPrecategorized says {case['TotalPrecategorized']}; "
            f"{marked} Media record(s) are marked IsPrecategorized.")
    seen, repeated = set(), 0
    for entry in media:
        md5 = (entry.get("MD5") or "").lower()
        if md5 and md5 in seen:
            repeated += 1
        seen.add(md5)
    if repeated:
        result["warnings"].append(
            f"{repeated} Media record(s) repeat an MD5 already in the file: one "
            f"file found in several places is counted more than once. VICS "
            f"gives such a file one Media record with several MediaFiles.")
    if not media:
        result["problems"].append("The Media array is empty; there is nothing "
                                  "for Griffeye to show.")
    for i, entry in enumerate(media[:200]):
        if not entry.get("MD5"):
            result["problems"].append(f"Media[{i}] has no MD5.")
            break
        if not entry.get("RelativeFilePath"):
            result["problems"].append(
                f"Media[{i}] has no RelativeFilePath, so the file cannot be "
                f"located next to the JSON.")
            break
    base = os.path.dirname(os.path.abspath(path))
    sample = media[:500]
    missing = sum(1 for entry in sample
                  if entry.get("RelativeFilePath")
                  and not os.path.isfile(os.path.join(
                      base, entry["RelativeFilePath"].replace("\\", os.sep))))
    if missing == len(sample) and sample:
        # Every file absent, categories filled in: this is a hash set, not a
        # case export. Perfectly valid, just not a thing that shows pictures.
        precategorized = sum(1 for e in sample if e.get("IsPrecategorized"))
        if precategorized > len(sample) // 2:
            result["identified_as"] = "Project VIC hash set (no media alongside)"
            result["note"] = ("A hash set: hashes and categories with no files. "
                              "Griffeye imports it as a set to match against, "
                              "not as images to display. To flag your own "
                              "recovered files against it, use "
                              "vic_import.py --match.")
        else:
            result["problems"].append(
                "Every record points at a file that is not on disk beside the "
                "JSON. If this is meant to be a case export, the media folder "
                "has to travel with it.")
    elif missing:
        result["problems"].append(
            f"{missing} of the first {len(sample)} records point at files that "
            f"are not on disk beside the JSON. Griffeye will load the records "
            f"but show no images for those.")
    result["is_vics"] = result["is_vics"] and not result["problems"]
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write a Project VIC (VICS 2.0) JSON file from a "
                    "media_core manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("-m", "--manifest", help="manifest.json from a run")
    ap.add_argument("-o", "--out", help="output .json path")
    ap.add_argument("-c", "--case", help="case number")
    ap.add_argument("--validate", metavar="FILE",
                    help="check whether a .json file is one Griffeye will "
                         "accept, and say what it is if not")
    ap.add_argument("--org", default="", help="contact organization")
    ap.add_argument("--contact", default="", help="contact name")
    ap.add_argument("--phone", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--evidence", default="",
                    help="evidence item name, used to root FilePath")
    ap.add_argument("--relative-root", default="",
                    help="prefix for RelativeFilePath, if the JSON will not sit "
                         "beside the recovered files")
    ap.add_argument("--media-id", choices=("sequential", "zero"),
                    default="sequential",
                    help="MediaID numbering. sequential is unique per record; "
                         "zero matches what Cellebrite writes, if a tool turns "
                         "out to expect that")
    ap.add_argument("--files-layout", choices=("inplace", "hash"),
                    default="inplace",
                    help="inplace points RelativeFilePath at the existing "
                         "output tree; hash stages copies next to the JSON in "
                         "a <name>Files folder named by MD5, as Cellebrite does")
    ap.add_argument("--source-app", default="media_core")
    ap.add_argument("--source-version", default="")
    args = ap.parse_args(argv)

    if args.validate:
        report = validate(args.validate)
        print(f"File      : {report['path']}")
        print(f"Looks like: {report['identified_as']}")
        print(f"Records   : {report['records']}")
        if report.get("note"):
            print(f"Note      : {report['note']}")
        for warning in report.get("warnings") or []:
            print(f"Warning   : {warning}")
        if report["is_vics"] and not report["problems"]:
            print("Verdict   : valid Project VIC file")
            return 0
        print("Verdict   : NOT loadable as Project VIC")
        for problem in report["problems"]:
            print(f"  - {problem}")
        return 1

    if not (args.manifest and args.out and args.case):
        ap.error("-m, -o and -c are all required unless --validate is used")

    try:
        result = export(args.manifest, args.out, case_number=args.case,
                        organization=args.org, contact_name=args.contact,
                        contact_phone=args.phone, contact_email=args.email,
                        contact_title=args.title, evidence=args.evidence,
                        relative_root=args.relative_root,
                        source_app=args.source_app,
                        source_version=args.source_version,
                        media_id=args.media_id,
                        files_layout=args.files_layout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {result['output']} ({result['bytes']:,} bytes)")
    print(f"  case      : {result['case_number']}  [{result['case_id']}]")
    print(f"  media     : {result['total']} "
          f"({result['images']} image, {result['videos']} video, "
          f"{result['other']} other)")
    print(f"  layout    : {result['files_layout']}")
    print("  Category  : null on every record — grade them in Griffeye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
