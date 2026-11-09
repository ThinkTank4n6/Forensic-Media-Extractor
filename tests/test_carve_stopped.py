"""A carve that stops early says so, in the summary and in the report.

Both carves stop when they have carved more bytes than the image holds,
which only false matches produce. They said so in the log alone: the run
finished, the carve was recorded as done, and the report read as a whole
run over the whole image. The same was true of a deleted-file pass that
stopped with an error.
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
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core
import _engine  # noqa: E402 -- replaces a name wherever the engine reads it
from fme.exports import report

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_carve_stopped-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
image = os.path.join(ROOT, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(image)

real = media_core.run_carve_engine


def storm(reader, out, args, stats, *rest, **kw):
    # As though false matches had already filled the case.
    stats["carved_bytes"] = reader.size + 1
    return real(reader, out, args, stats, *rest, **kw)


saved = _engine.replace("run_carve_engine", storm)
case = os.path.join(ROOT, "case")
with contextlib.redirect_stdout(io.StringIO()):
    summary = media_core.run_extraction(image, case, mode="carve")
_engine.restore(saved)

said = bool(summary.get("carve_incomplete"))
ok &= said
print(f"the summary says the carve stopped: {said} ({summary.get('carve_incomplete')})")
failed = "carve" in (summary.get("failed_phases") or [])
ok &= failed
print(f"and the carve is not recorded as done: {failed}")
note = media_core.read_progress(case)
redo = "carve" not in (note.get("phases_done") or [])
ok &= redo
print(f"so a resume would carve again: {redo}")

with contextlib.redirect_stdout(io.StringIO()):
    report.build(case, case + "_report", formats=["html"])
text = "".join(open(p, encoding="utf-8").read()
               for p in glob.glob(os.path.join(case + "_report", "**", "*.html"), recursive=True))
told = "What this report does not cover" in text and "did not cover the whole image" in text
ok &= told
print(f"the report says so before anything else: {told}")

limits = report.coverage_limits({"summary": {"failed_phases": ["mft"]}})
right = len(limits) == 1 and "deleted-NTFS-record pass stopped" in limits[0]
ok &= right
print(f"a deleted-file pass that stopped is named in the report too: {right}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
