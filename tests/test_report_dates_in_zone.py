"""The report's summary dates evidence the way its rows do.

The rows show each capture time in the zone the case chose. The summary's
"Capture dates" range and its by-year chart cut the stored string instead,
and a video's capture time is MP4's, which is UTC: a clip taken at 9 PM on
December 31 in Philadelphia was counted on January 1, in the next year, by
the same report whose row for it said December 31 at 21:00 EST.
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import timeutil  # noqa: E402
from fme.exports import report  # noqa: E402


def rows():
    base = {"category_name": "Uncategorized", "flag_names": "", "size": 10,
            "out_path": "x.mp4", "category": 5}
    return [dict(base, captured="2025-01-01T02:00:00+00:00"),   # video, mvhd, UTC
            dict(base, captured="2024-12-30T15:00:00"),         # photo, camera clock
            dict(base, captured="")]                            # nothing recorded


def capture_dates(zone):
    report._SHOWN_IN["zone"] = zone
    return next((value for title, value, _ in report._highlights({"rows": rows()})
                 if title == "Capture dates"), "")


def main() -> int:
    ok = True
    if timeutil.zone("America/New_York") is None:
        print("America/New_York not checked: no tz database here")
    else:
        shown = capture_dates("America/New_York")
        found = shown == "2024-12-30 to 2024-12-31"
        ok &= found
        print(f"in Eastern, the 9 PM Dec 31 video is dated Dec 31 ({shown}): {found}")
        chart = report._summary_block({"rows": rows()}, category_colors={})
        when = chart[chart.find("When they were taken"):]
        found = "2024" in when and "2025" not in when
        ok &= found
        print(f"and the by-year chart counts it in 2024, not 2025: {found}")

    shown = capture_dates("")
    found = shown == "2024-12-30 to 2025-01-01"
    ok &= found
    print(f"in UTC it is Jan 1, as UTC says ({shown}): {found}")

    found = report._shown_day({"captured": "2024-12-30T15:00:00"}, "captured") == "2024-12-30"
    ok &= found
    print(f"a camera-clock time keeps the day the camera recorded: {found}")
    found = report._shown_day({"captured": "not a time"}, "captured") == "" and report._shown_day({}, "captured") == ""
    ok &= found
    print(f"an unreadable or empty time is left out, not guessed: {found}")

    report._SHOWN_IN["zone"] = ""
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
