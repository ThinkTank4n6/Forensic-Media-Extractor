"""NSRL comes in as Non-Pertinent, Project VIC as its own categories, and the
gallery shows what they say.

Asked for by the examiner: the hash server's page has an NSRL section and a
Project VIC section; an import says how many files and hashes came in and,
by category, what they are (every NSRL file 0; so many Project VIC files
CAM); and a file matched against the store is graded in the gallery as the
set says.

What was wrong before, each checked here:

  * a VICS file that wrote Category as "1" was imported with no category, so
    a CAM file matched against it stayed Uncategorized;
  * a VICS file with no @odata header (a bare array, a case export) was
    taken for a custom list on the server's page, which had no way to say
    otherwise, and lost every category;
  * a VICS file imported as the NSRL was salvaged hash by hash into a
    known-good set -- so a CAM file named nowhere else was graded
    Non-Pertinent.

The gallery part needs playwright and its Chromium; the rest runs anywhere.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import csv
import hashlib
import json
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
from fme.engines import media_core              # noqa: E402
from fme.review import hashserver, hashsets, hashstore   # noqa: E402

logging.disable(logging.CRITICAL)
ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_hashset_import-") + str(os.getpid())


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def md5(text):
    return hashlib.md5(text.encode()).hexdigest()


def sha1(text):
    return hashlib.sha1(text.encode()).hexdigest()


shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

# The case: real files from the test image, so the gallery has them.
IMAGE = os.path.join(HERE, "media_test.img")
media_core.run_extraction(IMAGE, os.path.join(ROOT, "pre"), mode="both")
files = json.load(open(os.path.join(ROOT, "pre", "manifest.json"),
                       encoding="utf-8"))["files"]
assert len(files) >= 5, "the test image has too few files for this test"
A, B, C, D, E = files[:5]
# A  NSRL only                         -> 0 Non-Pertinent
# B  Project VIC, Category "1" (str)   -> 1 CAM
# C  NSRL and Project VIC category 2   -> 2 (the categorized set decides)
# D  Project VIC category 5            -> left for the examiner
# E  nowhere                           -> left for the examiner

# --- the sets ------------------------------------------------------------
NSRL_FILES = 2000
nsrl_txt = os.path.join(ROOT, "NSRLFile.txt")
with open(nsrl_txt, "w", newline="", encoding="utf-8") as fh:
    out = csv.writer(fh, quoting=csv.QUOTE_ALL)
    out.writerow(["SHA-1", "MD5", "CRC32", "FileName", "FileSize",
                  "ProductCode", "OpSystemCode", "SpecialCode"])
    out.writerow(["", A["md5"].upper(), "0", "a.jpg", 1, 1, "WIN", ""])
    out.writerow(["", C["md5"].upper(), "0", "c.jpg", 1, 1, "WIN", ""])
    for i in range(NSRL_FILES - 2):
        out.writerow([sha1(f"n{i}").upper(), md5(f"n{i}").upper(), "0",
                      f"f{i}.dll", 10, 1, "WIN", ""])

nsrl_db = os.path.join(ROOT, "RDS_modern.db")
con = sqlite3.connect(nsrl_db)
con.execute("CREATE TABLE FILE (sha256 TEXT, sha1 TEXT, md5 TEXT, "
            "file_name TEXT, file_size INTEGER)")
con.executemany("INSERT INTO FILE VALUES (?,?,?,?,?)",
                [(hashlib.sha256(f"r{i}".encode()).hexdigest(), sha1(f"r{i}"),
                  md5(f"r{i}"), f"r{i}.exe", 5) for i in range(300)])
con.commit()
con.close()

# Category counts like the examiner's screenshot, in a bare array (no
# @odata header) and with some categories written as strings.
VIC_COUNTS = {0: 67, 1: 33, 2: 58, 3: 262, 4: 230, 5: 179}
records = [{"MediaID": 1, "Category": "1", "MD5": B["md5"].upper(),
            "SHA1": B["sha1"]},
           {"MediaID": 2, "Category": 2, "MD5": C["md5"]},
           {"MediaID": 3, "Category": 5, "MD5": D["md5"]}]
done = {1: 1, 2: 1, 5: 1}
n = 10
for category, count in VIC_COUNTS.items():
    for _ in range(count - done.get(category, 0)):
        n += 1
        records.append({"MediaID": n,
                        "Category": str(category) if n % 2 else category,
                        "MD5": md5(f"v{n}"), "SHA1": sha1(f"v{n}")})
# One file listed twice, at 4 and then at 1: it lands once, as 1.
records.append({"MediaID": 9001, "Category": 4, "MD5": md5("twice")})
records.append({"MediaID": 9002, "Category": 1, "MD5": md5("twice")})
vic_json = os.path.join(ROOT, "vic_bare.json")
json.dump(records, open(vic_json, "w", encoding="utf-8"))
expected_records = {str(k): v for k, v in VIC_COUNTS.items()}
expected_records["4"] += 1
expected_records["1"] += 1

not_a_set = os.path.join(ROOT, "notes.txt")
# Hash-shaped text, but nowhere a reader of the NSRL looks for a hash: only
# the salvage scan would find it.
open(not_a_set, "w").write("lunch order\nthe stray one was " + md5("stray") + " today\n")

# --- the store, directly --------------------------------------------------
check("a bare VICS array is recognized as Project VIC by its content",
      hashsets.detect_kind(vic_json) == "projectvic", hashsets.detect_kind(vic_json))
check("a Category of \"1\" is category 1", hashsets.vic_category("1") == 1
      and hashsets.vic_category(1.0) == 1 and hashsets.vic_category(True) is None
      and hashsets.vic_category("CAM") is None)

scratch = hashstore.HashStore(os.path.join(ROOT, "scratch.db"))
for kind in ("nsrl", "custom", "bogus"):
    try:
        scratch.import_set(vic_json, kind=kind, name=f"vic-as-{kind}")
        refused = False
    except ValueError:
        refused = True
    check(f"a Project VIC file imported as {kind!r} is refused", refused)
check("and nothing of it is left in the store", scratch.sources() == [],
      str([s["name"] for s in scratch.sources()]))
junk = scratch.import_set(not_a_set, kind="nsrl", name="junk")
check("a file that is not the NSRL is not salvaged into a known-good set",
      junk["stored"] == 0 and scratch.sources() == [], junk["note"][:60])
rds = scratch.import_set(nsrl_db, kind="nsrl", name="rds")
check("the NSRL RDS database: 300 files, 900 hashes, all Non-Pertinent",
      rds["categories"] == {"records": {"0": 300}, "hashes": {"0": 900}},
      str(rds["categories"]))
scratch.close()

# --- the server's Hash sets page ------------------------------------------
store_path = os.path.join(ROOT, "hash_store.db")
app = hashserver.build_app(store_path, os.path.join(ROOT, "users.db"))
token = app.config["ACCOUNTS"].add("Admin", "admin")
client = app.test_client()
here = {"Origin": "http://localhost"}
client.post("/admin", data={"token": token}, headers=here)


def import_on_page(kind, path, name):
    page = client.post("/admin/sources", headers=here, data={
        "action": "import", "kind": kind, "path": path, "name": name})
    for job in list(app.config["IMPORTS"]):
        if job.get("thread"):
            job["thread"].join(120)
    return page.get_data(as_text=True), client.get("/admin/sources").get_data(as_text=True)


page = client.get("/admin/sources").get_data(as_text=True)
check("the page has an NSRL section and a Project VIC section",
      "Import NSRL" in page and "Import Project VIC" in page
      and "value='nsrl'" in page and "value='projectvic'" in page)

started, page = import_on_page("nsrl", vic_json, "wrong section")
check("a Project VIC file put in the NSRL section is refused on the page",
      "is a Project VIC (VICS) file" in started
      and not app.config["STORE"].sources())

_, page = import_on_page("nsrl", nsrl_txt, "NSRL test")
check("the NSRL import says how many files and that every one is 0",
      f"<b>{NSRL_FILES:,}</b> file(s)" in page
      and "every one Non-Pertinent (0)" in page, page[page.find("Imported"):][:160])
nsrl = app.config["STORE"].category_breakdown("NSRL test")
check("by category, the NSRL is nothing but 0",
      list(nsrl["records"]) == ["0"] and list(nsrl["hashes"]) == ["0"]
      and nsrl["records"]["0"] == NSRL_FILES, str(nsrl))

_, page = import_on_page("projectvic", vic_json, "VIC test")
vic = app.config["STORE"].category_breakdown("VIC test")
check("the Project VIC import counts files by category exactly",
      vic["records"] == expected_records, str(vic["records"]))
check("and says how many are CAM",
      f"<b>{expected_records['1']:,}</b> of them CAM" in page,
      page[page.find("Imported"):][:200])
check("a file listed at 4 and then at 1 is stored once, as 1",
      app.config["STORE"].lookup([md5("twice")])[0]["category"] == 1)
check("a category written as \"1\" is stored as 1",
      {m["category"] for m in app.config["STORE"].lookup([B["md5"]])} == {1})
check("the page says what each category grades a file as",
      "1 Child Abuse Material (CAM)" in page
      and "left Uncategorized for the examiner" in page)
activity = [row["detail"] for row in app.config["ACCOUNTS"].access()
            if row["action"] == "import"]
check("the activity record carries the counts by category",
      any(f"1 Child Abuse Material (CAM): {expected_records['1']}" in d
          for d in activity),
      next((d for d in activity if "VIC" in d or "vic" in d), ""))
app.config["STORE"].close()
app.config["ACCOUNTS"].close()

# --- the gallery ------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed; the gallery is not checked.")
    shutil.rmtree(ROOT, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    sys.exit(0 if ok else 1)

with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    PORT = probe.getsockname()[1]

server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
START = """async (path) => { const used = await api('/api/use', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
    setActive(path, used.info); document.getElementById('btn-start').click(); }"""
SHOWN = "(shas) => shas.map(h => (decisions[h] || {}).category ?? null)"
shas = [f["sha256"] for f in (A, B, C, D, E)]
want = [0, 1, 2, None, None]
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
        page.evaluate("""async (p) => api('/api/store', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: p})})""",
                      store_path)
        page.evaluate(START, IMAGE)
        page.wait_for_function(
            "() => /^done/.test(document.getElementById('status').innerText)", timeout=120000)
        try:
            page.wait_for_function(
                "(shas) => [0, 1, 2].every(i => (decisions[shas[i]] || {}).category !== undefined)",
                arg=shas, timeout=30000)
        except Exception:                                  # noqa: BLE001
            pass
        got = page.evaluate(SHOWN, shas)
        # D (VICS 5) and E (nowhere) may be absent or Uncategorized (8).
        got = [None if g == 8 else g for g in got]
        check("the gallery shows NSRL-only as 0, a \"1\" as CAM, NSRL+VIC 2 as 2, "
              "and leaves VICS 5 and an unknown file alone", got == want,
              f"{got} for {want}")
        by = page.evaluate("(h) => (decisions[h] || {}).by || ''", shas[0])
        check("and says the set graded it, not the examiner", "NSRL" in by, by)
        check("nothing the page did threw", not thrown, "; ".join(thrown[:2]))
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(ROOT, ignore_errors=True)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
