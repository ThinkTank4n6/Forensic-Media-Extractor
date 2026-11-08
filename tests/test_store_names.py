"""Two writers storing into one folder never overwrite each other's files.

store() checked that a name was free, then renamed the finished file onto
it later; os.replace overwrites without a word, so a second writer taking
the same name in between replaced the first file. Its temporary name, built
from the process id and the record count, could also be the same for two
writers in one process. Names are now claimed by creating them exclusively,
and temporary names are unique.
"""
import hashlib
import os
import random
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.engines import media_core

root = tempfile.mkdtemp(prefix="fme_store_names-")
writers = [media_core.OutputManager(os.path.join(root, "case"), metadata=False, dedupe=False)
           for _ in range(2)]
written = {}
lock = threading.Lock()


def work(index, out):
    for n in range(40):
        rng = random.Random(index * 1000 + n)
        data = b"\xff\xd8\xff\xe0" + bytes(rng.getrandbits(8) for _ in range(media_core.MIN_IMAGE_SIZE + 512)) + b"\xff\xd9"
        rec = out.store(iter([data]), category="image", ext="jpg", stem=f"same_{n}",
                        engine="carve")
        with lock:
            written[rec.out_path] = hashlib.sha256(data).hexdigest()


threads = [threading.Thread(target=work, args=(i, w)) for i, w in enumerate(writers)]
for t in threads:
    t.start()
for t in threads:
    t.join()
on_disk = {}
for path in written:
    with open(os.path.join(root, "case", path), "rb") as fh:
        on_disk[path] = hashlib.sha256(fh.read()).hexdigest()
distinct = len(written) == 80
intact = on_disk == written
leftovers = [n for n in os.listdir(os.path.join(root, "case")) if n.startswith(".tmp_")]
print(f"80 files stored under the same names by two writers: {len(written)} distinct names: {distinct}")
print(f"every file holds what was stored under its name: {intact}")
print(f"no temporary files left behind: {not leftovers}")
for w in writers:
    w.close({})
shutil.rmtree(root, ignore_errors=True)
ok = distinct and intact and not leftovers
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
