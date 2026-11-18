"""Background video decodes run below the examiner's priority, and only as
many at once as the machine's budget (v2026.11.332).

v2026.11.331 started the tile maker's FFmpegs at normal priority, and as many
at once as decode slots times processes per clip: twenty on a 32-core box
with two cards (tools/repro/repro_background_decode_priority.py reproduces it
in twenty lines). This drives the real code -- video_frames.extract and
gpu_video.frames -- with FFmpeg replaced by a stand-in that records how many
run at once and how each was started, on a machine said to have two cards.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fme.engines import gpu_video, video_frames  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "bg_decode_priority-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


lock = threading.Lock()
state = {"live": 0, "peak": 0, "runs": []}


def lowered(args, kwargs):
    if os.name == "nt":
        return bool(kwargs.get("creationflags", 0) & 0x00004000)
    return os.path.basename(args[0]) == "nice"


class Done:
    def __init__(self, stderr=b""):
        self.returncode, self.stdout, self.stderr = 0, b"", stderr


def fake_run(args, **kwargs):
    if "-i" in args and not any(a.endswith(".jpg") for a in args):
        return Done(b"Duration: 00:00:10.00, start: 0\n  Stream #0:0: Video: h264, 640x360, 25 fps")
    with lock:
        state["live"] += 1
        state["peak"] = max(state["peak"], state["live"])
        state["runs"].append({"lowered": lowered(args, kwargs),
                              "threads": "-threads" in args,
                              "cuda": "-hwaccel" in args})
    time.sleep(0.05)
    for a in args:
        if a.endswith(".jpg"):
            with open(a, "wb") as fh:
                fh.write(b"\xff\xd8\xff fake")
    with lock:
        state["live"] -= 1
    return Done()


def machine(gpus):
    gpu_video.configure("")
    with gpu_video._info_lock:
        gpu_video._info.clear()
        gpu_video._info.update({"ffmpeg": "/x/ffmpeg", "version": "stand-in",
                                "cuda": gpus > 0, "gpus": gpus, "nvenc": gpus > 0,
                                "x264": True, "why": ""})


def run_many(n, background, tag):
    clip = os.path.join(ROOT, "clip.mp4")
    open(clip, "wb").close()
    threads = [threading.Thread(target=video_frames.extract,
                                args=(clip, os.path.join(ROOT, f"{tag}{i}"), f"{i:064x}"),
                                kwargs={"background": background}) for i in range(n)]
    for i in range(n):
        os.makedirs(os.path.join(ROOT, f"{tag}{i}"), exist_ok=True)
    for t in threads:
        t.start()
    for t in threads:
        t.join()


with mock.patch.object(gpu_video, "find", lambda: "/x/ffmpeg"), \
        mock.patch.object(gpu_video, "info", lambda: dict(gpu_video._info)), \
        mock.patch("fme.engines.gpu_video.subprocess.run", fake_run), \
        mock.patch.object(video_frames, "_SLOTS", None), \
        mock.patch.object(video_frames, "_BG_SLOTS", None):
    print("two cards, the tile maker decoding eight clips")
    machine(2)
    state.update(live=0, peak=0, runs=[])
    run_many(8, True, "bg")
    check("no more FFmpegs at once than two per card", state["peak"] <= 4, state["peak"])
    check("every one below the examiner's priority",
          state["runs"] and all(r["lowered"] for r in state["runs"]),
          f"{sum(r['lowered'] for r in state['runs'])} of {len(state['runs'])}")
    check("and on the cards", all(r["cuda"] for r in state["runs"]))

    print("a clip the page is waiting for")
    state.update(live=0, peak=0, runs=[])
    run_many(1, False, "fg")
    check("is not lowered", state["runs"] and not any(r["lowered"] for r in state["runs"]))

    print("no card, 32 cores")
    machine(0)
    opencv = []
    with mock.patch.object(video_frames, "_extract_opencv",
                           lambda *a, **k: opencv.append(a) or {"ok": True, "frames": []}), \
            mock.patch.object(video_frames, "available", lambda: True):
        state.update(live=0, peak=0, runs=[])
        run_many(4, True, "cpu")
        check("frames come from OpenCV, which was the quicker there, and no FFmpeg runs",
              len(opencv) == 4 and not state["runs"], f"{len(opencv)} OpenCV, {len(state['runs'])} FFmpeg")
    # FFmpeg on the processor is still what bench measures.
    with mock.patch("fme.engines.gpu_video.os.cpu_count", lambda: 32):
        state.update(live=0, peak=0, runs=[])
        folder = os.path.join(ROOT, "bench")
        os.makedirs(folder)
        gpu_video.frames(os.path.join(ROOT, "clip.mp4"), folder, 12, 480, 72,
                         gpu=False, background=True)
        check("FFmpeg on the processor, when asked for, is told its share of the threads, "
              "not all 32", all(r["threads"] for r in state["runs"]) and gpu_video.cpu_threads() == 8,
              gpu_video.cpu_threads())
        check("and runs below the examiner's priority", state["runs"] and
              all(r["lowered"] for r in state["runs"]))

    print("an FFmpeg with cards set after the first decode")
    machine(0)
    first = video_frames._slots(True)
    machine(2)
    after = video_frames._slots(True)
    check("the decode slots follow it, without a restart",
          after is not first and video_frames._SLOTS_SIZE == gpu_video.slots(),
          video_frames._SLOTS_SIZE)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
