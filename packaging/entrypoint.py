"""Where a frozen build starts.

Kept outside the package so the freezer runs it as a plain script while
``fme`` stays importable as a package.

One thing it must do that a source checkout does not: a frozen one-file
build unpacks itself to a temporary folder, so the program has to look for
its own documents and vendored readers there rather than beside the
executable. Everything that reads a shipped file goes through
``fme.resources``, which knows both cases.
"""

import multiprocessing
import os
import sys


def main() -> int:
    # Windows re-runs the executable for each worker process, so without
    # this a frozen build starting a pool opens the program again instead.
    multiprocessing.freeze_support()

    if getattr(sys, "frozen", False):
        os.environ.setdefault("FME_FROZEN_ROOT", getattr(sys, "_MEIPASS", ""))

    # fme.cli.run() reads sys.argv itself, exactly as `python -m fme` does.
    # This used to call fme.cli.main(sys.argv[1:]), and main() takes no
    # arguments: every frozen build raised TypeError before drawing anything.
    # Nothing ran this file -- CI runs launch.py, which does not come through
    # here -- so tests/test_packaging.py now runs it as a script.
    from fme.cli import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
