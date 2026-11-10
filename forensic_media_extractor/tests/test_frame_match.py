"""Finding the video a still came from.

A picture and the video it was taken from share no bytes, so nothing this
program hashes relates them. A frame of that video and the picture, though,
are the same image -- which a perceptual hash can say.

The fixtures here are made with real, varied content, because the failure
mode worth guarding against is the opposite one: flat or near-featureless
frames match everything, and a tool that reports a screenshot for every
video is worse than one that reports none.
"""
import tempfile
import json
import os
import random
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.engines import frame_match

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "frame_match_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

state = frame_match.available()
if not state["ready"]:
    print(f"needs Pillow and OpenCV; skipping ({state})")
    sys.exit(0)

import cv2
import numpy as np

ok = True
source = os.path.join(ROOT, "src")
os.makedirs(source)
random.seed(11)

video_path = os.path.join(source, "clip.avi")
writer = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"MJPG"), 10,
                         (320, 240))
frames = []
for index in range(60):
    frame = np.zeros((240, 320, 3), np.uint8)
    for y in range(0, 240, 20):
        for x in range(0, 320, 20):
            frame[y:y + 20, x:x + 20] = ((x * 3 + index * 7) % 256,
                                         (y * 5 + index * 3) % 256,
                                         (x + y + index) % 256)
    cv2.putText(frame, f"scene {index // 10}", (30, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    writer.write(frame)
    frames.append(frame.copy())
writer.release()

# A screenshot of one moment, a resized and re-compressed copy of the same
# moment (what sharing does to a picture), and something unrelated.
grabbed = frames[25]
cv2.imwrite(os.path.join(source, "screenshot.jpg"), grabbed,
            [cv2.IMWRITE_JPEG_QUALITY, 70])
cv2.imwrite(os.path.join(source, "shared_small.jpg"),
            cv2.resize(grabbed, (160, 120)), [cv2.IMWRITE_JPEG_QUALITY, 60])
unrelated = np.zeros((240, 320, 3), np.uint8)
for y in range(0, 240, 8):
    for x in range(0, 320, 8):
        unrelated[y:y + 8, x:x + 8] = tuple(random.randint(0, 255)
                                            for _ in range(3))
cv2.imwrite(os.path.join(source, "unrelated.jpg"), unrelated,
            [cv2.IMWRITE_JPEG_QUALITY, 90])

run = os.path.join(ROOT, "run")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", source,
                "-o", run], capture_output=True, cwd=PROJECT)
with open(os.path.join(run, "manifest.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
names = {f["sha256"]: os.path.basename(f["out_path"]) for f in manifest["files"]}

# A run has a case as soon as it is reviewed; results are kept with it, so
# the case is opened before the matching rather than after.
from fme.review import casedb

casedb.open_case(run, examiner="Det. C. T.", case_number="DC26-253").close()

result = frame_match.analyze(run)
print(f"videos hashed : {result['hashed']} of {result['videos']}")
print(f"stills matched: {result['stills_matched']}")
ok &= result["hashed"] == 1

matched = {names[digest]: hits for digest, hits in result["matches"].items()}
for name, hits in matched.items():
    best = hits[0]
    print(f"  {name:32} -> {best['name']} frame {best['frame']} "
          f"({best['distance']} bits, {best['confidence']})")

# The screenshot has to find the video it came from.
screenshot = next((n for n in matched if "screenshot" in n), None)
ok &= screenshot is not None
print(f"\n  a screenshot finds its video: {screenshot is not None}")

# And so does a copy that was resized and re-compressed, which is what
# sharing a picture does to it -- the whole reason for using a perceptual
# hash rather than a cryptographic one.
resized = next((n for n in matched if "shared_small" in n), None)
ok &= resized is not None
print(f"  a resized, re-compressed copy finds it too: {resized is not None}")

# The unrelated picture must not match. A tool that answers "yes" for
# everything has told the examiner nothing.
false_positive = next((n for n in matched if "unrelated" in n), None)
ok &= false_positive is None
print(f"  an unrelated picture does not match: {false_positive is None}")

# The distance has to mean something: a true match is far closer than the
# threshold, or the threshold is doing the work rather than the hash.
if screenshot:
    best = matched[screenshot][0]
    ok &= best["distance"] <= frame_match.STRICT_DISTANCE
    print(f"  the match is {best['distance']} bits, well inside "
          f"{frame_match.STRICT_DISTANCE}: "
          f"{best['distance'] <= frame_match.STRICT_DISTANCE}")

# The result is kept with the case, so it survives closing the program.
case = casedb.CaseDB(os.path.join(run, "case.db"))
stored = case.derived("frame_matches")
case.close()
ok &= len(stored) == result["stills_matched"]
print(f"  kept with the case: {len(stored)} still(s)")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
