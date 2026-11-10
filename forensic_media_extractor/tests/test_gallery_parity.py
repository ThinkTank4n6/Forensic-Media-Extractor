"""The server's gallery filter gives the page's answer, exactly.

fme/review/galleryquery.py ports the page's filter (app.js visible() and the
Filters pane's counts) so the gallery can stop holding every file. A port
that disagreed would hide files from an examiner without a word. So this
builds a case with deliberately varied metadata, gradings, flags, notes,
hash-set hits, look-alike groups and screening results; then, for several
hundred random filter states, asks the page's own functions in Chromium and
the server the same question, and requires the same files in the same order
and the same facet counts.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("(not checked: playwright is not installed)")
    sys.exit(0)

import logging

from werkzeug.serving import make_server

logging.getLogger("werkzeug").setLevel(logging.ERROR)   # one line per request is noise

from fme.web import media_gui as gui
# This compares the page's own list with the server's, so the page must
# hold the list: whatever FME_PAGED_THRESHOLD says, this case is sent whole.
gui.PAGED_THRESHOLD = 10 ** 9

STATES = 400
ok = True
rng = random.Random(20261106)
ROOT = os.path.join(tempfile.gettempdir(), "gallery_parity-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def pick(*choices):
    return rng.choice(choices)


records = []
for i in range(400):
    category = pick("image", "image", "image", "video", "audio", "other")
    ext = {"image": pick("jpg", "png", "heic", "gif", "JPG"), "video": pick("mp4", "mov", "avi"),
           "audio": pick("m4a", "amr"), "other": pick("pdf", "")}[category]
    # Zone-less (a device clock) and zoned (UTC, as NTFS and MP4 record),
    # with zoned ones either side of New Year so the zone decides the year.
    stamp = lambda: pick("", "", f"20{rng.randint(10, 26):02d}-0{rng.randint(1, 9)}-11T10:00:00",
                         f"20{rng.randint(10, 26):02d}-01-01T0{rng.randint(1, 9)}:00:00+00:00",
                         f"20{rng.randint(10, 26):02d}-12-31T2{rng.randint(0, 3)}:30:00+00:00")
    records.append({
        "out_path": f"{category}s/{ext or 'none'}/f{i:04d}_{pick('IMG', 'DSC', 'vid', 'x')}_{i % 7}.{ext or 'bin'}",
        "ext": ext, "category": category,
        "engine": pick("filesystem", "carve", "database", "folder", "embedded"),
        "size": pick(0, rng.randint(1, 99_000), rng.randint(100_000, 9_000_000),
                     rng.randint(10_000_000, 2_000_000_000)),
        "deleted": rng.random() < 0.3,
        "sha256": f"{rng.getrandbits(256):064x}", "md5": f"{rng.getrandbits(128):032x}",
        "sha1": f"{rng.getrandbits(160):040x}",
        "source_path": pick("", f"/DCIM/100APPLE/IMG_{i}.JPG", f"/Users/x/Pictures/p{i}.jpg"),
        "offset": None if rng.random() < 0.6 else rng.randint(0, 10 ** 9),
        "fs_type": pick("FAT32", "NTFS", ""), "mtime": stamp(),
        "captured": stamp(), "atime": stamp(), "crtime": stamp(),
        "latitude": None if rng.random() < 0.7 else rng.uniform(-60, 60),
        "longitude": None, "camera_make": pick("", "", "Canon", "Apple", "samsung"),
        "camera_model": pick("", "iPhone 12", "EOS 5D", "SM-G991"),
        "artist": pick("", "", "J. Doe"), "body_serial": pick("", "", "SN123"),
        "city": pick("", "", "Media", "Philadelphia"), "state": pick("", "PA"),
        "country": pick("", "United States"),
        "animated": category == "image" and rng.random() < 0.1,
        "container": rng.random() < 0.08,
    })
case_dir = os.path.join(ROOT, "case")
os.makedirs(case_dir)
with open(os.path.join(case_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic"}, "files": records}, fh)

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
opened = client.post("/api/cases/open", json={"out_dir": case_dir}, headers=H).get_json()
job = opened.get("job_id") or gui.LAST_JOB.get("id")

# What later passes add to the job's rows: look-alike groups, hash-set hits,
# screening. Set on the rows the page and the server both read.
with gui.JOBS_LOCK:
    rows = [dict(r) for r in gui.JOBS[job]["files"]]
    for r in rows:
        if rng.random() < 0.3:
            r["similar_group"] = rng.randint(1, 25)
        if rng.random() < 0.15:
            r["hash_sets"] = [{"kind": pick("nsrl", "vic", "custom"), "set": "s"}]
        if rng.random() < 0.4:
            r["skin_band"] = pick("Low", "Medium", "High", "")
            r["faces"] = pick(-1, 0, 1, 3)
    gui.JOBS[job]["files"] = rows

case = gui._case_for(job)
digests = [r["sha256"] for r in rows]
for code in (0, 1, 2, 3, 9):
    case.set_category(rng.sample(digests, 25), code, by="Det. C. T.")
case.set_category(rng.sample(digests, 20), 2, by="hash set: VIC test")
flag_ids = [f["id"] for f in case.flags()][:3]
for sha in rng.sample(digests, 60):
    case.set_file_flag(sha, rng.choice(flag_ids), True)
for sha in rng.sample(digests, 15):
    case.set_note(sha, pick("seen before", "check with partner", "duplicate of exhibit 3"))

server = make_server("127.0.0.1", 0, gui.app, threaded=True)
port = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()


def ask_server(state):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/gallery/query", method="POST",
        data=json.dumps({"job": job, "state": state, "limit": 5000,
                         "facets": True, "counts": True}).encode(),
        headers={"Content-Type": "application/json", "X-FME-Request": "1"})
    with urllib.request.urlopen(request, timeout=60) as reply:
        return json.loads(reply.read())


ASK_PAGE = """(st) => {
  filter = st.filter; catFilter = st.catFilter; flagFilter = new Set(st.flagFilter);
  facetFilter = Object.fromEntries(Object.entries(st.facetFilter)
                  .map(([k, v]) => [k, new Set(v)]));
  document.getElementById('search').value = st.search;
  showContainers = st.showContainers; hideKnownSoftware = st.hideKnownSoftware;
  stackSimilar = st.stackSimilar;
  similarOnly = st.similarOnly ? new Set(st.similarOnly) : null;
  timeZone = st.timeZone;
  const paths = visible().map(f => f.rel_path);
  const facets = {};
  for (const facet of FACETS) {
    const counts = {}; let missing = 0;
    files.filter(f => passesAll(f, facet.key)).forEach(f => {
      const values = facetValues(facet, f);
      if (!values.length) { missing += 1; return; }
      values.forEach(v => { counts[v] = (counts[v] || 0) + 1; });
    });
    facets[facet.key] = {counts, missing};
  }
  // What the category bar and the Classify pane count, as this page counts
  // them (countsFromDecisions, renderClassification) -- the server's
  // aggregates must say the same for a case browsed a page at a time.
  const byCategory = countsFromDecisions();
  const base = files.filter(f => passesBase(f));
  const classifyCat = {};
  base.forEach(f => { const c = categoryOf(f); classifyCat[c] = (classifyCat[c] || 0) + 1; });
  const scope = catFilter === "" ? base : base.filter(f => String(categoryOf(f)) === catFilter);
  const flags = {};
  scope.forEach(f => ((decisions[f.sha256] || {}).flags || []).forEach(id => {
    flags[id] = (flags[id] || 0) + 1; }));
  return {paths, facets, byCategory, classifyCat, flags};
}"""

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
    page.wait_for_timeout(800)
    # The page's own loading path for a case: its rows from /api/status,
    # its decisions from /api/case.
    page.evaluate("""async (id) => { jobId = id;
        files = takeFiles(await api('/api/status?job_id=' + id), []);
        await loadCase(); }""", job)
    loaded = page.evaluate("files.length")
    print(f"a case of {loaded} files, {len(set(r.get('similar_group') for r in rows if r.get('similar_group')))} "
          f"look-alike groups, {len(case.decisions())} gradings or flags")
    ok &= loaded == len(rows)

    # Facet values worth choosing are the ones that occur.
    occurring = page.evaluate("""() => Object.fromEntries(FACETS.map(f =>
        [f.key, [...new Set(files.flatMap(x => facetValues(f, x)))]]))""")
    filters = ["all", "deleted", "carve", "database", "folder", "embedded", "gps",
               "auto", "needs-review", "image", "video", "audio", "other"]
    searches = ["", "", "", "jpg", "img", "name:dsc", "cam:canon", "cam:apple iphone",
                "note:seen", "md5:a", "nothing-matches", "dcim img", "path:/users", "jp"]
    every_path = [r["rel_path"] for r in rows]
    mismatches, sizes, count_mismatches = [], [], []
    for n in range(STATES):
        # Each part of the filter is on independently and not too often, so
        # most states return a real list rather than nothing -- an empty
        # answer agreeing with an empty answer proves little.
        facet_filter = {}
        if rng.random() < 0.35:
            for key in rng.sample(list(occurring), rng.choice((1, 1, 2))):
                values = occurring[key]
                chosen = rng.sample(values, min(len(values), rng.choice((1, 2, 3)))) if values else []
                if rng.random() < 0.15:
                    chosen.append("\u0000")
                facet_filter[key] = chosen
        state = {
            "filter": rng.choice(filters) if rng.random() < 0.5 else "all",
            "catFilter": rng.choice(["0", "1", "2", "8", "9"]) if rng.random() < 0.25 else "",
            "flagFilter": rng.sample(flag_ids, 1) if rng.random() < 0.15 else [],
            "facetFilter": facet_filter,
            "search": rng.choice(searches) if rng.random() < 0.3 else "",
            "showContainers": rng.random() < 0.3, "hideKnownSoftware": rng.random() < 0.3,
            "stackSimilar": rng.random() < 0.3,
            "similarOnly": rng.sample(every_path, 120) if rng.random() < 0.1 else None,
            "timeZone": rng.choice(["UTC", "America/New_York", "Asia/Tokyo"]),
        }
        mine = page.evaluate(ASK_PAGE, state)
        theirs = ask_server(state)
        server_paths = [f["rel_path"] for f in theirs["files"]]
        sizes.append(len(mine["paths"]))
        served = theirs.get("counts") or {}
        as_text = lambda d: {str(k): v for k, v in (d or {}).items()}
        counts_agree = (as_text(served.get("by_category")) == as_text(mine["byCategory"])
                        and as_text((served.get("classify") or {}).get("by_category"))
                        == as_text(mine["classifyCat"])
                        and as_text((served.get("classify") or {}).get("flags"))
                        == as_text(mine["flags"]))
        if not counts_agree:
            count_mismatches.append((n, state))
        if server_paths != mine["paths"] or theirs["facets"] != mine["facets"]:
            mismatches.append((n, state, len(mine["paths"]), len(server_paths),
                               server_paths == mine["paths"]))
    browser.close()

server.shutdown()
sizes.sort()
print(f"{STATES} filter states compared: {STATES - len(mismatches)} identical")
# A comparison of empty answers proves nothing: say how varied they were.
print(f"  results per state: {sum(1 for x in sizes if x == 0)} empty, median "
      f"{sizes[len(sizes) // 2]}, largest {sizes[-1]} of {len(rows)}")
ok &= sum(1 for x in sizes if x == 0) < STATES * 0.25 and sizes[-1] > 100
for n, state, page_n, server_n, same_list in mismatches[:5]:
    print(f"  state {n}: page {page_n} files, server {server_n}; same list: {same_list}; "
          f"{'facet counts differ' if same_list else ''}\n    {json.dumps(state)[:300]}")
print(f"  category and Classify counts agree in {STATES - len(count_mismatches)} of {STATES}")
for n, state in count_mismatches[:3]:
    print(f"    state {n}: {json.dumps(state)[:200]}")
ok &= not mismatches and not count_mismatches and not thrown
if thrown:
    print(f"  the page threw: {thrown[0][:120]}")
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
