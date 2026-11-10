"""
make_fat_deleted.py -- a FAT32 volume with a known answer.

The point is not the image. It is the `.intended` file written beside it:
the SHA-256 and size of every file **as it was written, before it was
deleted**. The expected answer therefore comes from what was put in, not
from any reader -- so a test built on it cannot be satisfied by a reader
that is confidently wrong, which is what a test comparing one of our
readers against another of our readers can be.

    python tools/make_fixtures/make_fat_deleted.py tests/fixtures/

Writes `fat-deleted.img` and `fat-deleted.intended`.

The approach is GLEAPP's (MIT, Alexis Brignoni), whose fixtures record
hashes before deletion and cross-check them against The Sleuth Kit at
build time. This is our own code; what is borrowed is the discipline.
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "tests"))

SECTOR = 512
# FAT32 by the specification, not only by its label. The type of a FAT volume
# is decided by its count of clusters alone -- under 4,085 is FAT12, under
# 65,525 FAT16 -- and this was 16 MiB in 4 KiB clusters: 4,083 of them, a
# FAT12 volume whose boot sector said FAT32. This program's reader trusts the
# boot sector and read it; The Sleuth Kit follows the specification, read a
# FAT12 volume with no root directory, listed nothing, and the cross-check in
# test_known_answer.py compared nothing ("0 agreed, 0 differed"). 34 MiB in
# 512-byte clusters is about 68,500 clusters: FAT32 to every reader.
CLUSTER_SECTORS = 1
CLUSTER = SECTOR * CLUSTER_SECTORS
RESERVED = 32
FATS = 2
SECTORS = 69632                      # 34 MiB
FAT32_MIN_CLUSTERS = 65525


def _boot_sector(fat_sectors: int) -> bytes:
    boot = bytearray(SECTOR)
    boot[0:3] = b"\xEB\x58\x90"
    boot[3:11] = b"FMEFIX  "
    struct.pack_into("<H", boot, 11, SECTOR)
    boot[13] = CLUSTER_SECTORS
    struct.pack_into("<H", boot, 14, RESERVED)
    boot[16] = FATS
    struct.pack_into("<H", boot, 19, 0)
    boot[21] = 0xF8
    struct.pack_into("<H", boot, 22, 0)
    struct.pack_into("<H", boot, 24, 63)
    struct.pack_into("<H", boot, 26, 255)
    struct.pack_into("<I", boot, 32, SECTORS)
    struct.pack_into("<I", boot, 36, fat_sectors)
    struct.pack_into("<I", boot, 44, 2)
    struct.pack_into("<H", boot, 48, 1)
    struct.pack_into("<H", boot, 50, 6)
    boot[66] = 0x29
    struct.pack_into("<I", boot, 67, 0x12345678)
    boot[71:82] = b"FMEKNOWN   "
    boot[82:90] = b"FAT32   "
    boot[510:512] = b"\x55\xAA"
    return bytes(boot)


def _dir_entry(stem: str, ext: str, cluster: int, size: int,
               deleted: bool = False) -> bytes:
    entry = bytearray(32)
    name = f"{stem[:8]:<8}{ext[:3]:<3}".upper().encode("ascii")
    entry[0:11] = name
    if deleted:
        # What FAT writes over the first character. The rest of the entry
        # -- the name, the first cluster, the size -- survives, which is
        # what makes a deleted FAT file recoverable at all.
        entry[0] = 0xE5
    entry[11] = 0x20
    struct.pack_into("<H", entry, 20, (cluster >> 16) & 0xFFFF)
    struct.pack_into("<H", entry, 26, cluster & 0xFFFF)
    struct.pack_into("<I", entry, 28, size)
    return bytes(entry)


def build(out_dir: str) -> dict:
    import make_media_test_image as fixtures

    os.makedirs(out_dir, exist_ok=True)
    image_path = os.path.join(out_dir, "fat-deleted.img")
    intended_path = os.path.join(out_dir, "fat-deleted.intended")

    clusters = (SECTORS - RESERVED) // (CLUSTER_SECTORS + FATS * 4 / SECTOR)
    fat_sectors = int((clusters * 4 + SECTOR - 1) // SECTOR) + 1
    data_start = (RESERVED + FATS * fat_sectors) * SECTOR
    counted = (SECTORS - RESERVED - FATS * fat_sectors) // CLUSTER_SECTORS
    if counted < FAT32_MIN_CLUSTERS:
        raise SystemExit(f"{counted} clusters is not FAT32 by the specification "
                         f"(it needs {FAT32_MIN_CLUSTERS}); The Sleuth Kit would "
                         f"read this as FAT12 or FAT16")

    image = bytearray(SECTORS * SECTOR)
    image[0:SECTOR] = _boot_sector(fat_sectors)

    fat = bytearray(fat_sectors * SECTOR)
    struct.pack_into("<I", fat, 0, 0x0FFFFFF8)
    struct.pack_into("<I", fat, 4, 0x0FFFFFFF)
    struct.pack_into("<I", fat, 8, 0x0FFFFFFF)          # cluster 2: root

    def place(blob: bytes, first_cluster: int, free_after: bool) -> None:
        """Write a file's bytes, and either chain or free its clusters."""
        needed = max(1, (len(blob) + CLUSTER - 1) // CLUSTER)
        at = data_start + (first_cluster - 2) * CLUSTER
        image[at:at + len(blob)] = blob
        for index in range(needed):
            slot = first_cluster + index
            last = index == needed - 1
            value = 0 if free_after else (
                0x0FFFFFFF if last else slot + 1)
            struct.pack_into("<I", fat, slot * 4, value)

    # What goes in, and what is expected back out.
    # First clusters follow from the sizes, with a gap after each: fixed
    # numbers were right for one cluster size and overlap at another.
    pictures = [
        # (name, bytes, deleted, the chain freed with it)
        ("LIVE0001", "JPG", fixtures.make_jpeg(7), False, False),
        ("GONE0002", "JPG", fixtures.make_jpeg(11), True, True),
        ("GONE0003", "JPG", fixtures.make_jpeg(3), True, True),
    ]
    plan, next_free = [], 3
    for stem, ext, blob, deleted, freed in pictures:
        plan.append((stem, ext, blob, next_free, deleted, freed))
        next_free += (len(blob) + CLUSTER - 1) // CLUSTER + 4

    root = bytearray()
    intended = []
    for stem, ext, blob, cluster, deleted, freed in plan:
        place(blob, cluster, free_after=freed)
        root += _dir_entry(stem, ext, cluster, len(blob), deleted=deleted)
        intended.append({
            "sha256": hashlib.sha256(blob).hexdigest(),
            "size": len(blob),
            "name": f"{stem}.{ext}",
            "deleted": deleted,
        })

    image[data_start:data_start + len(root)] = root
    for copy in range(FATS):
        at = (RESERVED + copy * fat_sectors) * SECTOR
        image[at:at + len(fat)] = fat

    with open(image_path, "wb") as fh:
        fh.write(bytes(image))

    with open(intended_path, "w", encoding="utf-8") as fh:
        fh.write("# What was written to fat-deleted.img, hashed before any\n"
                 "# of it was deleted. The expected answer comes from what\n"
                 "# went in, not from a reader -- so a reader that is\n"
                 "# confidently wrong cannot satisfy a test built on this.\n"
                 "# sha256  size  name  deleted\n")
        for row in intended:
            fh.write(f"{row['sha256']}  {row['size']}  {row['name']}  "
                     f"{'deleted' if row['deleted'] else 'live'}\n")

    return {"image": image_path, "intended": intended_path,
            "files": intended}


if __name__ == "__main__":
    where = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        PROJECT, "tests", "fixtures")
    made = build(where)
    print(f"wrote {made['image']}")
    print(f"wrote {made['intended']}:")
    for row in made["files"]:
        print(f"  {row['sha256'][:16]}…  {row['size']:>7}  {row['name']}  "
              f"{'deleted' if row['deleted'] else 'live'}")
