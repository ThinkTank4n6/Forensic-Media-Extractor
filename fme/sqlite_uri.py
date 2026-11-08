"""A read-only SQLite URI for a path, with the path escaped.

Twenty places opened a database read-only by pasting the path straight
after ``file:``. In a URI ``?`` starts the parameters, ``#`` the fragment
and ``%`` an escape, so a folder named "Case #12" or "50% done" opened the
wrong file or failed with an error that named neither (an outside review,
v2026.11.312).
"""
from __future__ import annotations

import os
import urllib.parse


def ro_uri(path, immutable: bool = False) -> str:
    """``file:`` URI opening *path* read-only, safe for any file name.

    Windows paths become ``file:///C:/...`` and UNC paths
    ``file:////server/share/...``, the forms SQLite documents.
    """
    full = os.path.abspath(os.fspath(path))
    if os.sep == "\\":
        full = full.replace("\\", "/")
    if not full.startswith("/"):
        full = "/" + full                      # C:/x -> /C:/x
    query = "mode=ro" + ("&immutable=1" if immutable else "")
    return "file://" + urllib.parse.quote(full, safe="/:") + "?" + query
