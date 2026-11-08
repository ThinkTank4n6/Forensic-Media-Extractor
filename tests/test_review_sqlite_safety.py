"""The review databases are opened safely for where they are, and shared safely.

- A hash store import ran with synchronous=OFF, in a file that also holds
  the unit's contributions; a crash could corrupt all of it. It is NORMAL
  under WAL and stays FULL under a rollback journal.
- case.db and cases.db turned WAL on unconditionally; on a network share
  WAL is how a SQLite file gets "disk I/O error" or corrupted. They now use
  a rollback journal there, as the hash store does.
- CaseRegistry.cases() and get() read the connection the GUI's threads share
  without the lock its writers take.
- galleryquery's caches were keyed on id(list), which is reused once a list
  is collected, and case_order cleared a shared dict before refilling it.
"""
import gc
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fme.review import casedb, galleryquery, hashstore, registry  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "review_sqlite_safety-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(what, passed):
    global ok
    ok &= bool(passed)
    print(f"  {what}: {bool(passed)}")


set_file = os.path.join(ROOT, "set.txt")
with open(set_file, "w") as fh:
    fh.write("\n".join(f"{i:032x}" for i in range(1, 50)) + "\n")


def synchronous_during_import(store):
    seen = []
    real = store._write

    def spy(rows, fill=False):
        seen.append(store._con.execute("PRAGMA synchronous").fetchone()[0])
        return real(rows, fill)

    store._write = spy
    store.import_set(set_file, kind="custom", name=f"set{len(seen)}{id(store)}")
    return set(seen)


real_is_network = hashstore.is_network_path

print("a hash store import")
store = hashstore.HashStore(os.path.join(ROOT, "local.db"))
levels = synchronous_during_import(store)
check("on a local disk (WAL) it writes with synchronous NORMAL, never OFF",
      levels == {1})
store.close()

hashstore.is_network_path = lambda path: True
try:
    store = hashstore.HashStore(os.path.join(ROOT, "share.db"))
    mode = store._con.execute("PRAGMA journal_mode").fetchone()[0]
    levels = synchronous_during_import(store)
    check("on a share (rollback journal) it stays FULL",
          mode == "delete" and levels == {2})
    store.close()

    print("\ncase.db and cases.db on a share")
    case = casedb.CaseDB(os.path.join(ROOT, "share_case", "case.db"))
    with case._lock:
        mode = case._con.execute("PRAGMA journal_mode").fetchone()[0]
    case.close()
    check("case.db uses a rollback journal", mode == "delete")
    book = registry.CaseRegistry(os.path.join(ROOT, "share_cases.db"))
    mode = book._con.execute("PRAGMA journal_mode").fetchone()[0]
    book.close()
    check("cases.db uses a rollback journal", mode == "delete")
finally:
    hashstore.is_network_path = real_is_network

case = casedb.CaseDB(os.path.join(ROOT, "local_case", "case.db"))
with case._lock:
    mode = case._con.execute("PRAGMA journal_mode").fetchone()[0]
case.close()
check("and WAL on a local disk", mode == "wal")

print("\nthe case list, shared by the GUI's threads")
book = registry.CaseRegistry(os.path.join(ROOT, "cases.db"))
book.register(os.path.join(ROOT, "local_case"), case_number="DC26-1")
for reader in ("cases", "get"):
    book._lock.acquire()
    started = time.time()
    done = threading.Event()

    def read():
        if reader == "cases":
            book.cases()
        else:
            book.get(os.path.join(ROOT, "local_case"))
        done.set()

    worker = threading.Thread(target=read)
    worker.start()
    waited = not done.wait(0.3)
    book._lock.release()
    worker.join(5)
    check(f"{reader}() waits for a writer holding the lock",
          waited and done.is_set())
book.close()

print("\nthe gallery's caches")
first = [{"rel_path": f"a/{i}"} for i in range(5)]
galleryquery.case_order(first)
del first
gc.collect()
# The next list of the same length is likely to be given the freed list's
# id; it must still get its own order.
second = [{"rel_path": f"b/{i}"} for i in range(5)]
order = galleryquery.case_order(second)
check("a new list of the same length gets its own order",
      order == {f"b/{i}": i for i in range(5)})

cache = galleryquery.Cache()
old = [{"rel_path": "old"}]
cache._get("visible", old, 1, {}, lambda: ["old answer"])
del old
gc.collect()
new = [{"rel_path": "new"}]
answer = cache._get("visible", new, 1, {}, lambda: ["new answer"])
check("the filtered-list cache answers the list it is asked about",
      answer == ["new answer"])

lists = [[{"rel_path": f"{n}/{i}"} for i in range(200)] for n in range(4)]
errors = []


def hammer(n):
    try:
        for _ in range(300):
            mine = lists[n % 4]
            if galleryquery.case_order(mine).get(f"{n % 4}/199") != 199:
                errors.append("wrong order")
    except Exception as exc:  # noqa: BLE001
        errors.append(repr(exc))


threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("eight threads asking for different lists' orders never see a "
      "half-built one", not errors)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
