"""A deleted NTFS file split across MFT records comes back whole and named.

When a file's run list outgrows its 1 KiB record -- a heavily fragmented
file, usually a large video -- NTFS keeps the name, the dates and an
$ATTRIBUTE_LIST in the base record and moves the $DATA runs out to
extension records that point back to it. Every record was judged alone:
the named base record had no $DATA of its own and was dropped, and the
first extension came back as a nameless, undated file cut off where the
next extension took over, with a hash that was not the file's.

Each file here is planted in the hand-built NTFS volume from
make_ntfs_test_image.py in two fragments, with its runs in extension
records, and the MFT pass is run over the volume as a real run does.
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

from fme.engines import media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_attr_list-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

saved_argv = sys.argv
sys.argv = [saved_argv[0], os.path.join(ROOT, "fixture.dd")]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        import make_ntfs_test_image as ntfs
finally:
    sys.argv = saved_argv

CLUSTER = ntfs.CLUSTER
image = bytearray(ntfs.image)
next_cluster = [110]


def place(number: int, raw: bytes) -> None:
    at = ntfs.MFT_CLUSTER * CLUSTER + number * ntfs.RECORD
    image[at:at + ntfs.RECORD] = raw


def extension(number: int, base: int, start: int, clusters: int, vcn: int,
              size: int, base_seq: int = 1, in_use: bool = False) -> bytes:
    data = bytearray(ntfs.attr_data_runs(start, clusters, size if vcn == 0 else 0))
    struct.pack_into("<Q", data, 16, vcn)                 # starting VCN
    struct.pack_into("<Q", data, 24, vcn + clusters - 1)  # last VCN
    raw = bytearray(ntfs.record(number, in_use, False, [bytes(data)]))
    struct.pack_into("<Q", raw, 32, base | (base_seq << 48))
    return bytes(raw)


def plant(name: str, payload_kb: int, base: int, ext_numbers: tuple,
          own_first: bool = False, second_seq: int = 1,
          second_in_use: bool = False) -> bytes:
    """A deleted video in two fragments whose runs sit in extension records."""
    video = ntfs.g.make_mp4(payload_kb)
    clusters = -(-len(video) // CLUSTER)
    half = clusters // 2
    first_at = next_cluster[0]
    second_at = first_at + half + 3                       # a gap: two fragments
    next_cluster[0] = second_at + (clusters - half) + 3
    padded = video.ljust(clusters * CLUSTER, b"\x00")
    image[first_at * CLUSTER:(first_at + half) * CLUSTER] = padded[:half * CLUSTER]
    image[second_at * CLUSTER:(second_at + clusters - half) * CLUSTER] = padded[half * CLUSTER:]
    attrs = [ntfs.attr_standard(1700001000, 1700002000, 1700003000),
             ntfs.attr_filename(name, 6, len(video)),
             ntfs.resident_header(0x20, b"\x00" * 64)]
    if own_first:
        data = bytearray(ntfs.attr_data_runs(first_at, half, len(video)))
        struct.pack_into("<Q", data, 24, half - 1)
        attrs.append(bytes(data))
    place(base, ntfs.record(base, False, False, attrs))
    first_ext, second_ext = ext_numbers
    if not own_first:
        place(first_ext, extension(first_ext, base, first_at, half, 0, len(video)))
    place(second_ext, extension(second_ext, base, second_at, clusters - half, half,
                                len(video), base_seq=second_seq,
                                in_use=second_in_use))
    return video


videos = {
    "trip.mp4": plant("trip.mp4", 40, 10, (11, 12)),
    "reversed.mp4": plant("reversed.mp4", 36, 13, (15, 14)),
    "own_first.mp4": plant("own_first.mp4", 32, 16, (0, 17), own_first=True),
    "reused.mp4": plant("reused.mp4", 28, 18, (19, 20), second_in_use=True),
    "stale.mp4": plant("stale.mp4", 24, 21, (22, 23), second_seq=7),
}

volume = os.path.join(ROOT, "fragmented.dd")
with open(volume, "wb") as fh:
    fh.write(bytes(image))
reader = media_core.open_image(volume)
out = media_core.OutputManager(os.path.join(ROOT, "out"), metadata=False)
stats = collections.defaultdict(int)
try:
    media_core.run_mft_recovery(reader, out, types.SimpleNamespace(image=volume),
                                stats, media_core.SIGNATURES)
finally:
    out.close(dict(stats))
    reader.close()
by_name = {os.path.basename(r.source_path): r for r in out.records}


def exact(name: str) -> bool:
    rec = by_name.get(name)
    return rec is not None and rec.sha256 == hashlib.sha256(videos[name]).hexdigest()


print("a deleted video whose run list is in two extension records")
rec = by_name.get("trip.mp4")
ok &= exact("trip.mp4")
print(f"   recovered by name, whole and exact: {exact('trip.mp4')}")
placed = rec is not None and rec.source_path == "Pictures/trip.mp4" and bool(rec.mtime)
ok &= placed
print(f"   with its folder and dates: {placed}")
said = rec is not None and "held in 2 MFT records" in rec.note
ok &= said
print(f"   and the note says it was joined: {said}")

print("\nthe same, other ways NTFS lays it out")
for name, how in (("reversed.mp4", "extensions in the table out of file order"),
                  ("own_first.mp4", "the first stretch in the base record itself")):
    ok &= exact(name)
    print(f"   {how}: whole and exact: {exact(name)}")

print("\na later stretch's record no longer this file's")
for name, how in (("reused.mp4", "taken by a live file"),
                  ("stale.mp4", "naming the base by an old sequence number")):
    rec = by_name.get(name)
    data = videos[name]
    clusters = -(-len(data) // CLUSTER)
    cut = rec is not None and rec.size == (clusters // 2) * CLUSTER \
        and rec.sha256 == hashlib.sha256(data[:(clusters // 2) * CLUSTER]).hexdigest()
    noted = rec is not None and "part of that list is missing" in rec.note
    ok &= cut and noted
    print(f"   {how}: named, cut off at the gap, and said so: {cut and noted}")

print("\nnothing comes back on its own from an extension record")
nameless = [r for r in out.records if not os.path.basename(r.source_path)]
ok &= not nameless
print(f"   no nameless file: {not nameless}")
fixture_ok = by_name.get("holiday_photo.png") is not None \
    and by_name["holiday_photo.png"].sha256 == hashlib.sha256(ntfs.png_big).hexdigest()
ok &= fixture_ok
print(f"   the fixture's own deleted file is unchanged: {fixture_ok}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
