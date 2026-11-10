"""Deleted files out of the NTFS master file table.

Carving finds content and loses everything else. The MFT keeps the name, the
path and the dates after a file is deleted, and for small files it keeps the
content too -- inside the record, where free space has never held it and a
carver will never look.

The volume these run against is built byte by byte in
make_ntfs_test_image.py, so what should come back is known exactly.
"""
import tempfile
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_media_test_image as g
from fme.engines import ntfs_mft

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "ntfs_mft_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

image = os.path.join(ROOT, "ntfs.dd")
subprocess.run([sys.executable,
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "make_ntfs_test_image.py"), image],
               capture_output=True, check=False)

ok = os.path.isfile(image)
with open(image, "rb") as fh:
    blob = fh.read()


def read(offset, count):
    return blob[offset:offset + count]


boot = ntfs_mft.read_boot(blob[:512])
ok &= bool(boot)
print(f"boot sector: {boot.get('cluster_size')}-byte clusters, "
      f"{boot.get('record_size')}-byte records, "
      f"table at {boot.get('mft_offset'):,}")

# A boot sector that is not NTFS must be refused rather than guessed at: the
# numbers in it decide where every record is read from.
ok &= ntfs_mft.read_boot(b"\x00" * 512) == {}
ok &= ntfs_mft.read_boot(blob[:512].replace(b"NTFS    ", b"FAT32   ")) == {}
print(f"a non-NTFS boot sector is refused: "
      f"{ntfs_mft.read_boot(b'x' * 512) == {}}")

folders = ntfs_mft.directory_map(read, boot)
ok &= any(entry["name"] == "Pictures" for entry in folders.values())
print(f"folders read: {sorted(e['name'] for e in folders.values())}")

expected = {
    "tiny_thumbnail.jpg": (hashlib.sha256(g.make_jpeg(1)[:600]).hexdigest(),
                           "resident"),
    "holiday_photo.png": (hashlib.sha256(g.make_png()).hexdigest(),
                          "from released clusters"),
}

print("\ndeleted files recovered:")
seen = {}
for record in ntfs_mft.deleted_files(read, boot):
    payload, how = ntfs_mft.content(record, read, boot)
    seen[record["name"]] = (hashlib.sha256(payload).hexdigest(), how, record)
    print(f"  {record['name']:22} {len(payload):>7,} bytes  {how}")
    print(f"     path     {ntfs_mft.path_of(record, folders)}")
    print(f"     modified {record['modified']}")

for name, (digest, how) in expected.items():
    got = seen.get(name)
    good = got is not None and got[0] == digest and got[1] == how
    ok &= good
    print(f"  {name:22} byte-exact and {how}: {good}")

# The whole point of the resident case: the content lived in the record, so
# free space never held it and carving cannot find it.
resident = seen.get("tiny_thumbnail.jpg")
ok &= resident is not None and resident[2]["resident"] is not None
print(f"\n  the resident file's bytes came from its record, not from disk: "
      f"{resident is not None and resident[2]['resident'] is not None}")

# Paths and dates are the difference between this and a carved file.
for name in expected:
    record = seen[name][2]
    has_path = ntfs_mft.path_of(record, folders).startswith("Pictures/")
    has_dates = bool(record["modified"]) and bool(record["created"])
    ok &= has_path and has_dates
    print(f"  {name:22} keeps its path and dates: {has_path and has_dates}")

# A live file must never be reported as deleted.
live = [r["name"] for r in ntfs_mft.records(read, boot)
        if r["in_use"] and not r["directory"] and r["name"]]
ok &= "still_here.png" in live and "still_here.png" not in seen
print(f"\n  a live file is not reported as deleted: "
      f"{'still_here.png' in live and 'still_here.png' not in seen}")

print("\nthrough the engine")
out_dir = os.path.join(ROOT, "out")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image,
                "-o", out_dir], capture_output=True, cwd=PROJECT)
with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
digests = {f["sha256"] for f in manifest["files"]}
for name, (digest, _) in expected.items():
    found = digest in digests
    ok &= found
    print(f"  {name:22} recovered by a run: {found}")

from_mft = [f for f in manifest["files"] if f["engine"] == "mft"]
ok &= len(from_mft) == 2
ok &= all(f["deleted"] for f in from_mft)
ok &= all(f["source_path"].startswith("Pictures/") for f in from_mft)
print(f"  all {len(from_mft)} marked deleted, with their paths: "
      f"{all(f['deleted'] and f['source_path'].startswith('Pictures/') for f in from_mft)}")

# And the note has to say which kind of recovery it was, because that is
# what decides how far the content can be relied on.
notes = " ".join(f["note"] for f in from_mft)
ok &= "resident in the record" in notes and "released" in notes
print(f"  each says how it was recovered: "
      f"{'resident in the record' in notes and 'released' in notes}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
