"""The carve recovers real media at its real length, and says how it knows.

Every other carve fixture in this suite is built from header stubs: the
"JPEGs" in media_test.img are a few marker segments that no decoder will
open. A carve can pass every check on those and still be wrong about real
files, and v2026.11.273 was: a JPEG cut off before its end marker ran 63 KB
past its last byte, over the header of an intact AVI behind it, and the AVI
was never reported as a video.

This image is built from media Pillow, OpenCV and the wave module wrote and
can read back, with each case the length judge (fme/engines/carve_judge.py)
has a rule for planted at a known offset:

    a truncated photograph carrying a complete thumbnail in APP1, then
    noise, then an intact AVI    -> the partial is kept, labelled capped,
                                    cut exactly where the AVI starts, not at
                                    its own thumbnail; the AVI is whole
    a JPEG header with nothing behind it -> not a file
    intact JPEG, PNG, GIF, WAV, MP4      -> exact offset, length and hash,
                                            decodes, header or parsed
    an MP4                               -> its own boxes are not reported
                                            again as an embedded .mov
    an AVI cut off by the end of the image -> capped, runs to the end, not
                                              cut at its first frame
    the truncated photo, a JPEG and a PNG inside a container -> the
                                    embedded scan judges them the same way

and the same image carved by one worker and by three gives the same files.

Needs Pillow, OpenCV and NumPy, to make media that decodes. Without them it
says so and passes, unless FME_TESTS_STRICT=1 (set in CI), where a skip is a
failure: a known-answer test that quietly does not run is the gap this file
exists to close.

Runs under the __main__ guard: the workers are spawned.
"""
import hashlib
import io
import json
import logging
import os
import random
import shutil
import struct
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import carve_judge, media_core  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageFile
except ImportError as exc:  # pragma: no cover - depends on the machine
    MISSING = str(exc)
else:
    MISSING = ""

RNG = random.Random(20261103)


def noise(n: int) -> bytes:
    return RNG.randbytes(n)


def photo(w: int, h: int, seed: int) -> bytes:
    """A JPEG with real content: a gradient under seeded noise."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([(x * 255 // w), (y * 255 // h), ((x + y) * 127 // (w + h))], -1)
    pixels = np.clip(base + rng.integers(-40, 40, (h, w, 3)), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(pixels).save(buf, "JPEG", quality=88)
    return buf.getvalue()


def with_thumbnail(main: bytes, thumb: bytes) -> bytes:
    """An APP1 Exif segment holding a complete JPEG, as cameras write them."""
    payload = (b"Exif\x00\x00" + b"MM\x00\x2a\x00\x00\x00\x08"
               + b"\x00\x00" + b"\x00\x00\x00\x00" + thumb)
    return main[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + main[2:]


def png(seed: int) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(seed).integers(
        0, 255, (40, 60, 3), dtype=np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def gif() -> bytes:
    buf = io.BytesIO()
    frames = [Image.new("P", (48, 32), i * 40) for i in range(4)]
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(RNG.randbytes(16000))
    return buf.getvalue()


def video(root: str, fourcc: str, ext: str, frames: int, seed: int) -> bytes:
    path = os.path.join(root, f"v_{seed}.{ext}")
    rng = np.random.default_rng(seed)
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*fourcc), 10, (96, 64))
    for _ in range(frames):
        writer.write(rng.integers(0, 255, (64, 96, 3), dtype=np.uint8))
    writer.release()
    with open(path, "rb") as fh:
        return fh.read()


def frames_in(data: bytes, root: str, ext: str) -> int:
    path = os.path.join(root, f"check.{ext}")
    with open(path, "wb") as fh:
        fh.write(data)
    cap = cv2.VideoCapture(path)
    count = 0
    while cap.read()[0]:
        count += 1
    cap.release()
    return count


def decodes(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).load()
        return True
    except Exception:  # noqa: BLE001 - any failure is "does not decode"
        return False


def build(root: str) -> dict:
    """Lay out the image. Returns the plan: what is where, and the bytes."""
    plan = {"files": {}}
    blob = bytearray()

    def put(name, data, gap=4096):
        blob.extend(noise(gap))
        plan["files"][name] = (len(blob), data)
        blob.extend(data)

    avi = video(root, "MJPG", "avi", 12, 1)
    mp4 = video(root, "mp4v", "mp4", 12, 2)
    thumb = photo(96, 72, 3)
    big = with_thumbnail(photo(640, 480, 4), thumb)
    # Cut in the scan data, well past the header and its thumbnail.
    scan = big.index(b"\xff\xda", 2 + 2 + len(thumb))
    truncated = big[:scan + 6000]
    plan["thumb"] = thumb
    plan["big_dims"] = (640, 480)

    put("jpeg", photo(320, 240, 5), gap=200_000)
    # The partial and the AVI straddle 512 KiB, a stripe boundary when the
    # parallel run below caps stripes at that size.
    blob.extend(noise((512 << 10) - 30_000 - len(blob)))
    plan["files"]["partial"] = (len(blob), truncated)
    blob.extend(truncated)
    blob.extend(noise(60_000))
    plan["files"]["avi"] = (len(blob), avi)
    blob.extend(avi)
    put("fake", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01" + b"\x00" * 6000)
    put("png", png(6))
    put("gif", gif())
    put("wav", wav())
    put("mp4", mp4)
    blob.extend(noise(300_000))
    # Last: an AVI the image ends part-way through.
    tail = video(root, "MJPG", "avi", 30, 7)
    plan["files"]["tail_avi"] = (len(blob), tail)
    plan["tail_kept"] = len(tail) // 2
    blob.extend(tail[:plan["tail_kept"]])
    plan["total"] = len(blob)

    path = os.path.join(root, "known.dd")
    with open(path, "wb") as fh:
        fh.write(bytes(blob))
    plan["path"] = path
    plan["frames"] = {"avi": frames_in(avi, root, "avi"),
                      "mp4": frames_in(mp4, root, "mp4")}
    return plan


def carve(plan: dict, out: str, workers: int) -> dict:
    return media_core.run_extraction(plan["path"], out, mode="carve",
                                     workers=workers, chunk_size="256K",
                                     progress=False)


def main() -> int:
    if MISSING:
        strict = os.environ.get("FME_TESTS_STRICT") == "1"
        print(f"(needs Pillow, OpenCV and NumPy to build media that decodes: "
              f"{MISSING}; {'FAILING, strict mode' if strict else 'skipped'})")
        return 1 if strict else 0

    ok = True

    def check(label, passed, detail=""):
        nonlocal ok
        ok &= bool(passed)
        print(f"  {label}: {bool(passed)}" + (f"  ({detail})" if detail and not passed else ""))

    root = tempfile.mkdtemp(prefix="fme_carve_judge-")
    try:
        plan = build(root)
        files = plan["files"]

        print("the fixture is real media")
        for name in ("jpeg", "png", "gif"):
            check(f"{name} decodes before carving", decodes(files[name][1]))
        check("the AVI plays", plan["frames"]["avi"] == 12, plan["frames"])
        check("the MP4 plays", plan["frames"]["mp4"] == 12, plan["frames"])
        with wave.open(io.BytesIO(files["wav"][1])) as w:
            check("the WAV opens", w.getnframes() == 8000)

        print("\nthe table of what mediacarve judges")
        unknown = sorted(set(carve_judge.JUDGED) - set(media_core.SIG_BY_KEY))
        check("every judged key is a real signature", not unknown, unknown)
        check("the judge loads", carve_judge.status()["available"],
              carve_judge.status())

        summary = carve(plan, os.path.join(root, "w1"), 1)
        carved = {f["offset"]: f for f in summary["files"]
                  if (f.get("engine") or "") == "carve"}
        out_dir = os.path.join(root, "w1")

        def stored(rec):
            with open(os.path.join(out_dir, rec["out_path"]), "rb") as fh:
                return fh.read()

        print("\nintact files: exact, whole, and labelled as confirmed")
        for name in ("jpeg", "png", "gif", "wav", "avi", "mp4"):
            at, data = files[name]
            rec = carved.get(at)
            check(f"{name} at {at}", rec is not None)
            if rec is None:
                continue
            check(f"  {name} length {len(data)}", rec["size"] == len(data), rec["size"])
            check(f"  {name} SHA-256 is the original's",
                  rec["sha256"] == hashlib.sha256(data).hexdigest())
            check(f"  {name} bounded is header or parsed",
                  rec.get("bounded") in ("header", "parsed"), rec.get("bounded"))
        for name in ("jpeg", "png", "gif"):
            if files[name][0] in carved:
                check(f"recovered {name} decodes", decodes(stored(carved[files[name][0]])))
        for name in ("avi", "mp4"):
            if files[name][0] in carved:
                got = frames_in(stored(carved[files[name][0]]), root, name)
                check(f"recovered {name} plays all 12 frames", got == 12, got)

        print("\nthe truncated photograph")
        at, data = files["partial"]
        rec = carved.get(at)
        check("kept", rec is not None)
        if rec is not None:
            avi_at = files["avi"][0]
            check("labelled capped", rec.get("bounded") == "capped", rec.get("bounded"))
            check(f"cut exactly where the AVI starts ({avi_at - at} bytes)",
                  rec["size"] == avi_at - at, rec["size"])
            check("not cut at its own thumbnail", rec["size"] >= len(data), rec["size"])
            body = stored(rec)
            check("holds every byte that survived", body[:len(data)] == data)
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            try:
                dims = Image.open(io.BytesIO(body)).size
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = False
            check("opens as the 640x480 photograph it was", dims == plan["big_dims"], dims)
            thumbs = [f for f in summary["files"] if f.get("engine") == "embedded"
                      and f["sha256"] == hashlib.sha256(plan["thumb"]).hexdigest()]
            check("its thumbnail found inside it, as embedded", bool(thumbs))

        print("\nwhat is not a file, and what is not a second file")
        check("a JPEG header with nothing behind it is not carved",
              files["fake"][0] not in carved)
        mp4_at = files["mp4"][0]
        phantom = [f for f in summary["files"]
                   if (f.get("source_path") or "").startswith(f"carved@{mp4_at}::")
                   and f.get("ext") in ("mov", "mp4")]
        check("an MP4's own boxes are not reported again as video", not phantom,
              [(f["ext"], f["size"]) for f in phantom])
        spans = sorted((f["offset"], f["offset"] + f["size"]) for f in carved.values())
        overlaps = [(a, b) for a, b in zip(spans, spans[1:]) if a[1] > b[0]]
        check("no two carved files claim the same bytes", not overlaps, overlaps)

        print("\nthe AVI the image ends inside")
        at, _ = files["tail_avi"]
        rec = carved.get(at)
        check("kept", rec is not None)
        if rec is not None:
            check("labelled capped", rec.get("bounded") == "capped", rec.get("bounded"))
            check("runs to the end of the image, not cut at its first frame",
                  rec["size"] == plan["total"] - at, rec["size"])

        print("\nthe manifest says it")
        with open(os.path.join(out_dir, "manifest.csv"), encoding="utf-8") as fh:
            header = fh.readline()
        check("manifest.csv has a bounded column", "bounded" in header.strip().split(","))
        with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
            listed = json.load(fh)["files"]
        labels = {f.get("bounded") for f in listed if f.get("engine") == "carve"}
        check("manifest.json carries it for carved files",
              labels <= {"header", "parsed", "capped"} and labels, labels)

        print("\nthe same judge inside a container (the embedded scan)")
        cut = files["partial"][1]
        whole_jpg, png_bytes = files["jpeg"][1], files["png"][1]
        container = (b"CNTR" + noise(508) + cut + noise(20_000) + whole_jpg
                     + noise(3000) + png_bytes + noise(4000))
        inner = {"partial": 512, "jpeg": 512 + len(cut) + 20_000}
        inner["png"] = inner["jpeg"] + len(whole_jpg) + 3000
        emb_out = media_core.OutputManager(os.path.join(root, "emb"), metadata=False)
        emb_stats = {"embedded_files": 0, "embedded_bytes": 0}
        media_core.scan_embedded(container, out=emb_out, stats=emb_stats,
                                 signatures=media_core.SIGNATURES,
                                 source_label="container", fs_label="", part_label="")
        emb = {int(r.source_path.rsplit("+", 1)[1]): r for r in emb_out.records}
        rec = emb.get(inner["partial"])
        check("the truncated photo inside it is kept, capped",
              rec is not None and rec.bounded == "capped", rec and rec.bounded)
        check("and cut where the next file starts",
              rec is not None and rec.size == inner["jpeg"] - inner["partial"],
              rec and rec.size)
        for name in ("jpeg", "png"):
            rec = emb.get(inner[name])
            data = files[name][1]
            check(f"the intact {name} after it is whole and confirmed",
                  rec is not None and rec.size == len(data)
                  and rec.sha256 == hashlib.sha256(data).hexdigest()
                  and rec.bounded in ("header", "parsed"),
                  rec and (rec.size, rec.bounded))
        emb_out.close({})

        print("\none judge, whatever the worker count")
        saved = _engine.replace("STRIPE_MAX_BYTES", 512 << 10)
        try:
            many = carve(plan, os.path.join(root, "w3"), 3)
        finally:
            _engine.restore(saved)
        key = lambda s: sorted((f["offset"], f["size"], f.get("bounded"))
                               for f in s["files"] if f.get("engine") == "carve")
        same = key(many) == key(summary)
        check("three workers carve exactly what one does", same,
              sorted(set(key(many)) ^ set(key(summary))))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
