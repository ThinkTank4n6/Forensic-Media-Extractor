"""Grading a whole page moves on to the next files still to grade (v2026.11.323).

An examiner on "Uncategorized", a page of files in front of them, selects the
page and grades it. The page should then show the next files nobody has
graded -- in a case large enough to be paged from the server too. Reported
against v2026.11.321 in a case of 221,592 files: the page stayed where it
was.
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

ROOT = os.path.join(tempfile.gettempdir(), "fme_bulk_advance-") + str(os.getpid())
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "src"))
COUNT = int(os.environ.get("FME_BULK_FILES", "25"))
PER = os.environ.get("FME_BULK_PER", "10")
for n in range(COUNT):
    with open(os.path.join(ROOT, "src", f"p{n:02d}.jpg"), "wb") as fh:
        fh.write(fixtures.make_jpeg(3100 + n))

for paged_label, threshold in (("held whole by the page", None), ("paged from the server", "0")):
    print(f"\n{paged_label}")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    if threshold is not None:
        env["FME_PAGED_THRESHOLD"] = threshold
    server = subprocess.Popen(
        [sys.executable, "-m", "fme", "gui", "--port", str(port), "--no-browser"],
        cwd=PROJECT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def api(path, payload=None):
        url = f"http://127.0.0.1:{port}{path}"
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
        case_dir = os.path.join(ROOT, "case" + (threshold or "w"))
        api("/api/use", {"path": os.path.join(ROOT, "src")})
        job = api("/api/start", {"out_dir": case_dir})["job_id"]
        for _ in range(150):
            state = api(f"/api/status?job_id={job}")
            if state["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        case_dir = (state.get("summary") or {}).get("output_dir") or case_dir
        with sync_playwright() as play:
            browser = play.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            thrown = []
            page.on("pageerror", lambda e: thrown.append(str(e)))
            page.on("dialog", lambda d: d.accept())
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.evaluate("async (d) => { await openCase(d); }", case_dir)
            page.wait_for_timeout(1200)
            # Ten to a page, so twenty-five files are three pages.
            page.evaluate("""(per) => { const s = document.getElementById('per-page');
                if (![...s.options].some(o => o.value === per)) {
                const o = document.createElement('option'); o.value = per; o.textContent = per;
                s.appendChild(o); } setPerPage(per); }""", PER)
            page.wait_for_timeout(800)
            page.locator(f'#cat-bar [data-cat-filter="{8}"]').first.click()
            page.wait_for_timeout(800)
            first = page.evaluate("pageOf(visible()).map(f => f.rel_path)")
            check("the page shows a page of files still to grade", len(first) == int(PER), len(first))
            page.locator("#gallery .card a.thumb").first.click()
            page.keyboard.press("Control+a")
            page.keyboard.press("2")
            for _ in range(40):
                page.wait_for_timeout(500)
                if not set(page.evaluate("pageOf(visible()).map(f => f.rel_path)")) & set(first):
                    break
            after = page.evaluate("pageOf(visible()).map(f => f.rel_path)")
            check("grading the page moves on to the next page still to grade",
                  len(after) == min(int(PER), COUNT - int(PER)) and not set(after) & set(first),
                  (len(after), len(set(after) & set(first))))
            graded = page.evaluate("(paths) => paths.map(p => (decisions[(lookup(p) || {}).sha256] || {}).category)", first)
            check("and the first ten were graded", all(c == 2 for c in graded), graded)
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
