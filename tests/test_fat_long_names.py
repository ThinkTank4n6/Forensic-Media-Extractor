"""FAT long names are trusted only when they belong, and deleted ones come back.

Each long-name entry carries a checksum of the short name it belongs to.
It was never read, so a leftover long name was attached to whatever short
entry followed it -- a wrong name on a file, in evidence. And a deleted
long name was thrown away, so a deleted file came back with only its short
name, first letter lost: "?HOTO_~1.JPG" for "Photo at the park.jpg".
Deleting overwrites the first byte of each entry but not the characters,
so the long name is still there to read.
"""
import collections
import contextlib
import hashlib
import io
import logging
import os
import shutil
import struct
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
ROOT = os.path.join(tempfile.gettempdir(), "fme_long_names-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def lfn(order: int, text: str, checksum: int, last: bool = False) -> bytes:
    chars = (text + "\0").ljust(13, "\uffff")[:13].encode("utf-16-le")
    entry = bytearray(32)
    entry[0] = order | (0x40 if last else 0)
    for n, off in enumerate((1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30)):
        entry[off:off + 2] = chars[2 * n:2 * n + 2]
    entry[11], entry[13] = 0x0F, checksum
    return bytes(entry)


def long_entries(name: str, short11: bytes, deleted: bool = False) -> bytes:
    """The long-name entries for a name, last part first, as FAT stores them."""
    parts = [name[i:i + 13] for i in range(0, len(name), 13)]
    out = b"".join(lfn(n + 1, part, fat32_core.lfn_checksum(short11),
                       last=n == len(parts) - 1)
                   for n, part in reversed(list(enumerate(parts))))
    if deleted:
        out = bytes(0xE5 if k % 32 == 0 else b for k, b in enumerate(out))
    return out


def short(name11: bytes, first: int = 7, size: int = 100, deleted: bool = False) -> bytes:
    raw = bytearray(fixtures.dir_entry(name11[:8].decode(), name11[8:].decode(),
                                       0x20, first, size))
    if deleted:
        raw[0] = 0xE5
    return bytes(raw)


def names(data: bytes) -> list:
    return [e.name for e in fat32_core.parse_dir_entries(data, "/")]


print("long names in a directory")
right = names(long_entries("Photo at the park.jpg", b"PHOTO_~1JPG")
              + short(b"PHOTO_~1JPG")) == ["Photo at the park.jpg"]
ok &= right
print(f"   a live long name is used: {right}")
got = names(long_entries("Photo at the park.jpg", b"PHOTO_~1JPG", deleted=True)
            + short(b"PHOTO_~1JPG", deleted=True))
right = got == ["Photo at the park.jpg"]
ok &= right
print(f"   a deleted long name comes back whole: {right} {got}")
got = names(long_entries("wedding.jpg", b"WEDDIN~1JPG") + short(b"DSC00042JPG"))
right = got == ["DSC00042.JPG"]
ok &= right
print(f"   a leftover long name is not given to another file: {right} {got}")
got = names(long_entries("old name.jpg", b"OLDNAM~1JPG", deleted=True) + short(b"DSC00043JPG"))
right = got == ["DSC00043.JPG"]
ok &= right
print(f"   nor is a deleted one, to a live file in its slot: {right} {got}")
got = names(long_entries("other file.jpg", b"OTHERF~1JPG", deleted=True)
            + short(b"PHOTO_~1JPG", deleted=True))
right = len(got) == 1 and got[0][1:] == "HOTO_~1.JPG"       # its own short name
ok &= right
print(f"   nor a deleted long name to a different deleted file: {right} {got}")
mixed = bytearray(long_entries("Photo at the park.jpg", b"PHOTO_~1JPG"))
mixed[0:32], mixed[32:64] = mixed[32:64], mixed[0:32]      # parts out of order
got = names(bytes(mixed) + short(b"PHOTO_~1JPG"))
right = got == ["PHOTO_~1.JPG"]
ok &= right
print(f"   long-name parts out of order are not used: {right} {got}")
kanji = bytearray(short(b"XABCDEFGJPG"))
kanji[0] = 0x05
parsed = fat32_core.parse_dir_entries(bytes(kanji), "/")
got = [e.name for e in parsed]
# 0xE5 is a Shift-JIS lead byte, so it and the "A" after it make one character.
right = got and got[0].endswith("BCDEFG.JPG") and "\x05" not in got[0] \
    and not parsed[0].is_deleted
ok &= bool(right)
print(f"   a short name starting 0x05 is read as 0xE5, not as deleted: {bool(right)}")

print("\nthe deleted-entry pass, on the FAT32 test card")
card = os.path.join(ROOT, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(card)
with open(card, "rb") as fh:
    image = bytearray(fh.read())
root = (fixtures.first_data_sector + (fixtures.ROOT_CLUSTER - 2)
        * fixtures.SECTORS_PER_CLUSTER) * fixtures.BYTES_PER_SECTOR
old = image[root:root + 32 * 4]                 # IMG_0009 (deleted), DCIM, MOVIES, NOTES
deleted_entry = bytearray(old[0:32])
deleted_entry[0:11] = b"\xe5OLIDA~1JPG"
new_root = (long_entries("Holiday at the lake.jpg", b"HOLIDA~1JPG", deleted=True)
            + bytes(deleted_entry) + old[32:])
image[root:root + len(new_root)] = new_root
with open(card, "wb") as fh:
    fh.write(bytes(image))
reader = media_core.open_image(card)
out = media_core.OutputManager(os.path.join(ROOT, "out"), metadata=False)
try:
    media_core.run_fat_deleted_recovery(
        reader, out, types.SimpleNamespace(image=card, max_file_size=1 << 32),
        collections.defaultdict(int), media_core.SIGNATURES)
finally:
    out.close({})
    reader.close()
rec = next((r for r in out.records if r.sha256 == hashlib.sha256(fixtures.make_jpeg(9)).hexdigest()), None)
right = rec is not None and rec.source_path == "/Holiday at the lake.jpg"
ok &= right
print(f"   the deleted picture comes back by its long name: {right}"
      + (f" ({rec.source_path})" if rec else ""))
right = rec is not None and "not recoverable" not in rec.note
ok &= right
print(f"   with no note of a lost first letter -- the long name has it: {right}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
