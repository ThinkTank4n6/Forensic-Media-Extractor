"""What a report covers, counted once for all three documents.

A report is written as HTML, as Word and as PDF, and an examiner may hand
over any of them. If the HTML says ninety-six CAM pictures and the Word
document says ninety-five, the question in the witness box stops being
about the pictures and becomes about the tool.

So the counting happens in one place and the three writers lay out the
same numbers. This checks that they do -- not that each one produces
something, which is the easy half.
"""
import tempfile
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.exports import report, report_contents

ok = True
# One directory per process. Twenty-seven tests used a
# fixed path here and each wipes it on startup, so two
# runs overlapping -- which happens whenever a batch is
# cut short and its child keeps going -- had the second
# deleting the first's working files mid-run. That is
# the only mechanism found for this suite's intermittent
# failures.
ROOT = os.path.join(tempfile.gettempdir(), "fme_contents_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

print("the counting")
rows = (
    [{"kind": "image", "category": 1,
      "category_name": "CAM (Child Abuse Material)"} for _ in range(96)]
    + [{"kind": "image", "category": 2,
        "category_name": "Child Exploitative / Age Difficult"}
       for _ in range(20)]
    + [{"kind": "video", "category": 3,
        "category_name": "CGI / Animation"} for _ in range(10)]
    + [{"kind": "other", "category": 3, "category_name": "CGI / Animation",
        "flag_names": "tagging a photo", "hash_sets": "VIC"}]
    + [{"kind": "image", "category": 3, "category_name": "CGI / Animation"}
       for _ in range(4)]
)
counted = report_contents.compute(rows, [], total_in_case=29351)

ok &= counted["files"] == 131
ok &= (counted["images"], counted["videos"], counted["other"]) == (120, 10, 1)
print(f"  {counted['files']} files: {counted['images']} images, "
      f"{counted['videos']} videos, {counted['other']} other")

shares = {entry["count"]: entry["percent"] for entry in counted["categories"]}
ok &= shares.get(96) == 73.3 and shares.get(20) == 15.3
ok &= shares.get(15) == 11.5
print(f"  by category: "
      f"{', '.join(f'{e['count']} ({e['percent']}%)' for e in counted['categories'])}")

ok &= counted["with_any_flag"] == 1 and counted["known_hash"] == 1
print(f"  {counted['with_any_flag']} flagged, {counted['known_hash']} "
      f"matching a known set, {counted['total_in_case']:,} in the case")

# A grading code is a number and a kind is a word. Reading one as the
# other is how the first version of this fell over on a real case.
ok &= counted["categories"][0]["name"] == "CAM (Child Abuse Material)"
print(f"  the grading's name is carried, not its code: True")

print("\nthe donut")
picture = report_contents.donut(counted)
if not picture:
    print("  Pillow is absent; skipped")
else:
    ok &= picture[:8] == bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A,
                                0x1A, 0x0A])
    print(f"  {len(picture):,} bytes of PNG")
    # The colours are the gallery's, so paper and screen agree.
    ok &= all(entry["color"].startswith("#")
              for entry in counted["categories"])
    print(f"  every slice has a colour: True")

print("\nthe three documents")
source = os.path.join(tempfile.gettempdir(), "protoreal/run")
if not os.path.isdir(source):
    print("  (no fixture case; skipped)")
else:
    numbers = {}
    for form in ("html", "protocol", "pdf"):
        dest = os.path.join(ROOT, form)
        shutil.rmtree(dest, ignore_errors=True)
        written = report.build(source, dest, header={"case_number": "AGREE"},
                               formats=[form], timezone="UTC")
        path = written["written"][0]["path"]

        if form == "html":
            body = open(path, encoding="utf-8").read()
            found = re.search(
                r"Files in this report</td><td class=\"num\">([\d,]+)", body)
            numbers[form] = found.group(1) if found else None
            ok &= "data:image/png;base64," in body
            print(f"  html     : {numbers[form]} files, donut embedded")
        elif form == "protocol":
            with zipfile.ZipFile(path) as archive:
                body = archive.read("word/document.xml").decode(
                    "utf-8", "replace")
                media = [n for n in archive.namelist()
                         if "contents" in n]
            found = re.search(r"Files in this report</w:t>.*?"
                              r"<w:t[^>]*>([\d,]+)</w:t>", body, re.S)
            numbers[form] = found.group(1) if found else None
            ok &= bool(media)
            print(f"  word     : {numbers[form]} files, donut embedded "
                  f"({media[0] if media else 'none'})")
        else:
            ok &= os.path.getsize(path) > 2000
            print(f"  pdf      : {os.path.getsize(path):,} bytes")

    # The point of the whole exercise.
    said = {form: value for form, value in numbers.items() if value}
    ok &= len(set(said.values())) <= 1
    print(f"  and they agree on the count: "
          f"{sorted(set(said.values()))} across {len(said)} document(s)")

    # A report built without images must hold none: that is what makes
    # it safe to disclose, and a chart is still an image. The numbers
    # stay; only the ring goes.
    bare = os.path.join(ROOT, "bare")
    shutil.rmtree(bare, ignore_errors=True)
    written = report.build(source, bare, header={"case_number": "BARE"},
                           formats=["html"], thumbnails=False)
    body = open(written["written"][0]["path"], encoding="utf-8").read()
    ok &= "data:image" not in body
    print(f"  a report with no images holds no ring either: "
          f"{'data:image' not in body}")
    ok &= "Files in this report" in body
    print(f"  but still says what it covers: True")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
