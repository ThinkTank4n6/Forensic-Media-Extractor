"""The carve looks inside carved files even without the database reader.

_scan_carved ran only when fme.engines.db_extract had imported, though
looking inside a carved file needs nothing from it: an install where that
module failed to load found no embedded media, and said nothing.
"""
import collections
import contextlib
import io
import logging
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures
from fme.engines import media_core
import _engine  # noqa: E402 -- replaces a name in every engine module

root = tempfile.mkdtemp(prefix="fme_no_db-")
inner, outer = fixtures.make_jpeg(3), fixtures.make_jpeg(12)
photo = outer[:2] + b"\xff\xe1" + struct.pack(">H", len(inner) + 2) + inner + outer[2:]
image = os.path.join(root, "e.dd")
blob = bytearray(4 << 20)
blob[1 << 20:(1 << 20) + len(photo)] = photo
with open(image, "wb") as fh:
    fh.write(bytes(blob))
_engine.replace("db_extract", None)          # as on an install where it did not load
reader = media_core.open_image(image)
out = media_core.OutputManager(os.path.join(root, "out"), metadata=False)
args = types.SimpleNamespace(image=image, chunk_size=1 << 20, align=1, max_carve_size=1 << 26,
                             signatures=media_core.SIGNATURES, resume_from=0, scan_inside=True,
                             progress=False)
media_core.run_carve_engine(reader, out, args, collections.defaultdict(int))
out.close({})
reader.close()
found = sorted(r.engine for r in out.records)
ok = found == ["carve", "embedded"]
print(f"the carved photo and the picture inside it, with no database reader: {ok} {found}")
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
