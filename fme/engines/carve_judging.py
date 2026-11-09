"""
carve_judging.py -- The one judge of a carved file's length (_carve_candidate), shared by
the carve and the embedded scan -- keep it the only one.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import struct
from typing import Callable, Iterator, Optional, Sequence
from fme.engines import carve_judge
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
    LOG, MAX_IMAGE_DEFAULT, MAX_VIDEO_DEFAULT, MIB,
)
from fme.engines.image_readers import (  # noqa: F401
    ImageReader,
)
from fme.engines.signatures import (  # noqa: F401
    IMAGE_EXTS, MAX_SIG_SPAN, VIDEO_EXTS, scan_buffer,
)
from fme.engines.length_parsers import (  # noqa: F401
    PARSERS, STREAM_PARSERS, WEAK_PARSERS, _p_generic,
)



def _carve_candidate(reader, sig, file_start: int, total: int,
                     max_carve_size: int, signatures, errors: Optional[dict] = None):
    """Judge one header hit: is there a file here, and how long is it?

    The single-worker carve, the parallel workers and the parent's re-scans
    all call this, so a hit is judged the same way whichever of them saw
    it. They used to carry three copies of this logic, and the copies had
    drifted: the workers dropped an undersized file before stepping over
    it, the serial carve after, so the two found different files nested
    inside it.

    Returns (length, ext, category, bounded) for a file the scan must step
    over -- including one too small to keep, which store() refuses but which
    is still a real file and still hides what is nested inside it -- or None
    for a hit that is not a file at all.

    Where mediacarve reads the signature (carve_judge.JUDGED), it is the
    judge of the length and FME's parser only supplies the extension and,
    for a file whose end neither can confirm, the partial length that
    carve_judge cuts at the next validated header. `bounded` is how the
    length was arrived at: header, parsed or capped (see carve_judge).
    """
    cap = MAX_IMAGE_DEFAULT if sig.category == "image" else MAX_VIDEO_DEFAULT
    cap = min(cap, max_carve_size, total - file_start)
    if cap <= 0:
        return None

    def read(off: int, length: int) -> bytes:
        # A read the source refuses is an unreadable stretch, not a parser
        # fault: the parser sees the data end there, as it would on a short
        # read, and the judgment goes on.
        try:
            return reader.read_clamped(off, length)
        except (OSError, IOError, RuntimeError) as exc:
            LOG.warning("read failed at %d while judging a file at %d: %s",
                        off, file_start, exc)
            if errors is not None:
                errors["read_errors"] = errors.get("read_errors", 0) + 1
            return b""

    parser = PARSERS.get(sig.parser, _p_generic)
    result = None
    try:
        result = parser(read, file_start, cap)
    except (struct.error, ValueError, OverflowError, IndexError) as exc:
        LOG.debug("parser %s failed at %d: %s", sig.parser, file_start, exc)
    except Exception as exc:  # noqa: BLE001 - one bad parse never ends a carve
        # Anything else is a bug in a parser, not junk on the disk. It is
        # counted, so a run that hit one says so, rather than ending the
        # whole carve -- or, in a worker, the whole stripe.
        LOG.warning("parser %s raised at %d: %r", sig.parser, file_start, exc)
        if errors is not None:
            errors["parser_errors"] = errors.get("parser_errors", 0) + 1
    fme_length, ext_override = (result if result and result[0]
                                and result[0] > 0 else (None, None))
    if fme_length:
        fme_length = min(fme_length, total - file_start)

    use_judge = sig.key in carve_judge.JUDGED
    if use_judge:
        try:
            judged = carve_judge.judge(reader, sig.key, file_start, total,
                                       min(max_carve_size, MAX_VIDEO_DEFAULT),
                                       fme_length, ext_override or None)
        except Exception as exc:  # noqa: BLE001 - one judgment never ends a carve
            # Raised, it ended the serial carve, or in a worker lost the
            # whole stripe after its retries. Counted and said; the hit is
            # then judged as a format mediacarve does not read -- FME's
            # parser's length, cut at the next header if it found no end.
            LOG.warning("carve judge raised at %d (%s): %r; FME's parser "
                        "judges this one", file_start, sig.key, exc)
            if errors is not None:
                errors["judge_errors"] = errors.get("judge_errors", 0) + 1
            use_judge = False
    if use_judge:
        if judged is None:
            return None
        length, ext, bounded = judged
        ext = ext or sig.ext
        if bounded == "capped" and length < _floor(
                "image" if sig.category == "image" else "video"):
            return None   # a partial cut that short is not a file
    else:
        # Not a format mediacarve reads: FME's parser stands, as before.
        if not fme_length:
            return None
        length = fme_length
        bounded = "parsed"
        # Running to the cap means the parser found no end: its length is a
        # guess. Not for a stream parser that stopped at the end of the
        # image -- it validated every packet up to there, and the image
        # simply ended. Read as a guess, the stream was cut at the first
        # bytes inside it that looked like a header and then dropped as too
        # short, so an MPEG-TS recording that was the last thing on a card,
        # or the whole of an extracted segment, was never carved.
        at_image_end = file_start + length >= total and cap >= total - file_start
        if sig.parser in WEAK_PARSERS or (
                length >= cap and not (sig.parser in STREAM_PARSERS and at_image_end)):
            # Length was a guess, not a parsed structure. Cut it at the next
            # file header so a junk match cannot swallow real files -- and a
            # guess cut that short is not a file at all.
            length = _truncate_at_next_signature(reader, file_start, length,
                                                 signatures=signatures)
            bounded = "capped"
            if length < _floor("image" if sig.category == "image" else "video"):
                return None
        ext = ext_override or sig.ext
    category = sig.category
    if ext in IMAGE_EXTS:
        category = "image"
    elif ext in VIDEO_EXTS:
        category = "video"
    return length, ext, category, bounded




# A second, identical copy of _worker_init, _SpecSignature and
# _worker_carve was here. Identical today; the danger is tomorrow,
# when a fix lands in one of them.


# The second copy of the parallel-carve machinery was here -- nine
# definitions, byte-identical to the ones above except for
# _parallel_carve_body, where the copy that won was the older one that
# wrote nothing until every stripe had finished.
#
# Two defects came out of that duplication: a worker initializer whose
# signature did not match its call, and an examiner watching a long
# carve seeing nothing at all because the progressive version above was
# shadowed and never ran.
#
# Deleted rather than kept in step. A function defined twice is a
# function whose fixes have to be applied twice, and this is what
# happens when one of them is missed.



def _truncate_at_next_signature(reader: ImageReader, start: int, length: int,
                                skip: int = 16,
                                signatures: Optional[list] = None) -> int:
    """Shorten a guessed carve so it stops at the next file header."""
    pos = start + skip
    end = start + length
    window = 4 * MIB
    while pos < end:
        try:
            buf = reader.read(pos, min(window + MAX_SIG_SPAN, end - pos))
        except (OSError, IOError, RuntimeError) as exc:
            # Treated as the empty read below: a raised one escaped the
            # carve's judge and ended the carve.
            LOG.warning("read failed at %d looking for the next header: %s",
                        pos, exc)
            break
        if not buf:
            break
        found = scan_buffer(buf, first_only=True, signatures=signatures)
        if found:
            rel, sig = min(found, key=lambda h: h[0])
            cut = pos + rel - sig.offset
            if cut > start:
                return min(length, cut - start)
        pos += window
    return length
