"""
The interface, in a real browser.

Every other test of the interface reads the page as text or runs its
script against a shim -- a few hundred lines pretending to be a browser,
which does what it was taught to do and no more. That is why a control
whose event could not be dispatched looked fine, why three classes went
unstyled, why the ribbon stayed lit over a closed pane, and why a slider
that moved nothing had to be found by the examiner using it.

This starts the real program, opens the real page in Chromium, and
presses things.

It needs `playwright` and its Chromium. Where either is absent it says so
and stops, because a test that silently does nothing is worse than one
that is missing: the suite would go green while checking nothing at all.
"""

import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import tempfile
import json
import os
import shutil
import socket
import subprocess
import sys
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
    print("    pip install playwright && playwright install chromium")
    sys.exit(0)

import make_media_test_image as fixtures

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "fme_browser_test-") + str(os.getpid())
PORT = 8791
ok = True
notes = []


def check(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok &= bool(passed)
    mark = "  works " if passed else " FAILED "
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail else ""))


def free_port(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


# A small case, so there is something real on the page to press.
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "src"))
for index in range(6):
    with open(os.path.join(ROOT, "src", f"p{index}.jpg"), "wb") as fh:
        fh.write(fixtures.make_jpeg(1400 + index))

if not free_port(PORT):
    print(f"port {PORT} is in use; skipping")
    sys.exit(0)

server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def api(path: str, payload=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    if payload is None:
        with urllib.request.urlopen(url, timeout=120) as response:
            return json.loads(response.read())
    request = urllib.request.Request(
        url, method="POST", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "X-FME-Request": "1"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


try:
    for _ in range(150):
        try:
            api("/api/capabilities")
            break
        except Exception:                                  # noqa: BLE001
            time.sleep(0.4)

    api("/api/use", {"path": os.path.join(ROOT, "src")})
    job = api("/api/start", {"out_dir": os.path.join(ROOT, "case")})["job_id"]
    for _ in range(150):
        state = api(f"/api/status?job_id={job}")
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    print(f"a case with {len(state.get('files') or [])} file(s) in it")

    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:                           # noqa: BLE001
            print(f"Chromium will not launch here: {str(exc)[:120]}")
            print("    playwright install chromium")
            sys.exit(0)

        page = browser.new_page(viewport={"width": 1500, "height": 950})

        # Anything the page throws, and anything it logs as an error. A
        # page that works but throws on every redraw is not working.
        thrown, logged = [], []
        page.on("pageerror", lambda error: thrown.append(str(error)))
        page.on("console", lambda message: logged.append(message.text)
                if message.type == "error" else None)

        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.wait_for_timeout(1200)

        print("\nloading it")
        check("the page loads without throwing", not thrown,
              thrown[0][:90] if thrown else "")
        check("and logs no error", not logged,
              logged[0][:90] if logged else "")
        check("the ribbon is drawn",
              page.locator(".ribbon-tab").count() >= 6,
              f"{page.locator('.ribbon-tab').count()} tabs")

        # ---- the Display tab, which is where controls were rebuilt ----
        print("\nthe Display tab")
        page.locator('.ribbon-tab:text-is("display")').click()
        page.wait_for_timeout(300)

        for control in ("tile-size", "per-page", "tz-select", "hold-page"):
            found = page.locator(f'.ribbon-control [id="{control}"]').count()
            check(f"{control} is on the ribbon", found == 1,
                  f"{found} found")

        # The one I could only check in a shim: does moving the slider
        # actually resize the tiles?
        before = page.evaluate(
            "getComputedStyle(document.querySelector('#gallery'))"
            ".gridTemplateColumns")
        page.evaluate("""() => {
            const slider = document.getElementById('tile-size');
            slider.value = String(Number(slider.max));
            slider.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        page.wait_for_timeout(400)
        after = page.evaluate(
            "getComputedStyle(document.querySelector('#gallery'))"
            ".gridTemplateColumns")
        check("moving the slider actually resizes the tiles",
              before != after, f"{before[:28]} -> {after[:28]}")

        # ---- the side pane and the ribbon's lit state ----
        # ---- open the case, the way a person does ----
        #
        # Until now this opened on the case list and checked the ribbon
        # around an empty screen. The faults an examiner meets are in the
        # gallery: selecting, grading, the pane. So open a case first.
        print("\nopening a case")
        page.locator('.ribbon-tab:text-is("home")').click()
        page.wait_for_timeout(200)
        rows = page.locator("#cases-list [data-open]")
        opened = False
        if rows.count():
            rows.first.click()
            page.wait_for_timeout(1500)
            opened = page.locator("#gallery .tile, #gallery .thumb").first \
                .is_visible()
        check("a case opens from the list into the gallery", opened,
              f"{page.locator('#gallery .tile, #gallery .thumb').count()} "
              f"tile(s)")

        print("\nthe side pane")
        # A person selects a file first: the pane shows what is selected,
        # and without a selection there is nothing for it to show.
        tile = page.locator("#gallery .tile, #gallery .thumb").first
        selected = False
        if opened and tile.count():
            try:
                tile.scroll_into_view_if_needed(timeout=5000)
                tile.click(timeout=5000)
                page.wait_for_timeout(400)
                selected = True
            except Exception as exc:                       # noqa: BLE001
                notes.append(f"a tile could not be clicked, so the pane "
                             f"was not exercised: {str(exc)[:80]}")

        page.locator('.ribbon-tab:text-is("view")').click()
        page.wait_for_timeout(200)
        shown = page.evaluate(
            "getComputedStyle(document.getElementById('details-col')).display")
        if selected:
            check("the pane is open once a file is selected", shown != "none",
                  f"display: {shown}")

        lit = page.locator('.ribbon-item.on:has-text("Details")').count()
        check("and the ribbon shows Details as the pane in view", lit == 1,
              f"{lit} lit")

        # The pane's own ✕ -- the case the examiner reported, where the
        # ribbon stayed lit over a pane that was no longer there.
        closer = page.locator("#details-close")
        if not closer.count():
            check("the pane has a close control", False, "#details-close")
        elif not selected:
            notes.append("nothing was selected, so closing the pane with "
                         "its own control was not exercised")
        else:
            if not closer.first.is_visible():
                # The ribbon item toggles, so pressing it again may have
                # shut the pane. Open it before closing it.
                page.locator('.ribbon-item:has-text("Details")').first.click()
                page.wait_for_timeout(300)
            closer.first.click()
            page.wait_for_timeout(400)
            open_now = page.evaluate(
                "getComputedStyle(document.getElementById('details-col'))"
                ".display")
            check("the ✕ closes the pane", open_now == "none", open_now)
            still = page.locator('.ribbon-item.on:has-text("Details")').count()
            check("and the ribbon stops showing it as open", still == 0,
                  f"{still} still lit")

        # ---- filters, and the ribbon following them ----
        print("\nfilters")
        page.locator('.ribbon-item:has-text("Hide known software")') \
            .first.click()
        page.wait_for_timeout(300)
        on = page.locator(
            '.ribbon-item.on:has-text("Hide known software")').count()
        check("Hide known software lights when pressed", on == 1)

        page.locator('.ribbon-item:has-text("Clear filters")').first.click()
        page.wait_for_timeout(400)
        after_clear = page.locator(
            '.ribbon-item.on:has-text("Hide known software")').count()
        check("and clearing the filters puts it out", after_clear == 0,
              f"{after_clear} still lit")

        # ---- every tab opens and draws something ----
        print("\nevery ribbon tab")
        empty = []
        for name in ("file", "home", "case data", "report / export",
                     "view", "display", "apps"):
            page.locator(f'.ribbon-tab:text-is("{name}")').click()
            page.wait_for_timeout(180)
            items = page.locator(
                ".ribbon-group .ribbon-item, .ribbon-group .ribbon-control"
            ).count()
            if not items:
                empty.append(name)
        check("each one draws its items", not empty,
              ", ".join(empty) if empty else "all seven")

        # ---- the gallery itself ----
        # ---- grading, which is what the program is for ----
        print("\ngrading")
        if not selected:
            notes.append("nothing was selected, so grading was not "
                         "exercised")
        else:
            before = page.evaluate(
                "Object.keys(decisions || {}).length")
            page.keyboard.press("1")
            page.wait_for_timeout(600)
            after = page.evaluate("Object.keys(decisions || {}).length")
            check("a keyboard key grades the selected file", after > before,
                  f"{before} decision(s) -> {after}")

            # It has to reach the case on disk, not just the screen.
            graded = api(f"/api/case?job={job}")
            recorded = len(graded.get("decisions") or {})
            check("and the case records it", recorded > 0,
                  f"{recorded} decision(s) in the case")

            page.keyboard.press("Control+z")
            page.wait_for_timeout(600)
            undone = page.evaluate("Object.keys(decisions || {}).length")
            check("Ctrl-Z takes it back", undone == before,
                  f"{undone} left")

        print("\nthe gallery")
        # Counted *visible*, not merely present. Counting elements that
        # exist is the mistake the shim made in a different costume: the
        # first version of this check passed on a screen showing the case
        # list, where the gallery existed and was hidden.
        tiles = page.locator("#gallery .tile, #gallery .thumb")
        drawn = tiles.count()
        seen = sum(1 for index in range(min(drawn, 12))
                   if tiles.nth(index).is_visible())
        if drawn and not seen:
            notes.append("the gallery holds tiles but none is on screen -- "
                         "the browser opened on the case list, so the "
                         "gallery itself was not exercised")
        else:
            check("tiles are drawn for the recovered files", seen > 0,
                  f"{seen} visible of {drawn}")

        print("\nnothing thrown along the way")
        check("no uncaught error while pressing things", not thrown,
              thrown[0][:100] if thrown else "")
        check("and nothing logged as an error", not logged,
              logged[0][:100] if logged else "")

        # ---- things that only a rendered page can show ----
        #
        # A message that lands on top of a control is not visible in the
        # markup and not visible to a shim: both say the control is
        # there. Only the browser knows what is on top of what.
        # ---- the screens the harness never opened ----
        #
        # Roughly half the interface was outside this test: the
        # extraction options, the report and export panels, the hash
        # store and the signature editor. They are where an examiner
        # spends the setup of every case, and a page that throws while
        # being used there is invisible to a markup check.
        print("\nevery screen opens and draws something")
        before = len(thrown)
        for name, label in (("review", "the workspace"),
                            ("cases", "the case list"),
                            ("hashes", "the hash store"),
                            ("sigs", "custom signatures"),
                            ("similar", "similar media")):
            page.evaluate(f"showTab('{name}')")
            page.wait_for_timeout(400)
            words = page.evaluate(f"""() => {{
                const panel = document.getElementById('tab-{name}');
                if (!panel) return -1;
                const seen = getComputedStyle(panel).display !== 'none';
                return seen ? (panel.innerText || '').trim().split(/\\s+/)
                              .length : 0;
            }}""")
            check(f"{label} draws", words > 5,
                  "missing" if words < 0 else f"{words} word(s)")
        check("and none of them threw", len(thrown) == before,
              thrown[-1][:90] if len(thrown) > before else "")

        # ---- the extraction options, which every case starts with ----
        print("\nthe extraction panel")
        # With a case open the form steps aside (v2026.11.320); New
        # extraction is how it is reached, as an examiner would.
        page.evaluate("newExtraction()")
        page.wait_for_timeout(300)
        for control, label in (("mode", "the engine choice"),
                               ("case-number", "the case number"),
                               ("opt-artifacts", "keep containers"),
                               ("scan-inside", "look inside files"),
                               ("workers", "parallel workers"),
                               ("align", "carve alignment")):
            there = page.locator(f'#{control}').count() == 1
            check(f"{label} is on the panel", there,
                  "" if there else f"#{control}")

        # Toggling one must change what a run would be told, not merely
        # move a tick: the options are read when Start is pressed, and a
        # checkbox wired to nothing looks identical until a case is
        # carved with the wrong settings.
        was = page.evaluate(
            "document.getElementById('opt-artifacts').checked")
        page.locator("#opt-artifacts").click()
        page.wait_for_timeout(200)
        now = page.evaluate(
            "document.getElementById('opt-artifacts').checked")
        check("and a tick actually changes", was != now, f"{was} -> {now}")
        page.locator("#opt-artifacts").click()

        # ---- reports and exports ----
        print("\nreports and exports")
        page.evaluate("showTab('review')")
        page.wait_for_timeout(300)
        # Present is not the same as reachable: these panels start
        # hidden behind a "show" control, and a panel that is in the
        # markup but never opens is a feature an examiner cannot get
        # to. So they are opened.
        for panel, opener_id, label in (
                ("export-panel", "export-toggle", "the export panel"),
                ("vic-panel", "vic-toggle", "the Project VIC panel"),
                ("reprocess-panel", "reprocess-toggle",
                 "the do-more-with-this-case panel")):
            holder = page.locator(f"#{panel}")
            if holder.count() != 1:
                check(f"{label} is in the page", False, f"#{panel}")
                continue
            opener = page.locator(f"#{opener_id}").first
            if opener.count():
                opener.click()
                page.wait_for_timeout(350)
            shown = page.evaluate(
                f"getComputedStyle(document.getElementById('{panel}'))"
                f".display")
            if shown == "none":
                # Opened by a chip the harness could not identify, so
                # say that rather than claim the panel works.
                notes.append(f"{label} did not open: the control that "
                             f"shows it was not found, so what is inside "
                             f"it is unchecked")
                print(f"  [not run ] {label} -- its opener was not found")
            else:
                words = page.evaluate(
                    f"(document.getElementById('{panel}').innerText || '')"
                    f".trim().split(/\\s+/).length")
                check(f"{label} opens and draws", words > 3,
                      f"{words} word(s)")

        # /api/case/reprocess had no button for many releases. Pressed
        # here, so it cannot become unreachable again unnoticed.
        if page.evaluate("typeof jobId !== 'undefined' && !!jobId"):
            page.evaluate("document.getElementById('reprocess-panel')"
                          ".style.display = 'block'")
            page.locator("#reprocess-thumbs").click()
            page.wait_for_function(
                "() => document.getElementById('reprocess-result').innerText"
                ".trim() || document.getElementById('reprocess-err')"
                ".innerText.trim()", timeout=15000)
            said = page.evaluate(
                "document.getElementById('reprocess-result').innerText")
            check("Rebuild gallery tiles reaches the server and answers",
                  "tiles" in said, said or page.evaluate(
                      "document.getElementById('reprocess-err').innerText"))
        else:
            notes.append("no case was open, so the reprocess buttons were "
                         "not pressed")

        # ---- the hash store screen ----
        print("\nthe hash store screen")
        page.evaluate("showTab('hashes')")
        page.wait_for_timeout(600)
        for control, label in (("store-path", "where the store is"),
                               ("store-token", "the token field"),
                               ("store-scan", "check this case"),
                               ("store-verify", "verify")):
            there = page.locator(f"#{control}").count() == 1
            check(f"{label} is there", there, "" if there else f"#{control}")

        print("\nnothing sits on top of anything")
        covered = page.evaluate("""() => {
            const trouble = [];
            const controls = document.querySelectorAll(
                "#details-col .panetab, .ribbon-tab, .cat-chip");
            for (const control of controls) {
                const box = control.getBoundingClientRect();
                if (!box.width || !box.height) continue;
                const x = box.left + box.width / 2;
                const y = box.top + box.height / 2;
                if (y < 0 || y > window.innerHeight) continue;
                const front = document.elementFromPoint(x, y);
                if (front && !control.contains(front)
                        && !front.contains(control)) {
                    trouble.push((control.textContent || "").trim()
                                 .slice(0, 24) + " <- " +
                                 (front.className || front.tagName));
                }
            }
            return trouble;
        }""")
        check("no control is covered by something else", not covered,
              "; ".join(covered[:2]) if covered else "")

        page.screenshot(path=os.path.join(tempfile.gettempdir(), "fme_browser.png"), full_page=False)
        print("\n  (a screenshot is at /tmp/fme_browser.png)")
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()

for note in notes:
    print(f"\nnot checked: {note}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
