"""Build a small NTFS volume with known deleted files in it.

Written by hand rather than with a formatting tool, so what should come back
is known exactly: one deleted file whose data is resident in its own MFT
record (which no carve can reach, because it never occupied a cluster), one
deleted file whose content is still in released clusters, and one live file
that must not be reported as deleted.
"""
import tempfile
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import make_media_test_image as g

SECTOR, SPC = 512, 8
CLUSTER = SECTOR * SPC            # 4096
RECORD = 1024
MFT_CLUSTER = 4

def boot(total_clusters):
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x52\x90"
    b[3:11] = b"NTFS    "
    struct.pack_into("<H", b, 11, SECTOR)
    b[13] = SPC
    struct.pack_into("<Q", b, 40, total_clusters * SPC)   # total sectors
    struct.pack_into("<Q", b, 48, MFT_CLUSTER)
    struct.pack_into("<b", b, 64, RECORD // CLUSTER if RECORD >= CLUSTER
                     else -(RECORD.bit_length() - 1))
    b[510:512] = b"\x55\xaa"
    return bytes(b)

def filetime(seconds):
    return int(seconds * 10_000_000) + 116444736000000000

def resident_header(kind, value):
    """A resident attribute header is 24 bytes: type, length, flags, then
    the value's own length and where it starts."""
    head = bytearray(24)
    struct.pack_into("<I", head, 0, kind)
    total = 24 + len(value)
    total += (-total) % 8
    struct.pack_into("<I", head, 4, total)
    head[8] = 0                       # resident
    struct.pack_into("<I", head, 16, len(value))
    struct.pack_into("<H", head, 20, 24)
    return (bytes(head) + value).ljust(total, b"\x00")


def attr_standard(created, modified, accessed):
    value = struct.pack("<QQQQ", filetime(created), filetime(modified),
                        filetime(modified), filetime(accessed)) + bytes(24)
    return resident_header(0x10, value)

def attr_filename(name, parent, size):
    encoded = name.encode("utf-16-le")
    # parent (8), four timestamps (32), allocated (8), real (8),
    # flags (4), reparse (4), name length, namespace, then the name.
    value = (struct.pack("<Q", parent | (1 << 48))
             + bytes(32)
             + struct.pack("<QQ", size, size)
             + struct.pack("<II", 0, 0)
             + bytes([len(name), 1])
             + encoded)
    return resident_header(0x30, value)

def attr_data_resident(payload):
    return resident_header(0x80, payload)

def attr_data_runs(start_cluster, clusters, size):
    # one run: length byte-count 1, offset byte-count depends
    offset = start_cluster.to_bytes(2, "little", signed=True)
    run = bytes([0x20 | 0x01]) + bytes([clusters]) + offset + b"\x00"
    head = bytearray(64)
    struct.pack_into("<I", head, 0, 0x80)
    head[8] = 1                                  # non-resident
    struct.pack_into("<Q", head, 16, 0)          # starting VCN
    struct.pack_into("<Q", head, 24, clusters-1) # last VCN
    struct.pack_into("<H", head, 32, 64)         # run list offset
    struct.pack_into("<Q", head, 40, clusters * CLUSTER)   # allocated
    struct.pack_into("<Q", head, 48, size)       # real size
    struct.pack_into("<Q", head, 56, size)       # initialized size: all of it written
    total = 64 + len(run)
    total += (-total) % 8
    struct.pack_into("<I", head, 4, total)
    return (bytes(head) + run).ljust(total, b"\x00")

def record(number, in_use, directory, attrs):
    body = b"".join(attrs) + struct.pack("<I", 0xFFFFFFFF)
    header = bytearray(48)
    header[0:4] = b"FILE"
    struct.pack_into("<H", header, 4, 48)        # fixup offset
    struct.pack_into("<H", header, 6, RECORD // SECTOR + 1)
    struct.pack_into("<H", header, 16, 1)        # sequence
    struct.pack_into("<H", header, 20, 56)       # first attribute
    flags = (1 if in_use else 0) | (2 if directory else 0)
    struct.pack_into("<H", header, 22, flags)
    struct.pack_into("<I", header, 24, 56 + len(body))
    struct.pack_into("<I", header, 28, RECORD)
    struct.pack_into("<I", header, 44, number)
    raw = bytearray(RECORD)
    raw[0:48] = header
    raw[56:56 + len(body)] = body
    # the update sequence: marker in the array, and at the end of each sector
    marker = b"\x01\x00"
    fixups = marker + b"".join(bytes(raw[i * SECTOR - 2:i * SECTOR])
                               for i in range(1, RECORD // SECTOR + 1))
    raw[48:48 + len(fixups)] = fixups
    for i in range(1, RECORD // SECTOR + 1):
        raw[i * SECTOR - 2:i * SECTOR] = marker
    return bytes(raw)

# --- the volume -----------------------------------------------------------
jpeg_resident = g.make_jpeg(1)[:600]          # small enough to live in a record
png_big = g.make_png()                        # lives in clusters
total_clusters = 200
image = bytearray(total_clusters * CLUSTER)
image[0:SECTOR] = boot(total_clusters)

DATA_CLUSTER = 100
clusters_needed = (len(png_big) + CLUSTER - 1) // CLUSTER
image[DATA_CLUSTER * CLUSTER:DATA_CLUSTER * CLUSTER + len(png_big)] = png_big

records = []
# 0: $MFT itself, contiguous from MFT_CLUSTER, 8 clusters
records.append(record(0, True, False, [
    attr_standard(1700000000, 1700000000, 1700000000),
    attr_filename("$MFT", 5, 8 * CLUSTER),
    attr_data_runs(MFT_CLUSTER, 8, 8 * CLUSTER)]))
for n in range(1, 5):
    records.append(record(n, True, False, [
        attr_standard(1700000000, 1700000000, 1700000000),
        attr_filename(f"$Reserved{n}", 5, 0),
        attr_data_resident(b"")]))
# 5: the root directory
records.append(record(5, True, True, [
    attr_standard(1700000000, 1700000000, 1700000000),
    attr_filename(".", 5, 0)]))
# 6: a live folder
records.append(record(6, True, True, [
    attr_standard(1700000000, 1700000000, 1700000000),
    attr_filename("Pictures", 5, 0)]))
# 7: a DELETED file with resident content -- a carve can never find this
records.append(record(7, False, False, [
    attr_standard(1700001111, 1700002222, 1700003333),
    attr_filename("tiny_thumbnail.jpg", 6, len(jpeg_resident)),
    attr_data_resident(jpeg_resident)]))
# 8: a DELETED file whose content is still in free clusters
records.append(record(8, False, False, [
    attr_standard(1700004444, 1700005555, 1700006666),
    attr_filename("holiday_photo.png", 6, len(png_big)),
    attr_data_runs(DATA_CLUSTER, clusters_needed, len(png_big))]))
# 9: a live file, which should not be reported as deleted
records.append(record(9, True, False, [
    attr_standard(1700000000, 1700000000, 1700000000),
    attr_filename("still_here.png", 6, len(png_big)),
    attr_data_runs(DATA_CLUSTER, clusters_needed, len(png_big))]))

table = b"".join(records)
image[MFT_CLUSTER * CLUSTER:MFT_CLUSTER * CLUSTER + len(table)] = table
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "ntfs_test.dd")
with open(OUT, "wb") as fh:
    fh.write(bytes(image))
print(f"built {OUT}: {len(image):,} bytes, {len(records)} MFT records")
print(f"  resident deleted file : tiny_thumbnail.jpg ({len(jpeg_resident)} bytes)")
print(f"  clustered deleted file: holiday_photo.png ({len(png_big):,} bytes)")
import hashlib
print(f"  expected sha256 resident : {hashlib.sha256(jpeg_resident).hexdigest()[:16]}…")
print(f"  expected sha256 clustered: {hashlib.sha256(png_big).hexdigest()[:16]}…")
