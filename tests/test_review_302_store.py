"""Shared hash store defects found by the review of v2026.11.302.

- a contributor could add hashes to an imported set (NSRL, Project VIC);
- a regraded file contributed again kept its old grading, reported "added";
- a failed import left a partial set that looked complete;
- /admin showed every person's activity to anyone signed in;
- the account database was read outside its lock;
- request bodies had no size limit.
"""
import _sandbox  # noqa: F401
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fme.review import hashserver, hashstore  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "review302_store-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}: {'ok' if good else 'WRONG'} {detail}".rstrip())


nsrl = os.path.join(ROOT, "nsrl.txt")
with open(nsrl, "w") as fh:
    fh.write("a" * 32 + "\n")
app = hashserver.build_app(os.path.join(ROOT, "s.db"),
                           os.path.join(ROOT, "users.db"))
store, accounts = app.config["STORE"], app.config["ACCOUNTS"]
store.import_set(nsrl, kind="custom", name="NSRL")
contrib = accounts.add("cara", "contribute")
reader = accounts.add("rudy", "read")
admin = accounts.add("ada", "admin")
client = app.test_client()


def post(route, body, token):
    return client.post(route, json=body,
                       headers={"Authorization": "Bearer " + token})


r = post("/api/v1/contribute", {"source": "NSRL", "rows": [
    {"md5": "b" * 32, "category": 1, "label": "CAM"}]}, contrib)
check("a contribution into an imported set is refused", r.status_code == 400
      and not store.lookup(["b" * 32]), str(r.status_code))

row = {"md5": "c" * 32, "category": 0, "label": "Non-Pertinent"}
post("/api/v1/contribute", {"source": "DC26-1", "rows": [row]}, contrib)
r = post("/api/v1/contribute", {"source": "DC26-1", "rows": [
    {**row, "category": 1, "label": "CAM"}]}, contrib)
now = store.lookup(["c" * 32])
check("a regraded file takes its new grading",
      [(m["category"], m["note"]) for m in now] == [(1, "CAM")]
      and r.get_json()["added"] == 1, str(now))
r = post("/api/v1/contribute", {"source": "DC26-1", "rows": [
    {**row, "category": 1, "label": "CAM"}]}, contrib)
check("sending the same grading again reports nothing added",
      r.get_json()["added"] == 0)

bad = os.path.join(ROOT, "bad.csv")
with open(bad, "w") as fh:
    fh.write("md5,note\n")
    for i in range(hashstore.BATCH + 10):
        fh.write(f"{i:032x},x\n")
    fh.write("f" * 32 + ',"' + "x" * 200_000 + '"\n')
try:
    store.import_set(bad, kind="custom", name="Broken")
    raised = False
except Exception:  # noqa: BLE001
    raised = True
names = [s["name"] for s in store.sources()]
check("a failed import leaves no partial set", raised and "Broken" not in names,
      str(names))
r = post("/api/v1/import", {"path": bad, "name": "Broken"}, admin)
check("and over the API it is a 400, not a 500", r.status_code == 400,
      str(r.status_code))
r = post("/api/v1/remove", {"source": "no such set"}, admin)
check("removing a set that is not there is a 400", r.status_code == 400,
      str(r.status_code))

page = app.test_client()
page.post("/admin", data={"token": reader})
html = page.get("/admin").get_data(as_text=True)
check("a reader's overview does not list other people",
      "<td>cara</td>" not in html and "<td>ada</td>" not in html)

errors = []


def hammer(fn):
    try:
        for _ in range(200):
            fn()
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)


threads = [threading.Thread(target=hammer, args=(f,)) for f in (
    lambda: accounts.record("x", "lookup", "1 digest(s)", "", version="1"),
    lambda: accounts.access(50), accounts.summary, accounts.client_versions)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("account reads alongside writes raise nothing", not errors,
      repr(errors[:1]))

capped = app.config.get("MAX_CONTENT_LENGTH") == hashserver.MAX_BODY
app.config["MAX_CONTENT_LENGTH"] = 1024      # the same check, without 128 MiB
r = client.post("/api/v1/lookup", data=b"x" * 2048,
                headers={"Authorization": "Bearer " + reader,
                         "Content-Type": "application/json"})
app.config["MAX_CONTENT_LENGTH"] = hashserver.MAX_BODY
check("an oversized request body is refused", capped and r.status_code == 413,
      str(r.status_code))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASSED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
