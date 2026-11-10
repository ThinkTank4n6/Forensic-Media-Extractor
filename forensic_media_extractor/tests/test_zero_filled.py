"""A file read across an unreadable sector says so; it is not stored as whole.

DeviceReader reads an unreadable sector as zeros, so that one bad sector
costs one sector rather than the whole request, and keeps where it was in
`bad_ranges`. Only the carve asked. The file-system walk, the MFT pass and
the FAT deleted-entry pass stored those zeros in the file and its hashes as
though the disk had held them, with nothing on the row; and a run with
--mode fs never reported the unreadable stretch at all. A read the device
answered short was padded with zeros the same way without being marked.

A failing device is stood in for by a file whose chosen sectors raise
OSError the way a failing drive does. Checked, for each reader: the stored
file's row is bounded "short" with a ZERO-FILLED note naming the source
offset, the run counts it, and the stretch is in the unreadable ranges --
including for a run that never carves. Also: a carved file's embedded scan
reads only as far as was stored (it used to read the judged length, on into
the bad stretch).
"""
import collections
import contextlib
import io
import logging
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import media_core, ntfs_mft, timeutil  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_zero_filled-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


class FailingDisk(media_core.DeviceReader):
    """A DeviceReader over a file, with chosen sectors that cannot be read,
    and optionally a stretch the device answers short (no error, fewer
    bytes)."""

    def __init__(self, path, bad=(), short_at=None):
        self.path = self.name = path
        self._fd = os.open(path, os.O_RDONLY)
        self.sector = 512
        self.size = os.path.getsize(path)
        self.bad = set(bad)
        self.short_at = short_at

    def _read_span(self, start, size):
        if any(start <= b < start + size for b in self.bad):
            raise OSError(5, "Input/output error")
        got = super()._read_span(start, size)
        if self.short_at is not None and start <= self.short_at < start + size:
            if size > 512:
                raise OSError(5, "Input/output error")   # into the retry
            return got[:self.short_at - start]           # answered short
        return got


def zero_noted(rec):
    return rec.bounded == "short" and "ZERO-FILLED" in (rec.note or "")


# --------------------------------------------------------------------------
print("a device read answered short is padded and marked, not padded silently")
plain = os.path.join(ROOT, "plain.dd")
with open(plain, "wb") as fh:
    fh.write(bytes(range(256)) * 64)               # 16 KiB
disk = FailingDisk(plain, short_at=8192 + 100)
got = disk.read(4096, 8192)
check("the read is the full length asked", len(got) == 8192, len(got))
check("the bytes the device did not give are marked bad",
      getattr(disk, "bad_ranges", None) == [(8192 + 100, 412)],
      getattr(disk, "bad_ranges", None))

# --------------------------------------------------------------------------
print("\nthe MFT pass: a deleted file whose cluster holds a bad sector")
saved_argv = sys.argv
sys.argv = [saved_argv[0], os.path.join(ROOT, "ntfs.dd")]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        import make_ntfs_test_image as ntfs
finally:
    sys.argv = saved_argv
ntfs_path = os.path.join(ROOT, "ntfs.dd")
bad_at = ntfs.DATA_CLUSTER * ntfs.CLUSTER + 512
disk = FailingDisk(ntfs_path, bad=[bad_at])
out = media_core.OutputManager(os.path.join(ROOT, "out_mft"), metadata=False)
stats = collections.defaultdict(int)
media_core.run_mft_recovery(disk, out, types.SimpleNamespace(image=ntfs_path),
                            stats, media_core.SIGNATURES)
byname = {os.path.basename(r.source_path): r for r in out.records}
png = byname.get("holiday_photo.png")
check("the file is recovered", png is not None)
if png is not None:
    check("its row is marked short, with a ZERO-FILLED note", zero_noted(png),
          png.note[-160:])
    check("the note names the source offset", f"{bad_at:,}" in png.note)
    # Item 14: creation time is crtime, not ctime.
    check("its creation time is crtime, and ctime is left empty",
          timeutil.stored(png, "crtime") and not timeutil.stored(png, "ctime"),
          (timeutil.stored(png, "crtime"), timeutil.stored(png, "ctime")))
thumb = byname.get("tiny_thumbnail.jpg")
check("a resident file read nowhere near it is not marked",
      thumb is not None and thumb.bounded == "" and "ZERO" not in thumb.note)
check("the run counts it", stats.get("zero_filled_files") == 1
      and stats.get("zero_filled_bytes") == 512,
      (stats.get("zero_filled_files"), stats.get("zero_filled_bytes")))
check("and the stretch is in the unreadable ranges",
      [bad_at, 512] in (stats.get("unreadable_ranges") or []),
      stats.get("unreadable_ranges"))
out.close({})
disk.close()

# --------------------------------------------------------------------------
print("\nthe FAT readers (deleted entries, and the walk without TSK)")
fat_path = os.path.join(HERE, "media_test.img")
with open(fat_path, "rb") as fh:
    fat_image = fh.read()


def first_sector_of(stored_path):
    with open(stored_path, "rb") as fh:
        head = fh.read(4096)
    at = fat_image.find(head)
    return at + 512 if at >= 0 else None


clean = media_core.OutputManager(os.path.join(ROOT, "fat_clean"), metadata=False)
plain_reader = media_core.open_image(fat_path)
media_core.run_fat_deleted_recovery(
    plain_reader, clean, types.SimpleNamespace(image=fat_path, max_file_size=1 << 32),
    collections.defaultdict(int), media_core.SIGNATURES)
walk_stats = collections.defaultdict(int)
walk_stats["volumes"] = []
walk_args = types.SimpleNamespace(image=fat_path, no_deleted=False,
                                  max_file_size=1 << 32, databases=False)
media_core.run_fat32_fallback(walk_args, clean, walk_stats, media_core.SIGNATURES,
                              reader=plain_reader)
plain_reader.close()
deleted = next(r for r in clean.records if r.engine == "fat-deleted")
walked = next(r for r in clean.records
              if r.engine == "filesystem" and r.size > 2048)
bad_deleted = first_sector_of(os.path.join(clean.outdir, deleted.out_path))
bad_walked = first_sector_of(os.path.join(clean.outdir, walked.out_path))
clean.close({})
check("the clean run's files are found in the image",
      bad_deleted is not None and bad_walked is not None)
check("a FAT deleted file's creation time is crtime, and ctime is empty",
      timeutil.stored(deleted, "crtime") and not timeutil.stored(deleted, "ctime"))

disk = FailingDisk(fat_path, bad=[bad_deleted, bad_walked])
out = media_core.OutputManager(os.path.join(ROOT, "fat_bad"), metadata=False)
stats = collections.defaultdict(int)
media_core.run_fat_deleted_recovery(
    disk, out, types.SimpleNamespace(image=fat_path, max_file_size=1 << 32),
    stats, media_core.SIGNATURES)
stats["volumes"] = []
media_core.run_fat32_fallback(walk_args, out, stats, media_core.SIGNATURES,
                              reader=disk)
again = {r.source_path: r for r in out.records}
check(f"the deleted file ({deleted.source_path}) is marked ZERO-FILLED",
      deleted.source_path in again and zero_noted(again[deleted.source_path]))
check(f"the walked file ({walked.source_path}) is marked ZERO-FILLED",
      walked.source_path in again and zero_noted(again[walked.source_path]))
untouched = [r for r in out.records
             if r.source_path not in (deleted.source_path, walked.source_path)]
check("no other file is marked", untouched and not any(zero_noted(r) for r in untouched),
      [r.source_path for r in untouched if zero_noted(r)])
# At least two: the walk without TSK reads deleted entries too, so the
# deleted file is read (and counted) a second time, as its duplicate.
check("both counted", stats.get("zero_filled_files", 0) >= 2,
      stats.get("zero_filled_files"))
out.close({})
disk.close()

# --------------------------------------------------------------------------
print("\na whole run with --mode fs reports what the device could not read")
real_open = _engine.replace("open_image", lambda path, credentials=None: FailingDisk(path, bad=[bad_walked]))
try:
    summary = media_core.run_extraction(fat_path, os.path.join(ROOT, "run_fs"),
                                        mode="fs", workers=1, metadata=False,
                                        kml=False)
finally:
    _engine.restore(real_open)
check("the summary lists the unreadable stretch",
      [bad_walked, 512] in (summary.get("unreadable_ranges") or []),
      summary.get("unreadable_ranges"))
check("and counts the file it is in", summary.get("zero_filled_files", 0) >= 1,
      summary.get("zero_filled_files"))

# --------------------------------------------------------------------------
print("\nwhat is found inside a zero-filled file says so")
mark = (disk, 0)
disk.__dict__["zero_log"] = [(4096, 512)]
target = media_core._embedded_target(object(), mark, collections.defaultdict(int))
check("its output marks every file stored through it",
      isinstance(target, media_core._ZeroNoted) and "4,096" in target._said)

# --------------------------------------------------------------------------
print("\na carved file's embedded scan reads only what was stored")
seen = []
real_scan = _engine.replace("scan_embedded", lambda blob, **kw: seen.append(len(blob)) or 0)
try:
    args = types.SimpleNamespace(scan_inside=True, max_inside_size=1 << 30,
                                 max_carve_size=1 << 30)
    media_core._scan_carved(media_core.BytesReader(b"\xff" * 100000), None, args,
                            {}, None, 0, 90000, 30000)
    media_core._scan_carved(media_core.BytesReader(b"Q" * 100000), None, args,
                            {}, None, 0, 90000, 30000, encoded=True)
finally:
    _engine.restore(real_scan)
check("a file stored short is scanned to its stored size",
      seen[:1] == [30000], seen[:1])
check("an encoded run is still read as the text it is on disk",
      seen[1:] == [90000], seen[1:])

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
