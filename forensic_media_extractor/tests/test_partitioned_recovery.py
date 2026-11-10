"""Deleted-file recovery reaches every partition, not just the first.

The deleted-FAT and MFT passes were handed a list of partitions to look in,
but nothing ever filled that list in a real run, so each looked only at the
start of the image. The FAT pass then read the partition table itself and
always took the first FAT32 entry; the MFT pass read offset 0, which on a
whole-disk image is the partition table, not a volume. So on an ordinary
disk image:

  - deleted FAT files on any partition after the first were never looked for;
  - a GPT disk had no FAT volume found at all;
  - MFT recovery found nothing, on any NTFS partition, because none starts
    at offset 0.

Nothing failed and nothing warned. The examiner saw a run that finished
with no deleted files from those volumes, which looks exactly like a disk
that had none.

This builds a disk with two FAT32 partitions and one NTFS partition, once
with an MBR and once with GPT, and runs both passes the way run_extraction
calls them -- with no partition list.
"""
import contextlib
import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

import make_media_test_image as fixtures
from fme.engines import media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_partitioned-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

SECTOR = 512
MIB = 2048                     # sectors in a MiB, where partitions usually start

# Two FAT32 volumes whose deleted pictures differ by one byte, so the hash
# check cannot fold them together and hide that the second was never read.
fat_path = os.path.join(ROOT, "fat.img")
with contextlib.redirect_stdout(io.StringIO()):   # the builder's own listing
    fixtures.main(fat_path)
with open(fat_path, "rb") as fh:
    fat_a = bytearray(fh.read())
deleted_jpeg = fixtures.make_jpeg(9)
at = fat_a.find(deleted_jpeg)
assert at > 0, "the fixture's deleted picture was not where it was expected"
fat_b = bytearray(fat_a)
fat_b[at + len(deleted_jpeg) // 2] ^= 0xFF
fat_b[71:82] = b"SECONDCARD "              # its own volume label

ntfs_path = os.path.join(ROOT, "ntfs.dd")
subprocess.run([sys.executable, os.path.join(HERE, "make_ntfs_test_image.py"),
                ntfs_path], capture_output=True, check=False)
with open(ntfs_path, "rb") as fh:
    ntfs = fh.read()

volumes = [(bytes(fat_a), 0x0C), (bytes(fat_b), 0x0C), (ntfs, 0x07)]
starts, lba = [], MIB
for blob, _ in volumes:
    starts.append(lba)
    lba += -(-len(blob) // SECTOR // MIB) * MIB + MIB
disk_sectors = lba + 64


def lay_out(disk: bytearray) -> bytearray:
    for (blob, _), start in zip(volumes, starts):
        disk[start * SECTOR:start * SECTOR + len(blob)] = blob
    return disk


def mbr_disk() -> bytes:
    disk = bytearray(disk_sectors * SECTOR)
    for slot, ((blob, kind), start) in enumerate(zip(volumes, starts)):
        struct.pack_into("<B3sB3sII", disk, 446 + 16 * slot, 0, b"", kind, b"",
                         start, -(-len(blob) // SECTOR))
    disk[510:512] = b"\x55\xaa"
    return bytes(lay_out(disk))


def gpt_disk() -> bytes:
    disk = bytearray(disk_sectors * SECTOR)
    # The protective MBR every GPT disk carries: one entry, type 0xEE.
    struct.pack_into("<B3sB3sII", disk, 446, 0, b"", 0xEE, b"", 1,
                     disk_sectors - 1)
    disk[510:512] = b"\x55\xaa"
    basic = uuid.UUID("ebd0a0a2-b9e5-4433-87c0-68b6b72699c7").bytes_le
    entries = bytearray(128 * 128)
    for slot, ((blob, _), start) in enumerate(zip(volumes, starts)):
        entry = basic + uuid.uuid4().bytes_le + struct.pack(
            "<QQQ", start, start + -(-len(blob) // SECTOR) - 1, 0)
        entry += f"part{slot + 1}".encode("utf-16-le").ljust(72, b"\x00")
        entries[slot * 128:slot * 128 + 128] = entry
    header = bytearray(92)
    header[0:8] = b"EFI PART"
    struct.pack_into("<IIIIQQQQ16sQIII", header, 8, 0x00010000, 92, 0, 0, 1,
                     disk_sectors - 1, 34, disk_sectors - 34,
                     uuid.uuid4().bytes_le, 2, 128, 128, 0)
    disk[SECTOR:SECTOR + 92] = header
    disk[2 * SECTOR:2 * SECTOR + len(entries)] = entries
    return bytes(lay_out(disk))


expected_fat = sorted({hashlib.sha256(bytes(v[at:at + len(deleted_jpeg)])).hexdigest()
                       for v in (fat_a, fat_b)})
args = types.SimpleNamespace(image="", max_file_size=1 << 32)

for name, build in (("MBR", mbr_disk), ("GPT", gpt_disk)):
    print(f"\na {name} disk with two FAT32 partitions and one NTFS partition")
    image = os.path.join(ROOT, f"{name.lower()}.dd")
    with open(image, "wb") as fh:
        fh.write(build())
    out_dir = os.path.join(ROOT, f"out_{name.lower()}")
    reader = media_core.open_image(image)
    out = media_core.OutputManager(out_dir, metadata=False)
    stats = {}
    args.image = image
    try:
        media_core.run_fat_deleted_recovery(reader, out, args, stats,
                                            media_core.SIGNATURES)
        media_core.run_mft_recovery(reader, out, args, stats,
                                    media_core.SIGNATURES)
    finally:
        out.close(stats)
        reader.close()

    fat_found = sorted(r.sha256 for r in out.records if r.engine == "fat-deleted")
    mft_names = sorted(os.path.basename(r.source_path) for r in out.records
                       if r.engine != "fat-deleted")
    labels = sorted({r.partition for r in out.records if r.engine == "fat-deleted"})

    both = fat_found == expected_fat
    ok &= both
    print(f"   the deleted picture on each FAT volume is recovered: {both}")
    counted = stats.get("fat_volumes") == 2
    ok &= counted
    print(f"   FAT volumes counted: {stats.get('fat_volumes')} (two exist): {counted}")
    apart = len(labels) == 2
    ok &= apart
    print(f"   each FAT file is credited to its own partition: {apart}")
    mft = mft_names == ["holiday_photo.png", "tiny_thumbnail.jpg"]
    ok &= mft
    print(f"   the NTFS partition's deleted files are recovered: {mft}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
