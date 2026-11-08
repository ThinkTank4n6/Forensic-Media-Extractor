"""A deleted NTFS file is read only as far as it was ever written.

NTFS records how much of a file was written -- the initialized size, or
valid data length -- and Windows shows anything past it as zeros, whatever
the clusters hold. The MFT pass read those clusters as the file, giving it
bytes it never held. And a record whose run list stopped before its size
came back short with nothing to say so.
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

ROOT = tempfile.mkdtemp(prefix="fme_ntfs_vdl-")
saved = sys.argv
sys.argv = [saved[0], os.path.join(ROOT, "fixture.dd")]
with contextlib.redirect_stdout(io.StringIO()):
    import make_ntfs_test_image as ntfs
sys.argv = saved
C = ntfs.CLUSTER
image = bytearray(ntfs.image)


def plant(number, name, written, size, start, clusters, initialized):
    data = bytearray(ntfs.attr_data_runs(start, clusters, size))
    struct.pack_into("<Q", data, 56, initialized)
    image[start * C:start * C + len(written)] = written
    tail = start * C + len(written)
    image[tail:(start + clusters) * C] = b"\xAB" * ((start + clusters) * C - tail)   # stale bytes
    raw = ntfs.record(number, False, False, [ntfs.attr_standard(1, 1, 1),
                                             ntfs.attr_filename(name, 6, size), bytes(data)])
    at = ntfs.MFT_CLUSTER * C + number * ntfs.RECORD
    image[at:at + ntfs.RECORD] = raw


head = ntfs.g.make_jpeg(4)[:4096]
plant(10, "recording.jpg", head, 12288, 120, 3, initialized=4096)
plant(11, "short.jpg", ntfs.g.make_jpeg(5)[:8192], 16384, 130, 2, initialized=16384)
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
ok = True
rec = by.get("recording.jpg")
want = hashlib.sha256(head + b"\x00" * (12288 - 4096)).hexdigest()
right = rec is not None and rec.sha256 == want
ok &= right
print(f"written to 4 KiB of 12 KiB: the rest read as zeros, as Windows shows it: {right}")
right = rec is not None and "had been written" in rec.note
ok &= right
print(f"   and the note says so: {right}")
rec = by.get("short.jpg")
right = rec is not None and rec.size == 8192 and "cut off" in rec.note
ok &= right
print(f"a run list that stops at 8 KiB of 16 KiB: cut off there, and said: {right}")
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
