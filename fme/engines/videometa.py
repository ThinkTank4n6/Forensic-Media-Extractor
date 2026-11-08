"""
videometa.py -- where a video was taken.

A photograph carries its location in EXIF, which this program has always
read. A video does not: phones and cameras write it into the QuickTime
structure instead, as ISO 6709 text, in one of two places depending on who
made the device.

    moov/udta/(c)xyz      Android and most cameras: a 16-bit length, a
                          16-bit language code, then the text.
    moov/meta             Apple: a key table naming
                          ``com.apple.quicktime.location.ISO6709``, and a
                          value table holding the text against that key's
                          index.

Until now a video in this program had no location at all, so a case where
the pictures were placed and the videos were not looked like a case where
the videos were taken somewhere unrecorded. They were not: nobody read it.

ISO 6709 is ``+DD.DDDD+DDD.DDDD/`` or the same with an altitude between
them, signs mandatory. The sign is the hemisphere, so dropping it moves a
point to the opposite side of the planet -- which is why this parses the
format rather than pulling numbers out with a regular expression.
"""

from __future__ import annotations

import re
import struct

__version__ = "1.0.0"

MAX_SCAN = 64 * 1024 * 1024        # how far into a file to look for moov

# +53.4808-002.2426/ , +53.4808-002.2426+038.000/ and the same to more
# places. Every field carries its sign; that is the hemisphere.
ISO6709 = re.compile(
    r"^(?P<lat>[+-]\d{2,6}(?:\.\d+)?)"
    r"(?P<lon>[+-]\d{3,7}(?:\.\d+)?)"
    r"(?P<alt>[+-]\d+(?:\.\d+)?)?/?$")

APPLE_KEY = b"com.apple.quicktime.location.ISO6709"


def _atoms(data: bytes, start: int = 0, end: int = None, depth: int = 0):
    """Walk QuickTime atoms, yielding (type, body start, body end)."""
    end = len(data) if end is None else end
    at = start
    while at + 8 <= end and depth < 10:
        size = struct.unpack(">I", data[at:at + 4])[0]
        kind = data[at + 4:at + 8]
        header = 8
        if size == 1 and at + 16 <= end:
            size = struct.unpack(">Q", data[at + 8:at + 16])[0]
            header = 16
        elif size == 0:
            size = end - at
        if size < header or at + size > end:
            return
        yield kind, at + header, at + size
        at += size


def _find(data: bytes, path: tuple, start: int = 0, end: int = None):
    """Follow a path of atom types, e.g. (b"moov", b"udta")."""
    if not path:
        yield start, end if end is not None else len(data)
        return
    for kind, body_start, body_end in _atoms(data, start, end):
        if kind == path[0]:
            yield from _find(data, path[1:], body_start, body_end)


def _degrees(text: str):
    """ISO 6709 text to decimal degrees, or None if it is not that."""
    found = ISO6709.match(text.strip().rstrip("/") + "/")
    if not found:
        return None

    def one(raw: str, degree_digits: int):
        sign = -1.0 if raw[0] == "-" else 1.0
        body = raw[1:]
        whole, _, fraction = body.partition(".")
        # Degrees, or degrees and minutes, or degrees minutes seconds --
        # the width says which, and guessing wrong moves the point.
        if len(whole) <= degree_digits:
            value = float(body)
        elif len(whole) == degree_digits + 2:
            value = (float(whole[:degree_digits])
                     + float(whole[degree_digits:] + "." + (fraction or "0")) / 60.0)
        elif len(whole) == degree_digits + 4:
            value = (float(whole[:degree_digits])
                     + float(whole[degree_digits:degree_digits + 2]) / 60.0
                     + float(whole[degree_digits + 2:] + "." + (fraction or "0"))
                     / 3600.0)
        else:
            return None
        return sign * value

    latitude = one(found.group("lat"), 2)
    longitude = one(found.group("lon"), 3)
    if latitude is None or longitude is None:
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    altitude = None
    if found.group("alt"):
        try:
            altitude = float(found.group("alt"))
        except ValueError:
            altitude = None
    return {"latitude": round(latitude, 6), "longitude": round(longitude, 6),
            "altitude": altitude, "iso6709": text.strip()}


def read(path: str) -> dict:
    """Where this video says it was taken, or an empty result."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_SCAN)
            found = read_bytes(data)
            if found.get("found") or len(data) < MAX_SCAN:
                return found
            # The moov of a video longer than MAX_SCAN can sit after the
            # mdat, past what was read: found by walking the box headers.
            from fme.engines.exif_read import moov_box

            fh.seek(0, 2)
            moov = moov_box(fh, fh.tell())
    except OSError as exc:
        return {"found": False, "error": str(exc)}
    return read_bytes(moov) if moov else found


def read_bytes(data: bytes) -> dict:
    if len(data) < 16 or data[4:8] not in (b"ftyp", b"moov", b"free",
                                           b"mdat", b"skip", b"wide"):
        return {"found": False}

    # Android and most cameras: moov/udta/(c)xyz
    for start, end in _find(data, (b"moov", b"udta")):
        for kind, body_start, body_end in _atoms(data, start, end):
            if kind not in (b"\xa9xyz", b"xyz "):
                continue
            body = data[body_start:body_end]
            if len(body) < 4:
                continue
            length = struct.unpack(">H", body[:2])[0]
            text = body[4:4 + length].decode("utf-8", "replace")
            place = _degrees(text)
            if place:
                return {"found": True, "where": "moov/udta/\\u00a9xyz", **place}

    # Apple: moov/meta, a key table and a value table
    for start, end in _find(data, (b"moov", b"meta")):
        index = _apple_key_index(data, start, end)
        if index is None:
            continue
        place = _apple_value(data, start, end, index)
        if place:
            return {"found": True, "where": "moov/meta (Apple)", **place}

    return {"found": False}


def _apple_key_index(data: bytes, start: int, end: int):
    """Which numbered key is the location, in Apple's key table."""
    for kind, body_start, body_end in _atoms(data, start, end):
        if kind != b"keys":
            continue
        at = body_start + 8                 # version/flags, then the count
        number = 0
        while at + 8 <= body_end:
            size = struct.unpack(">I", data[at:at + 4])[0]
            if size < 8 or at + size > body_end:
                break
            number += 1
            if APPLE_KEY in data[at + 8:at + size]:
                return number
            at += size
    return None


def _apple_value(data: bytes, start: int, end: int, index: int):
    """The text stored against that key number."""
    for kind, body_start, body_end in _atoms(data, start, end):
        if kind != b"ilst":
            continue
        at = body_start
        while at + 8 <= body_end:
            size = struct.unpack(">I", data[at:at + 4])[0]
            if size < 8 or at + size > body_end:
                break
            key_number = struct.unpack(">I", data[at + 4:at + 8])[0]
            if key_number == index:
                for inner, inner_start, inner_end in _atoms(
                        data, at + 8, at + size):
                    if inner != b"data":
                        continue
                    text = data[inner_start + 8:inner_end].decode(
                        "utf-8", "replace")
                    place = _degrees(text)
                    if place:
                        return place
            at += size
    return None
