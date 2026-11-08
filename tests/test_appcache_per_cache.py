"""ExoPlayer items are joined within their own cache, and say where they are.

An item id is a number each cache hands out from 0, so on a phone with
Instagram and Reddit installed both caches have an item 3. Pieces were
grouped by id across the whole tree: the two apps' item 3 became one
"video" made of both, and took whichever name the merged indexes gave last.
And the record's source path was the item's URL -- not where anything is.

Each cache here has its own <uid>.uid file and its own index table in one
shared database, the layout ExoPlayer's SimpleCache writes.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import make_media_test_image as g
from fme.engines import appcache

ROOT = os.path.join(tempfile.gettempdir(), "fme_appcache_per_cache-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail else ''} -- "
          f"{'ok' if good else 'WRONG'}")


apps = {
    "com.instagram.android": (g.make_mp4(30), "https://instagram.test/reel/1.mp4", 0x1F2E3D4C5B6A7988),
    # A uid with the top bit set: Java names its file "-..." and its table
    # with the unsigned form.
    "com.reddit.frontpage": (g.make_mp4(50), "https://reddit.test/v/2.mp4", -0x123456789ABCDEF),
}
db = os.path.join(ROOT, "data", "databases", "exoplayer_internal.db")
os.makedirs(os.path.dirname(db))
con = sqlite3.connect(db)
for package, (video, url, uid) in apps.items():
    cache = os.path.join(ROOT, "data", package, "cache", "exo")
    for sub in "0123456789":
        os.makedirs(os.path.join(cache, sub))
    half = len(video) // 2
    with open(os.path.join(cache, "3", f"3.0.1700000000000.v3.exo"), "wb") as fh:
        fh.write(video[:half])
    with open(os.path.join(cache, "7", f"3.{half}.1700000000001.v3.exo"), "wb") as fh:
        fh.write(video[half:])
    sign = "-" if uid < 0 else ""
    open(os.path.join(cache, f"{sign}{abs(uid):x}.uid"), "wb").close()
    table = "ExoPlayerCacheIndex" + format(uid & 0xFFFFFFFFFFFFFFFF, "x")
    con.execute(f'CREATE TABLE "{table}" (id INTEGER, key TEXT, metadata BLOB)')
    con.execute(f'INSERT INTO "{table}" VALUES (3, ?, NULL)', (url,))
con.commit()
con.close()

items = appcache.find_exo_pieces(ROOT)
check("two caches' item 3 are two items, not one", len(items) == 2, len(items))
found = {info["name"]: (payload, info) for _, payload, info in appcache.rejoin_folder(ROOT)}
for package, (video, url, _) in apps.items():
    got = found.get(url)
    check(f"{package}: its own pieces, byte for byte, under its own name",
          got is not None and got[0] == video)
    if got is None:
        continue
    info = got[1]
    check("   source_path is where the file begins on disk",
          info["source_path"] == f"data/{package}/cache/exo/3/3.0.1700000000000.v3.exo",
          info["source_path"])
    check("   with every piece, in order, and the cache it came from",
          len(info["piece_paths"]) == 2 and info["cache_dir"] == f"data/{package}/cache/exo"
          and all(os.path.isfile(os.path.join(ROOT, p)) for p in info["piece_paths"]))

print("\nan index no cache claims is not guessed at")
os.remove(os.path.join(ROOT, "data", "com.reddit.frontpage", "cache", "exo",
                       [n for n in os.listdir(os.path.join(ROOT, "data", "com.reddit.frontpage",
                                                           "cache", "exo"))
                        if n.endswith(".uid")][0]))
names = {info["cache_dir"]: name for name, _, info in appcache.rejoin_folder(ROOT)}
check("the cache that lost its uid keeps its item numbered, not another app's name",
      names.get("data/com.reddit.frontpage/cache/exo") == "3", names)
check("the other cache keeps its name",
      names.get("data/com.instagram.android/cache/exo") == "https://instagram.test/reel/1.mp4")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
