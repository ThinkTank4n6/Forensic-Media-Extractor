"""A resumed carve does not cut up a file the interrupted run had carved.

A resume restarts the scan a little before where the run stopped. A file
the interrupted run had already carved, starting before that point and
running past it, was unknown to the resumed scan, so the signatures nested
inside it -- a picture inside a video's data -- were carved as files of
their own, which a run that was never interrupted does not produce. Both
the single-worker and the parallel carve are checked.

Runs under the __main__ guard: the parallel carve spawns workers.
"""
import collections
import contextlib
import io
import logging
import os
import random
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

from fme.engines import media_core

MB = 1 << 20


def main() -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        import make_media_test_image as fixtures
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_straddle-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    inner = fixtures.make_jpeg(6)
    payload = bytearray(random.Random(3).getrandbits(8) for _ in range(2 * MB))
    payload[int(1.7 * MB):int(1.7 * MB) + len(inner)] = inner
    video = (fixtures._box(b"ftyp", b"isom\0\0\2\0isomiso2mp41")
             + fixtures._box(b"moov", bytes(600)) + fixtures._box(b"mdat", bytes(payload)))
    image = os.path.join(root, "straddle.dd")
    disk = bytearray(4 * MB)
    disk[MB // 2:MB // 2 + len(video)] = video          # 0.5 MiB to about 2.5 MiB
    with open(image, "wb") as fh:
        fh.write(bytes(disk))

    def carve(out_dir, resume_from=0, resume=False, workers=1):
        reader = media_core.open_image(image)
        out = media_core.OutputManager(out_dir, metadata=False, resume=resume)
        args = types.SimpleNamespace(
            image=image, chunk_size=MB, align=1, max_carve_size=64 * MB,
            signatures=media_core.SIGNATURES, resume_from=resume_from,
            scan_inside=False, progress=False, images_only=False,
            videos_only=False, artifacts=False)
        stats = collections.defaultdict(int)
        try:
            if workers > 1:
                media_core.run_carve_engine_parallel(reader, out, args, stats,
                                                     workers=workers)
            else:
                media_core.run_carve_engine(reader, out, args, stats)
        finally:
            out.close({})
            reader.close()
        return sorted((r.engine, r.offset) for r in out.records)

    whole = carve(os.path.join(root, "whole"))
    print(f"a run left alone carves the video, and only it: {whole}")
    for workers in (1, 2):
        case = os.path.join(root, f"resumed_{workers}")
        carve(case)                                  # the run that stopped
        resumed = carve(case, resume_from=2 * MB, resume=True, workers=workers)
        right = resumed == whole
        ok &= right
        print(f"   resumed at 2 MiB with {workers} worker(s): the same, nothing cut "
              f"out of the video: {right} {resumed}")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
