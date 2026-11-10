"""Hash-server accounts: no silent overwrite, safe under load, times with a zone.

- Adding a name that already had an account replaced that person's role and
  token without a word, and left a disabled account disabled with a token
  nobody could use. It is now refused unless a re-issue is asked for.
- identify() read the shared connection without the lock every write
  takes, so a sign-in alongside a write could fail at random.
- Times were local with no zone, ambiguous for the hour the clocks go back.
"""
import datetime as dt
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.review import casedb, hashserver

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_accounts-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, passed):
    global ok
    ok &= bool(passed)
    print(f"   {label}: {bool(passed)}")


accounts = hashserver.Accounts(os.path.join(ROOT, "users.db"))
first = accounts.add("jsmith", "admin")
print("adding a name already in use")
try:
    accounts.add("jsmith", "read")
    refused = False
except ValueError as exc:
    refused = "already has an account" in str(exc)
check("is refused", refused)
check("and the existing person keeps their role and token",
      (accounts.identify(first) or {}).get("role") == "admin")
accounts.disable("jsmith")
second = accounts.add("jsmith", "read", reissue=True)
check("a re-issue gives a working token with the new role",
      (accounts.identify(second) or {}).get("role") == "read")
check("enables the account it re-issues", accounts.identify(second) is not None)
check("and the old token stops working", accounts.identify(first) is None)

print("\nsign-ins while the access log is being written")
errors = []


def sign_in():
    try:
        for _ in range(300):
            accounts.identify(second)
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)


def log():
    try:
        for i in range(300):
            accounts.record("jsmith", "lookup", f"{i}", "127.0.0.1")
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)


threads = [threading.Thread(target=sign_in) for _ in range(6)] + \
          [threading.Thread(target=log) for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check(f"no sign-in or write failed ({len(errors)} errors)", not errors)

print("\ntimes")
stamp = hashserver._now()
check(f"carry their offset ({stamp})", dt.datetime.fromisoformat(stamp).tzinfo is not None)
with accounts._lock:
    accounts._con.execute("INSERT INTO access (at, who, action, detail, address) "
                          "VALUES ('2025-01-01T09:00:00','old','lookup','','')")
    accounts._con.commit()
try:
    accounts.usage()
    mixed = True
except Exception:  # noqa: BLE001
    mixed = False
check("a log mixing older zoneless rows with new ones still adds up", mixed)
case = casedb.CaseDB(os.path.join(ROOT, "case.db"), examiner="t") \
    if hasattr(casedb, "CaseDB") else None
if case is not None:
    with case._lock:
        case._con.execute("INSERT INTO meta (key, value) VALUES ('last_snapshot', "
                          "'2025-01-01T09:00:00') ON CONFLICT(key) DO UPDATE SET "
                          "value=excluded.value")
        case._con.commit()
    try:
        case.autosnapshot()
        snap = True
    except Exception:  # noqa: BLE001
        snap = False
    check("a case's snapshot timer reads an older zoneless time", snap)
check(f"case times carry their offset too ({casedb._now()})",
      dt.datetime.fromisoformat(casedb._now()).tzinfo is not None)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
