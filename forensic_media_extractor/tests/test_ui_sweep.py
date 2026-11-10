"""Every ribbon button and control, pressed in a real browser.

test_browser.py checks the interactions that have broken before. This one
presses everything, so a button nobody thought to check cannot fail
unnoticed: every tab of the ribbon, every button on it, every control on it,
with a case open. For each press it records what the page threw, what it
logged as an error, any request the server answered with a 5xx, and any
error message the page showed -- and closes any dialog the press opened
through the dialog's own close button, which is itself a control.

An error message is not automatically a fault: "connect a hash store first"
is the right answer on a machine with no store. Those are listed with the
button that produced them, against EXPECTED_MESSAGES. A thrown error, a 5xx,
or a message nobody expected fails the test.

Two buttons are pressed last, because they end the session being tested:
Close case and Release image.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

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

ROOT = os.path.join(tempfile.gettempdir(), "fme_ui_sweep-") + str(os.getpid())
ok = True

# Messages that are the right answer in this environment, by the button
# that gives them. Anything else shown as an error fails the test.
EXPECTED_MESSAGES = {
    "Add this case": ("store", "Store", "hash store"),
    "Check this case": ("store", "Store"),
    "Loaded sets": ("hash set", "set"),
    "Import C4All index": (),
    "Stills to videos": ("video", "OpenCV", "no still"),
    "Similar images": ("Pillow",),
    # A C4All index names its case; a case started without a number has none.
    "C4All": ("case number",),
    # No face models ship with the program; each carries its own licence.
    "Find faces": ("no face models",),
    # Content search needs an ONNX image model; none ships with the program.
    "Others like this": ("no model is set",),
    # Nothing has been loaded for this session, which is the case here.
    "Loaded sets": ("no hash set", "No hash set", "load", "Load"),
}
LAST = ("Close case", "Release image")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def png_chunk(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "src"))
for index in range(8):
    with open(os.path.join(ROOT, "src", f"p{index}.jpg"), "wb") as fh:
        fh.write(fixtures.make_jpeg(1400 + index))
# An Apple CgBI PNG and a KTX texture, so the viewer's handling of both is
# pressed as well: a flat orange square, which a black square is not.
w = h = 24
row = b"\x00" + bytes([0x20, 0x80, 0xF0, 0xFF]) * w       # BGRA, filter 0
z = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
with open(os.path.join(ROOT, "src", "Icon@2x.png"), "wb") as fh:
    fh.write(b"\x89PNG\r\n\x1a\n" + png_chunk(b"CgBI", bytes(4))
             + png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
             + png_chunk(b"IDAT", z.compress(row * h) + z.flush())
             + png_chunk(b"IEND", b""))
pixels = bytes([0xF0, 0x80, 0x20, 0xFF]) * (w * h)
with open(os.path.join(ROOT, "src", "snapshot.ktx"), "wb") as fh:
    fh.write(b"\xabKTX 11\xbb\r\n\x1a\n"
             + struct.pack("<13I", 0x04030201, 0x1401, 1, 0x1908, 0x8058, 0x1908,
                           w, h, 0, 0, 1, 1, 0)
             + struct.pack("<I", len(pixels)) + pixels)

PORT = free_port()
# The server's output goes to a file, never to a pipe nobody reads. Flask
# logs every request; a pipe left unread fills at about 64 KB and the server
# then blocks on its next log line and answers nothing -- which this test
# first did, and which looked exactly like the program hanging.
SERVER_LOG = os.path.join(tempfile.gettempdir(), f"fme_ui_sweep-server-{os.getpid()}.log")
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


results = []        # (tab, label, outcome, detail)


def record(tab, label, outcome, detail=""):
    global ok
    results.append((tab, label, outcome, detail))
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
        thrown, logged, server_errors, dialogs, popups = [], [], [], [], []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.on("console", lambda m: logged.append(m.text) if m.type == "error" else None)
        page.on("response", lambda r: server_errors.append(f"{r.status} {r.url}")
                if r.status >= 500 else None)
        page.on("dialog", lambda d: (dialogs.append(d.message[:80]), d.dismiss()))
        context.on("page", lambda p: popups.append(p))

        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.wait_for_timeout(1000)

        def open_case():
            page.evaluate("showTab('cases')")
            page.wait_for_timeout(500)
            rows = page.locator("#cases-list [data-open]")
            if rows.count():
                rows.first.click()
                page.wait_for_timeout(1500)
            return page.evaluate("!!jobId")

        def toast_bad():
            """The error the examiner is shown: the toast, or one of the
            page's inline error lines (ids ending -err) that is visible."""
            return page.evaluate(
                "(() => { const t = document.getElementById('toast');"
                " if (t && t.style.display !== 'none' && t.classList.contains('bad'))"
                "   return t.innerText;"
                " for (const e of document.querySelectorAll('[id$=\"-err\"]'))"
                "   if (e.offsetParent && e.innerText.trim()) return e.innerText.trim();"
                " return ''; })()")

        def clear_errors():
            page.evaluate("(() => { document.getElementById('toast').style.display='none';"
                          " document.querySelectorAll('[id$=\"-err\"]')"
                          ".forEach(e => e.textContent = ''); })()")

        def select_a_file():
            # Pressing Table earlier is meant to hide the gallery; put it back
            # the way a person would, then click a tile.
            if not page.locator("#gallery").is_visible():
                page.locator('.ribbon-tab[data-ribbon="view"]').click()
                page.wait_for_timeout(200)
                page.locator("#ribbon-groups [data-go]").filter(
                    has=page.locator('span:text-is("Gallery")')).first.click()
                page.wait_for_timeout(500)
            card = page.locator("#gallery .card").first
            if card.count() and card.is_visible():
                # The thumbnail is what selects (a.thumb, data-details); the
                # caption beneath it is only text.
                card.locator("a.thumb").click(timeout=5000)
                page.wait_for_timeout(500)
            return page.evaluate("typeof selectedPath !== 'undefined' && !!selectedPath")

        SHOWN = ("[...document.querySelectorAll('.modal-overlay')]"
                 ".filter(m => getComputedStyle(m).display !== 'none')"
                 ".map(m => m.id)")

        def close_dialogs():
            """Close what is in front by its own close button, then Escape;
            report any that neither closes. Dialogs here show by a class or
            by inline style -- the app handles both, and so must this."""
            stuck = []
            for ident in page.evaluate(SHOWN):
                # The close button is the ✕, which is not always the first
                # button in the header (the full size viewer's are its zoom).
                closer = page.locator(f"#{ident} .modal-head > button").last
                if closer.count() and closer.is_visible():
                    closer.click()
                    page.wait_for_timeout(250)
                if ident in page.evaluate(SHOWN):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(250)
                if ident in page.evaluate(SHOWN):
                    stuck.append(ident)
                    page.evaluate(f"(() => {{ const m = document.getElementById('{ident}');"
                                  f" m.classList.remove('open'); m.style.display = 'none'; }})()")
            return stuck

        def fresh():
            """A clean page with the case open and a file selected, so each
            press starts from the same place and one button's effect -- a
            toggle left on, a view switched -- is never blamed on the next."""
            # "load", not "networkidle": a job an earlier press started (a
            # similarity scan, a report) is polled until it ends, so the
            # network is never idle and that is correct.
            try:
                page.goto(f"http://127.0.0.1:{PORT}/", wait_until="load",
                          timeout=20000)
            except Exception:
                # Which side stopped: ask the server directly.
                t0 = time.monotonic()
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health",
                                           timeout=10).read()
                    side = f"server answered /api/health in {time.monotonic() - t0:.1f} s"
                except Exception as exc:  # noqa: BLE001
                    side = f"server did NOT answer /api/health: {exc}"
                print(f"    RELOAD STALLED -- {side}; prompts so far: {dialogs[-3:]}",
                      flush=True)
                raise
            page.wait_for_timeout(900)
            open_case()
            select_a_file()

        check_case = open_case()
        print(f"case opened in the gallery: {bool(check_case)}")
        ok &= bool(check_case)

        tabs = page.evaluate("Object.keys(ribbonDefinition())")
        print(f"ribbon tabs: {', '.join(tabs)}\n")
        deferred = []
        for tab in tabs:
            page.locator(f'.ribbon-tab[data-ribbon="{tab}"]').click()
            page.wait_for_timeout(300)
            labels = page.locator("#ribbon-groups [data-go]").all_inner_texts()
            labels = [" ".join(t.split()[1:]) if len(t.split()) > 1 else t
                      for t in labels]
            for label in labels:
                if label in LAST:
                    deferred.append((tab, label))
                    continue
                fresh()
                page.locator(f'.ribbon-tab[data-ribbon="{tab}"]').click()
                page.wait_for_timeout(200)
                item = page.locator("#ribbon-groups [data-go]").filter(
                    has=page.locator(f'span:text-is("{label}")')).first
                if "disabled" in (item.get_attribute("class") or ""):
                    record(tab, label, "disabled", "with a case open and a file selected")
                    continue
                marks = (len(thrown), len(logged), len(server_errors),
                         len(dialogs), len(popups))
                clear_errors()
                print(f"    pressing {tab} > {label}", flush=True)
                item.click()
                page.wait_for_timeout(1800)
                new_thrown = thrown[marks[0]:]
                new_logged = logged[marks[1]:]
                new_5xx = server_errors[marks[2]:]
                bad = toast_bad()
                opened = page.evaluate(SHOWN)
                new_pages = popups[marks[4]:]
                notes = []
                for p in new_pages:
                    try:
                        p.wait_for_load_state("load", timeout=15000)
                        body = p.evaluate("document.body.innerText.length")
                        notes.append(f"opened {p.url.split(str(PORT))[-1]} "
                                     f"({body:,} chars)")
                        if body < 200:
                            new_thrown.append(f"page {p.url} is nearly empty")
                    finally:
                        p.close()
                if opened:
                    notes.append("dialog " + ", ".join(opened))
                stuck = close_dialogs()
                if stuck:
                    new_thrown.append(f"dialog would not close: {stuck}")
                if dialogs[marks[3]:]:
                    notes.append("asked: " + "; ".join(dialogs[marks[3]:]))
                if new_thrown or new_5xx:
                    record(tab, label, "FAULT",
                           "; ".join((new_thrown + new_5xx)[:3])[:200])
                elif bad:
                    allowed = EXPECTED_MESSAGES.get(label)
                    if allowed is not None and (not allowed or
                                                any(a in bad for a in allowed)):
                        record(tab, label, "expected message", bad[:120])
                    else:
                        record(tab, label, "FAULT", "message: " + bad[:160])
                elif new_logged:
                    record(tab, label, "logged",
                           "; ".join(new_logged)[:160])
                else:
                    record(tab, label, "works", "; ".join(notes)[:120])

            # The controls on this tab: change each, check nothing breaks.
            fresh()
            page.locator(f'.ribbon-tab[data-ribbon="{tab}"]').click()
            page.wait_for_timeout(200)
            for holder in page.locator("#ribbon-groups [data-control]").all():
                ident = holder.get_attribute("data-control")
                label = holder.locator(".ribbon-control-label").inner_text()
                mark = (len(thrown), len(server_errors))
                kind = page.evaluate(
                    f"(() => {{ const e = document.getElementById('{ident}');"
                    f" if (!e) return 'missing';"
                    f" if (e.tagName === 'SELECT') {{"
                    f"   e.selectedIndex = (e.selectedIndex + 1) % e.options.length;"
                    f"   e.dispatchEvent(new Event('change', {{bubbles: true}}));"
                    f"   return 'select'; }}"
                    f" if (e.type === 'checkbox') {{ e.click(); e.click(); return 'toggle'; }}"
                    f" if (e.type === 'range') {{ e.value = e.min;"
                    f"   e.dispatchEvent(new Event('input', {{bubbles: true}}));"
                    f"   return 'slider'; }}"
                    f" e.dispatchEvent(new Event('change', {{bubbles: true}}));"
                    f" return e.tagName.toLowerCase(); }})()")
                page.wait_for_timeout(500)
                faults = thrown[mark[0]:] + server_errors[mark[1]:]
                if kind == "missing" or faults:
                    record(tab, f"[{label}]", "FAULT",
                           "control not found" if kind == "missing" else faults[0][:160])
                else:
                    record(tab, f"[{label}]", "works", f"{kind} changed")

        # The two Apple formats, through the gallery as a person sees them.
        print("the Apple formats in the gallery")
        fresh()
        for name in ("Icon@2x.png", "snapshot.ktx"):
            # Recovered names are made filesystem-safe: Icon@2x becomes Icon_2x.
            stem = name.rsplit(".", 1)[0].replace("@", "_")
            tile = page.locator(f'#gallery .card[data-pick*="{stem}"]').first
            if not tile.count():
                print("  on the page:", page.evaluate(
                    "[...document.querySelectorAll('#gallery .card')].map(c => c.dataset.pick)"))
            drawn = False
            detail = "tile not found"
            if tile.count():
                tile.scroll_into_view_if_needed()
                page.wait_for_timeout(800)
                img = tile.locator("img").first
                if img.count():
                    drawn = page.evaluate(
                        "(img) => { if (!img.complete || !img.naturalWidth) return false;"
                        " const c = document.createElement('canvas');"
                        " c.width = c.height = 1;"
                        " const g = c.getContext('2d'); g.drawImage(img, 0, 0, 1, 1);"
                        " const [r, gg, b] = g.getImageData(0, 0, 1, 1).data;"
                        " return r > 150 && b < 100; }", img.element_handle())
                    detail = img.get_attribute("src")[:60]
            record("gallery", name, "works" if drawn else "FAULT",
                   ("tile shows the orange picture " if drawn else
                    "tile is not the picture ") + detail)

        for tab, label in deferred:
            page.locator(f'.ribbon-tab[data-ribbon="{tab}"]').click()
            page.wait_for_timeout(200)
            item = page.locator("#ribbon-groups [data-go]").filter(
                has=page.locator(f'span:text-is("{label}")')).first
            mark = (len(thrown), len(server_errors))
            fresh()
            page.locator(f'.ribbon-tab[data-ribbon="{tab}"]').click()
            page.wait_for_timeout(200)
            item = page.locator("#ribbon-groups [data-go]").filter(
                has=page.locator(f'span:text-is("{label}")')).first
            item.click()
            page.wait_for_timeout(1500)
            faults = thrown[mark[0]:] + server_errors[mark[1]:]
            bad = toast_bad()
            record(tab, label, "FAULT" if faults or bad else "works",
                   (faults[0] if faults else bad)[:160] if faults or bad else
                   ("returned to the case list" if label == "Close case"
                    else "evidence released"))
            close_dialogs()
            open_case()
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=20)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(ROOT, ignore_errors=True)
    _log.close()
    # A traceback in the server is a fault even if the page coped with it.
    with open(SERVER_LOG, encoding="utf-8", errors="replace") as fh:
        _server_out = fh.read()
    if "Traceback" in _server_out:
        ok = False
        print("\nthe server logged a traceback:\n" +
              _server_out[_server_out.index("Traceback"):][:1500])
    os.remove(SERVER_LOG)

width = max(len(f"{t} > {l}") for t, l, _, _ in results) if results else 20
current = None
for tab, label, outcome, detail in results:
    if tab != current:
        print(f"\n{tab}")
        current = tab
    mark = {"works": " works ", "FAULT": " FAULT ", "disabled": "  off  ",
            "expected message": " answer", "logged": " logged"}[outcome]
    print(f"  [{mark}] {label:<28} {detail}")
counts = {}
for _, _, outcome, _ in results:
    counts[outcome] = counts.get(outcome, 0) + 1
print(f"\n{len(results)} pressed: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
