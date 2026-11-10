"""A deleted FAT file is never given another file's bytes.

A deleted entry keeps its name, first cluster and size after the clusters
are released, and anything written since may have taken them. Recovery
read them anyway. When the first cluster had been reused it followed the
FAT chain -- which now describes the file that owns it -- and returned that
file whole, under the deleted file's name, dates and a hash of content that
was never its own. When a cluster further in had been reused, it read
straight through and mixed the newer file's bytes into the middle.

Both deleted-file paths are checked: the deleted-entry pass, which runs on
every image, and the file-system fallback used when TSK is not installed.
Each sees the same volume three ways -- untouched, with the deleted file's
first cluster reused, and with its second cluster reused.
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

import make_media_test_image as fixtures
from fme.engines import media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_fat_overwritten-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

base_path = os.path.join(ROOT, "base.img")
with contextlib.redirect_stdout(io.StringIO()):     # the builder's own listing
    fixtures.main(base_path)
with open(base_path, "rb") as fh:
    base = fh.read()

CLUSTER = fixtures.CLUSTER_SIZE
deleted = fixtures.make_jpeg(9)                     # the fixture's IMG_0009.JPG
first = (base.find(deleted) // fixtures.BYTES_PER_SECTOR
         - fixtures.first_data_sector) // fixtures.SECTORS_PER_CLUSTER + 2
assert len(deleted) > 2 * CLUSTER, "the deleted picture must span three clusters"
other = fixtures.make_jpeg(5)                       # something written since


def at(cluster: int) -> int:
    return (fixtures.first_data_sector * fixtures.BYTES_PER_SECTOR
            + (cluster - 2) * CLUSTER)


def taken(image: bytearray, cluster: int, content: bytes) -> None:
    """Give a cluster to a newer one-cluster file, in both copies of the FAT."""
    for copy in range(fixtures.NUM_FATS):
        fat = (fixtures.RESERVED_SECTORS + copy * fixtures.FAT_SIZE_SECTORS) \
            * fixtures.BYTES_PER_SECTOR
        struct.pack_into("<I", image, fat + 4 * cluster, fixtures.EOC)
    image[at(cluster):at(cluster) + CLUSTER] = content[:CLUSTER].ljust(CLUSTER, b"\x00")


volumes = {"untouched": bytearray(base)}
volumes["first cluster reused"] = bytearray(base)
taken(volumes["first cluster reused"], first, other)
volumes["second cluster reused"] = bytearray(base)
taken(volumes["second cluster reused"], first + 1, b"\xAA" * CLUSTER)


def recovered(name: str, path_kind: str):
    """What each path returns for IMG_0009, and the run's counts."""
    image = os.path.join(ROOT, name.replace(" ", "_") + ".img")
    with open(image, "wb") as fh:
        fh.write(volumes[name])
    out = media_core.OutputManager(os.path.join(ROOT, f"{path_kind}_{name}"),
                                   metadata=False)
    reader = media_core.open_image(image)
    stats = collections.defaultdict(int, volumes=[])
    args = types.SimpleNamespace(image=image, max_file_size=1 << 32,
                                 no_deleted=False, include_system=False,
                                 databases=False)
    try:
        if path_kind == "deleted-entry pass":
            media_core.run_fat_deleted_recovery(reader, out, args, stats,
                                                media_core.SIGNATURES)
        else:
            media_core.run_fat32_fallback(args, out, stats,
                                          media_core.SIGNATURES, reader=reader)
    finally:
        out.close(dict(stats))
        reader.close()
    mine = [r for r in out.records
            if r.deleted and r.source_path.upper().endswith("MG_0009.JPG")]
    found = mine[0] if mine else None
    content = b""
    if found:
        with open(os.path.join(out.outdir, found.out_path), "rb") as fh:
            content = fh.read()
    return found, content, stats


for path_kind in ("deleted-entry pass", "fallback walk"):
    print(f"\n{path_kind}")

    rec, content, stats = recovered("untouched", path_kind)
    whole = rec is not None and content == deleted
    ok &= whole
    print(f"   untouched: recovered whole and exact: {whole}")

    rec, content, stats = recovered("first cluster reused", path_kind)
    refused = rec is None and stats.get("fat_deleted_overwritten") == 1
    ok &= refused
    print(f"   first cluster reused: nothing credited to the deleted name: {refused}")
    leaked = other[:CLUSTER] in content
    ok &= not leaked
    print(f"   the newer file's bytes are not in it: {not leaked}")

    rec, content, stats = recovered("second cluster reused", path_kind)
    cut = rec is not None and content == deleted[:CLUSTER]
    ok &= cut
    print(f"   second cluster reused: only the still-free first cluster kept: {cut}")
    said = rec is not None and f"cluster {first + 1} is in use" in rec.note
    ok &= said
    print(f"   and the note says where and why it was cut: {said}")
    clean = b"\xAA" * 64 not in content
    ok &= clean
    print(f"   none of the newer bytes mixed in: {clean}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
