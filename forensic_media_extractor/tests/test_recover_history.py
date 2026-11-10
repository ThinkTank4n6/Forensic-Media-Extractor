"""recover --undo puts files back to what their whole history says they were.

It replayed only "categorize" rows from the audit log. An undo in the
interface logged one summary row naming no files, a clear and a deleted
category were not replayed, and a version-1 case's codes were replayed as
written -- so a file graded CAM, undone, then graded Adult was "recovered"
to CAM, a grade nobody had left on it. It also wrote the files back one
category at a time, each committed, and stamped every one with "recovered
to ..." as its examiner and the minute of recovery as its time.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.review import casedb, recover  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "recover_history-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(what, passed):
    global ok
    ok &= bool(passed)
    print(f"  {what}: {bool(passed)}")


def fresh(name, examiner="Det. A"):
    folder = os.path.join(ROOT, name)
    os.makedirs(folder)
    return folder, casedb.CaseDB(os.path.join(folder, "case.db"), examiner=examiner)


def last_event(folder):
    return recover.events(folder)[-1]["number"]


def decision(folder, sha):
    case = casedb.CaseDB(os.path.join(folder, "case.db"))
    try:
        with case._lock:
            row = case._con.execute(
                "SELECT category, categorized_by, categorized_at FROM decisions "
                "WHERE sha256=?", (sha,)).fetchone()
        return dict(row) if row else None
    finally:
        case.close()


X, Y, Z, W, P, Q, R, S = (f"{i:064x}" for i in range(1, 9))

print("graded, undone in the interface, graded again")
folder, case = fresh("undo")
case.set_category([X], 1)
case.undo()
case.set_category([X], 5)
case.close()
recover.undo(folder, last_event(folder))
check("the file goes back to no decision, not to CAM", decision(folder, X) is None)

print("\ngraded, cleared, graded again")
folder, case = fresh("clear")
case.set_category([Y], 2)
case.clear_decisions([Y])
case.set_category([Y], 5)
case.close()
recover.undo(folder, last_event(folder))
check("the file goes back to no decision", decision(folder, Y) is None)

print("\ngraded into a category that was then deleted")
folder, case = fresh("deleted")
mine = case.add_category("Series X")
case.set_category([Z], mine)
case.remove_category(mine)
case.set_category([Z], 5)
logged = [r for r in case.audit(20) if r["action"] == "uncategorize"]
case.close()
check("the deletion names each file it changed", len(logged) == 1
      and logged[0]["sha256"] == Z)
recover.undo(folder, last_event(folder))
back = decision(folder, Z)
check("the file goes back to uncategorized, as the deletion left it",
      back is not None and back["category"] == casedb.UNCATEGORIZED)

print("\na case graded under the version-1 scheme")
folder, case = fresh("scheme1")
case.set_category([W], 5)          # in version 1, 5 meant Non-pertinent
with case._lock:
    case._con.execute("UPDATE meta SET value='1' WHERE key='scheme_version'")
    case._con.commit()
case.close()
case = casedb.CaseDB(os.path.join(folder, "case.db"), examiner="Det. A")
migrated = case.decision(W)["category"]
case.set_category([W], 1)
case.close()
check("opening it moved the file to Non-Pertinent (0)", migrated == 0)
recover.undo(folder, last_event(folder))
check("recovery returns it to 0, not to the 5 the old log says",
      decision(folder, W)["category"] == 0)

print("\nwho graded it, and when, survive recovery")
folder, case = fresh("who", examiner="Det. A")
case.set_category([P], 2)
original = case.decision(P)
case.close()
case = casedb.CaseDB(os.path.join(folder, "case.db"), examiner="Det. B")
case.set_category([P], 5)
case.close()
result = recover.undo(folder, last_event(folder), examiner="Det. C")
back = decision(folder, P)
check("the grade is back", back["category"] == 2)
check("graded by the original examiner, not 'recovered to ...'",
      back["categorized_by"] == "Det. A")
check("at the original time", back["categorized_at"] == original["categorized_at"])
case = casedb.CaseDB(os.path.join(folder, "case.db"))
trail = case.audit(20)
undoable = case.undoable()
case.close()
check("the recovery is in the audit log, by whoever ran it",
      any(r["action"] == "recover-restore" and r["sha256"] == P
          and r["who"] == "Det. C" and "recovered" in r["detail"] for r in trail))
check("and can be taken back from the interface",
      undoable and undoable[0]["action"] == "recover")
check("a snapshot was left", os.path.isfile(result["snapshot"]))

print("\nall or nothing")
folder, case = fresh("atomic")
mine = case.add_category("Series Y")
case.set_category([Q], mine)
case.set_category([R], 1)
case.set_category([Q, R], 5)
case.remove_category(mine)         # Q is 5 by now, so nothing moves
case.close()
event = last_event(folder)
try:
    recover.undo(folder, event)
    refused = False
except ValueError:
    refused = True
check("a target category the case no longer has is refused", refused)
check("and neither file was changed",
      decision(folder, Q)["category"] == 5 and decision(folder, R)["category"] == 5)
check("and no snapshot was taken",
      not os.path.isdir(os.path.join(folder, "backups")))

print("\nan undo from an older version, which logged no files")
folder, case = fresh("legacy")
case.set_category([S], 1)
case.undo()
case.set_category([S], 5)
case.close()
con = sqlite3.connect(os.path.join(folder, "case.db"))
con.execute("DELETE FROM audit WHERE action='undo-restore'")
con.execute("UPDATE audit SET detail=replace(detail, ' [per file]', '') "
            "WHERE action='undo'")
con.commit()
con.close()
event = last_event(folder)
before = recover.state_before(folder, event)["before"]
check("its files are found from the undo record still in the case",
      before == {S: None})
con = sqlite3.connect(os.path.join(folder, "case.db"))
con.execute("DELETE FROM undo_items")
con.execute("DELETE FROM undo_batches")
con.commit()
con.close()
try:
    recover.undo(folder, event)
    refused = False
except ValueError as exc:
    refused = "--accept-unattributed-undo" in str(exc)
check("with that record pruned, recovery refuses rather than guess", refused)
check("and changed nothing", decision(folder, S)["category"] == 5)
recover.undo(folder, event, accept_unattributed=True)
check("unless told to go ahead without it", decision(folder, S)["category"] == 1)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
