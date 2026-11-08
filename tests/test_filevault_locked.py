"""A locked APFS (FileVault) volume is reported as locked, not as empty.

A run over an APFS volume macOS had encrypted logged "Reading 1 volume(s)
the main engine cannot: apfs", then "0 file(s)", and recorded the volume as
read (vendor_volumes 1, damaged 0) with no encryption in its summary and no
limit in its report. The vendored reader had said why it listed nothing --
the volume is encrypted and its blocks are ciphertext -- and vendor_fs.walk()
kept only the count. BitLocker and LUKS were already flagged by their
headers; an APFS container has no such header, because it is the volumes
inside it that are encrypted, one by one.

The fixture is a real one, made on a Mac: tests/fixtures/apfs-filevault.*
says where it comes from and what is on it.
"""
import gzip
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core, vendor_fs  # noqa: E402
from fme.exports import report  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "apfs-filevault.sparseimage.gz")
ok = True


def check(what, passed):
    global ok
    ok &= bool(passed)
    print(f"  {what}: {bool(passed)}")


root = os.path.join(tempfile.gettempdir(), "fme_filevault-") + str(os.getpid())
shutil.rmtree(root, ignore_errors=True)
os.makedirs(root)

if not vendor_fs.available():
    print("not checked: the vendored reader is not installed here")
else:
    image = os.path.join(root, "filevault.sparseimage")
    with gzip.open(FIXTURE) as src, open(image, "wb") as dst:
        shutil.copyfileobj(src, dst)

    print("an APFS volume macOS encrypted, made on a Mac")
    locked = vendor_fs.locked_volumes(image)
    check(f"the reader names the locked volume ({[v['volume'] for v in locked]})",
          [v["volume"] for v in locked] == ["CONVVOL"])

    summary = media_core.run_extraction(image, os.path.join(root, "case"), workers=1)
    found = summary.get("encrypted_volumes") or []
    check("the run's summary records it as encrypted",
          any(v.get("volume") == "CONVVOL" and v.get("kind") == "APFS" for v in found))
    check("and recovered nothing from it, as it must not", summary.get("total_files") == 0)
    limits = report.coverage_limits({"summary": summary})
    said = next((line for line in limits if "CONVVOL" in line), "")
    check("the report says it was locked, not empty",
          "locked" in said and "not the same as it being empty" in said)
    print(f"    \"{said[:100]}...\"")
    warning = media_core._locked_message(dict(locked[0])) if locked else ""
    check("the warning says how to get at it, and promises no unlock",
          "recovery key" in warning and "does not break the encryption" in warning)

    info = media_core.inspect_image(image)
    check("inspecting the image says so before anything is run",
          any("CONVVOL" in w for w in info.get("warnings", [])))

    # What is on it, by unlocking it with the fixture's own password -- which
    # this program does not do; the vendored reader can, with pycryptodome.
    try:
        import Crypto  # noqa: F401
    except ImportError:
        print("  that it holds three files is not checked: pycryptodome is not "
              "installed here (the fixture records them)")
    else:
        import hashlib
        module = vendor_fs.probe()
        handle = module.open_image(image, None)
        opened, _ = module.unlock_apfs(handle, passwords=("qnxprobe-apfs-convert",))
        sums = {}
        for volume in module.volumes(opened):
            walker = volume.get("walker")
            for path, ino, size, _ in (module.collect(walker, walker.root) if walker else []):
                if size is not None:
                    sums[path] = hashlib.sha256(b"".join(walker.read_file(ino, size))).hexdigest()
        handle.close()
        expected = {}
        with open(os.path.join(HERE, "fixtures", "apfs-filevault.intended"), encoding="utf-8") as fh:
            for line in fh:
                if line[:1] not in ("#", "\n"):
                    digest, path = line.split()
                    expected[path] = digest
        check("unlocked with its password, it holds the three files it was made with",
              all(sums.get(p) == d or sums.get("/" + p) == d for p, d in expected.items()))

print("\nwhat is not locked is not reported as locked")


class Walker:
    root = 1

    def __init__(self, states):
        self.volumes = [(0, 0, f"V{i}", 0) for i in range(len(states))]
        self.states = states
        self.note = ""

    def encryption(self, index):
        return self.states[index]


class Module:
    def __init__(self, volumes):
        self.found = volumes

    def split_segments(self, path):
        return None

    def open_image(self, path, segments):
        return self

    def volumes(self, image):
        return self.found

    def close(self):
        pass


real_probe = vendor_fs.probe
try:
    vendor_fs.probe = lambda: Module([
        {"kind": "apfs", "base": 4096, "label": "p1", "walker": Walker(
            [None, "clear", "unlocked", "locked"])},
        {"kind": "ntfs", "base": 9, "label": "p2", "walker": Walker(["locked"])},
        {"kind": "apfs", "base": 8192, "label": "p3", "walker": None}])
    found = vendor_fs.locked_volumes("x")
    check("of not encrypted, read in the clear, opened and locked: only the "
          "locked one", [v["volume"] for v in found] == ["V3"])
    check("a walker that is not APFS is not asked", all(v["partition"] == "p1" for v in found))
    vendor_fs.probe = lambda: None
    check("no vendored reader: nothing, and no error", vendor_fs.locked_volumes("x") == [])
finally:
    vendor_fs.probe = real_probe

shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
