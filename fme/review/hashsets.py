"""
hashsets.py -- load hash sets and match recovered files against them.

Three kinds, because those are the three an examiner actually has:

  Project VIC   VICS JSON, with a category per hash. Categories come from
                the set; nothing here assigns one.
  NSRL          the National Software Reference Library, in any of the forms
                it ships: the classic NSRLFile.txt, a modern RDS SQLite
                database, or a plain export. NSRL is a *known-benign* set --
                operating system and application files -- so a match is a
                reason to set a file aside, not to look harder at it.
  Custom        your own list: one hash per line, or a CSV with a hash column.
                Anything an office builds up over time.

A set is loaded once into memory as {hash: entry} and matched against a run's
manifest by MD5, SHA-1 and SHA-256, so it does not matter which algorithm a
set was built with.

    python3 hashsets.py --scan out/manifest.json \\
        --vic VIC_US_2026.json --nsrl NSRLFile.txt --custom our_known.txt \\
        --csv matches.csv

What a match means is deliberately left to the examiner. The output says
which set a file appeared in and what that set recorded, and nothing else.
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
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Iterator, Optional
from fme.sqlite_uri import ro_uri

__version__ = "1.0.0"

HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")

# The hashes of a zero-byte file. Every empty file has these, so they identify
# none of them -- a set containing one would flag every empty file in a run as
# a match. Excluded on import, whatever kind of set it came from.
EMPTY_FILE_HASHES = frozenset({
    "d41d8cd98f00b204e9800998ecf8427e",                              # MD5
    "da39a3ee5e6b4b0d3255bfef95601890afd80709",                      # SHA-1
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # SHA-256
})

# What a match from each kind of set means, in one line, so a report does not
# leave a reader to guess.
KIND_MEANING = {
    "projectvic": "categorized in a Project VIC set",
    "nsrl": "known software or operating system file (NSRL)",
    "custom": "in a locally maintained list",
}


# The fields a Project VIC record carries that an examiner acts on. Kept as
# the set spelled them, so a value in a report can be traced back to the file
# it came from without a translation table in between. The store and a set
# loaded for the session both read records through vic_record, so a match
# says the same thing whichever of them made it.
VIC_FIELDS = (
    "MediaID", "Category", "MD5", "SHA1", "SHA256", "PhotoDNA",
    "MediaSize", "DateUpdated", "LastModified", "Tags", "Series",
    "IsPrecategorized", "PrecategorizationSource",
    "VictimIdentified", "OffenderIdentified", "IsDistributed",
    "RelativeFilePath", "Description", "Comment", "GroupID", "CategoryType",
)

# The ones a match CSV gives a column each: what decides what happens next.
VIC_CSV_FIELDS = ("MediaID", "Category", "Series", "Tags", "VictimIdentified",
                  "OffenderIdentified", "IsDistributed")


# The VICS scheme's own names, for saying what an imported set holds. These
# are the set's categories, not this program's: VICS 5 is "Uncategorized",
# where this program's 5 is Adult Pornography, and casedb.VIC_CATEGORY_IN is
# what decides which of them grade a file.
VIC_CATEGORY_NAMES = {
    0: "Non-Pertinent",
    1: "Child Abuse Material (CAM)",
    2: "Child Exploitive (non-CAM) / Age Difficult",
    3: "CGI / Animation - Child Exploitive",
    4: "Comparison Images",
    5: "Uncategorized",
}


def vic_category(value):
    """A VICS Category as an integer, or None when it does not name one.

    The VICS model makes Category an integer, and the national download
    honors that -- but sets converted from CSV, and some vendors' exports,
    write it as "1". Every reader checked isinstance(category, int), so a
    string "1" was imported with no category at all and a CAM file matched
    against it stayed Uncategorized, with nothing on screen to say why.
    A bool is refused even though Python counts it as an int.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def looks_like_vics(path: str) -> bool:
    """Does the front of this file look like a Project VIC (VICS) export?

    Asked of the content, never the name. A bare array of media records,
    or a case export, has no @odata.context, and detect_kind used to call
    such a file "custom" -- importing a national set with every category
    thrown away.
    """
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            head = fh.read(16384)
    except OSError:
        return False
    if "@odata.context" in head and "projectvic" in head.lower():
        return True
    stripped = head.lstrip()
    return stripped[:1] in ("{", "[") and '"MediaID"' in head and \
        ('"Category"' in head or '"MD5"' in head or '"SHA1"' in head)


# What the shared hash server may tell a caller about a matched VICS record.
# An allow-list, not a deny-list: the server once passed the stored record
# through whole, so a read-only account that sent one MD5 got back the
# file's SHA-1, SHA-256, its PhotoDNA (licence-controlled) and the record's
# free text (Description, Comment, RelativeFilePath). A field added to
# VIC_FIELDS later stays private until someone decides it may travel.
VIC_SHAREABLE_FIELDS = VIC_CSV_FIELDS + (
    "IsPrecategorized", "PrecategorizationSource", "CategoryType")


def vic_shareable(record: dict) -> dict:
    """The part of a stored VICS record a lookup may return to its caller."""
    if not isinstance(record, dict):
        return {}
    return {k: v for k, v in record.items() if k in VIC_SHAREABLE_FIELDS}


def vic_record(entry: dict) -> dict:
    """The fields of one VICS media entry worth keeping, as plain values."""
    kept = {}
    for name in VIC_FIELDS:
        value = entry.get(name)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            kept[name] = json.dumps(value)
        elif isinstance(value, list):
            kept[name] = ", ".join(str(v) for v in value)
        else:
            kept[name] = value
    return kept


def vic_digests(entry: dict) -> dict:
    """Every hash one VICS entry carries, by algorithm name: MD5 and SHA1,
    SHA256, and whatever sits in AlternativeHashes."""
    digests = {}
    for key in ("MD5", "SHA1", "SHA256"):
        value = entry.get(key) or ""
        if isinstance(value, str) and value:
            digests[key] = value.lower()
    alternatives = entry.get("AlternativeHashes") or []
    if isinstance(alternatives, list):
        for item in alternatives:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if isinstance(value, str) and HASH_RE.match(value):
                    digests.setdefault(key.upper(), value.lower())
    return digests


@dataclass
class HashSet:
    name: str
    kind: str                      # projectvic | nsrl | custom
    path: str
    hashes: dict = field(default_factory=dict, repr=False)
    notes: str = ""
    # digest -> the set's record for it, as compact JSON: one string per
    # entry, shared by that entry's hashes. A national set held in memory as
    # a dict per entry would cost several times what the hashes do.
    records: dict = field(default_factory=dict, repr=False)

    @property
    def size(self) -> int:
        return len(self.hashes)

    def summary(self) -> dict:
        return {"name": self.name, "kind": self.kind, "path": self.path,
                "size": self.size, "notes": self.notes,
                "meaning": KIND_MEANING.get(self.kind, "")}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def load_projectvic(path: str, name: str = "") -> HashSet:
    """A VICS file used as a hash set: hashes plus the category each carries."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fme.exports import vic_import

    # The whole record is kept, not only "category N". Before, a set loaded
    # here gave a match with no MediaID, Series, Tags or flags -- the details
    # pane had nothing to show -- and no category the program could act on,
    # so auto-categorize did nothing for it. Only MD5 and SHA1 were read, so
    # an entry the store matched on SHA256 did not match here at all.
    hashes, records = {}, {}
    for entry in vic_import.stream_media(path):
        category = vic_category(entry.get("Category"))
        label = "" if category is None else f"category {category}"
        kept = vic_record(entry)
        record = json.dumps(kept, separators=(",", ":")) if kept else ""
        for value in vic_digests(entry).values():
            if value and value not in EMPTY_FILE_HASHES:
                # A hash listed twice keeps its category: the last entry
                # won, and an uncategorized repeat erased a category 1.
                if not label and hashes.get(value):
                    continue
                if label and hashes.get(value) and not _more_severe(
                        category, hashes[value]):
                    continue
                hashes[value] = label
                if record:
                    records[value] = record
    return HashSet(name or os.path.basename(path), "projectvic", path, hashes,
                   records=records)


def _more_severe(category, label: str) -> bool:
    """Is `category` more severe than the one `label` ("category N") names?"""
    from fme.review.casedb import severity_rank

    try:
        new, old = int(category), int(label.rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return False
    return severity_rank(new) < severity_rank(old)


def load_nsrl(path: str, name: str = "") -> HashSet:
    """NSRL, in whichever form it arrived.

    The RDS has shipped as a quoted CSV (NSRLFile.txt) for years and as a
    SQLite database more recently; both turn up on analyst machines, so both
    are accepted rather than making someone convert one to the other.
    """
    if _is_sqlite(path):
        return _load_nsrl_sqlite(path, name)
    return _load_nsrl_text(path, name)


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _load_nsrl_sqlite(path: str, name: str = "") -> HashSet:
    hashes = {}
    con = sqlite3.connect(ro_uri(path), uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        # The RDS has used FILE, DISTINCT_HASH and a few other names; find
        # whichever table actually carries the hash columns.
        for table in tables:
            columns = {r[1].lower() for r in con.execute(f'PRAGMA table_info("{table}")')}
            hash_columns = [c for c in ("md5", "sha1", "sha256") if c in columns]
            if not hash_columns:
                continue
            selected = ", ".join(f'"{c}"' for c in hash_columns)
            for row in con.execute(f'SELECT {selected} FROM "{table}"'):
                for value in row:
                    if value and str(value).lower() not in EMPTY_FILE_HASHES:
                        hashes[str(value).lower()] = "NSRL"
            if hashes:
                break
    finally:
        con.close()
    return HashSet(name or os.path.basename(path), "nsrl", path, hashes,
                   notes="known software; a match is usually a reason to "
                         "exclude a file, not to examine it")


def _load_nsrl_text(path: str, name: str = "") -> HashSet:
    """NSRLFile.txt: "SHA-1","MD5","CRC32","FileName",FileSize,... with a
    header row naming the columns."""
    hashes = {}
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return HashSet(name or os.path.basename(path), "nsrl", path, {})
        lowered = [h.strip().strip('"').lower().replace("-", "") for h in header]
        indexes = [i for i, h in enumerate(lowered) if h in ("md5", "sha1", "sha256")]
        if not indexes:
            # No header: fall back to treating any hash-shaped field as one.
            fh.seek(0)
            for row in csv.reader(fh):
                for cell in row:
                    cell = cell.strip().strip('"')
                    if HASH_RE.match(cell) and cell.lower() not in EMPTY_FILE_HASHES:
                        hashes[cell.lower()] = "NSRL"
            return HashSet(name or os.path.basename(path), "nsrl", path, hashes,
                           notes="known software")
        for row in reader:
            for i in indexes:
                if i < len(row):
                    cell = row[i].strip().strip('"')
                    if HASH_RE.match(cell) and cell.lower() not in EMPTY_FILE_HASHES:
                        hashes[cell.lower()] = "NSRL"
    return HashSet(name or os.path.basename(path), "nsrl", path, hashes,
                   notes="known software; a match is usually a reason to "
                         "exclude a file, not to examine it")


def load_custom(path: str, name: str = "") -> HashSet:
    """A list of your own: one hash per line, or a CSV with a hash column.

    Anything after the hash on a line -- a comma, a tab, a space -- is kept
    as the note for that hash, so a list can carry a reason alongside each
    entry without needing a defined format.
    """
    hashes = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[,;\t ]+", line, maxsplit=1)
            value = parts[0].strip().strip('"')
            if not HASH_RE.match(value):
                # Maybe the hash is not the first column.
                for cell in re.split(r"[,;\t]", line):
                    cell = cell.strip().strip('"')
                    if HASH_RE.match(cell):
                        value = cell
                        break
                else:
                    continue
            if value.lower() in EMPTY_FILE_HASHES:
                continue
            note = parts[1].strip().strip('"') if len(parts) > 1 else ""
            hashes[value.lower()] = note
    return HashSet(name or os.path.basename(path), "custom", path, hashes)


def load(path: str, kind: str = "", name: str = "") -> HashSet:
    """Load a set, working out what it is when not told."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    kind = kind or detect_kind(path)
    if kind == "projectvic":
        return load_projectvic(path, name)
    if kind == "nsrl":
        return load_nsrl(path, name)
    return load_custom(path, name)


def detect_kind(path: str) -> str:
    """Identify a set by looking at it rather than at its file name."""
    if _is_sqlite(path):
        return "nsrl"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        return "custom"
    if looks_like_vics(path):
        return "projectvic"
    lowered = head.lower()
    if "nsrl" in lowered or ('"sha-1"' in lowered and '"md5"' in lowered):
        return "nsrl"
    return "custom"


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------
def match_records(records: list, sets: list) -> list:
    """Annotate each record with the sets it appears in.

    A file is checked against every set rather than stopping at the first
    hit: being in both a known-software set and a categorized set is itself
    worth seeing, and hiding the second match would obscure it.
    """
    results = []
    for rec in records:
        digests = [(rec.get(k) or "").lower() for k in ("md5", "sha1", "sha256")]
        digests = [d for d in digests if d]
        matches = []
        for hash_set in sets:
            for digest in digests:
                note = hash_set.hashes.get(digest)
                if note is not None:
                    raw = hash_set.records.get(digest)
                    record = json.loads(raw) if raw else {}
                    matches.append({"set": hash_set.name, "kind": hash_set.kind,
                                    "note": note, "matched_hash": digest,
                                    "category": vic_category(record.get("Category")),
                                    "record": record, "via": "session"})
                    break
        results.append({"record": rec, "matches": matches})
    return results


def scan_manifest(manifest_path: str, sets: list,
                  include_duplicates: bool = True) -> dict:
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    records = list(manifest.get("files", []))
    if include_duplicates:
        records += manifest.get("duplicates", [])

    annotated = match_records(records, sets)
    by_set: dict = {}
    matched = 0
    for row in annotated:
        if row["matches"]:
            matched += 1
        for hit in row["matches"]:
            by_set[hit["set"]] = by_set.get(hit["set"], 0) + 1

    return {
        "checked": len(records),
        "matched": matched,
        "unmatched": len(records) - matched,
        "by_set": by_set,
        "sets": [s.summary() for s in sets],
        "rows": annotated,
    }


def write_csv(result: dict, path: str) -> str:
    """One row per file checked. The Project VIC columns come after the
    original nine, so anything reading those by position still works; each
    holds the values from every matching set, joined with "; "."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["out_path", "source_path", "size", "md5", "sha1",
                         "sha256", "in_sets", "kinds", "notes"]
                        + [_csv_column(name) for name in VIC_CSV_FIELDS])
        for row in result["rows"]:
            rec, hits = row["record"], row["matches"]
            records = [h.get("record") or {} for h in hits]
            writer.writerow([
                rec.get("out_path", ""), rec.get("source_path", ""),
                rec.get("size", 0), rec.get("md5", ""), rec.get("sha1", ""),
                rec.get("sha256", ""),
                "; ".join(h["set"] for h in hits),
                "; ".join(sorted({h["kind"] for h in hits})),
                "; ".join(h["note"] for h in hits if h["note"]),
            ] + ["; ".join(str(r[name]) for r in records
                           if r.get(name) not in (None, ""))
                 for name in VIC_CSV_FIELDS])
    return os.path.abspath(path)


def _csv_column(name: str) -> str:
    """vic_media_id, vic_series, vic_victim_identified ..."""
    return "vic_" + re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower().replace(
        "media_i_d", "media_id")


def match_origin(match: dict) -> str:
    """Where a saved match came from: the shared store, a set loaded for the
    session, or the examiner's stash. Matches saved before this was recorded
    are told apart by what each path wrote."""
    if match.get("via"):
        return match["via"]
    if match.get("kind") == "stash":
        return "stash"
    return "session" if "matched_hash" in match else "store"


def merge_matches(existing: dict, new: dict, replacing) -> dict:
    """A case's saved matches, with those from `replacing` origins replaced
    by `new`, and every other match kept.

    The store check and the session-set check each saved only their own
    matches, over the case's whole list: checking a case against a VICS file
    loaded for the session erased every match the store had made -- with
    its MediaID, Series, Tags and flags -- from the case and from every
    report written after. Each check now replaces what it made before and
    nothing else, so running it again does not pile up copies either.
    """
    replacing = set(replacing)
    merged = {}
    for digest, matches in (existing or {}).items():
        kept = [m for m in matches or [] if match_origin(m) not in replacing]
        if kept:
            merged[digest] = kept
    for digest, matches in (new or {}).items():
        if matches:
            merged.setdefault(digest, []).extend(matches)
    return merged


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Match recovered files against hash sets.")
    ap.add_argument("--scan", metavar="MANIFEST", required=True,
                    help="manifest.json from a run")
    ap.add_argument("--vic", action="append", default=[],
                    help="a Project VIC (VICS) JSON file; repeatable")
    ap.add_argument("--nsrl", action="append", default=[],
                    help="an NSRL set: NSRLFile.txt or an RDS SQLite database; "
                         "repeatable")
    ap.add_argument("--custom", action="append", default=[],
                    help="your own list of hashes; repeatable")
    ap.add_argument("--csv", default="", help="write the results here")
    ap.add_argument("--no-duplicates", action="store_true",
                    help="check each unique file once rather than every copy")
    args = ap.parse_args(argv)

    sets = []
    try:
        for path in args.vic:
            sets.append(load(path, "projectvic"))
        for path in args.nsrl:
            sets.append(load(path, "nsrl"))
        for path in args.custom:
            sets.append(load(path, "custom"))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Could not load a hash set: {exc}", file=sys.stderr)
        return 2

    if not sets:
        print("No hash sets given.", file=sys.stderr)
        return 2

    for hash_set in sets:
        print(f"{hash_set.kind:<11} {hash_set.size:>12,} hashes  {hash_set.name}")
    print()

    result = scan_manifest(args.scan, sets, not args.no_duplicates)
    print(f"Checked  : {result['checked']:,}")
    print(f"Matched  : {result['matched']:,}")
    print(f"Unmatched: {result['unmatched']:,}  "
          f"(unknown, which is not the same as cleared)")
    for name, count in sorted(result["by_set"].items(), key=lambda kv: -kv[1]):
        print(f"   {count:>10,}  {name}")
    if args.csv:
        print(f"\nWrote {write_csv(result, args.csv)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
