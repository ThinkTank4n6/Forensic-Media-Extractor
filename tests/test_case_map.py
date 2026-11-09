"""The case map: every located file the filter shows, on the offline basemap.

Adopted from GLEAPP's gallery map (MIT) in v2026.11.313; it was the last
item AUDIT.md listed as theirs and not ours. Checked here the way an
examiner meets it -- the button pressed in Chromium -- and at the server:

1. the style and every asset it names come from this program's own server,
   so nothing is fetched from the internet;
2. the PMTiles file is served by byte range, and only a file the basemap
   list names;
3. the points are the gallery filter's own located files, with no
   thumbnail, and an impossible position is counted rather than drawn;
4. in the page, MapLibre loads under the page's Content-Security-Policy,
   draws the points, and a point takes the examiner to its file.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
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
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from fme.engines import basemaps  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "case_map-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
H = {"X-FME-Request": "1"}


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def record(i, lat, lon, deleted=False):
    return {"out_path": f"images/jpg/f{i:03d}.jpg", "ext": "jpg", "category": "image",
            "engine": "filesystem", "size": 1000 + i, "deleted": deleted,
            "sha256": f"{i:064x}", "md5": f"{i:032x}", "sha1": f"{i:040x}",
            "source_path": f"/DCIM/IMG_{i:03d}.JPG", "offset": None, "fs_type": "FAT32",
            "mtime": "2024-05-01T10:00:00", "captured": "", "atime": "", "crtime": "",
            "latitude": lat, "longitude": lon, "camera_make": "", "camera_model": "",
            "artist": "", "body_serial": "", "city": "", "state": "", "country": "",
            "animated": False, "container": False}


records = [record(0, 39.9526, -75.1652), record(1, 39.9180, -75.3880, deleted=True),
           record(2, None, None), record(3, 123.0, 400.0), record(4, 40.0, -75.2)]
case_dir = os.path.join(ROOT, "case")
os.makedirs(case_dir)
with open(os.path.join(case_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic"}, "files": records}, fh)

client = gui.app.test_client()

print("before a basemap is imported")
none_yet = client.get("/api/basemap/style")
check("the style says there is none, and where to import one",
      none_yet.status_code == 404 and "Settings" in none_yet.get_json()["error"])

rec = basemaps.import_basemap(os.path.join(HERE, "fixtures", "tiny.pmtiles"))
basemaps.set_active(rec["name"])

print("\nthe style")
style = client.get("/api/basemap/style").get_json()["style"]
check("a PMTiles of pictures is drawn as pictures",
      style["sources"]["basemap"]["type"] == "raster"
      and style["sources"]["basemap"]["url"] == f"pmtiles:///api/basemap/file/{rec['file']}")
# A vector PMTiles (the Protomaps build an examiner normally imports): the
# same file's record, said to hold vector tiles, gives the vector style.
vector_name = basemaps.import_basemap(os.path.join(HERE, "fixtures", "tiny.pmtiles"),
                                      name="vector-test")["name"]
sidecar = basemaps._sidecar(vector_name)
with open(sidecar, encoding="utf-8") as fh:
    meta = json.load(fh)
meta["tile_type"] = "mvt"
with open(sidecar, "w", encoding="utf-8") as fh:
    json.dump(meta, fh)
vector = basemaps.style(vector_name)
urls = [vector.get("glyphs", ""), vector.get("sprite", "")] + \
    [s.get("url", "") for s in vector["sources"].values()]
check("a vector one names its fonts, sprites and file, every one local",
      vector["sources"]["basemap"]["type"] == "vector"
      and all(u.startswith(("/maps/", "pmtiles:///api/basemap/file/")) for u in urls), urls)
basemaps.remove_basemap(vector_name)
basemaps.set_active(rec["name"])
assets = [vector["sprite"][len("/maps/"):] + ".json",
          "fonts/Noto Sans Regular/0-255.pbf", "maplibre-gl.js", "pmtiles.js",
          "maplibre-gl.css"]
served = {a: client.get("/maps/" + a).status_code for a in assets}
check("the assets it needs are served", all(code == 200 for code in served.values()),
      served)
check("a path out of the assets folder is refused",
      client.get("/maps/../../request_guard.py").status_code == 404
      and client.get("/maps/..%2F..%2Fmedia_gui.py").status_code == 404)

print("\nthe basemap file")
whole = os.path.getsize(rec["path"])
part = client.get(f"/api/basemap/file/{rec['file']}", headers={"Range": "bytes=0-126"})
check("is served by byte range", part.status_code == 206 and len(part.data) == 127
      and part.data[:7] == b"PMTiles")
check("only a file the basemap list names",
      client.get("/api/basemap/file/settings.json").status_code == 404
      and client.get(f"/api/basemap/file/{rec['name']}.json").status_code == 404)
check("(it is the whole basemap)", whole > 127)

opened = client.post("/api/cases/open", json={"out_dir": case_dir}, headers=H).get_json()
job = opened.get("job_id") or gui.LAST_JOB.get("id")

print("\nthe points")
answer = client.post("/api/case/map", json={"job": job, "state": {"filter": "all"}},
                     headers=H).get_json()
names = sorted(p["rel_path"] for p in answer["points"])
check("are the located files", names == ["images/jpg/f000.jpg", "images/jpg/f001.jpg",
                                          "images/jpg/f004.jpg"], names)
check("an impossible position is counted, not drawn", answer["out_of_range"] == 1)
check("no thumbnail travels with a point",
      not any("thumb" in key for p in answer["points"] for key in p))
deleted = client.post("/api/case/map", json={"job": job, "state": {"filter": "deleted"}},
                      headers=H).get_json()
check("and they follow the gallery's filter",
      [p["rel_path"] for p in deleted["points"]] == ["images/jpg/f001.jpg"])
check("a request from another site is refused",
      client.post("/api/case/map", json={"job": job}).status_code == 403)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("\n(not checked in a browser: playwright is not installed)")
    sync_playwright = None

if sync_playwright is not None:
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, gui.app, threaded=True)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"\n(not checked in a browser: Chromium will not launch: {str(exc)[:80]})")
            browser = None
        if browser is not None:
            print("\nin the page")
            page = browser.new_page()
            thrown, refused = [], []
            page.on("pageerror", lambda e: thrown.append(str(e)))
            page.on("console", lambda m: refused.append(m.text)
                    if "Content Security Policy" in m.text else None)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="load")
            page.wait_for_timeout(500)
            page.evaluate("""async (id) => { jobId = id;
                files = takeFiles(await api('/api/status?job_id=' + id), []);
                await loadCase(); render(); }""", job)
            page.evaluate("() => openCaseMap()")
            try:
                page.wait_for_function(
                    "() => caseMap && caseMap.loaded() && caseMap.getSource('files')",
                    timeout=30000)
                drawn = True
            except Exception:  # noqa: BLE001
                drawn = False
            check("MapLibre loads under the page's policy and draws the map", drawn,
                  (thrown + refused)[:2] or "")
            check("nothing the policy refused", not refused, refused[:2])
            info = page.inner_text("#map-info")
            check("the header counts what is drawn", info.startswith("3 of"), info[:60])
            if drawn:
                page.wait_for_timeout(800)
                rendered = page.evaluate(
                    "() => caseMap.querySourceFeatures('files').length")
                check("the points are on the map", rendered >= 1, rendered)
                page.evaluate("() => goToFileFromMap('images/jpg/f004.jpg')")
                page.wait_for_timeout(500)
                closed = not page.evaluate(
                    "() => document.getElementById('map-modal').classList.contains('open')")
                check("a point's button closes the map and goes to its file",
                      closed and page.evaluate("() => selectedPath") == "images/jpg/f004.jpg")
            check("no script error", not thrown, thrown[:2])
            browser.close()
    server.shutdown()

print("\nreachable")
with open(os.path.join(PROJECT, "fme", "web", "ui", "app.js"), encoding="utf-8") as fh:
    script = fh.read()
check("the View tab has the Case map button", 'label: "Case map"' in script
      and "go: openCaseMap" in script)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
