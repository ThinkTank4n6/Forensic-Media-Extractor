"""A grade goes only where it was made (v2026.11.320).

What a review of v2026.11.319 found, each reproduced before it was fixed:

1. opening case A and then case B before A's file list arrived put A's
   files under B's job, and a grading of one was written into B's case.db;
2. a hash-set scan read every decision once, then wrote file by file: a
   grade the examiner made during the scan was replaced by the set's;
3. the stash only ever added, so a grade undone in one case was applied to
   the next case holding the file, as "your stash";
4. an examiner's own category (10 and up) was carried to another case as a
   bare number, where it named a different category.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import contextlib
import io
import logging
import os
import shutil
import sqlite3
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
from fme.engines import media_core  # noqa: E402
from fme.review import stash  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_grading_right-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
H = {"X-FME-Request": "1"}
client = gui.app.test_client()
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def make_case(name, seeds):
    out_dir = os.path.join(ROOT, name)
    out = media_core.OutputManager(out_dir, metadata=False)
    for n in seeds:
        out.store(iter([fixtures.make_jpeg(n)]), category="image", ext="jpg",
                  stem=f"recovered_{out.next_index():04d}_{n}", engine="folder",
                  source_path=f"/{name}/{n}.jpg", fs_type="", partition="",
                  signature="folder-file")
    out.close({"image": f"/evidence/{name}"})
    job = client.post("/api/cases/open", json={"out_dir": out_dir},
                      headers=H).get_json()["job_id"]
    with gui.JOBS_LOCK:
        digests = [f["sha256"] for f in gui.JOBS[job]["files"]]
    return out_dir, job, digests


def on_disk(out_dir):
    con = sqlite3.connect(os.path.join(out_dir, "case.db"))
    try:
        return dict(con.execute("SELECT sha256, category FROM decisions"))
    finally:
        con.close()


gui.settings.save({"stash_path": os.path.join(ROOT, "stash.db")})
A, job_a, a_files = make_case("A", (3, 4))
B, job_b, b_files = make_case("B", (5, 6))

print("1. a grading names its case")
r = client.post("/api/case/categorize", headers=H,
                json={"job": job_b, "sha256": [a_files[0]], "category": 1})
check("a file of case A graded under case B's job is refused",
      r.status_code == 409 and not on_disk(B), (r.status_code, on_disk(B)))
r = client.post("/api/case/categorize", headers=H,
                json={"job": job_b, "sha256": [b_files[0]], "category": 7})
check("and B's own file is graded", r.status_code == 200 and on_disk(B) == {b_files[0]: 7})
client.post("/api/case/categorize", headers=H,
            json={"job": job_b, "sha256": [b_files[0]], "category": 8})

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is not None:
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, gui.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception:  # noqa: BLE001
            browser = None
        if browser is not None:
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="load")
            page.wait_for_timeout(400)
            # A's status answer held back, so B's open finishes first.
            page.evaluate("""() => { const real = api; let n = 0;
              window.api = api = async (url, opts) => {
                if (url.startsWith('/api/status?job_id=') && ++n === 1)
                  await new Promise(r => setTimeout(r, 1500));
                return real(url, opts); }; }""")
            page.evaluate("([a, b]) => { openCase(a); setTimeout(() => openCase(b), 300); }",
                          [A, B])
            page.wait_for_timeout(3500)
            shown = page.evaluate("() => files.map(f => f.sha256)")
            check("in the page, the slower earlier open does not take over the later one",
                  sorted(shown) == sorted(b_files), len(shown))
            browser.close()
    server.shutdown()

print("\n2. a hash-set scan never replaces a grade made during it")
case_a = gui._case_for(job_a)
stale = case_a.decisions()                  # what a scan read before the grade
case_a.set_category([a_files[0]], 2)        # the examiner, meanwhile
real = case_a.decisions
case_a.decisions = lambda: stale
try:
    applied = gui._apply_hash_categories(job_a, {a_files[0]: [
        {"set": "unit store", "kind": "case", "category": 1, "region": "US",
         "via": "store", "record": {}}]})
finally:
    case_a.decisions = real
check("the examiner's 2 stands; the set's 1 is not written",
      on_disk(A).get(a_files[0]) == 2 and applied["categorized"] == 0,
      (on_disk(A).get(a_files[0]), applied))

print("\n3. an undone grade leaves the stash")
client.post("/api/case/categorize", headers=H,
            json={"job": job_a, "sha256": [a_files[1]], "category": 1})
own = stash.Stash(os.path.join(ROOT, "stash.db"))
before = bool(own.lookup([a_files[1]]))
own.close()
client.post("/api/case/undo", headers=H, json={"job": job_a})
own = stash.Stash(os.path.join(ROOT, "stash.db"))
after = own.lookup([a_files[1]])
own.close()
check("graded CAM, it is stashed; undone, it is gone", before and not after, after)
C, job_c, c_files = make_case("C", (4,))      # holds A's second file
client.post("/api/store/scan", headers=H, json={"job": job_c})
check("so the next case holding it is not graded from the stash",
      on_disk(C).get(c_files[0]) in (None, 8), on_disk(C))

print("\n4. an examiner's own category stays in its case")
code = gui._case_for(job_a).add_category("Series X")
client.post("/api/case/categorize", headers=H,
            json={"job": job_a, "sha256": [a_files[1]], "category": code})
own = stash.Stash(os.path.join(ROOT, "stash.db"))
check("it is not put in the stash", not own.lookup([a_files[1]]))
own.close()
gui._case_for(job_c).add_category("Vacation photos")
applied = gui._apply_hash_categories(job_c, {c_files[0]: [
    {"set": "unit store", "kind": "case", "category": code, "region": "US",
     "via": "store", "record": {}}]})
check("and a store's code 10 is not applied as this case's 10",
      on_disk(C).get(c_files[0]) in (None, 8) and applied.get("other_scheme") == 1,
      (on_disk(C), applied))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
