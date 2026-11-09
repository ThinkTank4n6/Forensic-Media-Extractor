"""The GUI runs one extraction of an image at a time.

A second Start on the same image -- a double click was enough -- began a
second extraction into a second case, and the first to finish released the
image while the other was still reading it. A second Start is now refused
until the first run ends, including two Starts sent at the same moment.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
logging.disable(logging.CRITICAL)

from fme.web import media_gui

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_one_run-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
image = os.path.join(ROOT, "slow.dd")
with open(image, "wb") as fh:
    fh.write(os.urandom(96 << 20))           # enough carving to still be running

HEADERS = {"Content-Type": "application/json", "X-FME-Request": "1"}
client = media_gui.app.test_client()
with media_gui.ACTIVE_LOCK:
    media_gui.ACTIVE.update(path=os.path.abspath(image), info=None)
body = json.dumps({"path": image, "output_root": os.path.join(ROOT, "out"),
                   "mode": "carve", "workers": 1})


def start():
    return client.post("/api/start", data=body, headers=HEADERS)


print("two Starts at the same moment")
answers = []
threads = [threading.Thread(target=lambda: answers.append(start())) for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
codes = sorted(r.status_code for r in answers)
right = codes == [200, 409]
ok &= right
print(f"   one starts and one is refused: {right} {codes}")
job = next(r.get_json()["job_id"] for r in answers if r.status_code == 200)

print("\na Start while the run is going")
again = start()
right = again.status_code == 409 and "already being extracted" in again.get_json()["error"]
ok &= right
print(f"   refused, saying why: {right}")
with media_gui.ACTIVE_LOCK:
    held = media_gui.ACTIVE["path"] == os.path.abspath(image)
ok &= held
print(f"   and the image is still held for the run: {held}")

for _ in range(600):
    with media_gui.JOBS_LOCK:
        status = media_gui.JOBS[job]["status"]
    if status != "running":
        break
    time.sleep(0.5)
print(f"\nafter the run ends ({status})")
with media_gui.ACTIVE_LOCK:
    media_gui.ACTIVE.update(path=os.path.abspath(image), info=None)
after = start()
right = after.status_code == 200
ok &= right
print(f"   a new Start is accepted: {right}")
new_job = (after.get_json() or {}).get("job_id")
if new_job:
    with media_gui.JOBS_LOCK:
        media_gui.JOBS[new_job]["_cancel"].set()
    for _ in range(120):
        with media_gui.JOBS_LOCK:
            if media_gui.JOBS[new_job]["status"] != "running":
                break
        time.sleep(0.5)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
