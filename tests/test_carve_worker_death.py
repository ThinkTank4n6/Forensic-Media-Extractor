"""The parallel carve survives a worker dying, and Cancel always works.

A multiprocessing.Pool whose worker is killed outright -- by the operating
system when memory runs out, or by a crash in a C library on a damaged
segment -- replaces the worker and never hands its stripe out again, so the
run waited for it forever. Cancel was checked only when a stripe came
back, so it could not stop the run either, and the parallel carve stores
nothing until every stripe is in. Measured on v242: a 256 MB carve that
takes 14 s was still running 90 s after one worker was killed, and 30 s
after Cancel.

This kills a worker part way through a carve and checks the run finishes
with the same files as one left alone; checks that a run which keeps
losing workers stops with an error that says why; and checks Cancel stops
a carve within seconds.

The checks run under the __main__ guard because the workers are spawned:
each starts a fresh interpreter that imports this file.
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
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

from fme.engines import media_core
import _engine  # noqa: E402 -- replaces a name in every engine module


def build_image(root: str) -> str:
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image as fixtures
    rng = __import__("random").Random(91)
    blob = bytearray(rng.getrandbits(8) for _ in range(1 << 20)) * 192   # 192 MB
    for index in range(12):
        picture = fixtures.make_jpeg(40 + index)
        at = (index * 15 + 3) << 20
        blob[at:at + len(picture)] = picture
    path = os.path.join(root, "carve.dd")
    with open(path, "wb") as fh:
        fh.write(bytes(blob))
    return path


def carve(image: str, out: str, *, kill_after: float = 0.0,
          cancel_after: float = 0.0, deadline: float = 240.0) -> dict:
    """Run a two-worker carve, optionally killing a worker or canceling."""
    state, outcome = {"cancel": False}, {}

    def run():
        try:
            outcome["summary"] = media_core.run_extraction(
                image, out, mode="carve", workers=2,
                cancel_cb=lambda: state["cancel"])
        except media_core.Canceled:
            outcome["canceled"] = True
        except Exception as exc:  # noqa: BLE001 - reported below
            outcome["error"] = str(exc)

    started = time.time()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    if kill_after:
        while time.time() - started < 60 and len(multiprocessing.active_children()) < 2:
            time.sleep(0.2)
        time.sleep(kill_after)
        children = multiprocessing.active_children()
        if children:
            os.kill(children[0].pid, signal.SIGKILL)
            outcome["killed"] = True
    if cancel_after:
        while time.time() - started < 60 and len(multiprocessing.active_children()) < 2:
            time.sleep(0.2)
        time.sleep(cancel_after)
        state["cancel"] = True
        outcome["cancel_at"] = time.time()
    worker.join(deadline)
    outcome["finished"] = not worker.is_alive()
    outcome["ended"] = time.time()
    outcome["seconds"] = outcome["ended"] - started
    return outcome


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_worker_death-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    image = build_image(root)

    print("a carve left alone")
    alone = carve(image, os.path.join(root, "alone"))
    ok &= alone["finished"] and "summary" in alone
    files = (alone.get("summary") or {}).get("carved_files", 0)
    print(f"   finished in {alone['seconds']:.0f} s with {files} carved file(s)")

    print("\none worker killed part way through")
    killed = carve(image, os.path.join(root, "killed"), kill_after=1.5)
    summary = killed.get("summary") or {}
    finished = killed["finished"] and killed.get("killed") and "summary" in killed
    ok &= bool(finished)
    print(f"   the run finished: {bool(finished)} ({killed['seconds']:.0f} s)"
          + (f" -- {killed['error']}" if killed.get("error") else ""))
    same = summary.get("carved_files") == files
    ok &= same
    print(f"   with the same {files} file(s) as the run left alone: {same}")
    said = summary.get("carve_worker_deaths", 0) >= 1
    ok &= said
    print(f"   and the death is recorded in its summary: {said}")

    print("\na run that keeps losing workers")
    with _engine.replaced("WORKER_RETRIES", 0):
        gave_up = carve(image, os.path.join(root, "gave_up"), kill_after=1.5)
    message = gave_up.get("error", "")
    stopped = gave_up["finished"] and "died" in message and "--workers 1" in message
    ok &= stopped
    print(f"   stops, saying a worker died and what to try: {stopped}")

    print("\nCancel during a carve")
    canceled = carve(image, os.path.join(root, "canceled"), cancel_after=1.5, deadline=30)
    # A canceled run returns its summary marked canceled, keeping what it
    # had stored, rather than raising.
    was_canceled = canceled.get("canceled") or (canceled.get("summary") or {}).get("canceled")
    quick = canceled["finished"] and was_canceled \
        and canceled["ended"] - canceled["cancel_at"] < 15
    ok &= bool(quick)
    print(f"   stops within seconds: {bool(quick)}")
    time.sleep(1)
    left = multiprocessing.active_children()
    ok &= not left
    print(f"   and leaves no worker running: {not left}")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
