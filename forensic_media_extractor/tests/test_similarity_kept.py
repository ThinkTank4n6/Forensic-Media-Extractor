"""A rescan does not decode the case again, and finds what it found before.

Every similarity scan decoded each picture four times (dhash, then
is_featureless -- which decoded it again to dhash it -- then phash), and kept
nothing, so rescanning at another distance repeated all of it. Hashes are now
kept in case.db by content. This counts the decodes, and checks the numbers
are the ones the previous code produced, bit for bit: a cache that changed a
hash would quietly regroup a case.
"""
import _sandbox  # noqa: F401
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
try:
    from PIL import Image
except ImportError:
    print("(not checked: Pillow is not installed)")
    sys.exit(0)
from fme.engines import imaging, similarity
from fme.review import casedb

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "sim_kept-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "images"))
records = []
import random
rng = random.Random(7)
for i in range(30):
    # Structure a hash can describe: a grid of random blocks, scaled up.
    small = Image.new("RGB", (8, 6))
    small.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(48)])
    im = small.resize((240, 180), Image.BILINEAR)
    if i % 10 == 9:
        im = Image.new("RGB", (240, 180), (128, 128, 128))     # featureless
    rel = f"images/p{i}.jpg"
    im.save(os.path.join(ROOT, rel), quality=88)
    records.append({"out_path": rel, "category": "image", "sha256": f"{i:064x}"})
with open(os.path.join(ROOT, "images", "broken.jpg"), "wb") as fh:
    fh.write(b"\xff\xd8\xff\xe0 not a picture")
records.append({"out_path": "images/broken.jpg", "category": "image", "sha256": "f" * 64})
manifest = os.path.join(ROOT, "manifest.json")
with open(manifest, "w") as fh:
    json.dump({"files": records}, fh)
casedb.CaseDB(os.path.join(ROOT, "case.db")).close()

opens = [0]
real_open = imaging.open_image


def counting(path):
    opens[0] += 1
    return real_open(path)


imaging.open_image = counting
first = similarity.analyze(manifest, 8)
first_opens, opens[0] = opens[0], 0
second = similarity.analyze(manifest, 12)          # another distance
second_opens = opens[0]
imaging.open_image = real_open
decodable = 31 - 1
print(f"first scan: {first_opens} decodes for {decodable + 1} files "
      f"(was four a picture: about {4 * decodable})")
ok &= first_opens <= 3 * (decodable + 1)
# Only the picture that would not decode is tried again: a failure is not
# kept (v2026.11.304), since it may be a missing codec rather than the file.
print(f"rescan at another distance: {second_opens} decodes (the broken one)")
ok &= second_opens == 1
ok &= first["hashes"] == second["hashes"] and first["phashes"] == second["phashes"]
print(f"  the same hashes both times: {first['hashes'] == second['hashes']}")

# Against v2026.11.293's code, loaded beside this one.
old_path = os.path.join(HERE, "support", "similarity_v293.py")
if os.path.isfile(old_path):
    spec = importlib.util.spec_from_file_location("similarity_v293", old_path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    old_d, old_p, old_f = old.hash_records(records, ROOT)
    new_d, new_p, new_f = similarity.hash_records(records, ROOT)
    same = (old_d, old_p, sorted(old_f)) == (new_d, new_p, sorted(new_f))
    ok &= same and len(old_d) > 20 and bool(old_f)
    print(f"  identical to the previous code's dhash, phash and featureless "
          f"({len(old_d)} hashed, {len(old_f)} featureless): {same}")
else:
    ok = False
    print("  tests/support/similarity_v293.py is missing: nothing compared")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
