"""What an NTFS record's $DATA flags say about its clusters, and the times.

The MFT pass read every deleted file's clusters as the file. An LZNT1
compressed file's clusters are compressed, an EFS file's are ciphertext, an
overlay-compressed file's $DATA is an empty hole and an online-only cloud
placeholder's content is with the provider -- and each was stored and hashed
as though it were the file, a compressed JPEG often classed as a picture by
its name. A file over 2 GiB came back empty and was dropped without a word.

Checked against ntfs-windows.img, which Windows 11 wrote and hashed itself
(tests/fixtures/ntfs-windows.known.tsv): its two LZNT1 files must inflate to
the SHA-256 Windows recorded, read in place and again after their records
are marked deleted.

And NTFS times: kept to the microsecond, and before 1970 rather than empty.
"""
import gzip
import hashlib
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.engines import ntfs_mft

ROOT = os.path.join(tempfile.gettempdir(), "fme_ntfs_compressed-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail else ''} -- "
          f"{'ok' if good else 'WRONG'}")


print("LZNT1, by hand")
# "abc" as literals, then a reference 3 back for 6 bytes: flag byte 0x08
# (fourth item is a reference), token (3-1)<<12 | (6-3).
chunk = bytes([0x08]) + b"abc" + struct.pack("<H", (2 << 12) | 3)
unit = struct.pack("<H", 0xB000 | (len(chunk) - 1)) + chunk
out = ntfs_mft.lznt1_decompress(unit, 8192)
check("a back reference repeats what it points at", out[:9] == b"abcabcabc", out[:9])
check("a chunk that inflates short is zero-filled to 4096, the unit to its end",
      len(out) == 8192 and out[9:] == bytes(8192 - 9))
# Past 16 bytes out the split moves: 11 length bits, 5 displacement bits. A
# reference 17 back for 3 bytes is then (17-1)<<11 | 0.
literals = bytes(range(65, 65 + 17))
chunk = (b"\x00" + literals[:8] + b"\x00" + literals[8:16]
         + bytes([0x02]) + literals[16:17] + struct.pack("<H", 16 << 11))
unit = struct.pack("<H", 0xB000 | (len(chunk) - 1)) + chunk
out = ntfs_mft.lznt1_decompress(unit, 4096)
check("the split between displacement and length moves as output grows",
      out[:20] == literals + literals[:3], out[:20])
bad = struct.pack("<H", 0xB000 | 2) + bytes([0x01]) + struct.pack("<H", 0)
try:
    ntfs_mft.lznt1_decompress(bad, 4096)
    check("a reference before its chunk is refused", False)
except ntfs_mft.Undecodable:
    check("a reference before its chunk is refused", True)
stored = struct.pack("<H", 0x3000 | 4095) + bytes(range(256)) * 16
check("a stored chunk is copied as it is",
      ntfs_mft.lznt1_decompress(stored, 4096) == bytes(range(256)) * 16)

print("\nntfs-windows.img, read in place")
img = bytearray(gzip.open(os.path.join(HERE, "fixtures", "ntfs-windows.img.gz")).read())
known = {}
for line in open(os.path.join(HERE, "fixtures", "ntfs-windows.known.tsv"),
                 encoding="utf-8"):
    if not line.startswith("#"):
        path, _l, _a, _s, digest, _w = line.rstrip("\n").split("\t")
        known[path] = digest
base = 0
for i in range(4):
    lba = struct.unpack_from("<I", img, 446 + 16 * i + 8)[0]
    if lba and img[lba * 512 + 3:lba * 512 + 11] == b"NTFS    ":
        base = lba * 512
        break


def read(at, count):
    return bytes(img[base + at:base + at + count])


boot = ntfs_mft.read_boot(read(0, 512))
folders = ntfs_mft.directory_map(read, boot)
by_path = {}
for record in ntfs_mft.records(read, boot):
    if not record["directory"] and record["name"]:
        by_path[ntfs_mft.path_of(record, folders)] = record
for path in ("lznt1/text_100000.txt", "lznt1/mixed_70000.bin"):
    record = by_path[path]
    data, how = ntfs_mft.content(record, read, boot)
    check(f"{path} is flagged compressed and inflates to Windows' SHA-256",
          record["data_flags"] & ntfs_mft.DATA_COMPRESSED
          and hashlib.sha256(data).hexdigest() == known[path], how)
    pieces, _ = ntfs_mft.content_stream(dict(record), read, boot, step=4096)
    check("   and the same streamed",
          hashlib.sha256(b"".join(pieces)).hexdigest() == known[path])
for path in ("sparse/both_ends.bin", "Pictures/sparse_photo.jpg"):
    data, _ = ntfs_mft.content(by_path[path], read, boot)
    check(f"{path}, sparse and not compressed, still reads as Windows read it",
          hashlib.sha256(data).hexdigest() == known[path])
for path in ("wof/lzx/text_5000.txt", "wof/xpress4k/mixed_70000.bin",
             "CloudRoot/online_only_photo.jpg"):
    record = by_path[path]
    data, how = ntfs_mft.content(record, read, boot)
    check(f"{path} is refused, with the reason",
          how == "refused" and data == b"" and record.get("refused"),
          record.get("refused", "")[:60])
data, how = ntfs_mft.content(by_path["CloudRoot/hydrated_100000.txt"], read, boot)
check("a hydrated cloud file, all on the volume, is read",
      hashlib.sha256(data).hexdigest() == known["CloudRoot/hydrated_100000.txt"], how)

print("\nthe same records, deleted")


def record_offset(number):
    """Where record `number` sits in the image, through the table's runs."""
    per = boot["cluster_size"] // boot["record_size"] or 1
    seen = 0
    for lcn, count in ntfs_mft._mft_runs(read, boot):
        held = count * boot["cluster_size"] // boot["record_size"]
        if number < seen + held:
            return base + lcn * boot["cluster_size"] + (number - seen) * boot["record_size"]
        seen += held
    raise LookupError(number)


def data_attribute(number):
    """Offset in the image of the record's unnamed $DATA attribute header."""
    at = record_offset(number)
    position = struct.unpack_from("<H", img, at + 20)[0]
    while True:
        kind, length = struct.unpack_from("<II", img, at + position)
        if kind == ntfs_mft.ATTR_DATA and img[at + position + 9] == 0:
            return at + position
        position += length


targets = {}
for path in ("lznt1/text_100000.txt", "lznt1/mixed_70000.bin",
             "Pictures/plain.jpg"):
    number = by_path[path]["number"]
    targets[number] = path
    flags_at = record_offset(number) + 22
    struct.pack_into("<H", img, flags_at,
                     struct.unpack_from("<H", img, flags_at)[0] & ~1)
# plain.jpg's $DATA is flagged encrypted, as EFS writes it; its clusters are
# still the picture here, which is the trap: stored, it would pass as one.
plain = [n for n, p in targets.items() if p == "Pictures/plain.jpg"][0]
flags_at = data_attribute(plain) + 12
struct.pack_into("<H", img, flags_at,
                 struct.unpack_from("<H", img, flags_at)[0] | ntfs_mft.DATA_ENCRYPTED)
deleted = {r["number"]: r for r in ntfs_mft.deleted_files(read, boot)
           if r["number"] in targets}
for number, path in targets.items():
    record = deleted.get(number)
    if record is None:
        check(f"{path}, deleted, is found", False)
        continue
    data, how = ntfs_mft.content(record, read, boot)
    if path.startswith("lznt1/"):
        check(f"{path}, deleted, inflates to Windows' SHA-256",
              hashlib.sha256(data).hexdigest() == known[path], how)
    else:
        check(f"{path}, deleted and EFS-encrypted, is refused, not stored as a picture",
              how == "refused" and not data and "EFS" in record.get("refused", ""),
              record.get("refused", ""))

# A compressed unit whose clusters have been overwritten does not decode: the
# file stops there and says so, rather than storing what came out.
number = [n for n, p in targets.items() if p == "lznt1/text_100000.txt"][0]
record = deleted[number]
first_lcn = record["runs"][0][0]
saved = bytes(img[base + first_lcn * 4096:base + first_lcn * 4096 + 4])
img[base + first_lcn * 4096:base + first_lcn * 4096 + 2] = struct.pack("<H", 0xBFFF)
data, how = ntfs_mft.content(record, read, boot)
check("an overwritten compression unit stops the file, and says why",
      record.get("undecodable") and record.get("short_by") == 100000 - len(data)
      and len(data) < 100000, record.get("undecodable"))
img[base + first_lcn * 4096:base + first_lcn * 4096 + 4] = saved

print("\nover 2 GiB")
big = {"resident": None, "size_from": "data", "size": 3 * 1024 ** 3,
       "allocated": 3 * 1024 ** 3, "initialized": 3 * 1024 ** 3,
       "runs": [(10, 3 * 1024 ** 3 // 4096)], "data_flags": 0}
reads = []


def pattern(at, count):
    reads.append(count)
    return b"\x5A" * count


data, how = ntfs_mft.content(big, pattern, {"cluster_size": 4096})
check("content() says it is too large, rather than returning nothing",
      how == "too large" and data == b"" and "too large" in big.get("refused", ""))
pieces, how = ntfs_mft.content_stream(big, pattern, {"cluster_size": 4096})
total = sum(len(p) for p in pieces)
check("content_stream() reads all 3 GiB, a piece at a time",
      total == 3 * 1024 ** 3 and max(reads) <= ntfs_mft.STREAM_STEP
      and big.get("short_by") == 0, f"{total:,} bytes in {len(reads)} reads")

print("\ntimes")
t = ntfs_mft._filetime(133617024001234567)
check("sub-second precision is kept (to the microsecond)",
      t == "2024-06-01T08:00:00.123456+00:00", t)
t = ntfs_mft._filetime(133617024000000000)
check("a whole-second time reads as it always did", t == "2024-06-01T08:00:00+00:00", t)
t = ntfs_mft._filetime(ntfs_mft.WINDOWS_EPOCH_OFFSET - 10_000_000)
check("a time before 1970 is a time, not empty", t == "1969-12-31T23:59:59+00:00", t)
t = ntfs_mft._filetime(1)
check("the start of the NTFS epoch", t == "1601-01-01T00:00:00+00:00", t)
check("zero is no time", ntfs_mft._filetime(0) == "")
check("past year 9999 is no time", ntfs_mft._filetime(2 ** 64 - 1) == "")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
