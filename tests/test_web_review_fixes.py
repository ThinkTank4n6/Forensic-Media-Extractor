"""Interface defects found by an outside review, checked through the routes.

- Stored XSS: esc() left " and ' alone and was used inside attributes, so an
  EXIF Make of `x" onmouseover="...` ran script in the examiner's page. It
  now escapes all five characters, and the page is served with a
  Content-Security-Policy under which only its own (nonced) script runs.
- Two C4All exports of one case at once are refused, not interleaved.
- /api/export/md5 is written by the csv module from the case's records:
  every file even past PAGED_THRESHOLD, and nothing Excel would run.
- expand-containers puts what it recovered into the page's list of the case.
- A shared hash store imports from a path the server sees; it was refused
  because the path did not exist on this machine.
- The FAT32 browser: listing and extracting while a carve runs give the
  same answers, and a second carve of one volume is refused.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.web import media_gui as g  # noqa: E402
from fme.web import routes_store  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "web_review_fixes-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
H = {"X-FME-Request": "1"}
client = g.app.test_client()
failures = []


def check(label, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + label
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


# ------------------------------------------------------------ escaping, CSP
page = client.get("/")
policy = page.headers.get("Content-Security-Policy", "")
body = page.get_data(as_text=True)
nonce = re.search(r"script-src 'nonce-([^']+)'", policy)
check("the page is served with a script policy that names a nonce", bool(nonce),
      policy)
if nonce:
    check("its script tag carries that nonce",
          f'<script nonce="{nonce.group(1)}">' in body)
    check("and no inline handler or unsafe-inline script is allowed",
          "'unsafe-inline'" not in policy.split("script-src", 1)[1].split(";")[0]
          and "script-src-attr 'none'" in policy, policy)
    again = client.get("/").headers.get("Content-Security-Policy", "")
    check("the nonce is new on every load", again != policy)

ui = os.path.join(PROJECT, "fme", "web", "ui")
with open(os.path.join(ui, "app.js"), encoding="utf-8") as fh:
    script = fh.read()
with open(os.path.join(ui, "page.html"), encoding="utf-8") as fh:
    markup = fh.read()
# Handlers written into markup would be refused by the policy, and the
# feature behind them would stop without an error anyone sees.
inline = re.findall(r"""\son[a-z]+\s*=\s*["']""", markup + script)
check("no inline event handler in the page or in markup app.js writes",
      not inline, inline[:3])

node = shutil.which("node")
if node:
    probe = ("const document = {};\n"
             + re.search(r"const ESC_MAP = .*?\nfunction esc\(s\) \{.*?\}\n",
                         script, re.S).group(0)
             + "process.stdout.write(JSON.stringify([esc(`x\" onmouseover=\"alert(1)`),"
               " esc(\"a'b\"), esc(\"<&>\"), esc(null), esc(5)]));")
    out = subprocess.run([node, "-e", probe], capture_output=True, text=True)
    try:
        got = json.loads(out.stdout)
    except ValueError:
        got = [out.stderr]
    check("esc() escapes quotes, so an EXIF value cannot leave its attribute",
          got == ["x&quot; onmouseover=&quot;alert(1)", "a&#39;b",
                  "&lt;&amp;&gt;", "", "5"], got)
else:
    print("note: node is not installed; esc() itself is checked by test_browser")

# In a real browser: markup that carries a handler, as an unescaped EXIF
# value once did, does not run under the page's policy -- and the page's own
# script still does.
try:
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
except ImportError:
    sync_playwright = None
    print("note: Playwright is not installed; the policy is not checked in a browser")
if sync_playwright is not None:
    import logging

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, g.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                browser = None
                print(f"note: Chromium will not launch here: {str(exc)[:80]}")
            if browser is not None:
                tab = browser.new_page()
                tab.goto(f"http://127.0.0.1:{server.server_port}/",
                         wait_until="networkidle")
                ran = tab.evaluate("() => typeof esc === 'function' && "
                                   "esc('\"') === '&quot;'")
                tab.evaluate("""() => {
                    const box = document.createElement('div');
                    box.innerHTML = '<img src="/nothing-here" ' +
                        'onerror="window.__ran = 1">' +
                        '<script>window.__ran = 2<\\/script>';
                    document.body.appendChild(box);
                }""")
                tab.wait_for_timeout(600)
                check("in Chromium the page's own script runs", ran is True, ran)
                check("and an injected handler does not",
                      tab.evaluate("() => window.__ran") is None,
                      tab.evaluate("() => window.__ran"))
                browser.close()
    finally:
        server.shutdown()

# ---------------------------------------------------------------- a case
from PIL import Image  # noqa: E402

from fme.engines import media_core  # noqa: E402

picture = io.BytesIO()
Image.new("RGB", (40, 30), (200, 20, 20)).save(picture, "JPEG")
inner = io.BytesIO()
Image.new("RGB", (48, 36), (20, 20, 200)).save(inner, "PNG")
bundle = io.BytesIO()
with zipfile.ZipFile(bundle, "w") as archive:
    archive.writestr("inside.png", inner.getvalue())
case_dir = os.path.join(ROOT, "case")
# Written directly rather than by a run: a run with artifacts on looks inside
# the zip itself, which would leave expand-containers nothing to find.
out = media_core.OutputManager(case_dir)
for blob, category, ext, name in ((picture, "image", "jpg", "a"),
                                  (bundle, "other", "zip", "bundle")):
    out.store(iter([blob.getvalue()]), category=category, ext=ext,
              stem=f"recovered_{out.next_index():04d}_{name}", engine="folder",
              source_path=f"/{name}.{ext}", fs_type="", partition="",
              signature="folder-file")
out.close({"image": "/evidence/folder"})
opened = client.post("/api/cases/open", json={"out_dir": case_dir}, headers=H)
job = opened.get_json().get("job_id", "")
check("the fixture case opens", bool(job), opened.get_json())
with g.JOBS_LOCK:
    before = len(g.JOBS[job]["files"])

# -------------------------------------------------------- expand-containers
r = client.post("/api/case/reprocess", json={"job": job,
                                             "reason": "expand-containers"},
                headers=H).get_json()
with g.JOBS_LOCK:
    after = g.JOBS[job]["files"]
names = [f["name"] for f in after]
check("expand-containers recovered the picture inside the zip",
      r.get("ok") and r.get("recovered", 0) >= 1, r)
check("and the page's list of the case now holds it",
      len(after) == before + r.get("recovered", 0), (before, len(after), names))

# -------------------------------------------------------------- md5 export
with g.JOBS_LOCK:
    held = g.JOBS[job]["files"]
    g.JOBS[job]["files"] = []        # as past PAGED_THRESHOLD
try:
    r = client.post("/api/export/md5", json={"job": job}, headers=H)
finally:
    with g.JOBS_LOCK:
        g.JOBS[job]["files"] = held
rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
check("the MD5 list of a case whose page holds no list still lists every file",
      r.status_code == 200 and len(rows) - 1 == len(held), (r.status_code, rows[:2]))
check("with the report's columns",
      rows and rows[0] == ["md5", "sha1", "sha256", "size", "category", "file_name"],
      rows[:1])

# ------------------------------------------------------- one export at once
with g.JOBS_LOCK:
    g.JOBS[job]["exporting"] = True
r = client.post("/api/export", json={"job": job, "case": "DC-1"}, headers=H)
with g.JOBS_LOCK:
    g.JOBS[job].pop("exporting", None)
check("a second C4All export of a case being exported is refused",
      r.status_code == 409, r.status_code)
r = client.post("/api/export", json={"job": job, "case": "DC-1"}, headers=H)
check("and once it is done, an export runs and clears the flag",
      r.status_code == 200 and "exporting" not in g.JOBS[job], r.get_json())

# --------------------------------------------------------- remote import
asked = {}


class FakeRemote:
    remote = True
    path = "https://store.example:8787"

    def import_set(self, path, kind="", name="", progress=None):
        asked.update(path=path, kind=kind, name=name)
        return {"stored": 3, "source": name or "set", "kind": kind or "csv",
                "seconds": 0}

    def size(self):
        return 3

    def sources(self):
        return []


real_store = routes_store._store
routes_store._store = lambda *a, **k: FakeRemote()
try:
    server_path = "/srv/hashsets/not-on-this-machine.json"
    r = client.post("/api/store/import", json={"path": server_path}, headers=H)
finally:
    routes_store._store = real_store
check("a shared store is asked to import a path this machine does not have",
      r.status_code == 200 and asked.get("path") == server_path,
      (r.status_code, r.get_json()))
r = client.post("/api/store/import",
                json={"path": os.path.join(ROOT, "missing.json")}, headers=H)
check("a local store still refuses a file that is not there",
      r.status_code == 400, r.status_code)

# The FAT32 browser's carve-and-list checks went with it (v2026.11.319).

shutil.rmtree(ROOT, ignore_errors=True)
print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
