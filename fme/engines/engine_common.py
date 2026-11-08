"""
engine_common.py -- Shared by every part of the engine: constants, the optional third-party
modules, the progress-note and cancel helpers.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import logging


try:
    from fme.engines import db_extract
except ImportError:  # pragma: no cover
    db_extract = None

try:
    from fme.engines import fat32_core
except ImportError:  # pragma: no cover - standalone use outside the package
    fat32_core = None

try:
    import pytsk3
except ImportError:  # pragma: no cover
    pytsk3 = None

try:
    import pyewf
except ImportError:  # pragma: no cover
    pyewf = None

try:
    import pyvmdk
except ImportError:  # pragma: no cover
    pyvmdk = None

try:
    import pyvhdi
except ImportError:  # pragma: no cover
    pyvhdi = None

try:
    import pyqcow
except ImportError:  # pragma: no cover
    pyqcow = None


LOG = logging.getLogger("fme")

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB

# How deep a folder walk goes, for every reader. TSK's walk stopped at 64
# and the FAT fallback at 32, both without a word, so files 33 to 64 levels
# down were named by one reader and not the other and the cross-check
# between them showed a difference that came from the limits. One limit
# now, and a folder past it is counted (fs_dirs_too_deep) and logged.
WALK_DEPTH_LIMIT = 64


def _too_deep(stats, path: str) -> None:
    """Count, and say once per run, a folder the walk did not go into."""
    if stats is None:
        return
    stats["fs_dirs_too_deep"] = stats.get("fs_dirs_too_deep", 0) + 1
    if stats["fs_dirs_too_deep"] == 1:
        LOG.warning("Folders more than %d levels deep were not walked "
                    "(first: %s); a carve still reaches their files' "
                    "content, without names.", WALK_DEPTH_LIMIT, path)

# Caps applied per category when a parser cannot determine a real length.
MAX_IMAGE_DEFAULT = 128 * MIB
MAX_VIDEO_DEFAULT = 4 * GIB


# ==========================================================================
# SECTION 6 -- Carving engine
# ==========================================================================
class Canceled(Exception):
    """Raised when a caller's cancel_cb asks for the run to stop."""


def _check_cancel(cancel_cb) -> None:
    if cancel_cb is not None and cancel_cb():
        raise Canceled()
