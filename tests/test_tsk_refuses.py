"""A volume The Sleuth Kit refuses is still read, by this program's FAT reader.

Seen on Windows with v2026.11.274, where pytsk3 is installed: over the
fixture FAT volume TSK logged "No recognized file system at offset 0", the
file system engine found 0 files, and every live file came back from the
carve with no name and no path. Without pytsk3 the same image gave all five
live files with their paths, because only then was the program's own FAT
reader asked.

TSK was right to refuse. The FAT specification decides the FAT type by
cluster count alone, and the fixture is formatted FAT32 with 4,000
clusters, where FAT32 starts at 65,525. Volumes like that exist on
evidence -- mkfs.fat -F 32 makes them, and some devices write them -- and
this program's reader opens them. Now it is asked to whenever TSK refuses
a volume, and only for those volumes.

pytsk3 is not needed: a stand-in for it refuses every volume the way TSK
refused this one, which is the case under test. With the real pytsk3
installed the same fixture takes the same path, since TSK refuses it too.
"""
import contextlib
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


def refusing_tsk():
    """pytsk3 as far as media_core uses it, refusing every volume."""
    mock = types.ModuleType("pytsk3")
    mock.TSK_VERSION_STR = "stand-in that refuses every volume"
    mock.TSK_IMG_TYPE_EXTERNAL = 1
    mock.TSK_VS_PART_FLAG_META = 4

    class Img_Info:
        def __init__(self, url="", type=None):
            pass

    def refuse(*args, **kwargs):
        raise IOError("FS_Info: Unable to determine file system type")

    mock.Img_Info = Img_Info
    mock.Volume_Info = lambda img: refuse()
    mock.FS_Info = refuse
    return mock


sys.modules["pytsk3"] = refusing_tsk()
from fme.engines import media_core  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixture  # noqa: E402

ok = True


def check(label, passed, detail=""):
    global ok
    ok &= bool(passed)
    print(f"  {label}: {bool(passed)}" + (f"  ({detail})" if detail and not passed else ""))


LIVE = {"/DCIM/IMG_0001.JPG", "/DCIM/IMG_0002.PNG", "/DCIM/ANIM.GIF",
        "/MOVIES/VID_0003.MP4", "/MOVIES/CLIP.AVI"}

root = tempfile.mkdtemp(prefix="fme_tsk_refuses-")
try:
    image = os.path.join(root, "media_test.img")
    with contextlib.redirect_stdout(io.StringIO()):
        fixture.main(image)

    print("the stand-in is the one in use, and it refuses")
    check("media_core sees pytsk3 as installed", media_core.pytsk3 is not None)

    summary = media_core.run_extraction(image, os.path.join(root, "out"),
                                        mode="fs", progress=False)
    files = summary["files"]
    named = {f["source_path"] for f in files
             if f.get("engine") == "filesystem" and f.get("source_path")}

    print("\nthe refused volume is read anyway")
    check("all five live files come back from the file system, with their paths",
          LIVE <= named, sorted(LIVE - named))
    volumes = summary.get("volumes") or []
    by_own = [v for v in volumes if "fat32_core" in str(v.get("fs", ""))]
    check("the run's volume list says this program's reader read it", bool(by_own),
          volumes)
    check("and records that TSK refused it",
          summary.get("tsk_refused") == [{"offset": 0, "label": "p0"}],
          summary.get("tsk_refused"))
    paths = [f["source_path"] for f in files if f.get("engine") == "filesystem"]
    check("no file is read twice", len(paths) == len(set(paths)),
          sorted(p for p in paths if paths.count(p) > 1))
finally:
    shutil.rmtree(root, ignore_errors=True)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
