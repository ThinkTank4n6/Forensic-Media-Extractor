"""Visual similarity: does it group look-alikes and leave strangers alone?

Skipped cleanly when Pillow is not installed, which is also the behavior the
feature itself has.
"""
import tempfile
import json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fme.engines import similarity

if not similarity.available():
    print("Pillow is not installed, so there is nothing to test:")
    print(" ", similarity.UNAVAILABLE)
    sys.exit(0)

from PIL import Image

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "similarity_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(f"{ROOT}/images/jpg")


def photo(seed, size=(640, 480)):
    im = Image.new("RGB", size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = ((x * seed) % 256, (y * 3 + seed) % 256,
                        ((x + y) * seed) % 256)
    return im


base = photo(5)
cases = {
    "original.jpg": (base, 92),
    "resaved.jpg": (base, 55),                       # same picture, requantized
    "grayscale.jpg": (base.convert("L").convert("RGB"), 90),
    "cropped.jpg": (base.crop((0, 0, 600, 450)), 92),
    "unrelated.jpg": (photo(97), 92),
}
records = []
for index, (name, (image, quality)) in enumerate(cases.items()):
    path = f"{ROOT}/images/jpg/{name}"
    image.save(path, "JPEG", quality=quality)
    records.append({"out_path": f"images/jpg/{name}", "category": "image",
                    "ext": "jpg", "size": os.path.getsize(path),
                    "md5": f"{index:032x}", "sha1": f"{index:040x}",
                    "sha256": f"{index:064x}", "engine": "filesystem",
                    "source_path": f"/DCIM/{name}", "signature": "fs-entry",
                    "offset": None, "deleted": False, "note": "",
                    "mtime": "", "atime": "", "crtime": "", "fs_type": "",
                    "partition": "", "inode": "", "instances": 1})
json.dump({"summary": {"image": "test"}, "files": records, "duplicates": []},
          open(f"{ROOT}/manifest.json", "w"))

result = similarity.analyze(f"{ROOT}/manifest.json", max_distance=8)
hashes = {p: int(v, 16) for p, v in result["hashes"].items()}
neighbors = {row["out_path"].split("/")[-1]: row["distance"]
              for row in similarity.similar_to("images/jpg/original.jpg",
                                                hashes, 32)}

print(f"hashed {result['hashed']} of {result['images']} image(s), "
      f"{result['groups']} group(s)\n")
print(f"  {'variant':16} {'distance':>9}  grouped with the original")
ok = True
expectations = {"resaved.jpg": True, "grayscale.jpg": True,
                "cropped.jpg": True, "unrelated.jpg": False}
origin_group = result["membership"].get("images/jpg/original.jpg")
for name, should_group in expectations.items():
    grouped = result["membership"].get(f"images/jpg/{name}") == origin_group \
              and origin_group is not None
    ok &= grouped == should_group
    print(f"  {name:16} {neighbors.get(name, 99):>9}  "
          f"{'yes' if grouped else 'no':<4} "
          f"{'' if grouped == should_group else '  <-- UNEXPECTED'}")

# The unrelated picture must never be pulled in, at any sane threshold.
assert neighbors.get("unrelated.jpg", 99) > 8, "an unrelated image matched"
# Carved images are routinely truncated, and the decoder complains about
# each one. Those complaints belong in the run's own reporting, not printed
# to whatever window the program was started from, where a few thousand of
# them bury everything that matters.
import struct as _struct
import warnings as _warnings

from fme.engines import imaging

broken = os.path.join(ROOT, "truncated.tif")
_pixels = bytes([128]) * (8 * 8 * 3)
_entries = [(256, 3, 1, 8), (257, 3, 1, 8), (258, 3, 1, 8), (259, 3, 1, 1),
            (262, 3, 1, 2), (277, 3, 1, 3), (278, 3, 1, 8),
            (279, 4, 1, len(_pixels))]
_ifd_size = 2 + 12 * (len(_entries) + 2) + 4
_strip = 8 + _ifd_size
_ifd = _struct.pack("<H", len(_entries) + 2)
for _tag, _kind, _count, _value in _entries:
    _ifd += _struct.pack("<HHI", _tag, _kind, _count) + _struct.pack("<I", _value)
_ifd += _struct.pack("<HHII", 273, 4, 1, _strip)
# an entry pointing at data past the end of the file
_ifd += _struct.pack("<HHII", 0x8769, 4, 2, _strip + len(_pixels) + 4096)
_ifd += _struct.pack("<I", 0)
with open(broken, "wb") as fh:
    fh.write(b"II*\x00" + _struct.pack("<I", 8) + _ifd + _pixels)

print("\ndecoding a truncated image")
imaging.reset()
with _warnings.catch_warnings(record=True) as escaped:
    _warnings.simplefilter("always")
    try:
        with imaging.open_image(broken) as image:
            image.load()
            image.getexif()
    except Exception:  # noqa: BLE001 - the point is the warnings, not the read
        pass
leaked = len(escaped)
recorded = imaging.noticed()
ok &= leaked == 0
print(f"  warnings reaching the terminal: {leaked}")
print(f"  counted for the run instead   : {recorded}")
ok &= bool(recorded)

# Every pair within the distance is found -- not most of them.
#
# The bucketing once split each hash into four 16-bit bands at every
# distance. Two hashes 8 bits apart need share no 16-bit band, so at the
# default distance it missed 68% of the pairs 8 bits apart and 10% of those
# only 4 apart, while reporting its groups as if they were complete. The
# grouping is checked here against comparing every pair with every other.
print("\nevery pair within the distance is found")
import random as _random

_random.seed(20261106)
_hashes = {}
for _c in range(30):
    _base = _random.getrandbits(64)
    for _v in range(40):
        _h = _base
        for _bit in _random.sample(range(64), _random.randint(0, 11)):
            _h ^= 1 << _bit
        _hashes[f"c{_c}-{_v}"] = _h
for _i in range(600):
    _hashes[f"r{_i}"] = _random.getrandbits(64)


def _exhaustive(hashes, d):
    names = list(hashes)
    parent = {n: n for n in names}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if bin(hashes[a] ^ hashes[b]).count("1") <= d:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    out = {}
    for n in names:
        out.setdefault(find(n), []).append(n)
    return sorted(sorted(m) for m in out.values() if len(m) > 1)


for _d in (3, 8, 12):
    _got = sorted(sorted(m) for m in similarity.group(_hashes, _d).values())
    _same = _got == _exhaustive(_hashes, _d)
    ok &= _same
    print(f"  distance {_d:>2}: {len(_got)} groups, the same as comparing every "
          f"pair: {_same}")

# One pair at each distance on its own, so nothing is found transitively.
_missed = {}
for _d in range(0, similarity.DEFAULT_DISTANCE + 1):
    _miss = 0
    for _ in range(300):
        _a = _random.getrandbits(64)
        _b = _a
        for _bit in _random.sample(range(64), _d):
            _b ^= 1 << _bit
        _miss += not similarity.group({"a": _a, "b": _b})
    _missed[_d] = _miss
ok &= not any(_missed.values())
print(f"  pairs missed at each distance 0-{similarity.DEFAULT_DISTANCE} "
      f"(of 300 each): {_missed}")

_far = sum(bool(similarity.group({"a": (_a := _random.getrandbits(64)),
                                  "b": _a ^ sum(1 << _x for _x in _random.sample(range(64), 9))}))
           for _ in range(300))
ok &= _far == 0
print(f"  pairs 9 bits apart wrongly grouped at distance 8: {_far}")

# The fast path and the plain one must agree, and a large bucket is compared
# rather than dropped as the old code dropped any over 2,000.
_vals = list(_hashes.values()) * 3
_members = list(range(len(_vals)))
_fast = set(similarity._close_pairs(_members, _vals, 8))
_np_saved, similarity._np = similarity._np, None
_plain = set(similarity._close_pairs(_members, _vals, 8))
similarity._np = _np_saved
ok &= _fast == _plain and len(_members) > 2000
print(f"  a bucket of {len(_members):,}: compared, and both paths find the same "
      f"{len(_fast):,} pairs: {_fast == _plain}")

# Visual stacks inside one large group: compared a row at a time with NumPy,
# and the stacks -- including the member each is named after -- must be the
# ones the pure-Python loop makes.
import random as _random  # noqa: E402
_rng = _random.Random(7)
_bases = [_rng.getrandbits(64) for _ in range(40)]


def _jitter(value, bits):
    for _ in range(bits):
        value ^= 1 << _rng.randrange(64)
    return value


_group = [f"s{i}" for i in range(2500)]
_dh = {m: _jitter(_bases[i % 40], _rng.randrange(5)) for i, m in enumerate(_group)}
_ph = {m: _jitter(_bases[(i * 7) % 40], _rng.randrange(9)) for i, m in enumerate(_group)}
_fast_stacks = similarity.visual_stacks(_dh, _ph, {"g": _group})
_np_saved = similarity._np
similarity._np = None
_plain_stacks = similarity.visual_stacks(_dh, _ph, {"g": _group})
similarity._np = _np_saved
ok &= _fast_stacks == _plain_stacks and len(_fast_stacks) > 1
print(f"  visual stacks in a group of {len(_group):,}: {len(_fast_stacks)} stacks, "
      f"the same with and without NumPy: {_fast_stacks == _plain_stacks}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
