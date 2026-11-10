"""
manifestdb.py -- the case's record of what was recovered, in SQLite.

`manifest.csv` was the record: a row appended per file, flushed, never
updated. Three things followed from a CSV being the record. A crash could
tear the last row in half, and every reader had to know how to tell a torn
row from a short value. A file's row could not change after it was written,
so what was learned later -- another place the same bytes were found, an
instance count, a carved file matched to a deleted entry -- reached the
record only at close, and a crash lost it. And the live gallery, resume and
the rebuild of an interrupted case each re-derived the truth from the CSV
plus `duplicates.csv` in their own way.

`manifest.db` holds the same records, and each change is one transaction:
a file and its row, or a duplicate and the update to the file it copies,
land together or not at all. It is the record. `manifest.csv`,
`duplicates.csv` and `manifest.json` are still written -- for people, Excel,
and every reader written before this -- but they are made from it.

The schema is deliberately plain: one JSON document per record, plus the
columns a lookup needs. A Record gains fields often; a column per field
would need a migration per release, and a JSON document does not.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Iterable, Optional

SCHEMA_VERSION = 1
FILENAME = "manifest.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- One row per stored file. seq is the order it was stored in, which is the
-- order every export lists it.
CREATE TABLE IF NOT EXISTS files (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    out_path TEXT NOT NULL UNIQUE,
    sha256   TEXT NOT NULL,
    record   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_sha256 ON files (sha256);
-- Every further place stored bytes were found. Not unique: the same bytes
-- in the same place can be reached by two engines.
CREATE TABLE IF NOT EXISTS duplicates (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL,
    record TEXT NOT NULL
);
"""


class ManifestDBError(RuntimeError):
    """manifest.db exists and cannot be read as one."""


def path_in(outdir: str) -> str:
    return os.path.join(outdir, FILENAME)


def exists(outdir: str) -> bool:
    return os.path.isfile(path_in(outdir))


class ManifestDB:
    """The record of one case's recovered files.

    One connection, used under a lock: the parallel carve stores from more
    than one thread, and SQLite connections are not to be shared unguarded.
    """

    def __init__(self, outdir: str, *, create: bool = True):
        self.path = path_in(outdir)
        if not create and not os.path.isfile(self.path):
            raise ManifestDBError(f"{self.path} does not exist")
        self._lock = threading.RLock()
        try:
            self._con = sqlite3.connect(self.path, check_same_thread=False,
                                        isolation_level=None)
            # WAL keeps a reader (the live gallery, a report) from blocking
            # the run and the run from blocking it. FULL makes each commit
            # durable before store() returns: the row exists exactly when
            # the file does, which is the point of this file.
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA synchronous=FULL")
            self._con.execute("PRAGMA busy_timeout=15000")
            self._con.executescript(_SCHEMA)
            found = self._con.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ManifestDBError(f"{self.path}: {exc}") from exc
        if found is None:
            self._con.execute("INSERT INTO meta VALUES ('schema_version', ?)",
                              (str(SCHEMA_VERSION),))
        elif int(found[0]) > SCHEMA_VERSION:
            raise ManifestDBError(
                f"{self.path} was written by a newer release (schema "
                f"{found[0]}, this one reads {SCHEMA_VERSION}); open the case "
                f"with that release")

    # -- writing ---------------------------------------------------------
    def _begin(self):
        self._con.execute("BEGIN IMMEDIATE")

    def put_file(self, record: dict) -> None:
        """Add a stored file, or replace its row (same out_path)."""
        with self._lock:
            self._begin()
            try:
                self._put(record)
                self._con.execute("COMMIT")
            except BaseException:
                self._con.execute("ROLLBACK")
                raise

    def _put(self, record: dict) -> None:
        text = json.dumps(record, ensure_ascii=False)
        self._con.execute(
            "INSERT INTO files (out_path, sha256, record) VALUES (?,?,?) "
            "ON CONFLICT(out_path) DO UPDATE SET sha256=excluded.sha256, "
            "record=excluded.record",
            (record["out_path"], record["sha256"], text))

    def add_duplicate(self, duplicate: dict, kept: Optional[dict]) -> None:
        """A further place stored bytes were found, and what it taught the
        kept file -- in one transaction, so a crash keeps both or neither."""
        with self._lock:
            self._begin()
            try:
                self._con.execute(
                    "INSERT INTO duplicates (sha256, record) VALUES (?,?)",
                    (duplicate["sha256"], json.dumps(duplicate, ensure_ascii=False)))
                if kept is not None:
                    self._put(kept)
                self._con.execute("COMMIT")
            except BaseException:
                self._con.execute("ROLLBACK")
                raise

    def replace_all(self, files: Iterable[dict], duplicates: Iterable[dict]) -> None:
        """Make the record exactly these rows (an import, or a resume that
        settled what an older case held)."""
        with self._lock:
            self._begin()
            try:
                self._con.execute("DELETE FROM files")
                self._con.execute("DELETE FROM duplicates")
                for rec in files:
                    self._put(rec)
                for dup in duplicates:
                    self._con.execute(
                        "INSERT INTO duplicates (sha256, record) VALUES (?,?)",
                        (dup["sha256"], json.dumps(dup, ensure_ascii=False)))
                self._con.execute("COMMIT")
            except BaseException:
                self._con.execute("ROLLBACK")
                raise

    def remove_file(self, out_path: str) -> None:
        with self._lock:
            self._con.execute("DELETE FROM files WHERE out_path=?", (out_path,))

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE "
                "SET value=excluded.value", (key, value))

    # -- reading ---------------------------------------------------------
    def meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._con.execute("SELECT value FROM meta WHERE key=?",
                                    (key,)).fetchone()
        return row[0] if row else default

    def files(self, after_seq: int = 0) -> list:
        """(seq, record) for every stored file after after_seq, in order."""
        with self._lock:
            rows = self._con.execute(
                "SELECT seq, record FROM files WHERE seq > ? ORDER BY seq",
                (after_seq,)).fetchall()
        return [(seq, json.loads(text)) for seq, text in rows]

    def duplicates(self) -> list:
        with self._lock:
            rows = self._con.execute(
                "SELECT record FROM duplicates ORDER BY seq").fetchall()
        return [json.loads(text) for (text,) in rows]

    def count(self) -> int:
        with self._lock:
            return self._con.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            try:
                # Fold the WAL back in, so the case folder is one file a
                # copy or a zip cannot catch half-written.
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._con.close()


def read(outdir: str) -> tuple:
    """(files, duplicates) as record dicts, for a reader that is not the run.

    Opened read-only, so a report or a rebuild can never change a case's
    record, and a missing database is not created by looking for it.
    """
    path = path_in(outdir)
    if not os.path.isfile(path):
        raise ManifestDBError(f"{path} does not exist")
    try:
        from fme.sqlite_uri import ro_uri  # lazy: nothing else here imports fme
        con = sqlite3.connect(ro_uri(path), uri=True)
        try:
            files = [json.loads(t) for (t,) in con.execute(
                "SELECT record FROM files ORDER BY seq")]
            dups = [json.loads(t) for (t,) in con.execute(
                "SELECT record FROM duplicates ORDER BY seq")]
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise ManifestDBError(f"{path}: {exc}") from exc
    return files, dups
