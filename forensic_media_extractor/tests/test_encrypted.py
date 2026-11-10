"""A locked volume must not look like an empty one.

BitLocker and LUKS volumes read as random bytes. Every engine finds
nothing and the run reports "0 file(s)" -- which is also exactly what
an empty disk reports. An examiner cannot tell those apart from the
output, and concluding "there was nothing on it" about a locked drive
ends a case wrongly.

So the program says which it is, before anything is carved, in the log
and in the manifest.

This program does not break encryption and does not claim to. What it
owes an examiner is the difference between "nothing here" and "locked".
"""
import tempfile
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.engines.media_core import encryption_at

ROOT = os.path.join(tempfile.gettempdir(), "fme_encrypted-") + str(os.getpid())
ok = True


def check(label, passed, detail=""):
    global ok
    ok &= bool(passed)
    print(f"  [{'  works ' if passed else ' FAILED '}] {label}"
          + (f"  -- {detail}" if detail else ""))


print("recognising a locked volume from its first sector")
for name, head in (
        ("BitLocker", b"\xEB\x58\x90" + b"-FVE-FS-" + bytes(500)),
        ("LUKS", b"LUKS\xba\xbe" + bytes(500))):
    found = encryption_at(head)
    check(f"{name} is recognised", bool(found) and found["kind"] == name,
          (found or {}).get("kind", "nothing"))
    check(f"and it says what to do about {name}",
          bool(found) and len(found.get("advice", "")) > 40)

# A plain FAT volume starts the same way and must not be called
# encrypted: a false alarm on an ordinary disk is its own harm.
plain = b"\xEB\x58\x90" + b"MSWIN4.1" + bytes(500)
check("an ordinary FAT volume is not called encrypted",
      encryption_at(plain) is None)
check("and neither is a disk of zeros", encryption_at(bytes(512)) is None)

print("\na run over a locked volume")
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
volume = bytearray(4 * 1024 * 1024)
volume[0:3] = b"\xEB\x58\x90"
volume[3:11] = b"-FVE-FS-"
volume[510:512] = b"\x55\xAA"
image = os.path.join(ROOT, "locked.dd")
with open(image, "wb") as fh:
    fh.write(bytes(volume))

finished = subprocess.run(
    [sys.executable, "-m", "fme", "extract", "-i", image,
     "-o", os.path.join(ROOT, "out")],
    capture_output=True, text=True, cwd=PROJECT, timeout=600)
output = finished.stdout + finished.stderr

check("the run says the volume is BitLocker-encrypted",
      "BitLocker" in output)
check("and that this is not the same as an empty disk",
      "empty disk" in output)
check("and what to do about it", "recovery key" in output)

with open(os.path.join(ROOT, "out", "manifest.json"), encoding="utf-8") as fh:
    summary = json.load(fh)["summary"]
check("the manifest records it", summary.get("encrypted") == "BitLocker",
      str(summary.get("encrypted")))

# The point of the whole file: zero files from a locked volume must
# carry an explanation, where zero files from an empty one does not.
check("the run still reports no files, as it must",
      summary.get("total_files", 0) == 0)

# The pair a real fixture gives: the volume as seized, and the same
# volume after somebody opened it. The first must be reported as
# locked; the second must give the files back.
print("\na real LUKS pair, if it has been built")
for version in (1, 2):
    locked = os.path.join(HERE, "fixtures", f"luks{version}-locked.img")
    opened = os.path.join(HERE, "fixtures", f"luks{version}-unlocked.img")
    listing = os.path.join(HERE, "fixtures", f"luks{version}.intended")
    if not os.path.isfile(locked):
        print(f"  (no LUKS{version} fixture -- "
              f"sudo bash tools/make_fixtures/make_luks.sh {version})")
        continue

    with open(locked, "rb") as fh:
        found = encryption_at(fh.read(512))
    check(f"the locked LUKS{version} volume is recognised",
          bool(found) and found.get("version") == version,
          str((found or {}).get("kind")))
    if found and found.get("uuid"):
        print(f"      its UUID is {found['uuid']}")

    rows = []
    with open(listing, encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                rows.append(line.split())

    out_locked = os.path.join(ROOT, f"luks{version}_locked")
    shutil.rmtree(out_locked, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", locked,
                    "-o", out_locked], capture_output=True, cwd=PROJECT,
                   timeout=900)
    with open(os.path.join(out_locked, "manifest.json"),
              encoding="utf-8") as fh:
        got = json.load(fh)
    check(f"and a run over it recovers nothing, as it must",
          len(got["files"]) == 0, f"{len(got['files'])} file(s)")
    check("and the manifest says why",
          str(got["summary"].get("encrypted", "")).startswith("LUKS"))

    if os.path.isfile(opened):
        out_opened = os.path.join(ROOT, f"luks{version}_opened")
        shutil.rmtree(out_opened, ignore_errors=True)
        subprocess.run([sys.executable, "-m", "fme", "extract", "-i",
                        opened, "-o", out_opened], capture_output=True,
                       cwd=PROJECT, timeout=900)
        with open(os.path.join(out_opened, "manifest.json"),
                  encoding="utf-8") as fh:
            inside = {f.get("sha256") for f in json.load(fh)["files"]}
        for row in rows:
            check(f"{row[2]} comes back from the opened volume",
                  row[0] in inside)

# Not encryption, but the same harm: a volume the program cannot walk
# reports no files, which is what an empty disk reports too. ReFS is
# the one this unit meets.
print("\na volume that cannot be walked")
import struct as _struct

refs = bytearray(16 * 1024 * 1024)
refs[3:11] = b"ReFS\x00\x00\x00\x00"
refs[0x10:0x14] = b"FSRS"
_struct.pack_into("<QII", refs, 0x18, 32768, 512, 8)
refs[0x28], refs[0x29] = 3, 7
refs_image = os.path.join(ROOT, "refs.dd")
os.makedirs(ROOT, exist_ok=True)
with open(refs_image, "wb") as fh:
    fh.write(bytes(refs))

out_refs = os.path.join(ROOT, "refs_out")
shutil.rmtree(out_refs, ignore_errors=True)
finished = subprocess.run(
    [sys.executable, "-m", "fme", "extract", "-i", refs_image,
     "-o", out_refs], capture_output=True, text=True, cwd=PROJECT,
    timeout=600)
spoke = finished.stdout + finished.stderr

check("the run names the file system", "REFS" in spoke.upper())
check("and says it is not the same as an empty disk",
      "empty disk" in spoke)
check("and gives the reader's own reason",
      "walk" in spoke and "not possible" in spoke)

with open(os.path.join(out_refs, "manifest.json"), encoding="utf-8") as fh:
    refs_summary = json.load(fh)["summary"]
check("the manifest records which file system it was",
      refs_summary.get("unreadable_volume") == "refs",
      str(refs_summary.get("unreadable_volume")))

print("\nnot checked")
print("  No real BitLocker or LUKS volume is tested here: making one")
print("  needs Windows or cryptsetup. The signature is what is")
print("  checked, which is what the program acts on. Unlocking is not")
print("  implemented at all -- a vendored reader can do it and nothing")
print("  calls it; see docs/VALIDATION.md.")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
