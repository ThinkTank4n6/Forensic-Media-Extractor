"""
make_test_image.py — build a minimal but structurally valid FAT32 image
for testing fat32_core.py / app.py. Not part of the shipped tool.
"""
import struct
import os

BYTES_PER_SECTOR = 512
SECTORS_PER_CLUSTER = 1
RESERVED_SECTORS = 32
NUM_FATS = 2
TOTAL_CLUSTERS = 2000  # keep small
FAT_SIZE_SECTORS = ((TOTAL_CLUSTERS + 2) * 4 + BYTES_PER_SECTOR - 1) // BYTES_PER_SECTOR
ROOT_CLUSTER = 2

first_data_sector = RESERVED_SECTORS + NUM_FATS * FAT_SIZE_SECTORS
total_sectors = first_data_sector + TOTAL_CLUSTERS * SECTORS_PER_CLUSTER

def build_boot_sector():
    bs = bytearray(BYTES_PER_SECTOR)
    bs[0:3] = b"\xEB\x58\x90"
    bs[3:11] = b"MSWIN4.1"
    struct.pack_into("<H", bs, 11, BYTES_PER_SECTOR)
    bs[13] = SECTORS_PER_CLUSTER
    struct.pack_into("<H", bs, 14, RESERVED_SECTORS)
    bs[16] = NUM_FATS
    struct.pack_into("<H", bs, 19, 0)  # total_sectors_16 = 0 -> use 32-bit field
    bs[21] = 0xF8
    struct.pack_into("<H", bs, 22, 0)  # FATSz16 = 0 (FAT32)
    struct.pack_into("<H", bs, 24, 63)  # sectors per track
    struct.pack_into("<H", bs, 26, 255)  # heads
    struct.pack_into("<I", bs, 28, 0)   # hidden sectors
    struct.pack_into("<I", bs, 32, total_sectors)
    struct.pack_into("<I", bs, 36, FAT_SIZE_SECTORS)  # FATSz32
    struct.pack_into("<H", bs, 40, 0)   # ext flags
    struct.pack_into("<H", bs, 42, 0)   # fs version
    struct.pack_into("<I", bs, 44, ROOT_CLUSTER)
    struct.pack_into("<H", bs, 48, 1)   # FSInfo sector
    struct.pack_into("<H", bs, 50, 6)   # backup boot sector
    bs[64] = 0x80  # drive number
    bs[66] = 0x29  # boot signature
    struct.pack_into("<I", bs, 67, 0x12345678)  # volume serial
    bs[71:82] = b"TESTVOL    "
    bs[82:90] = b"FAT32   "
    bs[510:512] = b"\x55\xAA"
    return bytes(bs)


def make_short_name(name8, ext3):
    return name8.ljust(8)[:8].encode("ascii") + ext3.ljust(3)[:3].encode("ascii")


def dir_entry(name8, ext3, attr, first_cluster, size):
    e = bytearray(32)
    e[0:11] = make_short_name(name8, ext3)
    e[11] = attr
    e[13] = 0
    struct.pack_into("<H", e, 14, 0)  # ctime
    struct.pack_into("<H", e, 16, 0)  # cdate
    struct.pack_into("<H", e, 18, 0)  # adate
    struct.pack_into("<H", e, 20, (first_cluster >> 16) & 0xFFFF)
    struct.pack_into("<H", e, 22, 0)  # wtime
    struct.pack_into("<H", e, 24, 0)  # wdate
    struct.pack_into("<H", e, 26, first_cluster & 0xFFFF)
    struct.pack_into("<I", e, 28, size)
    return bytes(e)


def main():
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_image.img")

    fat = [0] * (TOTAL_CLUSTERS + 2)
    fat[0] = 0x0FFFFFF8
    fat[1] = 0x0FFFFFFF

    cluster_data = {}  # cluster_num -> bytes (exactly one cluster's worth)
    next_free_cluster = 3  # cluster 2 is root

    def alloc_chain(data: bytes):
        """Write `data` across as many clusters as needed, chain them in FAT,
        return the first cluster number."""
        nonlocal next_free_cluster
        cluster_size = BYTES_PER_SECTOR * SECTORS_PER_CLUSTER
        chunks = [data[i:i + cluster_size] for i in range(0, len(data), cluster_size)] or [b""]
        clusters = []
        for chunk in chunks:
            c = next_free_cluster
            next_free_cluster += 1
            cluster_data[c] = chunk.ljust(cluster_size, b"\x00")
            clusters.append(c)
        for i, c in enumerate(clusters):
            if i + 1 < len(clusters):
                fat[c] = clusters[i + 1]
            else:
                fat[c] = 0x0FFFFFF8
        return clusters[0]

    # --- files ---
    hello_data = b"Hello from a recovered FAT32 file!\nThis is REPORT.TXT.\n" * 5
    hello_cluster = alloc_chain(hello_data)

    readme_data = b"README for the test volume.\n" * 3
    readme_cluster = alloc_chain(readme_data)

    # A subdirectory DOCS/ containing NOTES.TXT
    notes_data = b"Notes inside a subdirectory.\n" * 4
    notes_cluster = alloc_chain(notes_data)

    docs_dir_entries = bytearray()
    docs_dir_entries += dir_entry(".", "", 0x10, 0, 0)  # placeholder, not emitted by parser (name "." skipped)
    docs_dir_entries += dir_entry("NOTES", "TXT", 0x20, notes_cluster, len(notes_data))
    docs_cluster = alloc_chain(bytes(docs_dir_entries))

    # A small valid JPEG (SOI...EOI) that we will "delete" by not referencing
    # it in any directory, so the carver has to find it by signature alone.
    jpeg_bytes = (
        b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\x00" * 40
        + b"FAKEJPEGDATA-CARVE-ME" * 3
        + b"\xFF\xD9"
    )
    deleted_jpeg_cluster = alloc_chain(jpeg_bytes)
    # Simulate "deletion": mark these clusters as FREE in the FAT so the
    # carver treats them as unallocated, but leave the bytes on disk (this
    # mirrors what actually happens when a file is deleted on FAT32).
    c = deleted_jpeg_cluster
    while True:
        nxt = fat[c]
        fat[c] = 0
        if nxt >= 0x0FFFFFF8:
            break
        c = nxt

    # --- root directory ---
    root_entries = bytearray()
    root_entries += dir_entry("REPORT", "TXT", 0x20, hello_cluster, len(hello_data))
    root_entries += dir_entry("README", "TXT", 0x20, readme_cluster, len(readme_data))
    root_entries += dir_entry("DOCS", "", 0x10, docs_cluster, 0)
    cluster_data[ROOT_CLUSTER] = bytes(root_entries).ljust(BYTES_PER_SECTOR * SECTORS_PER_CLUSTER, b"\x00")
    fat[ROOT_CLUSTER] = 0x0FFFFFF8

    # --- assemble image ---
    with open(out_path, "wb") as f:
        # reserved area
        f.write(build_boot_sector())
        f.write(b"\x00" * BYTES_PER_SECTOR * (RESERVED_SECTORS - 1))

        # FAT #1 and FAT #2 (identical)
        fat_bytes = bytearray(FAT_SIZE_SECTORS * BYTES_PER_SECTOR)
        for i, val in enumerate(fat):
            struct.pack_into("<I", fat_bytes, i * 4, val & 0x0FFFFFFF)
        f.write(fat_bytes)
        f.write(fat_bytes)

        # data area
        cluster_size = BYTES_PER_SECTOR * SECTORS_PER_CLUSTER
        for c in range(2, TOTAL_CLUSTERS + 2):
            f.write(cluster_data.get(c, b"\x00" * cluster_size))

    print(f"Wrote {out_path} ({os.path.getsize(out_path)} bytes)")
    print(f"Deleted/unreferenced JPEG hidden at cluster {deleted_jpeg_cluster} for carve testing")


if __name__ == "__main__":
    main()
