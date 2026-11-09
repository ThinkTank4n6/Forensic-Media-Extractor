"""
make_media_test_image.py — build a FAT32 image containing real image and
video files, for exercising media_core.py (and the Media Extraction tab).

Same spirit as make_test_image.py, but the volume holds media rather than
text: live JPEG/PNG/GIF/MP4/AVI files under DCIM/ and MOVIES/, plus two
files whose directory entries are absent and whose FAT chains are freed —
recoverable by carving only, exactly as a real deletion leaves them.

Uses nothing outside the standard library. Not part of the shipped tool.

    python3 make_media_test_image.py            # writes media_test.img
    python3 media_core.py -i media_test.img -o ./out
"""

import base64
import binascii
import os
import struct
import zlib

BYTES_PER_SECTOR = 512
SECTORS_PER_CLUSTER = 8            # 4 KiB clusters, like a real formatted card
RESERVED_SECTORS = 32
NUM_FATS = 2
TOTAL_CLUSTERS = 4000              # ~16 MiB volume
FAT_SIZE_SECTORS = ((TOTAL_CLUSTERS + 2) * 4 + BYTES_PER_SECTOR - 1) // BYTES_PER_SECTOR
ROOT_CLUSTER = 2
CLUSTER_SIZE = BYTES_PER_SECTOR * SECTORS_PER_CLUSTER

first_data_sector = RESERVED_SECTORS + NUM_FATS * FAT_SIZE_SECTORS
total_sectors = first_data_sector + TOTAL_CLUSTERS * SECTORS_PER_CLUSTER

# The default volume above is formatted FAT32 with 4,000 clusters. The FAT
# specification decides the type by cluster count alone -- FAT32 starts at
# 65,525 -- so by the specification it is FAT12, and The Sleuth Kit, which
# follows it, refuses to open it. That makes it the right fixture for "a
# volume TSK refuses is still read", and the wrong one for "TSK reads FAT32":
# on a machine with pytsk3 no test had ever had TSK read a FAT volume.
#
# --spec-fat32 builds the same files on a volume that is FAT32 by the
# specification: 512-byte clusters, 66,000 of them (past 65,525 with the
# margin the specification advises against implementations that miscount
# near the boundary), an FSInfo sector and a backup boot sector. About 33 MB.
DEFAULT_GEOMETRY = (SECTORS_PER_CLUSTER, TOTAL_CLUSTERS)
SPEC_FAT32_GEOMETRY = (1, 66_000)


def use_geometry(sectors_per_cluster: int, total_clusters: int) -> None:
    """Set the volume's shape; everything below reads these names."""
    global SECTORS_PER_CLUSTER, TOTAL_CLUSTERS, FAT_SIZE_SECTORS, CLUSTER_SIZE
    global first_data_sector, total_sectors
    SECTORS_PER_CLUSTER = sectors_per_cluster
    TOTAL_CLUSTERS = total_clusters
    FAT_SIZE_SECTORS = ((TOTAL_CLUSTERS + 2) * 4 + BYTES_PER_SECTOR - 1) // BYTES_PER_SECTOR
    CLUSTER_SIZE = BYTES_PER_SECTOR * SECTORS_PER_CLUSTER
    first_data_sector = RESERVED_SECTORS + NUM_FATS * FAT_SIZE_SECTORS
    total_sectors = first_data_sector + TOTAL_CLUSTERS * SECTORS_PER_CLUSTER


def fat_type_by_specification() -> str:
    """What the FAT specification says this volume is: by cluster count."""
    return ("FAT12" if TOTAL_CLUSTERS < 4085
            else "FAT16" if TOTAL_CLUSTERS < 65525 else "FAT32")


def build_fsinfo(free_clusters: int, next_free: int) -> bytes:
    """The FSInfo sector the boot sector points at (sector 1)."""
    info = bytearray(BYTES_PER_SECTOR)
    struct.pack_into("<I", info, 0, 0x41615252)
    struct.pack_into("<I", info, 484, 0x61417272)
    struct.pack_into("<I", info, 488, free_clusters)
    struct.pack_into("<I", info, 492, next_free)
    struct.pack_into("<I", info, 508, 0xAA550000)
    return bytes(info)

EOC = 0x0FFFFFF8


# --------------------------------------------------------------------------
# Sample media, all structurally valid so the carver's parsers can measure them
# --------------------------------------------------------------------------
def make_png(width=64, height=48) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        for x in range(width):
            raw += bytes(((x * 4) % 256, (y * 5) % 256, (x * y) % 256))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


# A real 48x48 baseline JPEG -- DQT, SOF0, DHT, SOS, entropy-coded data, EOI
# -- encoded once with Pillow (quality 90) and kept here as bytes, so this
# script still needs nothing outside the standard library.
_BASE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoM"
    "DAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsN"
    "FBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAAR"
    "CAAwADADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD83LDST5alP3qmPzAjRhdxGOOcjH3c4Pbr"
    "0rpItHdj5SNuZn8pcqxRnBPIJwSQdo4yCf02YfD8V39ndI41ijclWRgAmSSRvz8p5U4wece+"
    "Op0/RJBA107CSIFpJVY5G0Z4HpkBcggH8eK41WT1va39W/4P49tsrzON0pP+ui7fL/hzndI0"
    "RoryMFY3HH7uN2YDJIOCDkDIHqPoDk9baeHTG5LFvLUlfMOWJBBw2MHIxjjIGR6Gtew0hp1Z"
    "2XjzTuiRVc7MkdCMlccZwPTBxXR2mnXBCxyv5ZI3iJcvlc9sc/wgjoOO4JrZ1JSb5eu/9L+v"
    "wP1zKszk2m3+f9fJbGFp2mSzSKykxRrGN8sseBtXAOD3znPXqOxrd03ToxDHFJApHzCQxDd5"
    "fy5znODx9P8AHo9O0aN7oy71ChyQ0R2qAFyc4J4IK8Y5OecGussNFfanmp51xbsHjDKAOEyo"
    "+Xqw4/DoOM11Qqrt/Xp92unQ/ZcozKM9b/18vPy79zw210FZZpUcld7ASEnfhWAw+R6juMdW"
    "wTXQ2egGJIoY3jhVOki8FyMAYA52jJ6Ng4bpW9pvh1lKWjw4iljB2sMuWGDsGT07YOPw4z01"
    "h4ejslMjQ286yoc/Zx987m+Y4+6AAMnGOMcY5+UhW2/r+v8AgH+bmU5tZJJ/1/SOdsfDjFw5"
    "HmgDYrIjB1XAI29skFRkgdQORXR6fo6XqWztE8rK2XZJNoXnlvcjOeB6kEAVv2OgbIzC1ugG"
    "NgaMOuR825DgbtowOQD65611un6YNrO8JbZG2I2Q4AJy3zEEkZHLdsHpxWvtbtSvr/X9f8NY"
    "/Y8ozOLSt/X5b+dv8+e0zw8yXJHAjZvlbedrZyASRzycAckYP0A6OPwuwnLRmVpmYSGbPmYY"
    "NwPQDpgse5GDW5Do5nhEbJGy+UGWUMdofjPUHcSRnPXtxzXUaZpKwsLmUo5Gdysd+c9AegDc"
    "kDAGenpjRYhxfM3r287f8DU/XspzJx5bOz/rXp/n00tc8J0zw0IlRtscPQDzTtXtlW3DgnI6"
    "Z78nnO6PDclxIoZEMbgbpkIwMkDawwwUeh6fMOwrotK8KxxrbS2qjzFCyhJCMMxY7SV9Mkcj"
    "9MYrrLLw4k1o7hQFZsDzQcxvuxuB575HGOgFfOU63K+Z6n+b+W5u4ySjf8P6/ryObttGIILL"
    "FbOGE7jCuOWK4xjHofr+FdDaeGwJGk3mfEu8vv8Amc7uAV54YBcgc8HoTg9PY6JNmNQzW8m4"
    "uphjwu4KAFXng9DgjHfPXPRWOiiaUJFDPEYogMFzuXABAAOMZ4G7PUn610QrNWSX9bbuz79f"
    "yP2HKM0s03ol+X3nP2GkC7UqI42WIBwFABTG0EEZJJGQeeR05zXT2mk/YWeaWBfOjdlZ0AwT"
    "tU45xwMHjPZunQb2i6DDFPOIlYoAEAXIcnkuRkZI9snOACTkY6S08PSyM7rMpdUBDQZaNiBg"
    "kHOeqjpjGTxWrqwTtv8A1qtPn/Wh+xZVmi+GUv62/r5n/9k="
)


def make_jpeg(payload_kb=6) -> bytes:
    """A JPEG that decodes, about `payload_kb` KiB long.

    The header segments, scan and end marker are a real image's. COM
    segments after APP0 pad it to the size asked for, so different sizes
    are different files, and the marker walker steps over them the way it
    steps over EXIF on a camera photo.

    This used to be marker-shaped filler with no frame header and a scan
    header no decoder accepts. The carve passed every check on those while
    being wrong about real JPEGs (v2026.11.273: a truncated photo ran over
    the AVI behind it), so the fixture is now the thing it stands for.
    """
    app0_end = 4 + struct.unpack(">H", _BASE_JPEG[4:6])[0]
    target = payload_kb * 1024
    text = b"media_test sample %d " % payload_kb
    pad = bytearray()
    while True:
        room = target - len(_BASE_JPEG) - len(pad) - 4
        if room <= 0:
            break
        n = min(65533, room)
        pad += b"\xff\xfe" + struct.pack(">H", n + 2) + (text * (n // len(text) + 1))[:n]
    return _BASE_JPEG[:app0_end] + bytes(pad) + _BASE_JPEG[app0_end:]


def make_gif(width=32, height=32, comment_kb=3) -> bytes:
    header = b"GIF89a" + struct.pack("<HH", width, height) + bytes([0xF0, 0, 0])
    gct = bytes([0, 0, 0, 255, 255, 255])
    # A comment extension pads the file to a realistic size and exercises
    # the carver's sub-block walk, which has to skip it to reach the trailer.
    text = b"media_test comment block. " * 40
    comment = bytearray(b"\x21\xfe")
    for _ in range(comment_kb):
        for i in range(0, 1024, 255):
            block = text[i % len(text):(i % len(text)) + 255].ljust(255, b" ")
            comment += bytes([len(block)]) + block
    comment += b"\x00"
    gce = b"\x21\xf9\x04\x00\x00\x00\x00\x00"
    desc = b"\x2c" + struct.pack("<HHHH", 0, 0, width, height) + b"\x00"
    lzw = bytes([2]) + b"\x02" + bytes([0x8C, 0x2D]) + b"\x00"
    return header + gct + bytes(comment) + gce + desc + lzw + b"\x3b"


def _box(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + tag + payload


def make_mp4(payload_kb=40) -> bytes:
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    moov = _box(b"moov", bytes((i * 11) % 256 for i in range(600)))
    mdat = _box(b"mdat", bytes((i * 7) % 256 for i in range(payload_kb * 1024)))
    return ftyp + moov + mdat


def make_mov(payload_kb=24) -> bytes:
    ftyp = _box(b"ftyp", b"qt  " + struct.pack(">I", 512) + b"qt  ")
    moov = _box(b"moov", bytes((i * 13) % 256 for i in range(400)))
    mdat = _box(b"mdat", bytes((i * 3) % 256 for i in range(payload_kb * 1024)))
    return ftyp + moov + mdat


def make_avi(payload_kb=32) -> bytes:
    body = (b"AVI LIST" + bytes(200) + b"movi"
            + bytes((i * 5) % 256 for i in range(payload_kb * 1024)))
    return b"RIFF" + struct.pack("<I", len(body)) + body


# --------------------------------------------------------------------------
# FAT32 structures
# --------------------------------------------------------------------------
def build_boot_sector() -> bytes:
    bs = bytearray(BYTES_PER_SECTOR)
    bs[0:3] = b"\xEB\x58\x90"
    bs[3:11] = b"MSWIN4.1"
    struct.pack_into("<H", bs, 11, BYTES_PER_SECTOR)
    bs[13] = SECTORS_PER_CLUSTER
    struct.pack_into("<H", bs, 14, RESERVED_SECTORS)
    bs[16] = NUM_FATS
    struct.pack_into("<H", bs, 19, 0)
    bs[21] = 0xF8
    struct.pack_into("<H", bs, 22, 0)
    struct.pack_into("<H", bs, 24, 63)
    struct.pack_into("<H", bs, 26, 255)
    struct.pack_into("<I", bs, 28, 0)
    struct.pack_into("<I", bs, 32, total_sectors)
    struct.pack_into("<I", bs, 36, FAT_SIZE_SECTORS)
    struct.pack_into("<I", bs, 44, ROOT_CLUSTER)
    struct.pack_into("<H", bs, 48, 1)
    struct.pack_into("<H", bs, 50, 6)
    bs[64] = 0x80
    bs[66] = 0x29
    struct.pack_into("<I", bs, 67, 0xC0FFEE01)
    bs[71:82] = b"MEDIATEST  "
    bs[82:90] = b"FAT32   "
    bs[510:512] = b"\x55\xAA"
    return bytes(bs)


def dir_entry(name8: str, ext3: str, attr: int, first_cluster: int, size: int) -> bytes:
    e = bytearray(32)
    e[0:11] = name8.ljust(8)[:8].encode("ascii") + ext3.ljust(3)[:3].encode("ascii")
    e[11] = attr
    struct.pack_into("<H", e, 14, 0x6000)   # ctime
    struct.pack_into("<H", e, 16, 0x5A21)   # cdate  (2025-01-01-ish)
    struct.pack_into("<H", e, 18, 0x5A21)   # adate
    struct.pack_into("<H", e, 20, (first_cluster >> 16) & 0xFFFF)
    struct.pack_into("<H", e, 22, 0x6000)   # wtime
    struct.pack_into("<H", e, 24, 0x5A21)   # wdate
    struct.pack_into("<H", e, 26, first_cluster & 0xFFFF)
    struct.pack_into("<I", e, 28, size)
    return bytes(e)


def main(out_path: str = "media_test.img", spec_fat32: bool = False) -> None:
    # Set every time, so a second call in the same process does not inherit
    # the first one's shape.
    use_geometry(*(SPEC_FAT32_GEOMETRY if spec_fat32 else DEFAULT_GEOMETRY))
    fat = [0] * (TOTAL_CLUSTERS + 2)
    fat[0], fat[1] = 0x0FFFFFF8, 0xFFFFFFFF
    cluster_data: dict = {}
    next_free = [3]  # cluster 2 is the root directory

    def alloc_chain(data: bytes) -> int:
        n = max(1, (len(data) + CLUSTER_SIZE - 1) // CLUSTER_SIZE)
        clusters = list(range(next_free[0], next_free[0] + n))
        next_free[0] += n
        for i, c in enumerate(clusters):
            cluster_data[c] = data[i * CLUSTER_SIZE:(i + 1) * CLUSTER_SIZE].ljust(
                CLUSTER_SIZE, b"\x00")
            fat[c] = clusters[i + 1] if i + 1 < len(clusters) else EOC
        return clusters[0]

    def free_chain(start: int) -> None:
        """Simulate deletion: free the FAT chain, leave the data in place."""
        c = start
        while True:
            nxt = fat[c]
            fat[c] = 0
            if nxt >= EOC or nxt == 0:
                break
            c = nxt

    def folder(entries: bytes) -> int:
        """A subfolder as FAT32 writes one: "." pointing at itself and ".."
        at the root (0) ahead of its entries. Recovery checks for them
        before believing a deleted folder's cluster is still that folder."""
        own = next_free[0]
        dots = (dir_entry(".", "", 0x10, own, 0)
                + dir_entry("..", "", 0x10, 0, 0))
        assert alloc_chain(dots + entries) == own
        return own

    live, deleted = [], []

    # --- DCIM/ : live photos ---------------------------------------------
    jpg = make_jpeg(); jpg_c = alloc_chain(jpg)
    png = make_png(); png_c = alloc_chain(png)
    gif = make_gif(); gif_c = alloc_chain(gif)
    dcim_entries = (dir_entry("IMG_0001", "JPG", 0x20, jpg_c, len(jpg))
                    + dir_entry("IMG_0002", "PNG", 0x20, png_c, len(png))
                    + dir_entry("ANIM", "GIF", 0x20, gif_c, len(gif)))
    dcim_c = folder(dcim_entries)
    live += [("DCIM/IMG_0001.JPG", len(jpg)), ("DCIM/IMG_0002.PNG", len(png)),
             ("DCIM/ANIM.GIF", len(gif))]

    # --- MOVIES/ : live video --------------------------------------------
    mp4 = make_mp4(); mp4_c = alloc_chain(mp4)
    avi = make_avi(); avi_c = alloc_chain(avi)
    movies_entries = (dir_entry("VID_0003", "MP4", 0x20, mp4_c, len(mp4))
                      + dir_entry("CLIP", "AVI", 0x20, avi_c, len(avi)))
    movies_c = folder(movies_entries)
    live += [("MOVIES/VID_0003.MP4", len(mp4)), ("MOVIES/CLIP.AVI", len(avi))]

    # --- a non-media file, which the extractor should ignore --------------
    notes = b"Chain of custody notes for the test volume.\n" * 8
    notes_c = alloc_chain(notes)

    # --- deleted media: no directory entry, FAT chain freed ---------------
    # (a) a file whose directory entry survives but whose FAT chain was
    #     freed -- recoverable by the file system engine, contiguously.
    del_jpg = make_jpeg(9); del_jpg_c = alloc_chain(del_jpg); free_chain(del_jpg_c)

    # (b) files with no directory entry at all -- carver only.
    del_mov = make_mov(); del_mov_c = alloc_chain(del_mov); free_chain(del_mov_c)
    del_png = make_png(96, 72); del_png_c = alloc_chain(del_png); free_chain(del_png_c)
    deleted += [("JPG (entry present, chain freed)", len(del_jpg), del_jpg_c),
                ("MOV (unreferenced)", len(del_mov), del_mov_c),
                ("PNG (unreferenced)", len(del_png), del_png_c)]

    deleted_entry = bytearray(dir_entry("IMG_0009", "JPG", 0x20, del_jpg_c, len(del_jpg)))
    deleted_entry[0] = 0xE5  # the byte FAT32 writes over a deleted name

    root = (bytes(deleted_entry)
            + dir_entry("DCIM", "", 0x10, dcim_c, 0)
            + dir_entry("MOVIES", "", 0x10, movies_c, 0)
            + dir_entry("NOTES", "TXT", 0x20, notes_c, len(notes)))
    cluster_data[ROOT_CLUSTER] = root.ljust(CLUSTER_SIZE, b"\x00")
    fat[ROOT_CLUSTER] = EOC

    with open(out_path, "wb") as f:
        boot = build_boot_sector()
        f.write(boot)
        if spec_fat32:
            # FSInfo at 1, the backup boot sector at 6 and its FSInfo at 7,
            # where the boot sector says they are. The default image leaves
            # them zero, and its bytes are kept as they were.
            used = sum(1 for value in fat[2:] if value)
            info = build_fsinfo(TOTAL_CLUSTERS - used, next_free[0])
            reserved = [b"\x00" * BYTES_PER_SECTOR] * (RESERVED_SECTORS - 1)
            reserved[0], reserved[5], reserved[6] = info, boot, info
            f.write(b"".join(reserved))
        else:
            f.write(b"\x00" * BYTES_PER_SECTOR * (RESERVED_SECTORS - 1))
        fat_bytes = bytearray(FAT_SIZE_SECTORS * BYTES_PER_SECTOR)
        for i, val in enumerate(fat):
            struct.pack_into("<I", fat_bytes, i * 4, val & 0x0FFFFFFF)
        f.write(fat_bytes)
        f.write(fat_bytes)
        for c in range(2, TOTAL_CLUSTERS + 2):
            f.write(cluster_data.get(c, b"\x00" * CLUSTER_SIZE))

    print(f"Wrote {out_path} ({os.path.getsize(out_path)} bytes, "
          f"label MEDIATEST, {TOTAL_CLUSTERS:,} clusters: "
          f"{fat_type_by_specification()} by the FAT specification)")
    print("Live media (file system engine should find these):")
    for path, size in live:
        print(f"  {path:24} {size:>8} bytes")
    print("Deleted media:")
    for name, size, cluster in deleted:
        print(f"  {name:24} {size:>8} bytes at cluster {cluster}")
    print("Plus NOTES.TXT, which is not media and should be skipped.")


if __name__ == "__main__":
    import argparse

    # Arguments used to be ignored: tools/verify.py passed --out, the image
    # was written to media_test.img in the working directory anyway, and
    # verify picked up whatever .img it found there.
    parser = argparse.ArgumentParser(description="Build the FAT test volume.")
    parser.add_argument("--out", default="media_test.img",
                        help="where to write it (default: media_test.img here)")
    parser.add_argument("--spec-fat32", action="store_true",
                        help="FAT32 by the specification (66,000 clusters, "
                             "about 33 MB), which The Sleuth Kit will open")
    options = parser.parse_args()
    main(options.out, spec_fat32=options.spec_fat32)
