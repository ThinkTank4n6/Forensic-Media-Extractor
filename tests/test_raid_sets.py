"""A RAID array or multi-image LVM group is read with all its members.

A run took one image, so a member was read alone: an md member with 1.2
metadata not at all, a RAID5 never from parity, an LVM volume group across
disks only where one disk held a whole logical volume (v2026.11.314). The
vendored reader assembles them; since v2026.11.315 a run gives it the other
members -- named with --member, or found beside the image by what each file
records about itself (--find-members, on in the interface).

Checked against tests/fixtures/raid/raid-trees.sha256: every file's SHA-256
as the Linux kernel presented the assembled array (FIXTURES-PROVENANCE.md).
"""
import contextlib
import gzip
import hashlib
import io
import logging
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

from fme.engines import media_core, vendor_fs  # noqa: E402
from fme.exports import report  # noqa: E402

RAID = os.path.join(HERE, "fixtures", "raid")
ROOT = os.path.join(tempfile.gettempdir(), "fme_raid_sets-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def members(stem, roles, folder):
    os.makedirs(folder, exist_ok=True)
    paths = []
    for role in roles:
        dest = os.path.join(folder, f"{stem}-{role}.img")
        with gzip.open(os.path.join(RAID, f"{stem}-{role}.img.gz"), "rb") as fin, \
                open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        paths.append(dest)
    return paths


KNOWN = {}
with open(os.path.join(RAID, "raid-trees.sha256"), encoding="utf-8") as fh:
    for line in fh:
        if line.strip() and not line.startswith("#"):
            digest, path = line.split()
            KNOWN[path] = digest


def expected(tree):
    return {p[len(tree) + 1:]: d for p, d in KNOWN.items() if p.startswith(tree + "/")}


def read_set(paths):
    """{array label: {path: sha256}}, through the program's own walk."""
    got = {}

    def take(record):
        digest = hashlib.sha256()
        for piece in record["read"]():
            digest.update(piece)
        array = record["volume"].split("/", 1)[0]
        got.setdefault(array, {})[record["path"].lstrip("/")] = digest.hexdigest()
    result = vendor_fs.walk_raid(paths, take)
    return got, result


def run(path, mode="fs", **options):
    out = os.path.join(ROOT, "case")
    shutil.rmtree(out, ignore_errors=True)
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.run_extraction(path, out, mode=mode, workers=1,
                                         metadata=False, kml=False, **options)


print("md RAID1, metadata 1.2: one member alone, then both")
pair = members("md-r1", (0, 1), os.path.join(ROOT, "r1"))
alone = run(pair[0])
check("alone, the report says it is read with its array's other members",
      "other members" in " ".join(report.coverage_limits({"summary": alone})))
found = vendor_fs.raid_members(pair[0])
check("the other member is found beside it, by its superblock",
      found["paths"] == [pair[1]], [os.path.basename(p) for p in found["paths"]])
got, _ = read_set(pair)
files = next(iter(got.values())) if got else {}
check("assembled, every file reads to the kernel's hash",
      files == expected("md-r1"), f"{len(files)} of {len(expected('md-r1'))}")
summary = run(pair[0], find_members=True)
check("a run with --find-members reads the array and lists the member it used",
      [os.path.basename(m["path"]) for m in summary.get("raid_members", [])] == ["md-r1-1.img"]
      and any(a["read"] for a in summary.get("raid_arrays", [])))
check("and no longer calls the member unreadable", not summary.get("unreadable_volume"),
      summary.get("unreadable_volume"))

print("\nmd RAID5 (left-symmetric): all four, then three of four")
four = members("md-r5-ls", (0, 1, 2, 3), os.path.join(ROOT, "r5"))
got, _ = read_set(four)
files = next(iter(got.values())) if got else {}
check("all four: every file reads to the kernel's hash",
      files == expected("md-r5-ls"), f"{len(files)} of {len(expected('md-r5-ls'))}")
got, _ = read_set(four[:3])
files = next(iter(got.values())) if got else {}
check("three of four: the missing member is rebuilt from parity, every file right",
      files == expected("md-r5-ls"), f"{len(files)} of {len(expected('md-r5-ls'))}")
summary = run(four[0], raid_members=four[1:3])
limits = " ".join(report.coverage_limits({"summary": summary}))
check("and the report says a member was missing and rebuilt from parity",
      "rebuilt from parity" in limits, limits[:100])

print("\nLVM RAID5, RAID6 and RAID10 logical volumes across five images")
five = members("lvm-raid-multi", (0, 1, 2, 3, 4), os.path.join(ROOT, "lvm"))
found = vendor_fs.raid_members(five[0])
check("the other four are found by the volume group's id",
      sorted(found["paths"]) == sorted(five[1:]), len(found["paths"]))
got, result = read_set(five)
for level in ("r5", "r6", "r10"):
    tree = f"lvm-raid-multi-{level}"
    array = next((files for label, files in got.items()
                  if label.endswith(f"_{level}")), {})
    check(f"{level}: every file reads to the kernel's hash", array == expected(tree),
          f"{len(array)} of {len(expected(tree))}")

print("\nthe assembled arrays are carved too")
summary = run(four[0], mode="both", find_members=True, hash_source=True)
carved = summary.get("raid_carved") or []
check("the RAID5 array is carved as the kernel presented it (9 MiB)",
      [a["size"] for a in carved] == [9437184], carved)
hashed = {os.path.basename(m["path"]): (m.get("computed") or {}).get("sha256")
          for m in (summary.get("evidence") or {}).get("members", [])}
want = {os.path.basename(p): hashlib.sha256(open(p, "rb").read()).hexdigest()
        for p in four[1:]}
check("and with Hash the source, each other member is hashed like the source",
      hashed == want, sorted(hashed))
check("and each is checked like the source, with what it found said",
      all(m.get("note") and "verified" in m
          for m in (summary.get("evidence") or {}).get("members", [])))

# A picture larger than a stripe is on no member whole. The vendored
# reader's assembly is checked above; here a stand-in array holds one, to
# check what the run does with an array once assembled.
from fme.engines import carving  # noqa: E402
import contextlib as _cl  # noqa: E402
with _cl.redirect_stdout(io.StringIO()):
    import make_media_test_image as _fixtures  # noqa: E402
picture = _fixtures.make_jpeg(40)
blob = bytes(1 << 20) + picture + bytes(1 << 20)


class _Set:
    arrays = [types.SimpleNamespace(image=io.BytesIO(blob), label="md array stand-in")]
    foreign = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


real_probe = vendor_fs.probe
vendor_fs.probe = lambda: types.SimpleNamespace(raid_open=lambda paths, pw, keys: _Set())
try:
    out = media_core.OutputManager(os.path.join(ROOT, "standin"), metadata=False)
    args = types.SimpleNamespace(chunk_size=1 << 20, align=1, max_carve_size=1 << 26,
                                 signatures=media_core.SIGNATURES, resume_from=0,
                                 scan_inside=False, progress=False, images_only=False,
                                 videos_only=False, artifacts=False)
    stats = __import__("collections").defaultdict(int)
    carving.run_raid_carve("member.img", ["other.img"], out, args, stats)
finally:
    vendor_fs.probe = real_probe
whole = [r for r in out.records if r.sha256 == hashlib.sha256(picture).hexdigest()]
check("the picture is carved whole from the array", len(whole) == 1)
check("marked with the array it came from, at the array's offset",
      whole and whole[0].partition == "raid:md array stand-in"
      and whole[0].offset == 1 << 20 and "assembled RAID array" in whole[0].note)
check("and a resume of the image's own carve does not take it for one of its own",
      whole and not carving._is_carved(whole[0]))
check("the array's carve leaves the image's progress note alone",
      not os.path.exists(os.path.join(ROOT, "standin", media_core.PROGRESS_FILE)))

# v2026.11.318: what the review of v2026.11.317 found in the array carve.
print("\nthe array carve keeps its own account")
inner = _fixtures.make_jpeg(8)
holder = picture[:-2] + inner + b"\xff\xd9"          # a picture holding another
blob2 = bytes(1 << 20) + picture + bytes(200_000) + holder + bytes(1 << 20)


class _Gappy(io.BytesIO):
    """An array that stops reading in a stretch, as a degraded one does."""
    GAP = (1 << 20) + len(picture) + 30_000, (1 << 20) + len(picture) + 90_000

    def read(self, n=-1):
        at = self.tell()
        lo, hi = self.GAP
        if lo <= at < hi:
            return b""
        data = super().read(n)
        if at < lo < at + len(data):
            self.seek(lo)
            return data[:lo - at]
        return data


class _Set2(_Set):
    arrays = [types.SimpleNamespace(image=_Gappy(blob2), label="md array two")]


def array_carve(outdir, stats):
    vendor_fs.probe = lambda: types.SimpleNamespace(raid_open=lambda p, pw, k: _Set2())
    try:
        args2 = types.SimpleNamespace(**dict(vars(args), scan_inside=True,
                                             max_inside_size=1 << 26))
        carving.run_raid_carve("member.img", ["other.img"], outdir, args2, stats)
    finally:
        vendor_fs.probe = real_probe


out2 = media_core.OutputManager(os.path.join(ROOT, "standin2"), metadata=False)
stats2 = __import__("collections").defaultdict(int)
stats2["carved_bytes"] = 50 << 20          # the image's own carve, before
array_carve(out2, stats2)
after_gap = (1 << 20) + len(picture) + 200_000
check("the image's own carve does not use up the array's byte budget, and "
      "what lies past the gap is carved",
      any(r.offset == after_gap for r in out2.records)
      and not stats2.get("carve_incomplete"), stats2.get("carve_incomplete"))
entry = (stats2.get("raid_carved") or [{}])[0]
gap = entry.get("unreadable_ranges") or []
check("the array's unreadable stretch is the array's, not the image's",
      not stats2.get("unreadable_ranges") and gap, gap)
check("and is measured to within a probe step, not the rest of the chunk",
      gap and gap[0][1] < 128 * 1024, gap)
nested = [r for r in out2.records if r.sha256 == hashlib.sha256(inner).hexdigest()]
check("a picture found inside an array's file is marked with the array",
      nested and nested[0].partition == "raid:md array two", [r.partition for r in nested])
kept = len(out2.records) + len(out2.duplicates)
array_carve(out2, stats2)
check("a second pass over the same array writes nothing down again",
      len(out2.records) + len(out2.duplicates) == kept,
      (kept, len(out2.records) + len(out2.duplicates)))
out2.close({})
out.close({})

print("\nreachable")
parser = io.StringIO()
with contextlib.redirect_stdout(parser), contextlib.suppress(SystemExit):
    media_core.main(["--help"])
check("the command line has --member and --find-members",
      "--member" in parser.getvalue() and "--find-members" in parser.getvalue())
with open(os.path.join(PROJECT, "fme", "web", "ui", "app.js"), encoding="utf-8") as fh:
    script = fh.read()
with open(os.path.join(PROJECT, "fme", "web", "media_gui.py"), encoding="utf-8") as fh:
    gui = fh.read()
check("the interface sends find_members and the server reads it",
      'find_members: $("find-members").checked' in script and '"find_members"' in gui)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
