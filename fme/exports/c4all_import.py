"""
c4all_import.py -- reading a C4All report index back in.

This program could write a C4All index and not read one, which made the
exchange one-way: an examiner handed a C4All set by another unit, or
returning to one made years ago, had no way to bring the gradings in.

C4All writes one index beside the files it exported -- ``C4P Index.xml``
for pictures, ``C4M Index.xml`` for movies -- as UTF-16 XML:

    <ReportIndex version="2.0">
        <Image>                                  (<Movie> in a C4M index)
            <path><![CDATA[DVD01(Name)\\01\\]]></path>
            <picture>00000001.JPG</picture>      (<movie> in a C4M index)
            <category>1</category>
            <fullpath><![CDATA[...\\DCIM\\DSCN0001.JPG]]></fullpath>
            <created>1569647772</created>        Unix seconds
            <deleted>18446744073709551615</deleted>   all ones: not deleted
            <hash>...</hash>                     MD5
            <sha1Hash>...</sha1Hash>
            <fileSize>3434508</fileSize>
        </Image>
    </ReportIndex>

Three things this is careful about, because an import that gets them wrong
is worse than no import.

**A category is a claim somebody else made.** It is carried in and
attributed to the index it came from, never silently merged with this
examiner's own decisions. Where a file is already graded here, the
existing grading stands and the difference is reported.

**C4All writes 0 for "nobody has categorized this".** Treating that as
category 0 would mark every ungraded file Non-Pertinent, which is a
decision nobody made.

**The scheme may not be ours.** A number in another unit's index means
whatever their scheme said it meant. The codes are carried as given and
the index is named alongside them, so an examiner can see whose numbering
they are reading.

The structure of the format follows the reader in GLEAPP (MIT, Alexis
Brignoni), which documented it; this is written against that description
rather than copied, because it has to speak to this program's case
database rather than theirs.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Iterator, Optional

from fme.review import casedb

__version__ = "1.0.0"

# The element holding one file, and the child naming the exported copy.
ENTRY_NAME = {"Image": "picture", "Movie": "movie"}
KIND = {"Image": "image", "Movie": "video"}
# A 64-bit field whose all-ones value means "never deleted".
NOT_DELETED = 0xFFFFFFFFFFFFFFFF


@dataclass
class Entry:
    tag: str
    name: str
    rel_path: str
    abs_path: str
    exists: bool
    md5: Optional[str]
    sha1: Optional[str]
    category: Optional[int]
    original_path: Optional[str]
    size: Optional[int]
    created: Optional[float]
    written: Optional[float]
    accessed: Optional[float]
    deleted: Optional[float]


def looks_like_index(path: str) -> bool:
    """An .xml whose root is ReportIndex, read from the first few KB."""
    if os.path.splitext(path)[1].lower() != ".xml":
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    for encoding in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            if "<ReportIndex" in head.decode(encoding, "ignore"):
                return True
        except (UnicodeError, LookupError):
            continue
    return False


def _number(text) -> Optional[int]:
    try:
        return int((text or "").strip())
    except (TypeError, ValueError):
        return None


def _when(text) -> Optional[float]:
    value = _number(text)
    if value is None or value <= 0 or value >= NOT_DELETED:
        return None
    return float(value)


def read(path: str) -> Iterator[Entry]:
    """Stream the entries of an index.

    Streamed rather than loaded: an index of a large case is tens of
    megabytes, and iterparse reads the XML declaration itself, so the
    UTF-16 encoding needs no special handling.
    """
    folder = os.path.dirname(os.path.abspath(path))
    for _, element in ET.iterparse(path, events=("end",)):
        child = ENTRY_NAME.get(element.tag)
        if child is None:
            continue
        name = (element.findtext(child) or "").strip()
        if not name:
            element.clear()
            continue

        # The index is written on Windows; its backslashes have to be split
        # on every platform, not only there.
        relative = PureWindowsPath(
            (element.findtext("path") or "").strip(), name).as_posix()
        full = os.path.normpath(os.path.join(folder, relative))
        category = _number(element.findtext("category"))
        original = (element.findtext("fullpath") or "").strip() or None

        yield Entry(
            tag=element.tag,
            name=name,
            rel_path=relative,
            abs_path=full,
            exists=os.path.isfile(full),
            md5=((element.findtext("hash") or "").strip().lower() or None),
            sha1=((element.findtext("sha1Hash") or "").strip().lower()
                  or None),
            # C4All writes 0 for an entry nobody has categorized. Reading
            # that as category 0 would mark every ungraded file
            # Non-Pertinent -- a decision nobody made.
            category=category if category else None,
            original_path=original,
            size=_number(element.findtext("fileSize")),
            created=_when(element.findtext("created")),
            written=_when(element.findtext("written")),
            accessed=_when(element.findtext("accessed")),
            deleted=_when(element.findtext("deleted")),
        )
        element.clear()


def summarize(path: str) -> dict:
    """What an index holds, without changing anything.

    Shown before an import because the examiner is about to take in
    somebody else's gradings, and should see whose and how many first.
    """
    kinds, categories = {}, {}
    total = present = graded = 0
    for entry in read(path):
        total += 1
        kinds[KIND.get(entry.tag, entry.tag)] = kinds.get(
            KIND.get(entry.tag, entry.tag), 0) + 1
        if entry.exists:
            present += 1
        if entry.category is not None:
            graded += 1
            categories[entry.category] = categories.get(entry.category, 0) + 1
    return {
        "index": os.path.abspath(path),
        "entries": total,
        "files_present": present,
        "files_missing": total - present,
        "graded": graded,
        "by_category": categories,
        "by_kind": kinds,
        "note": ("The categories are the numbers this index records. They "
                 "mean whatever the scheme of the unit that wrote it meant, "
                 "which may not be this one's."),
    }


def apply_to_case(case, files, path: str, *, overwrite: bool = False,
                  index_region: str = None) -> dict:
    """Bring an index's gradings into a case, by hash.

    Matched on MD5 and SHA-1, because the exported copy and the file in
    this case are the same bytes under different names. Nothing is matched
    by file name: two cases full of IMG_0001.JPG would collide instantly.

    An existing grading in this case is left alone unless `overwrite` is
    asked for, and every difference is reported either way. An import that
    quietly replaces an examiner's own decisions with another unit's is
    not an import, it is a loss.
    """
    index_name = os.path.basename(path)
    # An index records bare numbers in the scheme of the unit that wrote it,
    # and the examiner is the one who knows which that was. Taken as this
    # case's own, a Canadian "3 Other" landed as US 3, CGI Animation
    # (v2026.11.317). The index's scheme must be stated and must be the
    # case's; nothing is carried across schemes.
    from fme.review import regions
    if index_region is None or not regions.is_region(index_region):
        raise ValueError(
            "Say which grading scheme the unit that wrote this index used. "
            "Its categories are bare numbers, and the same number means "
            "different things in different schemes.")
    mine = getattr(case, "region", "US")
    if regions.normalize(index_region) != mine:
        raise ValueError(
            f"This index was graded under {regions.get(index_region).label}; "
            f"this case is graded under {regions.get(mine).label}. The same "
            f"number means different things in the two, so nothing was "
            f"brought in.")
    by_md5, by_sha1 = {}, {}
    # Hashes the index itself grades two ways. The last entry won, and the
    # examiner was told of no disagreement; these are left ungraded and
    # listed instead.
    torn: dict = {}
    for entry in read(path):
        if entry.category is None:
            continue
        for table, digest in ((by_md5, entry.md5), (by_sha1, entry.sha1)):
            if not digest:
                continue
            had = table.get(digest)
            if had is not None and had.category != entry.category:
                torn.setdefault(digest, {had.category}).add(entry.category)
            table[digest] = entry

    # The case holds decisions; the file list is the run's. Both are
    # needed: one to match on hashes, the other to know what is already
    # decided here.
    decided = case.decisions() if hasattr(case, "decisions") else {}
    # A C4All index grades in the scheme its own case used. A code this
    # case's scheme does not have is not guessed at: it is counted and left.
    known = ({c["code"] for c in case.categories()}
             if hasattr(case, "categories") else None)
    not_in_scheme: dict = {}

    applied, already, disagreed, unmatched = 0, 0, [], 0
    index_conflicts: list = []
    for row in files:
        digest_md5 = (row.get("md5") or "").lower()
        digest_sha1 = (row.get("sha1") or "").lower()
        entry = by_md5.get(digest_md5) or by_sha1.get(digest_sha1)
        if entry is None:
            continue

        digest = row.get("sha256", "")
        if not digest:
            continue        # nothing to hang a decision on
        conflict = torn.get(digest_md5) or torn.get(digest_sha1)
        if conflict:
            index_conflicts.append({"sha256": digest,
                                    "the_index": sorted(conflict),
                                    "index": index_name})
            continue
        existing = decided.get(digest) or {}
        mine = existing.get("category")
        if mine == getattr(case, "uncategorized", casedb.UNCATEGORIZED):
            mine = None     # a row that says "not decided" is not a decision
        if mine is not None and mine != entry.category:
            disagreed.append({
                "sha256": row.get("sha256", ""),
                "this_case": mine,
                "the_index": entry.category,
                "index": index_name,
            })
            if not overwrite:
                already += 1
                continue
        elif mine is not None:
            already += 1
            continue

        if known is not None and entry.category not in known:
            not_in_scheme[entry.category] = not_in_scheme.get(entry.category, 0) + 1
            continue
        # Recorded as the index's, never as the examiner's own judgment: the
        # docstring promised that, and the call carried no attribution.
        # Unless overwriting was asked for, only a file still undecided when
        # the write is made: the examiner may grade it while this runs.
        wrote = case.set_category([digest], entry.category,
                                  by=f"hash set: C4All index {index_name}",
                                  only_if_undecided=not (overwrite and mine is not None))
        if not wrote:
            already += 1
            continue
        applied += 1

    for entry in by_md5.values():
        if not entry.exists:
            unmatched += 1

    return {
        "index": index_name,
        "applied": applied,
        "already_graded_here": already,
        "disagreements": disagreed,
        "index_disagrees_with_itself": index_conflicts,
        "entries_with_no_file": unmatched,
        # {code: files} graded with a code this case's scheme does not have.
        "not_in_this_scheme": not_in_scheme,
        "note": ("Gradings from " + index_name + ". Where this case had "
                 "already graded a file, the existing grading stands and "
                 "the difference is listed rather than resolved."),
    }
