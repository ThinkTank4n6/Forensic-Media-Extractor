"""
volume_survey.py -- What an image holds before anything is recovered: inspect_image(), the
encrypted and unopened volumes, and the volume details a run reports.

Split out of media_core.py in v2026.11.314, statement for statement: the
code below is what media_core held, unchanged. media_core re-exports every
name, so ``media_core.X`` still works for callers. **A test that patches a
name must patch it in the module that looks it up** -- patching media_core.X
does not reach code here (tests/test_patch_targets.py checks).
"""

from __future__ import annotations

import struct
from fme.engines import vendor_fs
from fme.engines.engine_common import (  # noqa: F401
    fat32_core, pytsk3,
)
from fme.engines.image_readers import (  # noqa: F401
    ImageReader, human,
)
from fme.engines.fs_walk import (  # noqa: F401
    _TSKImg, _mbr_sector_size, _open_fs_at, _partition_offsets,
    _table_labels, _tsk_label, fs_label_for, tsk_enabled,
)



def _volume_label(reader: ImageReader, offset: int, fs_type: str) -> str:
    """Read the volume label out of the boot sector.

    TSK's fs.info.fs_id is a raw serial-number byte array, not a name --
    printing it produced the [0, 0, 0, ...] noise. FAT and NTFS both keep a
    readable label at a fixed offset in the boot sector, so take it there;
    exFAT and APFS store theirs in directory entries, which is more work than
    an inspection panel warrants.
    """
    fs_type = (fs_type or "").upper()
    try:
        boot = reader.read(offset, 512)
    except Exception:  # noqa: BLE001
        return ""
    if len(boot) < 512:
        return ""
    raw = b""
    if "FAT32" in fs_type:
        raw = boot[71:82]
    elif "FAT" in fs_type:          # FAT12/FAT16 keep it earlier
        raw = boot[43:54]
    elif "NTFS" in fs_type:
        return ""                   # NTFS stores it in the $Volume record
    label = raw.decode("ascii", "ignore").strip()
    # Reject anything that is not a clean printable name: reading a label
    # field out of a sector that is not really a boot sector otherwise yields
    # control-character noise dressed up as a volume name.
    if (not label or label == "NO NAME"
            or not all(ch.isprintable() for ch in label)
            or not any(ch.isalnum() for ch in label)):
        return ""
    return label


def _looks_like_mbr(sector: bytes) -> bool:
    """A FAT or NTFS boot sector also ends in 55 AA, so the signature alone
    does not mean there is a partition table. Require a plausible entry."""
    if len(sector) < 512 or sector[510:512] != b"\x55\xaa":
        return False
    for i in range(4):
        entry = sector[446 + i * 16:462 + i * 16]
        boot, ptype = entry[0], entry[4]
        lba = struct.unpack("<I", entry[8:12])[0]
        sectors = struct.unpack("<I", entry[12:16])[0]
        if boot in (0x00, 0x80) and ptype != 0 and sectors > 0 and lba > 0:
            return True
    return False


# What an encrypted volume looks like from outside, and what to say
# about it. A locked volume reads as random bytes: every engine finds
# nothing, and the run reports "0 files" -- which is also what an empty
# disk reports. An examiner cannot tell those apart, and the wrong one
# ends a case.
ENCRYPTED_VOLUMES = (
    (3, b"-FVE-FS-", "BitLocker",
     "Unlock it in Windows with the password or the 48-digit recovery "
     "key and image the unlocked volume, or supply the key to a tool "
     "that can open it. This program reads the volume; it does not "
     "break the encryption."),
    (3, b"MSWIN4.1", None, None),          # ordinary FAT, not encrypted
    (0, b"LUKS\xba\xbe", "LUKS",
     "Unlock it with cryptsetup and image the mapped device."),
    (0, b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00", None, None),
)


def encryption_at(head: bytes):
    """Which encrypted container this is, if it is one.

    The version and the UUID are read where the header gives them,
    because an examiner writing this up needs them: LUKS2 needs a newer
    cryptsetup than LUKS1 and derives its key differently, and the UUID
    is how a volume is matched to a key somebody else holds.
    """
    for offset, magic, name, advice in ENCRYPTED_VOLUMES:
        if not name or head[offset:offset + len(magic)] != magic:
            continue
        found = {"kind": name, "advice": advice}
        if name == "LUKS" and len(head) >= 8:
            version = struct.unpack(">H", head[6:8])[0]
            if version in (1, 2):
                found["version"] = version
                found["kind"] = f"LUKS{version}"
                if version == 2:
                    found["advice"] = (
                        "Unlock it with cryptsetup 2.0 or newer -- LUKS2 "
                        "headers are not readable by older versions -- "
                        "and image the mapped device.")
            # The UUID sits at 168 in both versions and is how a volume
            # is matched to a key held elsewhere.
            raw = head[168:208].split(b"\x00")[0]
            try:
                text = raw.decode("ascii")
                if len(text) >= 32:
                    found["uuid"] = text
            except UnicodeDecodeError:
                pass
        return found
    return None


def encrypted_volumes(reader) -> list:
    """Every locked volume on the image, wherever it starts.

    A BitLocker or LUKS header sits at the start of its own volume. Looking
    only at the start of the evidence file found it on a bare volume image
    and nowhere else: on an E01 the start of the file is the E01's own
    header, and on a whole disk the start is the partition table. The
    commonest case of all -- an E01 of a laptop with a BitLocker partition --
    was never flagged, and the run's few files from the unencrypted system
    partition read as a nearly empty disk.

    So this reads through the image reader, which sees the disk inside the
    container, and checks the start of the image and the start of every
    partition. Each locked volume comes back with its offset and partition
    label beside what encryption_at says about it.
    """
    places = [(0, "whole image")]
    try:
        places += [(v["offset"], v.get("label") or f"offset {v['offset']}")
                   for v in _partition_offsets(reader) if v.get("offset")]
    except Exception:  # noqa: BLE001 - an unreadable table is not a reason to stop
        pass
    found, seen = [], set()
    for offset, label in places:
        if offset in seen:
            continue
        seen.add(offset)
        try:
            head = reader.read(offset, 512) or b""
        except Exception:  # noqa: BLE001 - past the end, or a short read
            continue
        locked = encryption_at(head)
        if locked:
            found.append(dict(locked, offset=offset, partition=label))
    return found


# Volume managers this program does not open, by how their partitions are
# marked. The volumes inside them -- and so their files -- are reached only
# by the carve, and a run over one should say so.
_LDM_GUIDS = {"5808c8aa-7e8f-42e0-85d2-e1e90434cfb3": "Windows dynamic disk (LDM metadata)",
              "af9b60a0-1431-4f62-bc68-3311714a69ad": "Windows dynamic disk (LDM data)"}
_LVM_GUID = "e6d6d379-f507-44c2-a23c-238f2a3df928"


def unopened_containers(reader) -> list:
    """Partitions holding volumes this program cannot open: LVM, dynamic disks.

    BitLocker and LUKS are flagged where they sit (v239). A LUKS or BitLocker
    volume -- or a plain one -- inside an LVM physical volume or a Windows
    dynamic disk was not looked for at all: neither TSK nor this program
    opens those volume managers, so everything inside was reached only by
    the carve, and nothing said why the run found so little there.
    """
    import uuid as _uuid
    found = []
    try:
        mbr = reader.read(0, 512)
    except Exception:  # noqa: BLE001
        return found
    if len(mbr) == 512 and mbr[510:512] == b"\x55\xaa":
        unit = _mbr_sector_size(reader, [
            struct.unpack_from("<B3sB3sII", mbr, 446 + 16 * slot)
            for slot in range(4)])
        for slot in range(4):
            kind, start = mbr[446 + 16 * slot + 4], struct.unpack_from("<I", mbr, 446 + 16 * slot + 8)[0]
            if kind == 0x42 and start:
                found.append({"offset": start * unit, "partition": f"p{slot + 1}",
                              "kind": "Windows dynamic disk"})
            elif kind == 0x8E and start:
                found.append({"offset": start * unit, "partition": f"p{slot + 1}",
                              "kind": "LVM physical volume"})
        for sector in (512, 4096):
            try:
                header = reader.read(sector, 92)
            except Exception:  # noqa: BLE001
                continue
            if header[:8] != b"EFI PART":
                continue
            table_lba, count, size = struct.unpack_from("<QII", header, 72)
            if not (128 <= size <= 4096 and 0 < count <= 1024):
                break
            table = reader.read(table_lba * sector, count * size)
            for slot in range(len(table) // size):
                entry = table[slot * size:(slot + 1) * size]
                kind = str(_uuid.UUID(bytes_le=bytes(entry[:16])))
                first = struct.unpack_from("<Q", entry, 32)[0]
                if kind in _LDM_GUIDS:
                    found.append({"offset": first * sector, "partition": f"gpt{slot + 1}",
                                  "kind": _LDM_GUIDS[kind]})
                elif kind == _LVM_GUID:
                    found.append({"offset": first * sector, "partition": f"gpt{slot + 1}",
                                  "kind": "LVM physical volume"})
            break
    # An LVM physical volume says so in its own label, whatever the table
    # calls its partition: "LABELONE" in one of its first four sectors.
    seen = {v["offset"] for v in found}
    places = [(0, "whole image")]
    try:
        places += [(v["offset"], v.get("label") or "") for v in _partition_offsets(reader)]
    except Exception:  # noqa: BLE001
        pass
    for offset, label in places:
        if offset in seen:
            continue
        try:
            head = reader.read(offset, 2048)
        except Exception:  # noqa: BLE001
            continue
        for at in range(0, len(head) - 32, 512):
            if head[at:at + 8] == b"LABELONE" and head[at + 24:at + 32] == b"LVM2 001":
                found.append({"offset": offset, "partition": label or "whole image",
                              "kind": "LVM physical volume"})
                seen.add(offset)
                break
    return found


def _locked_apfs(path: str, credentials=None) -> list:
    """Locked APFS volumes, from the vendored reader (vendor_fs.locked_volumes).

    Asked whichever engine walks the image: with TSK installed it is TSK that
    reads APFS, and a locked volume is no less locked for that.
    """
    try:
        # As for describe(): the keys only when there are any.
        return (vendor_fs.locked_volumes(path, credentials) if credentials
                else vendor_fs.locked_volumes(path))
    except Exception:  # noqa: BLE001 - never fail a run over a warning
        return []


def _locked_message(locked: dict) -> str:
    """What to tell the examiner about one locked volume."""
    if locked.get("volume"):
        where = ("this image" if not locked.get("offset") else
                 f"partition {locked.get('partition') or '?'} (at "
                 f"{human(locked['offset'])} into the image)")
        return (f"The {locked['kind']} volume {locked['volume']} in {where} is "
                f"encrypted (FileVault) and is locked: its blocks are "
                f"ciphertext, so none of its files can be read, and a run "
                f"lists none of them -- which is not the same as the volume "
                f"being empty. {locked.get('advice', '')}".rstrip())
    if not locked.get("offset"):
        return (f"This volume is {locked['kind']}-encrypted and is locked. "
                f"Nothing can be read from it as it stands, and a run over "
                f"it will report no files -- which is not the same as an "
                f"empty disk. {locked['advice']}")
    return (f"Partition {locked['partition']} (at {human(locked['offset'])} "
            f"into the image) is {locked['kind']}-encrypted and is locked. "
            f"Nothing on it can be read as it stands, so whatever it holds "
            f"is missing from a run -- the files that do come back are from "
            f"the other partitions, and are not the whole disk. "
            f"{locked['advice']}")


def _inspect_volumes(reader: ImageReader, path: str, warnings: list) -> list:
    """List every mountable file system, using TSK when it is available and
    falling back to fat32_core plus a raw MBR read when it is not.

    Setting FME_NO_TSK=1 ignores pytsk3 and takes the fallback path. That
    is not a preference: it is how an examiner finds out whether the two
    engines recover the same files from the same volume, which is a
    question worth being able to answer about one's own tool.
    """
    volumes = []
    if tsk_enabled():
        img = _TSKImg(reader)
        table = _table_labels(reader)
        offsets = []
        try:
            vs = pytsk3.Volume_Info(img)
            block = vs.info.block_size
            for part in vs:
                desc = (part.desc.decode("utf-8", "replace")
                        if isinstance(part.desc, bytes) else str(part.desc))
                if part.len <= 2 or part.flags == pytsk3.TSK_VS_PART_FLAG_META:
                    continue
                offsets.append((part.start * block, part.len * block,
                                _tsk_label(part, block, table), desc.strip()))
        except (OSError, IOError, RuntimeError):
            offsets = [(0, reader.size, "p0", "whole image")]
        if not offsets:
            offsets = [(0, reader.size, "p0", "whole image")]

        for offset, length, label, desc in offsets:
            entry = {"label": label, "description": desc, "offset": offset,
                     "size_human": human(length), "fs": None, "volume_label": ""}
            for fs, sublabel in _open_fs_at(img, offset):
                entry["fs"] = fs_label_for(fs)
                if sublabel:
                    entry["label"] = sublabel
                entry["volume_label"] = _volume_label(reader, offset, entry["fs"])
                break
            volumes.append(entry)
        return volumes

    # No TSK: read the MBR ourselves so the partition table is still visible,
    # and ask fat32_core whether it can mount the volume.
    warnings.append("pytsk3 is not installed, so only FAT32 can be mounted. "
                    "Install it for exFAT, NTFS, HFS+ and APFS.")
    mbr = reader.read(0, 512)
    if _looks_like_mbr(mbr):
        unit = _mbr_sector_size(reader, [
            struct.unpack_from("<B3sB3sII", mbr, 446 + 16 * slot)
            for slot in range(4)])
        for i in range(4):
            entry = mbr[446 + i * 16:462 + i * 16]
            ptype, lba, sectors = entry[4], struct.unpack("<I", entry[8:12])[0], \
                struct.unpack("<I", entry[12:16])[0]
            if ptype == 0 or sectors == 0:
                continue
            volumes.append({"label": f"p{i}", "description": f"MBR type 0x{ptype:02x}",
                            "offset": lba * unit, "size_human": human(sectors * unit),
                            "fs": None, "volume_label": ""})
    if fat32_core is not None:
        try:
            fs = fat32_core.Fat32FileSystem(path)
            try:
                target = volumes[0] if volumes else None
                info = {"label": "p0", "description": "FAT32 volume",
                        "offset": 0, "size_human": human(reader.size),
                        "fs": "FAT32", "volume_label": fs.bs.volume_label}
                if target is not None:
                    target.update(fs="FAT32", volume_label=fs.bs.volume_label)
                else:
                    volumes.append(info)
            finally:
                fs.close()
        except Exception:  # noqa: BLE001 - simply not a FAT32 volume
            pass
    return volumes


def _device_fields(archive_path: str) -> dict:
    """What this archive path means on the device, if anything.

    A Cellebrite or GrayKey archive is packaging around a phone's file
    system. Recording only the path inside the archive means a report cites
    the tool rather than the device, and an examiner asked in the witness
    box where a file was has the wrong answer in front of them.
    """
    try:
        from fme.engines import extraction

        where, tool = extraction.device_path(archive_path)
    except Exception:  # noqa: BLE001 - packaging we do not recognize
        return {}
    return {"device_path": where, "packed_by": tool}
