# Reference copy of fme/engines/similarity.py as of v2026.11.293 (its
# hashing is unchanged since v2026.11.292). Read only by
# tests/test_similarity_kept.py, to prove kept hashes equal what this code
# computes. Not imported by the program.
"""
similarity.py -- find visually similar images. Optional: needs Pillow.

A cryptographic hash answers "are these the same bytes". It cannot answer
"are these the same picture", because re-saving a JPEG, resizing it, or
screenshotting it changes every byte while leaving the image recognizable.
That question needs the pixels, which means decoding the image, which is why
this is the one part of the tool with a dependency outside the standard
library.

The method is a difference hash: decode, convert to grayscale, shrink to
9x8, and record for each row whether each pixel is brighter than the one to
its right. That yields 64 bits describing the image's structure rather than
its bytes. Two images are similar when their hashes differ in few bits --
the Hamming distance -- and the threshold is a judgment, not a fact, which
is why it is adjustable and why the distance is reported alongside every
match rather than hidden behind a yes or no.

    python3 similarity.py -m out/manifest.json --distance 8 --csv similar.csv

Without Pillow installed, everything here reports that it is unavailable and
nothing else in the tool is affected.

Two things to be clear about. Similar is not identical: a match means the
images look alike to an algorithm, and an examiner decides what that means.
And decoding image data from an exhibit means handing untrusted input to an
image library, so keep Pillow current.
"""

from __future__ import annotations

# Allow this file to be run directly as well as imported. Run as a script
# there is no package context, so the project root goes on the path first.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import csv
import json
import os
import sys
from typing import Iterator, Optional

__version__ = "1.0.0"

HASH_SIZE = 8                      # 8x8 comparisons -> 64 bits
PHASH_SIZE = 32                    # DCT input; the top-left 8x8 is kept
# Below this much variation in the shrunken image there is no structure to
# compare: gradients, flat screenshots, solid color tiles. They would
# otherwise all look alike to any perceptual hash and clump together.
FEATURELESS_VARIANCE = 40.0
DEFAULT_DISTANCE = 8               # bits of difference still called similar
MAX_DECODE_BYTES = 128 * 1024 * 1024

from fme.engines import imaging

try:
    from PIL import Image
    PILLOW = True
    try:
        # iPhones write HEIC, and Pillow cannot read it on its own. Without
        # this, every photograph off a modern phone is a file with no
        # thumbnail, no perceptual hash and no preview -- which is most of
        # the camera roll on a current exhibit.
        import pillow_heif
        pillow_heif.register_heif_opener()
        HEIF = True
    except ImportError:
        HEIF = False
    try:
        PILLOW_VERSION = Image.__version__
    except AttributeError:         # very old Pillow
        PILLOW_VERSION = "unknown"
except ImportError:                # pragma: no cover - the whole point
    Image = None
    PILLOW = False
    HEIF = False
    PILLOW_VERSION = ""

UNAVAILABLE = ("Visual similarity needs the Pillow imaging library, which is "
               "not installed. Install it with: pip install pillow")


def available() -> bool:
    return PILLOW


def status() -> dict:
    return {"available": PILLOW, "version": PILLOW_VERSION,
            "heif": HEIF,
            "message": "" if PILLOW else UNAVAILABLE}


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------
def dhash(path: str, size: int = HASH_SIZE) -> Optional[int]:
    """A 64-bit difference hash, or None if the image cannot be read.

    Anything Pillow refuses -- a truncated carve, a file that is not really
    an image, a format it does not support -- returns None rather than
    raising, because a recovery run will contain plenty of all three.
    """
    if not PILLOW:
        return None
    try:
        if os.path.getsize(path) > MAX_DECODE_BYTES:
            return None
        with imaging.open_image(path) as im:
            im.draft("L", (size * 8, size * 8))   # let JPEG decode smaller
            im = im.convert("L").resize((size + 1, size), Image.LANCZOS)
            pixels = list(im.getdata())
    except Exception:  # noqa: BLE001 - malformed input is expected here
        return None

    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits <<= 1
            if pixels[base + col] > pixels[base + col + 1]:
                bits |= 1
    return bits


def _dct_1d(values: list) -> list:
    """One-dimensional DCT-II, written out rather than pulled from numpy.

    A difference hash compares neighboring pixels, so it describes local
    contrast. A perceptual hash works on the low frequencies of the image
    instead, which survives rescaling and requantization that dHash notices.
    Requiring both to agree is what separates "the same picture" from
    "similar enough to look at".
    """
    import math
    n = len(values)
    out = []
    factor = math.pi / (2 * n)
    for k in range(n):
        total = 0.0
        for i, value in enumerate(values):
            total += value * math.cos((2 * i + 1) * k * factor)
        out.append(total * (0.5 ** 0.5 if k == 0 else 1.0))
    return out


def phash(path: str, size: int = PHASH_SIZE) -> Optional[int]:
    """A 64-bit perceptual hash from the low-frequency DCT coefficients."""
    if not PILLOW:
        return None
    try:
        if os.path.getsize(path) > MAX_DECODE_BYTES:
            return None
        with imaging.open_image(path) as im:
            im.draft("L", (size, size))
            im = im.convert("L").resize((size, size), Image.LANCZOS)
            pixels = list(im.getdata())
    except Exception:  # noqa: BLE001
        return None

    rows = [pixels[r * size:(r + 1) * size] for r in range(size)]
    rows = [_dct_1d(row) for row in rows]
    columns = [_dct_1d([rows[r][c] for r in range(size)]) for c in range(size)]
    block = [columns[c][r] for r in range(HASH_SIZE) for c in range(HASH_SIZE)]

    ordered = sorted(block[1:])                # drop the DC term
    median = ordered[len(ordered) // 2]
    bits = 0
    for value in block:
        bits = (bits << 1) | (1 if value > median else 0)
    return bits


def is_featureless(path: str) -> bool:
    """True when an image has too little structure to compare meaningfully."""
    if not PILLOW:
        return False
    try:
        with imaging.open_image(path) as im:
            im.draft("L", (32, 32))
            pixels = list(im.convert("L").resize((16, 16), Image.LANCZOS).getdata())
    except Exception:  # noqa: BLE001
        return True
    mean = sum(pixels) / len(pixels)
    variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
    if variance < FEATURELESS_VARIANCE:
        return True

    # A gradient passes the variance test -- it spans the whole range -- but
    # every pixel is brighter than the one beside it, so its difference hash
    # is all ones or all zeros. Nothing with that little contrast structure
    # can be told apart from any other gradient.
    value = dhash(path)
    if value is None:
        return True
    set_bits = bin(value).count("1")
    return set_bits < 4 or set_bits > 60


def distance(a: int, b: int) -> int:
    """How many bits two hashes differ in."""
    return bin(a ^ b).count("1")


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------
def hash_records(records: list, base_dir: str, progress=None) -> tuple:
    """Hash every image. Returns (dhashes, phashes, featureless paths)."""
    dhashes, phashes, featureless = {}, {}, []
    total = len(records)
    for index, rec in enumerate(records):
        if rec.get("category") != "image":
            continue
        path = os.path.join(base_dir, rec.get("out_path", ""))
        value = dhash(path)
        if value is None:
            continue
        if is_featureless(path):
            featureless.append(rec["out_path"])
            continue
        dhashes[rec["out_path"]] = value
        perceptual = phash(path)
        if perceptual is not None:
            phashes[rec["out_path"]] = perceptual
        if progress and index % 200 == 0:
            progress(index, total, len(dhashes))
    if progress:
        progress(total, total, len(dhashes))
    return dhashes, phashes, featureless


# How many rows of a bucket are compared against the whole bucket at once. A
# bucket of k members costs k * PAIR_ROWS * 8 bytes per block, so this bounds
# memory however lopsided the hashes are: 256 x 50,000 is about 100 MB.
PAIR_ROWS = 256

try:
    import numpy as _np
except ImportError:                 # pragma: no cover - NumPy is optional
    _np = None


def _popcount64(x):
    """Bits set in each element of a uint64 array.

    np.bitwise_count (NumPy 2.0) is the processor's own instruction. Older
    NumPy, which requirements.txt still accepts, gets the classic SWAR
    reduction: five whole-array operations, no lookup. The first version of
    this used a 256-entry byte table, which was correct and 27 times slower
    (13.7 ms against 0.5 ms per 256 x 1,250 block, measured) -- the
    difference between grouping 160,000 images in seconds and in minutes.
    """
    if hasattr(_np, "bitwise_count"):
        return _np.bitwise_count(x)
    x = x - ((x >> _np.uint64(1)) & _np.uint64(0x5555555555555555))
    x = (x & _np.uint64(0x3333333333333333)) + \
        ((x >> _np.uint64(2)) & _np.uint64(0x3333333333333333))
    x = (x + (x >> _np.uint64(4))) & _np.uint64(0x0F0F0F0F0F0F0F0F)
    return (x * _np.uint64(0x0101010101010101)) >> _np.uint64(56)


def bands(max_distance: int, bits: int = 64) -> list:
    """(shift, width) of each band the hash is split into for bucketing.

    The number of bands is the whole correctness argument, so it is worth
    stating. Two hashes within `max_distance` bits of each other differ in at
    most `max_distance` bands; with `max_distance + 1` bands at least one band
    is therefore identical in both, so every pair that should be compared
    shares at least one bucket. That is the pigeonhole principle, and it
    makes the grouping exact rather than approximately right.

    This used to be four 16-bit bands at every distance. Four bands only
    guarantee a shared band up to 3 bits of difference; at the default of 8
    it missed 68% of the pairs at distance 8, 53% at 7, 42% at 6 and 10% at 4
    (measured on random 64-bit hashes), and nothing said so. The approach --
    narrower bands, compared in bulk -- follows GLEAPP's dedupe.py (MIT,
    fme/vendor/LICENSE-gleapp), which uses eight 8-bit bands; eight is still
    one short at its own threshold of 10, so the count here is derived from
    the distance instead of fixed.
    """
    count = max(1, min(max_distance + 1, bits))
    edges = [round(i * bits / count) for i in range(count + 1)]
    return [(edges[i], edges[i + 1] - edges[i]) for i in range(count)]


def _close_pairs(members: list, values: list, max_distance: int):
    """(i, j) index pairs within a bucket, i < j, at most max_distance apart."""
    if _np is not None and len(members) > 32:
        vals = _np.array([values[m] for m in members], dtype=_np.uint64)
        for start in range(0, len(vals), PAIR_ROWS):
            # Only the upper triangle: a row is compared with the columns
            # after it, so each pair is counted once rather than twice.
            head = vals[start:start + PAIR_ROWS, None]
            counts = _popcount64(head ^ vals[None, start:])
            rows, cols = _np.nonzero(counts <= max_distance)
            cols = cols + start
            rows = rows + start
            keep = rows < cols
            for a, b in zip(rows[keep].tolist(), cols[keep].tolist()):
                yield members[a], members[b]
        return
    for x in range(len(members)):
        va = values[members[x]]
        for y in range(x + 1, len(members)):
            if bin(va ^ values[members[y]]).count("1") <= max_distance:
                yield members[x], members[y]


def group(hashes: dict, max_distance: int = DEFAULT_DISTANCE) -> dict:
    """Cluster images whose hashes are within max_distance of each other.

    Clustering is transitive -- A near B and B near C puts all three in one
    group -- which is what makes a group mean "this set of pictures belong
    together" rather than "these two happen to be close".

    Every pair within max_distance is found: see `bands`. No bucket is
    skipped for being large. The old code dropped buckets of more than 2,000
    files as noise, which on a case full of near-identical screenshots is
    exactly where the look-alikes are, and it did so without a word. A large
    bucket now costs time, in bounded memory, rather than answers.

    Exact-duplicate bytes never reach here: they were collapsed during
    extraction, so a group of two means two genuinely different files that
    look alike.
    """
    if not hashes:
        return {}
    paths = list(hashes)
    values = [int(hashes[p]) & 0xFFFFFFFFFFFFFFFF for p in paths]
    parent = list(range(len(paths)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for shift, width in bands(max_distance):
        mask = (1 << width) - 1
        buckets: dict = {}
        for index, value in enumerate(values):
            buckets.setdefault((value >> shift) & mask, []).append(index)
        for members in buckets.values():
            if len(members) < 2:
                continue
            # A pair already joined through an earlier band needs no second
            # look, but checking that per pair costs more than the compare.
            for a, b in _close_pairs(members, values, max_distance):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

    groups: dict = {}
    for index, path in enumerate(paths):
        groups.setdefault(paths[find(index)], []).append(path)
    return {root: members for root, members in groups.items() if len(members) > 1}


def similar_to(target: str, hashes: dict, max_distance: int = DEFAULT_DISTANCE,
               limit: int = 40) -> list:
    """Images similar to one file, nearest first, with the distance shown."""
    if target not in hashes:
        return []
    value = hashes[target]
    found = []
    for path, other in hashes.items():
        if path == target:
            continue
        d = distance(value, other)
        if d <= max_distance:
            found.append({"out_path": path, "distance": d})
    found.sort(key=lambda row: row["distance"])
    return found[:limit]


# Both hashes have to agree, but not to the same tolerance: a requantized
# JPEG moves the perceptual hash further than the difference hash, because
# pHash is reading frequency content that recompression alters.
STACK_DHASH_DISTANCE = 6
STACK_PHASH_DISTANCE = 10


def visual_stacks(dhashes: dict, phashes: dict, groups: dict,
                  dhash_distance: int = STACK_DHASH_DISTANCE,
                  phash_distance: int = STACK_PHASH_DISTANCE) -> dict:
    """Group files that are the same picture to the eye.

    Stricter than the similar-group cluster, and computed inside it: a stack
    is by definition a subset of a group, so the candidate pairs are already
    known. Doing it that way is both cheaper and correct -- bucketing the
    perceptual hashes independently missed pairs ten bits apart, because ten
    bits of difference need share no sixteen-bit window.

    Both hashes have to agree. One alone is easy to fool: dHash calls two
    dark images close, pHash calls two photographs of one scene close.
    """
    stacks = {}
    counter = 0
    for members in groups.values():
        usable = [m for m in members if m in dhashes and m in phashes]
        if len(usable) < 2:
            continue
        parent = {m: m for m in usable}

        def find(path):
            while parent[path] != path:
                parent[path] = parent[parent[path]]
                path = parent[path]
            return path

        for i, a in enumerate(usable):
            for b in usable[i + 1:]:
                if (distance(dhashes[a], dhashes[b]) <= dhash_distance
                        and distance(phashes[a], phashes[b]) <= phash_distance):
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[rb] = ra

        local = {}
        for path in usable:
            local.setdefault(find(path), []).append(path)
        for root, group_members in local.items():
            if len(group_members) > 1:
                counter += 1
                stacks[f"{root}#{counter}"] = group_members
    return stacks


def analyze(manifest_path: str, max_distance: int = DEFAULT_DISTANCE,
            progress=None) -> dict:
    """Hash and group every image in a run."""
    if not PILLOW:
        raise RuntimeError(UNAVAILABLE)
    base_dir = os.path.dirname(os.path.abspath(manifest_path))
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    records = manifest.get("files", [])
    images = [r for r in records if r.get("category") == "image"]

    hashes, phashes, featureless = hash_records(records, base_dir, progress)
    groups = group(hashes, max_distance)
    stacks = visual_stacks(hashes, phashes, groups)

    membership = {}
    for index, (_, members) in enumerate(sorted(groups.items()), start=1):
        for path in members:
            membership[path] = index

    stack_membership = {}
    for index, (_, members) in enumerate(sorted(stacks.items()), start=1):
        for path in members:
            stack_membership[path] = index

    return {
        "images": len(images),
        "hashed": len(hashes),
        "featureless": len(featureless),
        "undecodable": len(images) - len(hashes) - len(featureless),
        "stacks": len(stacks),
        "stacked_files": sum(len(m) for m in stacks.values()),
        "stack_membership": stack_membership,
        "stack_members": {str(i): sorted(m) for i, (_, m)
                          in enumerate(sorted(stacks.items()), start=1)},
        "phashes": {path: f"{value:016x}" for path, value in phashes.items()},
        "groups": len(groups),
        "grouped_files": sum(len(m) for m in groups.values()),
        "max_distance": max_distance,
        "hashes": {path: f"{value:016x}" for path, value in hashes.items()},
        "membership": membership,
        "group_members": {str(i): sorted(m) for i, (_, m)
                          in enumerate(sorted(groups.items()), start=1)},
    }


def write_csv(result: dict, path: str) -> str:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["similar_group", "visual_stack", "out_path",
                         "dhash", "phash"])
        for group_id, members in sorted(result["group_members"].items(),
                                        key=lambda kv: int(kv[0])):
            for member in members:
                writer.writerow([group_id,
                                 result["stack_membership"].get(member, ""),
                                 member, result["hashes"].get(member, ""),
                                 result["phashes"].get(member, "")])
    return os.path.abspath(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Group visually similar images in a run. Needs Pillow.")
    ap.add_argument("-m", "--manifest", required=True)
    ap.add_argument("--distance", type=int, default=DEFAULT_DISTANCE,
                    help="bits of difference still counted as similar; lower "
                         "is stricter (0 means visually identical)")
    ap.add_argument("--csv", default="", help="write the groups here")
    args = ap.parse_args(argv)

    if not PILLOW:
        print(UNAVAILABLE, file=sys.stderr)
        return 2

    def progress(done, total, hashed):
        print(f"\r  hashed {hashed:,} of {done:,}/{total:,} examined",
              end="", flush=True)

    try:
        result = analyze(args.manifest, args.distance, progress)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 2

    print(f"\n\nImages        : {result['images']:,}")
    print(f"Hashed        : {result['hashed']:,}")
    print(f"Not decodable : {result['undecodable']:,}  "
          f"(truncated carves, unsupported formats, files that are not images)")
    print(f"Featureless   : {result['featureless']:,}  "
          f"(gradients and flat screenshots, left out of grouping: they look "
          f"alike to any perceptual hash)")
    print(f"Visual stacks : {result['stacks']:,} covering "
          f"{result['stacked_files']:,} file(s) -- the same picture, both "
          f"hashes agreeing")
    print(f"Similar groups: {result['groups']:,} covering "
          f"{result['grouped_files']:,} file(s), at a distance of "
          f"{result['max_distance']} bits")
    if args.csv:
        print(f"Wrote {write_csv(result, args.csv)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
