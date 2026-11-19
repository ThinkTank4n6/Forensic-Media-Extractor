"""A run's options are locked while it runs (v2026.11.335).

A run reads its options once, when Start is pressed. They stayed open while
it ran, and an examiner who ticked "Extract media from SQLite databases"
after starting took it that the run would now do it: it did not, and nothing
said so. While a run is going they are locked, with a line saying a change
applies to the next run; when it ends they open again.

The browser holds the run's progress answers back so the run is seen while
it is still running, then lets them through.
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
sys.path.insert(0, HERE)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("(not checked: playwright is not installed)")
    sys.exit(0)

import make_media_test_image as fixtures  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_run_options-") + str(os.getpid())
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
os.makedirs(os.path.join(ROOT, "src"))
for index in range(3):
    with open(os.path.join(ROOT, "src", f"p{index}.jpg"), "wb") as fh:
        fh.write(fixtures.make_jpeg(1400 + index))

PORT = free_port()
server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(150):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/capabilities", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"(not checked: Chromium will not launch: {str(exc)[:100]})")
            sys.exit(0)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        thrown = []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.evaluate("() => showTab('review')")
        page.fill("#out-root", os.path.join(ROOT, "results"))

        check("before a run, the SQLite box can be changed", page.is_enabled("#databases"))
        held = []
        page.route("**/api/status?job_id=*", lambda route: held.append(route))
        page.evaluate("""async (p) => { const used = await api('/api/use', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: p})});
            setActive(p, used.info); document.getElementById('btn-start').click(); }""",
                      os.path.join(ROOT, "src"))
        page.wait_for_function("() => running === true", timeout=20000)
        page.wait_for_timeout(300)
        print("While the run is going")
        check("the SQLite box is locked", page.is_disabled("#databases"))
        check("and so is every other option the run read",
              page.evaluate("() => RUN_OPTIONS.every(id => !document.getElementById(id) "
                            "|| document.getElementById(id).disabled)"))
        check("a line says a change applies to the next run",
              page.is_visible("#run-options-note")
              and "next run" in page.inner_text("#run-options-note"))
        for route in list(held):
            route.continue_()
        page.unroute("**/api/status?job_id=*")
        page.wait_for_function("() => running === false", timeout=60000)
        page.wait_for_timeout(300)
        print("When it has finished")
        check("the options open again", page.is_enabled("#databases"))
        check("and the line goes", not page.is_visible("#run-options-note"))
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
