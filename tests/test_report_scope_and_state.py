"""A report's side files cover its scope, and one build cannot reach another.

What is checked, each found in an outside review:

- The MD5 list, the KML and the Project VIC file of a report scoped to one
  category list that category's files only. They read the whole
  manifest.json, so a report about one category came with a hash list, a
  map and an exchange file of every file in the run.
- Two reports built at once on two threads each keep their own zone and
  their own hash-set matches. Both were module-level state, reset and
  filled by whichever build ran last.
- The agency logo set through /api/logo (a path to a file) is in the HTML,
  Word and PDF reports. They took it only as a data: URI and never showed it.
- "Generated" is in the zone the header says the times are in, with the
  zone written.
- A rebuild with fewer pages removes the old run's later pages, and a folder
  beside the report that this program did not make is never deleted.
"""
import tempfile
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from fme.exports import report
from fme.review import casedb

ROOT = os.path.join(tempfile.gettempdir(), "report_scope_state-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

failures = []


def check(label, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + label
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


from PIL import Image
import random


def picture(path, seed, size=(160, 120)):
    image = Image.new("RGB", size)
    pixels = image.load()
    random.seed(seed)
    for y in range(0, size[1], 4):
        for x in range(0, size[0], 4):
            color = tuple(random.randint(0, 255) for _ in range(3))
            for dy in range(4):
                for dx in range(4):
                    if x + dx < size[0] and y + dy < size[1]:
                        pixels[x + dx, y + dy] = color
    image.save(path, quality=90)


def make_case(name, count):
    source = os.path.join(ROOT, name + "_src")
    os.makedirs(source)
    for index in range(count):
        picture(os.path.join(source, f"p{index:03d}.jpg"), hash((name, index)) & 0xffff)
    out_dir = os.path.join(ROOT, name)
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", source,
                    "-o", out_dir], capture_output=True, cwd=os.path.dirname(HERE))
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    # Every file located, so the KML would place all of them if it read the
    # whole manifest.
    for index, rec in enumerate(manifest["files"]):
        rec["latitude"], rec["longitude"] = 39.9 + index / 1000, -75.3
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return out_dir, manifest["files"]


# ---------------------------------------------------------------- scope
out_dir, records = make_case("scoped", 4)
check("the fixture run recovered four pictures", len(records) == 4, len(records))
case = casedb.open_case(out_dir, examiner="Det. Test", case_number="SCOPE-1")
case.set_category(records[0]["sha256"], 1)
case.set_category(records[1]["sha256"], 1)
case.close()

dest = os.path.join(ROOT, "scoped_report")
result = report.build(out_dir, dest, header={"case_number": "SCOPE1"},
                      scope={"categories": [1]},
                      formats=["html", "md5", "kml", "projectvic"],
                      thumbnails=False)
check("the report covers the two files in category 1", result["files"] == 2,
      result["files"])
with open(os.path.join(dest, "SCOPE1_md5.csv"), encoding="utf-8") as fh:
    md5_rows = fh.read().strip().splitlines()[1:]
check("the MD5 list holds the report's two files, not the run's four",
      len(md5_rows) == 2, len(md5_rows))
wanted = {records[0]["md5"].lower(), records[1]["md5"].lower()}
check("and they are the two in scope",
      {row.split(",")[0] for row in md5_rows} == wanted)
with open(os.path.join(dest, "SCOPE1.kml"), encoding="utf-8") as fh:
    kml = fh.read()
check("the KML places the report's two files only",
      kml.count("<Placemark") == 2, kml.count("<Placemark"))
with open(os.path.join(dest, "SCOPE1_ProjectVIC.json"), encoding="utf-8") as fh:
    vic = json.load(fh)
check("the Project VIC file carries the report's two files only",
      vic["value"][0]["TotalMediaFiles"] == 2, vic["value"][0]["TotalMediaFiles"])
with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
    after = json.load(fh)
check("the export did not write its annotations into the case's manifest",
      not any("vic_category" in r for r in after["files"]))

# ---------------------------------------------------------- per-build state
zones = {}
errors = []


def build_in(zone, label):
    try:
        report._SHOWN_IN["zone"] = zone
        seen = []
        for _ in range(200):
            seen.append(report._SHOWN_IN["zone"])
            threading.Event().wait(0.0005)
        zones[label] = set(seen)
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)


threads = [threading.Thread(target=build_in, args=("America/New_York", "a")),
           threading.Thread(target=build_in, args=("Asia/Tokyo", "b"))]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("two builds at once each see only their own zone",
      zones.get("a") == {"America/New_York"} and zones.get("b") == {"Asia/Tokyo"},
      zones)

# Hash-set matches: each case's own, even when built at the same moment.
out_b, records_b = make_case("other", 1)
for folder, recs, set_name in ((out_dir, records, "SET-A"), (out_b, records_b, "SET-B")):
    case = casedb.CaseDB(os.path.join(folder, "case.db")) \
        if os.path.isfile(os.path.join(folder, "case.db")) \
        else casedb.open_case(folder, examiner="x", case_number="x")
    case.save_derived("hash_matches", {r["sha256"]: [{"set": set_name}] for r in recs})
    case.close()
gathered = {}


def gather(folder, label):
    gathered[label] = report.gather(folder)


threads = [threading.Thread(target=gather, args=(out_dir, "a")),
           threading.Thread(target=gather, args=(out_b, "b"))]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("each case's rows carry its own hash-set matches",
      {r["hash_sets"] for r in gathered["a"]["rows"]} == {"SET-A"}
      and {r["hash_sets"] for r in gathered["b"]["rows"]} == {"SET-B"},
      ({r["hash_sets"] for r in gathered["a"]["rows"]},
       {r["hash_sets"] for r in gathered["b"]["rows"]}))
check("report.py keeps no module-level hash-match cache",
      not hasattr(report, "_HASH_MATCHES"))

# ------------------------------------------------------------------- logo
logo_path = os.path.join(ROOT, "agency logo.png")
Image.new("RGB", (300, 100), (20, 60, 140)).save(logo_path)
dest = os.path.join(ROOT, "logo_report")
formats = ["html", "docx"]
try:
    import reportlab  # noqa: F401
    formats.append("pdf")
except ImportError:
    print("note: ReportLab is not installed; the PDF logo is not checked here")
report.build(out_dir, dest, header={"case_number": "LOGO1", "logo": logo_path},
             formats=formats, thumbnails=False, timezone="America/New_York")
with open(os.path.join(dest, "LOGO1.html"), encoding="utf-8") as fh:
    page = fh.read()
match = re.search(r'<img class="logo" src="data:image/png;base64,([^"]+)"', page)
check("the HTML report carries the logo set as a file path", bool(match))
if match:
    import io
    with Image.open(io.BytesIO(base64.b64decode(match.group(1)))) as shown:
        check("and it is the logo, sized for the header",
              shown.getpixel((5, 5))[:3] == (20, 60, 140) and shown.width <= 440,
              (shown.size, shown.getpixel((5, 5))))
with zipfile.ZipFile(os.path.join(dest, "LOGO1.docx")) as archive:
    names = archive.namelist()
    document = archive.read("word/document.xml").decode("utf-8")
check("the Word report carries the logo",
      "word/media/logo.jpeg" in names and 'r:embed="rIdLogo"' in document)
if "pdf" in formats:
    with open(os.path.join(dest, "LOGO1.pdf"), "rb") as fh:
        pdf = fh.read()
    # The same report without a logo, for what else draws an image (the
    # contents chart): the logo must add one (two objects, with its mask).
    bare = os.path.join(ROOT, "no_logo_report")
    report.build(out_dir, bare, header={"case_number": "LOGO1"},
                 formats=["pdf"], thumbnails=False)
    with open(os.path.join(bare, "LOGO1.pdf"), "rb") as fh:
        without = fh.read().count(b"/Subtype /Image")
    check("the PDF report carries the logo as an image",
          pdf.count(b"/Subtype /Image") > without,
          (pdf.count(b"/Subtype /Image"), without))

# -------------------------------------------------------------- generated
generated = re.search(r"<td>Generated</td><td>([^<]+)</td>", page)
check("Generated is written with the report's zone",
      bool(generated) and re.search(r"[-+]0[45]:00 \(E[SD]T\)", generated.group(1)),
      generated.group(1) if generated else "absent")

# ------------------------------------------------------- pages and folders
big_dir, big_records = make_case("paged", report.EMBED_LIMIT + 1)
dest = os.path.join(ROOT, "paged_report")
os.makedirs(dest)
first = report.write_html(report.gather(big_dir), os.path.join(dest, "R.html"),
                          header={}, scope={}, page_size=100)
check("the first build wrote several pages and a thumbnail folder",
      len(first["pages"]) == 5 and os.path.isdir(os.path.join(dest, "R_thumbs")),
      len(first["pages"]))
second = report.write_html(report.gather(big_dir), os.path.join(dest, "R.html"),
                           header={}, scope={}, page_size=250)
left = sorted(n for n in os.listdir(dest) if n.endswith(".html"))
check("a rebuild with fewer pages leaves only its own pages",
      left == ["R_page001.html", "R_page002.html"], left)
small = report.write_html(report.gather(out_dir), os.path.join(dest, "R.html"),
                          header={}, scope={})
left = sorted(n for n in os.listdir(dest) if n.endswith(".html"))
check("a rebuild down to one page removes every numbered page",
      left == ["R.html"], left)
check("and the thumbnail folder it made, now unused, is gone",
      not os.path.exists(os.path.join(dest, "R_thumbs")))

theirs = os.path.join(dest, "Mine_media")
os.makedirs(theirs)
with open(os.path.join(theirs, "keep.txt"), "w") as fh:
    fh.write("the examiner's own file")
refused = None
try:
    report.write_html(report.gather(out_dir), os.path.join(dest, "Mine.html"),
                      header={}, scope={}, full_copies=True)
except ValueError as exc:
    refused = str(exc)
check("a folder the report did not make is refused, not deleted",
      refused is not None and os.path.isfile(os.path.join(theirs, "keep.txt")),
      refused)
with open(os.path.join(dest, "Other_page007.html"), "w") as fh:
    fh.write("<html>not a report page</html>")
report.write_html(report.gather(out_dir), os.path.join(dest, "Other.html"),
                  header={}, scope={})
check("a page-like file this program did not write is left alone",
      os.path.isfile(os.path.join(dest, "Other_page007.html")))

shutil.rmtree(ROOT, ignore_errors=True)
print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
