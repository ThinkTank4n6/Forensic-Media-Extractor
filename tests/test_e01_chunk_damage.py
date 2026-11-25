"""A file read out of an E01 through a chunk that fails its stored checksum
says so (v2026.11.338).

An E01 keeps an Adler-32 for every chunk it stores, and the vendored reader
checks each one as it decodes it -- and kept the failures to itself: a
picture read through a damaged chunk was stored as whole and sound, its
hashes those of bytes the acquisition does not vouch for. The other tool
began refusing such files in its 2026.5.8 release. FME keeps them -- the
carve would find the bytes again with no name -- and says so in the file's
note, "DAMAGED SOURCE", with which chunk and where.

  * one byte changed inside IMG_0001.JPG's stored chunk, its checksum left
    as written: that picture's note says DAMAGED SOURCE, naming the chunk;
  * the volume's other pictures, in sound chunks, say nothing of it;
  * the run counts the file.
"""
import _sandbox  # noqa: F401
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import ewfwriter  # noqa: E402
import make_media_test_image as fx  # noqa: E402
from fme.engines import media_core  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "e01_chunk_damage-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
volume_path = os.path.join(ROOT, "volume.img")
fx.main(volume_path)
with open(volume_path, "rb") as fh:
    volume = fh.read()
picture = fx.make_jpeg()                       # IMG_0001.JPG, the first live file


def acquire(folder, damage: bool) -> str:
    os.makedirs(folder)
    path = ewfwriter.write_ewf(folder, "card", volume, chunk_size=4096, compress=False)[0]
    if damage:
        with open(path, "rb") as fh:
            raw = bytearray(fh.read())
        at = raw.find(picture[64:96])
        assert at > 0, "the picture's bytes are not in the E01 as stored"
        raw[at] ^= 0xFF
        with open(path, "wb") as fh:
            fh.write(raw)
    return str(path)


def run(source, out):
    summary = media_core.run_extraction(source, out, mode="fs")
    with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
        rows = json.load(fh)["files"]
    return summary, {r.get("source_path", "").rsplit("/", 1)[-1]: r for r in rows
                     if r.get("engine") == "filesystem"}


print("A sound E01")
_, sound = run(acquire(os.path.join(ROOT, "sound"), False), os.path.join(ROOT, "case1"))
check("its pictures are read", "IMG_0001.JPG" in sound and len(sound) >= 3, sorted(sound))
check("and none says DAMAGED SOURCE",
      not any("DAMAGED SOURCE" in (r.get("note") or "") for r in sound.values()))

print("The same E01, one byte changed inside IMG_0001.JPG's chunk")
summary, damaged = run(acquire(os.path.join(ROOT, "damaged"), True),
                       os.path.join(ROOT, "case2"))
note = (damaged.get("IMG_0001.JPG") or {}).get("note") or ""
check("the picture read through it says DAMAGED SOURCE", "DAMAGED SOURCE" in note, note[:220])
check("naming the chunk that fails its checksum", "fail their stored checksum" in note)
others = {n: r for n, r in damaged.items() if n != "IMG_0001.JPG"}
check("the pictures in sound chunks say nothing of it",
      others and not any("DAMAGED SOURCE" in (r.get("note") or "") for r in others.values()),
      [n for n, r in others.items() if "DAMAGED SOURCE" in (r.get("note") or "")])
stats = (summary or {}).get("stats") or summary or {}
check("the run counts it", (stats.get("damaged_source_files") or 0) >= 1,
      stats.get("damaged_source_files"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
