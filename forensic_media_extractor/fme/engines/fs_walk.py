"""
fs_walk.py -- Section 5 -- the file system engine: TSK, the FAT fallback and the
vendored readers, walking volumes and naming what they hold.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import os
import itertools
import re
import struct
from typing import Callable, Iterator, Optional, Sequence
from fme.engines.output import (  # noqa: E402,F401
    MIN_IMAGE_SIZE,
    MIN_VIDEO_SIZE,
    _FLOORS,
    _floor,
    _set_floors,
    writable_text,
    _writable_record,
    Record,
    _record_from_row,
    BOUNDED_VALUES,
    SIZED_ENGINES,
    _CSV_FIELD_LIMIT,
    ManifestUnreadable,
    CaseFolderInUse,
    refuse_existing_case,
    _OUTPUT_FATAL_ERRNOS,
    _output_is_fatal,
    stop_when_output_fails,
    ORPHAN_ENGINE,
    ORPHAN_NOTE,
    OutputManager,
    _csv_rows,
    _write_replacing,
    _silent_unlink,
    is_animated,
)
from fme.engines.engine_common import (  # noqa: F401
    Canceled, GIB, LOG, MIB,
    WALK_DEPTH_LIMIT, _check_cancel, _too_deep, db_extract,
    fat32_core, pytsk3,
)
from fme.engines.image_readers import (  # noqa: F401
    ImageReader,
)
from fme.engines.signatures import (  # noqa: F401
    MAX_SIG_SPAN, SIGNATURES,
)
from fme.engines.length_parsers import (  # noqa: F401
    magic_matched, sniff_category, ts,
)
from fme.engines.embedded import (  # noqa: F401
    _extract_database, _tsk_read, scan_embedded,
)



# ==========================================================================
# SECTION 5 -- File system engine (The Sleuth Kit)
# ==========================================================================
def tsk_enabled() -> bool:
    """Whether this run reads with The Sleuth Kit.

    The one place that is decided. FME_NO_TSK=1 is how an examiner gets the
    second, independent answer -- the vendored readers and fat32_core --
    to set against TSK's (tools/verify.py, tests/test_known_answer.py). It
    was honored by the partition listing and by inspect, and not by the
    file-system walk or the vendored reader's choice of file systems: on any
    machine with pytsk3 installed, the "independent" run walked with TSK and
    the vendored reader was never asked for NTFS, exFAT, HFS+ or APFS. Every
    agreement the cross-check reported there was TSK agreeing with itself.
    """
    return pytsk3 is not None and os.environ.get("FME_NO_TSK") != "1"


# What TSK reads once it has a volume. APFS is not on the list on its own:
# it is a pool, opened through Pool_Info, which a pytsk3 build may not have.
_TSK_KINDS = frozenset({"ntfs", "fat32", "fat16", "fat12", "exfat",
                        "hfsplus", "hfs", "ext2", "ext3", "ext4"})


def tsk_kinds() -> frozenset:
    """The file systems The Sleuth Kit can read on this machine, this run.

    The vendored reader is asked for every kind not on this list. It was
    asked for none of the mainstream ones whenever pytsk3 was importable --
    but the pytsk3 published today has no Pool_Info, so TSK cannot open an
    APFS container at all, and on every lab machine with pytsk3 installed a
    Mac's APFS volume was read by nobody: "Nothing was recovered, and this
    is a APFS volume", with the reader that could read it in the tree.
    """
    if not tsk_enabled():
        return frozenset()
    if hasattr(pytsk3, "Pool_Info"):
        return _TSK_KINDS | {"apfs"}
    return _TSK_KINDS


class _TSKImg:
    """Adapter exposing an ImageReader to pytsk3."""

    def __new__(cls, reader: ImageReader):
        if pytsk3 is None:
            raise RuntimeError("pytsk3 not installed")

        class _Inner(pytsk3.Img_Info):
            def __init__(self, rdr):
                self._rdr = rdr
                super().__init__(url="", type=pytsk3.TSK_IMG_TYPE_EXTERNAL)

            def close(self):
                pass

            def read(self, offset, size):
                return self._rdr.read(offset, size)

            def get_size(self):
                return self._rdr.size

        return _Inner(reader)


# A file system's own structures, as readers present them at the top of a
# volume. Not evidence of anything a person did, and not pictures: an upcase
# table begins 00 00 01 00, which is also how an icon begins, so NTFS's
# $UpCase (read by the vendored reader) and exFAT's $UPCASE_TABLE (presented
# by TSK) were each stored and listed as a recovered "image".
_FS_METADATA = frozenset({
    # NTFS
    "$mft", "$mftmirr", "$logfile", "$volume", "$attrdef", "$bitmap", "$boot",
    "$badclus", "$secure", "$upcase", "$extend", "$quota", "$objid", "$reparse",
    # exFAT and FAT as TSK presents them
    "$upcase_table", "$alloc_bitmap", "$mbr", "$fat1", "$fat2",
    # HFS+
    "$extentsfile", "$catalogfile", "$badblockfile", "$allocationfile",
    "$attributesfile", "$startupfile",
})


def is_fs_metadata(path: str) -> bool:
    """Whether this path is a file system's own structure at the top of a
    volume (never anything inside a folder, and never $OrphanFiles, TSK's
    folder of real files whose directory entries are gone)."""
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p]
    return len(parts) == 1 and parts[0].lower() in _FS_METADATA


def _tsk_fat_lost_letter(info, name: str, fs_label: str) -> str:
    """A deleted FAT name from TSK with its lost first letter shown as "?".

    TSK writes "_" where FAT overwrote the first byte of a deleted short
    name; this program's readers write "?", which no FAT name can contain.
    The same deleted file was "/_hotos/_ne.jpg" with TSK and
    "/?hotos/?ne.jpg" without. Only a deleted entry with no long name is
    changed: when TSK rebuilt a long name it also gives the short one
    (shrt_name), and the long name's first letter is real.
    """
    if not fs_label.upper().startswith("FAT") or not name.startswith("_"):
        return name
    try:
        deleted = bool(info.flags & pytsk3.TSK_FS_NAME_FLAG_UNALLOC)
        short = info.shrt_name or b""
    except AttributeError:
        return name
    return "?" + name[1:] if deleted and not short else name


# TSK's name flags; read through getattr so the logic holds (and can be
# tested) without pytsk3.
_TSK_NAME_FLAG_UNALLOC = 0x02


def _name_unallocated(name) -> bool:
    """Is this directory entry -- the name, not the record -- released?"""
    flag = getattr(pytsk3, "TSK_FS_NAME_FLAG_UNALLOC", _TSK_NAME_FLAG_UNALLOC) \
        if pytsk3 is not None else _TSK_NAME_FLAG_UNALLOC
    try:
        return bool(int(getattr(name, "flags", 0) or 0) & int(flag))
    except (TypeError, ValueError):
        return False


def _name_on_reused_record(name, meta, fs_label: str) -> bool:
    """A deleted name whose metadata record is no longer the deleted file's.

    Two signs. The record is allocated while the name is not: the record
    belongs to a live file now. Or, on NTFS, the sequence number the name
    remembers differs from the record's: the record was reused (and perhaps
    deleted again) since this name pointed at it.
    """
    if not _name_unallocated(name):
        return False
    meta_flag = getattr(pytsk3, "TSK_FS_META_FLAG_UNALLOC", 0x02) \
        if pytsk3 is not None else 0x02
    try:
        if not int(getattr(meta, "flags", 0) or 0) & int(meta_flag):
            return True
    except (TypeError, ValueError):
        return False
    if fs_label.upper().startswith("NTFS"):
        named, held = getattr(name, "meta_seq", None), getattr(meta, "seq", None)
        try:
            if named is not None and held is not None and int(named) != int(held):
                return True
        except (TypeError, ValueError):
            pass
    return False


def walk_filesystem(fs, out: OutputManager, *, fs_label: str, part_label: str,
                    include_deleted: bool, include_system: bool,
                    max_file_size: int, stats: dict,
                    signatures: Optional[list] = None, progress_cb=None,
                    cancel_cb=None, image_size: int = 0, databases: bool = True,
                    scan_inside: bool = False,
                    max_inside_size: int = 256 * MIB,
                    db_freespace: bool = True,
                    max_db_size: int = 4 * GIB, reader=None) -> None:
    # `reader`, the image TSK reads through: asked whether a file's bytes
    # included sectors a failing device zero-filled (_zero_after_read).
    visited: set = set()

    def recurse(directory, path: str, depth: int) -> None:
        if depth > WALK_DEPTH_LIMIT:
            _too_deep(stats, path)
            return
        for entry in directory:
            _check_cancel(cancel_cb)
            try:
                if not hasattr(entry, "info") or entry.info.name is None:
                    continue
                raw_name = entry.info.name.name
                name = raw_name.decode("utf-8", "replace")
                if name in (".", ".."):
                    continue
                name = _tsk_fat_lost_letter(entry.info.name, name, fs_label)
                if not include_system and name.startswith("$") and fs_label.startswith("NTFS"):
                    if name not in ("$Recycle.Bin",):
                        continue
                if not include_system and is_fs_metadata(f"{path}/{name}"):
                    continue
                meta = entry.info.meta
                full = f"{path}/{name}"

                is_dir = False
                if meta is not None:
                    is_dir = meta.type == pytsk3.TSK_FS_META_TYPE_DIR
                elif entry.info.name.type == pytsk3.TSK_FS_NAME_TYPE_DIR:
                    is_dir = True

                if meta is not None and _name_on_reused_record(
                        entry.info.name, meta, fs_label):
                    # A deleted name whose record now belongs to another
                    # file (or to the same file under its live name). Read,
                    # it gave that file's bytes -- or that folder's listing
                    # -- under the deleted name, marked not deleted because
                    # the record is in use. The live file is walked under
                    # its own name; the deleted one's content is gone.
                    stats["deleted_name_reused"] = stats.get(
                        "deleted_name_reused", 0) + 1
                    LOG.info("%s: a deleted name whose MFT record %s is in "
                             "use again; its content is not this file's",
                             full, getattr(meta, "addr", "?"))
                    continue

                if is_dir:
                    if meta is None:
                        continue
                    # By record and sequence: a record is a folder once per
                    # sequence number, and keying by the record alone let
                    # one folder's walk stand for another that reused it.
                    key = (int(meta.addr), int(getattr(meta, "seq", 0) or 0))
                    if key in visited:
                        continue
                    visited.add(key)
                    try:
                        sub = entry.as_directory()
                    except (OSError, RuntimeError, IOError) as exc:
                        # Its files were never listed. This was a bare
                        # `pass`, so a folder TSK could not open looked the
                        # same in the summary as an empty one.
                        stats["fs_dirs_unreadable"] = stats.get(
                            "fs_dirs_unreadable", 0) + 1
                        stats["errors"] += 1
                        LOG.warning("folder %s could not be opened, so its "
                                    "files were not listed: %s", full, exc)
                        continue
                    recurse(sub, full, depth + 1)
                    continue

                if meta is None or meta.size == 0:
                    # A deleted entry whose size the file system no longer
                    # records looks exactly like this. Skipping it quietly
                    # is how a volume can report nothing deleted while an
                    # engine that reads the directory entries directly
                    # finds files -- so it is counted, and the count is in
                    # the summary where an examiner can see it.
                    if meta is not None and bool(
                            meta.flags & pytsk3.TSK_FS_META_FLAG_UNALLOC):
                        stats["deleted_without_size"] = stats.get(
                            "deleted_without_size", 0) + 1
                    continue
                # Deleted if either says so: the record (meta) or the name.
                # The record's flag alone called a deleted name on a reused
                # record live; those are passed over above, and a name the
                # file system released is deleted whatever its record says.
                deleted = (bool(meta.flags & pytsk3.TSK_FS_META_FLAG_UNALLOC)
                           or _name_unallocated(entry.info.name))
                if deleted:
                    stats["deleted_seen"] = stats.get("deleted_seen", 0) + 1
                if deleted and not include_deleted:
                    stats["deleted_skipped"] = stats.get(
                        "deleted_skipped", 0) + 1
                    continue
                if meta.size > max_file_size or (image_size and meta.size > image_size):
                    # Corrupt metadata routinely claims sizes larger than the
                    # volume itself, deleted entries especially.
                    stats["skipped_large"] += 1
                    continue

                head = b""
                zmark = _zero_mark(reader)
                try:
                    head = entry.read_random(0, min(meta.size, MAX_SIG_SPAN))
                except (OSError, IOError, RuntimeError) as exc:
                    # Nothing of it read, so not known to be media or not --
                    # counted, where it was a bare `continue`.
                    stats["fs_unreadable_files"] = stats.get(
                        "fs_unreadable_files", 0) + 1
                    stats["errors"] += 1
                    LOG.warning("%s could not be read (%s); it was not "
                                "examined", full, exc)
                    continue
                category, ext = sniff_category(head, name, signatures)
                if category is None:
                    # Not media itself -- but a database holds media in BLOB
                    # columns that no carver can reassemble, so open it.
                    if databases and db_extract is not None \
                            and db_extract.is_sqlite(head):
                        _extract_database(
                            out, stats, signatures,
                            read=lambda off, length, e=entry: _tsk_read(e, off, length),
                            size=meta.size, db_path=full, inode=str(meta.addr),
                            fs_label=fs_label, part_label=part_label,
                            freespace=db_freespace, max_size=max_db_size)
                    if scan_inside and meta.size <= max_inside_size:
                        bmark = _zero_mark(reader)
                        blob = b"".join(_fs_chunks(entry, meta.size))
                        if blob:
                            scan_embedded(blob, out=_embedded_target(
                                              out, bmark, stats), stats=stats,
                                          signatures=signatures,
                                          source_label=full, fs_label=fs_label,
                                          part_label=part_label,
                                          inode=str(meta.addr),
                                          deleted=deleted)
                    continue

                # Whether the bytes identified the format, or only the name
                # did. An extension-only hit is worth flagging: it may be a
                # truncated or partly overwritten file, or not media at all.
                by_magic = magic_matched(head, signatures)

                clean = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60].rsplit(".", 1)[0]
                stem = (f"recovered_{out.next_index():04d}_{part_label}_"
                        f"{meta.addr}_{clean}")
                if deleted:
                    stem = "deleted_" + stem

                fs_note = ("" if by_magic else
                           "classified by file name; the header did not match a "
                           "known format, so this may be truncated, overwritten "
                           "or not media at all")
                fs_got: dict = {}
                rec = out.store(
                    _fs_chunks(entry, meta.size, got=fs_got),
                    after_read=_zero_after_read(
                        zmark, fs_note, stats,
                        inner=_short_after_read(meta.size, fs_note, fs_got,
                                                stats)),
                    category=category,
                    ext=ext or "bin",
                    stem=stem,
                    engine="filesystem",
                    source_path=full,
                    inode=str(meta.addr),
                    fs_type=fs_label,
                    partition=part_label,
                    deleted=deleted,
                    signature="fs-entry" if by_magic else "fs-entry (extension only)",
                    note=fs_note,
                    mtime=ts(getattr(meta, "mtime", 0)),
                    atime=ts(getattr(meta, "atime", 0)),
                    ctime=ts(getattr(meta, "ctime", 0)),
                    crtime=ts(getattr(meta, "crtime", 0)),
                )
                # Stored by the run this one resumes (OutputManager skips
                # it): what is inside it is still looked at, since the
                # interrupted run may have stopped part way through that,
                # and what it did store is skipped the same way.
                resumed = (not rec and callable(getattr(out, "recorded_before", None))
                           and out.recorded_before(
                               engine="filesystem", partition=part_label,
                               source_path=full, inode=str(meta.addr),
                               size=meta.size))
                if rec:
                    stats["fs_files"] += 1
                    stats["fs_bytes"] += rec.size
                    if deleted:
                        stats["fs_deleted"] += 1
                if rec or resumed:
                    if scan_inside and meta.size <= max_inside_size:
                        # A photo carries its own EXIF thumbnail, and a
                        # container that also sniffs as media still has
                        # members worth pulling out.
                        bmark = _zero_mark(reader)
                        blob = b"".join(_fs_chunks(entry, meta.size))
                        if blob:
                            scan_embedded(blob, out=_embedded_target(
                                              out, bmark, stats), stats=stats,
                                          signatures=signatures,
                                          source_label=full, fs_label=fs_label,
                                          part_label=part_label,
                                          inode=str(meta.addr), deleted=deleted)
                    if progress_cb and stats["fs_files"] % 10 == 0:
                        progress_cb(stats["fs_files"], 0, stats["fs_files"],
                                    "filesystem")
            except Canceled:
                raise
            except (OSError, IOError, RuntimeError, AttributeError, UnicodeDecodeError) as exc:
                LOG.debug("entry error in %s: %s", path, exc)
                stats["errors"] += 1

    try:
        root = fs.open_dir(path="/")
    except (OSError, IOError, RuntimeError) as exc:
        LOG.warning("cannot open root of %s: %s", fs_label, exc)
        return
    recurse(root, "", 0)


def _fs_chunks(entry, size: int, chunk: int = 4 * MIB,
               got: Optional[dict] = None) -> Iterator[bytes]:
    """Stream a file system entry. A read that fails part way ends the
    stream -- what was read is still evidence -- but the reason goes into
    `got["error"]` so the caller can mark the record short (see
    `_short_after_read`): stopping quietly stored a 2 MB head of an 8 MB
    file as if it were the whole file."""
    off = 0
    while off < size:
        try:
            data = entry.read_random(off, min(chunk, size - off))
        except (OSError, IOError, RuntimeError) as exc:
            if got is not None:
                got["error"] = f"read failed at byte {off:,}: {exc}"
            return
        if not data:
            if got is not None:
                got["error"] = f"the source returned nothing at byte {off:,}"
            return
        yield data
        off += len(data)


def _short_after_read(expected: int, note: str, got: Optional[dict] = None,
                      stats: Optional[dict] = None):
    """An `after_read` for a file whose size a file system recorded: if
    fewer bytes were stored than the entry says, the row says so
    (bounded="short", a SHORT note, a count) instead of passing a cut-off
    file as the whole one. The carve has done this since v2026.11.279;
    the file-system, FAT and vendor-fs paths did not."""
    def after_read(size: int) -> dict:
        if expected <= 0 or size >= expected:
            return {}
        why = (got or {}).get("error") or "the source ended early"
        if stats is not None:
            stats["short_reads"] = stats.get("short_reads", 0) + 1
            stats["errors"] = stats.get("errors", 0) + 1
        LOG.warning("short read: %s bytes of %s stored (%s)",
                    f"{size:,}", f"{expected:,}", why)
        return {"bounded": "short",
                "note": ((note + "; ") if note else "")
                        + f"SHORT: the file system records {expected:,} bytes "
                          f"but only {size:,} could be read ({why}); the "
                          f"hashes are of the part stored, not the whole file"}
    return after_read


def _salvaging_reader(reader):
    """The reader under `reader` that zero-fills unreadable sectors
    (DeviceReader), or None. Looked for through the wrappers a run puts
    around it -- an unlocked BitLocker view, the FAT reader's seek/read
    adapter -- since the zeros are made at the bottom and read at the top."""
    for _ in range(6):
        if reader is None:
            return None
        if callable(getattr(type(reader), "_mark_bad", None)):
            return reader
        reader = (getattr(reader, "_inner", None) or getattr(reader, "_r", None)
                  or getattr(reader, "_rdr", None))
    return None


def _zero_mark(reader):
    """Where the reader's zero-fill log stands now. Taken before a file's
    bytes are read; `_zero_after_read` asks what was zero-filled since."""
    src = _salvaging_reader(reader)
    if src is None:
        return None
    return src, len(src.__dict__.get("zero_log") or ())


def _zero_filled_since(mark) -> list:
    if not mark:
        return []
    src, at = mark
    return list((src.__dict__.get("zero_log") or ())[at:])


def _zero_after_read(mark, note: str, stats: Optional[dict], inner=None):
    """An `after_read` that marks a stored file holding zeros a failing
    device read in place of sectors it could not read.

    DeviceReader zero-fills an unreadable sector so one bad sector costs
    one sector, not a whole request, and only the carve asked where those
    zeros were. A file read by the file-system walk, the MFT or the FAT
    deleted-entry pass was stored with the zeros in its bytes and its
    hashes, and its row said nothing. Now the row is bounded "short" --
    the flag every report treats as "not the whole file" -- and the note
    says how many bytes and where on the source, and the stretch is added
    to the run's unreadable ranges. `inner`, another after_read (the
    short-read check), is asked first and its note kept.

    The window is the reads made while this file was read, so a sector of
    file-system metadata read in that time counts too; and TSK's own block
    cache can answer a repeat read of a bad sector without asking the
    device. The note says "while this file was read" for that reason.
    """
    def after_read(size: int) -> dict:
        changes = dict((inner(size) if inner is not None else None) or {})
        filled = _zero_filled_since(mark)
        if not filled:
            return changes
        count = sum(width for _, width in filled)
        if stats is not None:
            stats["zero_filled_files"] = stats.get("zero_filled_files", 0) + 1
            stats["zero_filled_bytes"] = stats.get("zero_filled_bytes", 0) + count
            _note_unread(stats, filled)
        places = ", ".join(f"{start:,}" for start, _ in filled[:5])
        if len(filled) > 5:
            places += f" and {len(filled) - 5:,} more"
        LOG.warning("zero-filled: %s unreadable byte(s) at source offset(s) %s "
                    "were read as zeros into a stored file", f"{count:,}", places)
        base = changes.get("note", note)
        changes["bounded"] = "short"
        changes["note"] = (
            ((base + "; ") if base else "")
            + f"ZERO-FILLED: {count:,} byte(s) of the source could not be read "
              f"while this file was read (at source offset(s) {places}) and "
              f"were stored as zeros; the hashes include those zeros, not the "
              f"original bytes")
        return changes
    return after_read


class _ZeroNoted:
    """An OutputManager for the embedded scan of a file whose bytes, as read,
    included zero-filled unreadable sectors: every file found inside it says
    so in its note. Whether a given embedded file overlaps the zeros is not
    known, so its `bounded` is left as judged; the note is what keeps the
    parent's damage from being passed on silently."""

    def __init__(self, out, filled: list):
        self._out = out
        count = sum(width for _, width in filled)
        self._said = (f"the file it was found in held {count:,} zero-filled "
                      f"unreadable byte(s) when read (source offset(s) "
                      + ", ".join(f"{start:,}" for start, _ in filled[:5])
                      + (" and more" if len(filled) > 5 else "")
                      + "), which may fall inside this one")

    def store(self, chunks, *, after_read=None, **meta):
        said = self._said

        def amend(size, inner=after_read, base=meta.get("note", "")):
            changes = dict((inner(size) if inner is not None else None) or {})
            note = changes.get("note", base)
            changes["note"] = ((note + "; ") if note else "") + said
            return changes
        return self._out.store(chunks, after_read=amend, **meta)

    def __getattr__(self, name):
        return getattr(self._out, name)


def _embedded_target(out, mark, stats: dict):
    """`out`, or a _ZeroNoted over it when the read since `mark` was
    zero-filled anywhere -- for the embedded scan of a blob just read."""
    filled = _zero_filled_since(mark)
    if not filled:
        return out
    _note_unread(stats, filled)
    return _ZeroNoted(out, filled)


FS_TYPE_NAMES = {}
if pytsk3 is not None:  # populate readable names when available
    for attr in dir(pytsk3):
        if attr.startswith("TSK_FS_TYPE_"):
            FS_TYPE_NAMES[getattr(pytsk3, attr)] = attr.replace("TSK_FS_TYPE_", "")


def fs_label_for(fs) -> str:
    try:
        return FS_TYPE_NAMES.get(fs.info.ftype, str(fs.info.ftype))
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def run_fs_engine(reader: ImageReader, out: OutputManager, args, stats: dict,
                  progress_cb=None, cancel_cb=None) -> None:
    signatures = getattr(args, "signatures", None) or SIGNATURES
    if progress_cb:
        progress_cb(0, 0, 0, "filesystem")
    if not tsk_enabled():
        if pytsk3 is None:
            LOG.warning("pytsk3 not installed -- no multi-filesystem support. "
                        "Install with: pip install pytsk3")
        else:
            LOG.info("FME_NO_TSK=1: file systems are read without TSK")
        run_fat32_fallback(args, out, stats, signatures, progress_cb,
                           cancel_cb, reader=reader)
        return

    img = _TSKImg(reader)
    volumes: list[tuple[int, str]] = []
    table = _table_labels(reader)

    try:
        vs = pytsk3.Volume_Info(img)
        block = vs.info.block_size
        for part in vs:
            desc = part.desc.decode("utf-8", "replace") if isinstance(part.desc, bytes) else str(part.desc)
            if part.len <= 2:
                continue
            if part.flags == pytsk3.TSK_VS_PART_FLAG_META:
                continue
            volumes.append((part.start * block, _tsk_label(part, block, table)))
        LOG.info("Volume system: %d candidate partition(s)", len(volumes))
    except (OSError, IOError, RuntimeError):
        LOG.info("No volume system found -- treating image as a single volume")

    if not volumes:
        volumes = [(0, "p0")]

    refused = []
    for offset, label in volumes:
        opened = _open_fs_at(img, offset)
        if opened:
            for fs, sublabel in opened:
                lbl = fs_label_for(fs)
                LOG.info("Mounted %s at offset %d [%s]", lbl, offset, sublabel or label)
                stats["volumes"].append({"offset": offset, "fs": lbl,
                                         "label": sublabel or label})
                walk_filesystem(
                    fs, out,
                    fs_label=lbl,
                    part_label=(sublabel or label).replace("/", "_"),
                    include_deleted=not args.no_deleted,
                    include_system=args.include_system,
                    max_file_size=args.max_file_size,
                    stats=stats,
                    image_size=getattr(reader, "size", 0),
                    signatures=signatures,
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                    databases=getattr(args, "databases", True),
                    scan_inside=getattr(args, "scan_inside", False),
                    max_inside_size=getattr(args, "max_inside_size", 256 * MIB),
                    db_freespace=getattr(args, "db_freespace", True),
                    max_db_size=getattr(args, "max_db_size", 4 * GIB),
                    reader=reader,
                )
        else:
            LOG.info("No recognized file system at offset %d (%s)", offset, label)
            refused.append((offset, label))

    # A volume The Sleuth Kit will not open is not a volume with nothing on
    # it. TSK goes by the FAT specification, which decides the FAT type by
    # cluster count alone, so a volume formatted FAT32 with fewer than
    # 65,525 clusters -- which mkfs.fat -F 32 will make, and some cameras
    # and devices write -- is refused. This program's own FAT reader opens
    # those, but it was only ever asked when pytsk3 was not installed: with
    # pytsk3 present, every file on such a volume lost its name and path to
    # the carve (seen on Windows, v2026.11.274). The refused volumes, and
    # only those, now go to it; volumes TSK read are not read twice.
    if refused:
        before = stats.get("fs_files", 0)
        LOG.info("Reading %d volume(s) The Sleuth Kit did not recognize with "
                 "this program's own FAT reader", len(refused))
        run_fat32_fallback(args, out, stats, signatures, progress_cb,
                           cancel_cb, reader=reader, volumes=refused)
        stats["tsk_refused"] = [
            {"offset": offset, "label": label} for offset, label in refused]
        if stats.get("fs_files", 0) > before:
            LOG.warning("The Sleuth Kit did not recognize %d volume(s); this "
                        "program's own FAT reader read %d file(s) from them. "
                        "The run's volume list says which reader read which.",
                        len(refused), stats["fs_files"] - before)


class _ReaderSource:
    """Presents an ImageReader through the seek/read interface fat32_core
    expects, so the fallback works for .ufd chunk sets, raw devices and E01s
    -- anything that is not a single file it could open by name."""

    def __init__(self, reader: ImageReader):
        self._r = reader
        self._pos = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 1:
            offset += self._pos
        elif whence == 2:
            offset += self._r.size
        self._pos = max(0, offset)
        return self._pos

    def read(self, length: int = -1) -> bytes:
        if length is None or length < 0:
            length = max(0, self._r.size - self._pos)
        data = self._r.read(self._pos, length)
        self._pos += len(data)
        return data

    def tell(self) -> int:
        return self._pos

    def get_size(self) -> int:
        return self._r.size

    def close(self) -> None:
        pass


def _open_fat32(args, reader: Optional[ImageReader]):
    """Open the volume with fat32_core, from the reader when there is one."""
    if reader is not None and fat32_core is not None \
            and hasattr(fat32_core, "locate_fat32_volume"):
        source = _ReaderSource(reader)
        fs = fat32_core.Fat32FileSystem.__new__(fat32_core.Fat32FileSystem)
        fs.image_path = getattr(args, "image", "")
        fs._raw_source = source
        fs.source = fat32_core.locate_fat32_volume(source)
        fs.bs = fat32_core.parse_boot_sector(fs.source)
        fs.fat = fat32_core.FatTable(fs.source, fs.bs)
        return fs
    return fat32_core.Fat32FileSystem(args.image)


def run_fat32_fallback(args, out: OutputManager, stats: dict,
                       signatures: list, progress_cb=None, cancel_cb=None,
                       reader: Optional[ImageReader] = None,
                       volumes: Optional[list] = None) -> None:
    """No pytsk3? FAT volumes are still read through fat32_core -- every one
    on the image, each credited to its own partition.

    This opened only the first FAT32 partition the MBR listed and labelled
    it "p0" whatever it was, so on a card or disk with more than one FAT
    partition the live files of the others were left to the carve. It now
    walks every volume _open_fat_volumes finds, the same ones the
    deleted-entry pass reads.
    """
    if fat32_core is None:
        return
    if reader is None:
        try:
            volumes = [(_open_fat32(args, None), "p0")]
        except Exception as exc:  # noqa: BLE001 - not FAT, or unreadable
            LOG.info("fat32_core fallback unavailable for this image: %s", exc)
            return
    else:
        # `volumes`, when given, is (offset, label) for each volume to try:
        # the ones TSK refused. Otherwise every volume on the image.
        volumes = list(_open_fat_volumes(
            reader, args,
            volumes if volumes is not None else getattr(args, "volumes", None),
            cancel_cb))
    if not volumes:
        LOG.info("fat32_core fallback: no FAT volume on this image")
    for fs, part_label in volumes:
        _fat32_walk(fs, part_label, args, out, stats, signatures,
                    progress_cb, cancel_cb)


def _fat32_walk(fs, part_label: str, args, out: OutputManager, stats: dict,
                signatures: list, progress_cb=None, cancel_cb=None) -> None:
    """One FAT volume's files, live and deleted, into the case."""
    LOG.info("Using fat32_core for the file system engine (label %r)",
             fs.bs.volume_label)
    stats["volumes"].append({"offset": getattr(fs.source, "_base", 0),
                             "fs": f"{fs.bs.fat_type} (fat32_core)",
                             "label": fs.bs.volume_label, "partition": part_label})
    include_deleted = not args.no_deleted
    try:
        for entry in _fat32_all_files(fs, include_deleted, stats):
            _check_cancel(cancel_cb)
            if entry.size == 0 or entry.first_cluster < 2:
                continue
            if entry.size > args.max_file_size:
                stats["skipped_large"] += 1
                continue
            # A deleted entry's FAT chain has been zeroed, so the chain
            # cannot be followed -- read forward contiguously from the
            # recorded first cluster instead. That is the standard FAT
            # recovery heuristic and it is correct whenever the file was
            # not fragmented and has not been partially overwritten.
            contiguous = entry.is_deleted
            # Only as far as the FAT still marks the clusters free: past
            # that, the bytes belong to something written since. See
            # _fat_free_run.
            readable, overwrite_note = entry.size, ""
            if entry.is_deleted:
                needed, free = _fat_free_run(fs, entry.first_cluster,
                                             entry.size)
                if not free:
                    stats["fat_deleted_overwritten"] = stats.get(
                        "fat_deleted_overwritten", 0) + 1
                    continue
                if free < needed:
                    readable = free * fs.bs.cluster_size
                    overwrite_note = _fat_overwrite_note(
                        fs, entry.first_cluster, needed, free)
            zmark = _zero_mark(getattr(fs, "_raw_source", None))
            try:
                head = b"".join(_fat32_chunks(fs, entry, contiguous=contiguous,
                                              limit=min(MAX_SIG_SPAN, readable)))
            except Exception:  # noqa: BLE001
                stats["errors"] += 1
                continue
            # A deleted entry's lost first letter is shown as "?", as the
            # deleted-entry pass and a deleted folder already show it. It
            # was "_" here, which is a letter a FAT name can really begin
            # with: "_MG_0009.JPG" could not be told from a deleted
            # IMG_0009.JPG. "?" cannot appear in a FAT name, so it can only
            # mean the letter is gone.
            name = entry.name
            if getattr(entry, "is_deleted", False):
                name = _shown_deleted(name)
            name = name.replace("\ufffd", "_")
            parts = (entry.path or "").split("/")
            if parts and getattr(entry, "is_deleted", False):
                parts[-1] = _shown_deleted(parts[-1])
            display_path = "/".join(_shown_deleted(p) if p.startswith("\ufffd")
                                    else p for p in parts).replace("\ufffd", "_")
            category, ext = sniff_category(head, name, signatures)
            if category is None:
                if (getattr(args, "databases", True) and db_extract is not None
                        and db_extract.is_sqlite(head)
                        and entry.size <= getattr(args, "max_db_size", 4 * GIB)):
                    blob = b"".join(_fat32_chunks(fs, entry, contiguous=contiguous,
                                                  limit=readable))
                    _extract_database(
                        out, stats, signatures,
                        read=lambda off, length, b=blob: b[off:off + length],
                        size=len(blob), db_path=display_path,
                        inode=str(entry.first_cluster), fs_label=fs.bs.fat_type,
                        part_label=part_label,
                        freespace=getattr(args, "db_freespace", True),
                        max_size=getattr(args, "max_db_size", 4 * GIB),
                        deleted=entry.is_deleted)
                continue
            clean = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60].rsplit(".", 1)[0]
            stem = f"recovered_{out.next_index():04d}_{entry.first_cluster}_{clean}"
            if entry.is_deleted:
                stem = "deleted_" + stem
            fat_note = ("deleted entry, read contiguously from first cluster"
                        + overwrite_note
                        if entry.is_deleted
                        else "recovered via fat32_core (pytsk3 not installed)")
            fat_got: dict = {}
            fat_expected = min(entry.size, readable) if readable else entry.size
            rec = out.store(
                _fat32_chunks(fs, entry, contiguous=contiguous, limit=readable,
                              got=fat_got),
                after_read=_zero_after_read(
                    zmark, fat_note, stats,
                    inner=_short_after_read(fat_expected, fat_note, fat_got,
                                            stats)),
                category=category,
                ext=ext or "bin",
                stem=stem,
                engine="filesystem",
                source_path=display_path,
                inode=str(entry.first_cluster),
                fs_type=fs.bs.fat_type,
                partition=part_label,
                deleted=entry.is_deleted,
                signature="fs-entry",
                # FAT stores these with no time zone, so they pass through as
                # recorded rather than being converted to anything.
                # fat32_core's `ctime` is the entry's creation time (FAT
                # keeps no metadata-change time), so it is crtime here and
                # ctime is left empty, as TSK's walk uses ctime.
                mtime=getattr(entry, "mtime", ""),
                crtime=getattr(entry, "ctime", ""),
                atime=getattr(entry, "atime", ""),
                note=fat_note,
            )
            if rec:
                stats["fs_files"] += 1
                stats["fs_bytes"] += rec.size
                if entry.is_deleted:
                    stats["fs_deleted"] += 1
                if progress_cb and stats["fs_files"] % 10 == 0:
                    progress_cb(stats["fs_files"], 0, stats["fs_files"], "filesystem")
    finally:
        fs.close()


def _fat32_all_files(fs, include_deleted: bool, stats=None) -> Iterator:
    """Recurse the FAT32 tree yielding file entries. fat32_core.walk() skips
    deleted entries by design; this keeps them when asked, since a deleted
    entry still carries a name, size and first cluster."""
    seen: set[int] = set()

    def recurse(cluster: int, path: str, depth: int, released: bool = False):
        # The FAT12/FAT16 root is "cluster 0", a fixed region; anywhere
        # below it a cluster under 2 is not a folder.
        if (cluster < 2 and depth) or cluster in seen:
            return
        if depth > WALK_DEPTH_LIMIT:
            _too_deep(stats, path)
            return
        seen.add(cluster)
        try:
            entries = _list_folder(fs, cluster, path, released)
        except Exception as exc:  # noqa: BLE001 - unreadable directory cluster
            # Counted and said, as the TSK walk does: this returned without
            # a word, so a folder that could not be read looked the same in
            # the summary as an empty one.
            if stats is not None:
                stats["fs_dirs_unreadable"] = stats.get("fs_dirs_unreadable", 0) + 1
                stats["errors"] = stats.get("errors", 0) + 1
            LOG.warning("folder %s could not be read, so its files were not "
                        "listed: %s", path or "/", exc)
            return
        for entry in entries:
            if released:
                # Inside a deleted folder everything was freed with it,
                # whatever its own first byte says.
                entry.is_deleted = True
            if entry.is_dir:
                if not entry.is_deleted:
                    yield from recurse(entry.first_cluster, entry.path, depth + 1)
                elif include_deleted:
                    # The same deleted-folder rule as the deleted-entry
                    # pass: opened only while its first cluster is free and
                    # still reads as that folder.
                    child = _fat_folder_children(fs, entry, path, released)
                    if child:
                        yield from recurse(child[0], child[1], depth + 1, True)
            elif include_deleted or not entry.is_deleted:
                yield entry

    yield from recurse(fs.bs.root_cluster, "/", 0)


def _fat_free_run(volume, first: int, size: int) -> tuple:
    """How much of a deleted file's space is still unclaimed.

    Returns (needed, free): the clusters the entry's size calls for, read
    straight on from its first cluster, and how many of those, from the
    first, the FAT still marks free.

    A deleted entry keeps its first cluster and size after the file system
    has released the clusters, and anything written since may have taken
    them. Reading them without asking -- by following the chain from the
    first cluster, or straight through -- returned another file's bytes
    under the deleted file's name, dates and a hash of content that was
    never its own. The FAT is the only record of which clusters are taken,
    so it is asked about every one. Clusters past the end of the volume
    count as taken: the FAT is padded to a whole sector, and what lies past
    the last real cluster belongs to whatever follows the volume.
    """
    bs = volume.bs
    cluster_size = bs.cluster_size
    needed = max(1, -(-size // cluster_size))
    data_sectors = max(0, bs.total_sectors - bs.first_data_sector)
    last = min(len(volume.fat), data_sectors // bs.sectors_per_cluster + 2)
    free = 0
    for cluster in range(first, first + needed):
        if cluster < 2 or cluster >= last or not volume.fat.is_free(cluster):
            break
        free += 1
    return needed, free


def _fat_overwrite_note(volume, first: int, needed: int, free: int) -> str:
    """What a partial recovery says about the part it left out."""
    return (f"; the entry's size needs {needed} clusters but only the first "
            f"{free} are still free -- cluster {first + free} is in use by "
            f"something written since, so the file is cut off there rather "
            f"than finished with bytes that are not its own")


def _fat32_chunks(fs, entry, chunk: int = 4 * MIB, contiguous: bool = False,
                  limit: Optional[int] = None,
                  got: Optional[dict] = None) -> Iterator[bytes]:
    """Stream a FAT32 file in bounded-size pieces. contiguous=True reads
    clusters sequentially from the first one instead of following the FAT
    chain, which is the only option once the chain has been freed."""
    remaining = min(entry.size, limit) if limit else entry.size
    if contiguous:
        cluster_iter = itertools.count(entry.first_cluster)
    else:
        cluster_iter = fs.fat.chain(entry.first_cluster)
    buf = bytearray()
    for cluster in cluster_iter:
        if remaining <= 0:
            break
        # The volume's last cluster, not the FAT's padded length: a
        # contiguous read ran on into the next partition.
        if cluster >= min(len(fs.fat), getattr(fs.fat, "cluster_limit",
                                               len(fs.fat))):
            if got is not None:
                got["error"] = f"cluster {cluster} is past the end of the volume"
            break
        data = fs.read_cluster(cluster)
        if not data:
            if got is not None:
                got["error"] = f"cluster {cluster} could not be read"
            break
        take = min(len(data), remaining)
        buf += data[:take]
        remaining -= take
        if len(buf) >= chunk:
            yield bytes(buf)
            buf = bytearray()
    if remaining > 0 and got is not None and "error" not in got:
        # The chain ended (end-of-chain or a loop the FAT reader cut) before
        # the directory entry's size was reached.
        got["error"] = "the cluster chain ended before the recorded size"
    if buf:
        yield bytes(buf)


def _open_fs_at(img, offset: int):
    """Return [(fs, label)] for a volume offset, expanding APFS pools."""
    results = []
    try:
        fs = pytsk3.FS_Info(img, offset=offset)
        results.append((fs, ""))
        return results
    except (OSError, IOError, RuntimeError):
        pass

    # APFS (and other pools) need TSK >= 4.7 with pool support compiled in.
    if hasattr(pytsk3, "Pool_Info"):
        try:
            pool = pytsk3.Pool_Info(img, offset=offset)
            for vol in pool:
                try:
                    fs = pytsk3.FS_Info(pool, offset=offset,
                                        pvol_block=vol.block)  # type: ignore[call-arg]
                    results.append((fs, f"apfs_vol{vol.index}"))
                except (OSError, IOError, RuntimeError, TypeError, AttributeError):
                    continue
        except (OSError, IOError, RuntimeError, AttributeError):
            pass
    return results


def _open_fat_volumes(reader, args, volumes=None, cancel_cb=None):
    """Every FAT12/16/32 volume on the image, opened, with its partition label.

    Shared by the deleted-entry pass and the no-TSK file-system walk, so
    both find the same volumes the same way: each partition at its own
    offset (v232), never one volume twice, and nothing that only resembles
    a FAT boot sector.
    """
    if volumes is None:
        volumes = _partition_offsets(reader)
    examined = set()
    for offset, label in _fat_volumes(reader, volumes):
        _check_cancel(cancel_cb)
        try:
            whole = _ReaderSource(reader)
            if offset:
                # The volume at this partition's offset, and only that one.
                source = fat32_core.OffsetSource(whole, offset)
                base = offset
            else:
                # The start of the image: a bare volume, or a table to read.
                source = fat32_core.locate_fat32_volume(whole)
                base = getattr(source, "_base", 0)
                if base != offset:
                    label = ""
            if base in examined:
                continue
            volume = fat32_core.Fat32FileSystem.__new__(
                fat32_core.Fat32FileSystem)
            volume.image_path = getattr(args, "image", "")
            volume._raw_source = whole
            volume.source = source
            volume.bs = fat32_core.parse_boot_sector(volume.source)
            bs = volume.bs
            if not (bs.num_fats and bs.fat_size_sectors
                    and bs.reserved_sector_count
                    and (bs.root_cluster >= 2 if bs.fat_type == "FAT32"
                         else bs.root_entry_count)):
                raise ValueError("not a FAT boot sector")
            volume.fat = fat32_core.FatTable(volume.source, volume.bs)
        except Exception as exc:  # noqa: BLE001 - not FAT, or too damaged
            LOG.debug("no FAT volume at offset %d: %s", offset, exc)
            continue
        examined.add(base)
        yield volume, (label or "p0")


def _shown_deleted(name: str) -> str:
    """A deleted FAT name with its lost first character shown as '?'.

    FAT writes 0xE5 over the first byte of a deleted name, which arrives
    decoded as the replacement character. The letter is genuinely gone.
    """
    if name and (ord(name[0]) in (0xE5, 0xFFFD) or name[0] == "?"):
        return "?" + name[1:]
    return name


def _deleted_folder_readable(volume, entry) -> bool:
    """Whether a deleted folder's first cluster still holds that folder.

    Deleting a folder frees its clusters, and a later write may take them.
    Two things have to hold before its contents are believed: the FAT still
    marks the first cluster free (see _fat_free_run), and the cluster still
    reads as this folder -- every FAT32 subfolder starts with a "." entry
    pointing at its own first cluster and a ".." entry after it, and a
    cluster reused for anything else does not.
    """
    first = entry.first_cluster
    if first < 2:
        return False
    try:
        _, free = _fat_free_run(volume, first, 1)
        if not free:
            return False
        raw = volume.read_cluster(first)
    except Exception:  # noqa: BLE001 - out of range, or a short read
        return False
    if len(raw) < 64:
        return False
    dot, dotdot = raw[0:32], raw[32:64]
    if dot[0:11] != b".          " or not dot[11] & 0x10:
        return False
    if dotdot[0:11] != b"..         " or not dotdot[11] & 0x10:
        return False
    points_at = (struct.unpack_from("<H", dot, 20)[0] << 16) \
        | struct.unpack_from("<H", dot, 26)[0]
    return points_at == first


def _fat_folder_children(volume, entry, path: str, released: bool = False):
    """Folder entries worth opening, as (cluster, path, deleted).

    released says the folder holding this entry was itself deleted, so the
    entry was freed with it whatever its own first byte says.
    """
    if not entry.is_dir or entry.first_cluster < 2:
        return None
    if not (entry.is_deleted or released):
        return (entry.first_cluster, entry.path, False)
    # A deleted folder keeps the entries of the files that were in it --
    # names, sizes, first clusters, dates -- and deleting a whole folder is
    # one of the commonest things a user does. Its chain is freed, so only
    # its first cluster can be read, and only once it is shown still to be
    # that folder.
    if not _deleted_folder_readable(volume, entry):
        return None
    shown = path.rstrip("/") + "/" + _shown_deleted(entry.name)
    return (entry.first_cluster, shown, True)


_BAD_SHORT_NAME = set(b'"*+,/:;<=>?[\\]|')
DELETED_FOLDER_MAX_CLUSTERS = 64


def _looks_like_folder(raw: bytes) -> tuple:
    """(is a folder's entries, ends here): whether every 32-byte slot of a
    cluster is a plausible directory entry, and whether it holds the
    end-of-folder mark."""
    for at in range(0, len(raw) - 31, 32):
        first, attr = raw[at], raw[at + 11]
        if first == 0:
            return True, True
        if attr & 0x3F == 0x0F:
            if raw[at + 26:at + 28] != b"\0\0":
                return False, False          # a long-name entry has no cluster
            continue
        if attr & 0xC0:
            return False, False
        name = raw[at + (1 if first in (0xE5, 0x05) else 0):at + 11]
        if any(b < 0x20 or b in _BAD_SHORT_NAME for b in name):
            return False, False
    return True, False


def _list_folder(volume, cluster: int, path: str, released: bool) -> list:
    """A folder's entries; for a deleted folder, beyond its first cluster.

    A deleted folder's chain is freed, so only its first cluster could be
    read and a folder of more than about a hundred entries came back in
    part (v240). Its clusters are usually contiguous, so the clusters after
    the first are read too -- each only while the FAT still marks it free
    and every slot in it is a plausible directory entry, and no further than
    the end-of-folder mark. The first cluster that fails either test ends
    the folder there.
    """
    if not released or cluster < 2:
        return volume.list_dir(cluster, path)
    raw = bytearray(volume.read_cluster(cluster))
    plausible, ended = _looks_like_folder(bytes(raw))
    limit = volume.fat.cluster_limit if hasattr(volume.fat, "cluster_limit") else len(volume.fat)
    nxt = cluster + 1
    while not ended and plausible and nxt < limit and \
            nxt - cluster < DELETED_FOLDER_MAX_CLUSTERS and volume.fat.is_free(nxt):
        more = volume.read_cluster(nxt)
        plausible, ended = _looks_like_folder(more)
        if not plausible:
            break
        raw += more
        nxt += 1
    return fat32_core.parse_dir_entries(bytes(raw), path)


def _partition_offsets(reader) -> list:
    """Every partition on the image, as the volume passes expect them.

    The deleted-FAT and MFT passes take a list of partitions, and nothing in
    a real run ever supplied one, so both looked only at the start of the
    image. On a whole-disk image that is the partition table: the MFT pass
    found no NTFS volume at all, and the FAT pass reached only the first
    FAT32 partition the table listed, and none on a GPT disk.

    TSK reads the table when it is installed, as the file-system walk does,
    so both see the same partitions. Without it the MBR and GPT are read
    here. Logical partitions inside an MBR extended partition are listed by
    TSK only.
    """
    found = []
    if tsk_enabled():
        table = _table_labels(reader)
        try:
            vs = pytsk3.Volume_Info(_TSKImg(reader))
            block = vs.info.block_size
            for part in vs:
                if part.len <= 2 or part.flags == pytsk3.TSK_VS_PART_FLAG_META:
                    continue
                desc = (part.desc.decode("utf-8", "replace")
                        if isinstance(part.desc, bytes) else str(part.desc))
                found.append({"offset": part.start * block,
                              "label": _tsk_label(part, block, table)})
        except (OSError, IOError, RuntimeError):
            found = []
        if found:
            return found
    return _table_partitions(reader)


def _table_partitions(reader) -> list:
    """The MBR's primary partitions, or the GPT behind a protective MBR."""
    try:
        mbr = reader.read(0, 512)
    except Exception:  # noqa: BLE001 - too short to hold a table
        return []
    if len(mbr) < 512 or mbr[510:512] != b"\x55\xaa":
        return []
    entries = [struct.unpack_from("<B3sB3sII", mbr, 446 + 16 * slot)
               for slot in range(4)]
    if any(entry[2] == 0xEE for entry in entries):
        # GPT. The header is in the second sector, whatever size a sector
        # is, so both common sizes are tried.
        for sector in (512, 4096):
            try:
                header = reader.read(sector, 92)
            except Exception:  # noqa: BLE001
                continue
            if header[:8] != b"EFI PART":
                continue
            table_lba, count, size = struct.unpack_from("<QII", header, 72)
            if not 128 <= size <= 4096 or not 0 < count <= 1024:
                return []
            try:
                table = reader.read(table_lba * sector, count * size)
            except Exception:  # noqa: BLE001
                return []
            found = []
            for slot in range(len(table) // size):
                entry = table[slot * size:(slot + 1) * size]
                first = struct.unpack_from("<Q", entry, 32)[0]
                if entry[:16] == b"\x00" * 16 or not first:
                    continue
                name = entry[56:128].decode("utf-16-le", "replace").split("\x00")[0]
                found.append({"offset": first * sector,
                              "label": f"gpt{slot + 1}" + (f"({name})" if name else "")})
            return found
        return []
    sector = _mbr_sector_size(reader, entries)
    found = [{"offset": start * sector, "label": f"p{slot + 1}"}
             for slot, (_, _, kind, _, start, count) in enumerate(entries)
             if kind not in (0x00, 0x05, 0x0F, 0x85) and start and count]
    for _, _, kind, _, start, count in entries:
        if kind in (0x05, 0x0F, 0x85) and start and count:
            found += _logical_partitions(reader, start, len(found), sector)
    return found


def _looks_like_volume(head: bytes) -> bool:
    """Does a partition start here: a boot sector, or an ext superblock?"""
    if len(head) >= 512 and head[510:512] == b"\x55\xaa":
        return True
    if head[3:11] in (b"NTFS    ", b"EXFAT   ") or b"FAT" in head[54:62] \
            or b"FAT" in head[82:90]:
        return True
    return len(head) >= 1082 and head[1080:1082] == b"\x53\xef"


def _mbr_sector_size(reader, entries) -> int:
    """The sector size an MBR's addresses count in: 512, or 4096 on a 4Kn
    disk.

    MBR entries count sectors, and every address was multiplied by 512
    while the GPT path tried both sizes: on a 4Kn disk with an MBR each
    partition was looked for at an eighth of its offset, found nothing, and
    the volume's files were left to the carve without names. A device that
    reports a 4096-byte sector is taken at its word; otherwise 512 unless
    no listed partition begins with a volume at 512-byte addresses and at
    least one does at 4096-byte ones.
    """
    reported = getattr(_salvaging_reader(reader) or reader, "sector", None)
    if reported == 4096:
        return 4096
    starts = [start for _, _, kind, _, start, count in entries
              if kind and start and count]
    if not starts:
        return 512

    def volumes_at(sector: int) -> int:
        hits = 0
        for start in starts:
            if reader.size and start * sector >= reader.size:
                continue
            try:
                head = reader.read(start * sector, 2048)
            except Exception:  # noqa: BLE001 - unreadable is not a volume
                continue
            hits += _looks_like_volume(head)
        return hits

    if volumes_at(512):
        return 512
    return 4096 if volumes_at(4096) else 512


def _logical_partitions(reader, extended_lba: int, numbered: int,
                        sector: int = 512) -> list:
    """The logical partitions inside an MBR extended partition.

    Without TSK these were not listed at all, so a FAT or NTFS volume in a
    logical partition was never opened. An extended partition holds a chain
    of boot records, one per logical partition: the first entry is the
    partition (counted from its own record), the second the next record
    (counted from the start of the extended partition). The chain is
    followed with a guard, since a damaged one can loop.
    """
    found, seen, record = [], set(), extended_lba
    while record not in seen and len(seen) < 128:
        seen.add(record)
        try:
            ebr = reader.read(record * sector, 512)
        except Exception:  # noqa: BLE001 - past the end, or unreadable
            break
        if len(ebr) < 512 or ebr[510:512] != b"\x55\xaa":
            break
        first = struct.unpack_from("<B3sB3sII", ebr, 446)
        link = struct.unpack_from("<B3sB3sII", ebr, 462)
        if first[2] and first[4] and first[5]:
            found.append({"offset": (record + first[4]) * sector,
                          "label": f"p{numbered + len(found) + 1}(logical)"})
        if link[2] not in (0x05, 0x0F, 0x85) or not link[4]:
            break
        record = extended_lba + link[4]
    return found


def _tsk_label(part, block: int, table: dict) -> str:
    """A partition's label when TSK listed it: the same one this program
    gives it when it reads the table itself.

    TSK numbers its entries by slot in its own list, which counts the table
    itself and every unallocated gap, so the first partition of a plain MBR
    disk was "p2(Win95 FAT32 (0x0c))" with TSK installed and "p1" without.
    The partition a file came from is provenance; it must not depend on which
    library a machine has. A partition the table read here does not list (an
    unallocated gap, or one only TSK found) keeps TSK's description.
    """
    offset = part.start * block
    if offset in table:
        return table[offset]
    desc = (part.desc.decode("utf-8", "replace") if isinstance(part.desc, bytes)
            else str(part.desc)).strip()
    if part.flags & getattr(pytsk3, "TSK_VS_PART_FLAG_UNALLOC", 2):
        return f"unallocated at {offset}"
    return f"tsk{part.addr}({desc})"


def _table_labels(reader) -> dict:
    """{offset: label} from reading the partition table here (MBR primary and
    logical, or GPT), for _tsk_label."""
    try:
        return {p["offset"]: p["label"] for p in _table_partitions(reader)}
    except Exception:  # noqa: BLE001 - a damaged table names nothing
        return {}


def _volume_entry(volume) -> tuple:
    """(offset, label) of one partition, however the caller described it.

    The partition listing gives dicts; run_fs_engine hands over the volumes
    TSK refused as (offset, label) tuples. Only dicts were read, so every
    refused volume became offset 0: the search from the start found the
    first FAT volume and credited it to "p0", and every other refused
    partition was never opened -- its pictures came back only from the
    carve, with no name or folder. Anything else is refused loudly rather
    than read as the start of the image.
    """
    if isinstance(volume, dict):
        return (int(volume.get("offset", 0) or 0),
                volume.get("label") or volume.get("description", "") or "")
    if isinstance(volume, (tuple, list)) and len(volume) == 2:
        return int(volume[0] or 0), str(volume[1] or "")
    raise TypeError(f"not a partition: {volume!r}")


def _fat_volumes(reader, volumes):
    """Where to look for a FAT boot sector: each partition, then the start."""
    seen = []
    for volume in (volumes or []):
        offset, label = _volume_entry(volume)
        if offset not in [at for at, _ in seen]:
            seen.append((offset, label))
    # The start of the image last, whatever the table's order. TSK lists the
    # gap before the first partition as "Unallocated" at offset 0, first;
    # tried first, the search from the start found the first FAT partition
    # and took it with no label, so every file on it was credited to "p0"
    # and the partition's own entry was then skipped as already read.
    start = [entry for entry in seen if not entry[0]][:1] or [(0, "")]
    return [entry for entry in seen if entry[0]] + start


def _note_unread(stats: dict, ranges, reader=None) -> None:
    """Record stretches of the source that could not be read.

    A bad stretch is not a failure of the run -- a failing disk will fail
    the same way on every retry -- but it is a gap in what was examined,
    and the report says so.
    """
    merged = list(stats.get("unreadable_ranges") or [])
    merged += [list(r) for r in ranges]
    merged += [list(r) for r in (getattr(reader, "bad_ranges", None) or [])]
    if not merged:
        return
    merged.sort()
    joined = []
    for start, length in merged:
        if joined and start <= joined[-1][0] + joined[-1][1]:
            end = max(joined[-1][0] + joined[-1][1], start + length)
            joined[-1][1] = end - joined[-1][0]
        else:
            joined.append([start, length])
    # The whole list is kept for the run; OutputManager.close() trims what
    # goes into the summary. Trimming here lost every range past the
    # thousandth, and the next call recounted the bytes from the trimmed
    # list -- a failing disk with 1,501 bad stretches reported a third less
    # unread than there was (v2026.11.312).
    stats["unreadable_ranges"] = joined
    stats["unreadable_bytes"] = sum(length for _, length in joined)
