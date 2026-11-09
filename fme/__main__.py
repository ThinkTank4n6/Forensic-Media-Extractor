"""Entry point: `python -m fme`, and what the executable is built from.

The multiprocessing guard has to run before anything else touches
multiprocessing. Parallel carving starts workers with the "spawn" method, and
a frozen executable creates each one by re-running itself; without the guard
every worker restarts the whole program instead of running the worker
function.
"""

# Allow this file to be run directly -- "python fme/engines/media_core.py" --
# as well as imported. Run as a script there is no package context, so the
# project root goes on the path before the package imports below.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import multiprocessing
import os
import sys

from fme.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:      # piped into head, more or less
        os._exit(0)
