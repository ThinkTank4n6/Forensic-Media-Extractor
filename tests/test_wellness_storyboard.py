"""Grayscale, masks and storyboards -- three of Griffeye's review features.

v2026.11.317. Checked at the server and by pressing the controls in
Chromium:

1. Grayscale shows every picture in grey, and is remembered; it changes
   how a file is shown, never the file.
2. A mask covers part of one picture wherever it is shown (full-size view,
   details pane), is kept with the case and audited, and is refused when it
   is not a region of the picture.
3. A storyboard shows a whole clip as 48 evenly spaced frames, each with its
   time, decoded in a process of its own; clicking one plays from there.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402
from fme.engines import video_frames  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "wellness-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
H = {"X-FME-Request": "1"}


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


case_dir = os.path.join(ROOT, "case")
picture = fixtures.make_jpeg(30)
os.makedirs(os.path.join(case_dir, "images", "jpg"))
with open(os.path.join(case_dir, "images", "jpg", "a.jpg"), "wb") as fh:
    fh.write(picture)
records = [{"out_path": "images/jpg/a.jpg", "ext": "jpg", "category": "image",
            "engine": "filesystem", "size": len(picture), "deleted": False,
            "sha256": hashlib.sha256(picture).hexdigest(), "md5": "", "sha1": "",
            "source_path": "/DCIM/a.jpg", "offset": None, "fs_type": "FAT32",
            "mtime": "", "captured": "", "atime": "", "crtime": "",
            "latitude": None, "longitude": None}]

video_ok = video_frames.available()
if video_ok:
    import cv2
    import numpy as np
    clip = os.path.join(case_dir, "videos", "mp4", "clip.mp4")
    os.makedirs(os.path.dirname(clip))
    writer = cv2.VideoWriter(clip, cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for n in range(100):                       # ten seconds, a moving bar
        frame = np.zeros((120, 160, 3), np.uint8)
        frame[:, (n * 3) % 160:(n * 3) % 160 + 8] = (255, 255, 255)
        writer.write(frame)
    writer.release()
    data = open(clip, "rb").read()
    records.append(dict(records[0], out_path="videos/mp4/clip.mp4", ext="mp4",
                        category="video", size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        source_path="/DCIM/clip.mp4"))
with open(os.path.join(case_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic"}, "files": records}, fh)

client = gui.app.test_client()
opened = client.post("/api/cases/open", json={"out_dir": case_dir}, headers=H).get_json()
job = opened.get("job_id") or gui.LAST_JOB.get("id")
sha = records[0]["sha256"]

print("masks, at the server")
saved = client.post("/api/case/masks", json={"job": job, "sha256": sha,
                                             "masks": [[0.1, 0.2, 0.3, 0.4], [0.9, 0.9, 0.5, 0.5]]},
                    headers=H).get_json()
check("a region is kept, and one running off the picture is cut to it",
      saved["masks"] == [[0.1, 0.2, 0.3, 0.4], [0.9, 0.9, 0.1, 0.1]], saved.get("masks"))
again = client.get(f"/api/case/masks?job={job}&sha256={sha}").get_json()
check("and read back", again["masks"] == saved["masks"])
bad = client.post("/api/case/masks", json={"job": job, "sha256": sha, "masks": [["x"]]},
                  headers=H)
check("something that is not a region is refused", bad.status_code == 400)
audit = gui._case_for(job).audit()
check("covering a picture is in the audit log",
      any(row.get("action") == "mask" for row in audit))
check("a request from another site is refused",
      client.post("/api/case/masks", json={"job": job, "sha256": sha, "masks": []}
                  ).status_code == 403)
check("the file itself is untouched",
      open(os.path.join(case_dir, "images", "jpg", "a.jpg"), "rb").read() == picture)

if video_ok:
    print("\na storyboard, at the server")
    board = client.get(f"/api/storyboard?job={job}&file=videos/mp4/clip.mp4").get_json()
    frames = board.get("frames") or []
    times = [f["at"] for f in frames]
    check(f"{video_frames.BOARD_COUNT} frames of the clip", len(frames) == video_frames.BOARD_COUNT,
          len(frames))
    check("each with its time, in order, inside the clip",
          times == sorted(times) and 0 < times[0] < times[-1] < 10.01, (times[:2], times[-1:]))
    got = client.get(frames[0]["src"]) if frames else None
    check("served as pictures", got is not None and got.status_code == 200
          and got.data[:3] == b"\xff\xd8\xff")
    cached = client.get(f"/api/storyboard?job={job}&file=videos/mp4/clip.mp4").get_json()
    check("and kept: the second ask is the same board, with its times",
          [f["at"] for f in cached["frames"]] == times)
    # A decode in progress, or one that died, leaves frames and no record of
    # their times: never served as the whole video (v2026.11.318).
    import shutil as _sh
    folder = video_frames.frames_dir(case_dir, records[1]["sha256"], True)
    _sh.rmtree(folder)
    os.makedirs(folder)
    for n in range(3):
        with open(os.path.join(folder, f"{n:02d}.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8\xff partial")
    partial = client.get(f"/api/storyboard?job={job}&file=videos/mp4/clip.mp4").get_json()
    check("three frames left without their record are not the storyboard",
          len(partial.get("frames") or []) == video_frames.BOARD_COUNT
          and all(f["at"] is not None for f in partial["frames"]),
          len(partial.get("frames") or []))
    leftovers = [n for n in os.listdir(os.path.dirname(folder)) if ".part" in n]
    check("and the decode leaves no half-made folder behind", not leftovers, leftovers)
    small = video_frames.existing(case_dir, records[1]["sha256"])
    check("apart from the frames frame matching uses", len(small) == 0, len(small))
else:
    print("\n(storyboard not checked: OpenCV is not installed)")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
    print("\n(not checked in a browser: playwright is not installed)")

if sync_playwright is not None:
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, gui.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            browser = None
            print(f"\n(not checked in a browser: {str(exc)[:80]})")
        if browser is not None:
            print("\nin the page")
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            thrown = []
            page.on("pageerror", lambda e: thrown.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="load")
            page.wait_for_timeout(500)
            page.evaluate("""async (id) => { jobId = id;
                files = takeFiles(await api('/api/status?job_id=' + id), []);
                await loadCase(); render(); }""", job)
            page.evaluate("() => { const b = document.getElementById('gray-on'); b.checked = true; "
                          "b.dispatchEvent(new Event('change')); }")
            check("Grayscale greys the gallery",
                  page.evaluate("() => document.body.classList.contains('wellness-gray') && "
                                "getComputedStyle(document.querySelector('.thumb')).filter"
                                ".includes('grayscale')"))
            page.reload(wait_until="load")
            page.wait_for_timeout(400)
            check("and is remembered", page.evaluate(
                "() => document.body.classList.contains('wellness-gray')"))
            page.evaluate("""async (id) => { jobId = id;
                files = takeFiles(await api('/api/status?job_id=' + id), []);
                await loadCase(); render(); }""", job)
            # clear the masks set above, then draw one with the mouse
            client.post("/api/case/masks", json={"job": job, "sha256": sha, "masks": []},
                        headers=H)
            page.evaluate("(f) => { maskCache[maskKey(f)] = undefined; }", sha)
            page.evaluate("() => viewFullSize(lookup('images/jpg/a.jpg'))")
            page.wait_for_selector("#full-body .maskwrap img", timeout=5000)
            page.wait_for_function("() => document.querySelector('#full-body img').complete")
            page.click("#mask-draw")
            box = page.eval_on_selector("#full-body .maskwrap img",
                                        "e => { const r = e.getBoundingClientRect(); "
                                        "return [r.left, r.top, r.width, r.height]; }")
            page.mouse.move(box[0] + box[2] * 0.25, box[1] + box[3] * 0.25)
            page.mouse.down()
            page.mouse.move(box[0] + box[2] * 0.75, box[1] + box[3] * 0.5, steps=5)
            page.mouse.up()
            page.wait_for_timeout(600)
            stored = client.get(f"/api/case/masks?job={job}&sha256={sha}").get_json()["masks"]
            near = stored and all(abs(a - b) < 0.03 for a, b in
                                  zip(stored[0], [0.25, 0.25, 0.5, 0.25]))
            check("a box dragged over the picture is kept as that region", near, stored)
            check("and drawn over the picture",
                  page.evaluate("() => document.querySelectorAll('#full-body .mask').length") == 1)
            page.click("#full-close")
            page.evaluate("() => showDetails('images/jpg/a.jpg')")
            page.wait_for_timeout(800)
            check("the details pane covers the same region",
                  page.evaluate("() => document.querySelectorAll('#details .maskwrap .mask').length")
                  == 1)
            if video_ok:
                page.evaluate("() => openStoryboard('videos/mp4/clip.mp4')")
                page.wait_for_selector("#story-grid .storyframe", timeout=30000)
                shown = page.evaluate("() => document.querySelectorAll('#story-grid .storyframe').length")
                check("Storyboard shows the clip's frames", shown == video_frames.BOARD_COUNT, shown)
                page.click("#story-grid .storyframe:nth-child(25)")
                page.wait_for_timeout(1500)
                # Chromium as Playwright ships it plays no MPEG-4 Part 2, so
                # the seek it was asked for is read back rather than played.
                at = page.evaluate("() => { const v = document.querySelector('#full-body video'); "
                                   "return v ? parseFloat(v.dataset.startAt) : -1; }")
                check("and a frame opens the video at its moment", 4.0 < at < 6.5, at)
            check("no script error", not thrown, thrown[:2])
            browser.close()
    server.shutdown()

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
