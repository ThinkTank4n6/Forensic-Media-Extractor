"""An MPEG transport stream that runs to the end of the image is carved whole.

A parser that runs to its cap has found no end, so its length is treated as
a guess and cut at the next header-like bytes inside it. A stream parser
that stops at the end of the image has not guessed: it checked a sync byte
on every packet up to there, and the image simply ended. Read as a guess, a
93-packet recording was cut at a "moov" in its payload and dropped as too
short -- so a .ts that was the last thing on a card, or an extracted segment
carved on its own, was never recovered. And once recovered, it must not be
found again inside itself: every packet boundary of a stream begins another.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_stream_end-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def packet(n):
    # Sync byte, a PID, a continuity counter, and a payload that happens to
    # hold bytes a box-based parser would take for a header.
    body = bytes([0x47, 0x01, 0x00, 0x10 | (n & 0x0F)]) + b"\x00" * 120 + b"moov"
    return body + bytes((n + i) % 251 for i in range(188 - len(body)))


stream = b"".join(packet(n) for n in range(93))
want = hashlib.sha256(stream).hexdigest()
noise = bytes((i * 7919) % 253 for i in range(1 << 20))
cases = {"the whole image": stream,
         "the end of the image": noise + stream,
         "the start, noise after": stream + noise}

for name, data in cases.items():
    image = os.path.join(ROOT, f"{abs(hash(name))}.dd")
    with open(image, "wb") as fh:
        fh.write(data)
    out = image + "_out"
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image, "-o", out,
                    "--mode", "carve"], cwd=PROJECT, capture_output=True, timeout=600)
    with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
        files = json.load(fh)["files"]
    print(f"a stream at {name}")
    found = [f for f in files if f["sha256"] == want]
    check("is carved whole, byte for byte", len(found) == 1)
    check("and its length is parsed, not a guess",
          found and found[0].get("bounded") == "parsed",
          found[0].get("bounded") if found else "")
    tails = [f for f in files if f["sha256"] != want and f["ext"] in ("ts", "m2ts")]
    check("and is not found again inside itself", not tails,
          f"{len(tails)} more" if tails else "")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
