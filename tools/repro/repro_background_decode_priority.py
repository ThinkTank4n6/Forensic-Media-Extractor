"""v2026.11.331 on a 32-core, two-GPU Windows box: background decodes at full priority, unbounded."""
import os, subprocess, threading; from unittest import mock
CORES, GPUS, live, peak, normal = 32, 2, [0], [0], []; lock = threading.Lock()
def fake_run(args, **kw):            # mocked ffmpeg: count what runs at once, and at what priority
    with lock: live[0] += 1; peak[0] = max(peak[0], live[0]); normal.append(not kw.get("creationflags", 0) & 0x4000)
    threading.Event().wait(0.05)
    with lock: live[0] -= 1
slots = max(2, 3 * GPUS)                     # gpu_video.slots()
video_workers = max(1, slots - 1)            # media_gui._video_workers()
groups = GPUS * 2                            # gpu_video.frames(): processes per clip on the cards
def decode_one_clip():                       # gpu_video.frames(), as shipped: CREATE_NO_WINDOW only
    ts = [threading.Thread(target=subprocess.run, args=(["ffmpeg"],), kwargs={"creationflags": 0x08000000}) for _ in range(groups)]
    [t.start() for t in ts]; [t.join() for t in ts]
with mock.patch("subprocess.run", fake_run):
    workers = [threading.Thread(target=lambda: [decode_one_clip() for _ in range(5)]) for _ in range(video_workers)]
    [w.start() for w in workers]; [w.join() for w in workers]
print(f"ffmpeg at once: {peak[0]} (cards: {GPUS}, cores: {CORES}); at normal priority: {sum(normal)} of {len(normal)}")
raise SystemExit(1 if peak[0] > 2 * GPUS or any(normal) else 0)
