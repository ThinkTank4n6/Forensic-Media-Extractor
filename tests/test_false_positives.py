"""Regression test for the false positives seen on real evidence.

Case 1: a deleted directory entry named *.MOV whose recorded size is corrupt
        and whose clusters read as zeros. Reported as a 6.8 GB .mov of 0x00.
Case 2: an entry whose size exceeds the image it came from.
Case 3: a real video renamed to something else -- must still be recovered.
Case 4: a real video with a valid ftyp -- must still be recovered.
"""
import tempfile
import os
import tempfile, sys, time, types, json, shutil

GIB = 1024 ** 3

def build_mock(entries, image_size):
    mock = types.ModuleType("pytsk3")
    mock.TSK_IMG_TYPE_EXTERNAL = 1
    mock.TSK_FS_META_TYPE_DIR = 2
    mock.TSK_FS_META_FLAG_UNALLOC = 1
    mock.TSK_FS_NAME_TYPE_DIR = 2
    mock.TSK_VS_PART_FLAG_META = 4
    mock.TSK_FS_TYPE_NTFS = 9
    mock.TSK_VERSION_STR = "4.12.0-mock"
    class Img_Info:
        def __init__(self, url="", type=None): pass
    mock.Img_Info = Img_Info

    class _Name:
        def __init__(self, n, t): self.name = n.encode(); self.type = t
    class _Meta:
        def __init__(self, addr, size, flags):
            self.addr, self.size, self.type, self.flags = addr, size, 1, flags
            self.mtime = self.atime = self.ctime = self.crtime = 1700000000
    class _Info:
        def __init__(self, name, meta): self.name, self.meta = name, meta
    class _Entry:
        def __init__(self, name, meta, produce):
            self.info = _Info(name, meta); self._produce = produce
            self.reads = 0
        def as_directory(self): return []
        def read_random(self, off, size):
            self.reads += 1
            return self._produce(off, size)

    made = []
    for i, (name, size, deleted, produce) in enumerate(entries, start=10):
        made.append(_Entry(_Name(name, 1), _Meta(i, size, 1 if deleted else 0), produce))

    class FS_Info:
        def __init__(self, img, offset=0, **kw):
            if offset != 0: raise IOError("no fs")
            self.info = types.SimpleNamespace(ftype=mock.TSK_FS_TYPE_NTFS)
        def open_dir(self, path="/"): return made
    mock.FS_Info = FS_Info
    class Volume_Info:
        def __init__(self, img): raise IOError("no volume system")
    mock.Volume_Info = Volume_Info
    return mock, made

def zeros(off, size):      return b"\x00" * size
def real_mp4(payload):
    def produce(off, size): return payload[off:off + size]
    return produce

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import make_media_test_image as g
mp4 = g.make_mp4()
IMAGE_SIZE = 8 * GIB

entries = [
    ("DELETED_VID.MOV", 6_800_000_000, True,  zeros),        # case 1
    ("HUGE.MOV",        20 * GIB,      True,  zeros),        # case 2
    ("renamed_clip.dat", len(mp4),     False, real_mp4(mp4)),# case 3
    ("REAL_VIDEO.MP4",  len(mp4),      False, real_mp4(mp4)),# case 4
]
mock, made = build_mock(entries, IMAGE_SIZE)
sys.modules["pytsk3"] = mock
from fme.engines import media_core

class FakeSource:
    """Minimal fat32_core-style ImageSource over a zero-filled volume."""
    def __init__(self): self._pos = 0
    def seek(self, offset, whence=0): self._pos = offset; return offset
    def read(self, length=-1):
        n = 4096 if length < 0 else min(length, 4096)
        return b"\x00" * n
    def tell(self): return self._pos
    def get_size(self): return IMAGE_SIZE
    def close(self): pass

out_dir = os.path.join(tempfile.gettempdir(), "fp_test"); shutil.rmtree(out_dir, ignore_errors=True)
started = time.time()
summary = media_core.run_extraction(__file__, out_dir, mode="fs",
                                    source=FakeSource())
elapsed = time.time() - started
files = {f["out_path"].split("/")[-1]: f for f in summary["files"]}

print(f"ran in {elapsed:.1f}s\n")
print(f"{'case':46} {'result'}")
checks = [
    ("deleted .MOV, 6.8 GB of zeros",
     not any(".mov" in n and f["size"] > 1_000_000 for n, f in files.items())),
    ("entry claiming 20 GB on an 8 GB image",
     summary["skipped_large"] >= 1),
    ("real mp4 renamed to .dat, recovered by magic",
     any(f["size"] == len(mp4) and f["ext"] == "mp4" for f in files.values())),
    ("real .MP4 with valid ftyp, recovered",
     sum(1 for f in files.values() if f["ext"] == "mp4") >= 1
     or summary["duplicates_suppressed"] >= 1),
]
ok = True
for label, passed in checks:
    ok &= passed
    print(f"  {label:44} {'PASS' if passed else 'FAIL'}")
print(f"\nfiles written: {len(files)}  blank dropped: {summary.get('blank_dropped')}  "
      f"oversized skipped: {summary['skipped_large']}")
print("nothing gigabyte-sized written:",
      all(f["size"] < 10_000_000 for f in files.values()))
# mp4, HEIC, AVIF and M4A all begin with the same ftyp box; the brand inside
# it is what says which. Reading the signature alone made every iPhone
# photograph a video -- filtered as a video, scanned for frames, reported as
# a clip. The carver read the brand; every other path that classifies a file
# did not.
print("\nISO base media: the brand decides, not the box")
brands = [
    (b"heic", "image", "heic", "an iPhone photograph"),
    (b"mif1", "image", "heic", "a HEIF photograph"),
    (b"avif", "image", "avif", "an AVIF photograph"),
    (b"M4A ", "audio", "m4a", "an M4A recording"),
    (b"isom", "video", "mp4", "an MP4 video"),
    (b"qt  ", "video", "mov", "a QuickTime movie"),
    (b"3gp4", "video", "3gp", "a 3GP video"),
]
for brand, want_category, want_ext, label in brands:
    head = b"\x00\x00\x00\x20ftyp" + brand + b"\x00\x00\x02\x00" + bytes(400)
    category, ext = media_core.sniff_category(head, "recovered.bin")
    right = category == want_category and ext == want_ext
    ok &= right
    print(f"  {label:24} -> {category}/{ext}"
          + ("" if right else f"   EXPECTED {want_category}/{want_ext}"))

midi, midi_ext = media_core.sniff_category(
    b"MThd\x00\x00\x00\x06\x00\x01\x00\x60" + bytes(200), "x.bin")
ok &= midi == "audio"
print(f"  {'a MIDI file':24} -> {midi}/{midi_ext}")

# Pointing at the folder an .E01 lives in, rather than at the .E01, is the
# commonest way to get a run that finds nothing -- and "0 files" with no
# reason looks like the program failing rather than like the wrong thing
# being chosen.
print("\na folder that holds an acquisition")
import subprocess as _subprocess

advice_root = os.path.join(tempfile.gettempdir(), "false_positive_advice")
shutil.rmtree(advice_root, ignore_errors=True)
holder = os.path.join(advice_root, "DC25-321 Image")
os.makedirs(holder)
for name in ("DC25-321.E01", "DC25-321.E02", "DC25-321.txt"):
    with open(os.path.join(holder, name), "wb") as fh:
        fh.write(b"EVF\x09\x0d\x0a\xff\x00" + bytes(2048))

run_dir = os.path.join(advice_root, "run")
_subprocess.run([sys.executable, "-m", "fme", "extract", "-i", holder,
                 "-o", run_dir], capture_output=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as fh:
    summary = json.load(fh)["summary"]

advised = "acquisition" in (summary.get("advice") or "")
ok &= advised
print(f"  it says the folder holds an acquisition: {advised}")
names = summary.get("points_at_container") or []
ok &= any(name.lower().endswith(".e01") for name in names)
print(f"  and names it: {names}")

# A folder of actual pictures must not be nagged.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_media_test_image as _fixtures

pictures = os.path.join(advice_root, "pictures")
os.makedirs(pictures)
for index in range(3):
    with open(os.path.join(pictures, f"p{index}.jpg"), "wb") as fh:
        fh.write(_fixtures.make_jpeg(10 + index))
clean_dir = os.path.join(advice_root, "clean")
_subprocess.run([sys.executable, "-m", "fme", "extract", "-i", pictures,
                 "-o", clean_dir], capture_output=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(clean_dir, "manifest.json"), encoding="utf-8") as fh:
    clean = json.load(fh)["summary"]
ok &= not clean.get("advice")
print(f"  a folder of pictures is not nagged: {not clean.get('advice')}")

# A transport stream is recognized by one byte repeating every 188. One
# byte matches once in every 256 of anything, so walking those matches in
# Python meant millions of iterations over a disk image -- 77% of a
# measured run, and invisible because it was correct, only slow.
print("\nfinding transport streams without walking every byte")
import time as _time

try:
    import numpy as _np
except ImportError:
    print("  NumPy absent; the fallback walk is what runs here")
else:
    noise = _np.random.default_rng(12).integers(
        0, 255, 8 * 1024 * 1024, dtype=_np.uint8).tobytes()

    # A real stream: the sync byte every 188, which is what must be found.
    packet = bytearray(b"\x47" + bytes(187))
    stream = bytes(packet) * 40
    carrying = noise[:2 * 1024 * 1024] + stream + noise[2 * 1024 * 1024:]

    found = media_core._ts_positions(carrying)
    ok &= found is not None and len(found) >= 1
    print(f"  a stream buried in noise is found: {bool(found)}")

    # And it must not fire on noise alone, or every image carves nonsense.
    ok &= len(media_core._ts_positions(noise)) <= 2
    print(f"  noise alone gives "
          f"{len(media_core._ts_positions(noise))} false start(s)")

    # The array version and the old walk have to agree, or this traded
    # correctness for speed.
    def old_walk(buf):
        out, i = [], buf.find(b"\x47")
        while i >= 0:
            if (i + 377 <= len(buf) and buf[i + 188] == 0x47
                    and buf[i + 376] == 0x47):
                out.append(i)
                i = buf.find(b"\x47", i + media_core._TS_STRIDE)
                continue
            i = buf.find(b"\x47", i + 1)
        return out

    same = old_walk(carrying) == media_core._ts_positions(carrying)
    ok &= same
    print(f"  it agrees with the walk it replaced: {same}")

# A 64-core machine running 8 workers takes four times as long as it
# needs to. The cap was written for a single spinning disk.
import unittest.mock as _mock

with _mock.patch("os.cpu_count", return_value=64):
    many = media_core.default_workers()
ok &= many == 16
print(f"\n  a 64-core machine now starts {many} workers, not 8")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
