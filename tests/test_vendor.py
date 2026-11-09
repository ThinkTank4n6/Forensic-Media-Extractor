"""The vendored readers, and the licence terms that come with them.

Using somebody else's code under MIT asks for one thing in return: the
copyright notice and the permission text travel with it. That is not a
formality -- it is the condition on which the code may be shipped at all, so
it is tested like any other requirement, and a release that loses a licence
file fails here rather than going out.

The provenance record exists for a second reason: an examiner asked in court
what read the evidence should be able to be told exactly which version of
what, and shown that the file has not been altered since.
"""
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.engines import vendor_fs

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(PROJECT, "fme", "vendor")

ok = True

print("what is vendored")
with open(os.path.join(VENDOR, "vendored.json"), encoding="utf-8") as fh:
    record = json.load(fh)["vendored"]

for entry in record:
    path = os.path.join(VENDOR, entry["path"])
    there = os.path.isfile(path)
    ok &= there
    print(f"  {entry['path']:20} {entry.get('version', '-'):8} "
          f"{entry['licence']:11} {'present' if there else 'MISSING'}")

print("\nthe licence text travels with the code")
for entry in record:
    licence = os.path.join(VENDOR, entry["licence_file"])
    there = os.path.isfile(licence)
    ok &= there
    if there:
        with open(licence, encoding="utf-8") as fh:
            text = fh.read()
        # The two things MIT actually requires to be kept.
        keeps_notice = "Copyright" in text
        keeps_permission = ("Permission is hereby granted" in text
                            or "Apache License" in text)
        ok &= keeps_notice and keeps_permission
        print(f"  {entry['licence_file']:22} notice: {keeps_notice}, "
              f"terms: {keeps_permission}")
    else:
        print(f"  {entry['licence_file']:22} MISSING")

print("\nthe code is what the record says it is")
for entry in record:
    if "sha256" not in entry:
        continue
    path = os.path.join(VENDOR, entry["path"])
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    same = digest == entry["sha256"]
    ok &= same
    print(f"  {entry['path']:20} {digest[:16]}… "
          f"{'as recorded' if same else 'DOES NOT MATCH THE RECORD'}")

# MIT asks for the copyright notice to be included with the copy. A file
# that carries one in its header must keep it -- stripping it would be the
# one edit that actually breaches the terms. A file that never carried one
# is covered by the licence text shipped beside it, and the holder is
# recorded either way, so nobody has to go looking.
print("\nthe copyright notice is kept with each file")
for entry in record:
    if not entry["path"].endswith(".py"):
        continue
    with open(os.path.join(VENDOR, entry["path"]), encoding="utf-8",
              errors="replace") as fh:
        head = fh.read(6000)
    inline = "Copyright" in head
    recorded = bool(entry.get("copyright"))
    # If the original carried a notice, it is still there; if it did not,
    # the licence file beside it carries one.
    kept = inline if entry.get("notice_in_file") else recorded
    ok &= kept and recorded
    where = ("in the file" if inline
             else "in " + entry["licence_file"])
    print(f"  {entry['path']:20} {entry.get('copyright', '')}  ({where})")

# An Apache 1.1 licence asks for more than its own text: clause 3 requires
# an acknowledgment, in its exact words, in the end-user documentation. The
# words are taken from the licence file itself, so the manual cannot drift
# from what the licence actually says.
print("\nacknowledgments the licences require are in the manual")
import re as _re
with open(os.path.join(PROJECT, "MANUAL.md"), encoding="utf-8") as fh:
    _manual = " ".join(fh.read().split())
for entry in record:
    with open(os.path.join(VENDOR, entry["licence_file"]), encoding="utf-8") as fh:
        _lic = fh.read()
    _m = _re.search(r'must include the following acknowledgment:\s*"(.*?)"', _lic, _re.S)
    if not _m:
        continue
    _words = " ".join(_m.group(1).split())
    _there = _words in _manual
    ok &= _there
    print(f"  {entry['licence_file']:22} {'in MANUAL.md' if _there else 'MISSING from MANUAL.md'}")

print("\nwhat it adds")
state = vendor_fs.status()
print(f"  available: {state['available']}  version {state['version']}")
print(f"  kinds it is used for: {', '.join(state['kinds'][:8])}…")
ok &= state["available"]

# It must not be used where the program already has a reader: two readers
# for one volume is two answers to the same question.
# The committed fixture is in tests/. Looked for at the project root, it was
# never found there, so every run built a 16 MB copy among the code.
fixture = os.path.join(PROJECT, "tests", "media_test.img")
if not os.path.isfile(fixture):
    # Built here rather than skipped. Run on its own, this test used to say
    # "(no fixture image; run tests/make_media_test_image.py first)", skip
    # the walk and every acquisition check below, and still print ALL CHECKS
    # PASS -- which is how it was first run on Windows.
    import contextlib
    import io

    sys.path.insert(0, os.path.join(PROJECT, "tests"))
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image

        make_media_test_image.main(fixture)
    print(f"\n  (built {os.path.relpath(fixture, PROJECT)}, which this test reads)")
if os.path.isfile(fixture):
    seen = vendor_fs.kinds_in(fixture)
    extra = vendor_fs.extra_kinds(fixture)
    ok &= bool(seen) and not extra
    print(f"\n  a FAT32 image: sees {[v['kind'] for v in seen]}, "
          f"leaves it to the main engine: {not extra}")

    # And the walk itself works: forced over that volume, the bytes it
    # streams have to be the file's own.
    collected = []
    vendor_fs.walk(fixture, lambda r: collected.append(
        (r["path"], r["size"], len(b"".join(r["read"]())))), only_extra=False)
    exact = all(size == got for _, size, got in collected)
    ok &= bool(collected) and exact
    print(f"  forced walk read {len(collected)} file(s), every one the "
          f"declared size: {exact}")
else:
    ok = False
    print("\n  the fixture image could not be built: FAILING rather than "
          "skipping the checks that need it")

# The second vendored reader opens acquisitions. Two things it buys: the
# formats this program has no other reader for, and the common ones on a
# machine where the C extensions they normally need will not build -- which
# on Windows is most of them, and an examiner who cannot open an E01 cannot
# start.
print("\nacquisition formats")
import struct
import subprocess
import time

from fme.engines import media_core

# A fixed VHD is a raw disk with a 512-byte footer, so one can be built here
# exactly and compared against the disk it wraps.
def vhd_footer(size):
    footer = bytearray(512)
    footer[0:8] = b"conectix"
    struct.pack_into(">I", footer, 8, 2)
    struct.pack_into(">I", footer, 12, 0x00010000)
    struct.pack_into(">Q", footer, 16, 0xFFFFFFFFFFFFFFFF)
    struct.pack_into(">I", footer, 24, int(time.time()) - 946684800)
    footer[28:32] = b"vpc "
    struct.pack_into(">I", footer, 32, 0x00050003)
    footer[36:40] = b"Wi2k"
    struct.pack_into(">Q", footer, 40, size)
    struct.pack_into(">Q", footer, 48, size)
    cylinders = min(65535, max(1, size // 512 // (16 * 255)))
    struct.pack_into(">HBB", footer, 56, cylinders, 16, 255)
    struct.pack_into(">I", footer, 60, 2)
    struct.pack_into(">I", footer, 64, (~sum(footer)) & 0xFFFFFFFF)
    return bytes(footer)


if os.path.isfile(fixture):
    with open(fixture, "rb") as fh:
        raw = fh.read()
    work = os.path.join("/tmp", "vendor_test")
    os.makedirs(work, exist_ok=True)
    vhd = os.path.join(work, "evidence.vhd")
    with open(vhd, "wb") as fh:
        fh.write(raw + vhd_footer(len(raw)))

    reader = media_core.open_image(vhd)
    through_vendor = isinstance(reader, media_core.VendorImageReader)
    ok &= through_vendor
    print(f"  a VHD opens through the vendored reader: {through_vendor}")

    ok &= reader.size == len(raw)
    exact = reader.read(0, len(raw)) == raw
    ok &= exact
    print(f"  and reads the disk byte for byte: {exact}")

    # Reads have to be right at an offset, not just from the start: the
    # carver seeks all over an image.
    spots = all(reader.read(offset, 4096) == raw[offset:offset + 4096]
                for offset in (0, 1 << 20, len(raw) - 8192))
    ok &= spots
    print(f"  spot reads at offsets match: {spots}")
    reader.close()

    # And a whole run over it finds the same files, with their paths.
    run = os.path.join(work, "run")
    shutil.rmtree(run, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", vhd,
                    "-o", run], capture_output=True, cwd=PROJECT)
    with open(os.path.join(run, "manifest.json"), encoding="utf-8") as fh:
        found = json.load(fh)
    named = {f["source_path"] for f in found["files"] if f.get("source_path")}
    # Every live file, by its path -- not "at least one". On Windows with
    # pytsk3, v2026.11.274 recovered 8 files here with 1 path: TSK refused
    # the volume and nothing else read it, so the live files came back from
    # the carve, nameless, and this check passed.
    live = {"/DCIM/IMG_0001.JPG", "/DCIM/IMG_0002.PNG", "/DCIM/ANIM.GIF",
            "/MOVIES/VID_0003.MP4", "/MOVIES/CLIP.AVI"}
    every = live <= named
    ok &= len(found["files"]) >= 6 and every
    print(f"  a run over it recovered {len(found['files'])} file(s), "
          f"{len(named)} with their file system path")
    print(f"  every live file came back with its path: {every}"
          + ("" if every else f"  (missing {sorted(live - named)})"))

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
