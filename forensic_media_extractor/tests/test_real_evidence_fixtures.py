"""Real-evidence fixtures: each read, and checked against an answer recorded
outside this program.

Taken in v2026.11.315 from the other tool's fixture set (MIT; the images
come from the vendored readers' own test suites -- tests/fixtures/
FIXTURES-PROVENANCE.md). Most hold text and binary files rather than
pictures, so what is checked is what the reader stack this program uses
-- its image readers, then vendor_fs over the vendored file-system
reader -- lists and reads, by SHA-256, against hashes recorded when the
images were made or by the operating system that wrote them; and what a
run of this program and its report then say.

Each of these found something wrong when it was first run here:

* an AFF4 acquisition (a ZIP) was taken for a phone package: seven
  members listed, the disk inside never walked or carved, and the run
  reported as complete;
* LVM logical volumes the vendored reader had read were reported "not
  opened" in every report;
* an ext4 volume read in full, holding no pictures, was reported "could
  not be read by this program";
* one device of a Btrfs RAID1, and an md RAID member, lost the reader's
  own reason under a generic sentence.
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

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
logging.disable(logging.CRITICAL)

from fme.engines import media_core, vendor_fs  # noqa: E402
from fme.exports import report  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
ROOT = os.path.join(tempfile.gettempdir(), "fme_real_fixtures-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def unpacked(name):
    src = os.path.join(FIX, name)
    if not name.endswith(".gz"):
        return src
    dest = os.path.join(ROOT, os.path.basename(name)[:-3])
    if not os.path.exists(dest):
        with gzip.open(src, "rb") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    return dest


def walked(path):
    """{volume: {path: sha256}} as vendor_fs reads it, every file."""
    found = {}

    def take(record):
        digest = hashlib.sha256()
        for chunk in record["read"]():
            digest.update(chunk)
        found.setdefault(record["volume"], {})[record["path"].lstrip("/")] = \
            digest.hexdigest()
    result = vendor_fs.walk(path, take, only_extra=False)
    return found, result


def run(path, mode="fs"):
    out = os.path.join(ROOT, "case_" + os.path.basename(path))
    shutil.rmtree(out, ignore_errors=True)
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.run_extraction(path, out, mode=mode, workers=1,
                                         metadata=False, kml=False)


def answers(sha_file, prefix=""):
    out = {}
    with open(os.path.join(FIX, sha_file), encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            digest, path = line.split(None, 1)
            path = path.strip()
            if path.startswith(prefix):
                out[path[len(prefix):]] = digest
    return out


# --------------------------------------------------------------------------
print("an AFF4 acquisition is an image, not a package")
aff4 = os.path.join(FIX, "pyaff4-zlib.aff4")
renamed = os.path.join(ROOT, "acquired.zip")
shutil.copy(aff4, renamed)
for path in (aff4, renamed):
    name = os.path.basename(path)
    check(f"{name}: not taken for a phone package", not media_core.is_package(path))
    reader = media_core.open_image(path)
    check(f"{name}: opened by the acquisition reader, at the disk's size",
          type(reader).__name__ == "VendorImageReader" and reader.size == 63894,
          reader.size)
    reader.close()
summary = run(renamed, mode="both")
check("a run reads the 63,894-byte disk, not the 17,683-byte container",
      summary["image_size"] == 63894 and "evidence" in summary, summary["image_size"])
plain = os.path.join(ROOT, "plain.zip")
import zipfile  # noqa: E402
with zipfile.ZipFile(plain, "w") as z:
    z.writestr("DCIM/a.jpg", b"\xff\xd8\xff\xe0")
check("an ordinary zip is still a package", media_core.is_package(plain))

# --------------------------------------------------------------------------
print("\nthree virtual-machine disks holding one volume")
disks = {}
for name in ("qcow2-v3.qcow2.gz", "vmdk-base.vmdk.gz", "dirty-log.vhdx.gz"):
    found, _ = walked(unpacked(name))
    disks[name] = found
    check(f"{name}: one ext4 volume, its files read", len(found) == 1
          and sum(len(v) for v in found.values()) >= 4,
          {k: len(v) for k, v in found.items()})
same = len({repr(sorted(d.items())) for d in disks.values()}) == 1
check("QCOW2, VMDK and VHDX give the same files with the same hashes", same)
summary = run(unpacked("dirty-log.vhdx.gz"))
check("a run names the volume it read",
      any("EXT4" in v.get("fs", "") for v in summary.get("volumes", [])),
      [v.get("fs") for v in summary.get("volumes", [])])
check("and does not call it unreadable", not summary.get("unreadable_volume"))

# --------------------------------------------------------------------------
print("\nLVM2: linear, split, striped, mirrored and grown logical volumes")
lvm = unpacked("lvm-fixture.img.gz")
found, result = walked(lvm)
expected = answers("lvm-fixture.sha256")
got = {}
for volume, files in found.items():
    lv = volume.split("_fixvg_")[1].split("_")[0] if "_fixvg_" in volume else volume
    for path, digest in files.items():
        got[f"{lv}/{path}"] = digest
check("every file read to the hash recorded when the volume group was made",
      got == expected, f"{len(got)} of {len(expected)}")
summary = run(lvm)
limits = " ".join(report.coverage_limits({"summary": summary}))
check("the run lists the five logical volumes it read",
      len(summary.get("lvm_volumes_read") or []) == 5)
check("and no report says the volumes inside were not opened",
      "were not opened" not in limits)
check("the two it could not read are named, with the reader's reason",
      "fixvg_gap" in limits and "fixvg_grow" in limits
      and "not a filesystem this tool reads" in limits)

# --------------------------------------------------------------------------
print("\nRAID: one member of an md RAID1 with 0.90 metadata (at the end)")
md090 = unpacked("raid/md-r1-090-0.img.gz")
found, _ = walked(md090)
files = next(iter(found.values())) if found else {}
expected = answers(os.path.join("raid", "raid-trees.sha256"), "md-r1-090/")
check("its ext4 is read to the hashes the kernel presented", files == expected,
      f"{len(files)} of {len(expected)}")
summary = run(md090)
check("a run that finds no pictures there does not call ext4 unreadable",
      not summary.get("unreadable_volume"), summary.get("unreadable_volume"))

print("\nRAID: one member of an md RAID1 with 1.2 metadata")
summary = run(unpacked("raid/md-r1-0.img.gz"))
limits = " ".join(report.coverage_limits({"summary": summary}))
check("the report says it is a member, read with its array's other members",
      "role 0 of 2" in limits and "other members" in limits, limits[:120])

print("\nBtrfs: one device of a two-device RAID1")
summary = run(unpacked("btrfs-raid1-dev2.img.gz"))
limits = " ".join(report.coverage_limits({"summary": summary}))
check("the report says why nothing is listed, in the reader's words",
      "one device (devid 2) of 2" in limits and "could not be read by this" not in limits,
      limits[:120])

# --------------------------------------------------------------------------
print("\nBtrfs, one device, uncompressed and in zlib, LZO and zstd")
btrfs_known = {}
with open(os.path.join(FIX, "btrfs.src.sha256"), encoding="utf-8") as fh:
    for line in fh:
        digest, path = line.rstrip("\n").split("  ", 1)
        btrfs_known[path] = digest
for flavor in ("none", "zlib", "lzo", "zstd"):
    image = unpacked(f"btrfs-{flavor}.img.gz")
    found, _ = walked(image)
    files = {}
    for volume in found.values():
        files.update(volume)
    wrong = [p for p, d in btrfs_known.items() if files.get(p) != d]
    if flavor != "zstd":
        check(f"{flavor}: all {len(btrfs_known)} files read to their source's hashes",
              not wrong, f"{len(wrong)} wrong")
        continue
    # zstd needs Python 3.14's compression.zstd (or the reader's own); where
    # it is missing the files that use it cannot be read, and must be said.
    summary = run(image)
    unread = summary.get("vendor_unread_files", 0)
    check(f"zstd: every file reads right, or is counted as unread ({len(wrong)} not read here)",
          (not wrong) or unread >= 1 and all(
              e["why"] for e in summary.get("vendor_unread_examples", [])),
          unread)

# --------------------------------------------------------------------------
print("\nReFS 3.14, written by Windows 11")
module = vendor_fs.probe()
sparse = module._RefsSparseImage(gzip.decompress(
    open(os.path.join(FIX, "refs-fixture.bin.gz"), "rb").read()))
stand_in = os.path.join(ROOT, "refs.img")
with open(stand_in, "wb") as fh:
    fh.write(b"stand-in")
real_open = vendor_fs._open
vendor_fs._open = lambda mod, path, credentials=None: (
    sparse if path == stand_in else real_open(mod, path, credentials))
try:
    found, _ = walked(stand_in)
finally:
    vendor_fs._open = real_open
files = {}
for volume in found.values():
    files.update(volume)
lines = gzip.decompress(open(os.path.join(FIX, "refs-fixture.known.tsv.gz"), "rb")
                        .read()).decode("utf-8").splitlines()
head = lines[0].split("\t")
known = {k["path"]: k["sha256"] for k in (dict(zip(head, ln.split("\t")))
                                          for ln in lines[1:]) if k["kind"] == "file"}
not_shipped = set(sparse.head.get("not_shipped", []))
listed = set(known) <= set(files)
check(f"every one of the {len(known):,} files Windows listed is listed", listed,
      f"{len(set(known) & set(files)):,}")
wrong = [p for p, d in known.items() if p not in not_shipped and files.get(p) != d]
check("and each read to the SHA-256 Windows recorded", not wrong,
      f"{len(known) - len(not_shipped) - len(wrong):,} checked, {len(wrong)} wrong")

dirty = module._RefsSparseImage(gzip.decompress(
    open(os.path.join(FIX, "refs-dirty.bin.gz"), "rb").read()))
vendor_fs._open = lambda mod, path, credentials=None: (
    dirty if path == stand_in else real_open(mod, path, credentials))
try:
    _, result = walked(stand_in)
finally:
    vendor_fs._open = real_open
notes = " ".join(v.get("note", "") for v in result.get("walked", []))
check("one whose newest changes are still in its log says so, and why",
      "metadata log records" in notes, notes[:100])

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
