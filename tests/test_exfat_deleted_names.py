"""A deleted exFAT file comes back under the name it was deleted with.

exFAT keeps a deleted file's whole entry set -- the name across as many
15-character name entries as it needs, the first cluster, the length.
The vendored reader could list and read them; nothing in the engine asked it.
Without pytsk3, deleted exFAT files came back only from carving, unnamed.
Found by test_known_answer.py on the lab's WSL machine (v2026.11.288): "a
deleted file with a long name keeps its name" failed while its bytes matched.

The volume is built here from the exFAT specification, so the check does not
depend on mkfs.exfat or a mount: one live picture, two deleted in the root
(one with a 52-character name, four name entries), one deleted in a folder.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_exfat_names-") + str(os.getpid())
SEC, CLUSTERS, HEAP, FAT_AT = 512, 400, 16, 4


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def entry_set(name, first, size, *, is_dir=False, in_use=True):
    """A File entry, a Stream Extension and ceil(len/15) File Name entries."""
    names = [name[i:i + 15] for i in range(0, len(name), 15)] or [""]
    ent = bytearray(32 * (2 + len(names)))
    ent[0], ent[1] = 0x85, 1 + len(names)              # secondary count
    ent[4] = 0x10 if is_dir else 0x20
    struct.pack_into("<I", ent, 12, (22209 << 16) | 24576)
    ent[32], ent[33], ent[35] = 0xC0, 0x03, len(name)  # NoFatChain, name length
    struct.pack_into("<Q", ent, 40, size)
    struct.pack_into("<I", ent, 52, first)
    struct.pack_into("<Q", ent, 56, size)
    for k, part in enumerate(names):
        at = 64 + 32 * k
        ent[at] = 0xC1
        ent[at + 2:at + 2 + 2 * len(part)] = part.encode("utf-16-le")
    total = 0                                           # the specification's sum
    for i, byte in enumerate(ent):
        if i not in (2, 3):
            total = ((0x8000 if total & 1 else 0) + (total >> 1) + byte) & 0xFFFF
    struct.pack_into("<H", ent, 2, total)
    if not in_use:                                      # what a delete clears
        for at in range(0, len(ent), 32):
            ent[at] &= 0x7F
    return bytes(ent)


def build():
    buf = bytearray((HEAP + CLUSTERS) * SEC)
    buf[0:3], buf[3:11] = b"\xEB\x76\x90", b"EXFAT   "
    struct.pack_into("<Q", buf, 72, len(buf) // SEC)
    struct.pack_into("<I", buf, 80, FAT_AT)
    struct.pack_into("<I", buf, 84, 8)
    struct.pack_into("<I", buf, 88, HEAP)
    struct.pack_into("<I", buf, 92, CLUSTERS)
    struct.pack_into("<I", buf, 96, 3)                  # root at cluster 3
    buf[108], buf[109], buf[110] = 9, 0, 1              # 512-byte sectors, 1/cluster
    buf[510:512] = b"\x55\xaa"
    at = lambda c: (HEAP + c - 2) * SEC
    used = {2, 3, 4, 5, 6, 7, 8}

    def put(blob, first):
        buf[at(first):at(first) + len(blob)] = blob
        return (len(blob) + SEC - 1) // SEC

    pictures, intended, nxt = {}, [], 10
    plan = [("kept-live.jpg", "/", True), ("gone.jpg", "/", False),
            ("a_rather_long_file_name_for_this_deleted_picture.jpg", "/", False),
            ("inside-folder.jpg", "/DCIM", False)]
    # The root runs over four clusters, chained in the FAT (3 -> 6 -> 7 -> 8):
    # an entry set may cross from one to the next, as on a real volume.
    ROOT_CHAIN = [3, 6, 7, 8]
    root, folder = bytearray(SEC * len(ROOT_CHAIN)), bytearray(SEC)
    root[0] = 0x81                                      # allocation bitmap
    struct.pack_into("<I", root, 20, 2)
    struct.pack_into("<Q", root, 24, (CLUSTERS + 7) // 8)
    root[32] = 0x82                                     # up-case table
    struct.pack_into("<I", root, 52, 4)
    struct.pack_into("<Q", root, 56, SEC)
    rpos, fpos = 64, 0
    dcim = entry_set("DCIM", 5, SEC, is_dir=True)
    root[rpos:rpos + len(dcim)] = dcim
    rpos += len(dcim)
    for seed, (name, where, live) in enumerate(plan):
        blob = fixtures.make_jpeg(40 + seed)
        n = put(blob, nxt)
        if live:
            used.update(range(nxt, nxt + n))
        es = entry_set(name, nxt, len(blob), in_use=live)
        if where == "/":
            root[rpos:rpos + len(es)] = es
            rpos += len(es)
        else:
            folder[fpos:fpos + len(es)] = es
            fpos += len(es)
        intended.append((name, where, live, hashlib.sha256(blob).hexdigest()))
        nxt += n + 3
    assert rpos <= len(root) and fpos <= len(folder), "a directory overflowed"
    for k, clus in enumerate(ROOT_CHAIN):
        buf[at(clus):at(clus) + SEC] = root[k * SEC:(k + 1) * SEC]
    buf[at(5):at(5) + SEC] = folder
    for c in used:                                      # bitmap and FAT ends
        buf[at(2) + ((c - 2) >> 3)] |= 1 << ((c - 2) & 7)
        struct.pack_into("<I", buf, FAT_AT * SEC + c * 4, 0xFFFFFFFF)
    for a, b in zip(ROOT_CHAIN, ROOT_CHAIN[1:]):
        struct.pack_into("<I", buf, FAT_AT * SEC + a * 4, b)
    return bytes(buf), intended


shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
image, intended = build()
path = os.path.join(ROOT, "exfat.img")
with open(path, "wb") as fh:
    fh.write(image)

out = os.path.join(ROOT, "out")
finished = subprocess.run(
    [sys.executable, "-m", "fme", "extract", "-i", path, "-o", out, "--mode", "both"],
    cwd=PROJECT, capture_output=True, text=True, errors="replace",
    env=dict(os.environ, FME_NO_TSK="1"))
manifest = os.path.join(out, "manifest.json")
files = json.load(open(manifest, encoding="utf-8"))["files"] if os.path.isfile(manifest) else []
by_hash = {f["sha256"]: f for f in files}
check("the run finishes", finished.returncode == 0, f"exit {finished.returncode}")
for name, where, live, digest in intended:
    rec = by_hash.get(digest)
    state = "live" if live else "deleted"
    check(f"{name} ({state}) comes back byte-for-byte", rec is not None)
    if rec is None or live:
        continue
    want = f"{where.rstrip('/')}/{name}"
    check(f"  under its whole name and folder", rec.get("source_path") == want,
          repr(rec.get("source_path")))
    check(f"  marked deleted, from its entry, not carved",
          rec.get("deleted") and rec.get("engine") == "exfat-deleted", rec.get("engine"))

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
