"""
credentials.py -- Content Credentials (C2PA) a file declares about itself.

A growing number of cameras, phones and editors write a signed record into
the file saying what made it and what was done to it. Every major image
generator writes one too, and says so in a field of its own: a generated
picture declares ``trainedAlgorithmicMedia`` where a photograph declares
``digitalCapture``.

That matters here for one reason above the others. A category for
AI-generated material is otherwise decided entirely by eye, and a file that
states what made it is evidence about that judgment -- not a substitute for
it.

The reading is done by the vendored ``c2pareader`` (MIT, Alexis Brignoni),
which walks seven container formats and the XMP packets beside them. A
first version of this module parsed the containers here instead; it was
replaced because the vendored reader is stricter in the way that counts --
it refused a hand-built manifest whose JUMBF type was not a real C2PA
store, where the version written here accepted it. A reader that accepts
not-quite-C2PA is a reader that will one day call something a credential
that is not one.

**Nothing here is verified.** No signature is checked, no certificate chain
is built, no revocation is looked up. Every signer, time and claim is what
the file states. Three consequences follow, and all three are carried
wherever this appears:

- a manifest can be copied from one file into another by anyone who can
  write bytes,
- a signer's name is a name in the file, not a proven identity,
- and a file with no manifest has been shown nothing at all -- except where
  the walk completed, which is the one case where absence means the file
  holds none in any place this reader looks.
"""

from __future__ import annotations

import os
import sys

__version__ = "2.0.0"

# Beside the code from a checkout; inside the unpacked folder from a
# frozen build, which is somewhere different every time it starts.
from fme import resources

VENDOR_DIR = resources.vendor_dir()

_reader = None
_tried = False


def reader():
    """The vendored C2PA reader, imported on first use."""
    global _reader, _tried
    if _reader is not None or _tried:
        return _reader
    _tried = True
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    try:
        import c2pareader
        _reader = c2pareader
    except Exception:  # noqa: BLE001 - absent is a state, not a failure
        _reader = None
    return _reader


def available() -> bool:
    return reader() is not None


CAVEAT = ("What the file states about itself. No signature was checked and "
          "no certificate chain was built, a manifest can be copied into "
          "another file, and a signer's name here is a name in the file "
          "rather than a proven identity.")


def read(path: str) -> dict:
    """What a file declares, flattened to what a review screen needs."""
    module = reader()
    # An error is not an absence: with no "absence_established" here, scan()
    # took the missing key as True and counted a file it never read as one
    # examined and found to carry no credential.
    if module is None:
        return {"present": False, "absence_established": False,
                "error": "the vendored Content Credentials reader is absent"}
    try:
        found = module.probe(path)
    except Exception as exc:  # noqa: BLE001 - a malformed file is a fact
        return {"present": False, "absence_established": False,
                "error": str(exc)}

    manifests = found.get("manifests") or []
    if not manifests:
        return {
            "present": False,
            "container": found.get("container"),
            # The distinction that makes an absence worth anything: a walk
            # that finished found none, a walk that stopped early found
            # nothing yet.
            "complete": bool(found.get("complete")),
            "absence_established": bool(found.get("complete")),
        }

    # The last manifest is the active one.
    active = manifests[-1]
    sources = []
    for entry in active.get("source_types") or []:
        if isinstance(entry, dict):
            sources.append({"code": entry.get("code", ""),
                            "means": entry.get("label", ""),
                            "ai": bool(entry.get("ai"))})
    signature = active.get("signature") or {}

    return {
        "present": True,
        "container": found.get("container"),
        "complete": bool(found.get("complete")),
        "manifests": len(manifests),
        "claim_generator": active.get("claim_generator", ""),
        "title": active.get("title", ""),
        "software_agents": active.get("software_agents") or [],
        "source_types": sources,
        "says_ai_generated": bool(active.get("ai")),
        "ingredients": len(active.get("ingredients") or []),
        "signer": signature.get("signed_by", ""),
        "issuer": signature.get("issuer", ""),
        "signing_time": signature.get("signing_time", ""),
        "verified": False,
        "caveat": CAVEAT,
    }


def scan(out_dir: str, rows: list, progress=None) -> dict:
    """Read the credentials of every recovered image and video."""
    found, incomplete = {}, 0
    for index, row in enumerate(rows, start=1):
        path = os.path.join(out_dir, row.get("out_path", ""))
        if not os.path.isfile(path):
            continue
        result = read(path)
        if result.get("present"):
            found[row.get("sha256", "")] = result
        elif not result.get("absence_established", False):
            # The file could not be walked to its end, so "no credentials"
            # has not been established for it.
            incomplete += 1
        if progress and index % 50 == 0:
            progress(index, len(rows))
    ai = sum(1 for r in found.values() if r.get("says_ai_generated"))
    return {"checked": len(rows), "with_credentials": len(found),
            "say_ai_generated": ai, "not_established": incomplete,
            "by_sha256": found}
