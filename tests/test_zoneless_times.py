"""A time the evidence recorded with no zone is never given one.

A FAT file time and an EXIF capture time are what the device's clock said,
with no zone. The page showed them as recorded. The report read them as UTC
and converted them: a camera's 14:00 was printed as 10:00 EDT, with the zone
stated as though it were known. The Project VIC export wrote them as +00:00.
The C4All and LAVA exports turned them into epochs in the examining
machine's own zone, so two lab machines gave the same file two moments.

And two times that are UTC -- NTFS, an MP4's mvhd -- were stored with the
zone stripped, so they could not be told apart from those.
"""
import json
import logging
import os
import shutil
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import exif_read, ntfs_mft, timeutil  # noqa: E402
from fme.exports import c4all_export, kml_export, lava_export  # noqa: E402
from fme.exports import projectvic_export, report  # noqa: E402

ok = True
CAMERA = "2024-06-01T14:00:00"
ROOT = os.path.join(tempfile.gettempdir(), "fme_zoneless-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def tiff_block(offset: bytes = b"") -> bytes:
    """A little-endian TIFF block: IFD0 -> Exif IFD with DateTimeOriginal
    and, if given, OffsetTimeOriginal."""
    stamp = b"2024:06:01 14:00:00\x00"
    exif_entries = [(0x9003, 2, len(stamp), stamp)]
    if offset:
        exif_entries.append((0x9011, 2, len(offset) + 1, offset + b"\x00"))
    ifd0_at = 8
    exif_at = ifd0_at + 2 + 12 + 4
    data_at = exif_at + 2 + 12 * len(exif_entries) + 4
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif_at) + b"\0" * 4
    entries, blob = b"", b""
    for tag, kind, count, value in exif_entries:
        if len(value) <= 4:
            entries += struct.pack("<HHI", tag, kind, count) + value.ljust(4, b"\0")
        else:
            entries += struct.pack("<HHII", tag, kind, count, data_at + len(blob))
            blob += value
    exif = struct.pack("<H", len(exif_entries)) + entries + b"\0" * 4
    return b"II*\x00" + struct.pack("<I", ifd0_at) + ifd0 + exif + blob


print("where times are made")
plain = exif_read.read_tiff_block(tiff_block()).get("captured")
check("an EXIF capture time with no offset stays zone-less", plain == CAMERA, plain)
with_offset = exif_read.read_tiff_block(tiff_block(b"-04:00")).get("captured")
check("one with OffsetTimeOriginal carries that zone",
      with_offset == CAMERA + "-04:00", with_offset)
ntfs = ntfs_mft._filetime(133617024000000000)
check("an NTFS time says it is UTC", ntfs == "2024-06-01T08:00:00+00:00", ntfs)
mp4 = exif_read._iso_from_mp4_seconds(3_800_000_000)
check("an MP4 mvhd time says it is UTC", mp4.endswith("+00:00"), mp4)

print("\nthe report")
shown = timeutil.show(CAMERA, "America/New_York")
check("a zone-less time is shown as recorded and marked, not converted",
      shown == "2024-06-01 14:00:00 (device clock; zone not recorded)", shown)
shown = timeutil.show(CAMERA + "+00:00", "America/New_York")
check("a time with its zone still converts", shown == "2024-06-01 10:00:00 -04:00 (EDT)",
      shown)
header = report._describe_zone("America/New_York")
check("the header says which times the zone applies to",
      "zone not recorded" in header and "not converted" in header)

print("\nexports that write a moment, on two lab machines")
answers = {}
for lab in ("America/New_York", "Asia/Tokyo"):
    os.environ["TZ"] = lab
    time.tzset()
    answers[lab] = (c4all_export.epoch(CAMERA), lava_export.epoch(CAMERA),
                    c4all_export.epoch(CAMERA + "+00:00"),
                    lava_export.epoch(CAMERA + "+00:00"))
os.environ.pop("TZ", None)
time.tzset()
ny, tokyo = answers["America/New_York"], answers["Asia/Tokyo"]
check("C4All and LAVA give a zone-less time no epoch",
      ny[:2] == ("", None) and tokyo[:2] == ("", None), f"{ny[:2]} / {tokyo[:2]}")
check("and the same epoch for a zoned time whatever the machine",
      ny[2:] == tokyo[2:] == ("1717250400", 1717250400), f"{ny[2:]} / {tokyo[2:]}")

print("\nthe Project VIC export")
manifest = os.path.join(ROOT, "manifest.json")
with open(manifest, "w") as fh:
    json.dump({"summary": {}, "duplicates": [], "files": [
        {"out_path": "images/jpg/a.jpg", "md5": "1" * 32, "sha256": "a" * 64,
         "size": 1, "category": "image", "mtime": CAMERA, "crtime": CAMERA,
         "atime": ""},
        {"out_path": "images/jpg/b.jpg", "md5": "2" * 32, "sha256": "b" * 64,
         "size": 1, "category": "image", "mtime": ntfs, "crtime": "", "atime": ""}]}, fh)
out = os.path.join(ROOT, "vics.json")
summary = projectvic_export.export(manifest, out, case_number="T-1",
                                   use_categories=False)
media = json.load(open(out))["value"][0]["Media"]
fat, ntfs_file = media[0]["MediaFiles"][0], media[1]["MediaFiles"][0]
check("a zone-less time is written as VICS's unknown, not as +00:00",
      fat["Written"] == projectvic_export.UNKNOWN_TIME, fat["Written"])
check("a UTC time is written with its zone", ntfs_file["Written"] == ntfs,
      ntfs_file["Written"])
check("and the export says how many it could not carry",
      summary.get("times_without_zone") == 2, str(summary.get("times_without_zone")))

print("\nthe KML export")
point = kml_export._placemark({"out_path": "a.jpg", "captured": CAMERA,
                               "latitude": 1.0, "longitude": 2.0}, 1)
check("a zone-less capture time is not put on the time slider",
      "<TimeStamp>" not in point and "zone not recorded" in point)
point = kml_export._placemark({"out_path": "a.jpg", "captured": CAMERA + "-04:00",
                               "latitude": 1.0, "longitude": 2.0}, 1)
check("a zoned one is", "<TimeStamp><when>2024-06-01T14:00:00-04:00</when>" in point)

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
