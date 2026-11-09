"""Every worker count carves exactly the files one worker carves.

A worker cannot see a file that began in the stripe before its own. It
could accept something nested inside that file and, stepping over it, hide
a real file lying beyond the outer file's end; the merge dropped the nested
record, and the real file was never reported by anyone. Measured on
v2026.11.271: a JPEG just after a WAV that straddled a stripe boundary was
carved with one worker and lost with two.

The image here plants that shape at a stripe boundary, and the check is
the one that was missing when the parallel carve was first written: the
same image, carved with one worker and with several, gives the same files
at the same offsets.

Runs under the __main__ guard: the workers are spawned.
"""
import io
import logging
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module


def wav(size: int, rng) -> bytes:
    body = b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
    data = bytes(rng.getrandbits(8) for _ in range(size - len(body) - 16))
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def build(root: str) -> tuple:
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image as fixtures
    rng = __import__("random").Random(7)
    total = 8 << 20
    blob = bytearray(b"\x5a" * total)
    planted = {}
    # Stripes for two workers over 8 MiB with 256 KiB chunks are 1 MiB.
    for boundary in (1 << 20, 5 << 20):
        outer = wav(12288, rng)
        a0 = boundary - 4096
        blob[a0:a0 + len(outer)] = outer
        nested = wav(40000, rng)                 # inside outer, runs past it
        x0 = boundary + 1024
        blob[x0:x0 + len(nested)] = nested
        pic = fixtures.make_jpeg(boundary % 97)
        y0 = a0 + len(outer) + 4096              # after outer, inside nested's span
        blob[y0:y0 + len(pic)] = pic
        planted[y0] = "jpg"
        planted[a0] = "wav"
    for i in range(4):                           # ordinary pictures elsewhere
        pic = fixtures.make_jpeg(60 + i)
        at = (2 << 20) + i * (600 << 10) + 333
        blob[at:at + len(pic)] = pic
        planted[at] = "jpg"
    path = os.path.join(root, "merge.dd")
    with open(path, "wb") as fh:
        fh.write(bytes(blob))
    return path, planted


def carved(image: str, out: str, workers: int) -> dict:
    summary = media_core.run_extraction(image, out, mode="carve", workers=workers,
                                        chunk_size="256K", progress=False)
    return {f["offset"]: f["sha256"] for f in summary["files"]
            if (f.get("engine") or "").startswith("carve")}


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_parallel_merge-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    image, planted = build(root)
    one = carved(image, os.path.join(root, "w1"), 1)
    found = all(at in one for at in planted)
    ok &= found
    print(f"one worker carves every planted file: {found} "
          f"({sum(at in one for at in planted)} of {len(planted)})")
    default = media_core.STRIPE_MAX_BYTES
    for workers, cap in ((2, default), (3, default), (4, 1 << 20), (2, 700 << 10)):
        with _engine.replaced("STRIPE_MAX_BYTES", cap):
            many = carved(image, os.path.join(root, f"w{workers}-{cap}"), workers)
        same = many == one
        ok &= same
        lost = sorted(set(one) - set(many))
        extra = sorted(set(many) - set(one))
        print(f"{workers} workers, stripes up to {cap >> 10} KiB: same files as one: "
              f"{same}" + (f" (lost {lost}, extra {extra})" if not same else ""))
    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
