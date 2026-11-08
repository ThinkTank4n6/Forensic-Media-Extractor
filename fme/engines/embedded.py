"""
embedded.py -- Media inside other files: documents, archives, compressed streams,
SQLite databases and Snapchat bundles, found and carved out.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import re
import struct
import zlib
from typing import Callable, Iterator, Optional, Sequence
from fme.engines import appcache
from fme.engines import containers
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
    GIB, LOG, MIB, db_extract,
)
from fme.engines.image_readers import (  # noqa: F401
    BytesReader,
)
from fme.engines.signatures import (  # noqa: F401
    MAX_SIG_SPAN, scan_buffer,
)
from fme.engines.length_parsers import (  # noqa: F401
    STREAM_PARSERS, decode_payload, sniff_category,
)
from fme.engines.carve_judging import (  # noqa: F401
    _carve_candidate,
)



_QT_BOX_SIGNATURES = frozenset({"mov_moov", "mov_mdat", "mov_wide",
                                "mov_skip", "mov_pnot"})
_BMFF_PARENT_BOXES = frozenset({b"ftyp", b"moov", b"mdat", b"wide", b"skip",
                                b"pnot", b"free"})


def _scan_embedded_inner(data: bytes, *, out: OutputManager, stats: dict,
                  signatures, source_label: str, fs_label: str = "",
                  part_label: str = "", inode: str = "",
                  deleted: bool = False, max_carve_size: int = 4 * GIB,
                  skip_first_byte: bool = True) -> int:
    """Pull media out of a file that is itself a container.

    This is the equivalent of mounting compound files before looking for
    pictures: office documents, zips, mail stores, thumbnail caches, and the
    EXIF thumbnail sitting inside a photo. A tool that runs after a forensic
    suite has already mounted compound files sees all of these; one that only
    looks at whole files does not, which is exactly the kind of gap that shows
    up as tens of thousands of missing images.

    Two routes: a real zip is opened as a zip, because its members are
    deflated and invisible to a byte scan; anything else is scanned for
    signatures from offset 1, so the container's own header is not re-found.
    """
    found = 0

    # Formats that hold media the way a database does: the bytes are in
    # there, but not laid out where a signature scan would find them.
    if appcache.is_lzc(data[:8]):
        return _scan_lzc(data, out=out, stats=stats, signatures=signatures,
                         source_label=source_label, fs_label=fs_label,
                         part_label=part_label, inode=inode, deleted=deleted)

    document = containers.document_kind(data[:4096], source_label)
    if document:
        found += _scan_document(document, data, out=out, stats=stats,
                                signatures=signatures,
                                source_label=source_label, fs_label=fs_label,
                                part_label=part_label, inode=inode,
                                deleted=deleted)
        if document in ("thumbcache", "pdf", "mhtml"):
            return found          # a scan of the rest would only re-find these

    container = _container_kind(data)
    if container:
        payload = data
        if container in ("gzip", "bzip2", "xz"):
            # A compression wrapper hides whatever is inside from every byte
            # scan, whichever algorithm it used.
            payload = {"gzip": _gunzip, "bzip2": _bunzip2,
                       "xz": _unxz}[container](data)
            if not payload:
                return 0
            container = _container_kind(payload) or "raw"
            if container == "raw":
                # Recurse on the decompressed bytes rather than the wrapper.
                return _scan_embedded_inner(payload, out=out, stats=stats,
                                     signatures=signatures,
                                     source_label=f"{source_label}::{container}",
                                     fs_label=fs_label, part_label=part_label,
                                     inode=inode, deleted=deleted,
                                     max_carve_size=max_carve_size,
                                     skip_first_byte=False)
        if container == "zip":
            zipped = _scan_zip_bytes(payload, out=out, stats=stats,
                                     signatures=signatures,
                                     source_label=source_label,
                                     fs_label=fs_label, part_label=part_label,
                                     inode=inode, deleted=deleted)
            if zipped is not None:
                return found + zipped
            # Would not open as a zip: the byte scan below, on the bytes the
            # zip test was given (decompressed, when it was wrapped).
            if payload is not data:
                data, skip_first_byte = payload, False
        if container == "7z":
            return _scan_7z_bytes(
                payload, out=out, stats=stats, signatures=signatures,
                source_label=source_label, fs_label=fs_label,
                part_label=part_label, inode=inode, deleted=deleted)
        if container == "rar":
            # RAR needs a licensed unpacker that is not in the standard
            # library. Say so once rather than silently finding nothing:
            # C4ALL has a RarClass and will recover these, this will not.
            stats["rar_skipped"] = stats.get("rar_skipped", 0) + 1
            return 0

    # Judged by _carve_candidate, the carve's own judge: the length, the
    # partial rule, the weak-parser cut and the bounded label are the same
    # for a file inside a container as for one in unallocated space. This
    # loop used to carry a copy of that logic with none of them, so a
    # truncated JPEG inside a container still ran over the files after it.
    reader = BytesReader(data, name=source_label)
    # Normally start at 1 so a container does not re-find its own header. When
    # the bytes are the *result* of decompressing something, the media starts
    # at offset 0 and skipping it loses the file entirely.
    next_allowed = 1 if skip_first_byte else 0
    # In an ISO base media file, a box named mdat, moov, wide, skip or pnot is
    # one of the file's own boxes, not a QuickTime file stored inside it.
    # Matching them reported every carved MP4's mdat a second time, as an
    # "embedded" .mov of almost the same size. A video genuinely stored
    # inside one still begins with its own ftyp, which is still matched.
    own_boxes = data[4:8] in _BMFF_PARENT_BOXES
    hits = list(scan_buffer(data, signatures=signatures))
    # The same for a stream: every packet boundary of an MPEG transport or
    # program stream begins another valid stream, so a carved .ts was found
    # again inside itself from its eighth packet on, as an "embedded" video
    # that is its own tail. A stream does not contain another stream.
    own_stream = any(pos - sig.offset == 0 and sig.parser in STREAM_PARSERS
                     for pos, sig in hits)
    for pos, sig in hits:
        start = pos - sig.offset
        if start < next_allowed:
            continue
        if own_boxes and sig.key in _QT_BOX_SIGNATURES:
            continue
        if own_stream and sig.parser in STREAM_PARSERS:
            continue
        judged = _carve_candidate(reader, sig, start, len(data),
                                  max_carve_size, signatures, stats)
        if judged is None:
            continue
        length, ext, category, bounded = judged
        # Stepped over whether or not it is kept, as the carve does: a file
        # too small to keep is still a file, and what is inside it is not
        # a second one.
        next_allowed = start + length
        if length < _floor(category):
            continue
        payload = data[start:start + length]
        if sig.encoding:
            payload = decode_payload(payload, sig.encoding) or b""
            if not payload:
                continue
        rec = out.store(
            iter([payload]), category=category, ext=ext,
            stem=f"recovered_{out.next_index():04d}_embedded_{start}",
            engine="embedded", source_path=f"{source_label}::+{start}",
            inode=inode, fs_type=fs_label, partition=part_label,
            deleted=deleted, signature=f"embedded:{sig.key}", bounded=bounded,
            note="embedded in another file (compound file or thumbnail)")
        if rec:
            found += 1
            stats["embedded_files"] += 1
            stats["embedded_bytes"] += rec.size
    return found



def mark_containers(out: OutputManager, stats: dict) -> int:
    """Flag the files that other media came out of.

    A container is evidence like anything else: its hashes matter and it
    stays in the manifest and the reports. It is simply not a picture, so
    the gallery hides it unless asked.
    """
    labels = stats.pop("containers", None) or set()
    marked = 0
    for record in out.records:
        # The container itself, not what came out of it: a member records
        # its own path as "album.zip::photo0.jpg", and those are exactly
        # the pictures the gallery is meant to show.
        where = record.source_path or ""
        if where and "::" not in where and where in labels:
            record.container = True
            marked += 1
    # The set is working state: removed above so it never reaches the
    # manifest, which is JSON and cannot hold one. The count is what a
    # reader of the summary wants in any case.
    stats["container_files"] = marked
    return marked


def note_error(stats: dict, where: str, why: object) -> None:
    """Record why a file failed, not only that one did.

    A count tells an examiner that eleven files could not be read; it does
    not tell them which, or whether it matters, and it makes "try those
    again" impossible to offer. The list is capped because a corrupt image
    can fail on everything, and a hundred thousand reasons is not a
    report.
    """
    stats["errors"] = stats.get("errors", 0) + 1
    reasons = stats.setdefault("error_reasons", [])
    if len(reasons) < 500:
        reasons.append({"where": where, "why": str(why)[:200]})


def scan_embedded(data: bytes, *, out: OutputManager, stats: dict, **kwargs) -> int:
    """Media inside another file, and a note that the other file is a box.

    Everything that pulls media out of something goes through here, so this
    is the one place that can record that a file turned out to be a
    container. The gallery uses it to leave such files out of the tiles: a
    zip whose photographs are already listed one by one is a tile showing
    nothing. It stays in the manifest and the reports, with its hashes,
    because it is still evidence.
    """
    found = _scan_embedded_inner(data, out=out, stats=stats, **kwargs)
    if found:
        label = (kwargs.get("source_label") or "").split("::")[0]
        if label:
            stats.setdefault("containers", set()).add(label)
    return found


def _scan_lzc(data: bytes, *, out: OutputManager, stats: dict, signatures,
              source_label: str, fs_label: str, part_label: str, inode: str,
              deleted: bool) -> int:
    """Media inside a Snapchat content bundle."""
    if appcache.zstd_available() is None:
        # Say what is missing rather than finding nothing: these bundles are
        # a third of the content folder on a phone that has Snapchat.
        stats["lzc_skipped"] = stats.get("lzc_skipped", 0) + 1
        return 0

    found = 0
    for index, frame in enumerate(appcache.lzc_frames(data), start=1):
        if not frame:
            continue
        category, ext = sniff_category(frame[:MAX_SIG_SPAN], "", signatures)
        if category is None:
            # A frame can be an asset bundle rather than a media file, so
            # what is inside it still has to be looked for.
            found += scan_embedded(frame, out=out, stats=stats,
                                   signatures=signatures,
                                   source_label=f"{source_label}::frame{index}",
                                   fs_label=fs_label, part_label=part_label,
                                   inode=inode, deleted=deleted,
                                   skip_first_byte=False)
            continue
        rec = out.store(
            iter([frame]), category=category, ext=ext or "bin",
            stem=f"recovered_{out.next_index():04d}_lzc_{index}",
            engine="embedded", source_path=f"{source_label}::frame{index}",
            inode=inode, fs_type=fs_label, partition=part_label,
            deleted=deleted, signature="embedded:snapchat-lzc",
            note="frame of a Snapchat content bundle")
        if rec:
            found += 1
            stats["embedded_files"] += 1
            stats["embedded_bytes"] += rec.size
    return found


def _scan_document(kind: str, data: bytes, *, out: OutputManager, stats: dict,
                   signatures, source_label: str, fs_label: str,
                   part_label: str, inode: str, deleted: bool) -> int:
    """Media out of a thumbnail cache, a PDF, or a saved web page."""
    readers = {
        "thumbcache": (containers.thumbcache_entries, "thumbcache-entry",
                       "cached thumbnail; the original file may be long gone"),
        "pdf": (containers.pdf_images, "pdf-image",
                "image inside a PDF, verbatim as the producer wrote it"),
        "mhtml": (containers.mhtml_parts, "saved-page-part",
                  "resource of a saved web page"),
        "html": (containers.html_data_uris, "data-uri",
                 "embedded in a page as a data URI"),
    }
    reader, signature, note = readers[kind]
    found = 0
    walk: dict = {}
    try:
        if kind == "thumbcache":
            # The walk says where it stopped; a cache it could only partly
            # read used to look the same as one that held nothing more.
            items = list(reader(data, walk))
        else:
            items = list(reader(data))
    except (struct.error, ValueError, MemoryError, zlib.error):
        stats["errors"] += 1
        return 0
    if walk.get("stopped"):
        stats["thumbcache_stopped"] = stats.get("thumbcache_stopped", 0) + 1
        LOG.warning("thumbnail cache %s: walk stopped at %s (%s); entries "
                    "after that point were not read", source_label,
                    walk.get("stopped_at"), walk.get("stopped"))

    if kind == "pdf":
        try:
            items += list(containers.pdf_embedded_files(data))
        except (struct.error, ValueError, MemoryError, zlib.error):
            stats["errors"] += 1

    for name, payload, info in items:
        if info.get("raster_only"):
            # Images a PDF stores as pixels rather than as a file. Counted
            # rather than rebuilt: reconstructed bytes would carry this
            # tool's hashes, not the evidence's.
            stats["pdf_raster_skipped"] = (stats.get("pdf_raster_skipped", 0)
                                           + info["raster_only"])
            continue
        if not payload:
            continue
        category, ext = sniff_category(payload[:MAX_SIG_SPAN], name, signatures)
        if category is None:
            continue
        clean = re.sub(r"[^A-Za-z0-9._-]", "_", name.rsplit("/", 1)[-1])[:50]
        # A thumbnail's cache identifier is the only handle on what it was:
        # the Windows Search index records the same number against the item's
        # real path, so keeping it here is what makes that join possible
        # later.
        detail = note
        if info.get("cache_id"):
            detail = f"{note}; cache id {info['cache_id']}"
        rec = out.store(
            iter([payload]), category=category, ext=ext or info.get("ext", "bin"),
            stem=f"recovered_{out.next_index():04d}_{kind}_{clean or found}",
            engine="embedded", source_path=f"{source_label}::{name or found}",
            inode=inode, fs_type=fs_label, partition=part_label,
            deleted=deleted, signature=f"embedded:{signature}", note=detail)
        if rec:
            found += 1
            stats["embedded_files"] += 1
            stats["embedded_bytes"] += rec.size
    return found


def _container_kind(data: bytes) -> Optional[str]:
    """Identify a container whose contents a byte scan cannot see."""
    if data[:4] == b"PK\x03\x04" or data[:2] == b"PK":
        return "zip"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:3] == b"BZh" and data[3:4].isdigit():
        return "bzip2"
    if data[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if data[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if data[:7] == b"Rar!\x1a\x07\x00" or data[:8] == b"Rar!\x1a\x07\x01\x00":
        return "rar"
    return None


def _gunzip(data: bytes, limit: int = 512 * MIB) -> bytes:
    import zlib
    try:
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, limit)
    except (zlib.error, MemoryError):
        return b""


def _bunzip2(data: bytes, limit: int = 512 * MIB) -> bytes:
    import bz2
    try:
        return bz2.BZ2Decompressor().decompress(data, limit)
    except (OSError, ValueError, EOFError, MemoryError):
        return b""


def _unxz(data: bytes, limit: int = 512 * MIB) -> bytes:
    import lzma
    try:
        return lzma.LZMADecompressor().decompress(data, limit)
    except (lzma.LZMAError, EOFError, MemoryError):
        return b""


def _scan_7z_bytes(data: bytes, **kwargs) -> int:
    """7z members, when py7zr is installed.

    Unlike RAR there is a usable reader on PyPI, so this works when it is
    present and says what is missing when it is not, rather than quietly
    finding nothing inside a 7z full of pictures.
    """
    try:
        import py7zr
    except ImportError:
        kwargs["stats"]["sevenzip_skipped"] = \
            kwargs["stats"].get("sevenzip_skipped", 0) + 1
        return 0

    import io as _io
    out, stats = kwargs["out"], kwargs["stats"]
    signatures = kwargs["signatures"]
    found = 0
    try:
        with py7zr.SevenZipFile(_io.BytesIO(data)) as archive:
            for name, handle in (archive.readall() or {}).items():
                payload = handle.read()
                if not payload:
                    continue
                head = payload[:MAX_SIG_SPAN]
                category, ext = sniff_category(
                    head, name.rsplit("/", 1)[-1], signatures)
                if category is None:
                    continue
                clean = re.sub(r"[^A-Za-z0-9._-]", "_",
                               name.rsplit("/", 1)[-1])[:50].rsplit(".", 1)[0]
                rec = out.store(
                    iter([payload]), category=category, ext=ext or "bin",
                    stem=f"recovered_{out.next_index():04d}_7z_{clean}",
                    engine="embedded",
                    source_path=f"{kwargs['source_label']}::{name}",
                    inode=kwargs.get("inode", ""),
                    fs_type=kwargs.get("fs_label", ""),
                    partition=kwargs.get("part_label", ""),
                    deleted=kwargs.get("deleted", False),
                    signature="embedded:7z-entry",
                    note="member of a 7z archive")
                if rec:
                    found += 1
                    stats["embedded_files"] += 1
                    stats["embedded_bytes"] += rec.size
    except Exception:  # noqa: BLE001 - encrypted or damaged archive
        stats["errors"] += 1
    return found


def _scan_zip_bytes(data: bytes, *, out: OutputManager, stats: dict,
                    signatures, source_label: str, fs_label: str,
                    part_label: str, inode: str, deleted: bool) -> int:
    """Media inside a zip container: docx, xlsx, pptx, epub, apk, plain zips."""
    import io as _io
    import zipfile

    found = 0
    try:
        archive = zipfile.ZipFile(_io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError,
            MemoryError, EOFError) as exc:
        # Not a zip that can be opened -- no central directory (a carved or
        # truncated one), or just bytes that begin "PK". None tells the
        # caller to scan the bytes instead: returning 0 here, as this did,
        # meant nothing inside was looked for at all.
        LOG.info("%s does not open as a zip (%s); scanning its bytes instead",
                 source_label, exc)
        stats["zip_unopened"] = stats.get("zip_unopened", 0) + 1
        return None
    try:
        with archive:
            for entry in archive.infolist():
                if entry.is_dir() or entry.file_size == 0:
                    continue
                try:
                    with archive.open(entry) as fh:
                        head = fh.read(MAX_SIG_SPAN)
                except Exception:  # noqa: BLE001 - encrypted or damaged member
                    stats["errors"] += 1
                    continue
                name = entry.filename.rsplit("/", 1)[-1]
                category, ext = sniff_category(head, name, signatures)
                if category is None:
                    # A zip inside a zip, or a document inside an archive:
                    # recurse rather than stopping at the first layer.
                    if _container_kind(head) and entry.file_size <= 256 * MIB:
                        try:
                            nested = archive.read(entry)
                        except Exception:  # noqa: BLE001
                            continue
                        found += scan_embedded(
                            nested, out=out, stats=stats, signatures=signatures,
                            source_label=f"{source_label}::{entry.filename}",
                            fs_label=fs_label, part_label=part_label,
                            inode=inode, deleted=deleted,
                            skip_first_byte=False)
                    continue
                try:
                    payload = archive.read(entry)
                except Exception:  # noqa: BLE001
                    stats["errors"] += 1
                    continue
                clean = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:50].rsplit(".", 1)[0]
                rec = out.store(
                    iter([payload]), category=category, ext=ext or "bin",
                    stem=f"recovered_{out.next_index():04d}_zip_{clean}",
                    engine="embedded",
                    source_path=f"{source_label}::{entry.filename}",
                    inode=inode, fs_type=fs_label, partition=part_label,
                    deleted=deleted, signature="embedded:zip-entry",
                    note="member of a compound file")
                if rec:
                    found += 1
                    stats["embedded_files"] += 1
                    stats["embedded_bytes"] += rec.size
    except (zipfile.BadZipFile, OSError, MemoryError, EOFError) as exc:
        # Opened, then failed part way: what was read is kept, and the rest
        # is said rather than dropped without a word.
        LOG.warning("zip %s stopped part way (%s); members after that point "
                    "were not examined", source_label, exc)
        stats["zip_damaged"] = stats.get("zip_damaged", 0) + 1
        stats["errors"] += 1
        return found
    return found


def _tsk_read(entry, offset: int, length: int) -> bytes:
    """Random-access read from a TSK file, for the database parser."""
    try:
        return entry.read_random(offset, length)
    except (OSError, IOError, RuntimeError, ValueError):
        return b""


def _extract_database(out: OutputManager, stats: dict, signatures, *, read,
                      size: int, db_path: str, inode: str, fs_label: str,
                      part_label: str, freespace: bool, max_size: int,
                      deleted: bool = False) -> None:
    """Pull every media blob out of one SQLite database."""
    if size > max_size or size < 512:
        return
    try:
        hits = db_extract.extract(read, size, freespace=freespace,
                                  signatures=signatures)
        for hit in hits:
            found = db_extract.looks_like_media(hit.data, signatures)
            if not found:
                continue
            category, ext = found
            label = f"{hit.table}.{hit.column}"
            if hit.rowid is not None:
                label += f"_{hit.rowid}"
            stem = (f"recovered_{out.next_index():04d}_db_"
                    + re.sub(r"[^A-Za-z0-9._-]", "_", label)[:50])
            live = hit.source in ("record", "record-short")
            if not live:
                stem = "deleted_" + stem
            if hit.source == "record-short":
                note = ("blob in a live row; SHORT: the database's overflow "
                        "pages for it are missing or damaged, so this is only "
                        "the part that could be read")
            elif live:
                note = "blob in a live row"
            else:
                note = "recovered from database free space; may be partial"
            rec = out.store(
                iter([hit.data]), category=category, ext=ext, stem=stem,
                engine="database",
                source_path=f"{db_path}::{hit.table}.{hit.column}"
                            + (f" (rowid {hit.rowid})" if hit.rowid is not None else ""),
                inode=inode, fs_type=fs_label, partition=part_label,
                deleted=deleted or not live,
                signature=f"sqlite:{hit.source}",
                bounded="short" if hit.source == "record-short" else "",
                note=note,
            )
            if rec:
                stats["db_files"] += 1
                stats["db_bytes"] += rec.size
    except (ValueError, struct.error, OSError, MemoryError) as exc:
        LOG.debug("database %s: %s", db_path, exc)
        stats["errors"] += 1
