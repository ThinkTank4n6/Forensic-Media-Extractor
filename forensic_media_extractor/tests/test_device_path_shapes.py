"""A device path is given only where the member really is the phone's tree.

Two ways a path that never existed on the phone was recorded as the
device's, to be cited as "where the file was":

  * a UFDR that files members by category -- files/Image/x.jpg -- matched
    the catch-all ^files/(.*) and became "/Image/x.jpg";
  * lstrip("./") strips every leading dot and slash, so a hidden folder
    lost its dot: ".thumbnails/x.jpg" became "/thumbnails/x.jpg".

And a member nothing recognizes was given "/" + its archive path. Now the
device path is "" for all of these, so the archive path is what is shown.
"""
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.engines import extraction

ROOT = os.path.join(tempfile.gettempdir(), "fme_device_path_shapes-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail else ''} -- "
          f"{'ok' if good else 'WRONG'}")


cases = [
    ("files/Image/IMG_0001.jpg", ("", "Cellebrite"), "a category folder is not the device"),
    ("files/Video/clip.mp4", ("", "Cellebrite"), "nor is another"),
    ("files/Fs/1/private/var/mobile/Media/DCIM/a.jpg",
     ("/private/var/mobile/Media/DCIM/a.jpg", "Cellebrite"), "the file-system tree still maps"),
    ("files/private/var/mobile/Media/a.jpg",
     ("/private/var/mobile/Media/a.jpg", "Cellebrite"), "as does one rooted at a device folder"),
    ("files/data/media/0/DCIM/a.jpg", ("/data/media/0/DCIM/a.jpg", "Cellebrite"),
     "an Android tree under files/"),
    ("./data/media/0/.thumbnails/x.jpg", ("/data/media/0/.thumbnails/x.jpg", "Android"),
     "a leading ./ goes, the hidden folder's dot stays"),
    ("data/data/com.app/.cache/a.jpg", ("/data/data/com.app/.cache/a.jpg", "Android"),
     "a hidden folder inside the tree"),
    (".thumbnails/x.jpg", ("", ""), "an unrecognized member gets no device path"),
    ("holiday/photos/x.jpg", ("", ""), "nor does a plain zip's folder"),
]
for member, want, label in cases:
    got = extraction.device_path(member)
    check(f"{label} ({member})", got == want, got)

print("\na member with no device path keeps its archive path")
archive = os.path.join(ROOT, "category.ufdr")
with zipfile.ZipFile(archive, "w") as z:
    z.writestr("files/Image/a.jpg", b"\xff\xd8\xff\xe0one")
    z.writestr("files/Image/b.jpg", b"\xff\xd8\xff\xe0two")
with zipfile.ZipFile(archive) as z:
    mapped = {name: extraction.device_path(name) for name in z.namelist()}
check("both are given no device path, so the archive path is what is shown",
      len(mapped) == 2 and all(where == "" for where, _tool in mapped.values()))

print("\nthe unused extractor and its unsafe path builder are gone")
check("extraction.walk and extraction._safe no longer exist",
      not hasattr(extraction, "walk") and not hasattr(extraction, "_safe"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
