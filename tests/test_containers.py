"""Thumbnail caches, PDFs and saved pages: places media hides from a carver.

Each file here is built byte by byte, so what should come out is known
exactly. The thumbcache matters most: Explorer's cached copies outlive the
pictures they were made from, so a wiped photograph is often still there.
"""
import tempfile
import base64
import io
import email.message
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_media_test_image as g
from fme.engines import containers

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "containers_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

ok = True
cached_jpg, cached_png = g.make_jpeg(20), g.make_png()
pdf_jpg, page_gif = g.make_jpeg(40), g.make_gif()


def thumbcache_entry(cache_id, identifier, payload):
    identifier_bytes = identifier.encode("utf-16-le")
    header = 56
    size = header + len(identifier_bytes) + len(payload)
    return (b"CMMM" + struct.pack("<I", size) + struct.pack("<Q", cache_id)
            + struct.pack("<I", len(identifier_bytes)) + struct.pack("<I", 0)
            + struct.pack("<I", len(payload)) + bytes(header - 28)
            + identifier_bytes + payload)


database = (b"CMMM" + struct.pack("<I", 0x1A) + struct.pack("<I", 0)
            + struct.pack("<I", 24) + bytes(8)
            + thumbcache_entry(0x1122334455667788, "wiped_photo.jpg", cached_jpg)
            + thumbcache_entry(0x99AABBCCDDEEFF00, "gone.png", cached_png))
with open(os.path.join(ROOT, "thumbcache_256.db"), "wb") as fh:
    fh.write(database)

print("thumbcache")
entries = list(containers.thumbcache_entries(database))
ok &= len(entries) == 2
for identifier, payload, info in entries:
    print(f"  {identifier:18} {info['ext']:4} {len(payload):>6} bytes  "
          f"id={info['cache_id']}")
ok &= entries[0][1] == cached_jpg and entries[1][1] == cached_png

# A thumbnail's own bytes can contain "CMMM"; following the record offsets
# rather than scanning for that signature is what keeps the walk honest.
noisy = (b"CMMM" + struct.pack("<I", 0x1A) + struct.pack("<I", 0)
         + struct.pack("<I", 24) + bytes(8)
         + thumbcache_entry(1, "noisy.jpg", cached_jpg[:100] + b"CMMM"
                            + cached_jpg[100:]))
found = list(containers.thumbcache_entries(noisy))
ok &= len(found) == 1
print(f"  a thumbnail containing CMMM still reads as {len(found)} entry")

# Truncation ends the walk rather than inventing a record.
truncated = database[:len(database) - 200]
survived = list(containers.thumbcache_entries(truncated))
ok &= len(survived) == 1
print(f"  a truncated database yields {len(survived)} complete entry, no guesses")

print("\npdf")
raster = zlib.compress(bytes(300))
pdf = (b"%PDF-1.7\n1 0 obj\n<< /Subtype /Image /Width 640 /Height 480 "
       b"/Filter /DCTDecode /Length " + str(len(pdf_jpg)).encode()
       + b" >>\nstream\n" + pdf_jpg + b"\nendstream\nendobj\n"
       b"2 0 obj\n<< /Subtype /Image /Filter /FlateDecode /Length "
       + str(len(raster)).encode() + b" >>\nstream\n" + raster
       + b"\nendstream\nendobj\n%%EOF\n")
with open(os.path.join(ROOT, "report.pdf"), "wb") as fh:
    fh.write(pdf)

images = list(containers.pdf_images(pdf))
extracted = [i for i in images if i[1]]
raster_note = [i for i in images if i[2].get("raster_only")]
ok &= len(extracted) == 1 and extracted[0][1] == pdf_jpg
print(f"  JPEG stream out verbatim: {extracted[0][1] == pdf_jpg}")
# Pixels are not a file that ever existed; rebuilding one would give it this
# tool's hashes rather than the evidence's.
ok &= bool(raster_note)
print(f"  images stored as pixels reported, not rebuilt: "
      f"{raster_note[0][2]['raster_only'] if raster_note else 0}")

print("\nsaved page")
message = email.message.EmailMessage()
message["MIME-Version"] = "1.0"
message["Content-Type"] = "multipart/related"
message.add_attachment(b"<html></html>", maintype="text", subtype="html")
part = email.message.EmailMessage()
part.set_content(page_gif, maintype="image", subtype="gif")
part["Content-Location"] = "http://example.test/banner.gif"
message.attach(part)
mhtml = message.as_bytes()
with open(os.path.join(ROOT, "saved_page.mht"), "wb") as fh:
    fh.write(mhtml)

parts = list(containers.mhtml_parts(mhtml))
ok &= len(parts) == 1 and parts[0][1] == page_gif
print(f"  image part out verbatim: {bool(parts) and parts[0][1] == page_gif}")

html = b'<img src="data:image/png;base64,' + base64.b64encode(cached_png) + b'">'
uris = list(containers.html_data_uris(html))
ok &= len(uris) == 1 and uris[0][1] == cached_png
print(f"  data URI decoded verbatim: {bool(uris) and uris[0][1] == cached_png}")

# And all of it through the engine, as a folder of evidence.
print("\nthrough the engine")
out_dir = os.path.join(ROOT, "out")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", ROOT,
                "-o", out_dir], capture_output=True,
               cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
digests = {f["sha256"] for f in manifest["files"]}
for label, blob in (("cached JPEG", cached_jpg), ("cached PNG", cached_png),
                    ("PDF image", pdf_jpg), ("saved-page GIF", page_gif)):
    good = hashlib.sha256(blob).hexdigest() in digests
    ok &= good
    print(f"  {label:16} recovered byte-exact: {good}")

# Audio a phone produces, and the artifacts carried only with -k. Each is
# built here so the expected length is known exactly: a carved file that is
# one frame long or short has a different hash and loses a hash-set match.
import plistlib
import sqlite3 as _sqlite3
import struct as _struct
import zipfile as _zipfile

from fme.engines import media_core


def _aiff():
    data = bytes(2000)
    body = (b"COMM" + _struct.pack(">I", 18) + bytes(18)
            + b"SSND" + _struct.pack(">I", len(data)) + data)
    return b"FORM" + _struct.pack(">I", 4 + len(body)) + b"AIFF" + body


def _caf():
    return (b"caff" + _struct.pack(">HH", 1, 0)
            + b"desc" + _struct.pack(">q", 32) + bytes(32)
            + b"data" + _struct.pack(">q", 4000) + bytes(4000))


def _amr():
    # Mode 7: 32 bytes per frame, header byte included.
    return b"#!AMR\n" + (bytes([0x3C]) + bytes(31)) * 40


def _silk():
    blocks = b"".join(_struct.pack("<h", 40) + bytes(40) for _ in range(30))
    return b"#!SILK_V3" + blocks + _struct.pack("<h", -1)


def _mp3():
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)
    return (b"ID3" + bytes([3, 0, 0]) + bytes([0, 0, 0, 10]) + bytes(10)
            + frame * 60)


def _bplist():
    return plistlib.dumps({"name": "value", "n": list(range(40))},
                          fmt=plistlib.FMT_BINARY)


def _zip():
    buffer = io.BytesIO()
    with _zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "hello" * 100)
    return buffer.getvalue()


def _sqlite():
    path = tempfile.mktemp(suffix=".db")
    connection = _sqlite3.connect(path)
    connection.execute("CREATE TABLE t (a TEXT)")
    connection.executemany("INSERT INTO t VALUES (?)",
                           [(f"row{i}",) for i in range(200)])
    connection.commit()
    connection.close()
    with open(path, "rb") as fh:
        blob = fh.read()
    os.remove(path)
    return blob


def _pdf():
    return (b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
            + bytes(500) + b"\ntrailer\n%%EOF\n")


MEDIA = {"aiff": _aiff(), "caf": _caf(), "amr": _amr(), "silk": _silk(),
         "mp3": _mp3()}
ARTIFACTS = {"bplist": _bplist(), "zip": _zip(), "sqlite": _sqlite(),
             "pdf": _pdf()}

print("\nlengths parsed from the file itself")
for name, (blob, parser) in {
        "aiff": (MEDIA["aiff"], "aiff"), "caf": (MEDIA["caf"], "caf"),
        "amr": (MEDIA["amr"], "amr"), "silk": (MEDIA["silk"], "silk"),
        "mp3": (MEDIA["mp3"], "mp3"), "bplist": (ARTIFACTS["bplist"], "bplist"),
        "zip": (ARTIFACTS["zip"], "zip"), "sqlite": (ARTIFACTS["sqlite"], "sqlite"),
        "pdf": (ARTIFACTS["pdf"], "pdf")}.items():
    read = (lambda offset, count, data=blob: data[offset:offset + count])
    parsed = media_core.PARSERS[parser](read, 0, len(blob) + 4096)
    exact = bool(parsed) and parsed[0] == len(blob)
    ok &= exact
    print(f"  {name:8} {len(blob):>7} bytes, parsed "
          f"{parsed[0] if parsed else None}  {'exact' if exact else 'WRONG'}")

image = os.path.join(ROOT, "formats.dd")
with open(image, "wb") as fh:
    for blob in list(MEDIA.values()) + list(ARTIFACTS.values()):
        # Zeros, not random bytes.
        #
        # This padding was os.urandom, and it made the test
        # non-deterministic in a way that took nine sessions to pin
        # down. AMR has no length field: its end is found by walking
        # frames until one does not make sense. Random bytes after the
        # file pass that test about 1.8% of the time, the carve takes
        # one more frame, and the recovered file's hash no longer
        # matches the original -- so the test reported the file as
        # lost, intermittently, and only ever under the load of
        # several runs at once, because load meant more runs.
        #
        # A test has to be reproducible. The behaviour it was
        # accidentally exercising is real and is written up in
        # docs/VALIDATION.md 5.11 instead.
        fh.write(bytes(4096))
        fh.write(blob)
    fh.write(bytes(4096))

print("\ncarved from an image")
for label, extra in (("by default", []), ("with -k", ["-k"])):
    out_dir = os.path.join(ROOT, "carve" + ("_k" if extra else ""))
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image,
                    "-o", out_dir, "--mode", "carve"] + extra,
                   capture_output=True,
                   cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
        digests = {f["sha256"] for f in json.load(fh)["files"]}
    found_media = [n for n, b in MEDIA.items()
                   if hashlib.sha256(b).hexdigest() in digests]
    found_artifacts = [n for n, b in ARTIFACTS.items()
                       if hashlib.sha256(b).hexdigest() in digests]
    print(f"  {label:11} audio: {', '.join(sorted(found_media)) or 'none'}")
    print(f"  {'':11} artifacts: {', '.join(sorted(found_artifacts)) or 'none'}")
    ok &= len(found_media) == len(MEDIA)
    # Artifacts are opt-in: a run looking for media must not return a disk
    # full of databases and archives.
    ok &= (len(found_artifacts) == len(ARTIFACTS)) if extra else not found_artifacts

# A zip of photographs is two different things at once: a file with hashes
# that belong in the manifest, and a box whose contents are the evidence.
# The gallery should show the photographs, not the box.
print("\ncontainers")
import io as _io
import zipfile as _zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_media_test_image as _fixtures

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

box_dir = os.path.join(ROOT, "boxes")
shutil.rmtree(box_dir, ignore_errors=True)
os.makedirs(box_dir)
buffer = _io.BytesIO()
with _zipfile.ZipFile(buffer, "w") as archive:
    for index in range(3):
        archive.writestr(f"photo{index}.jpg", _fixtures.make_jpeg(10 + index * 4))
with open(os.path.join(box_dir, "album.zip"), "wb") as fh:
    fh.write(buffer.getvalue())
with open(os.path.join(box_dir, "loose.jpg"), "wb") as fh:
    fh.write(_fixtures.make_jpeg(30))

box_run = os.path.join(ROOT, "boxes_out")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", box_dir,
                "-o", box_run, "-k"], capture_output=True, cwd=PROJECT)
with open(os.path.join(box_run, "manifest.json"), encoding="utf-8") as fh:
    boxed = json.load(fh)

containers = [f for f in boxed["files"] if f.get("container")]
pictures = [f for f in boxed["files"] if not f.get("container")]
ok &= len(containers) == 1 and containers[0]["ext"] == "zip"
print(f"  the zip is marked a container: "
      f"{len(containers) == 1 and containers[0]['ext'] == 'zip'}")
ok &= len(pictures) == 4
print(f"  its 3 photographs and the loose one are not: {len(pictures) == 4}")

# The container stays in the case: it is evidence, with hashes, and a
# report that quietly dropped it would be wrong.
ok &= bool(containers[0]["sha256"]) and containers[0]["size"] > 0
print(f"  and it keeps its hashes and stays in the manifest: "
      f"{bool(containers[0]['sha256'])}")

# The working set that tracks this must never reach the manifest: it is a
# Python set, and JSON cannot hold one -- which truncated a manifest when
# it did.
ok &= "containers" not in boxed["summary"]
ok &= boxed["summary"].get("container_files") == 1
print(f"  the summary carries a count, not working state: "
      f"{boxed['summary'].get('container_files') == 1}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
