"""A carve worker that is alive but stuck does not stall the run.

v243 made the parallel carve survive a worker that dies. A worker that
stays alive inside a library call that never returns was not noticed: no
stripe came back, and the run waited forever. Past STRIPE_STALL_SECONDS
with nothing back, the workers are now dealt with as dead ones are.
A stopped process (SIGSTOP) stands in for the stuck one here.

Runs under the __main__ guard: the workers are spawned.
"""
import logging
import multiprocessing
import os
import shutil
import signal
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import media_core
import _engine  # noqa: E402 -- replaces a name in every engine module


def main() -> int:
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image as fixtures
    root = tempfile.mkdtemp(prefix="fme_stall-")
    # Seeded, so a run can be repeated. os.urandom gave a new 160 MiB each
    # run, and random data carves a stray EXR, DDS or ART about once in two
    # runs: the count came out 11 and the test failed for nothing the stall
    # did. The pictures are now found by their hashes, not counted.
    import hashlib
    import random
    noise = random.Random(20261104)
    blob = bytearray(noise.randbytes(1 << 20)) * 160
    planted = set()
    for i in range(10):
        pic = fixtures.make_jpeg(30 + i)
        at = (i * 15 + 3) << 20
        blob[at:at + len(pic)] = pic
        planted.add(hashlib.sha256(pic).hexdigest())
    image = os.path.join(root, "carve.dd")
    with open(image, "wb") as fh:
        fh.write(bytes(blob))
    _engine.replace("STRIPE_STALL_SECONDS", 4)    # for the rest of this test
    outcome = {}

    def run():
        outcome["summary"] = media_core.run_extraction(image, os.path.join(root, "out"),
                                                       mode="carve", workers=2)

    worker = threading.Thread(target=run, daemon=True)
    started = time.time()
    worker.start()
    while time.time() - started < 60 and len(multiprocessing.active_children()) < 2:
        time.sleep(0.2)
    time.sleep(1.0)
    stopped = multiprocessing.active_children()[0]
    os.kill(stopped.pid, signal.SIGSTOP)
    worker.join(180)
    summary = outcome.get("summary") or {}
    ok = not worker.is_alive()
    print(f"the run finishes though a worker is stuck: {ok} ({time.time() - started:.0f} s)")
    import json
    manifest = os.path.join(root, "out", "manifest.json")
    found = ({f["sha256"] for f in json.load(open(manifest, encoding="utf-8"))["files"]}
             if os.path.isfile(manifest) else set())
    right = planted <= found
    ok &= right
    print(f"with all 10 pictures: {right} ({len(planted & found)} of 10 by hash)")
    said = summary.get("carve_worker_stalls", 0) >= 1
    ok &= said
    print(f"and the stall recorded in its summary: {said}")
    try:
        os.kill(stopped.pid, signal.SIGKILL)
    except OSError:
        pass
    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
