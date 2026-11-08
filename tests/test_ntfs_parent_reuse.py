"""A deleted NTFS file's path never runs through a folder it was not in.

A file's record points to its folder by record number and by the sequence
number that record had at the time. NTFS reuses records, and bumps the
sequence number when it does, so the number alone can now name a
different folder. Path reconstruction ignored the sequence number: a file
from a deleted folder whose record had since gone to a new folder was
reported as being in the new one -- a specific, confident path the file
never had, in the manifest, the reports and every export.

The records here are built with make_ntfs_test_image.py's helpers. The
first checks drive parse_record and path_of directly; the last runs the
MFT pass over a volume with a reused folder record, the way a real run
does.
"""
import collections
import contextlib
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
ROOT = os.path.join(tempfile.gettempdir(), "fme_ntfs_parent-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

saved_argv = sys.argv
sys.argv = [saved_argv[0], os.path.join(ROOT, "fixture.dd")]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        import make_ntfs_test_image as ntfs
finally:
    sys.argv = saved_argv

PICTURE = ntfs.g.make_jpeg(31)[:700]       # small enough to be resident


def raw_record(number, name, parent, parent_seq, sequence, in_use, directory,
               content=None):
    attrs = [ntfs.attr_standard(1700000000, 1700000000, 1700000000),
             # The fixture ORs sequence 1 into every reference, so the
             # sequences used in references here are all odd.
             ntfs.attr_filename(name, parent | (parent_seq << 48), 0)]
    if content is not None:
        attrs.append(ntfs.attr_data_resident(content))
    raw = bytearray(ntfs.record(number, in_use, directory, attrs))
    struct.pack_into("<H", raw, 16, sequence)
    return bytes(raw)


def parsed(*args, **kwargs):
    return ntfs_mft.parse_record(raw_record(*args, **kwargs), 512, args[0])


def path(file_rec, *folders):
    return ntfs_mft.path_of(file_rec, {f["number"]: f for f in folders})


photo = parsed(77, "photo.jpg", 40, 3, 2, False, False, PICTURE)

print("each record keeps its sequence number, and its parent's")
ok &= photo["parent"] == 40 and photo.get("parent_sequence") == 3
print(f"   photo.jpg points to record 40, sequence 3: "
      f"{photo['parent'] == 40 and photo.get('parent_sequence') == 3}")

print("\na folder record reused since the file was deleted")
reused = parsed(40, "Kids_Homework", 5, 5, 4, True, True)
got = path(photo, reused)
ok &= got == "[deleted folder]/photo.jpg"
print(f"   {got!r}: not credited to the new folder: {got == '[deleted folder]/photo.jpg'}")

print("\nthe folder deleted too, its record not reused")
same = parsed(40, "Evidence_Locker", 5, 5, 3, False, True)
freed = parsed(40, "Evidence_Locker", 5, 5, 4, False, True)
for label, folder in (("sequence unchanged", same),
                      ("sequence advanced when freed", freed)):
    got = path(photo, folder)
    right = got == "Evidence_Locker/photo.jpg"
    ok &= right
    print(f"   {label}: {got!r}: {right}")
twice = parsed(40, "Evidence_Locker", 5, 5, 5, False, True)
got = path(photo, twice)
ok &= got == "[deleted folder]/photo.jpg"
print(f"   reused and freed again: {got!r}: {got == '[deleted folder]/photo.jpg'}")

print("\na live folder whose sequence matches")
live = parsed(40, "Pictures", 5, 5, 3, True, True)
got = path(photo, live)
ok &= got == "Pictures/photo.jpg"
print(f"   {got!r}: {got == 'Pictures/photo.jpg'}")

print("\nthe check holds at every level, not just the first")
child = parsed(41, "Trip", 40, 3, 1, False, True)
nested = parsed(78, "beach.jpg", 41, 1, 2, False, False, PICTURE)
got = path(nested, child, reused)
ok &= got == "[deleted folder]/Trip/beach.jpg"
print(f"   grandparent reused: {got!r}: {got == '[deleted folder]/Trip/beach.jpg'}")

print("\nthe MFT pass reports the safe path")
image = bytearray(ntfs.image)
for number, raw in ((20, raw_record(20, "Kids_Homework", 5, 1, 4, True, True)),
                    (21, raw_record(21, "lost.jpg", 20, 3, 2, False, False,
                                    PICTURE))):
    at = ntfs.MFT_CLUSTER * ntfs.CLUSTER + number * ntfs.RECORD
    image[at:at + ntfs.RECORD] = raw
volume = os.path.join(ROOT, "reused.dd")
with open(volume, "wb") as fh:
    fh.write(bytes(image))
reader = media_core.open_image(volume)
out = media_core.OutputManager(os.path.join(ROOT, "out"), metadata=False)
try:
    media_core.run_mft_recovery(reader, out, types.SimpleNamespace(image=volume),
                                collections.defaultdict(int), media_core.SIGNATURES)
finally:
    out.close({})
    reader.close()
paths = {os.path.basename(r.source_path): r.source_path for r in out.records}
got = paths.get("lost.jpg")
ok &= got == "[deleted folder]/lost.jpg"
print(f"   lost.jpg: {got!r}: {got == '[deleted folder]/lost.jpg'}")
got = paths.get("holiday_photo.png")
ok &= got == "Pictures/holiday_photo.png"
print(f"   the fixture's own file is unchanged: {got!r}: "
      f"{got == 'Pictures/holiday_photo.png'}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
