"""
exif_read.py -- capture time, camera and GPS, without any dependency.

A recovered photograph carries more than its bytes. The camera that took it,
when it was taken, and where, are often the point of the exhibit -- and they
survive carving, because they sit inside the file rather than in the file
system that was lost.

This reads them directly:

  JPEG / TIFF and the raw formats built on TIFF (CR2, NEF, DNG, ARW ...)
        EXIF in APP1, walked as TIFF image file directories.
  HEIC / AVIF
        the EXIF item inside the ISO-BMFF container.
  MP4 / MOV / 3GP
        the creation time in the movie header, and Apple's and Android's
        location atoms, which is where a phone's video records where it was
        shot.

No Pillow, no exifread, no piexif: the formats are read as bytes. That
matters because this runs over every recovered file, including thousands of
truncated carves, and it must never raise on malformed input -- anything it
cannot make sense of comes back as nothing at all.
"""

from __future__ import annotations

import datetime as _dt
import re
import os
import struct
from typing import Optional

__version__ = "1.0.0"

# The handful of tags worth carrying on every file. Reading only these keeps
# the pass cheap over a large recovery.
TIFF_TAGS = {
    0x010F: "camera_make",
    0x0110: "camera_model",
    0x013B: "artist",          # who the camera was told took the picture
    0x8298: "copyright",
    0x0112: "orientation",
    0x0132: "datetime_file",
    0x8769: "_exif_ifd",
    0x8825: "_gps_ifd",
}
EXIF_TAGS = {
    0x9003: "datetime_original",
    0x9004: "datetime_digitized",
    # EXIF 2.31: the zone each of those was recorded in, as "+HH:MM". Most
    # phones write them; without them a capture time has no zone at all.
    0x9010: "offset_time",
    0x9011: "offset_time_original",
    0x9012: "offset_time_digitized",
    0xA002: "pixel_width",
    0xA003: "pixel_height",
    0xA430: "camera_owner",
    0xA431: "body_serial",
    0x829A: "exposure_time",
    0x920A: "focal_length",
}
GPS_TAGS = {
    0x0001: "lat_ref", 0x0002: "lat",
    0x0003: "lon_ref", 0x0004: "lon",
    0x0005: "alt_ref", 0x0006: "alt",
    0x0007: "gps_time", 0x001D: "gps_date",
}

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 13: 4}     # 13: IFD, an offset like LONG


def _clean(value) -> str:
    if isinstance(value, bytes):
        value = value.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return str(value).strip()


def _read_values(data: bytes, offset: int, fmt: str, type_id: int,
                 count: int, endian: str):
    size = TYPE_SIZES.get(type_id, 0) * count
    if size <= 0 or offset < 0 or offset + size > len(data):
        return None
    raw = data[offset:offset + size]
    if type_id in (2, 7):                       # ASCII, undefined
        return raw
    if type_id in (5, 10):                      # rational
        out = []
        for i in range(count):
            num, den = struct.unpack(endian + "2" + ("L" if type_id == 5 else "l"),
                                     raw[i * 8:(i + 1) * 8])
            out.append(num / den if den else 0.0)
        return out
    try:
        return list(struct.unpack(f"{endian}{count}{fmt}", raw))
    except struct.error:
        return None


def _walk_ifd(data: bytes, offset: int, endian: str, wanted: dict) -> dict:
    """Read one image file directory, keeping only the tags asked for."""
    out = {}
    if offset < 0 or offset + 2 > len(data):
        return out
    count = struct.unpack(endian + "H", data[offset:offset + 2])[0]
    if count > 512:                             # malformed or not an IFD
        return out
    for i in range(count):
        entry = offset + 2 + i * 12
        if entry + 12 > len(data):
            break
        tag, type_id, length = struct.unpack(endian + "HHL", data[entry:entry + 8])
        if tag not in wanted:
            continue
        fmt = {1: "B", 3: "H", 4: "L", 6: "b", 8: "h", 9: "l",
               11: "f", 12: "d"}.get(type_id, "B")
        size = TYPE_SIZES.get(type_id, 0) * length
        if size <= 4:
            values = _read_values(data, entry + 8, fmt, type_id, length, endian)
        else:
            pointer = struct.unpack(endian + "L", data[entry + 8:entry + 12])[0]
            values = _read_values(data, pointer, fmt, type_id, length, endian)
        if values is None:
            continue
        out[wanted[tag]] = values
    return out


def _dms_to_degrees(values, ref, limit: float = 180.0) -> Optional[float]:
    """Degrees, minutes, seconds to decimal degrees, within +-`limit`
    (90 for a latitude: 120 degrees north is a corrupt tag, not a place)."""
    if not values or len(values) < 3:
        return None
    try:
        degrees = values[0] + values[1] / 60.0 + values[2] / 3600.0
    except (TypeError, ZeroDivisionError):
        return None
    if _clean(ref).upper() in ("S", "W"):
        degrees = -degrees
    if not -limit <= degrees <= limit:
        return None
    return round(degrees, 7)


def _exif_datetime(value) -> str:
    """EXIF writes 'YYYY:MM:DD HH:MM:SS'; give back ISO 8601."""
    text = _clean(value)
    if len(text) < 19:
        return ""
    try:
        parsed = _dt.datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return ""
    if parsed.year < 1970 or parsed.year > 2100:
        return ""
    return parsed.isoformat()


_OFFSET = re.compile(r"^([+-])(\d\d):?(\d\d)$")


def _with_offset(stamp: str, offset) -> str:
    """A capture time with the zone the camera recorded for it, if it did.

    Without an offset the time stays as the camera's clock said it, with no
    zone: that is all the evidence says, and anything added would be a guess.
    """
    text = _clean(offset) if offset else ""
    found = _OFFSET.match(text.strip())
    if not stamp or not found:
        return stamp
    sign, hours, minutes = found.groups()
    if int(hours) > 14 or int(minutes) > 59:
        return stamp
    return f"{stamp}{sign}{hours}:{minutes}"


def read_tiff_block(data: bytes) -> dict:
    """Parse a TIFF header and the IFDs that matter. Used for both a bare
    TIFF and the TIFF block inside a JPEG's APP1 segment."""
    if len(data) < 8:
        return {}
    if data[:2] == b"II":
        endian = "<"
    elif data[:2] == b"MM":
        endian = ">"
    else:
        return {}
    try:
        magic, first = struct.unpack(endian + "HL", data[2:8])
    except struct.error:
        return {}
    if magic != 42:
        return {}

    found = _walk_ifd(data, first, endian, TIFF_TAGS)
    result = {}
    for key in ("camera_make", "camera_model", "artist", "copyright"):
        if key in found:
            value = _clean(found[key])
            if value:
                result[key] = value
    # Tag 0x0132 (DateTime) is when the file was last changed -- usually by
    # whatever edited or re-saved it -- not when the picture was taken. It
    # was reported as the capture time whenever the original-time tags were
    # absent, which is exactly the case of an edited picture with them
    # stripped. It is not a capture time and is not given as one.

    if "_exif_ifd" in found and found["_exif_ifd"]:
        sub = _walk_ifd(data, int(found["_exif_ifd"][0]), endian, EXIF_TAGS)
        for key, offset_key in (("datetime_original", "offset_time_original"),
                                ("datetime_digitized", "offset_time_digitized")):
            stamp = _exif_datetime(sub.get(key, b""))
            if stamp:
                result["captured"] = _with_offset(
                    stamp, sub.get(offset_key) or sub.get("offset_time"))
                break
        for key in ("pixel_width", "pixel_height"):
            if key in sub and sub[key]:
                result[key] = int(sub[key][0])
        for key in ("camera_owner", "body_serial"):
            if key in sub:
                value = _clean(sub[key])
                if value:
                    result[key] = value
        # A camera writes the owner in one of two places depending on make;
        # either is "whose camera was this".
        if "camera_owner" in result and "artist" not in result:
            result["artist"] = result["camera_owner"]

    if "_gps_ifd" in found and found["_gps_ifd"]:
        gps = _walk_ifd(data, int(found["_gps_ifd"][0]), endian, GPS_TAGS)
        lat = _dms_to_degrees(gps.get("lat"), gps.get("lat_ref", b""), 90.0)
        lon = _dms_to_degrees(gps.get("lon"), gps.get("lon_ref", b""))
        if lat is not None and lon is not None and not (lat == 0 and lon == 0):
            result["latitude"] = lat
            result["longitude"] = lon
            if gps.get("alt"):
                altitude = gps["alt"][0]
                if gps.get("alt_ref") and gps["alt_ref"][0] == 1:
                    altitude = -altitude
                result["altitude"] = round(altitude, 2)
    return {k: v for k, v in result.items() if v not in ("", None)}


# IPTC datasets worth having. A camera does not write these; a person or a
# cataloguing tool does, which is exactly why they are worth reading -- a
# place name in a file came from somebody who knew it.
IPTC_FIELDS = {
    0x0190: "city",          # 2:90
    0x005A: "sublocation",   # 2:92 is 0x5C; 0x5A is 2:90 in some writers
    0x005C: "sublocation",
    0x005F: "state",         # 2:95
    0x0065: "country",       # 2:101
    0x0050: "byline",        # 2:80, the credited author
    0x0369: "caption",
}


def read_iptc(data: bytes) -> dict:
    """IPTC IIM records out of a Photoshop APP13 block.

    The structure is a chain of 8BIM resources; the IPTC data sits in
    resource 0x0404 as records of 0x1C, dataset number, length, value.
    """
    result = {}
    marker = data.find(b"8BIM")
    if marker < 0:
        return result
    pos = 0
    end = len(data)
    while pos + 12 < end:
        pos = data.find(b"8BIM", pos)
        if pos < 0:
            break
        try:
            resource = struct.unpack(">H", data[pos + 4:pos + 6])[0]
            name_len = data[pos + 6]
            # the name is padded to an even length, including its length byte
            after_name = pos + 6 + 1 + name_len + ((name_len + 1) % 2)
            size = struct.unpack(">I", data[after_name:after_name + 4])[0]
            body = data[after_name + 4:after_name + 4 + size]
        except (struct.error, IndexError):
            break
        if resource == 0x0404:
            i = 0
            while i + 5 <= len(body):
                if body[i] != 0x1C:
                    i += 1
                    continue
                dataset = struct.unpack(">H", body[i + 1:i + 3])[0]
                length = struct.unpack(">H", body[i + 3:i + 5])[0]
                value = body[i + 5:i + 5 + length]
                field = IPTC_FIELDS.get(dataset)
                if field and value:
                    text = _clean(value)
                    if text:
                        result.setdefault(field, text)
                i += 5 + length
        pos = after_name + 4 + size + (size % 2)
    return result


def read_xmp(data: bytes) -> dict:
    """The handful of XMP properties worth the same trouble as IPTC."""
    import re
    text = data.decode("utf-8", "replace")
    result = {}
    for prop, field in (("photoshop:City", "city"),
                        ("photoshop:State", "state"),
                        ("photoshop:Country", "country"),
                        ("dc:creator", "artist"),
                        ("tiff:Artist", "artist"),
                        ("aux:SerialNumber", "body_serial")):
        match = re.search(rf'{prop}="([^"]{{1,120}})"', text)
        if not match:
            match = re.search(rf"<{prop}>([^<]{{1,120}})</{prop}>", text)
        if not match:
            # dc:creator is usually an rdf list rather than a bare value
            match = re.search(rf"<{prop}>.*?<rdf:li[^>]*>([^<]{{1,120}})"
                              rf"</rdf:li>", text, re.S)
        if match:
            value = match.group(1).strip()
            if value:
                result.setdefault(field, value)
    return result


def read_jpeg(data: bytes) -> dict:
    """Find APP1/Exif in a JPEG and read the TIFF block inside it."""
    if data[:2] != b"\xff\xd8":
        return {}
    found = {}
    pos = 2
    limit = min(len(data), 2 * 1024 * 1024)      # EXIF lives near the start
    while pos + 4 <= limit:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:                        # image data begins
            break
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if length < 2:
            break
        segment = data[pos + 4:pos + 2 + length]
        if marker == 0xE1 and segment[:6] == b"Exif\x00\x00":
            found.update({k: v for k, v in read_tiff_block(segment[6:]).items()
                          if k not in found})
        elif marker == 0xE1 and b"<x:xmpmeta" in segment[:200]:
            found.update({k: v for k, v in read_xmp(segment).items()
                          if k not in found})
        elif marker == 0xED:                      # Photoshop APP13: IPTC
            found.update({k: v for k, v in read_iptc(segment).items()
                          if k not in found})
        pos += 2 + length
    return found


# Makes whose cameras write their own clock into mvhd, where the format says
# UTC. GoPro is the documented one (ExifTool's QuickTimeUTC notes; GoPro's
# own metadata format keeps a separate GPS time for that reason). Read as
# UTC, a GoPro clip shot at 14:00 in Philadelphia was dated 10:00 EDT.
# Matched on the make or on a track handler GoPro names after itself.
_LOCAL_CLOCK_MAKES = ("gopro",)

MVHD_UTC_CAUTION = ("QuickTime mvhd creation time: UTC as recorded by the "
                    "container; some devices (dash and action cameras among "
                    "them) write local time there, so it is the file's own "
                    "claim, not an established instant")


def _iso_from_mp4_seconds(seconds: int) -> str:
    """MP4 counts seconds from 1904-01-01 UTC."""
    if not 0 < seconds < 4_000_000_000:
        return ""
    try:
        # UTC, so said: stripped of its zone, it could not be told from a
        # camera's local clock time.
        base = _dt.datetime(1904, 1, 1, tzinfo=_dt.timezone.utc)
        return (base + _dt.timedelta(seconds=seconds)).isoformat()
    except (OverflowError, ValueError):
        return ""


def _parse_iso6709(text: str) -> dict:
    """Apple and Android write GPS as ISO 6709: +38.8977-077.0365+018.0/"""
    import re
    match = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)"
                     r"(?:([+-]\d+(?:\.\d+)?))?", text.strip())
    if not match:
        return {}
    try:
        lat = float(match.group(1))
        lon = float(match.group(2))
    except (TypeError, ValueError):
        return {}
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return {}
    out = {"latitude": round(lat, 7), "longitude": round(lon, 7)}
    if match.group(3):
        try:
            out["altitude"] = round(float(match.group(3)), 2)
        except ValueError:
            pass
    return out


def read_isobmff(data: bytes, depth: int = 0, limit: int = 8) -> dict:
    """Walk MP4/MOV/HEIC boxes for creation time and location."""
    result = {}
    pos = 0
    end = len(data)
    while pos + 8 <= end and depth < limit:
        try:
            size = struct.unpack(">I", data[pos:pos + 4])[0]
        except struct.error:
            break
        btype = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            break
        body = data[pos + header:pos + size]

        if btype == b"mvhd" and len(body) >= 20:
            version = body[0]
            if version == 1 and len(body) >= 28:
                created = struct.unpack(">Q", body[4:12])[0]
            else:
                created = struct.unpack(">I", body[4:8])[0]
            stamp = _iso_from_mp4_seconds(created)
            if stamp:
                result.setdefault("captured", stamp)
                result.setdefault("_mvhd", stamp)
        elif btype == b"hdlr" and len(body) > 24:
            # version/flags, pre_defined, handler type, 12 reserved, name
            if b"GoPro" in body[24:]:
                result.setdefault("_local_clock", "GoPro")
        elif btype in (b"\xa9xyz", b"loci") and body:
            # Apple stores ISO 6709 with a two-byte length and language code.
            text = body[4:].decode("utf-8", "replace") if len(body) > 4 else ""
            located = _parse_iso6709(text)
            if not located:
                located = _parse_iso6709(body.decode("utf-8", "replace"))
            result.update(located)
        elif btype in (b"\xa9mak", b"\xa9mod") and len(body) > 4:
            key = "camera_make" if btype == b"\xa9mak" else "camera_model"
            result.setdefault(key, _clean(body[4:]))
        elif btype == b"Exif" and len(body) > 4:
            result.update(read_tiff_block(body[4:]))
        elif btype in (b"moov", b"udta", b"meta", b"trak", b"mdia", b"ilst",
                       b"keys", b"minf", b"stbl"):
            inner = body[4:] if btype == b"meta" else body
            nested = read_isobmff(inner, depth + 1, limit)
            for key, value in nested.items():
                result.setdefault(key, value)
        pos += size
    return _mvhd_basis(result) if depth == 0 else result


def _mvhd_basis(result: dict) -> dict:
    """Say what a capture time taken from mvhd is, and keep a GoPro's as the
    zone-less reading it is.

    The format defines mvhd as UTC, phones write it so, and since
    v2026.11.281 it is stored with its zone (CHANGELOG): a time with no zone
    could not be told from a camera clock's. But plenty of cameras write
    their local clock there, and nothing in the file says which did. So the
    time keeps its UTC label -- it is what the container records -- and
    "captured_basis" says it is the container's claim; and where the camera
    is one known to write local time, the time is given with no zone, which
    timeutil keeps as a reading ("device clock; zone not recorded") rather
    than converting it to a moment it was not.
    """
    stamp = result.pop("_mvhd", None)
    device = result.pop("_local_clock", None)
    if not stamp or result.get("captured") != stamp:
        return result                  # the capture time came from elsewhere
    make = str(result.get("camera_make") or "").strip().lower()
    if device is None and make.startswith(_LOCAL_CLOCK_MAKES):
        device = result["camera_make"]
    if device:
        result["captured"] = stamp[:-len("+00:00")] if stamp.endswith("+00:00") else stamp
        result["captured_basis"] = (
            f"QuickTime mvhd creation time; {device} cameras write their own "
            f"clock there rather than UTC, so it is kept as recorded, with no "
            f"zone")
    else:
        result["captured_basis"] = MVHD_UTC_CAUTION
    return result


def read_bytes(data: bytes, ext: str = "") -> dict:
    """Metadata from a buffer already in memory."""
    if not data or len(data) < 16:
        return {}
    try:
        if data[:2] == b"\xff\xd8":
            return read_jpeg(data)
        if data[:2] in (b"II", b"MM"):
            return read_tiff_block(data)
        if len(data) > 8 and data[4:8] in (b"ftyp", b"moov", b"mdat", b"wide"):
            return read_isobmff(data)
    except (struct.error, ValueError, IndexError, OverflowError,
            MemoryError, RecursionError):
        return {}
    return {}


def moov_box(fh, size: int, limit: int = 64 * 1024 * 1024) -> bytes:
    """The whole moov box of an ISO-BMFF file, found by walking the top-level
    boxes with seeks, or b"" when there is none (or it is over `limit`).

    A large video read by its two ends -- or by its first 64 MB -- found no
    moov when the mdat ahead of it was bigger than that: a 73 MB phone video
    laid out ftyp, mdat, moov lost its capture time and location, and
    nothing said the reading was incomplete. Walking the box headers costs a
    few reads whatever the size.
    """
    at, steps = 0, 0
    while at + 8 <= size and steps < 10_000:
        steps += 1
        fh.seek(at)
        head = fh.read(16)
        if len(head) < 8:
            return b""
        length = struct.unpack(">I", head[:4])[0]
        kind = head[4:8]
        if length == 1:
            if len(head) < 16:
                return b""
            length = struct.unpack(">Q", head[8:16])[0]
        elif length == 0:
            length = size - at
        if length < 8 or at + length > size:
            return b""
        if kind == b"moov":
            if length > limit:
                return b""
            fh.seek(at)
            return fh.read(length)
        at += length
    return b""


def read_file(path: str, max_bytes: int = 32 * 1024 * 1024) -> dict:
    """Metadata from a file on disk.

    Only the head of a large video is read: the movie header and the location
    atoms sit in moov, which is at one end or the other, so both ends are
    sampled rather than loading a gigabyte to find 24 bytes.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size <= max_bytes:
                return read_bytes(fh.read(), path.rsplit(".", 1)[-1].lower())
            head = fh.read(max_bytes // 2)
            result = read_bytes(head)
            if result.get("latitude") is not None and result.get("captured"):
                return result
            if len(head) > 8 and head[4:8] in (b"ftyp", b"moov", b"mdat",
                                                b"wide", b"free", b"skip"):
                moov = moov_box(fh, size)
                if moov:
                    for key, value in read_isobmff(moov).items():
                        result.setdefault(key, value)
                    return result
            fh.seek(max(0, size - max_bytes // 2))
            tail = fh.read()
            # moov at the end is common; parse it on its own terms.
            for key, value in read_isobmff(tail).items():
                result.setdefault(key, value)
            return result
    except (OSError, MemoryError):
        return {}


def has_location(record: dict) -> bool:
    return (record.get("latitude") is not None
            and record.get("longitude") is not None)
