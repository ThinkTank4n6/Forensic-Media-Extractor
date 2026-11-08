"""A case's file list reaches the page gzipped, and unchanged by it.

Opening a case hands the page every file: 46 MB of JSON for 55,508 files.
Gzipped it is about a thirteenth of that, and every browser decompresses it
without being asked. A small answer, an error, or a client that does not
offer gzip gets the plain body.
"""
import gzip
import json
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.web import compression, media_gui  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


rows = [{"name": f"recovered_{i:06d}.jpg", "rel_path": f"images/jpg/recovered_{i:06d}.jpg",
         "sha256": f"{i:064x}", "category": "image", "engine": "filesystem",
         "source_path": f"/DCIM/100APPLE/IMG_{i:04d}.JPG", "size": 1000 + i}
        for i in range(5000)]
job_id = "compression-test-" + str(os.getpid())
with media_gui.JOBS_LOCK:
    media_gui.JOBS[job_id] = {"status": "done", "files": rows, "out_dir": ""}
client = media_gui.app.test_client()

print("a finished case's file list")
zipped = client.get(f"/api/status?job_id={job_id}", headers={"Accept-Encoding": "gzip, deflate"})
plain = client.get(f"/api/status?job_id={job_id}")
check("is gzipped for a browser that accepts it",
      zipped.headers.get("Content-Encoding") == "gzip")
body = gzip.decompress(zipped.get_data())
check("and is the same answer once decompressed", json.loads(body) == plain.get_json())
check("and much smaller", len(zipped.get_data()) * 5 < len(body),
      f"{len(body):,} bytes as {len(zipped.get_data()):,}")
check("the length header is the compressed length",
      int(zipped.headers["Content-Length"]) == len(zipped.get_data()))
check("caches are told it varies by encoding",
      "accept-encoding" in zipped.headers.get("Vary", "").lower())
check("a client that does not offer gzip gets it plain",
      "Content-Encoding" not in plain.headers and plain.get_json()["ok"])

print("what is left alone")
small = client.get("/api/basemap", headers={"Accept-Encoding": "gzip"})
check("a small answer", "Content-Encoding" not in small.headers)
missing = client.get("/api/status?job_id=nope", headers={"Accept-Encoding": "gzip"})
check("an error", "Content-Encoding" not in missing.headers)
check("the threshold is above a typical answer", compression.THRESHOLD >= 16 * 1024)

with media_gui.JOBS_LOCK:
    media_gui.JOBS.pop(job_id, None)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
