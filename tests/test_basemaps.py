"""Offline basemaps: imported once, hashed, and the report's and the details
pane's locator maps drawn from them with nothing fetched from anywhere.

The report used to draw located files as dots with no map under them,
because no map data ships and none may be fetched. With a basemap imported,
the overview is a map, each located card has a locator, and every map names
the basemap by SHA-256 so it can be drawn again the same way.
"""
import hashlib
import io
import logging
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_basemaps-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
# Settings, and the imported basemaps with them, go here and nowhere else.
os.environ["XDG_CONFIG_HOME"] = os.path.join(ROOT, "config")
os.environ["LOCALAPPDATA"] = os.path.join(ROOT, "config")

try:
    from PIL import Image
except ImportError:
    print("Pillow is not installed: not checked")
    sys.exit(0)

from fme.engines import basemaps, mvt, staticmap  # noqa: E402
from fme.exports import report_maps  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "tiny.pmtiles")


def check(label, good):
    global ok
    ok &= bool(good)
    print(f"{label} -- {'ok' if good else 'WRONG'}")


# 1. Nothing imported: no maps, and the report says why.
check("with nothing imported there is no basemap", report_maps.active() is None)

# 2. Import the PMTiles fixture: copied, hashed, made active.
rec = basemaps.import_basemap(FIXTURE)
with open(FIXTURE, "rb") as fh:
    digest = hashlib.sha256(fh.read()).hexdigest()
check("an imported basemap is recorded by its SHA-256", rec["sha256"] == digest)
check("and copied under the settings folder, not referenced where it was",
      rec["path"].startswith(os.path.join(ROOT, "config"))
      and os.path.isfile(rec["path"]))
check("the first one imported becomes the one maps are drawn on",
      basemaps.get_active() == rec["name"])

# 3. A raster MBTiles file, built here: one tile, flipped row (TMS).
mb = os.path.join(ROOT, "one.mbtiles")
tile = io.BytesIO()
Image.new("RGB", (256, 256), (90, 140, 200)).save(tile, "PNG")
con = sqlite3.connect(mb)
con.executescript("CREATE TABLE metadata (name TEXT, value TEXT);"
                  "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, "
                  "tile_row INTEGER, tile_data BLOB);")
con.executemany("INSERT INTO metadata VALUES (?,?)",
                [("name", "one"), ("format", "png"), ("minzoom", "0"),
                 ("maxzoom", "0"), ("bounds", "-180,-85,180,85")])
con.execute("INSERT INTO tiles VALUES (0,0,0,?)", (tile.getvalue(),))
con.commit()
con.close()
second = basemaps.import_basemap(mb, name="one")
check("an MBTiles file imports too", second["format"] == "mbtiles")
check("a second import does not take over from the first",
      basemaps.get_active() == rec["name"])
basemaps.set_active("one")
check("the examiner chooses which to draw on", basemaps.get_active() == "one")
basemaps.set_active(rec["name"])

# 4. Something that is not a basemap is refused.
junk = os.path.join(ROOT, "junk.pmtiles")
with open(junk, "wb") as fh:
    fh.write(b"not a map" * 100)
try:
    basemaps.import_basemap(junk)
    refused = False
except ValueError:
    refused = True
check("a file that is neither format is refused", refused)

# 5. Maps drawn from it.
maps = report_maps.active()
check("the report finds the active basemap", maps is not None)
png = maps.png([(38.9, -77.0)], report_maps.LOCATOR)
check("a locator is a PNG of the asked size",
      png and Image.open(io.BytesIO(png)).size == report_maps.LOCATOR)
check("its caption names the basemap by SHA-256", digest in maps.caption())
check("the tile decoder's module is the one carried here",
      mvt.__name__ == "fme.engines.mvt" and staticmap.__name__ == "fme.engines.staticmap")

# 6. The case report uses it: an overview map and a locator on the card.
from fme.exports import report  # noqa: E402
rows = [{"out_path": "images/jpg/a.jpg", "sha256": "a" * 64, "latitude": 38.9,
         "longitude": -77.0, "category_name": "Uncategorized", "flag_names": "",
         "size": 10, "ext": "jpg"},
        {"out_path": "images/jpg/b.jpg", "sha256": "b" * 64, "latitude": 40.7,
         "longitude": -74.0, "category_name": "Uncategorized", "flag_names": "",
         "size": 10, "ext": "jpg"}]
data = {"rows": rows, "_maps": maps, "out_dir": ROOT}
block = report._summary_block(data)
check("the overview is a map, not a scatter of dots",
      'class="map"' in block and "data:image/png;base64," in block
      and 'class="scatter"' not in block)
check("and says which basemap it was drawn on", digest in block)
card = report._card(rows[0], data, thumbnails=True, blur=False, fields=["size"],
                    media_folder="", link_full=False)
check("a located card carries a locator", 'class="locator"' in card)
data["_maps"] = None
check("without a basemap the report keeps the scatter and says why",
      'class="scatter"' in report._summary_block(data)
      and "no basemap is imported" in report._summary_block(data))

# 7. The page's endpoint.
from fme.web import media_gui  # noqa: E402
client = media_gui.app.test_client()
answer = client.get("/api/locator?lat=38.9&lon=-77.0")
check("the details pane's locator is served as a PNG",
      answer.status_code == 200 and answer.mimetype == "image/png"
      and answer.headers.get("X-FME-Basemap") == digest)
check("a nonsense position is refused",
      client.get("/api/locator?lat=x&lon=1").status_code == 400)
listed = client.get("/api/basemap").get_json()
check("the settings page lists what is imported",
      listed["ok"] and listed["active"] == rec["name"] and len(listed["basemaps"]) == 2)

# 8. Removing the active one leaves no basemap, not a broken one.
basemaps.remove_basemap(rec["name"])
basemaps.remove_basemap("one")
check("with every basemap removed there is none to draw on",
      report_maps.active() is None
      and client.get("/api/locator?lat=38.9&lon=-77.0").status_code == 404)

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
