"""An E01 libewf opens and reads nothing from is read by the other reader.

libewf can accept an acquisition, report its size and return only zeros --
it does so for the E01 this project builds as a fixture. The engine fell
back to the vendored reader when libewf refused a file, but not when it
accepted one and read nothing: the run "read by libewf" carved an empty disk
and reported no files, exactly as a blank drive would. Seen on the lab's
machine as test_known_answer.py failing in every validation run.

libewf is stood in for here, so this runs on any machine: a pyewf that reads
zeros, one that reads the media, and one that refuses the file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
E01 = os.path.join(HERE, "fixtures", "known.E01")
ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_ewf_nothing-") + str(os.getpid())

STAND_IN = '''
import os, sys
sys.path.insert(0, os.environ["FME_ROOT"])
MODE = os.environ["STAND_IN_EWF"]
def glob(path): return [path]
class handle:
    def open(self, names):
        if MODE == "refuses": raise IOError("unsupported")
        from fme.engines.media_core import VendorImageReader
        self._real = VendorImageReader(names[0]); self._at = 0
    def get_media_size(self): return self._real.size
    def seek(self, at): self._at = at
    def read(self, n):
        data = self._real.read(self._at, n) if MODE == "reads" else bytes(n)
        self._at += len(data); return data
    def close(self): self._real.close()
'''


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


if not os.path.isfile(E01):
    print("the E01 fixture is not built (tools/make_fixtures/make_e01.py); not checked")
    sys.exit(0)
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "pyewf"))
with open(os.path.join(ROOT, "pyewf", "__init__.py"), "w", encoding="utf-8") as fh:
    fh.write(STAND_IN)


def run(mode):
    out = os.path.join(ROOT, mode)
    finished = subprocess.run(
        [sys.executable, "-m", "fme", "extract", "-i", E01, "-o", out],
        capture_output=True, text=True, errors="replace", cwd=PROJECT,
        env=dict(os.environ, PYTHONPATH=ROOT, FME_ROOT=PROJECT, STAND_IN_EWF=mode))
    log = finished.stdout + finished.stderr
    path = os.path.join(out, "manifest.json")
    files = json.load(open(path, encoding="utf-8"))["files"] if os.path.isfile(path) else []
    return len(files), log


count, log = run("zeros")
check("libewf reading only zeros: the pictures are still recovered", count == 4, f"{count} of 4")
check("and the log says the vendored reader was used, and why",
      "read only zeros" in log and "vendored reader" in log)
check("and does not claim libewf read it", "read by libewf" not in log)

count, log = run("reads")
check("libewf reading the media: it is the reader", count == 4 and "read by libewf" in log,
      f"{count} of 4")
check("and nothing is said about another reader", "vendored" not in log)

count, log = run("refuses")
check("libewf refusing the file: the vendored reader takes it, as before",
      count == 4 and "would not read" in log, f"{count} of 4")

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
