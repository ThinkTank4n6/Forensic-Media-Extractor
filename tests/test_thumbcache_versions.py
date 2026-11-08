"""Every thumbcache version is read with its own layout, and a walk that stops
early says so.

Windows Vista's entry header (version 0x14) carries an 8-byte file extension
after the entry hash, so it is 56 bytes with every size 8 bytes later than
Windows 7's 48. It was read with the Windows 7 layout: the extension's
UTF-16 characters were taken as the identifier size, the walk judged that
not credible and returned -- no thumbnails, and nothing said. From Windows
8's second format (0x1C) the file header also moved the first entry's offset
from 12 to 16. Layouts per libyal's libwtcdb documentation.
"""
import logging
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
import make_media_test_image as g
from fme.engines import containers

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def entry(layout, cache_id, identifier, payload, extension="jpg"):
    name = identifier.encode("utf-16-le")
    sizes = struct.pack("<III", len(name), 0, len(payload))
    if layout == "vista":
        head = (struct.pack("<Q", cache_id) + extension.encode("utf-16-le").ljust(8, b"\0")
                + sizes + bytes(4) + bytes(16))
    elif layout == "7":
        head = struct.pack("<Q", cache_id) + sizes + bytes(4) + bytes(16)
    else:
        head = struct.pack("<Q", cache_id) + sizes + struct.pack("<II", 96, 64) + bytes(4) + bytes(16)
    size = 8 + len(head) + len(name) + len(payload)
    return b"CMMM" + struct.pack("<I", size) + head + name + payload


def database(version, entries, tail=b""):
    if version >= 0x1C:
        header = b"CMMM" + struct.pack("<IIIII", version, 0, 0, 28, 0) + bytes(4)
    else:
        header = b"CMMM" + struct.pack("<IIIII", version, 0, 24, 0, 0)
    body = b"".join(entries)
    free = len(header) + len(body)
    at = 20 if version >= 0x1C else 16
    header = header[:at] + struct.pack("<I", free) + header[at + 4:]
    return header + body + tail


photo, icon = g.make_jpeg(12), g.make_png()
for version, layout, label in ((0x14, "vista", "Vista"), (0x15, "7", "Windows 7"),
                               (0x1A, "8", "Windows 8"), (0x1F, "8", "Windows 8.1"),
                               (0x20, "8", "Windows 10")):
    data = database(version, [entry(layout, 0x1122, "photo.jpg", photo),
                              entry(layout, 0x3344, "icon.png", icon, "png")],
                    tail=bytes(4096))          # the free space a real file keeps
    report = {}
    found = list(containers.thumbcache_entries(data, report))
    check(f"{label} (0x{version:X}): both thumbnails, byte for byte, with names",
          [(n, p) for n, p, _ in found] == [("photo.jpg", photo), ("icon.png", icon)],
          f"{len(found)} found")
    check("   the free space after them is the end, not a stop",
          "stopped" not in report and report.get("entries") == 2, report)

print("\na walk that stops early")
good = entry("7", 1, "first.jpg", photo)
broken = bytearray(entry("7", 2, "second.jpg", photo))
broken[4:8] = struct.pack("<I", 3)                # an impossible entry size
data = database(0x15, [good, bytes(broken)])
report = {}
found = list(containers.thumbcache_entries(data, report))
check("the entries before the damage are kept", len(found) == 1)
check("and the stop is reported, with where and why",
      report.get("stopped") and report.get("stopped_at") == 24 + len(good), report)
data = database(0x15, [good, entry("7", 2, "second.jpg", photo)])[:-100]
report = {}
list(containers.thumbcache_entries(data, report))
check("a truncated database is reported as truncated",
      "truncated" in report.get("stopped", ""), report.get("stopped"))
report = {}
list(containers.thumbcache_entries(database(0x19, [entry("7", 1, "a.jpg", photo)]), report))
check("an undocumented version is read, and said to be a guess",
      report.get("layout_guessed") and report.get("entries") == 1, report)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
