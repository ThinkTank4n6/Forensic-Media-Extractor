"""A deleted FAT32 file whose first cluster lost its high half is not
recovered from the wrong cluster (v2026.11.338).

FAT32 keeps a file's first cluster in two 16-bit halves of its directory
entry, and Windows clears the high half when it deletes the file. On a
volume of 0x10000 clusters or more, the low half alone then names several
clusters. FME read from the first of them, and when that cluster happened to
be free it stored another file's leftover bytes under the deleted file's
name, with its dates -- and a hash of content that was never its own. The
other tool's qnxprobe 1.88 refuses such an entry unless the low half can mean
only one cluster; FME now does the same.

The volume here has 0x10000 + 2,000 clusters:

  * LOSTHI.JPG was at cluster 0x10000 + 100; its entry says 100, the high
    half cleared. Cluster 100 holds a different picture, deleted, with no
    entry of its own -- the trap. LOSTHI must not come back with that
    picture's bytes under its name;
  * KEPTHI.JPG was at cluster 0x10000 + 400 and its entry kept the high
    half: it comes back, under its name, with its own bytes;
  * every deleted file the pass recovers says its content is unverified.
"""
import _sandbox  # noqa: F401
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["FME_NO_TSK"] = "1"         # this pass, not TSK's walk

import make_media_test_image as fx  # noqa: E402
from fme.engines import media_core  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "fat32_high_word-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

fx.use_geometry(1, 0x10000 + 2000)
CS = fx.CLUSTER_SIZE
fat = [0] * (fx.TOTAL_CLUSTERS + 2)
fat[0], fat[1] = 0x0FFFFFF8, 0xFFFFFFFF
data: dict = {}


def place(at: int, body: bytes, keep: bool) -> None:
    """Write body from cluster `at` on; its chain is freed unless kept."""
    n = -(-len(body) // CS)
    for i in range(n):
        data[at + i] = body[i * CS:(i + 1) * CS].ljust(CS, b"\0")
        fat[at + i] = (at + i + 1 if i + 1 < n else fx.EOC) if keep else 0


lost = fx.make_jpeg(30)
trap = fx.make_jpeg(31)         # a different picture, where the low half points
kept = fx.make_jpeg(32)
LOST_AT, KEPT_AT = 0x10000 + 100, 0x10000 + 400
place(LOST_AT, lost, keep=False)
place(100, trap, keep=False)
place(KEPT_AT, kept, keep=False)


def deleted_entry(name, ext, first, size):
    raw = bytearray(fx.dir_entry(name, ext, 0x20, first, size))
    raw[0] = 0xE5
    return bytes(raw)


lost_entry = bytearray(deleted_entry("LOSTHI", "JPG", LOST_AT, len(lost)))
struct.pack_into("<H", lost_entry, 20, 0)          # the high half, cleared
root = (bytes(lost_entry)
        + deleted_entry("KEPTHI", "JPG", KEPT_AT, len(kept)))
data[fx.ROOT_CLUSTER] = root.ljust(CS, b"\0")
fat[fx.ROOT_CLUSTER] = fx.EOC

image = os.path.join(ROOT, "card.img")
with open(image, "wb") as fh:
    boot = fx.build_boot_sector()
    fh.write(boot)
    used = sum(1 for v in fat[2:] if v)
    info = fx.build_fsinfo(fx.TOTAL_CLUSTERS - used, 3)
    reserved = [b"\0" * fx.BYTES_PER_SECTOR] * (fx.RESERVED_SECTORS - 1)
    reserved[0], reserved[5], reserved[6] = info, boot, info
    fh.write(b"".join(reserved))
    table = bytearray(fx.FAT_SIZE_SECTORS * fx.BYTES_PER_SECTOR)
    for i, v in enumerate(fat):
        struct.pack_into("<I", table, i * 4, v & 0x0FFFFFFF)
    fh.write(table)
    fh.write(table)
    for c in range(2, fx.TOTAL_CLUSTERS + 2):
        fh.write(data.get(c, b"\0" * CS))

out = os.path.join(ROOT, "case")
media_core.run_extraction(image, out)
with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
    rows = json.load(fh)["files"]
named = {r.get("source_path", "").rsplit("/", 1)[-1]: r for r in rows
         if r.get("engine") in ("fat-deleted", "filesystem")}
sha = {name: hashlib.sha256(body).hexdigest()
       for name, body in (("lost", lost), ("trap", trap), ("kept", kept))}

print(f"A FAT32 volume of {fx.TOTAL_CLUSTERS:,} clusters")
under_lost = [r for n, r in named.items() if n.endswith("OSTHI.JPG")]
check("the file whose entry lost its high half is not recovered from cluster 100",
      not any(r["sha256"] == sha["trap"] for r in under_lost),
      [(r["source_path"], r["sha256"][:12]) for r in under_lost])
check("nor anything else under its name", not under_lost)
kept_rows = [r for n, r in named.items() if n.endswith("EPTHI.JPG")]
check("the file whose entry kept it comes back, under its name",
      len(kept_rows) == 1, [r.get("source_path") for r in kept_rows])
check("with its own bytes", kept_rows and kept_rows[0]["sha256"] == sha["kept"])
check("and says its content is unverified",
      kept_rows and "Content unverified" in (kept_rows[0].get("note") or ""),
      kept_rows and kept_rows[0].get("note", "")[:200])
carved = {r["sha256"] for r in rows if r.get("engine") == "carve"}
check("the trap picture is still found, by the carve, with no name",
      sha["trap"] in carved)
check("and the lost file's own picture too: refused by name, not lost",
      sha["lost"] in carved)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
