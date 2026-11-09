"""The case database keeps its decisions straight under failure (v2026.11.318).

What a review of v2026.11.317 found, each reproduced before it was fixed:

1. undo put a file back into a category since deleted -- graded with a code
   no category had;
2. undo of an older batch, named by number, wiped out what was decided
   about its files after it;
3. a write that failed part way left its transaction open on the shared
   connection: a phantom undo batch, and "database is locked" for others;
4. a build from before regional schemes renamed a UK case's locked
   categories to the US names when it opened the case;
5. the file index, given a case folder with "#" in its name, read every
   file as ungraded;
6. a Project VIC export whose case.db could not be read went out with every
   Category null, and nothing said.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.review import casedb, fileindex  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_case_integrity-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True
A, B = "a" * 64, "b" * 64


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def fresh(name, region=None):
    path = os.path.join(ROOT, name)
    os.makedirs(path, exist_ok=True)
    return casedb.open_case(path, "Det. Test", "1", region=region)


print("undo")
case = fresh("undo")
code = case.add_category("Series X")
case.set_category(A, code)
case.set_category(A, 5)
case.remove_category(code)
answer = case.undo()
check("does not put a file back into a deleted category",
      answer["undone"] == 0 and "no longer has" in answer["reason"]
      and case.decision(A)["category"] in (5, case.uncategorized), (answer, case.decision(A)))
case.set_category(B, 1)
first = case.undoable(1)[0]["batch_id"]
case.set_category(B, 5)
answer = case.undo(first)
check("an older batch does not overwrite what was decided after it",
      answer["undone"] == 0 and case.decision(B)["category"] == 5, answer)
check("the newest one still undoes", case.undo()["undone"] == 1
      and case.decision(B)["category"] == 1)

print("\na write that fails part way")
batches = len(case.undoable(50))
try:
    case.set_category([A, None], 1)
except Exception:  # noqa: BLE001
    pass
check("leaves no transaction open", not case._con.in_transaction)
other = sqlite3.connect(case.path, timeout=0.5)
try:
    other.execute("INSERT INTO meta (key, value) VALUES ('probe', '1')")
    other.commit()
    locked = False
except sqlite3.OperationalError:
    locked = True
other.close()
check("and does not hold case.db's write lock", not locked)
case.set_note(A, "next unrelated write")
check("and leaves no phantom undo batch for the next write to commit",
      len(case.undoable(50)) == batches, (batches, len(case.undoable(50))))
case.close()

print("\nan older build opening a UK case")
uk = fresh("uk", "UK")
uk.close()
con = sqlite3.connect(os.path.join(ROOT, "uk", "case.db"))
# What every build before regions runs on opening a case: the US wording
# written over each locked row.
for code_, _key, name, color in casedb.CATEGORIES:
    con.execute("UPDATE categories SET name=?, color=? WHERE code=? AND locked=1",
                (name, color, code_))
con.commit()
names = dict(con.execute("SELECT code, name FROM categories"))
needs = con.execute("SELECT value FROM meta WHERE key='min_reader_build'").fetchone()
con.close()
check("leaves the UK names alone", names.get(1) == "SC Category A", names.get(1))
check("and the case records the build it needs", needs and int(needs[0]) >= 318, needs)
uk = casedb.open_case(os.path.join(ROOT, "uk"))
check("while this build still treats them as locked",
      all(c["locked"] for c in uk.categories()))
try:
    uk.rename_category(1, "x")
    renamed = True
except ValueError:
    renamed = False
check("and refuses to rename one", not renamed)
uk.close()

print("\na case folder with # in its name")
hashed = fresh("Case #12")
hashed.set_category(A, 1)
hashed.close()
index = fileindex.FileIndex()
index.sync([{"rel_path": "a.jpg", "category": "image", "ext": "jpg", "size": 1,
             "sha256": A}])
page = index.page([{"rel_path": "a.jpg", "category": "image", "ext": "jpg", "size": 1,
                    "sha256": A}], os.path.join(ROOT, "Case #12", "case.db"), grade=1)
check("the file index reads its gradings", page.get("total") == 1, page.get("total"))
index.close()

print("\na Project VIC export whose case cannot be read")
import json  # noqa: E402
from fme.exports import projectvic_export  # noqa: E402
vics = os.path.join(ROOT, "vics")
os.makedirs(vics)
with open(os.path.join(vics, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "x"},
               "files": [{"sha256": A, "md5": A[:32], "sha1": A[:40], "out_path": "a.jpg",
                          "ext": "jpg", "category": "image", "size": 1,
                          "source_path": "/a.jpg"}]}, fh)
with open(os.path.join(vics, "case.db"), "wb") as fh:
    fh.write(b"this is not a database" * 100)
try:
    projectvic_export.export(os.path.join(vics, "manifest.json"),
                             os.path.join(ROOT, "out.json"), case_number="1")
    said = ""
except RuntimeError as exc:
    said = str(exc)
check("is refused, and says why, rather than sent with no categories",
      "could not be read" in said and not os.path.exists(os.path.join(ROOT, "out.json")),
      said[:80])

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
