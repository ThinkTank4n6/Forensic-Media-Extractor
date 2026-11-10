"""Only this program's own page can drive the local GUI.

The GUI listens on 127.0.0.1, and any website open in the examiner's
browser can send requests there. Every POST was read with
get_json(force=True), so a request marked text/plain -- which a browser
sends cross-site without asking first -- was taken as JSON, and nothing
checked where it came from. Once an image was accepted, one such request
started an extraction and pointed its output at a folder the sender chose.
Nothing checked the Host header either, so a site re-pointing its own name
at 127.0.0.1 could read case media and listings too.

This drives both Flask apps (the media GUI and the FAT32 browser) the way
a browser would, from this program's page and from another website.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

import logging
logging.disable(logging.CRITICAL)

from fme.web import media_gui, request_guard

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_request_guard-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
EVIL = "https://attacker.example"
OURS = {"X-FME-Request": "1"}


def check(label: str, passed: bool) -> None:
    global ok
    ok &= passed
    print(f"   {label}: {passed}")


gui = media_gui.app.test_client()
image = os.path.join(PROJECT, "media_test.img")
with media_gui.ACTIVE_LOCK:
    media_gui.ACTIVE.update(path=os.path.abspath(image), info=None)
root_before = media_gui.OUT_ROOT
with media_gui.JOBS_LOCK:
    jobs_before = set(media_gui.JOBS)
target = os.path.join(ROOT, "chosen_by_attacker")
payload = json.dumps({"output_root": target})

print("a request from another website, as a browser sends it")
r = gui.post("/api/start", data=payload,
             headers={"Content-Type": "text/plain", "Origin": EVIL})
check("refused with 403", r.status_code == 403)
check("and says why in the usual shape", (r.get_json() or {}).get("ok") is False)
check("the output folder is unchanged", media_gui.OUT_ROOT == root_before)
check("nothing was written where it asked", not os.path.exists(target))
with media_gui.JOBS_LOCK:
    check("no job was started", set(media_gui.JOBS) == jobs_before)

r = gui.post("/api/start", data=payload,
             headers={"Content-Type": "application/json", "Origin": EVIL, **OURS})
check("still refused if it somehow carries the header", r.status_code == 403)

r = gui.post("/api/cancel", data=json.dumps({"job_id": "x"}),
             headers={"Content-Type": "text/plain"})
check("a change with no header and no Origin is refused", r.status_code == 403)

print("\na site that has re-pointed its own name at this machine")
r = gui.get("/api/health", headers={"Host": "attacker.example:5001"})
check("a read is refused under a name that is not this machine's", r.status_code == 403)
r = gui.post("/api/cancel", data=json.dumps({"job_id": "x"}),
             headers={"Host": "attacker.example:5001",
                      "Origin": "http://attacker.example:5001", **OURS})
check("and so is a change, header or not", r.status_code == 403)

print("\nthis program's own page")
r = gui.get("/api/health")
check("a read needs nothing extra", r.status_code != 403)
r = gui.post("/api/cancel", data=json.dumps({"job_id": "no-such-job"}),
             headers={"Content-Type": "application/json",
                      "Origin": "http://localhost", **OURS})
check("a change with the header and its own Origin gets through", r.status_code != 403)
r = gui.get("/api/health", headers={"Host": "[::1]:5001"})
check("the IPv6 loopback name is this machine too", r.status_code != 403)
page = gui.get("/").get_data(as_text=True)
check("the page it serves sends the header", "X-FME-Request" in page)

print("\na read that names a path on this machine (v2026.11.318)")
for route in ("/api/content/model?path=/dev/zero", "/api/inspect?path=/etc/hostname",
              "/api/browse?path=/"):
    r = gui.get(route, headers={"Origin": EVIL})
    check(f"{route.split('?')[0]} from another website, no header: refused",
          r.status_code == 403)
r = gui.get("/api/browse?path=/", headers=OURS)
check("and from this program's page: answered", r.status_code == 200)
r = gui.get("/api/content/model?path=/dev/zero", headers=OURS)
check("a device is not hashed as a model, even from the page",
      r.status_code == 200 and not r.get_json()["ready"])

print("\nreaching it from another machine has to be asked for")
r = gui.get("/api/health", headers={"Host": "forensics-ws07:5001"})
refused_before = r.status_code == 403
request_guard.allow(media_gui.app, "forensics-ws07")
r = gui.get("/api/health", headers={"Host": "forensics-ws07:5001"})
check("refused until allowed, then answered", refused_before and r.status_code != 403)
media_gui.app.config["FME_ALLOWED_HOSTS"].discard("forensics-ws07")
request_guard.allow(media_gui.app, "0.0.0.0")
check("binding every address does not allow every name",
      "0.0.0.0" not in media_gui.app.config["FME_ALLOWED_HOSTS"])

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
