"""Three ways the carve lost files without a word, and what it does now.

1. AVCHD .m2ts was never carved. Its signature tested for sync bytes 188
   apart, as MPEG-TS is; an M2TS packet is 192 bytes (a four-byte
   timestamp, then the sync byte), so not one camcorder recording matched.
   Checked through the carve's own detection, with NumPy and without.
2. Anything beginning "PK" went to the zip reader, and one that would not
   open as a zip (no central directory: carved, truncated) gave 0 with the
   byte scan never run. It falls back to the byte scan now, and is counted.
3. A read that raised -- rather than came back empty -- ended the serial
   carve (the one phase run_extraction did not wrap) and lost a parallel
   stripe; an exception in the carve judge did the same. Both are now an
   unreadable stretch or a counted error, and the carve goes on.
"""
import collections
import contextlib
import io
import logging
import os
import random
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import carve_judge, media_core  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_carve_signals-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
rng = random.Random(11)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def noise(n):
    # No 0x47 at all, so nothing in the filler can look like a stream.
    return bytes(b if b != 0x47 else 0x46 for b in rng.randbytes(n))


def stream(packet, count):
    pre = packet - 188
    return b"".join(rng.randbytes(pre).replace(b"\x47", b"\x00") + b"\x47"
                    + noise(187) for _ in range(count))


def detect(buf):
    records, _ = media_core._detect_range(
        media_core.BytesReader(buf), media_core.active_signatures(),
        hit_lo=0, hit_hi=len(buf), start_lo=0, start_hi=None, next_allowed=0,
        align=1, max_carve_size=1 << 30, chunk_size=1 << 20)
    return records


def carve_args(chunk=1 << 20):
    return types.SimpleNamespace(
        chunk_size=chunk, align=1, max_carve_size=media_core.MAX_VIDEO_DEFAULT,
        signatures=media_core.active_signatures(), resume_from=0, resuming={},
        progress=False, scan_inside=False, max_inside_size=0, output=ROOT,
        image="x")


# --------------------------------------------------------------------------
print("an AVCHD (.m2ts) recording: 192-byte packets, sync byte at +4")
m2ts = stream(192, 200)
ts = stream(188, 200)
at_m2ts, at_ts = 70001, 70001 + len(m2ts) + 50003
image = bytearray(noise(at_ts + len(ts) + 40000))
image[at_m2ts:at_m2ts + len(m2ts)] = m2ts
image[at_ts:at_ts + len(ts)] = ts
image = bytes(image)
for label, patched in (("with NumPy", False), ("without NumPy", True)):
    saved = (_engine.replace("_ts_positions", lambda *a, **k: None)  # the fallback walk
             if patched else None)
    try:
        found = {(r["signature"], r["start"], r["length"]) for r in detect(image)}
    finally:
        if saved:
            _engine.restore(saved)
    check(f"{label}: the .m2ts is carved whole, from its first packet",
          ("m2ts", at_m2ts, len(m2ts)) in found, sorted(found))
    check(f"{label}: the 188-byte stream is still MPEG-TS, and only that",
          any(s == "mpegts" and st == at_ts for s, st, _ in found)
          and not any(s == "m2ts" and st >= at_ts - 4 for s, st, _ in found))

path = os.path.join(ROOT, "avchd.dd")
with open(path, "wb") as fh:
    fh.write(image)
reader = media_core.open_image(path)
out = media_core.OutputManager(os.path.join(ROOT, "out_m2ts"), metadata=False)
stats = collections.defaultdict(int)
media_core.run_carve_engine(reader, out, carve_args(), stats)
stored = [r for r in out.records if r.ext == "m2ts"]
check("and the carve stores it as a video", len(stored) == 1
      and stored[0].offset == at_m2ts and stored[0].size == len(m2ts)
      and stored[0].category == "video",
      [(r.ext, r.offset, r.size) for r in out.records])
out.close({})
reader.close()

# --------------------------------------------------------------------------
print("\na \"zip\" that will not open is scanned as bytes")
picture = fixtures.make_jpeg(9)
damaged = b"PK\x03\x04" + noise(3000) + picture + noise(2000)   # no directory
out = media_core.OutputManager(os.path.join(ROOT, "out_zip"), metadata=False)
stats = collections.defaultdict(int)
for key in ("embedded_files", "embedded_bytes", "errors"):
    stats[key] = 0
found = media_core.scan_embedded(damaged, out=out, stats=stats,
                                 signatures=media_core.active_signatures(),
                                 source_label="damaged.zip")
check("the picture inside is found", found == 1 and out.records
      and out.records[0].size == len(picture), found)
check("and the zip that would not open is counted", stats.get("zip_unopened") == 1,
      stats.get("zip_unopened"))
out.close({})

# --------------------------------------------------------------------------
print("\na read that raises is an unreadable stretch, not the end of the carve")
CHUNK = 1 << 20
pics = [fixtures.make_jpeg(20 + i) for i in range(3)]
blob = bytearray(noise(6 * CHUNK))
places = [CHUNK // 2, 3 * CHUNK + 1000, 5 * CHUNK + 777]
for at, pic in zip(places, pics):
    blob[at:at + len(pic)] = pic
blob = bytes(blob)
BAD_LO, BAD_HI = 2 * CHUNK, 2 * CHUNK + 4096


class Raising(media_core.BytesReader):
    def read(self, offset, length):
        if offset < BAD_HI and offset + length > BAD_LO:
            raise OSError(5, "Input/output error")
        return super().read(offset, length)


out = media_core.OutputManager(os.path.join(ROOT, "out_raise"), metadata=False)
stats = collections.defaultdict(int)
media_core.run_carve_engine(Raising(blob), out, carve_args(CHUNK), stats)
got = sorted(r.offset for r in out.records)
check("the serial carve goes on past it and finds every picture",
      got == places, got)
check("the read errors are counted", stats.get("read_errors", 0) >= 1,
      stats.get("read_errors"))
check("and the stretch is reported unreadable",
      any(lo <= BAD_LO < lo + n for lo, n in stats.get("unreadable_ranges") or []),
      stats.get("unreadable_ranges"))
out.close({})

media_core._WORKER.update(reader=Raising(blob),
                          signatures=media_core.active_signatures())
media_core._WORKER.pop("heartbeat", None)
result = media_core._worker_carve((0, len(blob), 1, 1 << 30, CHUNK))
check("a parallel stripe across it is not lost",
      "error" not in result and sorted(r["start"] for r in result["records"]) == places,
      result.get("error") or sorted(r["start"] for r in result["records"]))
check("and reports the stretch and the error count",
      result.get("unread") and result.get("read_errors", 0) >= 1,
      (result.get("unread"), result.get("read_errors")))

print("\nan exception in the carve judge costs that judgment, not the carve")
real_judge = carve_judge.judge
calls = {"n": 0}


def flaky(*a, **k):
    calls["n"] += 1
    if calls["n"] == 1:
        raise RuntimeError("a bug in the judge")
    return real_judge(*a, **k)


carve_judge.judge = flaky
try:
    out = media_core.OutputManager(os.path.join(ROOT, "out_judge"), metadata=False)
    stats = collections.defaultdict(int)
    media_core.run_carve_engine(media_core.BytesReader(blob), out,
                                carve_args(CHUNK), stats)
finally:
    carve_judge.judge = real_judge
got = sorted(r.offset for r in out.records)
check("the carve finishes and finds every picture", got == places, got)
check("the judge's exception is counted", stats.get("judge_errors") == 1,
      stats.get("judge_errors"))
out.close({})

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
