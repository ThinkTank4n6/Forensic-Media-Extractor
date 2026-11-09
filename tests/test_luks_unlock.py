"""A LUKS container opens with its passphrase (v2026.11.318).

The vendored reader has opened LUKS1 and LUKS2 since it was vendored, and
nothing asked it to: a LUKS volume whose passphrase the examiner held was
reported locked, and nothing on it was read. These are real containers
cryptsetup wrote (tests/fixtures/luks*.intended), and every file of the ext4
inside is checked against the SHA-256 sha256sum recorded from the mounted
volume (tests/fixtures/luks-fixture.sha256, from the other tool's fixtures).
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

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
logging.disable(logging.CRITICAL)
try:
    import Crypto.Cipher.AES  # noqa: F401
    STANDIN = False
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "support", "pycryptodome_standin"))
    STANDIN = True

from fme.engines import media_core, unlock, vendor_fs  # noqa: E402
from fme.exports import report  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_luks-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
PASS = "correct horse battery staple"
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def unpack(name):
    path = os.path.join(ROOT, name + ".img")
    with gzip.open(os.path.join(HERE, "fixtures", name + ".img.gz")) as src, \
            open(path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return path


def run(path, password):
    with contextlib.redirect_stdout(io.StringIO()):
        return media_core.run_extraction(
            path, os.path.join(ROOT, "case_" + os.path.basename(path) + password[:3]),
            mode="both", workers=1, metadata=False, kml=False,
            credentials=unlock.Credentials(passwords=[password]))


expected = {}
with open(os.path.join(HERE, "fixtures", "luks-fixture.sha256"), encoding="utf-8") as fh:
    for line in fh:
        if line.strip():
            digest, path = line.split(None, 1)
            expected[path.strip()] = digest

print(f"(decryption by {'the test stand-in' if STANDIN else 'pycryptodome'})")
print("\nLUKS1, aes-cbc-essiv")
image = unpack("luks1-cbc")
summary = run(image, PASS)
opened = [u for u in summary.get("unlocked_volumes") or [] if u.get("opened")]
check("opens with its passphrase", opened and opened[0]["kind"] == "LUKS1", opened)
check("and is no longer reported locked", not summary.get("encrypted_volumes"),
      summary.get("encrypted_volumes"))
check("the ext4 inside is walked",
      any("EXT4" in v.get("fs", "") for v in summary.get("volumes") or []),
      [v.get("fs") for v in summary.get("volumes") or []])
got = {}


def take(record):
    digest = hashlib.sha256()
    for piece in record["read"]():
        digest.update(piece)
    got[record["path"].lstrip("/")] = digest.hexdigest()


vendor_fs.walk(image, take, only_extra=False,
               credentials=unlock.Credentials(passwords=[PASS]))
check("every file reads to the hash recorded from the mounted volume",
      all(got.get(p) == d for p, d in expected.items()), f"{len(expected)} files")
summary = run(image, "not it")
check("a wrong passphrase leaves it locked, and the report says so",
      summary.get("encrypted_volumes")
      and any("LUKS1-encrypted and was locked" in line
              for line in report.coverage_limits({"summary": summary})))

print("\nLUKS2, Argon2id key slot")
summary = run(unpack("luks2-argon2id"), PASS)
state = (summary.get("unlocked_volumes") or summary.get("unlock_refused") or [{}])[0]
try:
    import argon2  # noqa: F401
    have_argon2 = True
except ImportError:
    have_argon2 = False
if have_argon2:
    check("opens with its passphrase", state.get("opened"), state)
else:
    check("without argon2-cffi installed, it is refused and the reason given",
          not state.get("opened") and "argon2" in (state.get("why") or ""), state)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
