"""
streams.py -- a video downloaded as segments, joined back by its manifest.

A DASH or HLS player keeps a video as an initialization segment, many short
media segments and a manifest (.mpd or .m3u8) that lists them in order. An
app that saves a video for offline viewing, a browser's cache, or an
extraction of either leaves those files side by side in one folder. Read
file by file, the folder gave the examiner a few hundred bytes of
initialization segment stored as a "video" that plays nothing, and dropped
every media segment -- which hold all the pictures -- because a segment
begins with a styp box, not ftyp.

This joins each stream the manifest lists from the files beside it, in the
manifest's order, up to the first segment that is missing, and says how far
it got. A video and its sound, which DASH serves as two streams, are put in
one file without re-encoding when the vendored reader can. The joining is
the vendored exoprobe's (fme/vendor), which does the same for ExoPlayer's
cache; this only presents a plain folder to it as a cache whose keys are the
files' own addresses.

Nothing is re-encoded and nothing is invented: every byte written is a byte
of the files on disk, and a stream joined short of its manifest says so.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile

from fme import resources

MANIFEST_EXTS = (".mpd", ".m3u8")
# The keys exoprobe resolves a manifest's segment addresses against. Any
# absolute address works; this one cannot be mistaken for a real one.
BASE = "https://local-folder.invalid/"
# Past this many files a folder is not a stream's download.
MAX_FOLDER_FILES = 20000

LOG = logging.getLogger("fme")


def _count(stats, key: str) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


def _probe():
    vendor = resources.vendor_dir()
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        import exoprobe  # noqa: PLC0415
        return exoprobe
    except Exception:  # noqa: BLE001 - absent, or will not import
        return None


def folders_with_manifests(root: str) -> list:
    """Every folder under root holding a .mpd or .m3u8."""
    found = []
    for here, _dirs, names in os.walk(root):
        if any(n.lower().endswith(MANIFEST_EXTS) for n in names):
            found.append(here)
    return found


def plan(folder: str, stats: dict = None) -> list:
    """The streams the manifests in this folder list, as exoprobe plans them
    for its cache: [{format, manifest, rep, init, segs, listed, handlers}],
    with every name a path.

    A folder whose manifests cannot be planned gives [] -- its files are
    still read one by one -- but it is logged and counted in
    stats["stream_plan_failed"]: it used to give [] in silence, and a
    download that was never joined looked the same as a folder with none.
    """
    probe = _probe()
    if probe is None:
        return []
    try:
        names = sorted(os.listdir(folder))
    except OSError as exc:
        LOG.warning("segmented streams in %s not joined: the folder could not "
                    "be listed (%s)", folder, exc)
        _count(stats, "stream_plan_failed")
        return []
    if len(names) > MAX_FOLDER_FILES:
        LOG.warning("segmented streams in %s not joined: %d files is more than "
                    "a stream's download holds", folder, len(names))
        _count(stats, "stream_plan_failed")
        return []
    items = {}
    for name in names:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            items[path] = {"key": BASE + name, "path": path, "complete": True}
    try:
        streams, _pairs = probe.plan_streams(items)
    except Exception as exc:  # noqa: BLE001 - one malformed manifest is not a fault
        LOG.warning("segmented streams in %s not joined: its manifest could "
                    "not be read (%s: %s); the files are read one by one",
                    folder, type(exc).__name__, exc)
        _count(stats, "stream_plan_failed")
        return []
    return streams


def join(folder: str, scratch: str, stats: dict = None) -> list:
    """Each planned stream written whole into scratch; a video and the audio
    its manifest lists beside it put into one MP4 where that can be done.

    Returns [{path, manifest, format, rep, segments, listed, complete,
    audio_added}] -- one per written file. `listed` is None when the
    manifest's plan did not say how many segments it lists, and such a
    stream is never called complete.
    """
    probe = _probe()
    streams = plan(folder, stats)
    if probe is None or not streams:
        return []
    # A folder of its own inside scratch. The files were named stream_<n>
    # in scratch itself, with n counted per folder, and joined_in calls this
    # once per folder into one scratch: the second folder's stream_0.bin
    # replaced the first's before either was stored, so one download was
    # stored twice under two manifests and the other not at all.
    here = tempfile.mkdtemp(prefix="join_", dir=scratch)
    written = []
    for n, st in enumerate(streams):
        parts = ([st["init"]] if st.get("init") else []) + list(st["segs"])
        dest = os.path.join(here, f"stream_{n}.bin")
        try:
            probe.write_stream(parts, dest)
        except OSError as exc:
            LOG.warning("a stream %s lists could not be joined: %s",
                        st.get("manifest"), exc)
            _count(stats, "stream_write_failed")
            continue
        listed = st.get("listed")
        listed = listed if isinstance(listed, int) and listed > 0 else None
        written.append({"path": dest, "manifest": st["manifest"],
                        "format": st.get("format", ""), "rep": st.get("rep") or {},
                        "segments": len(st["segs"]), "listed": listed,
                        # Complete only against a count the manifest gave:
                        # with none, "all of them" cannot be said.
                        "complete": listed is not None and len(st["segs"]) >= listed,
                        "handlers": st.get("handlers") or [], "audio_added": 0,
                        "parts": parts})

    # DASH serves picture and sound apart. Where one manifest lists a video
    # stream and audio streams, put them in one file, as the player did.
    by_manifest = {}
    for w in written:
        by_manifest.setdefault(w["manifest"], []).append(w)
    for group in by_manifest.values():
        videos = [w for w in group if "vide" in w["handlers"]]
        audios = [w for w in group if w["handlers"] == ["soun"]]
        if len(videos) != 1 or not audios:
            continue
        video = videos[0]
        combined = video["path"] + ".mux.mp4"
        try:
            probe.mux(video["path"], [a["path"] for a in audios], combined)
        except Exception:  # noqa: BLE001 - not combinable: kept apart, as cached
            continue
        shutil.move(combined, video["path"])
        video["audio_added"] = len(audios)
        video["complete"] = video["complete"] and all(a["complete"] for a in audios)
        video["parts"] += [p for a in audios for p in a["parts"]]
        for a in audios:
            a["merged"] = True
    return [w for w in written if not w.get("merged")]


def joined_in(root: str, stats: dict = None) -> list:
    """join() over every folder under root that holds a manifest, in one
    scratch folder the caller removes (returned as the second element)."""
    scratch = tempfile.mkdtemp(prefix="fme_streams_")
    out = []
    for folder in folders_with_manifests(root):
        out += join(folder, scratch, stats)
    return out, scratch
