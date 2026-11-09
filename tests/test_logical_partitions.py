"""Without TSK, volumes in MBR logical partitions are found.

The partition table reader used when TSK is not installed listed the MBR's
four primary entries and skipped the extended partition, so a volume in a
logical partition inside it was never opened. The chain of extended boot
records is now followed, with a guard against a chain that loops.
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
os.environ["FME_NO_TSK"] = "1"
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core

root = tempfile.mkdtemp(prefix="fme_logical-")
card = os.path.join(root, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(card)
with open(card, "rb") as fh:
    vol = fh.read()
sectors = -(-len(vol) // 512)
ext, size = 2048, sectors + 4096
disk = bytearray((ext + 2 * size + 4096) * 512)
struct.pack_into("<B3sB3sII", disk, 446, 0, b"", 0x0F, b"", ext, 2 * size + 2048)
disk[510:512] = b"\x55\xaa"
for n in range(2):                                    # two logical partitions
    ebr = ext + n * size
    struct.pack_into("<B3sB3sII", disk, ebr * 512 + 446, 0, b"", 0x0C, b"", 2048, sectors)
    if n == 0:
        struct.pack_into("<B3sB3sII", disk, ebr * 512 + 462, 0, b"", 0x05, b"", size, size)
    disk[ebr * 512 + 510:ebr * 512 + 512] = b"\x55\xaa"
    disk[(ebr + 2048) * 512:(ebr + 2048) * 512 + len(vol)] = vol
image = os.path.join(root, "logical.dd")
with open(image, "wb") as fh:
    fh.write(bytes(disk))
reader = media_core.open_image(image)
parts = media_core._partition_offsets(reader)
ok = [p["offset"] for p in parts] == [(ext + 2048) * 512, (ext + size + 2048) * 512]
print(f"both logical partitions listed at their offsets: {ok} {[p['label'] for p in parts]}")
loop = bytearray(disk)
struct.pack_into("<B3sB3sII", loop, (ext + size) * 512 + 462, 0, b"", 0x05, b"", 0, size)
with open(image + ".loop", "wb") as fh:
    fh.write(bytes(loop))
looped = media_core._partition_offsets(media_core.open_image(image + ".loop"))
right = len(looped) == 2
ok &= right
print(f"a chain that points back at itself ends: {right}")
out = media_core.OutputManager(os.path.join(root, "out"), metadata=False)
stats = collections.defaultdict(int)
media_core.run_fat_deleted_recovery(reader, out, types.SimpleNamespace(image=image, max_file_size=1 << 32),
                                    stats, media_core.SIGNATURES)
out.close({})
reader.close()
right = stats.get("fat_volumes") == 2
ok &= right
print(f"the deleted-entry pass reads both volumes: {right}")
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
