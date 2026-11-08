"""A deleted FAT folder is read past its first cluster.

A deleted folder's chain is freed, so only its first cluster was read
(v240): with 4 KiB clusters, files listed past about the 126th entry were
left to the carve, unnamed. The clusters after the first are now read
while the FAT marks them free and they read as directory entries, up to the
folder's end mark. A cluster that has been reused for something else ends
the folder there.
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
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fx
from fme.engines import fat32_core, media_core

root = tempfile.mkdtemp(prefix="fme_big_folder-")
card = os.path.join(root, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fx.main(card)
with open(card, "rb") as fh:
    image = bytearray(fh.read())
C = fx.CLUSTER_SIZE
at = lambda c: (fx.first_data_sector + (c - 2) * fx.SECTORS_PER_CLUSTER) * fx.BYTES_PER_SECTOR
FOLDER, FIRST_FILE, COUNT = 3000, 3010, 140
base = fx.make_jpeg(2)
digests = {}
entries = fx.dir_entry(".", "", 0x10, FOLDER, 0) + fx.dir_entry("..", "", 0x10, 0, 0)
for n in range(COUNT):
    pic = bytearray(base)
    pic[200:204] = struct.pack(">I", n)            # each picture its own
    image[at(FIRST_FILE + n):at(FIRST_FILE + n) + len(pic)] = pic
    name = f"IMG{n:05d}"
    digests[f"?MG{n:05d}.JPG"] = hashlib.sha256(pic).hexdigest()
    raw = bytearray(fx.dir_entry(name, "JPG", 0x20, FIRST_FILE + n, len(pic)))
    raw[0] = 0xE5
    entries += bytes(raw)
assert len(entries) > C, "the folder must need two clusters"
image[at(FOLDER):at(FOLDER) + len(entries)] = entries
folder_entry = bytearray(fx.dir_entry("BIGDIR", "", 0x10, FOLDER, 0))
folder_entry[0] = 0xE5
image[at(fx.ROOT_CLUSTER) + 32 * 4:at(fx.ROOT_CLUSTER) + 32 * 5] = folder_entry


def run(img, name):
    path = os.path.join(root, name)
    with open(path, "wb") as fh:
        fh.write(bytes(img))
    reader = media_core.open_image(path)
    out = media_core.OutputManager(path + "_out", metadata=False)
    media_core.run_fat_deleted_recovery(reader, out, types.SimpleNamespace(image=path, max_file_size=1 << 32),
                                        collections.defaultdict(int), media_core.SIGNATURES)
    out.close({})
    reader.close()
    return {os.path.basename(r.source_path): r.sha256 for r in out.records if "IGDIR" in r.source_path}


found = run(image, "big.img")
ok = len(found) == COUNT and all(found.get(k) == v for k, v in digests.items())
print(f"all {COUNT} files of a two-cluster deleted folder, by name: {ok} ({len(found)})")
reused = bytearray(image)
reused[at(FOLDER + 1):at(FOLDER + 1) + C] = bytes(range(256)) * (C // 256)   # second cluster overwritten
found = run(reused, "reused.img")
right = 0 < len(found) < COUNT and all(found.get(k) == digests[k] for k in found)
ok &= right
print(f"its second cluster overwritten: the first cluster's files only, none invented: {right} ({len(found)})")
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
