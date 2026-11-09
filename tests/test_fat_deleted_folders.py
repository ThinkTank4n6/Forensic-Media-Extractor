"""Files in a deleted FAT folder come back with their names.

Deleting a folder on FAT marks the folder's own entry deleted and every
entry inside it too, and those inner entries usually survive intact: name,
size, first cluster, dates. Both deleted-file paths stepped only into
folders that were not deleted, so they never read them. The carve still
found the pictures, as recovered_NNNN files with no name, no path and no
dates, and the folder itself vanished from the report.

A deleted folder's clusters can be reused like a deleted file's, so its
contents are believed only while the FAT still marks its first cluster
free and that cluster still reads as the folder -- a "." entry pointing at
itself, then "..". This takes the FAT32 test card, deletes its DCIM folder
the way FAT does, and checks both paths, a full run, and the two ways the
folder's cluster can have been taken since.
"""
import collections
import contextlib
import glob
import hashlib
import io
import json
import logging
import os
import random
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

import make_media_test_image as fixtures
from fme.engines import fat32_core, media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_deleted_folders-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

base_path = os.path.join(ROOT, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(base_path)
with open(base_path, "rb") as fh:
    base = bytearray(fh.read())


class Source(io.BytesIO):
    def get_size(self):
        return len(self.getvalue())


def volume_of(image: bytes):
    vol = fat32_core.Fat32FileSystem.__new__(fat32_core.Fat32FileSystem)
    vol.source = Source(bytes(image))
    vol.bs = fat32_core.parse_boot_sector(vol.source)
    vol.fat = fat32_core.FatTable(vol.source, vol.bs)
    return vol


vol = volume_of(base)
CLUSTER = vol.bs.cluster_size
dcim = next(e for e in vol.list_dir(vol.bs.root_cluster, "/") if e.name == "DCIM")
inside = [e for e in vol.list_dir(dcim.first_cluster, "/DCIM/") if not e.is_dir]
expected = {e.name.upper(): hashlib.sha256(vol.read_chain_bytes(e.first_cluster, e.size)).hexdigest()
            for e in inside}


def set_fat(image: bytearray, cluster: int, value: int) -> None:
    for copy in range(fixtures.NUM_FATS):
        at = (fixtures.RESERVED_SECTORS + copy * fixtures.FAT_SIZE_SECTORS) \
            * fixtures.BYTES_PER_SECTOR + 4 * cluster
        struct.pack_into("<I", image, at, value)


def mark_deleted(image: bytearray, dir_cluster: int, names: set) -> None:
    start = vol.bs.cluster_to_offset(dir_cluster)
    for at in range(start, start + CLUSTER, 32):
        raw = image[at:at + 32]
        if raw[11] == 0x0F or raw[0] in (0, 0xE5):
            continue
        short = raw[0:8].decode("ascii", "replace").rstrip()
        ext = raw[8:11].decode("ascii", "replace").rstrip()
        if (short + ("." + ext if ext else "")).upper() in names:
            image[at] = 0xE5


def deleted_folder_card() -> bytearray:
    """The card with DCIM deleted as FAT deletes a folder."""
    image = bytearray(base)
    mark_deleted(image, dcim.first_cluster, set(expected))
    mark_deleted(image, vol.bs.root_cluster, {"DCIM"})
    for entry in inside + [dcim]:
        for cluster in list(vol.fat.chain(entry.first_cluster)):
            set_fat(image, cluster, 0)
    return image


def write(name: str, image: bytes) -> str:
    path = os.path.join(ROOT, name)
    with open(path, "wb") as fh:
        fh.write(bytes(image))
    return path


def deleted_pass(path: str):
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
    return out.records, stats


def fallback_walk(path: str):
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
    return out.records


def from_folder(records) -> dict:
    """Recovered files credited to the deleted folder, by hash."""
    found = {}
    for rec in records:
        parts = rec.source_path.split("/")
        if len(parts) >= 3 and parts[-2][1:].upper() == "CIM" and parts[-2][0] in "?_\ufffd":
            found[rec.sha256] = rec
    return found


def same_files(found: dict) -> bool:
    return set(found) == set(expected.values())


card = write("deleted_folder.img", deleted_folder_card())

print(f"DCIM deleted, holding {sorted(expected)}")
records, stats = deleted_pass(card)
found = from_folder(records)
right = same_files(found)
ok &= right
print(f"   deleted-entry pass: all three, by name, exact: {right}")
noted = bool(found) and all("folder that had itself been deleted" in r.note
                            for r in found.values())
ok &= noted
print(f"   and each note says the folder was deleted too: {noted}")
counted = stats.get("fat_deleted_in_deleted_folders") == len(expected)
ok &= counted
print(f"   counted as found in deleted folders: {counted}")
walked = same_files(from_folder(fallback_walk(card)))
ok &= walked
print(f"   no-TSK file-system walk: all three, by name, exact: {walked}")

subprocess.run([sys.executable, "-m", "fme", "extract", "-i", card, "-o", card + "_full"],
               capture_output=True, cwd=PROJECT, timeout=900)
manifest = glob.glob(os.path.join(card + "_full", "**", "manifest.json"), recursive=True)
paths = []
if manifest:
    with open(manifest[0], encoding="utf-8") as fh:
        paths = [f.get("source_path", "") for f in json.load(fh)["files"]]
named = sum(1 for p in paths if "CIM/" in p.upper()) == len(expected)
ok &= named
print(f"   a full run lists them under the deleted folder: {named}")

print("\nthe deleted folder's first cluster taken by something written since")
taken = deleted_folder_card()
set_fat(taken, dcim.first_cluster, fixtures.EOC)
records, _ = deleted_pass(write("folder_reused.img", taken))
right = not from_folder(records)
ok &= right
print(f"   in use again: nothing credited to the folder: {right}")

overwritten = deleted_folder_card()
at = vol.bs.cluster_to_offset(dcim.first_cluster)
overwritten[at:at + CLUSTER] = bytes(random.Random(5).getrandbits(8) for _ in range(CLUSTER))
records, _ = deleted_pass(write("folder_overwritten.img", overwritten))
right = not from_folder(records)
ok &= right
print(f"   free but no longer a folder: nothing read from it: {right}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
