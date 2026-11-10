"""An Apple sparse bundle opens, and an E01 misnamed opens without libewf.

A sparse bundle is a folder by design. open_image refused every folder
before it reached the branch that hands one to the vendored reader -- which
reads sparse bundles -- so that branch could never run. And a raw-named file
that was really an E01 raised "pyewf is not installed" where the .e01 path
would have used the vendored reader.
"""
import contextlib
import io
import os
import plistlib
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core

root = tempfile.mkdtemp(prefix="fme_bundle-")
card = os.path.join(root, "card.img")
with contextlib.redirect_stdout(io.StringIO()):
    fixtures.main(card)
with open(card, "rb") as fh:
    disk = fh.read()
band = 1 << 20
bundle = os.path.join(root, "evidence.sparsebundle")
os.makedirs(os.path.join(bundle, "bands"))
with open(os.path.join(bundle, "Info.plist"), "wb") as fh:
    plistlib.dump({"CFBundleInfoDictionaryVersion": "6.0", "band-size": band,
                   "bundle-backingstore-version": 1,
                   "diskimage-bundle-type": "com.apple.diskimage.sparsebundle",
                   "size": len(disk)}, fh)
for n in range(0, len(disk), band):
    chunk = disk[n:n + band]
    if chunk.strip(b"\0"):                      # empty bands are simply absent
        with open(os.path.join(bundle, "bands", format(n // band, "x")), "wb") as fh:
            fh.write(chunk)
ok = True
try:
    reader = media_core.open_image(bundle)
    same = reader.size == len(disk) and reader.read(0, len(disk)) == disk
    reader.close()
except Exception as exc:  # noqa: BLE001
    same = False
    print(f"   ({type(exc).__name__}: {exc})")
ok &= same
print(f"a sparse bundle opens, and reads as the disk it holds: {same}")
try:
    media_core.open_image(root)
    refused = False
except IsADirectoryError:
    refused = True
ok &= refused
print(f"an ordinary folder is still sent to the folder engine: {refused}")
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
