"""A base record's $ATTRIBUTE_LIST decides which extension records count.

v242 joined extension records to their base by the records' back-pointers
alone. The base record's own attribute list says which records hold the
file's $DATA; it is now read when it is resident, so a stale record that
still points back at the base but is no longer listed is left out, and a
listed record that is gone marks the file partial.
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
from fme.engines import media_core

ROOT = tempfile.mkdtemp(prefix="fme_attrlist_read-")
saved = sys.argv
sys.argv = [saved[0], os.path.join(ROOT, "fixture.dd")]
with contextlib.redirect_stdout(io.StringIO()):
    import make_ntfs_test_image as ntfs
sys.argv = saved
C = ntfs.CLUSTER
image = bytearray(ntfs.image)


def place(number, raw):
    at = ntfs.MFT_CLUSTER * C + number * ntfs.RECORD
    image[at:at + ntfs.RECORD] = raw


def extension(number, base, start, clusters, vcn, size):
    data = bytearray(ntfs.attr_data_runs(start, clusters, size if vcn == 0 else 0))
    struct.pack_into("<QQ", data, 16, vcn, vcn + clusters - 1)
    raw = bytearray(ntfs.record(number, False, False, [bytes(data)]))
    struct.pack_into("<Q", raw, 32, base | (1 << 48))
    return bytes(raw)


def listing(*segments):
    out = b""
    for vcn, number in segments:
        entry = struct.pack("<IHBBQQH", 0x80, 32, 0, 26, vcn, number | (1 << 48), 0)
        out += entry.ljust(32, b"\0")
    return out


def plant(name, base, segments, start):
    video = ntfs.g.make_mp4(24)
    clusters = -(-len(video) // C)
    half = clusters // 2
    padded = video.ljust(clusters * C, b"\0")
    image[start * C:(start + half) * C] = padded[:half * C]
    image[(start + half + 2) * C:(start + clusters + 2) * C] = padded[half * C:]
    attrs = [ntfs.attr_standard(1, 1, 1), ntfs.attr_filename(name, 6, len(video)),
             ntfs.resident_header(0x20, listing(*[(0 if i == 0 else half, n) for i, n in enumerate(segments)]))]
    place(base, ntfs.record(base, False, False, attrs))
    place(segments[0], extension(segments[0], base, start, half, 0, len(video)))
    place(segments[1], extension(segments[1], base, start + half + 2, clusters - half, half, len(video)))
    return video, half


whole, _ = plant("listed.mp4", 10, (11, 12), 110)
# A stale record still pointing at base 10, not in its list, claiming VCN 0.
place(13, extension(13, 10, 180, 2, 0, 99999))
cut, half = plant("gone.mp4", 14, (15, 16), 140)
place(16, ntfs.record(16, True, False, [ntfs.attr_standard(1, 1, 1)]))   # reused, in use
volume = os.path.join(ROOT, "v.dd")
with open(volume, "wb") as fh:
    fh.write(bytes(image))
reader = media_core.open_image(volume)
out = media_core.OutputManager(os.path.join(ROOT, "out"), metadata=False)
media_core.run_mft_recovery(reader, out, types.SimpleNamespace(image=volume),
                            collections.defaultdict(int), media_core.SIGNATURES)
out.close({})
reader.close()
by = {os.path.basename(r.source_path): r for r in out.records}
ok = by.get("listed.mp4") is not None and by["listed.mp4"].sha256 == hashlib.sha256(whole).hexdigest()
print(f"a stale record pointing back but not listed is left out; the file is whole: {ok}")
rec = by.get("gone.mp4")
right = rec is not None and "part of that list is missing" in rec.note \
    and rec.sha256 == hashlib.sha256(cut[:half * C]).hexdigest()
ok &= right
print(f"a listed record that has been reused makes the file partial, and says so: {right}")
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
