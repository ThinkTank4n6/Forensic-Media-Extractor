"""
galleryquery.py -- the gallery's filter, on the server, answer for answer.

The gallery decides what to show in the browser (app.js: passesBase,
passesClassification, passesFacets, matchesSearch, collapseStacks), which
means the browser holds every file in the case -- 46 MB of JSON per open on a
55,000-file case, about 165 MB at 200,000 (the audit document, item 6). Moving that
decision to the server is what lets the page hold one page of files.

This is a port, not a redesign, and it is held to that: `tests/
test_gallery_parity.py` runs the page's own visible() and this module over
the same case for hundreds of filter combinations and requires identical
lists, in identical order, and identical facet counts. A filter that
disagreed with the page would hide files from an examiner without saying so,
which is the one failure a review tool cannot have. Change one side, change
the other, and the parity test says whether they still agree.

JavaScript semantics are kept deliberately: `truthy` below is JavaScript's
truthiness (an empty list is true there and false in Python), and a field
that is absent and a field that is null are both "not there", as `!== null
&& !== undefined` makes them in app.js.
"""

from __future__ import annotations

import math
import re
from fme.engines import timeutil

__version__ = "1.0.0"

UNCATEGORIZED = 8      # casedb.UNCATEGORIZED; the page reads it from /api/case

SIZE_BANDS = [
    (0, 100 * 1024, "under 100 KB"),
    (100 * 1024, 1024 * 1024, "100 KB – 1 MB"),
    (1024 * 1024, 10 * 1024 * 1024, "1 – 10 MB"),
    (10 * 1024 * 1024, 100 * 1024 * 1024, "10 – 100 MB"),
    (100 * 1024 * 1024, 1024 * 1024 * 1024, "100 MB – 1 GB"),
    (1024 * 1024 * 1024, math.inf, "over 1 GB"),
]

MISSING = "\u0000"     # the facet value that means "the file carries nothing"


def truthy(value) -> bool:
    """JavaScript's idea of true: [] and {} are true, NaN is false."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True


def present(value) -> bool:
    """`value !== null && value !== undefined`."""
    return value is not None


def _s(value) -> str:
    """`(value || "")`."""
    return str(value) if truthy(value) else ""


def year(value, zone: str = ""):
    """The year a time falls in, seen from `zone` -- app.js year(), exactly.

    A time that carries a zone is converted first: a video's capture time is
    UTC, and its first four characters put a 9 PM Dec 31 clip in the next
    year. A time with no zone is the device's clock and keeps its own year.
    """
    if not (truthy(value) and isinstance(value, str) and len(value) >= 4):
        return None
    if _ZONED.search(value):
        when = timeutil.parse(value)
        if when is not None and when.tzinfo is not None:
            where = timeutil.zone(zone) or timeutil.UTC
            return str(when.astimezone(where).year)
    return value[:4]


_ZONED = re.compile(r"(?:[zZ]|[+-]\d\d:?\d\d)$")


def has_exif(f: dict) -> bool:
    return (truthy(timeutil.stored(f, "captured")) or truthy(f.get("camera_make"))
            or truthy(f.get("camera_model")) or truthy(f.get("artist"))
            or truthy(f.get("body_serial_present")) or truthy(f.get("city"))
            or present(f.get("latitude")))


def _file_type(f):
    category = f.get("category")
    if category == "image":
        return ["Animated image" if truthy(f.get("animated")) else "Still image"]
    if category == "video":
        return ["Video"]
    if category == "audio":
        return ["Audio"]
    return ["Other"]


def _faces(f):
    faces = f.get("faces")
    if faces is None or (isinstance(faces, (int, float)) and faces < 0):
        return None
    return "none found" if faces == 0 else ("one" if faces == 1 else "more than one")


def _size_band(f):
    size = f.get("size")
    if not isinstance(size, (int, float)) or isinstance(size, bool):
        return None
    for low, high, label in SIZE_BANDS:
        if low <= size < high:
            return label
    return None


def _metadata(f):
    out = ["Has metadata" if has_exif(f) else "No metadata"]
    if present(f.get("latitude")):
        out.append("Has GPS")
    return out


# The page's FACETS table, in the same order, with the same values.
FACETS = [
    ("file_type", _file_type),
    ("ext", lambda f: _s(f.get("ext")).lower()),
    ("skin_band", lambda f: f.get("skin_band") if truthy(f.get("skin_band")) else None),
    ("faces", _faces),
    ("size_band", _size_band),
    ("deleted", lambda f: "Deleted" if truthy(f.get("deleted")) else "Not deleted"),
    ("metadata", _metadata),
    ("captured_year", lambda f, z="": year(timeutil.stored(f, "captured"), z)),
    ("crtime_year", lambda f, z="": year(timeutil.stored(f, "crtime"), z)),
    ("mtime_year", lambda f, z="": year(timeutil.stored(f, "mtime"), z)),
    ("atime_year", lambda f, z="": year(timeutil.stored(f, "atime"), z)),
    ("artist", lambda f: f.get("artist")),
    ("camera_make", lambda f: f.get("camera_make")),
    ("camera_model", lambda f: f.get("camera_model")),
    ("body_serial", lambda f: f.get("body_serial")),
    ("city", lambda f: f.get("city")),
    ("state", lambda f: f.get("state")),
    ("country", lambda f: f.get("country")),
]
_FACET = dict(FACETS)
# The facets whose value depends on the zone the page shows times in.
_IN_ZONE = frozenset({"captured_year", "crtime_year", "mtime_year", "atime_year"})


def facet_values(key: str, f: dict, zone: str = "") -> list:
    value = _FACET[key](f, zone) if key in _IN_ZONE else _FACET[key](f)
    if value is None or value == "":
        return []
    return [v for v in value if truthy(v)] if isinstance(value, list) else [value]


def matches_search(f: dict, query: str, decisions: dict) -> bool:
    if not query:
        return True
    fields = {
        "name": _s(f.get("name")).lower(),
        "path": _s(f.get("source_path")).lower(),
        "md5": _s(f.get("md5")).lower(),
        "sha1": _s(f.get("sha1")).lower(),
        "sha256": _s(f.get("sha256")).lower(),
        "cam": " ".join([_s(f.get("camera_make")), _s(f.get("camera_model"))]).lower(),
        "note": _s((decisions.get(f.get("sha256")) or {}).get("note")).lower(),
    }
    for term in [t for t in re.split(r"\s+", query) if t]:
        colon = term.find(":")
        if colon > 0:
            field, value = term[:colon], term[colon + 1:]
            if field in fields:
                if value not in fields[field]:
                    return False
                continue
        if not any(term in v for v in fields.values()):
            return False
    return True


def is_known_software(f: dict) -> bool:
    return any(_s(hit.get("kind")) == "nsrl" for hit in (f.get("hash_sets") or [])
               if isinstance(hit, dict))


class State:
    """What the page is filtering by, as the page sends it."""

    def __init__(self, raw: dict, uncategorized: int = UNCATEGORIZED):
        self.filter = raw.get("filter") or "all"
        self.cat = str(raw.get("catFilter") if raw.get("catFilter") is not None else "")
        self.flags = list(raw.get("flagFilter") or [])
        self.facets = {k: set(v) for k, v in (raw.get("facetFilter") or {}).items()}
        self.query = (raw.get("search") or "").strip().lower()
        self.show_containers = bool(raw.get("showContainers"))
        self.hide_known = bool(raw.get("hideKnownSoftware"))
        self.stack = bool(raw.get("stackSimilar"))
        similar = raw.get("similarOnly")
        self.similar_only = set(similar) if similar is not None else None
        self.uncategorized = uncategorized
        # The page's display zone, which its year facets are counted in.
        self.zone = str(raw.get("timeZone") or "")


def passes_base(f: dict, st: State, decisions: dict) -> bool:
    if truthy(f.get("container")) and not st.show_containers:
        return False
    if st.hide_known and is_known_software(f):
        return False
    if not matches_search(f, st.query, decisions):
        return False
    flt = st.filter
    decision = decisions.get(f.get("sha256"))
    if flt == "all":
        return True
    if flt == "deleted":
        return truthy(f.get("deleted"))
    if flt in ("carve", "database", "folder", "embedded"):
        return f.get("engine") == flt
    if flt == "gps":
        return present(f.get("latitude"))
    if flt == "auto":
        return truthy((decision or {}).get("auto"))
    if flt == "needs-review":
        return (not truthy(decision) or truthy(decision.get("auto"))
                or decision.get("category") == st.uncategorized)
    return f.get("category") == flt


def category_of(f: dict, decisions: dict, st: State):
    decision = decisions.get(f.get("sha256"))
    return decision["category"] if decision else st.uncategorized


def passes_classification(f: dict, st: State, decisions: dict) -> bool:
    if st.similar_only is not None and f.get("rel_path") not in st.similar_only:
        return False
    if st.cat != "" and str(category_of(f, decisions, st)) != st.cat:
        return False
    if st.flags:
        carried = (decisions.get(f.get("sha256")) or {}).get("flags") or []
        if any(flag not in carried for flag in st.flags):
            return False
    return True


def passes_facets(f: dict, st: State, except_key=None) -> bool:
    for key, chosen in st.facets.items():
        if key == except_key or not chosen or key not in _FACET:
            continue
        values = facet_values(key, f, st.zone)
        if not (any(v in chosen for v in values) if values else MISSING in chosen):
            return False
    return True


def passes_all(f, st, decisions, except_key=None) -> bool:
    return (passes_base(f, st, decisions) and passes_classification(f, st, decisions)
            and passes_facets(f, st, except_key))


def visible(files: list, decisions: dict, st: State) -> list:
    shown = [f for f in files if passes_all(f, st, decisions)]
    if not st.stack:
        return shown
    seen, out = set(), []
    for f in shown:
        group = f.get("similar_group")
        if not truthy(group):
            out.append(f)
        elif group not in seen:
            seen.add(group)
            out.append(f)
    return out


def facet_counts(files: list, decisions: dict, st: State) -> dict:
    """{facet: {"counts": {value: n}, "missing": n}}, as renderFilters counts:
    each facet against every other filter, so a number is what clicking gives.

    The chips, search and classification do not depend on which facet is
    being counted, so they are decided once per file rather than once per
    file per facet: eighteen passes over a million files took 17 seconds.
    """
    base = [f for f in files if passes_base(f, st, decisions)
            and passes_classification(f, st, decisions)]
    active = [k for k, chosen in st.facets.items() if chosen and k in _FACET]
    out = {key: {"counts": {}, "missing": 0} for key, _ in FACETS}
    for f in base:
        # Which facet filters this file fails; it counts for a facet when it
        # fails none of the *other* chosen ones.
        failed = [k for k in active if not passes_facets_one(f, st, k)] if active else []
        if len(failed) > 1:
            continue
        for key, _ in FACETS:
            if failed and failed[0] != key:
                continue
            entry = out[key]
            values = facet_values(key, f, st.zone)
            if not values:
                entry["missing"] += 1
            for v in values:
                entry["counts"][v] = entry["counts"].get(v, 0) + 1
    return out


def passes_facets_one(f: dict, st: State, key: str) -> bool:
    """Whether the file passes the one chosen facet `key`."""
    chosen = st.facets.get(key)
    if not chosen or key not in _FACET:
        return True
    values = facet_values(key, f, st.zone)
    return any(v in chosen for v in values) if values else MISSING in chosen


# ---------------------------------------------------------------------------
# What the page asks of the server once it no longer holds the case
# (v2026.11.309). The page held every file and answered these itself: the
# count beside each category, how many files a toggle hides, every file a
# stacked tile stands for, which page comes after a held one. With a case
# too large to send, each is one pass here instead.
# ---------------------------------------------------------------------------

def aggregates(files: list, decisions: dict, st: State) -> dict:
    """The counts the page draws around the gallery, in one pass:

    case_total     every file in the case
    by_category    every file by its category (the category bar)
    classify       {by_category, flags}: files passing the chips and the
                   search, by category; and by tag within the category
                   showing (the Classify pane, as renderClassification counts)
    known_software files a known-good set recognizes (the NSRL toggle)
    containers     files other media came out of
    groups         look-alike groups (the stack toggle)
    """
    by_category, classify_cat, flags = {}, {}, {}
    known = boxes = 0
    groups = set()
    for f in files:
        code = category_of(f, decisions, st)
        by_category[code] = by_category.get(code, 0) + 1
        if is_known_software(f):
            known += 1
        if truthy(f.get("container")):
            boxes += 1
        if truthy(f.get("similar_group")):
            groups.add(f.get("similar_group"))
        if not passes_base(f, st, decisions):
            continue
        classify_cat[code] = classify_cat.get(code, 0) + 1
        if st.cat != "" and str(code) != st.cat:
            continue
        for flag in (decisions.get(f.get("sha256")) or {}).get("flags") or []:
            flags[flag] = flags.get(flag, 0) + 1
    return {"case_total": len(files), "by_category": by_category,
            "classify": {"by_category": classify_cat, "flags": flags},
            "known_software": known, "containers": boxes, "groups": len(groups)}


def group_members(files: list, groups) -> list:
    """Every file's SHA-256 in these look-alike groups: what a stacked tile
    stands for, so grading it grades all of them."""
    wanted = {g for g in groups if truthy(g)}
    return sorted({f.get("sha256") for f in files
                   if f.get("similar_group") in wanted and f.get("sha256")})


def offset_after(shown: list, files: list, paths) -> int:
    """Where the files after a held page start, in the filtered list.

    Not simply the next page: grading a held page may have dropped its files
    out of the filter, moving everything after them up. The page after is
    the one holding the first file that still matches and comes after the
    last held one in the case's own order -- pageAfterPinned, exactly.
    """
    order = case_order(files)
    last = max((order.get(p, -1) for p in paths), default=-1)
    for index, f in enumerate(shown):
        if order.get(f.get("rel_path"), -1) > last:
            return index
    return max(0, len(shown) - 1)


def by_paths(files: list, paths) -> list:
    """These files, as they stand now, in the order given: a held page."""
    lookup = {f.get("rel_path"): f for f in files}
    return [lookup[p] for p in paths if p in lookup]


# (the list, its length, its order), replaced whole. The list itself is
# held, not its id(): an id is unique only among live objects, so once a
# run's list was collected the next could get the same id and length and be
# answered with the old run's order. And it was a dict cleared and then
# refilled in place, so a thread reading between the two got a KeyError;
# rebinding one tuple is a single step no reader can see half done.
_ORDER = (None, -1, {})


def case_order(files: list) -> dict:
    """{rel_path: position} for this list, made once per list. The job's list
    is replaced, never edited, so its identity and length name it."""
    global _ORDER
    held, length, order = _ORDER
    if held is files and length == len(files):
        return order
    order = {f.get("rel_path"): i for i, f in enumerate(files)}
    _ORDER = (files, len(files), order)
    return order


class Cache:
    """The filtered list for the last few states, so paging through a large
    case is a slice rather than a pass over every file each time.

    Keyed by the list's identity and length, the decisions' version, and the
    state as the page sent it: a new run, a grading or a new filter each
    make a new key, so nothing stale is ever served. Each entry holds the
    list it was made from and a hit must be that same list: id() alone is
    reused once a list is collected, and a new run's list could then be
    served the old one's results.
    """

    def __init__(self, size: int = 24):
        import collections
        import threading
        self._size = size
        self._lock = threading.Lock()
        self._items = collections.OrderedDict()

    def visible(self, files: list, decisions: dict, version, raw: dict,
                st: State) -> list:
        return self._get("visible", files, version, raw,
                         lambda: visible(files, decisions, st))

    def aggregates(self, files: list, decisions: dict, version, raw: dict,
                   st: State) -> dict:
        return self._get("counts", files, version, raw,
                         lambda: aggregates(files, decisions, st))

    def facets(self, files: list, decisions: dict, version, raw: dict,
               st: State) -> dict:
        return self._get("facets", files, version, raw,
                         lambda: facet_counts(files, decisions, st))

    def _get(self, kind: str, files: list, version, raw: dict, compute):
        import json
        key = (kind, id(files), len(files), version,
               json.dumps(raw, sort_keys=True, default=str))
        with self._lock:
            entry = self._items.get(key)
            if entry is not None and entry[0] is files:
                self._items.move_to_end(key)
                return entry[1]
        value = compute()
        with self._lock:
            self._items[key] = (files, value)
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return value


CACHE = Cache()
