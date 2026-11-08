"""A run is checked against the hash store, and what it classifies is shown.

Found on case DC25-320: a store of 367 million hashes held earlier cases'
gradings, Project VIC and the NSRL, and nothing in the gallery was classified.
Three faults, each checked here:

  * the check ran only when "Check this case against the store" was pressed;
    a finished run was never checked by itself;
  * when it was pressed, it wrote its categories to the case and the gallery
    -- which draws categories from the case's decisions -- never read them
    again: every file it classified still showed as uncategorized;
  * its counts were of locations, not files: one picture found in three
    places counted three times, and each copy was looked up again.

Needs playwright and its Chromium for the gallery checks; the counting is
checked on any machine.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import json
import logging
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
from fme.engines import media_core          # noqa: E402
from fme.review import hashstore            # noqa: E402

logging.disable(logging.CRITICAL)
ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_store_classifies-") + str(os.getpid())
PORT = 8804


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
IMAGE = os.path.join(HERE, "media_test.img")
media_core.run_extraction(IMAGE, os.path.join(ROOT, "pre"), mode="both")
manifest_path = os.path.join(ROOT, "pre", "manifest.json")
manifest = json.load(open(manifest_path, encoding="utf-8"))
known = [(f["md5"], f["sha256"]) for f in manifest["files"]][:3]
store_path = os.path.join(ROOT, "hash_store.db")
store = hashstore.HashStore(store_path)
store.contribute_rows([{"md5": m, "sha256": h, "category": 3, "label": "graded"}
                       for m, h in known], "DC25-320")

# Counting: a file and two copies of it elsewhere are one file.
copies = dict(manifest, duplicates=[dict(manifest["files"][0], out_path=f"copy{i}")
                                    for i in range(2)])
counted = os.path.join(ROOT, "copies.json")
json.dump(copies, open(counted, "w", encoding="utf-8"))
result = store.scan_manifest(counted)
store.close()
check("a file found in three places is checked once", result["checked"] == len(manifest["files"]),
      f"{result['checked']} for {len(manifest['files'])} file(s)")
check("and counted once for its set", result["by_source"].get("DC25-320") == 3,
      str(result["by_source"]))
check("while every place is still listed", result["locations"] == len(manifest["files"]) + 2
      and len(result["rows"]) == result["locations"])

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed; the gallery is not checked.")
    shutil.rmtree(ROOT, ignore_errors=True)
    sys.exit(0 if ok else 1)
with socket.socket() as probe:
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"port {PORT} is in use; the gallery is not checked")
        sys.exit(0 if ok else 1)

server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
START = """async (path) => { const used = await api('/api/use', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
    setActive(path, used.info); document.getElementById('btn-start').click(); }"""
shown = "(shas) => shas.map(h => (decisions[h] || {}).category ?? null)"
shas = [h for _, h in known]
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
            sys.exit(0 if ok else 1)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        thrown = []
        page.on("pageerror", lambda error: thrown.append(str(error)))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

        # A run with no store set up, then the store chosen and the button
        # pressed: the gallery must show what the check classified.
        page.evaluate(START, IMAGE)
        page.wait_for_function(
            "() => /^done/.test(document.getElementById('status').innerText)", timeout=120000)
        page.wait_for_timeout(1500)
        check("with no store, a finished run classifies nothing", page.evaluate(shown, shas)
              == [None, None, None], str(page.evaluate(shown, shas)))
        page.evaluate("""async (p) => api('/api/store', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: p})})""",
            store_path)
        page.evaluate("() => showTab('hashes')")
        page.click("#store-scan")
        page.wait_for_function(
            "() => document.getElementById('store-scan').textContent.startsWith('Check')",
            timeout=60000)
        page.wait_for_timeout(500)
        check("pressing the check shows its categories in the gallery at once",
              page.evaluate(shown, shas) == [3, 3, 3], str(page.evaluate(shown, shas)))

        # A second run, with the store already set up and nothing pressed.
        page.evaluate("() => showTab('review')")
        page.evaluate(START, IMAGE)
        page.wait_for_function(
            "() => /^done/.test(document.getElementById('status').innerText)", timeout=120000)
        try:
            page.wait_for_function(f"(shas) => shas.every(h => (decisions[h] || {{}}).category === 3)",
                                   arg=shas, timeout=30000)
        except Exception:                                  # noqa: BLE001
            pass
        check("a run that finishes is checked against the store by itself",
              page.evaluate(shown, shas) == [3, 3, 3], str(page.evaluate(shown, shas)))
        check("nothing the page did threw", not thrown, "; ".join(thrown[:2]))
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(ROOT, ignore_errors=True)

if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
