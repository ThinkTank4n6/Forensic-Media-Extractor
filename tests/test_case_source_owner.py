"""A contributor cannot regrade another examiner's case, and every regrade is kept.

The store took any case name a contributor sent as "source" and overwrote
the category of every digest that case held -- so an account with the
contribute role could turn another examiner's CAM gradings Non-Pertinent,
and the only record was "12 hash(es) as DC24-118". A case source now has
an owner, a contribution to someone else's case is refused (an
administrator excepted), and each changed grade is written, old and new,
to the store's grade_changes and to the server's access log.

The admin Sets page put a source's name inside an inline onsubmit
confirm('...'); html.escape's &#x27; is decoded before the script runs, so a
source name could run script in an administrator's session. It is now a
data attribute read by a same-origin script under a Content Security
Policy. Removing a set that is already gone said 500; it says why.
"""
import logging
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.review import hashserver, hashstore  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "case_source_owner-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(what, passed):
    global ok
    ok &= bool(passed)
    print(f"  {what}: {bool(passed)}")


# ---------------------------------------------------------------- migration
print("a store from before owners were kept")
old_path = os.path.join(ROOT, "old.db")
con = sqlite3.connect(old_path)
con.executescript("""
CREATE TABLE store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, path TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', hash_count INTEGER);
CREATE TABLE hashes (digest TEXT NOT NULL, algo TEXT NOT NULL,
    source_id INTEGER NOT NULL, category INTEGER, note TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '', PRIMARY KEY (digest, source_id)) WITHOUT ROWID;
INSERT INTO sources (name, kind, added_at, note) VALUES
    ('DC24-118', 'case', '2026-01-01', 'contributed by ltorres'),
    ('DC23-007', 'case', '2026-01-01', 'contributed from DC23-007'),
    ('NSRL', 'nsrl', '2026-01-01', '');
""")
con.commit()
con.close()
migrated = hashstore.HashStore(old_path)
owners = {s["name"]: s["owner"] for s in migrated.sources()}
migrated.close()
check("a server-made case belongs to the person its note names",
      owners["DC24-118"] == "ltorres")
check("a case made without an account is left to an administrator",
      owners["DC23-007"] == "")
check("an imported set has no owner", owners["NSRL"] is None)

# ---------------------------------------------------------------- the store
print("\ncontributing to a case that is not yours, in the store")
store_path = os.path.join(ROOT, "shared.db")
store = hashstore.HashStore(store_path)
cam = [{"md5": f"{i:032x}", "category": 1, "label": "CAM"} for i in range(3)]
store.contribute_rows(cam, "DC24-118", contributor="ltorres")
try:
    store.contribute_rows(
        [{"md5": f"{i:032x}", "category": 5, "label": "Non-Pertinent"}
         for i in range(3)], "DC24-118", contributor="mallory")
    refused = False
except hashstore.NotYourSource:
    refused = True
still = {m["category"] for m in store.lookup([f"{i:032x}" for i in range(3)])}
check("another contributor is refused", refused)
check("and the grades stay CAM", still == {1})
check("the refusal left no write lock behind",
      not store._con.in_transaction)

changes = []
store.contribute_rows([{"md5": f"{0:032x}", "category": 2, "label": "Age Difficult"}],
                      "DC24-118", contributor="ltorres", changes=changes)
check("the owner may regrade their own case", len(changes) == 1)
check("the change says old and new",
      changes and changes[0]["old_category"] == 1
      and changes[0]["new_category"] == 2 and changes[0]["by"] == "ltorres")
kept = store.grade_changes()
check("and the store keeps it in grade_changes",
      kept and kept[0]["old_label"] == "CAM" and kept[0]["new_label"] == "Age Difficult")

store.contribute_rows([{"md5": f"{1:032x}", "category": 4, "label": "Other"}],
                      "DC24-118", contributor="boss", admin=True)
check("an administrator may add to anyone's case",
      store.lookup([f"{1:032x}"])[0]["category"] == 4)
store.close()

# ---------------------------------------------------------------- the server
print("\nthe same through the server")
app = hashserver.build_app(store_path, os.path.join(ROOT, "users.db"))
accounts = app.config["ACCOUNTS"]
ltorres = accounts.add("ltorres", "contribute")
mallory = accounts.add("mallory", "contribute")
admin = accounts.add("Admin", "admin")
client = app.test_client()


def post(route, token, payload):
    return client.post(route, json=payload,
                       headers={"Authorization": f"Bearer {token}"})


answer = post("/api/v1/contribute", mallory,
              {"source": "DC24-118",
               "rows": [{"md5": f"{2:032x}", "category": 5, "label": "NP"}]})
check("naming another examiner's case is refused with 403",
      answer.status_code == 403)
check("and the store still says CAM",
      app.config["STORE"].lookup([f"{2:032x}"])[0]["category"] == 1)

answer = post("/api/v1/contribute", ltorres,
              {"source": "DC24-118",
               "rows": [{"md5": f"{2:032x}", "category": 5, "label": "NP"}]})
check("the owner's regrade is accepted",
      answer.status_code == 200 and answer.get_json()["regraded"] == 1)
log = accounts.access(50)
regrades = [r for r in log if r["action"] == "regrade"]
check("the access log has the regrade, old and new",
      regrades and "CAM (1) -> NP (5)" in regrades[0]["detail"]
      and f"{2:032x}" in regrades[0]["detail"])
check("and the refusal", any(r["action"] == "contribute refused" and
                             r["who"] == "mallory" for r in log))

answer = post("/api/v1/contribute", mallory,
              {"source": "DC25-001",
               "rows": [{"md5": f"{9:032x}", "category": 3, "label": "x"}]})
check("a contributor's own new case is theirs", answer.status_code == 200 and
      {s["name"]: s["owner"] for s in app.config["STORE"].sources()}
      ["DC25-001"] == "mallory")

# ---------------------------------------------------------------- admin page
print("\nthe admin Sets page")
hostile = "x');document.title='pwned';('"
post("/api/v1/contribute", mallory,
     {"source": hostile, "rows": [{"md5": f"{8:032x}", "category": 3,
                                   "label": "x"}]})
client.post("/admin", data={"token": admin}, headers={"Origin": "http://localhost"})
page = client.get("/admin/sources")
html = page.get_data(as_text=True)
check("the page has no inline event handler", "onsubmit" not in html
      and "onclick" not in html)
check("the name is in a data attribute, escaped",
      "data-confirm='Remove x&#x27;);document.title" in html)
csp = page.headers.get("Content-Security-Policy", "")
check("the page is served with a policy allowing only its own script",
      "script-src 'self'" in csp and "unsafe-inline" not in
      csp.split("script-src", 1)[1].split(";")[0])
script = client.get("/admin/admin.js")
check("that script is served", script.status_code == 200
      and "data-confirm" in script.get_data(as_text=True))

gone = client.post("/admin/sources", data={"action": "remove", "name": "no such set"},
                   headers={"Origin": "http://localhost"})
check("removing a set that is not there says so instead of a 500",
      gone.status_code == 200 and "No source named" in gone.get_data(as_text=True))

app.config["STORE"].close()
accounts.close()
shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
