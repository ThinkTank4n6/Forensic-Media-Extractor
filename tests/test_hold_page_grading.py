"""Hold the page keeps graded files on the page (reported against v2026.11.334).

"When categorizing images, even with Hold the page checked, it is still
removing those images from the view." With Not yet reviewed (or the
Uncategorized chip) on, a file graded leaves the filter -- and Hold the page
is there so it does not leave the page being worked down: the tiles stay
where they are until the examiner moves on.

Graded the way an examiner grades: a tile clicked, then the number key,
over and over. Run on a case the page holds whole and on one it pages from
the server (FME_PAGED_THRESHOLD=0), since they are two different paths.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
import logging
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("(not checked: playwright is not installed)")
    sys.exit(0)

from werkzeug.serving import make_server  # noqa: E402

logging.getLogger("werkzeug").setLevel(logging.ERROR)
from fme.web import media_gui as gui  # noqa: E402

N = 300
ok = True
ROOT = os.path.join(tempfile.gettempdir(), "hold_page-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


def make_case(name):
    case_dir = os.path.join(ROOT, name)
    os.makedirs(case_dir)
    records = [{
        "out_path": f"images/jpg/{name}{i:05d}.jpg", "ext": "jpg", "category": "image",
        "engine": "filesystem", "size": 1000 + i, "deleted": False,
        "sha256": f"{name}{i:063x}"[:64], "md5": f"{i:032x}", "sha1": f"{i:040x}",
        "source_path": f"/DCIM/IMG_{i:05d}.JPG", "offset": None, "fs_type": "FAT32",
        "mtime": "2024-05-01T10:00:00", "captured": "", "container": False,
    } for i in range(N)]
    with open(os.path.join(case_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": {"image": "synthetic"}, "files": records}, fh)
    return case_dir


server = make_server("127.0.0.1", 0, gui.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()

with sync_playwright() as play:
    try:
        browser = play.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        print(f"(not checked: Chromium will not launch: {str(exc)[:100]})")
        sys.exit(0)
    # Each path set here, whatever FME_PAGED_THRESHOLD says: the suite runs
    # the browser tests with paging forced too, and both rounds were paged.
    for paged, threshold, name in (("held whole", 20000, "w"), ("paged", 0, "p")):
        gui.PAGED_THRESHOLD = threshold
        case_dir = make_case(name)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        thrown = []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="load")
        page.wait_for_timeout(500)
        page.evaluate("(dir) => openCase(dir)", case_dir)
        page.wait_for_function("() => !!jobId && document.querySelectorAll("
                               "'#gallery [data-pick]').length > 0", timeout=30000)
        print(f"A case {paged} ({page.evaluate('paged')})")
        page.evaluate("() => { document.getElementById('per-page').value = '100'; }")
        page.locator('.chip[data-filter="needs-review"]').first.click()
        page.wait_for_timeout(800)
        check("Hold the page is ticked", page.is_checked("#hold-page"))
        before = page.evaluate("[...document.querySelectorAll('#gallery [data-pick]')]"
                               ".map(t => t.dataset.pick)")
        page.locator("#gallery .card a.thumb").first.click()
        page.wait_for_timeout(300)
        graded = []
        for _ in range(5):
            graded.append(page.evaluate("selectedPath"))
            page.keyboard.press("1")
            page.wait_for_timeout(700)
        page.wait_for_timeout(2500)          # the counts, refreshed after the saves
        after = page.evaluate("[...document.querySelectorAll('#gallery [data-pick]')]"
                              ".map(t => t.dataset.pick)")
        # A selection graded at once: Shift-click three tiles further down,
        # then the key. This is what redraws the page -- and a redraw is what
        # took every graded file out when the filter's files fit on one page.
        cards = page.locator("#gallery .card a.thumb")
        cards.nth(10).click()
        cards.nth(12).click(modifiers=["Shift"])
        page.wait_for_timeout(200)
        chosen = page.evaluate("[...selection]")
        page.keyboard.press("2")
        page.wait_for_timeout(1500)
        graded.extend(chosen)
        # And any other redraw: a pane opened, the page drawn again.
        page.evaluate("() => { showPane('classify'); render(); }")
        page.wait_for_timeout(1500)
        after = page.evaluate("[...document.querySelectorAll('#gallery [data-pick]')]"
                              ".map(t => t.dataset.pick)")
        check("five files graded one by one, three as a selection",
              len(set(graded)) == 8, len(set(graded)))
        check("every graded file is still on the page",
              all(p in after for p in graded), [p for p in graded if p not in after][:3])
        check("and the page is the same page", after == before,
              f"{len(before)} tiles before, {len(after)} after")
        check("nothing the page did threw", not thrown, "; ".join(thrown[:2]))
        page.close()
    browser.close()
server.shutdown()
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
