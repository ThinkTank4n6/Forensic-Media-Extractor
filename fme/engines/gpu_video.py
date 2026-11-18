"""
gpu_video.py -- video decoded by FFmpeg, on an NVIDIA card when there is one.

The gallery's posters and scrub frames, and the storyboard, were decoded by
OpenCV in a child process: start an interpreter, load OpenCV, seek twelve
times through the clip in software. For a 4K HEVC clip off a phone that is
seconds per video, and the page asked for them only when the pointer reached
a tile -- so a page of video was a page of blank tiles, and each one hovered
held a connection while it decoded (v2026.11.331).

OpenCV's own FFmpeg is built without NVIDIA's decoder. An FFmpeg that has it
(``ffmpeg -hwaccels`` lists ``cuda``) decodes H.264, HEVC, VP9 and AV1 on the
card, and its encoder makes a browser-playable copy of a clip the browser
cannot play. This module finds one -- the path in Settings, then
``FME_FFMPEG``, then ``ffmpeg`` beside the program, then on PATH -- asks it
what it can do, and uses the cards it finds in turn.

What is never done: nothing decoded here is evidence. Frames and display
copies are renderings kept beside the case (``_frames``, ``_playable``),
never hashed, counted or exported, exactly as ``_previews`` are. And nothing
is required: without an FFmpeg, or without a card, the OpenCV path runs as it
did, and says so in What is installed.
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
import sys
import threading

__version__ = "1.0.0"

ENV = "FME_FFMPEG"
# How long one decode or one display copy may take before it is stopped.
FRAMES_TIMEOUT = 120
PLAYABLE_TIMEOUT = 900
# The longest side of a display copy. A gallery viewer shows a clip at most a
# screen wide; a 4K copy would be the original's cost again.
PLAYABLE_EDGE = 1280

_configured = ""
_info_lock = threading.Lock()
_info: dict = {}
_next_gpu = itertools.count()
_making: dict = {}
_making_lock = threading.Lock()


def configure(path: str) -> None:
    """The FFmpeg named in Settings, or "" to look for one."""
    global _configured
    path = (path or "").strip().strip('"')
    if path != _configured:
        _configured = path
        with _info_lock:
            _info.clear()


def _quiet() -> dict:
    """No console window for each child on Windows."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _beside_program() -> list:
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                           else __file__))
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    return [os.path.join(here, name), os.path.join(here, "ffmpeg", name),
            os.path.join(here, "ffmpeg", "bin", name)]


def find() -> str:
    """The FFmpeg this machine has, or ""."""
    for candidate in [_configured, os.environ.get(ENV, "")] + _beside_program():
        if candidate and os.path.isfile(candidate):
            return candidate
    return shutil.which("ffmpeg") or ""


# Background decodes -- the tile maker's posters and scrub frames -- were
# started at the examiner's own priority and as many at once as the slots,
# times the processes per clip, allowed: on a 32-core, two-card workstation
# twenty FFmpegs at normal priority, each with a thread per core, beside the
# browser the examiner was grading in (v2026.11.332;
# tools/repro/repro_background_decode_priority.py). They now start below
# normal, as the OpenCV children always did (isolated._below_normal), and
# draw on one budget for the whole machine: two per card, or one per eight
# cores. A decode the page is waiting for is neither lowered nor counted.
_budget_lock = threading.Lock()
_budget = {"size": 0, "sem": None}


def process_budget() -> int:
    found = info()
    if found.get("cuda"):
        return 2 * max(1, int(found.get("gpus") or 1))
    return max(2, (os.cpu_count() or 4) // 8)


def _background_slot():
    """The semaphore background FFmpegs share, sized to what the machine has
    now (an FFmpeg set in Settings after start changes it)."""
    size = process_budget()
    with _budget_lock:
        if _budget["size"] != size:
            _budget["size"], _budget["sem"] = size, threading.BoundedSemaphore(size)
        return _budget["sem"]


def cpu_threads() -> int:
    """Decoder threads for one background FFmpeg on the processor: the
    machine's cores shared out over the budget, not all of them each."""
    return max(1, (os.cpu_count() or 4) // max(1, process_budget()))


def _run(args: list, timeout: float, background: bool = False) -> subprocess.CompletedProcess:
    options = _quiet()
    if background:
        from fme.engines import isolated
        args, lowered = isolated._below_normal(list(args))
        if "creationflags" in lowered:
            options["creationflags"] = options.get("creationflags", 0) | lowered["creationflags"]
        with _background_slot():
            return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=timeout, **options)
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, **options)


def info() -> dict:
    """What the FFmpeg here can do: {ffmpeg, version, cuda, gpus, nvenc,
    x264, why}. Asked once per FFmpeg and kept."""
    path = find()
    with _info_lock:
        if _info.get("ffmpeg") == path and "version" in _info:
            return dict(_info)
    found = {"ffmpeg": path, "version": "", "cuda": False, "gpus": 0,
             "nvenc": False, "x264": False, "why": ""}
    if not path:
        found["why"] = ("no FFmpeg found: set its path in Settings, or put "
                        "ffmpeg on PATH. Without it video is decoded by OpenCV, "
                        "on the processor.")
    else:
        try:
            version = _run([path, "-hide_banner", "-version"], 20)
            found["version"] = (version.stdout.decode("utf-8", "replace")
                                .splitlines() or [""])[0][:120]
            accels = _run([path, "-hide_banner", "-hwaccels"], 20).stdout.decode(
                "utf-8", "replace")
            encoders = _run([path, "-hide_banner", "-encoders"], 20).stdout.decode(
                "utf-8", "replace")
            found["nvenc"] = " h264_nvenc " in encoders
            found["x264"] = " libx264 " in encoders
            if re.search(r"^\s*cuda\s*$", accels, re.M):
                # Listed means built with it, not that a card is there: open
                # each device until one will not open.
                for index in range(8):
                    probe = _run([path, "-hide_banner", "-v", "error",
                                  "-init_hw_device", f"cuda=gpu:{index}",
                                  "-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.04",
                                  "-f", "null", "-"], 30)
                    if probe.returncode != 0:
                        break
                    found["gpus"] = index + 1
                found["cuda"] = found["gpus"] > 0
                if not found["cuda"]:
                    found["why"] = ("this FFmpeg can use NVIDIA cards, but none "
                                    "opened: is the NVIDIA driver installed?")
            else:
                found["why"] = ("this FFmpeg was built without NVIDIA decoding "
                                "(-hwaccels does not list cuda); it decodes on "
                                "the processor")
        except (OSError, subprocess.SubprocessError) as exc:
            found["why"] = f"FFmpeg would not run: {exc}"
            found["ffmpeg"] = ""
    with _info_lock:
        _info.clear()
        _info.update(found)
    return dict(found)


def available() -> bool:
    return bool(info().get("ffmpeg"))


def slots() -> int:
    """How many decodes to run at once: a few per card, or a share of the
    processor's cores."""
    found = info()
    if found.get("cuda"):
        return max(2, 3 * int(found.get("gpus") or 1))
    return max(2, min(8, (os.cpu_count() or 4) // 4))


def _gpu_args(found: dict) -> list:
    if not found.get("cuda"):
        return []
    device = next(_next_gpu) % max(1, int(found.get("gpus") or 1))
    return ["-hwaccel", "cuda", "-hwaccel_device", str(device)]


_DURATION = re.compile(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_FPS = re.compile(rb"Video:.*?,\s*(\d+(?:\.\d+)?)\s*fps")


def probe(path: str) -> tuple:
    """(length in seconds, frames per second) as the container states them,
    0 for what it does not."""
    ffmpeg = find()
    if not ffmpeg:
        return 0.0, 0.0
    try:
        done = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", path], 30)
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0.0
    match = _DURATION.search(done.stderr)
    length = 0.0
    if match:
        hours, minutes, seconds = match.groups()
        length = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    rate = _FPS.search(done.stderr)
    return length, float(rate.group(1)) if rate else 0.0


def duration(path: str) -> float:
    """The clip's length in seconds as its container states it, or 0."""
    return probe(path)[0]


def frames(path: str, folder: str, count: int, width: int, quality: int,
           gpu: bool = True, background: bool = False) -> tuple:
    """Evenly spaced frames of one clip into folder, as NN.jpg.

    One FFmpeg, one input per frame, each seeked before it is opened -- the
    fast seek to the key frame before the time, then decoded to it -- so a
    long clip costs the frames asked for, not its length. Returns (written
    paths, duration, how: "gpu", "cpu" or the reason none was written).
    """
    found = info()
    ffmpeg = found.get("ffmpeg")
    if not ffmpeg:
        return [], 0.0, found.get("why") or "no FFmpeg"
    length, fps = probe(path)
    if length <= 0:
        return [], 0.0, "FFmpeg read no duration from this file"
    # On the card when asked and there is one; on the processor only when
    # the card was not asked for (bench measures that way). A clip the card
    # will not take goes back to the caller, which has OpenCV: measured,
    # FFmpeg on the processor was slower than OpenCV for ordinary clips
    # (0.7 times, v2026.11.332), so it is not the fallback.
    attempts = [True] if gpu and found.get("cuda") else ([] if gpu else [False])
    if fps > 0:
        # The frames the OpenCV path takes: frame int(total * where), not
        # the time -- or the two decoders give different frames of the same
        # clip, and a still matched against one set misses the other's
        # (tests/test_frame_match.py). A quarter frame early, so the seek
        # lands on that frame and not the one after it.
        total = int(round(length * fps))
        wanted = [(index, max(0.0, (int(total * (index + 0.5) / count) - 0.25) / fps))
                  for index in range(count)]
    else:
        wanted = [(index, length * (index + 0.5) / count) for index in range(count)]
    why = ""
    for on_card in attempts:
        # Split across processes: one per card, or a few on a processor with
        # many cores. Each seek decodes from the key frame before it, and one
        # process does that one seek at a time; measured on a 30-second 4K
        # HEVC clip, 22 seconds for twelve frames in one process.
        groups = (max(1, int(found.get("gpus") or 1)) * 2 if on_card
                  else max(1, min(4, (os.cpu_count() or 2) // 8)))
        groups = min(groups, count)
        parts = [wanted[g::groups] for g in range(groups)]
        said = []

        def one(part):
            args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y"]
            accel = _gpu_args(found) if on_card else []
            if background and not on_card:
                accel = accel + ["-threads", str(cpu_threads())]
            for _index, at in part:
                args += accel + ["-ss", f"{at:.3f}", "-i", path]
            for slot, (index, _at) in enumerate(part):
                args += ["-map", f"{slot}:v:0", "-frames:v", "1",
                         "-vf", f"scale='min({width},iw)':-2:flags=area,format=yuvj420p",
                         "-q:v", str(max(2, min(31, (100 - quality) // 3))),
                         os.path.join(folder, f"{index:02d}.jpg")]
            try:
                done = _run(args, FRAMES_TIMEOUT, background=background)
                said.append(done.stderr.decode("utf-8", "replace").strip()[-300:])
            except subprocess.TimeoutExpired:
                said.append(f"FFmpeg did not finish within {FRAMES_TIMEOUT} seconds")
            except (OSError, subprocess.SubprocessError) as exc:
                said.append(str(exc))

        threads = [threading.Thread(target=one, args=(part,), daemon=True)
                   for part in parts if part]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        why = next((w for w in said if w), "")
        written = sorted(os.path.join(folder, n) for n in os.listdir(folder)
                         if n.endswith(".jpg") and os.path.getsize(os.path.join(folder, n)))
        if written:
            return written, length, "gpu" if on_card else "cpu"
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
    return [], length, why or "FFmpeg wrote no frame"


def playable_path(out_dir: str, sha256: str) -> str:
    return os.path.join(out_dir, "_playable", sha256[:2], sha256 + ".mp4")


def make_playable(path: str, out_dir: str, sha256: str) -> dict:
    """A copy of one clip the browser can play: H.264 and AAC in an MP4, at
    most PLAYABLE_EDGE wide, encoded on the card where it can be.

    A display copy. It is never hashed, counted or exported; the evidence is
    the file it was made from, and the page says so beside it.
    """
    target = playable_path(out_dir, sha256)
    if os.path.isfile(target) and os.path.getsize(target):
        return {"ok": True, "path": target, "cached": True}
    found = info()
    ffmpeg = found.get("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "error": found.get("why") or "no FFmpeg"}
    if not (found.get("nvenc") or found.get("x264")):
        return {"ok": False, "error": "this FFmpeg has neither the NVIDIA H.264 "
                                      "encoder nor libx264"}
    with _making_lock:
        lock = _making.setdefault(target, threading.Lock())
    try:
        with lock:
            return _make_playable_locked(path, target, found, ffmpeg)
    finally:
        # The lock is for one clip while it is made; kept after, one per clip
        # ever opened stayed for the life of the program (v2026.11.332).
        with _making_lock:
            if _making.get(target) is lock and not lock.locked():
                _making.pop(target, None)


def _make_playable_locked(path: str, target: str, found: dict, ffmpeg: str) -> dict:
    if os.path.isfile(target) and os.path.getsize(target):
        return {"ok": True, "path": target, "cached": True}
    os.makedirs(os.path.dirname(target), exist_ok=True)
    part = f"{target}.{os.getpid()}.{threading.get_ident()}.part.mp4"
    scale = (f"scale='min({PLAYABLE_EDGE},iw)':-2:flags=bicubic,"
             f"format=yuv420p")
    tries = []
    if found.get("cuda") and found.get("nvenc"):
        tries.append(("gpu", _gpu_args(found), ["-c:v", "h264_nvenc", "-preset", "p4",
                                                "-rc", "vbr", "-cq", "26"]))
    if found.get("x264"):
        tries.append(("cpu", [], ["-c:v", "libx264", "-preset", "veryfast",
                                  "-crf", "26"]))
    elif found.get("nvenc"):
        tries.append(("cpu decode, card encode", [],
                      ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"]))
    why = ""
    for how, accel, encoder in tries:
        args = ([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y"] + accel
                + ["-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-vf", scale]
                + encoder + ["-c:a", "aac", "-b:a", "128k",
                             "-movflags", "+faststart", part])
        try:
            done = _run(args, PLAYABLE_TIMEOUT)
            why = done.stderr.decode("utf-8", "replace").strip()[-300:]
            if done.returncode == 0 and os.path.isfile(part) and os.path.getsize(part):
                os.replace(part, target)
                return {"ok": True, "path": target, "cached": False, "how": how}
        except subprocess.TimeoutExpired:
            why = f"FFmpeg did not finish within {PLAYABLE_TIMEOUT} seconds"
        except (OSError, subprocess.SubprocessError) as exc:
            why = str(exc)
        try:
            os.remove(part)
        except OSError:
            pass
    return {"ok": False, "error": why or "FFmpeg made no copy"}
