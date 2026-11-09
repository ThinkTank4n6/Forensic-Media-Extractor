"""
output.py -- where recovered files go: storing, hashing, the manifest.

Moved out of media_core.py (SECTION 4), which had grown past 8,600 lines
with the readers, the carve, the containers and the output all in one file.
Every name here is still importable from media_core, so callers and tests
that say `media_core.OutputManager` are unaffected; new code should import
from here.

Nothing in this module reads evidence. It is handed bytes and decides what
to keep, under what name, and what to write down about it.
"""

from __future__ import annotations

import contextvars as _contextvars
import csv
import errno
import hashlib
import io
import json
import logging
import copy
import os
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Iterator, Optional

import sqlite3

from fme.engines import exif_read
from fme.engines import manifestdb
from fme.engines import timeutil

LOG = logging.getLogger("fme")
MIB = 1024 * 1024
# How many unreadable stretches the summary lists; the byte count covers all.
UNREAD_RANGES_KEPT = 1000

# Files smaller than this are treated as noise and dropped.
MIN_IMAGE_SIZE = 64   # small enough to keep icon-sized JPEG/PNG stubs
MIN_VIDEO_SIZE = 8 * 1024


# The floors one run uses. They were module globals that run_extraction
# reassigned, which went wrong two ways: the GUI runs jobs side by side, so
# one job's settings changed another's part way through; and the parallel
# carve's workers are spawned, import this module fresh and never saw the
# examiner's values at all -- the same settings gave different files with
# one worker and with several. A context variable is per thread (each GUI
# job runs in its own) and is set again in each worker by _worker_init.
_FLOORS: "_contextvars.ContextVar" = _contextvars.ContextVar("fme_floors",
                                                             default=None)


def _floor(category: str) -> int:
    """The smallest file of this category the current run keeps."""
    floors = _FLOORS.get()
    image, video = floors if floors else (MIN_IMAGE_SIZE, MIN_VIDEO_SIZE)
    return video if category == "video" else image


def _set_floors(image: int, video: int) -> None:
    _FLOORS.set((int(image), int(video)))



# ==========================================================================
# SECTION 4 -- Output management, hashing, manifest
# ==========================================================================
def writable_text(text: str) -> str:
    """Text that can be written as UTF-8, with any undecodable bytes shown.

    A name the evidence holds in some other encoding -- a Windows-1252 "café"
    on a folder from another machine -- reaches Python as a lone surrogate
    (os.fsdecode), and no UTF-8 file will take one. manifest.csv refused the
    row, the file was kept in memory without it, and close() then failed to
    write the manifest at all: one such name ended the run with no
    manifest.json. Each undecodable byte is now written as \\xNN, as `ls -b`
    shows it, so the name is legible and the original bytes recoverable.
    """
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        pass
    try:
        return text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    except UnicodeEncodeError:
        # A surrogate that is not an escaped byte (unpaired UTF-16).
        return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _writable_record(rec) -> None:
    """Make every text field of a record writable, in place."""
    for name, value in vars(rec).items():
        if isinstance(value, str):
            fixed = writable_text(value)
            if fixed is not value:
                setattr(rec, name, fixed)


@dataclass
class Record:
    engine: str                 # "filesystem" | "carve"
    category: str               # image | video
    out_path: str
    size: int
    md5: str
    sha256: str
    ext: str
    sha1: str = ""
    instances: int = 1          # how many places these exact bytes were found
    # From the file's own metadata, not the file system: these survive
    # carving, because they sit inside the file.
    captured: str = ""          # EXIF capture time, or a video's mvhd
    camera_make: str = ""
    camera_model: str = ""
    artist: str = ""            # EXIF Artist, camera owner, or XMP creator
    body_serial: str = ""       # the camera body's serial number
    animated: bool = False      # an animated GIF, APNG or animated WebP
    container: bool = False     # other media came out of this file
    city: str = ""              # IPTC or XMP; a camera does not write these
    state: str = ""
    country: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    source_path: str = ""       # original path inside the volume
    # A phone extraction is packaging around a device. The path inside the
    # archive is the vendor's; the path on the phone is the one an examiner
    # cites in evidence, so both are kept and the device's is shown.
    device_path: str = ""       # where it was on the phone
    packed_by: str = ""         # which tool's packaging it was recognized as
    also_seen_at: str = ""      # other mount points showing the same file
    inode: str = ""
    fs_type: str = ""
    partition: str = ""
    offset: Optional[int] = None
    deleted: bool = False
    signature: str = ""
    ctime: str = ""
    mtime: str = ""
    atime: str = ""
    crtime: str = ""
    # The time fields above hold instants only -- times that say their zone.
    # What the evidence recorded with no zone (FAT and exFAT file times, a
    # camera clock with no offset) is kept here instead, as JSON: field name
    # -> reading. timeutil.settle() sorts them in store(); read them through
    # timeutil.stored()/instant()/reading(), never from the fields directly.
    recorded_times: str = ""
    note: str = ""
    # Carved files: how many bytes of the source the carve judged this file
    # to span. Its size can be less -- a file cut short by an unreadable
    # stretch, a base64 run decoded -- and a resume needs the span to know
    # what the interrupted run had stepped over. Kept before `bounded` so
    # that stays the last column.
    extent: Optional[int] = None
    # Carved and embedded files: how the length was arrived at -- "header"
    # (the file recorded it), "parsed" (its structure was walked to the
    # end) or "capped" (the end was not confirmed; a partial, or a
    # ceiling). Empty for files a file system or an archive's directory
    # sized. "short": judged one of those ways, but the source could not be
    # read to the end, so less was stored (the note says how much) -- or,
    # for a file a file system sized, some of its bytes could not be read
    # and are zeros a failing device put in their place (a ZERO-FILLED
    # note says how many and where). See carve_judge.
    bounded: str = ""


def _record_from_row(row: dict) -> Optional["Record"]:
    """A manifest.csv row back as a Record, with its types restored.

    csv hands everything back as text: "False" is truthy and "" is not a
    number, so each field is converted by its declared type. A row that will
    not convert -- a half-written last line from a crash -- returns None
    rather than entering the case with wrong values.
    """
    values = {}
    try:
        for f in Record.__dataclass_fields__.values():
            raw = row.get(f.name)
            kind = str(f.type)
            if raw is None:
                continue
            if "bool" in kind:
                values[f.name] = raw.strip().lower() in ("true", "1")
            elif "float" in kind:
                values[f.name] = float(raw) if raw.strip() else None
            elif "int" in kind:
                if raw.strip():
                    values[f.name] = int(raw)
                elif "Optional" in kind:
                    values[f.name] = None
            else:
                values[f.name] = raw
        # The one field whose value is a claim about evidence: whether a
        # carved length was confirmed. A value outside the four this program
        # writes is a damaged row, not a fifth kind of length.
        if values.get("bounded", "") not in BOUNDED_VALUES:
            return None
        return Record(**values)
    except (TypeError, ValueError):
        return None


# What Record.bounded may hold: empty for a file a file system or directory
# sized, otherwise how the carve arrived at the length (see carve_judge) --
# or "short", a length judged one of those ways of which the source gave
# only part; the row's note says how much and where it stopped.
BOUNDED_VALUES = frozenset({"", "header", "parsed", "capped", "short"})


# Engines whose files have a size recorded by a file system or container,
# rather than one the carve worked out. The size floor does not apply to them.
SIZED_ENGINES = frozenset({"filesystem", "folder", "vendor-fs", "package",
                           "mft", "fat-deleted",
                           # a logical acquisition records each entry's size;
                           # a 2.7 KB clip was dropped as noise (v2026.11.317)
                           "logical"})


# Large enough for any field this program writes, and small enough to be a
# C long on every platform (sys.maxsize overflows csv on 64-bit Windows).
_CSV_FIELD_LIMIT = 2 ** 31 - 1


class ManifestUnreadable(RuntimeError):
    """An existing case's manifest could not be read in full, so it is not
    resumed: resuming rewrites the manifest from what was read."""

    def __init__(self, path: str, why: object):
        super().__init__(
            f"{os.path.basename(path)} in {os.path.dirname(path)} could not be "
            f"read in full ({why}). The case has not been changed. Resuming "
            f"rewrites this file from what is read, so it was not resumed: "
            f"copy the case folder, and check the file -- a row longer than "
            f"the rest, or a damaged line -- before trying again.")
        self.path = path


# A write that fails with one of these will fail the same way for every file
# after it: the output drive is full, over quota or read-only. Carrying on
# meant every later find was dropped with one warning each and the run still
# reported success (see OutputManager.store).
_OUTPUT_FATAL_ERRNOS = frozenset(
    e for e in (getattr(errno, "ENOSPC", None), getattr(errno, "EDQUOT", None),
                getattr(errno, "EROFS", None)) if e is not None)


def _output_is_fatal(exc) -> bool:
    # Windows reports a full disk as winerror 112 (ERROR_DISK_FULL) or 39
    # (ERROR_HANDLE_DISK_FULL), mapped to errno ENOSPC by CPython -- but a
    # share can hand back the winerror alone. SQLite says it in words.
    if isinstance(exc, sqlite3.Error):
        text = str(exc).lower()
        return "disk is full" in text or "readonly" in text or "read-only" in text
    return (getattr(exc, "errno", None) in _OUTPUT_FATAL_ERRNOS
            or getattr(exc, "winerror", None) in (39, 112))


def _record_from_dict(values: dict) -> Optional["Record"]:
    """A manifest.db record back as a Record. Its types survived JSON; a key
    this release does not know (a newer release wrote it) is set aside rather
    than refused, and a record missing what every record has is refused."""
    known = Record.__dataclass_fields__
    try:
        rec = Record(**{k: v for k, v in values.items() if k in known})
    except TypeError:
        return None
    if rec.bounded not in BOUNDED_VALUES or not rec.sha256:
        return None
    return rec


def stop_when_output_fails(out: "OutputManager", cancel_cb):
    """cancel_cb that also asks the run to stop once the output is unusable.

    Stopping goes through the engines' own cancel checks, which every phase
    already honors and which keep what was recovered, rather than an
    exception from store() that a caller's `except Exception` might swallow.
    """
    def check() -> bool:
        if out.output_failure is not None:
            return True
        return bool(cancel_cb()) if cancel_cb is not None else False
    return check


def _fsync_file(path: str) -> None:
    """Make a written file's bytes durable. Opened for writing because
    Windows will not flush a handle opened only to read."""
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: str) -> None:
    """Make a rename into `path` durable (POSIX). Windows has no directory
    handle to flush this way, and NTFS journals the rename itself."""
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # Some file systems (and network shares) refuse fsync on a
        # directory; the file itself is already durable.
        pass
    finally:
        os.close(fd)


class CaseFolderInUse(RuntimeError):
    """A new run was pointed at a folder that already holds a case."""

    def __init__(self, outdir: str, rows: int):
        super().__init__(
            f"{outdir} already holds a case: its manifest lists {rows:,} "
            f"recovered file(s) or place(s) found. A new run there would "
            f"empty that manifest while the recovered files stayed in the "
            f"folder, and a later --resume would take them in as files of "
            f"the new run. Choose a new output folder, or add --resume to "
            f"continue that case. --overwrite starts the manifest again "
            f"anyway; the files already in the folder are left where they are.")
        self.outdir = outdir
        self.rows = rows


def existing_case_rows(outdir: str) -> int:
    """How many rows the case in `outdir` already records: manifest.db's
    files and duplicates, or for a case from before it, manifest.csv's rows.
    A manifest.db that cannot be read counts as holding something -- not
    knowing is not a reason to empty it."""
    outdir = os.path.abspath(outdir)
    if manifestdb.exists(outdir):
        try:
            files, dups = manifestdb.read(outdir)
        except manifestdb.ManifestDBError:
            return 1
        if files or dups:
            return len(files) + len(dups)
    rows = 0
    for name in ("manifest.csv", "duplicates.csv"):
        try:
            with open(os.path.join(outdir, name), encoding="utf-8",
                      errors="replace", newline="") as fh:
                rows += max(0, sum(1 for line in fh if line.strip()) - 1)
        except OSError:
            pass
    return rows


def refuse_existing_case(outdir: str) -> None:
    """Raise CaseFolderInUse if `outdir` holds a case with anything in it."""
    rows = existing_case_rows(outdir)
    if rows:
        raise CaseFolderInUse(os.path.abspath(outdir), rows)


# The engine of a file found in a case folder with no manifest row -- left by
# a crash between storing it and writing its row -- registered on resume.
ORPHAN_ENGINE = "unlisted-after-crash"
ORPHAN_NOTE = ("In the case folder with no manifest row: a crash came between "
               "storing this file and recording it. Its hashes are its own; "
               "where it was found and its times were lost with the row.")


# Engines whose finds are named by the source they came from -- a volume, a
# path, a record -- so that a resumed run can tell a file it is reading again
# from a new one. The carve has its own (offsets: _already_carved).
RESUME_KEYED_ENGINES = frozenset({"filesystem", "mft", "fat-deleted",
                                  "exfat-deleted", "vendor-fs", "embedded",
                                  # named by its path in the acquisition; a
                                  # resume wrote each one down again as a
                                  # duplicate of itself (v2026.11.317)
                                  "logical",
                                  # and the same for package entries and
                                  # folder files (v2026.11.319)
                                  "package", "folder"})


def _resume_key(values: dict):
    """(engine, partition, source_path, inode, size), or None for a find
    that is not named by its source."""
    engine = values.get("engine") or ""
    if engine not in RESUME_KEYED_ENGINES or not values.get("source_path"):
        return None
    try:
        size = int(values.get("size") or 0)
    except (TypeError, ValueError):
        return None
    return (engine, values.get("partition") or "", values["source_path"],
            str(values.get("inode") or ""), size)


class OutputManager:
    """Writes recovered files, deduplicates by SHA-256, keeps the manifest."""

    def __init__(self, outdir: str, dedupe: bool = True, flat: bool = False,
                 metadata: bool = True, resume: bool = False):
        self.outdir = os.path.abspath(outdir)
        self.dedupe = dedupe
        self.flat = flat
        self.seen: dict[str, str] = {}
        self.by_digest: dict[str, Record] = {}
        self.records: list[Record] = []
        # Every additional place a file was found. The bytes are stored once,
        # but where each copy lived is evidence in its own right -- the same
        # picture in a camera folder, a chat cache and unallocated space is
        # three findings, not one.
        self.duplicates: list[Record] = []
        self.dupes = 0
        self.blank_dropped = 0
        self.undersized_dropped = 0
        # Files a reader could read only in part, stored marked "short"
        # (sources._read_marked and the readers that mark their own).
        self.read_short = 0
        # Finds that were lost, and why. These were a log warning each and no
        # count at all, so a run onto a full drive reported success with
        # files missing. Reading the evidence and writing the output fail for
        # different reasons and mean different things to an examiner, so they
        # are counted apart.
        self.read_failed = 0
        self.write_failed = 0
        self.failures: list[dict] = []
        # The first error that makes every later write fail too (a full or
        # read-only output). Once set, the run is asked to stop.
        self.output_failure: Optional[str] = None
        self.metadata = metadata
        for sub in ("images", "videos"):
            os.makedirs(os.path.join(self.outdir, sub), exist_ok=True)
        self.counter = 0
        # What the run being resumed stored from named sources (see
        # RESUME_KEYED_ENGINES), and how many finds were passed over
        # because of it.
        self._resumed_keys: set = set()
        self.resume_skipped = 0
        # CSV rows that could not be appended after their record committed
        # (see _csv_append); close() rewrites the CSVs from the record.
        self.csv_failed = 0
        fields = [f.name for f in Record.__dataclass_fields__.values()]
        self._csv_path = os.path.join(self.outdir, "manifest.csv")
        self._dup_csv_path = os.path.join(self.outdir, "duplicates.csv")

        # Adding to a case that is already finished rather than starting a
        # new one. Opening these for writing would truncate the manifest of
        # a case somebody has spent a week grading, so resume appends and
        # reads back what is already there: the digests, so nothing is
        # recovered twice, and the counter, so names continue rather than
        # colliding.
        self.resume = bool(resume) and (os.path.isfile(self._csv_path)
                                        or manifestdb.exists(self.outdir))
        self.orphans: list = []
        self.orphans_adopted = 0
        self.torn_rows = 0
        # manifest.db is the case's record; the CSVs and manifest.json are
        # made from it (fme/engines/manifestdb.py says why).
        had_db = manifestdb.exists(self.outdir)
        try:
            self._db = manifestdb.ManifestDB(self.outdir)
        except manifestdb.ManifestDBError as exc:
            raise ManifestUnreadable(manifestdb.path_in(self.outdir), exc) from exc
        if self.resume:
            if had_db:
                self._load_from_db()
            else:
                # A case from before manifest.db: read the CSVs as before,
                # then the database takes over from what they held.
                self._load_existing(fields)
            self._index_resumed()
            # Before the rewrite below, so the files a crash left unlisted
            # are in manifest.csv -- what the live gallery reads -- from the
            # start of the resumed run, not only after it closes.
            self._sweep_leftovers()
            if not had_db:
                self._db.replace_all([asdict(r) for r in self.records],
                                     [asdict(d) for d in self.duplicates])
            # Rewritten before anything is appended. Appending to the file
            # as it was glued the first new row onto a line torn by the
            # crash, and wrote rows in this version's column order under a
            # header written by an older one; a second crash before close()
            # left both in the manifest.
            _write_replacing(self._csv_path,
                             lambda fh: _csv_rows(fh, fields, self.records))
            _write_replacing(self._dup_csv_path,
                             lambda fh: _csv_rows(fh, fields, self.duplicates))
            self._csv_fh = open(self._csv_path, "a", newline="",
                                encoding="utf-8")
            self._csv = csv.DictWriter(self._csv_fh, fieldnames=fields)
            self._dup_csv_fh = open(self._dup_csv_path, "a", newline="",
                                    encoding="utf-8")
            self._dup_csv = csv.DictWriter(self._dup_csv_fh,
                                           fieldnames=fields)
            return

        # A new run into a folder truncates manifest.csv; the record it is
        # made from starts empty with it, or the two would disagree.
        self._db.replace_all([], [])
        self._csv_fh = open(self._csv_path, "w", newline="", encoding="utf-8")
        self._csv = csv.DictWriter(self._csv_fh, fieldnames=fields)
        self._csv.writeheader()
        self._dup_csv_fh = open(self._dup_csv_path, "w", newline="",
                                encoding="utf-8")
        self._dup_csv = csv.DictWriter(self._dup_csv_fh, fieldnames=fields)
        self._dup_csv.writeheader()

    def _load_from_db(self) -> None:
        """What this case already holds, from manifest.db.

        Every change was a transaction, so there is no torn row to find and
        no merge to replay: a kept file's row was updated in the same
        transaction as each duplicate that taught it something.
        """
        try:
            files = self._db.files()
            dups = self._db.duplicates()
        except sqlite3.DatabaseError as exc:
            raise ManifestUnreadable(self._db.path, exc) from exc
        highest = 0
        for _, values in files:
            rec = _record_from_dict(values)
            if rec is None:
                raise ManifestUnreadable(
                    self._db.path, f"a record for {values.get('out_path')!r} "
                                   f"is not one this release can read")
            self.records.append(rec)
            self.by_digest.setdefault(rec.sha256, rec)
            self.seen.setdefault(rec.sha256, rec.out_path)
            found = re.search(r"recovered_(\d+)_", rec.out_path or "")
            if found:
                highest = max(highest, int(found.group(1)))
        for values in dups:
            dup = _record_from_dict(values)
            if dup is not None:
                self.duplicates.append(dup)
        self.counter = highest + 1

    def _record_to_db(self, rec: "Record", category: str, ext: str, stem: str,
                      meta: dict) -> bool:
        """Write a stored file's record. False when it could not be: the file
        is then not recovered, and is counted as lost like any write."""
        try:
            self._db.put_file(asdict(rec))
            return True
        except (sqlite3.Error, OSError) as exc:
            self._lost("write", exc, category, ext, stem, meta)
            return False

    def _load_existing(self, fields: list) -> None:
        """What this case already holds, so adding to it adds only.

        The earlier rows are rebuilt as Records, not just as digests.
        manifest.json is written from self.records at close, so seeding only
        the digests made a resumed case's manifest.json list nothing but the
        files found after the resume -- and every report reads that file.
        It also left by_digest holding None, so a duplicate of an earlier
        file was counted but its location was never written down.
        """
        highest = 0
        # A field longer than csv's default limit (128 KiB) -- a long
        # also_seen_at or a note merged many times over -- raised csv.Error
        # part way through the file.
        csv.field_size_limit(_CSV_FIELD_LIMIT)
        try:
            with open(self._csv_path, newline="", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            raise ManifestUnreadable(self._csv_path, exc) from exc
        # A row a crash tore off part way through its last column parses as
        # a whole row: csv cannot tell "capped" cut to "cap" -- or to "",
        # which is what a file a file system sized carries -- from a value
        # that was written that way. Only a line that reached its line end
        # was finished. The bytes of an unfinished one are dropped here, and
        # its file is reported as an orphan by _sweep_leftovers.
        if text and not text.endswith(("\n", "\r")):
            cut = max(text.rfind("\n"), text.rfind("\r"))
            self.torn_rows += 1
            text = text[:cut + 1]
        try:
            for row in csv.DictReader(io.StringIO(text, newline="")):
                digest = (row.get("sha256") or "").strip()
                if not digest:
                    continue
                # Shorter than its own header: the row a crash tore.
                # DictReader fills the missing columns with None, and
                # it used to come back as a file with no offset, no
                # times and no metadata. Refused, its file is reported
                # as an orphan below instead of listed with blanks.
                if None in row.values() or None in row:
                    continue
                rec = _record_from_row(row)
                if rec is None:
                    continue
                # Without dedupe every copy was stored as a file of its
                # own, with a row of its own. Keeping only the first per
                # digest here dropped the rest from the manifest close()
                # rewrites -- the files stayed on disk, unlisted.
                earlier = self.by_digest.get(digest)
                if (earlier is not None and earlier.engine == ORPHAN_ENGINE
                        and rec.engine != ORPHAN_ENGINE):
                    # The orphan's row, then the row that adopted it after a
                    # resume: the second is the file's record.
                    self.records[self.records.index(earlier)] = rec
                    self.by_digest[digest] = rec
                elif digest not in self.by_digest or not self.dedupe:
                    self.records.append(rec)
                    self.by_digest.setdefault(digest, rec)
                # What dedupe actually consults. Seeding only
                # by_digest let a second pass over the same case
                # add every file again: the manifest grew, the
                # bytes were written twice, and the case quietly
                # claimed to hold more than it did.
                self.seen.setdefault(digest, row.get("out_path", ""))
                found = re.search(r"recovered_(\d+)_", row.get("out_path") or "")
                if found:
                    highest = max(highest, int(found.group(1)))
        except csv.Error as exc:
            # This used to return with whatever had been read so far, and the
            # constructor then rewrote manifest.csv from that partial list and
            # duplicates.csv from nothing: every row after the bad one was
            # gone, and its files turned up as "orphans" with no offset, no
            # source path and no deleted flag. Refusing to resume leaves the
            # case exactly as it was.
            raise ManifestUnreadable(self._csv_path, exc) from exc
        # The earlier duplicates too, or manifest.json drops them the same way.
        # A case written before duplicates.csv existed has none to read; any
        # other failure is the same refusal as above, since this file is
        # rewritten from what is read here.
        try:
            with open(self._dup_csv_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if None in row.values() or None in row:
                        continue
                    rec = _record_from_row(row)
                    if rec is not None:
                        self.duplicates.append(rec)
        except FileNotFoundError:
            pass
        except (OSError, csv.Error) as exc:
            raise ManifestUnreadable(self._dup_csv_path, exc) from exc
        # What each duplicate taught the kept file. A kept row is written
        # once, when the file is first stored; its instance count and what a
        # directory entry later told a carved file reached the CSV only at
        # close(), so a crash and a resume brought back "found once, not
        # deleted, no path" for a file duplicates.csv still listed. Both
        # are rebuilt here; on a case that did close they are already right,
        # and the merge does not apply twice.
        copies: dict = {}
        for dup in self.duplicates:
            kept = self.by_digest.get(dup.sha256)
            if kept is None:
                continue
            copies[dup.sha256] = copies.get(dup.sha256, 0) + 1
            self._merge_into(kept, dup)
        for digest, count in copies.items():
            kept = self.by_digest[digest]
            kept.instances = max(kept.instances or 1, 1 + count)
        self.counter = highest + 1

    CATEGORY_DIRS = {"image": "images", "video": "videos", "audio": "audio",
                     "other": "other"}

    def _sweep_leftovers(self) -> None:
        """What a crash left in a case folder that the manifest does not list.

        A file is written to a temporary name, its final name is claimed by
        creating it empty, it is renamed onto that name, and only then is
        its row written. A crash between those steps leaves a temporary
        file, an empty claimed name, or a whole file with no row. The first
        two are not evidence and are removed. The third is recovered data
        whose details were lost with the crash: it is left where it is, and
        named in the summary, so the case folder never holds a file the
        case does not account for without saying so.
        """
        listed = {os.path.normcase(os.path.join(self.outdir, r.out_path))
                  for r in self.records}
        for name in os.listdir(self.outdir):
            if name.startswith(".tmp_"):
                _silent_unlink(os.path.join(self.outdir, name))
        for folder in set(self.CATEGORY_DIRS.values()):
            top = os.path.join(self.outdir, folder)
            for here, _, files in os.walk(top):
                for name in files:
                    path = os.path.join(here, name)
                    if os.path.normcase(path) in listed:
                        continue
                    try:
                        empty = os.path.getsize(path) == 0
                    except OSError:
                        continue
                    if empty:
                        _silent_unlink(path)
                    else:
                        self._register_orphan(path, folder)
        if self.orphans:
            LOG.warning("%d file(s) in %s are not in its manifest -- left by a "
                        "crash between storing a file and writing its row. "
                        "They are listed as orphaned_files in the summary.",
                        len(self.orphans), self.outdir)

    def _register_orphan(self, path: str, folder: str) -> None:
        """Put a file a crash left unlisted into the manifest, by its hashes.

        It used to be named in orphaned_files and nowhere else: not in
        manifest.json, so not in the Project VIC or LAVA export, not in the
        gallery -- and not in `seen`, so the resumed run found the same bytes
        again and stored a second copy beside it. Registered here, it is
        exported and deduplicated like any file, and says plainly that its
        provenance was lost. If the resumed run finds it again, store() gives
        the record back everything the crash lost.
        """
        rel = os.path.relpath(path, self.outdir)
        self.orphans.append(rel)
        md5, sha, sha1 = hashlib.md5(), hashlib.sha256(), hashlib.sha1()
        size = 0
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    md5.update(chunk)
                    sha.update(chunk)
                    sha1.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            LOG.warning("cannot read %s to list it: %s", path, exc)
            return
        digest = sha.hexdigest()
        if digest in self.by_digest and self.dedupe:
            return                      # the same bytes are already listed
        category = {v: k for k, v in self.CATEGORY_DIRS.items()}.get(folder, "other")
        rec = Record(engine=ORPHAN_ENGINE, category=category, out_path=rel,
                     size=size, md5=md5.hexdigest(), sha256=digest,
                     sha1=sha1.hexdigest(),
                     ext=os.path.splitext(path)[1].lstrip(".").lower(),
                     note=ORPHAN_NOTE)
        _writable_record(rec)
        try:
            self._db.put_file(asdict(rec))
        except sqlite3.Error as exc:
            LOG.warning("cannot list %s in manifest.db: %s", path, exc)
            return
        self.records.append(rec)
        self.by_digest[digest] = rec
        self.seen[digest] = path

    def _index_resumed(self) -> None:
        """Every file the interrupted run took from a named source, by
        where it came from (_resume_key). The file-system, MFT and FAT
        deleted-entry phases have no resume point of their own -- one that
        did not finish is run again from its start -- and each file they
        had stored was found again, its digest known, and written down as
        a duplicate of itself: one more "place it was found", at the same
        place, on every resume."""
        for rec in list(self.records) + list(self.duplicates):
            key = _resume_key(asdict(rec))
            if key is not None:
                self._resumed_keys.add(key)

    def recorded_before(self, **meta) -> bool:
        """Did the run this one resumes already store this find? Takes the
        same keywords as store() plus `size`."""
        key = _resume_key(meta)
        return key is not None and key in self._resumed_keys

    def next_index(self) -> int:
        """Sequential index for recovered_NNNN_* names, matching the
        fat32_core carve output convention."""
        n = self.counter
        self.counter += 1
        return n

    # Characters that mean something to a path on either platform: the
    # separators, a drive or alternate-data-stream colon, the Windows
    # wildcards, and control characters.
    _UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

    def _dest(self, category: str, ext: str, stem: str) -> str:
        # Every caller is meant to clean its stem, and one did not: a DASH
        # manifest's Representation id went in as written, and an id of
        # "/../../x" put a recovered file outside the case folder. A name
        # is made safe here, where every stored file passes, and the result
        # is checked to be inside the output folder before anything is
        # created.
        stem = self._UNSAFE_NAME.sub("_", str(stem)).strip(" .") or "recovered"
        ext = re.sub(r"[^A-Za-z0-9.]", "", str(ext)).strip(".").lower() or "bin"
        folder = self.CATEGORY_DIRS.get(category, "other")
        parts = [self.outdir, folder]
        if not self.flat:
            parts.append(ext)
        d = os.path.join(*parts)
        root = os.path.realpath(self.outdir)
        if os.path.commonpath([root, os.path.realpath(d)]) != root:
            raise ValueError(f"refusing to store outside {self.outdir}: {d}")
        os.makedirs(d, exist_ok=True)
        base = f"{stem}.{ext}"
        path = os.path.join(d, base)
        # The name is claimed by creating it exclusively, then filled by the
        # rename in store(). Checking that a name was free and renaming onto
        # it later left a gap in which a second writer could take the same
        # name -- and os.replace overwrites without a word, so one recovered
        # file silently replaced another.
        n = 1
        while True:
            try:
                with open(path, "xb"):
                    pass
                return path
            except FileExistsError:
                path = os.path.join(d, f"{stem}_{n}.{ext}")
                n += 1

    @staticmethod
    def _merge_into(kept: "Record", duplicate: "Record") -> None:
        """What a later copy of a kept file adds to the kept file's row.

        Called as each duplicate is stored, and again for every row of
        duplicates.csv when a case is resumed: the kept row in manifest.csv
        is written once and these changes lived only in memory until
        close(), so a crash and a resume lost the name, path and deleted
        flag a directory entry had given a carved file.
        """
        # Android exposes one file through several mount points, so
        # these "duplicates" are often one photograph seen four
        # ways rather than four files. Where that is what they are,
        # the other spellings are recorded against the file that
        # was kept: an examiner asked where it was should be able
        # to say all of them.
        # A carved file has no name: carving finds content and
        # loses everything else. If the same bytes arrive again
        # from a directory entry, that entry knows what the file
        # was called, where it sat and that it was deleted -- and
        # those are the difference between a picture and a file
        # somebody can be asked about. The bytes are not stored
        # twice; what is added is what the carve could not know.
        if (getattr(kept, "engine", "") == "carve"
                and getattr(duplicate, "engine", "") in
                ("fat-deleted", "mft", "filesystem")):
            if duplicate.source_path and not kept.source_path:
                kept.source_path = duplicate.source_path
            if duplicate.deleted:
                kept.deleted = True
            if duplicate.fs_type and not kept.fs_type:
                kept.fs_type = duplicate.fs_type
            if duplicate.note and duplicate.note not in (kept.note or ""):
                kept.note = ((kept.note + " · ") if kept.note else "") \
                    + duplicate.note
            kept.engine = f"{kept.engine}+{duplicate.engine}"

        mine = getattr(duplicate, "device_path", "")
        theirs = getattr(kept, "device_path", "")
        if mine and theirs and mine != theirs:
            try:
                from fme.engines import storage_views

                same_file = (storage_views.identity(mine) is not None
                             and storage_views.identity(mine)
                             == storage_views.identity(theirs))
            except Exception:  # noqa: BLE001
                same_file = False
            if same_file:
                seen = [p for p in (kept.also_seen_at or "").split("; ")
                        if p]
                if mine not in seen:
                    seen.append(mine)
                    kept.also_seen_at = "; ".join(sorted(seen))

    def store(self, chunks: Iterator[bytes], *, category: str, ext: str,
              stem: str, after_read=None, **meta) -> Optional[Record]:
        """Stream chunks to disk, hash on the fly, drop duplicates.

        `after_read(size)`, if given, is called once every chunk has been
        read and before anything is decided or written, and returns changes
        to `meta`. It is how a caller that learns something only by reading
        -- that the source gave out part way -- gets that into the row: the
        row is written once, here, and a file's row cannot be amended later
        without a crash losing the amendment.
        """
        # Unique to this call: a name built from the process id and the count
        # of records was the same for two writers in one process.
        tmp = os.path.join(self.outdir, f".tmp_{os.getpid()}_{uuid.uuid4().hex}")
        md5 = hashlib.md5()
        sha = hashlib.sha256()
        sha1 = hashlib.sha1()
        size = 0
        blank = True
        fill = None
        if self.output_failure is not None:
            # The output already refused a write it will refuse again; trying
            # each remaining find would only fill the log.
            self._lost("write", self.output_failure, category, ext, stem, meta)
            return None
        source = iter(chunks)
        read_error: Optional[OSError] = None
        try:
            with open(tmp, "wb") as fh:
                while True:
                    # Reading the evidence and writing the output shared one
                    # `except OSError`, so a bad read was logged as "write
                    # failed" and neither was counted.
                    try:
                        chunk = next(source)
                    except StopIteration:
                        break
                    except OSError as exc:
                        # Handled after the file is closed: Windows will not
                        # delete a file that is still open.
                        read_error = exc
                        break
                    if not chunk:
                        continue
                    fh.write(chunk)
                    md5.update(chunk)
                    sha.update(chunk)
                    sha1.update(chunk)
                    size += len(chunk)
                    if blank:
                        if fill is None:
                            fill = chunk[:1]
                        if chunk.strip(fill):
                            blank = False
        except OSError as exc:
            _silent_unlink(tmp)
            self._lost("write", exc, category, ext, stem, meta)
            return None
        if read_error is not None:
            _silent_unlink(tmp)
            self._lost("read", read_error, category, ext, stem, meta)
            return None
        if after_read is not None:
            meta.update(after_read(size) or {})
            if meta.get("bounded") == "short" and meta.get("engine") != "carve":
                self.read_short += 1
        if self._resumed_keys and self.recorded_before(size=size, **meta):
            # Stored by the run this one resumes: the same engine, volume,
            # path, record and size. Not a second place it was found.
            self.resume_skipped += 1
            _silent_unlink(tmp)
            return None

        # The floor is for lengths the carve guessed, where a tiny result is
        # usually a false match. A file whose size a file system recorded is
        # that size: an allocated 5 KB clip in a phone's camera folder was
        # dropped here like noise, with nothing counted and nothing said.
        floor = 0 if meta.get("engine") in SIZED_ENGINES else _floor(category)
        if size < floor:
            self.undersized_dropped += 1
            _silent_unlink(tmp)
            return None
        if blank:
            # Every byte identical: a sparse extent, a wiped region, or a file
            # whose data never reached the disk. Not evidence, and the reason a
            # corrupt directory entry could yield gigabytes of zeros named .mov.
            self.blank_dropped += 1
            _silent_unlink(tmp)
            return None

        digest = sha.hexdigest()
        # A file a crash left with no row was registered on resume with its
        # hashes and nothing else. Finding the same bytes again is the
        # chance to give it back what the crash lost -- where it was, when,
        # by which engine -- so it is stored as a new find, onto its file.
        orphan = self.by_digest.get(digest)
        if orphan is not None and orphan.engine != ORPHAN_ENGINE:
            orphan = None
        if orphan is None and self.dedupe and digest in self.seen:
            self.dupes += 1
            _silent_unlink(tmp)
            kept = self.by_digest.get(digest)
            if kept is not None:
                # Record the occurrence against the copy that was kept, so the
                # manifest shows every location without storing the bytes again.
                duplicate = Record(
                    category=category,
                    out_path=kept.out_path,
                    size=size,
                    md5=md5.hexdigest(),
                    sha256=digest,
                    sha1=sha1.hexdigest(),
                    ext=ext.lower(),
                    **meta,
                )
                timeutil.settle(duplicate)
                _writable_record(duplicate)
                # Worked out on a copy and applied to the kept file only once
                # the database has both: changed first, a failed write still
                # turned a carved file into "deleted, at its path, two
                # instances" in the manifest written at close, with no
                # duplicate row behind it (v2026.11.319).
                taught = copy.copy(kept)
                taught.instances += 1
                self._merge_into(taught, duplicate)
                try:
                    # The duplicate and what it taught the kept file, together.
                    self._db.add_duplicate(asdict(duplicate), asdict(taught))
                except (sqlite3.Error, OSError) as exc:
                    # The bytes are stored once already; what is lost is
                    # this place they were also found. Counted, not hidden.
                    self._lost("write", exc, category, ext, stem, meta)
                    return None
                kept.__dict__.update(taught.__dict__)
                # Committed: in memory at once, so close() -- which rebuilds
                # manifest.db from memory -- cannot drop it if the CSV
                # line below fails.
                self.duplicates.append(duplicate)
                self._csv_append(self._dup_csv, self._dup_csv_fh, duplicate)
            return None

        if orphan is not None:
            # The bytes are already on disk under the orphan's name, and a
            # SHA-256 match says they are these bytes: keep that file, which
            # is the one the case folder has always held, and drop the copy.
            _silent_unlink(tmp)
            dest = os.path.join(self.outdir, orphan.out_path)
        else:
            dest = self._dest(category, ext, stem)
        try:
            if orphan is None:
                # On disk before its row commits. manifest.db commits with
                # synchronous=FULL, so after a power cut the row could
                # outlive the file's bytes: a listed file, with hashes, that
                # is empty or missing. The file, then the name, then the row.
                _fsync_file(tmp)
                os.replace(tmp, dest)
                _fsync_dir(os.path.dirname(dest))
        except OSError as exc:
            _silent_unlink(tmp)
            _silent_unlink(dest)        # the empty name it had claimed
            self._lost("write", exc, category, ext, stem, meta)
            return None

        self.seen[digest] = dest
        rec = Record(
            category=category,
            out_path=os.path.relpath(dest, self.outdir),
            size=size,
            md5=md5.hexdigest(),
            sha256=digest,
            sha1=sha1.hexdigest(),
            ext=ext.lower(),
            **meta,
        )
        if self.metadata and category == "image":
            rec.animated = is_animated(dest, ext)
        if self.metadata and category in ("image", "video"):
            # Read from the stored copy: it is already on disk, so this costs
            # a short read rather than holding the file in memory again.
            found_meta = exif_read.read_file(dest)
            for key, value in found_meta.items():
                if hasattr(rec, key) and value not in ("", None):
                    setattr(rec, key, value)
            # A container's own capture time (mvhd) is UTC by the format but
            # some cameras write local wall-clock there; the row says whose
            # claim it is so a report does not present it as settled.
            basis = found_meta.get("captured_basis")
            if basis and rec.captured:
                rec.note = (rec.note + "; " if rec.note else "") + \
                    f"capture time: {basis}"

            # A video keeps its location in the QuickTime structure rather
            # than in EXIF, so until this was read a case where the
            # pictures were placed and the videos were not looked like a
            # case whose videos were taken somewhere unrecorded.
            if category == "video" and rec.latitude is None:
                try:
                    from fme.engines import videometa
                    where = videometa.read(dest)
                except Exception:  # noqa: BLE001 - a clip that will not parse
                    where = {"found": False}
                if where.get("found"):
                    rec.latitude = where["latitude"]
                    rec.longitude = where["longitude"]

        # Last, after EXIF and the video's own header have been read into it:
        # every time it carries goes where its kind belongs (timeutil).
        timeutil.settle(rec)
        _writable_record(rec)
        # The row first: kept in memory first, a row that failed to write left
        # a record manifest.json had and manifest.csv -- what a resume and the
        # live gallery read -- did not.
        # The record first: a file without one would be in the case folder
        # and in nothing that reads it.
        if not self._record_to_db(rec, category, ext, stem, meta):
            if orphan is None:
                _silent_unlink(dest)
            return None
        # Committed to manifest.db: in memory now, before the CSV line. The
        # CSV write raising here once left the record out of self.records,
        # and close() -- which rebuilds manifest.db from memory -- then
        # deleted the row that had been committed.
        if orphan is not None:
            rec.instances = max(rec.instances, orphan.instances)
            self.records[self.records.index(orphan)] = rec
            if orphan.out_path in self.orphans:
                self.orphans.remove(orphan.out_path)
            self.orphans_adopted += 1
        else:
            self.records.append(rec)
        self.by_digest[digest] = rec
        self._csv_append(self._csv, self._csv_fh, rec)
        return rec

    def _csv_append(self, writer, fh, rec: "Record") -> None:
        """One row onto manifest.csv or duplicates.csv, after the record is
        committed. A failure is logged and counted, not raised: the record
        is safe in manifest.db, and close() rewrites both CSVs from it."""
        try:
            writer.writerow(asdict(rec))
            fh.flush()
        except (OSError, ValueError, csv.Error) as exc:
            self.csv_failed += 1
            LOG.error("manifest CSV row for %s not written (%s); it is in "
                      "manifest.db, and the CSV is rewritten from that when "
                      "the run closes", rec.out_path, exc)

    # adopt() was here: a path for worker processes that wrote carved files
    # themselves, for the parent to take over. The parallel carve stopped
    # working that way -- workers find, the parent stores -- and adopt() was
    # left uncalled, with a NameError on `digest` nobody could reach.

    # How many lost finds are named one by one in the summary and the log.
    # The counts are always complete; the list is for finding them again.
    FAILURES_KEPT = 200

    def _lost(self, kind: str, exc, category: str, ext: str, stem: str,
              meta: dict) -> None:
        """Count a find that could not be stored, and say so where it counts.

        kind is "read" (the evidence gave an error) or "write" (the output
        did). A write error that will repeat for every later file marks the
        output failed, which stops the run (stop_when_output_fails).
        """
        if kind == "read":
            self.read_failed += 1
        else:
            self.write_failed += 1
        reason = str(exc)
        where = (meta.get("source_path") or meta.get("device_path")
                 or (f"offset {meta['offset']}" if meta.get("offset") is not None
                     else "") or f"{stem}{ext}")
        if len(self.failures) < self.FAILURES_KEPT:
            self.failures.append({"kind": kind, "category": category,
                                  "where": where, "engine": meta.get("engine", ""),
                                  "error": reason})
        if (kind == "write" and self.output_failure is None
                and isinstance(exc, OSError) and _output_is_fatal(exc)):
            self.output_failure = reason
            LOG.error("OUTPUT FAILED: %s -- %s. Every later file would be lost "
                      "the same way, so the run is stopping. Files already "
                      "recovered are kept; free space or choose another output "
                      "folder, then resume.", self.outdir, reason)
        elif self.output_failure is None:
            LOG.error("%s failed, file NOT recovered: %s (%s)",
                      "Reading the evidence" if kind == "read" else "Writing the output",
                      where, reason)

    def close(self, summary: dict) -> None:
        fields = list(self._csv.fieldnames)
        self._csv_fh.close()
        self._dup_csv_fh.close()
        summary["duplicate_instances"] = len(self.duplicates)
        ranges = summary.get("unreadable_ranges")
        if ranges and len(ranges) > UNREAD_RANGES_KEPT:
            # The bytes were counted from every range; only the list of them
            # is shortened, and the count says how long it really was.
            summary["unreadable_range_count"] = len(ranges)
            summary["unreadable_ranges"] = list(ranges[:UNREAD_RANGES_KEPT])
        summary["undersized_dropped"] = self.undersized_dropped
        summary["read_short"] = self.read_short
        summary["read_failed"] = self.read_failed
        if self.resume_skipped:
            summary["resume_skipped"] = self.resume_skipped
        if self.csv_failed:
            summary["csv_rows_failed"] = self.csv_failed
        summary["write_failed"] = self.write_failed
        if self.failures:
            summary["failures"] = list(self.failures)
        if self.output_failure is not None:
            summary["output_failure"] = self.output_failure
        if self.orphans:
            summary["orphaned_files"] = list(self.orphans)
        if self.orphans_adopted:
            summary["orphans_restored"] = self.orphans_adopted
        # manifest.csv is written a row at a time, so a crash keeps what was
        # found -- but a file's row is written when it is first stored, and
        # what is learned after (another place it was found, its instance
        # count, a carved file matched to a deleted entry) reached only
        # manifest.json. A resume reads the CSV, so it brought back the older
        # values. Rewritten here from the records, it agrees with the JSON.
        # The record too: run_extraction changes records after they were
        # stored (containers marked, carved files matched to deleted
        # entries), and manifest.db must say what manifest.json says.
        self._db.replace_all([asdict(r) for r in self.records],
                             [asdict(d) for d in self.duplicates])
        self._db.set_meta("closed", "1")
        self._db.close()
        _write_replacing(self._csv_path, lambda fh: _csv_rows(fh, fields, self.records))
        # duplicates.csv too: a row that failed to append was missing from it
        # for good, while manifest.json listed it (v2026.11.319).
        _write_replacing(self._dup_csv_path,
                         lambda fh: _csv_rows(fh, fields, self.duplicates))
        # And both are written beside and renamed into place: a crash while
        # writing left an empty or half-written manifest.json, the file
        # every report reads.
        _write_replacing(
            os.path.join(self.outdir, "manifest.json"),
            lambda fh: json.dump(
                {"summary": summary,
                 "files": [asdict(r) for r in self.records],
                 "duplicates": [asdict(r) for r in self.duplicates]},
                fh, indent=2))
        # Its digest beside it, so a later change to the case record shows.
        try:
            from fme.engines import evidence_hash
            evidence_hash.write_manifest_digest(self.outdir)
        except OSError as exc:
            LOG.warning("Could not write the digest of manifest.json: %s", exc)


def _csv_rows(fh, fields: list, records: list) -> None:
    writer = csv.DictWriter(fh, fieldnames=fields)
    writer.writeheader()
    for rec in records:
        writer.writerow(asdict(rec))


def _write_replacing(path: str, write) -> None:
    """Write a file beside its final name, then rename it into place."""
    scratch = f"{path}.{os.getpid()}.tmp"
    with open(scratch, "w", newline="", encoding="utf-8") as fh:
        write(fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(scratch, path)


def _silent_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def is_animated(path: str, ext: str) -> bool:
    """Whether an image moves.

    A still and an animation are different things to look at and to grade,
    and the extension does not say which: most GIFs on a disk are static.
    GIF is counted by its image descriptors, APNG by its acTL chunk, WebP by
    its ANIM chunk -- each read from the first part of the file rather than
    by decoding it.
    """
    ext = (ext or "").lower()
    if ext not in ("gif", "png", "webp", "apng"):
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(2 * MIB)
    except OSError:
        return False

    if ext == "gif":
        # Each frame starts with an image descriptor; one of them is the
        # still image every GIF has.
        frames = 0
        # Header (6) plus logical screen descriptor (7), then the global
        # color table if the descriptor says there is one. Starting at 13
        # lands in the middle of that table and the walk goes nowhere.
        i = 13
        if len(head) > 10 and head[10] & 0x80:
            i += 3 * (2 ** ((head[10] & 7) + 1))
        while i < len(head) and frames < 2:
            block = head[i]
            if block == 0x2C:               # image descriptor
                frames += 1
                i += 10
                if i < len(head) and head[i] & 0x80:   # local color table
                    i += 3 * (2 ** ((head[i] & 7) + 1))
                i += 1
                while i < len(head) and head[i]:        # data sub-blocks
                    i += head[i] + 1
                i += 1
            elif block == 0x21:             # extension
                i += 2
                while i < len(head) and head[i]:
                    i += head[i] + 1
                i += 1
            else:
                break
        return frames > 1
    if ext in ("png", "apng"):
        return b"acTL" in head[:4096]
    return head[:4] == b"RIFF" and b"ANIM" in head[:4096]
