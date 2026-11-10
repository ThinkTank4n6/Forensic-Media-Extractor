"""
isolated.py -- run one risky read in a short-lived child process.

Some readers are known to fail in ways a try/except cannot catch. The
vendored ESE reader spins forever on a truncated Windows.edb (a short read at
end of file is retried with no exit). The GPU-texture decoder is a Rust
extension that can take the whole process down on malformed block data. In
the GUI either one, run in-process, ends the examiner's session: a hang
freezes a request thread for good, a segfault closes the program.

So these run here: in a child that is killed at a deadline, whose crash costs
one file, and whose answer comes back on stdout.

Two ways to start the child, because there are two kinds of build:

* From source, ``python -c`` with a bootstrap that puts this checkout's
  root first on sys.path. Not ``python -m``: that looks in the current
  directory first, so a run started anywhere but the project root could not
  import ``fme`` -- or imported a different ``fme`` that happened to be there.
  (GLEAPP found this the hard way: ``gleapp/workers.py``.)
* From a frozen build there is no interpreter to call, so the executable
  runs itself with the hidden first argument ``_worker``, which
  ``fme.cli.main`` hands straight to ``dispatch`` below. ``video_frames``
  gave up isolation in frozen builds for want of this; ESE cannot.

Only the workers named in ``WORKERS`` can be started this way. The hidden
argument is not a general "import and run anything" door into the program.
"""

from __future__ import annotations

import importlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Optional

__version__ = "1.0.0"

# name -> "module:function". The function takes the worker's own argument
# list, writes its answer to stdout and returns an exit code.
WORKERS = {
    "edb": "fme.engines.winsearch:edb_worker",
    "texture": "fme.engines.textures:texture_worker",
    "frames": "fme.engines.video_frames:frames_worker",
    "probe": "fme.engines.video_frames:probe_worker",
}

# The directory holding the ``fme`` package: the checkout root from source,
# site-packages for an installed copy.
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

_BOOTSTRAP = (
    "import sys; root = sys.argv[1]; "
    "sys.path[:1] == [''] and sys.path.pop(0); "
    "root in sys.path or sys.path.insert(0, root); "
    "from fme.engines import isolated; "
    "sys.exit(isolated.dispatch(sys.argv[2], sys.argv[3:]))"
)


def command(name: str, args: list) -> list:
    """The command line that runs worker ``name`` with ``args``."""
    if name not in WORKERS:
        raise ValueError(f"unknown worker {name!r}")
    if getattr(sys, "frozen", False):
        return [sys.executable, "_worker", name] + [str(a) for a in args]
    return ([sys.executable, "-c", _BOOTSTRAP, _PACKAGE_PARENT, name]
            + [str(a) for a in args])


def dispatch(name: str, args: list) -> int:
    """Run one worker in this process. Called only inside the child."""
    target = WORKERS.get(name)
    if target is None:
        print(f"unknown worker {name!r}", file=sys.stderr)
        return 2
    module_name, function_name = target.split(":")
    function = getattr(importlib.import_module(module_name), function_name)
    return int(function(list(args)) or 0)


class Outcome:
    """What came back from a child: one of ok, timeout, crashed, failed."""

    __slots__ = ("status", "stdout", "detail")

    def __init__(self, status: str, stdout: bytes = b"", detail: str = ""):
        self.status = status
        self.stdout = stdout
        self.detail = detail

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Outcome({self.status!r}, {len(self.stdout)} bytes, {self.detail!r})"


def _below_normal(cmd: list) -> tuple:
    """The command and Popen options that start it at lower priority.

    Decoding for a filmstrip or a report is background work; an examiner
    grading is the foreground. Through nice(1) rather than preexec_fn, which
    Python documents as unsafe in a program with threads -- the child can
    deadlock before it runs -- and the GUI is all threads.
    """
    if os.name == "nt":
        return cmd, {"creationflags": 0x00004000}    # BELOW_NORMAL_PRIORITY_CLASS
    nice = shutil.which("nice")
    return ([nice, "-n", "10"] + cmd if nice else cmd), {}


def run(name: str, args: list, timeout: float,
        command_line: Optional[list] = None,
        below_normal: bool = False) -> Outcome:
    """Run worker ``name`` and say how it ended. Never raises.

    ``command_line`` replaces the built command; the tests use it to check
    that a child which crashes or hangs is reported as such rather than
    taking this process with it.
    """
    cmd = command_line if command_line is not None else command(name, args)
    extra = {}
    if below_normal:
        cmd, extra = _below_normal(cmd)
    try:
        # subprocess.run kills the child itself when the deadline passes, so
        # a hung reader does not outlive the request that started it.
        done = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              check=False, cwd=_PACKAGE_PARENT, **extra)
    except subprocess.TimeoutExpired:
        return Outcome("timeout", detail=f"stopped after {timeout:g} seconds")
    except (OSError, ValueError) as exc:
        return Outcome("failed", detail=f"could not start: {exc}")
    if done.returncode < 0:
        return Outcome("crashed", done.stdout,
                       f"the reader crashed (signal {-done.returncode})")
    # Windows reports a crash as an NTSTATUS exit code, not a signal:
    # 0xC0000005 is an access violation. Reading it as an ordinary failure
    # would tell the examiner the file was bad when the decoder was.
    if done.returncode >= 0xC0000000:
        return Outcome("crashed", done.stdout,
                       f"the reader crashed (0x{done.returncode:08X})")
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        return Outcome("failed", done.stdout,
                       f"exit code {done.returncode}"
                       + (f": {tail[-1]}" if tail else ""))
    return Outcome("ok", done.stdout)


# What starts each answer line a batch worker prints. Anything else on its
# stdout -- a library printing a warning, a decoder's chatter -- was read as
# the answer for the current item, and every later answer then belonged to
# the item before it: the duration of one video given for the next.
ANSWER_MARK = "FME-ANSWER "


def answer(result) -> None:
    """Print one batch answer, marked, and flush it."""
    sys.stdout.write(ANSWER_MARK + json.dumps(result) + "\n")
    sys.stdout.flush()


def run_batch(name: str, items: list, per_item_timeout: float,
              below_normal: bool = False,
              command_line: Optional[list] = None, progress=None) -> list:
    """Run worker `name` over many items; one answer per item, in order.

    Each answer is `(status, result)`: ("ok", what the worker printed for
    that item), or ("crashed" | "timeout" | "failed", None). One child
    handles the items in turn, printing one JSON line per item as it
    finishes it. If the child dies or goes quiet for `per_item_timeout`
    seconds, the item it was on is recorded as such and a fresh child
    carries on from the next -- so one bad file costs one answer, and a
    thousand files cost a handful of process start-ups rather than a
    thousand.

    The items go to the child in a file. Writing them to its stdin while it
    writes answers to a stdout this process has not started reading fills
    both pipes and deadlocks them.

    `command_line` replaces the built command (tests); the list file's path
    is appended to it.
    """
    answers: list = [None] * len(items)
    start = 0
    while start < len(items):
        handle, list_path = tempfile.mkstemp(prefix="fme-batch-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(items[start:], fh)
        cmd = (list(command_line) + [list_path] if command_line is not None
               else command(name, [list_path]))
        extra = {}
        if below_normal:
            cmd, extra = _below_normal(cmd)
        try:
            child = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL,
                                     cwd=_PACKAGE_PARENT, **extra)
        except (OSError, ValueError):
            os.remove(list_path)
            for index in range(start, len(items)):
                answers[index] = ("failed", None)
            break
        lines: queue.Queue = queue.Queue()

        def pump(stream=child.stdout, out=lines):
            for raw in stream:
                out.put(raw)
            out.put(None)

        threading.Thread(target=pump, daemon=True).start()
        at = start
        try:
            while at < len(items):
                try:
                    raw = lines.get(timeout=per_item_timeout)
                except queue.Empty:
                    answers[at] = ("timeout", None)
                    break
                if raw is None:                    # the child is gone
                    child.wait()
                    answers[at] = ("crashed" if child.returncode else "failed", None)
                    break
                text = raw.decode("utf-8", "replace") if isinstance(
                    raw, bytes) else raw
                if not text.startswith(ANSWER_MARK):
                    continue                       # not an answer: chatter
                try:
                    answers[at] = ("ok", json.loads(text[len(ANSWER_MARK):]))
                except ValueError:
                    answers[at] = ("failed", None)
                at += 1
                if progress:
                    progress(at, len(items))
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            try:
                os.remove(list_path)
            except OSError:
                pass
        start = at + 1 if at < len(items) else at
    return answers
