"""The report: scope, header, and the choice not to include pictures.

The checks that matter here are about what a report says about itself. A
report covering one category must say so, and a report with no images must
say that too -- a reader cannot otherwise tell a deliberate omission from a
failure.
"""
import tempfile
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fme.exports import report
from fme.review import casedb

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "report_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

ok = True

# A run with three images, two of them graded.
source = os.path.join(ROOT, "source")
os.makedirs(source)
try:
    from PIL import Image
    import random
    for name, seed in (("a.jpg", 1), ("b.jpg", 2), ("c.jpg", 3)):
        image = Image.new("RGB", (240, 180))
        pixels = image.load()
        random.seed(seed)
        for y in range(0, 180, 4):
            for x in range(0, 240, 4):
                color = tuple(random.randint(0, 255) for _ in range(3))
                for dy in range(4):
                    for dx in range(4):
                        if x + dx < 240 and y + dy < 180:
                            pixels[x + dx, y + dy] = color
        image.save(os.path.join(source, name), quality=90)
except ImportError:
    import make_media_test_image as g
    for index, name in enumerate(("a.jpg", "b.jpg", "c.jpg")):
        with open(os.path.join(source, name), "wb") as fh:
            fh.write(g.make_jpeg(10 + index * 5))

out_dir = os.path.join(ROOT, "run")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", source,
                "-o", out_dir], capture_output=True,
               cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
records = manifest["files"]

case = casedb.open_case(out_dir, examiner="Det. C. Tankelewicz",
                        case_number="DC26-253")
case.set_category(records[0]["sha256"], 1)
case.set_category(records[1]["sha256"], 5)
flag = case.flags()[0]
case.set_file_flag(records[0]["sha256"], flag["id"], True)
case.set_note(records[0]["sha256"], "matches DC24-118")
case.close()

HEADER = {"agency": "Delaware County DA — CID", "case_number": "DC26-253",
          "evidence": "1TB WD", "examiner": "Det. C. Tankelewicz",
          "notes": "Prepared for review."}

print("a full report")
result = report.build(out_dir, os.path.join(ROOT, "full"), header=HEADER,
                      formats=["html", "csv", "json", "md5"])
written = [os.path.basename(w["path"]) for w in result["written"] if w.get("path")]
print(f"  covered {result['files']} of {result['of']}: {written}")
ok &= result["files"] == len(records) and len(written) == 4

with open(os.path.join(ROOT, "full", "DC26-253.html"), encoding="utf-8") as fh:
    full_html = fh.read()
for needle in ("Delaware County DA", "Det. C. Tankelewicz", "1TB WD",
               "matches DC24-118", "By category", "data:image/jpeg;base64"):
    present = needle in full_html
    ok &= present
    print(f"  contains {needle[:28]:30} {present}")

print("\nscoped to one category")
scoped = report.build(out_dir, os.path.join(ROOT, "cam"), header=HEADER,
                      formats=["html"], scope={"categories": [1]})
ok &= scoped["files"] == 1
with open(os.path.join(ROOT, "cam", "DC26-253.html"), encoding="utf-8") as fh:
    scoped_html = fh.read()
# A reader must not take a partial report for the whole run.
says_so = "does not cover the rest" in scoped_html
ok &= says_so
print(f"  covered {scoped['files']} of {scoped['of']}, and says so: {says_so}")

print("\nwithout images")
plain = report.build(out_dir, os.path.join(ROOT, "plain"), header=HEADER,
                     formats=["html"], thumbnails=False)
with open(os.path.join(ROOT, "plain", "DC26-253.html"), encoding="utf-8") as fh:
    plain_html = fh.read()
no_image = "data:image" not in plain_html
declared = "Not included" in plain_html
ok &= no_image and declared
print(f"  holds no image of any kind: {no_image}")
print(f"  and the header says so    : {declared}")

print("\nblurred")
blurred = report.build(out_dir, os.path.join(ROOT, "blur"), header=HEADER,
                       formats=["html"], blur=True)
with open(os.path.join(ROOT, "blur", "DC26-253.html"), encoding="utf-8") as fh:
    blur_html = fh.read()
# Blurring is a reviewing aid, not a safeguard, and the report has to say so.
warned = "not safe to disclose" in blur_html
ok &= warned and "shot blur" in blur_html
print(f"  blurs the thumbnails: {'shot blur' in blur_html}")
print(f"  and warns that the image is still in the file: {warned}")

print("\nthe CSV matches the HTML's scope")
csv_rows = report.build(out_dir, os.path.join(ROOT, "both"), header=HEADER,
                        formats=["html", "csv"], scope={"categories": [1]})
with open(os.path.join(ROOT, "both", "DC26-253.csv"), encoding="utf-8") as fh:
    lines = fh.read().strip().splitlines()
ok &= len(lines) - 1 == csv_rows["files"] == 1
print(f"  {len(lines) - 1} data row(s) for {csv_rows['files']} file(s) in scope")

# The front of a report has to carry the shape of the case: what was found,
# in what proportions, when and where. Charts are drawn as SVG by hand so a
# report opens years from now on a machine with no network and no software
# but a browser.
print("\nthe front of the report")
for needle, what in (('class="highlights"', "highlights"),
                     ('class="donut"', "category donut"),
                     ('class="legend"', "counts and percentages"),
                     ('class="bars"', "bar charts"),
                     ("<script", "no script at all")):
    present = needle in full_html
    good = (not present) if needle == "<script" else present
    ok &= good
    print(f"  {what:26} {'yes' if present else 'no':4}"
          f"{'' if good else '   <-- wrong'}")

# A chart of one value must not clip its own label, and a report of one file
# must not crash the drawing.
tiny = report.build(out_dir, os.path.join(ROOT, "tiny"), header=HEADER,
                    formats=["html"], scope={"categories": [1]})
with open(os.path.join(ROOT, "tiny", "DC26-253.html"), encoding="utf-8") as fh:
    tiny_html = fh.read()
ok &= 'class="donut"' in tiny_html
print(f"  a one-file report still draws: {'class=\"donut\"' in tiny_html}")

# A report of a large case has to stay openable. Embedding every thumbnail
# in one document produced a file that took minutes to write and that no
# browser would open -- which is how "Writing..." came to sit there forever.
print("\nlarge cases")
import zipfile as _zipfile

many = dict(manifest)
many["files"] = [dict(records[i % len(records)],
                      out_path=records[i % len(records)]["out_path"],
                      sha256=f"{i:064x}")
                 for i in range(report.EMBED_LIMIT + 120)]
big_dir = os.path.join(ROOT, "big")
os.makedirs(big_dir, exist_ok=True)
shutil.copy(os.path.join(out_dir, "manifest.json"),
            os.path.join(ROOT, "manifest.backup"))
with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump(many, fh)

big = report.build(out_dir, big_dir, header=HEADER, formats=["html"])
written = big["written"][0]
pages = written.get("pages", [written["path"]])
ok &= len(pages) > 1
print(f"  {big['files']:,} files -> {len(pages)} page(s)")
ok &= bool(written.get("thumb_folder"))
print(f"  thumbnails written beside the report, not embedded in it: "
      f"{bool(written.get('thumb_folder'))}")
with open(pages[0], encoding="utf-8") as fh:
    first = fh.read()
# The giveaway: a page of base64 is enormous and slow to open.
ok &= "data:image/jpeg;base64" not in first
print(f"  no embedded images in the page: "
      f"{'data:image/jpeg;base64' not in first}")
print(f"  each page is {os.path.getsize(pages[0]) / 1024:,.0f} KB")

# A report can be stopped: a run that takes minutes needs a way out.
stop = {"now": False}
try:
    report.build(out_dir, os.path.join(ROOT, "stopped"), header=HEADER,
                 formats=["html"], cancel=lambda: True)
    stopped = False
except report.Canceled:
    stopped = True
ok &= stopped
print(f"  a report can be canceled: {stopped}")

with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    with open(os.path.join(ROOT, "manifest.backup"), encoding="utf-8") as src:
        fh.write(src.read())

print("\nWord and PDF")
offices = report.build(out_dir, os.path.join(ROOT, "office"), header=HEADER,
                       formats=["docx"])
docx_path = offices["written"][0]["path"]
ok &= _zipfile.is_zipfile(docx_path)
with _zipfile.ZipFile(docx_path) as archive:
    names = archive.namelist()
    document = archive.read("word/document.xml").decode()
# The parts Word insists on, and the content it should carry.
for part in ("[Content_Types].xml", "_rels/.rels", "word/document.xml",
             "word/_rels/document.xml.rels"):
    present = part in names
    ok &= present
    print(f"  {part:32} {'present' if present else 'MISSING'}")
ok &= "DC26-253" in document and "not the same as cleared" in document
print(f"  carries the case number and the caveat: "
      f"{'DC26-253' in document and 'not the same as cleared' in document}")
images = [n for n in names if n.startswith("word/media/")]
print(f"  embedded images: {len(images)}")

try:
    pdfs = report.build(out_dir, os.path.join(ROOT, "pdf"), header=HEADER,
                        formats=["pdf"])
    with open(pdfs["written"][0]["path"], "rb") as fh:
        head = fh.read(5)
    ok &= head == b"%PDF-"
    print(f"  PDF written, starts with {head!r}")
except RuntimeError as exc:
    # Allowed to be absent, but it has to say what to install.
    ok &= "reportlab" in str(exc).lower()
    print(f"  PDF needs ReportLab, and says so: {'reportlab' in str(exc).lower()}")

# A report sent on to somebody else has no business carrying the
# Non-Pertinent files, and an examiner should not have to delete them
# afterwards.
print("\nchoosing which categories go in")
chosen_dir = os.path.join(ROOT, "chosen")
shutil.rmtree(chosen_dir, ignore_errors=True)
only_cam = report.build(out_dir, chosen_dir, header=HEADER, formats=["html"],
                        scope={"categories": [1],
                               "include_uncategorized": False})
with open(only_cam["written"][0]["path"], encoding="utf-8") as fh:
    chosen_html = fh.read()

# One file was graded CAM in the fixture above.
ok &= only_cam["files"] == 1
print(f"  covers {only_cam['files']} file(s), which is the CAM file and "
      f"nothing else: {only_cam['files'] == 1}")

leaked = [name for name in ("Non-Pertinent", "Adult")
          if name in chosen_html]
ok &= not leaked
print(f"  no other category appears anywhere in it: {not leaked}")

print("\nthe conclusion tables")
everything = report.gather(out_dir)
summary = report.conclusion(everything, report.durations(out_dir,
                                                         everything["rows"]))
by_name = {entry["category"]: entry for entry in summary["rows"]}
print(f"  {'category':30} {'images':>7} {'unique':>7} {'video':>6} {'total':>6}")
for entry in summary["rows"]:
    print(f"  {entry['category'][:30]:30} {entry['images']:>7,} "
          f"{entry['unique_images']:>7,} {entry['videos']:>6,} "
          f"{entry['total']:>6,}")

# Instances and unique files answer different questions, and a report that
# gives only one invites the wrong reading of both. Every category must have
# at least as many files as it has different ones.
consistent = all(entry["images"] >= entry["unique_images"]
                 and entry["videos"] >= entry["unique_videos"]
                 for entry in summary["rows"])
ok &= consistent
print(f"  files counted are never fewer than unique files: {consistent}")

totals = summary["totals"]
ok &= totals["total"] == sum(e["total"] for e in summary["rows"])
print(f"  the totals row adds up: "
      f"{totals['total'] == sum(e['total'] for e in summary['rows'])}")

# A clip that cannot be decoded has no length, rather than a length of zero:
# counting unreadable files as zero-second videos makes the total quietly
# wrong.
ok &= report._hms(None) == "\u2014"
ok &= report._hms(95.4) == "00:01:35"
print(f"  an unmeasurable clip shows as {report._hms(None)!r}, "
      f"not 00:00:00")

# The tables read in the unit's own order -- the order of the category bar
# and of the keys an examiner presses -- not by how many files landed in
# each. A table that reorders itself from case to case makes two reports
# hard to compare, and a reader looks for a category where they expect it.
print("\nthe order the tables read in")
spread = [{"category_name": name, "category": code,
           "sha256": f"{index:064x}", "kind": "image", "ext": "jpg"}
          for index, (code, name) in enumerate([
              (5, "Adult Pornography"), (0, "Non-Pertinent"),
              (2, "Child Exploitive (non-CAM)"),
              (1, "Child Abuse Material (CAM)"), (8, "Uncategorized"),
              (3, "CGI Animation"), (7, "Notable")])]
ordered = report.conclusion({"rows": spread, "manifest": {}}, {})["rows"]
codes = [entry["code"] for entry in ordered]
for entry in ordered:
    print(f"   {entry['code']}  {entry['category']}")

findings = [code for code in codes if code not in report.TRAILING_CATEGORIES]
ok &= findings == sorted(findings)
print(f"  findings are in scheme order: {findings == sorted(findings)}")

# The two buckets that are not findings go last: Non-Pertinent and
# Uncategorized at the top of a report about what was found puts the answer
# below the things that are not the answer.
tail = codes[-2:]
ok &= set(tail) == report.TRAILING_CATEGORIES
print(f"  Non-Pertinent and Uncategorized come last: "
      f"{set(tail) == report.TRAILING_CATEGORIES}")

# ---------------------------------------------------------------------------
# Which build produced this
#
# A manifest that names the tool but not its version answers "what made
# this" and not "which one" -- and the second is the question somebody
# will have to answer about a case rather than about a program. A case
# processed under one build and reopened under another left nothing
# behind to say which did the work.
import glob as _glob
import re as _re
import zipfile as _zipfile

print()
print("which build produced this")

_stamp_ok = True
with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as _fh:
    _summary = json.load(_fh)["summary"]

for _field in ("tool", "tool_version", "engine_version", "code_fingerprint"):
    _there = bool(_summary.get(_field))
    _stamp_ok &= _there
    print(f"  the manifest records {_field:18} {'yes' if _there else 'NO'}")

_version = _summary.get("tool_version", "")

# Every form of the report, because an examiner may hand over any one of
# them and they must not disagree about what made the case.
for _form, _reader in (
        ("html", lambda p: open(p, encoding="utf-8").read()),
        ("protocol", lambda p: _zipfile.ZipFile(p).read(
            "word/document.xml").decode("utf-8", "replace"))):
    _dest = os.path.join(ROOT, f"stamp_{_form}")
    shutil.rmtree(_dest, ignore_errors=True)
    _written = report.build(out_dir, _dest, header=HEADER, formats=[_form],
                            timezone="UTC")
    _body = _reader(_written["written"][0]["path"])
    _named = _version in _body
    _stamp_ok &= _named
    print(f"  the {_form} report names {_version}: "
          f"{'yes' if _named else 'NO'}")

# And the Griffeye set, whose version field defaulted to a number this
# program has never been released under.
_dest = os.path.join(ROOT, "stamp_vic")
shutil.rmtree(_dest, ignore_errors=True)
report.build(out_dir, _dest, header=HEADER, formats=["projectvic"],
             timezone="UTC")
_vic = json.load(open(_glob.glob(os.path.join(_dest, "*.json"))[0],
                      encoding="utf-8"))["value"][0]
_said = _vic.get("SourceApplicationVersion", "")
_stamp_ok &= _version in _said
print(f"  the Griffeye set says {_said!r}: "
      f"{'yes' if _version in _said else 'NO'}")

ok &= _stamp_ok


print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
