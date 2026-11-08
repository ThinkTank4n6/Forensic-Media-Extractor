"""Tie a case to the evidence it was made from.

A case recorded the image's path, size and start time, and nothing that
would show the image had been swapped or altered between acquisition and
processing: no hash of the source, no check of the hash an E01 carries, and
no hash of manifest.json itself (an outside review, v2026.11.312).

Three things now, each cheap where it can be:

* The hashes the acquisition recorded about itself (an E01's MD5 and SHA-1,
  an AFF4's, a DMG's), read from its headers. Costs nothing, always done.
* A hash of the source as this run read it -- MD5, SHA-1 and SHA-256 of the
  media, in one pass -- compared with what the acquisition recorded. A full
  read of the evidence, so it is an option (``hash_source``), on by default
  in the interface and off by default on the command line.
* The SHA-256 of manifest.json, written beside it as manifest.json.sha256
  in the format ``sha256sum -c`` reads. A later change to the case record
  shows.
"""
from __future__ import annotations

import hashlib
import logging
import os

LOG = logging.getLogger("fme")
BLOCK = 4 * 1024 * 1024
ALGORITHMS = ("md5", "sha1", "sha256")
MANIFEST_DIGEST = "manifest.json.sha256"


def stored_hashes(reader, image_path: str = "") -> dict:
    """The digests the acquisition recorded about its media, keyed by
    lower-case algorithm name. Empty when it recorded none, or for a raw
    image and a device, which carry nothing to compare with."""
    found: dict = {}
    image = getattr(reader, "_image", None)          # the vendored reader
    for algo, value in (getattr(image, "stored_hashes", None) or {}).items():
        if value:
            found[str(algo).lower()] = str(value).lower()
    handle = getattr(reader, "_h", None)             # libewf
    if handle is not None and not found:
        for algo in ("MD5", "SHA1"):
            value = None
            for name in ("get_hash_value", "get_hash_value_by_identifier"):
                getter = getattr(handle, name, None)
                if getter is None:
                    continue
                try:
                    value = getter(algo)
                except Exception:  # noqa: BLE001 - not every build has it
                    value = None
                if value:
                    break
            if value:
                found[algo.lower()] = str(value).strip().lower()
    if not found and image_path and os.path.splitext(image_path)[1].lower() in (
            ".e01", ".ex01", ".s01", ".aff4", ".aff", ".dmg"):
        # libewf opened it and would not say: the vendored reader reads the
        # hash section from the headers without reading the media.
        try:
            from fme.engines import vendor_fs
            module = vendor_fs.ewfprobe()
            if module is not None:
                probe = module.open_image(image_path, None)
                try:
                    for algo, value in (probe.stored_hashes or {}).items():
                        if value:
                            found[str(algo).lower()] = str(value).lower()
                finally:
                    probe.close()
        except Exception:  # noqa: BLE001 - a record, never a failure
            LOG.debug("could not read the acquisition's stored hashes",
                      exc_info=True)
    return found


def hash_source(reader, progress=None, cancel=None) -> dict:
    """MD5, SHA-1 and SHA-256 of everything the reader presents, in one pass.

    ``progress(done, total)`` is called as it goes; ``cancel()`` returning
    True stops it, and the result then says it is incomplete rather than
    giving a digest of part of the evidence as if it were the whole.
    """
    hashers = {a: hashlib.new(a) for a in ALGORITHMS}
    size = int(getattr(reader, "size", 0) or 0)
    done = 0
    while done < size:
        if cancel is not None and cancel():
            break
        data = reader.read(done, min(BLOCK, size - done))
        if not data:
            break
        for h in hashers.values():
            h.update(data)
        done += len(data)
        if progress is not None:
            progress(done, size)
    complete = done == size
    result = {"bytes": done, "size": size, "complete": complete}
    if complete:
        result.update({a: h.hexdigest() for a, h in hashers.items()})
    return result


def evidence_record(reader, image_path: str, compute: bool = False,
                    progress=None, cancel=None) -> dict:
    """What the summary says about the evidence itself."""
    record: dict = {"stored": stored_hashes(reader, image_path)}
    if not compute:
        record["verified"] = None
        record["note"] = ("the source was not hashed by this run"
                          + (": the acquisition's own hashes are recorded "
                             "above, unchecked" if record["stored"] else ""))
        return record
    got = hash_source(reader, progress, cancel)
    if not got["complete"]:
        record["verified"] = None
        record["note"] = (f"hashing the source stopped after {got['bytes']:,} "
                          f"of {got['size']:,} bytes; no digest is recorded")
        return record
    record["computed"] = {a: got[a] for a in ALGORITHMS}
    agree = [got[a] == v for a, v in record["stored"].items() if a in got]
    record["verified"] = (all(agree) if agree else None)
    bad = list(getattr(reader, "bad_ranges", None) or [])
    if record["verified"] is True:
        record["note"] = "the source matches the hash the acquisition recorded"
    elif record["verified"] is False:
        record["note"] = ("THE SOURCE DOES NOT MATCH THE HASH THE ACQUISITION "
                          "RECORDED: it has changed since it was acquired, or "
                          "it was read differently")
        LOG.warning("The source does not match the hash its acquisition "
                    "recorded: %s", record["stored"])
    else:
        record["note"] = ("the acquisition recorded no hash to compare with; "
                          "these digests identify the source as this run read it")
    if bad:
        record["note"] += (f". {len(bad)} unreadable stretch(es) were read as "
                           f"zeros and are included in these digests")
    return record


def write_manifest_digest(outdir: str) -> str:
    """SHA-256 of manifest.json, beside it, as ``sha256sum -c`` reads it.
    Returns the digest, or an empty string when there is no manifest."""
    path = os.path.join(outdir, "manifest.json")
    if not os.path.isfile(path):
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(BLOCK), b""):
            digest.update(block)
    value = digest.hexdigest()
    scratch = os.path.join(outdir, f"{MANIFEST_DIGEST}.{os.getpid()}.tmp")
    with open(scratch, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{value}  manifest.json\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(scratch, os.path.join(outdir, MANIFEST_DIGEST))
    return value


def check_manifest_digest(outdir: str):
    """True if manifest.json still has the digest written beside it, False
    if it has changed, None if no digest was written."""
    try:
        with open(os.path.join(outdir, MANIFEST_DIGEST), encoding="utf-8") as fh:
            expected = fh.read().split()[0].lower()
    except (OSError, IndexError):
        return None
    digest = hashlib.sha256()
    try:
        with open(os.path.join(outdir, "manifest.json"), "rb") as fh:
            for block in iter(lambda: fh.read(BLOCK), b""):
                digest.update(block)
    except OSError:
        return False
    return digest.hexdigest() == expected
