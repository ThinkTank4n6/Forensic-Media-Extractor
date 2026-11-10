"""While a run goes on, the gallery gets every file, and shows them.

Two faults made the gallery of a running job look empty and stuck:

- The live list was read from manifest.csv, where `container` is the text
  "False" -- which is true. Every file counted as a container, and the
  gallery hides containers unless Show containers is on: "No files match
  this filter" under every chip.
- /api/status sent a running job's newest 500 files and nothing more. Every
  page size past 500 showed the same 500, the pager said "of 500", and the
  All count never moved.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
from fme.web import media_gui  # noqa: E402
# This compares the page's own list with the server's, so the page must
# hold the list: whatever FME_PAGED_THRESHOLD says, this case is sent whole.
media_gui.PAGED_THRESHOLD = 10 ** 9

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_live_gallery-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


print("the live list, read from manifest.csv")
out = media_core.OutputManager(ROOT)
for i in range(1200):
    out.store(iter([os.urandom(16) + i.to_bytes(4, "big") * 1024]), category="image",
              ext="jpg", stem=f"recovered_{i:04d}_{i}", engine="carve", offset=i * 4096)
out._csv_fh.flush()
rows = media_gui._ManifestTail(os.path.join(ROOT, "manifest.csv")).read_new()
live = [media_gui._job_file_entry(r) for r in map(media_gui._live_row, rows) if r]
check("every row comes through", len(live) == 1200, str(len(live)))
check("no file is taken for a container", not any(f["container"] for f in live))
check("or for animated", not any(f["animated"] for f in live))
check("a carved file's source is its offset",
      live[1]["source"] == "offset 4096", repr(live[1]["source"]))

print("\n/api/status while the run goes on, as the page asks for it")
job = "f" * 32
media_gui.JOBS[job] = {"status": "running", "out_dir": ROOT, "files": live,
                       "_cancel": None}
client = media_gui.app.test_client()
real_batch = media_gui.LIVE_BATCH
media_gui.LIVE_BATCH = 500
try:
    held, polls = [], 0
    while polls < 20:
        polls += 1
        d = client.get(f"/api/status?job_id={job}&files_from={len(held)}").get_json()
        held = held[:d["files_from"]] + d["files"]
        if len(held) >= d["files_known"]:
            break
    check("the page catches up to every file, in batches",
          len(held) == 1200 and polls == 3, f"{len(held)} in {polls} polls")
    check("each file once, in order",
          [f["rel_path"] for f in held] == [f["rel_path"] for f in live])
    d = client.get(f"/api/status?job_id={job}&files_from=1200").get_json()
    check("once caught up, a poll sends nothing new", d["files"] == [])
    live.extend(live[:3])          # the run finds three more
    d = client.get(f"/api/status?job_id={job}&files_from=1200").get_json()
    check("and then only what was found since", len(d["files"]) == 3)
    d = client.get(f"/api/status?job_id={job}&files_from=99999").get_json()
    check("a page holding more than the job has is started again",
          d.get("files_reset") and d["files_from"] == 0 and len(d["files"]) == 500)
    d = client.get(f"/api/status?job_id={job}").get_json()
    check("a caller that does not ask still gets the window, marked as one",
          len(d["files"]) == 500 and d["files_windowed"])
finally:
    media_gui.LIVE_BATCH = real_batch
    with media_gui.JOBS_LOCK:
        media_gui.JOBS.pop(job, None)

print("\nthe page's side")
page = open(os.path.join(os.path.dirname(HERE), "fme", "web", "ui", "app.js"),
            encoding="utf-8").read()
check("the poll asks for the files after the ones it holds",
      "files_from=${from}" in page)
check("and no status answer replaces the list with a window",
      "if (d.files_windowed) return files;" in page
      and page.count("takeFiles(") >= 7)

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
