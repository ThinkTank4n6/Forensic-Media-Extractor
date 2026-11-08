"""A grade never crosses from one region's scheme into another's (v2026.11.318).

v2026.11.317 gave each case a regional scheme, then let grades leak between
schemes three ways, all through the real routes rather than the function its
own test called:

1. the stash kept no region, and a stash match was read as a US code: a file
   graded 1 in a Swedish case (not a notable grade there) was auto-graded
   1 Child Abuse Material in the next US case;
2. /api/store/scan rebuilt the store's matches without their region, so a
   Europol store's 3 (NON PERTINENT) became a US 3 (CGI Animation);
3. a non-US case could be sent to a hash server too old to keep it apart,
   and gradings a hash set had made were contributed as the case's own.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from fme.review import casedb, hashstore, remotestore, stash  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_scheme_leaks-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


with contextlib.redirect_stdout(io.StringIO()):
    from fme.web import media_gui as gui
client = gui.app.test_client()
H = {"X-FME-Request": "1"}
A, B, C = "a" * 64, "b" * 64, "c" * 64
STASH = os.path.join(ROOT, "stash.db")


def case(name, region, digests):
    out_dir = os.path.join(ROOT, name)
    os.makedirs(out_dir)
    files = [{"sha256": d, "md5": d[:32], "sha1": d[:40], "out_path": f"{d[:6]}.jpg",
              "rel_path": f"{d[:6]}.jpg", "category": "image", "ext": "jpg",
              "size": 1, "source_path": "/x"} for d in digests]
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": {"image": name}, "files": files}, fh)
    job = "job-" + name
    with gui.JOBS_LOCK:
        gui.JOBS[job] = {"out_dir": out_dir, "files": files, "status": "done"}
    gui.settings.save({"category_region": region, "stash_path": STASH})
    return job, gui._case_for(job)


print("the stash keeps the scheme a grade was made in")
job_se, se = case("se", "SE", [A])
check("graded 1 in a Swedish case through the page",
      client.post("/api/case/categorize", headers=H,
                  json={"job": job_se, "sha256": [A], "category": 1}).status_code == 200)
kept = stash.Stash(STASH)
row = kept.lookup([A]).get(A) or {}
kept.close()
check("the stash row carries SE and the name it was graded with",
      row.get("region") == "SE" and row.get("label") == "Färdigpuberterad <18 år", row)

print("\nthe store scan, through the route")
store_path = os.path.join(ROOT, "store.db")
store = hashstore.HashStore(store_path)
store.set_region("EUROPOL")
store.contribute_rows([{"sha256": B, "category": 3, "label": "NON PERTINENT"}],
                      "EU-1", region="EUROPOL")
store.close()
check("the store is chosen", client.post("/api/store", headers=H,
                                         json={"path": store_path}).status_code == 200)
job_us, us = case("us", "US", [A, B, C])
answer = client.post("/api/store/scan", headers=H, json={"job": job_us}).get_json()
got = {d: us.decision(d)["category"] for d in (A, B)}
check("in a US case, neither the Swedish 1 nor the Europol 3 is applied",
      got == {A: 8, B: 8}, (got, answer.get("applied")))
check("and both are counted as graded under another scheme",
      (answer.get("applied") or {}).get("other_scheme") == 2, answer.get("applied"))
job_eu, eu = case("eu", "EUROPOL", [B, C])
client.post("/api/store/scan", headers=H, json={"job": job_eu})
check("in a Europol case, the Europol store's 3 is applied as NON PERTINENT",
      eu.decision(B)["category"] == 3, eu.decision(B))
job_se2, se2 = case("se2", "SE", [A])
client.post("/api/store/scan", headers=H, json={"job": job_se2})
check("in another Swedish case, the stash's Swedish 1 is applied",
      se2.decision(A)["category"] == 1, se2.decision(A))

print("\na stash row from before regions is shown, never applied")
old = stash.Stash(STASH)
old._con.execute("UPDATE stash SET region=NULL")
old._con.commit()
old.close()
job_us2, us2 = case("us2", "US", [A])
answer = client.post("/api/store/scan", headers=H, json={"job": job_us2}).get_json()
check("its 1 is not taken for CAM in a US case", us2.decision(A)["category"] == 8
      and answer.get("your_stash") == 1, (us2.decision(A), answer.get("your_stash")))

print("\ncontributing")
eu.set_category(C, 3, by="hash set: NSRL")
eu.set_category(B, 1)                       # the examiner's own finding
store = hashstore.HashStore(store_path)
before = len(store.lookup([C]))
result = store.contribute(os.path.join(ROOT, "eu"), "eu case")
check("a grading a hash set made is not contributed as the case's own",
      result.get("skipped_hash_set") == 1 and result.get("files") == 1
      and len(store.lookup([C])) == before, result)
store.close()

remote = remotestore.RemoteHashStore.__new__(remotestore.RemoteHashStore)
sent = []


def fake_call(path, body=None, method="GET", **kw):
    if path == "/api/v1/stats":
        return {"ok": True, "server": fake_call.server, **fake_call.extra}
    sent.append(body)
    return {"ok": True, "added": 1, "regraded": 0}


remote._call = fake_call
for version, extra, want in (("1.1.0", {}, "too old"),
                             ("1.2.0", {"region": "US"}, "holds US"),
                             ("1.2.0", {"region": "EUROPOL"}, "")):
    fake_call.server, fake_call.extra = version, extra
    try:
        remote.contribute_rows([{"sha256": B, "category": 3, "label": "x"}], "eu",
                               region="EUROPOL")
        said = ""
    except remotestore.RemoteStoreError as exc:
        said = str(exc)
    check(f"a Europol case and a {version} server holding {extra.get('region', '?')}: "
          f"{'refused' if want else 'sent'}",
          (want in said) if want else (not said and sent), said[:70])
fake_call.server, fake_call.extra = "1.1.0", {}
sent.clear()
remote.contribute_rows([{"sha256": B, "category": 1, "label": "x"}], "us", region="US")
check("a US case still goes to an older server, as it always has", len(sent) == 1)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
