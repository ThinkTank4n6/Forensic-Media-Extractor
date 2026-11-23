"""Griffeye's wellbeing list, in FME (v2026.11.336).

Griffeye's "Prioritizing wellbeing" sets out what a review program should do
for the people using it. FME had most of it -- the review blur, hiding the
screen, grayscale, masks, muted video, pre-categorization -- and not these:

  * the review blur can pixelate instead, and hover still lifts it;
  * "hide everything when a case opens": a case opened shows nothing until
    Ctrl-B, and so does the program when it opens;
  * a reminder to take a break after a set time of work, counted from
    activity; "Take a break" hides the screen, "Remind me in 10 minutes"
    puts it off, and "never" turns it off;
  * all of it remembered on this machine, and none of it sends a write.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import hashlib
import io
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

try:
    from playwright.sync_api import sync_playwright
    from PIL import Image
except ImportError:
    print("(not checked: playwright or Pillow is not installed)")
    sys.exit(0)

ROOT = os.path.join(tempfile.gettempdir(), "fme_wellbeing-") + str(os.getpid())
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
for name, color in (("a_wide.jpg", (190, 40, 40)), ("b_next.jpg", (40, 90, 190))):
    picture = Image.new("RGB", (800, 400), color)
    for x in range(0, 800, 40):
        for y in range(0, 400, 40):
            if (x + y) // 40 % 2:
                picture.putpixel((x, y), (255, 255, 255))
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=90)
    with open(os.path.join(ROOT, "src", name), "wb") as fh:
        fh.write(buffer.getvalue())

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
    api("/api/use", {"path": os.path.join(ROOT, "src")})
    job = api("/api/start", {"out_dir": os.path.join(ROOT, "results")})["job_id"]
    for _ in range(200):
        state = api(f"/api/status?job_id={job}")
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.3)
    case_dir = state["out_dir"]

    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"(not checked: Chromium will not launch: {str(exc)[:100]})")
            sys.exit(0)
        context = browser.new_context(viewport={"width": 1500, "height": 950})
        page = context.new_page()
        thrown, writes = [], []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.evaluate("(dir) => openCase(dir)", case_dir)
        page.wait_for_function("() => document.querySelectorAll('#gallery [data-pick]').length === 2",
                               timeout=30000)
        page.on("request", lambda r: writes.append(f"{r.method} {r.url}")
                if r.method != "GET" and "/api/gallery/query" not in r.url
                and "/api/cases/open" not in r.url else None)

        print("Pixelated")
        page.evaluate("() => { const b = $('blur-on'); if (!b.checked) b.click(); "
                      "const s = $('blur-scope'); s.value = 'all'; "
                      "s.dispatchEvent(new Event('change')); }")
        page.wait_for_function("() => document.querySelector('#gallery .card.blurred')", timeout=5000)
        style = "() => getComputedStyle(document.querySelector('#gallery .card.blurred .thumb img')).filter"
        check("blurred to begin with", "blur(" in page.evaluate(style), page.evaluate(style))
        page.evaluate("() => { const s = $('blur-style'); s.value = 'pixel'; "
                      "s.dispatchEvent(new Event('change')); }")
        check("pixelated when chosen", "fme-pixelate" in page.evaluate(style), page.evaluate(style))
        check("and the filter it names is on the page",
              page.evaluate("() => !!document.getElementById('fme-pixelate')"))
        page.hover("#gallery .card.blurred .thumb img")
        page.wait_for_timeout(200)
        check("hovering a tile still shows it",
              page.evaluate(style) in ("none", ""), page.evaluate(style))
        page.mouse.move(5, 5)

        print("Hide everything when a case opens")
        page.evaluate("() => { const h = $('hide-on-open'); if (!h.checked) h.click(); }")
        page.evaluate("() => hideScreen(false)")
        page.evaluate("(dir) => openCase(dir)", case_dir)
        page.wait_for_timeout(1500)
        check("a case opened shows nothing",
              page.evaluate("() => document.body.classList.contains('hidden-screen')"))
        page.keyboard.press("Control+b")
        check("until Ctrl-B", not page.evaluate("() => document.body.classList.contains('hidden-screen')"))
        page.reload(wait_until="networkidle")
        check("and the program opens hidden",
              page.evaluate("() => document.body.classList.contains('hidden-screen')"))
        check("both remembered on this machine",
              page.evaluate("() => $('blur-style').value === 'pixel' && $('hide-on-open').checked"))
        page.keyboard.press("Control+b")

        print("Take a break")
        page.evaluate("() => { breakEvery = 0.02; workSince = 0; noteActivity(); }")  # 1.2 s
        check("not before its time",
              not page.evaluate("() => $('break-modal').classList.contains('open')"))
        page.keyboard.press("Shift")
        page.wait_for_function("() => $('break-modal').classList.contains('open')", timeout=6000)
        check("the reminder comes after working for the time set", True)
        page.click("#break-snooze")
        page.wait_for_timeout(2200)
        check("put off for ten minutes, it does not come straight back",
              not page.evaluate("() => $('break-modal').classList.contains('open')"))
        page.evaluate("() => { breakSnoozed = Date.now() - 1; noteActivity(); }")
        page.wait_for_function("() => $('break-modal').classList.contains('open')", timeout=6000)
        page.click("#break-take")
        check("taking the break hides the screen",
              page.evaluate("() => document.body.classList.contains('hidden-screen')"
                            " && !$('break-modal').classList.contains('open')"))
        page.keyboard.press("Control+b")
        page.evaluate("() => { lastActive = Date.now() - 6 * 60 * 1000; }")
        page.wait_for_timeout(1500)
        check("time away is a break already: no reminder while away",
              not page.evaluate("() => $('break-modal').classList.contains('open')"))
        page.evaluate("() => { const s = $('break-every'); s.value = '0'; "
                      "s.dispatchEvent(new Event('change')); workSince = 1; noteActivity(); }")
        page.wait_for_timeout(1500)
        check("set to never, it never comes",
              not page.evaluate("() => $('break-modal').classList.contains('open')"))
        check("the default is an hour on a new machine",
              page.evaluate("() => { localStorage.removeItem('fme.breakEvery'); return true; }"))
        page.reload(wait_until="networkidle")
        check("an hour", page.evaluate("() => $('break-every').value") == "60",
              page.evaluate("() => $('break-every').value"))

        check("nothing was written", not writes, writes[:5])
        check("no script errors", not thrown, thrown[:3])
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:  # noqa: BLE001
        server.kill()
    shutil.rmtree(ROOT, ignore_errors=True)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
