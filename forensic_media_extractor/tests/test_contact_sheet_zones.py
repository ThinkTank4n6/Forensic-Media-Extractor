"""The contact sheet says which zone a time is in, or that it has none.

Its "Modified" cell was the stored time cut to 19 characters, which cut off
the zone: an NTFS time recorded in UTC printed exactly like a FAT time in the
device's own clock, and a reader in Philadelphia read 18:00 UTC as 6 PM local
when it was 2 PM EDT. Every other export goes through timeutil; this one now
does too.
"""
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import timeutil  # noqa: E402
from fme.exports import report_html  # noqa: E402


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_contact_zones-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    manifest = os.path.join(root, "manifest.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump({"summary": {"image": "test.E01"}, "files": [
            {"out_path": "images/ntfs.jpg", "ext": "jpg", "category": "image",
             "size": 10, "mtime": "2024-07-04T18:00:00+00:00", "engine": "filesystem"},
            {"out_path": "images/fat.jpg", "ext": "jpg", "category": "image",
             "size": 10, "mtime": "2024-07-04T18:00:00", "engine": "filesystem",
             "captured": "2024-07-04T17:59:58"},
        ]}, fh)

    utc = report_html.build(manifest, embed=False)
    found = "2024-07-04 18:00:00 +00:00" in utc
    ok &= found
    print(f"a UTC time keeps its offset when shown in UTC: {found}")
    found = "2024-07-04T18:00:00<" not in utc
    ok &= found
    print(f"no time is printed bare, with its zone cut off: {found}")
    found = utc.count(timeutil.AS_RECORDED) >= 3      # FAT mtime, EXIF, header
    ok &= found
    print(f"zone-less times are labeled as the device's clock: {found}")

    if timeutil.zone("America/New_York") is None:
        print("conversion to America/New_York not checked: no tz database here")
    else:
        local = report_html.build(manifest, embed=False, timezone="America/New_York")
        found = "2024-07-04 14:00:00 -04:00 (EDT)" in local
        ok &= found
        print(f"the same time converts to 14:00 EDT in Philadelphia: {found}")
        found = "2024-07-04 18:00:00 (" + timeutil.AS_RECORDED in local
        ok &= found
        print(f"and the FAT time is not shifted with it: {found}")

    # A suspect's SVG is shown through <img>, where no script runs, and is
    # never linked: a link opened it as a page, scripts and all.
    svg = report_html._tile({"out_path": "images/svg/x.svg", "ext": "svg"},
                            root, False, {})
    jpg = report_html._tile({"out_path": "images/jpg/x.jpg", "ext": "jpg"},
                            root, False, {})
    unlinked = "<a href" not in svg and '<img src="images/svg/x.svg"' in svg
    ok &= unlinked and "<a href" in jpg
    print(f"an SVG is shown but not linked; a JPEG is linked: {unlinked}")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
