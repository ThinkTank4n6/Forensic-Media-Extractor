"""Real encrypted volumes, made by the tools that make them, are named.

Until v2026.11.301 the encrypted-volume checks were tested only against the
signatures they look for (docs/VALIDATION.md 5.13), never a real volume. These
were written by Windows BitLocker and by cryptsetup, and come with what they
hold recorded when they were made: tests/fixtures/*.intended. The FileVault
volume has a test of its own (test_filevault_locked.py), because it is found
another way.
"""
import gzip
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
from fme.exports import report  # noqa: E402

ok = True
root = os.path.join(tempfile.gettempdir(), "fme_real_encrypted-") + str(os.getpid())
shutil.rmtree(root, ignore_errors=True)
os.makedirs(root)

for name, kind in (("bitlocker-xts128", "BitLocker"), ("luks1-cbc", "LUKS1"),
                   ("luks2-argon2id", "LUKS2")):
    image = os.path.join(root, name + ".img")
    with gzip.open(os.path.join(HERE, "fixtures", name + ".img.gz")) as src, \
            open(image, "wb") as dst:
        shutil.copyfileobj(src, dst)
    summary = media_core.run_extraction(image, os.path.join(root, name), workers=1)
    found = summary.get("encrypted_volumes") or []
    limits = report.coverage_limits({"summary": summary})
    right = ([v.get("kind") for v in found] == [kind]
             and summary.get("total_files") == 0
             and any(f"{kind}-encrypted and was locked" in line for line in limits))
    ok &= right
    print(f"{name}: named {[v.get('kind') for v in found]}, {summary.get('total_files')} "
          f"files, the report says it was locked: {right}")

shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
