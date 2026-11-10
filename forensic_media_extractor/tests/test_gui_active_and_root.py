"""What a run does to the accepted image and the results folder, while it
runs and when it ends.

- A run that finished cleared the accepted image whatever it was, so an
  image the examiner had accepted during the run was forgotten.
- Releasing the image while a run was still reading it said "the program is
  no longer holding it", and let go of nothing.
- A Start that was refused as busy had already moved the results folder for
  every later job, and a job's folder was read from that shared setting
  after the request had changed it.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import json
import logging
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)

from fme.web import media_gui  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_active_root-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
first = os.path.join(ROOT, "first.dd")
other = os.path.join(ROOT, "other.dd")
with open(first, "wb") as fh:
    fh.write(os.urandom(64 << 20))           # enough carving to still be running
with open(other, "wb") as fh:
    fh.write(os.urandom(1 << 20))

HEADERS = {"Content-Type": "application/json", "X-FME-Request": "1"}
client = media_gui.app.test_client()


def accept(path):
    with media_gui.ACTIVE_LOCK:
        media_gui.ACTIVE.update(path=os.path.abspath(path), info=None)


def active():
    with media_gui.ACTIVE_LOCK:
        return media_gui.ACTIVE["path"]


def start(path, root):
    return client.post("/api/start", headers=HEADERS, data=json.dumps(
        {"path": path, "output_root": root, "mode": "carve", "workers": 1}))


def check(label, good):
    global ok
    ok &= bool(good)
    print(f"   {label}: {'ok' if good else 'WRONG'}")


root_a = os.path.join(ROOT, "out-a")
root_b = os.path.join(ROOT, "out-b")
accept(first)
answer = start(first, root_a)
job = answer.get_json().get("job_id")
print("a run starts")
with media_gui.JOBS_LOCK:
    out_dir = media_gui.JOBS[job]["out_dir"]
check("its case is in the folder it asked for",
      os.path.dirname(out_dir) == os.path.abspath(root_a))

print("\na second Start on the same image, with another folder, while it runs")
refused = start(first, root_b)
check("is refused", refused.status_code == 409)
check("and has not moved the folder later jobs use",
      media_gui.OUT_ROOT == os.path.abspath(root_a))

print("\nreleasing the image while the run reads it")
release = client.post("/api/release", headers=HEADERS, data="{}")
check("is refused, saying a run is reading it",
      release.status_code == 409 and "still reading" in release.get_json()["error"])
check("and the image stays accepted", active() == os.path.abspath(first))

print("\nanother request changes the folder while this Start is being handled")
# Simulated deterministically: the moment this Start makes its job id,
# another request points later jobs at root_b.
real_uuid4 = media_gui.uuid.uuid4
racer_root = os.path.join(ROOT, "out-racer")


def uuid4_while_another_request_moves_the_folder():
    media_gui.set_output_root(os.path.join(ROOT, "out-elsewhere"))
    return real_uuid4()


accept(other)
media_gui.uuid.uuid4 = uuid4_while_another_request_moves_the_folder
try:
    raced = start(other, racer_root)
finally:
    media_gui.uuid.uuid4 = real_uuid4
raced_job = raced.get_json().get("job_id")
with media_gui.JOBS_LOCK:
    raced_dir = media_gui.JOBS[raced_job]["out_dir"]
check("its case still goes in the folder it asked for",
      os.path.dirname(raced_dir) == os.path.abspath(racer_root))
for _ in range(400):
    with media_gui.JOBS_LOCK:
        if media_gui.JOBS[raced_job]["status"] != "running":
            break
    time.sleep(0.25)

print("\nthe examiner accepts another image while the run goes on")
accept(other)
for _ in range(1200):
    with media_gui.JOBS_LOCK:
        status = media_gui.JOBS[job]["status"]
    if status != "running":
        break
    time.sleep(0.25)
check(f"the run ends ({status})", status in ("done", "canceled"))
check("and the image accepted during it is still accepted",
      active() == os.path.abspath(other))

print("\nreleasing with nothing running")
release = client.post("/api/release", headers=HEADERS, data="{}")
body = release.get_json()
check("lets go of it", release.status_code == 200 and body.get("ok")
      and body.get("released") == os.path.abspath(other) and active() is None)

time.sleep(1)       # let the thumbnail prefill thread finish with the case
shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.stdout.flush()
os._exit(0 if ok else 1)
