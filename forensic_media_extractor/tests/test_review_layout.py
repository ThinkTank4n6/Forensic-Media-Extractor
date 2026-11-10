"""Reviewing a case, in a real browser at a laptop's size (v2026.11.320).

From the usability half of the v2026.11.319 review, U1-U12:

- with a case open, the tiles start at the top of the screen and the
  grading pane is on screen beside them at 1280 x 720, and picking a file
  does not reflow the gallery;
- double-click and Enter open a file full size; the arrows move; the viewer
  says what the file is graded as;
- Undo reverses a flag set a moment ago, not the grading before it, and
  says which file;
- messages are clear of the ribbon and can be closed; the first review asks
  how pictures are shown, without stopping anything;
- ribbon items and chips are reachable from the keyboard;
- removing a case from the list can be undone.
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
    print("playwright is not installed; skipping the browser checks.")
    sys.exit(0)

import make_media_test_image as fixtures  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_review_layout-") + str(os.getpid())
PORT = 8811
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


with socket.socket() as probe:
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"port {PORT} is in use; skipping")
        sys.exit(0)

shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "src"))
for n in range(12):
    with open(os.path.join(ROOT, "src", f"p{n:02d}.jpg"), "wb") as fh:
        fh.write(fixtures.make_jpeg(2100 + n))

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
            time.sleep(0.4)
    case_dir = os.path.join(ROOT, "case")
    api("/api/use", {"path": os.path.join(ROOT, "src")})
    job = api("/api/start", {"out_dir": case_dir})["job_id"]
    for _ in range(150):
        state = api(f"/api/status?job_id={job}")
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    case_dir = (state.get("summary") or {}).get("output_dir") or case_dir

    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"Chromium will not launch here: {str(exc)[:120]}")
            sys.exit(0)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        thrown = []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.evaluate("async (d) => { await openCase(d); }", case_dir)
        page.wait_for_timeout(1200)

        print("the layout while reviewing")
        check("the extraction form steps aside",
              not page.locator("#controls-col").is_visible())
        check("the first review asks how pictures are shown, as a card",
              page.locator("#wellbeing-card").is_visible()
              and not page.evaluate("!!openDialog()"))
        page.locator("#wellbeing-ok").click()
        check("and answering it puts it away", not page.locator("#wellbeing-card").is_visible())
        width = page.evaluate("document.getElementById('gallery').getBoundingClientRect().width")
        page.locator("#gallery .card a.thumb").first.click()
        page.wait_for_timeout(400)
        check("picking a file does not reflow the gallery",
              abs(page.evaluate("document.getElementById('gallery')"
                                ".getBoundingClientRect().width") - width) < 1)
        tile = page.locator("#gallery .card").first.bounding_box()
        check("the first row of tiles is on screen, not below the fold", tile and tile["y"] + 150 < 720,
              tile and round(tile["y"]))
        pane = page.locator("#details-col").bounding_box()
        check("the details pane is on screen, to the right of the gallery",
              pane and pane["y"] < 720 and pane["x"] > tile["x"] + tile["width"],
              pane and (round(pane["x"]), round(pane["y"])))
        if os.environ.get("SHOT"):
            page.screenshot(path=os.environ["SHOT"])
        first = page.locator("#summary .sumfirst").bounding_box()
        check("the run summary's first line shows whole, not cut in half",
              first and first["height"] >= 14, first and round(first["height"]))
        check("the rest is folded behind a button that says so",
              not page.locator("#summary .sumrest").is_visible()
              and page.locator("#summary .sumtoggle").is_visible())
        page.locator("#summary .sumtoggle").click()
        check("and the button opens it",
              page.locator("#summary .sumrest").is_visible()
              and "Written to" in page.locator("#summary").inner_text())
        page.locator("#summary .sumtoggle").click()
        page.locator("#review-settings").click()
        check("the settings come back on request", page.locator("#controls-col").is_visible())
        page.locator("#review-settings").click()
        squeezed = page.evaluate("""() => [...document.querySelectorAll(
            '#results-col > :not(.resultsscroll)')].filter(e => e.offsetParent
            && e.scrollHeight > e.clientHeight + 1).map(e => e.id || e.className)""")
        check("and with them hidden again, nothing above the tiles is squeezed",
              not squeezed, squeezed)
        # A short window is where it showed: the column's flex box shrank the
        # summary to half a line of cut-off text.
        page.set_viewport_size({"width": 1280, "height": 430})
        page.wait_for_timeout(300)
        short = page.evaluate("""() => { const s = document.querySelector('#summary .sumfirst');
            const r = s.getBoundingClientRect(), box = document.getElementById('summary')
            .getBoundingClientRect(); return [Math.round(r.height), r.bottom <= box.bottom + 1]; }""")
        check("in a short window too, the first line is whole", short[0] >= 14 and short[1], short)
        page.set_viewport_size({"width": 1280, "height": 720})
        page.wait_for_timeout(300)

        print("\nthe viewer")
        page.keyboard.press("Escape")
        page.locator("#gallery .card a.thumb").nth(1).click()
        first = page.evaluate("selectedPath")
        page.locator("body").press("Enter")
        page.wait_for_timeout(300)
        check("Enter opens the selected file full size",
              page.evaluate("document.getElementById('full-modal').classList.contains('open')"
                            " && fullFile.rel_path") == first)
        check("the viewer says what it is graded as",
              "Uncategorized" in page.locator("#full-grade").inner_text(),
              page.locator("#full-grade").inner_text())
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(300)
        moved = page.evaluate("fullFile.rel_path")
        check("→ moves to the next file", moved != first)
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(300)
        check("← moves back", page.evaluate("fullFile.rel_path") == first)
        page.keyboard.press("2")
        page.wait_for_timeout(800)
        check("a grading made in the viewer is confirmed there",
              "✓" in page.locator("#full-confirm").inner_text(),
              page.locator("#full-confirm").inner_text())
        page.keyboard.press("Escape")
        page.locator("#gallery .card a.thumb").nth(3).dblclick()
        page.wait_for_timeout(300)
        check("double-click opens a tile full size",
              page.evaluate("document.getElementById('full-modal').classList.contains('open')"))
        page.keyboard.press("Escape")

        print("\nundo")
        target = page.evaluate("visible()[4]")
        page.evaluate("async (sha) => { await categorize(sha, 1); }", target["sha256"])
        page.wait_for_timeout(500)
        page.evaluate("async ([sha, id]) => { await toggleFlag(sha, id); }",
                      [target["sha256"], page.evaluate("caseFlags[0].id")])
        page.wait_for_timeout(500)
        page.evaluate("async () => { await undoLast(); }")
        page.wait_for_timeout(500)
        state = page.evaluate("(sha) => decisions[sha]", target["sha256"])
        check("Ctrl-Z takes back the flag, not the grading before it",
              state["category"] == 1 and not state.get("flags"), state)
        check("and says which file", target["name"] in page.locator("#status").inner_text(),
              page.locator("#status").inner_text()[:80])
        page.evaluate("async () => { await undoLast(); }")
        page.wait_for_timeout(500)
        check("the next undo takes back the grading",
              page.evaluate("(sha) => (decisions[sha] || {}).category", target["sha256"])
              != 1)

        print("\nmessages and the keyboard")
        page.evaluate("toast('a message')")
        box = page.locator("#toast").bounding_box()
        ribbon = page.locator(".ribbon").bounding_box()
        check("a message is clear of the ribbon",
              box["y"] > ribbon["y"] + ribbon["height"], round(box["y"]))
        page.locator("#toast-close").click()
        check("and closes", not page.locator("#toast").is_visible())
        check("ribbon items and chips can be reached with Tab",
              page.evaluate("[...document.querySelectorAll('.ribbon-item:not(.disabled), "
                            ".chip[data-filter]')].every(e => e.tabIndex === 0)"))
        check("tiles name their file", page.evaluate(
            "[...document.querySelectorAll('#gallery .card img')].every(i => i.alt)"))

        print("\nthe list of cases")
        page.evaluate("async () => { await closeCase(); }")
        page.wait_for_timeout(600)
        check("closing the case leaves reviewing",
              not page.evaluate("document.body.classList.contains('reviewing')"))
        page.locator("[data-forget]").first.click()
        page.wait_for_timeout(600)
        check("removing one offers Undo", page.locator("#forget-undo").count() == 1)
        page.locator("#forget-undo").click()
        page.wait_for_timeout(600)
        check("and Undo puts it back", page.locator("[data-forget]").count() >= 1)
        check("nothing threw", not thrown, thrown[:1])
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
