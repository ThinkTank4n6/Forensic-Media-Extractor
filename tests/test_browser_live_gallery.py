"""The gallery during a run, in a real browser: tiles appear as files are
found, the page holds more than 500 of them, the case's categories and tags
are there, a file's details open, and a file can be classified.

Reported from the lab on v2026.11.283, and reproduced in Chromium before
it was fixed: with a run going, the gallery showed no tiles at all under
any filter until Show containers was clicked, and the page never held more
than 500 files whatever the page size -- 1,261 recovered, 500 held, 0
shown. Then, on v2026.11.284: no categories on the bar until the run
ended, the Classification pane saying the case "could not be read", and
Details answering "[Errno 2] No such file or directory: ...manifest.json"
for any file clicked -- manifest.json is written when a run ends.
test_gui_live_gallery.py checks the server's side of the file list; only a
real browser shows what the examiner sees.

Needs playwright and its Chromium; says so and stops where they are absent.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import contextlib
import io
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed; skipping the browser checks.")
    print("    pip install playwright && playwright install chromium")
    sys.exit(0)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_browser_live-") + str(os.getpid())
PORT = 8793
PICTURES = 1200


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


with socket.socket() as probe:
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"port {PORT} is in use; skipping")
        sys.exit(0)

# A raw image with no file system, so the carve is the run: 1,200 distinct
# pictures spread through 240 MiB of seeded noise, long enough to watch.
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
image = os.path.join(ROOT, "disk.dd")
rng = random.Random(20261104)
size = 240 << 20
blob = bytearray(b"".join(rng.randbytes(1 << 20) for _ in range(size >> 20)))
picture = fixtures.make_jpeg(6)
for i in range(PICTURES):
    note = f"picture {i:05d}".encode()
    one = picture[:2] + b"\xff\xfe" + struct.pack(">H", len(note) + 2) + note + picture[2:]
    at = i * (size // PICTURES) + 4096
    blob[at:at + len(one)] = one
with open(image, "wb") as fh:
    fh.write(bytes(blob))
del blob

# This watches the page build the run's list as it arrives, so the list is
# sent whole whatever FME_PAGED_THRESHOLD says; a paged case's live view is
# tests/test_gallery_paged.py's.
server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env=dict(os.environ, FME_PAGED_THRESHOLD=str(10 ** 9)))
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
        page.wait_for_timeout(600)

        # As an examiner does it: 1,500 a page, accept the image, start.
        page.evaluate("""async (path) => {
            const pick = document.getElementById('per-page');
            pick.value = '1500'; pick.dispatchEvent(new Event('change'));
            const used = await api('/api/use', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({path})});
            setActive(path, used.info);
            document.getElementById('mode').value = 'carve';
            document.getElementById('btn-start').click();
        }""", image)

        seen, started = [], time.time()
        while time.time() - started < 180:
            page.wait_for_timeout(1000)
            look = page.evaluate("""() => ({
                tiles: document.querySelectorAll('[data-pick]').length,
                held: files.length,
                categories: document.querySelectorAll('#cat-bar [data-cat-filter]').length - 1,
                flags: caseFlags.length,
                status: document.getElementById('status').innerText})""")
            seen.append(look)
            # Classify a file mid-run, as an examiner would: click it, press 7.
            if (look["status"].startswith("carving") and look["tiles"]
                    and look["categories"] and "graded" not in locals()):
                page.evaluate("() => document.querySelector('[data-pick] [data-details]').click()")
                page.wait_for_timeout(1500)
                details = page.evaluate("() => document.getElementById('details').innerText")
                page.keyboard.press("7")
                page.wait_for_timeout(800)
                graded = page.evaluate("""async () => {
                    const pick = document.querySelector('[data-pick]').dataset.pick;
                    const file = lookup(pick);
                    const on_page = (decisions[file.sha256] || {}).category;
                    const d = await api(`/api/case?job=${jobId}`);
                    return {on_page, saved: (d.decisions[file.sha256] || {}).category};
                }""")
            if look["status"].startswith(("done", "canceled", "failed")):
                break

        during = [s for s in seen if s["status"].startswith("carving")]
        print(f"watched {len(during)} moment(s) of the run, "
              f"{', '.join(str(s['tiles']) for s in during)} tile(s) shown")
        if len(during) < 3:
            print("not checked: the run finished too quickly here to watch")
        else:
            check("tiles appear while the run goes on",
                  any(s["tiles"] > 0 for s in during))
            check("every file the page holds is shown",
                  all(s["tiles"] == min(s["held"], 1500) for s in during if s["held"]),
                  "; ".join(f"{s['held']} held, {s['tiles']} shown" for s in during[-3:]))
            check("the case's 10 categories are on the bar while it runs",
                  all(s["categories"] == 10 for s in during[2:]),
                  ", ".join(str(s["categories"]) for s in during))
            check("and its 6 tags", all(s["flags"] == 6 for s in during[2:]))
            check("clicking a file mid-run shows its details, not an error",
                  "details" in locals() and "recovered_" in details
                  and "Errno" not in details and "No such file" not in details,
                  (locals().get("details") or "")[:90].replace("\n", " "))
            check("a file classified mid-run is saved to the case",
                  "graded" in locals() and graded == {"on_page": 7, "saved": 7},
                  str(locals().get("graded")))
            check("the page holds more than 500 before the run ends",
                  any(s["held"] > 500 for s in during),
                  str(max(s["held"] for s in during)))
        final = seen[-1] if seen else {}
        check("when it finishes, every file is held",
              final.get("status", "").startswith("done") and final.get("held", 0) >= PICTURES,
              f"{final.get('held')} held")
        check("and nothing the page did threw", not thrown, "; ".join(thrown[:2]))
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
