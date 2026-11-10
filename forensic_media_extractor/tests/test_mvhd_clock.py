"""A video's mvhd time is the container's claim, and a GoPro's is its clock.

The MP4 format defines mvhd's creation time as UTC, and since v2026.11.281
it is stored with that zone so it cannot be confused with a camera clock's
reading. But many cameras -- GoPro is the documented one -- write their own
local clock there, and read as UTC a GoPro clip shot at 14:00 in
Philadelphia was dated 10:00 EDT.

Now the time says what it rests on ("captured_basis"), and a clip from a
camera known to write local time keeps its time with no zone, which
timeutil stores as a reading ("device clock; zone not recorded").
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.engines import exif_read, timeutil

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


# 2026-07-04 14:00:00 counted from 1904-01-01.
SECONDS = 3865968000 + 14 * 3600
CLOCK = "2026-07-04T14:00:00"


def movie(handler_name=b"", make=b""):
    mvhd = box(b"mvhd", bytes(4) + struct.pack(">I", SECONDS) + bytes(92))
    tracks = b""
    if handler_name:
        hdlr = box(b"hdlr", bytes(4) + bytes(4) + b"vide" + bytes(12) + handler_name + b"\0")
        tracks = box(b"trak", box(b"mdia", hdlr))
    udta = box(b"udta", box(b"\xa9mak", struct.pack(">HH", len(make), 0) + make)) if make else b""
    return (box(b"ftyp", b"mp42\0\0\0\0mp42isom") + box(b"moov", mvhd + tracks + udta)
            + box(b"mdat", bytes(64)))


print("a phone's clip: UTC, as the format defines, and said to be the file's claim")
meta = exif_read.read_bytes(movie(b"Core Media Video", b"Apple"))
check("the time keeps its UTC label", meta.get("captured") == CLOCK + "+00:00",
      meta.get("captured"))
check("and says what it rests on, and that some devices write local time",
      "UTC as recorded by the container" in meta.get("captured_basis", "")
      and "local time" in meta.get("captured_basis", ""))
check("no working keys leak out", not [k for k in meta if k.startswith("_")], list(meta))

print("\na GoPro's clip: its own clock, kept with no zone")
for label, blob in (("named by its track handler", movie(b"GoPro AVC")),
                    ("named by its make", movie(make=b"GoPro"))):
    meta = exif_read.read_bytes(blob)
    check(f"{label}: the time has no zone", meta.get("captured") == CLOCK, meta.get("captured"))
    record = {"captured": meta.get("captured")}
    timeutil.settle(record)
    check("   and is stored as a reading, shown as recorded",
          record["captured"] == "" and timeutil.reading(record, "captured") is not None
          and timeutil.show(timeutil.stored(record, "captured"), "America/New_York")
          == "2026-07-04 14:00:00 (device clock; zone not recorded)")
    check("   and the basis says why", "GoPro" in meta.get("captured_basis", ""))

print("\na capture time from EXIF is not relabeled")
exif_like = {"captured": "2026-07-04T14:00:00", "_mvhd": CLOCK + "+00:00"}
check("the basis is only for an mvhd time",
      "captured_basis" not in exif_read._mvhd_basis(dict(exif_like)))

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
