"""A note written on a file travels with it through the hash store, and the
next case to meet the file starts with it (v2026.11.336).

Griffeye's GID shares a description of known material between examiners, so
the second examiner reads what the first one wrote. FME's store carried the
grade and the category's name, and nothing anyone wrote.

  * case A grades a picture, writes a note on it, and contributes;
  * case B holds the same picture, run with no page: it is graded, and its
    note is A's description, saying where it came from;
  * a note already written in B is never replaced;
  * contributed again with the note changed, the store holds the new one;
  * the shared server returns a case's description, and still never an
    imported Project VIC record's free text.
"""
import _sandbox  # noqa: F401
import json
import logging
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)

import make_media_test_image as fixtures  # noqa: E402
from fme.review import hashserver, hashstore  # noqa: E402
from fme.web import media_gui as gui  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


ROOT = os.path.join(tempfile.gettempdir(), "store_descriptions-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
pictures = [fixtures.make_jpeg(40 + n) for n in range(3)]
for folder in ("phone_a", "phone_b"):
    os.makedirs(os.path.join(ROOT, folder))
    for n, body in enumerate(pictures):
        with open(os.path.join(ROOT, folder, f"{folder}_{n}.jpg"), "wb") as fh:
            fh.write(body)

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
store_path = os.path.join(ROOT, "store.db")
hashstore.HashStore(store_path).close()
client.post("/api/store", json={"path": store_path}, headers=H)


def run(folder):
    client.post("/api/use", json={"path": os.path.join(ROOT, folder)}, headers=H)
    job = client.post("/api/start", json={"output_root": os.path.join(ROOT, "results")},
                      headers=H).get_json()["job_id"]
    for _ in range(600):
        state = client.get(f"/api/status?job_id={job}").get_json()
        if state["status"] in ("done", "error", "canceled"):
            break
        time.sleep(0.1)
    for _ in range(300):
        scan = client.get(f"/api/store/scan?job={job}").get_json()
        if scan.get("state") in ("done", "failed", None) and scan.get("state") != "running":
            break
        time.sleep(0.1)
    client.post("/api/release", json={}, headers=H)
    with open(os.path.join(state["out_dir"], "manifest.json"), encoding="utf-8") as fh:
        rows = json.load(fh)["files"]
    return job, {r["sha256"]: r for r in rows}


print("Case A: graded, a note written, contributed")
job_a, rows_a = run("phone_a")
first, second, third = sorted(rows_a)
client.post("/api/case/categorize", headers=H,
            json={"job": job_a, "sha256": [first, second, third], "category": 1})
words = "Known series 'Blue Room'; victim identified 2024 (NCMEC report 1234)."
answer = client.post("/api/case/note", headers=H,
                     json={"job": job_a, "sha256": first, "note": words}).get_json()
check("the note is kept", answer.get("ok"), answer.get("error"))
client.post("/api/case/note", headers=H,
            json={"job": job_a, "sha256": second, "note": "A's words on the second"})
client.post("/api/store/contribute", headers=H, json={"job": job_a, "source": "DC-A"})
store = hashstore.HashStore(store_path)
check("not asked to, the notes stay in the case",
      not store.lookup([first])[0]["record"].get("Description"))
store.close()
answer = client.post("/api/store/contribute", headers=H,
                     json={"job": job_a, "source": "DC-A", "descriptions": True}).get_json()
check("contributed", answer.get("ok"), answer.get("error"))
store = hashstore.HashStore(store_path)
found = store.lookup([first])
check("the store holds the description with the grade",
      found and found[0]["record"].get("Description") == words, found)
store.close()

print("Case B: the same pictures, run with no page")
# The second picture already has a note of B's own before the store is asked.
real_scan = gui._store_scan_in_background
pending = {}


def hold(job_id, job, data):
    pending["args"] = (job_id, job, data)


gui._store_scan_in_background = hold
gui._LiveStoreCheck.EVERY = 3600          # nothing checked during the run
job_b, rows_b = run("phone_b")
gui._store_scan_in_background = real_scan
client.post("/api/case/note", headers=H,
            json={"job": job_b, "sha256": second, "note": "B's own words"})
real_scan(*pending["args"])
for _ in range(300):
    scan = client.get(f"/api/store/scan?job={job_b}").get_json()
    if scan.get("state") in ("done", "failed"):
        break
    time.sleep(0.1)
decided = gui._case_for(job_b).decisions()
check("graded from the store", decided.get(first, {}).get("category") == 1)
note = decided.get(first, {}).get("note", "")
check("its note is A's description", note.startswith(words), note)
check("and says where it came from", "DC-A" in note and "hash store" in note, note)
check("a note B had already written is not replaced",
      decided.get(second, {}).get("note") == "B's own words",
      decided.get(second, {}).get("note"))
check("a file with no description gets no note",
      not decided.get(third, {}).get("note"))
check("the answer says how many were described",
      (scan.get("result") or scan).get("described") == 1, scan.get("result", scan).get("described"))

print("Contributed again, the note changed")
client.post("/api/case/note", headers=H,
            json={"job": job_a, "sha256": first, "note": "Corrected: series 'Red Room'."})
client.post("/api/store/contribute", headers=H, json={"job": job_a, "source": "DC-A", "descriptions": True})
store = hashstore.HashStore(store_path)
check("the store holds the new description",
      store.lookup([first])[0]["record"].get("Description") == "Corrected: series 'Red Room'.")
store.close()
client.post("/api/store/contribute", headers=H, json={"job": job_a, "source": "DC-A"})
store = hashstore.HashStore(store_path)
check("contributed again without them, the description is kept",
      store.lookup([first])[0]["record"].get("Description") == "Corrected: series 'Red Room'.")
store.close()

print("Through the shared server")
server_store = os.path.join(ROOT, "shared.db")
shared = hashstore.HashStore(server_store)
shared.contribute_rows([{"md5": "a" * 32, "category": 1, "label": "CAM",
                         "description": "seen in DC-A"}], "DC-A", contributor="ltorres")
shared.contribute_rows([{"md5": "a" * 32, "category": 1, "label": "CAM"}],
                       "DC-A", contributor="ltorres")      # an older client: no key
vics = os.path.join(ROOT, "vics.json")
with open(vics, "w", encoding="utf-8") as fh:
    json.dump({"@odata.context": "http://github.com/ICMEC/ProjectVic/DataModels/1.3.xml#Media",
               "value": [{"MediaID": 1, "MD5": "b" * 32, "Category": 1,
                          "Description": "private to the set"}]}, fh)
shared.import_set(vics, kind="projectvic", name="VIC US")
shared.close()
app = hashserver.build_app(server_store, os.path.join(ROOT, "users.db"))
token = app.config["ACCOUNTS"].add("reader", "read")
server = app.test_client()
matches = server.post("/api/v1/lookup", json={"digests": ["a" * 32, "b" * 32]},
                      headers={"Authorization": f"Bearer {token}"}).get_json().get("matches", [])
by_set = {m["source"]: m for m in matches}
check("a case's description is returned",
      (by_set.get("DC-A") or {}).get("record", {}).get("Description") == "seen in DC-A", by_set.get("DC-A"))
check("an imported set's free text is not",
      "Description" not in (by_set.get("VIC US") or {}).get("record", {}), by_set.get("VIC US"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
