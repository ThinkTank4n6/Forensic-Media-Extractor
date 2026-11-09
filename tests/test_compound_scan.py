"""Does compound scanning find images a whole-file scan misses?"""
import tempfile
import io, os, sys, types, zipfile, shutil, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.chdir(HERE)
import make_media_test_image as g

# a .docx-shaped zip with two images inside (deflated -- invisible to carving)
docx = io.BytesIO()
with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", "<Types/>")
    z.writestr("word/document.xml", "<w:document/>" * 200)
    z.writestr("word/media/image1.png", g.make_png())
    z.writestr("word/media/image2.jpeg", g.make_jpeg())
DOCX = docx.getvalue()

# a JPEG carrying an EXIF thumbnail (a second JPEG inside the first)
thumb = g.make_jpeg(3)
main = g.make_jpeg(40)
JPEG_WITH_THUMB = main[:2] + b"\xff\xe1" + len(thumb).to_bytes(2, "big") + thumb + main[2:]

FILES = [("REPORT.DOCX", DOCX), ("PHOTO.JPG", JPEG_WITH_THUMB)]

def build_mock():
    mock = types.ModuleType("pytsk3")
    for k, v in (("TSK_IMG_TYPE_EXTERNAL",1),("TSK_FS_META_TYPE_DIR",2),
                 ("TSK_FS_META_FLAG_UNALLOC",1),("TSK_FS_NAME_TYPE_DIR",2),
                 ("TSK_VS_PART_FLAG_META",4),("TSK_FS_TYPE_NTFS",9)):
        setattr(mock, k, v)
    mock.TSK_VERSION_STR = "mock"
    class Img_Info:
        def __init__(self, url="", type=None): pass
    mock.Img_Info = Img_Info
    class _N:
        def __init__(s, n, t): s.name = n.encode(); s.type = t
    class _M:
        def __init__(s, a, sz):
            s.addr, s.size, s.type, s.flags = a, sz, 1, 0
            s.mtime = s.atime = s.ctime = s.crtime = 1700000000
    class _I:
        def __init__(s, n, m): s.name, s.meta = n, m
    class _E:
        def __init__(s, name, data, addr):
            s.info = _I(_N(name,1), _M(addr, len(data))); s._d = data
        def as_directory(s): return []
        def read_random(s, off, size): return s._d[off:off+size]
    made = [_E(n, d, 10+i) for i, (n, d) in enumerate(FILES)]
    class FS_Info:
        def __init__(self, img, offset=0, **kw):
            if offset: raise IOError("no fs")
            self.info = types.SimpleNamespace(ftype=9)
        def open_dir(self, path="/"): return made
    mock.FS_Info = FS_Info
    class Volume_Info:
        def __init__(self, img): raise IOError("none")
    mock.Volume_Info = Volume_Info
    return mock

sys.modules["pytsk3"] = build_mock()
from fme.engines import media_core

class Src:
    def seek(self, o, w=0): return o
    def read(self, n=-1): return b"\x00" * (4096 if n < 0 else min(n, 4096))
    def tell(self): return 0
    def get_size(self): return 64 * 1024**2
    def close(self): pass

found = {}
for label, inside in (("without --scan-inside", False), ("with --scan-inside", True)):
    out = os.path.join(tempfile.gettempdir(), f"inside_{inside}"); shutil.rmtree(out, ignore_errors=True)
    s = media_core.run_extraction("mock", out, mode="fs", source=Src(),
                                  scan_inside=inside)
    imgs = [f for f in s["files"] if f["category"] == "image"]
    found[inside] = {f["source_path"] for f in imgs}
    print(f"{label:24} -> {len(imgs)} image(s)")
    for f in imgs:
        print(f"     {f['engine']:9} {f['size']:>7}  {f['source_path'][:58]}")

# This script used to print the table above and exit 0 whatever it said, so
# compound scanning could have stopped working with nothing failing. What it
# shows is now checked.
ok = True
plain = found[False] == {"/PHOTO.JPG"}
ok &= plain
print(f"\nwithout scanning inside, only the photo itself: {plain}")
members = {"/REPORT.DOCX::word/media/image1.png",
           "/REPORT.DOCX::word/media/image2.jpeg"} <= found[True]
ok &= members
print(f"with it, both pictures deflated inside the .docx: {members}")
thumb = any(p.startswith("/PHOTO.JPG::+") for p in found[True])
ok &= thumb
print(f"and the thumbnail inside the photo: {thumb}")
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
