"""
image_readers.py -- Section 1 -- the image access layer: raw, split, device, EWF, the vendored
acquisition reader, and open_image().

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import os
import re
import struct
import threading
import sys
from typing import Callable, Iterator, Optional, Sequence
from fme.engines import vendor_fs
from fme.engines.engine_common import (  # noqa: F401
    LOG, pyewf, pyvmdk,
)




# ==========================================================================
# SECTION 1 -- Image access layer
# ==========================================================================
class ImageReader:
    """Read-only random access to a forensic image, whatever its container."""

    name: str = "image"
    size: int = 0

    def __init_subclass__(cls, **kwargs):
        """Every reader's read() runs under a lock of its own.

        A read is a seek and then a read on one open file, and only the
        vendored reader locked the pair; the raw, segmented, device, libyal
        and source readers did not, so two threads reading one reader could
        each seek before the other read, and get each other's bytes. Nothing
        shares a reader between threads today, which is exactly when a lock
        is cheap to add and easy to forget. Wrapped here, so a reader added
        later is covered without anyone remembering to.
        """
        super().__init_subclass__(**kwargs)
        own = cls.__dict__.get("read")
        if own is None or getattr(own, "_locked", False):
            return

        def read(self, offset, length, _own=own):
            lock = self.__dict__.get("_read_lock")
            if lock is None:
                lock = self.__dict__.setdefault("_read_lock", threading.RLock())
            with lock:
                return _own(self, offset, length)

        read._locked = True
        read.__doc__ = own.__doc__
        cls.read = read

    def read(self, offset: int, length: int) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # Convenience: clamped read used by the carver's parsers.
    def read_clamped(self, offset: int, length: int) -> bytes:
        if offset >= self.size or offset < 0:
            return b""
        return self.read(offset, min(length, self.size - offset))


class RawImageReader(ImageReader):
    """Single or split raw/dd image (.raw .dd .img .bin .001/.002/...)."""

    def __init__(self, path: str):
        self.paths = self._expand_segments(path)
        self._fh = [open(p, "rb") for p in self.paths]
        self._lengths = [os.path.getsize(p) for p in self.paths]
        self._starts = []
        running = 0
        for ln in self._lengths:
            self._starts.append(running)
            running += ln
        self.size = running
        self.name = os.path.basename(path)
        if len(self.paths) > 1:
            LOG.info("Raw image: %d segments, %s total", len(self.paths), human(self.size))

    @staticmethod
    def _expand_segments(path: str) -> list[str]:
        """Detect .001/.002... or .aa/.ab style split raw sets.

        Three ways this used to give a wrong image without a word: a set
        with a missing middle segment stopped at the gap, so the image was
        simply shorter; a set opened from its second segment started there,
        so every offset in it was wrong; and .aa/.ab sets, though this said
        it detected them, were read as a single file. Each now refuses, and
        says what to open instead -- a split set read wrongly is worse than
        one not read.
        """
        folder = os.path.dirname(os.path.abspath(path))
        name = os.path.basename(path)
        numbered = re.match(r"^(?P<base>.*)\.(?P<num>\d{3})$", name)
        lettered = re.match(r"^(?P<base>.*)\.(?P<suf>[a-z]{2})$", name)
        if numbered:
            base = numbered.group("base")
            index_of = int
            first = int(numbered.group("num"))
            pattern = re.compile(re.escape(base) + r"\.(\d{3})$")
            spell = lambda n: f"{n:03d}"              # noqa: E731
        elif lettered and os.path.isfile(os.path.join(
                folder, lettered.group("base") + ".aa")):
            base = lettered.group("base")

            def index_of(text):
                return (ord(text[0]) - 97) * 26 + ord(text[1]) - 97

            first = index_of(lettered.group("suf"))
            pattern = re.compile(re.escape(base) + r"\.([a-z]{2})$")

            def spell(n):
                return chr(97 + n // 26) + chr(97 + n % 26)
        else:
            return [path]
        try:
            present = sorted(index_of(m.group(1)) for m in
                             (pattern.match(f) for f in os.listdir(folder)) if m)
        except OSError:
            present = [first]
        if len(present) <= 1:
            return [path]
        lowest, highest = present[0], present[-1]
        if first != lowest:
            raise RuntimeError(
                f"{name} is not the first segment of its split image: "
                f"{base}.{spell(lowest)} is. Open that one, so the image "
                f"starts where it should.")
        missing = sorted(set(range(lowest, highest + 1)) - set(present))
        if missing:
            raise RuntimeError(
                f"The split image {base}.{spell(lowest)} is missing segment(s) "
                f"{', '.join(base + '.' + spell(n) for n in missing[:5])}"
                f"{' ...' if len(missing) > 5 else ''}. Read without them "
                f"everything after the gap would be in the wrong place. Copy "
                f"the missing segment(s) beside the others.")
        return [os.path.join(folder, f"{base}.{spell(n)}")
                for n in range(lowest, highest + 1)]

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0 or offset >= self.size:
            return b""
        length = min(length, self.size - offset)
        out = bytearray()
        for fh, start, ln in zip(self._fh, self._starts, self._lengths):
            if offset >= start + ln:
                continue
            if len(out) >= length:
                break
            local = offset + len(out) - start
            if local < 0:
                break
            fh.seek(local)
            chunk = fh.read(length - len(out))
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def close(self) -> None:
        for fh in self._fh:
            try:
                fh.close()
            except OSError:
                pass


class DeviceReader(ImageReader):
    r"""A live block device: a plugged-in USB stick, a write-blocked disk, a
    physical drive on Windows.

    Two things make a device different from an image file. Its size is not in
    the directory entry, so it has to be asked for with a seek to the end (or
    an ioctl when that returns zero, as it does for some character devices).
    And reads must be sector-aligned: Windows rejects an unaligned read on
    \\.\PhysicalDrive outright, and Linux O_DIRECT-style paths do the same, so
    every request is widened to sector boundaries and trimmed afterwards.

    The device is opened read-only. That is not a substitute for a hardware
    write blocker: the OS can still touch a mounted volume behind your back.
    Unmount it, or use a blocker, before imaging anything that matters.
    """

    def __init__(self, path: str):
        self.name = os.path.basename(path.rstrip("\\/")) or path
        self.path = path
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        self._fd = os.open(path, flags)
        self.sector = self._sector_size()
        self.size = self._device_size()
        if self.size <= 0:
            os.close(self._fd)
            raise RuntimeError(
                f"Could not determine the size of {path}. On Windows use an "
                f"admin prompt and a path like \\\\.\\PhysicalDrive1; on Linux "
                f"and macOS the device usually needs root.")
        LOG.info("Block device %s: %s, %d-byte sectors",
                 path, human(self.size), self.sector)

    # Block-device queries. Linux and macOS answer through ioctl, Windows
    # through DeviceIoControl; each is tried only on its own platform.
    _LINUX_BLKSSZGET = 0x1268
    _LINUX_BLKGETSIZE64 = 0x80081272
    _MAC_DKIOCGETBLOCKSIZE = 0x40046418          # _IOR('d', 24, uint32_t)
    _MAC_DKIOCGETBLOCKCOUNT = 0x40086419         # _IOR('d', 25, uint64_t)
    _WIN_GET_LENGTH_INFO = 0x0007405C            # IOCTL_DISK_GET_LENGTH_INFO
    _WIN_GET_GEOMETRY_EX = 0x000700A0            # IOCTL_DISK_GET_DRIVE_GEOMETRY_EX

    def _sector_size(self) -> int:
        """The device's logical sector size, for aligning every read.

        Only Linux was asked. A macOS disk fell back to 512 bytes, so on a
        4Kn drive every read was misaligned for the raw device; Windows,
        whose physical-drive reads must be sector-aligned, was not asked
        either.
        """
        value = 0
        try:
            if sys.platform == "win32":
                raw = self._win_ioctl(self._WIN_GET_GEOMETRY_EX, 32)
                value = struct.unpack_from("<I", raw, 20)[0]   # BytesPerSector
            else:
                import fcntl
                code = (self._MAC_DKIOCGETBLOCKSIZE if sys.platform == "darwin"
                        else self._LINUX_BLKSSZGET)
                value = struct.unpack("I", fcntl.ioctl(self._fd, code, b"\x00" * 4))[0]
        except Exception:  # noqa: BLE001 - not a block device, or no answer
            value = 0
        return value if value in (512, 1024, 2048, 4096) else 512

    def _device_size(self) -> int:
        """The device's size in bytes.

        A seek to the end answers for an image file and for most Linux
        devices. It does not for a Windows physical drive, where the
        program had no other way to ask -- so a PhysicalDriveN path, the
        one this class's own error message tells examiners to use, could
        not be opened at all.
        """
        try:
            size = os.lseek(self._fd, 0, os.SEEK_END)
            if size > 0:
                return size
        except OSError:
            pass
        try:
            if sys.platform == "win32":
                raw = self._win_ioctl(self._WIN_GET_LENGTH_INFO, 8)
                return struct.unpack("<q", raw[:8])[0]
            import fcntl
            if sys.platform == "darwin":
                block = struct.unpack("I", fcntl.ioctl(
                    self._fd, self._MAC_DKIOCGETBLOCKSIZE, b"\x00" * 4))[0]
                count = struct.unpack("Q", fcntl.ioctl(
                    self._fd, self._MAC_DKIOCGETBLOCKCOUNT, b"\x00" * 8))[0]
                return block * count
            buf = fcntl.ioctl(self._fd, self._LINUX_BLKGETSIZE64, b"\x00" * 8)
            return struct.unpack("Q", buf)[0]
        except Exception:  # noqa: BLE001
            pass
        try:
            return os.stat(self.path).st_size
        except OSError:
            return 0

    def _win_ioctl(self, code: int, out_size: int) -> bytes:
        """DeviceIoControl on this device's handle; raises OSError on failure."""
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(self._fd)
        out = ctypes.create_string_buffer(out_size)
        returned = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            wintypes.HANDLE(handle), wintypes.DWORD(code), None, 0, out,
            out_size, ctypes.byref(returned), None)
        if not ok:
            raise OSError(ctypes.get_last_error(), "DeviceIoControl failed")
        return out.raw[:returned.value]

    # Sectors read one at a time after a read fails, in runs of this many,
    # so one bad sector costs one sector of data rather than the request.
    RETRY_RUN = 64

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0 or offset >= self.size:
            return b""
        length = min(length, self.size - offset)
        start = (offset // self.sector) * self.sector
        end = min(self.size,
                  ((offset + length + self.sector - 1) // self.sector) * self.sector)
        try:
            raw = self._read_span(start, end - start)
        except OSError as exc:
            # A failed read used to return nothing for the whole request --
            # megabytes, for the carve -- logged at debug level only, and the
            # carve took the empty answer as the end of the image. Now the
            # span is read again a run of sectors at a time, the sectors that
            # still fail are read as zeros, and where they are is kept in
            # bad_ranges for the report.
            LOG.warning("Read error on %s at %d (%s); retrying sector by sector",
                        self.path, start, exc)
            raw = self._read_salvaging(start, end - start)
        skip = offset - start
        return bytes(raw[skip:skip + length])

    def _read_span(self, start: int, size: int) -> bytearray:
        os.lseek(self._fd, start, os.SEEK_SET)
        raw = bytearray()
        while len(raw) < size:
            chunk = os.read(self._fd, size - len(raw))
            if not chunk:
                break
            raw += chunk
        return raw

    def _read_salvaging(self, start: int, size: int) -> bytearray:
        raw = bytearray()
        step = self.sector * self.RETRY_RUN
        pos = start
        while pos < start + size:
            run = min(step, start + size - pos)
            try:
                raw += self._padded(pos, run)
            except OSError:
                for sector in range(pos, pos + run, self.sector):
                    width = min(self.sector, start + size - sector)
                    try:
                        raw += self._padded(sector, width)
                    except OSError:
                        raw += b"\x00" * width
                        self._mark_bad(sector, width)
            pos += run
        return raw

    def _padded(self, pos: int, width: int) -> bytearray:
        """A span read, with whatever the device did not return as zeros --
        and those zeros marked bad. A short answer inside the device's size
        is as unreadable as an error; padding it unmarked put zeros into
        files and hashes as if they were the disk's own bytes."""
        got = self._read_span(pos, width)
        if len(got) < width:
            self._mark_bad(pos + len(got), width - len(got))
            got = got.ljust(width, b"\x00")
        return got

    def _mark_bad(self, start: int, width: int) -> None:
        bad = self.__dict__.setdefault("bad_ranges", [])
        if bad and bad[-1][0] + bad[-1][1] == start:
            bad[-1] = (bad[-1][0], bad[-1][1] + width)
        else:
            bad.append((start, width))
        # Every zero-fill as it happens, never merged or cleared. bad_ranges
        # is where the disk is bad (and a carve worker empties it once
        # reported); this is what each read was handed, so a caller can ask
        # "did the bytes I just read include made-up zeros?" -- see
        # _zero_mark. A sector read twice is logged twice, as it should be:
        # each file that read it holds the zeros.
        self.__dict__.setdefault("zero_log", []).append((start, width))

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


def read_ufd_segments(path: str) -> list:
    """Resolve a Cellebrite .ufd file to the dump chunks it describes.

    A .ufd is an INI-style manifest written beside the binary chunks of a
    physical extraction. Key names vary between UFED versions, so rather than
    matching on any one of them this takes every value that names a file
    existing next to the .ufd, keeps the binary ones in the order they appear,
    and falls back to the sibling chunks on disk if the manifest lists none.

    The chunks are then read as a split raw image, which is what they are.
    """
    folder = os.path.dirname(os.path.abspath(path))
    names, seen = [], set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith((";", "#", "[")):
            continue
        value = line.split("=", 1)[1].strip().strip('"')
        if not value:
            continue
        candidate = os.path.join(folder, os.path.basename(value))
        if candidate in seen or not os.path.isfile(candidate):
            continue
        if os.path.splitext(candidate)[1].lower() not in (
                ".bin", ".dd", ".img", ".raw", ""):
            continue
        seen.add(candidate)
        names.append(candidate)

    if not names:
        stem = os.path.splitext(os.path.basename(path))[0]
        # In number order, not text order: sorted() put dump10.bin before
        # dump2.bin, and the chunks were joined out of order without a word.
        siblings = [os.path.join(folder, f)
                    for f in sorted(os.listdir(folder), key=_natural_key)
                    if f.lower().endswith((".bin", ".dd", ".img"))
                    and f.startswith(stem[:6])]
        if siblings:
            LOG.warning("%s names no chunk the program recognizes; reading the "
                        "%d file(s) beside it whose names start like it, in "
                        "number order: %s", os.path.basename(path),
                        len(siblings), ", ".join(os.path.basename(f)
                                                 for f in siblings[:6]))
        names = siblings
    return names


def _natural_key(name: str) -> list:
    """Sort key that puts dump2 before dump10."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part.lower())
            for part in re.split(r"(\d+)", name)]


class SegmentedReader(ImageReader):
    """An explicit list of files read as one contiguous image."""

    def __init__(self, paths: list, name: str = ""):
        if not paths:
            raise RuntimeError("No data chunks to read")
        self.paths = list(paths)
        self._fh = [open(p, "rb") for p in self.paths]
        self._lengths = [os.path.getsize(p) for p in self.paths]
        self._starts, running = [], 0
        for length in self._lengths:
            self._starts.append(running)
            running += length
        self.size = running
        self.name = name or os.path.basename(self.paths[0])
        LOG.info("Segmented image: %d chunk(s), %s total",
                 len(self.paths), human(self.size))

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0 or offset >= self.size:
            return b""
        length = min(length, self.size - offset)
        out = bytearray()
        for fh, start, size in zip(self._fh, self._starts, self._lengths):
            if offset + len(out) >= start + size:
                continue
            if len(out) >= length:
                break
            local = offset + len(out) - start
            if local < 0:
                break
            fh.seek(local)
            chunk = fh.read(length - len(out))
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def close(self) -> None:
        for fh in self._fh:
            try:
                fh.close()
            except OSError:
                pass


def is_device_path(path: str) -> bool:
    """Block/character device, or a Windows raw path."""
    if path.startswith("\\\\.\\") or path.startswith("//./"):
        return True
    try:
        import stat as _stat
        mode = os.stat(path).st_mode
        return _stat.S_ISBLK(mode) or _stat.S_ISCHR(mode)
    except OSError:
        return False


class LibyalImageReader(ImageReader):
    """Wraps any libyal-style handle (pyewf, pyvmdk, pyvhdi, pyqcow)."""

    def __init__(self, handle, size: int, name: str, extra=None):
        self._h = handle
        self.size = size
        self.name = name
        self._extra = extra or []

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0 or offset >= self.size:
            return b""
        self._h.seek(offset)
        return self._h.read(min(length, self.size - offset))

    def close(self) -> None:
        try:
            self._h.close()
        except Exception:  # noqa: BLE001
            pass
        for obj in self._extra:
            try:
                obj.close()
            except Exception:  # noqa: BLE001
                pass


class SourceReader(ImageReader):
    """Adapts a fat32_core ImageSource (stateful seek/read) to the random
    access interface used here, so an already-open session -- including an
    OffsetSource pointed at one partition -- can be handed straight over."""

    def __init__(self, source, name: str = "source", size: Optional[int] = None):
        self._s = source
        self.name = name
        if size is None:
            try:
                size = int(source.get_size())
            except Exception as exc:  # noqa: BLE001
                # Reads still work unbounded, but everything sized by
                # reader.size -- the carve, read_clamped -- sees an empty
                # source. Said here; the carve says so in the summary.
                LOG.warning("%s did not report its size (%s); it cannot be "
                            "carved", name, exc)
                size = 0
        self.size = size

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0:
            return b""
        if self.size and offset >= self.size:
            return b""
        if self.size:
            length = min(length, self.size - offset)
        self._s.seek(offset)
        return self._s.read(length)

    def close(self) -> None:
        pass  # the caller owns the underlying source


class BytesReader(ImageReader):
    """A file already in memory, read the way an image is.

    What lets the embedded scan judge a file inside a container with the
    same `_carve_candidate` the carve uses, rather than a copy of it.
    """

    def __init__(self, data: bytes, name: str = "bytes"):
        self._data = data
        self.name = name
        self.size = len(data)

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0:
            return b""
        return self._data[offset:offset + length]


def wrap_source(source, name: str = "source", size: Optional[int] = None) -> ImageReader:
    """Public entry point for fat32_core ImageSource interop."""
    return SourceReader(source, name=name, size=size)


class VendorImageReader(ImageReader):
    """An acquisition opened by the vendored reader.

    It presents the acquired disk as an ordinary seekable file, so the rest
    of this program reads an E01, an AFF4 or a virtual disk exactly as it
    reads a raw image. Two things that buys:

    It needs nothing installed. The libyal modules this program otherwise
    depends on for these formats are C extensions that frequently will not
    build on a Windows examination machine, and an examiner who cannot read
    an E01 cannot start.

    And it reads formats this program has no other reader for at all: AFF,
    AFD, AFF4, Apple .dmg, .sparseimage and sparse bundles, L01 and AD1
    logical evidence, and VHD, VHDX, VMDK and QCOW virtual disks.
    """

    def __init__(self, path: str, password: str = "", private_key: str = "",
                 credentials=None):
        from fme.engines import unlock, vendor_fs

        module = vendor_fs.ewfprobe()
        if module is None:
            raise RuntimeError("the vendored acquisition reader is not present")
        segments = None
        try:
            segments = module.split_segments(path)
        except Exception:  # noqa: BLE001 - not a split set
            segments = None
        # The examiner's passwords and private keys, tried in turn: an
        # encrypted AFF, an AD-encrypted E01 or raw set, an AFF sealed to a
        # certificate, an encrypted Apple disk image. The parameters were
        # here and nothing passed them, so a run given the right password
        # still refused the evidence (v2026.11.315).
        passwords = [password] if password else []
        keys = [private_key] if private_key else []
        if credentials:
            passwords += list(credentials.passwords)
            keys += unlock.private_keys(credentials)
        last = None
        for pw in passwords or [None]:
            for key in keys or [None]:
                try:
                    self._image = module.open_image(path, segments, password=pw,
                                                    private_key=key)
                    last = None
                    break
                except Exception as exc:  # noqa: BLE001 - try the next key
                    if not unlock.is_password_error(exc):
                        raise
                    last = exc
            if last is None:
                break
        if last is not None:
            raise last
        self.name = os.path.basename(path)
        self._lock = threading.Lock()
        try:
            self.size = self._image.seek(0, os.SEEK_END)
        finally:
            self._image.seek(0)

    def read(self, offset: int, length: int) -> bytes:
        # One handle, read from several threads during a parallel carve, so
        # the seek and the read have to happen together.
        with self._lock:
            self._image.seek(offset)
            return self._image.read(length)

    def close(self) -> None:
        try:
            self._image.close()
        except Exception:  # noqa: BLE001
            pass


class FileObjectReader(ImageReader):
    """An already-open, seekable image -- an assembled RAID array, say --
    read the way this program reads any other. The caller closes it."""

    def __init__(self, handle, name: str):
        self._h = handle
        self.name = name
        self._lock = threading.Lock()
        try:
            self.size = handle.seek(0, os.SEEK_END)
        finally:
            handle.seek(0)

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0 or offset >= self.size:
            return b""
        with self._lock:
            self._h.seek(offset)
            return self._h.read(min(length, self.size - offset))

    def close(self) -> None:
        pass


# Containers the vendored reader opens that nothing else here does.
VENDOR_ONLY_EXTENSIONS = {
    ".aff", ".aff4", ".afm", ".afd", ".dmg", ".dmgpart", ".sparseimage",
    ".ad1", ".vhd", ".vhdx", ".qcow", ".qcow2", ".qed",
}


EWF_PROBE = 64 * 1024


def _ewf_samples(reader, size: int) -> list:
    """64 KiB from the start, the middle and the end of the media."""
    spots = sorted({0, max(0, size // 2 - EWF_PROBE // 2), max(0, size - EWF_PROBE)})
    return [reader.read(spot, min(EWF_PROBE, size - spot)) for spot in spots]


def _ewf_reads_nothing(path: str, libewf_reader, size: int):
    """The vendored reader, if libewf opened this E01 and reads nothing from it.

    libewf can accept an acquisition, report its size and return nothing but
    zeros: it does so for the E01 this project builds as a test fixture
    (verify.py, test_known_answer.py on any machine with libewf). Nothing
    noticed. The run carved an empty disk and reported no files -- the one
    outcome an examiner cannot tell from a blank drive. A blank start, middle
    and end is now compared with the vendored reader's: if it reads data
    there, libewf did not, and the run uses the vendored reader and says so.
    Both reading zeros is a blank drive, and libewf keeps it. Costs three
    small reads; the second reader is opened only when all three are zeros.
    """
    try:
        if any(any(chunk) for chunk in _ewf_samples(libewf_reader, size)):
            return None
    except Exception as exc:  # noqa: BLE001 - libewf could not read its own media
        LOG.warning("libewf opened %s but could not read it: %s",
                    os.path.basename(path), exc)
    try:
        other = VendorImageReader(path)
        theirs = _ewf_samples(other, min(size, other.size))
    except Exception:  # noqa: BLE001 - libewf's zeros stand unchallenged
        return None
    if not any(any(chunk) for chunk in theirs):
        other.close()
        return None                     # a blank drive, read so by both
    libewf_reader.close()
    LOG.warning("libewf opened %s and read only zeros where the vendored "
                "reader reads data. This run uses the vendored reader. Hash "
                "the media with the acquisition tool's verification before "
                "relying on it.", os.path.basename(path))
    return other


def _open_ewf(path: str, credentials=None) -> ImageReader:
    """An EWF acquisition by libewf, or by the vendored reader if libewf
    refuses it or reports no media in it."""
    handle = None
    try:
        filenames = pyewf.glob(path)
        handle = pyewf.handle()
        handle.open(filenames)
        size = handle.get_media_size()
        if not size:
            raise RuntimeError("libewf opened it and reports no media")
        reader = LibyalImageReader(handle, size, os.path.basename(path))
        instead = _ewf_reads_nothing(path, reader, size)
        if instead is not None:
            handle = None               # closed by the check, which says why
            return instead
        LOG.info("EWF image: %d segment file(s), read by libewf",
                 len(filenames))
        return reader
    except Exception as exc:  # noqa: BLE001 - try the other reader
        # The handle libewf opened is closed before the other reader opens
        # the same segments. It was left open, and on Windows an open
        # handle is what keeps an examiner's evidence drive from detaching.
        if handle is not None:
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - it never opened
                pass
        LOG.warning("libewf would not read %s: %s",
                    os.path.basename(path), exc)
        try:
            reader = VendorImageReader(path, credentials=credentials)
        except Exception:  # noqa: BLE001 - neither could
            raise RuntimeError(
                f"{os.path.basename(path)} could not be read. libewf "
                f"refused it ({exc}) and so did the vendored reader. "
                f"If the acquisition is sound, check its segments are "
                f"all present and named as the tool wrote them."
            ) from exc
        LOG.warning("    the vendored reader opened it instead; this "
                    "run used that reader, not libewf")
        return reader


def is_aff4(path: str) -> bool:
    """An AFF4 acquisition (Standard v1, Evimetry, pyaff4), told apart from
    an ordinary ZIP by what it holds, not by its name.

    An AFF4 is a ZIP container: a disk image stored as chunked streams under
    aff4:// names, described by information.turtle. Read as a package it
    listed seven members and never read the disk inside -- an empty run,
    reported as a package examined in full (v2026.11.314).
    """
    import zipfile
    try:
        if not os.path.isfile(path) or not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile, ValueError):
        return False
    return ("information.turtle" in names or "container.description" in names
            or any(name.lower().startswith("aff4%3a") for name in names))


def open_image(path: str, credentials=None) -> ImageReader:
    """Open any supported forensic container by extension, then by content."""
    if is_device_path(path):
        return DeviceReader(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path.rstrip("/\\"))[1].lower()
    if os.path.isdir(path) and (ext == ".sparsebundle"
                                or os.path.isdir(os.path.join(path, "bands"))):
        # An Apple sparse bundle is a folder by design. It was refused here as
        # "a folder" before the vendored reader, which reads it, was asked:
        # the branch below that offered it a folder could never be reached.
        try:
            return VendorImageReader(path, credentials=credentials)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"{os.path.basename(path)} would not open as a "
                               f"sparse bundle: {exc}") from exc
    if os.path.isdir(path):
        raise IsADirectoryError(
            f"{path} is a folder. Folders are scanned file by file rather "
            f"than opened as an image -- run the extraction against it "
            f"directly and the folder engine handles it.")

    if ext == ".ufd":
        segments = read_ufd_segments(path)
        if not segments:
            raise RuntimeError(
                f"{path} lists no readable dump chunks. Point the tool at the "
                f".bin chunk directly if you know which it is.")
        return SegmentedReader(segments, name=os.path.basename(path))

    if ext in VENDOR_ONLY_EXTENSIONS:
        try:
            return VendorImageReader(path, credentials=credentials)
        except Exception as exc:  # noqa: BLE001
            # An acquisition that will not open is a normal thing to meet
            # -- a truncated copy, a wrong extension, a format this does
            # not read. What an examiner met before was a Python traceback
            # and no manifest, which says nothing about what to do.
            raise RuntimeError(
                f"{os.path.basename(path)} would not open as "
                f"{ext.lstrip('.') or 'an acquisition'}: {exc}\n\n"
                f"  If the file is sound, it may be a format this does not "
                f"read, or\n  the copy may be truncated -- check its size "
                f"against the original.\n  To look for media in it as "
                f"plain bytes instead, run with --mode carve\n  against a "
                f"copy renamed to .dd, which ignores the container "
                f"entirely.") from exc

    if ext in (".e01", ".ex01", ".s01", ".l01", ".lx01"):
        # FME_NO_LIBEWF=1, like FME_NO_TSK, reads by the vendored reader alone:
        # the known-answer test reads its E01 each way and compares.
        if pyewf is None or os.environ.get("FME_NO_LIBEWF") == "1":
            # No libewf: the vendored reader opens it anyway, which is the
            # difference between an examiner working and an examiner stuck
            # on a machine where a C extension will not build.
            try:
                return VendorImageReader(path, credentials=credentials)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"This E01 could not be opened. libewf is not installed "
                    f"(pip install libewf-python), and the vendored reader "
                    f"refused it: {exc}") from exc
        # libewf first, because it is the reference implementation and
        # reads what the acquisition tools write. But if it refuses the
        # file, or opens it and reports nothing in it, the vendored
        # reader is tried before giving up.
        #
        # An examiner whose acquisition libewf will not take should get
        # whatever the other reader can give them and a line saying
        # which read it -- not an empty run. Found when an E01 this
        # project wrote was read by the vendored reader and refused by
        # libewf: the fixture was at fault, but the lesson is that one
        # reader refusing is not the end of the evidence.
        return _open_ewf(path, credentials)

    if ext == ".vmdk":
        if pyvmdk is None:
            # The vendored reader reads VMDK too; libvmdk is preferred, not
            # required.
            try:
                return VendorImageReader(path, credentials=credentials)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"{os.path.basename(path)} could not be read: libvmdk is not "
                    f"installed (pip install libvmdk-python) and the vendored "
                    f"reader refused it: {exc}") from exc
        handle = pyvmdk.handle()
        handle.open(path)
        handle.open_extent_data_files()
        return LibyalImageReader(handle, handle.get_media_size(), os.path.basename(path))

    # .vhd, .vhdx, .qcow, .qcow2, .aff, .aff4 and .ad1 are in
    # VENDOR_ONLY_EXTENSIONS and were answered above. Branches for them here
    # -- one saying AFF4 and AD1 "are not supported", which the vendored
    # reader does read -- could never be reached and are gone.

    # An AFF4 is a ZIP, renamed or not: the acquisition reader opens it by
    # content. Before v2026.11.315 a ZIP was taken for a phone package
    # before this function was ever asked (sources.is_package).
    if is_aff4(path):
        return VendorImageReader(path, credentials=credentials)

    # Default: treat as raw. Sniff for an EWF header that was misnamed.
    with open(path, "rb") as fh:
        magic = fh.read(8)
    if magic[:3] in (b"EVF", b"LVF") or magic[:8] == b"EVF2\x0d\x0a\x81\x00":
        if pyewf is None or os.environ.get("FME_NO_LIBEWF") == "1":
            # Misnamed, but an E01 all the same: read as the .e01 branch
            # reads one without libewf.
            return VendorImageReader(path, credentials=credentials)
        # And with libewf, read as the .e01 branch reads one too: this path
        # had no fallback to the vendored reader and no check that libewf
        # found any media, so a misnamed E01 libewf refused was a traceback
        # where a correctly named one was read.
        return _open_ewf(path, credentials)

    return RawImageReader(path)


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"
