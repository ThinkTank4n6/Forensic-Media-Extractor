"""An E01 libewf itself wrote, read by libewf and by the vendored reader.

The project's own E01 fixture (known.E01) is packed by this project, and
libewf does not read it -- so until now the libewf path was "untested by
anyone who wrote it" (docs/EXAMINER_TASKS.md). libewf-acquired.E01 was made
by libewf's own ewfacquire 20240506 (EnCase 6 format, best compression) from
fat-deleted.img, whose contents fat-deleted.intended records from before
anything was deleted. Both readers must return every recorded file, byte for
byte, and agree with each other; with libewf installed, the log must say
libewf was the reader -- not a fall-back.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
E01 = os.path.join(FIXTURES, "libewf-acquired.E01")
RAW = os.path.join(FIXTURES, "fat-deleted.img")
# What ewfacquire reported as the MD5 of the data it acquired.
ACQUIRED_MD5 = "2e8d3bbe2083eb1e17b627c540c7c916"

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_libewf_acq-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def intended():
    rows = []
    with open(os.path.join(FIXTURES, "fat-deleted.intended"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                parts = line.split()
                rows.append((parts[0], parts[2]))
    return rows


def run(no_libewf):
    out = os.path.join(ROOT, "vendored" if no_libewf else "libewf")
    env = dict(os.environ)
    env.pop("FME_NO_LIBEWF", None)
    if no_libewf:
        env["FME_NO_LIBEWF"] = "1"
    done = subprocess.run([sys.executable, "-m", "fme", "extract", "-i", E01, "-o", out],
                          cwd=PROJECT, env=env, capture_output=True, text=True,
                          errors="replace", timeout=900)
    path = os.path.join(out, "manifest.json")
    files = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            files = json.load(fh)["files"]
    return files, done.stdout + done.stderr, done.returncode


with open(RAW, "rb") as fh:
    check("the raw volume is the one ewfacquire acquired",
          hashlib.md5(fh.read()).hexdigest() == ACQUIRED_MD5)

sys.path.insert(0, PROJECT)
try:
    import pyewf  # noqa: F401
    have_libewf = True
except ImportError:
    have_libewf = False
    print("not checked: libewf-python is not installed, so only the vendored "
          "reader reads this acquisition")

want = intended()
got = {}
for label, no_libewf in ([("libewf", False)] if have_libewf else []) + [("the vendored reader", True)]:
    print(f"\nread by {label}")
    files, log, code = run(no_libewf)
    check("the run finishes", code == 0, f"exit {code}")
    digests = {f["sha256"] for f in files}
    got[label] = digests
    for digest, name in want:
        check(f"{name} comes back byte for byte", digest in digests)
    if not no_libewf:
        check("and libewf was the reader, not a fall-back",
              "read by libewf" in log and "vendored reader" not in log.split("read by libewf")[0][-400:]
              and "libewf would not read" not in log and "could not read it" not in log)

if len(got) == 2:
    a, b = got.values()
    check("libewf and the vendored reader return the same files", a == b,
          f"{len(a ^ b)} differ")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
