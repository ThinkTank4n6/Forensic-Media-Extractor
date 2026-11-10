"""A video saved as DASH segments beside its manifest is joined back into one.

Read file by file, a folder holding stream.mpd, init-0.mp4 and seg-0-*.m4s
gave a few hundred bytes of initialization segment stored as a "video" that
plays nothing, and dropped every .m4s -- which hold all the pictures --
because a segment begins with a styp box. The fixture is a real DASH
encoding (H.264 video, AAC audio, three seconds) from the comparison tool's
suite (MIT; fme/vendor/vendored.json).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
DASH = os.path.join(HERE, "fixtures", "dash")

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_streams-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def run(folder, name):
    out = os.path.join(ROOT, name)
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", folder, "-o", out],
                   cwd=PROJECT, capture_output=True, timeout=600)
    with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
        return out, json.load(fh)["files"]


def frames(path):
    try:
        import cv2
    except ImportError:
        return None
    capture, n = cv2.VideoCapture(path), 0
    while capture.read()[0]:
        n += 1
    return n


print("a complete DASH download")
folder = os.path.join(ROOT, "offline", "video1")
shutil.copytree(DASH, folder)
out, files = run(os.path.join(ROOT, "offline"), "whole")
joined = [f for f in files if "::" in (f.get("source_path") or "")]
check("one video is joined from the manifest", len(joined) == 1)
if joined:
    rec = joined[0]
    check("from every segment it lists, with its sound", "3 of 3" in rec["note"]
          and "1 audio stream" in rec["note"], rec["note"][:80])
    count = frames(os.path.join(out, rec["out_path"]))
    if count is None:
        print("  not checked: OpenCV is absent, so the frames are not counted")
    else:
        check("and it plays: frames decode", count >= 25, f"{count} frames")
segments = [f for f in files if (f.get("source_path") or "").endswith("init-0.mp4")]
check("the initialization segment is kept, and kept out of the tiles",
      segments and segments[0].get("container"))

print("a download with a segment missing")
partial = os.path.join(ROOT, "partial", "video1")
shutil.copytree(DASH, partial)
os.remove(os.path.join(partial, "seg-0-3.m4s"))
_, files = run(os.path.join(ROOT, "partial"), "partial")
joined = [f for f in files if "::" in (f.get("source_path") or "")]
check("is joined as far as it goes, and says it is partial",
      len(joined) == 1 and "2 of 3" in joined[0]["note"] and "missing" in joined[0]["note"],
      joined[0]["note"][:100] if joined else "")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
