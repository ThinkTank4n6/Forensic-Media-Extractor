"""Every button and chip in the panels and dialogs, pressed in a real browser.

test_ui_sweep.py presses the ribbon: every item on every ribbon tab, and
every dialog it opens is closed again. It never pressed what is inside the
panels and the dialogs themselves -- the case list, the extraction form, the
gallery's chips and category bar, the Hashes, Similar and Signatures panels,
Settings, the report and export dialogs, the logo dialog, the file picker.
This does (v2026.11.335): each visible, enabled button and chip, one at a
time, from a page reloaded with the case open.

A fault is what the page threw, a 5xx from the server, a traceback in the
server's log, or a dialog that would not close. An error message shown to
the examiner is listed, not failed: "connect a hash store first" is the right
answer on a machine with none, and the list is read by a person.

Buttons that end the session or start a long run are left out by name
(SKIP), and said so in the output.
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

import make_media_test_image as fixtures

ROOT = os.path.join(tempfile.gettempdir(), "fme_ui_panels-") + str(os.getpid())
ok = True

# Pressed elsewhere, or they end what is being tested: by the text they show.
SKIP = ("Start Extraction", "Release", "Close case", "Delete", "Remove",
        "Cancel", "Stop", "Forget", "Clear the", "Reset")
# Where to look, and how to get there. A dialog is opened by the function the
# ribbon calls for it.
PLACES = [
    ("case list", "tab-cases", "showTab('cases')"),
    ("extraction and gallery", "tab-review", "showTab('review')"),
    ("hashes", "tab-hashes", "showTab('hashes')"),
    ("similar", "tab-similar", "showTab('similar')"),
    ("signatures", "tab-sigs", "showTab('sigs')"),
    ("settings", "settings-modal", "openSettings()"),
    ("file export", "export-modal", "openFileExport()"),
    ("report", "report-modal", "openReport()"),
    ("logo", "logo-modal", "openLogo()"),
    ("file picker", "browse-modal",
     "openBrowser(p => {}, '', 'Choose a folder for results')"),
]

PICK = """(holder) => {
  const root = document.getElementById(holder);
  const path = el => {
    if (el.id) return '#' + CSS.escape(el.id);
    const up = el.parentElement;
    const n = [...up.children].indexOf(el) + 1;
    return path(up) + ' > :nth-child(' + n + ')';
  };
  return [...root.querySelectorAll('button, .chip, .catchip')]
    .filter(el => el.offsetParent && !el.disabled
            && !el.closest('.modal-head'))
    .map(el => ({sel: path(el), text: (el.innerText || el.title || '').trim()
                                       .split('\\n')[0].slice(0, 60)}));
}"""

SHOWN = ("[...document.querySelectorAll('.modal-overlay')]"
         ".filter(m => getComputedStyle(m).display !== 'none')"
         ".map(m => m.id)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "src"))
for index in range(6):
    with open(os.path.join(ROOT, "src", f"p{index}.jpg"), "wb") as fh:
        fh.write(fixtures.make_jpeg(1400 + index))

PORT = free_port()
SERVER_LOG = os.path.join(tempfile.gettempdir(), f"fme_ui_panels-server-{os.getpid()}.log")
_log = open(SERVER_LOG, "wb")
server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=_log, stderr=subprocess.STDOUT)


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


results = []        # (place, label, outcome, detail)


def record(place, label, outcome, detail=""):
    global ok
    results.append((place, label, outcome, detail))
    if outcome == "FAULT":
        ok = False


try:
    for _ in range(150):
        try:
            api("/api/capabilities")
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    api("/api/use", {"path": os.path.join(ROOT, "src")})
    job = api("/api/start", {"out_dir": os.path.join(ROOT, "case")})["job_id"]
    for _ in range(300):
        state = api(f"/api/status?job_id={job}")
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    print(f"a case of {len(state.get('files') or [])} file(s), run {state['status']}")

    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"(not checked: Chromium will not launch: {str(exc)[:100]})")
            sys.exit(0)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        thrown, server_errors, dialogs, popups = [], [], [], []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.on("response", lambda r: server_errors.append(f"{r.status} {r.url}")
                if r.status >= 500 else None)
        page.on("dialog", lambda d: (dialogs.append(d.message[:80]), d.dismiss()))
        context.on("page", lambda p: popups.append(p))

        def fresh(opener):
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="load", timeout=30000)
            page.wait_for_timeout(700)
            page.evaluate("showTab('cases')")
            page.wait_for_timeout(300)
            rows = page.locator("#cases-list [data-open]")
            if rows.count():
                rows.first.click()
                page.wait_for_timeout(1200)
            card = page.locator("#gallery .card a.thumb").first
            if card.count() and card.is_visible():
                card.click()
                page.wait_for_timeout(300)
            page.evaluate(opener)
            page.wait_for_timeout(600)

        def message():
            return page.evaluate(
                "(() => { const t = document.getElementById('toast');"
                " if (t && t.style.display !== 'none' && t.classList.contains('bad'))"
                "   return t.innerText.split('\\n')[0];"
                " for (const e of document.querySelectorAll('[id$=\"-err\"]'))"
                "   if (e.offsetParent && e.innerText.trim()) return e.innerText.trim();"
                " return ''; })()")

        def close_dialogs():
            stuck = []
            # The one on top first: a dialog can open another over itself
            # (Settings opens the file picker), and the lower one's close
            # button is then under the upper one.
            for ident in reversed(page.evaluate(SHOWN)):
                closer = page.locator(f"#{ident} .modal-head > button").last
                if closer.count() and closer.is_visible():
                    try:
                        closer.click(timeout=3000)
                    except Exception:  # noqa: BLE001 - covered: Escape below
                        pass
                    page.wait_for_timeout(200)
                if ident in page.evaluate(SHOWN):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
                if ident in page.evaluate(SHOWN):
                    stuck.append(ident)
                    page.evaluate(f"(() => {{ const m = document.getElementById('{ident}');"
                                  f" m.classList.remove('open'); m.style.display = 'none'; }})()")
            return stuck

        for place, holder, opener in PLACES:
            fresh(opener)
            items = page.evaluate(PICK, holder)
            print(f"{place}: {len(items)} to press", flush=True)
            for item in items:
                label = item["text"] or item["sel"][-40:]
                # Whole words: "Delete" is skipped, the "Deleted" filter chip is not.
                if any(label == word or label.startswith(word + " ") for word in SKIP):
                    record(place, label, "skipped")
                    continue
                fresh(opener)
                target = page.locator(item["sel"]).first
                if not target.count() or not target.is_visible() or not target.is_enabled():
                    record(place, label, "skipped", "not there after a reload")
                    continue
                marks = (len(thrown), len(server_errors), len(popups))
                try:
                    target.click(timeout=5000)
                except Exception as exc:  # noqa: BLE001
                    record(place, label, "skipped", f"could not be clicked: {str(exc)[:80]}")
                    continue
                page.wait_for_timeout(1500)
                faults = thrown[marks[0]:] + server_errors[marks[1]:]
                for p in popups[marks[2]:]:
                    try:
                        p.wait_for_load_state("load", timeout=15000)
                    finally:
                        p.close()
                said = message()
                stuck = [d for d in close_dialogs() if d != holder]
                if stuck:
                    faults.append(f"dialog would not close: {stuck}")
                if faults:
                    record(place, label, "FAULT", "; ".join(faults)[:200])
                elif said:
                    record(place, label, "message", said[:150])
                else:
                    record(place, label, "works")
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=20)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(ROOT, ignore_errors=True)
    _log.close()
    with open(SERVER_LOG, encoding="utf-8", errors="replace") as fh:
        _server_out = fh.read()
    if "Traceback" in _server_out:
        ok = False
        print("\nthe server logged a traceback:\n" +
              _server_out[_server_out.index("Traceback"):][:2500])
    os.remove(SERVER_LOG)

current = None
for place, label, outcome, detail in results:
    if place != current:
        print(f"\n{place}")
        current = place
    mark = {"works": " works ", "FAULT": " FAULT ", "skipped": "skipped",
            "message": "message"}[outcome]
    print(f"  [{mark}] {label[:44]:<44} {detail}")
counts = {}
for _, _, outcome, _ in results:
    counts[outcome] = counts.get(outcome, 0) + 1
print(f"\n{len(results)} found: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
