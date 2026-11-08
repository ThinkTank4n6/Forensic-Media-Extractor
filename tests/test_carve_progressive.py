"""The parallel carve keeps its work, and a slow stripe is not a stuck one.

Two things the parallel carve got wrong:

- It stored nothing until every stripe was in, and wrote no progress
  note, so a crash or a Cancel at 95% kept no carved file at all and a
  resume carved the image again from the start. Stripes are now stored as
  soon as every stripe before them is in, and the note records how far
  that is.
- It called the workers stuck when no stripe had come back for
  STRIPE_STALL_SECONDS. A healthy stripe on a slow source can take longer
  than that; the workers were killed and the carve restarted with half as
  many. A worker now beats a heartbeat for every chunk it reads, and only
  silence counts as stuck.

Runs under the __main__ guard: the workers are spawned.
"""
import io
import logging
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module


def build(root: str) -> str:
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image as fixtures
    rng = __import__("random").Random(5)
    blob = bytearray(rng.getrandbits(8) for _ in range(1 << 20)) * 96
    for i in range(16):
        pic = fixtures.make_jpeg(20 + i)
        at = (i * 6 + 1) << 20
        blob[at:at + len(pic)] = pic
    path = os.path.join(root, "progressive.dd")
    with open(path, "wb") as fh:
        fh.write(bytes(blob))
    return path


def offsets(summary: dict) -> set:
    return {f["offset"] for f in summary["files"]
            if (f.get("engine") or "").startswith("carve")}


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_carve_progressive-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    image = build(root)

    alone = media_core.run_extraction(image, os.path.join(root, "alone"),
                                      mode="carve", workers=2, progress=False)
    want = offsets(alone)
    print(f"a carve left alone: {len(want)} file(s)")

    print("\na slow stripe is not a stuck one")
    # A slow source, made by running the workers a tenth of the time: they
    # keep reading, but no stripe comes back within the stall limit.
    import multiprocessing
    import signal
    saved = _engine.replace("STRIPE_STALL_SECONDS", 3.0)
    result = {}

    def run():
        result["summary"] = media_core.run_extraction(
            image, os.path.join(root, "slow"), mode="carve", workers=2,
            chunk_size="256K", progress=False)

    worker = threading.Thread(target=run, daemon=True)
    started = time.time()
    worker.start()
    while worker.is_alive():
        children = multiprocessing.active_children()
        for child in children:
            try:
                os.kill(child.pid, signal.SIGSTOP)
            except OSError:
                pass
        time.sleep(0.9)
        for child in children:
            try:
                os.kill(child.pid, signal.SIGCONT)
            except OSError:
                pass
        time.sleep(0.1)
    _engine.restore(saved)
    slow = result.get("summary") or {}
    calm = bool(slow) and not slow.get("carve_worker_stalls") \
        and not slow.get("carve_worker_deaths")
    ok &= calm
    print(f"   no stall declared though no stripe came back within the limit: "
          f"{calm} ({time.time() - started:.0f} s)")
    same = bool(slow) and offsets(slow) == want
    ok &= same
    print(f"   and the same files: {same}")

    print("\na carve canceled part way through")
    out = os.path.join(root, "canceled")
    flag = {"stop": False}
    seen = {"stripes": 0}

    def progress(done, total, recovered, phase, *_):
        if phase == "carve" and done:
            seen["stripes"] += 1
            if seen["stripes"] >= 3:
                flag["stop"] = True

    first = media_core.run_extraction(image, out, mode="carve", workers=2,
                                      progress=False, progress_cb=progress,
                                      cancel_cb=lambda: flag["stop"])
    kept = first.get("canceled") and first.get("carved_files", 0) > 0
    ok &= bool(kept)
    print(f"   keeps the files carved before the cancel: {bool(kept)} "
          f"({first.get('carved_files')} of {len(want)})")
    note = media_core.read_progress(out)
    noted = note.get("phase") == "carve" and 0 < note.get("offset", 0) < os.path.getsize(image)
    ok &= noted
    print(f"   and notes how far it reached: {noted} ({note.get('offset')})")
    second = media_core.run_extraction(image, out, mode="carve", workers=2,
                                       progress=False, resume=True)
    whole = offsets(second) == want
    ok &= whole
    print(f"   a resume finishes with the files of a run left alone: {whole}")
    twice = second.get("duplicate_instances", 0)
    ok &= twice == 0
    print(f"   none of them written down twice: {twice == 0} ({twice})")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
