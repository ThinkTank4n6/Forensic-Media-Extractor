"""
accel.py -- the image models on the graphics cards, through ONNX Runtime.

Describing a case for "search by what is in a picture" ran the DINOv2 model
through OpenCV, one picture at a time, on the processor: about 150 to 200 ms
a picture, so a case of 200,000 pictures was ten hours before its first
search could answer. The model takes one picture per run (its batch size is
fixed at 1), so the gain is in running several at once, on every card the
machine has (v2026.11.332).

Where it runs, in the order tried:

  CUDA      onnxruntime-gpu, with NVIDIA's CUDA and cuDNN libraries present.
            Two sessions per card.
  DirectML  onnxruntime-directml on Windows: any DirectX 12 card, NVIDIA
            included, with nothing else to install. Two sessions per card,
            each run one at a time (DirectML does not take two runs on one
            session at once).
  CPU       onnxruntime on the processor: a session per eight cores, each
            given eight threads.
  OpenCV    no ONNX Runtime at all: the OpenCV path, as before.

ONNX Runtime falls back to the processor without saying so when a card's
libraries are missing, so a session is used as a card's only after asking it
which provider it got. What was used is reported (status(), plan_for()), and
FME_ACCEL=opencv|cpu|dml|cuda forces one, for measuring (tools: fme bench).

A description is the same model's numbers whichever way it ran: measured on
the bundled DINOv2, ONNX Runtime and OpenCV agree to 0.00017 in the raw
output and the descriptions' cosine is 1.0. Descriptions kept with a case
stay comparable.
"""

from __future__ import annotations

import logging
import os
import queue
import threading

LOG = logging.getLogger("fme")

__version__ = "1.0.0"

ENV = "FME_ACCEL"
SESSIONS_PER_CARD = 2
MAX_CARDS = 8

_lock = threading.Lock()
_plans: dict = {}


def runtime():
    """onnxruntime, or None."""
    try:
        import onnxruntime  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - absent, or will not import
        return None
    preload = getattr(onnxruntime, "preload_dlls", None)
    if preload is not None:
        # 1.21 and later: CUDA and cuDNN from NVIDIA's pip packages, when
        # those are what the machine has. Harmless when they are not.
        try:
            preload()
        except Exception:  # noqa: BLE001
            pass
    return onnxruntime


def status() -> dict:
    """What could run the models here, without loading one."""
    ort = runtime()
    if ort is None:
        return {"runtime": "", "providers": [], "choice": os.environ.get(ENV, "auto"),
                "why": "ONNX Runtime is not installed: the models run through "
                       "OpenCV, one picture at a time, on the processor"}
    providers = list(ort.get_available_providers())
    return {"runtime": f"onnxruntime {ort.__version__}", "providers": providers,
            "choice": os.environ.get(ENV, "auto"),
            "why": "" if any(p in providers for p in ("CUDAExecutionProvider",
                                                      "DmlExecutionProvider"))
            else "this ONNX Runtime has no graphics-card provider: install "
                 "onnxruntime-directml (Windows) or onnxruntime-gpu"}


class _Engine:
    """One session, and how its runs are taken one at a time where that is
    required."""

    def __init__(self, session, label: str, serial: bool):
        self.session, self.label = session, label
        self.input = session.get_inputs()[0].name
        self.lock = threading.Lock() if serial else None

    def run(self, tensor):
        if self.lock is None:
            return self.session.run(None, {self.input: tensor})[0]
        with self.lock:
            return self.session.run(None, {self.input: tensor})[0]


def _cards(ort, model_path: str, provider: str) -> list:
    """Sessions on each card this provider can open, two per card. A card
    is counted only if the session says it is running there."""
    engines = []
    for device in range(MAX_CARDS):
        options = ort.SessionOptions()
        if provider == "DmlExecutionProvider":
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        try:
            first = ort.InferenceSession(
                model_path, options,
                providers=[(provider, {"device_id": device}), "CPUExecutionProvider"])
        except Exception:  # noqa: BLE001 - no card at this index
            break
        if first.get_providers()[0] != provider:
            break       # it fell back to the processor: no card here after all
        short = "CUDA" if provider.startswith("CUDA") else "DirectML"
        engines.append(_Engine(first, f"{short} card {device}", serial=short == "DirectML"))
        for _ in range(SESSIONS_PER_CARD - 1):
            try:
                more = ort.InferenceSession(
                    model_path, options,
                    providers=[(provider, {"device_id": device}), "CPUExecutionProvider"])
                engines.append(_Engine(more, f"{short} card {device}",
                                       serial=short == "DirectML"))
            except Exception:  # noqa: BLE001
                break
    return engines


def _sample(session):
    """An input of the model's own shape, for timing a session."""
    import numpy as np

    shape = [d if isinstance(d, int) and d > 0 else 1
             for d in session.get_inputs()[0].shape]
    return np.zeros(shape, np.float32)


def _seconds(engine, tensor) -> float:
    import time

    engine.run(tensor)                      # the first run sets the card up
    best = float("inf")
    for _ in range(2):
        started = time.perf_counter()
        engine.run(tensor)
        best = min(best, time.perf_counter() - started)
    return best


# A card more than this many times slower than the fastest is not used.
SLOWER_LIMIT = 4.0


def _keep_real_cards(ort, model_path: str, engines: list) -> list:
    """Each card timed on one picture, and the ones that are not worth it let
    go. DirectML lists every DirectX adapter, and Windows lists its software
    renderer ("Microsoft Basic Render Driver") among them -- counted as a
    card, it was given pictures at a software renderer's speed. Nothing in
    ONNX Runtime names the adapter, so it is told apart by speed: slower than
    the processor, or far slower than the fastest card, and it goes."""
    if not engines:
        return engines
    tensor = _sample(engines[0].session)
    by_card = {}
    for engine in engines:
        by_card.setdefault(engine.label, []).append(engine)
    timed = {}
    for label, group in by_card.items():
        try:
            timed[label] = _seconds(group[0], tensor)
        except Exception:  # noqa: BLE001 - a card that will not run the model
            timed[label] = float("inf")
    try:
        cpu = _seconds(_Engine(ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]), "cpu", True), tensor)
    except Exception:  # noqa: BLE001
        cpu = float("inf")
    fastest = min(timed.values())
    keep = {label for label, seconds in timed.items()
            if seconds < cpu and seconds <= fastest * SLOWER_LIMIT}
    for engine in engines:
        engine.seconds = timed.get(engine.label)
    return [e for e in engines if e.label in keep]


def _processor(ort, model_path: str) -> list:
    cores = os.cpu_count() or 4
    count = max(1, cores // 8)
    threads = max(1, cores // count)
    engines = []
    for _ in range(count):
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        session = ort.InferenceSession(model_path, options,
                                       providers=["CPUExecutionProvider"])
        engines.append(_Engine(session, "processor (ONNX Runtime)", serial=True))
    return engines


def plan_for(model_path: str) -> list:
    """The engines that run this model here, made once per model file and
    choice. [] means the OpenCV path."""
    from fme.engines.content_search import _file_key
    choice = (os.environ.get(ENV) or "auto").strip().lower()
    key = (_file_key(model_path), choice)
    with _lock:
        if key in _plans:
            return _plans[key]
    ort = runtime()
    engines = []
    if ort is not None and choice != "opencv":
        available = set(ort.get_available_providers())
        order = {"cuda": ["CUDAExecutionProvider"],
                 "dml": ["DmlExecutionProvider"],
                 "cpu": []}.get(choice, ["CUDAExecutionProvider", "DmlExecutionProvider"])
        for provider in order:
            if provider in available:
                engines = _keep_real_cards(ort, model_path,
                                           _cards(ort, model_path, provider))
                if engines:
                    break
        if not engines and choice in ("auto", "cpu"):
            try:
                engines = _processor(ort, model_path)
            except Exception:  # noqa: BLE001 - a model ONNX Runtime will not load
                engines = []
    with _lock:
        _plans[key] = engines
    return engines


def describe_label(engines: list) -> str:
    if not engines:
        return "OpenCV on the processor, one picture at a time"
    labels = sorted({e.label for e in engines})
    return f"{len(engines)} session(s): " + ", ".join(labels)


def run_many(items, prepare, finish, engines: list, workers: int = 0,
             progress=None) -> dict:
    """prepare(item) -> tensor or None, run on the engines, finish(item,
    output) -> result; {item: result or None}.

    Preparing (reading and shrinking each picture) runs on a pool of
    threads -- OpenCV releases the interpreter while it decodes -- and feeds
    one thread per engine. The queue between them is bounded, so a fast
    reader cannot hold the whole case in memory waiting for a slow card.
    """
    items = list(items)
    results = {}
    if not items:
        return results
    workers = workers or max(2, min(16, (os.cpu_count() or 4) // 2))
    work = queue.Queue(maxsize=max(8, 4 * len(engines)))
    lock = threading.Lock()
    done = [0]
    feeding = list(items)
    feed_lock = threading.Lock()

    def reader():
        while True:
            with feed_lock:
                if not feeding:
                    return
                item = feeding.pop()
            try:
                tensor = prepare(item)
            except Exception:  # noqa: BLE001 - one picture that will not read
                tensor = None
            if tensor is None:
                with lock:
                    results[item] = None
                    done[0] += 1
                continue
            work.put((item, tensor))

    said = set()

    def runner(engine):
        while True:
            got = work.get()
            if got is None:
                return
            item, tensor = got
            try:
                value = finish(item, engine.run(tensor))
            except Exception as exc:  # noqa: BLE001 - a picture the model will not take
                value = None
                # Said once per session: a card lost part way (a driver
                # reset) answered every picture it took with nothing, and
                # nothing said why the case's search came back short.
                if engine.label not in said:
                    said.add(engine.label)
                    LOG.warning("the image model failed on %s (%s: %s); the "
                                "pictures it was given are not described",
                                engine.label, type(exc).__name__, exc)
            with lock:
                results[item] = value
                done[0] += 1
                count = done[0]
            if progress and count % 50 == 0:
                progress(count, len(items))

    readers = [threading.Thread(target=reader, daemon=True, name=f"prepare-{n}")
               for n in range(workers)]
    runners = [threading.Thread(target=runner, args=(e,), daemon=True, name=f"model-{n}")
               for n, e in enumerate(engines)]
    for thread in readers + runners:
        thread.start()
    for thread in readers:
        thread.join()
    for _ in runners:
        work.put(None)
    for thread in runners:
        thread.join()
    if progress:
        progress(len(items), len(items))
    return results
