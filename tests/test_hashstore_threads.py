"""Threads sharing one hash store keep its counts right and its reads whole.

The store opened one SQLite connection and every thread of the hash server
(and of the GUI) used it, with nothing serializing them. On one connection
there is no isolation between threads: the running totals were read and
rewritten by several threads inside one another's transactions, and
measured on v2026.11.271 eight threads adding 1,600 hashes left the store
reporting 4,633. Each thread now has its own connection, and SQLite
queues the writers.
"""
import hashlib
import logging
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.review import hashstore  # noqa: E402


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_hashstore_threads-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    store = hashstore.HashStore(os.path.join(root, "store.db"), lock_wait=5)
    store._con.execute("INSERT INTO sources (name, kind, path, added_at, note) "
                       "VALUES ('set', 'test', '', '', '')")
    store._con.commit()
    source = store._con.execute("SELECT id FROM sources").fetchone()["id"]

    added = []

    def contribute(n: int):
        rows = [(hashlib.md5(f"{n}-{i}".encode()).hexdigest(), "md5", source, "1", "", "")
                for i in range(200)]
        store._write(rows)
        store._con.commit()
        added.append(len(rows))

    threads = [threading.Thread(target=contribute, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    total = store.size()
    right = total == 1600 == store.recount()
    ok &= right
    print(f"eight threads adding at once leave the count right: {right} ({total})")

    errors = []

    def lookups():
        try:
            for _ in range(200):
                store.lookup([hashlib.md5(f"3-{i}".encode()).hexdigest()
                              for i in range(50)])
                store.size()
        except Exception as exc:  # noqa: BLE001 - what the check counts
            errors.append(repr(exc))

    threads = [threading.Thread(target=lookups) for _ in range(4)] + \
        [threading.Thread(target=contribute, args=(n,)) for n in range(8, 12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    calm = not errors and store.size() == 2400 == store.recount()
    ok &= calm
    print(f"lookups alongside contributions raise nothing and count right: {calm}"
          + (f" ({errors[0]})" if errors else f" ({store.size()})"))
    store.close()

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
