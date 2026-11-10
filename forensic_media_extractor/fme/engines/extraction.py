"""
extraction.py -- a mobile extraction, read as the device rather than as an
archive.

A phone does not arrive as a disk image. It arrives as one archive holding
the device's file system: a Cellebrite ``.ufdr`` or its zip, a GrayKey zip
or tar, a Magnet export, an ``.ab`` Android backup. Tens of gigabytes, tens
of thousands of members, and three things that make it different from an
ordinary zip:

**The path an examiner needs is not the path in the archive.** A picture
sits at ``files/Fs/1/Media/DCIM/100APPLE/IMG_0423.HEIC`` in the archive and
at ``/private/var/mobile/Media/DCIM/100APPLE/IMG_0423.HEIC`` on the phone.
A report that cites the first is citing the tool's packaging, not the
device, and an examiner asked where a file was in the witness box needs the
second. Both are kept here, separately, and the device path is the one
shown.

**A large minority of members carry no extension**, because iOS and Android
store plenty of media under names that are a UUID, a cache key or a number.
Those are classified by their first bytes, not their name, which is the
only thing that works.

**The archive is bigger than the machine.** Members are read as a stream
and only the ones worth keeping are written out, so a 60 GB extraction does
not need 60 GB of scratch space before anything can be looked at.

What this does *not* do is parse an extraction's own report files -- the
UFDR XML, a GrayKey manifest -- to learn what the vendor's tool concluded.
That is their analysis, and reading media out of the device's own file
system does not depend on it.
"""

from __future__ import annotations

import os
import re
import tarfile
import zipfile

__version__ = "1.0.0"

ARCHIVE_EXTENSIONS = {".zip", ".ufdr", ".tar", ".gz", ".tgz", ".ab"}

# How each tool packs the device's file system, and what to strip to get
# back to the path the phone used. Ordered: the first that matches wins.
LAYOUTS = (
    # Cellebrite UFDR / UFD: the device tree under files/.
    (re.compile(r"^files/[^/]+/\d+/(.*)$", re.I), "Cellebrite", "/{0}"),
    # Anything else under files/ is the device tree only where it starts at
    # a folder a phone has at its root. A report-style UFDR files members by
    # category -- files/Image/x.jpg, files/Video/... -- and those mapped to
    # "/Image/x.jpg", a path no phone ever had, cited as the device's.
    (re.compile(r"^files/((?:private|var|data|storage|sdcard|system|mnt|"
                r"vendor|product|cache|apex|Users|Library|Applications|"
                r"System|mobile|root)/.*)$", re.I), "Cellebrite", "/{0}"),
    (re.compile(r"^files/.*$", re.I), "Cellebrite", ""),
    # GrayKey: a filesystem folder, often with the volume beneath it.
    (re.compile(r"^(?:[^/]+/)?filesystem\d*/(.*)$", re.I), "GrayKey", "/{0}"),
    (re.compile(r"^(?:[^/]+/)?private/var/(.*)$", re.I), "iOS",
     "/private/var/{0}"),
    # Android: a data partition, or an adb backup's apps/ tree.
    (re.compile(r"^(?:[^/]+/)?data/data/(.*)$", re.I), "Android",
     "/data/data/{0}"),
    (re.compile(r"^(?:[^/]+/)?data/media/(.*)$", re.I), "Android",
     "/data/media/{0}"),
    (re.compile(r"^apps/([^/]+)/(?:f|r|d)/(.*)$", re.I), "Android backup",
     "/data/data/{0}/{1}"),
    (re.compile(r"^shared/(\d+)/(.*)$", re.I), "Android backup",
     "/storage/emulated/{0}/{1}"),
    # Magnet and others: a device folder at the root.
    (re.compile(r"^(?:image|device|extraction)/(.*)$", re.I), "extraction",
     "/{0}"),
)

SKIP_FOLDERS = re.compile(
    r"(^|/)(ufdreports?|reports?|__macosx|\.svn|thumbs\.db)(/|$)", re.I)


def looks_like_extraction(path: str) -> bool:
    extension = os.path.splitext(path)[1].lower()
    return extension in ARCHIVE_EXTENSIONS or path.lower().endswith(".tar.gz")


def device_path(member: str) -> tuple:
    """The path on the phone, and which packing it was recognized as.

    The path is "" where the member is not a path on the device -- nothing
    matches, or the packing is recognized but files the member by its own
    scheme rather than by the phone's tree -- so the archive path is the
    one shown. Prefixing the archive path with "/" invented a device path
    that could be quoted in a report as where the file was on the phone.
    """
    cleaned = member.replace("\\", "/")
    # Only a leading "./" (and a leading "/"): lstrip("./") took the dot off
    # a hidden folder too, so ".thumbnails/x.jpg" became "thumbnails/x.jpg".
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/")
    for pattern, tool, shape in LAYOUTS:
        found = pattern.match(cleaned)
        if found:
            return shape.format(*found.groups()), tool
    return "", ""


def _members(path: str):
    """Every member, as (archive path, size, opener), whatever the format."""
    lowered = path.lower()
    if lowered.endswith((".tar", ".tar.gz", ".tgz", ".gz")):
        try:
            archive = tarfile.open(path, "r:*")
        except (tarfile.TarError, OSError):
            return
        with archive:
            for member in archive:
                if not member.isfile():
                    continue
                yield (member.name, member.size,
                       lambda m=member, a=archive: a.extractfile(m))
        return

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return
    with archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            yield (member.filename, member.file_size,
                   lambda m=member, a=archive: a.open(m))


def survey(path: str, sample: int = 4000) -> dict:
    """What this archive is, before committing to reading all of it.

    Run first so an examiner is told what they have -- which tool packed
    it, how many members, how much of it is media -- rather than starting a
    long job blind.
    """
    tools, kinds = {}, {}
    members = 0
    total = 0
    nameless = 0
    for index, (name, size, _) in enumerate(_members(path)):
        if SKIP_FOLDERS.search(name):
            continue
        members += 1
        total += size or 0
        _, tool = device_path(name)
        if tool:
            tools[tool] = tools.get(tool, 0) + 1
        extension = os.path.splitext(name)[1].lower().lstrip(".")
        if not extension:
            nameless += 1
        else:
            kinds[extension] = kinds.get(extension, 0) + 1
        if index >= sample:
            break

    best = max(tools.items(), key=lambda pair: pair[1])[0] if tools else ""
    return {
        "path": os.path.abspath(path),
        "members_seen": members,
        "bytes_seen": total,
        "packed_by": best,
        "layouts": tools,
        "without_an_extension": nameless,
        "commonest": sorted(kinds.items(), key=lambda pair: -pair[1])[:12],
        "sampled": members >= sample,
    }


# walk() and _safe() were here: an extractor of their own that nothing in the
# program called (production reads packages through media_core and names
# their files with device_path). _safe split only on "/", so a member named
# "..\\..\\x.jpg" kept its backslashes -- folder separators on Windows --
# and could be written outside the output folder, over another file of the
# same name. Removed rather than fixed: dead code is not safe code.
