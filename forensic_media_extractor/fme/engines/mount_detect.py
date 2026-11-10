"""
mount_detect.py — the drives attached to this machine, and the raw device
path backing each one.

(The FAT32-only mount scan the retired FAT32 browser used went with it in
v2026.11.319; list_attached_sources() is what the interface lists.)

Why this exists: carving unallocated space requires raw block-level reads.
Once a partition is mounted, the OS's own filesystem driver serves file
access through the mount point — that's normal file APIs, not a
byte-addressable source, and it never exposes unallocated clusters. So
"open a mounted FAT32 partition" really means: find the raw device node
backing that mount, and open *that*. This module finds the mapping.

Reading the raw device typically needs elevated privileges (root/sudo on
Linux/macOS, Administrator on Windows) even for a partition you can
already browse normally as a mounted user — that's an OS permission
model thing, not a limitation of this tool.

Only Linux has been exercised against a real mounted filesystem in
testing (see the module docstring notes in the test suite / conversation
this shipped from) — Windows and macOS logic follows documented OS
conventions but wasn't hand-verified against real hardware. Treat those
two as best-effort.
"""

import os
import platform
import subprocess


def list_attached_sources() -> list:
    """Everything on this machine that could be opened as evidence: mounted
    volumes and the raw block devices behind them.

    The FAT32 tabs want vfat mounts specifically; the media extractor takes
    any file system, plus folders, so this is the broader listing. Each entry
    says whether it is a folder to scan or a device to read, because the two
    give very different results: a mount point serves files, a device gives
    the whole volume including unallocated space.
    """
    system = platform.system()
    if system == "Linux":
        return _linux_sources()
    if system == "Darwin":
        return _macos_sources()
    if system == "Windows":
        return _windows_sources()
    return []


def _linux_sources() -> list:
    sources, seen = [], set()
    pseudo = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
              "securityfs", "debugfs", "tracefs", "pstore", "bpf", "configfs",
              "fusectl", "mqueue", "hugetlbfs", "autofs", "binfmt_misc",
              "efivarfs", "squashfs", "overlay", "ramfs", "nsfs"}
    try:
        with open("/proc/mounts") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype in pseudo or not device.startswith("/dev/"):
            continue
        if mountpoint in seen:
            continue
        seen.add(mountpoint)
        sources.append({
            "kind": "mount", "path": mountpoint, "device": device,
            "fs_type": fstype, "label": mountpoint,
            "removable": _linux_removable(device),
            "note": "scan the mounted files",
        })

    # Raw devices, whether or not anything is mounted from them.
    try:
        with open("/proc/partitions") as fh:
            rows = fh.readlines()[2:]
    except OSError:
        rows = []
    for row in rows:
        cols = row.split()
        if len(cols) < 4:
            continue
        blocks, name = cols[2], cols[3]
        path = f"/dev/{name}"
        try:
            size = int(blocks) * 1024
        except ValueError:
            size = 0
        sources.append({
            "kind": "device", "path": path, "device": path, "fs_type": "",
            "label": path, "size": size, "removable": _linux_removable(path),
            "note": "whole volume, including unallocated space (needs root)",
        })
    return sources


def _linux_removable(device: str) -> bool:
    """Whether the disk holding `device` is removable.

    The partition's own disk, found through sysfs: stripping trailing digits
    turned nvme0n1p1 into "nvme0n1p" and mmcblk0p1 into "mmcblk0p", which do
    not exist, so an SD card read as fixed.
    """
    name = os.path.basename(device)
    node = os.path.realpath(f"/sys/class/block/{name}")
    for folder in (node, os.path.dirname(node)):
        try:
            with open(os.path.join(folder, "removable")) as fh:
                return fh.read().strip() == "1"
        except OSError:
            continue
    return False


def _macos_sources() -> list:
    sources = []
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    for line in out.splitlines():
        if " on " not in line:
            continue
        device, rest = line.split(" on ", 1)
        mountpoint = rest.split(" (")[0]
        fstype = rest.split("(")[-1].split(",")[0].rstrip(")") if "(" in rest else ""
        if not device.startswith("/dev/"):
            continue
        sources.append({"kind": "mount", "path": mountpoint, "device": device,
                        "fs_type": fstype, "label": mountpoint,
                        "removable": False, "note": "scan the mounted files"})
        sources.append({"kind": "device", "path": device, "device": device,
                        "fs_type": fstype, "label": device, "removable": False,
                        "note": "whole volume, including unallocated space "
                                "(needs sudo; try the /dev/rdiskN raw node "
                                "for speed)"})
    return sources


def _windows_sources() -> list:
    sources = []
    try:
        import ctypes
        import string
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if not bitmask & (1 << i):
                continue
            root = f"{letter}:\\"
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
            # 2 = removable, 3 = fixed, 4 = network, 5 = cdrom
            if drive_type in (2, 3, 4, 5):
                sources.append({
                    "kind": "mount", "path": root, "device": f"\\\\.\\{letter}:",
                    "fs_type": {2: "removable", 3: "fixed", 4: "network",
                                5: "cdrom"}[drive_type],
                    "label": root, "removable": drive_type == 2,
                    "note": "scan the mounted files",
                })
                sources.append({
                    "kind": "device", "path": f"\\\\.\\{letter}:",
                    "device": f"\\\\.\\{letter}:", "fs_type": "",
                    "label": f"{letter}: (raw volume)", "removable": drive_type == 2,
                    "note": "whole volume, including unallocated space "
                            "(needs an Administrator prompt)",
                })
    except Exception:  # noqa: BLE001 - not Windows, or no ctypes
        pass
    for n in range(8):
        path = f"\\\\.\\PhysicalDrive{n}"
        try:
            with open(path, "rb"):
                pass
        except OSError:
            continue
        sources.append({"kind": "device", "path": path, "device": path,
                        "fs_type": "", "label": f"PhysicalDrive{n}",
                        "removable": False,
                        "note": "whole disk, all partitions (needs an "
                                "Administrator prompt)"})
    return sources
