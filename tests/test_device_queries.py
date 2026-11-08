"""A device is sized and aligned on Windows and macOS, not only on Linux.

DeviceReader asked only Linux for a sector size, so a macOS 4Kn disk was
read with 512-byte alignment; and when a seek to the end gave no size --
which is what a Windows physical drive gives -- it had no other way to
ask, so the PhysicalDriveN path its own error message recommends could
not be opened at all.

This machine has neither, so the operating system's answers are stood in
for: a pipe plays the device (seeking to its end fails, as on Windows),
and the platform's query returns what a real 2 TB 4Kn disk would.
"""
import os
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.engines import media_core  # noqa: E402

SIZE = 2 * 1000 ** 4
SECTOR = 4096


def device(fd: int):
    reader = object.__new__(media_core.DeviceReader)
    reader._fd, reader.path, reader.name = fd, "/dev/fake", "fake"
    return reader


def main() -> int:
    ok = True
    read_end, write_end = os.pipe()
    platform, fcntl = sys.platform, sys.modules.get("fcntl")
    try:
        answers = {media_core.DeviceReader._MAC_DKIOCGETBLOCKSIZE: struct.pack("I", SECTOR),
                   media_core.DeviceReader._MAC_DKIOCGETBLOCKCOUNT:
                       struct.pack("Q", SIZE // SECTOR)}
        sys.modules["fcntl"] = types.SimpleNamespace(
            ioctl=lambda fd, code, buf: answers[code])
        sys.platform = "darwin"
        mac = device(read_end)
        right = mac._sector_size() == SECTOR and mac._device_size() == SIZE
        ok &= right
        print(f"macOS: sized and aligned from the disk's own answers: {right}")

        sys.platform = "win32"
        win = device(read_end)
        geometry = struct.pack("<qIIII", 0, 12, 255, 63, SECTOR) + struct.pack("<q", SIZE)

        def answer(code, size):
            if code == media_core.DeviceReader._WIN_GET_LENGTH_INFO:
                return struct.pack("<q", SIZE)
            return geometry

        win._win_ioctl = answer
        right = win._sector_size() == SECTOR and win._device_size() == SIZE
        ok &= right
        print(f"Windows: sized and aligned from the disk's own answers: {right}")

        def refuse(code, size):
            raise OSError(1, "no")

        win._win_ioctl = refuse
        quiet = win._sector_size() == 512
        ok &= quiet
        print(f"a device that does not answer falls back to 512-byte sectors: {quiet}")
    finally:
        sys.platform = platform
        if fcntl is None:
            sys.modules.pop("fcntl", None)
        else:
            sys.modules["fcntl"] = fcntl
        os.close(read_end)
        os.close(write_end)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
