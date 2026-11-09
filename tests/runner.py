"""Run the test scripts: one interpreter each, judged by what they report.

Every file named tests/test_*.py is a script. Most run their checks at
module level and end with sys.exit; the ones that spawn carve workers run
under a __main__ guard and end with os._exit. Importing them into one
process -- the way pytest collects ordinary test modules -- would end that
process at the first sys.exit, share logging.disable() and sys.path edits
between scripts, and re-import a script inside every worker it spawns. So
each one runs exactly as an examiner would run it, `python tests/<name>.py`
from the project root, in a fresh interpreter.

A script passes when it exits 0 and does not print FAILURES ABOVE. Lines in
which it says something was not checked (a library missing, a fixture
absent) are collected and reported beside the verdict, so a green run says
what it did not cover rather than hiding it.

Two ways in, one logic:

    python -m pytest tests          (tests/conftest.py wraps this module)
    python tests/runner.py          (no pytest needed: the lab machines)

    python tests/runner.py -k carve --strict

Environment:
    FME_TEST_TIMEOUT    seconds one script may take (default 900)
    FME_TESTS_STRICT=1  a test that cannot run for want of a library fails
                        instead of passing; CI sets it
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PROJECT = TESTS.parent

FAIL_MARK = "FAILURES ABOVE"
# How the scripts say a check did not happen. Matched per line.
NOT_CHECKED = re.compile(
    r"not checked|is not installed|not installed here|; skipped\)|\(skipped|"
    r"skipped\)|needs .* to build", re.IGNORECASE)
# Lines that contain those words but report a result, not a gap.
REPORTS_A_RESULT = re.compile(r":\s*(True|False)\s*$|oversized skipped")

# These measure how long a stuck or dying worker takes to be noticed, so
# they are run after the rest, when nothing else is competing for the CPU.
# Under load test_carve_worker_stall missed its window (seen on the
# baseline run of v2026.11.273, five scripts at once) and passed alone.
TIMING_SENSITIVE = ("test_carve_worker_stall", "test_carve_worker_death")


@dataclass
class Result:
    name: str
    passed: bool
    returncode: int
    seconds: float
    output: str
    not_checked: list = field(default_factory=list)
    timed_out: bool = False

    def tail(self, lines: int = 60) -> str:
        return "\n".join(self.output.splitlines()[-lines:])


def scripts(pattern: str = "") -> list:
    """Every test script, the timing-sensitive ones last."""
    found = sorted(p for p in TESTS.glob("test_*.py") if p.is_file())
    if pattern:
        found = [p for p in found if pattern in p.stem]
    return ([p for p in found if p.stem not in TIMING_SENSITIVE]
            + [p for p in found if p.stem in TIMING_SENSITIVE])


def ensure_fixtures() -> None:
    """The shared FAT32 fixture several scripts read, from the root and tests/.

    A fresh checkout may not carry it (it is generated, and 16 MB). It is
    built once, by the script that defines it, before anything needs it.
    """
    in_tests = TESTS / "media_test.img"
    at_root = PROJECT / "media_test.img"
    generator = TESTS / "make_media_test_image.py"
    stale = lambda image: (not image.is_file()
                           or image.stat().st_mtime < generator.stat().st_mtime)
    # Rebuilt when the script that defines it changed, not only when it is
    # missing: a fixture left over from the old generator would test the
    # old fixture.
    if stale(in_tests):
        subprocess.run([sys.executable, generator.name], cwd=TESTS,
                       check=True, capture_output=True)
    if stale(at_root) or at_root.stat().st_mtime < in_tests.stat().st_mtime:
        shutil.copyfile(in_tests, at_root)


def not_checked(output: str) -> list:
    return [line.strip() for line in output.splitlines()
            if NOT_CHECKED.search(line) and not REPORTS_A_RESULT.search(line)]


def run(path: Path, timeout: float = 0) -> Result:
    timeout = timeout or float(os.environ.get("FME_TEST_TIMEOUT", "900"))
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    try:
        done = subprocess.run([sys.executable, str(path)], cwd=PROJECT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", "replace")
        return Result(path.stem, False, -1, time.monotonic() - started,
                      output + f"\n[runner] timed out after {timeout:.0f} s",
                      not_checked(output), timed_out=True)
    output = done.stdout.decode("utf-8", "replace")
    passed = done.returncode == 0 and FAIL_MARK not in output
    return Result(path.stem, passed, done.returncode,
                  time.monotonic() - started, output, not_checked(output))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the FME test scripts.")
    parser.add_argument("-k", default="", help="only scripts whose name contains this")
    parser.add_argument("--strict", action="store_true",
                        help="a test that cannot run for want of a library fails")
    parser.add_argument("-v", action="store_true", help="print each script's output")
    args = parser.parse_args(argv)
    if args.strict:
        os.environ["FME_TESTS_STRICT"] = "1"

    ensure_fixtures()
    chosen = scripts(args.k)
    failed, gaps = [], []
    for index, path in enumerate(chosen, 1):
        result = run(path)
        verdict = "pass" if result.passed else ("TIMEOUT" if result.timed_out else "FAIL")
        print(f"[{index:3}/{len(chosen)}] {verdict:7} {result.seconds:6.1f}s  {result.name}",
              flush=True)
        if args.v or not result.passed:
            print("      " + result.tail().replace("\n", "\n      "))
        if not result.passed:
            failed.append(result.name)
        gaps += [(result.name, line) for line in result.not_checked]

    if gaps:
        print("\nnot checked on this machine:")
        for name, line in gaps:
            print(f"  {name}: {line}")
    print(f"\n{len(chosen) - len(failed)} passed, {len(failed)} failed"
          + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
