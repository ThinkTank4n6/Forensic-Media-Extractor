"""A scan through the shared server counts every matching file, not one per set.

The server's /lookup answered one match per hash set across the whole
batch, and the client maps matches back to files by digest -- so five files
of one set came back as one matched and four unmatched (review of
v2026.11.302). The remote scan must agree with the local one.
"""
import _sandbox  # noqa: F401
import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fme.review import hashserver, remotestore  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "remote_scan_batch-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True

digs = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(5)]
setf = os.path.join(ROOT, "set.txt")
with open(setf, "w") as fh:
    fh.write("\n".join(digs) + "\n")
app = hashserver.build_app(os.path.join(ROOT, "s.db"))
app.config["STORE"].import_set(setf, kind="custom", name="KnownCAM")
tok = app.config["ACCOUNTS"].add("ex", "read")
client = app.test_client()


class Remote(remotestore.RemoteHashStore):
    def _call(self, route, payload=None, method="GET"):
        return client.open(route, method=method, json=payload,
                           headers={"Authorization": "Bearer " + tok}).get_json()


man = os.path.join(ROOT, "manifest.json")
with open(man, "w") as fh:
    json.dump({"files": [{"sha256": d} for d in digs]}, fh)
remote = Remote("http://x", tok).scan_manifest(man)
local = app.config["STORE"].scan_manifest(man)
print(f"remote: matched {remote['matched']} of {remote['checked']}")
print(f"local : matched {local['matched']} of {local['checked']}")
ok &= remote["matched"] == local["matched"] == 5
ok &= remote["by_source"] == {"KnownCAM": 5}
print(f"by source: {remote['by_source']}")
# One file's three digests still count that file once per set locally.
one = app.config["STORE"].lookup([digs[0], digs[0].upper()])
ok &= len(one) == 1
print(f"one file, one set, one match: {len(one) == 1}")
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASSED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
