"""The TSK walk: deleted names on reused records, and what it could not read.

Driven with stand-in objects shaped like pytsk3's (pytsk3 need not be
installed), so each case is exactly the one meant:

- An NTFS deleted name whose MFT record now belongs to a live file. The
  walk took "deleted" from the record's flags alone, so it read the live
  file's bytes and stored them under the deleted name, marked not deleted.
  The same for a record reused and deleted again (the name's sequence
  number no longer matches the record's). Both are passed over and counted.
- A deleted folder name on a reused record was walked first, and since
  folders were remembered by record number alone, the live folder that now
  owns the record was then skipped: its files came back under the deleted
  folder's path.
- A folder TSK would not open (`as_directory()` raising) was a bare
  `pass`, and a file whose first bytes would not read a bare `continue`.
  Both are logged and counted now.
"""
import collections
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
import contextlib  # noqa: E402
import io  # noqa: E402

from fme.engines import media_core  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_tsk_walk_names-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


FAKE_TSK = types.SimpleNamespace(
    TSK_FS_META_TYPE_DIR=2, TSK_FS_META_TYPE_REG=1, TSK_FS_NAME_TYPE_DIR=3,
    TSK_FS_NAME_TYPE_REG=5, TSK_FS_META_FLAG_ALLOC=1, TSK_FS_META_FLAG_UNALLOC=2,
    TSK_FS_NAME_FLAG_ALLOC=1, TSK_FS_NAME_FLAG_UNALLOC=2)
ALLOC, UNALLOC = 1, 2


class Entry:
    def __init__(self, name, *, addr, seq, name_seq=None, name_flags=ALLOC,
                 meta_flags=ALLOC, data=b"", children=None, unreadable=False,
                 refuse_dir=False):
        is_dir = children is not None or refuse_dir
        self.info = types.SimpleNamespace(
            name=types.SimpleNamespace(
                name=name.encode(), flags=name_flags,
                meta_seq=seq if name_seq is None else name_seq,
                type=3 if is_dir else 5),
            meta=types.SimpleNamespace(
                addr=addr, seq=seq, flags=meta_flags, type=2 if is_dir else 1,
                size=len(data), mtime=0, atime=0, ctime=0, crtime=0))
        self._data, self._children = data, children
        self._unreadable, self._refuse = unreadable, refuse_dir

    def read_random(self, off, length):
        if self._unreadable:
            raise IOError("Read error: sector not found")
        return self._data[off:off + length]

    def as_directory(self):
        if self._refuse:
            raise IOError("Error opening directory")
        return list(self._children)


live = fixtures.make_jpeg(11)
gone = fixtures.make_jpeg(12)
again = fixtures.make_jpeg(13)
inside = fixtures.make_jpeg(14)
root = [
    Entry("live.jpg", addr=40, seq=3, data=live),
    # Deleted name, its record (40) now a live file's.
    Entry("old.jpg", addr=40, seq=3, name_seq=2, name_flags=UNALLOC, data=live),
    # Deleted name, its record reused and deleted again (sequence moved on).
    Entry("older.jpg", addr=41, seq=5, name_seq=4, name_flags=UNALLOC,
          meta_flags=UNALLOC, data=again),
    # A genuinely deleted file: name and record both released, same sequence.
    Entry("gone.jpg", addr=42, seq=7, name_flags=UNALLOC, meta_flags=UNALLOC,
          data=gone),
    # A deleted folder name on the record a live folder now owns -- first.
    Entry("OldFolder", addr=50, seq=9, name_seq=8, name_flags=UNALLOC,
          children=[Entry("inside.jpg", addr=60, seq=1, data=inside)]),
    Entry("LiveFolder", addr=50, seq=9,
          children=[Entry("inside.jpg", addr=60, seq=1, data=inside)]),
    Entry("Locked", addr=70, seq=1, refuse_dir=True),
    Entry("bad.jpg", addr=80, seq=1, data=live, unreadable=True),
]
fs = types.SimpleNamespace(open_dir=lambda path="/": list(root))

real_tsk = _engine.replace("pytsk3", FAKE_TSK)
try:
    out = media_core.OutputManager(os.path.join(ROOT, "out"), metadata=False)
    stats = collections.defaultdict(int)
    media_core.walk_filesystem(
        fs, out, fs_label="NTFS", part_label="p1", include_deleted=True,
        include_system=False, max_file_size=1 << 30, stats=stats,
        signatures=media_core.SIGNATURES, databases=False, scan_inside=False)
finally:
    _engine.restore(real_tsk)

stored = {r.source_path: r for r in out.records}
seen = {r.source_path for r in out.records} | {d.source_path for d in out.duplicates}
print("deleted names and reused records")
check("the live file is stored, not deleted",
      "/live.jpg" in stored and not stored["/live.jpg"].deleted)
check("a deleted name on a record now in use is not stored as its content",
      "/old.jpg" not in seen, sorted(seen))
check("nor one whose record was reused and deleted again",
      "/older.jpg" not in seen)
check("a genuinely deleted file is stored, marked deleted",
      "/gone.jpg" in stored and stored["/gone.jpg"].deleted)
check("the reused names are counted", stats.get("deleted_name_reused") == 3,
      stats.get("deleted_name_reused"))
print("\nfolders")
check("the live folder is walked under its own name",
      "/LiveFolder/inside.jpg" in stored, sorted(stored))
check("and not under the deleted folder's", "/OldFolder/inside.jpg" not in seen)
check("a folder that would not open is counted",
      stats.get("fs_dirs_unreadable") == 1, stats.get("fs_dirs_unreadable"))
print("\nunreadable files")
check("a file whose first bytes would not read is counted",
      stats.get("fs_unreadable_files") == 1, stats.get("fs_unreadable_files"))
check("and both are errors", stats.get("errors") == 2, stats.get("errors"))
out.close({})

print("\nthe same tests, read without pytsk3 constants")
name = types.SimpleNamespace(flags=UNALLOC, meta_seq=4)
meta = types.SimpleNamespace(flags=UNALLOC, seq=4)
check("a deleted name on its own deleted record is not called reused",
      not media_core._name_on_reused_record(name, meta, "NTFS"))
check("an allocated name is never called reused",
      not media_core._name_on_reused_record(
          types.SimpleNamespace(flags=ALLOC, meta_seq=1),
          types.SimpleNamespace(flags=ALLOC, seq=2), "NTFS"))
check("sequence numbers are compared only on NTFS",
      not media_core._name_on_reused_record(
          types.SimpleNamespace(flags=UNALLOC, meta_seq=1),
          types.SimpleNamespace(flags=UNALLOC, seq=2), "ext4"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
