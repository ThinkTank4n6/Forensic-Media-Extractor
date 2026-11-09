"""
content_search.py -- finding pictures of the same thing, not the same file.

Everything else here answers "is this the same picture?". A hash answers it
for a copy, point matching answers it for a copy somebody edited. Neither
answers "are there other pictures of this room, this child, this piece of
furniture, this character" -- and that is the question an examiner asks
most often after finding one picture that matters.

A model answers it by turning each picture into a few hundred numbers that
describe what is in it, so that two photographs of the same subject land
near each other even though they share no pixels. The picture searched for
is compared to every picture in the case by the angle between those
numbers.

**This needs a model.** Since v2026.11.317 DINOv2-small (Apache-2.0) ships
in fme/models/ and is used when Settings names none (fme/engines/models.py,
which checks its SHA-256 first); a path in Settings still wins. The engine
refuses rather than pretending when no model is usable. Anything that
takes an image and returns one vector will work.

**What a result means, and what it does not.** A high score means the
model finds the two pictures alike. It is not an identification of a
person, a place or an object, and nothing here should ever be written down
as one. Two children who resemble each other score highly; so do two rooms
with the same furniture. The ranking is a way to put a hundred thousand
files into an order worth looking at, and the looking is still the
examiner's.

For that reason this reports a **rank and a score**, never a verdict, and
never applies a category.

**How a picture is described** (recipe 2, from GLEAPP's content.py, MIT):
the whole picture is fitted inside the model's square and padded, never
cropped, and the description is the model's summary token and the average
of its patch tokens, each made unit length and joined. Recipe 1 cropped to
the centre square -- a 16:9 photograph lost 44% of its width before the
model saw it -- and averaged the summary token in with the 256 patches.
Measured with the real DINOv2-small on 19 photographs, searched among
their re-framings and 28 distractors: a photograph shown off-centre in a
wide frame, as a screenshot holds one, came in the top three 42% of the
time under recipe 1 (worst rank 60 of 67) and 95% under recipe 2 (worst
4); mean average precision 0.742 to 0.978. Each half of the change helped;
together they helped most. Synthetic framings of a small set, so a
direction, not a figure to quote -- GLEAPP's own measurement on a real case
is in its content.py.

Descriptions are kept with the case under CACHE_NAME, which names the
recipe: ones made by another recipe are ignored and remade, rather than
compared with these and silently scoring nothing.
"""

from __future__ import annotations

import math
import os

__version__ = "2.0.0"

SIZE = 224                      # what the model is fed
BATCH_NOTE = "the first search over a case works out every picture's numbers"

RECIPE = 2
CACHE_NAME = f"content_vectors_v{RECIPE}"

_MEAN = (0.485, 0.456, 0.406)   # ImageNet's, which DINOv2 was trained with
_STD = (0.229, 0.224, 0.225)


# DINOv2-small, which this was written against: what to expect of the file
# so a wrong or truncated one is caught here rather than by a search that
# quietly returns nonsense.
KNOWN_MODELS = {
    "b88d3157590250f1ed413bd41289ce2b4f6c65f3699684eaa913efc2330e2822": {
        "name": "DINOv2-small (Meta AI)",
        "bytes": 88411480,
        "licence": "Apache-2.0",
        "needs_opencv": (5, 0),
    },
}


def model_details(model_path: str) -> dict:
    """What this file is, as far as it can be told from the file itself."""
    import hashlib

    # A regular file only: a device or a pipe has no end to hash to.
    if not os.path.isfile(model_path):
        return {}
    try:
        size = os.path.getsize(model_path)
    except OSError:
        return {}
    digest = hashlib.sha256()
    try:
        with open(model_path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return {}
    found = digest.hexdigest()
    known = KNOWN_MODELS.get(found, {})
    return {"sha256": found, "bytes": size, "recognized": bool(known), **known}


def available(model_path: str = "") -> tuple:
    """Whether a search can run, and what is missing when it cannot."""
    try:
        import cv2
        import numpy  # noqa: F401
    except ImportError:
        return (False, "needs OpenCV and NumPy")
    if not model_path:
        return (False, "no model is set, and the bundled one is not usable. "
                       "Settings takes the path to an ONNX image model.")
    if not os.path.isfile(model_path):
        return (False, f"there is no model at {model_path}")

    # The failure worth naming. OpenCV 4 stops on this model's cubic Resize
    # node with an error about node handling, which tells an examiner
    # nothing about what to do. The fix is a newer OpenCV, so say that.
    details = model_details(model_path)
    needs = details.get("needs_opencv")
    if needs:
        here = tuple(int(part) for part in cv2.__version__.split(".")[:2])
        if here < needs:
            return (False,
                    f"{details.get('name', 'this model')} needs OpenCV "
                    f"{needs[0]}.{needs[1]} or newer to load; this machine "
                    f"has {cv2.__version__}. "
                    f"pip install --upgrade \"opencv-python-headless>=5.0\"")
    try:
        _net(model_path)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "Resize" in message or "handleNode" in message:
            return (False, f"this build of OpenCV ({cv2.__version__}) cannot "
                           f"load that model: it stops on a node the model "
                           f"uses. OpenCV 5.0 or newer loads it.")
        return (False, f"that model would not load: {message[:160]}")
    return (True, "")


_loaded = {}
# One inference at a time per process. A loaded net holds its input between
# setInput and forward, and two searches on the server's threads could each
# set theirs and read the other's answer -- a wrong vector, no error.
import threading as _threading  # noqa: E402
_infer = _threading.Lock()
_digests: dict = {}


def model_id(model_path: str) -> str:
    """A short name for the model that made a description: its SHA-256.

    Descriptions are kept with the case under it, so a case searched with
    one model is never compared with another's (v2026.11.317: a changed
    model found nothing, or compared two models' numbers, silently). Worked
    out once per file, size and time.
    """
    import hashlib

    try:
        st = os.stat(model_path)
    except OSError:
        return ""
    key = (os.path.abspath(model_path), st.st_size, st.st_mtime_ns)
    if key not in _digests:
        digest = hashlib.sha256()
        with open(model_path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        _digests[key] = digest.hexdigest()
    return _digests[key][:16]


def cache_name(model_path: str) -> str:
    """Where a case keeps this model's descriptions."""
    return f"{CACHE_NAME}_{model_id(model_path)}"


def _file_key(path: str):
    """A loaded model's key: its path, size and time. By path alone, a model
    replaced at the same path stayed loaded, and its descriptions were kept
    under the new model's name (v2026.11.319)."""
    try:
        st = os.stat(path)
        return (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    except OSError:
        return (os.path.abspath(path), None, None)


def _net(model_path: str):
    """The model, loaded once per file as it now is."""
    import cv2

    key = _file_key(model_path)
    if key in _loaded:
        return _loaded[key]
    net = cv2.dnn.readNetFromONNX(model_path)
    _loaded[key] = net
    return net


def _prepare(picture):
    """The whole picture, fitted inside SIZE x SIZE, padded with the mean
    colour (zero once normalised), as the model's RGB input."""
    import cv2
    import numpy as np

    height, width = picture.shape[:2]
    scale = SIZE / max(height, width)
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    fitted = cv2.resize(picture, (w, h), interpolation=cv2.INTER_AREA)
    fitted = cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    canvas = np.empty((SIZE, SIZE, 3), np.float32)
    canvas[:] = _MEAN
    top, left = (SIZE - h) // 2, (SIZE - w) // 2
    canvas[top:top + h, left:left + w] = fitted
    normalised = (canvas - np.array(_MEAN, np.float32)) / np.array(_STD, np.float32)
    return np.ascontiguousarray(normalised.transpose(2, 0, 1)[None])


def _vector(out):
    """One unit-length description from what the model returned.

    A transformer like DINOv2 returns one row per token, the summary token
    first: that row and the average of the rest, each unit length, joined.
    A model that returns a single vector is used as it is.
    """
    import numpy as np

    out = np.asarray(out, dtype=np.float32)
    if out.ndim == 3 and out.shape[1] > 1:
        tokens = out[0]
        summary = tokens[0] / (np.linalg.norm(tokens[0]) + 1e-12)
        patches = tokens[1:].mean(axis=0)
        patches = patches / (np.linalg.norm(patches) + 1e-12)
        vector = np.concatenate([summary, patches])
    else:
        vector = out.reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm and vector.size else None


def encode(vector) -> str:
    """A description as it is kept with the case: float16, base64.

    About 2 KB a picture at 768 numbers. As a JSON list of floats it was
    about 15 KB, which over a 100,000-picture case is 1.5 GB of database.
    """
    import base64

    import numpy as np

    return base64.b64encode(np.asarray(vector, np.float16).tobytes()).decode("ascii")


def decode(stored):
    """A kept description back as numbers, or None if it is not one."""
    import base64

    import numpy as np

    if isinstance(stored, str):
        try:
            return np.frombuffer(base64.b64decode(stored), np.float16).astype(np.float32)
        except ValueError:
            return None
    if isinstance(stored, np.ndarray) and stored.size:
        return stored.astype(np.float32).reshape(-1)
    if isinstance(stored, (list, tuple)) and stored:
        return np.asarray(stored, np.float32)
    return None


def describe(path: str, model_path: str):
    """What is in this picture, encoded as kept with the case, or None."""
    import cv2
    import numpy as np

    try:
        with open(path, "rb") as fh:
            blob = np.frombuffer(fh.read(), dtype=np.uint8)
        # IMREAD_COLOR applies the EXIF orientation, so a photograph the
        # phone stored on its side is described the right way up.
        picture = cv2.imdecode(blob, cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None
    if picture is None or picture.size == 0:
        return None
    try:
        prepared = _prepare(picture)
        with _infer:
            net = _net(model_path)
            net.setInput(prepared)
            vector = _vector(net.forward())
    except Exception:  # noqa: BLE001 - a model that will not run on this file
        return None
    return encode(vector) if vector is not None else None


def similarity(left, right) -> float:
    """How alike two descriptions are, 0 to 1: their cosine, below 0 as 0.

    It used to map a negative cosine to (cosine + 1) / 2, so -0.5 scored
    0.25 and ranked above +0.2: further apart, ranked as closer.
    """
    import numpy as np

    a, b = decode(left), decode(right)
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    return float(min(1.0, max(0.0, float(np.dot(a, b)))))


def rank(target, library: dict, limit: int = 200) -> list:
    """Every picture in the case, in the order worth looking at.

    A rank and a score, never a verdict: the model finding two pictures
    alike is not an identification of anybody or anything. One matrix
    product over the case rather than a Python loop per picture.
    """
    import numpy as np

    wanted = decode(target)
    if wanted is None:
        return []
    digests, rows = [], []
    for digest, stored in library.items():
        vector = decode(stored)
        if vector is not None and vector.shape == wanted.shape:
            digests.append(digest)
            rows.append(vector)
    if not rows:
        return []
    scores = np.clip(np.stack(rows) @ wanted, 0.0, 1.0)
    order = np.argsort(-scores, kind="stable")
    ranked = []
    for index in order:
        value = float(scores[index])
        if value <= 0 or len(ranked) >= limit:
            break
        ranked.append({"sha256": digests[index], "score": round(value, 4),
                       "rank": len(ranked) + 1})
    return ranked


def search(target_path: str, model_path: str, library: dict,
           limit: int = 200) -> dict:
    """Rank a case against this picture."""
    ready, why = available(model_path)
    if not ready:
        return {"ok": False, "error": why, "results": []}
    target = describe(target_path, model_path)
    if target is None:
        return {"ok": False, "error": "that picture could not be described "
                                      "by the model", "results": []}
    return {
        "ok": True,
        "results": rank(target, library, limit=limit),
        "caveat": ("A score is how alike the model finds two pictures. It is "
                   "not an identification of a person, a place or an object, "
                   "and no category is applied from it. Two children who "
                   "resemble each other score highly, and so do two rooms "
                   "with the same furniture."),
    }
