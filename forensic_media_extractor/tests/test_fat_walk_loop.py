"""fat32_core.walk() survives a folder that points back up, and free
clusters stop at the end of the volume.

The FAT32 browser's walk recursed into every folder it found, with no
record of where it had been: on a damaged card whose folder entry pointed
back at a folder above it, it went round until Python's recursion limit
stopped it. And free_clusters() counted the FAT's padding entries past the
last real cluster as free space on the disk.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import fat32_core

root = tempfile.mkdtemp(prefix="fme_walk_loop-")
card = os.path.join(root, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(card)
fs = fat32_core.Fat32FileSystem(card)
dcim = next(e for e in fs.list_dir(fs.bs.root_cluster, "/") if e.name == "DCIM")
fs.close()
with open(card, "r+b") as fh:
    at = fs.bs.cluster_to_offset(dcim.first_cluster) + 32 * 5      # after ., .. and three files
    fh.seek(at)
    fh.write(fixtures.dir_entry("LOOP", "", 0x10, fixtures.ROOT_CLUSTER, 0))
fs = fat32_core.Fat32FileSystem(card)
try:
    names = [e.path for e in fs.walk()]
    survived = True
except RecursionError:
    names, survived = [], False
ok = survived and "/DCIM/IMG_0001.JPG" in names
print(f"a folder pointing back at the root: the walk ends, every file listed once: {ok}"
      f" ({len(names)} entries, {len(set(names))} distinct)")
right = len(names) == len(set(names))
ok &= right
limit = fs.fat.cluster_limit
free = list(fs.fat.free_clusters())
right = bool(free) and max(free) < limit and limit <= fixtures.TOTAL_CLUSTERS + 2
ok &= right
print(f"free clusters stop at the volume's last cluster ({limit - 1}), "
      f"not the FAT's padding ({len(fs.fat) - 1}): {right}")
fs.close()
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
