"""
setaside.py -- which recovered files are not for grading.

One judge for the gallery (fme/web/media_gui.py), which hides these and
leaves them off the category bar and Not yet reviewed, and for the reports
(fme/exports/report.py), which list them like every other file but say how
many of their Uncategorized are these -- so a case reviewed to the end does
not read "0 Uncategorized" on screen and "4,441 Uncategorized" on paper
without a word about the difference (v2026.11.335).

Set aside:

  * a container -- a file other media came out of: an archive, a document, a
    cache -- as the run marked it. Not a picture, video or audio file that
    something was found inside by a byte scan (its own EXIF thumbnail, a
    video's cover art, recorded "<path>::+<offset>"): cases made before
    v2026.11.334 marked those as containers, hiding the full picture;
  * a SQLite database or a binary property list recovered with "also recover
    artifacts": nobody gives a database a category. Zips and PDFs recovered
    the same way are still for grading.
"""

from __future__ import annotations

__version__ = "1.0.0"

NOT_GRADED = ("sqlite", "plist")
MEDIA = ("image", "video", "audio")


def not_graded(f: dict) -> bool:
    """A recovered database or property list."""
    return f.get("category") == "other" and (f.get("ext") or "").lower() in NOT_GRADED


def media_holding_a_find(rows) -> set:
    """Source paths something was found inside by a byte scan."""
    inside = set()
    for f in rows:
        where = f.get("source_path") or ""
        if "::+" in where:
            inside.add(where.split("::+", 1)[0])
    return inside


def is_set_aside(f: dict, shown_again=frozenset()) -> bool:
    """Is this file not for grading? `shown_again` is media_holding_a_find()
    of every row of the same case."""
    container = bool(f.get("container")) and not (
        f.get("category") in MEDIA and (f.get("source_path") or "") in shown_again)
    return container or not_graded(f)
