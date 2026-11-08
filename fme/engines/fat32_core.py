"""
fat32_core.py

Core FAT32 parsing, cluster-chain walking, and signature-based carving,
built over a pluggable ImageSource so the same logic works against:

  - Raw / dd images (.img, .dd, .raw, or a raw block device path)
  - Forensic EWF images (.E01, .E02, ... / .Ex01 EnCase segments) via the
    optional `pyewf` binding (pip install pyewf-python / libewf-python).
    If pyewf isn't installed, E01 images are simply reported as
    unsupported at open time — the rest of the tool still works on raw
    images.

It also auto-detects an MBR partition table and locates the first FAT32
partition, since forensic acquisitions are normally *whole-disk* images,
not bare volume images. If no MBR is found, the image is treated as a
bare FAT32 volume starting at byte 0 (this matches the old behavior).

No third-party dependency is required for raw-image support. pyewf is
optional and only needed for .E01 images.
"""

from __future__ import annotations

from array import array
import glob as _glob
import logging
import os
import re
import struct
import sys
import threading
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional, Protocol

LOG = logging.getLogger("fme")

try:
    import pyewf  # type: ignore
    HAVE_PYEWF = True
except ImportError:
    HAVE_PYEWF = False


# --------------------------------------------------------------------------
# Image source abstraction
# --------------------------------------------------------------------------

# A source's seek() and read() are two calls on one file position, so two
# threads sharing a source can each seek and then read where the other
# seeked. The FAT32 interface does exactly that -- one Fat32FileSystem per
# session, read by the carve thread and by every request thread at once --
# and a cluster read that way is another cluster's bytes, written out under
# this one's name. read_at() is the one safe way in: positioned reads
# (os.pread) where the platform has them, a lock round the pair where not.
# seek()/read() remain for code that owns its source alone.


def read_at(source, offset: int, size: int, lock=None) -> bytes:
    """`size` bytes at `offset` of any source, safe to call from threads.

    Uses the source's own read_at() when it has one. A source with only
    seek()/read() -- media_core's _ReaderSource, a caller's own -- is read
    under `lock`, which every thread reading that source must share.
    """
    own = getattr(source, "read_at", None)
    if own is not None:
        return own(offset, size)
    if lock is None:
        raise ValueError("a seek/read source shared between threads needs a lock")
    with lock:
        source.seek(offset)
        return source.read(size)


class ImageSource(Protocol):
    def seek(self, offset: int) -> None: ...
    def read(self, size: int) -> bytes: ...
    def get_size(self) -> int: ...
    def close(self) -> None: ...


class RawFileSource:
    """A plain dd/img file or block device — including a Windows raw
    volume handle like \\\\.\\E: or a raw physical drive like
    \\\\.\\PhysicalDrive0.

    Size is probed lazily and defensively: Windows volume/device handles
    reject a plain seek-to-end (SetFilePointer with FILE_END raises
    ERROR_INVALID_PARAMETER / Python OSError errno 22 — "Invalid
    argument"), since a raw volume has no size concept via that API. The
    actual size has to come from IOCTL_DISK_GET_LENGTH_INFO instead. This
    is probed only if/when get_size() is actually called (in practice,
    opening a bare FAT32 volume directly — e.g. \\\\.\\E: — never needs
    it at all, since the boot sector carries its own total-sectors field),
    so a device that doesn't support any size query still opens and reads
    fine; get_size() alone falls back to 0 ("unknown") rather than raising.
    """

    def __init__(self, path: str):
        self._f: BinaryIO = open(path, "rb")
        self._path = path
        self._size: Optional[int] = None  # computed lazily on first get_size() call
        self._lock = threading.Lock()
        # os.pread reads at an offset without touching the file position, so
        # threads need no lock at all. Not on Windows, and not for a handle
        # that has no descriptor.
        self._pread = None
        if hasattr(os, "pread"):
            try:
                self._fd = self._f.fileno()
                self._pread = os.pread
            except (OSError, ValueError, AttributeError):
                self._pread = None

    def _probe_size(self) -> int:
        try:
            # Under the lock: it moves the file position, and a read_at()
            # between its seeks would read from the end of the volume.
            with self._lock:
                cur = self._f.tell()
                self._f.seek(0, os.SEEK_END)
                size = self._f.tell()
                self._f.seek(cur)
            return size
        except OSError:
            pass  # not seekable this way — typical for a raw Windows volume/device handle

        if os.name == "nt":
            try:
                return self._windows_ioctl_size()
            except Exception:
                pass

        return 0  # genuinely unknown — callers must treat 0 as "unavailable", not "empty"

    def _windows_ioctl_size(self) -> int:
        """Query the true size of a Windows volume/device handle via
        DeviceIoControl(IOCTL_DISK_GET_LENGTH_INFO), the documented way
        to get the size of a raw volume or physical drive on Windows."""
        import ctypes
        import msvcrt
        from ctypes import wintypes

        IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C
        handle = msvcrt.get_osfhandle(self._f.fileno())
        length = ctypes.c_ulonglong(0)
        bytes_returned = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            handle, IOCTL_DISK_GET_LENGTH_INFO, None, 0,
            ctypes.byref(length), ctypes.sizeof(length),
            ctypes.byref(bytes_returned), None,
        )
        if not ok:
            raise OSError("DeviceIoControl(IOCTL_DISK_GET_LENGTH_INFO) failed")
        return length.value

    def seek(self, offset: int) -> None:
        self._f.seek(offset)

    def read(self, size: int) -> bytes:
        return self._f.read(size)

    def read_at(self, offset: int, size: int) -> bytes:
        if self._pread is not None:
            # pread may return less than asked short of the end; loop, so a
            # short read means the end of the source and nothing else.
            out = bytearray()
            while len(out) < size:
                got = self._pread(self._fd, size - len(out), offset + len(out))
                if not got:
                    break
                out += got
            return bytes(out)
        with self._lock:
            self._f.seek(offset)
            return self._f.read(size)

    def get_size(self) -> int:
        if self._size is None:
            self._size = self._probe_size()
        return self._size

    def close(self) -> None:
        self._f.close()


class EwfSource:
    """Wraps a pyewf handle to expose the same seek/read/get_size/close API."""

    def __init__(self, path: str):
        if not HAVE_PYEWF:
            raise RuntimeError(
                "This looks like an EWF (.E01) image but the optional 'pyewf' "
                "package is not installed. Install it with:\n"
                "    pip install pyewf-python\n"
                "(requires libewf). Raw .img/.dd images work without it."
            )
        filenames = pyewf.glob(path)
        if not filenames:
            filenames = [path]
        self._handle = pyewf.handle()
        self._handle.open(filenames)
        self._lock = threading.Lock()

    def seek(self, offset: int) -> None:
        self._handle.seek(offset)

    def read(self, size: int) -> bytes:
        return self._handle.read(size)

    def read_at(self, offset: int, size: int) -> bytes:
        # One pyewf handle has one position (and libewf's chunk cache is not
        # thread-safe either), so a positioned read is still a locked pair.
        with self._lock:
            self._handle.seek(offset)
            return self._handle.read(size)

    def get_size(self) -> int:
        return self._handle.get_media_size()

    def close(self) -> None:
        self._handle.close()


class OffsetSource:
    """Wraps another source, translating all offsets by a fixed base
    (e.g. the start of a partition inside a whole-disk image)."""

    def __init__(self, inner: ImageSource, base_offset: int, size: Optional[int] = None):
        self._inner = inner
        self._base = base_offset
        self._size = size if size is not None else max(0, inner.get_size() - base_offset)
        # Used only when the inner source has no read_at(): then this lock
        # stands for it, and nothing else may read the inner source meanwhile.
        self._lock = threading.Lock()

    def seek(self, offset: int) -> None:
        self._inner.seek(self._base + offset)

    def read(self, size: int) -> bytes:
        return self._inner.read(size)

    def read_at(self, offset: int, size: int) -> bytes:
        return read_at(self._inner, self._base + offset, size, self._lock)

    def get_size(self) -> int:
        return self._size

    def close(self) -> None:
        pass  # inner owns the real handle; closed by caller


EWF_EXTENSIONS = (".e01", ".e02", ".ex01", ".l01")


def open_raw_source(path: str) -> ImageSource:
    """Open the right ImageSource for a file, based on extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in EWF_EXTENSIONS:
        return EwfSource(path)
    return RawFileSource(path)


# --------------------------------------------------------------------------
# MBR partition detection
# --------------------------------------------------------------------------

FAT32_PARTITION_TYPES = {0x0B, 0x0C, 0x0E}  # FAT32, FAT32 LBA, FAT16B-LBA(rare on 32)
# FAT12 (0x01) and FAT16 (0x04 under 32 MB, 0x06 above, 0x0E with LBA).
FAT_PARTITION_TYPES = FAT32_PARTITION_TYPES | {0x01, 0x04, 0x06}

@dataclass
class PartitionInfo:
    index: int
    part_type: int
    start_lba: int
    sector_count: int


def read_mbr_partitions(source: ImageSource, bytes_per_sector: int = 512) -> list:
    source.seek(0)
    mbr = source.read(512)
    if len(mbr) < 512 or mbr[510:512] != b"\x55\xAA":
        return []
    partitions = []
    for i in range(4):
        entry = mbr[446 + i * 16 : 446 + (i + 1) * 16]
        part_type = entry[4]
        start_lba = struct.unpack_from("<I", entry, 8)[0]
        sector_count = struct.unpack_from("<I", entry, 12)[0]
        if part_type != 0 and sector_count != 0:
            partitions.append(PartitionInfo(i, part_type, start_lba, sector_count))
    return partitions


def locate_fat32_volume(source: ImageSource) -> "OffsetSource | ImageSource":
    """
    Try, in order:
      1. Bare FAT32 volume at offset 0 (valid boot sector signature there).
      2. An MBR partition table with a FAT32-typed partition.
    Returns a source positioned so offset 0 == start of the FAT32 volume.
    Raises ValueError if nothing usable is found.
    """
    source.seek(0)
    boot = source.read(512)
    if len(boot) >= 512 and boot[510:512] == b"\x55\xAA" and (
            boot[82:90].startswith(b"FAT32") or boot[54:59] in (b"FAT12", b"FAT16")):
        return source  # already a bare volume

    partitions = read_mbr_partitions(source)
    fat32_parts = [p for p in partitions if p.part_type in FAT_PARTITION_TYPES]
    if fat32_parts:
        p = fat32_parts[0]
        base = p.start_lba * 512
        size = p.sector_count * 512
        candidate = OffsetSource(source, base, size)
        candidate.seek(0)
        cboot = candidate.read(512)
        if len(cboot) >= 512 and cboot[510:512] == b"\x55\xAA":
            return candidate

    # Last resort: some images have a valid boot-sector-looking sector 0
    # even without the "FAT32   " fs_type string being exact — let the
    # caller's boot-sector parser raise a clear error if this fails too.
    source.seek(0)
    return source


# --------------------------------------------------------------------------
# FAT32 constants
# --------------------------------------------------------------------------

FAT32_EOC_MIN = 0x0FFFFFF8
FAT32_BAD_CLUSTER = 0x0FFFFFF7
FAT32_FREE_CLUSTER = 0x00000000
CLUSTER_MASK = 0x0FFFFFFF

ATTR_READ_ONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME_ID = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LONG_NAME = ATTR_READ_ONLY | ATTR_HIDDEN | ATTR_SYSTEM | ATTR_VOLUME_ID

DIR_ENTRY_SIZE = 32
ENTRY_FREE = 0xE5
ENTRY_END = 0x00


# --------------------------------------------------------------------------
# Boot sector / BPB
# --------------------------------------------------------------------------

@dataclass
class BootSector:
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sector_count: int
    num_fats: int
    total_sectors: int
    fat_size_sectors: int
    root_cluster: int
    volume_label: str
    fs_type: str
    # FAT12 and FAT16 keep the root folder in a fixed region after the
    # FATs, root_entry_count entries long, instead of in a cluster chain;
    # their root_cluster is 0. FAT32's root_entry_count is 0.
    fat_type: str = "FAT32"
    root_entry_count: int = 0

    @property
    def root_dir_sectors(self) -> int:
        return -(-self.root_entry_count * 32 // self.bytes_per_sector)

    @property
    def root_dir_offset(self) -> int:
        return (self.reserved_sector_count
                + self.num_fats * self.fat_size_sectors) * self.bytes_per_sector

    @property
    def cluster_size(self) -> int:
        return self.bytes_per_sector * self.sectors_per_cluster

    @property
    def fat_start_sector(self) -> int:
        return self.reserved_sector_count

    @property
    def first_data_sector(self) -> int:
        return (self.reserved_sector_count + (self.num_fats * self.fat_size_sectors)
                + self.root_dir_sectors)

    def cluster_to_sector(self, cluster: int) -> int:
        return self.first_data_sector + (cluster - 2) * self.sectors_per_cluster

    def cluster_to_offset(self, cluster: int) -> int:
        return self.cluster_to_sector(cluster) * self.bytes_per_sector


def parse_boot_sector(source: ImageSource) -> BootSector:
    source.seek(0)
    data = source.read(512)
    if len(data) < 512:
        raise ValueError("Image too small to contain a boot sector")
    if data[510:512] != b"\x55\xAA":
        raise ValueError("Missing 0x55AA boot sector signature — not a valid FAT volume at this offset")

    bytes_per_sector = struct.unpack_from("<H", data, 11)[0]
    sectors_per_cluster = data[13]
    reserved_sector_count = struct.unpack_from("<H", data, 14)[0]
    num_fats = data[16]
    total_sectors_16 = struct.unpack_from("<H", data, 19)[0]
    total_sectors_32 = struct.unpack_from("<I", data, 32)[0]
    total_sectors = total_sectors_16 or total_sectors_32
    fat_size_16 = struct.unpack_from("<H", data, 22)[0]
    fat_size_32 = struct.unpack_from("<I", data, 36)[0]
    fat_size_sectors = fat_size_16 or fat_size_32
    root_entry_count = struct.unpack_from("<H", data, 17)[0]

    if bytes_per_sector not in (512, 1024, 2048, 4096) or sectors_per_cluster == 0 \
            or sectors_per_cluster & (sectors_per_cluster - 1):
        raise ValueError("Invalid BPB values — not a FAT volume")
    # An NTFS or exFAT boot sector passes the signature check; one with no
    # FAT, no reserved area or no sectors is not a FAT volume of any kind.
    if not (num_fats and fat_size_sectors and reserved_sector_count and total_sectors):
        raise ValueError("No FAT, reserved area or size in the BPB — not a FAT volume")

    if fat_size_16 == 0:
        # FAT32 -- the only kind whose 16-bit FAT size is zero, whatever its
        # cluster count; small FAT32 volumes exist.
        fat_type = "FAT32"
        # FAT32 keeps its size in the 32-bit field; the 16-bit one must be
        # zero. A stale non-zero value there was taken first, and since the
        # FAT chains are bounded by the volume's size it could leave the
        # volume reading as empty.
        if total_sectors_32 and total_sectors_16 < total_sectors_32:
            total_sectors = total_sectors_32
        root_cluster = struct.unpack_from("<I", data, 44)[0]
        fs_type = data[82:90].decode("ascii", errors="replace").strip()
        volume_label = data[71:82].decode("ascii", errors="replace").strip()
        root_entry_count = 0
    else:
        # FAT12 and FAT16 were refused here outright, so deleted files on
        # every card of 2 GB and under -- older cameras, dashcams, trail
        # cameras, voice recorders -- were never recovered by name. They
        # keep deleted entries exactly as FAT32 does; what differs is the
        # root folder, in a fixed region rather than a chain, and the width
        # of a FAT entry. Which of the two it is follows from the cluster
        # count, as the specification says, not from the label.
        if not root_entry_count:
            raise ValueError("FAT12/FAT16 boot sector with no root folder entries")
        root_sectors = -(-root_entry_count * 32 // bytes_per_sector)
        data_sectors = total_sectors - (reserved_sector_count
                                        + num_fats * fat_size_sectors + root_sectors)
        if data_sectors <= 0:
            raise ValueError("The FATs and root folder are larger than the volume")
        clusters = data_sectors // sectors_per_cluster
        fat_type = "FAT12" if clusters < 4085 else "FAT16"
        root_cluster = 0
        fs_type = data[54:62].decode("ascii", errors="replace").strip()
        volume_label = data[43:54].decode("ascii", errors="replace").strip()

    return BootSector(
        bytes_per_sector=bytes_per_sector,
        sectors_per_cluster=sectors_per_cluster,
        reserved_sector_count=reserved_sector_count,
        num_fats=num_fats,
        total_sectors=total_sectors,
        fat_size_sectors=fat_size_sectors,
        root_cluster=root_cluster,
        volume_label=volume_label,
        fs_type=fs_type,
        fat_type=fat_type,
        root_entry_count=root_entry_count,
    )


# --------------------------------------------------------------------------
# FAT table
# --------------------------------------------------------------------------

def _uint32_array() -> "array":
    """An empty array of unsigned 32-bit integers on this platform."""
    for code in ("I", "L"):
        if array(code).itemsize == 4:
            return array(code)
    raise RuntimeError("no 32-bit unsigned array type on this platform")


def _fat_entries(raw: bytes, fat_type: str) -> "array":
    """The FAT's entries, widened to FAT32's values.

    FAT16 entries are 16 bits and FAT12's are 12, packed two to three
    bytes. Their end-of-chain and bad-cluster markers are widened to FAT32's
    so the chain-following, free-cluster and end-of-chain checks below work
    unchanged for all three.

    An array of 32-bit integers, not a tuple of Python ints: a tuple took
    about nine times the FAT's size in memory (137 MiB for four million
    entries; a 256 GB FAT32 card's table would have needed over 2 GiB).
    The array takes the FAT's own size.
    """
    entries = _uint32_array()
    if fat_type == "FAT32":
        count = len(raw) // 4
        entries.frombytes(raw[: count * 4])
        if sys.byteorder != "little":
            entries.byteswap()
        return entries
    if fat_type == "FAT16":
        count = len(raw) // 2
        values = array("H")
        values.frombytes(raw[: count * 2])
        if sys.byteorder != "little":
            values.byteswap()
        top, bad = 0xFFF8, 0xFFF7
    else:
        count = len(raw) * 2 // 3

        def twelve():
            for cluster in range(count):
                at = cluster + cluster // 2
                word = raw[at] | (raw[at + 1] << 8) if at + 1 < len(raw) else raw[at]
                yield word >> 4 if cluster & 1 else word & 0x0FFF
        values = twelve()
        top, bad = 0x0FF8, 0x0FF7
    entries.extend(FAT32_EOC_MIN if v >= top else FAT32_BAD_CLUSTER if v == bad else v
                   for v in values)
    return entries


class FatTable:
    def __init__(self, source: ImageSource, bs: BootSector):
        self.bs = bs
        fat_offset = bs.fat_start_sector * bs.bytes_per_sector
        fat_bytes = bs.fat_size_sectors * bs.bytes_per_sector
        source.seek(fat_offset)
        raw = source.read(fat_bytes)
        if len(raw) < fat_bytes:
            raise ValueError("Could not read full FAT table — image truncated?")
        self.entries = _fat_entries(raw, bs.fat_type)

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, cluster: int) -> int:
        return self.entries[cluster] & CLUSTER_MASK

    def is_free(self, cluster: int) -> bool:
        return self.get(cluster) == FAT32_FREE_CLUSTER

    def is_eoc(self, value: int) -> bool:
        return value >= FAT32_EOC_MIN

    def is_bad(self, value: int) -> bool:
        return value == FAT32_BAD_CLUSTER

    def chain(self, start_cluster: int, max_clusters: int = 10_000_000) -> Iterator[int]:
        seen = set()
        cluster = start_cluster
        count = 0
        # Bounded by the clusters the volume has, not by the FAT's length:
        # the FAT is padded to whole sectors, and a chain through one of the
        # padding entries read past the volume into the next partition.
        limit = min(len(self.entries), self.cluster_limit)
        while 2 <= cluster < limit:
            if cluster in seen:
                break
            seen.add(cluster)
            yield cluster
            count += 1
            if count >= max_clusters:
                break
            nxt = self.get(cluster)
            if self.is_eoc(nxt) or self.is_bad(nxt) or nxt == FAT32_FREE_CLUSTER:
                break
            cluster = nxt

    @property
    def cluster_limit(self) -> int:
        """One past the last cluster the volume really has.

        The FAT is written in whole sectors, so it usually has entries past
        the last real cluster -- zero, so they look free. Anything reading
        "free clusters" as places on the disk read past the end of the
        volume, into whatever follows it.
        """
        bs = self.bs
        data_sectors = bs.total_sectors - bs.first_data_sector
        if data_sectors <= 0:
            # A size field that cannot be right -- smaller than the FATs in
            # front of the data -- bounds nothing; the FAT's own length is
            # the only limit left. Bounding by it as zero emptied the volume.
            return len(self.entries)
        return min(len(self.entries), data_sectors // max(1, bs.sectors_per_cluster) + 2)

    def free_clusters(self) -> Iterator[int]:
        for c in range(2, self.cluster_limit):
            if self.is_free(c):
                yield c


# --------------------------------------------------------------------------
# Directory entries
# --------------------------------------------------------------------------

@dataclass
class DirEntry:
    name: str
    is_dir: bool
    is_deleted: bool
    first_cluster: int
    size: int
    attr: int
    path: str = ""
    # FAT records these in local time with no zone, which is the format's
    # own limitation, not a choice made here. They are carried as naive ISO
    # strings and labeled as recorded rather than converted to anything.
    mtime: str = ""
    ctime: str = ""
    atime: str = ""


def _fat_datetime(date_word: int, time_word: int = 0, *,
                  hundredths: int = 0, date_only: bool = False) -> str:
    """A FAT date and time pair as an ISO string.

    FAT counts years from 1980, stores seconds in two-second units, and
    records no time zone at all -- the value is whatever the clock of the
    machine that wrote it said. It is returned naive for that reason.

    The access time is a date and nothing else (date_only): it came back as
    midnight, a time of day the volume never recorded. The creation time has
    a byte of hundredths (0-199) beside it, which was ignored, so a creation
    time could read up to 1.99 s early.
    """
    if not date_word:
        return ""
    if date_only:
        full = _fat_datetime(date_word, 0)
        return full[:10] if full else ""
    year = 1980 + ((date_word >> 9) & 0x7F)
    month = (date_word >> 5) & 0x0F
    day = date_word & 0x1F
    hour = (time_word >> 11) & 0x1F
    minute = (time_word >> 5) & 0x3F
    second = (time_word & 0x1F) * 2
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24
            and minute < 60 and second < 60):
        return ""
    extra = hundredths if 0 < hundredths < 200 else 0
    try:
        import datetime as _dt
        when = _dt.datetime(year, month, day, hour, minute, second)
        if extra:
            when += _dt.timedelta(milliseconds=extra * 10)
        return when.isoformat()
    except (ValueError, OverflowError):
        return ""


def _short_name(raw: bytes) -> str:
    base = raw[0:8]
    if base[:1] == b"\x05":
        # 0x05 stands for a real first byte of 0xE5, which FAT reserves for
        # "deleted"; it is a lead byte in Shift-JIS, so the name is decoded
        # as that.
        name = (b"\xe5" + base[1:]).decode("cp932", errors="replace").rstrip()
    else:
        name = base.decode("ascii", errors="replace").rstrip()
    ext = raw[8:11].decode("ascii", errors="replace").rstrip()
    # Windows NT and Linux store a name that fits 8.3 and is all lower case
    # as a short name with case bits (byte 12: 0x08 the name, 0x10 the
    # extension) instead of a long name. Ignored, "one.jpg" read as ONE.JPG,
    # which is not what the examiner's operating system or TSK shows.
    flags = raw[12] if len(raw) > 12 else 0
    if flags & 0x08:
        name = name.lower()
    if flags & 0x10:
        ext = ext.lower()
    return f"{name}.{ext}" if ext else name


def lfn_checksum(name11: bytes) -> int:
    """The checksum a long-name entry carries of the short name it belongs to."""
    total = 0
    for byte in name11:
        total = (((total & 1) << 7) + (total >> 1) + byte) & 0xFF
    return total


def _long_name(run: list, raw: bytes, is_deleted: bool) -> str:
    """The long name in the entries before a short entry, if it is that entry's.

    Every long-name entry carries a checksum of the short name it belongs
    to. It was never read, so a leftover long name -- slots reused by a
    short-name-only writer, as cameras are -- was attached to whatever short
    entry followed it. And a deleted long name was thrown away, leaving a
    deleted file with only its short name, first letter lost: "?HOTO_~1.JPG"
    for "Photo at the park.jpg". A long name is now used only when its
    entries are whole, in order, and their checksum is the short entry's.

    Deleting a file overwrites the first byte of every entry, its order
    numbers included, but not the characters of the long name. For a deleted
    file the order is taken from where the entries sit (last part first),
    and the checksum is tried against every possible first byte of the
    short name: the long name stands only if one of them matches.
    """
    if not run:
        return ""
    checksums = {entry[3] for entry in run}
    if len(checksums) != 1:
        return ""
    wanted = checksums.pop()
    pieces = [entry[2] for entry in reversed(run)]       # stored last part first
    if any(piece is None for piece in pieces):
        return ""
    if is_deleted:
        if not all(entry[0] for entry in run):
            return ""                       # live long entries on a deleted short one
        # Any checksum can be met by some first byte, so trying them all
        # proves nothing. The short name was made from the long one, so its
        # first letter is the long name's first (spaces and leading dots
        # dropped, upper-cased, "_" for what 8.3 cannot hold): that one
        # letter is tried, and the long name stands only if it matches.
        text_guess = "".join(pieces).lstrip(" .").replace(" ", "")
        if not text_guess:
            return ""
        letter = text_guess[0].upper()
        guesses = {letter.encode("ascii") if letter.isascii() and letter.isprintable()
                   else b"_", b"_"}
        if not any(lfn_checksum(guess + raw[1:11]) == wanted for guess in guesses):
            return ""
    else:
        if any(entry[0] for entry in run):
            return ""                       # a deleted long name before a live entry
        orders = [entry[1] for entry in run]
        count = len(run)
        if orders[0] != (0x40 | count) or \
                [o & 0x1F for o in orders] != list(range(count, 0, -1)):
            return ""
        if lfn_checksum(raw[0:11]) != wanted:
            return ""
    text = "".join(pieces)
    if not text or len(text) > 13 * len(run) or len(text) <= 13 * (len(run) - 1):
        return ""
    return text


def _lfn_piece(raw: bytes):
    """The characters one long-name entry holds, or None if they are not text."""
    chars = bytearray()
    for off in (1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30):
        chars += raw[off : off + 2]
    try:
        text = chars.decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    text = text.split("\x00")[0]
    return text.rstrip("\uffff") if "\uffff" in text else text


def parse_dir_entries(data: bytes, dir_path: str) -> list:
    entries = []
    run = []          # long-name entries since the last short entry
    i = 0
    while i + DIR_ENTRY_SIZE <= len(data):
        raw = data[i : i + DIR_ENTRY_SIZE]
        first_byte = raw[0]
        if first_byte == ENTRY_END:
            break
        attr = raw[11]

        if attr & 0x3F == ATTR_LONG_NAME:
            run.append((first_byte == ENTRY_FREE, first_byte, _lfn_piece(raw), raw[13]))
            i += DIR_ENTRY_SIZE
            continue

        is_deleted = first_byte == ENTRY_FREE
        if attr & ATTR_VOLUME_ID and not (attr & ATTR_DIRECTORY):
            run = []
            i += DIR_ENTRY_SIZE
            continue

        long_name = _long_name(run, raw, is_deleted)
        run = []

        name = long_name if long_name else _short_name(raw)
        first_cluster_hi = struct.unpack_from("<H", raw, 20)[0]
        first_cluster_lo = struct.unpack_from("<H", raw, 26)[0]
        first_cluster = (first_cluster_hi << 16) | first_cluster_lo
        size = struct.unpack_from("<I", raw, 28)[0]

        if name not in (".", ".."):
            entries.append(
                DirEntry(
                    name=name,
                    is_dir=bool(attr & ATTR_DIRECTORY),
                    is_deleted=is_deleted,
                    first_cluster=first_cluster,
                    size=size,
                    attr=attr,
                    path=(dir_path.rstrip("/") + "/" + name) if name else dir_path,
                    # created at 14-17, accessed (date only) at 18, written
                    # at 22-25, per the FAT directory entry layout
                    ctime=_fat_datetime(
                        struct.unpack("<H", raw[16:18])[0],
                        struct.unpack("<H", raw[14:16])[0],
                        hundredths=raw[13]),
                    atime=_fat_datetime(struct.unpack("<H", raw[18:20])[0],
                                        date_only=True),
                    mtime=_fat_datetime(
                        struct.unpack("<H", raw[24:26])[0],
                        struct.unpack("<H", raw[22:24])[0]),
                )
            )
        i += DIR_ENTRY_SIZE
    return entries


# --------------------------------------------------------------------------
# Filesystem
# --------------------------------------------------------------------------

class Fat32FileSystem:
    def __init__(self, image_path: str):
        self.image_path = image_path
        self._raw_source = open_raw_source(image_path)
        self.source = locate_fat32_volume(self._raw_source)
        self._io_lock = threading.Lock()
        self.bs = parse_boot_sector(self.source)
        self.fat = FatTable(self.source, self.bs)

    def close(self):
        self._raw_source.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read_at(self, offset: int, size: int) -> bytes:
        """Bytes of the volume, safe from any thread (see read_at above).

        The lock is made here as well as in __init__ because media_core
        builds this object with __new__ over its own source.
        """
        lock = self.__dict__.get("_io_lock")
        if lock is None:
            lock = self.__dict__.setdefault("_io_lock", threading.Lock())
        return read_at(self.source, offset, size, lock)

    def read_cluster(self, cluster: int) -> bytes:
        return self.read_at(self.bs.cluster_to_offset(cluster), self.bs.cluster_size)

    def read_chain_bytes(self, start_cluster: int, size_hint: Optional[int] = None) -> bytes:
        buf = bytearray()
        for cluster in self.fat.chain(start_cluster):
            buf += self.read_cluster(cluster)
            if size_hint is not None and len(buf) >= size_hint:
                break
        return bytes(buf[:size_hint]) if size_hint is not None else bytes(buf)

    def list_dir(self, cluster: int, path: str = "/") -> list:
        if cluster == 0 and self.bs.fat_type != "FAT32":
            # The FAT12/FAT16 root folder: a fixed region, not a chain. A
            # subfolder's ".." names it as cluster 0 too.
            raw = self.read_at(self.bs.root_dir_offset,
                               self.bs.root_entry_count * 32)
        else:
            raw = self.read_chain_bytes(cluster)
        return parse_dir_entries(raw, path)

    def walk(self, cluster: Optional[int] = None, path: str = "/") -> Iterator[DirEntry]:
        """Every live entry, folders before what they hold.

        Iterative, with a record of the folders already read: a damaged card
        whose folder entry points back at a folder above it sent the
        recursive walk round that loop until Python's recursion limit
        stopped the FAT32 browser.
        """
        if cluster is None:
            cluster = self.bs.root_cluster
        seen = set()
        stack = [(cluster, path)]
        while stack:
            folder, where = stack.pop()
            if folder in seen:
                continue
            if len(seen) > 100_000:
                # A real card does not hold this many folders; a damaged one
                # whose entries point at random clusters can. Said, not
                # skipped in silence.
                if not getattr(self, "_walk_cut_said", False):
                    self._walk_cut_said = True
                    LOG.warning("Stopped walking after 100,000 folders; the "
                                "folder tree is probably damaged and the rest "
                                "was not listed.")
                continue
            seen.add(folder)
            children = []
            for entry in self.list_dir(folder, where):
                if entry.is_deleted:
                    continue
                yield entry
                if entry.is_dir and entry.first_cluster >= 2:
                    children.append((entry.first_cluster, entry.path))
            stack.extend(reversed(children))

    def find(self, target_path: str) -> Optional[DirEntry]:
        target_path = target_path.strip("/")
        parts = target_path.split("/") if target_path else []
        cluster = self.bs.root_cluster
        current_path = "/"
        entry = None
        for part in parts:
            found = None
            for e in self.list_dir(cluster, current_path):
                if e.is_deleted:
                    continue
                if e.name.lower() == part.lower():
                    found = e
                    break
            if found is None:
                return None
            entry = found
            current_path = found.path
            cluster = found.first_cluster
        return entry

    def extract_file(self, entry: DirEntry, out_path: str) -> int:
        """Write the file out; returns how many bytes of its size were not
        there to read (0 for a whole file). A chain that ends before the
        directory entry's size was written short and said nothing.

        Cluster by cluster: the whole file was read into memory first, so a
        4 GB video took 4 GB of memory to save."""
        want = entry.size or 0
        written = 0
        with open(out_path, "wb") as out:
            if want:
                for cluster in self.fat.chain(entry.first_cluster):
                    piece = self.read_cluster(cluster)[: want - written]
                    out.write(piece)
                    written += len(piece)
                    if written >= want:
                        break
        return max(0, want - written)

    def unallocated_clusters(self) -> Iterator[int]:
        yield from self.fat.free_clusters()


# --------------------------------------------------------------------------
# File-header/footer carving
# --------------------------------------------------------------------------

@dataclass
class FileSignature:
    ext: str
    header: bytes
    footer: Optional[bytes] = None
    max_size: int = 64 * 1024 * 1024


# The FAT32 browser's own carve (carve_unallocated, its footer rules and its
# signature list) went with the browser in v2026.11.319: nothing else called
# it. The program's carve is fme/engines/carving.py, and its one judge of a
# file's length is carve_judge.py.


# --------------------------------------------------------------------------
# Signature detection from sample files
# --------------------------------------------------------------------------

def _ascii_preview(data: bytes) -> str:
    """Printable-ASCII preview with non-printable bytes shown as '.'."""
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _common_prefix(chunks: list) -> bytes:
    if not chunks:
        return b""
    min_len = min(len(c) for c in chunks)
    out = bytearray()
    for i in range(min_len):
        b0 = chunks[0][i]
        if all(c[i] == b0 for c in chunks):
            out.append(b0)
        else:
            break
    return bytes(out)


def _common_suffix(chunks: list) -> bytes:
    if not chunks:
        return b""
    min_len = min(len(c) for c in chunks)
    out = bytearray()
    for i in range(1, min_len + 1):
        b0 = chunks[0][-i]
        if all(c[-i] == b0 for c in chunks):
            out.append(b0)
        else:
            break
    return bytes(bytes(out)[::-1])


def analyze_samples(paths: list, scan_len: int = 32) -> dict:
    """
    Read the first/last `scan_len` bytes of each given file and compute:
      - per-file header/footer bytes (hex + ASCII preview)
      - the header bytes common to ALL samples (longest matching prefix)
      - the footer bytes common to ALL samples (longest matching suffix)

    A single sample just yields its own header/footer as the "common" one.
    Files smaller than scan_len are read in full (header == footer == the
    whole file in that case, truncated to what's available).
    Unreadable paths are reported per-file with an error and excluded from
    the common-prefix/suffix computation.
    """
    samples = []
    for p in paths:
        try:
            size = os.path.getsize(p)
            with open(p, "rb") as f:
                header = f.read(scan_len)
                if size > scan_len:
                    f.seek(max(0, size - scan_len))
                    footer = f.read(scan_len)
                else:
                    footer = header
            samples.append({"path": p, "size": size, "header": header, "footer": footer, "error": None})
        except OSError as exc:
            samples.append({"path": p, "size": 0, "header": b"", "footer": b"", "error": str(exc)})

    valid = [s for s in samples if s["error"] is None and s["header"]]
    common_header = _common_prefix([s["header"] for s in valid]) if valid else b""
    common_footer = _common_suffix([s["footer"] for s in valid]) if valid else b""

    return {
        "samples": [
            {
                "path": s["path"],
                "size": s["size"],
                "header_hex": s["header"].hex(),
                "header_ascii": _ascii_preview(s["header"]),
                "footer_hex": s["footer"].hex(),
                "footer_ascii": _ascii_preview(s["footer"]),
                "error": s["error"],
            }
            for s in samples
        ],
        "common_header_hex": common_header.hex(),
        "common_header_ascii": _ascii_preview(common_header),
        "common_footer_hex": common_footer.hex(),
        "common_footer_ascii": _ascii_preview(common_footer),
        "sample_count": len(valid),
    }


def signature_from_hex(ext: str, header_hex: str, footer_hex: str = "",
                        max_size: int = 64 * 1024 * 1024) -> FileSignature:
    """Build a FileSignature from user-supplied hex strings (as produced by
    analyze_samples, possibly hand-edited). Raises ValueError on bad input."""
    ext = ext.strip().lstrip(".").lower()
    if not ext:
        raise ValueError("File extension is required")
    header_hex = header_hex.strip().replace(" ", "")
    if not header_hex:
        raise ValueError("Header bytes are required")
    try:
        header = bytes.fromhex(header_hex)
    except ValueError as exc:
        raise ValueError(f"Header is not valid hex: {exc}") from exc
    if not header:
        raise ValueError("Header must be at least 1 byte")

    footer_hex = footer_hex.strip().replace(" ", "")
    footer = None
    if footer_hex:
        try:
            footer = bytes.fromhex(footer_hex)
        except ValueError as exc:
            raise ValueError(f"Footer is not valid hex: {exc}") from exc
        if not footer:
            footer = None

    if max_size <= 0:
        raise ValueError("max_size must be positive")

    return FileSignature(ext=ext, header=header, footer=footer, max_size=max_size)


def human_size(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"
