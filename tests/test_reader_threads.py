"""An image reader gives each thread its own bytes.

A read is a seek and then a read on one open file. Only the vendored reader
locked the pair; the raw, segmented, device, libyal and source readers did
not, so two threads reading one reader could each seek before the other
read and get each other's bytes. Every reader's read() now runs under a lock
of its own, added by the base class so a reader written later is covered.
"""
import os
import random
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.engines import media_core

root = tempfile.mkdtemp(prefix="fme_reader_threads-")
data = bytes(random.Random(5).getrandbits(8) for _ in range(4 << 20))
raw = os.path.join(root, "disk.dd")
with open(raw, "wb") as fh:
    fh.write(data)
for n, part in enumerate((data[:1 << 21], data[1 << 21:])):          # a split image too
    with open(os.path.join(root, f"split.{n + 1:03d}"), "wb") as fh:
        fh.write(part)

ok = True
for label, path in (("a raw image", raw), ("a split image", os.path.join(root, "split.001"))):
    reader = media_core.open_image(path)
    wrong = []

    def hammer(seed):
        rng = random.Random(seed)
        for _ in range(3000):
            at = rng.randrange(0, len(data) - 4096)
            if reader.read(at, 4096) != data[at:at + 4096]:
                wrong.append(at)

    threads = [threading.Thread(target=hammer, args=(s,)) for s in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    reader.close()
    right = not wrong
    ok &= right
    print(f"{label}: six threads, 18,000 reads, every one the right bytes: {right} ({len(wrong)} wrong)")
locked = all(getattr(cls.__dict__.get("read"), "_locked", False)
             for cls in media_core.ImageReader.__subclasses__() if "read" in cls.__dict__)
ok &= locked
print(f"every reader class's read() is locked: {locked}")
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
