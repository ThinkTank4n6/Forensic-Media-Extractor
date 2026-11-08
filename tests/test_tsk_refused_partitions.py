"""Every partition The Sleuth Kit refuses is read by this program's FAT reader,
each at its own offset and under its own label.

TSK refuses FAT32 volumes with fewer than 65,525 clusters -- small cards,
and the EFI system partition of many Windows disks. Those volumes are handed
to fat32_core, as (offset, label) tuples; _fat_volumes read only dicts, so
every one became offset 0. The search from the start of the image found the
first FAT volume and credited it to "p0", and every other refused partition
was never opened: its pictures came back from the carve, with no name, no
folder and no partition. Only on a machine with pytsk3.
"""
import collections
import contextlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_tsk_refused-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


print("a partition described either way is read at its own offset")
check("a dict", media_core._volume_entry({"offset": 4096, "label": "p1"}) == (4096, "p1"))
check("a tuple, as run_fs_engine hands over what TSK refused",
      media_core._volume_entry((8192, "p2")) == (8192, "p2"))
check("both partitions are tried, then the start",
      [o for o, _ in media_core._fat_volumes(None, [(4096, "a"), (8192, "b")])]
      == [4096, 8192, 0])
try:
    media_core._volume_entry("4096")
    refused = False
except TypeError:
    refused = True
check("anything else is refused, not read as the start of the image", refused)

# Two small FAT32 partitions, which TSK refuses; the second holds a picture
# the first does not.
card = os.path.join(ROOT, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(card)
with open(card, "rb") as fh:
    a = bytearray(fh.read())
b = bytearray(a)
live = fixtures.make_jpeg()
at = b.find(live)
b[at + len(live) // 2] ^= 0xFF
sectors = -(-len(a) // 512)
second = 2048 + -(-sectors // 2048) * 2048
disk = bytearray((second + sectors + 64) * 512)
for slot, start in enumerate((2048, second)):
    struct.pack_into("<B3sB3sII", disk, 446 + 16 * slot, 0, b"", 0x0C, b"", start, sectors)
disk[510:512] = b"\x55\xaa"
disk[2048 * 512:2048 * 512 + len(a)] = a
disk[second * 512:second * 512 + len(b)] = b
image = os.path.join(ROOT, "disk.dd")
with open(image, "wb") as fh:
    fh.write(bytes(disk))

modes = [("this program's readers", "1")]
if media_core.pytsk3 is not None:
    modes.insert(0, ("TSK, which refuses both", ""))
else:
    print("\nnot checked: pytsk3 is not installed, so TSK refuses nothing here")

counts = {}
for label, no_tsk in modes:
    print(f"\nread with {label}")
    out = os.path.join(ROOT, "out" + no_tsk)
    env = dict(os.environ, FME_NO_TSK=no_tsk) if no_tsk else \
        {k: v for k, v in os.environ.items() if k != "FME_NO_TSK"}
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image, "-o", out,
                    "--mode", "both"], cwd=PROJECT, env=env, capture_output=True,
                   timeout=900)
    with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
        files = json.load(fh)["files"]
    walked = [f for f in files if f["engine"] != "carve"]
    parts = collections.Counter(f.get("partition") for f in walked)
    check("both partitions are walked", len(parts) == 2, str(dict(parts)))
    check("no file is credited to p0", "p0" not in parts)
    pictures = sorted(f.get("partition") for f in walked
                      if (f.get("source_path") or "").endswith("IMG_0001.JPG"))
    check("each partition's IMG_0001.JPG comes back with its path",
          len(pictures) == 2 and pictures[0] != pictures[1], str(pictures))
    counts[label] = (len(walked), sum(1 for f in files if f["engine"] == "carve"),
                     tuple(sorted(parts)))

if len(counts) == 2:
    first, second_ = counts.values()
    check("\nTSK's machine and the other recover the same files the same way",
          first == second_, f"{first} against {second_} (walked, carved, partition labels)")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
