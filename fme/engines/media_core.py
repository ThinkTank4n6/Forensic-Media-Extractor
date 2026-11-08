
#!/usr/bin/env python3
"""
media_core.py -- the Forensic Media Extractor engine.

Multi-filesystem, multi-format media extraction.

The generalization of fat32_core.py: same ImageSource conventions, same
FileSignature / progress_cb / human_size contracts, but it ingests any
forensic image format and recovers image and video files from FAT12/16/32,
exFAT, NTFS, HFS+/HFSX and APFS volumes -- or from raw unallocated space
when the file system is gone entirely.

Containers
    raw / dd / img / bin, split raw (.001, .002, ...), EnCase E01/Ex01
    (pyewf), and optionally VMDK / VHD / QCOW (pyvmdk / pyvhdi / pyqcow).

Two engines, either or both
    FS engine     walks the volume system and mounts every recognized file
                  system through The Sleuth Kit (pytsk3), recovering
                  allocated AND deleted entries with path, inode and MAC
                  times. If pytsk3 is missing it falls back to fat32_core
                  for FAT32 volumes, so the tool still works unassisted.
    Carve engine  header detection plus structure-aware length parsing
                  across the whole image, independent of the file system.

Both engines write through one output manager that de-duplicates by
SHA-256, so a file recovered twice is stored once.

Interop with fat32_core
    * ImageSource objects from fat32_core (RawFileSource, EwfSource,
      OffsetSource) can be passed straight to wrap_source().
    * fat32_core.FileSignature objects -- including the ones the GUI's
      Custom Signatures tab produces via signature_from_hex() -- are
      accepted as extra_signatures and carved by header/footer.
    * progress_cb(done, total, recovered, phase) mirrors the fat32_core
      carve callback with one extra argument naming the current phase.
    * human_size() is re-exported so existing callers keep working.

Optional dependencies (all degrade gracefully):
    pip install pytsk3            # FAT/exFAT/NTFS/HFS+/APFS parsing
    pip install libewf-python     # E01 / Ex01            (module: pyewf)
    pip install libvmdk-python libvhdi-python libqcow-python

The source image is opened read-only and never written to.

Layout (v2026.11.314)
    This file was 8,829 lines. It now holds the options, run_extraction(),
    the end of a run and the command line; the rest is split out, each
    module using only the ones above it in this list, with no cycles:

        engine_common     constants, optional modules, cancel and progress
        image_readers     raw, split, device, EWF and vendored readers
        signatures        what a file's first bytes look like
        length_parsers    how long a carved file is
        carve_judging     the one judge of a carved file's length
        embedded          media inside documents, archives and databases
        fs_walk           TSK, the FAT fallback, partitions and volumes
        deleted_recovery  the MFT, FAT and exFAT deleted entries
        carving           the signature carve, serial and parallel
        volume_survey     inspect_image(), encrypted and unopened volumes
        sources           packages, folders, rejoined streams

    Every name is re-exported here, so media_core.X still works. To replace
    a name in a test, use tests/_engine.py: assigning media_core.X does not
    reach the module that calls it (tests/test_patch_targets.py).

CLI:
    python3 media_core.py -i evidence.E01 -o ./out
    python3 media_core.py -i disk.dd -o ./out --mode carve --align 512
"""

from __future__ import annotations

# Allow this file to be run directly -- "python fme/engines/media_core.py" --
# as well as imported. Run as a script there is no package context, so the
# project root goes on the path before the package imports below.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import contextlib
import csv
import datetime as _dt
import hashlib
import errno
import io
import json
import logging
import os
import functools
import itertools
import re
import shutil
import tempfile
import struct
import threading
import uuid
import zlib
import stat
import sys
import time
import types
from dataclasses import dataclass, asdict
from typing import Callable, Iterator, Optional, Sequence

# --------------------------------------------------------------------------
# Optional third-party modules
# --------------------------------------------------------------------------
from fme.engines import appcache
from fme.engines import carve_judge
from fme.engines import containers
from fme.engines import exif_read
from fme.engines import ntfs_mft
from fme.engines import timeutil
from fme.engines import vendor_fs




# Output management, hashing and the manifest live in fme/engines/output.py;
# re-exported so media_core.<name> keeps working everywhere.
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

# Split out in v2026.11.314; every name is re-exported so media_core.X
# keeps working. Patch a name where it is looked up, not here
# (tests/test_patch_targets.py).
from fme.engines.engine_common import (  # noqa: F401
    Canceled, GIB, KIB, LOG,
    MAX_IMAGE_DEFAULT, MAX_VIDEO_DEFAULT, MIB, WALK_DEPTH_LIMIT,
    _check_cancel, _too_deep, db_extract, fat32_core,
    pyewf, pyqcow, pytsk3, pyvhdi,
    pyvmdk,
)
from fme.engines.image_readers import (  # noqa: F401
    is_aff4,
    BytesReader, DeviceReader, EWF_PROBE, ImageReader,
    LibyalImageReader, RawImageReader, SegmentedReader, SourceReader,
    VENDOR_ONLY_EXTENSIONS, VendorImageReader, _ewf_reads_nothing, _ewf_samples,
    _natural_key, _open_ewf, human, is_device_path,
    open_image, read_ufd_segments, wrap_source,
)
from fme.engines.signatures import (  # noqa: F401
    AUDIO_EXTS, IMAGE_EXTS, MAX_SIG_SPAN, SIGNATURES,
    SIG_BY_KEY, Signature, VIDEO_EXTS, _STREAM_PACKET,
    _TS_STRIDE, _ts_positions, scan_buffer,
)
from fme.engines.length_parsers import (  # noqa: F401
    AMR_FRAME_BYTES, AMR_WB_FRAME_BYTES, ARTIFACT_SIGNATURES, B64_ALPHABET,
    B64_SKIP, HEX_ALPHABET, HEX_SKIP, ISOBMFF_AUDIO_EXTS,
    ISOBMFF_CONTENT_BOXES, ISOBMFF_IMAGE_EXTS, ISOBMFF_TOP_BOXES, MP3_BITRATES_V1_L3,
    MP3_BITRATES_V2_L3, MP3_RATES, PARSERS, Reader,
    STREAM_PARSERS, WEAK_PARSERS, _JPEG_FIRST_MARKERS, _TIFF_TYPE_SIZE,
    _brand_to_ext, _encoded_run, _gif_subblocks, _isobmff_category,
    _mp3_frame_length, _p_aiff, _p_amr, _p_asf,
    _p_base64, _p_bmp, _p_bplist, _p_caf,
    _p_dds, _p_ebml, _p_emf, _p_exr,
    _p_flv, _p_footer, _p_generic, _p_gif,
    _p_hex, _p_icns, _p_ico, _p_isobmff,
    _p_isobmff_qt, _p_jpeg, _p_jxr, _p_m2ts,
    _p_mp3, _p_mpegps, _p_mpegts, _p_ogg,
    _p_pcx, _p_pdf, _p_png, _p_psd,
    _p_riff, _p_silk, _p_sqlite, _p_swf,
    _p_tiff, _p_wmf, _p_zip, _plist_candidates,
    _read_vint, _skip_entropy, _tiff_inline, _tiff_values,
    _validate_decoded, decode_payload, header_is_blank, magic_matched,
    parse_size, signatures_from_fat32, sniff_category, ts,
)
from fme.engines.carve_judging import (  # noqa: F401
    _carve_candidate, _truncate_at_next_signature,
)
from fme.engines.embedded import (  # noqa: F401
    _BMFF_PARENT_BOXES, _QT_BOX_SIGNATURES, _bunzip2, _container_kind,
    _extract_database, _gunzip, _scan_7z_bytes, _scan_document,
    _scan_embedded_inner, _scan_lzc, _scan_zip_bytes, _tsk_read,
    _unxz, mark_containers, note_error, scan_embedded,
)
from fme.engines.fs_walk import (  # noqa: F401
    DELETED_FOLDER_MAX_CLUSTERS, FS_TYPE_NAMES, _BAD_SHORT_NAME, _FS_METADATA,
    _ReaderSource, _TSKImg, _TSK_KINDS, _TSK_NAME_FLAG_UNALLOC,
    _ZeroNoted, _deleted_folder_readable, _embedded_target, _fat32_all_files,
    _fat32_chunks, _fat32_walk, _fat_folder_children, _fat_free_run,
    _fat_overwrite_note, _fat_volumes, _fs_chunks, _list_folder,
    _logical_partitions, _looks_like_folder, _looks_like_volume, _mbr_sector_size,
    _name_on_reused_record, _name_unallocated, _note_unread, _open_fat32,
    _open_fat_volumes, _open_fs_at, _partition_offsets, _salvaging_reader,
    _short_after_read, _shown_deleted, _table_labels, _table_partitions,
    _tsk_fat_lost_letter, _tsk_label, _volume_entry, _zero_after_read,
    _zero_filled_since, _zero_mark, fs_label_for, is_fs_metadata,
    run_fat32_fallback, run_fs_engine, tsk_enabled, tsk_kinds,
    walk_filesystem,
)
from fme.engines.deleted_recovery import (  # noqa: F401
    _exfat_folder_paths, _fat_deleted_entries, _mft_not_read, _mft_note,
    _ntfs_volumes, _vendor_time, ewfprobe_available, run_exfat_deleted_recovery,
    run_fat_deleted_recovery, run_mft_recovery, run_vendor_fs,
)
from fme.engines.carving import (  # noqa: F401
    run_raid_carve, RAID_PARTITION,
    CarveIncomplete, PROGRESS_FILE, STRIPE_MAX_BYTES, STRIPE_STALL_SECONDS,
    WORKER_RETRIES, _CarveStop, _PROGRESS_LOCK, _SkipListed,
    _SpecSignature, _WORKER, _already_carved, _beat_on_every_read,
    _carve_chunks, _carve_stripes, _carved_reach, _detect_range,
    _first_bad, _is_carved, _note_sizeless, _parallel_carve_body,
    _payload_chunks, _progress_at, _rescan_inside, _save_progress,
    _scan_carved, _stop_pool, _store_carved, _stripe_tasks,
    _update_progress, _worker_carve, _worker_init, active_signatures,
    clear_progress, default_workers, phase_done, phase_skipped,
    read_progress, run_carve_engine, run_carve_engine_parallel,
)
from fme.engines.volume_survey import (  # noqa: F401
    ENCRYPTED_VOLUMES, _LDM_GUIDS, _LVM_GUID, _device_fields,
    _inspect_volumes, _locked_apfs, _locked_message, _looks_like_mbr,
    _volume_label, encrypted_volumes, encryption_at, unopened_containers,
)
from fme.engines.sources import (  # noqa: F401
    run_logical_entries, LOGICAL_TIMES,
    PACKAGE_EXTENSIONS, SKIP_DIR_NAMES, _archive_chunks, _archive_kind,
    _carve_inside_file, _entry_head, _entry_info, _file_chunks,
    _join_loose_streams, _open_archive, _rejoin_exoplayer, _rejoin_with_vendor,
    is_package, run_folder_engine, run_package_engine,
)

__version__ = "2.6.4"          # the engine's own version


def _release() -> str:
    """The release number of the package as a whole, from the VERSION file.

    The engine version tracks changes to the carving and extraction code; the
    release number tracks the package handed over. They move independently,
    so both are reported rather than one standing in for the other.
    """
    # VERSION ships inside the package and also sits at the root of the
    # source tree, so both an installed copy and a checkout can find it.
    folder = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        path = os.path.join(folder, "VERSION")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return "v" + fh.read().strip()
            except OSError:
                break
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return "v?"


RELEASE = _release()


def tool_provenance() -> dict:
    """Which build produced this, in a form that survives the case.

    A manifest that names the tool but not its version answers "what
    made this" and not "which one" -- and the second is the question
    somebody will eventually have to answer about a case rather than
    about a program. A case processed under one build and reopened
    under another left nothing behind to say which did the work.

    The fingerprint is included because a version number says what was
    meant to be installed, not what was. Two machines reporting the
    same version and the same fingerprint ran the same code.
    """
    import platform

    try:
        from fme.cli import code_fingerprint

        mark = code_fingerprint()
    except Exception:  # noqa: BLE001 - a run is not lost over provenance
        mark = ""

    return {
        "tool": "Forensic Media Extractor",
        "tool_version": RELEASE,
        "engine_version": __version__,
        "code_fingerprint": mark,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def mixed_install() -> str:
    """Say so when the files on disk are from more than one release.

    A copy over the top of an older install is the normal way to upgrade, and
    the normal way for it to go wrong is for some files to land and others
    not. The release number ships twice -- at the source root and inside the
    package -- so disagreement between them means exactly that, and stale
    compiled files mean the same thing in a different way.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    numbers = {}
    for label, path in (("source root", os.path.join(root, "VERSION")),
                        ("package", os.path.join(os.path.dirname(here), "VERSION"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                numbers[label] = fh.read().strip()
        except OSError:
            continue
    if len(set(numbers.values())) > 1:
        return ("The files on disk are from more than one release: "
                + ", ".join(f"{k} says v{v}" for k, v in numbers.items())
                + ". Unpack the new version into an empty folder rather than "
                  "copying over the old one.")

    # A .pyc with no .py beside it is a module the new version removed and
    # the old compiled copy of which is still importable.
    orphans = []
    for folder, _, names in os.walk(os.path.dirname(here)):
        if not folder.endswith("__pycache__"):
            continue
        for name in names:
            if not name.endswith(".pyc"):
                continue
            stem = name.split(".")[0]
            if not os.path.isfile(os.path.join(os.path.dirname(folder),
                                               stem + ".py")):
                orphans.append(os.path.join(folder, name))
    if orphans:
        return (f"{len(orphans)} compiled file(s) are left from an older "
                f"version with no source beside them, e.g. "
                f"{os.path.basename(orphans[0])}. Delete every __pycache__ "
                f"folder under the program and start it again.")
    return ""
# Advertised so a GUI can check what this copy of the module supports before
# calling it. A mismatched pair of files (new media_gui.py, old media_core.py)
# used to surface as a bare "Unknown option(s): cancel_cb" from run_extraction.
FEATURES = frozenset({"cancel", "inspect", "custom_signatures", "progress",
                      "workers", "databases", "folder", "device", "package",
                      "ufd"})


# ==========================================================================
# SECTION 7 -- CLI
# ==========================================================================
def inspect_image(path: str, credentials=None) -> dict:
    """Open an image and report what it is, without extracting anything.

    Answers "did my E01 actually load, and what is on it?" before a long job
    is started: container type and size, how many segments were stitched
    together, the partition table, and every file system that could be
    mounted. Anything that could not be read comes back as a warning rather
    than an exception, so the GUI can show a partial picture.
    """
    info = {
        "path": os.path.abspath(path), "ok": False, "container": "unknown",
        "size": 0, "size_human": "0 B", "segments": 1, "partition_scheme": None,
        "volumes": [], "warnings": [], "boot_preview": "",
        "engine": _engine_description(), "version": __version__,
    }
    if not os.path.exists(path):
        info["warnings"].append(f"No such file: {path}")
        return info

    if os.path.isdir(path):
        # A folder is not an image: report what is in it instead.
        files = total = 0
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                    files += 1
                except OSError:
                    pass
                if files > 200000:      # enough to characterize it
                    break
        info.update(ok=True, container="folder", size=total,
                    size_human=human(total), partition_scheme="n/a (folder)",
                    volumes=[{"label": "folder", "description":
                              f"{files} file(s)", "offset": 0,
                              "size_human": human(total), "fs": "folder scan",
                              "volume_label": os.path.basename(path.rstrip("/\\"))}])
        info["warnings"].append(
            "Folders are scanned file by file: live files only. A mount point "
            "exposes no unallocated space, no slack and no deleted entries, so "
            "nothing here can recover them. If this is a mounted forensic "
            "image, scan the image itself instead -- or, if it had to be "
            "mounted to be readable at all (encryption, RAID, an unsupported "
            "file system), use a mounter that presents a disk device and point "
            "this at the device rather than the folder.")
        return info

    if is_package(path):
        try:
            archive, entries = _open_archive(path)
            with archive:
                kind = _archive_kind(archive)
                total = sum(_entry_info(e)[0] for e in entries)
            info.update(ok=True, container=f"package ({kind}: UFDR, GrayKey, "
                                           f"backup, plain archive)",
                        size=os.path.getsize(path),
                        size_human=human(os.path.getsize(path)),
                        partition_scheme="n/a (package)",
                        volumes=[{"label": "package", "offset": 0,
                                  "description": f"{len(entries)} entr(ies)",
                                  "size_human": human(total),
                                  "fs": kind, "volume_label": ""}])
            info["warnings"].append(
                "Packages are read entry by entry. There is no unallocated "
                "space in a report file -- for deleted data you need the "
                "physical extraction (.ufd chunk set or .bin) instead.")
        except Exception as exc:  # noqa: BLE001
            info["warnings"].append(f"Could not read the package: {exc}")
        return info

    ext = os.path.splitext(path)[1].lower()
    if ext in (".e01", ".ex01", ".s01", ".l01", ".lx01") and pyewf is None:
        info["warnings"].append(
            "This is an EnCase image but the pyewf module is not installed, "
            "so it cannot be opened. Install it with: pip install libewf-python")
        return info

    try:
        reader = open_image(path, credentials)
        if credentials:
            from fme.engines import unlock
            reader, unlocked = unlock.wrap(reader, credentials)
            for entry in unlocked:
                info["warnings"].append(unlock.opened_line(entry) if entry["opened"]
                                        else unlock.refused_line(entry))
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        info["warnings"].append(f"Could not open image: {exc}")
        return info

    try:
        info["size"] = reader.size
        info["size_human"] = human(reader.size)
        if isinstance(reader, DeviceReader):
            info["container"] = f"block device ({reader.sector}-byte sectors)"
            info["segments"] = 1
        elif isinstance(reader, RawImageReader):
            info["segments"] = len(reader.paths)
            info["container"] = ("split raw / dd" if len(reader.paths) > 1
                                 else "raw / dd")
        elif isinstance(reader, SegmentedReader):
            info["container"] = "Cellebrite .ufd chunk set"
            info["segments"] = len(reader.paths)
        elif ext in (".e01", ".ex01", ".s01"):
            info["container"] = "EnCase EWF (E01)"
        elif ext == ".vmdk":
            info["container"] = "VMDK"
        elif ext in (".vhd", ".vhdx"):
            info["container"] = "VHD"
        elif ext in (".qcow", ".qcow2"):
            info["container"] = "QCOW"
        else:
            info["container"] = type(reader).__name__

        if reader.size == 0:
            info["warnings"].append("Image reports zero size -- unreadable or empty.")
            return info
        info["ok"] = True

        first = reader.read(0, 512)
        info["boot_preview"] = first[:32].hex(" ")
        if reader.read(512, 8)[:8] == b"EFI PART":
            info["partition_scheme"] = "GPT"
        elif _looks_like_mbr(first):
            info["partition_scheme"] = "MBR"
        else:
            # A bare volume image (no partition table) is normal for a
            # logically-acquired card or a single-partition dd.
            info["partition_scheme"] = "none (volume image)"

        info["volumes"] = _inspect_volumes(reader, path, info["warnings"])

        # Said before anything is carved, because a locked volume
        # produces an empty run and an empty run looks like an empty
        # disk.
        try:
            locked_all = encrypted_volumes(reader)
        except Exception:  # noqa: BLE001
            locked_all = []
        locked_all += _locked_apfs(path, credentials)
        if locked_all:
            info["encrypted"] = locked_all[0]
            info["encrypted_volumes"] = locked_all
            for locked in locked_all:
                info["warnings"].append(_locked_message(locked))
        try:
            containers = unopened_containers(reader)
        except Exception:  # noqa: BLE001
            containers = []
        if containers:
            info["unopened_containers"] = containers
            for item in containers:
                info["warnings"].append(
                    f"Partition {item['partition']} (at {human(item['offset'])}) "
                    f"is a {item['kind']}. This program does not open the "
                    f"volumes inside it: their files are reached only by the "
                    f"carve, without names, and an encrypted volume inside is "
                    f"not identified.")
        if not info["volumes"]:
            info["warnings"].append(
                "No file system could be mounted. Carving still works -- it "
                "does not need one.")
    finally:
        reader.close()
    return info


DEFAULT_OPTIONS = {
    # Continue a run that was interrupted, from where its progress note
    # says it reached. Off by default: a resume is a deliberate decision
    # about a case, not something to happen silently because a folder
    # happened to be there.
    "resume": False,
    # Start a new run in a folder that already holds a case. Off: a run
    # without --resume refuses such a folder (output.CaseFolderInUse).
    "overwrite": False,
    "mode": "both",              # both | fs | carve
    "align": 1,          # every byte: sector alignment loses embedded media
    "no_deleted": False,
    "include_system": False,
    "no_dedupe": False,
    "flat": False,
    "images_only": False,
    "videos_only": False,
    "max_carve_size": 4 * GIB,
    "max_file_size": 8 * GIB,
    "chunk_size": 16 * MIB,
    "min_image_size": MIN_IMAGE_SIZE,
    "min_video_size": MIN_VIDEO_SIZE,
    "progress": True,
    "workers": 1,                # >1 carves in parallel processes
    "databases": True,           # pull media out of SQLite BLOB columns
    "db_freespace": True,        # and out of deleted rows / free pages
    "max_db_size": 4 * GIB,
    "carve_files": False,        # in folder mode, scan inside non-media files
    # On by default. A forensic suite mounts compound files before its
    # picture scan, so leaving this off produced a like-for-like comparison
    # that was not like for like at all.
    "artifacts": False,          # also carve SQLite, plists, zips and PDFs
    "metadata": True,            # read EXIF, camera and GPS out of each file
    "scan_inside": True,         # open compound files and pull embedded media
    "max_inside_size": 256 * MIB,
    "vic_case": "",              # set a case number to also write ProjectVIC.json
    "vic_evidence": "",
    "vic_org": "",
    "lava_case": "",             # set a case number to also write a LAVA set
    "lava_examiner": "",
    "lava_abs_media": False,     # absolute media paths, if previews are blank
    "kml": True,                 # write geolocated media as KML, if any has GPS
    # MD5, SHA-1 and SHA-256 of the whole source, compared with the hash the
    # acquisition recorded. A full read of the evidence before the run, so
    # off by default here; the interface turns it on.
    "hash_source": False,
    # The other members of a RAID array or multi-image LVM group: given by
    # path, or found in the image's folder by what each records about
    # itself. Off on the command line; the interface turns finding on.
    "raid_members": [],
    "find_members": False,
}


# What an acquisition looks like, for telling an examiner that the folder
# they chose holds one.
ACQUISITION_EXTENSIONS = {
    ".e01", ".ex01", ".s01", ".l01", ".lx01", ".aff", ".aff4", ".afm",
    ".ad1", ".dd", ".raw", ".img", ".dmg", ".vhd", ".vhdx", ".vmdk",
    ".qcow", ".qcow2", ".001", ".ufdr", ".tar", ".zip",
}


def live_counts(stats: dict) -> dict:
    """The running totals worth showing while a run is going.

    An examiner watching a long run wants to know what it is doing now --
    how much has been walked, how much carved, how much kept -- not a bar
    that could mean anything. These are the numbers the finished summary
    reports, read while they are still moving.
    """
    return {
        "walked": (stats.get("fs_files", 0) + stats.get("folder_files", 0)
                   + stats.get("package_files", 0)),
        "carved": stats.get("carved_files", 0),
        "from_databases": stats.get("db_files", 0),
        "embedded": stats.get("embedded_files", 0),
        "deleted": (stats.get("mft_deleted_seen", 0)
                    + stats.get("fat_deleted_files", 0)),
        "errors": stats.get("errors", 0),
    }


# Options that change what a run finds, and so must match for a resume.
RUN_IDENTITY_OPTIONS = ("mode", "align", "images_only", "videos_only",
                        "artifacts", "chunk_size", "max_carve_size",
                        "include_system", "no_deleted")


def _run_identity(image_path: str, opts: dict, extra_signatures=None) -> dict:
    """The source and options a run was made with, as its note records them."""
    path = os.path.abspath(image_path)
    try:
        size = os.path.getsize(path) if os.path.isfile(path) else None
    except OSError:
        size = None
    custom = sorted(f"{getattr(s, 'ext', '')}:{getattr(s, 'header', b'').hex()}"
                    for s in (extra_signatures or []))
    return {"source": path, "source_size": size,
            **{k: opts.get(k) for k in RUN_IDENTITY_OPTIONS},
            "custom_signatures": custom}


def run_extraction(image_path: str, out_dir: str, *, progress_cb=None,
                   cancel_cb=None, extra_signatures=None, source=None,
                   credentials=None, **options) -> dict:
    """Run the extraction and return a summary dict.

    This is the API the Flask layer calls. progress_cb, if given, is called
    as progress_cb(done, total, recovered, phase) -- the fat32_core carve
    callback plus a phase name ("filesystem" or "carve"). extra_signatures
    takes fat32_core.FileSignature objects, so custom signatures defined in
    the GUI apply here too. cancel_cb, if given, is polled during both
    engines; returning True stops the run and marks the summary canceled,
    keeping everything recovered so far. Pass source to reuse an already-open
    fat32_core ImageSource instead of reopening the file.

    Every option in DEFAULT_OPTIONS may be overridden by keyword.

    credentials, an unlock.Credentials, opens the encrypted volumes its
    passwords and key files open. It is a parameter rather than an option so
    that nothing which records the options can record a password.
    """
    # A caller from a different release may pass these positionally through
    # **options rather than as named arguments. Accept them either way
    # instead of failing the run over an argument we understand perfectly.
    for name, value in (("progress_cb", progress_cb), ("cancel_cb", cancel_cb),
                        ("extra_signatures", extra_signatures), ("source", source)):
        if name in options:
            supplied = options.pop(name)
            if value is None:
                if name == "progress_cb":
                    progress_cb = supplied
                elif name == "cancel_cb":
                    cancel_cb = supplied
                elif name == "extra_signatures":
                    extra_signatures = supplied
                else:
                    source = supplied

    unknown = set(options) - set(DEFAULT_OPTIONS)
    if unknown:
        raise ValueError(
            f"Unknown option(s): {', '.join(sorted(unknown))}. "
            f"This media_core is version {__version__} at {__file__}; valid "
            f"options are: {', '.join(sorted(DEFAULT_OPTIONS))}.")

    opts = dict(DEFAULT_OPTIONS)
    opts.update(options)
    for key in ("max_carve_size", "max_file_size", "chunk_size",
                "min_image_size", "min_video_size"):
        if isinstance(opts[key], str):
            opts[key] = parse_size(opts[key])

    _set_floors(opts["min_image_size"], opts["min_video_size"])

    if opts["workers"] in (0, "auto", None):
        opts["workers"] = default_workers()
    opts["workers"] = max(1, int(opts["workers"]))

    from fme.engines import unlock
    unlock.require(credentials)        # a key it cannot use: said before reading

    args = types.SimpleNamespace(image=image_path, output=out_dir, **opts)
    # Held for this run only: never in stats, the summary or the progress
    # note (see unlock.py). Carve workers get it through their init args.
    args.credentials = credentials or None
    args.signatures = active_signatures(opts["images_only"], opts["videos_only"],
                                        extra_signatures,
                                        artifacts=opts.get("artifacts", False))
    # Workers rebuild the signature list themselves, so keep the raw specs.
    args.raw_custom_signatures = list(extra_signatures or [])

    started = time.time()
    stats = {
        "fs_files": 0, "fs_bytes": 0, "fs_deleted": 0, "skipped_large": 0,
        "carved_files": 0, "carved_bytes": 0, "candidates": 0,
        "db_files": 0, "db_bytes": 0,
        "folder_files": 0, "folder_bytes": 0, "folder_scanned": 0,
        "embedded_files": 0, "embedded_bytes": 0, "rar_skipped": 0,
        "pdf_raster_skipped": 0, "lzc_skipped": 0,
        "vendor_files": 0, "vendor_bytes": 0, "vendor_volumes": 0,
        "vendor_damaged": 0,
        "mft_files": 0, "mft_bytes": 0, "mft_resident": 0,
        "mft_volumes": 0, "mft_deleted_seen": 0,
        "fat_volumes": 0, "fat_deleted_seen": 0, "fat_deleted_files": 0,
        "exfat_volumes": 0, "exfat_deleted_seen": 0, "exfat_deleted_files": 0,
        "exo_items": 0, "exo_pieces": 0, "exo_partial": 0,
        "sevenzip_skipped": 0,
        "package_files": 0, "package_bytes": 0, "package_scanned": 0,
        "errors": 0, "volumes": [],
    }

    # Every progress report carries the running counts, without each of
    # the twenty-odd places that reports progress having to remember to
    # send them. A caller that does not want them is called as before.
    if progress_cb is not None:
        _inner_progress = progress_cb

        def progress_cb(done, total, recovered, phase, _stats=stats):
            try:
                _inner_progress(done, total, recovered, phase, _stats)
            except TypeError:
                _inner_progress(done, total, recovered, phase)

    # A resume reopens the case rather than starting a new one: the rows
    # already written are read back so nothing is recovered twice, and the
    # carve picks up from the recorded offset.
    resuming = {}
    if opts.get("resume"):
        resuming = read_progress(out_dir)
        if resuming:
            LOG.info("Resuming %s: it reached %s of %s",
                     os.path.basename(out_dir),
                     human(resuming.get("offset", 0)),
                     human(resuming.get("total", 0)))
        else:
            LOG.warning("Nothing to resume in %s: no progress note. "
                        "Running from the start.", out_dir)
    if resuming.get("unlocked") and not credentials:
        # The carve would carry on through ciphertext where it had been
        # reading plaintext: the two halves of one run would disagree.
        raise unlock.UnlockUnavailable(
            "This run read encrypted volumes decrypted ("
            + "; ".join(resuming["unlocked"]) + "). Resume it with the same "
            "password or key file; the key is never stored, so it has to be "
            "given again.")
    # What this run is, so a resume can be refused when the note it finds is
    # another run's. A note from another image or other options had every
    # carve header before its offset skipped, and the case said the carve
    # was complete (v2026.11.319).
    identity = _run_identity(image_path, opts, extra_signatures)
    if resuming:
        theirs = resuming.get("run")
        if theirs and theirs != identity:
            differ = sorted(k for k in set(theirs) | set(identity)
                            if theirs.get(k) != identity.get(k))
            raise ValueError(
                f"The progress note in {out_dir} belongs to a different run "
                f"({', '.join(differ)} differ), so it cannot be resumed here. "
                f"Resume with the same source and options, or start a new case.")
        if not theirs:
            LOG.warning("The progress note in %s predates run records; it is "
                        "resumed on trust that it is this run's.", out_dir)
    args.resume_from = resuming.get("offset", 0)
    args.resuming = resuming
    if not opts.get("resume") and not opts.get("overwrite"):
        # Before anything is opened. A run without --resume into a case
        # folder emptied manifest.db and manifest.csv and left the recovered
        # files beside them; a later --resume then took those in as orphans
        # of the new run, with no record of where they had been found.
        refuse_existing_case(out_dir)
    try:
        os.makedirs(out_dir, exist_ok=True)
        _update_progress(out_dir, lambda old: {**old, "run": identity})
    except OSError as exc:
        LOG.warning("Could not record the run in its progress note: %s", exc)

    folder_mode = source is None and os.path.isdir(image_path)
    if folder_mode:
        return _run_folder_extraction(image_path, out_dir, args, opts, stats,
                                      started, progress_cb, cancel_cb)
    if source is None and is_package(image_path):
        return _run_package_extraction(image_path, out_dir, args, opts, stats,
                                       started, progress_cb, cancel_cb)

    owns_reader = source is None
    reader = wrap_source(source, name=os.path.basename(image_path)) if source \
        else open_image(image_path, credentials)
    if reader.size == 0:
        if owns_reader:
            reader.close()
        raise ValueError("Image reports zero size -- unreadable or empty")

    LOG.info("Image: %s (%s)", image_path, human(reader.size))

    # Before anything is unlocked: the digests are of the evidence as
    # acquired, which is what the acquisition's own hash is of.
    try:
        from fme.engines import evidence_hash
        hash_progress = None
        if progress_cb is not None:
            def hash_progress(done, total):
                progress_cb(done, total, 0, "hash")
        stats["evidence"] = evidence_hash.evidence_record(
            reader, image_path, compute=bool(opts.get("hash_source")),
            progress=hash_progress, cancel=cancel_cb)
        if stats["evidence"].get("verified") is True:
            LOG.info("Source verified against the acquisition's own hash")
    except Exception as exc:  # noqa: BLE001 - a record, never the run's end
        LOG.warning("Could not record the source's hashes: %s", exc)
        stats["evidence"] = {"stored": {}, "verified": None,
                             "note": f"the source's hashes could not be "
                                     f"recorded: {exc}"}

    # The other members of a RAID set, settled once for every phase: the
    # walk reads the assembled arrays, the carve carves them, and with
    # hash_source each member is hashed like the source (v2026.11.317).
    members = [m for m in (opts.get("raid_members") or []) if m]
    if opts.get("find_members") and source is None:
        try:
            search = vendor_fs.raid_members(image_path, credentials)
            stats["raid_search"] = {k: search[k] for k in
                                    ("added", "missing", "warnings", "ambiguous")}
            members += [p for p in search["paths"] if p not in members]
        except Exception as exc:  # noqa: BLE001 - a search, never the run's end
            LOG.warning("The other RAID members were not searched for: %s", exc)
    args.raid_members, args.find_members = members, False
    if members and opts.get("hash_source"):
        member_hashes = []
        for member in members:
            try:
                # Like the source, not merely hashed: compared with the hashes
                # its acquisition recorded, and any stretch read as zeros
                # said. A member that no longer matched its own MD5 was
                # reported only as "read as" (v2026.11.317).
                with contextlib.closing(open_image(member, credentials)) as other:
                    got = evidence_hash.evidence_record(other, member, compute=True,
                                                        cancel=cancel_cb)
                member_hashes.append({"path": os.path.abspath(member), **got})
            except Exception as exc:  # noqa: BLE001 - recorded, not fatal
                member_hashes.append({"path": os.path.abspath(member),
                                      "error": str(exc)})
        stats.setdefault("evidence", {})["members"] = member_hashes

    if credentials:
        try:
            reader, unlocked = unlock.wrap(reader, credentials)
        except Exception:
            if owns_reader:
                reader.close()
            raise
        opened = [u for u in unlocked if u["opened"]]
        refused = [u for u in unlocked if not u["opened"]]
        for entry in opened:
            LOG.info("%s", unlock.opened_line(entry))
        for entry in refused:
            LOG.warning("%s", unlock.refused_line(entry))
        if not unlocked:
            LOG.warning("A password or key file was given, and no encrypted "
                        "volume it could open was found on this image.")
        if opened:
            stats["unlocked_volumes"] = opened
        if refused:
            stats["unlock_refused"] = refused
        args.unlocked_apfs = any(u["kind"] == "APFS" for u in opened)

    # A locked volume produces an empty run -- or, on a disk with other
    # partitions, a short one -- and either looks like a disk with little
    # on it. Said here, where every image run passes, rather than only in
    # the inspect path that a command-line run never calls; and read
    # through the reader, so an E01 is looked at for the disk inside it.
    try:
        locked_all = encrypted_volumes(reader)
    except Exception:  # noqa: BLE001 - never fail a run over a warning
        locked_all = []
    locked_all += _locked_apfs(image_path, credentials)
    # A LUKS container keeps its header in the clear once opened -- only its
    # data is decrypted underneath -- so it was still found, and reported
    # "locked", beside the line saying it was opened. One the keys opened is
    # not locked.
    opened_at = {(str(u.get("kind", ""))[:4], u.get("offset"))
                 for u in stats.get("unlocked_volumes") or [] if u.get("opened")}
    locked_all = [v for v in locked_all
                  if (str(v.get("kind", ""))[:4], v.get("offset")) not in opened_at]
    if locked_all:
        stats["encrypted"] = locked_all[0]["kind"]
        stats["encrypted_volumes"] = [
            {k: v[k] for k in ("kind", "offset", "partition", "uuid", "volume")
             if k in v}
            for v in locked_all]
        for locked in locked_all:
            LOG.warning("%s: %s", os.path.basename(image_path) or "image",
                        _locked_message(locked))
    try:
        containers = unopened_containers(reader)
    except Exception:  # noqa: BLE001 - never fail a run over a warning
        containers = []
    if containers:
        stats["unopened_containers"] = containers
        for item in containers:
            LOG.warning("%s at %s is a %s. The volumes inside it are not "
                        "opened by this program, so their files are reached "
                        "only by the carve, without names.", item["partition"],
                        human(item["offset"]), item["kind"])
    out = OutputManager(out_dir, resume=bool(opts.get("resume")),
                        dedupe=not opts["no_dedupe"], flat=opts["flat"],
                        metadata=opts.get("metadata", True))
    cancel_cb = stop_when_output_fails(out, cancel_cb)
    if stats.get("unlocked_volumes"):
        # So a resume knows to ask for the key again. Where, never with what.
        marked = [unlock.where(u) for u in stats["unlocked_volumes"]]
        _update_progress(out.outdir, lambda old: {**old, "unlocked": marked})
    canceled = False
    finished = False
    # A phase is recorded as done only when all of it worked. Recording it
    # regardless -- as this once did -- meant a reader that failed part way
    # was marked finished, and a resume skipped it: the partition it stopped
    # on was never read again. A phase that failed is left unrecorded, so a
    # resume retries it.
    failed_phases = []
    resuming = getattr(args, "resuming", None) or {}
    try:
        if opts["mode"] in ("both", "fs") and \
                not phase_skipped(resuming, "filesystem"):
            # Caught like every other phase. Uncaught, one exception in the
            # walk ended the run before the deleted-file passes and the
            # carve -- which do not need the walk -- had begun.
            try:
                run_fs_engine(reader, out, args, stats, progress_cb, cancel_cb)
                phase_done(out.outdir, "filesystem")
            except Canceled:
                raise
            except Exception as exc:  # noqa: BLE001 - never fail a run for it
                LOG.warning("The file-system walk stopped: %s", exc)
                stats["errors"] += 1
                failed_phases.append("filesystem")
        # A logical acquisition (L01, Lx01, AD1) holds files, not a disk:
        # each is read with its own name, times and recorded MD5.
        if opts["mode"] in ("both", "fs") and source is None and \
                not phase_skipped(resuming, "logical"):
            try:
                run_logical_entries(reader, out, args, stats, args.signatures,
                                    progress_cb, cancel_cb)
                phase_done(out.outdir, "logical")
            except Canceled:
                raise
            except Exception as exc:  # noqa: BLE001 - never fail a run
                LOG.warning("Reading the acquisition's own file list stopped: %s", exc)
                stats["errors"] += 1
                failed_phases.append("logical")
        # File systems the main engine has no reader for: QNX, F2FS and
        # the flash file systems. Nothing happens here when the image
        # holds none of them. Its own phase, so a failure here does not
        # make a resume walk the main file systems again.
        if opts["mode"] in ("both", "fs") and source is None and \
                not phase_skipped(resuming, "other-filesystems"):
            try:
                run_vendor_fs(image_path, out, args, stats,
                              args.signatures, progress_cb, cancel_cb)
                phase_done(out.outdir, "other-filesystems")
            except Canceled:
                raise
            except Exception as exc:  # noqa: BLE001 - never fail a run
                LOG.warning("The extra file system reader stopped: %s", exc)
                stats["errors"] += 1
                failed_phases.append("other-filesystems")
        if opts["mode"] in ("both", "fs") and not opts.get("skip_deleted"):
            if not phase_skipped(resuming, "mft"):
                try:
                    run_mft_recovery(reader, out, args, stats, args.signatures,
                                     volumes=getattr(args, "volumes", None),
                                     progress_cb=progress_cb, cancel_cb=cancel_cb)
                    phase_done(out.outdir, "mft")
                except Canceled:
                    raise
                except Exception as exc:  # noqa: BLE001 - never fail a run for it
                    LOG.warning("MFT recovery stopped: %s", exc)
                    stats["errors"] += 1
                    failed_phases.append("mft")

            # And the same for FAT, whether or not TSK is installed. TSK's
            # entry for a deleted FAT file can carry no size, which leaves
            # its walk nothing to read -- so a machine with TSK was finding
            # fewer deleted files than one without.
            if not phase_skipped(resuming, "fat-deleted"):
                try:
                    run_fat_deleted_recovery(
                        reader, out, args, stats, args.signatures,
                        volumes=getattr(args, "volumes", None),
                        progress_cb=progress_cb, cancel_cb=cancel_cb)
                    # exFAT's deleted entries keep the whole name; FAT's pass
                    # above does not read exFAT, and nothing else did.
                    run_exfat_deleted_recovery(
                        args.image, out, args, stats, args.signatures,
                        cancel_cb=cancel_cb)
                    phase_done(out.outdir, "fat-deleted")
                except Canceled:
                    raise
                except Exception as exc:  # noqa: BLE001 - never fail a run for it
                    LOG.warning("FAT deleted-entry recovery stopped: %s", exc)
                    stats["errors"] += 1
                    failed_phases.append("fat-deleted")
        if opts["mode"] in ("both", "carve") and \
                not phase_skipped(resuming, "carve"):
            workers = opts["workers"]
            # Caught like every other phase: it was the one that was not,
            # so anything it raised ended the run with no summary written
            # by the phase and nothing marked as failed for a resume.
            try:
                if workers > 1 and source is None and reader.size:
                    run_carve_engine_parallel(reader, out, args, stats,
                                              progress_cb, cancel_cb,
                                              workers=workers)
                else:
                    if workers > 1:
                        LOG.info("Parallel carving needs a file path, not an "
                                 "open source -- carving in this process "
                                 "instead.")
                    run_carve_engine(reader, out, args, stats, progress_cb,
                                     cancel_cb)
            except (Canceled, CarveIncomplete):
                raise
            except Exception as exc:  # noqa: BLE001 - never fail a run for it
                LOG.warning("The carve stopped: %s", exc)
                stats["errors"] += 1
                stats.setdefault("carve_incomplete",
                                 f"the carve stopped on an error: {exc}")
            if stats.get("carve_incomplete"):
                failed_phases.append("carve")
            else:
                phase_done(out.outdir, "carve")
        # The assembled RAID arrays, carved as the operating system saw
        # them. Only after the image's own carve finished: it writes no
        # progress note of its own, and must not move the image's.
        if opts["mode"] in ("both", "carve") and args.raid_members and \
                source is None and "carve" not in failed_phases and \
                not phase_skipped(resuming, "raid-carve"):
            try:
                run_raid_carve(image_path, args.raid_members, out, args, stats,
                               progress_cb, cancel_cb, credentials)
                if any(a.get("carve_incomplete") for a in stats.get("raid_carved") or []):
                    failed_phases.append("raid-carve")
                else:
                    phase_done(out.outdir, "raid-carve")
            except Canceled:
                raise
            except Exception as exc:  # noqa: BLE001 - never fail a run for it
                LOG.warning("Carving the assembled RAID arrays stopped: %s", exc)
                stats["errors"] += 1
                failed_phases.append("raid-carve")
        finished = True
    except Canceled:
        canceled = True
        LOG.warning("canceled -- keeping the %d file(s) already recovered",
                    len(out.records))
    finally:
        # Nothing found, and nobody told the examiner why.
        #
        # A volume the program cannot walk reports "0 files", which is
        # also what an empty disk reports. The vendored reader usually
        # knows exactly why -- a ReFS superblock whose checksum does
        # not hold, a format it refuses -- and that is the difference
        # between "there was nothing on it" and "this could not be
        # read".
        #
        # Counted by the files the case holds. out.counter counts names
        # handed out, and every engine takes a name before store() decides;
        # a carve whose few candidates were all dropped as undersized or
        # blank left counter above zero, and this warning never fired on
        # exactly the volume it is for. A resume also seeds counter from
        # the manifest it reads.
        if not out.records and not stats.get("encrypted"):
            try:
                # The keys only when there are any: the call is otherwise the
                # one it always was, and a stand-in for it (a test's, or a
                # caller's) that takes the path alone is not silently broken.
                said = (vendor_fs.describe(image_path, credentials) if credentials
                        else vendor_fs.describe(image_path))
            except Exception:  # noqa: BLE001
                said = None
            # identify_fs can return a kind of None for a region it
            # recognises but cannot name. Treating that as a name
            # crashed the end of the run, after all the work was done.
            # Unless the vendored reader did walk it, or said by name why it
            # could not: an ext4 volume read in full that held no pictures
            # was reported "could not be read by this program", which is
            # false, and one device of a Btrfs RAID1 lost the reader's own
            # reason under that sentence (v2026.11.314).
            explained = {str(v.get("kind", "")).lower()
                         for v in (stats.get("vendor_walked") or [])
                         + (stats.get("vendor_not_walked") or [])}
            if any(a.get("read") for a in stats.get("raid_arrays") or []):
                # A member whose array was assembled and read is not
                # "a volume this program could not read".
                explained |= {"linux md member", "lvm2 pv", "lvm physical volume"}
            if said and said.get("kind") and \
                    str(said["kind"]).lower() not in explained:
                stats["unreadable_volume"] = said["kind"]
                # The reader's own lines, which usually say why: "a RAID
                # member: its array is read from all its members together".
                stats["unreadable_volume_detail"] = [
                    str(line).strip() for line in (said.get("lines") or [])[:2]
                    if str(line).strip()]
                LOG.warning("Nothing was recovered, and this is a %s "
                            "volume: that is not the same as an empty "
                            "disk.", said["kind"].upper())
                for line in said["lines"][:6]:
                    LOG.warning("    %s", line)

        mark_containers(out, stats)
        _reconcile_containers(stats)
        # Whatever the device could not read, whichever phase read it. Only
        # the carve reported these, so a file-system-only run on a failing
        # disk said nothing about the sectors it had read as zeros.
        try:
            _note_unread(stats, [], _salvaging_reader(reader))
        except Exception:  # noqa: BLE001 - never fail the summary over it
            LOG.debug("could not record the device's bad ranges", exc_info=True)
        elapsed = time.time() - started
        summary = {
            "image": os.path.abspath(image_path),
            "image_size": reader.size,
            "image_size_human": human(reader.size),
            "mode": opts["mode"],
            "started_utc": _dt.datetime.fromtimestamp(
                started, _dt.timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            **tool_provenance(),
            "engine": _engine_description(),
            "canceled": canceled,
            "workers": opts["workers"],
            "duplicates_suppressed": out.dupes,
            "blank_dropped": out.blank_dropped,
            "with_location": sum(1 for r in out.records
                                 if r.latitude is not None),
            "with_capture_time": sum(1 for r in out.records
                                     if timeutil.stored(r, "captured")),
            **stats,
            "total_files": len(out.records),
            "output_dir": out.outdir,
        }
        if failed_phases:
            summary["failed_phases"] = failed_phases
        out.close(summary)
        _finish_outputs(out, summary, opts)
        if owns_reader:
            reader.close()
        # A run that finished has nothing to resume. clear_progress existed
        # and nothing called it, so the note outlived every finished run and
        # a later --resume on the case skipped what it said was done. The
        # note stays only when something is left to do: a cancel, an error
        # that stopped the run, or a phase that failed and a resume would
        # retry.
        if finished and not canceled and not failed_phases:
            clear_progress(out.outdir)
        elif finished and failed_phases:
            LOG.warning("Kept %s: --resume retries %s, which did not finish.",
                        PROGRESS_FILE, ", ".join(failed_phases))

    summary["files"] = [asdict(r) for r in out.records]
    return summary


def _reconcile_containers(stats: dict) -> None:
    """Take back "not opened" for an LVM the vendored reader did open.

    unopened_containers() is asked before anything is walked, and an LVM
    physical volume is one TSK cannot open. The vendored reader can: it
    walked the logical volumes inside, and the report still said of the
    physical volume that "the volumes inside it were not opened"
    (v2026.11.314, found against a real LVM fixture). Where it read any
    logical volume the entry goes, and the ones it could not read are said
    by name instead (vendor_not_walked).
    """
    read_lvm = [v for v in stats.get("vendor_walked") or []
                if str(v.get("name", "")).startswith("lvm_")]
    if not read_lvm or not stats.get("unopened_containers"):
        return
    kept = [c for c in stats["unopened_containers"]
            if "lvm" not in str(c.get("kind", "")).lower()]
    stats["lvm_volumes_read"] = [v["name"] for v in read_lvm]
    if kept:
        stats["unopened_containers"] = kept
    else:
        stats.pop("unopened_containers", None)


def module_identity() -> dict:
    """Which media_core is actually loaded. When two copies exist on disk,
    this is the one that matters."""
    return {"version": __version__, "path": os.path.abspath(__file__),
            "features": sorted(FEATURES)}


def _run_folder_extraction(root: str, out_dir: str, args, opts: dict,
                           stats: dict, started: float, progress_cb,
                           cancel_cb) -> dict:
    """Folder input: no image to open, no unallocated space, one engine."""
    LOG.info("Folder: %s", os.path.abspath(root))
    out = OutputManager(out_dir, resume=bool(opts.get("resume")),
                        dedupe=not opts["no_dedupe"], flat=opts["flat"],
                        metadata=opts.get("metadata", True))
    cancel_cb = stop_when_output_fails(out, cancel_cb)
    canceled = False
    try:
        run_folder_engine(root, out, args, stats, progress_cb, cancel_cb)
    except Canceled:
        canceled = True
        LOG.warning("canceled -- keeping the %d file(s) already recovered",
                    len(out.records))
    finally:
        # The image path's "nothing recovered" explanation -- what the
        # vendored reader says about a volume it could not walk -- has no
        # meaning for a folder, and was written here against `image_path`,
        # a name this function does not have: the NameError was swallowed
        # and the block never did anything. A folder's own explanation is
        # below: the acquisition it holds, if it holds one.
        mark_containers(out, stats)
        _reconcile_containers(stats)
        elapsed = time.time() - started
        summary = {
            "image": os.path.abspath(root),
            "image_size": stats.get("folder_bytes", 0),
            "image_size_human": human(stats.get("folder_bytes", 0)),
            "mode": "folder",
            "started_utc": _dt.datetime.fromtimestamp(
                started, _dt.timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            **tool_provenance(),
            "engine": _engine_description(),
            "canceled": canceled,
            "workers": 1,
            "duplicates_suppressed": out.dupes,
            "blank_dropped": out.blank_dropped,
            "with_location": sum(1 for r in out.records
                                 if r.latitude is not None),
            "with_capture_time": sum(1 for r in out.records
                                     if timeutil.stored(r, "captured")),
            **stats,
            "total_files": len(out.records),
            "output_dir": out.outdir,
        }
        # A folder that holds an acquisition is the commonest way to get a run
        # that finds nothing: the examiner points at the folder the .E01 lives
        # in rather than at the .E01 itself. The walk then looks for pictures
        # among the segments of a disk image, finds none, and reports "0 files"
        # with no hint why -- which looks like the program failing.
        if not out.records:
            acquisitions = []
            try:
                for name in sorted(os.listdir(root))[:400]:
                    if os.path.splitext(name)[1].lower() in ACQUISITION_EXTENSIONS:
                        acquisitions.append(name)
            except OSError:
                acquisitions = []
            if acquisitions:
                summary["points_at_container"] = acquisitions[:6]
                summary["advice"] = (
                    f"Nothing was recovered, and this folder holds an "
                    f"acquisition: {', '.join(acquisitions[:3])}"
                    f"{' and others' if len(acquisitions) > 3 else ''}. A folder "
                    f"scan looks for media among the files in the folder, and "
                    f"the segments of a disk image are not media. Point this at "
                    f"the image itself and the file systems inside it are read, "
                    f"including what was deleted.")
                LOG.warning("%s", summary["advice"])
        out.close(summary)
    _finish_outputs(out, summary, opts)
    summary["files"] = [asdict(r) for r in out.records]
    LOG.info("Folder engine      : %d file(s), %s from %d file(s) scanned",
             stats["folder_files"], human(stats["folder_bytes"]),
             stats.get("folder_scanned", 0))
    if stats.get("db_files"):
        LOG.info("Database blobs     : %d file(s), %s",
                 stats["db_files"], human(stats["db_bytes"]))

    return summary


def _run_package_extraction(path: str, out_dir: str, args, opts: dict,
                            stats: dict, started: float, progress_cb,
                            cancel_cb) -> dict:
    """UFDR / zip input: one engine, no volume and no unallocated space."""
    out = OutputManager(out_dir, resume=bool(opts.get("resume")),
                        dedupe=not opts["no_dedupe"], flat=opts["flat"],
                        metadata=opts.get("metadata", True))
    cancel_cb = stop_when_output_fails(out, cancel_cb)
    canceled = False
    try:
        run_package_engine(path, out, args, stats, progress_cb, cancel_cb)
    except Canceled:
        canceled = True
        LOG.warning("canceled -- keeping the %d file(s) already recovered",
                    len(out.records))
    finally:
        elapsed = time.time() - started
        size = os.path.getsize(path)
        summary = {
            "image": os.path.abspath(path), "image_size": size,
            "image_size_human": human(size), "mode": "package",
            "started_utc": _dt.datetime.fromtimestamp(
                started, _dt.timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 1), **tool_provenance(),
            "engine": _engine_description(), "canceled": canceled,
            "workers": 1, "duplicates_suppressed": out.dupes, **stats,
            "total_files": len(out.records), "output_dir": out.outdir,
        }
        out.close(summary)
    _finish_outputs(out, summary, opts)
    summary["files"] = [asdict(r) for r in out.records]
    LOG.info("Package engine     : %d file(s), %s from %d entr(ies)",
             stats["package_files"], human(stats["package_bytes"]),
             stats.get("package_scanned", 0))
    return summary


def _finish_outputs(out: OutputManager, summary: dict, opts: dict) -> None:
    """Write the Project VIC file when a case number was given, and always
    leave a note saying which output goes where.

    The failure this prevents: manifest.json is the run's own record, and
    handing it to Griffeye produces "could not determine format of 0 data",
    which does not tell you that you picked the wrong file.
    """
    case = (opts.get("vic_case") or "").strip()
    if case and out.records:
        try:
            from fme.exports import projectvic_export
            path = os.path.join(out.outdir, "ProjectVIC.json")
            projectvic_export.export(
                os.path.join(out.outdir, "manifest.json"), path,
                case_number=case, organization=opts.get("vic_org", ""),
                evidence=opts.get("vic_evidence", ""),
                source_app="media_core",
                source_version=f"{__version__} ({RELEASE})")
            summary["project_vic"] = path
            LOG.info("Project VIC        : %s", path)
        except Exception as exc:  # noqa: BLE001 - never lose a run over this
            LOG.warning("could not write ProjectVIC.json: %s", exc)

    lava_case = (opts.get("lava_case") or "").strip()
    if lava_case and out.records:
        try:
            from fme.exports import lava_export
            path = os.path.join(out.outdir, "lava")
            result = lava_export.export(
                os.path.join(out.outdir, "manifest.json"), path,
                case_number=lava_case,
                examiner=opts.get("lava_examiner", ""),
                evidence=opts.get("vic_evidence", ""),
                absolute_media=opts.get("lava_abs_media", False),
                tool_name="media_core",
                tool_version=f"{__version__} ({RELEASE})")
            summary["lava"] = result["output_dir"]
            LOG.info("LAVA output        : %s (%d artifact(s), %d row(s))",
                     result["output_dir"], result["artifacts"], result["rows"])
        except Exception as exc:  # noqa: BLE001 - never lose a run over this
            LOG.warning("could not write the LAVA output set: %s", exc)

    if opts.get("kml", True) and any(r.latitude is not None for r in out.records):
        try:
            from fme.exports import kml_export
            path = os.path.join(out.outdir, "locations.kml")
            result = kml_export.export(
                os.path.join(out.outdir, "manifest.json"), path)
            summary["kml"] = result["path"]
            LOG.info("Geolocated media   : %d file(s) with coordinates -> %s",
                     result["placemarks"], path)
        except Exception as exc:  # noqa: BLE001 - never lose a run over this
            LOG.warning("could not write the KML: %s", exc)

    try:
        with open(os.path.join(out.outdir, "WHICH_FILE_IS_WHICH.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(_output_guide(out, summary))
    except OSError:
        pass


def _output_guide(out: OutputManager, summary: dict) -> str:
    """Plain-language note left in every output folder."""
    has_vic = os.path.isfile(os.path.join(out.outdir, "ProjectVIC.json"))
    lines = [
        "What is in this folder",
        "======================",
        "",
        "images/, videos/, other/",
        "    The recovered files themselves, foldered by extension.",
        "",
        "manifest.csv, manifest.json",
        "    This tool's own record of the run: one row per recovered file,",
        "    with its size, MD5, SHA-1, SHA-256, where it came from, whether",
        "    it was deleted, and which engine found it. For your own use and",
        "    for review.",
        "",
        "    These are NOT an interchange format. Loading manifest.json into",
        "    Griffeye gives \"could not determine format of 0 data\" -- it is",
        "    not a Project VIC file and does not claim to be.",
        "",
        "lava/ (present)" if os.path.isdir(os.path.join(out.outdir, "lava"))
        else "lava/ (not written -- no case number was given; pass "
             "--lava CASENUMBER, or fill in the Case number box in the GUI "
             "before starting a run)",
        "    A LAVA output set for the LEAPP Artifact Viewer App. Open the",
        "    project FILE, not the folder:",
        "",
        "        lava/_lava_data.lava",
        "",
        "    Double-click it if .lava is associated with LAVA, or run:",
        "",
        "        python3 lava_launch.py lava/",
        "",
        "    Recovered media appears as artifacts with previews, hashes and",
        "    timestamps, one artifact per recovery engine.",
        "",
        "ProjectVIC.json " + ("(present)" if has_vic
                              else "(NOT WRITTEN -- see below)"),
        "    The file to load into Griffeye or any other Project VIC tool.",
        "    It follows the VICS 2.0 US data model.",
    ]
    if not has_vic:
        lines += [
            "",
            "    This run did not write one, because no case number was given.",
            "    Give one up front -- --vic CASENUMBER on the command line, or",
            "    the Case number box in the GUI -- and every future run writes",
            "    it automatically. To produce one for this run now:",
            "",
            "        python3 projectvic_export.py -m manifest.json"
            " -o ProjectVIC.json -c CASENUMBER",
            "",
            "    or re-run the extraction with --vic CASENUMBER, or use the",
            "    Export panel in the GUI, which also writes the C4All XML",
            "    reports.",
        ]
    lines += [
        "",
        "    Category is null on every record. This tool recovers and hashes",
        "    files; it does not classify them. Grade them in Griffeye.",
        "",
        "    Check any file before importing it:",
        "",
        "        python3 projectvic_export.py --validate ProjectVIC.json",
        "",
        "Run summary",
        "-----------",
        f"    mode          : {summary.get('mode')}",
        f"    files         : {summary.get('total_files')}",
        f"    duplicates    : {summary.get('duplicates_suppressed')} collapsed by SHA-256",
        f"    elapsed       : {summary.get('elapsed_seconds')}s",
        f"    source        : {summary.get('image')}",
        "",
    ]
    return "\n".join(lines)


def _engine_description() -> str:
    """What this installation can read, in one line."""
    bits = []
    if tsk_enabled():
        bits.append(f"pytsk3 {getattr(pytsk3, 'TSK_VERSION_STR', 'yes')}")
    elif pytsk3 is not None:
        # Said in every summary, so a record can never claim the
        # independent reader read a volume TSK actually read, or the reverse.
        bits.append("pytsk3 present, switched off (FME_NO_TSK=1)")
    else:
        bits.append("pytsk3 absent (fat32_core fallback)")
    if pyewf:
        bits.append("pyewf")
    else:
        # Not having libewf stopped meaning an E01 cannot be read, so the
        # line should not keep implying it does.
        try:
            from fme.engines import vendor_fs
            vendored = vendor_fs.ewfprobe() is not None
        except Exception:  # noqa: BLE001 - a description is not worth failing
            vendored = False
        bits.append("E01 through the vendored reader" if vendored
                    else "no pyewf, and no vendored reader: E01 cannot be read")
    return ", ".join(bits)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="media_core.py",
        description="Forensic Media Extractor -- recover images, video and "
                    "audio from forensic images, live devices, mounted "
                    "folders, report packages and databases.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("-i", "--image", required=True,
                   help="forensic image, block device (/dev/sdb1, "
                        r"\\.\PhysicalDrive1) or a folder to scan")
    p.add_argument("-o", "--output", required=True, help="output directory")
    p.add_argument("--mode", choices=("both", "fs", "carve"), default="both",
                   help="which engine(s) to run")
    p.add_argument("--no-deleted", action="store_true",
                   help="do not recover deleted file system entries")
    p.add_argument("--include-system", action="store_true",
                   help="include NTFS $ system files")
    p.add_argument("--no-dedupe", action="store_true",
                   help="write every copy as its own file instead of storing "
                        "identical bytes once. Provenance for every copy is "
                        "recorded either way, in duplicates.csv -- this only "
                        "changes whether the bytes are written repeatedly, "
                        "which on a large exhibit can multiply the output "
                        "several times over")
    p.add_argument("--flat", action="store_true",
                   help="do not create per-extension subfolders")
    # Encrypted volumes. There is deliberately no --password VALUE: a password
    # on the command line is kept in the shell's history and shown in the
    # process list to anyone on the machine.
    # Matched exactly, so argparse cannot take "--password secret" as an
    # abbreviation of --password-file and go looking for a file named after
    # the password.
    p.add_argument("--password", action=_RefusePassword, nargs="?",
                   help=argparse.SUPPRESS)
    p.add_argument("--password-file", action="append", default=[], metavar="FILE",
                   help="open encrypted volumes (BitLocker, FileVault/APFS) with "
                        "the passwords or recovery keys in FILE, one per line; "
                        "may be given more than once. The passwords are never "
                        "written to the case, the log or a report")
    p.add_argument("--key-file", action="append", default=[], metavar="FILE",
                   help="a BitLocker startup key (.BEK) file; may be given more "
                        "than once. Recorded in the case by name and SHA-256")
    p.add_argument("--ask-password", action="store_true",
                   help="type passwords or recovery keys at the terminal, "
                        "unechoed, a blank line to finish")
    p.add_argument("--resume", action="store_true",
                   help="continue a run that was interrupted, from where "
                        "its progress note says it reached. The output "
                        "folder is reopened rather than replaced, and "
                        "nothing already recovered is recovered again")
    p.add_argument("--overwrite", action="store_true",
                   help="start a new run in an output folder that already "
                        "holds a case. Without it (or --resume) such a folder "
                        "is refused: its manifest would be emptied while the "
                        "files already recovered stayed in it")
    p.add_argument("--align", type=int, default=1,
                   help="only consider candidates at multiples of this many "
                        "bytes. The default of 1 examines every byte, which "
                        "is what finds media embedded inside other files and "
                        "base64 in text. 512 is faster and cuts false "
                        "positives, but silently skips anything not sitting "
                        "on a sector boundary")
    p.add_argument("--max-carve-size", type=parse_size, default=str(4 * GIB),
                   help="upper bound for any single carved file (e.g. 512M, 2G)")
    p.add_argument("--max-file-size", type=parse_size, default=str(8 * GIB),
                   help="skip file system files larger than this")
    p.add_argument("--chunk-size", type=parse_size, default=str(16 * MIB),
                   help="carver read window")
    p.add_argument("-w", "--workers", default=1,
                   help="carve with this many worker processes, or 'auto' for "
                        "one per core less one (max 8). Carving is CPU-bound "
                        "on the byte scan, so this is the setting that makes a "
                        "large image finish sooner")
    p.add_argument("--min-image-size", type=parse_size, default=str(MIN_IMAGE_SIZE),
                   help="discard recovered images smaller than this")
    p.add_argument("--min-video-size", type=parse_size, default=str(MIN_VIDEO_SIZE),
                   help="discard recovered videos smaller than this")
    p.add_argument("--no-databases", dest="databases", action="store_false",
                   help="do not open SQLite databases found on the file system")
    p.add_argument("--no-db-freespace", dest="db_freespace", action="store_false",
                   help="inside databases, skip the free-space sweep for "
                        "deleted rows")
    p.add_argument("--max-db-size", type=parse_size, default=str(4 * GIB),
                   help="skip databases larger than this")
    p.add_argument("--vic", dest="vic_case", default="", metavar="CASE",
                   help="also write ProjectVIC.json for this case number -- "
                        "this is the file Griffeye loads, not manifest.json")
    p.add_argument("--vic-evidence", dest="vic_evidence", default="",
                   help="evidence item name recorded in the Project VIC file")
    p.add_argument("--vic-org", dest="vic_org", default="",
                   help="contact organization recorded in the Project VIC file")
    p.add_argument("--lava", dest="lava_case", default="", metavar="CASE",
                   help="also write a LAVA output set under lava/ for this "
                        "case number, for the LEAPP Artifact Viewer App")
    p.add_argument("--lava-abs-media", dest="lava_abs_media",
                   action="store_true",
                   help="write absolute paths to the media in the LAVA set. "
                        "Try this if LAVA shows the right filenames but blank "
                        "previews")
    p.add_argument("--lava-examiner", dest="lava_examiner", default="",
                   help="examiner name recorded in the LAVA summary artifact")
    p.add_argument("--member", dest="raid_members", action="append", default=[],
                   metavar="IMAGE",
                   help="another member image of the same RAID array or LVM "
                        "volume group; repeat for each. The array is assembled "
                        "and its volumes walked")
    p.add_argument("--find-members", dest="find_members", action="store_true",
                   help="look in the image's folder for the other members of any "
                        "RAID array or LVM volume group it belongs to, by what "
                        "each file records about itself (never by its name)")
    p.add_argument("--hash-source", dest="hash_source", action="store_true",
                   help="hash the whole source (MD5, SHA-1, SHA-256) before "
                        "the run and compare it with the hash the acquisition "
                        "recorded. Reads the evidence once more end to end")
    p.add_argument("--no-kml", dest="kml", action="store_false",
                   help="do not write locations.kml for media carrying GPS")
    p.add_argument("-k", "--artifacts", action="store_true",
                   help="also recover SQLite databases (including encrypted "
                        "ones, which are found rather than decrypted), binary "
                        "property lists, zip archives (APK, IPA, JAR, Office "
                        "and OpenDocument among them) and PDFs. Off by "
                        "default: a disk holds far more of these than it "
                        "holds pictures")
    p.add_argument("--no-metadata", dest="metadata", action="store_false",
                   help="do not read EXIF, camera and GPS out of recovered "
                        "files. They are read by default: a carved photograph "
                        "still carries when and where it was taken, even when "
                        "the file system that held it is gone")
    p.add_argument("--no-scan-inside", dest="scan_inside", action="store_false",
                   help="do not open compound files. By default they are "
                        "opened and the media inside them recovered: office "
                        "documents, zips, archives, mail stores, thumbnail "
                        "caches, and EXIF thumbnails within photos")
    p.add_argument("--max-inside-size", dest="max_inside_size",
                   type=parse_size, default=str(256 * MIB),
                   help="skip compound files larger than this when scanning "
                        "inside them")
    p.add_argument("--carve-files", action="store_true",
                   help="folder mode: also scan inside non-media files for "
                        "embedded images and video (office documents, mail "
                        "stores, app caches)")
    p.add_argument("--images-only", action="store_true", help="skip video signatures")
    p.add_argument("--videos-only", action="store_true", help="skip image signatures")
    p.add_argument("--no-progress", dest="progress", action="store_false",
                   help="suppress periodic progress lines")
    p.set_defaults(progress=True)
    p.add_argument("--inspect", action="store_true",
                   help="report what the image is and what is on it, then exit "
                        "without extracting anything")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def _no_arguments_hint() -> None:
    """Say something useful when this file is double-clicked.

    media_core is the engine, not the interface: with no arguments argparse
    prints usage and exits, and on Windows the console window closes before
    anyone can read it. Point at the launcher instead, and wait for a
    keypress so the message can actually be read.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    print("Forensic Media Extractor -- extraction engine\n")
    print("This file is the engine. It runs from a command line and needs an")
    print("image and an output folder:\n")
    print("    python media_core.py -i evidence.E01 -o ./out\n")
    print("For the browser interface, start the launcher at the top of the")
    print("source tree instead -- you can double-click it:\n")
    print(f"    {os.path.join(root, 'launch.py')}\n")
    print("Run this file with --help for the full list of options.")
    try:
        input("\nPress Enter to close.")
    except (EOFError, KeyboardInterrupt):
        pass


class _RefusePassword(argparse.Action):
    """--password VALUE, refused with the reason and what to use instead."""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error("there is no --password: a password on the command line "
                     "stays in the shell's history and shows in the process "
                     "list. Use --ask-password to type it, or --password-file "
                     "with a file holding it. If you typed one just now, it "
                     "is in your shell history: clear it.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    from fme.engines import unlock
    try:
        credentials = unlock.from_files(args.password_file, args.key_file)
        if args.ask_password:
            typed = unlock.ask()
            credentials = unlock.Credentials(credentials.passwords + typed.passwords,
                                             credentials.key_files)
    except (OSError, UnicodeDecodeError) as exc:
        LOG.error("cannot read the password or key file: %s", exc)
        return 2

    if args.inspect:
        try:
            return _print_inspection(inspect_image(args.image, credentials or None))
        except BrokenPipeError:      # piped into head/less
            os._exit(0)

    options = {k: getattr(args, k) for k in DEFAULT_OPTIONS if hasattr(args, k)}
    try:
        summary = run_extraction(args.image, args.output, credentials=credentials or None,
                                 **options)
    except KeyboardInterrupt:
        LOG.warning("interrupted")
        return 130
    except CaseFolderInUse as exc:
        # Not a fault in the image: said as what it is.
        LOG.error("not started: %s", exc)
        return 2
    except (RuntimeError, FileNotFoundError, OSError, ValueError) as exc:
        LOG.error("cannot process image: %s", exc)
        return 2
    return _report(summary)


def _print_inspection(info: dict) -> int:
    if True:
        print(f"Image      : {info['path']}")
        print(f"Container  : {info['container']}"
              + (f" ({info['segments']} segments)" if info["segments"] > 1 else ""))
        print(f"Size       : {info['size_human']}")
        print(f"Partitions : {info['partition_scheme'] or 'none detected'}")
        print(f"Engine     : {info['engine']}")
        print(f"media_core : {__version__} ({RELEASE}) at "
              f"{os.path.abspath(__file__)}")
        for v in info["volumes"]:
            print(f"  {v['label']:<12} {v['fs'] or 'unrecognized':<12} "
                  f"{v['size_human']:>10}  offset {v['offset']}"
                  + (f"  [{v['volume_label']}]" if v["volume_label"] else "")
                  + (f"  {v['description']}" if v["description"] else ""))
        for w in info["warnings"]:
            print(f"  ! {w}")
        return 0 if info["ok"] else 2


# Exit status of a run that finished but lost files it found. Distinct from
# 2 (could not process) so a script can tell "nothing ran" from "ran, but
# the output is incomplete".
EXIT_FILES_LOST = 3


def _report(summary: dict) -> int:
    status = _report_counts(summary)
    return _report_losses(summary) or status


def _report_losses(summary: dict) -> int:
    """Say, last and loudly, what a run found and could not keep."""
    read_failed = summary.get("read_failed", 0)
    write_failed = summary.get("write_failed", 0)
    if not (read_failed or write_failed or summary.get("output_failure")):
        return 0
    LOG.error("=" * 62)
    if summary.get("output_failure"):
        LOG.error("RUN STOPPED EARLY: the output failed (%s). This case is "
                  "INCOMPLETE; free space and resume.", summary["output_failure"])
    if write_failed:
        LOG.error("Files NOT recovered (output write failed): %d", write_failed)
    if read_failed:
        LOG.error("Files NOT recovered (evidence read failed): %d", read_failed)
    for item in summary.get("failures", [])[:20]:
        LOG.error("    %-5s %s  (%s)", item["kind"], item["where"], item["error"])
    LOG.error("Every loss is listed under \"failures\" in manifest.json.")
    return EXIT_FILES_LOST


def _report_counts(summary: dict) -> int:
    LOG.info("-" * 62)
    if summary["mode"] == "package":
        LOG.info("Entries scanned    : %d", summary.get("package_scanned", 0))
        LOG.info("Package engine     : %d file(s), %s",
                 summary.get("package_files", 0),
                 human(summary.get("package_bytes", 0)))
        if summary.get("db_files"):
            LOG.info("Database blobs     : %d file(s), %s",
                     summary["db_files"], human(summary["db_bytes"]))
        LOG.info("Duplicate copies   : %d (bytes stored once; every location is "
             "in duplicates.csv)", summary["duplicates_suppressed"])
        LOG.info("Output             : %s", summary["output_dir"])
        LOG.info("Elapsed            : %.1fs", summary["elapsed_seconds"])
        return 0
    if summary["mode"] == "folder":
        LOG.info("Files scanned      : %d", summary.get("folder_scanned", 0))
        LOG.info("Folder engine      : %d file(s), %s",
                 summary.get("folder_files", 0), human(summary.get("folder_bytes", 0)))
        if summary.get("db_files"):
            LOG.info("Database blobs     : %d file(s), %s",
                     summary["db_files"], human(summary["db_bytes"]))
        LOG.info("Duplicate copies   : %d (bytes stored once; every location is "
             "in duplicates.csv)", summary["duplicates_suppressed"])
        LOG.info("Output             : %s", summary["output_dir"])
        LOG.info("Elapsed            : %.1fs", summary["elapsed_seconds"])
        return 0
    LOG.info("File system engine : %d file(s), %s (%d deleted)",
             summary["fs_files"], human(summary["fs_bytes"]), summary["fs_deleted"])
    if summary.get("db_files"):
        LOG.info("Database blobs     : %d file(s), %s",
                 summary["db_files"], human(summary["db_bytes"]))
    if summary.get("embedded_files"):
        LOG.info("Embedded in files  : %d file(s), %s",
                 summary["embedded_files"], human(summary["embedded_bytes"]))
    try:
        from fme.engines import imaging
        notes = imaging.noticed()
    except ImportError:
        notes = {}
    if notes:
        LOG.info("Decoder notes      : %s (normal for carved files, which "
                 "often stop mid-image)",
                 "; ".join(f"{k} x{v:,}" for k, v in notes.items()))
    if summary.get("vendor_files"):
        LOG.info("Other file systems  : %d file(s), %s from %d volume(s)",
                 summary["vendor_files"], human(summary["vendor_bytes"]),
                 summary.get("vendor_volumes", 0))
    if summary.get("mft_files"):
        LOG.info("Deleted MFT records : %d file(s), %s (%d resident, which a "
                 "carve cannot reach)", summary["mft_files"],
                 human(summary["mft_bytes"]), summary.get("mft_resident", 0))
    # Deleted files read from their directory entries, under their names.
    # They were counted in the summary data and said nowhere in the log, so a
    # run that recovered every file this way reported "File system engine: 0"
    # and "Carving engine: 0" -- read as nothing found.
    if summary.get("fat_deleted_files"):
        LOG.info("Deleted FAT entries : %d file(s), under the names the entries kept",
                 summary["fat_deleted_files"])
    if summary.get("exfat_deleted_files"):
        LOG.info("Deleted exFAT entries: %d file(s), under the names the entries kept",
                 summary["exfat_deleted_files"])
    if summary.get("exo_items"):
        LOG.info("App video caches   : %d item(s) rejoined from %d piece(s), "
                 "%d partial", summary["exo_items"], summary["exo_pieces"],
                 summary["exo_partial"])
    if summary.get("lzc_skipped"):
        LOG.warning("Snapchat bundles   : %d skipped -- no Zstandard "
                    "decompressor; pip install zstandard",
                    summary["lzc_skipped"])
    LOG.info("Carving engine     : %d file(s), %s (%d candidates)",
             summary["carved_files"], human(summary["carved_bytes"]),
             summary["candidates"])
    LOG.info("Duplicate copies   : %d (bytes stored once; every location is "
             "in duplicates.csv)", summary["duplicates_suppressed"])
    LOG.info("Output             : %s", summary["output_dir"])
    LOG.info("Elapsed            : %.1fs", summary["elapsed_seconds"])
    return 0


if __name__ == "__main__":
    # No arguments at all, with a console attached, usually means the file
    # was double-clicked rather than run deliberately.
    if len(sys.argv) == 1 and sys.stdin is not None and sys.stdin.isatty():
        _no_arguments_hint()
        sys.exit(0)
    sys.exit(main())
