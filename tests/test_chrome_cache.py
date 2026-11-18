"""Video Chrome on Android cached as MP4 fragments is rebuilt and stored.

Chrome keeps a streamed video in its Simple Cache as moof/mdat fragments with
no moov, which plays nothing one entry at a time. Read entry by entry, a run
of a phone extraction stored no video from it. The vendored
chromiumcacheprobe rebuilds them; this checks that a run reaches it, that
each clip it stores plays, and that the note says what was rebuilt.

The real fixture is 16 unmodified files of the public DFRWS 2021 Galaxy S10
extraction (tests/fixtures/FIXTURES-PROVENANCE.md).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures", "chrome-cache")

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_chrome_cache-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def run(folder, name):
    out = os.path.join(ROOT, name)
    done = subprocess.run([sys.executable, "-m", "fme", "extract", "-i", folder,
                           "-o", out], cwd=PROJECT, capture_output=True,
                          text=True, timeout=900)
    path = os.path.join(out, "manifest.json")
    if not os.path.isfile(path):
        print(done.stdout[-2000:], done.stderr[-2000:])
        return out, []
    with open(path, encoding="utf-8") as fh:
        return out, json.load(fh)["files"]


def decodes(path):
    try:
        import cv2
    except ImportError:
        return None
    capture, n = cv2.VideoCapture(path), 0
    while capture.read()[0]:
        n += 1
    return n


print("the DFRWS 2021 Galaxy S10 cache")
dump = os.path.join(ROOT, "s10")
with zipfile.ZipFile(os.path.join(FIX, "dfrws2021_s10_subset.zip")) as z:
    z.extractall(dump)
out, files = run(dump, "s10_out")
clips = [f for f in files if (f.get("signature") or "").startswith("embedded:chrome-cache")]
check("the run stores the clips the cache rebuilds into", len(clips) == 12,
      f"{len(clips)} stored")
check("each is a video", all(f.get("category") == "video" for f in clips))
check("each says it came from Chrome's cache and not that it was watched",
      all("Chrome's cache" in f.get("note", "") and "not that anyone watched it"
          in f.get("note", "") for f in clips))
check("each names the cache file it came from",
      all("/cache/Cache/" in (f.get("source_path") or "") for f in clips))
counted = [decodes(os.path.join(out, f["out_path"])) for f in clips]
if counted and counted[0] is None:
    print("  not checked: OpenCV is absent, so the clips are not decoded")
else:
    check("and every one decodes to frames", counted and all(n and n > 0 for n in counted),
          f"{sum(n or 0 for n in counted)} frames over {len(counted)} clips")

print("fragments with no header left in the cache")
synth = os.path.join(ROOT, "synth")
with zipfile.ZipFile(os.path.join(FIX, "synthetic_fragments.zip")) as z:
    z.extractall(synth)
out, files = run(synth, "synth_out")
clips = [f for f in files if (f.get("signature") or "").startswith("embedded:chrome-cache")]
check("a clip is rebuilt", len(clips) >= 1, f"{len(clips)} stored")
check("and its note says the header was taken from the samples",
      clips and all("taken from the samples" in f.get("note", "") for f in clips))

print("the same cache inside a phone extraction's zip and tar")
import tarfile
for kind in ("zip", "tar.gz"):
    pkg = os.path.join(ROOT, "s10." + kind)
    if kind == "zip":
        with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
            for here, _d, names in os.walk(dump):
                for n in names:
                    full = os.path.join(here, n)
                    z.write(full, os.path.relpath(full, dump))
    else:
        with tarfile.open(pkg, "w:gz") as t:
            t.add(os.path.join(dump, "Dump"), arcname="Dump")
    out, files = run(pkg, "pkg_" + kind.replace(".", "_"))
    clips = [f for f in files if (f.get("signature") or "").startswith("embedded:chrome-cache")]
    check(f"a .{kind} gives the same twelve clips", len(clips) == 12, f"{len(clips)} stored")
    check("named by the entry they came from",
          all((f.get("source_path") or "").startswith("Dump/data/data/com.android.chrome/cache/Cache/")
              for f in clips))
    check("and the copy it was joined from is gone",
          not [n for n in os.listdir(out) if n.startswith(".fme_cache_spill_")])

print("a package whose cache entries are named to climb out")
escape = os.path.join(ROOT, "escape-test")
os.makedirs(escape)
pkg = os.path.join(escape, "hostile.zip")
cache_dir = os.path.join(dump, "Dump/data/data/com.android.chrome/cache/Cache")
with zipfile.ZipFile(pkg, "w") as z:
    for n in sorted(os.listdir(cache_dir)):
        full = os.path.join(cache_dir, n)
        if os.path.isfile(full):
            z.write(full, "../../../climbed/Cache/" + n)
            z.write(full, "C:/climbed2/Cache/" + n)
out, files = run(pkg, "hostile_out")
outside = [p for p in ("climbed", "climbed2") if os.path.exists(os.path.join(ROOT, p))
           or os.path.exists(os.path.join(escape, p))
           or os.path.exists(os.path.join(os.path.dirname(ROOT), p))]
check("nothing is written outside the case", not outside, outside)
check("and the clips are still rebuilt",
      len([f for f in files if (f.get("signature") or "").startswith("embedded:chrome-cache")]) >= 12)

print("a folder with no cache in it")
plain = os.path.join(ROOT, "plain")
os.makedirs(plain)
with open(os.path.join(plain, "readme.txt"), "w") as fh:
    fh.write("nothing here")
_, files = run(plain, "plain_out")
check("rebuilds nothing", not any((f.get("signature") or "").startswith(
    "embedded:chrome-cache") for f in files))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
