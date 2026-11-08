"""A case is tied to the evidence it was made from.

Until v2026.11.313 a case recorded the image's path, size and start time,
and nothing that would show the image had been swapped or changed between
acquisition and processing (an outside review). Now:

1. the hashes an acquisition recorded about itself are kept in the summary,
   always -- read from its headers, at no cost;
2. with hash_source, the run hashes the whole source (MD5, SHA-1, SHA-256)
   and says whether it matches -- checked against an E01 that ewfacquire
   wrote, so the stored hash is libewf's, not ours;
3. a source that does not match is said in every report, as a limit;
4. manifest.json gets its SHA-256 written beside it, and a report says
   whether the record has changed since.
"""
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
logging.disable(logging.CRITICAL)

from fme.engines import evidence_hash, media_core  # noqa: E402
from fme.exports import report  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_evidence_hash-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
RAW = os.path.join(HERE, "media_test.img")
EWF = os.path.join(HERE, "fixtures", "libewf-acquired.E01")


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def run(source, out, **options):
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.run_extraction(source, out, workers=1, metadata=False,
                                         kml=False, mode="fs", **options)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


print("a raw image, hashed by the run")
case = os.path.join(ROOT, "raw")
summary = run(RAW, case, hash_source=True)
evidence = summary.get("evidence") or {}
check("the summary carries the source's SHA-256",
      (evidence.get("computed") or {}).get("sha256") == file_sha256(RAW))
check("a raw image recorded no hash of its own, so nothing is claimed verified",
      evidence.get("stored") == {} and evidence.get("verified") is None,
      evidence.get("verified"))
with open(os.path.join(case, "manifest.json"), encoding="utf-8") as fh:
    written = json.load(fh)["summary"].get("evidence") or {}
check("and manifest.json says the same", written.get("computed") == evidence.get("computed"))

print("\nmanifest.json's own digest")
sidecar = os.path.join(case, evidence_hash.MANIFEST_DIGEST)
with open(sidecar, encoding="utf-8") as fh:
    line = fh.read()
check("is written beside it, as sha256sum -c reads it",
      line == f"{file_sha256(os.path.join(case, 'manifest.json'))}  manifest.json\n",
      line.strip()[:20])
check("and is unchanged after the run", evidence_hash.check_manifest_digest(case) is True)
notes = report.evidence_notes({"summary": written}, case)
check("the report says so", any("unchanged since the run" in n for n in notes))
with open(os.path.join(case, "manifest.json"), "a", encoding="utf-8") as fh:
    fh.write(" ")
check("a change to the record is found", evidence_hash.check_manifest_digest(case) is False)
notes = report.evidence_notes({"summary": written}, case)
check("and the report says it has changed", any("HAS CHANGED" in n for n in notes))

print("\nan E01 acquired by ewfacquire, checked against its own hash")
case = os.path.join(ROOT, "ewf")
summary = run(EWF, case, hash_source=True)
evidence = summary.get("evidence") or {}
check("its stored MD5 is recorded", len((evidence.get("stored") or {}).get("md5", "")) == 32,
      evidence.get("stored"))
check("the run's MD5 matches it, so the source is verified",
      evidence.get("verified") is True, evidence.get("note"))
check("nothing about it is a limit of the report",
      not any("does not match" in x for x in report.coverage_limits(
          {"summary": summary})))

print("\nwithout hash_source")
case = os.path.join(ROOT, "ewf-quick")
summary = run(EWF, case)
evidence = summary.get("evidence") or {}
check("the acquisition's own hash is still recorded",
      bool((evidence.get("stored") or {}).get("md5")))
check("nothing is claimed verified", evidence.get("verified") is None
      and "computed" not in evidence)

print("\na source that does not match")


class Altered:
    """The E01's reader, with one byte of the media different."""

    def __init__(self, reader):
        self._inner, self.size = reader, reader.size
        self._image = reader._image

    def read(self, offset, length):
        data = bytearray(self._inner.read(offset, length))
        if offset == 0 and data:
            data[0] ^= 0xFF
        return bytes(data)


reader = media_core.open_image(EWF)
try:
    record = evidence_hash.evidence_record(Altered(reader), EWF, compute=True)
finally:
    reader.close()
check("is reported not verified", record.get("verified") is False, record.get("note"))
limits = report.coverage_limits({"summary": {"evidence": record}})
check("and every report states it as a limit",
      any("does not match the hash its acquisition recorded" in x for x in limits))

print("\na canceled hash")
reader = media_core.open_image(RAW)
try:
    stopped = evidence_hash.evidence_record(reader, RAW, compute=True,
                                            cancel=lambda: True)
finally:
    reader.close()
check("records no digest of part of the source as if it were the whole",
      "computed" not in stopped and stopped.get("verified") is None)

print("\nreachable")
parser_help = io.StringIO()
with contextlib.redirect_stdout(parser_help), contextlib.suppress(SystemExit):
    media_core.main(["--help"])
check("the command line has --hash-source", "--hash-source" in parser_help.getvalue())
with open(os.path.join(PROJECT, "fme", "web", "ui", "app.js"), encoding="utf-8") as fh:
    script = fh.read()
with open(os.path.join(PROJECT, "fme", "web", "ui", "page.html"), encoding="utf-8") as fh:
    page = fh.read()
with open(os.path.join(PROJECT, "fme", "web", "media_gui.py"), encoding="utf-8") as fh:
    gui = fh.read()
check("the interface has the box and sends it, and the server reads it",
      'id="hash-source"' in page and 'hash_source: $("hash-source").checked' in script
      and '"hash_source"' in gui)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
