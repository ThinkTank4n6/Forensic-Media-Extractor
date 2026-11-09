"""
frame_match.py -- finding the video a still came from.

A picture and the video it was taken from are, to every hash this program
computes, two unrelated files: one is a JPEG, the other is an MP4, and their
bytes have nothing in common. But a frame of that video and the still *are*
the same picture, and a perceptual hash says so.

So each video's key frames are hashed the same way stills are, and the
hashes kept with the case. Then:

* a still can find the video it was taken from, or the video somebody
  screenshotted;
* a video can be matched to another video that shares footage, frame for
  frame;
* a still that matches a frame of a video already graded tells an examiner
  what they are looking at before they open it.

Two cautions that belong with the result rather than in a footnote. A match
is **the same picture to the eye**, not the same file: a frame re-encoded,
cropped slightly or resized still matches, which is the point, and so does a
different photograph of the same flat wall, which is not. And a video is
represented by a dozen frames out of thousands, so a still from a moment
between them will not match at all -- a miss here is weaker evidence than a
hit.

Needs Pillow for the hashing and OpenCV for the frames.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

from fme.engines import similarity, video_frames

__version__ = "1.0.0"

# A frame is a lossy render of a lossy stream, so it is a little further from
# a still of the same moment than two stills of each other would be. Measured
# on re-encoded frames: exact matches come in at 0-2 bits, a re-encode at
# 2-6, and unrelated material rarely under 18.
DEFAULT_DISTANCE = 10
STRICT_DISTANCE = 6


def available() -> dict:
    return {"hashing": similarity.PILLOW,
            "frames": video_frames.available(),
            "ready": similarity.PILLOW and video_frames.available()}


def hash_frames(run_dir: str, records: list, progress=None,
                cancel=None) -> dict:
    """Hash every key frame of every video in a run.

    Frames the run already extracted are reused; a video with none has them
    made now, which is the slow part. The hashes are keyed by the video's
    own SHA-256, so they stay meaningful if the media is moved.
    """
    if not similarity.PILLOW:
        raise RuntimeError(similarity.UNAVAILABLE)

    videos = [r for r in records if r.get("category") == "video"]
    found, decoded, failed = {}, 0, 0
    for index, record in enumerate(videos, start=1):
        if cancel is not None and cancel():
            break
        digest = record.get("sha256") or ""
        path = os.path.join(run_dir, record.get("out_path", ""))
        if not digest or not os.path.isfile(path):
            continue

        frames = video_frames.existing(run_dir, digest)
        if not frames:
            result = video_frames.extract(path, run_dir, digest)
            frames = result.get("frames", [])
        if not frames:
            failed += 1
            continue

        entries = []
        for position, frame_path in enumerate(frames):
            difference = similarity.dhash(frame_path)
            perceptual = similarity.phash(frame_path)
            if difference is None:
                continue
            # A frame of black or of a flat title card matches every other
            # flat frame in the run. Excluded for the same reason featureless
            # stills are.
            if similarity.is_featureless(frame_path):
                continue
            entries.append({"n": position, "dhash": difference,
                            "phash": perceptual})
        if entries:
            found[digest] = {"frames": entries,
                             "out_path": record.get("out_path", ""),
                             "name": os.path.basename(record.get("out_path", ""))}
            decoded += 1
        if progress and index % 5 == 0:
            progress(index, len(videos))
    if progress:
        progress(len(videos), len(videos))
    return {"videos": len(videos), "hashed": decoded, "unreadable": failed,
            "frames": found}


def match_still(dhash_value: int, phash_value, frames: dict,
                max_distance: int = DEFAULT_DISTANCE) -> list:
    """Which videos hold a frame that looks like this picture.

    Compared on the difference hash, and where both have one, confirmed on
    the perceptual hash as well -- the same two-hash agreement the visual
    stacks use, because one hash alone groups flat images that merely share
    a brightness pattern.
    """
    if dhash_value is None:
        return []
    hits = []
    for digest, entry in frames.items():
        best = None
        for frame in entry["frames"]:
            gap = similarity.distance(dhash_value, frame["dhash"])
            if gap > max_distance:
                continue
            confirmed = True
            if phash_value is not None and frame.get("phash") is not None:
                confirmed = similarity.distance(
                    phash_value, frame["phash"]) <= max_distance
            if not confirmed:
                continue
            if best is None or gap < best["distance"]:
                best = {"distance": gap, "frame": frame["n"]}
        if best:
            hits.append({"sha256": digest, "out_path": entry["out_path"],
                         "name": entry["name"], "frame": best["frame"],
                         "distance": best["distance"],
                         "confidence": ("the same picture" if best["distance"]
                                        <= STRICT_DISTANCE else "similar")})
    hits.sort(key=lambda hit: hit["distance"])
    return hits


def match_run(run_dir: str, records: list, frames: dict,
              max_distance: int = DEFAULT_DISTANCE, progress=None) -> dict:
    """Every still in a run that matches a frame of a video in it."""
    stills = [r for r in records if r.get("category") == "image"]
    matches = {}
    for index, record in enumerate(stills, start=1):
        path = os.path.join(run_dir, record.get("out_path", ""))
        if not os.path.isfile(path):
            continue
        if similarity.is_featureless(path):
            continue
        hits = match_still(similarity.dhash(path), similarity.phash(path),
                           frames, max_distance)
        if hits:
            matches[record.get("sha256", "")] = hits
        if progress and index % 50 == 0:
            progress(index, len(stills))
    if progress:
        progress(len(stills), len(stills))
    return matches


def analyze(run_dir: str, max_distance: int = DEFAULT_DISTANCE,
            progress=None, cancel=None) -> dict:
    """Hash the frames, then find the stills that came from them."""
    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    records = manifest.get("files", [])

    hashed = hash_frames(run_dir, records, progress=progress, cancel=cancel)
    matches = match_run(run_dir, records, hashed["frames"], max_distance,
                        progress=progress)

    case_path = os.path.join(run_dir, "case.db")
    if os.path.isfile(case_path):
        try:
            from fme.review import casedb
            case = casedb.CaseDB(case_path)
            case.save_derived("frame_hashes", hashed["frames"])
            case.save_derived("frame_matches", matches)
            case.close()
        except Exception:  # noqa: BLE001 - the result is still returned
            pass

    return {"videos": hashed["videos"], "hashed": hashed["hashed"],
            "unreadable": hashed["unreadable"],
            "stills_matched": len(matches),
            "max_distance": max_distance, "matches": matches}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Match stills to the videos they came from.")
    ap.add_argument("-o", "--run", required=True, help="a run's output folder")
    ap.add_argument("--distance", type=int, default=DEFAULT_DISTANCE)
    args = ap.parse_args(argv)

    state = available()
    if not state["ready"]:
        missing = []
        if not state["hashing"]:
            missing.append("Pillow (pip install pillow)")
        if not state["frames"]:
            missing.append("OpenCV (pip install opencv-python-headless)")
        print("This needs " + " and ".join(missing), file=sys.stderr)
        return 2

    def progress(done, total):
        print(f"\r  {done:,}/{total:,}", end="", flush=True)

    try:
        result = analyze(args.run, args.distance, progress)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 2

    print(f"\n\nVideos        : {result['videos']:,}")
    print(f"Frames hashed : {result['hashed']:,} video(s)")
    print(f"Unreadable    : {result['unreadable']:,}")
    print(f"Stills matched: {result['stills_matched']:,}")
    for digest, hits in list(result["matches"].items())[:20]:
        best = hits[0]
        print(f"  {digest[:12]}…  ->  {best['name']} frame {best['frame']} "
              f"({best['distance']} bits, {best['confidence']})")
    if result["stills_matched"]:
        print("\nA match is the same picture to the eye, not the same file. A\n"
              "video is represented by a dozen frames out of thousands, so a\n"
              "still from a moment between them will not match: a miss here\n"
              "means less than a hit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
