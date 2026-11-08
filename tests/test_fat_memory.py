"""The FAT is held at its own size, and a file is saved without holding it.

The table was a tuple of Python integers, about nine times its size on
disk: 137 MiB for four million FAT32 entries, over 2 GiB for a 256 GB card.
And extract_file read a whole file into memory before writing it.
"""
import os
import struct
import sys
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.engines import fat32_core as fat  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


print("four million FAT32 entries")
count = 4_000_000
raw = struct.pack("<%dI" % count, *range(count))
tracemalloc.start()
entries = fat._fat_entries(raw, "FAT32")
used = tracemalloc.get_traced_memory()[0]
tracemalloc.stop()
check("take about the FAT's own size (under 24 MiB, was 137)",
      used < 24 * 1024 * 1024, f"{used / 2 ** 20:.1f} MiB")
check("and read back as written", len(entries) == count
      and entries[0] == 0 and entries[count - 1] == count - 1)

print("\nFAT16 and FAT12 markers are widened to FAT32's")
raw16 = struct.pack("<6H", 0, 0xFFF8, 3, 0xFFF7, 0, 5)
check("FAT16", list(fat._fat_entries(raw16, "FAT16"))
      == [0, fat.FAT32_EOC_MIN, 3, fat.FAT32_BAD_CLUSTER, 0, 5])
raw12 = bytes([0xF8, 0xFF, 0xFF, 0x03, 0x40, 0x00])
check("FAT12", list(fat._fat_entries(raw12, "FAT12"))
      == [fat.FAT32_EOC_MIN, fat.FAT32_EOC_MIN, 3, 4])

print("\nextract_file writes cluster by cluster")
with open(fat.__file__, encoding="utf-8") as fh:
    source = fh.read()
body = source[source.index("    def extract_file("):]
body = body[:body.index("\n    def ", 10)]
check("it no longer reads the whole chain first",
      "read_chain_bytes" not in body and "self.fat.chain" in body)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
