"""App caches: a video in pieces, and a Snapchat content bundle.

ExoPlayer does not store a cached video as a file. It cuts it into pieces
named for where each starts, scatters them across ten subfolders, and keeps
the name in a separate index. Carving finds the first piece and nothing else,
because every later piece begins mid-stream.
"""
import tempfile
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_media_test_image as g
from fme.engines import appcache

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "appcache_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
CACHE = os.path.join(ROOT, "cache", "exoplayer")
for sub in "0123456789":
    os.makedirs(os.path.join(CACHE, sub))

ok = True
whole = g.make_mp4(4000)
partial_source = g.make_mp4(8000)

# One video the user watched to the end, in three pieces.
piece_size = len(whole) // 3 + 1
position = 0
for index in range(3):
    chunk = whole[position:position + piece_size]
    name = f"12.{position}.{1700000000000 + index}.v3.exo"
    with open(os.path.join(CACHE, str(index % 10), name), "wb") as fh:
        fh.write(chunk)
    position += len(chunk)

# One they abandoned, leaving a hole in the middle.
with open(os.path.join(CACHE, "3", "77.0.1700000000000.v3.exo"), "wb") as fh:
    fh.write(partial_source[:20000])
with open(os.path.join(CACHE, "4", "77.60000.1700000000001.v3.exo"), "wb") as fh:
    fh.write(partial_source[60000:])

con = sqlite3.connect(os.path.join(CACHE, "exoplayer_internal.db"))
con.execute("CREATE TABLE ExoPlayerCacheIndexabc (id INTEGER, key TEXT, metadata BLOB)")
con.executemany("INSERT INTO ExoPlayerCacheIndexabc VALUES (?,?,NULL)",
                [(12, "https://cdn.example.test/stories/8821.mp4"),
                 (77, "https://cdn.example.test/reels/4410.mp4")])
con.commit()
con.close()

items = appcache.find_exo_pieces(CACHE)
print(f"found {len(items)} item(s) in "
      f"{sum(len(i.pieces) for i in items.values())} pieces")
ok &= len(items) == 2

rejoined = {info["name"]: (payload, info)
            for name, payload, info in appcache.rejoin_folder(CACHE)}
print("\nrejoined:")
for name, (payload, info) in rejoined.items():
    print(f"  {name}")
    print(f"    {info['pieces_joined']}/{info['pieces_total']} pieces, "
          f"{info['bytes']:,} bytes, {info['ending']}")

# The complete one has to come back byte for byte, or it is not the video.
complete = rejoined.get("https://cdn.example.test/stories/8821.mp4")
ok &= complete is not None and complete[0] == whole
print(f"\n  complete video byte-exact: {complete is not None and complete[0] == whole}")
# Every cached piece joined -- which is not the same as the whole video:
# the pieces cannot say how long the video was, so "complete" is never
# claimed (v2026.11.304), and all_cached says nothing cached was left out.
ok &= complete[1]["all_cached"] is True and complete[1]["complete"] is False

# The gapped one must stop at the gap. Joining across it would produce a
# file that looks whole, plays wrongly, and hashes as nothing.
gapped = rejoined.get("https://cdn.example.test/reels/4410.mp4")
ok &= gapped is not None and gapped[1]["ending"] == "stops at a gap"
ok &= gapped[0] == partial_source[:20000]
print(f"  gapped item stops at the gap: "
      f"{gapped is not None and gapped[1]['ending'] == 'stops at a gap'}, "
      f"keeping only the {len(gapped[0]):,} bytes before it")

# The index gives the items their names; without it they are only ids.
ok &= "https://cdn.example.test/stories/8821.mp4" in rejoined
print(f"  names read from the cache index: "
      f"{'https://cdn.example.test/stories/8821.mp4' in rejoined}")

print("\nthrough the engine")
out_dir = os.path.join(ROOT, "out")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", CACHE, "-o", out_dir],
               capture_output=True,
               cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
digests = {f["sha256"] for f in manifest["files"]}
ok &= hashlib.sha256(whole).hexdigest() in digests
print(f"  rejoined video recovered: {hashlib.sha256(whole).hexdigest() in digests}")

# A piece is not a video: the first one opens with a real header, and
# registering it would put a truncated clip beside the rejoined file.
# The rejoined record itself names the first piece's path on disk, which
# is where the cached video is, so it is not counted as a stray piece.
pieces_as_media = [f for f in manifest["files"] if ".exo" in f["source_path"]
                   and f.get("signature") != "embedded:exoplayer-cache"]
ok &= not pieces_as_media
print(f"  pieces not registered as videos: {not pieces_as_media}")
print(f"  summary: {manifest['summary']['exo_items']} item(s), "
      f"{manifest['summary']['exo_pieces']} piece(s) joined, "
      f"{manifest['summary']['exo_partial']} partial")

print("\nSnapchat bundles")
available = appcache.zstd_available()
print(f"  Zstandard decompressor: {available or 'none installed'}")
bundle = b"LZC\x00" + bytes(12) + b"\x28\xb5\x2f\xfd" + bytes(40)
ok &= appcache.is_lzc(bundle)
print(f"  a bundle is recognized by its header: {appcache.is_lzc(bundle)}")
if available is None:
    # Counted and reported, never silently skipped: these are a third of a
    # Snapchat content folder.
    print("  without one, bundles are counted and reported, not read")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
