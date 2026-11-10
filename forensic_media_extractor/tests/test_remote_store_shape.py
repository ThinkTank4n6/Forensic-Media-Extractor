"""The server client takes the local store's arguments and answers in its shape.

RemoteHashStore.import_set was (path, name, kind) while HashStore's is
(path, kind, name) and the web route passes kind second -- so the server
was sent the kind as the set's name and no kind. Its contribute answered
{source, files, stored} where the local store answers {source, files,
hashes, skipped_uncategorized}, so the page read different keys depending
on which store was open.
"""
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.review import casedb, hashserver, remotestore  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "remote_store_shape-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(what, passed):
    global ok
    ok &= bool(passed)
    print(f"  {what}: {bool(passed)}")


app = hashserver.build_app(os.path.join(ROOT, "s.db"), os.path.join(ROOT, "u.db"))
token = app.config["ACCOUNTS"].add("ex", "admin")
client = app.test_client()
sent = []


class Remote(remotestore.RemoteHashStore):
    def _call(self, route, payload=None, method="GET"):
        sent.append((route, payload))
        return client.open(route, method=method, json=payload,
                           headers={"Authorization": "Bearer " + token}).get_json()


remote = Remote("http://x", token)

print("import, in the local store's argument order")
setf = os.path.join(ROOT, "set.txt")
with open(setf, "w") as fh:
    fh.write("d41d8cd98f00b204e9800998ecf8427f\n" + "a" * 32 + "\n")
result = remote.import_set(setf, "custom", "KnownSet")
check("the server is sent kind and name in their own fields",
      sent[-1][1] == {"path": setf, "kind": "custom", "name": "KnownSet"})
listed = {s["name"]: s["kind"] for s in app.config["STORE"].sources()}
check("and the set lands under its name, as that kind",
      listed.get("KnownSet") == "custom" and result.get("ok"))

print("\ncontribute answers in the local store's shape")
case_dir = os.path.join(ROOT, "case")
os.makedirs(case_dir)
files = [{"md5": f"{i:032x}", "sha1": "", "sha256": f"{i:064x}"} for i in range(1, 4)]
with open(os.path.join(case_dir, "manifest.json"), "w") as fh:
    json.dump({"files": files}, fh)
case = casedb.CaseDB(os.path.join(case_dir, "case.db"))
case.set_category([f"{1:064x}", f"{2:064x}"], 1)
case.close()
answer = remote.contribute(case_dir, "DC26-1")
local_keys = {"source", "files", "hashes", "skipped_uncategorized"}
check("it has every key the local store returns",
      local_keys <= set(answer))
check("the counts are right", answer["files"] == 2 and answer["hashes"] == 4
      and answer["skipped_uncategorized"] == 1 and answer["stored"] == 4)

app.config["STORE"].close()
app.config["ACCOUNTS"].close()
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
