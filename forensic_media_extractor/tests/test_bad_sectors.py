"""An unreadable stretch of a disk is a gap in the carve, not its end.

DeviceReader.read caught the error of a failed read and returned nothing
for the whole request -- for the carve, megabytes -- and logged it at debug
level. The carve took an empty answer as the end of the image and stopped,
and because nothing marked it incomplete, the run reported a full carve.
On a failing disk everything after its first bad sector was silently
unexamined.

A device is stood in for by a file whose "bad" sectors raise OSError the
way a failing drive does. Checked: the reader retries sector by sector and
loses only the bad sectors; the carve goes on past them and finds the
pictures beyond; and the summary and the report say which stretches could
not be read.
"""
import io
import logging
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
from fme.exports import report  # noqa: E402

BAD = (5 << 20) + 4096          # one bad sector, 5 MiB in


class FailingDisk(media_core.DeviceReader):
    """A DeviceReader over a file, with one sector that cannot be read."""

    def __init__(self, path: str):           # noqa: D401 - no device to size
        self.path = self.name = path
        self._fd = os.open(path, os.O_RDONLY)
        self.sector = 512
        self.size = os.path.getsize(path)

    def _read_span(self, start: int, size: int) -> bytearray:
        if start <= BAD < start + size:
            raise OSError(5, "Input/output error")
        return super()._read_span(start, size)


def main() -> int:
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image as fixtures
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_bad_sectors-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    rng = __import__("random").Random(3)
    blob = bytearray(rng.getrandbits(8) for _ in range(1 << 20)) * 12
    planted = []
    for i, mib in enumerate((1, 3, 7, 9, 11)):
        pic = fixtures.make_jpeg(70 + i)
        at = (mib << 20) + 1000
        blob[at:at + len(pic)] = pic
        planted.append(at)
    image = os.path.join(root, "failing.dd")
    with open(image, "wb") as fh:
        fh.write(bytes(blob))

    disk = FailingDisk(image)
    whole = disk.read(BAD - 8192, 1 << 20)
    kept = len(whole) == 1 << 20 and whole[:8192] == bytes(blob[BAD - 8192:BAD]) \
        and whole[8704:] == bytes(blob[BAD + 512:BAD - 8192 + (1 << 20)])
    ok &= kept
    print(f"a read across the bad sector returns everything but that sector: {kept}")
    marked = getattr(disk, "bad_ranges", None) == [(BAD, 512)]
    ok &= marked
    print(f"and the reader keeps where it was: {marked} ({getattr(disk, 'bad_ranges', None)})")

    disk = FailingDisk(image)
    out = media_core.OutputManager(os.path.join(root, "out"), metadata=False)
    args = types.SimpleNamespace(
        chunk_size=1 << 20, align=1, max_carve_size=media_core.MAX_VIDEO_DEFAULT,
        signatures=media_core.active_signatures(), resume_from=0, resuming={},
        progress=False, scan_inside=False, max_inside_size=0, output=root, image=image)
    stats = {"carved_files": 0, "carved_bytes": 0, "candidates": 0}
    media_core.run_carve_engine(disk, out, args, stats)
    got = sorted(r.offset for r in out.records)
    past = got == planted
    ok &= past
    print(f"the carve goes on past the bad sector and finds all {len(planted)} "
          f"pictures: {past} ({len(got)})")
    said = stats.get("unreadable_bytes") == 512 and \
        stats.get("unreadable_ranges") == [[BAD, 512]]
    ok &= said
    print(f"the summary says which stretch could not be read: {said}")
    limits = report.coverage_limits({"summary": {**stats}})
    told = any("could not be read" in line for line in limits)
    ok &= told
    print(f"and the report says so: {told}")
    out.close({})

    # A carved file that spans a bad sector is stored up to it and recorded
    # short there -- not zero-filled and hashed as though it were whole.
    class Salvaged:
        bad_ranges = [(1536, 512)]

        def read(self, offset, length):
            return b"\x00" * length

    taken = b"".join(media_core._carve_chunks(Salvaged(), 1024, 4096,
                                              got=(seen := {})))
    stops = len(taken) == 512 and seen["bytes"] == 512
    ok &= stops
    print(f"a carved file stops at a zero-filled bad sector: {stops} "
          f"({len(taken)} bytes)")

    # Past a thousand stretches the list was cut, and the next call counted
    # the bytes from the cut list: 1,501 gaps of 512 bytes reported 512,512
    # bytes unread instead of 768,512 (v2026.11.312).
    many = {}
    media_core._note_unread(many, [(i * 4096, 512) for i in range(1501)])
    media_core._note_unread(many, [(10 ** 9, 512)])
    counted = many.get("unreadable_bytes") == 1502 * 512
    ok &= counted
    print(f"1,502 bad stretches count every byte: {counted} "
          f"({many.get('unreadable_bytes'):,} bytes)")
    trimmed = {"unreadable_ranges": many["unreadable_ranges"],
               "unreadable_bytes": many["unreadable_bytes"]}
    out = media_core.OutputManager(os.path.join(root, "trim"), metadata=False)
    out.close(trimmed)
    listed = (len(trimmed["unreadable_ranges"]) == 1000
              and trimmed.get("unreadable_range_count") == 1502
              and trimmed["unreadable_bytes"] == 1502 * 512)
    ok &= listed
    print(f"the summary lists 1,000 of them and says there were 1,502: {listed}")
    said = "1,502 stretch(es)" in " ".join(
        report.coverage_limits({"summary": trimmed}))
    ok &= said
    print(f"and the report gives the real count: {said}")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
