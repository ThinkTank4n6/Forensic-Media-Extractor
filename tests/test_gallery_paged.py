"""A case too large to send is browsed a page at a time, and works the same.

Past PAGED_THRESHOLD files the server does not send the case to the page
(46 MB of JSON for 55,508 files was the cost, and a case the size reviewed in
Griffeye would not have opened). The page asks /api/gallery/query for the page
it shows, the counts around it, and -- when an action needs them -- every file
a filter matches or a look-alike group holds.

This builds a 30,000-file case, opens it in Chromium, and does what an
examiner does: page, filter, grade one file and a selection, select all that
match, grade a stacked tile, run off the end of a page, and hold a page while
grading it -- and checks each against what the server says the case holds.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
import logging
import os
import random
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

from werkzeug.serving import make_server

logging.getLogger("werkzeug").setLevel(logging.ERROR)
from fme.web import media_gui as gui  # noqa: E402

N = 30000
ok = True
rng = random.Random(20261107)
ROOT = os.path.join(tempfile.gettempdir(), "gallery_paged-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


records = []
for i in range(N):
    records.append({
        "out_path": f"images/jpg/f{i:06d}.jpg", "ext": "jpg", "category": "image",
        "engine": "filesystem" if i % 5 else "carve", "size": 1000 + i,
        "deleted": i % 3 == 0, "sha256": f"{i:064x}", "md5": f"{i:032x}",
        "sha1": f"{i:040x}", "source_path": f"/DCIM/IMG_{i:06d}.JPG", "offset": None,
        "fs_type": "FAT32", "mtime": "2024-05-01T10:00:00", "captured": "",
        "atime": "", "crtime": "", "latitude": None, "longitude": None,
        "camera_make": "", "camera_model": "", "artist": "", "body_serial": "",
        "city": "", "state": "", "country": "", "animated": False, "container": False,
    })
case_dir = os.path.join(ROOT, "case")
os.makedirs(case_dir)
with open(os.path.join(case_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic"}, "files": records}, fh)

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
opened = client.post("/api/cases/open", json={"out_dir": case_dir}, headers=H).get_json()
job = opened.get("job_id") or gui.LAST_JOB.get("id")
with gui.JOBS_LOCK:
    rows = [dict(r) for r in gui.JOBS[job]["files"]]
    for n, r in enumerate(rows):
        if n % 100 < 3:
            r["similar_group"] = n // 100 + 1        # groups of three
    gui.JOBS[job]["files"] = rows

server = make_server("127.0.0.1", 0, gui.app, threaded=True)
port = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()

status = client.get(f"/api/status?job_id={job}").get_data()
print(f"opening a case of {N:,} files (threshold {gui.PAGED_THRESHOLD:,})")
check("the status answer does not carry the case", len(status) < 64 * 1024,
      f"{len(status):,} bytes")

with sync_playwright() as play:
    try:
        browser = play.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        print(f"(not checked: Chromium will not launch: {str(exc)[:100]})")
        sys.exit(0)
    page = browser.new_page()
    thrown = []
    page.on("pageerror", lambda e: thrown.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/", wait_until="load")
    page.wait_for_timeout(600)
    page.evaluate("""async (id) => { jobId = id;
        files = takeFiles(await api('/api/status?job_id=' + id), []);
        await loadCase();
        document.getElementById('per-page').value = '200';
        page = 0; unpin(); await renderPaged(); }""", job)

    held = page.evaluate("({paged, files: files.length, seen: seen.size, total: pv.total, "
                         "case: caseSize(), tiles: document.querySelectorAll('#gallery [data-pick]').length})")
    print("\nthe page holds a page, not the case")
    check("it is in paged mode, holding no case list", held["paged"] and held["files"] == 0)
    check("one page of tiles is drawn", held["tiles"] == 200, held["tiles"])
    check("the totals are the case's", held["total"] == N and held["case"] == N,
          f"{held['total']:,} shown of {held['case']:,}")
    pager = page.inner_text("#pager")
    check("the pager counts the whole case", f"of {N:,}" in pager, pager.strip()[:60])
    bar = page.inner_text("#cat-bar")
    check("the category bar's All is the case", f"{N:,}" in bar)

    print("\npaging and filtering")
    page.evaluate("async () => { page = 3; unpin(); await renderPaged(); }")
    first = page.evaluate("pv.files[0].rel_path")
    check("page 4 starts at file 600", first == "images/jpg/f000600.jpg", first)
    page.evaluate("async () => { filter = 'deleted'; page = 0; unpin(); await renderPaged(); }")
    deleted = sum(1 for r in rows if r["deleted"])
    shown = page.evaluate("pv.total")
    check("a filter is the server's count of it", shown == deleted, f"{shown:,} of {deleted:,}")
    check("and every tile on the page matches it",
          page.evaluate("pv.files.every(f => f.deleted)"))

    print("\ngrading")
    page.evaluate("async () => { filter = 'all'; page = 0; unpin(); await renderPaged(); }")
    sha = page.evaluate("pv.files[0].sha256")
    page.evaluate("async (s) => { selection = new Set(); await categorize(s, 1); }", sha)
    # The count is the server's, asked for again a moment after a grading:
    # waited for, not assumed, on a machine that may be busy.
    try:
        # Each predicate is written as a function: a bare expression is
        # compiled in the page with new Function, which the page's
        # Content-Security-Policy (no 'unsafe-eval') refuses.
        page.wait_for_function("() => ((pv.counts || {}).by_category || {})[1] === 1", timeout=15000)
    except Exception:  # noqa: BLE001 - the check below says what it saw
        pass
    saved = gui._case_for(job).decisions().get(sha, {}).get("category")
    check("one file graded from a page is saved", saved == 1)
    counted = page.evaluate("(pv.counts.by_category || {})[1] || 0")
    check("and the category's count follows", counted == 1, counted)

    print("\nselect all that match, across pages")
    page.evaluate("async () => { filter = 'carve'; page = 0; unpin(); await renderPaged(); }")
    page.evaluate("async () => { document.getElementById('sel-all').click(); }")
    try:
        page.wait_for_function("() => selection.size > 500", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    carved = sum(1 for r in rows if r["engine"] == "carve")
    picked = page.evaluate("selection.size")
    check("every match is selected, not only this page", picked == carved,
          f"{picked:,} of {carved:,}")
    known = page.evaluate("selectedDigests().length")
    check("and each one's SHA-256 is known to grade it by", known == carved)
    page.evaluate("async () => { setSelection([]); filter = 'all'; }")

    print("\na stacked tile grades its group")
    page.evaluate("async () => { stackSimilar = true; page = 0; unpin(); await renderPaged(); }")
    lead = page.evaluate("pv.files.find(f => f.similar_group === 1).sha256")
    page.evaluate("async (s) => { selection = new Set(); await categorize(s, 2); }", lead)
    page.wait_for_timeout(1200)
    decided = gui._case_for(job).decisions()
    group = [r["sha256"] for r in rows if r.get("similar_group") == 1]
    check("every file in the group is graded, though the page held one",
          all(decided.get(d, {}).get("category") == 2 for d in group), len(group))
    page.evaluate("async () => { stackSimilar = false; }")

    print("\nrunning off the end of a page")
    page.evaluate("""async () => { document.getElementById('hold-page').checked = false;
                      catFilter = '8'; page = 0; unpin(); await renderPaged(); }""")
    last = page.evaluate("pv.files[pv.files.length - 1]")
    page.evaluate("async (p) => { selectedPath = p; selection = new Set([p]); }", last["rel_path"])
    page.evaluate("async (s) => { await categorize(s, 3); }", last["sha256"])
    try:
        page.wait_for_function("() => selectedPath !== %s && pv.files.length && selectedPath === pv.files[0].rel_path"
                               % json.dumps(last["rel_path"]), timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    moved = page.evaluate("({sel: selectedPath, first: pv.files[0] && pv.files[0].rel_path, "
                          "graded: pv.files.some(f => f.rel_path === %s)})"
                          % json.dumps(last["rel_path"]))
    check("grading the last file of a page lands on the next page's first",
          moved["sel"] == moved["first"] and moved["sel"] != last["rel_path"], moved["sel"])
    check("and the graded file has left the uncategorized list", not moved["graded"])

    print("\na held page, graded whole")
    page.evaluate("""async () => { document.getElementById('hold-page').checked = true;
                      page = 0; unpin(); await renderPaged(); }""")
    before = page.evaluate("pv.files.map(f => f.rel_path)")
    page.evaluate("async () => { setSelection(pv.files.map(f => f.rel_path)); }")
    page.evaluate("async () => { await categorize(null, 4); }")
    try:
        page.wait_for_function("() => !pv.files.some(f => %s.includes(f.rel_path))" % json.dumps(before),
                               timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    after = page.evaluate("pv.files.map(f => f.rel_path)")
    check("a page graded whole moves on to files still to be graded",
          after and not set(after) & set(before), f"{len(after)} new")
    decided = gui._case_for(job).decisions()
    check("and every file on it was saved",
          all(decided.get(f"{int(p[12:18]):064x}", {}).get("category") == 4 for p in before))

    print("\n\"all\" on a paged case")
    page.evaluate("""async () => { catFilter = ''; document.getElementById('per-page').value = '0';
                      page = 0; unpin(); await renderPaged(); }""")
    shown = page.evaluate("({tiles: pv.files.length, pager: document.getElementById('pager').style.display})")
    check("is pages of the tile cap, with a pager to reach the rest",
          shown["tiles"] == 5000 and shown["pager"] != "none", shown)

    print("\na run that is past the threshold while it goes")
    with gui.JOBS_LOCK:
        gui.JOBS[job]["status"] = "running"
        gui.JOBS[job]["phase"] = "carve"
    page.evaluate("""async () => { leavePaged(); files = []; lastLiveDraw = 0;
                      document.getElementById('per-page').value = '200'; await poll(); }""")
    try:
        page.wait_for_function("() => paged && pv.files.length === 200", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    live = page.evaluate("({paged, held: files.length, tiles: pv.files.length})")
    check("the page switches to pages instead of collecting the run",
          live["paged"] and live["held"] == 0 and live["tiles"] == 200, live)
    with gui.JOBS_LOCK:
        gui.JOBS[job]["status"] = "done"
    page.evaluate("() => { clearTimeout(timer); }")

    print("\nwhat the page holds")
    seen = page.evaluate("seen.size")
    check("the files it has looked up are capped, not the case", seen <= 50000, seen)
    check("no script error", not thrown, "; ".join(thrown[:2]))
    browser.close()

server.shutdown()
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
