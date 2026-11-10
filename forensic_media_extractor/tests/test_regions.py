"""Regional category schemes: a case grades under its own region's (v2026.11.317).

Every case was seeded with the Project VIC 2.0 (US) scheme. A grade is a bare
number, and the same number means different things in different schemes:
Europol's 3 is NON PERTINENT and the US 3 is CGI Animation - Child
Exploitive; the UK's 0 is Uncategorised and the US 0 is Non-Pertinent. So:

1. a new case is made under the region chosen in Settings and keeps it for
   life; a case from before regions is a US case;
2. what a case starts as, what the NSRL may grade a file as, and how a
   Project VIC category maps in and out all come from the case's scheme;
3. a hash store's bare codes are in one region's scheme, and are neither
   contributed from nor applied to a case under another's;
4. reports, the gallery index and the case list ask the case, not a
   constant.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

from fme.review import casedb, fileindex, hashstore, recover, regions  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_regions-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def folder(name):
    path = os.path.join(ROOT, name)
    os.makedirs(path, exist_ok=True)
    return path


A, B, C = "a" * 64, "b" * 64, "c" * 64

print("the schemes")
for scheme in regions.REGIONS.values():
    codes = [p.code for p in scheme.presets]
    keys = [p.key for p in scheme.presets if p.key]
    good = (len(codes) == len(set(codes)) and len(keys) == len(set(keys))
            and scheme.uncategorized in codes
            and (scheme.nonpertinent is None or scheme.nonpertinent in codes)
            and all(c in codes for c in scheme.vic_out)
            and scheme.first_custom > max(codes))
    if not good:
        check(f"{scheme.id}: codes and keys unique, its own codes named", False)
check(f"{len(regions.REGIONS)} schemes, each with unique codes and keys", ok)
us = regions.get("US")
check("the US scheme is the one every case had: ten codes, Uncategorized 8",
      us.rows() == [(0, "0", "Non-Pertinent", "#16a34a"),
                    (1, "1", "Child Abuse Material (CAM)", "#dc2626"),
                    (2, "2", "Child Exploitive (non-CAM) / Age Difficult", "#ea580c"),
                    (3, "3", "CGI Animation - Child Exploitive", "#d97706"),
                    (4, "4", "Comparison Images", "#2563eb"),
                    (5, "5", "Adult Pornography", "#7c3aed"),
                    (6, "6", "Abnormal Adult Porn", "#9333ea"),
                    (7, "7", "Notable", "#0891b2"),
                    (8, "8", "Uncategorized", "#6b7280"),
                    (9, "9", "Suspect Pictures", "#be123c")]
      and us.uncategorized == 8 and us.first_custom == 10
      and casedb.CATEGORIES == us.rows())
check("and its severity is as it was: 1, 2, 3, then 4-9 and its own, then 0",
      sorted(range(12), key=us.severity_rank) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 0]
      and sorted(range(12), key=us.severity_rank)
      == sorted(range(12), key=casedb.severity_rank))
check("an unknown or unset region is the US", regions.get("") is us
      and regions.get("atlantis") is us and regions.get("uk").id == "UK")
check("only the US scheme maps VICS beyond Non-Pertinent",
      regions.get("EUROPOL").vic_out == {3: 0} and regions.get("UK").vic_out == {}
      and us.vic_out == {0: 0, 1: 1, 2: 2, 3: 3, 4: 4})

print("\na case keeps the scheme it was made under")
uk_dir = folder("uk")
case = casedb.open_case(uk_dir, "Det. A", "24-1", region="UK")
names = [c["name"] for c in case.categories()]
check("a new UK case is seeded with the UK scheme, in its own order",
      names[0] == "SC Category A" and names[-1] == "Uncategorised", names[:2])
check("and starts files at the UK's Uncategorised, 0",
      case.uncategorized == 0 and case.decision(A)["category"] == 0)
case.set_note(B, "seen on the phone")
check("a note on an ungraded file leaves it Uncategorised, not the US 8",
      case.decision(B)["category"] == 0, case.decision(B)["category"])
check("the region is in the audit log", any(r["action"] == "region"
                                            for r in case.audit()))
case.close()
case = casedb.open_case(uk_dir, region="US")
check("reopened asking for the US, it is still a UK case", case.region == "UK")
case.close()

old_dir = folder("old")
case = casedb.open_case(old_dir)
case.set_category(A, 1)
case.close()
con = sqlite3.connect(os.path.join(old_dir, "case.db"))
con.execute("DELETE FROM meta WHERE key='category_region'")
con.commit()
con.close()
case = casedb.open_case(old_dir, region="UK")
check("a case from before regions is a US case, whatever Settings say",
      case.region == "US" and case.uncategorized == 8
      and case.decision(A)["category"] == 1)
case.close()

dk = casedb.open_case(folder("dk"), region="DK")
check("an examiner's own category is numbered above the scheme's own codes",
      dk.add_category("Series X") == 25)
dk.close()

print("\nhash sets are read in the case's scheme")
import contextlib  # noqa: E402
import io  # noqa: E402
import logging  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)
with contextlib.redirect_stdout(io.StringIO()):
    from fme.web import media_gui as gui  # noqa: E402


def graded(region, lookup):
    out_dir = folder("apply-" + region + str(len(os.listdir(ROOT))))
    job = "job-" + os.path.basename(out_dir)
    with gui.JOBS_LOCK:
        gui.JOBS[job] = {"out_dir": out_dir, "files": []}
    gui.settings.save({"category_region": region})
    answer = gui._apply_hash_categories(job, lookup)
    case = gui._case_for(job)
    return answer, {d: case.decision(d)["category"] for d in lookup}


vic1 = {"kind": "projectvic", "set": "VICS", "record": {"Category": 1}}
nsrl = {"kind": "nsrl", "set": "NSRL", "record": {}}
answer, got = graded("UK", {A: [vic1], B: [nsrl]})
check("in a UK case, VICS 1 is not taken for SC Category A, and the NSRL "
      "grades nothing", got == {A: 0, B: 0}, got)
answer, got = graded("EUROPOL", {A: [{"kind": "projectvic", "set": "VICS",
                                      "record": {"Category": 0}}], B: [nsrl]})
check("in a Europol case, VICS 0 and an NSRL hit are its NON PERTINENT, 3",
      got == {A: 3, B: 3}, got)
store_us = {"kind": "custom", "set": "unit store", "category": 3, "record": {},
            "region": "US"}
store_eu = dict(store_us, region="EUROPOL")
answer, got = graded("EUROPOL", {A: [store_us], B: [store_eu]})
check("a US store's 3 is not applied to a Europol case; a Europol store's is",
      got == {A: 0, B: 3} and answer.get("other_scheme") == 1, (got, answer))
answer, got = graded("US", {A: [store_eu], B: [dict(store_us, region=None)]})
check("and the reverse: a Europol 3 never becomes the US CGI category; a "
      "store from before regions is a US store",
      got == {A: 8, B: 3}, got)

print("\nthe hash store keeps to one scheme")
store = hashstore.HashStore(os.path.join(ROOT, "store.db"))
check("a store is a US store until told otherwise", store.region == "US")
eu_dir = folder("eu-case")
case = casedb.open_case(eu_dir, region="EUROPOL")
case.set_category(A, 1)
case.close()
with open(os.path.join(eu_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"files": [{"sha256": A, "md5": "1" * 32, "sha1": "2" * 40}]}, fh)
try:
    store.contribute(eu_dir, "eu case")
    said = ""
except ValueError as exc:
    said = str(exc)
check("a Europol case is not contributed to a US store, and it says why",
      "Europol" in said and "United States" in said, said[:80])
try:
    store.contribute_rows([{"sha256": A, "category": 1, "label": "CSEM"}],
                          "remote", region="EUROPOL")
    said = ""
except ValueError as exc:
    said = str(exc)
check("nor sent to one over the network", "different things" in said)
check("an empty store can be set to Europol", store.set_region("europol") == "EUROPOL")
result = store.contribute(eu_dir, "eu case")
match = store.lookup([A])
check("then it takes the case, and says which scheme its codes are in",
      result["files"] == 1 and match and match[0]["region"] == "EUROPOL"
      and match[0]["category"] == 1)
try:
    store.set_region("US")
    said = ""
except ValueError as exc:
    said = str(exc)
check("and cannot be switched once it holds codes", "already holds" in said)
store.close()

print("\nexports and readers ask the case")
from fme.exports import projectvic_export, report  # noqa: E402
case = casedb.open_case(eu_dir)
case.set_category(B, 3)
case.close()
with open(os.path.join(eu_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "x"},
               "files": [{"sha256": d, "md5": d[:32], "sha1": d[:40],
                          "out_path": f"images/jpg/{d[:4]}.jpg", "ext": "jpg",
                          "category": "image", "size": 1, "source_path": "/x"}
                         for d in (A, B, C)]}, fh)
out = os.path.join(ROOT, "vics.json")
with contextlib.redirect_stdout(io.StringIO()):
    projectvic_export.export(os.path.join(eu_dir, "manifest.json"), out,
                             case_number="1")
media = json.load(open(out, encoding="utf-8"))["value"][0]["Media"]
cats = {m["MD5"]: m.get("Category") for m in media}
check("a Project VIC export of a Europol case carries only Non-Pertinent, as 0",
      cats.get(A[:32]) is None and cats.get(B[:32]) == 0 and cats.get(C[:32]) is None,
      cats)
data = report.gather(eu_dir)
check("a report knows the case's Uncategorised, findings and region",
      data["uncategorized"] == 0 and data["notable"] == {1}
      and data["trailing"] == {0, 3} and data["region"] == "Europol")
rows = {r["category_name"] for r in data["rows"]}
check("and names each file's grade in the case's scheme",
      rows == {"CSEM", "NON PERTINENT", "UNCATEGORISED"}, rows)
index = fileindex.FileIndex()
index.attach_case(os.path.join(eu_dir, "case.db"))
check("the gallery index counts an ungraded file as the case's Uncategorised",
      index._uncategorized == 0)
index.close()
con = sqlite3.connect(os.path.join(eu_dir, "case.db"))
con.row_factory = sqlite3.Row
check("recovery replays a note on an ungraded file as the case's Uncategorised",
      recover.History(con).uncategorized == 0)
con.close()

print("\nreachable")
client = gui.app.test_client()
H = {"X-FME-Request": "1"}
listed = client.get("/api/regions").get_json()
check("the page can list the schemes, US first",
      listed["regions"][0]["id"] == "US" and len(listed["regions"]) == len(regions.REGIONS))
check("Settings refuses a region that does not exist",
      client.post("/api/settings", json={"category_region": "XX"},
                  headers=H).status_code == 400)
client.post("/api/settings", json={"category_region": "uk"}, headers=H)
check("and remembers one that does", client.get("/api/settings").get_json()
      ["category_region"] == "UK")
job = "job-reach"
with gui.JOBS_LOCK:
    gui.JOBS[job] = {"out_dir": folder("reach"), "files": []}
answer = client.get(f"/api/case?job={job}").get_json()
check("a case opened then is a UK case, and the page is told so",
      answer["region"]["id"] == "UK" and answer["uncategorized"] == 0
      and answer["categories"][0]["name"] == "SC Category A")
page = open(os.path.join(PROJECT, "fme", "web", "ui", "app.js"), encoding="utf-8").read()
html = open(os.path.join(PROJECT, "fme", "web", "ui", "page.html"), encoding="utf-8").read()
check("Settings has the picker and sends it",
      'id="set-region"' in html and 'category_region: $("set-region").value' in page)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
