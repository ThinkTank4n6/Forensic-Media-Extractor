"""No video is ever opened in the examiner's own process.

Opening a video is FFmpeg parsing it, in C, and a carved clip can crash
that outright. Until v2026.11.294 three paths did it in-process anyway: a
frozen build -- what examiners run -- decoded every filmstrip here because it
had no interpreter to start; a child that could not be started fell back to
decoding here; and the report opened every video in the case here, in every
build, to read its duration. One malformed clip ended the session.

The proof used: cv2.VideoCapture in this process is replaced by a tripwire.
Children load their own OpenCV and are unaffected, so if the tripwire is
touched, a video was opened where it must not be.
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

try:
    import cv2
    import numpy as np
except ImportError:
    print("(not checked: OpenCV is not installed)")
    sys.exit(0)

from fme.engines import isolated, video_frames
from fme.exports import report

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "video_isolation-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

# A real two-second clip, written before the tripwire goes in.
clip = os.path.join(ROOT, "clip.avi")
writer = cv2.VideoWriter(clip, cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
for n in range(20):
    writer.write(np.full((48, 64, 3), n * 12, np.uint8))
writer.release()
damaged = os.path.join(ROOT, "carved.mp4")
with open(damaged, "wb") as fh:
    fh.write(b"\x00\x00\x00\x18ftypisom" + os.urandom(20000))

opened_here = []
cv2.VideoCapture = lambda *a, **k: opened_here.append(a) or (_ for _ in ()).throw(
    AssertionError("a video was opened in the program's own process"))


def run_as_frozen(action):
    """Run `action` as a frozen build would: sys.frozen set, and
    sys.executable a program that takes `_worker` as its first argument --
    which is what the packaged executable does through fme.cli."""
    exe = os.path.join(ROOT, "ForensicMediaExtractor")
    with open(exe, "w") as fh:
        fh.write(f"#!/bin/sh\ncd '{PROJECT}' && exec '{sys.executable}' -m fme \"$@\"\n")
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
    was_exe, sys.executable = sys.executable, exe
    sys.frozen = True
    try:
        return action()
    finally:
        sys.executable = was_exe
        del sys.frozen


print("filmstrip frames")
got = video_frames.extract(clip, os.path.join(ROOT, "src"), "a" * 64)
ok &= got.get("ok") and len(got["frames"]) > 0
print(f"  from source: {len(got.get('frames', []))} frames: {bool(got.get('ok'))}")
got = run_as_frozen(lambda: video_frames.extract(clip, os.path.join(ROOT, "frz"), "b" * 64))
ok &= got.get("ok") and len(got["frames"]) > 0
print(f"  as a frozen build: {len(got.get('frames', []))} frames, decoded in a "
      f"child: {bool(got.get('ok'))}")

print("\nthe report's durations")
rows = [{"kind": "video", "sha256": "c" * 64, "out_path": "clip.avi"},
        {"kind": "video", "sha256": "d" * 64, "out_path": "carved.mp4"}]
measured = report.durations(ROOT, rows)
right = measured.get("c" * 64) == 2.0 and measured.get("d" * 64) is None
ok &= right
print(f"  the real clip 2.0 s, the damaged one unknown (not zero): {right}  {measured}")
frozen_probe = run_as_frozen(lambda: video_frames.probe(clip))
ok &= frozen_probe.get("duration") == 2.0
print(f"  probed as a frozen build: {frozen_probe.get('duration')} s")

ok &= not opened_here
print(f"\nvideos opened in this process: {len(opened_here)}")

print("\na batch survives a file that crashes or stalls its reader")
mock = [sys.executable, "-c",
        "import json, os, sys, time\n"
        "for item in json.load(open(sys.argv[1])):\n"
        "    if item == 'crash': os.abort()\n"
        "    if item == 'stall': time.sleep(60)\n"
        "    print('a library talking to itself', flush=True)\n"
        "    print('FME-ANSWER ' + json.dumps({'item': item}), flush=True)\n"]
items = ["a", "crash", "b", "stall", "c"]
t0 = time.monotonic()
answers = isolated.run_batch("probe", items, 3, command_line=mock)
took = time.monotonic() - t0
statuses = [status for status, _ in answers]
right = statuses == ["ok", "crashed", "ok", "timeout", "ok"] and \
    [r["item"] for s, r in answers if s == "ok"] == ["a", "b", "c"]
ok &= right and took < 20
print(f"  {statuses} in {took:.1f} s: {right}")
print("  (the files after each bad one were still read, by a fresh child)")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
