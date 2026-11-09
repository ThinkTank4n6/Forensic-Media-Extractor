"""manifest.csv agrees with manifest.json when a run closes.

A file's CSV row is written when the file is first stored; what is learned
after -- another place it was found, its instance count, other mount points
showing it -- reached only manifest.json. A resume reads the CSV, so it
brought the older values back. The CSV is now rewritten from the records at
close, and both manifests are written beside and renamed into place.
"""
import csv
import json
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.engines import media_core

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_manifest_close-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def picture(seed):
    rng = random.Random(seed)
    return b"\xff\xd8\xff\xe0" + bytes(rng.getrandbits(8) for _ in range(media_core.MIN_IMAGE_SIZE + 2048)) + b"\xff\xd9"


def store(out, data, where):
    return out.store(iter([data]), category="image", ext="jpg", stem="x",
                     engine="filesystem", source_path=where)


def csv_instances(folder):
    with open(os.path.join(folder, "manifest.csv"), newline="", encoding="utf-8") as fh:
        return [int(r["instances"]) for r in csv.DictReader(fh)]


A = picture(1)
case = os.path.join(ROOT, "case")
out = media_core.OutputManager(case, metadata=False)
store(out, A, "/DCIM/a.jpg")
store(out, A, "/Backup/a.jpg")
out.close({})
with open(os.path.join(case, "manifest.json"), encoding="utf-8") as fh:
    in_json = [f["instances"] for f in json.load(fh)["files"]]
right = csv_instances(case) == in_json == [2]
ok &= right
print(f"a file found twice: manifest.csv and manifest.json both say 2: {right}")

out = media_core.OutputManager(case, metadata=False, resume=True)
store(out, A, "/Cache/a.jpg")
out.close({})
right = csv_instances(case) == [3]
ok &= right
print(f"resumed and found a third time: 3, not 2: {right} {csv_instances(case)}")

left = [n for n in os.listdir(case) if n.endswith(".tmp")]
ok &= not left
print(f"nothing half-written is left beside the manifests: {not left}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
