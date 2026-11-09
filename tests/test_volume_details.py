"""Small things that changed what a case holds without saying so.

1. Joined streams (fme/engines/streams.py) were written as stream_<n>.bin
   in one scratch folder for the whole run, with n counted afresh in each
   folder: the second folder's stream_0.bin replaced the first's before
   either was stored, so one download was stored under two manifests and
   the other not at all. A manifest that could not be planned gave nothing
   without a word, and a stream whose plan did not say how many segments
   it lists was called complete.
2. MBR partition addresses were multiplied by 512 whatever the disk's
   sector size; on a 4Kn disk every partition was looked for at an eighth
   of its offset. GPT already tried both.
3. A deleted NTFS file too large for ntfs_mft.content() (over 2 GiB) came
   back empty and the MFT pass dropped it with a bare `continue`. It is
   streamed now (content_stream), and one that cannot be read is counted.
"""
import collections
import contextlib
import hashlib
import io
import logging
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import media_core, ntfs_mft, streams  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_volume_details-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


# --------------------------------------------------------------------------
print("streams joined from two folders are two files")


class Probe:
    """exoprobe's plan and write, reduced to what join() asks of them."""
    listed = True

    @staticmethod
    def plan_streams(items):
        paths = sorted(items)
        if any(p.endswith("broken.mpd") for p in paths):
            raise ValueError("not a manifest")
        segs = [p for p in paths if p.endswith(".m4s")]
        st = {"manifest": next(p for p in paths if p.endswith(".mpd")),
              "format": "DASH", "rep": {"id": "v"}, "init": None,
              "segs": segs, "handlers": ["vide"]}
        if Probe.listed:
            st["listed"] = len(segs)
        return [st], []

    @staticmethod
    def write_stream(parts, dest):
        with open(dest, "wb") as fh:
            for p in parts:
                with open(p, "rb") as src:
                    fh.write(src.read())


tree = os.path.join(ROOT, "tree")
for folder, body in (("a", b"AAAA"), ("b", b"BBBB")):
    os.makedirs(os.path.join(tree, folder))
    with open(os.path.join(tree, folder, "stream.mpd"), "w") as fh:
        fh.write("<MPD/>")
    with open(os.path.join(tree, folder, "seg-1.m4s"), "wb") as fh:
        fh.write(body * 100)
real_probe = streams._probe
streams._probe = lambda: Probe
try:
    stats = {}
    joined, scratch = streams.joined_in(tree, stats)
    contents = []
    for item in joined:
        with open(item["path"], "rb") as fh:
            contents.append(fh.read()[:4])
    check("each folder's stream is its own file", len({i["path"] for i in joined}) == 2
          and sorted(contents) == [b"AAAA", b"BBBB"], contents)
    check("and is complete against the count its manifest gave",
          all(i["complete"] for i in joined))
    shutil.rmtree(scratch, ignore_errors=True)

    Probe.listed = False
    joined, scratch = streams.joined_in(tree, stats)
    check("with no count from the manifest, a stream is not called complete",
          joined and not any(i["complete"] for i in joined)
          and all(i["listed"] is None for i in joined))
    shutil.rmtree(scratch, ignore_errors=True)

    with open(os.path.join(tree, "a", "broken.mpd"), "w") as fh:
        fh.write("not xml")
    joined, scratch = streams.joined_in(tree, stats)
    check("a manifest that cannot be planned is counted, not silent",
          stats.get("stream_plan_failed") == 1, stats)
    shutil.rmtree(scratch, ignore_errors=True)
finally:
    streams._probe = real_probe

# --------------------------------------------------------------------------
print("\nan MBR on a 4Kn disk counts in 4,096-byte sectors")
saved_argv = sys.argv
sys.argv = [saved_argv[0], os.path.join(ROOT, "ntfs.dd")]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        import make_ntfs_test_image as ntfs
finally:
    sys.argv = saved_argv
volume = bytes(ntfs.image)
expected = hashlib.sha256(ntfs.png_big).hexdigest()


def disk(lba, unit):
    start = lba * unit
    raw = bytearray(start + len(volume) + 4096)
    mbr = bytearray(512)
    struct.pack_into("<B3sB3sII", mbr, 446, 0, b"\0\0\0", 0x07, b"\0\0\0",
                     lba, len(volume) // unit)
    mbr[510:512] = b"\x55\xaa"
    raw[0:512] = mbr
    raw[start:start + len(volume)] = volume
    path = os.path.join(ROOT, f"disk_{unit}.dd")
    with open(path, "wb") as fh:
        fh.write(bytes(raw))
    return path


for unit, lba in ((512, 16), (4096, 2)):
    path = disk(lba, unit)
    reader = media_core.open_image(path)
    parts = media_core._table_partitions(reader)
    check(f"{unit}-byte sectors: the partition is at its offset",
          [p["offset"] for p in parts] == [lba * unit], parts)
    out = media_core.OutputManager(os.path.join(ROOT, f"out_{unit}"), metadata=False)
    media_core.run_mft_recovery(reader, out, types.SimpleNamespace(image=path),
                                collections.defaultdict(int), media_core.SIGNATURES,
                                volumes=parts)
    got = {os.path.basename(r.source_path): r for r in out.records}
    check(f"{unit}-byte sectors: the deleted file on it is recovered",
          "holiday_photo.png" in got and got["holiday_photo.png"].sha256 == expected
          and got["holiday_photo.png"].partition == "p1")
    out.close({})
    reader.close()

# --------------------------------------------------------------------------
print("\na deleted file too large to read into memory is streamed")
path = os.path.join(ROOT, "ntfs.dd")
real_limit = ntfs_mft.IN_MEMORY_LIMIT
ntfs_mft.IN_MEMORY_LIMIT = 1000           # the PNG is larger than this
try:
    reader = media_core.open_image(path)
    out = media_core.OutputManager(os.path.join(ROOT, "out_stream"), metadata=False)
    stats = collections.defaultdict(int)
    media_core.run_mft_recovery(reader, out, types.SimpleNamespace(image=path),
                                stats, media_core.SIGNATURES)
    got = {os.path.basename(r.source_path): r for r in out.records}
    check("it is recovered whole", "holiday_photo.png" in got
          and got["holiday_photo.png"].sha256 == expected)
    check("nothing was left unread", not stats.get("mft_not_read"),
          stats.get("mft_not_read"))
    out.close({})

    # The MFT pass as it is with a reader that cannot stream: the module
    # it sees has no content_stream.
    saved_mft = _engine.replace("ntfs_mft", types.SimpleNamespace(**{
        k: getattr(ntfs_mft, k) for k in dir(ntfs_mft)
        if k != "content_stream" and not k.startswith("__")}))
    try:
        out = media_core.OutputManager(os.path.join(ROOT, "out_nostream"),
                                       metadata=False)
        stats = collections.defaultdict(int)
        media_core.run_mft_recovery(reader, out, types.SimpleNamespace(image=path),
                                    stats, media_core.SIGNATURES)
        check("one that cannot be read is counted, not dropped in silence",
              stats.get("mft_not_read") == 1, stats.get("mft_not_read"))
        out.close({})
    finally:
        _engine.restore(saved_mft)
    reader.close()
finally:
    ntfs_mft.IN_MEMORY_LIMIT = real_limit

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
