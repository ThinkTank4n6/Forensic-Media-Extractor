"""
face_match.py -- the same face, across a case.

Everything else here works on whole files. A hash finds a copy; point
matching finds a copy somebody edited; neither answers the question an
examiner asks after finding one picture that matters: **are there others of
this child?**

A photograph can hold several people, so the unit of this search is one
detected face rather than a file. Each face is described by a few hundred
numbers, and two faces of the same person land near each other even in
different photographs, lighting and years.

Detection is YuNet and the description is SFace, both run through OpenCV's
own interface. Since v2026.11.317 both ship in fme/models/ under their own
licences and are used when Settings names none (fme/engines/models.py checks
each one's SHA-256 first).

**What a result is, and what it is not.**

A score says how alike the model finds two faces. It is **not an
identification**, and nothing here should ever be written down as one.
Siblings score highly. So do strangers who happen to resemble each other,
and so do two pictures of the same person taken ten years apart score
lower than either. The model was trained on adults; faces of children are
the case this is most often used on and the case it is least measured on.

So this orders a case by what is worth looking at next, and that is all it
does. It applies no category, writes no conclusion, and the examiner's eye
decides. Where a threshold is used it is OpenCV's own published figure for
SFace, which is quoted rather than invented, and it is shown with every
result so a reader can see how close the match actually was.
"""

from __future__ import annotations

import math
import os

__version__ = "1.0.0"

# OpenCV's published figures for SFace: two faces of one person usually
# score above this, and two different people usually below. "Usually" is
# the whole point -- it is a sorting aid, not a decision.
SAME_PERSON_COSINE = 0.363
DETECT_SIZE = (320, 320)
MIN_CONFIDENCE = 0.80
MAX_FACES_PER_FILE = 24


def available(detector_path: str = "", recognizer_path: str = "") -> tuple:
    """Whether a face search can run, and what is missing when it cannot."""
    try:
        import cv2
        import numpy  # noqa: F401
    except ImportError:
        return (False, "needs OpenCV and NumPy")
    if not hasattr(cv2, "FaceDetectorYN_create"):
        return (False, "this build of OpenCV has no face detector")
    if not detector_path or not recognizer_path:
        return (False, "no face models are set, and the bundled ones are not "
                       "usable. Settings takes the paths to a YuNet detector "
                       "and an SFace recognizer.")
    for label, path in (("detector", detector_path),
                        ("recognizer", recognizer_path)):
        if not os.path.isfile(path):
            return (False, f"there is no {label} model at {path}")
    try:
        cv2.FaceDetectorYN_create(detector_path, "", DETECT_SIZE)
        cv2.FaceRecognizerSF_create(recognizer_path, "")
    except Exception as exc:  # noqa: BLE001
        return (False, f"a model would not load: {str(exc)[:160]}")
    return (True, "")


_tools = {}
# One picture at a time: setInputSize and detect, and the recognizer's
# feature, keep state on the shared objects, and two requests on the
# server's threads could swap boxes or descriptions (v2026.11.317).
import threading as _threading  # noqa: E402
_use = _threading.Lock()


def model_id(detector_path: str, recognizer_path: str) -> str:
    """Which pair of models found and described the faces kept with a case."""
    from fme.engines import content_search
    return (content_search.model_id(detector_path)[:8] + "_"
            + content_search.model_id(recognizer_path)[:8])


def _detector(path: str):
    import cv2

    from fme.engines.content_search import _file_key
    key = ("det",) + _file_key(path)
    if key not in _tools:
        _tools[key] = cv2.FaceDetectorYN_create(path, "", DETECT_SIZE)
    return _tools[key]


def _recognizer(path: str):
    import cv2

    from fme.engines.content_search import _file_key
    key = ("rec",) + _file_key(path)
    if key not in _tools:
        _tools[key] = cv2.FaceRecognizerSF_create(path, "")
    return _tools[key]


def faces_in(path: str, detector_path: str, recognizer_path: str) -> list:
    """Every face in one picture, with a description of each."""
    import cv2
    import numpy as np

    try:
        with open(path, "rb") as fh:
            blob = np.frombuffer(fh.read(), dtype=np.uint8)
        picture = cv2.imdecode(blob, cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return []
    if picture is None or picture.size == 0:
        return []

    with _use:
        return _faces(picture, detector_path, recognizer_path)


def _faces(picture, detector_path: str, recognizer_path: str) -> list:
    import numpy as np

    height, width = picture.shape[:2]
    detector = _detector(detector_path)
    detector.setInputSize((width, height))
    try:
        _, found = detector.detect(picture)
    except Exception:  # noqa: BLE001 - a picture the detector will not take
        return []
    if found is None:
        return []

    recognizer = _recognizer(recognizer_path)
    faces = []
    for row in found[:MAX_FACES_PER_FILE]:
        confidence = float(row[-1])
        if confidence < MIN_CONFIDENCE:
            continue
        try:
            aligned = recognizer.alignCrop(picture, row)
            vector = recognizer.feature(aligned)
        except Exception:  # noqa: BLE001 - a face too close to the edge
            continue
        numbers = np.asarray(vector).reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(numbers))
        if not norm:
            continue
        x, y, w, h = (int(row[0]), int(row[1]), int(row[2]), int(row[3]))
        faces.append({
            "box": [x, y, w, h],
            "confidence": round(confidence, 3),
            # Where in the picture, as a fraction, so a face can be pointed
            # at whatever size the picture is shown.
            "at": [round(x / max(1, width), 4), round(y / max(1, height), 4),
                   round(w / max(1, width), 4), round(h / max(1, height), 4)],
            "vector": (numbers / norm).tolist(),
        })
    return faces


def similarity(left, right) -> float:
    """How alike two faces are, 0 to 1. Not a probability of anything."""
    if not left or not right or len(left) != len(right):
        return 0.0
    total = sum(a * b for a, b in zip(left, right))
    return max(0.0, min(1.0, total))


def scan(out_dir: str, rows: list, detector_path: str, recognizer_path: str,
         progress=None) -> dict:
    """Find the faces in every picture of a case."""
    ready, why = available(detector_path, recognizer_path)
    if not ready:
        return {"ok": False, "error": why, "by_sha256": {}}

    found, total = {}, 0
    wanted = [row for row in rows if row.get("category") == "image"]
    for index, row in enumerate(wanted, start=1):
        path = os.path.join(out_dir, row.get("out_path")
                            or row.get("rel_path") or "")
        if not os.path.isfile(path):
            continue
        faces = faces_in(path, detector_path, recognizer_path)
        if faces:
            found[row.get("sha256", "")] = faces
            total += len(faces)
        if progress and index % 25 == 0:
            progress(index, len(wanted))

    return {"ok": True, "pictures": len(wanted), "with_faces": len(found),
            "faces": total, "by_sha256": found,
            "caveat": CAVEAT}


CAVEAT = (
    "A score says how alike the model finds two faces. It is not an "
    "identification: siblings score highly, so do strangers who resemble "
    "each other, and two pictures of one person years apart can score low. "
    "The model was trained on adults. This orders what to look at next; it "
    "decides nothing and applies no category."
)


def search(target, library: dict, limit: int = 200,
           above: float = None) -> dict:
    """Every face in the case, ordered by how alike this one it is.

    `library` is {sha256: [face, ...]} from scan(). `target` is one face's
    vector -- a search starts from a face somebody pointed at, not from a
    whole picture, because a picture can hold several people.
    """
    floor = SAME_PERSON_COSINE if above is None else above
    scored = []
    for digest, faces in library.items():
        for position, face in enumerate(faces):
            score = similarity(target, face.get("vector"))
            if score <= 0:
                continue
            scored.append({
                "sha256": digest,
                "face": position,
                "at": face.get("at"),
                "score": round(score, 4),
                # Said for every result, so a reader can see how close it
                # actually was rather than only that it was returned.
                "above_opencv_threshold": score >= SAME_PERSON_COSINE,
            })
    scored.sort(key=lambda row: -row["score"])
    kept = [row for row in scored if row["score"] >= floor][:limit]
    for rank, row in enumerate(kept, start=1):
        row["rank"] = rank
    return {
        "ok": True,
        "results": kept,
        "considered": len(scored),
        "threshold": floor,
        "threshold_note": (
            f"OpenCV publishes {SAME_PERSON_COSINE} as the point above which "
            f"SFace usually puts two faces of one person. Usually is the "
            f"point: it sorts, it does not decide."),
        "caveat": CAVEAT,
    }
