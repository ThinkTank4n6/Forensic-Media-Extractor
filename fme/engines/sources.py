"""
sources.py -- Sources that are not one disk image: phone and vendor packages, folders,
and the loose streams and app caches joined back into videos.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import shutil
import tempfile
import struct
import stat
from typing import Callable, Iterator, Optional, Sequence
from fme.engines import appcache
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
from fme.engines.image_readers import is_aff4  # noqa: E402
from fme.engines.engine_common import (  # noqa: F401
    GIB, LOG, MAX_IMAGE_DEFAULT, MAX_VIDEO_DEFAULT,
    MIB, _check_cancel, db_extract,
)
from fme.engines.signatures import (  # noqa: F401
    IMAGE_EXTS, MAX_SIG_SPAN, SIGNATURES, VIDEO_EXTS,
    scan_buffer,
)
from fme.engines.length_parsers import (  # noqa: F401
    PARSERS, _p_generic, decode_payload, sniff_category,
    ts,
)
from fme.engines.embedded import (  # noqa: F401
    _container_kind, _extract_database, note_error, scan_embedded,
)
from fme.engines.volume_survey import (  # noqa: F401
    _device_fields,
)



def run_package_engine(path: str, out: OutputManager, args, stats: dict,
                      progress_cb=None, cancel_cb=None) -> None:
    """Extract media from an archive package.

    Covers Cellebrite UFDR reports and advanced-logical zips, GrayKey and
    Axiom exports, iOS backups, and plain zip or tar archives. Entries are
    read straight out of the archive -- nothing is unpacked to disk first --
    and classified the same way files on a volume are: extension first, magic
    bytes second, so entries stored under hashed or numbered names are still
    identified. SQLite entries are opened and their blobs pulled out too,
    which matters for reports that carry app databases alongside the media.
    """
    signatures = getattr(args, "signatures", None) or SIGNATURES
    scanned = 0
    try:
        archive, entries = _open_archive(path)
    except (EOFError, OSError) as exc:
        raise RuntimeError(
            f"{os.path.basename(path)} is damaged or incomplete: {exc}") from exc

    with archive:
        LOG.info("Package: %d entr(ies) in %s", len(entries),
                 os.path.basename(path))
        if progress_cb:
            progress_cb(0, len(entries), 0, "package")

        for index, entry in enumerate(entries):
            _check_cancel(cancel_cb)
            if progress_cb and index % 50 == 0:
                progress_cb(index, len(entries), stats["package_files"], "package")
            size, entry_name, when = _entry_info(entry)
            if size == 0 or size > args.max_file_size:
                if size:
                    stats["skipped_large"] += 1
                continue
            scanned += 1
            name = entry_name.rsplit("/", 1)[-1]
            head = _entry_head(archive, entry, MAX_SIG_SPAN)
            if head is None:
                stats["errors"] += 1          # encrypted or damaged entry
                continue

            category, ext = sniff_category(head, name, signatures)

            if category:
                clean = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60].rsplit(".", 1)[0]
                chunks, judged = _read_marked(
                    _archive_chunks(archive, entry, strict=True), size,
                    "entry in a package (UFDR / zip / tar)")
                rec = out.store(
                    chunks, after_read=judged,
                    category=category, ext=ext or "bin",
                    stem=f"recovered_{out.next_index():04d}_{clean}",
                    engine="package", source_path=entry_name,
                    **_device_fields(entry_name),
                    inode="", fs_type=_archive_kind(archive), partition="",
                    signature="package-entry", mtime=when,
                    note="entry in a package (UFDR / zip / tar)")
                if rec:
                    stats["package_files"] += 1
                    stats["package_bytes"] += rec.size
                continue

            if getattr(args, "databases", True) and db_extract is not None \
                    and db_extract.is_sqlite(head) \
                    and size <= getattr(args, "max_db_size", 4 * GIB):
                blob = b"".join(_archive_chunks(archive, entry))
                if not blob:
                    stats["errors"] += 1
                    continue
                _extract_database(
                    out, stats, signatures,
                    read=lambda off, length, b=blob: b[off:off + length],
                    size=len(blob), db_path=entry_name, inode="",
                    fs_label=_archive_kind(archive), part_label="",
                    freespace=getattr(args, "db_freespace", True),
                    max_size=getattr(args, "max_db_size", 4 * GIB))

    stats["package_scanned"] = scanned
    if progress_cb:
        progress_cb(len(entries), len(entries), stats["package_files"], "package")


def _open_archive(path: str):
    """Open a zip or tar package and list its file entries.

    Tar matters because GrayKey, iOS full-filesystem extractions and several
    Android tools ship .tar or .tar.gz rather than zip. A compressed tar can
    only be read forwards, which is why entries are streamed in order rather
    than seeked to.
    """
    import tarfile
    import zipfile

    if zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        return archive, [e for e in archive.infolist() if not e.is_dir()]
    if _android_backup_header(path):
        return _open_android_backup(path)
    try:
        archive = tarfile.open(path, "r:*")
        members = [m for m in archive.getmembers() if m.isfile()]
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise RuntimeError(f"Cannot read {path} as a package: {exc}") from exc
    return archive, members


def _archive_kind(archive) -> str:
    return "tar" if hasattr(archive, "extractfile") else "zip"


def _entry_info(entry) -> tuple:
    """(size, name, iso timestamp) for a zip or tar entry."""
    if hasattr(entry, "file_size"):          # zipfile.ZipInfo
        when = ""
        try:
            when = _dt.datetime(*entry.date_time).isoformat()
        except (ValueError, TypeError):
            pass
        return entry.file_size, entry.filename, when
    when = ""                                 # tarfile.TarInfo
    try:
        # UTC like every other file system timestamp, so the interface can
        # convert it; a naive local time could not be converted safely.
        when = _dt.datetime.fromtimestamp(entry.mtime, _dt.timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        pass
    return entry.size, entry.name, when


def _entry_head(archive, entry, length: int):
    """First bytes of an entry, or None if it cannot be read."""
    for chunk in _archive_chunks(archive, entry, chunk=length):
        return chunk
    return None


def _read_marked(chunks: Iterator[bytes], expected: Optional[int], note: str = ""):
    """(chunks, after_read): a file stored as far as it read, and marked.

    The chunk readers below ended quietly on a read error -- a CRC error in a
    zip entry, an I/O error from a failing drive or a dropped share -- and
    store() took the end for the end of the file: a 10 MB picture was stored
    as 8 MB, unmarked, with no error counted (v2026.11.319). The error is now
    caught here, and the row says how much was read, of how much, and why.
    """
    state = {"why": "", "read": 0}

    def guarded():
        try:
            for piece in chunks:
                state["read"] += len(piece)
                yield piece
        except Exception as exc:  # noqa: BLE001 - this file only
            if not state["read"]:
                # Nothing read at all: a read failure, which store() counts
                # as one, rather than an empty file it would drop as blank.
                raise OSError(str(exc) or type(exc).__name__) from exc
            state["why"] = str(exc) or type(exc).__name__

    def after_read(size):
        short = expected is not None and size < expected
        if not (state["why"] or short):
            return {}
        LOG.warning("read short: %s of %s bytes%s", size, expected,
                    f" ({state['why']})" if state["why"] else "")
        said = (f"read short: {size:,}"
                + (f" of the {expected:,} bytes listed" if expected is not None else " bytes")
                + (f", then {state['why']}" if state["why"] else "")
                + "; stored as far as it read")
        return {"bounded": "short", "note": f"{note}; {said}" if note else said}

    return guarded(), after_read


def _archive_chunks(archive, entry, chunk: int = 4 * MIB,
                    strict: bool = False) -> Iterator[bytes]:
    try:
        if hasattr(archive, "extractfile"):
            fh = archive.extractfile(entry)
            if fh is None:
                return
        else:
            fh = archive.open(entry)
        with fh:
            while True:
                data = fh.read(chunk)
                if not data:
                    return
                yield data
    except Exception:  # noqa: BLE001 - damaged, encrypted or unsupported entry
        if strict:
            raise
        return


# .xry was listed here and claimed in the manual; nothing ever read XRY's
# own format, and it is no longer offered (v2026.11.318).
PACKAGE_EXTENSIONS = (".ufdr", ".zip", ".ab", ".tar", ".gz", ".tgz",
                      ".bz2", ".xz", ".graykey")

ANDROID_BACKUP_MAGIC = b"ANDROID BACKUP\n"


def _android_backup_header(path: str):
    """(version, compressed, encryption, header length) of an Android
    backup (`adb backup`), or None for any other file."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    if not head.startswith(ANDROID_BACKUP_MAGIC):
        return None
    lines = head.split(b"\n", 4)
    if len(lines) < 5:
        return None
    try:
        version, compressed = int(lines[1]), lines[2] == b"1"
    except ValueError:
        return None
    encryption = lines[3].decode("ascii", "replace").strip()
    length = sum(len(x) + 1 for x in lines[:4])
    return version, compressed, encryption, length


def _open_android_backup(path: str):
    """An Android backup's tar, decompressed to a temporary file and listed.

    The format is four header lines and a tar, usually zlib-compressed.
    It was listed among the packages this program reads, and nothing read
    it: is_package refused the file and the run carved it as a disk
    (v2026.11.317). An encrypted backup needs the device's backup password
    and is refused, said so, rather than carved as noise.
    """
    import tarfile
    import tempfile
    import zlib

    version, compressed, encryption, start = _android_backup_header(path)
    if encryption.lower() != "none":
        raise RuntimeError(
            f"{os.path.basename(path)} is an encrypted Android backup "
            f"({encryption}); this program cannot open it without the backup "
            f"password, which it does not take. Nothing was read from it.")
    spool = tempfile.TemporaryFile()
    inflate = zlib.decompressobj() if compressed else None
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            for block in iter(lambda: fh.read(4 * MIB), b""):
                spool.write(inflate.decompress(block) if inflate else block)
            if inflate:
                spool.write(inflate.flush())
        spool.seek(0)
        archive = tarfile.open(fileobj=spool, mode="r:")
        members = [m for m in archive.getmembers() if m.isfile()]
    except (zlib.error, tarfile.TarError, OSError, EOFError) as exc:
        spool.close()
        raise RuntimeError(f"Cannot read {path} as an Android backup "
                           f"(version {version}): {exc}") from exc
    # Closed with the archive: tarfile does not own a file object it was
    # given, so the decompressed backup -- as large as the phone's data --
    # stayed in the temp folder until garbage collection (v2026.11.319).
    closing = archive.close

    def close_both():
        try:
            closing()
        finally:
            spool.close()
    archive.close = close_both
    return archive, members


def is_package(path: str) -> bool:
    """A zip or tar package: .ufdr, .zip, .tar, .tar.gz and friends.

    Content is checked as well as the name, because acquisition tools rename
    archives freely - a GrayKey or Axiom export may arrive with any extension
    at all, and a .zip that is really a tar should still open.
    """
    if os.path.isdir(path) or not os.path.isfile(path):
        return False
    lower = path.lower()
    named = lower.endswith(PACKAGE_EXTENSIONS) or lower.endswith(".tar.gz")
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return False
    if head.startswith(ANDROID_BACKUP_MAGIC):
        return True
    if head[:2] == b"PK":
        # An AFF4 is a ZIP too, and is an acquisition, not a package: read
        # as one its disk was never walked or carved (v2026.11.314).
        return not is_aff4(path)
    if head[257:262] == b"ustar":        # tar magic sits at offset 257
        return True
    if not named:
        return False
    # gzip/bzip2/xz wrapper: only a package if a tar is inside, which
    # tarfile settles cheaply.
    if head[:2] in (b"\x1f\x8b", b"BZ") or head[:6] == b"\xfd7zXZ\x00":
        import tarfile
        try:
            with tarfile.open(path, "r:*") as probe:
                probe.next()          # force a read, so truncation shows up here
            return True
        except Exception:  # noqa: BLE001 - not a tar, or too damaged to open
            return False
    return False


SKIP_DIR_NAMES = {"$RECYCLE.BIN", "System Volume Information", ".Trashes",
                  ".Spotlight-V100", ".fseventsd", "__pycache__"}


def run_folder_engine(root: str, out: OutputManager, args, stats: dict,
                      progress_cb=None, cancel_cb=None) -> None:
    """Walk a folder tree and recover media from the files in it.

    This is the mode for a USB stick the operating system has already
    mounted, a network share, or a logical export. There is no unallocated
    space to carve here -- the OS hands out files, not the volume behind
    them -- so what this does is classify every file, pull media out of any
    database it finds, and optionally scan inside non-media files for
    embedded media.

    To recover deleted files from a USB stick, point the tool at the block
    device instead: /dev/sdb1 on Linux, /dev/diskNsM on macOS,
    \\\\.\\PhysicalDriveN on Windows. That needs root or Administrator.
    """
    signatures = getattr(args, "signatures", None) or SIGNATURES
    root = os.path.abspath(root)
    out_root = os.path.abspath(out.outdir)
    scanned = 0

    if progress_cb:
        progress_cb(0, 0, 0, "folder")

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        _check_cancel(cancel_cb)
        # Never walk into our own output, and skip the OS bookkeeping folders
        # that only ever yield thumbnails of things already recovered.
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIR_NAMES
                       and not os.path.islink(os.path.join(dirpath, d))
                       and os.path.abspath(os.path.join(dirpath, d)) != out_root]
        for name in filenames:
            _check_cancel(cancel_cb)
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                info = os.stat(full)
            except OSError as exc:
                # Which file, and why. A count alone cannot be acted on.
                note_error(stats, rel, exc)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
                continue
            scanned += 1
            if info.st_size > args.max_file_size:
                stats["skipped_large"] += 1
                continue

            try:
                with open(full, "rb") as fh:
                    head = fh.read(MAX_SIG_SPAN)
            except OSError:
                stats["errors"] += 1
                continue

            rel = os.path.relpath(full, root)
            if appcache.EXO_PIECE.match(name):
                # A piece of a cached video, not a video. The first piece
                # opens with a real file header, so classifying it here
                # would register a truncated clip beside the rejoined one
                # that supersedes it.
                continue

            category, ext = sniff_category(head, name, signatures)
            if category:
                clean = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60].rsplit(".", 1)[0]
                chunks, judged = _read_marked(_file_chunks(full, strict=True),
                                              int(getattr(info, "st_size", 0)) or None)
                rec = out.store(
                    chunks, after_read=judged,
                    category=category, ext=ext or "bin",
                    stem=f"recovered_{out.next_index():04d}_{clean}",
                    engine="folder", source_path=rel.replace(os.sep, "/"),
                    inode=str(getattr(info, "st_ino", "")), fs_type="",
                    partition="", signature="folder-file",
                    mtime=ts(int(info.st_mtime)), atime=ts(int(info.st_atime)),
                    ctime=ts(int(info.st_ctime)),
                    crtime=ts(int(getattr(info, "st_birthtime", 0)) or 0),
                )
                if rec:
                    stats["folder_files"] += 1
                    stats["folder_bytes"] += rec.size
                    if progress_cb and stats["folder_files"] % 10 == 0:
                        progress_cb(stats["folder_files"], 0,
                                    stats["folder_files"], "folder")
                # A zip is both a file and a box. Storing it is no reason to
                # leave the pictures inside unrecovered, which is what
                # stopping here did whenever artifacts were being kept.
                if (getattr(args, "scan_inside", True)
                        and _container_kind(head) is not None):
                    try:
                        with open(full, "rb") as fh:
                            blob = fh.read()
                    except OSError:
                        blob = b""
                    if blob:
                        scan_embedded(blob, out=out, stats=stats,
                                      signatures=signatures,
                                      source_label=rel.replace(os.sep, "/"),
                                      fs_label="", part_label="")
                continue

            if getattr(args, "databases", True) and db_extract is not None \
                    and db_extract.is_sqlite(head):
                with open(full, "rb") as fh:
                    def read(offset, length, _fh=fh):
                        _fh.seek(offset)
                        return _fh.read(length)
                    _extract_database(
                        out, stats, signatures, read=read, size=info.st_size,
                        db_path=rel.replace(os.sep, "/"), inode="",
                        fs_label="", part_label="",
                        freespace=getattr(args, "db_freespace", True),
                        max_size=getattr(args, "max_db_size", 4 * GIB))
                continue

            # The same compound handling the file system engine does: a
            # thumbnail cache, a PDF or an archive on a mounted volume holds
            # exactly what it holds on an image.
            if (getattr(args, "scan_inside", True)
                    and info.st_size <= getattr(args, "max_inside_size", 256 * MIB)):
                try:
                    with open(full, "rb") as fh:
                        blob = fh.read()
                except OSError:
                    blob = b""
                if blob:
                    scan_embedded(blob, out=out, stats=stats,
                                  signatures=signatures,
                                  source_label=rel.replace(os.sep, "/"),
                                  fs_label="", part_label="")
            elif getattr(args, "carve_files", False):
                _carve_inside_file(full, rel, out, args, stats, signatures)

    if getattr(args, "scan_inside", True):
        _rejoin_exoplayer(root, out, stats, signatures)
        _join_loose_streams(root, out, stats, signatures)

    stats["folder_scanned"] = scanned
    if progress_cb:
        progress_cb(stats["folder_files"], stats["folder_files"],
                    stats["folder_files"], "folder")


def _join_loose_streams(root: str, out: OutputManager, stats: dict,
                        signatures) -> int:
    """Videos saved as DASH or HLS segments beside their manifest, joined.

    File by file, such a folder gave a few hundred bytes of initialization
    segment stored as a "video" that plays nothing, and dropped every media
    segment, which hold the pictures. fme/engines/streams.py says how they
    are joined; every byte written is a byte of the files on disk.
    """
    try:
        from fme.engines import streams
        joined, scratch = streams.joined_in(root, stats)
    except Exception as exc:  # noqa: BLE001 - the files are still read one by one
        LOG.warning("segmented streams not joined: %s", exc)
        return 0
    kept = 0
    try:
        for item in joined:
            try:
                with open(item["path"], "rb") as fh:
                    head = fh.read(MAX_SIG_SPAN)
            except OSError:
                continue
            category, ext = sniff_category(head, "stream.mp4", signatures)
            if category is None:
                continue
            manifest = os.path.relpath(item["manifest"], root).replace(os.sep, "/")
            rep = item.get("rep") or {}
            listed = (f"{item['listed']}" if item.get("listed") is not None
                      else "an unknown number of")
            note = (f"{item['format']} stream joined from {item['segments']} of "
                    f"{listed} segment(s) {os.path.basename(manifest)} lists"
                    + (f", with {item['audio_added']} audio stream(s) put beside it "
                       f"without re-encoding" if item["audio_added"] else "")
                    + ("" if item["complete"] else
                       "; segments are missing, so this is the part that was saved, "
                       "not the whole video" if item.get("listed") is not None else
                       "; the manifest's plan did not say how many segments it "
                       "lists, so whether this is the whole video is not known"))
            chunks, judged = _file_marked(item["path"], note)
            rec = out.store(
                chunks, after_read=judged, category=category,
                ext="ts" if ext == "ts" else (ext or "mp4"),
                stem=(f"recovered_{out.next_index():04d}_stream_"
                      + re.sub(r"[^A-Za-z0-9._-]", "_", str(rep.get("id") or "x"))[:40]),
                engine="embedded",
                source_path=f"{manifest}::{rep.get('id', '')}",
                inode="", fs_type="", partition="", deleted=False,
                signature=f"embedded:{item['format'].lower()}-segments", note=note)
            if rec:
                kept += 1
                # The segments it was joined from are what it came out of:
                # kept, hashed and listed, and left out of the gallery's
                # tiles, where an initialization segment plays nothing.
                stats.setdefault("containers", set()).update(
                    os.path.relpath(p, root).replace(os.sep, "/")
                    for p in item.get("parts", []))
                stats["stream_items"] = stats.get("stream_items", 0) + 1
                if not item["complete"]:
                    stats["stream_partial"] = stats.get("stream_partial", 0) + 1
                stats["embedded_files"] += 1
                stats["embedded_bytes"] += rec.size
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if kept:
        LOG.info("Segmented streams  : %d joined from their manifests", kept)
    return kept


def _rejoin_exoplayer(root: str, out: OutputManager, stats: dict,
                      signatures) -> None:
    """Rejoin videos an Android app cached in pieces.

    This runs over the folder rather than over each file, because a cached
    video is not a file: it is a set of pieces named for where they start,
    which only mean something together.
    """
    # The vendored reader first. It resolves an item's name from the cache
    # index rather than leaving it numbered, and -- the reason it is worth
    # preferring -- it joins the segments of a DASH or HLS stream in the
    # order the cached manifest lists them. Streamed video arrives as
    # separate video and audio streams of many short segments, which the
    # older rejoiner, built for contiguous pieces, leaves as fragments.
    if _rejoin_with_vendor(root, out, stats, signatures):
        return

    try:
        items = list(appcache.rejoin_folder(root))
    except (OSError, ValueError, MemoryError):
        stats["errors"] += 1
        return

    for name, payload, info in items:
        stats["exo_items"] += 1
        stats["exo_pieces"] += info.get("pieces_joined", 0)
        if not info.get("all_cached"):
            stats["exo_partial"] += 1
        category, ext = sniff_category(payload[:MAX_SIG_SPAN], name, signatures)
        if category is None:
            continue
        clean = re.sub(r"[^A-Za-z0-9._-]", "_", name.rsplit("/", 1)[-1])[:50]
        note = (f"rejoined from {info['pieces_joined']} of "
                f"{info['pieces_total']} cached piece(s): {info['ending']}"
                + ("; every cached piece was joined, but the cache holds what "
                   "was played, and whether that was the whole video is not "
                   "recorded in the pieces" if info.get("all_cached") else
                   "; the app had only part of it, so this is what was cached, "
                   "not the whole video"))
        # Where the pieces are on disk, not the item's key or URL: two apps'
        # caches can both hold an item 3, and the key alone names neither.
        if info.get("cache_dir"):
            note += f"; cached item {name} in {info['cache_dir']}"
        rec = out.store(
            iter([payload]), category=category, ext=ext or "mp4",
            stem=f"recovered_{out.next_index():04d}_exo_{clean or 'item'}",
            engine="embedded", source_path=info.get("source_path") or f"{name}",
            inode="", fs_type="", partition="", deleted=False,
            signature="embedded:exoplayer-cache", note=note)
        if rec:
            stats["embedded_files"] += 1
            stats["embedded_bytes"] += rec.size


def _rejoin_with_vendor(root: str, out: OutputManager, stats: dict,
                        signatures) -> bool:
    """Rejoin through the vendored reader. False means it could not run.

    Returning False rather than raising matters: the older rejoiner still
    works for ordinary caches, and a cache that this cannot read is not a
    reason to recover nothing from it.
    """
    try:
        from fme.engines import appvideo
    except Exception:  # noqa: BLE001
        return False
    if not appvideo.available():
        return False

    caches = appvideo.find(root)
    if not caches:
        return False

    scratch = tempfile.mkdtemp(prefix="fme_exo_")
    try:
        result = appvideo.rebuild(root, scratch)
    except Exception:  # noqa: BLE001 - fall back rather than lose the cache
        shutil.rmtree(scratch, ignore_errors=True)
        return False

    if not result.get("ok"):
        shutil.rmtree(scratch, ignore_errors=True)
        return False

    kept = 0
    for item in result.get("items", []):
        path = item.get("path") or ""
        if not path or not os.path.isfile(path):
            continue
        stats["exo_items"] += 1
        stats["exo_pieces"] += int(item.get("pieces_joined") or 0)
        if not item.get("complete"):
            stats["exo_partial"] += 1

        try:
            with open(path, "rb") as fh:
                head = fh.read(MAX_SIG_SPAN)
        except OSError:
            continue
        name = item.get("name") or "exoplayer_item"
        category, ext = sniff_category(head, name, signatures)
        if category is None:
            continue

        # What it came from, said where an examiner will read it. A cache
        # holds what was watched, which is often not the whole file.
        where = item.get("key") or ""
        note = (f"rejoined from {item.get('pieces_joined', 0)} of "
                f"{item.get('pieces', 0)} cached piece(s)"
                + (f"; streamed from {where}" if where else "")
                + ("" if item.get("complete") else
                   "; the app had only part of it, so this is what was "
                   "cached, not the whole video"))
        clean = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:50].rsplit(".", 1)[0]
        chunks, judged = _file_marked(path, note)
        rec = out.store(
            chunks, after_read=judged, category=category, ext=ext or "mp4",
            stem=f"recovered_{out.next_index():04d}_exo_{clean or 'item'}",
            engine="embedded", source_path=name,
            inode="", fs_type="", partition="", deleted=False,
            signature="embedded:exoplayer-cache", note=note)
        if rec:
            kept += 1
            stats["embedded_files"] += 1
            stats["embedded_bytes"] += rec.size

    shutil.rmtree(scratch, ignore_errors=True)
    return kept > 0


def _carve_inside_file(full: str, rel: str, out: OutputManager, args,
                       stats: dict, signatures) -> None:
    """Scan a non-media file for embedded media: office documents, mail
    stores, application caches, anything that is a container in practice."""
    try:
        size = os.path.getsize(full)
        if size > 512 * MIB:
            return
        with open(full, "rb") as fh:
            data = fh.read()
    except (OSError, MemoryError):
        stats["errors"] += 1
        return
    read = lambda off, length: data[off:off + length]
    next_allowed = 0
    for pos, sig in scan_buffer(data, signatures=signatures):
        start = pos - sig.offset
        if start < next_allowed or start < 0:
            continue
        cap = min(len(data) - start, args.max_carve_size,
                  MAX_IMAGE_DEFAULT if sig.category == "image" else MAX_VIDEO_DEFAULT)
        parser = PARSERS.get(sig.parser, _p_generic)
        try:
            result = parser(read, start, cap)
        except (struct.error, ValueError, OverflowError, IndexError):
            continue
        if not result or not result[0]:
            continue
        length = min(result[0], len(data) - start)
        ext = result[1] or sig.ext
        category = "image" if ext in IMAGE_EXTS else "video" if ext in VIDEO_EXTS \
            else sig.category
        next_allowed = start + length
        payload = data[start:start + length]
        if sig.encoding:
            payload = decode_payload(payload, sig.encoding) or b""
            if not payload:
                continue
        rec = out.store(
            iter([payload]), category=category, ext=ext,
            stem=f"recovered_{out.next_index():04d}_embedded_{start}",
            engine="folder", source_path=f"{rel.replace(os.sep, '/')}::+{start}",
            signature=sig.key, fs_type="", partition="",
            note="embedded in a non-media file")
        if rec:
            stats["folder_files"] += 1
            stats["folder_bytes"] += rec.size


def _file_chunks(path: str, chunk: int = 4 * MIB,
                 strict: bool = False) -> Iterator[bytes]:
    try:
        with open(path, "rb") as fh:
            while True:
                data = fh.read(chunk)
                if not data:
                    return
                yield data
    except OSError:
        if strict:
            raise
        return


def _file_marked(path: str, note: str = ""):
    """A file's chunks and its after_read, sized from the file system."""
    try:
        expected = os.path.getsize(path)
    except OSError:
        expected = None
    return _read_marked(_file_chunks(path, strict=True), expected, note)


# --------------------------------------------------------------------------
# Logical evidence: L01, Lx01 and AD1
# --------------------------------------------------------------------------
LOGICAL_TIMES = {  # what each format calls the times this program records
    "mtime": ("modified", "wr", "mo"),
    "crtime": ("created", "cr"),
    "atime": ("accessed", "ac"),
}


def run_logical_entries(reader, out: OutputManager, args, stats: dict, signatures,
                        progress_cb=None, cancel_cb=None) -> int:
    """The files of a logical acquisition, each with its own name and times.

    An L01, Lx01 or AD1 holds files, not a disk: what the acquisition tool
    collected, each with its path, its times and the MD5 the tool took. The
    reader lists them (logical_entries), and nothing here asked it to: the
    run carved the container's data stream as if it were a disk, so a
    picture came back -- where it came back at all -- with no name, no
    folder, no date, and nothing tying it to the hash the acquisition
    recorded (v2026.11.315). Each entry is now read through the reader, its
    MD5 checked against the one stored, and stored under its own path.
    """
    image = getattr(reader, "_image", None)
    entries = list(getattr(image, "logical_entries", None) or [])
    if not entries or not hasattr(image, "open_entry"):
        return 0
    LOG.info("Logical evidence: %d entr(ies) listed by %s", len(entries),
             getattr(image, "format", "the acquisition"))
    found = 0
    for entry in entries:
        _check_cancel(cancel_cb)
        size = int(getattr(entry, "size", 0) or 0)
        if not size or getattr(entry, "item_type", None) == 5:     # AD1 folder
            continue
        stats["logical_entries"] = stats.get("logical_entries", 0) + 1
        path = "/" + str(getattr(entry, "path", "") or "/".join(entry.names))
        name = path.rsplit("/", 1)[-1] or "file"
        try:
            fh = image.open_entry(entry)
            head = fh.read(MAX_SIG_SPAN)
        except Exception as exc:  # noqa: BLE001 - one entry, not the run
            stats["logical_unread"] = stats.get("logical_unread", 0) + 1
            LOG.warning("%s could not be read from the acquisition: %s", path, exc)
            continue
        category, ext = sniff_category(head, name, signatures)
        if category is None:
            fh.close()
            continue
        digest = hashlib.md5()
        digest.update(head)
        seen = [len(head)]
        failed: list = []

        def body(first=head, handle=fh):
            yield first
            try:
                while True:
                    try:
                        piece = handle.read(4 * MIB)
                    except Exception as exc:  # noqa: BLE001 - this entry only
                        # A damaged compressed entry raises zlib.error or
                        # ValueError, not OSError: it ended the whole phase,
                        # and every entry after it went unread (v2026.11.317).
                        # What was read is kept, and marked short.
                        failed.append(str(exc) or type(exc).__name__)
                        break
                    if not piece:
                        break
                    digest.update(piece)
                    seen[0] += len(piece)
                    yield piece
            finally:
                handle.close()

        listed_md5 = (getattr(entry, "md5", None) or "").lower()

        def judged(stored_size, path=path, size=size, listed_md5=listed_md5):
            """The row's own account of the read, written with the row.

            A short read was stored as if whole, its MD5 check silently
            skipped; a mismatch was a count in the summary and nothing on
            the file's own row (v2026.11.317).
            """
            if seen[0] != size:
                stats["logical_short"] = stats.get("logical_short", 0) + 1
                why = f": {failed[0]}" if failed else ""
                LOG.warning("%s read %d of the %d bytes the acquisition lists%s",
                            path, seen[0], size, why)
                return {"bounded": "short",
                        "note": (f"logical entry read short: {seen[0]:,} of the "
                                 f"{size:,} bytes the acquisition lists{why}; its "
                                 f"recorded MD5 could not be checked")}
            if not listed_md5:
                return {"note": "logical entry; the acquisition recorded no MD5"}
            if digest.hexdigest() != listed_md5:
                stats["logical_md5_mismatch"] = stats.get("logical_md5_mismatch", 0) + 1
                LOG.warning("%s does not match the MD5 the acquisition recorded", path)
                return {"note": (f"DOES NOT MATCH the MD5 the acquisition recorded "
                                 f"({listed_md5}); read as {digest.hexdigest()}")}
            stats["logical_md5_verified"] = stats.get("logical_md5_verified", 0) + 1
            return {"note": "logical entry; matches the MD5 the acquisition recorded"}

        times = getattr(entry, "times", None) or {}
        stamps = {}
        for field, names in LOGICAL_TIMES.items():
            for key in names:
                if times.get(key) not in (None, ""):
                    stamps[field] = ts(int(times[key]))
                    break
        clean = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60].rsplit(".", 1)[0]
        try:
            rec = out.store(
                body(), category=category, ext=ext or "bin",
                stem=f"recovered_{out.next_index():04d}_{clean}",
                engine="logical", source_path=path, inode="",
                fs_type=str(getattr(image, "format", "") or "logical"),
                partition="", signature="logical",
                deleted=getattr(entry, "item_type", None) == 2,
                mtime=stamps.get("mtime", ""), crtime=stamps.get("crtime", ""),
                atime=stamps.get("atime", ""), after_read=judged)
        except Exception as exc:  # noqa: BLE001 - one entry, not the phase
            stats["logical_unread"] = stats.get("logical_unread", 0) + 1
            LOG.warning("%s could not be stored: %s", path, exc)
            continue
        if rec:
            found += 1
            stats["logical_files"] = stats.get("logical_files", 0) + 1
            if progress_cb and found % 25 == 0:
                progress_cb(0, 0, len(out.records), "logical")
    return found
