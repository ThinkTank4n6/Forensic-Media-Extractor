"""Video tiles: posters and scrub frames made ahead, by FFmpeg where there is
one, on an NVIDIA card where it has one; and a playable copy of a clip the
browser cannot play (v2026.11.331).

Reported on a workstation with two RTX 4080s: video barely played in the
scrub view, and grading a video took about twenty seconds to come back. The
page asked for each clip's frames as the pointer reached it, each decode held
a connection for seconds, and grading waited behind them. This checks the
server side: the decoders, the routes that never decode in the request, and
the display copy. The page side is tests/test_gui_markup.py (the queue) and
the browser run described in docs/EXAMINER_TASKS.md.

No NVIDIA card is here. The card's branch is checked with a stand-in ffmpeg
that says it has one, records the command it was given, and decodes on the
processor: that proves the commands, not the card.
"""
import _sandbox  # noqa: F401
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_video_tiles-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


REAL = shutil.which("ffmpeg")
if not REAL:
    print("(not checked: no ffmpeg on this machine to make the clips with)")
    sys.exit(0)

from fme.engines import gpu_video, output, video_frames  # noqa: E402

src = os.path.join(ROOT, "src")
os.makedirs(src)
subprocess.run([REAL, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "testsrc=size=640x360:rate=25", "-t", "4", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", os.path.join(src, "a.mp4")], check=True)
subprocess.run([REAL, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "testsrc=size=640x360:rate=25", "-t", "4", "-c:v", "mpeg4",
                os.path.join(src, "b.avi")], check=True)

print("finding FFmpeg")
gpu_video.configure("")
found = gpu_video.info()
check("the one on PATH is found", found.get("ffmpeg"), found.get("ffmpeg"))
check("and says why it is not using a card here", found.get("why") and not found.get("cuda"),
      found.get("why"))

print("frames without a card")
out_dir = os.path.join(ROOT, "frames")
os.makedirs(out_dir)
made = video_frames.extract(os.path.join(src, "b.avi"), out_dir, "ab" * 32)
check("twelve frames of an AVI, by OpenCV: FFmpeg decodes frames only on a card",
      made.get("ok") and len(made["frames"]) == 12 and not made.get("decoded_by"),
      made.get("decoded_by") or "OpenCV")
check("in the folder the OpenCV path uses, so either is the other's cache",
      video_frames.existing(out_dir, "ab" * 32) == made["frames"])
board = video_frames.extract(os.path.join(src, "a.mp4"), out_dir, "cd" * 32,
                             count=48, width=320, board=True)
check("a storyboard of 48, with its times", board.get("ok") and len(board["frames"]) == 48
      and video_frames.board_info(out_dir, "cd" * 32).get("duration", 0) > 3)

print("without FFmpeg")
gpu_video.configure(os.path.join(ROOT, "no-such-ffmpeg"))
path_was = os.environ["PATH"]
os.environ["PATH"] = ""
try:
    gone = gpu_video.info()
    check("none is found, and that is said", not gone.get("ffmpeg") and gone.get("why"))
    if video_frames.available():
        fallback = video_frames.extract(os.path.join(src, "a.mp4"), out_dir, "ef" * 32)
        check("frames still come from OpenCV", fallback.get("ok") and len(fallback["frames"]) == 12)
    else:
        print("  not checked: OpenCV is absent too")
finally:
    os.environ["PATH"] = path_was
    gpu_video.configure("")

print("the card's branch, through a stand-in that says it has two")
fake_dir = os.path.join(ROOT, "fakebin")
os.makedirs(fake_dir)
log = os.path.join(ROOT, "fake.log")
fake = os.path.join(fake_dir, "ffmpeg")
with open(fake, "w") as fh:
    fh.write(f"""#!{sys.executable}
import subprocess, sys
args = sys.argv[1:]
open({log!r}, "a").write(" ".join(args) + "\\n")
if "-hwaccels" in args:
    print("Hardware acceleration methods:\\ncuda\\n"); sys.exit(0)
if "-init_hw_device" in args:
    dev = args[args.index("-init_hw_device") + 1]
    sys.exit(0 if dev in ("cuda=gpu:0", "cuda=gpu:1") else 1)
clean, skip = [], 0
for a in args:
    if skip: skip -= 1; continue
    if a in ("-hwaccel", "-hwaccel_device"): skip = 1; continue
    clean.append(a)
sys.exit(subprocess.run([{REAL!r}] + clean).returncode)
""")
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
if os.name == "nt":
    print("  not checked: the stand-in is a script, which Windows will not run as ffmpeg")
else:
    gpu_video.configure(fake)
    card = gpu_video.info()
    check("two cards are counted, by opening each until one will not open",
          card.get("cuda") and card.get("gpus") == 2, card.get("gpus"))
    open(log, "w").close()
    on_card = video_frames.extract(os.path.join(src, "a.mp4"), out_dir, "12" * 32)
    lines = [l for l in open(log).read().splitlines() if "-ss" in l]
    devices = {l.split("-hwaccel_device ")[1].split()[0] for l in lines if "-hwaccel_device" in l}
    check("the frames are decoded on the card", on_card.get("decoded_by") == "gpu"
          and len(on_card["frames"]) == 12)
    check("asking for CUDA before every input", lines and all(
        l.count("-hwaccel cuda") == l.count("-i ") for l in lines))
    check("and using both cards", devices == {"0", "1"}, sorted(devices))
    gpu_video.configure("")

print("the routes")
os.environ["FME_PAGED_THRESHOLD"] = "100000"
from fme.web import media_gui as gui  # noqa: E402

case = os.path.join(ROOT, "case")
out = output.OutputManager(case, metadata=False)
for name in ("a.mp4", "b.avi"):
    with open(os.path.join(src, name), "rb") as fh:
        data = fh.read()
    out.store(iter([data]), category="video", ext=name.rsplit(".", 1)[1],
              stem=name.split(".")[0], engine="folder", source_path="/" + name,
              inode="", fs_type="", partition="", signature="folder-file")
out.close({"image": "/evidence"})
client = gui.app.test_client()
H = {"X-FME-Request": "1"}
job = client.post("/api/cases/open", json={"out_dir": case}, headers=H).get_json()["job_id"]
with gui.JOBS_LOCK:
    files = {f["rel_path"]: f for f in gui.JOBS[job]["files"]}
avi = next(r for r in files if r.endswith(".avi"))
mp4 = next(r for r in files if r.endswith(".mp4"))
# The tile maker started when the case opened; wait for both posters.
for _ in range(600):
    if all(video_frames.existing(case, files[r]["sha256"]) for r in files):
        break
    time.sleep(0.1)
answer = client.get(f"/api/poster?job={job}&file={avi}")
check("a video's poster is its first frame, made ahead", answer.status_code == 200
      and answer.mimetype == "image/jpeg")
shutil.rmtree(video_frames.frames_dir(case, files[mp4]["sha256"]), ignore_errors=True)
started = time.perf_counter()
answer = client.get(f"/api/poster?job={job}&file={mp4}")
check("one not made yet answers at once, and is not decoded in the request",
      answer.status_code == 404 and time.perf_counter() - started < 0.5,
      f"{time.perf_counter() - started:.2f}s")
cached = client.get(f"/api/frames?job={job}&file={mp4}&cached=1").get_json()
check("asked ahead of the pointer, frames are never decoded in the request either",
      cached.get("pending") and not cached.get("frames"))
for _ in range(300):
    if video_frames.existing(case, files[mp4]["sha256"]):
        break
    time.sleep(0.1)
check("the tile maker makes it next", video_frames.existing(case, files[mp4]["sha256"]))
check("and then it is served", client.get(f"/api/poster?job={job}&file={mp4}").status_code == 200)
check("a picture is no poster", client.get(f"/api/poster?job={job}&file=nope.jpg").status_code == 404)

print("a playable copy")
before = open(os.path.join(case, avi), "rb").read()
started = time.perf_counter()
made = client.post("/api/playable", json={"job": job, "file": avi}, headers=H).get_json()
check("the request answers at once, and the copy is made beside it",
      made.get("pending") and time.perf_counter() - started < 0.5,
      f"{time.perf_counter() - started:.2f}s")
for _ in range(600):
    if not made.get("pending"):
        break
    time.sleep(0.1)
    made = client.post("/api/playable", json={"job": job, "file": avi}, headers=H).get_json()
check("is made for the AVI", made.get("ok"), made.get("error") or made.get("how"))
copy = gpu_video.playable_path(case, files[avi]["sha256"])
check("as H.264 in an MP4", made.get("ok") and b"avc1" in open(copy, "rb").read(4096 * 4))
served = client.get(made.get("url", "/x"))
check("served to the page", served.status_code == 200 and served.mimetype == "video/mp4")
check("the evidence file is untouched", open(os.path.join(case, avi), "rb").read() == before)
with open(os.path.join(case, "manifest.json"), encoding="utf-8") as fh:
    listed = [f["out_path"] for f in json.load(fh)["files"]]
check("and the copy is in no list of what was recovered",
      not any("_playable" in p for p in listed))
again = client.post("/api/playable", json={"job": job, "file": avi}, headers=H).get_json()
check("asked again, the same copy", again.get("ok") and again.get("how") == "kept")
check("and nothing is left held for it",
      not gpu_video._making and not gui.PLAYABLE, (gpu_video._making, gui.PLAYABLE))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
