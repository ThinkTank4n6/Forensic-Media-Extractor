"""
fileindex.py -- a job's files as a table, so a page of them is a query.

The gallery asks for one page at a time (`/api/files`): filtered, sorted,
counted, `limit`/`offset`. That endpoint used to copy the job's whole list
under the job lock on every request -- 200,000 rows to hand over 500 -- and
filter it in Python. Worse, its category filter compared each row's `grade`,
a field no job row has ever carried, so filtering by category returned
nothing at all, and said nothing. Gradings live in `case.db`, keyed by SHA-256,
not in the job's rows.

Here the rows are kept in an in-memory SQLite table, and `case.db` is
attached read-only, so a category filter is a join against the examiner's
actual decisions -- the same table the details pane reads -- rather than a
copy of them that can fall behind. The pattern is GLEAPP's gallery query
(MIT; fme/vendor/LICENSE-gleapp): one SQL path whether the run is live or
finished, every column a caller can name checked against a list, and the
count kept until something it depends on changes.

**Staying in step with the job.** The job's list is never edited in place;
each update replaces it with a longer one, or with a new list when a run
restarts. So `sync` adds the tail when the list it was given starts with
what is already indexed, and rebuilds otherwise. It checks the last indexed
row, not only the length, because a restarted run can reach the same length
with different files.

**Every name is checked.** Values are bound as parameters. Column names and
sort direction cannot be, so they come only from `SORTS`, and the SQL text is
built from constants. Nothing a request sends is ever spliced into SQL.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional
from fme.engines import timeutil

__version__ = "1.0.0"

UNCATEGORIZED = 8          # fme.review.casedb.UNCATEGORIZED, without the import

# What a page can be sorted by: request name -> SQL expression. Anything else
# is refused, never passed through.
SORTS = {
    "order": "seq",                     # the order files were recovered
    "name": "rel_path COLLATE NOCASE",
    "size": "size",
    "type": "ext",
    "kind": "kind",
    "deleted": "deleted",
    "modified": "mtime",
    "captured": "captured",
    "grade": "grade",
}

_COLUMNS = ("seq", "rel_path", "kind", "ext", "size", "deleted", "sha256",
            "source_path", "mtime", "captured", "container")

# How many counts are kept. A count depends on the filter and on what has
# changed, so only the latest few are ever wanted again.
_COUNT_CACHE = 32


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class FileIndex:
    """One job's files. Thread-safe; every method takes the index's own lock."""

    def __init__(self) -> None:
        # Re-entrant, so page() can hold it across the three steps below.
        self._lock = threading.RLock()
        # isolation_level=None: no transaction is ever opened behind this
        # code's back. Python's default opens one before the first INSERT and
        # leaves it open until commit() -- and an open transaction pins a read
        # snapshot of the attached case.db. The first version of this did
        # exactly that: after the first sync every query, cached or not, saw
        # the gradings as they were then, and grading a file changed nothing
        # on the page. tests/test_fileindex.py grades mid-test to catch it.
        self._con = sqlite3.connect(":memory:", check_same_thread=False,
                                    uri=True, isolation_level=None)
        self._con.execute(
            "CREATE TABLE files (seq INTEGER PRIMARY KEY, rel_path TEXT, "
            "kind TEXT, ext TEXT, size INTEGER, deleted INTEGER, sha256 TEXT, "
            "source_path TEXT, mtime TEXT, captured TEXT, container INTEGER)")
        for column in ("kind", "sha256", "deleted"):
            self._con.execute(f"CREATE INDEX i_{column} ON files ({column})")
        self._indexed = 0
        self._last = None          # rel_path of the last indexed row
        self._generation = 0
        self._case_path = ""
        self._uncategorized = UNCATEGORIZED
        self._counts: dict = {}
        self.rebuilds = 0          # for the tests: a tail sync is not a rebuild

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # -- keeping up with the job -------------------------------------------
    def sync(self, rows: list) -> None:
        """Bring the table level with the job's current list."""
        with self._lock:
            continues = (self._indexed <= len(rows) and
                         (self._indexed == 0 or
                          rows[self._indexed - 1].get("rel_path") == self._last))
            if continues and self._indexed == len(rows):
                return
            # One explicit transaction, committed before the lock is released:
            # fast (one journal write, not one per row) and never left open.
            # The counters change only after COMMIT, so a failed insert leaves
            # the table and what this object believes about it in agreement.
            start = self._indexed if continues else 0
            self._con.execute("BEGIN")
            try:
                if not continues:
                    self._con.execute("DELETE FROM files")
                self._insert(rows, start)
                self._con.execute("COMMIT")
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            if not continues:
                self.rebuilds += 1
            self._indexed = len(rows)
            self._last = rows[-1].get("rel_path") if rows else None
            self._generation += 1

    def _insert(self, rows: list, start: int) -> None:
        tail = rows[start:]
        if tail:
            self._con.executemany(
                f"INSERT INTO files ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                [(start + i, r.get("rel_path") or "", r.get("category") or "",
                  r.get("ext") or "", int(r.get("size") or 0),
                  1 if r.get("deleted") else 0, r.get("sha256") or "",
                  r.get("source_path") or r.get("source") or "",
                  timeutil.stored(r, "mtime"), timeutil.stored(r, "captured"),
                  1 if r.get("container") else 0)
                 for i, r in enumerate(tail)])

    def attach_case(self, case_path: str) -> bool:
        """Read gradings from this case.db. Read-only: the index never writes
        to a case. Returns whether the case is attached."""
        with self._lock:
            case_path = os.path.abspath(case_path) if case_path else ""
            if case_path == self._case_path:
                return bool(case_path)
            if self._case_path:
                self._con.execute("DETACH DATABASE c")
                self._case_path = ""
            if not case_path or not os.path.isfile(case_path):
                return False
            # Through ro_uri, as every read-only open is: built by hand, a
            # "#" in the case folder's name ended the path, ATTACH failed and
            # every file read as ungraded (v2026.11.317).
            from fme.sqlite_uri import ro_uri
            uri = ro_uri(case_path)
            try:
                self._con.execute("ATTACH DATABASE ? AS c", (uri,))
                self._con.execute("SELECT 1 FROM c.decisions LIMIT 1").fetchall()
            except sqlite3.Error:
                try:
                    self._con.execute("DETACH DATABASE c")
                except sqlite3.Error:
                    pass
                return False
            self._case_path = case_path
            # The case's own Uncategorized code (fme/review/regions.py): 8 in
            # a US case, 0 in most others. A file with no row is that.
            self._uncategorized = UNCATEGORIZED
            try:
                row = self._con.execute("SELECT value FROM c.meta WHERE "
                                        "key='category_region'").fetchone()
                if row:
                    from fme.review import regions
                    self._uncategorized = regions.get(row[0]).uncategorized
            except sqlite3.Error:
                pass
            self._counts.clear()
            return True

    # -- asking for a page --------------------------------------------------
    def page(self, rows: list, case_path: str, **query) -> dict:
        """sync, attach_case and query as one step, with the page's rows.

        Taken separately, another request could sync a different list
        between this one's sync and its query: positions from that list
        were then looked up in this request's own -- past its end (a 500)
        during a run, or naming other files after a restarted one.
        """
        with self._lock:
            self.sync(rows)
            self.attach_case(case_path)
            found = self.query(**query)
            found["files"] = [rows[i] for i in found["seqs"]]
            return found

    def query(self, *, kind: str = "", grade: Optional[int] = None,
              deleted: bool = False, text: str = "", sort: str = "order",
              descending: bool = False, limit: int = 500,
              offset: int = 0) -> dict:
        """One page: {"seqs": [...], "total": n, "offset": o}.

        `seqs` are positions in the list last given to `sync`, in page order.
        Raises ValueError for a sort it does not know.
        """
        if sort not in SORTS:
            raise ValueError(f"cannot sort by {sort!r}; one of: "
                             f"{', '.join(sorted(SORTS))}")
        with self._lock:
            graded = bool(self._case_path)
            where, params = [], []
            if kind:
                where.append("kind = ?")
                params.append(kind)
            if deleted:
                where.append("deleted = 1")
            if text:
                where.append("(rel_path LIKE ? ESCAPE '\\' OR "
                             "source_path LIKE ? ESCAPE '\\')")
                params += [_like(text), _like(text)]
            if grade is not None:
                # No decision row means not yet graded, which is the
                # Uncategorized code -- what the details pane shows too.
                where.append("grade = ?")
                params.append(int(grade))
            source = ("(SELECT f.*, COALESCE(d.category, ?) AS grade FROM files f "
                      "LEFT JOIN c.decisions d ON d.sha256 = f.sha256)"
                      if graded else "(SELECT *, ? AS grade FROM files)")
            head = [self._uncategorized]
            clause = (" WHERE " + " AND ".join(where)) if where else ""

            # The count is the expensive half on a large case, and it does not
            # change when only the page or the sort does. It is kept until the
            # files or the gradings change: data_version moves whenever another
            # connection -- the GUI's CaseDB -- commits to case.db.
            graded_version = (self._con.execute("PRAGMA c.data_version").fetchone()[0]
                              if graded else 0)
            key = (self._generation, graded_version, clause, tuple(params))
            total = self._counts.get(key)
            if total is None:
                total = self._con.execute(
                    f"SELECT COUNT(*) FROM {source}{clause}",
                    head + params).fetchone()[0]
                if len(self._counts) >= _COUNT_CACHE:
                    self._counts.clear()
                self._counts[key] = total

            # Past the end comes back to the last page: during a run the list
            # grows under the examiner, and a page valid a moment ago should
            # not come back empty.
            if offset >= total and total:
                offset = max(0, ((total - 1) // limit) * limit)
            order = "DESC" if descending else "ASC"
            seqs = [row[0] for row in self._con.execute(
                f"SELECT seq FROM {source}{clause} "
                f"ORDER BY {SORTS[sort]} {order}, seq {order} LIMIT ? OFFSET ?",
                head + params + [limit, offset])]
            return {"seqs": seqs, "total": total, "offset": offset}
