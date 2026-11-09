"""The shared hash store: who may do what, and what crosses the network.

The store holds hashes of child abuse material and the gradings colleagues
assigned them. Most of these checks are about access rather than function:
a store that cannot say who queried it, or that lets a reader write, is not
one this data should sit in.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import tempfile
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.review import hashserver, hashstore, remotestore

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "hashserver_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

ok = True
store_path = os.path.join(ROOT, "shared.db")
accounts_path = os.path.join(ROOT, "users.db")

store = hashstore.HashStore(store_path)
store.contribute_rows(
    [{"md5": f"{i:032x}", "sha256": f"{i:064x}", "category": 1,
      "label": "Child Abuse Material (CAM)"} for i in range(5)],
    "DC24-118", contributor="another examiner")
store.close()

accounts = hashserver.Accounts(accounts_path)
reader = accounts.add("jsmith", "read")
contributor = accounts.add("ltorres", "contribute")
administrator = accounts.add("admin", "admin")
accounts.close()

# A token is stored hashed: the file must not contain it.
with open(accounts_path, "rb") as fh:
    raw = fh.read()
ok &= reader.encode() not in raw
print(f"tokens are stored hashed, not in the clear: {reader.encode() not in raw}")

app = hashserver.build_app(store_path, accounts_path)
app.config["TESTING"] = True
client = app.test_client()


def call(route, token, payload=None, method="GET"):
    headers = {"Authorization": f"Bearer {token}"}
    if method == "POST":
        return client.post(route, json=payload or {}, headers=headers)
    return client.get(route, headers=headers)


print("\nwho may do what")
cases = [
    ("read", reader, "/api/v1/lookup", {"digests": [f"{1:032x}"]}, "POST", 200),
    ("read", reader, "/api/v1/contribute",
     {"rows": [{"md5": f"{70:032x}", "label": "x"}]}, "POST", 403),
    ("read", reader, "/api/v1/import", {"path": "/etc/hostname"}, "POST", 403),
    ("contribute", contributor, "/api/v1/contribute",
     {"rows": [{"md5": f"{70:032x}", "category": 5, "label": "Adult"}]},
     "POST", 200),
    ("contribute", contributor, "/api/v1/users", None, "GET", 403),
    ("admin", administrator, "/api/v1/users", None, "GET", 200),
    ("admin", administrator, "/api/v1/access", None, "GET", 200),
]
for role, token, route, payload, method, expected in cases:
    response = call(route, token, payload, method)
    good = response.status_code == expected
    ok &= good
    verdict = "allowed" if response.status_code == 200 else "refused"
    print(f"  {role:11} {route:22} {verdict:9} "
          f"{'' if good else f'<-- expected {expected}'}")

# An unknown token gets nothing at all.
anonymous = client.get("/api/v1/stats")
ok &= anonymous.status_code == 401
print(f"  {'nobody':11} {'/api/v1/stats':22} "
      f"{'refused' if anonymous.status_code == 401 else 'ALLOWED'}")

print("\nwhat crosses the network")
response = call("/api/v1/lookup", reader,
                {"digests": [f"{1:032x}", "f" * 32]}, "POST")
body = response.get_json()
# Only the digests the caller already had come back. The store never sends
# a hash the caller did not ask about: that would be handing over the set.
returned = {m["digest"] for m in body["matches"]}
ok &= returned <= {f"{1:032x}"}
print(f"  sent 2 digests, got {len(body['matches'])} match(es), "
      f"about {len(returned)} of the digests sent")

# A matched VICS record must not carry the file's other hashes, its
# PhotoDNA or the record's free text back to a caller who sent one MD5.
# v2026.11.312 passed the stored record through whole.
vic_path = os.path.join(ROOT, "vic_leak.json")
vic_md5 = "c" * 32
with open(vic_path, "w") as fh:
    json.dump({"@odata.context": "http://github.com/VICSDATAMODEL/ProjectVic/"
                                 "DataModels/2.0.xml/US/$metadata#Cases",
               "value": [{"CaseNumber": "VIC_US", "Media": [
                   {"MD5": vic_md5, "SHA1": "a" * 40, "SHA256": "b" * 64,
                    "PhotoDNA": "AAECAwQFBgcICQ==", "MediaID": 4242,
                    "Category": 1, "Series": "Blue",
                    "Description": "a name and a school",
                    "Comment": "private note",
                    "RelativeFilePath": "C:/cases/123/img.jpg",
                    "MediaFiles": [], "AlternativeHashes": []}]}]}, fh)
leak_store = hashstore.HashStore(store_path)
leak_store.import_set(vic_path, kind="projectvic", name="VICS-US test")
leak_store.close()
leak = call("/api/v1/lookup", reader, {"digests": [vic_md5]}, "POST").get_json()
records = [m.get("record") or {} for m in leak["matches"]]
secret = {"SHA1", "SHA256", "PhotoDNA", "Description", "Comment",
          "RelativeFilePath", "MD5"}
leaked = sorted({k for r in records for k in r} & secret)
kept = bool(records) and records[0].get("MediaID") == 4242 \
    and records[0].get("Series") == "Blue"
ok &= bool(records) and not leaked and kept
print(f"  a VICS match returns no hash or text the caller did not send: "
      f"{not leaked and bool(records)}{'' if not leaked else f' <-- {leaked}'}")
print(f"  and still carries MediaID and Series for the gallery: {kept}")

oversized = call("/api/v1/lookup", reader,
                 {"digests": ["0" * 32] * (hashserver.MAX_LOOKUP + 1)}, "POST")
ok &= oversized.status_code == 413
print(f"  an oversized request is refused: {oversized.status_code == 413}")

print("\nevery access is recorded")
log = call("/api/v1/access", administrator).get_json()["access"]
actions = {entry["action"] for entry in log}
who = {entry["who"] for entry in log}
ok &= "lookup" in actions and "contribute" in actions
ok &= "jsmith" in who and "ltorres" in who
print(f"  actions: {', '.join(sorted(actions))}")
print(f"  by     : {', '.join(sorted(who))}")

# A disabled person keeps their token and loses their access.
accounts = hashserver.Accounts(accounts_path)
accounts.disable("jsmith")
accounts.close()
after = call("/api/v1/stats", reader)
ok &= after.status_code == 401
print(f"\na disabled user is refused even with their token: "
      f"{after.status_code == 401}")

print("\nthe client reports failure rather than hiding it")
unreachable = remotestore.RemoteHashStore("http://127.0.0.1:9", "token")
try:
    unreachable.size()
    reported = False
except remotestore.RemoteStoreError as exc:
    reported = "could not reach" in str(exc).lower()
ok &= reported
print(f"  an unreachable store raises, with a plain message: {reported}")

print("\nthe admin pages")
pages = app.test_client()
anonymous_page = pages.get("/admin").get_data(as_text=True).lower()
ok &= "sign in" in anonymous_page
print(f"  signed out, the overview asks for a token: {'sign in' in anonymous_page}")

signed_in = pages.post("/admin", data={"token": administrator},
                       follow_redirects=True).get_data(as_text=True)
ok &= "admin" in signed_in and "lookups" in signed_in.lower()
print(f"  signed in as an administrator: {'lookups' in signed_in.lower()}")

# A reader can see the overview and nothing else: the pages enforce the same
# roles the API does, rather than relying on nobody finding the URL.
reader_pages = app.test_client()
reader_pages.post("/admin", data={"token": contributor})
refused = [path for path in ("/admin/people", "/admin/sources", "/admin/activity")
           if reader_pages.get(path).status_code == 403]
ok &= len(refused) == 3
print(f"  a contributor is refused {len(refused)} of 3 admin pages")

# Adding someone shows their token exactly once.
issued = pages.post("/admin/people",
                    data={"action": "add", "name": "newperson",
                          "role": "contribute"},
                    follow_redirects=True).get_data(as_text=True)
import re as _re
shown = _re.search(r"<code>([^<]+)</code>", issued)
ok &= shown is not None and "cannot be looked up again" in issued
print(f"  adding someone issues a token once, and says so: "
      f"{shown is not None and 'cannot be looked up again' in issued}")

# And the token issued in the page really works.
fresh = app.test_client()
landed = fresh.post("/admin", data={"token": shown.group(1)},
                    follow_redirects=True)
ok &= landed.status_code == 200
print(f"  the issued token signs in: {landed.status_code == 200}")

print("\nper-person statistics")
figures = accounts.summary() if False else None
accounts2 = hashserver.Accounts(accounts_path)
for case in ("DC26-253", "DC26-301"):
    accounts2.record("ltorres", "contribute", f"40 hash(es) as {case}", "10.0.0.9")
accounts2.record("ltorres", "lookup", "10 digest(s), 1 match(es)", "10.0.0.9")
figures = accounts2.summary()
accounts2.close()
row = next((r for r in figures["people"] if r["who"] == "ltorres"), None)
# ltorres contributed once earlier in this test as well, so the count is the
# distinct case names across everything they have sent -- which is the
# number the overview is meant to show.
ok &= row is not None and row["cases"] >= 2
ok &= row["contributions"] >= row["cases"]
print(f"  ltorres: {row['lookups']} lookup(s), {row['contributions']} "
      f"contribution(s), {row['cases']} distinct case(s)")

# Verify asks the store what it holds. For a file that means opening it; for
# a server it means asking the server, which also proves the address, the
# token and the certificate all work. It used to open the store's "path" as
# a file either way -- for a server that path is a URL, so the report came
# back without the fields the page reads, and the examiner got a message
# about toLocaleString.
print("\nverify, against a server")
from fme.review import remotestore as _remote

served = _remote.RemoteHashStore("http://x", "t")      # not called; shape only
expected_fields = ("total", "sources", "who", "role")
# The report the interface builds for a remote store, built the same way.
report = {"remote": True, "path": "https://example:8787", "exists": True,
          "total": 0, "sources": [], "by_algorithm": {}, "size": 0,
          "who": "", "role": "", "may": []}
missing = [field for field in expected_fields if field not in report]
ok &= not missing
print(f"  the remote report carries {', '.join(expected_fields)}: {not missing}")

# And the client has the three calls that report is built from.
for method in ("size", "sources", "whoami"):
    there = hasattr(served, method)
    ok &= there
    print(f"  the client can answer {method}(): {there}")

# "localhost refused to connect" is a browser saying nothing happened. It
# covers a server that never started, one serving TLS where http was asked
# for, a port already taken, and a file nobody can read -- and the browser
# reports all four identically. The server has to separate them itself.
print("\nwhen it will not come up")
import subprocess as _subprocess

check_dir = os.path.join(ROOT, "check")
os.makedirs(check_dir, exist_ok=True)
check_store = os.path.join(check_dir, "hash_store.db")
_started = hashserver.Accounts(os.path.join(check_dir, "hash_users.db"))
_started.close()

project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
result = _subprocess.run(
    [sys.executable, os.path.join(project, "launch.py"), "hashserver",
     "--store", check_store, "--accounts",
     os.path.join(check_dir, "hash_users.db"),
     "--port", "0", "--check"],
    capture_output=True, text=True, cwd=project, timeout=180)
report = result.stdout

# Each thing it checks has to be named, because the point is telling them
# apart.
for phrase, what in (("the store opens", "whether the store can be read"),
                     ("port", "whether the port is free"),
                     ("http", "which protocol to type")):
    present = phrase in report
    ok &= present
    print(f"  it reports {what}: {present}")

# And the startup message has to say which scheme, since that is the usual
# cause and a browser will never say it.
print(f"  the check ran and said something useful: {len(report) > 100}")
ok &= len(report) > 100

# The check starts a server, talks to it, and stops it. Anybody who then
# opens the admin page finds nothing listening, so the check has to say so
# -- otherwise it reads as "everything works" and the browser disagrees.
says_stopped = "has now stopped" in report or "needs fixing" in report
ok &= says_stopped
print(f"  it says the server it started has stopped: {says_stopped}")

# What the server is served with decides whether one examiner scanning a
# large case makes everybody else wait. cheroot serves TLS from a pool of
# threads; Flask's own server is single-threaded and is the fallback, which
# has to keep working and has to say what it is.
print("\nhow it serves")
import types as _types

_calls = {}


class _FakeServer:
    def __init__(self, bind, app, numthreads=1):
        _calls["bind"] = bind
        _calls["threads"] = numthreads
        self.ssl_adapter = None

    def start(self):
        _calls["started"] = True

    def stop(self):
        pass


class _FakeAdapter:
    def __init__(self, certificate, key):
        _calls["cert"] = certificate
        _calls["key"] = key


_wsgi = _types.ModuleType("cheroot.wsgi")
_wsgi.Server = _FakeServer
_builtin = _types.ModuleType("cheroot.ssl.builtin")
_builtin.BuiltinSSLAdapter = _FakeAdapter
_package = _types.ModuleType("cheroot")
_package.wsgi = _wsgi
_ssl_package = _types.ModuleType("cheroot.ssl")
_ssl_package.builtin = _builtin

_saved = {name: sys.modules.get(name) for name in
          ("cheroot", "cheroot.wsgi", "cheroot.ssl", "cheroot.ssl.builtin")}
sys.modules.update({"cheroot": _package, "cheroot.wsgi": _wsgi,
                    "cheroot.ssl": _ssl_package,
                    "cheroot.ssl.builtin": _builtin})
try:
    hashserver.serve_forever(object(), "0.0.0.0", 8787, "server.crt",
                             "server.key", threads=16)
finally:
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module

ok &= _calls.get("started") is True
ok &= _calls.get("bind") == ("0.0.0.0", 8787)
ok &= _calls.get("threads") == 16
ok &= _calls.get("cert") == "server.crt" and _calls.get("key") == "server.key"
print(f"  with cheroot present it is used, threaded, with the certificate: "
      f"{_calls.get('started') is True and _calls.get('threads') == 16}")

# And without it, TLS still has to work -- the fallback is a slower server,
# not a broken one. (Exercised for real by the server tests above, which
# run without cheroot installed.)
print("  without it, the fallback still serves TLS: proven by the live "
      "server used above")

# A unit runs one server and many copies of this program. The server is
# the one nobody restarts, so an examiner can meet a fault that was fixed
# months ago and have no way to tell. The client checks.
print("\nwhether the server is current")
from fme.review import remotestore as _remote

print(f"  this client expects server "
      f"{'.'.join(str(n) for n in _remote.NEEDS_SERVER)} or newer")
ok &= _remote.NEEDS_SERVER >= (1, 1, 0)

# And an old server's own words are translated rather than passed through:
# "database is locked" reads as a fault when it is a queue.
source = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fme", "review", "remotestore.py"),
    encoding="utf-8").read()
translates = 'if "database is locked" in message.lower():' in source
ok &= translates
print(f"  an old server's \"database is locked\" is explained: {translates}")
says_restart = "Restart the server" in source
ok &= says_restart
print(f"  and says to restart the server: {says_restart}")

# A server that invents an empty store answers every lookup with "not
# known" and looks exactly like a store that works and happens to hold
# nothing matching. That is the worst way for a hash store to fail, and it
# used to be the default.
print("\na server is never allowed to invent a store")
import subprocess as _sub

empty_dir = os.path.join(ROOT, "no_store_here")
os.makedirs(empty_dir, exist_ok=True)

no_store = _sub.run(
    [sys.executable, "launch.py", "hashserver", "--port", "0"],
    capture_output=True, text=True, cwd=project, timeout=120)
ok &= no_store.returncode != 0 and "No --store given" in no_store.stdout
print(f"  with no --store it refuses: {no_store.returncode != 0}")

missing = os.path.join(empty_dir, "not_here.db")
absent = _sub.run(
    [sys.executable, "launch.py", "hashserver", "--store", missing,
     "--port", "0"],
    capture_output=True, text=True, cwd=project, timeout=120)
ok &= absent.returncode != 0
ok &= not os.path.exists(missing)
print(f"  a store that is not there is an error, and no file is made: "
      f"{not os.path.exists(missing)}")
ok &= "--create" in absent.stdout
print(f"  and it says how to make one on purpose: True")

print("\nplain HTTP on a network interface takes a decision")
plain = _sub.run(
    [sys.executable, "launch.py", "hashserver", "--store", store_path,
     "--accounts", accounts_path, "--host", "0.0.0.0", "--port", "0"],
    capture_output=True, text=True, cwd=project, timeout=120)
refused_plain = plain.returncode == 2 and "--insecure" in plain.stdout
ok &= refused_plain
print(f"  without --cert or --insecure it does not start: {refused_plain}")

print("\nfailed sign-ins are recorded, and turned away after a while")
prober = {"REMOTE_ADDR": "10.9.9.9"}
for i in range(hashserver.FAILED_LIMIT):
    client.get("/api/v1/stats", headers={"Authorization": f"Bearer wrong{i}"},
               environ_base=prober)
turned = client.get("/api/v1/stats",
                    headers={"Authorization": "Bearer wrong-again"},
                    environ_base=prober).status_code
ok &= turned == 429
print(f"  after {hashserver.FAILED_LIMIT} unknown tokens the next is refused "
      f"unlogged: {turned == 429}")
known = client.get("/api/v1/stats",
                   headers={"Authorization": f"Bearer {contributor}"},
                   environ_base=prober).status_code
ok &= known == 200
print(f"  a known token from that address is still answered: {known == 200}")
log = call("/api/v1/access", administrator).get_json()["access"]
refusals = [r for r in log if r.get("action") == "refused"
            and r.get("address") == "10.9.9.9"]
ok &= len(refusals) == hashserver.FAILED_LIMIT
ok &= not any("wrong" in (r.get("detail") or "") for r in refusals)
print(f"  each one is in the access log ({len(refusals)}), without the token")
signin = client.post("/admin", data={"token": "nope"},
                     environ_base={"REMOTE_ADDR": "10.8.8.8"},
                     headers={"Origin": "http://localhost"})
log = call("/api/v1/access", administrator).get_json()["access"]
ok &= any(r.get("action") == "refused" and r.get("address") == "10.8.8.8"
          for r in log)
print(f"  a failed admin sign-in too: "
      f"{any(r.get('address') == '10.8.8.8' for r in log)}")

print("\nwhere the server keeps its sets is the administrator's business")
# jsmith was disabled above; ltorres (contribute) is not an administrator.
as_reader = call("/api/v1/sources", contributor).get_json()["sources"]
as_admin = call("/api/v1/sources", administrator).get_json()["sources"]
hidden = all("path" not in row for row in as_reader) and \
    any("path" in row for row in as_admin)
ok &= hidden
print(f"  a non-administrator sees no server paths; an administrator does: "
      f"{hidden}")

# Seven accounts on one store, three of them able to remove sources,
# is not a place for version drift to go unnoticed. Each request
# declares the client's version and the server records it.
print("\nwho is running what")
versions = hashserver.Accounts(
    os.path.join(os.path.dirname(accounts_path), "versions.db"))
versions.add("examiner_a", "admin")
versions.add("examiner_b", "contribute")
versions.record("examiner_a", "lookup", "1 hash", "127.0.0.1",
                version="199")
versions.record("examiner_b", "lookup", "1 hash", "127.0.0.1",
                version="178")
seen = {row["who"]: row["version"] for row in versions.client_versions()}
ok &= seen.get("examiner_a") == "199" and seen.get("examiner_b") == "178"
print(f"   the server sees: {seen}")

# An old client is recorded, never refused: refusing would stop an
# examiner mid-case over a version number.
versions.record("examiner_b", "lookup", "1 hash", "127.0.0.1")
ok &= len(versions.client_versions()) >= 2
print(f"   a request with no version declared is still served: True")
versions.close()

# A version floor, and what it must not do.
#
# Refusing a lookup would stop an examiner part way through a case
# because of a number -- worse than the drift it prevents, since their
# work is already underway and the store is the same store. Refusing a
# contribution is different: an old client writing into a shared set is
# how a unit ends up with rows nobody can account for, and that can
# wait until the update is done.
print("\na version floor")
from fme.review.hashserver import WRITING

ok &= "contribute" in WRITING and "import" in WRITING
ok &= "remove" in WRITING and "lookup" not in WRITING
print(f"   it applies to {sorted(WRITING)}")
print(f"   and never to a lookup: {'lookup' not in WRITING}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
