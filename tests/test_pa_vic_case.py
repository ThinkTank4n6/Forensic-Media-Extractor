"""A Project VIC export from Physical Analyzer opens as a case of its own,
graded as the export graded it (v2026.11.336).

Asked: Physical Analyzer, after processing a phone, exports every picture
and video as a Project VIC JSON with a folder of the files beside it. Does
FME take that file as a new case? It did not: the extraction screen refused
a JSON, and the Hashes tab's "Import its media" copied the files into a
folder no case list showed, with none of the export's categories applied.

  * the extraction screen accepts the export and says what it is;
  * a hash set with no media beside it is refused there, and told where to go;
  * Start runs it as a case: listed, its files in the gallery, each graded
    as the export graded it -- marked as the export's, not by hand;
  * a file whose bytes do not match its record is not graded from it;
  * a file missing from the folder is counted, not invented;
  * the Hashes tab's import is a case too, and can be canceled.
"""
import _sandbox  # noqa: F401
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import make_media_test_image as fixtures  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "pa_vic_case-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
export = os.path.join(ROOT, "export")
files_dir = os.path.join(export, "Report Files")
os.makedirs(files_dir)

pictures = [fixtures.make_jpeg(20 + n) for n in range(6)]
grades = [1, 2, 1, 0, None, 1]          # the last one's file is swapped below
media = []
for n, body in enumerate(pictures):
    md5 = hashlib.md5(body).hexdigest()
    name = f"{md5}.jpg"
    with open(os.path.join(files_dir, name), "wb") as fh:
        fh.write(body if n != 5 else pictures[0] + b"\x00\x00")
    record = {"MediaID": n + 1, "MD5": md5, "SHA1": hashlib.sha1(body).hexdigest(),
              "RelativeFilePath": f"Report Files\\{name}",
              "MediaFiles": [{"FileName": f"IMG_{n:04d}.JPG",
                              "FilePath": f"/private/var/mobile/Media/DCIM/IMG_{n:04d}.JPG"}]}
    if grades[n] is not None:
        record["Category"] = grades[n]
    media.append(record)
media.append({"MediaID": 99, "MD5": "f" * 32, "Category": 1,
              "RelativeFilePath": "Report Files\\gone.jpg",
              "MediaFiles": [{"FileName": "gone.jpg"}]})
doc = {"@odata.context": "http://github.com/ICMEC/ProjectVic/DataModels/1.3.xml#Cases",
       "value": [{"CaseNumber": "DC-2026-0412", "SourceApplicationName":
                  "Cellebrite Physical Analyzer", "Media": media}]}
vic_path = os.path.join(export, "Report.json")
with open(vic_path, "w", encoding="utf-8") as fh:
    json.dump(doc, fh)

bare = os.path.join(ROOT, "national_set.json")
with open(bare, "w", encoding="utf-8") as fh:
    json.dump({"@odata.context": doc["@odata.context"],
               "value": [{"MediaID": 1, "MD5": "a" * 32, "Category": 1}]}, fh)

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
gui.STORE_PATH = os.path.join(ROOT, "no_store.db")


def wait(job):
    for _ in range(600):
        state = client.get(f"/api/status?job_id={job}").get_json()
        if state["status"] in ("done", "error", "canceled"):
            return state
        time.sleep(0.1)
    return state


print("The extraction screen")
answer = client.post("/api/use", json={"path": bare}, headers=H).get_json()
check("a hash set with no media is refused, and pointed to the Hashes tab",
      not answer["ok"] and "Hashes tab" in answer["error"], answer.get("error"))
answer = client.post("/api/use", json={"path": vic_path}, headers=H).get_json()
info = answer.get("info") or {}
check("the export is accepted", answer.get("ok") and info.get("kind") == "projectvic",
      answer.get("error"))
check("and it says what it is", "Physical Analyzer" in info.get("container", "")
      and info.get("records") == 7, (info.get("container"), info.get("records")))

print("Start")
job = client.post("/api/start", json={"out_dir": os.path.join(ROOT, "results"),
                                      "output_root": os.path.join(ROOT, "results")},
                  headers=H).get_json()
check("it starts", job.get("ok"), job.get("error"))
state = wait(job["job_id"])
check("and finishes", state["status"] == "done", state.get("error"))
summary = state.get("summary") or {}
check("every file found is in the case", summary.get("found") == 6
      and summary.get("total_files") == 6, summary.get("total_files"))
check("the one missing from the folder is counted", summary.get("missing") == 1)
check("the mismatched file is counted", summary.get("hash_mismatch") == 1)
check("the case number comes from the export",
      state.get("case_number") == "DC-2026-0412", state.get("case_number"))
listed = client.get("/api/cases").get_json()
check("it is in the case list", any(c.get("out_dir") == state["out_dir"]
                                    for c in listed.get("cases", [])),
      len(listed.get("cases", [])))

case = gui._case_for(job["job_id"])
decided = case.decisions()
by_digest = {}
for row in json.load(open(os.path.join(state["out_dir"], "manifest.json"),
                          encoding="utf-8"))["files"]:
    by_digest[row["md5"]] = row["sha256"]
want = {}
for n, body in enumerate(pictures[:5]):
    sha = hashlib.sha256(body).hexdigest()
    if grades[n] is not None:
        want[sha] = case.scheme.vic_in.get(grades[n])
graded = {d: decided.get(d, {}).get("category") for d in want}
check("each file is graded as the export graded it",
      all(graded[d] == want[d] for d in want), graded)
check("marked as the export's, not by hand",
      all(decided[d].get("auto") for d in want))
check("the one the export left ungraded is left to grade",
      decided.get(hashlib.sha256(pictures[4]).hexdigest(), {}).get("category")
      in (None, case.uncategorized))
swapped = [d for d in by_digest.values() if d not in want
           and d != hashlib.sha256(pictures[4]).hexdigest()]
check("the file whose bytes did not match is not graded from its record",
      len(swapped) == 1 and decided.get(swapped[0], {}).get("category")
      in (None, case.uncategorized), swapped)

print("The Hashes tab's import")
answer = client.post("/api/vic/import", json={"path": vic_path}, headers=H).get_json()
state2 = wait(answer["job_id"])
check("is a case too", state2["status"] == "done"
      and any(c.get("out_dir") == state2["out_dir"] for c in
              client.get("/api/cases").get_json().get("cases", [])))
decided2 = gui._case_for(answer["job_id"]).decisions()
check("graded as the export graded it",
      all(decided2.get(d, {}).get("category") == want[d] for d in want))
real = gui.vic_import._hash_file


def slow(path):
    time.sleep(0.3)
    return real(path)


gui.vic_import._hash_file = slow
try:
    answer = client.post("/api/vic/import", json={"path": vic_path}, headers=H).get_json()
    time.sleep(0.2)
    stop = client.post("/api/cancel", json={"job_id": answer["job_id"]}, headers=H).get_json()
    state3 = wait(answer["job_id"])
    check("and can be canceled", stop.get("ok") and state3["status"] == "canceled",
          (stop, state3["status"]))
finally:
    gui.vic_import._hash_file = real

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
