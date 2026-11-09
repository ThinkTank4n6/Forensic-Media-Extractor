"""Mobile extractions, documents, streamed video and content search.

Four things a phone case needs that a disk-image tool does not have:

* an extraction read as the device, with the path the phone used rather
  than the path the vendor's packaging used,
* media inside documents, which is a cheap and complete way to defeat a
  hash-set sweep,
* video an app streamed, which is kept as numbered fragments that will not
  open and have no name,
* and a search by what is in a picture rather than by which file it is.
"""
import tempfile
import os
import shutil
import sys
import tarfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.engines import appvideo, content_search, extraction

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "mobile_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True

try:
    from PIL import Image
except ImportError:
    print("Pillow is not installed; skipping")
    sys.exit(0)


def picture(seed, path, size=(160, 120)):
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = ((x * 2 + seed * 30) % 256, (y * 4) % 256,
                            (x + y + seed * 10) % 256)
    image.save(path, quality=88)
    return path


one = picture(1, os.path.join(ROOT, "a.jpg"))
two = picture(2, os.path.join(ROOT, "b.jpg"))

print("a mobile extraction, read as the device")
# The path an examiner cites in the witness box is the path on the phone,
# not the path inside the vendor's packaging.
layouts = {
    "cellebrite.ufdr": [
        ("files/Fs/1/private/var/mobile/Media/DCIM/100APPLE/IMG_0423.JPG",
         "/private/var/mobile/Media/DCIM/100APPLE/IMG_0423.JPG", "Cellebrite"),
        # No extension at all, which is most of an app's cache.
        ("files/Fs/1/private/var/mobile/Containers/Data/Application/4F2A/"
         "Library/Caches/7a9f2b31",
         "/private/var/mobile/Containers/Data/Application/4F2A/Library/"
         "Caches/7a9f2b31", "Cellebrite"),
    ],
    "graykey.zip": [
        ("DC25-321/filesystem1/private/var/mobile/Media/DCIM/101APPLE/"
         "IMG_9001.JPG",
         "/private/var/mobile/Media/DCIM/101APPLE/IMG_9001.JPG", "GrayKey"),
    ],
}
def kept_members(path):
    """What the program keeps from an extraction: the members outside the
    vendor's own folders whose first bytes are a JPEG, each with the device
    path device_path() gives it. extraction.walk did this and nothing in the
    program called it; it was removed in v2026.11.313."""
    rows = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            items = [(n, z.read(n)) for n in z.namelist() if not n.endswith("/")]
    else:
        with tarfile.open(path) as t:
            items = [(m.name, t.extractfile(m).read()) for m in t.getmembers()
                     if m.isfile()]
    for name, data in items:
        if extraction.SKIP_FOLDERS.search(name) or data[:3] != b"\xff\xd8\xff":
            continue
        where, tool = extraction.device_path(name)
        rows.append({"archive_path": name, "device_path": where, "packed_by": tool})
    return {"written": rows}


for name, members in layouts.items():
    path = os.path.join(ROOT, name)
    with zipfile.ZipFile(path, "w") as archive:
        for inside, _, _ in members:
            archive.write(one, inside)
        archive.writestr("UFDReports/report.xml", "<report/>")

    survey = extraction.survey(path)
    kept = kept_members(path)
    by_archive = {row["archive_path"]: row for row in kept["written"]}
    for inside, expected, tool in members:
        row = by_archive.get(inside)
        right = row is not None and row["device_path"] == expected
        ok &= right
        print(f"  {name:17} {expected[:54]}")
        if not right:
            print(f"    WRONG: {row['device_path'] if row else 'not kept'}")
    ok &= survey["packed_by"] == members[0][2]
    print(f"  {'':17} recognized as packed by {survey['packed_by']}")

# The vendor's own report folder is not the device, and must not be read
# as part of it.
report_kept = any("UFDReports" in row["archive_path"]
                  for row in kept["written"])
ok &= not report_kept
print(f"  the tool's own report files are left out: {not report_kept}")

print("\nan Android backup")
backup = os.path.join(ROOT, "android.tar")
with tarfile.open(backup, "w") as archive:
    archive.add(one, arcname="apps/com.whatsapp/f/Media/IMG-2026.jpg")
    archive.add(two, arcname="shared/0/DCIM/Camera/20261029.jpg")
found = kept_members(backup)
paths = sorted(row["device_path"] for row in found["written"])
ok &= paths == ["/data/data/com.whatsapp/Media/IMG-2026.jpg",
                "/storage/emulated/0/DCIM/Camera/20261029.jpg"]
for where in paths:
    print(f"  {where}")

print("\npictures hidden in documents")
# Twenty pictures in a .docx takes a minute and defeats a hash sweep
# completely: the document is one file with one hash, and no hash set
# holds hashes of documents somebody assembled.
#
# Tested through a real run, not through a module. A module that recovers
# them and a program that never calls it are not the same thing, which is
# a mistake already made here once.
import json as _json
import subprocess as _sub

docs_dir = os.path.join(ROOT, "documents")
os.makedirs(docs_dir, exist_ok=True)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with zipfile.ZipFile(os.path.join(docs_dir, "statement.docx"), "w") as archive:
    archive.writestr("word/document.xml", "<document/>")
    for index in range(3):
        archive.write(picture(index + 4, os.path.join(ROOT, f"m{index}.jpg")),
                      f"word/media/image{index}.jpg")
with zipfile.ZipFile(os.path.join(docs_dir, "deck.pptx"), "w") as archive:
    archive.write(picture(9, os.path.join(ROOT, "slide.jpg")),
                  "ppt/media/image1.jpg")
with zipfile.ZipFile(os.path.join(docs_dir, "notes.odt"), "w") as archive:
    archive.writestr("content.xml", "<d/>")
    archive.write(picture(10, os.path.join(ROOT, "odt.jpg")),
                  "Pictures/10000.jpg")

# And a PDF, whose images are taken out verbatim -- the file as its
# producer wrote it, so its hashes can still match a hash set.
with open(picture(11, os.path.join(ROOT, "pdfpic.jpg")), "rb") as fh:
    jpeg = fh.read()
pdf = (b"%PDF-1.4\n1 0 obj\n<< /Subtype /Image /Width 64 /Height 48 "
       b"/Filter /DCTDecode /Length " + str(len(jpeg)).encode() + b" >>\n"
       b"stream\n" + jpeg + b"\nendstream\nendobj\ntrailer<<>>\n%%EOF\n")
with open(os.path.join(docs_dir, "scan.pdf"), "wb") as fh:
    fh.write(pdf)

doc_run = os.path.join(ROOT, "doc_run")
_sub.run([sys.executable, "-m", "fme", "extract", "-i", docs_dir,
          "-o", doc_run], capture_output=True, cwd=project_root)
with open(os.path.join(doc_run, "manifest.json"), encoding="utf-8") as fh:
    out_files = _json.load(fh)["files"]

from_documents = [row for row in out_files
                  if "::" in (row.get("source_path") or "")]
ok &= len(from_documents) == 6
print(f"  {len(from_documents)} picture(s) recovered from 4 documents")

holders = {(row["source_path"].split("::")[0]) for row in from_documents}
ok &= holders == {"statement.docx", "deck.pptx", "notes.odt", "scan.pdf"}
print(f"  from each kind: {sorted(holders)}")

# And each one records the document it came out of, so an examiner can say
# where it was rather than only that it existed.
ok &= all("::" in row["source_path"] for row in from_documents)
print(f"  each records the document it was inside: True")

print("\nvideo an app streamed")
if not appvideo.available():
    print("  the vendored reader is absent; skipped")
else:
    import time

    cache = os.path.join(ROOT, "exo", "3")
    os.makedirs(cache)
    blob = bytes(range(256)) * 80          # stands in for a video
    stamp = int(time.time() * 1000)
    for number, start in enumerate(range(0, len(blob), 4096)):
        with open(os.path.join(cache, f"9.{start}.{stamp + number}.v3.exo"),
                  "wb") as fh:
            fh.write(blob[start:start + 4096])

    rebuilt = appvideo.rebuild(os.path.join(ROOT, "exo"),
                               os.path.join(ROOT, "rebuilt"))
    ok &= rebuilt["written"] == 1
    joined = rebuilt["items"][0]
    with open(joined["path"], "rb") as fh:
        back = fh.read()
    ok &= back == blob
    print(f"  {joined['pieces_joined']} fragment(s) joined into "
          f"{joined['name']}")
    print(f"  byte for byte what was cached: {back == blob}")

    # A cache holds what was watched. Saying a partial file is whole would
    # be a claim about what somebody saw.
    print(f"  completeness reported: complete={joined['complete']}, "
          f"length recorded={joined['length_known']}")

print("\nsearch by what is in a picture")
# No model ships with this program, so the engine must refuse rather than
# pretend. That refusal is the testable part until a model is supplied.
ready, why = content_search.available("")
ok &= not ready and "no model" in why
print(f"  with no model: refuses — {why[:46]}…")
ready, why = content_search.available(os.path.join(ROOT, "absent.onnx"))
ok &= not ready
print(f"  with a path to nothing: refuses")

# The ranking itself does not need a model to be checked.
import math


def unit(values):
    length = math.sqrt(sum(v * v for v in values))
    return [v / length for v in values]


ranked = content_search.rank(unit([1, 0, 0, 0]), {
    "identical": unit([1, 0, 0, 0]),
    "alike": unit([0.9, 0.3, 0.1, 0]),
    "unrelated": unit([0, 1, 0, 0])})
ok &= ranked[0]["sha256"] == "identical" and ranked[0]["rank"] == 1
ok &= ranked[1]["sha256"] == "alike"
print(f"  ranking puts them in order: "
      f"{[row['sha256'] for row in ranked]}")

result = content_search.search(one, "", {})
ok &= not result["ok"]
print(f"  a search with no model returns an error, not an empty answer")

# A model file is recognized by its hash, so a wrong or truncated one is
# caught before a search quietly returns nonsense -- and the one failure
# worth naming is named: OpenCV 4 cannot load this model, and the error it
# raises on its own ("error in handleNode") tells an examiner nothing.
known = list(content_search.KNOWN_MODELS.values())[0]
ok &= known["needs_opencv"] == (5, 0)
print(f"  {known['name']} is recognized by hash, and needs OpenCV "
      f"{known['needs_opencv'][0]}.{known['needs_opencv'][1]}")

import cv2

here = tuple(int(part) for part in cv2.__version__.split(".")[:2])
if here < (5, 0):
    # On this machine the model cannot run, so the message is the feature.
    fake = os.path.join(ROOT, "pretend.onnx")
    with open(fake, "wb") as fh:
        fh.write(b"not a model")
    _, why = content_search.available(fake)
    ok &= "would not load" in why or "cannot" in why
    print(f"  a file that is not a model is refused: {why[:52]}…")

# The modules above are only worth having if the ordinary extraction path
# uses them. For four versions they did not: they had tests and nothing
# called them, so a real UFDR still recorded the vendor's archive path.
print("\nthrough the ordinary extraction path")
import json as _json
import subprocess as _sub
import zipfile as _zip

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_media_test_image as _fixtures

project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
flow = os.path.join(ROOT, "through")
os.makedirs(flow, exist_ok=True)
# Two different photographs: one on an iPhone, one on an Android seen
# through three mount points. They must be different bytes, or ordinary
# duplicate suppression collapses all four and the mount-point behaviour
# is never exercised.
iphone = os.path.join(flow, "iphone.jpg")
with open(iphone, "wb") as fh:
    fh.write(_fixtures.make_jpeg(44))
android_photo = os.path.join(flow, "android.jpg")
with open(android_photo, "wb") as fh:
    fh.write(_fixtures.make_jpeg(45))

ufdr = os.path.join(flow, "DC25-900.ufdr")
with _zip.ZipFile(ufdr, "w") as archive:
    archive.write(iphone, "files/Fs/1/private/var/mobile/Media/DCIM/"
                          "100APPLE/IMG_0423.JPG")
    for view in ("storage/emulated/0", "data/media/0",
                 "mnt/user/0/emulated/0"):
        archive.write(android_photo,
                      f"files/Fs/1/{view}/DCIM/Camera/IMG_1.jpg")

run = os.path.join(flow, "run")
_sub.run([sys.executable, "-m", "fme", "extract", "-i", ufdr, "-o", run],
         capture_output=True, cwd=project)
with open(os.path.join(run, "manifest.json"), encoding="utf-8") as fh:
    recovered = _json.load(fh)["files"]

paths = {row.get("device_path", "") for row in recovered}
ok &= "/private/var/mobile/Media/DCIM/100APPLE/IMG_0423.JPG" in paths
print(f"  the manifest records the path on the phone: "
      f"{'/private/var/...' if any('private/var' in p for p in paths) else 'NO'}")

packed = {row.get("packed_by", "") for row in recovered}
ok &= "Cellebrite" in packed
print(f"  and which packaging it came in: {sorted(packed - {''})}")

# The archive path is kept too: it is how the file is found again inside
# the evidence, and losing it would be its own problem.
ok &= any("files/Fs/1/" in (row.get("source_path") or "")
          for row in recovered)
print(f"  without losing the path inside the archive: True")

# One photograph, three mount points, one file.
android = [row for row in recovered
           if "DCIM/Camera" in (row.get("device_path") or "")]
ok &= len(android) == 1
if android:
    others = android[0].get("also_seen_at", "")
    ok &= others.count(";") == 1          # the two other spellings
    print(f"  three mount points became one file, with the others recorded:")
    print(f"    kept : {android[0]['device_path']}")
    for seen in others.split("; "):
        print(f"    also : {seen}")

# Streamed video is where phone media increasingly lives, and it does not
# arrive as one contiguous file. The vendored reader resolves an item's
# name from the cache index and joins a DASH or HLS stream's segments in
# the order its cached manifest lists them; the older rejoiner, built for
# contiguous pieces, is kept as the fallback because a cache the vendored
# reader cannot read is no reason to recover nothing.
print("\nstreamed video, through the extraction path")
import time as _time

stream_root = os.path.join(ROOT, "stream")
cache_dir = os.path.join(stream_root, "exo", "7")
os.makedirs(cache_dir, exist_ok=True)
blob = bytes(range(256)) * 900          # stands in for a video
stamp = int(_time.time() * 1000)
piece = len(blob) // 10
for number, start in enumerate(range(0, len(blob), piece)):
    with open(os.path.join(cache_dir, f"21.{start}.{stamp + number}.v3.exo"),
              "wb") as fh:
        fh.write(blob[start:start + piece])

rebuilt = appvideo.rebuild(os.path.join(stream_root, "exo"),
                           os.path.join(stream_root, "out"))
ok &= rebuilt["written"] == 1
joined = rebuilt["items"][0]
with open(joined["path"], "rb") as fh:
    ok &= fh.read() == blob
print(f"  {joined['pieces_joined']} segment(s) joined, byte for byte: True")

# A cache holds what was watched. Where a segment is missing the join
# stops there and says so, rather than presenting a short file as whole.
gappy = os.path.join(stream_root, "gappy")
shutil.copytree(os.path.join(stream_root, "exo"), gappy)
# By position, not by name: the names sort lexicographically, so the
# "middle" name can be the last piece, and removing that truncates the
# video rather than leaving a hole in it. A hole is the case worth
# testing, because that is where a join has to stop.
pieces = sorted(os.listdir(os.path.join(gappy, "7")),
                key=lambda name: int(name.split(".")[1]))
os.remove(os.path.join(gappy, "7", pieces[len(pieces) // 2]))
short = appvideo.rebuild(gappy, os.path.join(stream_root, "gapout"))
ok &= short["partial"] == 1
item = short["items"][0]
ok &= item["complete"] is False
print(f"  with a segment missing: complete={item['complete']}, "
      f"joined {item['pieces_joined']} of {item['pieces']}")

# The older rejoiner still handles an ordinary cache, which is what the
# fallback depends on.
from fme.engines import appcache

older = list(appcache.rejoin_folder(os.path.join(stream_root, "exo")))
ok &= len(older) == 1 and older[0][1] == blob
print(f"  the fallback still joins an ordinary cache: {len(older)} item(s)")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
