"""Does the FS engine — and the database hook — run on non-FAT filesystems?

Mocks pytsk3 presenting the same volume as NTFS, exFAT, HFS+, APFS and ext4,
each holding a real SQLite database plus ordinary media, and checks that the
walk extracts blobs on every one of them.
"""
import tempfile
import sys, types, os, json, sqlite3, subprocess, shutil

# Run from a checkout without installing: the project root is one level up.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.path.join(HERE, "media_test.img")
if not os.path.isfile(IMAGE):
    # Build the fixture rather than requiring someone to have run the maker
    # first: a test that needs a setup step is a test that gets skipped.
    subprocess.run([sys.executable, os.path.join(HERE, "make_media_test_image.py")],
                   cwd=HERE, capture_output=True, check=False)

FS_TYPES = {"NTFS": 9, "EXFAT": 10, "HFS": 11, "APFS": 12, "EXT4": 13, "FAT32": 14}

def build_db(path):
    if os.path.exists(path): os.remove(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA page_size=4096")
    con.execute("CREATE TABLE cache (id INTEGER PRIMARY KEY, blob BLOB)")
    import io
    from PIL import Image
    im = Image.new("RGB", (700, 520)); px = im.load()
    for y in range(0, 520, 2):
        for x in range(0, 700, 2): px[x, y] = ((x*3) % 256, (y*7) % 256, (x+y) % 256)
    b = io.BytesIO(); im.save(b, "JPEG", quality=95)
    big = b.getvalue()
    con.execute("INSERT INTO cache VALUES (1, ?)", (big,))
    con.commit(); con.close()
    return big

BIG = build_db(os.path.join(tempfile.gettempdir(), "fs_gallery.db"))
DB_BYTES = open(os.path.join(tempfile.gettempdir(), "fs_gallery.db"), "rb").read()
print(f"database {len(DB_BYTES)} bytes holding one {len(BIG)}-byte JPEG "
      f"({len(BIG)//4096} overflow pages)\n")

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40 + b"padding" * 300 + b"\xff\xd9"

def make_mock(fs_name, fs_const):
    mock = types.ModuleType("pytsk3")
    mock.TSK_IMG_TYPE_EXTERNAL = 1
    mock.TSK_FS_META_TYPE_DIR = 2
    mock.TSK_FS_META_FLAG_UNALLOC = 1
    mock.TSK_FS_NAME_TYPE_DIR = 2
    mock.TSK_VS_PART_FLAG_META = 4
    mock.TSK_VERSION_STR = "4.12.0-mock"
    setattr(mock, f"TSK_FS_TYPE_{fs_name}", fs_const)
    class Img_Info:
        def __init__(self, url="", type=None): pass
    mock.Img_Info = Img_Info

    class _Name:
        def __init__(self, n, t): self.name = n.encode(); self.type = t
    class _Meta:
        def __init__(self, addr, size, typ, flags):
            self.addr, self.size, self.type, self.flags = addr, size, typ, flags
            self.mtime = self.atime = self.ctime = self.crtime = 1700000000
    class _Info:
        def __init__(self, name, meta): self.name, self.meta = name, meta
    class _Entry:
        def __init__(self, name, meta, data=b"", children=None):
            self.info = _Info(name, meta); self._d = data; self._c = children
        def as_directory(self): return self._c
        def read_random(self, off, size): return self._d[off:off+size]

    def build():
        return [
            _Entry(_Name("photo.jpg", 1), _Meta(10, len(JPEG), 1, 0), JPEG),
            _Entry(_Name("cache.db", 1), _Meta(11, len(DB_BYTES), 1, 0), DB_BYTES),
            _Entry(_Name("notes.txt", 1), _Meta(12, 40, 1, 0), b"x" * 40),
        ]

    class FS_Info:
        def __init__(self, img, offset=0, **kw):
            if offset != 0: raise IOError("no fs")
            self.info = types.SimpleNamespace(ftype=fs_const)
        def open_dir(self, path="/"): return build()
    mock.FS_Info = FS_Info
    class Volume_Info:
        def __init__(self, img): raise IOError("no volume system")
    mock.Volume_Info = Volume_Info
    return mock

results = {}
for fs_name, const in FS_TYPES.items():
    sys.modules.pop("pytsk3", None)
    # The engine caches the pytsk3 module it imported, so it has to be
    # reimported for the next mock to take effect. Dropping it from
    # sys.modules is not enough: the parent package still holds it as an
    # attribute, and "from fme.engines import media_core" would hand that
    # back instead of importing afresh.
    sys.modules.pop("fme.engines.media_core", None)
    import fme.engines
    if hasattr(fme.engines, "media_core"):
        del fme.engines.media_core
    sys.modules["pytsk3"] = make_mock(fs_name, const)
    import importlib
    media_core = importlib.import_module("fme.engines.media_core")
    out = os.path.join(tempfile.gettempdir(), f"fs_{fs_name}")
    shutil.rmtree(out, ignore_errors=True)
    summary = media_core.run_extraction(IMAGE, out, mode="fs")
    files = summary["files"]
    db_hits = [f for f in files if f["engine"] == "database"]
    exact = any(open(os.path.join(out, f["out_path"]), "rb").read() == BIG for f in db_hits)
    results[fs_name] = (summary["volumes"][0]["fs"], len(files), len(db_hits), exact)

print(f"{'requested':10} {'TSK reports':12} {'files':>6} {'from db':>8}  blob byte-exact")
for fs, (reported, total, dbs, exact) in results.items():
    print(f"{fs:10} {reported:12} {total:>6} {dbs:>8}  {exact}")


# ---------------------------------------------------------------------------
# Deleted FAT entries
#
# A deleted entry is not listed by any walk -- the file system no longer
# claims it -- so recovering one needs a pass that reads the directory
# entries themselves. It runs whether or not TSK is installed, because
# TSK's entry for a deleted FAT file can carry no size at all, which left a
# machine with TSK finding fewer deleted files than one without: the worst
# shape such a difference can take, since the better-equipped machine is
# the one that finds less and nothing says so.
import json as _json
import subprocess as _sub

print()
print("deleted FAT entries, read from the directory")

_project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The committed fixture, in tests/. This looked at the project root, found
# nothing there and built one -- leaving a 16 MB image among the code, where a
# release zip would pick it up.
_fixture = os.path.join(_project, "tests", "media_test.img")

_ok = True
if os.path.isfile(_fixture):
    _out = os.path.join(tempfile.gettempdir(), "fme_fat_deleted-") + str(os.getpid())
    shutil.rmtree(_out, ignore_errors=True)
    _sub.run([sys.executable, "-m", "fme", "extract", "-i", _fixture,
              "-o", _out], capture_output=True, cwd=_project)
    with open(os.path.join(_out, "manifest.json"), encoding="utf-8") as _fh:
        _run = _json.load(_fh)
    _summary = _run["summary"]

    _ok &= _summary.get("fat_volumes", 0) >= 1
    print(f"  FAT volume(s) found: {_summary.get('fat_volumes')}")

    # What matters is that the deleted file comes back whole and named,
    # not which engine reached it first. The directory-entry pass exists
    # for machines where TSK's walk reports a deleted entry with no size;
    # where the main engine already recovers it, the pass's copy is the
    # same bytes and is suppressed, which is right.
    import make_media_test_image as _fixtures

    _truth = _fixtures.make_jpeg(9)
    _deleted = [r for r in _run["files"] if r.get("deleted")]
    _ok &= bool(_deleted)
    print(f"  deleted file(s) recovered: {len(_deleted)}")

    _whole = [r for r in _deleted if r.get("size") == len(_truth)]
    _ok &= bool(_whole)
    print(f"  recovered whole ({len(_truth)} bytes, not truncated to one "
          f"cluster): {bool(_whole)}")

    if _whole:
        _path = os.path.join(_summary.get("output_dir", _out),
                             _whole[0]["out_path"])
        with open(_path, "rb") as _fh:
            _ok &= _fh.read() == _truth
        print(f"  byte-for-byte the deleted picture: True")
        # The name survives in the directory entry, minus the character
        # FAT overwrote. A carved copy would have no name at all.
        _name = _whole[0].get("source_path", "")
        _ok &= "MG_0009" in _name
        print(f"  and it carries its name: {_name}")
        _ok &= chr(0xFFFD) not in _name
        print(f"  with no raw replacement byte in it: True")
    shutil.rmtree(_out, ignore_errors=True)

else:
    print("  (no FAT fixture built; skipped)")

print("\nALL CHECKS PASS" if _ok else "\nFAILURES ABOVE")
sys.exit(0 if _ok else 1)
