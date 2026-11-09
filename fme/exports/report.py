"""
report.py -- the report an examiner hands over.

A contact sheet is one output among several, and which one is wanted depends
on who is reading it. This writes a report as a set of files chosen at export
time: an HTML document to read, a CSV to work with, JSON for another tool,
a KML of anywhere the media was taken, an MD5 list, and the Project VIC
exchange file.

Three decisions in here are about the subject matter rather than the code.

**Thumbnails are optional, and off is a first-class choice.** A report about
indecent images frequently has to go to someone who must not be shown them --
counsel, a court filing, a defense expert under terms. `thumbnails=False`
writes a report with no picture of any file anywhere in it, and the header
says so, so a reader can tell the difference between a report without images
and a report whose images failed to load.

**Blurring is available and is not a safeguard.** It makes a contact sheet
reviewable over a shoulder without exposing the contents. The underlying
image is still in the file, so a blurred report is not safe to disclose; the
header says that too.

**Scope is recorded in the report.** A report covering one category of a run
must say so on its face, or a reader will take it for the whole run.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as _dt
import html
import json
import os
import shutil
import sys
import threading

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

from fme.exports import report_html
from fme.exports import report_maps

__version__ = "1.0.0"

# Fields that hold a time, so they are converted rather than printed raw.
TIME_FIELDS = {"mtime", "crtime", "atime", "ctime", "captured", "taken"}

class _PerBuild:
    """A dict-like holder whose contents belong to the calling thread.

    The zone a report shows times in is read by every cell formatter, far
    from build(). It was a plain module dict, and the interface builds each
    report in its own thread, so two examiners' reports built at once could
    each print times in the other's zone -- under a header naming its own.
    Every report is built from start to finish on one thread (the thumbnail
    pool formats no times), so per-thread is per-build.
    """

    def __init__(self, **defaults):
        self._defaults = defaults
        self._local = threading.local()

    def _values(self) -> dict:
        values = getattr(self._local, "values", None)
        if values is None:
            values = self._local.values = dict(self._defaults)
        return values

    def __getitem__(self, key):
        return self._values()[key]

    def __setitem__(self, key, value):
        self._values()[key] = value

    def get(self, key, default=None):
        return self._values().get(key, default)


# The zone the current report is being written in. Set once per report so
# every row agrees with the header.
_SHOWN_IN = _PerBuild(zone="")

FIELDS = [
    ("name", "File name"),
    ("device_path", "Path on the device"),
    ("packed_by", "Extraction packed by"),
    ("also_seen_at", "Also seen at"),
    ("source_path", "Path"),
    ("category_name", "Category"),
    ("flag_names", "Flags"),
    ("note", "Note"),
    ("size", "Size"),
    ("ext", "Type"),
    ("captured", "Captured (EXIF)"),
    ("camera", "Camera"),
    ("artist", "Camera author"),
    ("body_serial", "Camera serial"),
    ("place", "Place"),
    ("coordinates", "Coordinates"),
    ("mtime", "Modified"),
    ("crtime", "Created"),
    ("atime", "Accessed"),
    ("deleted", "Deleted"),
    ("engine", "Recovered by"),
    ("offset", "Byte offset"),
    ("md5", "MD5"),
    ("sha1", "SHA-1"),
    ("sha256", "SHA-256"),
    ("instances", "Copies"),
    ("hash_sets", "Hash sets"),
]
DEFAULT_FIELDS = ["name", "source_path", "category_name", "flag_names", "note",
                  "size", "captured", "camera", "place", "mtime", "deleted",
                  "engine", "md5", "sha256"]

# Counted and reported like any other category, but listed after the rest:
# they say what was not a finding, and a reader is looking for what was.
TRAILING_CATEGORIES = {0, 8}

VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v",
                    "3gp", "wmv", "flv", "mpg", "mpeg", "ts",
                    "m2ts", "mts", "ogv", "rm", "asf", "dv",
                    "mxf", "swf"}

MAX_FULL_COPY = 256 * 1024 * 1024

# Above this many files, thumbnails are written as files beside the report
# and the contact sheet is split into pages. Embedding every picture in one
# document is fine for a hundred files and catastrophic for a hundred
# thousand: a run of that size produced a single multi-gigabyte page that no
# browser would open, after minutes of apparently doing nothing.
EMBED_LIMIT = 400
PAGE_SIZE = 500

# The same bands the Filters pane uses, so a report and the interface
# describe a run the same way.
SIZE_BANDS = [
    (0, 100 * 1024, "under 100 KB"),
    (100 * 1024, 1024 * 1024, "100 KB – 1 MB"),
    (1024 * 1024, 10 * 1024 * 1024, "1 – 10 MB"),
    (10 * 1024 * 1024, 100 * 1024 * 1024, "10 – 100 MB"),
    (100 * 1024 * 1024, 1024 * 1024 * 1024, "100 MB – 1 GB"),
    (1024 * 1024 * 1024, float("inf"), "over 1 GB"),
]


def _generated(zone_name: str = "") -> str:
    """When the report was written, in the zone its times are shown in.

    It was the examining machine's local clock with no zone, printed one
    line above "Times shown in <zone>" -- so a report shown in Eastern,
    written on a machine set to UTC, gave a generation time five hours off
    under a header saying every time was Eastern. Through timeutil like
    every other time in the report, with the zone written beside it.
    """
    from fme.engines import timeutil

    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    return timeutil.show(now.isoformat(), zone_name or "")


def _generated_utc() -> str:
    """The same moment for a machine reader: ISO 8601 with its offset."""
    return _dt.datetime.now(_dt.timezone.utc).replace(
        microsecond=0).isoformat()


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _describe_zone(name: str) -> str:
    """What the header says about the times below it."""
    from fme.engines import timeutil

    _SHOWN_IN["zone"] = name or ""
    # Said in the header because it is true of every time below it: the
    # zone applies only to times the evidence recorded a zone for.
    return (f"{timeutil.describe(name or 'UTC')}. Times the evidence recorded "
            f"with no zone -- a FAT volume's file times, a camera's capture "
            f"time without an offset -- are shown as the device recorded "
            f"them, not converted, and marked ({timeutil.AS_RECORDED})")


def _size(value) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return str(value)


def gather(out_dir: str, *, categories=None, flags=None, sha256=None,
           include_uncategorized: bool = True) -> dict:
    """The records a report should cover, with their review attached.

    Scope is applied here rather than in each writer, so every format in one
    export describes the same set of files -- a CSV that disagrees with the
    HTML beside it is worse than either alone.
    """
    from fme.review import casedb

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    records = manifest.get("files", [])

    decisions, category_names, flag_names = {}, {}, {}
    category_colors = {}
    uncategorized = casedb.UNCATEGORIZED
    from fme.review import regions
    scheme = regions.get("US")
    case_path = os.path.join(out_dir, "case.db")
    if os.path.isfile(case_path):
        case = casedb.CaseDB(case_path)
        uncategorized = case.uncategorized
        scheme = case.scheme
        decisions = case.decisions()
        category_names = {c["code"]: c["name"] for c in case.categories()}
        category_colors = {c["name"]: c["color"] for c in case.categories()}
        flag_names = {f["id"]: f["name"] for f in case.flags()}
        case.close()

    wanted_categories = set(categories) if categories else None
    wanted_flags = set(flags) if flags else None
    wanted_files = set(sha256) if sha256 else None

    hash_matches = _hash_matches(out_dir)
    rows = []
    for record in records:
        digest = record.get("sha256", "")
        decided = decisions.get(digest, {})
        code = decided.get("category", uncategorized)
        carried = decided.get("flags", [])

        if wanted_files is not None and digest not in wanted_files:
            continue
        if wanted_categories is not None and code not in wanted_categories:
            continue
        if wanted_flags is not None and not (set(carried) & wanted_flags):
            continue
        if not include_uncategorized and code == uncategorized:
            continue

        row = dict(record)
        # The manifest records a path; a report wants the file's name.
        row["name"] = os.path.basename(record.get("out_path", ""))
        # What kind of media it is, kept before the grading overwrites the
        # field. They are different questions -- "is this a video" and "what
        # did the examiner decide" -- and sharing one name for both lost the
        # first, which is how every video came to have no length.
        row["kind"] = record.get("category", "")
        row["category"] = code
        row["category_name"] = category_names.get(code, str(code))
        row["flags"] = carried
        row["flag_names"] = ", ".join(flag_names.get(i, str(i)) for i in carried)
        row["note"] = decided.get("note", "")
        row["camera"] = " ".join(x for x in (record.get("camera_make", ""),
                                             record.get("camera_model", "")) if x)
        row["place"] = ", ".join(x for x in (record.get("city", ""),
                                             record.get("state", ""),
                                             record.get("country", "")) if x)
        row["coordinates"] = ("" if record.get("latitude") is None
                              else f"{record['latitude']}, {record['longitude']}")
        row["hash_sets"] = _hash_set_text(hash_matches, digest)
        rows.append(row)

    return {"rows": rows, "manifest": manifest, "out_dir": out_dir,
            "coverage_limits": coverage_limits(manifest),
            "decryption": decryption_notes(manifest),
            "evidence": evidence_notes(manifest, out_dir),
            "category_colors": category_colors,
            "total_in_run": len(records),
            "category_names": category_names, "flag_names": flag_names,
            "uncategorized": uncategorized,
            # The case's own scheme (fme/review/regions.py): which grades
            # are the findings, which are not, and the order the unit uses.
            "region": scheme.label, "notable": set(scheme.notable_codes),
            "trailing": {c for c in (scheme.nonpertinent, scheme.uncategorized)
                         if c is not None},
            "order": {p.code: p.position for p in scheme.presets}}


def scoped_manifest(data: dict) -> dict:
    """The run's manifest cut down to the files this report covers.

    The MD5 list, the KML and the Project VIC file used to read the whole
    manifest.json, so a report scoped to one category came with a hash list,
    a map and an exchange file of every file in the run. Scope is decided per
    file (by its SHA-256) in gather(); the records kept here are the
    manifest's own, not the report rows, whose "category" holds the grading
    rather than the kind of media. A duplicate goes with the file it copies.
    """
    manifest = data.get("manifest") or {}
    wanted = {row.get("sha256") for row in data.get("rows", [])}
    return dict(manifest,
                files=[r for r in manifest.get("files", [])
                       if r.get("sha256") in wanted],
                duplicates=[r for r in manifest.get("duplicates", [])
                            if r.get("sha256") in wanted])


def _hash_matches(out_dir: str) -> dict:
    """What the case recorded the hash sets saying, by SHA-256.

    Read once per gather() and held there. It was a module-level cache, and
    two reports built at once (the interface runs each in its own thread)
    reset and filled the same one: a report of case A could carry case B's
    hash-set matches, or none.
    """
    case_path = os.path.join(out_dir, "case.db")
    if not os.path.isfile(case_path):
        return {}
    try:
        from fme.review import casedb
        case = casedb.CaseDB(case_path)
        try:
            return case.derived("hash_matches") or {}
        finally:
            case.close()
    except Exception:  # noqa: BLE001 - a report should still write
        return {}


def _hash_set_text(matches_by_digest: dict, digest: str) -> str:
    """What the hash sets said about a file, for a report column.

    Read from the case rather than recomputed: the match was made when the
    run was checked against the store, and a report should say what was
    found then rather than what a different store would say today.
    """
    matches = matches_by_digest.get(digest) or []
    out = []
    for match in matches:
        record = match.get("record") or {}
        bits = [match.get("set", "")]
        for key in ("MediaID", "Category", "Series", "Tags"):
            if record.get(key) not in (None, ""):
                bits.append(f"{key} {record[key]}")
        out.append(" · ".join(str(b) for b in bits if b))
    return "; ".join(out)


def decryption_notes(manifest: dict) -> list:
    """Which encrypted volumes the run opened, and with what kind of key.

    The files from such a volume were read through a decryption, with a key
    the examiner supplied; a reader of the report is entitled to know that,
    and which key -- by kind, and a key file by name and SHA-256. The secret
    itself is never in the case to be reported (fme/engines/unlock.py).
    """
    from fme.engines import unlock

    summary = (manifest or {}).get("summary") or {}
    return [unlock.opened_line(entry) for entry in summary.get("unlocked_volumes") or []]


def _refusal(volume: dict, summary: dict) -> str:
    """What the keys given made of a volume that stayed locked, if any were."""
    for entry in summary.get("unlock_refused") or []:
        same_place = (entry.get("offset") or 0) == (volume.get("offset") or 0)
        same_kind = (entry.get("kind") or "").lower() == (volume.get("kind") or "").lower()
        same_volume = (entry.get("volume") or "") == (volume.get("volume") or "")
        if same_place and same_kind and same_volume:
            text = " The passwords or key files given for this run did not open it."
            if entry.get("hint"):
                text += (f' Its passphrase hint, as the volume stores it: '
                         f'"{entry["hint"]}".')
            return text
    return ""


_ALGO_NAMES = {"md5": "MD5", "sha1": "SHA-1", "sha256": "SHA-256",
               "sha512": "SHA-512", "blake2b": "BLAKE2b"}


def _digests_text(digests: dict) -> str:
    return ", ".join(f"{_ALGO_NAMES.get(a, a.upper())} {v}"
                     for a, v in sorted((digests or {}).items()))


def evidence_notes(manifest: dict, out_dir: str = "") -> list:
    """What ties this case to its evidence, as sentences a report carries.

    The acquisition's own hash, the digests this run took of the source and
    whether they agree, and the digest of manifest.json written beside it.
    A case from before v2026.11.313 has none of these and says so.
    """
    summary = (manifest or {}).get("summary") or {}
    evidence = summary.get("evidence")
    lines = []
    if not isinstance(evidence, dict):
        if summary.get("image"):
            lines.append("This case was made by a release that did not hash "
                         "the source; nothing in it ties it to a specific "
                         "acquisition.")
    else:
        if evidence.get("stored"):
            lines.append("The acquisition recorded: "
                         + _digests_text(evidence["stored"]) + ".")
        if evidence.get("computed"):
            lines.append("This run read the source as: "
                         + _digests_text(evidence["computed"]) + ".")
        if evidence.get("note"):
            note = str(evidence["note"])
            lines.append(note[:1].upper() + note[1:] + ".")
        for member in evidence.get("members") or []:
            name = os.path.basename(member.get("path", ""))
            # evidence_record's shape since v2026.11.318; the bare digests of
            # hash_source before it.
            digests = member.get("computed") or {
                a: member[a] for a in ("md5", "sha1", "sha256") if member.get(a)}
            if digests:
                lines.append(f"RAID member {name} was read as: "
                             + _digests_text(digests)
                             + (f"; {member['note']}" if member.get("note") else "")
                             + ".")
            else:
                lines.append(f"RAID member {name} was not hashed: "
                             f"{member.get('error') or member.get('note') or 'the read stopped early'}.")
    if out_dir:
        from fme.engines import evidence_hash
        path = os.path.join(out_dir, evidence_hash.MANIFEST_DIGEST)
        try:
            with open(path, encoding="utf-8") as fh:
                digest = fh.read().split()[0]
        except (OSError, IndexError):
            digest = ""
        if digest:
            same = evidence_hash.check_manifest_digest(out_dir)
            lines.append(f"manifest.json, the run's record, has SHA-256 "
                         f"{digest}" + (" and is unchanged since the run "
                                        "wrote it." if same else
                                        "; IT HAS CHANGED since the run "
                                        "wrote it."))
    return lines


def coverage_limits(manifest: dict) -> list:
    """What the run could not read, as sentences a report must carry.

    The run records a locked partition (encrypted_volumes, since v239) and a
    volume it could not mount (unreadable_volume) in its summary, and the
    report used to be built from the file list alone. A BitLocker laptop
    came out as "All 8 file(s) recovered in this run", eight files from the
    small system partition, with nothing to say the main partition was
    never read -- in the document that goes in the case file, to people who
    never saw the console or the program's warnings.
    """
    summary = (manifest or {}).get("summary") or {}
    limits = []
    locked = summary.get("encrypted_volumes") or []
    if not locked and summary.get("encrypted"):
        locked = [{"kind": summary["encrypted"], "offset": 0}]
    for volume in locked:
        kind = volume.get("kind") or "encrypted"
        where = ("The image" if not volume.get("offset") else
                 f"Partition {volume.get('partition') or '?'} "
                 f"(at {_size(volume.get('offset'))} into the image)")
        if volume.get("volume"):
            # An APFS container is not encrypted; a volume inside it is.
            inside = "the image" if where == "The image" else where[0].lower() + where[1:]
            limits.append(
                f"The {kind} volume {volume['volume']} in {inside} is encrypted "
                f"(FileVault) and was locked when this run was made. Its blocks "
                f"are ciphertext, so none of its files could be read, and none "
                f"of what it holds is in this report. That is not the same as it "
                f"being empty." + _refusal(volume, summary))
            continue
        line = (f"{where} is {kind}-encrypted and was locked when this run "
                f"was made. Nothing on it could be read, so none of what it "
                f"holds is in this report. That is not the same as it "
                f"being empty.")
        if volume.get("uuid"):
            line += f" Volume UUID: {volume['uuid']}."
        limits.append(line + _refusal(volume, summary))
    for item in summary.get("unopened_containers") or []:
        limits.append(
            f"Partition {item.get('partition') or '?'} (at "
            f"{_size(item.get('offset'))} into the image) is a "
            f"{item.get('kind')}. The volumes inside it were not opened, so "
            f"their files appear here only where the carve found them, "
            f"without names, folders or dates, and anything encrypted inside "
            f"was not identified.")
    evidence = summary.get("evidence") if isinstance(
        summary.get("evidence"), dict) else {}
    if evidence.get("verified") is False:
        limits.append("The source this run read does not match the hash its "
                      "acquisition recorded (" + _digests_text(
                          evidence.get("stored")) + "). Either the evidence "
                      "changed after it was acquired or it was read "
                      "differently; nothing in this report should be relied "
                      "on until that is explained.")
    for volume in summary.get("vendor_not_walked") or []:
        where = volume.get("name") or f"at offset {volume.get('base')}"
        limits.append(f"Volume {where} ({volume.get('kind') or 'unknown'}) was "
                      f"not read: {volume.get('note') or 'the reader gave no reason'}. "
                      f"Its files appear here only where the carve found them, "
                      f"without names, folders or dates.")
    search = summary.get("raid_search") or {}
    for line in (search.get("warnings") or []) + [f"two copies of a member disagree "
                                                    f"and neither was used: {a}"
                                                    for a in search.get("ambiguous") or []]:
        limits.append(f"RAID members: {line}.")
    if search.get("missing"):
        limits.append(f"RAID members named by the array but not found beside the "
                      f"image: {'; '.join(search['missing'])}. What only they held "
                      f"is not in this report unless parity rebuilt it.")
    for array in summary.get("raid_arrays") or []:
        gaps = [line.strip() for line in array.get("lines") or []
                if "missing" in line or "rebuilt" in line]
        if not array.get("read"):
            limits.append(f"RAID array {array.get('label')} could not be assembled "
                          f"from the members given: {'; '.join(array.get('lines') or [])}.")
        elif gaps:
            limits.append(f"RAID array {array.get('label')}: {'; '.join(gaps)}.")
    for array in summary.get("raid_carved") or []:
        # The array's own gaps, in the array's own offsets: merged into the
        # image's list they read as stretches of the source (v2026.11.317).
        if array.get("unreadable_bytes"):
            ranges = array.get("unreadable_ranges") or []
            limits.append(
                f"{_size(array['unreadable_bytes'])} of the assembled RAID array "
                f"{array.get('label')} could not be read and was not carved "
                f"({len(ranges)} stretch(es), offsets in the array"
                + (f", the first at {ranges[0][0]:,}" if ranges else "") + ").")
        if array.get("carve_incomplete"):
            limits.append(f"The carve of the assembled RAID array {array.get('label')} "
                          f"did not finish: {array['carve_incomplete']}")
    if summary.get("raid_members"):
        limits.append(f"{len(summary['raid_members'])} other member image(s) were "
                      f"read with this one to assemble its RAID set "
                      f"({', '.join(os.path.basename(m['path']) for m in summary['raid_members'])}); "
                      f"they are hashed with the source when Hash the source is on, "
                      f"and their arrays are carved as well as walked.")
    if summary.get("logical_md5_mismatch"):
        limits.append(
            f"{summary['logical_md5_mismatch']:,} file(s) read from the logical "
            f"acquisition do not match the MD5 the acquisition tool recorded "
            f"for them; each one's own row says so. Their content here is what "
            f"this program read, not what was acquired.")
    if summary.get("read_short"):
        limits.append(
            f"{summary['read_short']:,} file(s) could be read only in part -- a "
            f"damaged entry in a package, or a read error from the source -- and "
            f"are stored as far as they read, each marked short with the reason.")
    if summary.get("logical_short"):
        limits.append(
            f"{summary['logical_short']:,} file(s) from the logical acquisition "
            f"could be read only in part; each is stored as far as it was read, "
            f"marked short, and its recorded MD5 could not be checked.")
    if summary.get("logical_unread"):
        limits.append(
            f"{summary['logical_unread']:,} file(s) the logical acquisition "
            f"lists could not be read from it and are not in this report.")
    if summary.get("vendor_unread_files"):
        examples = summary.get("vendor_unread_examples") or []
        shown = "; ".join(f"{e.get('path')} ({e.get('why', '').split(': ', 1)[-1]})"
                          for e in examples[:3])
        limits.append(
            f"{summary['vendor_unread_files']:,} file(s) the file system names "
            f"could not be read and are not in this report, though some may be "
            f"pictures or videos -- for example {shown}. A file stored "
            f"compressed is not found by the carve either; the full list is in "
            f"manifest.json under vendor_unread_examples.")
    for volume in summary.get("vendor_walked") or []:
        if not volume.get("files") and volume.get("note"):
            limits.append(f"Volume {volume.get('name')} ({volume.get('kind')}) "
                          f"was read and listed nothing: {volume['note']}.")
    if summary.get("carve_incomplete"):
        limits.append(f"The carve did not cover the whole image: "
                      f"{summary['carve_incomplete']}. Files in the rest of "
                      f"the image are not in this report.")
    if summary.get("unreadable_bytes"):
        ranges = summary.get("unreadable_ranges") or []
        shown = ", ".join(f"{_size(start)} (+{_size(length)})"
                          for start, length in ranges[:5])
        count = summary.get("unreadable_range_count") or len(ranges)
        more = f", and {count - 5:,} more" if count > 5 else ""
        limits.append(
            f"{_size(summary['unreadable_bytes'])} of the source could not be "
            f"read, in {count:,} stretch(es): {shown}{more}. Nothing "
            f"stored in those stretches is in this report: a device read "
            f"directly gives zeros there, any other source gives nothing. A "
            f"failing disk reads the same way every time; imaging it with a "
            f"tool that retries and maps bad sectors may recover more.")
    if summary.get("carved_short"):
        limits.append(
            f"{summary['carved_short']} carved file(s) run into a stretch the "
            f"source could not read, and are stored only up to it. Each is "
            f"marked bounded \"short\" in the manifest, and its note gives the "
            f"offset where reading stopped and how much of the judged length "
            f"was stored. A partial file can still be opened in part; its "
            f"digest is of the part stored, not of the original file.")
    if summary.get("short_reads"):
        limits.append(
            f"{summary['short_reads']} file(s) found through a file system "
            f"could not be read to the size the file system records, and are "
            f"stored only as far as they could be read. Each is marked "
            f"bounded \"short\" in the manifest, and its note gives the "
            f"recorded size, the size stored and why reading stopped. Its "
            f"digest is of the part stored, not of the original file.")
    if summary.get("zero_filled_files"):
        limits.append(
            f"{summary['zero_filled_files']} file(s) were read across sectors "
            f"the device could not read, which it returned as zeros "
            f"({_size(summary.get('zero_filled_bytes') or 0)} in all). Each is "
            f"marked bounded \"short\" and its note says ZERO-FILLED and gives "
            f"the source offsets; its digest is not that of the original file.")
    # Things the run passed over and counted. Each used to be silent; a
    # report that leaves them out says less was missed than was.
    passed_over = [
        ("fs_dirs_unreadable", "folder(s) the file system walk could not open "
                               "(their contents were not walked)"),
        ("fs_dirs_too_deep", "folder(s) nested too deep for the walk to "
                             "enter (their files are reached only by the "
                             "carve, without names)"),
        ("fs_unreadable_files", "file(s) whose first bytes could not be read"),
        ("deleted_name_reused", "deleted name(s) whose file record now belongs "
                                "to another file (not stored, so another "
                                "file's content is not shown under them)"),
        ("mft_not_read", "deleted MFT record(s) whose data could not be read "
                         "or was refused (encrypted, or compressed in a way "
                         "that cannot be decoded)"),
        ("read_errors", "read error(s) during the carve, passed over as "
                        "unreadable stretches"),
        ("judge_errors", "carve candidate(s) the length judge failed on, "
                         "measured by this program's own parser instead"),
        ("zip_unopened", "file(s) beginning like a zip that would not open "
                         "as one, scanned as bytes instead"),
        ("zip_damaged", "zip archive(s) that failed part way through"),
        ("stream_plan_failed", "streaming manifest(s) that could not be read"),
        ("stream_write_failed", "segmented stream(s) that could not be joined"),
        ("thumbcache_stopped", "thumbnail cache(s) whose walk stopped before "
                               "the end"),
        ("csv_rows_failed", "manifest.csv line(s) that could not be written "
                            "(the records are in manifest.db and manifest.json)"),
    ]
    shown_over = [f"{summary[key]:,} {what}" for key, what in passed_over
                  if summary.get(key)]
    if shown_over:
        limits.append("The run also counted: " + "; ".join(shown_over) + ".")
    orphans = summary.get("orphaned_files") or []
    if orphans:
        limits.append(
            f"{len(orphans)} file(s) in the case folder are not in its manifest: "
            f"an earlier run stopped between storing them and recording them, "
            f"so where they came from was lost. They are listed in the run's "
            f"summary as orphaned_files and are not in this report.")
    names = {"filesystem": "the walk of the image's file systems",
             "other-filesystems": "the reader for file systems the main "
                                  "engine cannot read (QNX, F2FS, flash)",
             "mft": "the deleted-NTFS-record pass",
             "fat-deleted": "the deleted-FAT-entry pass"}
    for phase in summary.get("failed_phases") or []:
        if phase in names:
            limits.append(f"{names[phase][0].upper()}{names[phase][1:]} stopped "
                          f"with an error, so what it would have found may be "
                          f"missing from this report. The run's log has the "
                          f"error; --resume retries it.")
    unreadable = summary.get("unreadable_volume")
    if unreadable:
        limits.append(
            f"A {str(unreadable).upper()} volume on the image could not be "
            f"read by this program, so none of what it holds is in this "
            f"report. That is not the same as it being empty."
            + (f" The reader says: {'; '.join(summary['unreadable_volume_detail'])}."
               if summary.get("unreadable_volume_detail") else ""))
    return limits


def _coverage_block(data: dict) -> str:
    """How encrypted volumes were read, and the limits above, before anything
    else on the page."""
    out = ""
    notes = data.get("decryption") or []
    if notes:
        items = "".join(f"<li>{_esc(line)}</li>" for line in notes)
        out += ('<section class="coverage"><h2>Encrypted volumes opened</h2>'
                f'<ul>{items}</ul></section>')
    evidence = data.get("evidence") or []
    if evidence:
        items = "".join(f"<li>{_esc(line)}</li>" for line in evidence)
        out += ('<section class="coverage"><h2>The evidence</h2>'
                f'<ul>{items}</ul></section>')
    limits = data.get("coverage_limits") or []
    if limits:
        items = "".join(f"<li>{_esc(line)}</li>" for line in limits)
        out += ('<section class="coverage"><h2>What this report does not '
                f'cover</h2><ul>{items}</ul></section>')
    return out


def _scope_sentence(scope: dict, data: dict) -> str:
    """What this report covers, in a sentence, for the header."""
    shown, total = len(data["rows"]), data["total_in_run"]
    caveat = (" Part of the image could not be read: see \u201cWhat this "
              "report does not cover\u201d." if data.get("coverage_limits") else "")
    if total == 0:
        return f"No files were recovered in this run.{caveat}"
    if shown == total:
        return f"All {total:,} file(s) recovered in this run.{caveat}"
    parts = []
    if scope.get("categories"):
        names = [data["category_names"].get(c, str(c)) for c in scope["categories"]]
        parts.append("categories: " + ", ".join(names))
    if scope.get("flags"):
        names = [data["flag_names"].get(f, str(f)) for f in scope["flags"]]
        parts.append("flags: " + ", ".join(names))
    if scope.get("sha256"):
        parts.append("a selection made in the gallery")
    if not scope.get("include_uncategorized", True):
        parts.append("categorized files only")
    detail = "; ".join(parts) if parts else "a subset"
    return (f"{shown:,} of {total:,} file(s) recovered in this run "
            f"({detail}). This report does not cover the rest.{caveat}")



# --------------------------------------------------------------------------
# Charts. Drawn as SVG by hand: a report has to open on any machine years
# from now, including one with no network and no software but a browser, so
# it carries no script and no library.
# --------------------------------------------------------------------------
PALETTE = ["#2f6fd8", "#dc2626", "#ea580c", "#d97706", "#16a34a", "#7c3aed",
           "#0891b2", "#be123c", "#4d7c0f", "#9333ea", "#64748b"]


def _donut(counts: dict, colors: dict = None, size: int = 168) -> str:
    """A ring showing how a run divides up, with the total in the middle."""
    total = sum(counts.values())
    if not total:
        return ""
    radius, width = size / 2 - 16, 22
    circumference = 2 * 3.141592653589793 * radius
    offset = 0.0
    segments = []
    for index, (label, count) in enumerate(
            sorted(counts.items(), key=lambda kv: -kv[1])):
        if not count:
            continue
        portion = count / total
        color = (colors or {}).get(label) or PALETTE[index % len(PALETTE)]
        segments.append(
            f'<circle class="ring" cx="{size/2}" cy="{size/2}" r="{radius}" '
            f'fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-dasharray="{portion * circumference:.3f} '
            f'{circumference:.3f}" '
            f'stroke-dashoffset="{-offset * circumference:.3f}" '
            f'transform="rotate(-90 {size/2} {size/2})">'
            f'<title>{_esc(label)}: {count:,} ({portion * 100:.1f}%)</title>'
            f'</circle>')
        offset += portion
    return (f'<svg class="donut" viewBox="0 0 {size} {size}" '
            f'role="img" aria-label="breakdown">{"".join(segments)}'
            f'<text x="{size/2}" y="{size/2 - 2}" text-anchor="middle" '
            f'class="donut-n">{total:,}</text>'
            f'<text x="{size/2}" y="{size/2 + 12}" text-anchor="middle" '
            f'class="donut-lbl">files</text></svg>')


def _legend(counts: dict, colors: dict = None) -> str:
    total = sum(counts.values()) or 1
    rows = []
    for index, (label, count) in enumerate(
            sorted(counts.items(), key=lambda kv: -kv[1])):
        color = (colors or {}).get(label) or PALETTE[index % len(PALETTE)]
        rows.append(
            f'<tr><td class="lbl"><span class="sw" style="background:{color}">'
            f'</span>{_esc(label)}</td><td class="n">{count:,}</td>'
            f'<td class="pct">{count / total * 100:.1f}%</td></tr>')
    return f'<table class="legend">{"".join(rows)}</table>'


def _bars(counts: dict, *, order=None, color: str = "#2f6fd8",
          height: int = 120) -> str:
    """A bar chart, ordered by value or by a given order."""
    if not counts:
        return ""
    items = ([(k, counts.get(k, 0)) for k in order if k in counts] if order
             else sorted(counts.items(), key=lambda kv: -kv[1])[:14])
    items = [(k, v) for k, v in items if v]
    if not items:
        return ""
    top = max(v for _, v in items)
    width = max(120, len(items) * 52)
    gap = 10
    bar = max(14, (width - gap * len(items)) / len(items))
    parts = []
    for index, (label, count) in enumerate(items):
        # Room for the value above the bar and the label beneath it: using
        # the full height clips the number on the tallest one.
        tall = (count / top) * (height - 42)
        x = index * (bar + gap)
        parts.append(
            f'<rect x="{x:.1f}" y="{height - 18 - tall:.1f}" width="{bar:.1f}" '
            f'height="{tall:.1f}" rx="2" fill="{color}">'
            f'<title>{_esc(label)}: {count:,}</title></rect>'
            f'<text x="{x + bar / 2:.1f}" y="{height - 6}" text-anchor="middle" '
            f'class="bl">{_esc(str(label)[:9])}</text>'
            f'<text x="{x + bar / 2:.1f}" y="{height - 22 - tall:.1f}" '
            f'text-anchor="middle" class="bn">{count:,}</text>')
    # Fixed height and a left-aligned aspect ratio: letting the SVG stretch
    # to the panel scales the labels with it, and a chart of one bar ends up
    # with text an inch tall.
    return (f'<svg class="bars" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" '
            f'preserveAspectRatio="xMinYMid meet" '
            f'role="img">{"".join(parts)}</svg>')


def _scatter(points: list, size: int = 260) -> str:
    """Where geolocated media sits, relative to the rest of it.

    Deliberately not a map: no tiles are shipped and none are fetched, so
    drawing coastlines would be inventing them. This shows the spread and
    the clustering, and the report links each file's coordinates out to a
    map of the reader's choosing.
    """
    if len(points) < 2:
        return ""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    span_lat = max(max(lats) - min(lats), 0.0005)
    span_lon = max(max(lons) - min(lons), 0.0005)
    dots = []
    for lat, lon, label in points[:4000]:
        x = (lon - min(lons)) / span_lon * (size - 20) + 10
        y = (max(lats) - lat) / span_lat * (size - 20) + 10
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" '
                    f'class="dot"><title>{_esc(label)}</title></circle>')
    return (f'<svg class="scatter" viewBox="0 0 {size} {size}" role="img">'
            f'<rect width="{size}" height="{size}" class="plot"/>'
            f'{"".join(dots)}</svg>'
            f'<div class="axis">{min(lats):.4f}–{max(lats):.4f} lat, '
            f'{min(lons):.4f}–{max(lons):.4f} lon</div>')


def _field_value(row: dict, key: str):
    """A row's value for a report column. A time comes through timeutil:
    an instant from its field, a zone-less reading from recorded_times --
    read directly, a FAT time would be blank (see timeutil.settle)."""
    from fme.engines import timeutil

    return timeutil.stored(row, key) if key in TIME_FIELDS else row.get(key, "")


def _time_shown(value) -> str:
    """A time as every human-readable format of the report shows it: an
    instant in the report's zone with its offset, a reading as recorded and
    marked. The DOCX and PDF writers printed the stored text, so one report
    gave a time in UTC in the PDF and in Eastern in the HTML beside it."""
    from fme.engines import timeutil

    return timeutil.show(value, _SHOWN_IN.get("zone", ""))


def _shown_day(row: dict, key: str) -> str:
    """The date a row's time falls on, in the zone this report shows times in.

    The summary cut the stored string to its first ten characters. A video's
    capture time is MP4's, which is UTC, so a clip taken at 9 PM on December
    31 in Philadelphia was counted on January 1 and in the next year -- while
    its own row, two pages on, said December 31 at 21:00 EST. An instant is
    converted the way _time_shown converts the row; a reading (a camera
    clock, a FAT time) stays on the day the device recorded.
    """
    from fme.engines import timeutil

    return timeutil.day_in(timeutil.stored(row, key), _SHOWN_IN.get("zone", ""))


def _highlights(data: dict) -> list:
    """The handful of things a reader should see before anything else."""
    rows = data["rows"]
    out = []

    notable = data.get("notable", {1, 2, 3})
    serious = [r for r in rows if r.get("category") in notable]
    if serious:
        out.append(("Category 1–3 material" if notable == {1, 2, 3}
                    else "Notable material (" + ", ".join(
                        str(c) for c in sorted(notable)) + ")",
                    f"{len(serious):,} file(s)",
                    "the categories a case usually turns on"))
    flagged = [r for r in rows if r.get("flag_names")]
    if flagged:
        names = {}
        for row in flagged:
            for name in row["flag_names"].split(", "):
                names[name] = names.get(name, 0) + 1
        top = ", ".join(f"{k} ({v:,})" for k, v in
                        sorted(names.items(), key=lambda kv: -kv[1])[:4])
        out.append(("Flagged", f"{len(flagged):,} file(s)", top))

    located = [r for r in rows if r.get("latitude") is not None]
    if located:
        out.append(("Carrying coordinates", f"{len(located):,} file(s)",
                    "each links out to a map in the table below"))

    cameras = {}
    for row in rows:
        if row.get("camera"):
            cameras[row["camera"]] = cameras.get(row["camera"], 0) + 1
    if cameras:
        top = ", ".join(f"{k} ({v:,})" for k, v in
                        sorted(cameras.items(), key=lambda kv: -kv[1])[:3])
        out.append(("Cameras seen", f"{len(cameras):,} distinct", top))

    serials = {r.get("body_serial") for r in rows if r.get("body_serial")}
    if serials:
        out.append(("Camera bodies by serial number", f"{len(serials):,}",
                    ", ".join(sorted(serials)[:3])))

    dates = sorted(day for day in (_shown_day(r, "captured") for r in rows)
                   if day)
    if dates:
        out.append(("Capture dates", f"{dates[0]} to {dates[-1]}",
                    f"{len(dates):,} file(s) record when they were taken"))

    deleted = [r for r in rows if r.get("deleted")]
    if deleted:
        out.append(("Deleted", f"{len(deleted):,} file(s)",
                    "recovered from entries the file system had released"))
    carved = [r for r in rows if r.get("engine") == "carve"]
    if carved:
        out.append(("Carved from unallocated space", f"{len(carved):,} file(s)",
                    "no file name or path: the file system no longer had one"))
    matched = [r for r in rows if r.get("hash_sets")]
    if matched:
        out.append(("Matched a hash set", f"{len(matched):,} file(s)",
                    "which set is named against each file"))
    return out


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def _logo(header: dict, kind: str = "PNG", box=(440, 144)):
    """The agency logo as image bytes sized for a report header, or None.

    /api/logo stores the logo as a path to a file, and the report writers
    took it only as a data: URI -- so with a logo set, no HTML, Word or PDF
    report ever showed it, while the protocol (which reads the file) did.
    Read from the file here, a data: URI still accepted, and re-encoded:
    what goes into the report is a picture of the logo at header size,
    never whatever file sits at that path. `box` is twice the size the HTML
    header draws it, so it stays sharp when printed.
    """
    logo = (header or {}).get("logo") or ""
    if not logo:
        return None
    try:
        import io as _io

        from PIL import Image

        if logo.startswith("data:image/"):
            if len(logo) > 8_000_000:
                return None
            source = _io.BytesIO(base64.b64decode(logo.split(",", 1)[1]))
            with Image.open(source) as opened:
                picture = opened.convert("RGBA")
        elif os.path.isfile(logo):
            from fme.engines import imaging

            with imaging.open_image(logo) as opened:
                picture = opened.convert("RGBA")
        else:
            return None
        picture.thumbnail(box, Image.LANCZOS)
        if kind == "JPEG":
            # JPEG has no transparency; on white, as the page it sits on.
            flat = Image.new("RGB", picture.size, (255, 255, 255))
            flat.paste(picture, mask=picture.split()[3])
            picture = flat
        buffer = _io.BytesIO()
        picture.save(buffer, kind, **({"quality": 90} if kind == "JPEG" else {}))
        return {"bytes": buffer.getvalue(), "width": picture.width,
                "height": picture.height}
    except Exception:  # noqa: BLE001 - a report without a logo still writes
        return None


def _header(header: dict, data: dict, scope: dict, *, thumbnails: bool,
            blur: bool, timezone: str) -> str:
    logo = _logo(header, "PNG")
    logo_html = ""
    if logo:
        encoded = base64.b64encode(logo["bytes"]).decode("ascii")
        logo_html = f'<img class="logo" src="data:image/png;base64,{encoded}" alt="">'

    rows = [("Agency", header.get("agency", "")),
            ("Case number", header.get("case_number", "")),
            ("Evidence item", header.get("evidence", "")),
            ("Examiner", header.get("examiner", "")),
            ("Source", data["manifest"].get("summary", {}).get("image", "")),
            ("Generated", _generated(timezone)),
            # What was actually done, not what was asked for: a zone this
            # machine does not know means the times below are UTC, and a
            # header claiming otherwise is a misleading line in a document
            # that may go to a court.
            ("Times shown in", _describe_zone(timezone)),
            ("Produced by", _built_by(data)),
                         ("Graded under", data.get("region", "")),
            ("Covers", _scope_sentence(scope, data))]
    if not thumbnails:
        rows.append(("Images", "Not included. This report holds no thumbnail, "
                               "full-size copy or video frame of any file — "
                               "only the record of each one."))
    elif blur:
        rows.append(("Images", "Thumbnails are blurred for review. The "
                               "underlying image is still in this file, so "
                               "this report is not safe to disclose."))
    table = "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
                    for k, v in rows if v)
    notes = (f'<div class="notes">{_esc(header.get("notes", ""))}</div>'
             if header.get("notes") else "")
    return (f'<header>{logo_html}<div><h1>'
            f'{_esc(header.get("title") or "Recovered media report")}</h1>'
            f'<table class="meta">{table}</table>{notes}</div></header>')



def durations(out_dir: str, rows: list, progress=None) -> dict:
    """How long each video runs, by SHA-256.

    The manifest does not record it -- a carved clip's length is not in its
    header, it is found by decoding -- so it is measured here, once, and
    kept with the case. A clip that cannot be decoded has no length rather
    than a length of zero: a report that counts unreadable files as
    zero-second videos is quietly wrong about the total.
    """
    from fme.engines import video_frames

    wanted = [r for r in rows if r.get("kind") == "video"]
    if not wanted:
        return {}

    known = {}
    case_path = os.path.join(out_dir, "case.db")
    case = None
    if os.path.isfile(case_path):
        try:
            from fme.review import casedb
            case = casedb.CaseDB(case_path)
            known = case.derived("durations") or {}
        except Exception:  # noqa: BLE001 - measuring again is only slower
            known = {}

    found = dict(known)
    measured = 0
    if video_frames.available():
        # One batch, opened in a child process: opening a video is FFmpeg
        # parsing it, and a carved clip can crash that. Measured here, in the
        # program's own process, one bad clip ended the report and the session.
        todo, seen = [], set()
        for row in wanted:
            digest = row.get("sha256", "")
            if digest and digest not in found and digest not in seen:
                seen.add(digest)
                todo.append((digest, os.path.join(out_dir, row.get("out_path", ""))))
        tick = (lambda done, total: progress(done, total)
                if progress and done % 10 == 0 else None)
        details = video_frames.probe_many([path for _, path in todo], progress=tick)
        for digest, path in todo:
            try:
                seconds = float((details.get(path) or {}).get("duration") or 0)
            except (TypeError, ValueError):
                seconds = 0
            found[digest] = round(seconds, 2) if seconds > 0 else None
            measured += 1

    if case is not None:
        try:
            if measured:
                case.save_derived("durations", found)
            case.close()
        except Exception:  # noqa: BLE001
            pass
    return found


def _built_by(data: dict) -> str:
    """Which build produced the case, for a report's header.

    Taken from the manifest the run wrote rather than from the program
    writing the report: those can differ, and the one that matters is
    the build that did the work.
    """
    summary = (data or {}).get("summary") or {}
    if not summary:
        summary = ((data or {}).get("manifest") or {}).get("summary") or {}
    version = summary.get("tool_version") or ""
    mark = (summary.get("code_fingerprint") or "")[:12]
    if not version:
        return "not recorded (the case predates version stamping)"
    return f"{version}" + (f" · {mark}" if mark else "")


def _hms(seconds) -> str:
    """Seconds as hh:mm:ss, or a dash where there is no length to give."""
    if seconds is None:
        return "\u2014"
    seconds = int(round(float(seconds)))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def conclusion(data: dict, lengths: dict = None) -> dict:
    """The counts a report is read for, by category.

    Instances and unique files are counted separately because they answer
    different questions: how many files were found, and how many different
    pictures they are. Twenty copies of one image is twenty files and one
    picture, and a report giving only one of those numbers invites the wrong
    reading of both.
    """
    lengths = lengths or {}
    duplicates = {}
    for extra in (data.get("manifest", {}) or {}).get("duplicates", []):
        digest = (extra.get("sha256") or "").lower()
        if digest:
            duplicates[digest] = duplicates.get(digest, 0) + 1

    by_category = {}
    for row in data["rows"]:
        name = row["category_name"]
        entry = by_category.setdefault(name, {
            "category": name, "code": row.get("category"),
            "images": 0, "unique_images": 0, "videos": 0, "unique_videos": 0,
            "image_hashes": set(), "video_hashes": set(),
            "seconds": [], "unreadable": 0})
        digest = (row.get("sha256") or "").lower()
        copies = 1 + duplicates.get(digest, 0)
        is_video = (row.get("kind") == "video"
                    or str(row.get("ext", "")).lower() in VIDEO_EXTENSIONS)
        if is_video:
            entry["videos"] += copies
            entry["video_hashes"].add(digest)
            length = lengths.get(digest)
            if length is None:
                entry["unreadable"] += 1
            else:
                entry["seconds"].append(float(length))
        else:
            entry["images"] += copies
            entry["image_hashes"].add(digest)

    rows = []
    for entry in by_category.values():
        entry["unique_images"] = len(entry["image_hashes"])
        entry["unique_videos"] = len(entry["video_hashes"])
        entry["total"] = entry["images"] + entry["videos"]
        entry["longest"] = max(entry["seconds"]) if entry["seconds"] else None
        entry["shortest"] = min(entry["seconds"]) if entry["seconds"] else None
        entry["sum"] = sum(entry["seconds"]) if entry["seconds"] else None
        entry.pop("image_hashes")
        entry.pop("video_hashes")
        rows.append(entry)
    # In the unit's own order -- the order of the category bar and of the
    # keys an examiner presses -- not by how many files landed in each. A
    # reader looks for a category where they expect it, and a table that
    # reorders itself from case to case makes two reports hard to compare.
    # In the unit's own order -- the order of the category bar and of the
    # keys an examiner presses -- not by how many files landed in each. A
    # reader looks for a category where they expect it, and a table that
    # reorders itself from case to case makes two reports hard to compare.
    #
    # The two buckets that are not findings go last. Non-Pertinent and
    # Uncategorized at the top of a report about what was found puts the
    # answer below the things that are not the answer.
    trailing = data.get("trailing", TRAILING_CATEGORIES)
    positions = data.get("order") or {}

    def order(row):
        code = row.get("code")
        if not isinstance(code, int):
            return (2, 9999, row["category"])
        # The scheme's own order (a US case's is its codes'); an examiner's
        # own categories after the scheme's.
        place = positions.get(code, 100 + code)
        if code in trailing:
            return (1, place, row["category"])
        return (0, place, row["category"])

    rows.sort(key=order)

    totals = {
        "category": "Total",
        "images": sum(r["images"] for r in rows),
        "unique_images": sum(r["unique_images"] for r in rows),
        "videos": sum(r["videos"] for r in rows),
        "unique_videos": sum(r["unique_videos"] for r in rows),
        "total": sum(r["total"] for r in rows),
        "longest": max([r["longest"] for r in rows if r["longest"] is not None],
                       default=None),
        "shortest": min([r["shortest"] for r in rows
                         if r["shortest"] is not None], default=None),
        "sum": sum([r["sum"] for r in rows if r["sum"] is not None]) or None,
        "unreadable": sum(r["unreadable"] for r in rows),
    }
    return {"rows": rows, "totals": totals}


def _contents_block(rows, scheme, total_in_case: int,
                    pictures: bool = True) -> str:
    """What this report covers, as HTML, beside its donut.

    The numbers come from report_contents, which the Word and PDF
    writers use too: three documents from one count, so they cannot
    disagree about how many CAM pictures a case holds.
    """
    import base64

    from fme.exports import report_contents

    contents = report_contents.compute(rows, scheme, total_in_case)
    if not contents["files"]:
        return ""

    def line(label, value, note="", indent=False, head=False):
        if head:
            return (f'<tr><td class="ch" colspan="3">{_esc(label)}</td></tr>')
        pad = ' style="padding-left:18px;"' if indent else ""
        swatch = ""
        return (f'<tr><td{pad}>{swatch}{_esc(label)}</td>'
                f'<td class="num">{_esc(value)}</td>'
                f'<td class="pct">{_esc(note)}</td></tr>')

    body = [line("Files in this report", f"{contents['files']:,}")]
    for label, key in (("Images", "images"), ("Videos", "videos"),
                       ("Other files", "other")):
        if contents[key]:
            body.append(line(label, f"{contents[key]:,}", indent=True))

    if contents["categories"]:
        body.append(line("By category", "", head=True))
        for entry in contents["categories"]:
            dot = (f'<span class="swatch" style="background:'
                   f'{_esc(entry["color"])}"></span>')
            body.append(
                f'<tr><td style="padding-left:18px;">{dot}'
                f'{_esc(entry["name"])}</td>'
                f'<td class="num">{entry["count"]:,}</td>'
                f'<td class="pct">{entry["percent"]}%</td></tr>')

    if contents["flags"]:
        body.append(line("By flag", "", head=True))
        for entry in contents["flags"]:
            body.append(line(entry["name"], f"{entry['count']:,}",
                             f"{entry['percent']}%", indent=True))
        body.append(line("Files with at least one flag",
                         f"{contents['with_any_flag']:,}",
                         f"{contents['with_any_flag_percent']}%",
                         indent=True))

    body.append(line("Known-hash", "", head=True))
    body.append(line("Files matching a known set",
                     f"{contents['known_hash']:,}", indent=True))
    if contents["total_in_case"]:
        body.append(line("Total media in the case",
                         f"{contents['total_in_case']:,}"))

    # A report built without images holds no image: that is what makes it
    # safe to disclose, and a chart is still an image. The numbers are the
    # same either way; only the ring goes.
    picture = report_contents.donut(contents) if pictures else b""
    donut = ""
    if picture:
        encoded = base64.b64encode(picture).decode("ascii")
        donut = (f'<div class="donut">'
                 f'<img src="data:image/png;base64,{encoded}" alt=""></div>')

    return (f'<section class="contents"><h2>Report contents</h2>'
            f'<div class="contents-row">{donut}'
            f'<table class="contents-table">{"".join(body)}</table>'
            f'</div></section>')


def _conclusion_block(summary: dict, colors: dict = None) -> str:
    """The two tables, as an examiner expects to read them."""
    colors = colors or {}
    rows, totals = summary["rows"], summary["totals"]
    if not rows:
        return ""

    def cells(entry, keys):
        out = []
        for key in keys:
            value = entry.get(key, 0)
            out.append(f'<td class="n">{value:,}</td>'
                       if isinstance(value, int) else
                       f'<td class="n">{_esc(value)}</td>')
        return "".join(out)

    counts = "".join(
        f'<tr><td><span class="swatch" style="background:'
        f'{_esc(colors.get(entry["category"], "#6b7280"))}"></span>'
        f'{_esc(entry["category"])}</td>'
        + cells(entry, ("images", "unique_images", "videos", "unique_videos",
                        "total")) + "</tr>"
        for entry in rows)
    counts += ('<tr class="totals"><td>Total</td>'
               + cells(totals, ("images", "unique_images", "videos",
                                "unique_videos", "total")) + "</tr>")

    with_video = [entry for entry in rows
                  if entry["videos"] or entry["unreadable"]]
    lengths = ""
    if with_video:
        lengths = "".join(
            f'<tr><td>{_esc(entry["category"])}</td>'
            f'<td class="n">{_hms(entry["longest"])}</td>'
            f'<td class="n">{_hms(entry["shortest"])}</td>'
            f'<td class="n">{_hms(entry["sum"])}</td></tr>'
            for entry in with_video)
        lengths += (f'<tr class="totals"><td>Total</td>'
                    f'<td class="n">{_hms(totals["longest"])}</td>'
                    f'<td class="n">{_hms(totals["shortest"])}</td>'
                    f'<td class="n">{_hms(totals["sum"])}</td></tr>')

    unreadable_note = ""
    if totals.get("unreadable"):
        unreadable_note = (
            f'<p class="note">{totals["unreadable"]:,} video(s) could not be '
            f'decoded, so they have no length here and are left out of the '
            f'totals. They are still counted as files above. A carved clip '
            f'often starts mid-stream, which is enough to recover the content '
            f'and not enough to measure it.</p>')

    return (
        '<section class="conclusion"><h2>Conclusion</h2>'
        '<table class="counts"><thead><tr><th>Category</th>'
        '<th class="n">Images</th><th class="n">Unique</th>'
        '<th class="n">Video</th><th class="n">Unique</th>'
        '<th class="n">Total</th></tr></thead>'
        f'<tbody>{counts}</tbody></table>'
        '<p class="note">Images and Video count every file found, copies '
        'included. Unique counts how many different files those are, by '
        'SHA-256.</p>'
        + (('<h2>Overview of video length</h2>'
            '<table class="counts"><thead><tr><th>Category</th>'
            '<th class="n">Longest (hh:mm:ss)</th>'
            '<th class="n">Shortest (hh:mm:ss)</th>'
            '<th class="n">Total (hh:mm:ss)</th></tr></thead>'
            f'<tbody>{lengths}</tbody></table>') if lengths else "")
        + unreadable_note + '</section>')


def _summary_block(data: dict, *, category_colors: dict = None) -> str:
    """What was found, before any of it is shown.

    A reader opening a report needs the shape of the case first: how much,
    of what, when, and what the examiner marked. The detail is underneath,
    and stays underneath.
    """
    rows = data["rows"]
    by_category, by_flag, by_kind, by_year, by_band = {}, {}, {}, {}, {}
    located = []

    for row in rows:
        by_category[row["category_name"]] = by_category.get(
            row["category_name"], 0) + 1
        for name in (row["flag_names"].split(", ") if row["flag_names"] else []):
            by_flag[name] = by_flag.get(name, 0) + 1
        kind = (row.get("category") if isinstance(row.get("category"), str)
                else row.get("kind")) or _kind_of(row)
        by_kind[kind] = by_kind.get(kind, 0) + 1
        day = _shown_day(row, "captured")
        if day:
            by_year[day[:4]] = by_year.get(day[:4], 0) + 1
        band = next((name for low, high, name in SIZE_BANDS
                     if low <= (row.get("size") or 0) < high), "")
        if band:
            by_band[band] = by_band.get(band, 0) + 1
        if row.get("latitude") is not None:
            located.append((row["latitude"], row["longitude"],
                            os.path.basename(row.get("out_path", ""))))

    highlights = "".join(
        f'<div class="hl"><div class="hl-n">{_esc(value)}</div>'
        f'<div class="hl-t">{_esc(title)}</div>'
        f'<div class="hl-s">{_esc(detail)}</div></div>'
        for title, value, detail in _highlights(data))

    blocks = [
        f'<section class="highlights">{highlights}</section>' if highlights else "",
        '<section class="panelrow">'
        '<div class="panel"><h3>By category</h3><div class="row">'
        + _donut(by_category, category_colors) + _legend(by_category, category_colors)
        + '</div></div>'
        + ('<div class="panel"><h3>By flag</h3><div class="row">'
           + _donut(by_flag) + _legend(by_flag) + '</div></div>'
           if by_flag else "")
        + '</section>',
        '<section class="panelrow">'
        + f'<div class="panel"><h3>What kind of file</h3>{_bars(by_kind)}</div>'
        + (f'<div class="panel"><h3>By size</h3>'
           f'{_bars(by_band, order=[b[2] for b in SIZE_BANDS], color="#0891b2")}'
           f'</div>' if by_band else "")
        + '</section>',
        (f'<section class="panelrow"><div class="panel wide">'
         f'<h3>When they were taken <span class="sub">from EXIF; files '
         f'without it are not counted</span></h3>'
         f'{_bars(by_year, order=sorted(by_year), color="#7c3aed")}</div>'
         f'</section>' if by_year else ""),
        _where_block(located, data.get("_maps")),
    ]
    return "".join(b for b in blocks if b)


def _where_block(located: list, maps) -> str:
    """Where located files were taken: on a map when an offline basemap is
    imported and covers them, as a scatter of dots otherwise."""
    if maps is not None:
        covered = [(lat, lon) for lat, lon, _ in located if maps.covers(lat, lon)]
        png = maps.png(covered, report_maps.OVERVIEW) if covered else None
        if png:
            outside = len(located) - len(covered)
            return (f'<section class="panelrow"><div class="panel wide">'
                    f'<h3>Where they were taken <span class="sub">{len(covered):,} '
                    f'of {len(located):,} located file(s) are inside the basemap'
                    f'{f"; {outside:,} outside it are not drawn" if outside else ""}'
                    f'</span></h3><img class="map" alt="map of located files" '
                    f'src="{report_maps.data_uri(png)}">'
                    f'<div class="axis">{_esc(maps.caption())}</div></div></section>')
    if len(located) < 2:
        return ""
    return (f'<section class="panelrow"><div class="panel">'
            f'<h3>Where they were taken <span class="sub">spread and clustering '
            f'only — no basemap is imported here (fme basemap import), so no '
            f'map is drawn</span></h3>'
            f'{_scatter(located)}</div></section>')


def _kind_of(row: dict) -> str:
    extension = (row.get("ext") or "").lower()
    if extension in ("jpg", "jpeg", "png", "gif", "webp", "heic", "avif",
                     "tif", "tiff", "bmp", "ico", "psd", "jp2"):
        return "image"
    if extension in ("mp4", "mov", "avi", "mkv", "webm", "3gp", "wmv", "flv",
                     "mpg", "m2ts", "ogv"):
        return "video"
    if extension in ("mp3", "m4a", "wav", "aiff", "caf", "amr", "silk", "ogg"):
        return "audio"
    return extension or "other"


def _prepare_thumbnails(rows, data, folder, progress=None, cancel=None) -> int:
    """Make every thumbnail up front, several at a time."""
    import concurrent.futures

    workers = min(8, (os.cpu_count() or 2))
    made = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_thumbnail_file, row, data, folder): row
                   for row in rows}
        for index, future in enumerate(
                concurrent.futures.as_completed(futures), start=1):
            if cancel is not None and cancel():
                pool.shutdown(wait=False, cancel_futures=True)
                raise Canceled("The report was canceled.")
            try:
                if future.result():
                    made += 1
            except Exception:  # noqa: BLE001 - a bad file is not a failure
                pass
            if progress and index % 50 == 0:
                progress(index, len(rows) * 2)   # thumbnails, then pages
    return made


def _thumbnail_file(row: dict, data: dict, folder: str) -> str:
    """Write one thumbnail beside the report and return its relative path."""
    source = os.path.join(data["out_dir"], row.get("out_path", ""))
    name = (row.get("sha256") or os.path.basename(source))[:32] + ".jpg"
    target = os.path.join(folder, name)
    if not os.path.isfile(target):
        try:
            from fme.engines import imaging
            from PIL import Image

            with imaging.open_image(source) as opened:
                image = opened.convert("RGB")
                image.thumbnail(report_html.THUMB_SIZE, Image.LANCZOS)
                image.save(target, "JPEG", quality=70)
        except Exception:  # noqa: BLE001 - a carved file may not decode
            return ""
    return f"{os.path.basename(folder)}/{name}"


def _card(row: dict, data: dict, *, thumbnails: bool, blur: bool,
          fields: list, media_folder: str, link_full: bool,
          thumb_folder: str = "") -> str:
    rel = row.get("out_path", "")
    source = os.path.join(data["out_dir"], rel)

    preview = ""
    if thumbnails and thumb_folder:
        # Written as a file rather than embedded: a thousand base64 images
        # in one document is tens of megabytes of text the browser has to
        # parse before it shows anything.
        relative = _thumbnail_file(row, data, thumb_folder)
        preview = (f'<div class="shot{" blur" if blur else ""}">'
                   f'<img src="{_esc(relative)}" alt="" loading="lazy"></div>'
                   if relative else
                   f'<div class="shot none">{_esc((row.get("ext") or "?").upper())}'
                   f'<span>no thumbnail</span></div>')
    elif thumbnails:
        thumb = report_html.make_thumbnail(source)
        if thumb:
            preview = (f'<div class="shot{" blur" if blur else ""}">'
                       f'<img src="{_esc(thumb)}" alt="" loading="lazy"></div>')
        else:
            preview = (f'<div class="shot none">{_esc((row.get("ext") or "?").upper())}'
                       f'<span>no thumbnail</span></div>')
    if link_full and media_folder:
        name = os.path.basename(rel)
        target = os.path.join(media_folder, name)
        try:
            if os.path.getsize(source) <= MAX_FULL_COPY:
                shutil.copy2(source, target)
                preview = (f'<a href="{_esc(os.path.basename(media_folder))}/'
                           f'{_esc(name)}">{preview or _esc(name)}</a>')
        except OSError:
            pass

    cells = []
    for key in fields:
        label = dict(FIELDS).get(key, key)
        value = _field_value(row, key)
        if key == "size":
            value = _size(value)
        elif key == "deleted":
            value = "yes" if value else "no"
        elif key == "hash_sets":
            value = row.get("hash_sets", "")
        elif key in TIME_FIELDS and value:
            # Shown in the zone the case chose, with its offset. The header
            # used to name a zone and print UTC underneath it, which is a
            # misleading line in a document that goes to a court.
            value = _time_shown(value)
        if value in ("", None):
            continue
        cells.append(f"<tr><td>{_esc(label)}</td><td>{_esc(value)}</td></tr>")

    tags = []
    if row.get("category_name"):
        tags.append(f'<span class="tag cat">{_esc(row["category_name"])}</span>')
    for name in (row["flag_names"].split(", ") if row["flag_names"] else []):
        tags.append(f'<span class="tag flag">{_esc(name)}</span>')
    if row.get("deleted"):
        tags.append('<span class="tag del">deleted</span>')

    locator = ""
    if thumbnails and data.get("_maps") is not None:
        src = report_maps.locator_src(data["_maps"], row, thumb_folder)
        if src:
            locator = (f'<img class="locator" alt="where this file was taken" '
                       f'src="{_esc(src)}">')

    return (f'<article class="card">{preview}<div class="body">'
            f'<div class="nm">{_esc(os.path.basename(rel))}</div>'
            f'<div class="tags">{"".join(tags)}</div>'
            f'<table>{"".join(cells)}</table>{locator}</div></article>')


STYLE = """

.contents { margin: 18px 0 8px; }
.contents-row { display: flex; gap: 26px; align-items: center;
                flex-wrap: wrap; }
.contents .donut img { width: 190px; height: 190px; }
.contents-table { border-collapse: collapse; min-width: 330px; }
.contents-table td { padding: 3px 10px 3px 0; vertical-align: middle; }
.contents-table td.num { text-align: right; font-weight: 600;
                         font-variant-numeric: tabular-nums; }
.contents-table td.pct { text-align: right; color: #6b7280;
                         font-variant-numeric: tabular-nums; }
.contents-table td.ch { padding-top: 11px; font-size: 11px;
                        letter-spacing: .08em; text-transform: uppercase;
                        color: #6b7280; border-bottom: 1px solid #e5e7eb; }
.swatch { display: inline-block; width: 9px; height: 9px;
          border-radius: 2px; margin-right: 7px; }

 .highlights { display: grid; gap: 10px; padding: 16px 26px 0;
   grid-template-columns: repeat(auto-fill, minmax(215px, 1fr)); }
 .hl { background: #fff; border: 1px solid #d8dbe0; border-left: 4px solid #2f6fd8;
       border-radius: 6px; padding: 10px 12px; }
 .hl-n { font-size: 17px; font-weight: 700; }
 .hl-t { font-size: 12px; font-weight: 600; margin-top: 1px; }
 .hl-s { font-size: 11px; color: #5a6472; margin-top: 3px; }
 .panelrow { display: flex; gap: 14px; flex-wrap: wrap; padding: 16px 26px 0; }
 .panel { background: #fff; border: 1px solid #d8dbe0; border-radius: 8px;
          padding: 12px 15px; flex: 1 1 320px; }
 .panel.wide { flex-basis: 100%; }
 .panel h3 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase;
             letter-spacing: .05em; color: #5a6472; }
 .panel h3 .sub { text-transform: none; letter-spacing: 0; font-weight: 400;
                  color: #8a94a6; }
 .panel .row { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
 .donut { width: 148px; height: 148px; flex: none; }
 .donut-n { font-size: 17px; font-weight: 700; fill: #1d2129; }
 .donut-lbl { font-size: 8px; fill: #8a94a6; text-transform: uppercase;
              letter-spacing: .06em; }
 .donut .ring { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
 table.legend { border-collapse: collapse; font-size: 12px; flex: 1; min-width: 180px; }
 table.legend td { padding: 2px 0; border: none; }
 table.legend td.n { text-align: right; font-weight: 600; padding-left: 16px;
                     font-variant-numeric: tabular-nums; }
 table.legend td.pct { text-align: right; color: #7a8394; padding-left: 10px;
                       font-variant-numeric: tabular-nums; }
 .sw { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
       margin-right: 7px; -webkit-print-color-adjust: exact;
       print-color-adjust: exact; }
 .bars { max-width: 100%; height: auto; }
 .bars .bl { font-size: 9px; fill: #7a8394; }
 .bars .bn { font-size: 9px; fill: #1d2129; font-weight: 600; }
 .scatter { width: 260px; height: 260px; }
 .map { max-width: 100%; border: 1px solid #d8dbe0; }
 .locator { display: block; margin-top: 6px; width: 240px; border: 1px solid #d8dbe0; }
 .scatter .plot { fill: #f2f4f7; stroke: #d8dbe0; }
 .scatter .dot { fill: #2f6fd8; fill-opacity: .7; }
 .axis { font-size: 10px; color: #7a8394; margin-top: 4px;
         font-family: ui-monospace, Menlo, monospace; }
 :root { color-scheme: light; }
 body { font: 13px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
        margin: 0; background: #f4f5f7; color: #1d2129; }
 header { background: #fff; border-bottom: 2px solid #d8dbe0; padding: 20px 26px;
          display: flex; gap: 20px; align-items: flex-start; }
 header .logo { max-height: 72px; max-width: 220px; }
 h1 { margin: 0 0 8px; font-size: 21px; }
 table.meta td { padding: 2px 14px 2px 0; vertical-align: top; }
 table.meta td:first-child { color: #5a6472; white-space: nowrap; }
 .notes { margin-top: 10px; white-space: pre-wrap; }
 .coverage { border: 2px solid #b45309; background: #fffbeb; color: #78350f;
             border-radius: 6px; padding: 10px 16px; margin: 14px 0; }
 .coverage h2 { margin: 0 0 6px; font-size: 1.05em; color: #78350f; }
 .coverage ul { margin: 0; padding-left: 20px; }
 .summary { display: flex; gap: 14px; flex-wrap: wrap; padding: 16px 26px 0; }
 .sumblock { background: #fff; border: 1px solid #d8dbe0; border-radius: 8px;
             padding: 10px 14px; min-width: 210px; }
 .sumblock h3 { margin: 0 0 6px; font-size: 12px; color: #5a6472;
                text-transform: uppercase; letter-spacing: .04em; }
 .sumblock td { padding: 2px 0; }
 .sumblock td.n { text-align: right; font-family: ui-monospace, Menlo, monospace;
                  padding-left: 18px; }
 .conclusion { padding: 18px 26px 4px; }
 .conclusion h2 { font-size: 15px; margin: 14px 0 8px; }
 table.counts { border-collapse: collapse; width: 100%; max-width: 760px;
                margin-bottom: 6px; }
 table.counts th, table.counts td { border: 1px solid #d8dbe0; padding: 5px 9px;
                                    font-size: 12.5px; text-align: left; }
 table.counts th { background: #eceef1; font-weight: 600; }
 table.counts td.n, table.counts th.n { text-align: right;
                                        font-variant-numeric: tabular-nums; }
 table.counts tr.totals td { font-weight: 600; background: #f6f7f9; }
 .swatch { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
           margin-right: 6px; vertical-align: middle; }
 .conclusion .note { color: #5a6472; font-size: 11.5px; margin: 2px 0 10px;
                     max-width: 760px; }
 .pager { padding: 12px 26px; font-size: 12px; color: #5a6472; }
 .pager a { display: inline-block; padding: 2px 8px; margin: 0 1px;
            border: 1px solid #d8dbe0; border-radius: 4px; color: #2f6fd8;
            text-decoration: none; background: #fff; }
 .pager a.here { background: #2f6fd8; color: #fff; border-color: #2f6fd8; }
 .sheet { display: grid; gap: 14px; padding: 18px 26px 40px;
          grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
 .card { background: #fff; border: 1px solid #d8dbe0; border-radius: 8px;
         overflow: hidden; display: flex; flex-direction: column; }
 .shot { background: #0d1117; text-align: center; }
 .shot img { max-width: 100%; max-height: 230px; vertical-align: middle; }
 .shot.blur img { filter: blur(14px); }
 .shot.none { color: #8a94a6; padding: 46px 0; font-size: 12px; }
 .shot.none span { display: block; font-size: 11px; }
 .body { padding: 9px 11px 11px; }
 .nm { font-weight: 600; word-break: break-all; }
 .tags { display: flex; gap: 4px; flex-wrap: wrap; margin: 5px 0 7px; }
 .tag { font-size: 10px; padding: 1px 7px; border-radius: 3px;
        background: #eef0f3; color: #444c59; }
 .tag.cat { background: #e3f1ff; color: #14508c; }
 .tag.flag { background: #efe3ff; color: #5b2ea6; }
 .tag.del { background: #ffe3e3; color: #a61b1b; }
 .card table { width: 100%; border-collapse: collapse; font-size: 11px; }
 .card td { padding: 2px 0; vertical-align: top; word-break: break-all;
            font-family: ui-monospace, Menlo, Consolas, monospace; }
 .card td:first-child { color: #7a8394; width: 96px; padding-right: 8px;
                        font-family: inherit; }
 footer { padding: 16px 26px 34px; color: #7a8394; font-size: 12px;
          border-top: 1px solid #d8dbe0; background: #fff; }
 @media print {
   body { background: #fff; }
   .card { break-inside: avoid; }
 }
"""


def write_html(data: dict, out_path: str, *, header: dict, scope: dict,
               thumbnails: bool = True, blur: bool = False,
               full_copies: bool = False, fields: list = None,
               group_by_flag: bool = False, timezone: str = "",
               page_size: int = PAGE_SIZE, progress=None,
               cancel=None) -> dict:
    """Write the report, split across pages when it is large.

    One document per case stops being readable somewhere around a thousand
    files and stops opening at all well before a hundred thousand. Past that
    size the front page -- which is what a reader actually wants -- becomes a
    contents page, and the files follow in numbered pages beside it.
    """
    fields = [f for f in (fields or DEFAULT_FIELDS) if f in dict(FIELDS)] \
        or DEFAULT_FIELDS
    rows = data["rows"]
    stem = os.path.splitext(os.path.basename(out_path))[0]
    folder = os.path.dirname(os.path.abspath(out_path))

    # The folders beside the report are emptied before they are filled, and
    # the destination is one the examiner picked: a folder of their own that
    # happened to be called <stem>_media was deleted with everything in it.
    # Only a folder this program made (it carries a marker, or the report
    # beside it is one of ours that points into it) is removed.
    media_folder = ""
    stale = os.path.join(folder, stem + "_media")
    _clear_own_folder(stale, folder, stem)
    if full_copies and thumbnails:
        media_folder = stale
        _make_own_folder(media_folder)

    # Past a few hundred files the thumbnails go beside the report instead
    # of inside it.
    thumb_folder = ""
    if thumbnails and len(rows) > EMBED_LIMIT:
        thumb_folder = os.path.join(folder, stem + "_thumbs")
        _clear_own_folder(thumb_folder, folder, stem)
        _make_own_folder(thumb_folder)
    else:
        _clear_own_folder(os.path.join(folder, stem + "_thumbs"), folder, stem)

    head = (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
        f'<meta name="generator" content="{GENERATOR_MARK}">\n'
        f"<title>{_esc(header.get('title') or 'Recovered media report')}</title>\n"
        f"<style>{STYLE}</style></head><body>\n")
    # The offline basemap, if one is imported: the overview and each card's
    # locator are drawn from it, and every map names it.
    data["_maps"] = report_maps.active()
    lengths = durations(data["out_dir"], data["rows"], progress=progress)
    front = (_header(header, data, scope, thumbnails=thumbnails, blur=blur,
                     timezone=timezone)
             + _coverage_block(data)
             + _contents_block(
                 data["rows"], data.get("scheme") or [],
                 (data.get("total_in_run") or len(data["rows"])),
                 pictures=thumbnails)
             + _conclusion_block(conclusion(data, lengths),
                                 data.get("category_colors"))
             + _summary_block(data, category_colors=data.get("category_colors")))
    footer = ("<footer>Categories and flags in this report were applied by "
              "the examiner named above. Hash-set matches state which set a "
              "file appears in, not what it is. A file matching nothing is "
              "unknown, which is not the same as cleared.</footer>"
              "</body></html>\n")

    # Group first, so a section never straddles a page boundary oddly.
    if group_by_flag:
        groups = {}
        for row in rows:
            names = row["flag_names"].split(", ") if row["flag_names"] else ["(no flag)"]
            for name in names:
                groups.setdefault(name, []).append(row)
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] == "(no flag)", kv[0]))
    else:
        ordered = [("", rows)]

    pages, current, count = [], [], 0
    for title, group in ordered:
        for start in range(0, len(group), page_size):
            chunk = group[start:start + page_size]
            current.append((title if start == 0 else title + " (continued)",
                            chunk))
            count += len(chunk)
            if count >= page_size:
                pages.append(current)
                current, count = [], 0
    if current:
        pages.append(current)
    if not pages:
        pages = [[("", [])]]

    # Thumbnails are the whole cost of a large report, and each one is an
    # independent read-decode-write. Doing them together first turns a
    # twenty-minute wait into a few minutes, and means the page-building
    # loop afterwards is just text.
    if thumb_folder and rows:
        _prepare_thumbnails(rows, data, thumb_folder, progress, cancel)

    done = 0
    written = []
    for index, page in enumerate(pages, start=1):
        sections = []
        for title, group in page:
            cards = []
            for row in group:
                if cancel is not None and cancel():
                    raise Canceled("The report was canceled.")
                cards.append(_card(row, data, thumbnails=thumbnails, blur=blur,
                                   fields=fields, media_folder=media_folder,
                                   link_full=full_copies,
                                   thumb_folder=thumb_folder))
                done += 1
                if progress and done % 25 == 0:
                    progress(done, len(rows))
            heading = (f'<h2 style="padding:18px 26px 0; margin:0; '
                       f'font-size:15px;">{_esc(title)} '
                       f'<span style="color:#7a8394; font-weight:400;">'
                       f'({len(group):,})</span></h2>') if title else ""
            sections.append(heading + f'<div class="sheet">{"".join(cards)}</div>')

        name = (f"{stem}.html" if len(pages) == 1
                else f"{stem}_page{index:03d}.html")
        path = os.path.join(folder, name)
        navigation = _pager(stem, index, len(pages)) if len(pages) > 1 else ""
        body = (head
                + (front if index == 1 else _continued(header, index, len(pages)))
                + navigation + "".join(sections) + navigation + footer)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        written.append(path)

    # A rebuild with fewer pages left the old run's later pages beside the
    # new ones, carrying files the new report does not; going from one page
    # to several left <stem>.html as an older front page. Removed only when
    # they are this program's reports of this stem.
    _drop_stale_pages(folder, stem, written)

    if progress:
        progress(len(rows), len(rows))
    maps = data.pop("_maps", None)
    return {"path": written[0], "pages": written, "files": len(rows),
            "bytes": sum(os.path.getsize(p) for p in written),
            "media_folder": media_folder or "",
            "thumb_folder": thumb_folder or "",
            # Which basemap the maps were drawn on, for the run's record.
            "basemap": maps.provenance if maps and maps.drawn else None}


class Canceled(RuntimeError):
    """The report was stopped while it was being written."""


# Written into every HTML page of a report, and into a marker file in every
# folder a report makes beside itself, so a later build can tell what it
# owns in a destination the examiner chose.
GENERATOR_MARK = "fme report"
FOLDER_MARKER = ".fme-report-folder"
# Every report page ends with this; pages written before GENERATOR_MARK
# existed are recognized by it.
_FOOTER_SIGNATURE = "Hash-set matches state which set a file appears in"


def _our_page(path: str) -> bool:
    """Is this HTML file a page of a report this program wrote?"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
            if f'content="{GENERATOR_MARK}"' in head:
                return True
            fh.seek(max(0, os.path.getsize(path) - 4096))
            return _FOOTER_SIGNATURE in fh.read()
    except OSError:
        return False


def _report_pages(folder: str, stem: str) -> list:
    import re

    pattern = re.compile(re.escape(stem) + r"(_page\d{3,})?\.html\Z")
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names if pattern.match(n)]


def _drop_stale_pages(folder: str, stem: str, written: list) -> None:
    keep = {os.path.abspath(p) for p in written}
    for path in _report_pages(folder, stem):
        if os.path.abspath(path) not in keep and _our_page(path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _make_own_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, FOLDER_MARKER), "w", encoding="utf-8") as fh:
        fh.write("Made by this program's report writer beside the report of "
                 "the same name. A rebuild of that report replaces it.\n")


def _clear_own_folder(path: str, folder: str, stem: str) -> None:
    """Remove `path` if a report made it; refuse if something else did.

    Ours: it carries FOLDER_MARKER, or (a report from before the marker) a
    page of ours beside it links into it. Anything else is the examiner's,
    and writing into it -- or deleting it -- is not this program's call.
    """
    if not os.path.lexists(path):
        return
    ours = os.path.isfile(os.path.join(path, FOLDER_MARKER))
    if not ours and os.path.isdir(path) and not os.path.islink(path):
        link = os.path.basename(path) + "/"
        for page in _report_pages(folder, stem):
            if not _our_page(page):
                continue
            try:
                with open(page, "r", encoding="utf-8", errors="replace") as fh:
                    if link in fh.read():
                        ours = True
                        break
            except OSError:
                continue
    if not ours:
        raise ValueError(
            f"{path} is already there and this program did not make it. A "
            f"report writes its pictures into that folder; move it, or "
            f"choose another destination or case number for the report.")
    shutil.rmtree(path, ignore_errors=True)


def _pager(stem: str, index: int, total: int) -> str:
    links = []
    for number in range(1, total + 1):
        name = f"{stem}_page{number:03d}.html"
        links.append(f'<a href="{_esc(name)}"'
                     f'{" class=\"here\"" if number == index else ""}>'
                     f'{number}</a>')
    return (f'<nav class="pager">Page {index} of {total}: '
            + " ".join(links) + "</nav>")


def _continued(header: dict, index: int, total: int) -> str:
    return (f'<header><div><h1>'
            f'{_esc(header.get("title") or "Recovered media report")}'
            f' <span style="color:#7a8394; font-weight:400;">page {index} of '
            f'{total}</span></h1>'
            f'<table class="meta"><tr><td>Case number</td>'
            f'<td>{_esc(header.get("case_number", ""))}</td></tr>'
            f'<tr><td>Examiner</td>'
            f'<td>{_esc(header.get("examiner", ""))}</td></tr></table>'
            f'</div></header>')


# --------------------------------------------------------------------------
# The other formats
# --------------------------------------------------------------------------
def spreadsheet_safe(value):
    """A cell a spreadsheet will show rather than run.

    Paths and names come from the evidence, and one beginning with = + - @
    (or a tab or carriage return before one) is a formula when the CSV is
    opened in Excel -- run on the reviewer's machine, its result shown in
    place of what the evidence said. Such text gets a leading apostrophe,
    which Excel shows as the text itself. Numbers are left alone.
    """
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        try:
            float(value)
            return value        # -3.5 is a number, not a formula
        except ValueError:
            return "'" + value
    return value


def write_csv(data: dict, out_path: str, fields: list = None) -> dict:
    fields = [f for f in (fields or DEFAULT_FIELDS) if f in dict(FIELDS)] \
        or DEFAULT_FIELDS
    labels = dict(FIELDS)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([labels[f] for f in fields])
        for row in data["rows"]:
            writer.writerow([spreadsheet_safe(_field_value(row, f))
                             for f in fields])
    return {"path": os.path.abspath(out_path), "files": len(data["rows"])}


def write_json(data: dict, out_path: str) -> dict:
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"generated": _generated_utc(),
                   "encrypted_volumes_opened": data.get("decryption") or [],
                   "evidence": data.get("evidence") or [],
                   "does_not_cover": data.get("coverage_limits") or [],
                   "files": data["rows"]}, fh, indent=2)
    return {"path": os.path.abspath(out_path), "files": len(data["rows"])}


def build(out_dir: str, dest_dir: str, *, header: dict = None, scope: dict = None,
          formats: list = None, thumbnails: bool = True, blur: bool = False,
          full_copies: bool = False, fields: list = None,
          group_by_flag: bool = False, timezone: str = "",
          progress=None, cancel=None) -> dict:
    """Write a report in every format asked for, over one scope."""
    header = header or {}
    scope = scope or {}
    formats = formats or ["html"]
    # Before anything is written, so every row and the header agree. Rows
    # are rendered before the header, so leaving this to the header meant
    # the times stayed in UTC under a line naming another zone.
    _SHOWN_IN["zone"] = timezone or ""
    os.makedirs(dest_dir, exist_ok=True)

    data = gather(out_dir, categories=scope.get("categories"),
                  flags=scope.get("flags"), sha256=scope.get("sha256"),
                  include_uncategorized=scope.get("include_uncategorized", True))
    # A selection that leaves nothing out of a run that found files is a
    # mistake to say so about. A run that found nothing is a result: the
    # report is still written, saying so -- and saying why where the run
    # knows, a locked or unopened volume. Refusing it left exactly the cases
    # where the explanation mattered most with no report at all.
    if not data["rows"] and data["total_in_run"]:
        raise ValueError("Nothing is in scope for this report.")

    stem = header.get("case_number") or "report"
    stem = "".join(c for c in stem if c.isalnum() or c in "-_") or "report"
    written = []

    if "html" in formats:
        written.append(write_html(
            data, os.path.join(dest_dir, f"{stem}.html"), header=header,
            scope=scope, thumbnails=thumbnails, blur=blur,
            full_copies=full_copies, fields=fields,
            group_by_flag=group_by_flag, timezone=timezone, progress=progress,
            cancel=cancel))
    if "protocol" in formats:
        from fme.exports import protocol
        written.append(protocol.write(
            data, os.path.join(dest_dir, f"{stem}_protocol.docx"),
            header=header, scope=scope, conclusion=conclusion,
            durations=durations, hms=_hms, thumbnails=thumbnails, blur=blur,
            thumbnail_of=_docx_thumbnail, progress=progress, cancel=cancel,
            introduction=header.get("introduction", ""),
            definition=header.get("definition", "")))
    if "docx" in formats:
        written.append(write_docx(
            data, os.path.join(dest_dir, f"{stem}.docx"), header=header,
            scope=scope, fields=fields, thumbnails=thumbnails, blur=blur,
            timezone=timezone, progress=progress, cancel=cancel))
    if "pdf" in formats:
        written.append(write_pdf(
            data, os.path.join(dest_dir, f"{stem}.pdf"), header=header,
            scope=scope, fields=fields, thumbnails=thumbnails, blur=blur,
            timezone=timezone, progress=progress, cancel=cancel))
    if "csv" in formats:
        written.append(write_csv(data, os.path.join(dest_dir, f"{stem}.csv"), fields))
    if "json" in formats:
        written.append(write_json(data, os.path.join(dest_dir, f"{stem}.json")))
    if {"md5", "kml", "projectvic"} & set(formats):
        scoped = scoped_manifest(data)
    if "md5" in formats:
        written.append(report_html.write_md5_list(
            os.path.join(out_dir, "manifest.json"),
            os.path.join(dest_dir, f"{stem}_md5.csv"), manifest=scoped))
    if "kml" in formats:
        from fme.exports import kml_export
        try:
            written.append(kml_export.export(
                os.path.join(out_dir, "manifest.json"),
                os.path.join(dest_dir, f"{stem}.kml"),
                title=header.get("case_number", ""), manifest=scoped))
        except ValueError as exc:
            written.append({"path": "", "skipped": str(exc)})
    if "projectvic" in formats:
        from fme.exports import projectvic_export
        written.append(projectvic_export.export(
            os.path.join(out_dir, "manifest.json"),
            os.path.join(dest_dir, f"{stem}_ProjectVIC.json"),
            case_number=header.get("case_number", ""), manifest=scoped))

    # Each exporter names its own result differently -- "path", "output",
    # "out_path". The dialog shows what came out, so they are normalized
    # here; otherwise a format that worked perfectly appears as a blank line
    # and nobody can tell where the file went.
    for entry in written:
        if isinstance(entry, dict) and not entry.get("path"):
            for key in ("output", "out_path", "file", "csv"):
                if entry.get(key):
                    entry["path"] = entry[key]
                    break
    return {"files": len(data["rows"]), "of": data["total_in_run"],
            "written": written, "folder": os.path.abspath(dest_dir)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write a case report.")
    ap.add_argument("-o", "--out-dir", required=True,
                    help="a run's output folder")
    ap.add_argument("-d", "--dest", required=True, help="where to write")
    ap.add_argument("--format", action="append", default=[],
                    choices=["html", "csv", "json", "md5", "kml", "projectvic"])
    ap.add_argument("--case", default="")
    ap.add_argument("--agency", default="")
    ap.add_argument("--examiner", default="")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--no-thumbnails", dest="thumbnails", action="store_false",
                    help="a report with no picture of any file in it")
    ap.add_argument("--blur", action="store_true",
                    help="blur the thumbnails; the image is still in the file")
    ap.add_argument("--full-copies", action="store_true",
                    help="copy the files beside the report and link them")
    ap.add_argument("--category", action="append", type=int, default=[])
    ap.add_argument("--by-flag", action="store_true",
                    help="a section per flag")
    ap.add_argument("--graded-only", action="store_true")
    args = ap.parse_args(argv)

    try:
        result = build(
            args.out_dir, args.dest,
            header={"case_number": args.case, "agency": args.agency,
                    "examiner": args.examiner, "evidence": args.evidence,
                    "notes": args.notes},
            scope={"categories": args.category or None,
                   "include_uncategorized": not args.graded_only},
            formats=args.format or ["html"], thumbnails=args.thumbnails,
            blur=args.blur, full_copies=args.full_copies,
            group_by_flag=args.by_flag)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 2

    print(f"Covered {result['files']:,} of {result['of']:,} file(s)")
    for item in result["written"]:
        if item.get("skipped"):
            print(f"  skipped: {item['skipped']}")
        else:
            print(f"  {item['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------
# Word and PDF
# --------------------------------------------------------------------------
def write_docx(data: dict, out_path: str, *, header: dict, scope: dict,
               fields: list = None, thumbnails: bool = True,
               blur: bool = False, timezone: str = "",
               progress=None, cancel=None) -> dict:
    """A Word document, written without a Word library.

    A .docx is a zip of XML, so it is built here directly rather than
    through a dependency that would have to be installed on every
    examination machine. What that costs is elaborate layout; what it buys
    is a file that opens in Word, LibreOffice and Google Docs from a program
    whose only requirement is Flask.

    Thumbnails are embedded as JPEGs at the size they are drawn, because a
    Word file is handed over as one object and a linked image would arrive
    broken.
    """
    import struct
    import zipfile

    fields = [f for f in (fields or DEFAULT_FIELDS) if f in dict(FIELDS)] \
        or DEFAULT_FIELDS
    labels = dict(FIELDS)
    rows = data["rows"]

    def xml_escape(value) -> str:
        from fme.exports.docx_parts import xml_text

        return xml_text(value)      # control characters too (see there)

    def paragraph(text, *, bold=False, size=20, space_after=60,
                  color="000000") -> str:
        return (f'<w:p><w:pPr><w:spacing w:after="{space_after}"/></w:pPr>'
                f'<w:r><w:rPr>{"<w:b/>" if bold else ""}'
                f'<w:sz w:val="{size}"/><w:color w:val="{color}"/></w:rPr>'
                f'<w:t xml:space="preserve">{xml_escape(text)}</w:t>'
                f'</w:r></w:p>')

    media, relationships, body = [], [], []
    logo = _logo(header, "JPEG", box=(360, 120))
    if logo:
        media.append(("media/logo.jpeg", logo["bytes"]))
        relationships.append(
            '<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/image" Target="media/logo.jpeg"/>')
        # Drawn at half its pixel size, as the HTML header draws it. The
        # docPr id is one no thumbnail takes (those count up from 1).
        body.append(_docx_picture("rIdLogo", 999_999, logo["width"] // 2,
                                  logo["height"] // 2))
    body.append(paragraph(header.get("title") or "Recovered media report",
                          bold=True, size=36))
    for label, value in (("Agency", header.get("agency", "")),
                         ("Case number", header.get("case_number", "")),
                         ("Evidence item", header.get("evidence", "")),
                         ("Examiner", header.get("examiner", "")),
                         ("Source", data["manifest"].get("summary", {}).get("image", "")),
                         ("Generated", _generated(timezone)),
                         ("Times shown in", _describe_zone(timezone)),
                         ("Produced by", _built_by(data)),
                         ("Graded under", data.get("region", "")),
                         ("Covers", _scope_sentence(scope, data))):
        if value:
            body.append(paragraph(f"{label}: {value}", size=20, color="44505f"))
    if header.get("notes"):
        body.append(paragraph(header["notes"], size=20))
    if not thumbnails:
        body.append(paragraph(
            "Images are not included. This report holds no thumbnail or "
            "picture of any file — only the record of each one.",
            size=20, color="8a2b2b"))
    if data.get("decryption"):
        body.append(paragraph("Encrypted volumes opened", bold=True,
                              size=24, space_after=40, color="1f3a5f"))
        for note in data["decryption"]:
            body.append(paragraph(note, size=20, color="1f3a5f"))
    if data.get("evidence"):
        body.append(paragraph("The evidence", bold=True,
                              size=24, space_after=40, color="1f3a5f"))
        for note in data["evidence"]:
            body.append(paragraph(note, size=20, color="1f3a5f"))
    if data.get("coverage_limits"):
        body.append(paragraph("What this report does not cover", bold=True,
                              size=24, space_after=40, color="78350f"))
        for limit in data["coverage_limits"]:
            body.append(paragraph(limit, size=20, color="78350f"))

    counts = {}
    for row in rows:
        counts[row["category_name"]] = counts.get(row["category_name"], 0) + 1
    body.append(paragraph("Summary", bold=True, size=26, space_after=80))
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        body.append(paragraph(f"{name}: {count:,}", size=20, space_after=20))

    body.append(paragraph("Files", bold=True, size=26, space_after=80))
    for index, row in enumerate(rows, start=1):
        if cancel is not None and cancel():
            raise Canceled("The report was canceled.")
        body.append(paragraph(os.path.basename(row.get("out_path", "")),
                              bold=True, size=22, space_after=20))

        if thumbnails:
            picture = _docx_thumbnail(row, data, blur=blur)
            if picture:
                name = f"media/image{len(media) + 1}.jpeg"
                media.append((name, picture["bytes"]))
                relationship = f"rId{100 + len(media)}"
                relationships.append(
                    f'<Relationship Id="{relationship}" Type="http://schemas.'
                    f'openxmlformats.org/officeDocument/2006/relationships/'
                    f'image" Target="{name}"/>')
                body.append(_docx_picture(relationship, len(media),
                                          picture["width"], picture["height"]))

        for key in fields:
            value = _field_value(row, key)
            if key == "size":
                value = _size(value)
            elif key == "deleted":
                value = "yes" if value else "no"
            elif key in TIME_FIELDS and value:
                value = _time_shown(value)
            if value in ("", None):
                continue
            body.append(paragraph(f"{labels.get(key, key)}: {value}",
                                  size=18, space_after=10, color="44505f"))
        body.append(paragraph("", size=14, space_after=120))
        if progress and index % 25 == 0:
            progress(index, len(rows))

    body.append(paragraph(
        "Categories and flags in this report were applied by the examiner "
        "named above. Hash-set matches state which set a file appears in, "
        "not what it is. A file matching nothing is unknown, which is not "
        "the same as cleared.", size=16, color="7a8394"))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<w:body>{"".join(body)}</w:body></w:document>')

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="jpeg" ContentType="image/jpeg"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        archive.writestr("_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(relationships) + '</Relationships>')
        for name, blob in media:
            archive.writestr(f"word/{name}", blob)

    if progress:
        progress(len(rows), len(rows))
    return {"path": os.path.abspath(out_path), "files": len(rows),
            "bytes": os.path.getsize(out_path)}


def _docx_thumbnail(row: dict, data: dict, blur: bool = False):
    """A JPEG of one file, sized for a Word page."""
    try:
        from fme.engines import imaging
        from PIL import Image, ImageFilter
    except ImportError:
        return None
    source = os.path.join(data["out_dir"], row.get("out_path", ""))
    try:
        with imaging.open_image(source) as opened:
            image = opened.convert("RGB")
            image.thumbnail((320, 320), Image.LANCZOS)
            if blur:
                image = image.filter(ImageFilter.GaussianBlur(12))
            import io as _io
            buffer = _io.BytesIO()
            image.save(buffer, "JPEG", quality=72)
            return {"bytes": buffer.getvalue(), "width": image.width,
                    "height": image.height}
    except Exception:  # noqa: BLE001 - a carved file may not decode
        return None


def _docx_picture(relationship: str, index: int, width: int, height: int) -> str:
    """The drawing element Word needs to show an embedded image."""
    # Word measures in English Metric Units: 914,400 to the inch, and a
    # pixel is treated as 1/96 inch.
    emu_width = int(width * 9525)
    emu_height = int(height * 9525)
    return (
        f'<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{emu_width}" cy="{emu_height}"/>'
        f'<wp:docPr id="{index}" name="Picture {index}"/><a:graphic>'
        f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{index}" name="Picture {index}"/>'
        f'<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>'
        f'<a:blip r:embed="{relationship}"/><a:stretch><a:fillRect/></a:stretch>'
        f'</pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/>'
        f'<a:ext cx="{emu_width}" cy="{emu_height}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        f'</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')


def write_pdf(data: dict, out_path: str, *, header: dict, scope: dict,
              fields: list = None, thumbnails: bool = True, blur: bool = False,
              timezone: str = "", progress=None, cancel=None) -> dict:
    """A PDF, with ReportLab where it is installed.

    Unlike .docx, a PDF with embedded images is not something to hand-roll:
    the format allows it, but a half-correct writer produces files that open
    in one reader and not another, which is the worst outcome for a document
    meant to be handed to somebody else. So this uses ReportLab if it is
    there, and otherwise says plainly what to install -- and points out that
    printing the HTML report to PDF from a browser gives a good result with
    nothing installed at all.
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas as pdf_canvas
    except ImportError:
        raise RuntimeError(
            "Writing PDF directly needs ReportLab: pip install reportlab. "
            "Without it, open the HTML report in a browser and print it to "
            "PDF -- the report is laid out for printing and the result is "
            "usually better.")

    fields = [f for f in (fields or DEFAULT_FIELDS) if f in dict(FIELDS)] \
        or DEFAULT_FIELDS
    labels = dict(FIELDS)
    rows = data["rows"]

    page = pdf_canvas.Canvas(out_path, pagesize=LETTER)
    width, height = LETTER
    margin = 0.75 * inch
    y = height - margin

    def line(text, size=10, gap=14, color=(0.1, 0.12, 0.16), bold=False):
        nonlocal y
        if y < margin + 40:
            page.showPage()
            y = height - margin
        page.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        page.setFillColorRGB(*color)
        page.drawString(margin, y, str(text)[:110])
        y -= gap

    logo = _logo(header, "PNG")
    if logo:
        import io as _logo_io
        from reportlab.lib.utils import ImageReader as _LogoReader

        # 72 points to the inch, and the logo was sized at twice the 96 dpi
        # the HTML header draws it at.
        drawn_w, drawn_h = logo["width"] * 0.375, logo["height"] * 0.375
        page.drawImage(_LogoReader(_logo_io.BytesIO(logo["bytes"])),
                       margin, y - drawn_h, width=drawn_w, height=drawn_h,
                       mask="auto")
        y -= drawn_h + 18
    line(header.get("title") or "Recovered media report", size=17, gap=24,
         bold=True)
    for label, value in (("Agency", header.get("agency", "")),
                         ("Case number", header.get("case_number", "")),
                         ("Evidence item", header.get("evidence", "")),
                         ("Examiner", header.get("examiner", "")),
                         ("Generated", _generated(timezone)),
                         ("Times shown in",
                          _describe_zone(timezone)),
                         ("Produced by", _built_by(data)),
                         ("Graded under", data.get("region", "")),
                         ("Covers", _scope_sentence(scope, data))):
        if value:
            line(f"{label}: {value}", size=9, gap=12, color=(0.35, 0.39, 0.45))
    if not thumbnails:
        line("Images are not included: this report holds no picture of any "
             "file.", size=9, gap=16, color=(0.55, 0.15, 0.15))
    if data.get("decryption"):
        import textwrap

        y -= 4
        line("Encrypted volumes opened", size=11, gap=15, bold=True,
             color=(0.12, 0.23, 0.37))
        for note in data["decryption"]:
            for part in textwrap.wrap(note, 100):
                line(part, size=9, gap=12, color=(0.12, 0.23, 0.37))
            y -= 3
    if data.get("evidence"):
        import textwrap

        y -= 4
        line("The evidence", size=11, gap=15, bold=True,
             color=(0.12, 0.23, 0.37))
        for note in data["evidence"]:
            for part in textwrap.wrap(note, 100):
                line(part, size=9, gap=12, color=(0.12, 0.23, 0.37))
            y -= 3
    if data.get("coverage_limits"):
        import textwrap

        y -= 4
        line("What this report does not cover", size=11, gap=15, bold=True,
             color=(0.47, 0.21, 0.06))
        for limit in data["coverage_limits"]:
            # line() cuts at 110 characters; these are wrapped instead,
            # because a cut-off limit is a limit half-stated.
            for part in textwrap.wrap(limit, 100):
                line(part, size=9, gap=12, color=(0.47, 0.21, 0.06))
            y -= 3
    y -= 6

    counts = {}
    for row in rows:
        counts[row["category_name"]] = counts.get(row["category_name"], 0) + 1
    # The same count the HTML and the Word document use.
    try:
        from fme.exports import report_contents

        contents = report_contents.compute(
            data["rows"], data.get("scheme") or [],
            (data.get("total_in_run") or len(data["rows"])))
        if contents["files"]:
            line("Report contents", size=12, gap=18, bold=True)

            picture = report_contents.donut(contents, size=300)
            drawn_to = y
            if picture:
                import io as _bytes
                from reportlab.lib.utils import ImageReader

                side = 1.5 * inch
                page.drawImage(ImageReader(_bytes.BytesIO(picture)),
                               margin, y - side + 12, width=side,
                               height=side, mask="auto")
                drawn_to = y - side + 12

            left = margin + (1.8 * inch if picture else 0)
            for label, value, note in report_contents.as_rows(contents):
                if y < 90:
                    page.showPage()
                    y = height - margin
                heading = label.strip() == label and not value
                page.setFont("Helvetica-Bold" if heading else "Helvetica",
                             8.5 if heading else 9.5)
                page.setFillColorRGB(*((0.42, 0.45, 0.5) if heading
                                       else (0.1, 0.12, 0.16)))
                page.drawString(left, y, label.strip())
                if value:
                    page.drawRightString(left + 2.6 * inch, y, value)
                if note:
                    page.setFillColorRGB(0.42, 0.45, 0.5)
                    page.drawRightString(left + 3.3 * inch, y, note)
                y -= 13
            y = min(y, drawn_to) - 10
    except Exception as exc:  # noqa: BLE001 - a report is not lost over it
        import logging

        logging.getLogger("fme.report").warning(
            "the contents block was left out of the PDF: %s", exc)

    line("Summary", size=12, gap=18, bold=True)
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        line(f"{name}: {count:,}", size=9, gap=12)
    y -= 8

    line("Files", size=12, gap=18, bold=True)
    for index, row in enumerate(rows, start=1):
        if cancel is not None and cancel():
            page.save()
            raise Canceled("The report was canceled.")
        if y < margin + 180:
            page.showPage()
            y = height - margin
        line(os.path.basename(row.get("out_path", "")), size=10, gap=14,
             bold=True)

        if thumbnails:
            picture = _docx_thumbnail(row, data, blur=blur)
            if picture:
                import io as _io

                from reportlab.lib.utils import ImageReader
                drawn_height = min(150, picture["height"])
                drawn_width = picture["width"] * (drawn_height / picture["height"])
                if y - drawn_height < margin:
                    page.showPage()
                    y = height - margin
                page.drawImage(ImageReader(_io.BytesIO(picture["bytes"])),
                               margin, y - drawn_height, width=drawn_width,
                               height=drawn_height, mask="auto")
                y -= drawn_height + 8

        for key in fields:
            value = _field_value(row, key)
            if key == "size":
                value = _size(value)
            elif key == "deleted":
                value = "yes" if value else "no"
            elif key in TIME_FIELDS and value:
                value = _time_shown(value)
            if value in ("", None):
                continue
            line(f"{labels.get(key, key)}: {value}", size=8, gap=10,
                 color=(0.35, 0.39, 0.45))
        y -= 10
        if progress and index % 25 == 0:
            progress(index, len(rows))

    page.showPage()
    page.save()
    if progress:
        progress(len(rows), len(rows))
    return {"path": os.path.abspath(out_path), "files": len(rows),
            "bytes": os.path.getsize(out_path)}
