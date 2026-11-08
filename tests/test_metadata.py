"""EXIF, video metadata and KML, built from bytes so nothing is assumed.

Everything here is constructed by hand rather than shipped as a sample, so
the expected values are known exactly and the test cannot pass by accident.
"""
import tempfile
import datetime as dt
import io
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fme.engines import exif_read
from fme.exports import kml_export


def rational(num, den=1):
    return struct.pack(">2L", num, den)


def dms(deg, minute, sec_num, sec_den):
    return rational(deg) + rational(minute) + struct.pack(">2L", sec_num, sec_den)


def build_exif_jpeg():
    """A JPEG carrying camera, capture time and GPS: 38.8977 N, 77.0365 W."""
    header = b"MM\x00\x2a" + struct.pack(">L", 8)
    make, model = b"Canon\x00", b"Canon EOS 5D Mark IV\x00"
    stamp = b"2026:03:19 14:22:07\x00"

    exif_off = 8 + 2 + 4 * 12 + 4
    gps_off = exif_off + 2 + 2 * 12 + 4
    pos = gps_off + 2 + 4 * 12 + 4
    blobs = b""

    def add(blob):
        nonlocal pos, blobs
        offset = pos
        blobs += blob
        pos += len(blob)
        return offset

    make_off, model_off, stamp_off = add(make), add(model), add(stamp)
    lat_off = add(dms(38, 53, 5172, 100))
    lon_off = add(dms(77, 2, 1314, 100))

    ifd0 = struct.pack(">H", 4)
    ifd0 += struct.pack(">HHL", 0x010F, 2, len(make)) + struct.pack(">L", make_off)
    ifd0 += struct.pack(">HHL", 0x0110, 2, len(model)) + struct.pack(">L", model_off)
    ifd0 += struct.pack(">HHL", 0x8769, 4, 1) + struct.pack(">L", exif_off)
    ifd0 += struct.pack(">HHL", 0x8825, 4, 1) + struct.pack(">L", gps_off)
    ifd0 += struct.pack(">L", 0)

    exif = struct.pack(">H", 2)
    exif += struct.pack(">HHL", 0x9003, 2, len(stamp)) + struct.pack(">L", stamp_off)
    exif += struct.pack(">HHL", 0xA002, 4, 1) + struct.pack(">L", 6720)
    exif += struct.pack(">L", 0)

    gps = struct.pack(">H", 4)
    gps += struct.pack(">HHL", 0x0001, 2, 2) + b"N\x00\x00\x00"
    gps += struct.pack(">HHL", 0x0002, 5, 3) + struct.pack(">L", lat_off)
    gps += struct.pack(">HHL", 0x0003, 2, 2) + b"W\x00\x00\x00"
    gps += struct.pack(">HHL", 0x0004, 5, 3) + struct.pack(">L", lon_off)
    gps += struct.pack(">L", 0)

    app1 = b"Exif\x00\x00" + header + ifd0 + exif + gps + blobs
    return (b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xdb\x00\x43" + bytes(65)
            + b"\xff\xda\x00\x08" + bytes(6) + bytes(3000) + b"\xff\xd9")


def build_located_mp4():
    """An MP4 with a movie header and an Apple location atom."""
    def box(btype, payload):
        return struct.pack(">I", len(payload) + 8) + btype + payload

    created = int((dt.datetime(2026, 7, 4, 9, 30, 0)
                   - dt.datetime(1904, 1, 1)).total_seconds())
    mvhd = box(b"mvhd", bytes(4) + struct.pack(">I", created)
               + struct.pack(">I", created) + struct.pack(">I", 1000)
               + struct.pack(">I", 30000) + bytes(80))
    text = "+38.8977-077.0365+018.000/"
    xyz = box(b"\xa9xyz", struct.pack(">HH", len(text), 0x15C7) + text.encode())
    return (box(b"ftyp", b"mp42" + bytes(4) + b"mp42isom")
            + box(b"mdat", bytes(5000))
            + box(b"moov", mvhd + box(b"udta", xyz)))


ok = True
print("JPEG EXIF")
meta = exif_read.read_bytes(build_exif_jpeg())
expected = {"camera_make": "Canon", "camera_model": "Canon EOS 5D Mark IV",
            "captured": "2026-03-19T14:22:07", "latitude": 38.8977}
for key, want in expected.items():
    got = meta.get(key)
    good = got == want
    ok &= good
    print(f"  {key:16} {str(got):28} {'' if good else f'expected {want}'}")
longitude = meta.get("longitude")
good = longitude is not None and abs(longitude + 77.0369833) < 0.0001
ok &= good
print(f"  {'longitude':16} {str(longitude):28} {'' if good else 'expected ~-77.037'}")

print("\nMP4 metadata")
meta = exif_read.read_bytes(build_located_mp4())
# An mvhd time is UTC by the format's definition, and says so (v2026.11.281):
# stripped of its zone it could not be told from a camera's local clock time.
for key, want in (("captured", "2026-07-04T09:30:00+00:00"), ("latitude", 38.8977),
                  ("longitude", -77.0365), ("altitude", 18.0)):
    got = meta.get(key)
    good = got == want
    ok &= good
    print(f"  {key:16} {str(got):28} {'' if good else f'expected {want}'}")

print("\nMalformed input must never raise")
for label, blob in (("random", os.urandom(4096)),
                    ("truncated jpeg", build_exif_jpeg()[:40]),
                    ("truncated mp4", build_located_mp4()[:30]),
                    ("empty", b""), ("zeros", bytes(8192))):
    result = exif_read.read_bytes(blob)
    good = isinstance(result, dict)
    ok &= good
    print(f"  {label:16} {'returned ' + str(result or '{}'):28}")

print("\nKML")
root = os.path.join(tempfile.gettempdir(), "metadata_test")
shutil.rmtree(root, ignore_errors=True)
os.makedirs(f"{root}/images/jpg")
with open(f"{root}/images/jpg/photo.jpg", "wb") as fh:
    fh.write(build_exif_jpeg())
record = {"out_path": "images/jpg/photo.jpg", "category": "image", "ext": "jpg",
          "size": 3334, "md5": "0" * 32, "sha1": "0" * 40, "sha256": "0" * 64,
          "engine": "carve", "source_path": "", "signature": "jpeg",
          "offset": 4096, "deleted": False, "note": "", "instances": 1,
          "captured": "2026-03-19T14:22:07", "camera_make": "Canon",
          "camera_model": "Canon EOS 5D Mark IV",
          "latitude": 38.8977, "longitude": -77.0369833, "altitude": None}
json.dump({"summary": {"image": "test"}, "files": [record], "duplicates": []},
          open(f"{root}/manifest.json", "w"))

result = kml_export.export(f"{root}/manifest.json", f"{root}/locations.kmz",
                           embed=True)
import xml.dom.minidom
import zipfile
with zipfile.ZipFile(f"{root}/locations.kmz") as archive:
    names = archive.namelist()
    doc = xml.dom.minidom.parseString(archive.read("doc.kml"))
coords = doc.getElementsByTagName("coordinates")[0].firstChild.data
good = coords.startswith("-77.0369833,38.8977")
ok &= good
print(f"  placemarks       {result['placemarks']}")
print(f"  coordinates      {coords}  {'' if good else '<-- wrong'}")
print(f"  picture embedded {'files/photo.jpg' in names}")
ok &= "files/photo.jpg" in names

# Whether an image moves is a different thing to look at and to grade, and
# the extension does not say which: most GIFs on a disk are static.
print("\nAnimation")
try:
    from PIL import Image as _Image
    import io as _io
    from fme.engines import media_core

    frames = []
    for shade in (40, 120, 200):
        frame = _Image.new("P", (32, 32))
        frame.putpalette([0, 0, 0] + [shade, shade, shade] * 255)
        frame.paste(1, (0, 0, 16, 32))
        frames.append(frame)

    cases = []
    buffer = _io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
    cases.append(("animated.gif", buffer.getvalue(), "gif", True))

    buffer = _io.BytesIO()
    frames[0].save(buffer, "GIF")
    cases.append(("still.gif", buffer.getvalue(), "gif", False))

    buffer = _io.BytesIO()
    rgb = [f.convert("RGB") for f in frames]
    rgb[0].save(buffer, "PNG", save_all=True, append_images=rgb[1:],
                duration=100, loop=0)
    cases.append(("animated.png", buffer.getvalue(), "png", True))

    buffer = _io.BytesIO()
    rgb[0].save(buffer, "PNG")
    cases.append(("still.png", buffer.getvalue(), "png", False))

    for name, blob, ext, expected in cases:
        path = os.path.join(root, name)
        with open(path, "wb") as fh:
            fh.write(blob)
        got = media_core.is_animated(path, ext)
        good = got == expected
        ok &= good
        print(f"  {name:16} animated={str(got):5} "
              f"{'' if good else f'expected {expected}'}")
except ImportError:
    print("  Pillow is not installed, so these were not built")


# A Windows Search index comes off a seized machine: truncated, or
# written while the machine was pulled. Two things must not happen --
# reading an ESE database we do not understand, and hanging on a
# damaged SQLite one.
print("\nthe Windows Search index")
import tempfile as _temp
import time as _time

from fme.engines import winsearch as _ws

_where = _temp.mkdtemp()

_ese = os.path.join(_where, "Windows.edb")
with open(_ese, "wb") as _fh:
    _fh.write(b"\x00\x00\x00\x00" + b"\xef\xcd\xab\x89" + os.urandom(4096))
ok &= _ws.is_ese(_ese)
print(f"   an ESE database is recognised: {_ws.is_ese(_ese)}")

# From v2026.11.293 an ESE index is read rather than refused, in a child
# process with a deadline (the reader loops forever on a damaged file). What
# must hold for random bytes is that the read ends, promptly, with a reason
# -- never a hang and never a quiet "nothing matched".
with open(os.path.join(_where, "manifest.json"), "w", encoding="utf-8") as _fh:
    _fh.write('{"files": []}')
_limit, _ws.EDB_TIME_LIMIT = _ws.EDB_TIME_LIMIT, 8
_began = _time.monotonic()
_said = ""
try:
    _ws.resolve(_where, _ese)
except ValueError as _exc:
    _said = str(_exc)
_ws.EDB_TIME_LIMIT = _limit
_took = _time.monotonic() - _began
_ended = bool(_said) and _took < 30
ok &= _ended
print(f"   a damaged one ends in {_took:.0f} s with a reason: {_ended}"
      f"  ({_said[:70]}...)")

_tiny = os.path.join(_where, "Truncated.edb")
with open(_tiny, "wb") as _fh:
    _fh.write(b"\x00\x00\x00\x00" + b"\xef\xcd\xab\x89")
ok &= _ws.is_ese(_tiny)
print(f"   a truncated one too: {_ws.is_ese(_tiny)}")

# A damaged SQLite index must not run forever. A run that hangs with no
# message is the worst way for this to fail: an examiner waits, kills
# it, and cannot say what stalled.
_corrupt = os.path.join(_where, "Corrupt.db")
with open(_corrupt, "wb") as _fh:
    _fh.write(b"SQLite format 3\x00" + os.urandom(60000))
_began = _time.monotonic()
_ws.index_map(_corrupt)
_took = _time.monotonic() - _began
ok &= _took < 30
print(f"   a corrupt SQLite index returns in {_took:.2f}s, not never")
ok &= getattr(_ws, "INDEX_TIME_LIMIT", 0) > 0
print(f"   and the read carries a {_ws.INDEX_TIME_LIMIT:.0f}s limit "
      f"whatever it meets")

shutil.rmtree(_where, ignore_errors=True)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
