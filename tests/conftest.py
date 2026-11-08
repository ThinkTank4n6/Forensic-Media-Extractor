"""pytest collects each test script as one test, run in its own interpreter.

The logic is in tests/runner.py, which also runs the suite without pytest;
this file only presents it to pytest. pyproject.toml sets python_files to a
pattern nothing matches, so pytest's own collector never imports a script
(most of them call sys.exit at import), and this hook collects them instead.

    python -m pytest tests                 everything
    python -m pytest tests -k carve_judge  one script
    python -m pytest tests -rw             with what each one did not check
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner  # noqa: E402


class NotChecked(UserWarning):
    """A script ran, but said part of what it covers was not checked here."""


class ScriptFailed(Exception):
    def __init__(self, result):
        super().__init__(result.name)
        self.result = result


def pytest_sessionstart(session):
    runner.ensure_fixtures()


def pytest_collect_file(parent, file_path):
    if (file_path.parent == runner.TESTS and file_path.suffix == ".py"
            and file_path.name.startswith("test_")):
        return ScriptFile.from_parent(parent, path=file_path)
    return None


def pytest_collection_modifyitems(session, config, items):
    """The timing-sensitive scripts last, as the plain runner runs them."""
    items.sort(key=lambda item: item.name in runner.TIMING_SENSITIVE)


class ScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name=self.path.stem)


class ScriptItem(pytest.Item):
    def runtest(self):
        result = runner.run(self.path)
        for line in result.not_checked:
            warnings.warn(NotChecked(f"{self.name}: {line}"))
        if not result.passed:
            raise ScriptFailed(result)

    def repr_failure(self, excinfo, style=None):
        if isinstance(excinfo.value, ScriptFailed):
            result = excinfo.value.result
            why = ("timed out" if result.timed_out
                   else f"exit code {result.returncode}"
                   + (f", printed {runner.FAIL_MARK!r}"
                      if runner.FAIL_MARK in result.output else ""))
            return (f"{self.name} failed ({why}, {result.seconds:.1f} s). "
                    f"Last lines of its output:\n\n{result.tail()}\n\n"
                    f"Run it alone: python tests/{self.name}.py")
        return super().repr_failure(excinfo, style=style)

    def reportinfo(self):
        return self.path, 0, f"script {self.name}"

