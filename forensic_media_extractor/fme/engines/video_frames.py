"""
video_frames.py -- frames out of a video. Optional: needs OpenCV.

The interface scrubs a video by seeking it in the browser, which costs
nothing and works for anything the browser can play. It cannot play
everything: most AVI, some MOV codecs, MPEG-2, and a good share of what comes
off a camera or a DVR. Those tiles show no frames at all, which is the one
case where an examiner has to open each file to know what it is.

This fills that gap by decoding the video once and writing evenly spaced
frames as JPEGs, so a clip can be judged from a filmstrip without being
played. The frames are cached beside the run's output and reused.

They are **generated images, not evidence**: their hashes are this tool's,
not the file's, and they are written outside the recovered media so nothing
confuses them for recovered files. The original is never touched.

    python -m fme frames -o out/manifest.json          # every unplayable video
    python -m fme frames -o out/manifest.json --all     # every video
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

__version__ = "1.0.0"

FRAME_COUNT = 12
FRAME_WIDTH = 480
JPEG_QUALITY = 72
FRAMES_DIRNAME = "_frames"

# What a browser will play. Anything else has to be decoded here or not seen.
BROWSER_PLAYABLE = {"mp4", "m4v", "mov", "webm", "ogg", "ogv"}

UNAVAILABLE = ("Frame extraction needs OpenCV, which is not installed. "
               "Install it with: pip install opencv-python-headless")

# OpenCV's video decoder is FFmpeg, and FFmpeg complains to the terminal
# directly -- not through Python, so warnings.catch_warnings cannot reach
# it. A carved video usually starts mid-stream with no header describing how
# to decode it, which produces "non-existing PPS 0 referenced" and
# "decode_slice_header error" several times per file. On a run with a few
# thousand carved clips that is thousands of lines of noise about files that
# simply cannot be decoded, which is already reported properly as "no frame
# could be decoded".
#
# These have to be set before OpenCV is imported, because it reads them when
# it loads its FFmpeg backend.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")   # AV_LOG_QUIET
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_VIDEOIO_DEBUG", "0")


def available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def status() -> dict:
    try:
        import cv2
        return {"available": True, "version": cv2.__version__, "message": ""}
    except ImportError:
        return {"available": False, "version": "", "message": UNAVAILABLE}


def needs_extraction(ext: str) -> bool:
    """Whether the browser would show nothing for this format."""
    return (ext or "").lower().lstrip(".") not in BROWSER_PLAYABLE


# A storyboard: the whole clip as a grid of frames, as Griffeye shows one
# (v2026.11.317). Kept apart from the dozen frames frame matching hashes, so
# neither replaces the other's cache.
BOARD_COUNT = 48
BOARD_WIDTH = 320
BOARD_SUFFIX = "-board"


def frames_dir(out_dir: str, sha256: str, board: bool = False) -> str:
    return os.path.join(out_dir, FRAMES_DIRNAME, sha256[:2],
                        sha256 + (BOARD_SUFFIX if board else ""))


def board_info(out_dir: str, sha256: str) -> dict:
    """Duration and frame count recorded with a storyboard, for its times."""
    try:
        with open(os.path.join(frames_dir(out_dir, sha256, True), "board.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def existing(out_dir: str, sha256: str, board: bool = False) -> list:
    folder = frames_dir(out_dir, sha256, board)
    if not os.path.isdir(folder):
        return []
    if board and not os.path.isfile(os.path.join(folder, "board.json")):
        # A storyboard is finished when its record of times is written; a
        # folder without one is a decode still running or one that died.
        return []
    return sorted(os.path.join(folder, n) for n in os.listdir(folder)
                  if n.endswith(".jpg"))


def _quiet_ffmpeg():
    """Keep FFmpeg's complaints off the terminal while a clip is read.

    The environment variables above cover OpenCV's own logging, but a
    damaged stream still makes FFmpeg write to the process's stderr at the
    C level, underneath Python. The only way to stop that is to point the
    file descriptor somewhere else for the duration.
    """
    import contextlib

    @contextlib.contextmanager
    def redirected():
        try:
            saved = os.dup(2)
        except (OSError, AttributeError):
            yield                      # no file descriptors to move
            return
        sink = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(sink, 2)
            yield
        finally:
            os.dup2(saved, 2)
            os.close(sink)
            os.close(saved)

    return redirected()


# How long one video may take before it is treated as hung. A large clip on
# a slow disk is a few seconds; a minute is generous and still bounded.
DECODE_TIMEOUT = 120


def extract(path: str, out_dir: str, sha256: str, count: int = FRAME_COUNT,
            width: int = FRAME_WIDTH, board: bool = False) -> dict:
    """Frames from one video, decoded in a process of its own.

    The decoder is FFmpeg underneath OpenCV, in C. A malformed stream can
    crash it outright -- not raise an exception, crash -- and carved video
    is exactly the malformed input that provokes it. Decoding in this
    process means one bad clip takes down the server, the run in progress
    and whatever review was open.

    So the work happens in a child process. If it dies, the parent sees an
    exit code and records that this file could not be read; everything else
    carries on. A hang is bounded by a timeout, because a decoder stuck in a
    loop is as bad as one that crashed and harder to notice.

    There is no way to ask for it in this process. There used to be two:
    ``isolate=False``, and a frozen build, which has no interpreter to start
    and so decoded here -- in exactly the build examiners run. And when the
    child could not be started, it fell back to decoding here. Each was a way
    for one clip to end the session. The child is now started through
    fme/engines/isolated.py, which works in a frozen build; if it cannot be
    started, the file is reported as not decoded.
    """
    cached = existing(out_dir, sha256, board)
    if cached:
        return {"ok": True, "frames": cached, "cached": True,
                **({k: v for k, v in board_info(out_dir, sha256).items()
                    if k in ("duration", "count")} if board else {})}
    if not available():
        return {"ok": False, "error": UNAVAILABLE, "frames": []}
    from fme.engines import isolated
    import uuid

    # This decode's own working folder. Another request may be decoding the
    # same video at the same moment, into its own.
    part = uuid.uuid4().hex[:12]
    outcome = isolated.run("frames", [path, out_dir, sha256, count, width,
                                      "board" if board else "", part],
                           DECODE_TIMEOUT, below_normal=True)
    if outcome.status != "ok" or not outcome.ok:
        # The frames it wrote before it was stopped are not a set: left in
        # place, the next request found them and served them as the cached,
        # complete frames of the video.
        # Only this decode's own folder. Removing every ".part*" and the
        # finished folder too took another request's decode away from under
        # it, or the set it had just finished (v2026.11.319). A finished set
        # only ever arrives whole, by rename, so it is never half-made.
        import shutil

        shutil.rmtree(f"{frames_dir(out_dir, sha256, board)}.part{part}",
                      ignore_errors=True)
    if outcome.status == "timeout":
        return {"ok": False, "frames": [],
                "error": f"the decoder did not finish within "
                         f"{DECODE_TIMEOUT} seconds and was stopped"}
    if outcome.status == "crashed":
        return {"ok": False, "frames": [], "crashed": True,
                "error": f"the decoder crashed on this file ({outcome.detail})"}
    if not outcome.ok:
        return {"ok": False, "frames": [],
                "error": f"the decoder could not run ({outcome.detail})"}
    try:
        return json.loads(outcome.stdout.decode("utf-8", "replace").strip()
                          or "{}") or {"ok": False, "frames": [],
                                       "error": "the decoder returned nothing"}
    except ValueError:
        return {"ok": False, "frames": [],
                "error": "the decoder returned something unreadable"}


def frames_worker(args: list) -> int:
    """The child's side of `extract`: decode one video, answer as JSON."""
    try:
        path, out_dir, sha256, count, width = args[:5]
        board = len(args) > 5 and args[5] == "board"
        part = args[6] if len(args) > 6 else ""
        result = _extract_here(path, out_dir, sha256, int(count), int(width),
                               board=board, part=part)
    except Exception as exc:  # noqa: BLE001 - the parent only needs the fact
        result = {"ok": False, "frames": [],
                  "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(result))
    return 0


def _extract_here(path: str, out_dir: str, sha256: str, count: int = FRAME_COUNT,
                  width: int = FRAME_WIDTH, board: bool = False,
                  part: str = "") -> dict:
    """Evenly spaced frames from one video, cached by the file's hash.

    Spacing is by position rather than by key frame: an examiner wants a
    picture of the whole clip, and the key frames of a long static shot are
    all the same picture. Seeking is by frame index where the container
    reports a frame count, and by timestamp where it does not, because a
    damaged or streamed file often reports neither reliably.
    """
    if not available():
        return {"ok": False, "error": UNAVAILABLE, "frames": []}

    import cv2

    with _quiet_ffmpeg():
        capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return {"ok": False, "error": "OpenCV could not open this file",
                "frames": []}

    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        duration = (total / fps) if (total and fps and fps > 0) else 0.0

        # Decoded beside the final folder and moved into place whole. Written
        # straight into it, a second request during the decode -- or any
        # request after a crash -- found three frames of 48 and served them
        # as the whole video (v2026.11.317).
        import shutil

        final = frames_dir(out_dir, sha256, board)
        folder = f"{final}.part{part or os.getpid()}"
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
        written = []
        for index in range(count):
            where = (index + 0.5) / count
            if total > 1:
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(total * where))
            elif duration > 0:
                capture.set(cv2.CAP_PROP_POS_MSEC, duration * where * 1000.0)
            else:
                # Neither is reliable: read forward instead of seeking.
                for _ in range(max(1, int(30 * where))):
                    capture.grab()
            with _quiet_ffmpeg():
                ok, frame = capture.read()
            if not ok or frame is None:
                continue

            height, original_width = frame.shape[:2]
            if original_width > width:
                scale = width / float(original_width)
                frame = cv2.resize(frame, (width, max(1, int(height * scale))),
                                   interpolation=cv2.INTER_AREA)
            target = os.path.join(folder, f"{index:02d}.jpg")
            if cv2.imwrite(target, frame,
                           [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]):
                written.append(target)

        if board and written:
            # Each frame's time is (index + 0.5) / count of the duration;
            # kept so a cached storyboard can still say when each was.
            with open(os.path.join(folder, "board.json"), "w", encoding="utf-8") as fh:
                json.dump({"duration": round(duration, 3), "count": count,
                           "indexes": [int(os.path.basename(w)[:2]) for w in written]}, fh)
        if written:
            if board and os.path.isdir(final) and not existing(out_dir, sha256, True):
                # What an earlier decode left part made: not a set to keep.
                shutil.rmtree(final, ignore_errors=True)
            try:
                os.rename(folder, final)
            except OSError:
                # Another decode of the same file finished first: its set is
                # complete, and is the one kept.
                shutil.rmtree(folder, ignore_errors=True)
            written = existing(out_dir, sha256, board)
        else:
            shutil.rmtree(folder, ignore_errors=True)
        return {"ok": bool(written), "frames": written,
                "duration": round(duration, 2), "fps": round(fps, 2), "count": count,
                "frame_count": total, "cached": False,
                "error": "" if written else "no frame could be decoded"}
    except Exception as exc:  # noqa: BLE001 - a damaged clip is expected
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "frames": []}
    finally:
        capture.release()


# How long one file's probe may take. Opening a container and reading its
# header is fast; this bounds a demuxer that has stopped making progress.
PROBE_TIMEOUT = 30


def _probe_here(path: str) -> dict:
    """Dimensions and duration, opened in this process. Child only."""
    import cv2
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return {}
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        return {
            "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
            "fps": round(fps, 2),
            "frames": total,
            "duration": round(total / fps, 2) if total and fps else 0.0,
        }
    finally:
        capture.release()


def probe_worker(args: list) -> int:
    """The child's side of `probe_many`: one JSON line per file, as each
    finishes, so the parent knows exactly which file a crash happened on."""
    with open(args[0], encoding="utf-8") as fh:
        paths = json.load(fh)
    for path in paths:
        try:
            details = _probe_here(path)
        except Exception:  # noqa: BLE001 - a file that will not open
            details = {}
        from fme.engines import isolated

        isolated.answer(details)
    return 0


def probe_many(paths: list, progress=None) -> dict:
    """{path: details} for many videos, opened outside this process.

    Opening a video is FFmpeg parsing the file, and the report measures every
    video in a case this way -- in the examiner's own process, before this
    change, in every build. A file the demuxer crashes or stalls on gets {}
    (no length, which the report shows as unknown, never as zero seconds),
    and the rest are measured by a fresh child.
    """
    if not available() or not paths:
        return {path: {} for path in paths}
    from fme.engines import isolated
    answers = isolated.run_batch("probe", list(paths), PROBE_TIMEOUT,
                                 below_normal=True, progress=progress)
    return {path: (result if status == "ok" and isinstance(result, dict) else {})
            for path, (status, result) in zip(paths, answers)}


def probe(path: str) -> dict:
    """Dimensions and duration of one video, opened outside this process."""
    return probe_many([path]).get(path, {})


def extract_run(manifest_path: str, only_unplayable: bool = True,
                progress=None) -> dict:
    """Frames for the videos in a run."""
    if not available():
        raise RuntimeError(UNAVAILABLE)
    out_dir = os.path.dirname(os.path.abspath(manifest_path))
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    videos = [f for f in manifest.get("files", [])
              if f.get("category") == "video"
              and (not only_unplayable or needs_extraction(f.get("ext", "")))]
    done = failed = 0
    for index, record in enumerate(videos, start=1):
        path = os.path.join(out_dir, record.get("out_path", ""))
        digest = record.get("sha256") or hashlib.sha256(
            path.encode()).hexdigest()
        result = extract(path, out_dir, digest)
        if result.get("ok"):
            done += 1
        else:
            failed += 1
        if progress and index % 5 == 0:
            progress(index, len(videos), done)
    if progress:
        progress(len(videos), len(videos), done)
    return {"videos": len(videos), "extracted": done, "failed": failed,
            "folder": os.path.join(out_dir, FRAMES_DIRNAME)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write frames for videos a browser cannot play. Needs "
                    "OpenCV.")
    ap.add_argument("-o", "--manifest", required=True,
                    help="manifest.json from a run")
    ap.add_argument("--all", action="store_true",
                    help="every video, not only the unplayable ones")
    args = ap.parse_args(argv)

    if not available():
        print(UNAVAILABLE, file=sys.stderr)
        return 2

    def progress(done, total, made):
        print(f"\r  {done}/{total} videos, {made} with frames",
              end="", flush=True)

    try:
        result = extract_run(args.manifest, not args.all, progress)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 2

    print(f"\n\nVideos    : {result['videos']:,}")
    print(f"With frames: {result['extracted']:,}")
    print(f"Failed     : {result['failed']:,}  (damaged, or a codec OpenCV "
          f"does not have)")
    print(f"Folder     : {result['folder']}")
    print("\nThese are generated images, not recovered files: their hashes "
          "are this tool's,\nnot the evidence's, which is why they are kept "
          "outside the media folders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
