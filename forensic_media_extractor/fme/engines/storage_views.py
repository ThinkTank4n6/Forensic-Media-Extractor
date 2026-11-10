"""
storage_views.py -- one photograph is not three photographs.

Android exposes the same storage through several paths, and a full file
system extraction records every one of them. The picture a person took sits
at all of these, and they are the same bytes:

    /storage/emulated/0/DCIM/Camera/IMG_0001.jpg
    /data/media/0/DCIM/Camera/IMG_0001.jpg
    /mnt/user/0/emulated/0/DCIM/Camera/IMG_0001.jpg
    /sdcard/DCIM/Camera/IMG_0001.jpg

An app's own data is exposed the same way, through ``data/data/<package>``,
``data/user/<user>/<package>`` and the credential-encrypted mirror at
``data_mirror/data_ce/<volume>/<user>/<package>``.

Registering each spelling makes one photograph three or four files, three
or four "exact copies", and a count in a report that is three or four times
the truth. An examiner then explains in evidence why a device held 10,908
pictures when it held 4,318.

Two rules this is careful about, because getting them wrong is worse than
not doing it at all:

**Credential-encrypted and device-encrypted storage are different places.**
``data_ce`` and ``data_de`` hold different files, and are never folded
together.

**The Android user id is part of the identity.** A second user's data, or a
work profile's, is never folded into user 0's. Two people's photographs are
not one person's photographs.

And a group is only collapsed where its members **agree**: the same size,
and the same hash where one is known. A file being written while the
extraction ran can differ between views, and a group that disagrees is kept
in full rather than silently reduced to whichever copy came first.

The spelling kept is the first in the table below, so it is the same on
every extraction; the others are recorded against it, because an examiner
asked where a file was should be able to say all of them.

The view tables follow those in ALEAPP (`abrignoni/ALEAPP`,
`scripts/artifacts/storagePathViews.py`), which were measured against real
extractions.
"""

from __future__ import annotations

import re

__version__ = "1.0.0"

# Shared storage: the same files, four spellings. The first is kept.
SHARED_VIEWS = (
    re.compile(r"^/storage/emulated/(\d+)/(.*)$"),
    re.compile(r"^/data/media/(\d+)/(.*)$"),
    re.compile(r"^/mnt/user/(\d+)/emulated/(\d+)/(.*)$"),
    re.compile(r"^/sdcard/(.*)$"),            # the primary user's, by alias
)

# An app's own data. The encryption kind is part of the key: ce and de hold
# different files and must never collapse together.
APP_VIEWS = (
    (re.compile(r"^/data/data/(.*)$"), "ce", "0"),
    (re.compile(r"^/data/user/(\d+)/(.*)$"), "ce", None),
    (re.compile(r"^/data/user_de/(\d+)/(.*)$"), "de", None),
    (re.compile(r"^/data_mirror/data_ce/[^/]+/(\d+)/(.*)$"), "ce", None),
    (re.compile(r"^/data_mirror/data_de/[^/]+/(\d+)/(.*)$"), "de", None),
    (re.compile(r"^/data_mirror/cur_profile/(\d+)/(.*)$"), "ce", None),
)


def identity(device_path: str):
    """What this path is, regardless of which view it was seen through.

    Returns a key that is the same for every spelling of one file, or None
    where the path is not one of the mirrored views -- in which case it is
    its own identity and nothing is collapsed.
    """
    path = "/" + (device_path or "").replace("\\", "/").strip("/")

    for index, pattern in enumerate(SHARED_VIEWS):
        found = pattern.match(path)
        if not found:
            continue
        groups = found.groups()
        if index == 3:                       # /sdcard is user 0 by definition
            user, rest = "0", groups[0]
        elif index == 2:                     # /mnt/user/<a>/emulated/<b>/...
            user, rest = groups[1], groups[2]
        else:
            user, rest = groups[0], groups[1]
        return ("shared", user, rest)

    for pattern, encryption, fixed_user in APP_VIEWS:
        found = pattern.match(path)
        if not found:
            continue
        groups = found.groups()
        if fixed_user is not None:
            user, rest = fixed_user, groups[0]
        else:
            user, rest = groups[0], groups[1]
        return ("app", encryption, user, rest)

    return None


def preferred(paths) -> str:
    """Which spelling to keep: the first view in the table, consistently."""
    order = ("/storage/emulated/", "/data/media/", "/mnt/user/", "/sdcard/",
             "/data/data/", "/data/user/", "/data/user_de/",
             "/data_mirror/")
    def rank(path):
        for position, prefix in enumerate(order):
            if path.startswith(prefix):
                return (position, path)
        return (len(order), path)
    return sorted(paths, key=rank)[0]


def collapse(files) -> dict:
    """Fold the views of one file into one file.

    `files` are dicts with at least `device_path`, and ideally `size` and a
    hash. Returns what to keep, what was folded away, and -- the part that
    matters -- which groups disagreed and were therefore left alone.
    """
    groups, loose = {}, []
    for entry in files:
        key = identity(entry.get("device_path", ""))
        if key is None:
            loose.append(entry)
            continue
        groups.setdefault(key, []).append(entry)

    kept, folded, disagreed = list(loose), 0, []
    for key, members in groups.items():
        if len(members) == 1:
            kept.append(members[0])
            continue

        # Only collapse where the members agree. A file written while the
        # extraction ran can differ between views, and quietly keeping one
        # of two different files is a worse error than reporting both.
        sizes = {entry.get("size") for entry in members if entry.get("size")
                 is not None}
        digests = {entry.get("sha256") or entry.get("md5") or ""
                   for entry in members}
        digests.discard("")
        if len(sizes) > 1 or len(digests) > 1:
            disagreed.append({
                "paths": [entry.get("device_path", "") for entry in members],
                "sizes": sorted(sizes),
                "hashes": sorted(digests),
                "why": ("these views of the same path do not hold the same "
                        "bytes, so they are kept separately"),
            })
            kept.extend(members)
            continue

        paths = [entry.get("device_path", "") for entry in members]
        best = preferred(paths)
        chosen = next(entry for entry in members
                      if entry.get("device_path") == best)
        chosen = dict(chosen)
        chosen["device_path"] = best
        chosen["also_seen_at"] = [p for p in sorted(paths) if p != best]
        kept.append(chosen)
        folded += len(members) - 1

    return {"files": kept, "folded_away": folded,
            "disagreed": disagreed,
            "note": ("Android exposes the same storage through several "
                     "paths. Views of one file are reported once, with the "
                     "other paths recorded against it; views that do not "
                     "hold the same bytes are kept separately.")}
