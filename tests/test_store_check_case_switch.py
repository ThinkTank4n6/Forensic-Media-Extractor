"""A store check's answer belongs to the case it was for (v2026.11.335).

Since v2026.11.334 the check runs on the server and the page asks after it
each second, minutes on a large case -- and the page looked at whether the
case had changed only before each question, not after it. Opening case B
while case A was being checked put A's result on B: "Store check finished:
3 matched" in the corner and the status line, A's figures in the Hashes
panel, so B looked checked when it was not. And the file list it then read
was assigned to the page whatever case was open, the race v2026.11.319
fixed elsewhere: A's files under B's job, where a grading is written to B.

Here the browser holds the check's answer back while case B is opened, then
lets it through. tools/repro/repro_store_check_wrong_case.py is the same
sequence in twenty lines with nothing real behind it.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("(not checked: playwright is not installed)")
    sys.exit(0)

import make_media_test_image as fixtures  # noqa: E402
from fme.review import hashstore  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_store_switch-") + str(os.getpid())
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


shutil.rmtree(ROOT, ignore_errors=True)
for name, base in (("a", 1400), ("b", 1700)):
    os.makedirs(os.path.join(ROOT, f"src_{name}"))
    for index in range(3):
        with open(os.path.join(ROOT, f"src_{name}", f"{name}{index}.jpg"), "wb") as fh:
            fh.write(fixtures.make_jpeg(base + index * 37))

PORT = free_port()
server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def api(path, payload=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    if payload is None:
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.loads(r.read())
    req = urllib.request.Request(url, method="POST", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-FME-Request": "1"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


try:
    for _ in range(150):
        try:
            api("/api/capabilities")
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    cases = {}
    for name in ("a", "b"):
        api("/api/use", {"path": os.path.join(ROOT, f"src_{name}")})
        job = api("/api/start", {"out_dir": os.path.join(ROOT, f"case_{name}")})["job_id"]
        for _ in range(300):
            state = api(f"/api/status?job_id={job}")
            if state["status"] in ("done", "error"):
                break
            time.sleep(0.4)
        cases[name] = {f["sha256"] for f in state["files"]}
        folders = {**(globals().get("folders") or {}), name: state["out_dir"]}
        globals()["folders"] = folders
        api("/api/release", {})
    store_path = os.path.join(ROOT, "store.db")
    store = hashstore.HashStore(store_path)
    out = json.load(open(os.path.join(folders["a"], "manifest.json"), encoding="utf-8"))
    store.contribute_rows([{"md5": f["md5"], "sha256": f["sha256"], "category": 3,
                            "label": "graded"} for f in out["files"]], "DC26-A")
    store.close()
    api("/api/store", {"path": store_path})

    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"(not checked: Chromium will not launch: {str(exc)[:100]})")
            sys.exit(0 if ok else 1)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        thrown = []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

        def open_case(name):
            # As the case list's Open button does: openCase(its folder).
            page.evaluate("(dir) => { showTab('cases'); openCase(dir); }", folders[name])
            page.wait_for_function(
                "(n) => !!jobId && files.length === n", arg=len(cases[name]), timeout=30000)
            page.wait_for_timeout(800)
            return page.evaluate("jobId")

        job_a = open_case("a")
        # The first time the page asks how the check stands, the answer is
        # held back: that is the minutes a large case takes.
        held = []
        page.route("**/api/store/scan?job=*", lambda route: held.append(route))
        page.evaluate("() => { showTab('hashes'); document.getElementById('store-scan').click(); }")
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        check("the page is waiting on the store check of case A", bool(held))
        job_b = open_case("b")
        check("case B is open", job_b and job_b != job_a)
        page.evaluate("() => { document.getElementById('status').textContent = ''; }")
        # Let the held answer through, and every later one at once.
        release = len(held)
        for route in held[:release]:
            route.continue_()
        page.route("**/api/store/scan?job=*", lambda route: route.continue_())
        page.wait_for_function(
            "() => !document.getElementById('store-scan').disabled", timeout=30000)
        page.wait_for_timeout(1000)

        status = page.inner_text("#status")
        check("case B's status line does not say a check finished",
              "Store check finished" not in status, status[:100])
        corner = page.inner_text("#toast")
        check("the message says the result is the case left's",
              "case you left" in corner, corner.split("\n")[0][:140])
        check("the page is still on case B", page.evaluate("jobId") == job_b)
        shown = set(page.evaluate("files.map(f => f.sha256)"))
        check("and its file list is case B's, not case A's",
              shown == cases["b"], f"{len(shown & cases['a'])} of case A's files on it")
        check("nothing the page did threw", not thrown, "; ".join(thrown[:2]))
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(ROOT, ignore_errors=True)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
