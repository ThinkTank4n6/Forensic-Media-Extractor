"""The full size viewer grades the picture it shows (v2026.11.318).

In v2026.11.317 a grading key in the full size viewer graded the file shown
and moved the cursor on -- but the viewer stayed on the old picture, so the
next key graded a file the examiner had never seen. And 0 and 1 were taken
for zoom there: in a US case those are Non-Pertinent and CAM, and pressing
them graded nothing, without a word. Pressed in Chromium.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import contextlib
import hashlib
import io
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
sys.path.insert(0, HERE)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_viewer_grading-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "case", "images", "jpg"))
H = {"X-FME-Request": "1"}


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


case_dir = os.path.join(ROOT, "case")
records = []
for n, name in enumerate(("a.jpg", "b.jpg", "c.jpg")):
    data = fixtures.make_jpeg(20 + n)
    with open(os.path.join(case_dir, "images", "jpg", name), "wb") as fh:
        fh.write(data)
    records.append({"out_path": f"images/jpg/{name}", "ext": "jpg", "category": "image",
                    "engine": "filesystem", "size": len(data), "deleted": False,
                    "sha256": hashlib.sha256(data).hexdigest(), "md5": "", "sha1": "",
                    "source_path": f"/DCIM/{name}", "offset": None, "fs_type": "FAT32",
                    "mtime": "", "captured": "", "atime": "", "crtime": "",
                    "latitude": None, "longitude": None})
with open(os.path.join(case_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic"}, "files": records}, fh)

client = gui.app.test_client()
opened = client.post("/api/cases/open", json={"out_dir": case_dir}, headers=H).get_json()
job = opened.get("job_id") or gui.LAST_JOB.get("id")
sha = [r["sha256"] for r in records]

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
    print("(not checked: playwright is not installed)")

if sync_playwright is not None:
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, gui.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            browser = None
            print(f"(not checked: {str(exc)[:80]})")
        if browser is not None:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            thrown = []
            page.on("pageerror", lambda e: thrown.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="load")
            page.wait_for_timeout(500)
            page.evaluate("""async (id) => { jobId = id;
                files = takeFiles(await api('/api/status?job_id=' + id), []);
                await loadCase(); render(); }""", job)
            page.evaluate("() => { const f = lookup('images/jpg/a.jpg'); moveTo(f); "
                          "viewFullSize(f); }")
            page.wait_for_selector("#full-body img", timeout=5000)
            title = lambda: page.evaluate("() => document.getElementById('full-title').textContent")
            graded = lambda: [client.get(f"/api/case?job={job}").get_json()["decisions"]
                              .get(h, {}).get("category") for h in sha]
            page.keyboard.press("2")
            page.wait_for_timeout(700)
            check("a key grades the picture shown", graded()[0] == 2, graded())
            check("and the viewer moves on with the cursor", title() == "b.jpg", title())
            page.keyboard.press("1")
            page.wait_for_timeout(700)
            check("1 grades in a US case (CAM), rather than zooming",
                  graded()[:2] == [2, 1], graded())
            check("again on the picture that was shown", title() == "c.jpg", title())
            page.keyboard.press("+")
            check("+ still zooms", page.evaluate("() => zoomScale") > 0)
            check("no script error", not thrown, thrown[:2])
            browser.close()
    server.shutdown()

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
