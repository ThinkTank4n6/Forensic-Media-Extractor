"""
deleted_recovery.py -- Deleted files from the records that still name them: the NTFS MFT, FAT
and exFAT directory entries, and the vendored readers' volumes.

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
from fme.engines import ntfs_mft
from fme.engines import vendor_fs
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
    LOG, _check_cancel,
)
from fme.engines.signatures import (  # noqa: F401
    MAX_SIG_SPAN,
)
from fme.engines.length_parsers import (  # noqa: F401
    sniff_category, ts,
)
from fme.engines.fs_walk import (  # noqa: F401
    _fat_folder_children, _fat_free_run, _fat_overwrite_note, _list_folder,
    _open_fat_volumes, _partition_offsets, _short_after_read, _shown_deleted,
    _volume_entry, _zero_after_read, _zero_mark, is_fs_metadata,
    tsk_kinds,
)



def ewfprobe_available() -> bool:
    """Whether an .E01 can be read without libewf installed.

    The vendored reader presents an acquisition as an ordinary seekable
    file, so an E01 becomes readable on a machine where libewf will not
    build -- which on Windows is most of them.
    """
    try:
        from fme.engines import vendor_fs
        module = vendor_fs.probe()
        return module is not None and hasattr(module, "open_image")
    except Exception:  # noqa: BLE001
        return False


# How many files the vendored reader refused are named in the summary; the
# count covers all of them.
VENDOR_UNREAD_LISTED = 50


def run_vendor_fs(image_path: str, out: OutputManager, args, stats: dict,
                  signatures, progress_cb=None, cancel_cb=None) -> int:
    """Walk the file systems the main engine does not read.

    QNX for vehicle systems, F2FS for recent Android, and the flash file
    systems embedded devices use. On any of these the main engine finds
    nothing and carving recovers the pictures with no name and no date,
    which is a worse answer than this one.
    """
    if not vendor_fs.available():
        return 0
    # Asked with the same kinds the walk below reads. It was asked with the
    # extra kinds alone, so on a machine without TSK an image holding only
    # exFAT, HFS+, APFS or ext answered "nothing to read" and the walk that
    # would have read it -- include_mainstream, below -- was never reached:
    # every live file on it came back from carving, with no name or folder.
    wanted = set(vendor_fs.kinds_to_read(include_mainstream=True)) - tsk_kinds()
    if getattr(args, "unlocked_apfs", False):
        # TSK cannot take a password, so an APFS volume opened with one is
        # read here whatever else is installed.
        wanted = set(wanted) | {"apfs"}
    credentials = getattr(args, "credentials", None)
    extra = vendor_fs.extra_kinds(image_path, wanted, credentials)
    # The other members of any RAID array or multi-image LVM group this
    # image belongs to: given (--member), or found beside it by what each
    # file records about itself (--find-members). A member read alone was
    # the most this program could do before v2026.11.315.
    members = [m for m in (getattr(args, "raid_members", None) or []) if m]
    if getattr(args, "find_members", False):
        search = vendor_fs.raid_members(image_path, credentials)
        stats["raid_search"] = {k: search[k] for k in
                                ("added", "missing", "warnings", "ambiguous")}
        members += [p for p in search["paths"] if p not in members]
    if not extra and not members:
        return 0

    if extra:
        LOG.info("Reading %d volume(s) the main engine cannot: %s",
                 len(extra), ", ".join(sorted({v["kind"] for v in extra})))
    found = [0]

    def take(record):
        _check_cancel(cancel_cb)
        if not getattr(args, "include_system", False) and is_fs_metadata(record["path"]):
            return
        vgot: dict = {}
        try:
            chunks = record["read"](got=vgot)
        except TypeError:       # a walker whose reader takes no arguments
            chunks = record["read"]()
        try:
            head = next(chunks)
        except StopIteration:
            # Nothing came back. For an empty file that is right; for one
            # the reader refused -- an APFS file compressed with LZBITMAP,
            # which Apple has not published, or a read that failed at its
            # first byte -- it was dropped here without a word: not stored,
            # not counted, in no report (v2026.11.315, found against a
            # volume macOS wrote). Counted now, with the reader's reason.
            if int(record.get("size") or 0) and vgot.get("error"):
                stats["vendor_unread_files"] = stats.get("vendor_unread_files", 0) + 1
                listed = stats.setdefault("vendor_unread_examples", [])
                if len(listed) < VENDOR_UNREAD_LISTED:
                    listed.append({"path": record["path"],
                                   "size": int(record.get("size") or 0),
                                   "why": str(vgot["error"])})
            return
        name = os.path.basename(record["path"]) or "file"
        category, ext = sniff_category(head[:MAX_SIG_SPAN], name, signatures)
        if category is None:
            return

        def body(first=head, rest=chunks):
            yield first
            for piece in rest:
                yield piece

        clean = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60].rsplit(".", 1)[0]
        stored = out.store(
            body(), category=category, ext=ext or "bin",
            stem=f"recovered_{out.next_index():04d}_{clean}",
            engine="vendor-fs", source_path=record["path"],
            inode=record.get("inode", ""),
            fs_type=record.get("kind", "").upper(),
            partition=record.get("volume", ""), signature="filesystem",
            mtime=_vendor_time(record.get("mtime")),
            after_read=_short_after_read(int(record.get("size") or 0), "",
                                         vgot, stats))
        if stored:
            found[0] += 1
            stats["vendor_files"] = stats.get("vendor_files", 0) + 1
            stats["vendor_bytes"] = stats.get("vendor_bytes", 0) + stored.size
            if progress_cb and found[0] % 25 == 0:
                progress_cb(0, 0, len(out.records), "vendor-fs")

    # Without TSK this reader is the only one that can read a Windows or
    # Apple volume, and it is already in the tree. Restricting it to the
    # exotic file systems meant a machine with no pytsk3 could read a car
    # system and not a laptop.
    result = (vendor_fs.walk(image_path, take, only_extra=True, cancel=cancel_cb,
                             kinds=wanted, credentials=credentials)
              if extra else {"volumes": 0, "damaged": 0, "walked": [], "not_walked": []})
    if members:
        LOG.info("Reading the RAID set of %s with %d other member(s)",
                 os.path.basename(image_path), len(members))
        raid = vendor_fs.walk_raid([image_path] + members, take, cancel=cancel_cb,
                                   credentials=credentials)
        stats["raid_members"] = [{"path": os.path.abspath(m),
                                  "size": os.path.getsize(m) if os.path.isfile(m) else None}
                                 for m in members]
        stats["raid_arrays"] = [{"label": a["label"], "read": a["read"],
                                 "lines": a["lines"][:6]} for a in raid["arrays"]]
        for key in ("volumes", "damaged"):
            result[key] = result.get(key, 0) + raid.get(key, 0)
        result["walked"] = list(result.get("walked", [])) + raid["walked"]
        result["not_walked"] = list(result.get("not_walked", [])) + raid["not_walked"]
    stats["vendor_volumes"] = result.get("volumes", 0)
    stats["vendor_damaged"] = result.get("damaged", 0)
    # Named in the summary like every other volume the run read. They were
    # counted and nothing else, so a report's list of what was read left
    # them out -- and the LVM volumes read here were reported "not opened".
    stats["vendor_walked"] = [
        {k: v.get(k) for k in ("name", "kind", "base", "files", "note")}
        for v in result.get("walked", [])]
    if result.get("not_walked"):
        stats["vendor_not_walked"] = list(result["not_walked"])
    for volume in result.get("walked", []):
        stats.setdefault("volumes", []).append({
            "offset": volume.get("base", 0),
            "fs": f"{volume.get('kind', '').upper()} (vendored reader)",
            "label": volume.get("label", ""),
            "partition": volume.get("name", "")})
    return found[0]


def _vendor_time(value) -> str:
    """Whatever the reader gave for a time, as this program writes times."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    try:
        return ts(int(value))
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def run_mft_recovery(reader, out: OutputManager, args, stats: dict,
                     signatures, volumes=None, progress_cb=None,
                     cancel_cb=None) -> int:
    """Deleted files from an NTFS master file table.

    Worth a pass of its own because it reaches two things carving cannot. A
    file small enough to live inside its own record never occupied a
    cluster, so free space has never held it. And a record whose parent
    folder was itself deleted is unreachable by walking directories, while
    the record still carries the name.

    What comes back has the name, the path and the dates the file system
    recorded, which is the whole difference between a carved picture and a
    file somebody can be asked about.
    """
    found = 0
    if volumes is None:
        volumes = _partition_offsets(reader)
    for offset, label in _ntfs_volumes(reader, volumes):
        def read(where, count, base=offset):
            try:
                return reader.read(base + where, count)
            except Exception:  # noqa: BLE001 - a short read is not a failure
                return b""

        boot = ntfs_mft.read_boot(read(0, 512))
        if not boot:
            continue
        stats["mft_volumes"] = stats.get("mft_volumes", 0) + 1

        try:
            folders = ntfs_mft.directory_map(read, boot)
        except (struct.error, ValueError, MemoryError):
            folders = {}

        for record in ntfs_mft.deleted_files(read, boot):
            _check_cancel(cancel_cb)
            stats["mft_deleted_seen"] = stats.get("mft_deleted_seen", 0) + 1
            zmark = _zero_mark(reader)
            try:
                payload, how = ntfs_mft.content(record, read, boot)
            except (struct.error, ValueError, MemoryError) as exc:
                _mft_not_read(stats, record, f"{type(exc).__name__}: {exc}")
                continue
            chunks = None
            if not payload and how == "too large" \
                    and hasattr(ntfs_mft, "content_stream"):
                # Past what content() reads into memory: streamed instead,
                # so a deleted video over 2 GiB is recovered, not dropped.
                try:
                    stream, how = ntfs_mft.content_stream(record, read, boot)
                    payload = next(stream, b"")
                except (struct.error, ValueError, MemoryError, OSError) as exc:
                    _mft_not_read(stats, record, f"{type(exc).__name__}: {exc}")
                    continue
                chunks = itertools.chain([payload], stream)
            if not payload:
                if how == "unreadable" and not record.get("refused"):
                    # No data to read: an empty file. Counted, not warned.
                    stats["mft_empty"] = stats.get("mft_empty", 0) + 1
                else:
                    # Refused (encrypted, impossibly compressed), too large
                    # with no streaming reader, or nothing came back. It
                    # used to be a bare `continue`: a deleted file the
                    # record names, not recovered, and nothing said.
                    _mft_not_read(stats, record,
                                  record.get("refused") or how or "nothing read")
                continue

            category, ext = sniff_category(payload[:MAX_SIG_SPAN],
                                           record["name"], signatures)
            if category is None:
                continue

            path = ntfs_mft.path_of(record, folders) or record["name"]
            # Built after the bytes are read: a streamed file learns that it
            # was cut short, or ran past its written part, only as it is
            # read (ntfs_mft.content_stream), so the note is made then.
            note = _mft_note(record, how, None)

            def mft_after_read(size, record=record, how=how):
                return {"note": _mft_note(record, how, stats)}
            clean = re.sub(r"[^A-Za-z0-9._-]", "_", record["name"])[:60]
            stored = out.store(
                chunks if chunks is not None else iter([payload]),
                category=category, ext=ext or "bin",
                stem=f"recovered_{out.next_index():04d}_mft_{clean or 'file'}",
                engine="mft", source_path=path, inode=str(record["number"]),
                fs_type="NTFS", partition=label, deleted=True,
                signature=f"mft:{how.replace(' ', '-')}", note=note,
                after_read=_zero_after_read(zmark, note, stats,
                                            inner=mft_after_read),
                # $STANDARD_INFORMATION's creation time is crtime: ctime is
                # the metadata-change time on every other path (TSK's walk),
                # and putting creation there left crtime empty and the two
                # passes disagreeing about what "ctime" meant.
                mtime=record["modified"], crtime=record["created"],
                atime=record["accessed"])
            if stored:
                found += 1
                stats["mft_files"] = stats.get("mft_files", 0) + 1
                stats["mft_bytes"] = stats.get("mft_bytes", 0) + stored.size
                if how == "resident":
                    stats["mft_resident"] = stats.get("mft_resident", 0) + 1
            if progress_cb and found % 25 == 0:
                progress_cb(0, 0, len(out.records), "mft")
    return found


def _mft_not_read(stats: dict, record: dict, why: str) -> None:
    """A deleted file an MFT record names, whose content was not read."""
    stats["mft_not_read"] = stats.get("mft_not_read", 0) + 1
    LOG.warning("deleted %s (MFT record %s) was not recovered: %s",
                record.get("name") or "?", record.get("number", "?"), why)


def _mft_note(record: dict, how: str, stats: Optional[dict]) -> str:
    """The caution that travels with a file recovered from a deleted MFT
    record, because it decides how much the content can be relied on.
    Counted into `stats` when given -- once, after the bytes are read."""
    def count(key):
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    note = ("recovered from a deleted MFT record; the data was "
            "resident in the record, so nothing can have overwritten "
            "it" if how == "resident" else
            "recovered from a deleted MFT record; the content was "
            "read from clusters the file system had released, so it "
            "is what is there now")
    if record.get("data_flags", 0) & getattr(ntfs_mft, "DATA_COMPRESSED", 0):
        note += "; NTFS-compressed, decompressed as Windows reads it"
    if record.get("undecodable"):
        note += (f"; a compressed stretch would not decompress "
                 f"({record['undecodable']}), so its clusters have been "
                 f"overwritten and the file stops there")
    if record.get("short_by") and not record.get("partial"):
        note += (f"; the last {record['short_by']:,} bytes of the "
                 f"size the record gives could not be read (its "
                 f"cluster list ends early or a run points outside "
                 f"the volume), so the file is cut off there")
        count("mft_partial")
    if record.get("zeroed_from") is not None:
        note += (f"; only its first {record['zeroed_from']:,} bytes had "
                 f"been written, and the rest is zeros, as Windows "
                 f"shows it")
    if record.get("pieces", 1) > 1:
        # A file too fragmented for one record: its run list was
        # spread over extension records, now joined back up.
        note += (f"; its cluster list was held in {record['pieces']} "
                 f"MFT records, joined in file order")
        count("mft_joined")
    if record.get("partial"):
        note += ("; part of that list is missing -- a record holding "
                 "a later stretch of the file has been reused -- so "
                 "the file is cut off where the missing stretch "
                 "began, not completed with bytes that are not its "
                 "own")
        count("mft_partial")
    return note


def run_fat_deleted_recovery(reader, out: OutputManager, args, stats: dict,
                             signatures, volumes=None, progress_cb=None,
                             cancel_cb=None) -> int:
    """Deleted files from a FAT directory, read from the entries themselves.

    A pass of its own, for the same reason the MFT has one: a walk lists
    what a file system still claims, and a deleted entry is not claimed.
    This reads the directory entries directly, where a deleted file keeps
    its name, its first cluster and its size until the entry is reused.

    It runs whether or not TSK is installed, and that is the point. With
    TSK the volume is walked by TSK, whose entries for a deleted file can
    carry no size at all -- the walk then has nothing to read and the file
    is reported as though it were not there. A machine with TSK installed
    was recovering fewer deleted files than one without, which is the worst
    shape a difference like that can take: an examiner cannot see it, and
    the better-equipped machine is the one that finds less.

    Anything already recovered is suppressed by the usual hash check, so
    running both engines costs duplicates in the count and nothing in the
    case.
    """
    found = 0
    for volume, label in _open_fat_volumes(reader, args, volumes, cancel_cb):
        stats["fat_volumes"] = stats.get("fat_volumes", 0) + 1
        try:
            entries = list(_fat_deleted_entries(volume))
        except Exception:  # noqa: BLE001
            continue

        for entry, in_deleted_folder in entries:
            _check_cancel(cancel_cb)
            stats["fat_deleted_seen"] = stats.get("fat_deleted_seen", 0) + 1
            if entry.is_dir or entry.first_cluster < 2 or not entry.size:
                continue
            if entry.size > getattr(args, "max_file_size", 1 << 32):
                continue
            # Deleting a file frees its chain, so the FAT says nothing about
            # where the rest of it was. The clusters are usually contiguous,
            # so the file is read straight on from its first cluster for the
            # size the entry records -- but only as far as the FAT still
            # marks those clusters free. Following the chain instead, as
            # this once did, followed whatever file now owns the first
            # cluster, and reading straight through without asking mixed in
            # whatever had taken a cluster in the middle. Both came back
            # under the deleted file's name.
            needed, free = _fat_free_run(volume, entry.first_cluster,
                                         entry.size)
            if not free:
                stats["fat_deleted_overwritten"] = stats.get(
                    "fat_deleted_overwritten", 0) + 1
                LOG.info("deleted %s: its first cluster %d is in use again, "
                         "so its content is gone", entry.name,
                         entry.first_cluster)
                continue
            partial = free < needed
            zmark = _zero_mark(getattr(volume, "_raw_source", None))
            try:
                start = volume.bs.cluster_to_offset(entry.first_cluster)
                volume.source.seek(start)
                payload = volume.source.read(
                    min(entry.size, free * volume.bs.cluster_size))
            except Exception as exc:  # noqa: BLE001
                # Said rather than swallowed: a read failing silently is
                # what once left a picture truncated and reported whole.
                LOG.warning("could not read %s from its clusters: %s",
                            entry.name, exc)
                payload = b""
            if not payload:
                continue

            category, ext = sniff_category(payload[:MAX_SIG_SPAN],
                                           entry.name, signatures)
            if category is None:
                continue

            # FAT writes 0xE5 over the first character of a deleted name,
            # so the first letter is genuinely gone. Showing the raw byte
            # gives an examiner mojibake; replacing it with a question
            # mark says what happened, and the note says it too, because
            # the name in a report has to be defensible.
            shown = _shown_deleted(entry.name)
            # The byte has usually been decoded already, so it arrives as
            # the replacement character rather than as 0xE5.
            lost_first = shown != entry.name or shown.startswith("?")
            path_shown = (entry.path or entry.name)
            if lost_first and path_shown.endswith(entry.name):
                path_shown = path_shown[:-len(entry.name)] + shown

            clean = re.sub(r"[^A-Za-z0-9._-]", "_", shown)[:60]
            fat_deleted_note = ("recovered from a deleted directory entry, which kept "
                      "the name, the first cluster and the size; the content "
                      "was read from clusters the file system had released, "
                      "so it is what is there now"
                      + ("; the chain was freed, so the bytes were read "
                         "straight through from the first cluster, which "
                         "assumes the file was not fragmented")
                      + (_fat_overwrite_note(volume, entry.first_cluster,
                                             needed, free) if partial else "")
                      + (". The first character of the name is shown as ? "
                         "because FAT overwrites it when a file is deleted, "
                         "so it is not recoverable" if lost_first else "")
                      + (". It was in a folder that had itself been deleted; "
                         "that folder's entries were read from its first "
                         "cluster, which the FAT still marks free and which "
                         "still reads as that folder" if in_deleted_folder
                         else ""))
            stored = out.store(
                iter([payload]), category=category, ext=ext or "bin",
                stem=f"recovered_{out.next_index():04d}_fat_{clean or 'file'}",
                engine="fat-deleted", source_path=path_shown,
                inode="", fs_type=volume.bs.fat_type, partition=label, deleted=True,
                signature="fat:deleted-entry", note=fat_deleted_note,
                after_read=_zero_after_read(zmark, fat_deleted_note, stats),
                # The entry's creation time (fat32_core calls it ctime):
                # crtime, as the walk and the MFT pass record it.
                mtime=entry.mtime, crtime=entry.ctime, atime=entry.atime)
            if stored:
                found += 1
                stats["fat_deleted_files"] = stats.get(
                    "fat_deleted_files", 0) + 1
                if in_deleted_folder:
                    stats["fat_deleted_in_deleted_folders"] = stats.get(
                        "fat_deleted_in_deleted_folders", 0) + 1
                if partial:
                    stats["fat_deleted_partial"] = stats.get(
                        "fat_deleted_partial", 0) + 1
            if progress_cb and found % 25 == 0:
                progress_cb(0, 0, len(out.records), "fat-deleted")
    return found


def run_exfat_deleted_recovery(image_path: str, out: "OutputManager", args,
                               stats: dict, signatures, cancel_cb=None) -> int:
    """Deleted files from exFAT directories, with the names they kept.

    exFAT keeps a deleted file whole in its directory: deleting clears the
    in-use bit of each entry and frees the clusters, and the name -- in as
    many 15-character name entries as it needs -- the first cluster and the
    length all survive. The vendored reader lists them (deleted_files()) and
    reads them (read_deleted()), and nothing here asked it: the deleted-entry
    pass read FAT12, 16 and 32 only. Without pytsk3, a deleted exFAT file --
    an SD card's, a camera's -- came back only from carving, with no name,
    no folder and no times. Found by test_known_answer.py on the lab's WSL
    machine: the file's bytes came back and its name did not.

    Anything already recovered (by The Sleuth Kit's walk, where it lists
    deleted exFAT files) is suppressed by the usual hash check.
    """
    from fme.engines import vendor_fs
    module = vendor_fs.probe()
    if module is None:
        return 0
    try:
        image = vendor_fs._open(module, image_path, getattr(args, "credentials", None))
    except Exception as exc:  # noqa: BLE001
        LOG.info("exFAT deleted entries not read: %s", exc)
        return 0
    found = 0
    try:
        for volume in module.volumes(image):
            if (volume.get("kind") or "").lower() != "exfat":
                continue
            walker = volume.get("walker")
            if walker is None:
                continue
            stats["exfat_volumes"] = stats.get("exfat_volumes", 0) + 1
            label = volume.get("name", "") or volume.get("label", "")
            folders = _exfat_folder_paths(walker)
            deleted = list(walker.deleted_files())
            # A deleted folder keeps its entry set too, so the files deleted
            # with it can be shown inside it. Only the live tree was mapped,
            # and a picture deleted with its folder came back as
            # "(folder at cluster 6)/one.jpg" where TSK said /photos/one.jpg.
            # Repeated so a folder deleted inside a deleted folder resolves.
            for _ in range(8):
                added = False
                for entry in deleted:
                    first = getattr(entry, "first_cluster", None)
                    if (entry.is_dir and first and first not in folders
                            and entry.parent in folders):
                        folders[first] = (f"{folders[entry.parent].rstrip('/')}/"
                                          f"{entry.name}")
                        added = True
                if not added:
                    break
            for entry in deleted:
                _check_cancel(cancel_cb)
                if entry.is_dir:
                    continue
                stats["exfat_deleted_seen"] = stats.get("exfat_deleted_seen", 0) + 1
                if not entry.recoverable or not entry.size:
                    LOG.info("deleted %s on exFAT: not recoverable (%s)",
                             entry.name, entry.reason)
                    continue
                if entry.size > getattr(args, "max_file_size", 1 << 32):
                    continue
                try:
                    # In chunks, as the reader gives it; a deleted file is held
                    # whole only up to max_file_size, checked above.
                    payload = walker.read_deleted(entry)
                    if not isinstance(payload, (bytes, bytearray)):
                        payload = b"".join(payload)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("could not read deleted %s: %s", entry.name, exc)
                    continue
                if not payload:
                    continue
                category, ext = sniff_category(payload[:MAX_SIG_SPAN],
                                               entry.name, signatures)
                if category is None:
                    continue
                folder = folders.get(entry.parent)
                shown = (f"{folder.rstrip('/')}/{entry.name}" if folder is not None
                         else f"(folder at cluster {entry.parent})/{entry.name}")
                clean = re.sub(r"[^A-Za-z0-9._-]", "_", entry.name)[:60]
                times = entry.times or {}
                stored = out.store(
                    iter([payload]), category=category, ext=ext or "bin",
                    stem=f"recovered_{out.next_index():04d}_exfat_{clean or 'file'}",
                    engine="exfat-deleted", source_path=shown, inode="",
                    fs_type="exfat", partition=label, deleted=True,
                    signature="exfat:deleted-entry",
                    note=("recovered from a deleted exFAT directory entry set, "
                          "which kept the whole name, the first cluster and "
                          "the length; the content was read from clusters "
                          "the volume had released, so it is what is there now"
                          + ("; the file was written in one run (NoFatChain), "
                             "so it was read straight on from its first cluster"
                             if not entry.assumed_contiguous else "")
                          + ("; it was in a folder that had itself been deleted"
                             if entry.in_deleted_dir else "")
                          + (f". Times as stored, no zone applied: modified "
                             f"{times.get('modified')}" if times.get("modified")
                             else "")))
                if stored:
                    found += 1
                    stats["exfat_deleted_files"] = stats.get("exfat_deleted_files", 0) + 1
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass
    return found


def _exfat_folder_paths(walker, limit: int = 20000) -> dict:
    """{directory cluster: its path} for the live tree, so a deleted entry
    can be shown in the folder it was deleted from."""
    paths = {walker.root[0]: "/"}
    queue = [(walker.root, "/")]
    while queue and len(paths) < limit:
        node, here = queue.pop()
        try:
            children = walker.listdir_records(node)
        except Exception:  # noqa: BLE001 - a damaged folder
            continue
        for name, child, _times in children:
            if child[2] and child[0] not in paths:
                path = f"{here.rstrip('/')}/{name}"
                paths[child[0]] = path
                queue.append((child, path))
    return paths


def _fat_deleted_entries(volume):
    """Every deleted entry in every directory, including nested ones.

    The reader's own walk skips deleted entries, which is right for
    listing a volume and wrong here, so the directories are walked again
    keeping what it drops -- deleted folders included, which this once
    never opened: every file in a deleted folder was left to the carve,
    which returns it with no name, no path and no dates.

    Yields (entry, in_deleted_folder).
    """
    seen, stack = set(), [(volume.bs.root_cluster, "/", False)]
    while stack:
        cluster, path, in_deleted = stack.pop()
        if cluster in seen or len(seen) > 20000:
            continue
        seen.add(cluster)
        try:
            entries = _list_folder(volume, cluster, path, in_deleted)
        except Exception:  # noqa: BLE001 - a damaged directory is not fatal
            continue
        for entry in entries:
            child = _fat_folder_children(volume, entry, path, in_deleted)
            if child:
                stack.append((child[0], child[1], in_deleted or child[2]))
            if entry.is_deleted or in_deleted:
                # Inside a deleted folder every entry was released with it,
                # whatever its own first byte says.
                if not entry.is_dir:
                    yield entry, in_deleted


def _ntfs_volumes(reader, volumes):
    """Where to look for an NTFS boot sector: each partition, then the start.

    A partition table is the usual case; an image of a single volume with no
    table is common enough to be worth trying as well.
    """
    seen = []
    for volume in (volumes or []):
        offset, label = _volume_entry(volume)
        seen.append((offset, label or f"offset {offset}"))
    if not any(offset == 0 for offset, _ in seen):
        seen.append((0, "whole image"))
    return seen
