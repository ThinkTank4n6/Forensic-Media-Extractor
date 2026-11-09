"""FAT12 and FAT16 volumes are read, deleted files included.

fat32_core.parse_boot_sector refused any boot sector whose 16-bit FAT size
was set -- which is how FAT12 and FAT16 say what they are -- so the
deleted-entry pass skipped the volume, logging it at debug level only, and
the no-TSK walk could not read it at all. Deleted files on every card of
2 GB and under came back, if at all, as unnamed carved files. Those
formats keep deleted entries exactly as FAT32 does; what differs is the
root folder, in a fixed region after the FATs, and the width of a FAT
entry.

Each card here is built by hand: a deleted picture in the root folder, a
live folder holding a live picture and a deleted one, and a deleted folder
holding a picture. Both deleted-file paths and a full run are checked, on
a FAT16 card and a FAT12 card, and -- where pytsk3 is installed -- the run
is checked with TSK too.
"""
import collections
import contextlib
import glob
import hashlib
import io
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import fat32_core, media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_fat16-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

BPS, SPC, RES, NFATS, ROOTENT = 512, 4, 1, 2, 512
CLUSTER = BPS * SPC
EOC = {"FAT16": 0xFFFF, "FAT12": 0xFFF}


def card(kind: str, total_sectors: int):
    """A hand-built FAT12 or FAT16 card, and the pictures it holds."""
    clusters = total_sectors // SPC
    width = 2 if kind == "FAT16" else 1.5
    fat_sectors = -(-int((clusters + 2) * width + 1) // BPS)
    root_sectors = ROOTENT * 32 // BPS
    first_data = RES + NFATS * fat_sectors + root_sectors
    image = bytearray(total_sectors * BPS)
    image[0:3] = b"\xeb\x3c\x90"
    image[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHII", image, 11, BPS, SPC, RES, NFATS, ROOTENT,
                     total_sectors if total_sectors < 65536 else 0, 0xF8,
                     fat_sectors, 63, 255, 0,
                     0 if total_sectors < 65536 else total_sectors)
    image[38] = 0x29
    image[43:54] = b"CAMERA     "
    image[54:62] = kind.encode().ljust(8)
    image[510:512] = b"\x55\xaa"
    fat = {0: 0xFF8 if kind == "FAT12" else 0xFFF8, 1: EOC[kind]}
    next_free = [2]

    def write(data: bytes, keep: bool) -> int:
        n = max(1, -(-len(data) // CLUSTER))
        start = next_free[0]
        next_free[0] += n
        for i in range(n):
            at = (first_data + (start + i - 2) * SPC) * BPS
            image[at:at + CLUSTER] = data[i * CLUSTER:(i + 1) * CLUSTER].ljust(CLUSTER, b"\0")
            if keep:
                fat[start + i] = start + i + 1 if i + 1 < n else EOC[kind]
        return start

    def entry(name, ext, attr, first, size, deleted=False):
        raw = bytearray(fixtures.dir_entry(name, ext, attr, first, size))
        if deleted:
            raw[0] = 0xE5
        return bytes(raw)

    pictures = {"ROOT_DEL": fixtures.make_jpeg(3), "LIVE": fixtures.make_jpeg(4),
                "DCIM_DEL": fixtures.make_jpeg(5), "GONE": fixtures.make_jpeg(6)}
    root_del = write(pictures["ROOT_DEL"], False)
    live = write(pictures["LIVE"], True)
    dcim_del = write(pictures["DCIM_DEL"], False)
    gone = write(pictures["GONE"], False)
    dcim = next_free[0]
    write(entry(".", "", 0x10, dcim, 0) + entry("..", "", 0x10, 0, 0)
          + entry("IMG_0004", "JPG", 0x20, live, len(pictures["LIVE"]))
          + entry("IMG_0005", "JPG", 0x20, dcim_del, len(pictures["DCIM_DEL"]), True), True)
    private = next_free[0]
    write(entry(".", "", 0x10, private, 0) + entry("..", "", 0x10, 0, 0)
          + entry("IMG_0006", "JPG", 0x20, gone, len(pictures["GONE"]), True), False)
    root = (RES + NFATS * fat_sectors) * BPS
    listing = (entry("IMG_0003", "JPG", 0x20, root_del, len(pictures["ROOT_DEL"]), True)
               + entry("DCIM", "", 0x10, dcim, 0)
               + entry("PRIVATE", "", 0x10, private, 0, True))
    image[root:root + len(listing)] = listing
    for copy in range(NFATS):
        base = (RES + copy * fat_sectors) * BPS
        for cluster, value in fat.items():
            if kind == "FAT16":
                struct.pack_into("<H", image, base + 2 * cluster, value)
            else:
                at = base + cluster + cluster // 2
                word = image[at] | (image[at + 1] << 8)
                word = (word & 0x000F) | (value << 4) if cluster & 1 \
                    else (word & 0xF000) | value
                image[at], image[at + 1] = word & 0xFF, word >> 8
    path = os.path.join(ROOT, kind.lower() + ".img")
    with open(path, "wb") as fh:
        fh.write(bytes(image))
    return path, {k: hashlib.sha256(v).hexdigest() for k, v in pictures.items()}


def deleted_pass(path):
    reader = media_core.open_image(path)
    out = media_core.OutputManager(path + "_pass", metadata=False)
    stats = collections.defaultdict(int)
    try:
        media_core.run_fat_deleted_recovery(
            reader, out, types.SimpleNamespace(image=path, max_file_size=1 << 32),
            stats, media_core.SIGNATURES)
    finally:
        out.close(dict(stats))
        reader.close()
    return {r.sha256: r for r in out.records}, stats


def fallback_walk(path):
    reader = media_core.open_image(path)
    out = media_core.OutputManager(path + "_walk", metadata=False)
    stats = collections.defaultdict(int, volumes=[])
    args = types.SimpleNamespace(image=path, max_file_size=1 << 32, no_deleted=False,
                                 include_system=False, databases=False)
    try:
        media_core.run_fat32_fallback(args, out, stats, media_core.SIGNATURES, reader=reader)
    finally:
        out.close(dict(stats))
        reader.close()
    return {r.sha256: r for r in out.records}


def full_run(path, tsk: bool):
    out = path + ("_tsk" if tsk else "_full")
    env = dict(os.environ)
    if not tsk:
        env["FME_NO_TSK"] = "1"
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", path, "-o", out],
                   capture_output=True, cwd=PROJECT, timeout=900, env=env)
    found = glob.glob(os.path.join(out, "**", "manifest.json"), recursive=True)
    if not found:
        return {}
    with open(found[0], encoding="utf-8") as fh:
        return {f["sha256"]: f for f in json.load(fh)["files"]}


def named(found: dict, digest: str, tail: str) -> bool:
    rec = found.get(digest)
    path = (rec.source_path if hasattr(rec, "source_path") else (rec or {}).get("source_path", "")) or ""
    return rec is not None and path.upper().replace("_", "?").endswith(tail)


try:
    import pytsk3  # noqa: F401
    HAVE_TSK = True
except ImportError:
    HAVE_TSK = False

for kind, sectors in (("FAT16", 65536), ("FAT12", 8192)):
    path, digests = card(kind, sectors)
    with open(path, "rb") as fh:
        bs = fat32_core.parse_boot_sector(io.BytesIO(fh.read(512)))
    print(f"\na hand-built {kind} card")
    right = bs.fat_type == kind and bs.root_cluster == 0 and bs.volume_label == "CAMERA"
    ok &= right
    print(f"   its boot sector is read as {bs.fat_type}, label {bs.volume_label!r}: {right}")

    found, stats = deleted_pass(path)
    checks = {"the deleted picture in the root folder": ("ROOT_DEL", "/?MG?0003.JPG"),
              "the deleted picture in a live folder": ("DCIM_DEL", "DCIM/?MG?0005.JPG"),
              "the picture in a deleted folder": ("GONE", "?RIVATE/?MG?0006.JPG")}
    for label, (key, tail) in checks.items():
        right = named(found, digests[key], tail)
        ok &= right
        print(f"   deleted-entry pass: {label}, by name: {right}")
    typed = all(r.fs_type == kind for r in found.values())
    ok &= typed
    print(f"   recorded as {kind}: {typed}")

    walked = fallback_walk(path)
    right = named(walked, digests["LIVE"], "DCIM/IMG?0004.JPG") \
        and named(walked, digests["DCIM_DEL"], "DCIM/?MG?0005.JPG")
    ok &= right
    print(f"   no-TSK walk: the live picture and the deleted one beside it: {right}")

    # The same card as the one partition on a disk, as a reader shows it.
    with open(path, "rb") as fh:
        volume = fh.read()
    disk = bytearray(2048 * 512) + bytearray(volume)
    struct.pack_into("<B3sB3sII", disk, 446, 0, b"", 0x06 if kind == "FAT16" else 0x01,
                     b"", 2048, len(volume) // 512)
    disk[510:512] = b"\x55\xaa"
    disk_path = path + ".disk.dd"
    with open(disk_path, "wb") as fh:
        fh.write(bytes(disk))
    found, _ = deleted_pass(disk_path)
    right = all(named(found, digests[key], tail) for key, tail in checks.values())
    ok &= right
    print(f"   as a partition on a disk: the deleted pictures, by name: {right}")

    full = full_run(path, tsk=False)
    right = all(named(full, digests[key], tail) for key, tail in checks.values()) \
        and named(full, digests["LIVE"], "DCIM/IMG?0004.JPG")
    ok &= right
    print(f"   a full run without TSK: all four, by name: {right}")
    if HAVE_TSK:
        full = full_run(path, tsk=True)
        right = all(named(full, digests[key], tail) for key, tail in checks.values()) \
            and named(full, digests["LIVE"], "DCIM/IMG?0004.JPG")
        ok &= right
        print(f"   a full run with TSK: all four, by name: {right}")
    else:
        print("   (pytsk3 is not installed here: the run with TSK is not checked)")

print("\nboot sectors that are not FAT are still refused")
for label, raw in (("NTFS", b"\xeb\x52\x90NTFS    " + bytes(499) + b"\x55\xaa"),
                   ("exFAT", b"\xeb\x76\x90EXFAT   " + bytes(499) + b"\x55\xaa")):
    try:
        fat32_core.parse_boot_sector(io.BytesIO(raw))
        refused = False
    except ValueError:
        refused = True
    ok &= refused
    print(f"   {label}: refused: {refused}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
