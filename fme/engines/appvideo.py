"""
appvideo.py -- video an app streamed, rebuilt from its cache.

An Android app that plays video over the network almost always uses
ExoPlayer, and ExoPlayer does not keep a video as a file. It keeps numbered
pieces -- ``<id>.<position>.<timestamp>.v3.exo`` -- with the name of the
thing they came from in a separate index. A carve over that folder recovers
fragments that will not open and have no name; a file-system walk lists
thousands of ``.exo`` files and tells an examiner nothing.

Joined back together they are the video the person watched, with the
address it came from.

The reading is the vendored ``exoprobe`` (MIT, Alexis Brignoni), which also
joins DASH and HLS segments in the order the cached manifest lists them and
can put a video and its audio into one MP4 without re-encoding.

Two things this is careful about.

**An item joined only as far as its first gap is said to be partial.** A
cache holds what was watched, which is often not the whole file, and a
partial video presented as whole invites a conclusion about what somebody
saw that the bytes do not support.

**Nothing is re-encoded.** The pieces are concatenated and, where tracks
are combined, re-wrapped. Every byte written came out of the cache.
"""

from __future__ import annotations

import os
import sys

__version__ = "1.0.0"

# Beside the code from a checkout; inside the unpacked folder from a
# frozen build, which is somewhere different every time it starts.
from fme import resources

VENDOR_DIR = resources.vendor_dir()

_probe = None
_tried = False


def probe():
    global _probe, _tried
    if _probe is not None or _tried:
        return _probe
    _tried = True
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    try:
        import exoprobe
        _probe = exoprobe
    except Exception:  # noqa: BLE001
        _probe = None
    return _probe


def available() -> bool:
    return probe() is not None


def find(folder: str) -> list:
    """Every ExoPlayer cache under this folder."""
    module = probe()
    if module is None:
        return []
    try:
        return module.find_caches(folder)
    except Exception:  # noqa: BLE001 - a folder that is not one is not a fault
        return []


def rebuild(folder: str, out_dir: str, *, combine: bool = True,
            progress=None) -> dict:
    """Join every cached item under `folder` into `out_dir`.

    Returns one record per item: what it was, where it came from, whether
    what was cached is the whole of it, and the file written.
    """
    module = probe()
    if module is None:
        return {"ok": False,
                "error": "the vendored streamed-video reader is absent",
                "items": []}

    caches = find(folder)
    if not caches:
        return {"ok": True, "caches": 0, "items": [], "written": 0,
                "partial": 0}

    os.makedirs(out_dir, exist_ok=True)
    items, written, partial = [], 0, 0
    for index, cache in enumerate(caches, start=1):
        try:
            records = module.rejoin(cache, out_dir, combine=combine)
        except Exception as exc:  # noqa: BLE001 - one bad cache, not all
            items.append({"cache": getattr(cache, "root", ""),
                          "error": str(exc)})
            continue
        for record in records:
            name = record.get("file") or ""
            path = os.path.join(out_dir, name) if name else ""
            if record.get("status") == "written" and name:
                written += 1

            # Three different things the reader can tell us, and only the
            # first means the file is short: a gap, a recorded length the
            # joined bytes fall short of, or a length that was never
            # recorded -- which says nothing about completeness either way.
            length = record.get("length_recorded")
            joined = int(record.get("bytes") or 0)
            short = (record.get("gap_at") is not None
                     or (length is not None and joined < int(length)))
            if short:
                partial += 1

            items.append({
                "name": name,
                "path": path,
                "key": record.get("key") or "",       # usually the URL
                "key_from": record.get("key_from", ""),
                "status": record.get("status", ""),
                "state": record.get("state", ""),
                "complete": not short,
                "length_known": length is not None,
                "bytes": joined,
                "length": length,
                "pieces": record.get("pieces_total", 0),
                "pieces_joined": record.get("pieces_joined", 0),
                "kind": record.get("kind", ""),
                "cache": record.get("cache_folder", ""),
                "app": record.get("app_folder") or "",
            })
        if progress:
            progress(index, len(caches))

    return {"ok": True, "caches": len(caches), "items": items,
            "written": written, "partial": partial,
            "caveat": ("A cache holds what was watched, which is often not "
                       "the whole file. An item is called short only where "
                       "the pieces have a gap or fall below a length the "
                       "cache recorded; where no length was recorded, "
                       "whether it is whole is not known either way. "
                       "Nothing was re-encoded.")}
