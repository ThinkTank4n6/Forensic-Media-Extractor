"""Keys pressed in a dialog stay in the dialog, and Escape closes it.

The grading keys skipped text boxes and nothing else: with Settings, Report,
Export, Hex or Logo open and a button or dropdown focused, a digit graded the
file hidden behind the dialog, Ctrl-Z undid the last grading and Ctrl-A
selected a page. Reproduced in Chromium on v2026.11.286: "3" pressed in
Settings graded the selected file 3. And Escape closed no dialog.

Needs playwright and its Chromium; says so and stops where they are absent.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import os
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed; skipping the browser checks.")
    sys.exit(0)

ok = True
PORT = 8799


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


with socket.socket() as probe:
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"port {PORT} is in use; skipping")
        sys.exit(0)

server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(150):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/capabilities", timeout=2)
            break
        except Exception:                                  # noqa: BLE001
            time.sleep(0.3)
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:                           # noqa: BLE001
            print(f"Chromium will not launch here: {str(exc)[:120]}")
            sys.exit(0)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        thrown = []
        page.on("pageerror", lambda error: thrown.append(str(error)))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.evaluate("""async (path) => { const used = await api('/api/use', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
            setActive(path, used.info); document.getElementById('btn-start').click(); }""",
            os.path.join(HERE, "media_test.img"))
        page.wait_for_function(
            "() => /^done/.test(document.getElementById('status').innerText)", timeout=120000)
        page.evaluate("() => showTab('review')")
        page.wait_for_timeout(500)
        page.click("[data-pick]")
        page.wait_for_timeout(300)
        digest = page.evaluate("() => lookup(selectedPath).sha256")
        grade = lambda: page.evaluate("(d) => (decisions[d] || {}).category ?? null", digest)
        shown = lambda id_: page.evaluate(
            "(i) => getComputedStyle(document.getElementById(i)).display !== 'none'", id_)

        page.evaluate("() => openSettings()")
        page.wait_for_timeout(800)
        page.focus("#settings-modal button")
        page.keyboard.press("3")
        page.wait_for_timeout(600)
        check("a digit pressed in Settings grades nothing behind it", grade() is None, str(grade()))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape closes Settings", not shown("settings-modal"))

        page.evaluate("() => openReport()")
        page.wait_for_timeout(800)
        check("Report opens", shown("report-modal"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape closes Report", not shown("report-modal"))
        page.evaluate("() => openReport()")
        page.wait_for_timeout(500)
        check("and Report opens again after it", shown("report-modal"))
        page.keyboard.press("Escape")

        page.click("[data-pick]")
        page.keyboard.press("3")
        page.wait_for_timeout(800)
        check("with no dialog open, a digit still grades", grade() == 3, str(grade()))

        # The full size viewer is left out of "the dialog in front" on
        # purpose: grading from it is how it is used. That also left Escape
        # with nothing to close, so it fell through to the gallery and
        # cleared the selection -- the viewer stayed open on a file that was
        # no longer selected, and the next grading key had nothing to grade.
        # Grading moves the cursor on, so the file shown is whatever is
        # selected now -- read back that file's grade, not the first one's.
        shown_digest = page.evaluate(
            "() => { const f = lookup(selectedPath);"
            " viewFullSize(f); return f.sha256; }")
        page.wait_for_timeout(500)
        check("the full size viewer opens", shown("full-modal"))
        page.keyboard.press("4")
        page.wait_for_timeout(800)
        viewed_grade = page.evaluate("(d) => (decisions[d] || {}).category ?? null",
                                     shown_digest)
        check("a digit in the full size viewer grades the file it shows",
              viewed_grade == 4, str(viewed_grade))
        page.evaluate("() => viewFullSize(lookup(selectedPath))")
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape closes the full size viewer", not shown("full-modal"))
        check("and leaves the file it showed selected",
              page.evaluate("() => !!selectedPath && selection.size > 0"))

        # The examiner's own categories, from the Classification pane: added
        # with Enter, digits typed into the name grade nothing, then renamed
        # and deleted through the pane's own buttons.
        page.on("dialog", lambda d: d.accept("Renamed 42") if d.type == "prompt" else d.accept())
        page.click("#btn-classify")
        page.wait_for_timeout(500)
        page.evaluate("() => document.getElementById('cat-new-name').scrollIntoView()")
        before = grade()
        page.fill("#cat-new-name", "Seized phone 2")
        page.press("#cat-new-name", "Enter")
        page.wait_for_timeout(800)
        names = page.evaluate("() => categories.map(c => c.name)")
        check("a category is added from the pane", "Seized phone 2" in names)
        check("and the digit typed in its name graded nothing", grade() == before, str(grade()))
        code = page.evaluate("() => categories.find(c => c.name === 'Seized phone 2').code")
        page.click(f"[data-cat-rename='{code}']")
        page.wait_for_timeout(800)
        check("it can be renamed", page.evaluate(
            "(c) => (categories.find(x => x.code === c) || {}).name", code) == "Renamed 42")
        page.click(f"[data-cat-delete='{code}']")
        page.wait_for_timeout(800)
        check("and deleted", not page.evaluate("(c) => categories.some(x => x.code === c)", code))
        check("the scheme's own categories offer no Rename or Delete",
              page.evaluate("() => categories.filter(c => c.locked).every(c => !document.querySelector(`[data-cat-delete='${c.code}']`))"))
        label = page.evaluate("() => { ribbonTab = 'file'; renderRibbon(); return [...document.querySelectorAll('#ribbon-groups [data-go]')].map(e => e.innerText).join('|'); }")
        check("the File ribbon says Custom Signatures, not Categories",
              "Custom Signatures" in label and "Categories" not in label)
        check("nothing the page did threw", not thrown, "; ".join(thrown[:2]))
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()

if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
