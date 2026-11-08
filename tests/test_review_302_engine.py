"""Evidence-integrity defects found by the review of v2026.11.302.

Each check here failed on v2026.11.302:

- a parallel resume at a stripe boundary lost a file that starts just
  before it with its header just after it (WAV header at +8);
- a rebuilt manifest called every file deleted ("False" is truthy), kept a
  torn row with no hash, and dropped duplicates.csv;
- a re-export to C4All wrote through a hard link into the case's own file;
- an NTFS run that will not read put every later byte at the wrong offset;
- a sparse stretch of $MFT renumbered every record after it;
- a FAT chain through the FAT's padding read into the next partition.
"""
import contextlib
import io
import json
import logging
import os
import random
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

from fme.engines import fat32_core, media_core as mc, ntfs_mft  # noqa: E402
from fme.exports import c4all_export  # noqa: E402
from fme.review import recover  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "review302_engine-") + str(os.getpid())


def check(label, good, detail=""):
    print(f"{label}: {'ok' if good else 'WRONG'} {detail}".rstrip())
    return bool(good)


def wav(size, rng):
    body = b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
    data = bytes(rng.getrandbits(8) for _ in range(size - len(body) - 16))
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def resume_straddle():
    total, boundary = 8 << 20, 2 << 20
    blob = bytearray(b"\x5a" * total)
    w = wav(60000, random.Random(1))
    blob[boundary - 4:boundary - 4 + len(w)] = w
    img = os.path.join(ROOT, "x.dd")
    with open(img, "wb") as fh:
        fh.write(blob)
    good = True
    for workers in (2, 1):
        d = os.path.join(ROOT, f"resume{workers}")
        os.makedirs(d)
        with open(os.path.join(d, "run_state.json"), "w") as fh:
            json.dump({"phase": "carve", "offset": boundary, "total": total}, fh)
        r = mc.run_extraction(img, d, mode="carve", workers=workers,
                              chunk_size="256K", progress=False, resume=True)
        got = sorted(f["offset"] for f in r["files"])
        good &= check(f"resume at a stripe boundary, {workers} worker(s), finds "
                      f"the straddling WAV", got == [boundary - 4], str(got))
    return good


def rebuild_types():
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image as fx
    root = os.path.join(ROOT, "rebuild")
    out = mc.OutputManager(root, metadata=False)
    a, b = fx.make_jpeg(1), fx.make_jpeg(2)
    for data, off in ((a, 100), (a, 900), (b, 5000)):
        out.store(iter([data]), category="image", ext="jpg",
                  stem=f"recovered_{off:04d}_{off}", engine="carve",
                  offset=off, bounded="parsed")
    out._csv_fh.flush()
    out._dup_csv_fh.flush()
    path = os.path.join(root, "manifest.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        text = fh.read()
    last = text.rstrip("\r\n").rfind("\n") + 1
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(text[:last + 60])      # a crash tore the last row
    out._csv_fh.close()
    out._dup_csv_fh.close()
    # Since v2026.11.306 manifest.db is the record and holds the row whole;
    # the CSV-only rebuild this checks is that of a case from before it.
    import shutil as _shutil
    current = root + "-current"
    _shutil.copytree(root, current)
    out._db.close()
    os.remove(os.path.join(root, "manifest.db"))
    recover.rebuild_manifest(root)
    with open(os.path.join(root, "manifest.json"), encoding="utf-8") as fh:
        m = json.load(fh)
    files = m["files"]
    recover.rebuild_manifest(current)
    with open(os.path.join(current, "manifest.json"), encoding="utf-8") as fh:
        whole = json.load(fh)["files"]
    good = check("rebuilt manifest: no file marked deleted",
                 all(f["deleted"] is False for f in files))
    good &= check("rebuilt manifest: sizes are numbers",
                  all(isinstance(f["size"], int) for f in files))
    good &= check("rebuilt manifest: the torn row is not listed",
                  len(files) == 1 and all(f["sha256"] for f in files))
    good &= check("rebuilt manifest: duplicates kept",
                  len(m.get("duplicates", [])) == 1)
    good &= check("rebuilt from manifest.db: the row the CSV tore is whole",
                  len(whole) == 2 and all(f["sha256"] for f in whole))
    return good


def c4all_reexport():
    case = os.path.join(ROOT, "case")
    os.makedirs(os.path.join(case, "images"))
    recs = []
    for name, body in (("a.jpg", b"A" * 64), ("b.jpg", b"B" * 64)):
        with open(os.path.join(case, "images", name), "wb") as fh:
            fh.write(body)
        recs.append({"out_path": f"images/{name}", "category": "image",
                     "ext": "jpg", "size": 64, "md5": name, "sha256": name})
    man = os.path.join(case, "manifest.json")
    out = os.path.join(ROOT, "c4all")
    for order in (recs, recs[::-1]):
        with open(man, "w") as fh:
            json.dump({"summary": {}, "files": order}, fh)
        c4all_export.export(man, out, case="C1")
    with open(os.path.join(case, "images", "a.jpg"), "rb") as fh:
        a = fh.read()
    return check("a second C4All export leaves the case's files alone",
                 a == b"A" * 64, repr(a[:8]))


def ntfs_runs():
    image = bytearray(4096 * 4)
    image[4096:8192] = b"B" * 4096
    image[12288:16384] = b"C" * 4096

    def read(off, n):
        return bytes(image[off:off + n]) if 0 <= off < len(image) else b""

    boot = {"cluster_size": 4096}
    rec = {"resident": None, "size_from": "data", "size": 12288,
           "initialized": None, "runs": [(1, 1), (-5, 1), (3, 1)]}
    data, _ = ntfs_mft.content(rec, read, boot)
    good = check("NTFS: a run that will not read ends the file there",
                 data == b"B" * 4096 and rec["short_by"] == 8192,
                 f"({len(data)} bytes, short by {rec['short_by']})")

    size = 1024
    table = bytearray(4096 * 4)
    for n in range(16):
        struct.pack_into("<4s", table, n * size, b"FILE")
    real = ntfs_mft._mft_runs
    ntfs_mft._mft_runs = lambda read, boot: [(0, 1), (None, 1), (2, 1)]
    numbers = []
    real_parse = ntfs_mft.parse_record
    ntfs_mft.parse_record = lambda raw, bps, n: numbers.append(n) or {"n": n}
    try:
        list(ntfs_mft.records(lambda o, n: bytes(table[o:o + n]),
                              {"record_size": size, "cluster_size": 4096,
                               "bytes_per_sector": 512}))
    finally:
        ntfs_mft._mft_runs, ntfs_mft.parse_record = real, real_parse
    good &= check("NTFS: a sparse stretch of $MFT keeps record numbers",
                  numbers == [0, 1, 2, 3, 8, 9, 10, 11], str(numbers))
    return good


def fat_chain():
    bs = fat32_core.BootSector(512, 1, 1, 1, 10, 1, 2, "", "FAT32")
    table = fat32_core.FatTable.__new__(fat32_core.FatTable)
    table.bs = bs
    table.entries = [0] * 128
    table.entries[2], table.entries[3], table.entries[40] = 3, 40, 0x0FFFFFFF
    chain = list(table.chain(2))
    return check("FAT: a chain stops at the volume's last cluster",
                 chain == [2, 3], str(chain))


def main():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT)
    ok = True
    for step in (resume_straddle, rebuild_types, c4all_reexport, ntfs_runs,
                 fat_chain):
        ok &= step()
    shutil.rmtree(ROOT, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
