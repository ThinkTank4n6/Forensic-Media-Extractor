"""
resources.py -- finding the files that ship with this program.

From a source checkout they sit beside the code. From a frozen one-file
build they sit in a temporary folder the executable unpacks itself into,
which is somewhere different every time it starts. Code that opens a
shipped file by walking up from ``__file__`` works in the first case and
fails in the second -- and it fails late, at the moment somebody asks for
the manual or reaches a QNX image, not at startup where it would be
noticed.

So everything that reads a shipped file asks here.
"""

from __future__ import annotations

import os
import sys

__version__ = "1.0.0"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def root() -> str:
    """Where this program's own files are, however it was started."""
    if frozen():
        unpacked = getattr(sys, "_MEIPASS", "") or os.environ.get(
            "FME_FROZEN_ROOT", "")
        if unpacked:
            return unpacked
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find(*relative: str) -> str:
    """A shipped file, or an empty string when it is not there.

    Several places are tried because a source checkout, a one-folder build
    and a one-file build lay the same files out differently, and a program
    that works from one and not the others is a program nobody can ship.
    """
    base = root()
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (base, os.path.join(base, "fme"), here,
                   os.path.dirname(here),
                   os.path.dirname(os.path.dirname(here))):
        candidate = os.path.join(folder, *relative)
        if os.path.exists(candidate):
            return candidate
    return ""


def vendor_dir() -> str:
    """Where the vendored readers are."""
    for candidate in (find("fme", "vendor"), find("vendor"),
                      os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "vendor")):
        if candidate and os.path.isdir(candidate):
            return candidate
    return ""


def document(name: str) -> str:
    """A shipped document: the manual, the setup guide, the comparison."""
    return find(name) or find("docs", name)
