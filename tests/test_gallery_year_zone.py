"""The gallery's year filters count a time in the zone the page shows it in.

A video's capture time is UTC (MP4), and the year facets took the first four
characters of the stored string. Filtering the gallery to 2024 hid a clip
taken at 9 PM on December 31 in Philadelphia, whose details pane said
2024-12-31 21:00 EST. The gallery rows also cut file-system times to 19
characters, so an NTFS time in UTC lost its zone before the page saw it.
The page's own year() is held to this one by tests/test_gallery_parity.py.
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import timeutil  # noqa: E402
from fme.review import galleryquery as gq  # noqa: E402


def main() -> int:
    ok = True
    video = {"captured": "2025-01-01T02:00:00+00:00", "mtime": "2025-01-01T02:00:05+00:00"}
    camera = {"captured": "2025-01-01T02:00:00", "mtime": ""}

    if timeutil.zone("America/New_York") is None:
        print("America/New_York not checked: no tz database here")
    else:
        east = gq.State({"timeZone": "America/New_York",
                         "facetFilter": {"captured_year": ["2024"]}})
        found = gq.passes_facets(video, east)
        ok &= found
        print(f"seen from Eastern, the 9 PM Dec 31 video is in 2024: {found}")
        found = not gq.passes_facets(camera, east)
        ok &= found
        print(f"a camera-clock time keeps the year it recorded: {found}")
        counts = gq.facet_counts([video, camera], {}, gq.State({"timeZone": "America/New_York"}))
        found = counts["captured_year"]["counts"] == {"2024": 1, "2025": 1}
        ok &= found
        print(f"and the Filters pane counts them that way: {found}")

    utc = gq.State({"facetFilter": {"captured_year": ["2025"]}})
    found = gq.passes_facets(video, utc)
    ok &= found
    print(f"with no zone sent, UTC, as before: {found}")
    found = gq.year("not a time but long", "America/New_York") == "not "
    ok &= found
    print(f"text that is not a zoned time is cut as before: {found}")

    from fme.web import media_gui
    entry = media_gui._job_file_entry({
        "out_path": "videos/mp4/a.mp4", "ext": "mp4", "category": "video",
        "engine": "filesystem", "size": 1, "deleted": False, "sha256": "x",
        "md5": "y", "source_path": "/DCIM/a.mp4", "offset": None, "fs_type": "NTFS",
        "mtime": video["mtime"], "atime": video["mtime"], "crtime": video["mtime"]})
    found = all(entry[k].endswith("+00:00") for k in ("mtime", "atime", "crtime"))
    ok &= found
    print(f"the gallery row keeps the zone of a UTC file time: {found}")

    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
