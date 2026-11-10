"""Finding the same picture after somebody edited it.

A perceptual hash describes the whole frame, so changing the frame defeats
it: a crop, a rotation, a caption bar, a paste into a screenshot. Those are
exactly what happens to a picture as it is traded, so a tool that only
finds re-saved copies finds the easy half.

This matches what is inside the picture and accepts a match only when the
matched points agree on one transform. The numbers below are the point of
the test: what it finds, and -- just as important -- that it refuses
unrelated pictures. A matcher that finds everything is worth nothing.
"""
import tempfile
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.engines import edit_match

ready, why = edit_match.available()
if not ready:
    print(f"skipping: {why}")
    sys.exit(0)

import cv2
import numpy as np

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "edit_match_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True

rng = np.random.default_rng(11)
base = np.zeros((600, 800, 3), np.uint8)
for _ in range(240):
    colour = tuple(int(v) for v in rng.integers(40, 235, 3))
    cv2.circle(base, tuple(int(v) for v in rng.integers(0, 800, 2)),
               int(rng.integers(8, 60)), colour, -1)
for _ in range(60):
    cv2.line(base, tuple(int(v) for v in rng.integers(0, 800, 2)),
             tuple(int(v) for v in rng.integers(0, 600, 2)), (255, 255, 255), 2)
cv2.putText(base, "EVIDENCE", (60, 320), cv2.FONT_HERSHEY_SIMPLEX, 3,
            (255, 255, 255), 6)

original = os.path.join(ROOT, "original.jpg")
cv2.imwrite(original, base, [cv2.IMWRITE_JPEG_QUALITY, 92])


def screenshot(picture):
    frame = np.full((1200, 720, 3), 28, np.uint8)
    frame[300:810, 20:700] = cv2.resize(picture, (680, 510))
    cv2.putText(frame, "9:41", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                (255, 255, 255), 2)
    return frame


def captioned(picture):
    out = picture.copy()
    cv2.rectangle(out, (0, 0), (800, 90), (0, 0, 0), -1)
    cv2.putText(out, "look at this", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6,
                (255, 255, 255), 4)
    return out


edits = {
    "re-saved at low quality": cv2.imdecode(
        cv2.imencode(".jpg", base, [cv2.IMWRITE_JPEG_QUALITY, 40])[1], 1),
    "resized to 45%": cv2.resize(base, (360, 270)),
    "cropped by 30%": base[90:510, 120:680],
    "rotated 7 degrees": cv2.warpAffine(
        base, cv2.getRotationMatrix2D((400, 300), 7, 1.0), (800, 600)),
    "rotated 90 degrees": cv2.rotate(base, cv2.ROTATE_90_CLOCKWISE),
    "mirrored": cv2.flip(base, 1),
    "bordered": cv2.copyMakeBorder(base, 60, 60, 60, 60,
                                   cv2.BORDER_CONSTANT, value=(20, 20, 20)),
    "captioned": captioned(base),
    "pasted into a screenshot": screenshot(base),
    "made grayscale": cv2.cvtColor(cv2.cvtColor(base, cv2.COLOR_BGR2GRAY),
                                   cv2.COLOR_GRAY2BGR),
    "brightened": cv2.convertScaleAbs(base, alpha=1.25, beta=25),
}

print("the same picture, edited:")
found = 0
for name, picture in edits.items():
    path = os.path.join(ROOT, name.replace(" ", "_") + ".jpg")
    cv2.imwrite(path, picture, [cv2.IMWRITE_JPEG_QUALITY, 88])
    result = edit_match.confirm(original, path)
    if result.get("match"):
        found += 1
    detail = []
    if result.get("mirrored"):
        detail.append("mirrored")
    if result.get("rotation"):
        detail.append(f"{result['rotation']:+.0f} degrees")
    print(f"  {name:26} {'found' if result.get('match') else 'MISSED':>7}  "
          f"{result.get('points', 0):>4} point(s)  {' '.join(detail)}")

ok &= found == len(edits)
print(f"\n  {found} of {len(edits)} edits found")

# The half that matters more. A matcher that says yes to everything would
# score perfectly above and be worse than useless: every unrelated picture
# it returns is a file an examiner has to look at to dismiss.
print("\nunrelated pictures, which must be refused:")
wrong = 0
for index in range(8):
    other = np.zeros((600, 800, 3), np.uint8)
    for _ in range(240):
        colour = tuple(int(v) for v in rng.integers(40, 235, 3))
        cv2.circle(other, tuple(int(v) for v in rng.integers(0, 800, 2)),
                   int(rng.integers(8, 60)), colour, -1)
    path = os.path.join(ROOT, f"unrelated_{index}.jpg")
    cv2.imwrite(path, other, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if edit_match.confirm(original, path).get("match"):
        wrong += 1
ok &= wrong == 0
print(f"  {wrong} of 8 wrongly matched")

# A picture with no detail cannot be matched this way, and says so rather
# than guessing.
flat = np.full((600, 800, 3), 128, np.uint8)
flat_path = os.path.join(ROOT, "flat.jpg")
cv2.imwrite(flat_path, flat)
featureless = edit_match.confirm(flat_path, flat_path)
ok &= not featureless.get("match")
print(f"\n  a flat picture is refused rather than guessed at: "
      f"{not featureless.get('match')} "
      f"({featureless.get('why', '')})")

print("\nsearching a case rather than one pair:")
library, paths = {}, {}
for name in os.listdir(ROOT):
    path = os.path.join(ROOT, name)
    prints = edit_match.fingerprints(path)
    if prints:
        library[name] = prints
        paths[name] = path
result = edit_match.find_copies(original, library, paths)
names = {m["sha256"] for m in result["matches"]}
ok &= result["ok"] and len(names) >= len(edits) - 1
print(f"  {len(library)} file(s) fingerprinted, "
      f"{result['considered']} shortlisted, "
      f"{len(result['matches'])} confirmed as the same picture")
ok &= not any(n.startswith("unrelated") for n in names)
print(f"  no unrelated file among them: "
      f"{not any(n.startswith('unrelated') for n in names)}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
