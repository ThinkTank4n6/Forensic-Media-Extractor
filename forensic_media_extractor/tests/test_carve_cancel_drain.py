"""A cancel during the parallel carve's merge keeps every unstored file in
front of the resume.

The merge stores a finished stripe's records one at a time and checks for a
cancel before each. The progress note said the first unfinished stripe was
where a resume should start, but the records still waiting in the merge lay
below that, so a resume skipped them: they were never stored by either run.
When the last stripe was the one being merged, the note said the whole
image was done.
"""
import logging
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_cancel_drain-") + str(os.getpid())
STRIPE = 1000
RECORDS = [{"hit": i * 90, "start": i * 90, "length": 50, "ext": "jpg",
            "category": "image", "bounded": "parsed", "signature": "jpeg",
            "encoding": None} for i in range(10)]


def run(stripes_back: int) -> tuple:
    """Merge with `stripes_back` of two stripes returned; cancel after 4."""
    case = os.path.join(ROOT, f"case{stripes_back}")
    os.makedirs(case)
    out = types.SimpleNamespace(outdir=case, records=[])
    stored, checks = [], {"n": 0}

    def cancel_cb():
        checks["n"] += 1
        return checks["n"] > 4

    tasks = [(0, STRIPE + 10, 1, 10 ** 9, 100), (STRIPE, 2 * STRIPE, 1, 10 ** 9, 100)]

    def stripes(args, tasks, workers, specs, stats, on_stripe, cancel_cb):
        if stripes_back == 2:
            on_stripe(tasks[1], {"records": [], "candidates": 0, "unread": []})
        on_stripe(tasks[0], {"records": [dict(r) for r in RECORDS],
                             "candidates": len(RECORDS), "unread": []})
        return [], {"deaths": 0, "errors": {}, "last": None}

    saved_store = _engine.replace(
        "_store_carved", lambda reader, out, args, stats, sigs, rec, total:
        stored.append(rec["start"]) or True)
    saved_stripes = _engine.replace("_carve_stripes", stripes)
    args = types.SimpleNamespace(align=1, max_carve_size=10 ** 9, chunk_size=100,
                                 resume_from=0, resuming=None)
    stats = {"candidates": 0, "carved_files": 0, "carved_bytes": 0}
    try:
        media_core._parallel_carve_body(None, out, args, stats, None, cancel_cb,
                                        2, tasks, [], 2 * STRIPE)
    except media_core.Canceled:
        pass
    finally:
        _engine.restore(saved_store)
        _engine.restore(saved_stripes)
    return stored, media_core.read_progress(case).get("offset")


shutil.rmtree(ROOT, ignore_errors=True)
for back, label in ((1, "with a later stripe outstanding"),
                    (2, "while merging the last stripe")):
    stored, offset = run(back)
    lost = [r["start"] for r in RECORDS
            if r["start"] not in stored and offset is not None and r["start"] < offset]
    good = offset is not None and not lost
    ok &= good
    print(f"cancel {label}: stored {len(stored)}, resume offset {offset}, "
          f"skipped by a resume {lost} -- {'ok' if good else 'WRONG'}")

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
