"""Face-level search, and what time it was.

**Faces.** Everything else here works on whole files, and a file is the
wrong unit for the question an examiner asks after finding one picture that
matters: are there others of this child? A photograph can hold several
people, so the unit is one detected face.

What this must never do is overstate itself. A score is how alike a model
finds two faces, not an identification: siblings score highly, strangers
who resemble each other score highly, and two pictures of one person years
apart can score low. The threshold used is OpenCV's own published figure,
quoted rather than invented, and every result carries its score so a reader
can see how close it actually was.

**Time.** Timestamps are stored in UTC, which is right, and read in a local
zone, which is also right. Before this the report printed a header naming a
zone and timestamps in UTC underneath it — a misleading line in a document
that may go to a court. Daylight saving is the whole difficulty: a fixed
offset puts half the year an hour out, and an hour is the difference
between somebody being at work and being at home.
"""
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.engines import face_match, timeutil

ok = True

print("what the face search will not claim")
ready, why = face_match.available("", "")
ok &= not ready and "no face models" in why
print(f"  with no models it refuses: {why[:52]}…")

ok &= abs(face_match.SAME_PERSON_COSINE - 0.363) < 1e-9
print(f"  the threshold is OpenCV's published figure, not one of ours: "
      f"{face_match.SAME_PERSON_COSINE}")

for phrase in ("not an identification", "siblings", "trained on adults",
               "decides nothing"):
    there = phrase in face_match.CAVEAT
    ok &= there
    print(f'  the caveat says "{phrase}": {there}')

# The ranking can be checked without photographs of anybody.
import math


def unit(values):
    length = math.sqrt(sum(v * v for v in values))
    return [v / length for v in values]


target = unit([1, 0, 0, 0])
library = {
    "same": [{"vector": target, "at": [0, 0, 1, 1]}],
    "alike": [{"vector": unit([0.95, 0.3, 0, 0]), "at": [0, 0, 1, 1]}],
    "unrelated": [{"vector": unit([0, 1, 0, 0]), "at": [0, 0, 1, 1]}],
}
found = face_match.search(target, library)
names = [row["sha256"] for row in found["results"]]
ok &= names[:2] == ["same", "alike"]
ok &= "unrelated" not in names
print(f"  ordering: {names}")

# Every result says how close it was, and whether that cleared the
# threshold -- not just that it came back.
ok &= all("score" in row and "above_opencv_threshold" in row
          for row in found["results"])
print(f"  every result carries its score: True")

# A picture can hold several people, so a search starts from one face.
two_people = {"group": [{"vector": target, "at": [0.1, 0.1, 0.2, 0.2]},
                        {"vector": unit([0, 1, 0, 0]),
                         "at": [0.6, 0.1, 0.2, 0.2]}]}
one = face_match.search(target, two_people)
ok &= len(one["results"]) == 1 and one["results"][0]["face"] == 0
print(f"  a picture with two people matches on the face, not the file: "
      f"{len(one['results'])} of 2")

print("\nwhat time it was")
# The case a fixed offset gets wrong.
summer = timeutil.show("2026-07-15T18:30:00+00:00", "America/New_York")
winter = timeutil.show("2026-12-15T18:30:00+00:00", "America/New_York")
ok &= "14:30:00 -04:00" in summer and "13:30:00 -05:00" in winter
print(f"  July    : {summer}")
print(f"  December: {winter}")

# Every shape a stored timestamp arrives in.
for value in (1793275800, "2026-10-29T14:30:00Z", "2026-10-29 14:30:00",
              "2026:10:29 14:30:00"):
    ok &= bool(timeutil.show(value, "UTC"))
print(f"  every stored shape is read: True")

# And anything unreadable is blank, never a guess: an invented timestamp
# in a report is worse than a missing one.
for rubbish in ("", None, "not a date", 0):
    ok &= timeutil.show(rubbish, "UTC") == ""
print(f"  anything unreadable is left blank: True")

print("\nthe report's header agrees with its rows")
source = os.path.join("/tmp", "protoreal", "run")
if os.path.isdir(source):
    from fme.exports import report

    for name, expect_offset in (("America/New_York", "-0"),
                                ("UTC", "+00:00"),
                                ("Mars/Olympus", "+00:00")):
        dest = os.path.join("/tmp", "tz_test_" + name.replace("/", "_"))
        shutil.rmtree(dest, ignore_errors=True)
        written = report.build(source, dest, header={"case_number": "TZ"},
                               formats=["html"], timezone=name)
        html = open(written["written"][0]["path"], encoding="utf-8").read()
        header = re.search(r"Times shown in</td><td>([^<]*)", html)
        modified = re.search(r"<td>Modified</td><td>([^<]*)", html)
        stated = header.group(1) if header else ""
        shown = modified.group(1) if modified else ""
        agrees = expect_offset in shown
        ok &= agrees
        print(f"  {name:17} header: {stated[:48]}")
        print(f"  {'':17} a file: {shown}")
        if name == "Mars/Olympus":
            # A zone this machine does not know must not be claimed in the
            # header while UTC is printed underneath it.
            honest = "not a zone this machine knows" in stated
            ok &= honest
            print(f"  {'':17} and says so rather than claiming it: {honest}")
else:
    print("  (no fixture run available; skipped)")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
