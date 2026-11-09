"""The image models that ship with the program, and which one a search uses.

Until v2026.11.317 no model shipped: content search and face search each
asked Settings for a path, and on an examiner's machine that had never been
set the two features were present, documented and unusable. GLEAPP (MIT,
Copyright (c) 2026 charpy4n6, Alexis Brignoni) bundles three published
models under their own licences; the same three files now ship in
``fme/models/``, beside those licences:

* DINOv2-small (Meta AI, Apache-2.0) -- content search;
* YuNet 2023mar (Shiqi Yu, MIT) -- finding faces;
* SFace 2021dec (OpenCV Zoo, Apache-2.0) -- describing a face.

**Each is checked by SHA-256 before it is used.** A truncated or replaced
file would load and return numbers, and a search over it would quietly rank
a case by nonsense. One that does not match is not used, and the search says
why.

A path set in Settings still wins: a unit that has validated a different
model, or a newer one, keeps using it.

**Everything these models produce is an investigative lead only** (LEAD).
A score says how alike a model finds two pictures or two faces. It is not
an identification and never applies a category; the interface labels every
result with LEAD so no reader takes it for more.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading

LEAD = "Investigative lead only"

# kind -> (file, sha256, bytes, what it is, licence file)
BUNDLED = {
    "content": ("dinov2_small.onnx",
                "b88d3157590250f1ed413bd41289ce2b4f6c65f3699684eaa913efc2330e2822",
                88411480, "DINOv2-small (Meta AI), Apache-2.0", "LICENSE-dinov2"),
    "face_detector": ("face_detection_yunet_2023mar.onnx",
                      "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
                      232589, "YuNet 2023mar (Shiqi Yu), MIT", "LICENSE-yunet"),
    "face_recognizer": ("face_recognition_sface_2021dec.onnx",
                        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
                        38696353, "SFace 2021dec (OpenCV Zoo), Apache-2.0",
                        "LICENSE-sface"),
}


def folder() -> str:
    """Where the bundled models are: beside the package, or in a frozen
    build's unpacked data."""
    frozen = getattr(sys, "_MEIPASS", "")
    if frozen and os.path.isdir(os.path.join(frozen, "fme", "models")):
        return os.path.join(frozen, "fme", "models")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "models")


_checked: dict = {}
_lock = threading.Lock()


def check(kind: str) -> tuple:
    """(path, "") for a bundled model that is present and intact, or
    ("", why not). The hash is worked out once per file and size and time."""
    name, digest, size, _what, _licence = BUNDLED[kind]
    path = os.path.join(folder(), name)
    try:
        stat = os.stat(path)
    except OSError:
        return ("", f"the bundled {kind.replace('_', ' ')} model is not "
                    f"installed ({name})")
    if stat.st_size != size:
        return ("", f"the bundled {name} is {stat.st_size:,} bytes, not "
                    f"{size:,}: incomplete or replaced, so it is not used")
    key = (path, stat.st_size, stat.st_mtime_ns)
    with _lock:
        if key not in _checked:
            found = hashlib.sha256()
            with open(path, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    found.update(block)
            _checked[key] = found.hexdigest() == digest
    if not _checked[key]:
        return ("", f"the bundled {name} does not match its published "
                    f"SHA-256, so it is not used")
    return (path, "")


def resolve(kind: str, configured: str = "") -> tuple:
    """The model a search uses: (path, where it came from, why not).

    A path set in Settings first; otherwise the bundled one, if intact.
    """
    configured = (configured or "").strip()
    if configured:
        return (configured, "settings", "")
    path, why = check(kind)
    return (path, "bundled" if path else "", why)


def describe() -> list:
    """Each bundled model, for `fme doctor` and the manual."""
    out = []
    for kind, (name, digest, size, what, licence) in BUNDLED.items():
        path, why = check(kind)
        out.append({"kind": kind, "file": name, "what": what, "sha256": digest,
                    "bytes": size, "licence": licence, "ready": bool(path),
                    "why": why})
    return out
