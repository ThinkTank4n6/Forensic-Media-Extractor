"""The hash store's counts stay true, and one failed write does not lock out
every other writer.

- A batch holding rows for more than one source moved each source's count
  by the rows asked for, not the rows that landed: writing the same rows
  again inflated every source in it.
- remove_source counted, deleted and moved the total in separate steps, the
  last a read and then a write, so another writer between them was lost
  from the total.
- A write that failed part way left its thread's connection holding the
  write lock; on a server whose threads persist, every other writer then
  failed with "database is locked".
- close() closed connections other threads were in the middle of using.
"""
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.review import hashstore  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_hashstore_integrity-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(' ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def counts(store):
    listed = {s["name"]: s["hashes"] for s in store.sources()}
    real = {r["name"]: r["n"] for r in store._con.execute(
        "SELECT s.name, COUNT(h.digest) AS n FROM sources s "
        "LEFT JOIN hashes h ON h.source_id = s.id GROUP BY s.id")}
    total = store._con.execute("SELECT COUNT(*) AS n FROM hashes").fetchone()["n"]
    return listed, real, store.size(), total


def labelled(prefix: str, n: int):
    return [{"md5": f"{prefix}{i:031x}", "label": "test"} for i in range(n)]


store = hashstore.HashStore(os.path.join(ROOT, "store.db"), lock_wait=1.0)

# 1. One batch, two sources, written twice.
store.contribute_rows(labelled("a", 1), "set-a")
store.contribute_rows(labelled("b", 1), "set-b")
ids = {r["name"]: r["id"] for r in store._con.execute("SELECT id, name FROM sources")}
batch = ([(f"c{i:031x}", "md5", ids["set-a"], None, "t") for i in range(5)]
         + [(f"d{i:031x}", "md5", ids["set-b"], None, "t") for i in range(3)])
store._write(batch)
store._con.commit()
# Again, with one new row for set-a: the old code moved set-a by 6 and
# set-b by 3 for the one row that landed.
store._write(batch + [(f"c{99:031x}", "md5", ids["set-a"], None, "t")])
store._con.commit()
listed, real, size, total = counts(store)
check("a two-source batch written twice: each source's count is what it holds",
      listed == real and size == total, f"listed {listed}, held {real}")

# 2. remove_source keeps the total true.
store.contribute_rows(labelled("e", 4), "set-e")
removed = store.remove_source("set-e")
listed, real, size, total = counts(store)
check("remove_source: rows removed and the total", removed == 4 and size == total,
      f"removed {removed}, total says {size}, table holds {total}")

# 3. A write that fails part way, on a thread that stays alive.
failed, go_on, done = threading.Event(), threading.Event(), threading.Event()
real_adjust = hashstore.HashStore._adjust_count


def broken_adjust(self, *args):
    if threading.current_thread().name == "server-thread":
        raise sqlite3.OperationalError("disk I/O error (simulated)")
    return real_adjust(self, *args)


def server_thread():
    try:
        store.contribute_rows(labelled("f", 3), "set-f")
    except sqlite3.OperationalError:
        failed.set()
    go_on.wait(30)             # a pooled server thread: alive, idle
    done.set()


# Fails between the INSERT and its commit.
hashstore.HashStore._adjust_count = broken_adjust
worker = threading.Thread(target=server_thread, name="server-thread")
worker.start()
failed.wait(30)
hashstore.HashStore._adjust_count = real_adjust
try:
    store.contribute_rows(labelled("9", 2), "set-9")
    locked_out = ""
except sqlite3.OperationalError as exc:
    locked_out = str(exc)
go_on.set()
worker.join(30)
check("after another thread's write failed, a contribution still lands",
      failed.is_set() and not locked_out, locked_out)

# 4. close() while another thread is part way through using its connection.
mid_query, closed, answer = threading.Event(), threading.Event(), {}


def reader_thread():
    con = store._con
    con.execute("SELECT 1").fetchone()
    mid_query.set()
    closed.wait(30)
    try:
        answer["n"] = con.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]
    except sqlite3.Error as exc:
        answer["error"] = str(exc)


reader = threading.Thread(target=reader_thread)
reader.start()
mid_query.wait(30)
store.close()
closed.set()
reader.join(30)
check("close() leaves another thread's query to finish",
      "n" in answer, answer.get("error", ""))

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
