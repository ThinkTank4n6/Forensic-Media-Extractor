"""Interface and export defects found by the review of v2026.11.302.

- reprocess rewrote manifest.json with an empty summary;
- "rebuild" and "open" acted on a folder an extraction was still writing;
- a report that failed with an unexpected error stayed "running" for ever;
- two tools read any file on the machine through a bare path join;
- cancel answered "ok" for runs that had finished or never read the flag;
- `fme fat32gui` called a main() that did not exist;
- a time near year 1 or 9999 stopped any report that showed it;
- a PDF stream with Flate before DCT came out as zlib bytes named .jpg;
- two files sharing an MD5 were staged for Project VIC as one file;
- a FAT access date came back as midnight.
"""
import _sandbox  # noqa: F401
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fme.engines import containers, fat32_core, media_core, timeutil  # noqa: E402
from fme.exports import projectvic_export  # noqa: E402
from fme.web import media_gui as g  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "review302_web-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
H = {"X-FME-Request": "1"}
c = g.app.test_client()
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}: {'ok' if good else 'WRONG'} {detail}".rstrip())


# Reprocess keeps the case's summary.
case = os.path.join(ROOT, "case1")
os.makedirs(case)
media_core.OutputManager(case).close({"image": "/evidence/x.E01",
                                      "image_md5": "abc", "tool_version": "t"})
jid = c.post("/api/cases/open", json={"out_dir": case}, headers=H).get_json()["job_id"]
r = c.post("/api/case/reprocess", json={"job": jid, "reason": "expand-containers"},
           headers=H)
with open(os.path.join(case, "manifest.json"), encoding="utf-8") as fh:
    summary = json.load(fh)["summary"]
check("reprocess keeps the source image and its hash",
      r.status_code == 200 and summary.get("image") == "/evidence/x.E01"
      and summary.get("image_md5") == "abc", str(summary)[:120])

import sqlite3  # noqa: E402
probe = sqlite3.connect(os.path.join(case, "case.db"), timeout=0.5)
try:
    probe.execute("BEGIN IMMEDIATE")
    probe.rollback()
    free = True
except sqlite3.OperationalError:
    free = False
probe.close()
check("after a reprocess, case.db is not left locked", free)

# Rebuild and open refuse a folder a run is writing.
running = os.path.join(ROOT, "running")
om = media_core.OutputManager(running)
om._csv_fh.flush()
with g.JOBS_LOCK:
    g.JOBS["live"] = {"status": "running", "out_dir": running, "files": [],
                      "_cancel": threading.Event()}
r = c.post("/api/case/rebuild", json={"out_dir": running}, headers=H)
check("rebuild refuses a folder a run is writing", r.status_code == 409
      and not os.path.exists(os.path.join(running, "manifest.json")),
      str(r.status_code))
with g.JOBS_LOCK:
    g.JOBS["live"]["status"] = "done"
om._csv_fh.close()
om._dup_csv_fh.close()

# A report that fails says so.
rid = c.post("/api/report/build", json={"job": jid, "fields": 5},
             headers=H).get_json()["id"]
for _ in range(150):
    status = c.get(f"/api/report/status?id={rid}").get_json()
    if status.get("status") != "running":
        break
    time.sleep(0.1)
check("a report that fails is reported as failed", status.get("status") == "error",
      status.get("status"))

# Paths stay inside the case.
r = c.post("/api/credentials/verify", json={"job": jid, "file": "../../../../etc/passwd"},
           headers=H)
check("credentials check refuses a path outside the case", r.status_code == 400,
      str(r.status_code))

# Cancel tells the truth.
r = c.post("/api/cancel", json={"job_id": jid}, headers=H)
check("cancel of a finished run is not 'ok'", r.status_code == 409, str(r.status_code))

# `fme fat32gui` and the interface it started were retired in v2026.11.319
# (tests/test_fat32_retired.py).

# Times at the ends of the calendar.
try:
    shown = (timeutil.show("0001-01-01T00:00:00+00:00", "America/New_York"),
             timeutil.day_in("9999-12-31T23:30:00+00:00", "Asia/Tokyo"))
    check("a time at the calendar's edge is shown, not raised",
          shown[0].startswith("0001-01-01 00:00:00 +00:00"), str(shown))
except OverflowError as exc:
    check("a time at the calendar's edge is shown, not raised", False, repr(exc))

# PDF: the image file under a transport filter.
jpeg = b"\xff\xd8\xff\xe0" + b"J" * 60 + b"\xff\xd9"
packed = zlib.compress(jpeg)
pdf = (b"%PDF-1.4\n1 0 obj\n<< /Type /XObject /Subtype /Image /Filter "
       b"[/FlateDecode /DCTDecode] /Length " + str(len(packed)).encode() +
       b" >>\nstream\n" + packed + b"\nendstream\nendobj\n")
images = [p for n, p, m in containers.pdf_images(pdf) if n]
check("a Flate-wrapped JPEG in a PDF comes out as the JPEG", images == [jpeg])
attach = b"hello\n\n"
pdf = (b"%PDF-1.4\n2 0 obj\n<< /Filter /FlateDecode /Type /EmbeddedFile >>\nstream\n"
       + zlib.compress(attach) + b"\nendstream\nendobj\n")
files = [p for n, p, m in containers.pdf_embedded_files(pdf)]
check("a PDF attachment with its filter first is decoded, newlines kept",
      files == [attach], repr(files))

# Project VIC staging keeps two files that share an MD5 apart.
vic = os.path.join(ROOT, "vic")
os.makedirs(os.path.join(vic, "images"))
for name, body in (("a", b"first"), ("b", b"second")):
    with open(os.path.join(vic, "images", name), "wb") as fh:
        fh.write(body)
import hashlib  # noqa: E402
manifest = {"files": [{"out_path": f"images/{n}", "md5": "a" * 32,
                       "sha256": hashlib.sha256(b).hexdigest()}
                      for n, b in (("a", b"first"), ("b", b"second"))]}
staged = projectvic_export.stage_hash_layout(
    manifest, os.path.join(vic, "manifest.json"), os.path.join(vic, "out.json"))
check("two files with one MD5 are staged as two files",
      len(set(staged.values())) == 2, str(staged))

# FAT: the access date is a date.
entry = bytearray(32)
entry[0:11] = b"A       JPG"
entry[11] = 0x20
entry[18:20] = (0x5821).to_bytes(2, "little")
got = fat32_core.parse_dir_entries(bytes(entry), "/")
check("a FAT access date has no time of day", got and got[0].atime == "2024-01-01",
      got[0].atime if got else "")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASSED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
