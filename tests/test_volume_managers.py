"""LVM physical volumes and Windows dynamic disks are named, not passed over.

Neither TSK nor this program opens those volume managers, so the volumes
inside them -- and any BitLocker or LUKS volume there -- were reached only by
the carve, and nothing said why a run found so little. They are now found
by partition type, GPT type GUID and the LVM label itself, and named in the
inspect warnings, the run summary and the report.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import contextlib
import glob
import io
import logging
import os
import shutil
import struct
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core
from fme.exports import report

root = tempfile.mkdtemp(prefix="fme_vm-")
ok = True


def write(name, disk):
    path = os.path.join(root, name)
    with open(path, "wb") as fh:
        fh.write(bytes(disk))
    return path


def kinds(path):
    with contextlib.redirect_stdout(io.StringIO()):
        info = media_core.inspect_image(path)
    return [v["kind"] for v in info.get("unopened_containers", [])], info


lvm = bytearray(8 << 20)
struct.pack_into("<B3sB3sII", lvm, 446, 0, b"", 0x83, b"", 2048, 8192)   # typed "Linux"
lvm[510:512] = b"\x55\xaa"
lvm[2048 * 512 + 512:2048 * 512 + 520] = b"LABELONE"
lvm[2048 * 512 + 512 + 24:2048 * 512 + 512 + 32] = b"LVM2 001"
found, _ = kinds(write("lvm.dd", lvm))
right = found == ["LVM physical volume"]
ok &= right
print(f"an LVM physical volume, found by its own label: {right} {found}")

dyn = bytearray(4 << 20)
struct.pack_into("<B3sB3sII", dyn, 446, 0, b"", 0x42, b"", 2048, 4096)
dyn[510:512] = b"\x55\xaa"
found, _ = kinds(write("dynamic.dd", dyn))
right = found == ["Windows dynamic disk"]
ok &= right
print(f"an MBR dynamic disk: {right} {found}")

gpt = bytearray(4 << 20)
struct.pack_into("<B3sB3sII", gpt, 446, 0, b"", 0xEE, b"", 1, 8191)
gpt[510:512] = b"\x55\xaa"
header = bytearray(92)
header[:8] = b"EFI PART"
struct.pack_into("<QII", header, 72, 2, 128, 128)
gpt[512:604] = header
gpt[1024:1040] = uuid.UUID("af9b60a0-1431-4f62-bc68-3311714a69ad").bytes_le
struct.pack_into("<Q", gpt, 1024 + 32, 2048)
path = write("gpt_ldm.dd", gpt)
found, info = kinds(path)
right = found == ["Windows dynamic disk (LDM data)"] and any("dynamic disk" in w for w in info["warnings"])
ok &= right
print(f"a GPT dynamic disk, with a warning: {right} {found}")

with contextlib.redirect_stdout(io.StringIO()):
    summary = media_core.run_extraction(path, os.path.join(root, "out"), mode="fs")
right = [c["kind"] for c in summary.get("unopened_containers", [])] == found
ok &= right
print(f"recorded in the run summary: {right}")
with contextlib.redirect_stdout(io.StringIO()):
    report.build(os.path.join(root, "out"), os.path.join(root, "rep"), formats=["html"])
html = "".join(open(p, encoding="utf-8").read() for p in glob.glob(os.path.join(root, "rep", "**", "*.html"), recursive=True))
right = "dynamic disk" in html and "were not opened" in html
ok &= right
print(f"and named in the report: {right}")
plain = bytearray(4 << 20)
struct.pack_into("<B3sB3sII", plain, 446, 0, b"", 0x0C, b"", 2048, 4096)
plain[510:512] = b"\x55\xaa"
found, _ = kinds(write("plain.dd", plain))
ok &= not found
print(f"an ordinary partition says nothing: {not found}")
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
