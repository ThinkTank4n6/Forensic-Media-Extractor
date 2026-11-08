"""A new run never shows the last case's grades, however late an answer is.

Start cleared the page's grades and categories, but an answer for the last
case still in flight could land after it: the last case's grades then showed
on this run's files (graded by hash, so any file the two shared), and its
categories stopped this run asking for its own -- the poll asked only while
there were none. Reproduced by delaying the last case's answer 2.5 s and
starting a run at once: the shared file showed the last case's category 7.

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
    sys.exit(0)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_browser_stale-") + str(os.getpid())
PORT = 8798


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


with socket.socket() as probe:
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"port {PORT} is in use; skipping")
        sys.exit(0)

# Long enough to look at the second run while it is still carving.
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
image = os.path.join(ROOT, "disk.dd")
rng = random.Random(20261104)
size = 160 << 20
blob = bytearray(b"".join(rng.randbytes(1 << 20) for _ in range(size >> 20)))
picture = fixtures.make_jpeg(6)
for i in range(600):
    note = f"picture {i:05d}".encode()
    one = picture[:2] + b"\xff\xfe" + struct.pack(">H", len(note) + 2) + note + picture[2:]
    at = i * (size // 600) + 4096
    blob[at:at + len(one)] = one
with open(image, "wb") as fh:
    fh.write(bytes(blob))
del blob

START = """async (path) => {
    const used = await api('/api/use', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
    setActive(path, used.info);
    document.getElementById('mode').value = 'carve';
    document.getElementById('btn-start').click(); }"""

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
        page = browser.new_page()
        thrown = []
        page.on("pageerror", lambda error: thrown.append(str(error)))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

        page.evaluate(START, image)
        page.wait_for_function(
            "() => /^done/.test(document.getElementById('status').innerText)", timeout=240000)
        first, digest = page.evaluate("() => [jobId, visible()[0].sha256]")
        page.evaluate("""async ([job, sha]) => api('/api/case/categorize', {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({job, sha256: [sha], category: 7})})""", [first, digest])

        # The first case's answer arrives 2.5 s late; ask for it, then start.
        page.evaluate("""(first) => { const real = window.fetch;
            window.fetch = (url, options) => String(url).includes('/api/case?job=' + first)
              ? new Promise(r => setTimeout(r, 2500)).then(() => real(url, options))
              : real(url, options); }""", first)
        page.evaluate("() => { loadCase(); }")
        page.evaluate(START, image)
        page.wait_for_timeout(4000)
        look = page.evaluate("""(sha) => ({job: jobId,
            status: document.getElementById('status').innerText,
            grade: (decisions[sha] || {}).category ?? null, caseFor})""", digest)
        if not look["status"].startswith("carving"):
            print(f"not checked: the second run was not carving at the look ({look['status']})")
        else:
            check("the second run does not show the first case's grade",
                  look["grade"] is None, str(look["grade"]))
            check("and holds its own case, not the first's",
                  look["caseFor"] == look["job"] != first)
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
