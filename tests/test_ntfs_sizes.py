"""A deleted NTFS file is recovered at its own size, not its name's.

Every MFT record carries a file's size twice. $FILE_NAME holds a copy that
Windows updates only when the name changes, so it is often stale; $DATA
holds the real one. Attributes are stored in type order, so $FILE_NAME is
always read first, and its size won. A file that had grown since it was
named came back cut short; one that had shrunk came back with its slack on
the end. Neither hashed as the file -- which is what a Project VIC match,
or anyone re-hashing the file from the image later, depends on.

This adds records to the hand-built NTFS volume from
make_ntfs_test_image.py whose $FILE_NAME and $DATA sizes disagree, and runs
the MFT pass over it the way a real run does.
"""
import collections
import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

from fme.engines import media_core, ntfs_mft

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_ntfs_sizes-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

# The fixture builds its volume when imported, and writes it where argv says.
saved_argv = sys.argv
sys.argv = [saved_argv[0], os.path.join(ROOT, "fixture.dd")]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        import make_ntfs_test_image as ntfs
finally:
    sys.argv = saved_argv

CLUSTER = ntfs.CLUSTER
image = bytearray(ntfs.image)


def content_for(seed: int, size: int) -> bytes:
    """A picture of an exact size: a JPEG header, then filler."""
    rng = __import__("random").Random(seed)
    head = ntfs.g.make_jpeg(seed)[:64]
    return head + bytes(rng.getrandbits(8) for _ in range(size - len(head)))


def plant(number: int, name: str, name_size: int, content: bytes,
          start_cluster: int, resident: bool = False) -> None:
    """A deleted record whose $FILE_NAME claims name_size."""
    if resident:
        data = ntfs.attr_data_resident(content)
    else:
        clusters = -(-len(content) // CLUSTER)
        data = ntfs.attr_data_runs(start_cluster, clusters, len(content))
        at = start_cluster * CLUSTER
        image[at:at + clusters * CLUSTER] = content.ljust(clusters * CLUSTER, b"\xEE")
    raw = ntfs.record(number, False, False, [
        ntfs.attr_standard(1700000000, 1700000000, 1700000000),
        ntfs.attr_filename(name, 6, name_size),
        data])
    at = ntfs.MFT_CLUSTER * CLUSTER + number * ntfs.RECORD
    image[at:at + ntfs.RECORD] = raw


grew = content_for(21, 10_240)        # named at 4 KiB, then written to 10 KiB
shrank = content_for(22, 10_240)      # named at 12,000 bytes, then cut to 10 KiB
small = ntfs.g.make_jpeg(23)[:500]    # resident, with a stale name size
plant(10, "grew.jpg", 4_096, grew, start_cluster=120)
plant(11, "shrank.jpg", 12_000, shrank, start_cluster=130)
plant(12, "small.jpg", 300, small, start_cluster=0, resident=True)
expected = {"grew.jpg": grew, "shrank.jpg": shrank, "small.jpg": small}

volume_path = os.path.join(ROOT, "sizes.dd")
with open(volume_path, "wb") as fh:
    fh.write(bytes(image))

print("parse_record takes the size from $DATA")
boot = ntfs_mft.read_boot(bytes(image[:512]))
for number, name in ((10, "grew.jpg"), (11, "shrank.jpg"), (12, "small.jpg")):
    at = ntfs.MFT_CLUSTER * CLUSTER + number * ntfs.RECORD
    record = ntfs_mft.parse_record(bytes(image[at:at + ntfs.RECORD]),
                                   boot["bytes_per_sector"], number)
    right = record.get("size") == len(expected[name]) \
        and record.get("size_from") == "data"
    ok &= right
    print(f"   {name}: size {record.get('size')}, from {record.get('size_from')}: {right}")

print("\nthe MFT pass recovers each file exactly")
reader = media_core.open_image(volume_path)
out = media_core.OutputManager(os.path.join(ROOT, "out"), metadata=False)
stats = collections.defaultdict(int)
try:
    media_core.run_mft_recovery(reader, out, types.SimpleNamespace(image=volume_path),
                                stats, media_core.SIGNATURES)
finally:
    out.close(dict(stats))
    reader.close()
by_name = {os.path.basename(r.source_path): r for r in out.records}
for name, data in expected.items():
    rec = by_name.get(name)
    exact = rec is not None and rec.size == len(data) \
        and rec.sha256 == hashlib.sha256(data).hexdigest()
    ok &= exact
    print(f"   {name}: {rec.size if rec else 'not recovered'} bytes, hash is the file's: {exact}")

# The fixture's own files, whose two sizes agree, are unchanged.
for name, data in (("tiny_thumbnail.jpg", ntfs.jpeg_resident),
                   ("holiday_photo.png", ntfs.png_big)):
    rec = by_name.get(name)
    same = rec is not None and rec.sha256 == hashlib.sha256(data).hexdigest()
    ok &= same
    print(f"   {name} (sizes agree) still exact: {same}")

print("\nan empty file's allocation is not read as its content")
record = {"resident": None, "size": 0, "size_from": "data",
          "allocated": CLUSTER, "runs": [(120, 1)]}
payload, _ = ntfs_mft.content(record, lambda at, n: bytes(image[at:at + n]), boot)
ok &= payload == b""
print(f"   a zero-byte $DATA reads as nothing: {payload == b''}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
