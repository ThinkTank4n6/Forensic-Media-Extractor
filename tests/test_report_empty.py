"""A run that found nothing still gets a report, saying so and saying why.

report.build refused any report with no files in it ("Nothing is in scope
for this report") -- right for a selection that leaves everything out, wrong
for a run that recovered nothing. That left the runs where an explanation
matters most, a fully locked disk or one inside a volume manager, with no
report at all. A selection that leaves out every file of a run that did
find files is still refused.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import contextlib
import glob
import io
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core
from fme.exports import report

root = tempfile.mkdtemp(prefix="fme_report_empty-")
image = os.path.join(root, "locked.dd")
locked = bytearray(4 << 20)
locked[3:11] = b"-FVE-FS-"
locked[510:512] = b"\x55\xaa"
with open(image, "wb") as fh:
    fh.write(bytes(locked))
case = os.path.join(root, "case")
with contextlib.redirect_stdout(io.StringIO()):
    media_core.run_extraction(image, case, mode="both")
ok = True
try:
    with contextlib.redirect_stdout(io.StringIO()):
        report.build(case, os.path.join(root, "rep"),
                     formats=["html", "docx", "protocol", "pdf", "json", "csv"])
    built = True
except Exception as exc:  # noqa: BLE001
    built = False
    print(f"   ({type(exc).__name__}: {exc})")
ok &= built
print(f"a run over a locked volume that found nothing gets a report in every format: {built}")
html = "".join(open(p, encoding="utf-8").read()
               for p in glob.glob(os.path.join(root, "rep", "**", "*.html"), recursive=True))
right = "No files were recovered in this run" in html and "BitLocker" in html
ok &= right
print(f"saying nothing was recovered, and that the volume is BitLocker-locked: {right}")
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
