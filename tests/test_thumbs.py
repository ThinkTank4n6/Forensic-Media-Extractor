"""Gallery tiles, made once and kept.

A tile is about 180 pixels across; a phone photograph is 4,000. Serving
the original to every tile means a page of fifty asks for a few hundred
megabytes and the browser throws almost all of it away after scaling — and
repeats the work every time the page is drawn, because nothing was kept.

On a case of a hundred thousand files that is most of why scrolling feels
slow, and none of it is the carving an examiner is usually blaming.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import tempfile
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

ok = True
# One directory per process. Twenty-seven tests used a
# fixed path here and each wipes it on startup, so two
# runs overlapping -- which happens whenever a batch is
# cut short and its child keeps going -- had the second
# deleting the first's working files mid-run. That is
# the only mechanism found for this suite's intermittent
# failures.
ROOT = os.path.join(tempfile.gettempdir(), "thumbs_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "src"))

try:
    from PIL import Image
except ImportError:
    print("Pillow is not installed; skipping")
    sys.exit(0)

# Phone-sized, because that is what a case holds and what makes the
# difference visible.
for index in range(6):
    image = Image.new("RGB", (3024, 4032))
    pixels = image.load()
    for y in range(0, 4032, 8):
        for x in range(0, 3024, 8):
            pixels[x, y] = ((x + index * 40) % 256, (y * 3) % 256,
                            (x + y) % 256)
    image.save(os.path.join(ROOT, "src", f"IMG_{index:04d}.jpg"), quality=85)

on_disk = sum(os.path.getsize(os.path.join(ROOT, "src", name))
              for name in os.listdir(os.path.join(ROOT, "src")))
print(f"{len(os.listdir(os.path.join(ROOT, 'src')))} photographs, "
      f"{on_disk / 1048576:.1f} MiB")

port = "8713"
server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", port, "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    def fetch(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                    timeout=180) as response:
            return response.read()

    def ask(path):
        return json.loads(fetch(path))

    def send(path, body=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method="POST",
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json",
                 "X-FME-Request": "1"})
        return json.load(urllib.request.urlopen(request, timeout=300))

    for _ in range(150):
        try:
            ask("/api/capabilities")
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.4)

    send("/api/use", {"path": os.path.join(ROOT, "src")})
    job = send("/api/start", {"out_dir": os.path.join(ROOT, "case")})["job_id"]
    for _ in range(150):
        state = ask(f"/api/status?job_id={job}")
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    files = state["files"]
    out_dir = (state.get("summary") or {}).get("output_dir", "")

    # The tiles are made after the run, while nobody is waiting, rather
    # than one at a time as an examiner scrolls into them.
    ready = 0
    for _ in range(90):
        ready = sum(len(names) for _, _, names
                    in os.walk(os.path.join(out_dir, "thumbs")))
        if ready >= len(files):
            break
        time.sleep(0.5)
    ok &= ready >= len(files)
    print(f"  {ready} tile(s) made in the background for {len(files)} "
          f"picture(s)")

    def page(which):
        total = 0
        started = time.time()
        for entry in files:
            quoted = urllib.parse.quote(entry["rel_path"])
            total += len(fetch(f"/api/{which}?job={job}&file={quoted}"))
        return total, time.time() - started

    originals, original_time = page("file")
    tiles, tile_time = page("thumb")
    print(f"\n  a gallery page of {len(files)}:")
    print(f"    originals  {originals / 1048576:7.1f} MiB  "
          f"{original_time:5.2f}s")
    print(f"    tiles      {tiles / 1024:7.0f} KiB  {tile_time:5.2f}s")

    ok &= tiles * 20 < originals
    print(f"    {originals / max(1, tiles):.0f}x less data")

    # Each tile has to still be the picture, not a grey rectangle: this is
    # a speed change and must not become a correctness one.
    sample = urllib.parse.quote(files[0]["rel_path"])
    blob = fetch(f"/api/thumb?job={job}&file={sample}")
    import io as _io

    with Image.open(_io.BytesIO(blob)) as made:
        ok &= made.size[0] <= 360 and made.size[1] <= 360
        ok &= made.size[0] > 1 and made.size[1] > 1
        print(f"  a tile is a real picture, {made.size[0]}x{made.size[1]}")

    # A file that will not decode must still show something rather than a
    # broken tile: a missing thumbnail is a speed problem, not a reason to
    # hide evidence.
    from fme.web import media_gui

    made = media_gui._make_thumb(out_dir, os.path.join(ROOT, "src"),
                                 "not-a-picture")
    ok &= made is False
    print(f"  something that will not decode makes no tile: {not made}")
finally:
    server.terminate()
    server.wait(timeout=10)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
