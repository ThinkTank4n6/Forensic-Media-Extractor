"""A file whose size a file system recorded is kept, however small.

The size floor (64 bytes for pictures, 8 KiB for video) exists for the
carve, where a tiny result is usually a false match. It was applied to
every file stored, so an allocated 5 KB clip in a phone's camera folder --
a named, real file -- was dropped as noise, and nothing counted it.
Now the floor applies to guessed lengths only, and what it drops is
counted in the summary.
"""
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_sized_files-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    out = media_core.OutputManager(root, metadata=False)
    clip = b"\x00\x00\x00\x18ftypmp42" + os.urandom(5000)        # a 5 KB video
    named = out.store(iter([clip]), category="video", ext="mp4",
                      stem="VID_0001", engine="filesystem",
                      source_path="/DCIM/VID_0001.mp4")
    ok &= named is not None
    print(f"an allocated 5 KB clip is kept: {named is not None}")
    deleted = out.store(iter([clip + b"x"]), category="video", ext="mp4",
                        stem="VID_0002", engine="fat-deleted", deleted=True)
    ok &= deleted is not None
    print(f"a deleted entry of that size is kept: {deleted is not None}")
    carved = out.store(iter([clip + b"y"]), category="video", ext="mp4",
                       stem="recovered_0001_0", engine="carve", offset=0)
    ok &= carved is None
    print(f"a carved one of that size is still dropped: {carved is None}")
    summary = {}
    out.close(summary)
    counted = summary.get("undersized_dropped") == 1
    ok &= counted
    print(f"and the drop is counted in the summary: {counted}")
    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
