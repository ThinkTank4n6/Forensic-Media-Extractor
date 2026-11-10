"""Keys reach the acquisition, logical evidence is read as files, and a file
the reader refuses is counted -- three defects real fixtures found
(v2026.11.315).

1. A run took the examiner's passwords and key files and never handed
   them to the acquisition reader: VendorImageReader had the parameters
   and nothing passed them. An encrypted AFF, an AFF sealed to a
   certificate or an AD-encrypted AD1 was refused with the right key.
2. A logical acquisition (L01, Lx01, AD1) was carved as if it were a disk.
   Its files came back -- where they came back -- with no name, folder or
   time, and nothing checked them against the MD5 the acquisition tool
   recorded. Its entries are now read as files.
3. A file the vendored reader refuses to decode (an APFS file compressed
   with LZBITMAP, which Apple has not published) was dropped: not stored,
   not counted, in no report.

The keys and passwords in tests/fixtures were made for these fixtures and
open nothing else. Without pycryptodome the decryption runs on the test
stand-in in tests/support (never on the program's path), and says so.
"""
import contextlib
import gzip
import hashlib
import io
import logging
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
try:
    import Crypto.Cipher.DES3  # noqa: F401
    import Crypto.PublicKey.RSA  # noqa: F401
    STANDIN = False
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "support", "pycryptodome_standin"))
    for name in [m for m in sys.modules if m == "Crypto" or m.startswith("Crypto.")]:
        del sys.modules[name]
    STANDIN = True

with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402
from fme.engines import media_core, unlock, vendor_fs  # noqa: E402
from fme.exports import report  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
ROOT = os.path.join(tempfile.gettempdir(), "fme_acq_keys-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def run(path, credentials=None, mode="both"):
    out = os.path.join(ROOT, "case")
    shutil.rmtree(out, ignore_errors=True)
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.run_extraction(path, out, mode=mode, workers=1,
                                         metadata=False, kml=False,
                                         credentials=credentials)


def refused(path, credentials):
    try:
        run(path, credentials, mode="carve")
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return ""


print(f"(decryption by {'the test stand-in' if STANDIN else 'pycryptodome'})")
print("\nan AFF encrypted with a passphrase")
aff = os.path.join(FIX, "aff-enc-pass.aff")
summary = run(aff, unlock.Credentials(passwords=["ewfprobe-aff-password"]), "carve")
check("opens with the password the run was given, and its six pictures are carved",
      summary["total_files"] == 6, summary["total_files"])
# In a child interpreter: the carve's workers are spawned, and a spawned
# worker would re-run this script from the top if it were the main module.
import subprocess  # noqa: E402
env = dict(os.environ)
if STANDIN:
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(HERE, "support", "pycryptodome_standin"), PROJECT,
         env.get("PYTHONPATH", "")])
code = (f"import io, contextlib, logging, sys; sys.path.insert(0, {PROJECT!r}); "
        f"logging.disable(logging.CRITICAL)\n"
        f"from fme.engines import media_core, unlock\n"
        f"with contextlib.redirect_stdout(io.StringIO()):\n"
        f"    s = media_core.run_extraction({aff!r}, {os.path.join(ROOT, 'parallel')!r}, "
        f"mode='carve', workers=2, metadata=False, kml=False, "
        f"credentials=unlock.Credentials(passwords=['ewfprobe-aff-password']))\n"
        f"print(s['total_files'], s.get('carve_worker_deaths') or 0)\n")
child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=600)
answer = (child.stdout.strip().splitlines() or [""])[-1]
check("the parallel carve's workers open it with the same password",
      answer == "6 0", answer or child.stderr[-200:])
said = refused(aff, unlock.Credentials(passwords=["not it"]))
check("a wrong password is refused, and says so", "not its passphrase" in said, said[:80])

print("\nan AFF sealed to a certificate")
sealed = os.path.join(FIX, "aff-enc-inplace-cert.aff")
summary = run(sealed, unlock.Credentials(
    key_files=[os.path.join(FIX, "aff-enc-test-key.pem")]), "carve")
check("opens with the certificate's private key", summary["total_files"] == 6,
      summary["total_files"])
said = refused(sealed, unlock.Credentials(
    key_files=[os.path.join(FIX, "ad-cert-test-key-2048.pem")]))
check("another key is refused", "opens none" in said, said[:80])
check("a BitLocker startup key is not offered as a private key",
      unlock.private_keys(unlock.Credentials(
          key_files=[os.path.join(FIX, "bitlocker-xts128.BEK"),
                     os.path.join(FIX, "aff-enc-test-key.pem")]))
      == [os.path.join(FIX, "aff-enc-test-key.pem")])

print("\nan AD1 encrypted to a certificate (FTK Imager 4.7)")
ad1 = os.path.join(FIX, "ftk-ad-cert-ad1.ad1")
key = unlock.Credentials(key_files=[os.path.join(FIX, "ad-cert-test-key-2048.pem")])
summary = run(ad1, key)
check("opens, and the run reads its three entries as files",
      summary.get("logical_entries") == 3, summary.get("logical_entries"))
reader = media_core.open_image(ad1, key)
image = reader._image
matched = 0
for entry in image.logical_entries:
    with image.open_entry(entry) as fh:
        matched += hashlib.md5(fh.read()).hexdigest() == entry.md5
reader.close()
check("each read to the MD5 FTK Imager recorded", matched == 3, matched)

print("\nlogical evidence holding a picture")
jpeg = fixtures.make_jpeg(9)
text = b"not a picture\n"
entries = [types.SimpleNamespace(path="Users/kim/Pictures/IMG_0042.JPG", names=(),
                                 size=len(jpeg), item_type=0,
                                 md5=hashlib.md5(jpeg).hexdigest(),
                                 times={"modified": 1700000000, "created": 1690000000}),
           types.SimpleNamespace(path="Users/kim/notes.txt", names=(), size=len(text),
                                 item_type=0, md5=hashlib.md5(text).hexdigest(), times={}),
           types.SimpleNamespace(path="Users/kim/Pictures/copy.jpg", names=(),
                                 size=len(jpeg), item_type=0, md5="0" * 32, times={})]
content = {id(entries[0]): jpeg, id(entries[1]): text, id(entries[2]): jpeg[:-1] + b"\x00"}
logical = types.SimpleNamespace(
    format="L01", logical_entries=entries,
    open_entry=lambda e: io.BytesIO(content[id(e)]))
out = media_core.OutputManager(os.path.join(ROOT, "logical"), metadata=False)
stats = {"errors": 0}
media_core.run_logical_entries(types.SimpleNamespace(_image=logical), out,
                               types.SimpleNamespace(), stats, media_core.SIGNATURES)
named = [r for r in out.records if r.source_path == "/Users/kim/Pictures/IMG_0042.JPG"]
check("the picture is stored under its own path, as logical evidence",
      len(named) == 1 and named[0].engine == "logical", [r.source_path for r in out.records])
check("with the time the acquisition recorded",
      bool(named) and named[0].mtime.startswith("2023-11-14"), named[0].mtime if named else "")
check("the text file is listed and not stored", stats.get("logical_entries") == 3
      and len(out.records) == 2, len(out.records))
check("its MD5 is checked: one agrees, one does not",
      stats.get("logical_md5_verified") == 1 and stats.get("logical_md5_mismatch") == 1,
      (stats.get("logical_md5_verified"), stats.get("logical_md5_mismatch")))
check("and the one that does not is a limit of the report",
      any("do not match the MD5" in x for x in report.coverage_limits({"summary": stats})))
bad = [r for r in out.records if r.source_path == "/Users/kim/Pictures/copy.jpg"]
check("and its own row says it does not match (v2026.11.318)",
      bad and "DOES NOT MATCH" in bad[0].note, bad[0].note if bad else "")
out.close({})

print("\nlogical entries that read short, fail, or are small (v2026.11.318)")
import zlib  # noqa: E402
small = fixtures.make_jpeg(1)[:2700]


class _Damaged(io.BytesIO):
    def read(self, n=-1):
        if self.tell() >= 4096:
            raise zlib.error("Error -3 while decompressing data: invalid block type")
        return super().read(min(n, 4096) if n and n > 0 else 4096)


big = fixtures.make_jpeg(20)
odd = [types.SimpleNamespace(path="a/short.jpg", names=(), size=len(big) * 2, item_type=0,
                             md5=hashlib.md5(big).hexdigest(), times={}),
       types.SimpleNamespace(path="a/damaged.jpg", names=(), size=len(big), item_type=0,
                             md5=hashlib.md5(big).hexdigest(), times={}),
       types.SimpleNamespace(path="a/after.jpg", names=(), size=len(jpeg), item_type=0,
                             md5=hashlib.md5(jpeg).hexdigest(), times={}),
       types.SimpleNamespace(path="a/tiny.jpg", names=(), size=len(small), item_type=0,
                             md5=hashlib.md5(small).hexdigest(), times={})]
opener = {"short.jpg": lambda: io.BytesIO(big), "damaged.jpg": lambda: _Damaged(big),
          "after.jpg": lambda: io.BytesIO(jpeg), "tiny.jpg": lambda: io.BytesIO(small)}
logical2 = types.SimpleNamespace(format="L01", logical_entries=odd,
                                 open_entry=lambda e: opener[e.path.split("/")[-1]]())
where = os.path.join(ROOT, "logical2")
out = media_core.OutputManager(where, metadata=False)
stats = {"errors": 0}
media_core.run_logical_entries(types.SimpleNamespace(_image=logical2), out,
                               types.SimpleNamespace(), stats, media_core.SIGNATURES)
rows = {r.source_path.rsplit("/", 1)[-1]: r for r in out.records}
check("an entry read short is stored, marked short, its MD5 not claimed",
      rows.get("short.jpg") and rows["short.jpg"].bounded == "short"
      and "read short" in rows["short.jpg"].note, rows.get("short.jpg") and rows["short.jpg"].note)
check("a damaged entry is kept as far as it read, with the decoder's reason",
      rows.get("damaged.jpg") and rows["damaged.jpg"].bounded == "short"
      and "invalid block type" in rows["damaged.jpg"].note)
check("and the entries after it are still read", "after.jpg" in rows, sorted(rows))
check("a small entry the acquisition sized is kept, not dropped as noise",
      "tiny.jpg" in rows, sorted(rows))
check("the short ones are a limit of the report",
      any("read only in part" in x for x in report.coverage_limits({"summary": stats})))
out.close({})
kept = len(rows)
out = media_core.OutputManager(where, metadata=False, resume=True)
media_core.run_logical_entries(types.SimpleNamespace(_image=logical2), out,
                               types.SimpleNamespace(), stats, media_core.SIGNATURES)
check("a resume does not write them down again as duplicates of themselves",
      len(out.records) == kept and not out.duplicates,
      (len(out.records), len(out.duplicates)))
out.close({})

print("\nan APFS volume macOS wrote, with every decmpfs method")
apfs = os.path.join(ROOT, "decmpfs.img")
with gzip.open(os.path.join(FIX, "decmpfs-apfs.img.gz"), "rb") as fin, \
        open(apfs, "wb") as fout:
    shutil.copyfileobj(fin, fout)
expected = {}
with open(os.path.join(FIX, "decmpfs-apfs.sha256"), encoding="utf-8") as fh:
    for line in fh:
        if line.strip() and not line.startswith("#"):
            digest, method, path = line.split()
            expected[path] = (digest, int(method))
got = {}


def take(record):
    data = hashlib.sha256()
    for piece in record["read"]():
        data.update(piece)
    got[record["path"].split("DECMPFS/", 1)[-1]] = data.hexdigest()


vendor_fs.walk(apfs, take, only_extra=False)
readable = {p: d for p, (d, m) in expected.items() if m not in (13, 14)}
check("every file in a published method reads to the hash macOS read back",
      all(got.get(p) == d for p, d in readable.items()), f"{len(readable)} files")
summary = run(apfs, mode="fs")
check("the two in LZBITMAP, which is not published, are counted",
      summary.get("vendor_unread_files") == 2, summary.get("vendor_unread_files"))
limits = " ".join(report.coverage_limits({"summary": summary}))
check("and the report names them and why", "LZBITMAP" in limits and "method13" in limits)

print("\nan APFS volume encrypted in place, opened with its password")
conv = os.path.join(ROOT, "converted.sparseimage")
with gzip.open(os.path.join(FIX, "apfs-converted.sparseimage.gz"), "rb") as fin, \
        open(conv, "wb") as fout:
    shutil.copyfileobj(fin, fout)
got.clear()
vendor_fs.walk(conv, take, only_extra=False,
               credentials=unlock.Credentials(passwords=["qnxprobe-apfs-convert"]))
expected = {}
with open(os.path.join(FIX, "apfs-converted.sha256"), encoding="utf-8") as fh:
    for line in fh:
        if line.strip() and not line.startswith("#"):
            digest, path = line.split()
            expected[path] = digest
check("its files read to the hashes recorded when it was made",
      all(any(g.endswith(p) and got[g] == d for g in got) for p, d in expected.items()),
      f"{len(expected)} files")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
