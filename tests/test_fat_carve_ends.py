"""fat32_core reads one volume correctly from two threads.

Until v2026.11.319 this also checked the FAT32 browser's own carve, retired
with the browser; what follows is kept from when it did.

The FAT32 browser's carve ended a file where the file ends.

carve_unallocated stopped at the first footer it found, searching from byte
0. For three formats that is not the end of the file:

  * a camera JPEG carries its EXIF thumbnail -- a whole JPEG, FF D9 and all
    -- in its APP1 segment, so the carve stored the header and the thumbnail
    and called it the photo;
  * a ZIP's end-of-central-directory record is 22 bytes and a comment after
    its signature, and the carve cut it at the signature; a ZIP stored
    inside the ZIP has its own record, which came first;
  * a PDF saved with incremental updates has a %%EOF after each, and only
    the last one ends it.

Each is planted in free clusters of a hand-built FAT32 volume with unrelated
bytes behind it, and must come back byte for byte.

And reads of one volume from two threads: the browser shares one
Fat32FileSystem between its carve thread and its request threads, and a
seek in one landed between the other's seek and read.
"""
import io
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from PIL import Image

import make_test_image as mk
from fme.engines import fat32_core

ROOT = os.path.join(tempfile.gettempdir(), "fme_fat_carve_ends-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def jpeg(size, seed):
    """A photo-like JPEG Pillow encodes (noise, so it does not shrink)."""
    picture = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)
                              if seed is None else bytes((i * seed) % 251
                                                         for i in range(size[0] * size[1] * 3)))
    buf = io.BytesIO()
    picture.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def with_exif_thumbnail(main, thumb):
    """`main` with an APP1 EXIF segment whose IFD1 holds `thumb`, as a
    camera writes it: TIFF header, an empty IFD0, then IFD1 with
    JPEGInterchangeFormat / JPEGInterchangeFormatLength and the thumbnail."""
    ifd1_at = 8 + 2 + 4                       # header, IFD0 (no entries), next
    thumb_at = ifd1_at + 2 + 2 * 12 + 4
    tiff = (b"II*\x00" + struct.pack("<I", 8)
            + struct.pack("<H", 0) + struct.pack("<I", ifd1_at)
            + struct.pack("<H", 2)
            + struct.pack("<HHII", 0x0201, 4, 1, thumb_at)
            + struct.pack("<HHII", 0x0202, 4, 1, len(thumb))
            + struct.pack("<I", 0) + thumb)
    app1 = b"Exif\x00\x00" + tiff
    return main[:2] + b"\xFF\xE1" + struct.pack(">H", len(app1) + 2) + app1 + main[2:]


thumb = jpeg((160, 120), 7)
photo = with_exif_thumbnail(jpeg((320, 240), None), thumb)
assert photo.find(b"\xFF\xD9") < len(photo) - 2      # the thumbnail's EOI comes first
Image.open(io.BytesIO(photo)).load()

inner = io.BytesIO()
with zipfile.ZipFile(inner, "w", zipfile.ZIP_STORED) as z:
    z.writestr("inner.txt", b"inside the inner archive\n" * 20)
outer = io.BytesIO()
with zipfile.ZipFile(outer, "w", zipfile.ZIP_STORED) as z:
    z.writestr("evidence.zip", inner.getvalue())     # stored: its record is visible
    z.writestr("notes.txt", b"notes\n" * 400)
    z.comment = b"exported by the device's backup app on 2024-06-01" * 3
archive = outer.getvalue()

first = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
         b"xref\n0 2\n0000000000 65535 f \n0000000009 00000 n \n"
         b"trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n52\n%%EOF\n")
update = (b"2 0 obj\n<< /Producer (incremental update) >>\nendobj\n"
          + b"% padding so the update spans clusters\n" * 30
          + b"xref\n0 1\n0000000000 65535 f \n2 1\n0000000150 00000 n \n"
          b"trailer\n<< /Size 3 /Root 1 0 R /Prev 52 >>\nstartxref\n150\n%%EOF\n")
pdf = first + update

CLUSTER = mk.BYTES_PER_SECTOR * mk.SECTORS_PER_CLUSTER
junk = b"UNRELATED-BYTES-AFTER-THE-FILE " * (CLUSTER // 8)
volume = bytearray(mk.total_sectors * mk.BYTES_PER_SECTOR)
volume[0:512] = mk.build_boot_sector()
fat_at = mk.RESERVED_SECTORS * mk.BYTES_PER_SECTOR
for copy in range(mk.NUM_FATS):
    at = fat_at + copy * mk.FAT_SIZE_SECTORS * mk.BYTES_PER_SECTOR
    struct.pack_into("<III", volume, at, 0x0FFFFFF8, 0x0FFFFFFF, 0x0FFFFFF8)


def offset(cluster):
    return (mk.first_data_sector + (cluster - 2) * mk.SECTORS_PER_CLUSTER) * mk.BYTES_PER_SECTOR


planted = {}
cluster = 10
for name, blob in (("jpg", photo), ("zip", archive), ("pdf", pdf)):
    volume[offset(cluster):offset(cluster) + len(blob)] = blob
    used = -(-len(blob) // CLUSTER)
    # Unrelated bytes in the slack and the clusters after it, so a carve
    # that runs on is seen to.
    tail = offset(cluster) + len(blob)
    volume[tail:offset(cluster + used)] = junk[:offset(cluster + used) - tail]
    volume[offset(cluster + used):offset(cluster + used + 3)] = junk[:3 * CLUSTER]
    planted[cluster] = (name, blob)
    cluster += used + 4

image = os.path.join(ROOT, "fat32.img")
with open(image, "wb") as fh:
    fh.write(volume)

# The FAT32 browser's carve, checked here until v2026.11.318, was retired
# with the browser in v2026.11.319; the program's carve has its own tests.

print("\nreads from two threads")


class SeekRead:
    """A seek/read source with no read_at, as media_core's _ReaderSource is,
    that gives up the processor between the two calls -- so any interleaving
    that can happen does."""

    def __init__(self, path):
        self._f = open(path, "rb")

    def seek(self, where):
        self._f.seek(where)
        time.sleep(0)

    def read(self, count):
        return self._f.read(count)

    def get_size(self):
        return len(volume)

    def close(self):
        self._f.close()


def hammer(fs, label):
    wrong = []

    def reader(clusters):
        for _ in range(300):
            for c in clusters:
                if fs.read_cluster(c) != bytes(volume[offset(c):offset(c) + CLUSTER]):
                    wrong.append(c)

    threads = [threading.Thread(target=reader, args=([c for c in range(10, 60) if c % 2 == k],))
               for k in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(f"{label}: every cluster read from two threads is that cluster",
          not wrong, f"{len(wrong)} wrong" if wrong else "")


with fat32_core.Fat32FileSystem(image) as fs:
    hammer(fs, "a raw image")
source = SeekRead(image)
fs = fat32_core.Fat32FileSystem.__new__(fat32_core.Fat32FileSystem)
fs.source = fat32_core.locate_fat32_volume(source)
fs.bs = fat32_core.parse_boot_sector(fs.source)
fs.fat = fat32_core.FatTable(fs.source, fs.bs)
hammer(fs, "a seek/read source, built as media_core builds it")
source.close()

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
