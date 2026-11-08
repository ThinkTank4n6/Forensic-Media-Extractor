"""Every endpoint in the interface, exercised against a real case.

Most of this program's faults have not been wrong answers -- they have been
features that quietly stopped being reachable: a handler removed by an edit
aimed at something else, a route nothing calls any more, a result the dialog
cannot display. Unit tests do not catch that, because each piece still works
on its own.

So this drives the interface the way the browser does, end to end, and says
which features answer. A failure here means something a person would click
is broken, whatever the other tests say.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import tempfile
import json, os, shutil, subprocess, sys, time, urllib.error, urllib.request

def _free_port() -> int:
    """A port nothing else is on.

    A fixed one fails intermittently and misleadingly: the test looks
    broken when what actually happened is that something else -- a server
    from an earlier run, another copy of the suite -- was already on it.
    """
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PORT = _free_port()
BASE = f"http://127.0.0.1:{PORT}"
results = []

def call(method, path, payload=None, expect=200):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json",
                 "X-FME-Request": "1"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            try: return r.status, json.loads(body)
            except Exception: return r.status, {"_raw": len(body)}
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

def check(label, method, path, payload=None, want=200, test=None):
    code, body = call(method, path, payload)
    ok = (code == want) and (test(body) if test else True)
    results.append((label, ok, code, str(body)[:90]))
    return body

# A small case of its own, so the audit does not depend on anything left
# lying about from another test.
# One directory per process. Twenty-seven tests used a
# fixed path here and each wipes it on startup, so two
# runs overlapping -- which happens whenever a batch is
# cut short and its child keeps going -- had the second
# deleting the first's working files mid-run. That is
# the only mechanism found for this suite's intermittent
# failures.
ROOT = os.path.join(tempfile.gettempdir(), "endpoint_audit-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
SOURCE = os.path.join(ROOT, "src")
os.makedirs(SOURCE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import make_media_test_image as _fixtures

for index in range(8):
    with open(os.path.join(SOURCE, f"p{index}.jpg"), "wb") as fh:
        fh.write(_fixtures.make_jpeg(12 + index))
with open(os.path.join(SOURCE, "clip.mp4"), "wb") as fh:
    fh.write(_fixtures.make_mp4(40000))

RUN = os.path.join(ROOT, "run")
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", SOURCE, "-o", RUN],
               capture_output=True,
               cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

srv = subprocess.Popen([sys.executable, "-m", "fme", "gui", "--port", str(PORT),
                        "--no-browser"],
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
# Waiting for the server to answer. Generous on purpose: when the whole
# suite runs, several of these start in sequence on a machine already busy
# with extractions and decoding, and a server that takes twenty seconds to
# bind is slow, not broken. A short wait here fails the test for a reason
# that has nothing to do with what it is testing, which is worse than
# waiting.
ready = False
for _ in range(300):
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=5)
        ready = True
        break
    except Exception:  # noqa: BLE001 - not up yet is the normal case
        if srv.poll() is not None:
            break                       # it exited; waiting will not help
        time.sleep(0.4)

if not ready:
    out, err = "", ""
    try:
        srv.terminate()
        out, err = srv.communicate(timeout=10)
    except Exception:  # noqa: BLE001
        pass
    print(f"the server never answered on {BASE} "
          f"(exit code {srv.poll()})")
    if err:
        print(err.decode("utf-8", "replace")[-800:])
    sys.exit(1)

try:
    check("health", "GET", "/api/health", test=lambda b: b.get("ok"))
    check("capabilities", "GET", "/api/capabilities", test=lambda b: "engine" in b)
    check("output folder", "GET", "/api/output")
    check("signature list", "GET", "/api/signature/list")
    check("case list", "GET", "/api/cases", test=lambda b: "cases" in b)
    check("current job", "GET", "/api/job/current")
    check("hash sets", "GET", "/api/hashsets")
    # Asking what the store is must answer whether or not one exists yet:
    # "none yet" is a state the tab has to be able to show. This failed
    # intermittently for exactly that reason -- it passed only when an
    # earlier test had happened to leave a store file behind.
    check("store, when one exists or not", "GET", "/api/store",
          test=lambda b: "total" in b)
    check("scheme", "GET", "/api/scheme")
    check("report fields", "GET", "/api/report/fields",
          test=lambda b: len(b.get("fields", [])) > 10)
    check("manual page", "GET", "/manual")
    check("setup guide", "GET", "/server-setup")
    check("favicon", "GET", "/favicon.ico")

    opened = check("open a case", "POST", "/api/cases/open",
                   {"out_dir": RUN}, test=lambda b: b.get("files"))
    job = opened.get("job_id", "")
    status = check("status", "GET", f"/api/status?job_id={job}",
                   test=lambda b: b.get("files"))
    files = status.get("files", [])
    one = files[0]

    check("details", "GET", f"/api/details?job={job}&file={one['rel_path']}",
          test=lambda b: b.get("record"))
    check("file bytes", "GET", f"/api/file?job={job}&file={one['rel_path']}",
          test=lambda b: b.get("_raw", 0) > 0)
    check("hex view", "GET", f"/api/hex?job={job}&file={one['rel_path']}&length=64",
          test=lambda b: b.get("lines"))
    check("case", "GET", f"/api/case?job={job}", test=lambda b: b.get("categories"))

    check("categorize one", "POST", "/api/case/categorize",
          {"job": job, "sha256": one["sha256"], "category": 5},
          test=lambda b: b.get("changed") == 1)
    check("categorize many", "POST", "/api/case/categorize",
          {"job": job, "sha256": [f["sha256"] for f in files[1:6]], "category": 1},
          test=lambda b: b.get("changed") == 5)
    case = call("GET", f"/api/case?job={job}")[1]
    flag = case["flags"][0]
    check("flag on", "POST", "/api/case/flag",
          {"job": job, "sha256": one["sha256"], "flag_id": flag["id"], "on": True})
    check("flag off", "POST", "/api/case/flag",
          {"job": job, "sha256": one["sha256"], "flag_id": flag["id"], "on": False})
    check("note", "POST", "/api/case/note",
          {"job": job, "sha256": one["sha256"], "note": "audit"})
    check("add category", "POST", "/api/case/category",
          {"job": job, "action": "add", "name": "Audit category",
           "color": "#123456"})
    check("snapshot", "POST", "/api/case/snapshot", {"job": job, "label": "audit"})
    check("audit log", "GET", f"/api/case/audit?job={job}",
          test=lambda b: b.get("audit"))

    check("similarity", "POST", "/api/similarity/scan", {"job": job, "distance": 8},
          test=lambda b: "hashed" in b)
    check("screening", "POST", "/api/screening/scan", {"job": job, "faces": False},
          test=lambda b: "bands" in b)
    # Rendering applies to formats a browser cannot draw. The fixtures are
    # not decodable images, so a refusal here is correct behavior -- what
    # matters is that it refuses with a reason rather than breaking.
    code, body = call("GET", f"/api/preview?job={job}&file={one['rel_path']}")
    results.append(("preview refuses undecodable files clearly",
                    code in (200, 400) and (code == 200 or "Could not render"
                                            in str(body.get("error", ""))),
                    code, str(body)[:80]))
    check("frames", "GET", f"/api/frames?job={job}&file={one['rel_path']}")
    check("md5 export", "POST", "/api/export/md5", {"job": job},
          test=lambda b: b.get("_raw", 0) > 0)
    check("store scan", "POST", "/api/store/scan", {"job": job},
          test=lambda b: "checked" in b)
    # Load a set first: scanning with none loaded is correctly refused.
    import tempfile
    seed = tempfile.mktemp(suffix=".txt")
    with open(seed, "w") as fh:
        fh.write("\n".join(f["md5"] for f in files[:5]))
    check("load a hash set", "POST", "/api/hashsets", {"path": seed})
    check("hash set scan", "POST", "/api/hashsets/scan", {"job": job},
          test=lambda b: "checked" in b)

    started = check("report start", "POST", "/api/report/build",
                    {"job": job, "case_number": "AUDIT", "formats": ["html"],
                     "dest": os.path.join(tempfile.gettempdir(), "auditreport")}, test=lambda b: b.get("id"))
    rid = started.get("id", "")
    for _ in range(90):
        code, body = call("GET", f"/api/report/status?id={rid}")
        if body.get("status") != "running": break
        time.sleep(0.5)
    results.append(("report finishes", body.get("status") == "done", 200,
                    body.get("status", "")))
    # Every format that wrote something has to say where it went: a file
    # with no path shown is a file nobody can find.
    pathless = [w for w in body.get("written", [])
                if not w.get("path") and not w.get("skipped")]
    results.append(("every written file reports its path", not pathless, 200,
                    str(pathless)[:80]))
    check("export C4All", "POST", "/api/export", {"job": job, "case": "AUDIT"})
    check("export LAVA", "POST", "/api/export/lava",
          {"job": job, "case": "AUDIT", "examiner": "Det. C. T."})
    # Project VIC and KML are written during a run and by the report
    # builder; there is no separate endpoint for them, and the report check
    # above asks for Project VIC explicitly.
    check("contact sheet", "POST", "/api/report/html", {"job": job})
    check("scheme save", "POST", "/api/scheme", {"job": job, "action": "save"})
    # An examiner finishing a case wants the drive back without closing
    # the program. Releasing has to let go of the choice and of anything
    # still holding the file open.
    check("release the image", "POST", "/api/release", {},
          test=lambda b: b.get("ok") and "note" in b)
    check("scheme apply", "POST", "/api/scheme", {"job": job, "action": "apply"})
finally:
    srv.terminate(); srv.wait(timeout=10)

print(f"\n{'feature':34} {'':4} status")
bad = 0
for label, ok, code, detail in results:
    if not ok: bad += 1
    print(f"  {label:34} {'ok ' if ok else 'FAIL'} {code}  {'' if ok else detail}")
# A running job sends a window of its files, not all of them.
#
# This endpoint is polled every few hundred milliseconds. It used to
# return the whole list each time: at two hundred thousand files that
# is a ninety-nine megabyte response, several times a second, and the
# browser stops drawing while the console keeps counting. An examiner
# sees a gallery that stays empty while the recovered count climbs.
#
# A finished job still hands over everything -- that is what the
# gallery browses once a case is open.
print("\nwhat a running job sends to the page")
import json as _win_json

from fme.web import media_gui as _gui

_many = [{"rel_path": f"images/jpg/f{_i}.jpg", "size": 2847193,
          "sha256": "a" * 64, "category": "image", "deleted": False}
         for _i in range(50000)]
with _gui.JOBS_LOCK:
    _gui.JOBS["__window_probe__"] = {
        "status": "running", "done": 1, "total": 2, "phase": "carve",
        "recovered": len(_many), "files": _many, "summary": {},
        "error": None}

# Below the paging threshold, as the page has always been sent it. Above
# it (v2026.11.309) nothing is sent and the page asks for its pages; that
# is checked after.
_threshold = _gui.PAGED_THRESHOLD
_gui.PAGED_THRESHOLD = len(_many) + 1
with _gui.app.test_client() as _client:
    _raw = _client.get("/api/status?job_id=__window_probe__").get_data()
_said = _win_json.loads(_raw)

_windowed = len(_said.get("files", [])) <= _gui.LIVE_WINDOW
if not (_windowed and _said.get("files_known") == len(_many)):
    bad += 1
print(f"  holds {_said.get('files_known'):,}, sends "
      f"{len(_said.get('files', [])):,}, response "
      f"{len(_raw) / 1024:.0f} KB")

# The window must carry the newest files: an examiner watching a run
# wants what was just found, not the first screenful from an hour ago.
_newest = _said.get("files", [])[-1].get("rel_path") if _said.get("files") else ""
if _newest != _many[-1]["rel_path"]:
    bad += 1
print(f"  and the newest file is in it: {_newest == _many[-1]['rel_path']}")

with _gui.JOBS_LOCK:
    _gui.JOBS["__window_probe__"]["status"] = "done"
with _gui.app.test_client() as _client:
    _done = _win_json.loads(
        _client.get("/api/status?job_id=__window_probe__").get_data())
if len(_done.get("files", [])) != len(_many):
    bad += 1
print(f"  a finished job still hands over all "
      f"{len(_done.get('files', [])):,}")

_gui.PAGED_THRESHOLD = len(_many) - 1
with _gui.app.test_client() as _client:
    _big = _win_json.loads(
        _client.get("/api/status?job_id=__window_probe__").get_data())
    _current = _win_json.loads(_client.get("/api/job/current").get_data())
_gui.PAGED_THRESHOLD = _threshold
if not (_big.get("paged") and not _big.get("files")
        and _big.get("files_known") == len(_many)):
    bad += 1
print(f"  above the paging threshold it sends none and says paged: "
      f"{bool(_big.get('paged'))}, {len(_big.get('files') or [])} sent of "
      f"{_big.get('files_known'):,}")
with _gui.JOBS_LOCK:
    del _gui.JOBS["__window_probe__"]

# One page of files, counted and filtered on the server.
print("\nasking for a page of a large case")
_rows = [{"rel_path": f"images/jpg/f{_i}.jpg",
          "category": "image" if _i % 3 else "video",
          "deleted": _i % 5 == 0, "sha256": "a" * 64, "size": 1000 + _i}
         for _i in range(200000)]
with _gui.JOBS_LOCK:
    _gui.JOBS["__page_probe__"] = {"status": "done", "files": _rows,
                                   "recovered": len(_rows), "summary": {},
                                   "error": None, "done": 1, "total": 1}
with _gui.app.test_client() as _client:
    _raw = _client.get("/api/files?job_id=__page_probe__&limit=500").get_data()
    _page = _win_json.loads(_raw)
    if not (len(_page["files"]) == 500 and _page["total"] == 200000):
        bad += 1
    print(f"  200,000 files: page of {len(_page['files'])}, "
          f"{len(_raw) / 1024:.0f} KB, {_page['server_ms']} ms")

    # An offset past the end comes back to the last page: the list
    # grows during a run and an offset valid a moment ago can fall off.
    _past = _win_json.loads(_client.get(
        "/api/files?job_id=__page_probe__&limit=500&offset=999999").get_data())
    if not _past["files"]:
        bad += 1
    print(f"  an offset past the end returns {len(_past['files'])} "
          f"at offset {_past['offset']:,}")

    # Filters are applied where the rows are, not in the browser.
    _some = _win_json.loads(_client.get(
        "/api/files?job_id=__page_probe__&kind=video&deleted=1").get_data())
    _right = all(r["deleted"] and r["category"] == "video"
                 for r in _some["files"])
    if not (_right and 0 < _some["total"] < 200000):
        bad += 1
    print(f"  deleted videos: {_some['total']:,} matched, every row "
          f"on the page matches: {_right}")

    # A limit nobody should be able to lift: an unbounded page is the
    # problem this endpoint exists to solve.
    _huge = _win_json.loads(_client.get(
        "/api/files?job_id=__page_probe__&limit=999999").get_data())
    if len(_huge["files"]) > 5000:
        bad += 1
    print(f"  a limit of 999,999 is capped at {len(_huge['files']):,}")
with _gui.JOBS_LOCK:
    del _gui.JOBS["__page_probe__"]

# Anything media_gui rebinds must be seen by the route modules.
#
# They copy its namespace at import, so a rebound value leaves the
# copy stale -- and the symptom is not an error. The route answers
# 200 with the old value, which is how "the results folder cannot be
# changed" reached an examiner rather than a test.
#
# Checked by rebinding each one and asking the route modules what
# they see, rather than by listing the three that are known.
print("\nvalues the routes must not keep a stale copy of")
import ast as _rb_ast
import importlib as _rb_imp

from fme.web import media_gui as _core_mod

with open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "fme", "web", "media_gui.py"),
        encoding="utf-8") as _fh:
    _core_tree = _rb_ast.parse(_fh.read())
_rebound = sorted({name for node in _rb_ast.walk(_core_tree)
                   if isinstance(node, _rb_ast.Global)
                   for name in node.names})

_stale = []
for _name in _rebound:
    _was = getattr(_core_mod, _name, None)
    _probe = f"__probe__{_name}"
    setattr(_core_mod, _name, _probe)
    for _module in sorted(n for n in dir(_core_mod) if n.startswith("routes_")):
        pass
    import glob as _rb_glob
    for _path in sorted(_rb_glob.glob(os.path.join(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))),
            "fme", "web", "routes_*.py"))):
        _mod = _rb_imp.import_module(
            "fme.web." + os.path.basename(_path)[:-3])
        # A copy is stale when the module still holds the old value.
        if hasattr(_mod, _name) and getattr(_mod, _name) != _probe:
            _stale.append(f"{os.path.basename(_path)}:{_name}")
    if _was is not None:
        setattr(_core_mod, _name, _was)

if _stale:
    bad += 1
print(f"  {len(_rebound)} rebound value(s): {', '.join(_rebound)}")
print(f"  stale copies: {', '.join(sorted(set(_stale))) if _stale else 'none'}")

# Choosing where results go must actually change where they go.
#
# The route modules copy media_gui's namespace when they load, which
# is right for anything shared by reference and wrong for anything
# rebound afterwards. OUT_ROOT is rebound whenever an examiner picks a
# results folder, so the route answered with the folder it had at
# import and the field in the page snapped back to it. An examiner
# could not put a case anywhere but the default.
print("\nchoosing where results go")
import tempfile as _tf

_central = os.path.join(_tf.gettempdir(), "fme_results_choice")
shutil.rmtree(_central, ignore_errors=True)
os.makedirs(_central, exist_ok=True)

_said = check("choose a results folder", "POST", "/api/output",
              {"path": _central},
              test=lambda b: os.path.abspath(b.get("path", "")) ==
              os.path.abspath(_central))
_now = check("and it is remembered", "GET", "/api/output",
             test=lambda b: os.path.abspath(b.get("path", "")) ==
             os.path.abspath(_central))

# Put it back, so the rest of this file runs where it expects to.
check("and it can be set back", "POST", "/api/output", {"path": ROOT})

print(f"\n{len(results) - bad} of {len(results)} working")
# The routes live in files of their own now, and those files must hold
# routes and nothing else: a constant that moves with them lands where
# media_gui cannot see it. The first version of this split took
# CONVERTIBLE and THUMB_EDGE along, after which opening a case returned
# 500 and nothing on the page explained why.
# Every document the program offers must actually open. A Help menu
# item that 404s is worse than one that is not there: an examiner asked
# in a hearing what the tool does not do should find the answer in the
# tool, not an error page.
print("\nthe documents the program offers")
from fme.web import media_gui as _gui
from fme.web.media_gui import SHIPPED_DOCUMENTS as _docs

# Counted here rather than through check(), which reports into a
# summary already printed by this point: a failure below has to fail
# the run, not be noted after the verdict.
for _name, (_file, _about) in sorted(_docs.items()):
    # Through the application rather than the server: by this point in
    # the file the server has been stopped, and an HTTP call here would
    # fail for that reason rather than for anything about the document.
    try:
        with _gui.app.test_client() as _client:
            _body = _client.get(f"/doc/{_name}").data
        _opened = len(_body) > 500
    except Exception:                                      # noqa: BLE001
        _opened = False
    _shipped = os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), _file))
    if not (_opened and _shipped):
        bad += 1
    print(f"  /doc/{_name:12} {'ok ' if _opened else 'FAIL'}  "
          f"{'shipped' if _shipped else 'NOT SHIPPED'}  {_file}")

# And the comparison document must not be offered: it names another
# tool and is a working document, not something to put in front of an
# examiner or a defence expert reading over their shoulder.
_audit = [f for f in _docs.values() if "AUDIT" in f[0]]
if _audit:
    bad += 1
print(f"  the comparison document is not offered     "
      f"{'ok ' if not _audit else 'FAIL'}")

print("\nthe route files hold routes and nothing else")
import ast as _ast

_web = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "fme", "web")
for _name in sorted(n for n in os.listdir(_web) if n.startswith("routes_")):
    with open(os.path.join(_web, _name), encoding="utf-8") as _fh:
        _tree = _ast.parse(_fh.read())
    _stray = []
    for _node in _tree.body:
        if isinstance(_node, _ast.Assign):
            for _target in _node.targets:
                # _core and REBOUND are the import shim at the top of
                # every route file, not stray module state: REBOUND is
                # the list of names deliberately *not* copied from
                # media_gui, and it has to live beside the copy it
                # excludes them from.
                if getattr(_target, "id", "") not in ("", "_core",
                                                      "REBOUND"):
                    _stray.append(_target.id)
        elif isinstance(_node, _ast.AnnAssign):
            # REPORTS: dict = {} is an AnnAssign, not an Assign. A guard
            # that checks only for Assign walks past it, which is how a
            # module-level dict left the page's sight and opening a
            # report began returning 500.
            if getattr(_node.target, "id", "") not in ("", "_core"):
                _stray.append(_node.target.id)
        elif isinstance(_node, _ast.FunctionDef) and not _node.decorator_list:
            _stray.append(_node.name + "()")
        elif isinstance(_node, _ast.ClassDef):
            _stray.append(_node.name)
    if _stray:
        bad += 1
    print(f"  {_name:34} "
          f"{'routes only' if not _stray else 'ALSO HOLDS ' + ', '.join(_stray)}")

print("\nALL CHECKS PASS" if not bad else "\nFAILURES ABOVE")
sys.exit(0 if not bad else 1)
