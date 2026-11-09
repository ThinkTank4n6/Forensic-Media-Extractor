"""A report says what the run could not read.

Since v239 a run records a locked partition in its summary
(encrypted_volumes), beside a volume it could not mount
(unreadable_volume). The report was built from the file list alone, so a
BitLocker laptop came out as "All 8 file(s) recovered in this run" -- eight
files from the small system partition -- with nothing to say the main
partition was never read. The report is what goes in the case file, to
people who never saw the console or the program's warnings.

This runs the program over a disk with a FAT32 partition and a BitLocker
partition, writes the report in every format, and checks each one says so
before anything else -- and that a report of an ordinary disk says nothing
of the kind.
"""
import contextlib
import glob
import io
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

import make_media_test_image as fixtures
from fme.exports import report, report_html

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_report_coverage-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

fat_path = os.path.join(ROOT, "fat.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(fat_path)
with open(fat_path, "rb") as fh:
    fat = fh.read()
SECTOR, MIB = 512, 2048
FAT_SECTORS = -(-len(fat) // SECTOR)
LOCKED = MIB + -(-FAT_SECTORS // MIB) * MIB + MIB


def disk(locked: bool) -> str:
    image = bytearray((LOCKED + 4 * MIB) * SECTOR)
    for slot, (lba, kind, count) in enumerate(((MIB, 0x0C, FAT_SECTORS),
                                               (LOCKED, 0x07, 4 * MIB - 1))):
        struct.pack_into("<B3sB3sII", image, 446 + 16 * slot, 0, b"", kind, b"",
                         lba, count)
    image[510:512] = b"\x55\xaa"
    image[MIB * SECTOR:MIB * SECTOR + len(fat)] = fat
    if locked:
        image[LOCKED * SECTOR + 3:LOCKED * SECTOR + 11] = b"-FVE-FS-"
    path = os.path.join(ROOT, ("locked" if locked else "plain") + ".dd")
    with open(path, "wb") as fh:
        fh.write(bytes(image))
    return path


def case_of(path: str) -> str:
    out = path + "_out"
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", path, "-o", out],
                   capture_output=True, cwd=PROJECT, timeout=900)
    return os.path.dirname(glob.glob(os.path.join(out, "**", "manifest.json"),
                                     recursive=True)[0])


def pdf_text(path: str) -> str:
    """The words on a PDF's pages. Its page streams are compressed."""
    try:
        import pypdf
        return "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(path).pages)
    except ImportError:
        pass
    try:
        return subprocess.run(["pdftotext", path, "-"], capture_output=True,
                              text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        print("   (no PDF text reader here: pypdf or pdftotext; the PDF check is skipped)")
        return "BitLocker p2"


def texts(case: str) -> dict:
    """Every format of the report, as text."""
    dest = case + "_report"
    with contextlib.redirect_stdout(io.StringIO()):
        report.build(case, dest, formats=["html", "docx", "protocol", "pdf",
                                          "json", "csv"])
    found = {}
    for path in glob.glob(os.path.join(dest, "**", "*"), recursive=True):
        name = os.path.basename(path)
        if name.endswith("_protocol.docx"):
            kind = "protocol"
        else:
            kind = os.path.splitext(name)[1].lstrip(".")
        if kind in ("docx", "protocol"):
            with zipfile.ZipFile(path) as archive:
                found[kind] = archive.read("word/document.xml").decode("utf-8", "replace")
        elif kind in ("html", "json", "csv"):
            with open(path, encoding="utf-8") as fh:
                found[kind] = found.get(kind, "") + fh.read()
        elif kind == "pdf":
            found[kind] = pdf_text(path)
    sheet = os.path.join(dest, "contact_sheet.html")
    report_html.export(os.path.join(case, "manifest.json"), sheet)
    with open(sheet, encoding="utf-8") as fh:
        found["contact sheet"] = fh.read()
    return found


print("a disk with a FAT32 partition and a locked BitLocker partition")
locked_case = case_of(disk(True))
with open(os.path.join(locked_case, "manifest.json"), encoding="utf-8") as fh:
    summary = json.load(fh)["summary"]
recorded = [v["kind"] for v in summary.get("encrypted_volumes", [])] == ["BitLocker"]
ok &= recorded
print(f"   the run records it: {recorded}")
written = texts(locked_case)
# The label the run gave the partition: "p2" when the table is read here,
# TSK's own "p<slot>(description)" when TSK listed it. The report must name
# whichever the run recorded.
where = (summary.get("encrypted_volumes") or [{}])[0].get("partition") or "p2"
where = where.split("(")[0]
for kind in ("html", "docx", "protocol", "pdf", "json", "contact sheet"):
    said = "BitLocker" in written.get(kind, "") and where in written.get(kind, "")
    ok &= said
    print(f"   {kind}: names the locked partition: {said}")
html_text = written.get("html", "")
first = html_text.find("What this report does not cover")
before_counts = 0 < first < html_text.find("Report contents") if "Report contents" in html_text else first > 0
ok &= before_counts
print(f"   html: says so before the counts: {before_counts}")
caveat = "Part of the image could not be read" in html_text
ok &= caveat
print(f"   html: and the 'Covers' line points to it: {caveat}")

print("\nthe same disk with nothing locked")
written = texts(case_of(disk(False)))
quiet = all("does not cover" not in written.get(k, "") and "BitLocker" not in written.get(k, "")
            for k in ("html", "docx", "protocol", "pdf", "contact sheet"))
ok &= quiet
print(f"   no format says anything about it: {quiet}")

print("\nthe other limits a summary can carry")
limits = report.coverage_limits({"summary": {"unreadable_volume": "refs"}})
right = len(limits) == 1 and "REFS volume" in limits[0]
ok &= right
print(f"   a volume that could not be read: {right}")
limits = report.coverage_limits({"summary": {"encrypted": "BitLocker"}})
right = len(limits) == 1 and limits[0].startswith("The image is BitLocker")
ok &= right
print(f"   a run from before v239, with only the kind: {right}")
ok &= report.coverage_limits({"summary": {}}) == []
print(f"   nothing recorded, nothing said: {report.coverage_limits({'summary': {}}) == []}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
