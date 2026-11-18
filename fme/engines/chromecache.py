"""
chromecache.py -- video Chrome on Android kept in its cache, rebuilt.

Chrome (and every Chromium browser on Android) keeps what it downloads in a
Simple Cache: one file per entry, ``<16 hex digits>_0``, holding the address,
the HTTP response and the body, and ``_s`` files for ranges. A video a web
player streamed is cached as MP4 fragments -- ``moof`` and ``mdat`` boxes
with no ``moov`` -- which no player opens one at a time. File by file, a run
saw cache entries and nothing that played.

The vendored ``chromiumcacheprobe`` (MIT; Alexis Brignoni, recovery engine by
Forensik.IT GmbH) finds those fragments, groups them by the address they came
from and writes each run of them as an ordinary MP4. Two things it is careful
about, which this keeps:

**The sample bytes are the cache's.** Only the container around them is
written. Every sample's source file, offset and size is in the probe's
provenance, and its SHA-256 of each source is checked again at the end: a
source that changed during the run fails the run rather than giving clips.

**A rebuilt header is said to be rebuilt.** When the cache no longer holds the
original ``moov``, the H.264 settings are taken from the samples themselves,
and the clip carries ``video_header_reconstructed``. That goes into the note
an examiner reads.

Cached video shows the browser fetched it, not that anyone watched it. The
note says so.
"""

from __future__ import annotations

import os
import re
import sys

from fme import resources

__version__ = "1.0.0"

# A Simple Cache entry's file name: sixteen hex digits, then _0, _1 or _s.
ENTRY = re.compile(r"^[0-9a-f]{16}_[01s]$")
# Past this many entry files a folder is read anyway, but not rebuilt here:
# the probe hashes every source twice, and this runs inside an extraction.
MAX_ENTRIES = 200000

_probe = None
_tried = False


def probe():
    global _probe, _tried
    if _probe is not None or _tried:
        return _probe
    _tried = True
    vendor = resources.vendor_dir()
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        import chromiumcacheprobe  # noqa: PLC0415
        _probe = chromiumcacheprobe
    except Exception:  # noqa: BLE001 - absent, or will not import
        _probe = None
    return _probe


def available() -> bool:
    return probe() is not None


def find(root: str) -> list:
    """Every folder under root that holds Simple Cache entries, with the
    entry files in it: [(folder, [paths])]."""
    found = []
    for here, _dirs, names in os.walk(root):
        entries = [os.path.join(here, n) for n in names if ENTRY.match(n)]
        if entries:
            found.append((here, sorted(entries)))
    return found


def rebuild(root: str, scratch: str, stats: dict = None) -> list:
    """Rebuild the video in every Simple Cache under root into scratch.

    Returns one record per clip: {path, source, url, kind, warnings,
    reconstructed, seconds, width, height, audio}. A cache the probe could
    not finish gives nothing and is counted in stats["chrome_cache_failed"]
    -- its entries are still read one by one by the folder walk.
    """
    module = probe()
    if module is None:
        return []
    caches = find(root)
    if not caches:
        return []
    files = []
    for folder, entries in caches:
        if len(entries) > MAX_ENTRIES:
            if stats is not None:
                stats["chrome_cache_skipped"] = stats.get("chrome_cache_skipped", 0) + 1
            continue
        files += [(path, os.path.relpath(path, root).replace(os.sep, "/"))
                  for path in entries]
    if not files:
        return []
    try:
        out, manifest = module.recover_files(files, scratch)
    except Exception:  # noqa: BLE001 - one bad cache is not a failed run
        if stats is not None:
            stats["chrome_cache_failed"] = stats.get("chrome_cache_failed", 0) + 1
        return []
    if manifest.get("status") != "completed":
        if stats is not None:
            stats["chrome_cache_failed"] = stats.get("chrome_cache_failed", 0) + 1
        return []
    # The probe names each source by an id; its manifest says which cache
    # file and which address the id is.
    known = manifest.get("sources") or {}
    clips = []
    for clip in manifest.get("clips", []):
        path = os.path.join(str(out), clip.get("file", ""))
        if not os.path.isfile(path):
            continue
        warnings = list(clip.get("warnings") or [])
        sources = sorted({s.get("source", "")
                          for t in clip.get("tracks", [])
                          for s in t.get("source_samples", [])} - {""})
        if not sources and clip.get("source"):
            sources = [clip["source"]]
        urls = sorted({(known.get(s) or {}).get("url", "") for s in sources} - {""})
        group = clip.get("resource_group", "")
        url = group.split("url:", 1)[1] if "url:" in group else (urls[0] if urls else "")
        sources = [(known.get(s) or {}).get("path") or s for s in sources]
        clips.append({
            "path": path,
            "kind": clip.get("kind", "rebuilt"),
            "url": url,
            "sources": sources,
            "warnings": warnings,
            "reconstructed": "video_header_reconstructed" in warnings,
            "seconds": clip.get("video_seconds"),
            "width": clip.get("width"), "height": clip.get("height"),
            "audio": bool(clip.get("audio")),
        })
    return clips


def note_for(clip: dict) -> str:
    """What the examiner reads about a rebuilt clip."""
    if clip.get("kind") == "complete_mp4_carved":
        what = "a complete MP4 found whole in Chrome's cache, copied byte for byte"
    else:
        what = (f"rebuilt from MP4 fragments in Chrome's cache "
                f"({len(clip.get('sources') or [])} cache file(s)); the sample "
                f"bytes are the cache's, only the container was written")
    bits = [what]
    if clip.get("url"):
        bits.append(f"fetched from {clip['url']}")
    if clip.get("reconstructed"):
        bits.append("the original MP4 header was not in the cache, so its H.264 "
                    "settings were taken from the samples themselves")
    other = [w for w in clip.get("warnings") or []
             if w != "video_header_reconstructed"]
    if other:
        bits.append("warnings: " + ", ".join(other))
    bits.append("cached video shows the browser fetched it, not that anyone "
                "watched it")
    return "; ".join(bits)
