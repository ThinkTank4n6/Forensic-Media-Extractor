"""Opening a case says how far it has got, and a second open is quicker.

Opening a case of 220,000 files took ten seconds and more inside one request
with nothing on the screen; it looked hung, and a second click started it
again. The page now opens it in the background and follows
/api/cases/opening (v2026.11.331). This checks the stages are reported, that
the background open gives what the plain one gives, that the prepared file
list is kept and used, and that it is not used once the manifest changes.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

ROOT = os.path.join(tempfile.gettempdir(), "case_open_progress-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

from fme.web import media_gui as gui  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


N = 40000
case = os.path.join(ROOT, "case")
os.makedirs(case)
records = [{"out_path": f"images/jpg/f{i:06d}.jpg", "ext": "jpg", "category": "image",
            "engine": "filesystem", "size": 1000 + i, "deleted": False,
            "sha256": f"{i:064x}", "md5": f"{i:032x}", "sha1": f"{i:040x}",
            "source_path": f"/DCIM/IMG_{i:06d}.JPG", "offset": None, "fs_type": "APFS",
            "mtime": "2024-05-01T10:00:00+00:00", "captured": "", "container": False}
           for i in range(N)]
with open(os.path.join(case, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic.E01"}, "files": records}, fh)

client = gui.app.test_client()
H = {"X-FME-Request": "1"}


def open_in_background():
    started = client.post("/api/cases/open", json={"out_dir": case, "background": True},
                          headers=H).get_json()
    stages, last = [], None
    deadline = time.time() + 120
    while time.time() < deadline:
        state = client.get(f"/api/cases/opening?job={started['job_id']}").get_json()
        if state.get("stage") and state["stage"] != last:
            stages.append(state["stage"])
            last = state["stage"]
        if state.get("ready"):
            # The server's own record of every stage, not only those this
            # loop happened to look at: on GitHub's runner the kept list was
            # read back between two looks 20 ms apart, and this check failed
            # though the open had used it (v2026.11.340).
            return started, state, state.get("stages") or stages
        time.sleep(0.02)
    return started, {}, stages


print("opening in the background")
started, state, stages = open_in_background()
check("answers at once, with the job it will be", started.get("opening") and started.get("job_id"))
check("says what it is doing as it goes", "Reading the case's list of files" in stages
      and "Preparing the files for the gallery" in stages, " / ".join(stages))
result = state.get("result") or {}
check("and ends with what a plain open answers", result.get("files") == N
      and result.get("job_id") == started["job_id"], result.get("files"))
with gui.JOBS_LOCK:
    job = gui.JOBS.get(started["job_id"]) or {}
check("the case is a job the gallery can read", len(job.get("files") or []) == N)
first_files = [dict(f) for f in job.get("files") or []]
again = client.get(f"/api/cases/opening?job={started['job_id']}")
check("the answer is given once", again.status_code == 404)

print("the prepared file list")
cache = os.path.join(case, gui.OPEN_CACHE)
for _ in range(200):
    if os.path.isfile(cache):
        break
    time.sleep(0.05)
check("is kept beside the manifest", os.path.isfile(cache))
started, state, stages = open_in_background()
check("and read back on the next open", "Reading the case's prepared file list" in stages
      and "Preparing the files for the gallery" not in stages, " / ".join(stages))
with gui.JOBS_LOCK:
    job = gui.JOBS.get(started["job_id"]) or {}
check("giving the same files", [dict(f) for f in job.get("files") or []] == first_files)

print("once the manifest changes")
records[0]["size"] = 1
with open(os.path.join(case, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic.E01"}, "files": records}, fh)
started, state, stages = open_in_background()
check("the kept list is not used", "Preparing the files for the gallery" in stages,
      " / ".join(stages))
with gui.JOBS_LOCK:
    job = gui.JOBS.get(started["job_id"]) or {}
check("and the change is there", (job.get("files") or [{}])[0].get("size") == 1)

print("a case that cannot be read")
with open(os.path.join(case, "manifest.json"), "w", encoding="utf-8") as fh:
    fh.write("{ not json")
started, state, stages = open_in_background()
check("ends with an error the page can show", state.get("ready") and state.get("error"),
      state.get("error"))

print("an answer nobody comes for")
started = client.post("/api/cases/open", json={"out_dir": case, "background": True},
                      headers=H).get_json()
for _ in range(600):
    with gui.OPENING_LOCK:
        if (gui.OPENING.get(started["job_id"]) or {}).get("ready"):
            gui.OPENING[started["job_id"]]["finished"] -= gui.OPENING_KEPT + 1
            break
    time.sleep(0.05)
with open(os.path.join(case, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic.E01"}, "files": records}, fh)
later = client.post("/api/cases/open", json={"out_dir": case, "background": True},
                    headers=H).get_json()
with gui.OPENING_LOCK:
    check("is dropped once it has waited long enough", started["job_id"] not in gui.OPENING)
for _ in range(600):        # this one finished before the manifest is written again
    with gui.OPENING_LOCK:
        if (gui.OPENING.get(later["job_id"]) or {}).get("ready"):
            break
    time.sleep(0.05)

print("the plain open, which tests and scripts use")
with open(os.path.join(case, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "synthetic.E01"}, "files": records}, fh)
plain = client.post("/api/cases/open", json={"out_dir": case}, headers=H).get_json()
check("still answers with the job", plain.get("job_id") and plain.get("files") == N)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
