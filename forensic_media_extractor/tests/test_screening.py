"""Screening: a triage signal, and the proof that it is only that.

Most of these checks exist to pin down what the number is *not*. A column
that looks like a verdict gets read as one, so the failure modes are tested
as deliberately as the successes: if sand ever stops scoring high, somebody
has quietly turned a colour measurement into a classifier.
"""
import tempfile
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.engines import screening
from fme.engines import video_frames

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "screening_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

ok = True

try:
    from PIL import Image
except ImportError:
    print("Pillow is not installed, so screening cannot be tested")
    sys.exit(0)


def solid(name, color, size=(200, 200)):
    path = os.path.join(ROOT, name)
    Image.new("RGB", size, color).save(path)
    return path


print("what the measurement does")
cases = [
    ("a light skin tone", (222, 170, 135), "high"),
    ("a darker skin tone", (141, 85, 55), "high"),
    ("sky", (120, 170, 230), "low"),
    ("grass", (70, 140, 60), "low"),
    ("black", (0, 0, 0), "low"),
]
for label, color, expect in cases:
    value = screening.skin_proportion(solid(label.replace(" ", "_") + ".png", color))
    good = (value > 0.5) if expect == "high" else (value < 0.2)
    ok &= good
    print(f"  {label:22} {value:>6.2f}  {'' if good else '<-- unexpected'}")

# These are the false positives, and they are tested so that nobody later
# mistakes the number for a judgement about the picture.
print("\nwhat it cannot tell apart")
for label, color in (("sand", (194, 178, 128)), ("wood", (160, 110, 70)),
                     ("brick", (170, 110, 90))):
    value = screening.skin_proportion(solid(label + ".png", color))
    scores_high = value > 0.5
    ok &= scores_high
    print(f"  {label:22} {value:>6.2f}  scores high, as skin does — "
          f"{'as documented' if scores_high else 'NO LONGER, check the docs'}")

print("\nbands")
for value, expected in ((0.0, "Very little skin tone"), (0.10, "Low"),
                        (0.30, "Low–medium"), (0.45, "Medium"),
                        (0.60, "Medium–high"), (0.90, "High")):
    got = screening.band_of(value)
    ok &= got == expected
    print(f"  {value:>5.2f} -> {got}")

# A file that cannot be decoded has to come back as unreadable rather than
# as zero, which would put it in the lowest band and read as "nothing here".
broken = os.path.join(ROOT, "broken.jpg")
with open(broken, "wb") as fh:
    fh.write(b"\xff\xd8\xff" + bytes(40))
result = screening.score(broken, faces=False)
ok &= result["skin"] is None and result["band"] == "Could not be read"
print(f"\nan unreadable file: {result['band']!r}, not a score of zero")

# No detector must be distinguishable from a detector that found nothing.
faces = screening.face_count(solid("plain.png", (200, 200, 200)))
print(f"face count on a blank image: {faces} "
      f"({'no detector installed' if faces < 0 else 'detector ran'})")
ok &= isinstance(faces, int)

# FFmpeg, which OpenCV decodes video with, writes its complaints straight to
# file descriptor 2 -- underneath Python, where catching warnings cannot
# reach. A carved clip that starts mid-stream produces several lines per
# file, and a run with thousands of them buries everything else.
print("\nkeeping the video decoder quiet")
import subprocess as _subprocess

probe = f"""
import os, sys
sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r})
from fme.engines import video_frames
os.write(2, b"before the context\\n")
with video_frames._quiet_ffmpeg():
    os.write(2, b"[h264 @ 0x1] decode_slice_header error\\n")
os.write(2, b"after the context\\n")
"""
result = _subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True)
escaped = [line for line in result.stderr.splitlines() if line.strip()]
quiet = not any("h264" in line for line in escaped)
ok &= quiet
print(f"  writes below Python are caught: {quiet}")
print(f"  and the ones outside still appear: {len(escaped)} line(s)")
ok &= len(escaped) == 2

# Decoding runs below the priority of the program that asked for it. An
# examiner grading files is doing the work; frames for clips nobody has
# hovered are a convenience, and a convenience must not make a keystroke
# wait. Measured: 80 ms per grading without this, 6 ms with it.
print("\nframe decoding yields to the work")
# Checked by what is launched, not by reading the source: the source check
# required preexec_fn, which Python documents as unsafe with threads.
import subprocess as _subprocess
import tempfile as _tempfile

_launched = {}


def _capture(command, **options):
    _launched.update(command=list(command), **options)
    raise _subprocess.TimeoutExpired(command, 0)


_real_run, _subprocess.run = _subprocess.run, _capture
try:
    with _tempfile.TemporaryDirectory() as _scratch:
        try:
            video_frames.extract(os.path.join(_scratch, "clip.mp4"), _scratch, "0" * 64)
        except Exception:                                   # noqa: BLE001
            pass
finally:
    _subprocess.run = _real_run
if not _launched:
    lowered = True      # nothing launched here (no decoder, or a frozen build)
    print("  (no decoder was launched on this machine; not checked)")
elif os.name == "nt":
    lowered = _launched.get("creationflags") == 0x00004000
else:
    lowered = (_launched["command"][1:3] == ["-n", "10"]
               and "preexec_fn" not in _launched)
ok &= lowered
print(f"  the decoder is started at a lower priority: {lowered}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
