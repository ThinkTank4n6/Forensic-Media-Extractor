"""A case stays usable after any analysis has run on it.

_case_for() hands back the one CaseDB the program keeps for a case, shared by
every request. Five routes -- Find faces, face search, Others like this,
Content Credentials and Find edited copies -- called close() on it when they
finished. Every later request on that case then failed with "Cannot operate on
a closed database": grading, opening the case, the audit log, hash checks,
similar images and screening, until the program was restarted. Found by
pressing every button on a finished case (v2026.11.285).
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
PORT = 8797
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


# The rule, read from the source: a handle from _case_for is never closed.
for name in sorted(os.listdir(os.path.join(PROJECT, "fme", "web"))):
    if name.endswith(".py"):
        text = open(os.path.join(PROJECT, "fme", "web", name), encoding="utf-8").read()
        found = [m.start() for m in re.finditer(r"\bcase\.close\(\)", text)]
        check(f"fme/web/{name} closes no shared case handle", not found,
              f"{len(found)} case.close()" if found else "")

# And the handle itself: a borrower's close() leaves a shared case open.
sys.path.insert(0, PROJECT)
import shutil, tempfile                                       # noqa: E402
from fme.review import casedb                                 # noqa: E402
_scratch = os.path.join(tempfile.gettempdir(), "fme_shared_case-") + str(os.getpid())
shared = casedb.open_case(_scratch)
shared.shared = True
shared.close()
try:
    shared.categories()
    check("a shared case handle survives a borrower's close()", True)
except Exception as exc:                                      # noqa: BLE001
    check("a shared case handle survives a borrower's close()", False, str(exc))
shared.close(force=True)
own = casedb.open_case(_scratch)
own.close()
try:
    own.categories()
    check("a handle nobody shares still closes", False)
except Exception:                                             # noqa: BLE001
    check("a handle nobody shares still closes", True)
shutil.rmtree(_scratch, ignore_errors=True)

with socket.socket() as probe:
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"port {PORT} is in use; the running checks are skipped")
        sys.exit(0 if ok else 1)


def call(path, body=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-FME-Request": "1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as answer:
            return answer.status, json.loads(answer.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {}


server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(150):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/capabilities", timeout=2)
            break
        except Exception:                                   # noqa: BLE001
            time.sleep(0.3)
    image = os.path.join(HERE, "media_test.img")
    call("/api/use", {"path": image})
    job = call("/api/start", {"path": image, "mode": "both"})[1]["job_id"]
    while call(f"/api/status?job_id={job}")[1]["status"] in ("running", "starting"):
        time.sleep(0.5)
    listed = call(f"/api/status?job_id={job}")[1]["files"]
    picture = next(f for f in listed if f.get("category") == "image")
    digest = picture["sha256"]

    # The two of the five that need no model run here; the other three close
    # only after their model has run, and the source check above covers them.
    for label, path in (("Content Credentials", "/api/credentials/scan"),
                        ("Find edited copies", "/api/editmatch")):
        status, _ = call(path, {"job": job, "file": picture["rel_path"]})
        graded, _ = call("/api/case/categorize", {"job": job, "sha256": [digest], "category": 3})
        opened, _ = call(f"/api/case?job={job}")
        check(f"after {label} ({status}), a file can still be graded", graded == 200, str(graded))
        check(f"and the case still opens", opened == 200, str(opened))
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()

if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
