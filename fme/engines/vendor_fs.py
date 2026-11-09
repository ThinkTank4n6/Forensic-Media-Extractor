"""
vendor_fs.py -- the file systems this program does not read itself.

The file system engine reads what The Sleuth Kit reads: ext, FAT, exFAT,
NTFS, HFS+, APFS and a few more. A vehicle head unit runs QNX. A phone from
about 2019 onward is likely F2FS. An embedded device is SquashFS, JFFS2,
UBIFS or YAFFS. On all of those the engine finds nothing, carving recovers
the pictures with no names and no dates, and the file system's own record of
what was where is lost.

A reader for each of those is months of work. One already exists, under a
licence that permits using it, so this uses it: `fme/vendor/qnxprobe.py`,
copied in verbatim with its copyright notice and licence text beside it, and
its provenance -- version, upstream, SHA-256 -- recorded in
`fme/vendor/vendored.json`.

**What that obliges.** It is MIT, which asks for one thing: the copyright
notice and the permission text travel with the code, in the program and in
anything built from it. That is why `fme/vendor/LICENSE-*` exists, why the
credits section of the manual names it, and why `tests/test_vendor.py`
fails if any of those go missing. It also means this program now names
another project, which until now it never did -- a deliberate trade, not an
oversight.

**It is used only where it adds something.** A FAT32 or NTFS volume is read
by the engine as before: two readers for the same volume is two answers to
the same question. This is reached for the kinds nothing else here can read.
"""

from __future__ import annotations

import os
import re
import sys

__version__ = "1.0.0"

# Beside the code from a checkout; inside the unpacked folder from a
# frozen build, which is somewhere different every time it starts.
from fme import resources

VENDOR_DIR = resources.vendor_dir()

# What this reads that nothing else here does.
WANTED_KINDS = {
    "qnx6", "qnx4", "etfs", "efs", "ifs",
    "f2fs", "squashfs", "jffs2", "ubi", "ubifs", "yaffs1", "yaffs2",
    "btrfs", "xfs", "refs",
}

# And what it reads that TSK also reads.
#
# These were left out on the grounds that "the engine reads those" -- true
# only where pytsk3 is installed. Without it this program could read a QNX
# car system and not a Windows laptop, while carrying a reader for both.
#
# So they are read here when TSK is absent, and are available as a second
# opinion when it is not: the two disagreed about a deleted FAT entry, and
# an examiner should be able to ask both.
MAINSTREAM_KINDS = {"ntfs", "fat32", "fat16", "fat12", "exfat",
                    "hfsplus", "hfs", "apfs", "ext2", "ext3", "ext4"}


# Volumes the reader finds inside a container -- an LVM logical volume, the
# decrypted inside of a LUKS volume, an assembled md array -- carry these name
# prefixes. The Sleuth Kit as pytsk3 ships it opens none of those containers,
# so a volume inside one is read here whatever its kind: an ext4 inside LVM or
# LUKS was left to TSK because TSK "reads ext4", and TSK never saw it. Found by
# the suite on a machine with pytsk3 installed (v2026.11.322).
CONTAINER_PREFIXES = ("lvm_", "luks_", "md_")


def inside_container(name: str) -> bool:
    """Whether this volume is one the reader found inside a container."""
    return str(name or "").lower().startswith(CONTAINER_PREFIXES)


def kinds_to_read(include_mainstream: bool = False) -> set:
    """Which file systems this reader should be asked for."""
    return WANTED_KINDS | MAINSTREAM_KINDS if include_mainstream \
        else set(WANTED_KINDS)

_probe = None
_tried = False


def probe():
    """The vendored reader, imported on first use.

    Imported late because it is large, and because a program that never
    meets one of these file systems should not pay for loading it.
    """
    global _probe, _tried
    if _probe is not None or _tried:
        return _probe
    _tried = True
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    try:
        import qnxprobe  # noqa: PLC0415 - deliberately late
        _probe = qnxprobe
    except Exception:  # noqa: BLE001 - absence is not a failure
        _probe = None
    return _probe


_ewf = None
_ewf_tried = False


def ewfprobe():
    """The vendored acquisition reader, imported on first use."""
    global _ewf, _ewf_tried
    if _ewf is not None or _ewf_tried:
        return _ewf
    _ewf_tried = True
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    try:
        import ewfprobe  # noqa: PLC0415 - deliberately late
        _ewf = ewfprobe
    except Exception:  # noqa: BLE001
        _ewf = None
    return _ewf


def available() -> bool:
    return probe() is not None


def status() -> dict:
    module = probe()
    record = {}
    try:
        import json
        with open(os.path.join(VENDOR_DIR, "vendored.json"),
                  encoding="utf-8") as fh:
            for entry in json.load(fh).get("vendored", []):
                if entry.get("path") == "qnxprobe.py":
                    record = entry
    except (OSError, ValueError):
        pass
    return {"available": module is not None,
            "kinds": sorted(WANTED_KINDS),
            "version": record.get("version", ""),
            "licence": record.get("licence", ""),
            "upstream": record.get("upstream", "")}


def _open(module, path: str, credentials=None):
    """The image as the reader sees it, with whatever the keys open opened
    (unlock.open_vendored). Raises what open_image raises."""
    try:
        segments = module.split_segments(path)
    except Exception:  # noqa: BLE001 - not a split set, or not readable
        segments = None
    if credentials:
        # The keys open the acquisition itself too (an encrypted AFF, an
        # AD-encrypted set), not only the volumes inside it.
        from fme.engines import unlock
        image = module.open_image_trying(
            path, segments, passwords=list(credentials.passwords),
            private_keys=unlock.private_keys(credentials))
    else:
        image = module.open_image(path, segments)
    if credentials:
        from fme.engines import unlock
        image, _ = unlock.open_vendored(module, image, credentials)
    return image


def kinds_in(path: str, credentials=None) -> list:
    """What file systems are in this image, as the reader sees them.

    Identification only: nothing is walked and nothing is read, so this is
    cheap enough to ask of every image before deciding whether the extra
    reader is needed at all.
    """
    module = probe()
    if module is None:
        return []
    found = []
    try:
        image = _open(module, path, credentials)
    except Exception:  # noqa: BLE001
        return []
    try:
        for volume in module.volumes(image):
            found.append({"name": volume.get("name", ""),
                          "kind": (volume.get("kind") or "").lower(),
                          "label": volume.get("label", ""),
                          "base": volume.get("base", 0),
                          "size": volume.get("size", 0),
                          "readable": volume.get("walker") is not None})
    except Exception:  # noqa: BLE001 - a damaged image is not a crash here
        pass
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass
    return found


# What to tell the examiner about a locked APFS volume. The vendored reader
# can unlock one with its password, but nothing in this program asks for it:
# whether to is an open decision for the unit, so the advice does not
# promise it.
FILEVAULT_ADVICE = (
    "Image the Mac while the volume is unlocked (logged in), or obtain the "
    "user's password or the FileVault recovery key for a tool that can open "
    "it. This program reads the volume; it does not break the encryption.")


def locked_volumes(path: str, credentials=None) -> list:
    """The APFS volumes in this image that are encrypted and locked.

    An APFS container is not encrypted; the volumes inside it are, one by
    one, so no header at a partition's start says "locked" the way a
    BitLocker or LUKS header does (media_core.encrypted_volumes). The
    vendored reader knows: it tests each volume and lists nothing from a
    locked one, because its blocks are ciphertext. walk() asked it for files
    and kept only the count, so a FileVault Mac came back as a volume read
    and found empty -- the one answer an examiner cannot tell from the truth.

    Each locked volume comes back as media_core reports a locked partition,
    with the volume's name and the reader's own words. A volume that is
    flagged encrypted but reads in the clear (an image made through the
    Mac's own decryption) is not locked, and is not listed.
    """
    module = probe()
    if module is None:
        return []
    try:
        image = _open(module, path, credentials)
    except Exception:  # noqa: BLE001
        return []
    found = []
    try:
        for volume in module.volumes(image):
            walker = volume.get("walker")
            encryption = getattr(walker, "encryption", None)
            inside = getattr(walker, "volumes", None)
            if not ((volume.get("kind") or "").lower().startswith("apfs")
                    and callable(encryption) and inside is not None):
                continue
            for index in range(len(inside)):
                try:
                    if encryption(index) != "locked":
                        continue
                    name = inside[index][2] or f"volume {index}"
                except Exception:  # noqa: BLE001 - a damaged entry is not a crash
                    continue
                found.append({"kind": "APFS", "volume": name,
                              "offset": volume.get("base", 0),
                              "partition": volume.get("label", ""),
                              "note": getattr(walker, "note", "") or "",
                              "advice": FILEVAULT_ADVICE})
    except Exception:  # noqa: BLE001 - a damaged image is not a crash here
        pass
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass
    return found


def extra_kinds(path: str, kinds=None, credentials=None) -> list:
    """The volumes worth reading this way: the kinds nothing else reads, or
    the ``kinds`` given (kinds_to_read(), which adds the mainstream ones when
    TSK is absent)."""
    return [volume for volume in kinds_in(path, credentials)
            if volume["readable"] and (_wanted(volume["kind"], kinds)
                                       or inside_container(volume["name"]))]


def _wanted(kind: str, kinds=None) -> bool:
    kind = (kind or "").lower()
    wanted_set = WANTED_KINDS if kinds is None else kinds
    return any(kind.startswith(wanted) or wanted in kind
               for wanted in wanted_set)


def describe(path: str, credentials=None):
    """What the vendored reader makes of this volume, in its own words.

    Used when a run recovers nothing. A volume the program cannot walk
    still reports "0 files", which is also what an empty disk reports
    -- and the reader usually knows exactly why it could not walk it
    (a ReFS superblock whose checksum does not hold, a format it
    refuses, a damaged header). Saying that is the difference between
    "there was nothing on it" and "this could not be read".
    """
    module = probe()
    if module is None:
        return None
    try:
        if credentials:
            # Opened as the run read it: a volume the keys opened is described
            # by what is inside it, not by the encryption it no longer has.
            image = _open(module, path, credentials)
            try:
                found = module.identify_fs(image, 0)
            finally:
                image.close()
        else:
            with open(path, "rb") as fh:
                found = module.identify_fs(fh, 0)
    except Exception:  # noqa: BLE001 - a diagnosis is not worth a failure
        return None
    if not found:
        return None
    kind, lines = found
    return {"kind": kind, "lines": list(lines or [])}


def walk(path: str, on_file, only_extra: bool = True, cancel=None,
         kinds=None, credentials=None) -> dict:
    """Walk the volumes and hand each file to the caller.

    `on_file(record)` receives a dict per file with its path, size, times and
    a `read()` that streams the content. Streamed rather than returned,
    because a run meets video files and a forensic image is not a thing to
    hold in memory.
    """
    module = probe()
    if module is None:
        return {"volumes": 0, "files": 0,
                "error": "the vendored reader is not present"}

    image = _open(module, path, credentials)
    try:
        return _walk_image(module, image, on_file, only_extra, cancel, kinds)
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass


def _walk_image(module, image, on_file, only_extra=True, cancel=None, kinds=None,
                prefix: str = "") -> dict:
    """Every volume of one open image, walked; what walk() returns."""
    volumes_read, files_seen, damaged = 0, 0, 0
    # Which volumes were walked and which were not, with the reader's own
    # word on each. Only a count came back before, so a report could not
    # name what was read, said the LVM volumes it had read were "not
    # opened", and called one device of a Btrfs RAID1 unreadable without
    # saying it was one device of two.
    walked, not_walked = [], []
    for volume in module.volumes(image):
        if cancel is not None and cancel():
            break
        walker = volume.get("walker")
        kind = (volume.get("kind") or "").lower()
        name = prefix + (volume.get("name") or "")
        if walker is None:
            if kind and kind != "not recognised" and \
                    "container" not in kind and "pv" not in kind.split():
                not_walked.append({"name": name, "kind": kind,
                                   "base": volume.get("base", 0),
                                   "note": str(volume.get("note") or "")})
            elif (volume.get("name") or "").startswith("lvm_"):
                not_walked.append({"name": name, "kind": kind,
                                   "base": volume.get("base", 0),
                                   "note": str(volume.get("note") or
                                               "no file system was recognised in it")})
            continue
        if only_extra and not _wanted(kind, kinds) \
                and not inside_container(volume.get("name")):
            continue
        volumes_read += 1
        record = {"name": name, "kind": kind,
                  "base": volume.get("base", 0), "size": volume.get("size", 0),
                  "label": volume.get("label", ""), "files": 0, "note": ""}
        walked.append(record)
        try:
            listing = module.collect(walker, walker.root)
        except Exception as exc:  # noqa: BLE001 - a damaged volume still counts
            damaged += 1
            record["note"] = f"could not be listed: {exc}"
            continue
        record["note"] = _reader_says(module, walker)

        for entry in listing:
            if cancel is not None and cancel():
                break
            try:
                file_path, ino, size, mtime = entry
            except (TypeError, ValueError):
                continue
            if size is None:            # a link or a device node
                continue
            files_seen += 1
            record["files"] += 1

            def read(_ino=ino, _size=size, _walker=walker, got=None):
                # Damage ends the stream rather than the walk, but the
                # reason is handed back in `got` so the caller can mark
                # the record short instead of storing a cut-off file as
                # the whole one.
                done = 0
                try:
                    for chunk in _walker.read_file(_ino, _size):
                        done += len(chunk)
                        yield chunk
                except Exception as exc:  # noqa: BLE001 - damage, not a crash
                    if got is not None:
                        got["error"] = f"read failed at byte {done:,}: {exc}"
                    return

            on_file({"path": file_path, "size": size, "mtime": mtime,
                     "inode": str(ino), "kind": kind,
                     "volume": name,
                     "label": volume.get("label", ""),
                     "read": read})
    return {"volumes": volumes_read, "files": files_seen, "damaged": damaged,
            "walked": walked, "not_walked": not_walked}


def raid_members(path: str, credentials=None) -> dict:
    """The other members of the RAID arrays and LVM volume groups `path` holds
    a member of, found in its folder by what each file records about itself
    (an md array's uuid and role, an LVM volume group's id) -- never by name.

    Returns {"paths": [the other members], "added": [...], "missing": [...],
    "warnings": [...], "ambiguous": [...], "lines": [the reader's account]}.
    A file two copies of which disagree is not used, and is said.
    """
    module = probe()
    empty = {"paths": [], "added": [], "missing": [], "warnings": [],
             "ambiguous": [], "lines": []}
    if module is None or not hasattr(module, "raid_siblings"):
        return empty
    from fme.engines import unlock
    passwords = list(getattr(credentials, "passwords", ()) or ())
    keys = unlock.private_keys(credentials) if credentials else []
    try:
        found = module.raid_siblings(path, passwords=passwords, private_keys=keys)
    except Exception as exc:  # noqa: BLE001 - a search, never a failure
        return dict(empty, warnings=[f"the other members were not searched for: {exc}"])
    return {"paths": [c["path"] for c in found.added],
            "added": [{"path": c["path"], "how": str(c.get("identity") or c.get("why") or "")}
                      for c in found.added],
            "missing": [str(m) for m in found.missing],
            "warnings": [str(w) for w in found.warnings]
                        + ([str(found.error)] if found.error else []),
            "ambiguous": [str(a) for a in found.ambiguous],
            "lines": list(found.lines())}


def walk_raid(paths: list, on_file, cancel=None, credentials=None) -> dict:
    """Assemble the RAID arrays and multi-image LVM volume groups the images
    at `paths` hold, and walk every volume on each. What walk() returns, with
    "arrays": [{"label", "read", "lines"}] for each array found.

    A run took one image, so a member was read alone: an md member with 1.2
    metadata not at all, a RAID5 never from parity, a Btrfs RAID1 from one
    device. The vendored reader assembles them; nothing asked it to
    (v2026.11.315).
    """
    module = probe()
    if module is None or not hasattr(module, "raid_open"):
        return {"volumes": 0, "files": 0, "damaged": 0, "walked": [],
                "not_walked": [], "arrays": []}
    from fme.engines import unlock
    passwords = list(getattr(credentials, "passwords", ()) or ())
    keys = unlock.private_keys(credentials) if credentials else []
    total = {"volumes": 0, "files": 0, "damaged": 0, "walked": [],
             "not_walked": [], "arrays": []}
    with module.raid_open(list(paths), passwords, keys) as raid:
        for array in raid.arrays:
            label = str(getattr(array, "label", "") or "array")
            try:
                said = [str(x) for x in array.lines()]
            except Exception:  # noqa: BLE001
                said = []
            entry = {"label": label, "read": array.image is not None, "lines": said}
            total["arrays"].append(entry)
            if array.image is None:
                total["not_walked"].append({
                    "name": label, "kind": "raid array", "base": 0,
                    "note": str(getattr(array, "refused", "") or "; ".join(said[-2:])
                                or "the array could not be assembled")})
                continue
            result = _walk_image(module, array.image, on_file, only_extra=False,
                                 cancel=cancel, prefix=label.replace("/", "_") + "/")
            for key in ("volumes", "files", "damaged"):
                total[key] += result[key]
            total["walked"] += result["walked"]
            total["not_walked"] += result["not_walked"]
        for label, line in raid.foreign:
            total["not_walked"].append({"name": str(label) or "image", "kind": "raid",
                                        "base": 0, "note": str(line)})
    return total


def _reader_says(module, walker) -> str:
    """The reader's own note on a volume it walked, and any damage it found."""
    parts = []
    note = getattr(walker, "note", None)
    if note:
        parts.append(str(note))
    try:
        count, lines = module.damage_lines(walker)
    except Exception:  # noqa: BLE001 - not every walker keeps a damage list
        count, lines = 0, []
    if count:
        parts.append(f"{count:,} damaged record{'' if count == 1 else 's'}: "
                     + "; ".join(lines))
    return "; ".join(parts)
