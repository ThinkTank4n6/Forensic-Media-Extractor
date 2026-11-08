"""A locked volume is flagged wherever it sits on the image.

A BitLocker or LUKS header is at the start of its own volume. Both checks
looked only at the start of the evidence: run_extraction read the first
512 bytes of the file itself -- on an E01, the E01's own header -- and
inspect_image read sector 0 of the disk, which on a partitioned disk is
the partition table. So the commonest case, an E01 of a laptop with a
BitLocker partition, was never flagged. The run recovered a handful of
files from the unencrypted system partition and read as a nearly empty
disk, and the "nothing recovered" explanation never fired, because
something had been.

Each disk here is built by hand. The E01 case is a stand-in: a file whose
own header is an EWF signature, read through a source that presents the
disk inside it -- which is what the E01 readers do.
"""
import contextlib
import io
import logging
import os
import shutil
import struct
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

import make_media_test_image as fixtures
from fme.engines import media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_encrypted-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

SECTOR, MIB = 512, 2048
fat_path = os.path.join(ROOT, "fat.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(fat_path)
with open(fat_path, "rb") as fh:
    fat = fh.read()
FAT_SECTORS = -(-len(fat) // SECTOR)
LOCKED_LBA = MIB + -(-FAT_SECTORS // MIB) * MIB + MIB
DISK_SECTORS = LOCKED_LBA + 4 * MIB


def bitlocker() -> bytes:
    head = bytearray(SECTOR)
    head[0:3] = b"\xeb\x58\x90"
    head[3:11] = b"-FVE-FS-"
    head[510:512] = b"\x55\xaa"
    return bytes(head)


def luks() -> bytes:
    head = bytearray(SECTOR)
    head[0:6] = b"LUKS\xba\xbe"
    struct.pack_into(">H", head, 6, 2)
    head[168:204] = str(uuid.UUID(int=7)).encode()
    return bytes(head)


def mbr_disk(locked: bytes) -> bytes:
    disk = bytearray(DISK_SECTORS * SECTOR)
    for slot, (lba, kind, count) in enumerate(((MIB, 0x0C, FAT_SECTORS),
                                               (LOCKED_LBA, 0x07, 4 * MIB - 1))):
        struct.pack_into("<B3sB3sII", disk, 446 + 16 * slot, 0, b"", kind, b"",
                         lba, count)
    disk[510:512] = b"\x55\xaa"
    disk[MIB * SECTOR:MIB * SECTOR + len(fat)] = fat
    disk[LOCKED_LBA * SECTOR:LOCKED_LBA * SECTOR + SECTOR] = locked
    return bytes(disk)


def gpt_disk(locked: bytes) -> bytes:
    disk = bytearray(DISK_SECTORS * SECTOR)
    struct.pack_into("<B3sB3sII", disk, 446, 0, b"", 0xEE, b"", 1, DISK_SECTORS - 1)
    disk[510:512] = b"\x55\xaa"
    linux = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4").bytes_le
    entry = linux + uuid.uuid4().bytes_le + struct.pack(
        "<QQQ", LOCKED_LBA, DISK_SECTORS - 34, 0) + "home".encode("utf-16-le").ljust(72, b"\0")
    header = bytearray(92)
    header[0:8] = b"EFI PART"
    struct.pack_into("<QII", header, 72, 2, 128, 128)
    disk[SECTOR:SECTOR + 92] = header
    disk[2 * SECTOR:2 * SECTOR + len(entry)] = entry
    disk[LOCKED_LBA * SECTOR:LOCKED_LBA * SECTOR + SECTOR] = locked
    return bytes(disk)


def write(name: str, data: bytes) -> str:
    path = os.path.join(ROOT, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def inspected(path: str) -> dict:
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.inspect_image(path)


class DiskInside:
    """The disk an E01 holds, as an E01 reader presents it."""

    def __init__(self, data: bytes):
        self._data, self._pos = data, 0

    def seek(self, offset: int, whence: int = 0) -> int:
        self._pos = offset + (self._pos if whence == 1 else
                              len(self._data) if whence == 2 else 0)
        return self._pos

    def read(self, length: int = -1) -> bytes:
        end = len(self._data) if length is None or length < 0 else self._pos + length
        chunk = self._data[self._pos:end]
        self._pos += len(chunk)
        return chunk

    def tell(self) -> int:
        return self._pos

    def get_size(self) -> int:
        return len(self._data)

    def close(self) -> None:
        pass


def run(path: str, source=None) -> dict:
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.run_extraction(path, os.path.join(ROOT, "out_" + os.path.basename(path)),
                                         source=source, mode="fs")


print("a bare BitLocker volume, as before")
info = inspected(write("bare.img", bitlocker() + bytes(8 * SECTOR)))
right = (info.get("encrypted") or {}).get("kind") == "BitLocker"
ok &= right
print(f"   flagged: {right}")

print("\na disk with a FAT32 system partition and a BitLocker partition")
disk = mbr_disk(bitlocker())
info = inspected(write("laptop.dd", disk))
found = info.get("encrypted_volumes") or []
right = [(v["kind"], v["offset"]) for v in found] == [("BitLocker", LOCKED_LBA * SECTOR)]
ok &= right
print(f"   inspect flags partition 2 at its own offset: {right}")
said = any("Partition" in w and "BitLocker" in w and "other partitions" in w
           for w in info["warnings"])
ok &= said
print(f"   and the warning says the files that come back are not the whole disk: {said}")
summary = run(write("laptop_run.dd", disk))
flagged = [(v["kind"], v["offset"]) for v in summary.get("encrypted_volumes", [])]
ok &= flagged == [("BitLocker", LOCKED_LBA * SECTOR)]
print(f"   a run records it in its summary: {flagged == [('BitLocker', LOCKED_LBA * SECTOR)]}")
recovered = summary.get("total_files", 0) > 0
ok &= recovered
print(f"   while still recovering the readable partition's files: {recovered}")

print("\nthe same disk inside an E01")
container = write("laptop.E01", b"EVF\x09\x0d\x0a\xff\x00" + bytes(SECTOR))
summary = run(container, source=DiskInside(disk))
flagged = [(v["kind"], v["offset"]) for v in summary.get("encrypted_volumes", [])]
ok &= flagged == [("BitLocker", LOCKED_LBA * SECTOR)]
print(f"   read through the disk, not the container's header: "
      f"{flagged == [('BitLocker', LOCKED_LBA * SECTOR)]}")

print("\na GPT disk with a LUKS2 partition")
info = inspected(write("linux.dd", gpt_disk(luks())))
found = info.get("encrypted_volumes") or []
right = [(v["kind"], v["offset"]) for v in found] == [("LUKS2", LOCKED_LBA * SECTOR)] \
    and found[0].get("uuid") == str(uuid.UUID(int=7))
ok &= right
print(f"   flagged with its version and UUID: {right}")

print("\nan ordinary disk")
info = inspected(write("plain.dd", mbr_disk(bytes(SECTOR))))
quiet = not info.get("encrypted") and not any("encrypted" in w for w in info["warnings"])
ok &= quiet
print(f"   nothing said: {quiet}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
