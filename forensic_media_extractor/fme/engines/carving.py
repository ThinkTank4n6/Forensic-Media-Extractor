"""
carving.py -- Section 6 -- the carving engine: signature scan, length judgment, embedded
media, and the parallel carve with its progress note.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import itertools
import threading
import time
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
    Canceled, LOG, MIB, _check_cancel,
)
from fme.engines.image_readers import (  # noqa: F401
    ImageReader, human, open_image,
)
from fme.engines.signatures import (  # noqa: F401
    MAX_SIG_SPAN, SIGNATURES, scan_buffer,
)
from fme.engines.length_parsers import (  # noqa: F401
    ARTIFACT_SIGNATURES, decode_payload, signatures_from_fat32,
)
from fme.engines.carve_judging import (  # noqa: F401
    _carve_candidate,
)
from fme.engines.embedded import (  # noqa: F401
    scan_embedded,
)
from fme.engines.fs_walk import (  # noqa: F401
    _note_unread,
)



def _scan_carved(reader, out, args, stats: dict, signatures, start: int,
                 length: int, size: int, encoded: bool = False) -> None:
    """Media embedded in a file the carve just stored.

    The carve skips any signature that falls inside a file it has already
    accepted -- an mp4's moov box, a JPEG's thumbnail -- so that one file is
    not cut into many. What is genuinely embedded is found here instead, by
    opening the stored file. Both the single-worker and the parallel carve
    call this, so the same image gives the same files whatever the worker
    count.
    """
    # Not conditional on db_extract: looking inside a carved file needs
    # nothing from the database reader, and requiring it meant an install
    # without that module silently found no embedded media at all.
    if not (getattr(args, "scan_inside", False)
            and size <= getattr(args, "max_inside_size", 256 * MIB)):
        return
    # Only as far as was stored. A file cut short by an unreadable stretch
    # is `size` bytes long, and reading its judged length went on into the
    # bad stretch (zeros from a failing device) and past it, so "embedded"
    # finds came from bytes the stored file does not hold. An encoded run is
    # the exception: its stored size is the decoded image, its extent the
    # text on disk, and the two do not compare.
    blob = reader.read_clamped(start, length if encoded else min(length, size))
    if blob:
        # The partition of the file it was found in: inside a file carved
        # from an assembled array, `start` is the array's offset, and with no
        # partition it read as the image's (v2026.11.317).
        scan_embedded(blob, out=out, stats=stats, signatures=signatures,
                      source_label=f"carved@{start}", fs_label="",
                      part_label=getattr(args, "carve_partition", "") or "",
                      max_carve_size=args.max_carve_size)


class _SkipListed:
    """An OutputManager that will not store again what the case already lists.

    For re-scanning the inside of a carved file on a resume: the interrupted
    run may have stored some of what was embedded in it, and every embedded
    file names where it came from (carved@<offset>::...), so those are
    passed over rather than written down a second time as duplicates.
    """

    def __init__(self, out):
        self._out = out
        self._listed = {r.source_path for r in itertools.chain(
            out.records, getattr(out, "duplicates", ())) if r.source_path}

    def store(self, chunks, **meta):
        if meta.get("source_path") in self._listed:
            return None
        return self._out.store(chunks, **meta)

    def __getattr__(self, name):
        return getattr(self._out, name)


def _rescan_inside(reader, out, args, stats, signatures, rec: dict) -> None:
    """Finish looking inside a carved file a resumed case already holds.

    The interrupted run stored the file and then looked inside it; a crash
    part way through that left the rest of what was embedded unrecovered,
    and the resume skipped the file as already carved, so it never was.
    Only files after the resume point can be in that state -- the progress
    note is written after each file's inside has been scanned.
    """
    size = next((r.size for r in out.records
                 if _is_carved(r) and r.offset == rec["start"]), None)
    if size is None:
        return
    _scan_carved(reader, _SkipListed(out), args, stats, signatures,
                 rec["start"], rec["length"], size,
                 encoded=bool(rec.get("encoding")))


def _carved_reach(out, before: int) -> int:
    """How far the carved files already in the case, starting before
    `before`, reach: the first offset a resumed carve may accept a file at."""
    # Duplicates too, and by the span the carve stepped over rather than
    # the bytes stored. Reading only the kept files by size, a carve stored
    # as a duplicate (common: the carve covers allocated space too), one cut
    # short, or a decoded base64 run protected nothing nested inside it, and
    # a resume carved a "file" from inside it that an uninterrupted run never
    # reported. Rows from before `extent` existed fall back to the size.
    reach = 0
    for rec in itertools.chain(out.records, getattr(out, "duplicates", ())):
        if _is_carved(rec) and rec.offset is not None and rec.offset < before:
            reach = max(reach, rec.offset + (getattr(rec, "extent", None)
                                             or rec.size or 0))
    return reach


def _detect_range(reader, signatures, *, hit_lo: int, hit_hi: int,
                  start_lo: int, start_hi: Optional[int], next_allowed: int,
                  align: int, max_carve_size: int, chunk_size: int,
                  unread: Optional[list] = None, errors: Optional[dict] = None,
                  heartbeat=None, cancel_cb=None) -> tuple:
    """Every file the carve accepts among header hits in [hit_lo, hit_hi).

    A hit counts only if the file it implies starts in [start_lo,
    start_hi) and at or after `next_allowed`; each accepted file moves
    `next_allowed` to its end, exactly as in the single-worker carve.
    Hits are taken in position order, as the serial scan meets them.

    Each record carries `hit`, the position of the header bytes, because
    that -- not the file's start -- is the order the serial carve takes
    them in, and the merge has to take them in the same order.

    A read that comes back empty before the end of the image is not the
    end of the image. It is noted in `unread` and the scan moves on to
    the next chunk; it used to end the scan, so one unreadable stretch of
    a failing disk ended the carve -- and the run reported the carve as
    finished.
    """
    total = reader.size
    out, candidates = [], 0
    pos = hit_lo
    while pos < hit_hi:
        _check_cancel(cancel_cb)
        want = min(chunk_size + MAX_SIG_SPAN, total - pos)
        if want <= 0:
            break
        try:
            buf = reader.read(pos, want)
        except (OSError, IOError, RuntimeError) as exc:
            # A raised read is an unreadable stretch, as an empty one is
            # (below). Uncaught, it ended the serial carve -- the one phase
            # of the run not wrapped -- and in a worker lost the stripe.
            # Tried once more without the overlap into the next chunk, so a
            # bad stretch there does not cost this chunk too.
            LOG.warning("carve: read failed at %d (%s); noted as unreadable, "
                        "carrying on past it", pos, exc)
            if errors is not None:
                errors["read_errors"] = errors.get("read_errors", 0) + 1
            try:
                buf = reader.read(pos, min(chunk_size, total - pos))
            except (OSError, IOError, RuntimeError):
                buf = b""
        if heartbeat is not None:
            heartbeat.value += 1
        # Short of this chunk's own span -- a segment shorter than its
        # neighbors say, a reader that stops early (a degraded array stops
        # at its first missing piece), a bad stretch -- is a gap. Until
        # v2026.11.318 the whole rest of the chunk was called unreadable and
        # skipped, readable data after the gap with it: a 49 KB gap was
        # reported as 706 KB and a whole picture after it was never carved.
        # The gap is now probed for where reading starts again.
        expected = min(chunk_size, total - pos)
        resume_at = pos + chunk_size
        if len(buf) < expected:
            gap = pos + len(buf)
            resume_at = _past_gap(reader, gap, pos + expected)
            if unread is not None and resume_at > gap:
                unread.append((gap, resume_at - gap))
            if resume_at >= pos + expected:
                resume_at = pos + chunk_size
        if not buf:
            pos = resume_at
            continue
        for rel, sig in scan_buffer(buf, signatures=signatures):
            hit = pos + rel
            if rel >= chunk_size or hit >= hit_hi:
                break
            file_start = hit - sig.offset
            if file_start < 0 or file_start < start_lo or file_start < next_allowed:
                continue
            if start_hi is not None and file_start >= start_hi:
                continue
            if align > 1 and file_start % align != 0:
                continue
            candidates += 1
            judged = _carve_candidate(reader, sig, file_start, total,
                                      max_carve_size, signatures, errors)
            if judged is None:
                continue
            length, ext, category, bounded = judged
            out.append({"hit": hit, "start": file_start, "length": length,
                        "ext": ext, "category": category, "bounded": bounded,
                        "signature": sig.key, "encoding": sig.encoding})
            next_allowed = file_start + length
        pos = resume_at
    return out, candidates


# The step a gap is probed in. Small enough that readable data after a bad
# stretch is carved; large enough that a failing disk is not asked for every
# sector of a dead megabyte one at a time.
GAP_PROBE = 64 * 1024


def _past_gap(reader, start: int, end: int) -> int:
    """The first probe step after `start` that reads, or `end`.

    The read at `start` has just failed, so probing starts at the next step:
    starting at `start` could find it readable in a smaller read and never
    move on.
    """
    at = min(end, (start // GAP_PROBE + 1) * GAP_PROBE)
    while at < end:
        try:
            if reader.read(at, min(4096, end - at)):
                return at
        except (OSError, IOError, RuntimeError):
            pass
        at = min(end, (at // GAP_PROBE + 1) * GAP_PROBE)
    return end


def _already_carved(out) -> set:
    """Offsets of files a resumed case already holds from the carve.

    A resume backs up a little before where the interrupted run stopped,
    so files just before that point are found again. They are already in
    the case; storing them again found their digests known and wrote each
    one down as a second place the file was found -- at the same offset.

    The duplicates too, as _SkipListed reads them: a carved file whose
    bytes the case already held is recorded only as a duplicate, and
    looking in the kept files alone wrote that duplicate down again on
    every resume.
    """
    return {rec.offset for rec in itertools.chain(
                out.records, getattr(out, "duplicates", ()))
            if _is_carved(rec) and rec.offset is not None}


def _is_carved(rec) -> bool:
    """Was this file found by the carve? Its engine is "carve", or "carve+"
    and the engine that later named it (OutputManager.store merges a deleted
    entry into a carved file). The two resume helpers asked this two ways --
    one of them only "carve" -- and a merged file then reached one and not
    the other."""
    engine = rec.engine or ""
    if str(getattr(rec, "partition", "") or "").startswith(RAID_PARTITION):
        # Carved from an assembled RAID array: its offsets are the array's,
        # not the image's, and a resume of the image's carve must not take
        # them for its own.
        return False
    return engine == "carve" or engine.startswith("carve+")


def _store_carved(reader, out, args, stats, signatures, rec: dict,
                  total: int) -> bool:
    """Store one accepted carve record. False once the carve must stop."""
    got: dict = {}
    short: dict = {}

    def after_read(size: int) -> dict:
        read = got.get("bytes", rec["length"])
        if read >= rec["length"]:
            return {}
        # The source gave out inside the file. What was read is kept --
        # the start of a picture or a clip is still evidence -- but the row
        # must not claim a length nobody stored: bounded becomes "short",
        # and the note keeps how the full length had been judged.
        short.update(at=rec["start"] + read, missing=rec["length"] - read)
        return {"bounded": "short",
                "note": (f"unallocated or fs-independent; SHORT: the source "
                         f"could not be read at offset {rec['start'] + read:,}, "
                         f"so {read:,} of the {rec['length']:,} bytes judged "
                         f"({rec.get('bounded') or 'parsed'}) were stored")}

    stored = out.store(
        _payload_chunks(reader, rec["start"], rec["length"], rec.get("encoding"),
                        got=got),
        category=rec["category"], ext=rec["ext"],
        stem=f"recovered_{out.next_index():04d}_{rec['start']}",
        engine="carve", offset=rec["start"], signature=rec["signature"],
        bounded=rec.get("bounded", ""), extent=rec["length"],
        fs_type="", partition=getattr(args, "carve_partition", ""),
        note=getattr(args, "carve_note", "") or "unallocated or fs-independent",
        after_read=after_read)
    if short:
        # Counted and placed whether or not the part read was kept: a
        # stretch the carve could not read is a gap in what was examined.
        stats["carved_short"] = stats.get("carved_short", 0) + (1 if stored else 0)
        _note_unread(stats, [(short["at"], short["missing"])])
        LOG.warning("Carved file at %s cut short: unreadable at %s, %s not read",
                    human(rec["start"]), human(short["at"]), human(short["missing"]))
    if not stored:
        return True
    stats["carved_files"] += 1
    stats["carved_bytes"] += stored.size
    _scan_carved(reader, out, args, stats, signatures,
                 rec["start"], rec["length"], stored.size,
                 encoded=bool(rec.get("encoding")))
    # Non-overlapping extents cannot exceed the image. If they do, stop
    # rather than fill the disk with false positives.
    if stats["carved_bytes"] > total:
        LOG.error("carved %s from a %s image -- stopping; this is a "
                  "false-positive storm, please report it",
                  human(stats["carved_bytes"]), human(total))
        # Said in the summary and the report, not only the log: a carve
        # that stopped here covers the image only up to this point.
        stats["carve_incomplete"] = (
            f"the carve stopped at {human(rec['start'])} of {human(total)}: "
            f"it had carved more bytes than the image holds, which only "
            f"false matches produce")
        return False
    return True


def _note_sizeless(reader: ImageReader, stats: dict) -> bool:
    """A source of no size has nothing to carve -- say so rather than
    record a carve of zero bytes as a carve that finished. True if so."""
    if reader.size > 0:
        return False
    stats["carve_incomplete"] = (
        "the source reported a size of zero bytes, so nothing was carved; if "
        "it is not empty, its size could not be read and it was not examined")
    LOG.warning("Not carved: %s reports a size of zero bytes",
                getattr(reader, "name", "the source"))
    return True


def run_carve_engine(reader: ImageReader, out: OutputManager, args, stats: dict,
                     progress_cb=None, cancel_cb=None) -> None:
    if _note_sizeless(reader, stats):
        return
    total = reader.size
    chunk_size = args.chunk_size
    align = max(1, args.align)
    signatures = getattr(args, "signatures", None) or SIGNATURES

    # Picking up where an interrupted run stopped. The offset is rounded
    # down to a chunk and backed off by one signature's span, so a file
    # that straddled the point where the run died is still found: losing
    # a picture to the resume would be worse than scanning a megabyte
    # twice.
    pos = 0
    resume_from = int(getattr(args, "resume_from", 0) or 0)
    if resume_from > 0:
        pos = max(0, (resume_from // max(1, chunk_size)) * chunk_size
                  - MAX_SIG_SPAN)
        LOG.info("Resuming the carve at %s of %s", human(pos), human(total))

    # Skip matches inside an already-carved file. On a resume that includes
    # the files the interrupted run carved: one that began before the point
    # the scan restarts from and runs past it would otherwise have the
    # signatures nested inside it -- a thumbnail in a video's data -- carved
    # as files of their own, which a run that was never interrupted does not.
    next_allowed = _carved_reach(out, pos) if resume_from > 0 else 0
    # Any resume, not only one with an offset: a note written before the
    # carve had saved a position still leaves carved files in the case.
    already = _already_carved(out) if getattr(args, "resuming", None) else set()
    if getattr(args, "carve_already", None) is not None:
        already = args.carve_already       # an assembled array's own finds
    last_progress = last_log = time.time()
    if progress_cb:
        progress_cb(0, total, stats["carved_files"], "carve")
    unread: list = []

    LOG.info("Carving %s (chunk %s, align %d)", human(total), human(chunk_size), align)

    while pos < total:
        if cancel_cb is not None and cancel_cb():
            # Never behind where this run resumed from: the scan backs up
            # before that point, and everything behind it was dealt with by
            # the run before -- a note moved backward had the next resume
            # store those files' duplicates a second time.
            if getattr(args, "note_progress", True):
                _save_progress(out.outdir, {"phase": "carve",
                                            "offset": int(max(pos, resume_from)),
                                            "total": int(total),
                                            "carved": stats.get("carved_files", 0)},
                               every=0)
            raise Canceled()
        records, candidates = _detect_range(
            reader, signatures, hit_lo=pos, hit_hi=min(pos + chunk_size, total),
            start_lo=0, start_hi=None, next_allowed=next_allowed, align=align,
            max_carve_size=args.max_carve_size, chunk_size=chunk_size,
            unread=unread, errors=stats)
        stats["candidates"] += candidates
        for rec in records:
            # Advance past this file whether or not it was stored: a
            # duplicate or undersized result is still a real file here, and
            # leaving next_allowed behind would let the scanner match
            # signatures nested inside it (an mp4's moov box, a thumbnail).
            next_allowed = rec["start"] + rec["length"]
            if rec["start"] in already:
                if rec["hit"] >= resume_from:
                    _rescan_inside(reader, out, args, stats, signatures, rec)
                continue
            # Every header before the resume point was dealt with by the
            # interrupted run. The scan backs up to find a file straddling
            # it, and storing those again wrote each duplicate's location
            # down a second time and inflated its instance count.
            if rec["hit"] < resume_from:
                continue
            if not _store_carved(reader, out, args, stats, signatures, rec, total):
                _note_unread(stats, unread, reader)
                return

        pos += chunk_size
        # Where the scan has reached, written often enough that a crash
        # costs minutes rather than the whole run. A crash is not a
        # hypothetical: one took a 298 GiB case down with nothing in the
        # console to say why.
        if getattr(args, "note_progress", True):
            _save_progress(out.outdir, {"phase": "carve",
                                        "offset": int(max(pos, resume_from)),
                                        "total": int(total),
                                        "carved": stats.get("carved_files", 0)})
        now = time.time()
        if progress_cb and now - last_progress > 0.5:
            progress_cb(min(pos, total), total, stats["carved_files"], "carve")
            last_progress = now
        if args.progress and now - last_log > 5:
            pct = 100.0 * min(pos, total) / total if total else 100.0
            LOG.info("  carve %.1f%% (%s) -- %d files so far",
                     pct, human(min(pos, total)), stats["carved_files"])
            last_log = now

    _note_unread(stats, unread, reader)
    if progress_cb:
        progress_cb(total, total, stats["carved_files"], "carve")


# --------------------------------------------------------------------------
# Parallel carving
#
# Carving is not a GPU problem: the work is a byte-scan whose cost sits on
# memory bandwidth and disk (and, for E01, zlib decompression), not on
# arithmetic. What it is, is embarrassingly parallel across the image. Each
# worker opens the image itself, scans one stripe and reports what it
# found; the parent merges the reports in the order a serial scan would
# meet them, re-scans whatever a worker could not judge on its own, and
# stores, so it alone owns the manifest.
# --------------------------------------------------------------------------
_WORKER = {}


def _worker_init(image_path: str, custom_specs: list, images_only: bool,
                 videos_only: bool, artifacts: bool = False, floors=None,
                 heartbeat=None, credentials=None) -> None:
    """Runs once per worker process: open the image and rebuild the signature
    list, including any custom signatures (their length parsers are closures,
    so they are registered here rather than pickled).

    The size floors come across explicitly: a spawned worker imports this
    module fresh, and before they were passed it carved with the defaults
    whatever the examiner had set.
    """
    if floors:
        _set_floors(*floors)
    extra = [_SpecSignature(**spec) for spec in custom_specs]
    if heartbeat is not None:
        heartbeat.value += 1            # alive: opening an image can be slow
    reader = open_image(image_path, credentials)
    if credentials:
        # Without it a worker carves the ciphertext of a volume its parent
        # reads in the clear, and the two disagree on what is there.
        from fme.engines import unlock
        reader, _ = unlock.wrap(reader, credentials)
    if heartbeat is not None:
        _beat_on_every_read(reader, heartbeat)
    _WORKER["reader"] = reader
    _WORKER["signatures"] = active_signatures(images_only, videos_only, extra,
                                              artifacts)
    _WORKER["heartbeat"] = heartbeat


def _beat_on_every_read(reader, heartbeat) -> None:
    """Make every read this worker does count as a sign of life.

    The heartbeat used to beat once per chunk of the scan, and nowhere else.
    Judging one header -- carve_judge walking a video's frames, a parser
    following a chain of boxes, the cut at the next header -- reads up to
    max_carve_size without returning to the scan, and on a slow source that
    is longer than STRIPE_STALL_SECONDS. Healthy workers were killed as
    stuck, the run was retried with half as many, and the error blamed
    memory. A worker that is reading is not stuck, whichever part of the
    carve is asking.
    """
    inner = reader.read

    def read(offset, length):
        heartbeat.value += 1
        return inner(offset, length)

    read.__doc__ = inner.__doc__
    reader.read = read      # an instance attribute: read_clamped goes through it


class _SpecSignature:
    """Minimal stand-in for a FileSignature, rebuilt inside each worker."""

    def __init__(self, ext, header, footer, max_size):
        self.ext = ext
        self.header = header
        self.footer = footer
        self.max_size = max_size


def _worker_carve(task: tuple) -> dict:
    """Carve one stripe -- detection only, nothing written.

    Workers used to extract and write what they found, leaving the parent to
    delete whatever overlapped. On a large image that is badly wrong: a
    stripe cannot see a file that began in the previous stripe, so every
    stripe wrote its own overlapping copies and peak disk use ran to many
    times the size of the image before the parent could reject them.
    Detection parallelises; extraction happens once, in the parent, for the
    candidates that survive the merge.

    A stripe that fails is reported as failed, with what went wrong, rather
    than raised: a raised exception ended the whole carve and threw away
    every stripe that had already come back.
    """
    start, end, align, max_carve_size, chunk_size = task
    reader = _WORKER["reader"]
    unread: list = []
    errors: dict = {}
    try:
        records, candidates = _detect_range(
            reader, _WORKER["signatures"], hit_lo=start, hit_hi=end,
            start_lo=start, start_hi=None, next_allowed=start, align=align,
            max_carve_size=max_carve_size, chunk_size=chunk_size,
            unread=unread, errors=errors,
            heartbeat=_WORKER.get("heartbeat"))
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        return {"records": [], "candidates": 0, "unread": unread,
                "error": f"{type(exc).__name__}: {exc}"}
    bad = list(getattr(reader, "bad_ranges", None) or [])
    if bad:
        reader.bad_ranges = []
    return {"records": records, "candidates": candidates,
            "unread": unread + bad, "parser_errors": errors.get("parser_errors", 0),
            "read_errors": errors.get("read_errors", 0),
            "judge_errors": errors.get("judge_errors", 0)}


# A stripe's upper bound in bytes. Stripes were sized only by count --
# four per worker -- so on a 2 TB image with sixteen workers each was
# 32 GB, and over a source delivering 200 MB/s between them the first
# came back after about three-quarters of an hour: past the stall limit,
# so healthy workers were killed and the carve started again with half as
# many, each slower. Capped, no stripe takes long enough to look stuck.
STRIPE_MAX_BYTES = int(os.environ.get("FME_STRIPE_MAX_BYTES", str(1 << 30)))


def _stripe_tasks(total: int, workers: int, align: int, max_carve_size: int,
                  chunk_size: int, resume_from: int = 0) -> list:
    """Split the image into more stripes than workers, so a stripe full of
    candidates cannot leave the other cores idle at the end of the run.

    A resume drops the stripes that finished. Without this the parallel
    carve ignored the resume entirely and re-read the whole image: no
    file was recovered twice, because the digests are known, but the
    hours a resume exists to save were spent anyway -- and the default is
    sixteen workers, so this is the path most runs take.

    `resume_from` means every header before it has been dealt with, which
    is what both carves record, so a stripe that ends at or before it is
    done.
    """
    stripes = max(workers * 4, workers, 1)
    if STRIPE_MAX_BYTES > 0:
        stripes = max(stripes, (total + STRIPE_MAX_BYTES - 1) // STRIPE_MAX_BYTES)
    size = max(chunk_size, (total + stripes - 1) // stripes)
    tasks = []
    pos = 0
    while pos < total:
        end = min(pos + size, total)
        # Each stripe reads a signature's span past its end, so a file
        # whose header straddles the boundary belongs to a stripe
        # rather than to neither.
        #
        # The stripes used to abut exactly. A signature starting within
        # MAX_SIG_SPAN of a boundary was in no worker's range and the
        # file was simply not recovered -- and because the number of
        # workers follows the machine's load, the boundaries move, so
        # the same image could give different results on different
        # runs. That is how this was found: a test lost one audio file
        # when three copies of it ran at once.
        #
        # The overlap can make two workers find the same file; the
        # merge keeps the first and drops the second, which starts
        # inside it.
        reach = min(end + MAX_SIG_SPAN, total) if end < total else end
        # Kept while its header window reaches past the resume point, not
        # only while the stripe itself does. A file starting just before a
        # stripe boundary with its header just after it (MP4 at +4, WAV and
        # AVI at +8) belongs to the stripe before the boundary -- the next
        # one refuses a file that starts before it -- and a resume at the
        # boundary dropped that stripe and lost the file without a word.
        # The merge does not store again what came before the resume point.
        if reach > max(0, resume_from):
            tasks.append((pos, reach, align, max_carve_size, chunk_size))
        pos += size
    return tasks


def default_workers() -> int:
    """One free core for the parent, capped: past a handful of workers the
    disk, not the CPU, is the limit."""
    try:
        cpus = os.cpu_count() or 1
    except NotImplementedError:  # pragma: no cover
        cpus = 1
    # Measured: one worker scans roughly 30 MiB/s, because the scan is 52
    # passes over every byte and that is memory-bound rather than
    # disk-bound. Eight workers therefore ask about 240 MiB/s of the disk,
    # which a single spinning disk cannot feed -- the old cap. A lab RAID
    # or NVMe can, and a machine with 64 cores sitting at 8 is leaving the
    # job four times longer than it needs to be.
    #
    # Sixteen asks about 500 MiB/s, which is roughly a SATA SSD. Beyond
    # that the disk usually is the limit, so the rest is left to the
    # examiner, who knows what the evidence is sitting on; the dropdown
    # offers every count up to one less than the cores.
    return max(1, min(16, cpus - 1)) if cpus > 2 else 1


PROGRESS_FILE = "run_state.json"
_progress_at: dict = {}      # case folder -> when its progress was last written
_PROGRESS_LOCK = threading.Lock()


def _save_progress(out_dir: str, state: dict, every: float = 5.0) -> None:
    """Write where the run has reached, beside the case.

    Written beside and renamed, like everything else here that must not be
    found half-written, and no more than every few seconds: the point is
    to lose minutes to a crash, not to make the run slower than the crash
    would have.
    """
    # Throttled per case, not across the process: the GUI runs jobs side
    # by side, and one module-wide clock let one job's save hold back the
    # others', so a crash could cost them far more than a few seconds.
    now = time.time()
    with _PROGRESS_LOCK:
        if now - _progress_at.get(out_dir, 0.0) < every:
            return
        _progress_at[out_dir] = now
    # Merged, not replaced: the carve's position and the record of which
    # phases have finished live in the same note, and writing only one of
    # them threw the other away.
    _update_progress(out_dir, lambda old: {
        **old, **state, "at": _dt.datetime.now(_dt.timezone.utc).isoformat()})


def _update_progress(out_dir: str, change) -> None:
    """Read the progress note, change it, and write it back, as one step.

    _save_progress took a lock around its clock but not around its write,
    phase_done took none, and both wrote through the same scratch name: two
    writers at once could each read the old note and lose the other's
    change, or interleave inside one scratch file. A torn note reads as no
    note, and a resume then starts from the beginning.
    """
    path = os.path.join(out_dir, PROGRESS_FILE)
    scratch = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with _PROGRESS_LOCK:
        try:
            state = change(read_progress(out_dir))
            with open(scratch, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(scratch, path)
        except (OSError, TypeError, ValueError) as exc:
            # A run is not failed over its own progress note -- including a
            # value json cannot write, which raised TypeError through the
            # carve and left a half-written scratch file in the case.
            LOG.debug("progress note not written: %s", exc)
            _silent_unlink(scratch)


def phase_done(out_dir: str, name: str) -> None:
    """Record that a phase of the run finished.

    The carve is the long one, but it is not the only one: a run that died
    during the databases pass used to redo the file-system walk, the MFT
    recovery and the whole carve to reach it again. A phase recorded here
    is skipped by a resume, so a resume starts at the point of failure
    rather than at the point the carve happened to reach.
    """
    def mark(state: dict) -> dict:
        done = set(state.get("phases_done") or [])
        done.add(name)
        return {**state, "phases_done": sorted(done)}

    _update_progress(out_dir, mark)


def phase_skipped(resuming: dict, name: str) -> bool:
    """Was this phase already finished by the run being resumed?"""
    if not resuming:
        return False
    if name in (resuming.get("phases_done") or []):
        LOG.info("Skipping %s: the interrupted run had finished it", name)
        return True
    return False


def read_progress(out_dir: str) -> dict:
    """Where an interrupted run had reached, if it said."""
    try:
        with open(os.path.join(out_dir, PROGRESS_FILE), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def clear_progress(out_dir: str) -> None:
    """A run that finished has nothing to resume."""
    # And the next run in this folder writes its first note at once: the
    # throttle remembered the last run's time, so a run started within a
    # few seconds of the last one finishing skipped its first save.
    with _PROGRESS_LOCK:
        _progress_at.pop(out_dir, None)
    try:
        os.remove(os.path.join(out_dir, PROGRESS_FILE))
    except OSError:
        pass


def run_carve_engine_parallel(reader: ImageReader, out: OutputManager, args,
                              stats: dict, progress_cb=None, cancel_cb=None,
                              workers: int = 2) -> None:

    total = reader.size
    custom_specs = [
        {"ext": s.ext, "header": s.header, "footer": s.footer,
         "max_size": s.max_size}
        for s in (getattr(args, "raw_custom_signatures", None) or [])
    ]
    tasks = _stripe_tasks(total, workers, max(1, args.align),
                          args.max_carve_size, args.chunk_size,
                          resume_from=int(getattr(args, "resume_from", 0) or 0))
    LOG.info("Carving %s with %d worker(s) over %d stripe(s)",
             human(total), workers, len(tasks))

    _parallel_carve_body(reader, out, args, stats, progress_cb, cancel_cb,
                         workers, tasks, custom_specs, total)


# How many times the stripes still outstanding are handed to a fresh set of
# workers after one dies, each time with half as many.
WORKER_RETRIES = 3
# How long the carve waits with no worker reading anything before it treats
# the workers as stuck -- alive, but inside a library call that never
# returns. Measured by a heartbeat every worker beats once per chunk read,
# not by stripes coming back: a stripe is a slice of the image, and on a
# slow source a healthy one can take longer than this.
STRIPE_STALL_SECONDS = float(os.environ.get("FME_STRIPE_STALL_SECONDS", "1800"))


class CarveIncomplete(RuntimeError):
    """The parallel carve lost stripes it could not get back. Raised on
    purpose, after carve_incomplete is recorded, so the run fails loudly
    with what to try -- unlike an unexpected error, which run_extraction
    records and carries on from."""


class _CarveStop(Exception):
    """The merge decided the carve must stop (a false-positive storm)."""


def _stop_pool(pool) -> None:
    """End a worker pool now, its running workers included."""
    for process in list((getattr(pool, "_processes", None) or {}).values()):
        try:
            process.kill()          # a stopped or stuck worker ignores SIGTERM
        except Exception:  # noqa: BLE001 - already gone
            pass
    pool.shutdown(wait=False, cancel_futures=True)


def _carve_stripes(args, tasks, workers, custom_specs, stats, on_stripe,
                   cancel_cb) -> tuple:
    """Carve every stripe in worker processes, surviving a worker's death.

    A multiprocessing.Pool whose worker is killed outright -- by the
    operating system when memory runs out, or by a crash in a C library
    such as libewf on a damaged segment -- replaces the worker and never
    hands its stripe out again, so the run waited for that stripe forever.

    A ProcessPoolExecutor notices a dead worker and says so. The stripes
    still outstanding then go to a fresh pool with half as many workers --
    memory being the usual reason a worker dies -- up to WORKER_RETRIES
    times. A stripe that fails with an error is retried the same way.
    Cancel is checked twice a second whether or not anything has come back.

    Returns (stripes never carved, why). It no longer raises when it gives
    up: the caller has stored what came back, and it is the caller that
    says what is missing.
    """
    import multiprocessing
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    from concurrent.futures.process import BrokenProcessPool

    context = multiprocessing.get_context("spawn")
    remaining = list(tasks)
    why = {"deaths": 0, "stalls": 0, "errors": {}, "last": None}
    for attempt in range(1, WORKER_RETRIES + 2):
        heartbeat = context.RawValue("q", 0)
        initargs = (args.image, custom_specs, args.images_only, args.videos_only,
                    getattr(args, "artifacts", False),
                    (_floor("image"), _floor("video")), heartbeat,
                    getattr(args, "credentials", None))
        pool = ProcessPoolExecutor(
            max_workers=max(1, workers), mp_context=context,
            initializer=_worker_init, initargs=initargs)
        futures = {pool.submit(_worker_carve, task): task for task in remaining}
        finished_tasks, broken = set(), None
        last_beat, last_moved = -1, time.time()
        try:
            pending = set(futures)
            while pending and broken is None:
                if cancel_cb is not None and cancel_cb():
                    _stop_pool(pool)
                    raise Canceled()
                finished, pending = wait(pending, timeout=0.5,
                                         return_when=FIRST_COMPLETED)
                beat = heartbeat.value
                if finished or beat != last_beat:
                    last_beat, last_moved = beat, time.time()
                elif time.time() - last_moved > STRIPE_STALL_SECONDS:
                    # Alive but not reading: dealt with as a dead worker is,
                    # since waiting longer recovers nothing.
                    stats["carve_worker_stalls"] = stats.get("carve_worker_stalls", 0) + 1
                    why["stalls"] += 1
                    broken = RuntimeError(
                        f"no worker read anything for {STRIPE_STALL_SECONDS:.0f} s")
                    break
                for future in finished:
                    task = futures[future]
                    try:
                        result = future.result()
                    except BrokenProcessPool as exc:
                        broken = exc
                        continue
                    except Exception as exc:  # noqa: BLE001 - one stripe, retried
                        why["errors"][task] = f"{type(exc).__name__}: {exc}"
                        continue
                    if result.get("error"):
                        why["errors"][task] = result["error"]
                        LOG.warning("Stripe at %s failed: %s", human(task[0]),
                                    result["error"])
                        continue
                    finished_tasks.add(task)
                    why["errors"].pop(task, None)
                    on_stripe(task, result)
        except BaseException:
            _stop_pool(pool)
            raise
        if broken is None:
            pool.shutdown(wait=True)
        else:
            _stop_pool(pool)
            why["deaths"] += 1
            why["last"] = broken
            stats["carve_worker_deaths"] = stats.get("carve_worker_deaths", 0) + 1
        remaining = [task for task in remaining if task not in finished_tasks]
        if not remaining:
            return [], why
        if attempt > WORKER_RETRIES:
            break
        if broken is not None:
            workers = max(1, workers // 2)
            LOG.warning("A carve worker process died (%s). %d stripe(s) were "
                        "outstanding; carving them again with %d worker(s).",
                        broken, len(remaining), workers)
        else:
            LOG.warning("%d stripe(s) failed; carving them again.", len(remaining))
    return remaining, why


def _parallel_carve_body(reader, out, args, stats, progress_cb, cancel_cb,
                         workers, tasks, custom_specs, total):
    """Merge the stripes into exactly what a single worker would carve.

    The merge takes records in the order the serial scan meets their
    headers and skips anything that starts inside a file already
    accepted. That alone was not enough: a worker cannot see a file that
    began in the previous stripe, so it could accept something nested
    inside that file and, stepping over it, hide a real file that lay
    beyond the outer file's end. The merge then correctly dropped the
    nested record -- and nobody had reported the file it hid. So when the
    merge drops a record, the stretch that record had hidden in its worker
    is scanned again here, from the merge's own position. Measured before
    this: a JPEG just after a WAV that straddled a stripe boundary was lost
    with two workers and found with one.

    Stripes are stored as soon as every stripe before them is in, rather
    than only once all are: a crash keeps what came before the first
    unfinished stripe, and the run's progress note says where that is.
    The progressive version written before this recovered nothing, because
    no test carved with more than one worker; test_parallel_merge.py now
    compares every worker count against one.
    """
    import heapq

    signatures = getattr(args, "signatures", None) or SIGNATURES
    align = max(1, args.align)
    resume_from = int(getattr(args, "resume_from", 0) or 0)
    outstanding = sorted(tasks, key=lambda t: t[0])
    heap: list = []
    order = itertools.count()
    unread: list = []
    state = {"done": 0, "found": 0, "stopped": False}
    # The same on a resume as in run_carve_engine: nothing nested inside a
    # file the interrupted run had already carved, and nothing it had
    # already stored written down again as a duplicate of itself.
    state["next_allowed"] = _carved_reach(
        out, min((t[0] for t in tasks), default=0)) if resume_from > 0 else 0
    already = _already_carved(out) if getattr(args, "resuming", None) else set()

    def push(records):
        for rec in records:
            heapq.heappush(heap, (rec["hit"], next(order), rec))

    def rescan(hit: int, hidden_end: int) -> None:
        # Every header the dropped record's worker stepped over: from the
        # record's own header (or the merge's position, if that is later --
        # nothing before it can start a file the merge would take) to the
        # end of the record.
        lo = max(hit, state["next_allowed"])
        records, candidates = _detect_range(
            reader, signatures, hit_lo=lo,
            hit_hi=min(total, hidden_end + MAX_SIG_SPAN), start_lo=0,
            start_hi=hidden_end, next_allowed=state["next_allowed"],
            align=align, max_carve_size=args.max_carve_size,
            chunk_size=args.chunk_size, unread=unread, errors=stats,
            cancel_cb=cancel_cb)
        stats["candidates"] += candidates
        stats["carve_rescans"] = stats.get("carve_rescans", 0) + 1
        push(records)

    def drain(boundary: float) -> None:
        while heap and heap[0][0] < boundary and not state["stopped"]:
            _check_cancel(cancel_cb)
            _, _, rec = heapq.heappop(heap)
            # Off the heap but not yet dealt with: a cancel during the
            # re-scan or the store below must keep it in front of the
            # resume point (resume_point), or the stretch is lost to both runs.
            state["pending"] = rec["hit"]
            end = rec["start"] + rec["length"]
            if rec["start"] < state["next_allowed"]:
                if end > state["next_allowed"]:
                    rescan(rec["hit"], end)
                state["pending"] = None
                continue
            state["next_allowed"] = end
            # As in the serial carve: a header before the resume point was
            # dealt with by the interrupted run; this one only re-establishes
            # where the merge is.
            if rec["start"] in already or rec["hit"] < resume_from:
                if rec["start"] in already and rec["hit"] >= resume_from:
                    _rescan_inside(reader, out, args, stats, signatures, rec)
                state["pending"] = None
                continue
            if not _store_carved(reader, out, args, stats, signatures, rec, total):
                state["stopped"] = True
            state["pending"] = None

    def committed_to() -> float:
        return outstanding[0][0] if outstanding else float("inf")

    def resume_point() -> float:
        # Where a resume must start: not the first unfinished stripe, but
        # the first header nothing has stored yet. A cancel lands inside
        # drain(), between one stored record and the next, and the records
        # still in the heap lie below committed_to(); saving committed_to()
        # there told the resume they were done, and they were never stored.
        # When the last stripe came back committed_to() is infinite, which
        # wrote offset = total and lost the whole unstored remainder.
        reached = committed_to()
        if heap:
            reached = min(reached, heap[0][0])
        if state.get("pending") is not None:
            reached = min(reached, state["pending"])
        return reached

    def note_progress(every: float = 5.0) -> None:
        reached = resume_point()
        _save_progress(out.outdir, {
            "phase": "carve", "total": int(total),
            # Not behind the point this run resumed from (see the serial
            # carve): the kept stripes start before it.
            "offset": int(max(resume_from,
                              total if reached == float("inf") else reached)),
            "carved": stats.get("carved_files", 0)}, every=every)

    def on_stripe(task, result: dict) -> None:
        outstanding.remove(task)
        push(result["records"])
        unread.extend(tuple(r) for r in result.get("unread") or [])
        stats["candidates"] += result["candidates"]
        for key in ("parser_errors", "read_errors", "judge_errors"):
            if result.get(key):
                stats[key] = stats.get(key, 0) + result[key]
        state["done"] += 1
        drain(committed_to())
        if state["stopped"]:
            raise _CarveStop()
        note_progress()
        if progress_cb:
            progress_cb(int(total * state["done"] / max(1, len(tasks))), total,
                        stats["carved_files"], "carve")

    try:
        try:
            missed, why = _carve_stripes(args, tasks, workers, custom_specs,
                                         stats, on_stripe, cancel_cb)
        except _CarveStop:
            missed, why = [], None

        if not state["stopped"]:
            # Whatever is left: everything, when all stripes came back; and,
            # when some never did, the stripes after them, merged across the
            # gap. Inside the cancel handler: a cancel here also has records
            # left in the heap that the note must keep in front of a resume.
            drain(float("inf"))
    except Canceled:
        # The throttled note can be seconds behind what was stored; a
        # resume from a stale note carves again what is already in the case.
        note_progress(every=0)
        raise
    _note_unread(stats, unread, reader)

    if missed:
        gaps = ", ".join(f"{human(t[0])}-{human(t[1])}" for t in missed[:5])
        stats["carve_incomplete"] = (
            f"{len(missed)} stripe(s) of the image could not be carved "
            f"({gaps}{', ...' if len(missed) > 5 else ''}); files found "
            f"elsewhere were kept")
        if why["stalls"] and why["stalls"] >= why["deaths"]:
            # Stuck, not dead: running out of memory is not the likely
            # reason, and telling the examiner it was sent them the wrong way.
            raise CarveIncomplete(
                f"The carve workers stopped reading for "
                f"{STRIPE_STALL_SECONDS:.0f} s at a time, {why['stalls']} "
                f"times, and {len(missed)} stripe(s) of the image could not "
                f"be carved. That is usually a source that has stopped "
                f"answering -- a failing disk, a dropped network share -- or "
                f"a library call that never returned. The files carved from "
                f"the rest of the image were kept; --workers 1 carves in "
                f"this process alone, and FME_STRIPE_STALL_SECONDS sets the "
                f"limit for a source that is simply slow.")
        if why["deaths"]:
            raise CarveIncomplete(
                f"A carve worker process died {why['deaths']} times and "
                f"{len(missed)} stripe(s) of the image could not be "
                f"carved. A worker dying without an error is usually the "
                f"machine running out of memory: run again with fewer "
                f"workers (--workers 1 carves in this process alone). The "
                f"files carved from the rest of the image were kept.")
        first = next(iter(why["errors"].values()), "unknown error")
        raise CarveIncomplete(
            f"{len(missed)} stripe(s) of the image failed to carve ({first}). "
            f"The files carved from the rest of the image were kept; "
            f"--workers 1 carves in this process alone.")

    if progress_cb:
        progress_cb(total, total, stats["carved_files"], "carve")


def _payload_chunks(reader: ImageReader, start: int, length: int,
                    encoding: Optional[str] = None,
                    got: Optional[dict] = None) -> Iterator[bytes]:
    """The bytes to write for one carved extent.

    For an ordinary signature that is the raw range. For an encoded one --
    base64 in an HTML page, hex in an RTF document -- it is the decoded
    image, because the run on disk is text and writing it out verbatim would
    give an examiner a wall of characters rather than a picture.
    """
    if not encoding:
        yield from _carve_chunks(reader, start, length, got=got)
        return
    raw = reader.read_clamped(start, length)
    if got is not None:
        got["bytes"] = len(raw)     # source bytes read, before decoding
    decoded = decode_payload(raw, encoding)
    if decoded:
        yield decoded


def _carve_chunks(reader: ImageReader, start: int, length: int,
                  chunk: int = 4 * MIB, got: Optional[dict] = None) -> Iterator[bytes]:
    """The bytes of one carved extent, as far as the source will give them.

    An empty read ends it: nothing past an unreadable stretch is stored, and
    no bytes are made up to fill the gap -- a zero-filled file would have a
    digest no copy of the original ever had. What it does do is say how far
    it got, in `got["bytes"]`, so the caller can record the file as short.
    It used to stop without a word, and a video judged 4 MiB by its own
    header was stored as 2 MiB under bounded = "header".
    """
    off = 0
    try:
        while off < length:
            data = reader.read(start + off, min(chunk, length - off))
            if not data:
                return
            # A device reader that hits a bad sector salvages the rest of the
            # read and hands back zeros where the sector was (bad_ranges says
            # where). Those zeros went into the file and its hash under
            # bounded "header" or "parsed", with nothing on the row -- the
            # made-up bytes this function exists to refuse. Stop at the
            # first one instead, so the file is recorded as short there.
            cut = _first_bad(reader, start + off, len(data))
            if cut is not None:
                data = data[:cut - (start + off)]
                if data:
                    yield data
                    off += len(data)
                return
            yield data
            off += len(data)
    finally:
        if got is not None:
            got["bytes"] = off


def _first_bad(reader, pos: int, size: int) -> Optional[int]:
    """The first offset in [pos, pos + size) a reader filled with zeros."""
    first = None
    for bad_start, width in getattr(reader, "bad_ranges", None) or ():
        if bad_start < pos + size and bad_start + width > pos:
            here = max(pos, bad_start)
            first = here if first is None else min(first, here)
    return first


def active_signatures(images_only: bool = False, videos_only: bool = False,
                      extra_signatures=None, artifacts: bool = False) -> list:
    """Build the signature list for one run. Never mutates module state --
    the Flask server runs concurrent jobs against different settings."""
    sigs = list(SIGNATURES)
    if artifacts:
        sigs += list(ARTIFACT_SIGNATURES)
    if images_only and not videos_only:
        sigs = [x for x in sigs if x.category == "image"]
    elif videos_only and not images_only:
        sigs = [x for x in sigs if x.category == "video"]
    return signatures_from_fat32(extra_signatures) + sigs



# --------------------------------------------------------------------------
# Carving an assembled RAID array
# --------------------------------------------------------------------------
RAID_PARTITION = "raid:"


def run_raid_carve(image_path: str, members: list, out, args, stats: dict,
                   progress_cb=None, cancel_cb=None, credentials=None) -> int:
    """Carve each RAID array the image and its members assemble into.

    The file system walk read the assembled arrays (v2026.11.316); the
    carve still covered only the image named as the source. On a striped
    or parity array a member holds every other stripe, so a deleted
    picture larger than a stripe is on no member whole: carving the member
    found pieces or nothing (v2026.11.316). Each array is carved here as
    the operating system saw it, in this process, its files marked with
    the array they came from; offsets are the array's.
    """
    from fme.engines import image_readers, vendor_fs, unlock
    module = vendor_fs.probe()
    if module is None or not hasattr(module, "raid_open") or not members:
        return 0
    passwords = list(getattr(credentials, "passwords", ()) or ())
    keys = unlock.private_keys(credentials) if credentials else []
    carved_before = stats.get("carved_files", 0)
    arrays = []
    with module.raid_open([image_path] + list(members), passwords, keys) as raid:
        for array in raid.arrays:
            if array.image is None:
                continue
            _check_cancel(cancel_cb)
            label = str(getattr(array, "label", "") or "array")
            reader = image_readers.FileObjectReader(array.image, label)
            sub = argparse.Namespace(**vars(args))
            sub.resume_from, sub.resuming = 0, None
            sub.note_progress = False
            sub.carve_partition = RAID_PARTITION + label
            sub.carve_note = (f"unallocated or fs-independent, in the assembled "
                              f"RAID array {label} (offset in the array)")
            # What an interrupted run already stored from this array, so a
            # resume does not write each of them down again as a duplicate
            # of itself at the same offset (v2026.11.317).
            sub.carve_already = {
                rec.offset for rec in itertools.chain(
                    out.records, getattr(out, "duplicates", ()))
                if getattr(rec, "partition", "") == sub.carve_partition
                and rec.offset is not None}
            # Counted on its own. Shared with the image's carve, the byte
            # budget ("more carved than the array holds") already held all
            # the image's finds: the array stored one file and stopped, and
            # its gaps were listed as if they were the image's.
            local = collections.defaultdict(int)
            LOG.info("Carving the assembled array %s (%s)", label, human(reader.size))
            try:
                run_carve_engine(reader, out, sub, local, progress_cb, cancel_cb)
            finally:
                entry = {"label": label, "size": reader.size,
                         "carved": local.get("carved_files", 0)}
                for key, value in local.items():
                    if key in ("unreadable_bytes",):
                        continue
                    if isinstance(value, int) and not isinstance(value, bool):
                        stats[key] = stats.get(key, 0) + value
                    elif value:
                        entry[key] = value
                if local.get("unreadable_bytes"):
                    entry["unreadable_bytes"] = local["unreadable_bytes"]
                arrays.append(entry)
                stats["raid_carved"] = arrays
    return stats.get("carved_files", 0) - carved_before
