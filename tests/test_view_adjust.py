"""Rotate, brightness and contrast in the full-size viewer (v2026.11.336).

Asked for by the examiner: a sideways photo turned upright, a dark one
lifted, in the viewer a double-click opens. A way of looking, like zoom:
nothing is written to the file, the case, an export or a report.

Checked in Chromium, on a picture wider than it is tall:
  * turned a quarter, it is as tall as it was wide, and fits the frame;
  * R and Shift+R turn it; zoom and drag still keep it in the frame;
  * brightness is on the wrapper, so the review blur on the picture stays;
  * a slider gives the keyboard back once set, so the next grading key works;
  * the next file starts upright, and still lifted;
  * masks cannot be drawn on a turned picture, and Reset view puts it back;
  * not one request that writes is sent, and the file's bytes are unchanged.
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

ROOT = os.path.join(tempfile.gettempdir(), "fme_view_adjust-") + str(os.getpid())
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


RECT = """(sel) => { const r = document.querySelector(sel).getBoundingClientRect();
                     return [r.left, r.top, r.width, r.height]; }"""

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
    stored = {f["name"] if "name" in f else os.path.basename(f["out_path"]): f
              for f in json.load(open(os.path.join(case_dir, "manifest.json"),
                                      encoding="utf-8"))["files"]}
    on_disk = {}
    for f in stored.values():
        with open(os.path.join(case_dir, f["out_path"]), "rb") as fh:
            on_disk[f["out_path"]] = hashlib.sha256(fh.read()).hexdigest()

    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"(not checked: Chromium will not launch: {str(exc)[:100]})")
            sys.exit(0)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        thrown, writes = [], []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.evaluate("(dir) => openCase(dir)", case_dir)
        page.wait_for_function("() => document.querySelectorAll('#gallery [data-pick]').length === 2",
                               timeout=30000)
        page.locator("#gallery .card a.thumb").first.dblclick()
        page.wait_for_function("() => { const i = document.querySelector('#full-body img');"
                               " return i && i.complete && i.naturalWidth > 0; }", timeout=15000)
        page.wait_for_timeout(300)
        # Anything but a GET -- save the one POST that only reads: a case
        # paged from the server asks for its pages with it.
        page.on("request", lambda r: writes.append(f"{r.method} {r.url}")
                if r.method != "GET" and "/api/gallery/query" not in r.url else None)

        body = page.evaluate(RECT, "#full-body")
        upright = page.evaluate(RECT, "#full-body img")
        middle = (body[0] + body[2] / 2, body[1] + body[3] / 2)
        print("Zoom, upright")
        page.click("#zoom-in")
        page.click("#zoom-in")
        page.wait_for_timeout(300)
        zoomed = page.evaluate(RECT, "#full-body img")
        # Zoomed about the middle, the middle stays put: the picture's middle
        # is where it was. It went 290 px to the right before v2026.11.336.
        def centered(rect):
            return (abs(rect[0] + rect[2] / 2 - middle[0]) < 4
                    and abs(rect[1] + rect[3] / 2 - middle[1]) < 4)
        check("zoomed in, an upright picture keeps its middle in the middle of the frame",
              centered(zoomed) and zoomed[2] > upright[2] * 1.5,
              f"middle at {zoomed[0] + zoomed[2] / 2:.0f},{zoomed[1] + zoomed[3] / 2:.0f}; "
              f"frame's at {middle[0]:.0f},{middle[1]:.0f}")
        page.click("#zoom-fit")
        page.wait_for_timeout(200)
        print("Turned a quarter")
        page.click("#view-rotate-right")
        page.wait_for_timeout(300)
        turned = page.evaluate(RECT, "#full-body img")
        check("the wide picture is now taller than it is wide",
              turned[3] > turned[2] * 1.5, f"{turned[2]:.0f} x {turned[3]:.0f}")
        check("and fits the frame", turned[0] >= body[0] - 1 and turned[1] >= body[1] - 1
              and turned[0] + turned[2] <= body[0] + body[2] + 1
              and turned[1] + turned[3] <= body[1] + body[3] + 1, [round(v) for v in turned])
        check("the viewer says it is turned", "90" in page.inner_text("#view-rotation"))
        page.keyboard.press("r")
        page.wait_for_timeout(200)
        check("R turns it on, to 180", "180" in page.inner_text("#view-rotation"))
        page.keyboard.press("Shift+R")
        page.wait_for_timeout(200)
        check("Shift+R turns it back, to 90", "90" in page.inner_text("#view-rotation")
              and "180" not in page.inner_text("#view-rotation"))

        page.click("#zoom-in")
        page.click("#zoom-in")
        page.wait_for_timeout(300)
        zoomed = page.evaluate(RECT, "#full-body img")
        check("zoomed in, the turned picture keeps its middle in the middle too",
              centered(zoomed) and zoomed[3] > turned[3] * 1.5,
              f"middle at {zoomed[0] + zoomed[2] / 2:.0f},{zoomed[1] + zoomed[3] / 2:.0f}")
        page.click("#zoom-fit")
        check("drawing a mask is off while it is turned", page.is_disabled("#mask-draw"))

        print("Brightness and contrast")
        page.evaluate("""() => { const s = document.getElementById('view-bright');
            s.focus(); s.value = 160; s.dispatchEvent(new Event('input'));
            s.dispatchEvent(new Event('change')); }""")
        page.wait_for_timeout(200)
        wrap_filter = page.evaluate("getComputedStyle(document.querySelector('#full-body .maskwrap')).filter")
        check("brightness is on the wrapper", "brightness(1.6)" in wrap_filter, wrap_filter)
        check("the slider gives the keyboard back once set",
              page.evaluate("document.activeElement.tagName") != "INPUT",
              page.evaluate("document.activeElement.tagName"))
        page.evaluate("() => { const b = document.getElementById('blur-on');"
                      " if (!b.checked) b.click(); }")
        page.evaluate("() => document.getElementById('full-body').classList.add('blurred')")
        picture_filter = page.evaluate("getComputedStyle(document.querySelector('#full-body img')).filter")
        check("the review blur on the picture is still there", "blur" in picture_filter, picture_filter)
        page.evaluate("() => document.getElementById('full-body').classList.remove('blurred')")

        print("The next file")
        page.keyboard.press("ArrowRight")
        page.wait_for_function("() => { const i = document.querySelector('#full-body img');"
                               " return i && i.complete && i.naturalWidth > 0; }", timeout=15000)
        page.wait_for_timeout(300)
        nxt = page.evaluate(RECT, "#full-body img")
        check("starts upright", page.inner_text("#view-rotation") == "" and nxt[2] > nxt[3],
              f"{nxt[2]:.0f} x {nxt[3]:.0f}")
        check("and is still lifted",
              "brightness(1.6)" in page.evaluate(
                  "getComputedStyle(document.querySelector('#full-body .maskwrap')).filter"))
        page.click("#view-rotate-left")
        page.click("#view-reset")
        page.wait_for_timeout(200)
        back = page.evaluate(RECT, "#full-body img")
        check("Reset view puts it back as it was",
              page.evaluate("getComputedStyle(document.querySelector('#full-body .maskwrap')).filter")
              == "none" and abs(back[2] - upright[2]) < 2 and abs(back[3] - upright[3]) < 2,
              f"{back[2]:.0f} x {back[3]:.0f}")
        check("and drawing a mask is on again", page.is_enabled("#mask-draw"))

        print("Nothing written")
        check("no request that writes was sent", not writes, writes[:3])
        changed = []
        for path, digest in on_disk.items():
            with open(os.path.join(case_dir, path), "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != digest:
                    changed.append(path)
        check("the files' bytes are unchanged", not changed, changed)
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
