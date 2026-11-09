"""
edit_match.py -- finding the same picture after somebody edited it.

A perceptual hash finds a picture that was re-saved, resized or slightly
recompressed. It does not find one that was cropped, rotated, mirrored,
captioned, bordered, or pasted into a screenshot -- and those are exactly
what happens to a picture as it is traded. A hash describes the whole
frame, so changing the frame changes the hash.

This looks at what is *inside* the picture instead. Distinctive points are
found in both images, matched to each other, and then the match is only
accepted if the matched points agree on one geometric transform -- the same
move, scale and rotation for all of them. Random points between two
unrelated photographs never agree on one transform, which is what makes
this refuse rather than guess.

Two stages, because one picture against a hundred thousand cannot be done
by matching points to each one:

1. **Shortlist.** Cheap fingerprints over thumbnails, several of them,
   because each fails differently: a difference hash follows structure, a
   colour signature follows palette, and a centre-weighted hash survives a
   border or a caption bar that moves everything inward.

2. **Confirmation.** Points matched one to one between the two thumbnails,
   then a transform fitted by RANSAC. A match is accepted only when enough
   points survive the fit and they cover enough of the picture.

A mirrored copy is found by searching the flipped image as well: a flip is
not a rotation, so no single transform covers both.

**What it does not do.** It finds the same picture, not the same subject.
Two different photographs of one child are not a match here and should not
be: that is a judgment about people, not about pixels, and a tool that
blurred the two would be inviting an examiner to treat a resemblance as an
identity.
"""

from __future__ import annotations

import math
import os

__version__ = "1.0.0"

THUMB = 480               # thumbnails are matched at this size
SHORTLIST = 150           # candidates each view keeps
# Set from measurement, not by feel. Over a fixture of eleven edits of one
# picture and eight unrelated pictures of the same kind: every true match
# kept at least 58 points through the transform, and the worst coincidence
# kept 17. A threshold of 12 accepted two of the eight unrelated pictures;
# 25 sits in the gap with room either side.
#
# Erring high is deliberate. A missed copy is a file an examiner still sees
# in the gallery and still grades. A false match is a file they are told is
# the same picture, which is a worse error and a harder one to notice.
MIN_POINTS = 25           # matched points that must survive the transform
MIN_COVER = 0.12          # of either picture, by the area those points span
RATIO = 0.75              # Lowe's ratio test


def available() -> tuple:
    """Whether this can run here, and why not when it cannot."""
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return (False, "needs OpenCV and NumPy: "
                       "pip install opencv-python-headless numpy")
    try:
        import cv2
        cv2.SIFT_create()
    except Exception:  # noqa: BLE001
        return (False, "this build of OpenCV has no SIFT")
    return (True, "")


def _thumbnail(path: str):
    """A grayscale thumbnail, or None where the file will not decode."""
    import cv2
    import numpy as np

    try:
        with open(path, "rb") as fh:
            blob = np.frombuffer(fh.read(), dtype=np.uint8)
        picture = cv2.imdecode(blob, cv2.IMREAD_GRAYSCALE)
    except Exception:  # noqa: BLE001 - a file that will not decode is a fact
        return None
    if picture is None or picture.size == 0:
        return None
    height, width = picture.shape[:2]
    if max(height, width) > THUMB:
        scale = THUMB / float(max(height, width))
        picture = cv2.resize(picture, (max(1, int(width * scale)),
                                       max(1, int(height * scale))),
                             interpolation=cv2.INTER_AREA)
    return picture


def fingerprints(path: str) -> dict:
    """The cheap views used to shortlist. Each fails differently.

    One fingerprint means one way to be defeated. A difference hash follows
    structure and survives recompression; a coarse colour signature follows
    the palette and survives a crop that keeps the subject; a hash of the
    middle half ignores whatever was added around the edges, which is what
    a caption bar, a border or a screenshot does.
    """
    import cv2
    import numpy as np

    small = _thumbnail(path)
    if small is None:
        return {}

    def dhash(picture, size=8):
        grid = cv2.resize(picture, (size + 1, size),
                          interpolation=cv2.INTER_AREA)
        bits = grid[:, 1:] > grid[:, :-1]
        value = 0
        for bit in bits.flatten():
            value = (value << 1) | int(bit)
        return value

    height, width = small.shape[:2]
    middle = small[height // 4:height * 3 // 4, width // 4:width * 3 // 4]
    if middle.size == 0:
        middle = small

    # A coarse signature of brightness over a 4x4 grid: the shape of the
    # picture's tones, which a crop keeps and a different picture does not.
    cells = cv2.resize(small, (4, 4), interpolation=cv2.INTER_AREA)
    signature = tuple(int(v) >> 4 for v in cells.flatten())

    return {
        "whole": dhash(small),
        "middle": dhash(middle),
        "signature": signature,
        "shape": (height, width),
    }


def _distance(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def _signature_distance(left, right) -> int:
    return sum(abs(a - b) for a, b in zip(left, right))


def shortlist(target: dict, library: dict, keep: int = SHORTLIST) -> list:
    """Candidates worth matching points against, by each view in turn."""
    if not target:
        return []
    picked = {}
    for name, score in (("whole", None), ("middle", None)):
        ranked = sorted(
            ((_distance(target[name], entry[name]), digest)
             for digest, entry in library.items() if name in entry),
            key=lambda pair: pair[0])
        for distance, digest in ranked[:keep]:
            picked[digest] = min(picked.get(digest, 999), distance)

    ranked = sorted(
        ((_signature_distance(target["signature"], entry["signature"]), digest)
         for digest, entry in library.items() if "signature" in entry),
        key=lambda pair: pair[0])
    for distance, digest in ranked[:keep]:
        picked.setdefault(digest, 64)

    return [digest for digest, _ in sorted(picked.items(),
                                           key=lambda pair: pair[1])]


def _points(picture):
    import cv2

    detector = cv2.SIFT_create(nfeatures=300)
    keypoints, descriptors = detector.detectAndCompute(picture, None)
    return keypoints, descriptors


def confirm(left_path: str, right_path: str) -> dict:
    """Are these the same picture? Decided by whether the points agree.

    Matched points are fitted to one transform. Points that do not fit it
    are thrown away, and what is left has to be numerous enough and spread
    widely enough over the picture. Two unrelated photographs produce
    matches that scatter, and scattered points fit no single transform.
    """
    import cv2
    import numpy as np

    left = _thumbnail(left_path)
    right = _thumbnail(right_path)
    if left is None or right is None:
        return {"match": False, "why": "one of them would not decode"}

    left_keys, left_desc = _points(left)
    if left_desc is None or len(left_desc) < MIN_POINTS:
        return {"match": False, "why": "too little detail to match on",
                "featureless": True}

    best = {"match": False, "points": 0, "cover": 0.0, "mirrored": False}
    for mirrored in (False, True):
        sample = cv2.flip(right, 1) if mirrored else right
        right_keys, right_desc = _points(sample)
        if right_desc is None or len(right_desc) < MIN_POINTS:
            continue

        matcher = cv2.BFMatcher()
        pairs = matcher.knnMatch(left_desc, right_desc, k=2)
        # Lowe's ratio test: a point whose best match is barely better than
        # its second best has not been identified, it has been guessed.
        good = [a for a, b in (p for p in pairs if len(p) == 2)
                if a.distance < RATIO * b.distance]
        if len(good) < MIN_POINTS:
            continue

        source = np.float32([left_keys[m.queryIdx].pt
                             for m in good]).reshape(-1, 1, 2)
        target = np.float32([right_keys[m.trainIdx].pt
                             for m in good]).reshape(-1, 1, 2)
        # A partial affine fit allows move, scale and rotation and nothing
        # else -- the transforms an edit actually applies. A full homography
        # would also allow perspective, which lets unrelated points agree.
        matrix, inliers = cv2.estimateAffinePartial2D(
            source, target, method=cv2.RANSAC, ransacReprojThreshold=4.0,
            maxIters=3000, confidence=0.995)
        if matrix is None or inliers is None:
            continue
        kept = int(inliers.sum())
        if kept < MIN_POINTS:
            continue

        fitted = source[inliers.ravel() == 1].reshape(-1, 2)
        span_x = float(fitted[:, 0].max() - fitted[:, 0].min())
        span_y = float(fitted[:, 1].max() - fitted[:, 1].min())
        cover = (span_x * span_y) / float(left.shape[0] * left.shape[1])

        scale = math.hypot(matrix[0, 0], matrix[0, 1])
        rotation = math.degrees(math.atan2(matrix[0, 1], matrix[0, 0]))
        if kept > best["points"]:
            best = {"match": kept >= MIN_POINTS and cover >= MIN_COVER,
                    "points": kept, "cover": round(cover, 3),
                    "mirrored": mirrored,
                    "scale": round(scale, 3),
                    "rotation": round(rotation, 1),
                    "why": ("the matched points agree on one transform"
                            if cover >= MIN_COVER
                            else "the points agree but cover too little of "
                                 "the picture")}
    return best


def find_copies(target_path: str, library: dict, paths: dict,
                limit: int = 40, progress=None) -> dict:
    """Every file in the case that is this picture, however it was edited.

    `library` is the fingerprints of the case by SHA-256, `paths` the file
    for each. The shortlist keeps this to a few hundred comparisons rather
    than one per file in the case.
    """
    ready, why = available()
    if not ready:
        return {"ok": False, "error": why, "matches": []}

    target = fingerprints(target_path)
    if not target:
        return {"ok": False, "error": "that file would not decode",
                "matches": []}

    candidates = shortlist(target, library)
    matches = []
    for index, digest in enumerate(candidates, start=1):
        path = paths.get(digest)
        if not path or not os.path.isfile(path) or os.path.samefile(
                path, target_path) if os.path.isfile(path or "") else True:
            continue
        found = confirm(target_path, path)
        if found.get("match"):
            matches.append({"sha256": digest, **found})
            if len(matches) >= limit:
                break
        if progress and index % 25 == 0:
            progress(index, len(candidates))

    matches.sort(key=lambda m: -m["points"])
    return {"ok": True, "considered": len(candidates), "matches": matches}
