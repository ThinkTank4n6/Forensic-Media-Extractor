"""
settings.py -- what this machine should remember between sessions.

Small and deliberately dull: where the hash store is, which certificate to
trust, the examiner's name. Typing those again every morning is not
security, it is friction -- and friction is what makes somebody keep the
token in a text file on the desktop, which is worse than anything this was
protecting against.

So the address and the certificate path are remembered without being asked:
neither is a secret, and both are wrong to have to retype.

**The token is different, and is only remembered if asked.** It is the
holder's identity on a store full of hashes of child abuse material, and
anyone who reads it can act as them. Remembering it writes it to a file
readable by that Windows account, which is a real reduction in security and
a fair trade for some units and not for others. The choice is the
examiner's, it is off by default, and the file says plainly what it holds.

Kept per Windows account, beside the program's other per-user data, not in
the program folder -- a program folder is shared, replaced on update, and
sometimes on a share.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from typing import Optional

__version__ = "1.0.0"

FILENAME = "settings.json"

REMEMBERED = ("store_path", "store_cert", "examiner", "agency", "case_number",
              "output_root", "timezone", "content_model",
              "agency_logo", "face_detector", "face_recognizer",
              "c2pa_trust_list", "stash_path",
              # The grading scheme new cases are created under
              # (fme/review/regions.py); a case keeps its own for life.
              "category_region",
              # The offline basemap the locator maps are drawn on
              # (fme/engines/basemaps.py).
              "basemap")
SECRET = "store_token"


def folder() -> str:
    """Where this machine keeps per-user settings."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ForensicMediaExtractor")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "forensic-media-extractor")


def path() -> str:
    return os.path.join(folder(), FILENAME)


def load() -> dict:
    """What was remembered, or an empty set of settings."""
    try:
        with open(path(), "r", encoding="utf-8") as fh:
            found = json.load(fh)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


# One writer at a time in this process. Two requests -- the settings page
# and a logo upload, say -- each loaded the file, changed their key and wrote
# it back, and the second write erased the first one's change.
_LOCK = threading.Lock()


def _write(current: dict) -> None:
    """Write the settings beside their name and rename them into place.

    Written in place before: a crash or a full disk part way left a file
    load() could not read, which it answers as {} -- and the next save then
    wrote back only the one key it was given, losing every other setting. The
    scratch file is created readable by this account only, so a token in it
    is never on disk at the default permissions, as it was until the chmod
    that followed the write.
    """
    os.makedirs(folder(), exist_ok=True)
    target = path()
    scratch = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
    handle = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(scratch, target)
    except BaseException:
        try:
            os.remove(scratch)
        except OSError:
            pass
        raise


def save(values: dict, remember_token: Optional[bool] = None) -> dict:
    """Remember these. The token only when asked, and never by accident.

    `remember_token` True keeps the token (and stores one given); False
    forgets it; None, the default, leaves whatever is stored as it is and
    stores no token given. The default was False, so any save that did not
    think about the token -- importing a basemap -- deleted the one the
    examiner had asked to keep (v2026.11.319); and callers that passed
    "whatever is there now" read it outside the lock, racing a token save.
    """
    with _LOCK:
        return _save(values, remember_token)


def _save(values: dict, remember_token: Optional[bool]) -> dict:
    current = load()
    for key in REMEMBERED:
        if key in values:
            current[key] = values[key] or ""

    if remember_token is None:
        pass                    # the stored token, if any, as it was
    elif remember_token:
        # Only replace the token when one was actually given. Saving
        # something else -- the store path, say -- must not wipe it, which
        # is what passing "no token" as an empty string did: the setting
        # survived a restart as a blank, and the examiner was asked for it
        # again having ticked the box that says they would not be.
        if SECRET in values:
            current[SECRET] = values.get(SECRET) or ""
        current.setdefault(SECRET, "")
        if not current.get(SECRET):
            current.pop(SECRET, None)
        current["token_warning"] = (
            "This file holds a hash store token. Anyone who can read it can "
            "use the store as you. Delete this file if that is not what you "
            "intended.")
    else:
        current.pop(SECRET, None)
        current.pop("token_warning", None)

    # Readable by this account only (on Windows the user profile's own
    # permissions already do this).
    _write(current)
    return current


def forget() -> bool:
    """Remove everything this machine remembered."""
    try:
        os.remove(path())
        return True
    except OSError:
        return False


def forget_token() -> dict:
    """Keep the address and certificate; drop the token."""
    with _LOCK:
        current = load()
        current.pop(SECRET, None)
        current.pop("token_warning", None)
        if current:
            _write(current)
        else:
            forget()
    return current
