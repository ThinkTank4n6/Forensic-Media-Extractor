"""MFT recovery works on a volume with 4,096-byte sectors.

NTFS protects each MFT record with an update sequence: the last two bytes
of every 512-byte block are swapped for a marker when the record is
written, and the reader puts them back. The blocks are 512 bytes whatever
the sector size. apply_fixups stepped by the volume's sector size instead,
which is the same thing on a 512-byte drive -- so every test passed -- and
on a 4Kn drive rejected every record, the table's own included. MFT
recovery found nothing on such a volume and gave no sign that it had not
looked.

This takes the hand-built NTFS volume from make_ntfs_test_image.py, whose
records are written the way NTFS writes them, and describes it as a 4Kn
volume: 4,096-byte sectors, one sector per cluster, the same 4 KiB
clusters. Nothing else about the volume changes, so the same deleted
files must come back.
"""
import collections
import contextlib
import hashlib
import io
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

from fme.engines import media_core, ntfs_mft

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_ntfs_4kn-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

saved_argv = sys.argv
sys.argv = [saved_argv[0], os.path.join(ROOT, "fixture.dd")]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        import make_ntfs_test_image as ntfs
finally:
    sys.argv = saved_argv

expected = {"tiny_thumbnail.jpg": hashlib.sha256(ntfs.jpeg_resident).hexdigest(),
            "holiday_photo.png": hashlib.sha256(ntfs.png_big).hexdigest()}


def recover(image: bytes, name: str) -> dict:
    path = os.path.join(ROOT, name + ".dd")
    with open(path, "wb") as fh:
        fh.write(image)
    reader = media_core.open_image(path)
    out = media_core.OutputManager(os.path.join(ROOT, "out_" + name), metadata=False)
    try:
        media_core.run_mft_recovery(reader, out, types.SimpleNamespace(image=path),
                                    collections.defaultdict(int), media_core.SIGNATURES)
    finally:
        out.close({})
        reader.close()
    return {os.path.basename(r.source_path): r.sha256 for r in out.records}


print("the update sequence is undone in 512-byte blocks, whatever the sector size")
for record_size in (1024, 4096):
    ntfs.RECORD = record_size                  # record() sizes itself from this
    raw = bytearray(ntfs.record(30, False, False, [
        ntfs.attr_standard(1, 1, 1),
        ntfs.attr_filename("x.jpg", 5, 600),
        ntfs.attr_data_resident(ntfs.jpeg_resident)]))
    for sector in (512, 4096):
        accepted = ntfs_mft.apply_fixups(bytearray(raw), sector)
        ok &= accepted
        print(f"   {record_size}-byte record, {sector}-byte sectors: accepted: {accepted}")
ntfs.RECORD = 1024

print("\na record that was not written this way is still refused")
good = bytearray(ntfs.record(31, False, False, [ntfs.attr_standard(1, 1, 1)]))
torn = bytearray(good)
torn[1022:1024] = b"\x00\x00"                  # a block that never got its marker
short_count = bytearray(good)
struct.pack_into("<H", short_count, 6, 2)      # claims two blocks, has two plus the marker
for label, raw in (("a block with no marker", torn),
                   ("a count that does not match its size", short_count)):
    refused = not ntfs_mft.apply_fixups(raw, 512)
    ok &= refused
    print(f"   {label}: refused: {refused}")

print("\nthe same volume described with 512-byte sectors")
image_512 = bytes(ntfs.image)
found = recover(image_512, "512")
same = found.get("tiny_thumbnail.jpg") == expected["tiny_thumbnail.jpg"] \
    and found.get("holiday_photo.png") == expected["holiday_photo.png"]
ok &= same
print(f"   both deleted files recovered exactly: {same}")

print("\nand with 4,096-byte sectors")
image_4k = bytearray(ntfs.image)
struct.pack_into("<H", image_4k, 11, 4096)                 # bytes per sector
image_4k[13] = 1                                           # sectors per cluster
struct.pack_into("<Q", image_4k, 40, len(image_4k) // 4096)  # total sectors
boot = ntfs_mft.read_boot(bytes(image_4k[:512]))
read_right = boot.get("bytes_per_sector") == 4096 and boot.get("cluster_size") == 4096
ok &= read_right
print(f"   the boot sector reads as 4Kn with 4 KiB clusters: {read_right}")
found = recover(bytes(image_4k), "4kn")
for name, digest in expected.items():
    right = found.get(name) == digest
    ok &= right
    print(f"   {name} recovered exactly: {right}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
