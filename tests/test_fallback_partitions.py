"""Without TSK, the file-system walk reads every FAT partition.

The no-TSK walk opened only the first FAT32 partition the MBR listed and
labelled it "p0", so on a disk with more than one FAT partition the live
files of the others were left to the carve, unnamed. It now walks every FAT
volume, each credited to its own partition.
"""
import collections
import contextlib
import io
import logging
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core

root = tempfile.mkdtemp(prefix="fme_fallback_parts-")
card = os.path.join(root, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(card)
with open(card, "rb") as fh:
    a = bytearray(fh.read())
b = bytearray(a)
live = fixtures.make_jpeg()
at = b.find(live)
b[at + len(live) // 2] ^= 0xFF                 # the second card's picture differs
sectors = -(-len(a) // 512)
second = 2048 + -(-sectors // 2048) * 2048
disk = bytearray((second + sectors + 64) * 512)
for slot, start in enumerate((2048, second)):
    struct.pack_into("<B3sB3sII", disk, 446 + 16 * slot, 0, b"", 0x0C, b"", start, sectors)
disk[510:512] = b"\x55\xaa"
disk[2048 * 512:2048 * 512 + len(a)] = a
disk[second * 512:second * 512 + len(b)] = b
image = os.path.join(root, "disk.dd")
with open(image, "wb") as fh:
    fh.write(bytes(disk))

ok = True
# Without TSK (the MBR read here), and with it where it is installed: TSK
# lists the gap before the first partition as "Unallocated" at offset 0, and
# trying that first credited the first partition's files to "p0".
modes = [("1", ["p1", "p2"])]
if media_core.pytsk3 is not None:
    # The same labels: since v2026.11.307 a partition TSK lists is named by
    # its slot in the table, as when the table is read here.
    modes.append(("", ["p1", "p2"]))
for no_tsk, expected in modes:
    os.environ["FME_NO_TSK"] = no_tsk
    reader = media_core.open_image(image)
    out = media_core.OutputManager(os.path.join(root, "out" + no_tsk), metadata=False)
    stats = collections.defaultdict(int, volumes=[])
    args = types.SimpleNamespace(image=image, max_file_size=1 << 32, no_deleted=False,
                                 include_system=False, databases=False)
    media_core.run_fat32_fallback(args, out, stats, media_core.SIGNATURES, reader=reader)
    out.close({})
    reader.close()
    pictures = [(r.partition, r.source_path) for r in out.records
                if r.source_path.endswith("IMG_0001.JPG")]
    good = sorted(p for p, _ in pictures) == expected
    ok &= good
    how = "partitions read without TSK" if no_tsk else "partitions listed by TSK"
    print(f"{how}: IMG_0001.JPG from both partitions, each under its own "
          f"label: {good} {pictures}")
    right = len(stats["volumes"]) == 2
    ok &= right
    print(f"  both volumes listed: {right}")
os.environ.pop("FME_NO_TSK", None)
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
