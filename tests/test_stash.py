"""The examiner's own notable hashes, kept on this machine.

The shared store holds what the unit knows. This holds what one examiner
knows, and the two are not the same thing: contributing to the store is a
deliberate act made later, often after review, and until then an
examiner's own gradings help nobody -- including them. Grade a file on
Monday, meet it again on Thursday, grade it twice.

What it must never do is quietly disagree with the store. Two sources that
both say what a file is, with no sign of which said what, is worse than
one source.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import tempfile
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.review import stash

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "stash_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
path = os.path.join(ROOT, "stash.db")
ok = True

print("what goes in")
own = stash.Stash(path)
own.add([{"sha256": "a" * 64, "md5": "1" * 32, "category": 1, "label": "CAM"},
         {"sha256": "b" * 64, "md5": "2" * 32, "category": 5,
          "label": "Adult"}], note="DC25-320", region="US")
ok &= own.size() == 4           # two files, two algorithms each
print(f"  two graded files -> {own.size()} hash row(s)")

# A file nobody graded is not a notable hash and does not go in.
own.add([{"sha256": "c" * 64, "category": None}])
ok &= own.size() == 4
print(f"  an ungraded file is not stored: still {own.size()}")

# An examiner who changes their mind means the later decision. A stash
# that kept the first one would quietly argue with them.
own.add([{"sha256": "a" * 64, "md5": "1" * 32, "category": 2,
          "label": "Exploitive"}], region="US")
counts = own.by_category()
ok &= own.size() == 4 and counts.get(2) == 2 and 1 not in counts
print(f"  changing your mind replaces it: {counts}")

# And it is the examiner's to empty.
removed = own.forget(["b" * 64, "2" * 32])
ok &= own.size() == 2
print(f"  forgetting a file removes {removed} row(s); {own.size()} left")
own.close()

print("\nwhat a later case gets")
later = stash.check([{"sha256": "a" * 64, "md5": "1" * 32},
                     {"sha256": "z" * 64, "md5": "9" * 32}], path)
ok &= len(later["matched"]) == 1
ok &= later["matched"][0]["source"] == "your stash"
print(f"  {len(later['matched'])} of 2 matched, and the match says where "
      f"it came from: {later['matched'][0]['source']!r}")

# The note must not let a match read as the unit's view.
ok &= "not what the unit has agreed" in later["note"]
print(f"  and that it is not the unit's view: True")

print("\nwhen the two disagree")
# The case this exists for. An examiner who graded something CAM last
# month, and a shared store that says otherwise, needs to know -- not be
# handed one of the two answers.
clash = stash.check(
    [{"sha256": "a" * 64, "md5": "1" * 32}], path,
    store_matches={"a" * 64: {"category": 5, "source": "shared store"}})
ok &= len(clash["disagreements"]) == 1
found = clash["disagreements"][0]
ok &= found["your_stash"] == 2 and found["shared_store"] == 5
print(f"  yours says {found['your_stash']}, the store says "
      f"{found['shared_store']}")
ok &= "Neither has been applied" in found["why"]
print(f"  and neither is applied: True")

# Where they agree there is nothing to report.
quiet = stash.check(
    [{"sha256": "a" * 64, "md5": "1" * 32}], path,
    store_matches={"a" * 64: {"category": 2, "source": "shared store"}})
ok &= not quiet["disagreements"]
print(f"  where they agree, nothing is reported: True")

print("\nwhere it lives")
# Beside the other per-user settings, not in a case and not on a share.
default = stash.default_path()
ok &= "ForensicMediaExtractor" in default and default.endswith("stash.db")
print(f"  {default}")

# Filling a stash as an examiner works and never consulting it is the
# same as not having one: the point is that the next case meets a file
# already graded. For several versions it filled and was never read.
print("\nthe next case actually sees it")
import json as _json
import subprocess as _sub
import time as _time
import urllib.error as _err
import urllib.request as _req

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_media_test_image as _fixtures

project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
flow = os.path.join(ROOT, "two_cases")
for folder in ("src1", "src2"):
    os.makedirs(os.path.join(flow, folder), exist_ok=True)
shared = _fixtures.make_jpeg(140)
for folder in ("src1", "src2"):
    with open(os.path.join(flow, folder, "a.jpg"), "wb") as fh:
        fh.write(shared)
with open(os.path.join(flow, "src2", "b.jpg"), "wb") as fh:
    fh.write(_fixtures.make_jpeg(141))

port = "8719"
environment = dict(os.environ, LOCALAPPDATA=os.path.join(flow, "appdata"))
server = _sub.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", port, "--no-browser"],
    cwd=project, stdout=_sub.DEVNULL, stderr=_sub.DEVNULL, env=environment)
try:
    def ask(path):
        with _req.urlopen(f"http://127.0.0.1:{port}{path}", timeout=120) as r:
            return _json.loads(r.read())

    def send(path, body=None):
        request = _req.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                               data=_json.dumps(body or {}).encode(),
                               headers={"Content-Type": "application/json",
                 "X-FME-Request": "1"})
        try:
            return _json.load(_req.urlopen(request, timeout=300))
        except _err.HTTPError as exc:
            return _json.loads(exc.read())

    for _ in range(150):
        try:
            ask("/api/capabilities")
            break
        except Exception:  # noqa: BLE001
            _time.sleep(0.4)

    send("/api/use", {"path": os.path.join(flow, "src1")})
    first = send("/api/start", {"out_dir": os.path.join(flow, "case1"),
                                "case_number": "DC25-010"})["job_id"]
    for _ in range(120):
        state = ask(f"/api/status?job_id={first}")
        if state["status"] in ("done", "error"):
            break
        _time.sleep(0.5)
    send("/api/case/categorize",
         {"job": first, "sha256": [state["files"][0]["sha256"]],
          "category": 1})

    send("/api/store", {"path": os.path.join(flow, "store.db"),
                        "create": True})
    send("/api/use", {"path": os.path.join(flow, "src2")})
    second = send("/api/start", {"out_dir": os.path.join(flow, "case2"),
                                 "case_number": "DC25-011"})["job_id"]
    for _ in range(120):
        state2 = ask(f"/api/status?job_id={second}")
        if state2["status"] in ("done", "error"):
            break
        _time.sleep(0.5)

    checked = send("/api/store/scan", {"job": second, "autocategorize": False})
    ok &= checked.get("your_stash") == 1
    print(f"  the second case found {checked.get('your_stash')} file(s) it "
          f"had graded before")

    # And the match says where it came from, never implying the unit
    # agreed with it.
    labelled = False
    for entry in ask(f"/api/status?job_id={second}")["files"]:
        for match in entry.get("hash_sets") or []:
            if match.get("set") == "your stash":
                labelled = True
                ok &= match.get("category") == 1
    ok &= labelled
    print(f"  and it is labelled as the examiner's own: {labelled}")

    # Turning it off for a run has to actually turn it off.
    without = send("/api/store/scan", {"job": second, "autocategorize": False,
                                       "use_stash": False})
    ok &= without.get("your_stash") == 0
    print(f"  with the option off: {without.get('your_stash')} match(es)")
finally:
    server.terminate()
    server.wait(timeout=10)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
