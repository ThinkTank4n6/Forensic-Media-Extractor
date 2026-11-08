"""
hashstore.py -- hash sets that outlive the case.

Loading a national set into memory for one run and throwing it away was
always wrong: the NSRL is tens of millions of hashes, and an office's own
list of what it has seen before is the most valuable set it owns. Both belong
somewhere permanent that every case consults.

This is that place: one SQLite file, imported into once and matched against
from then on.

    python -m fme.review.hashstore --store D:\\hashes\\store.db \\
        --import NSRLFile.txt --kind nsrl
    python -m fme.review.hashstore --store D:\\hashes\\store.db --list
    python -m fme.review.hashstore --store D:\\hashes\\store.db \\
        --scan out/manifest.json

and, after a review, putting what this case learned back:

    python -m fme.review.hashstore --store D:\\hashes\\store.db \\
        --contribute out --source "DC26-253"

Hashes are stored lowercase, one row per digest per source, so the same file
appearing in the NSRL and in your own list is two rows and a match reports
both. A match is never a conclusion -- it says which set a hash is in and
what that set recorded about it, nothing more.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

from fme.sqlite_uri import ro_uri
from fme.review import hashsets

LOG = logging.getLogger(__name__)

__version__ = "1.0.0"

# How often a long import folds its write-ahead log back in. Large enough
# that the cost is lost in the import, small enough that stopping the
# program never leaves tens of gigabytes to recover.
CHECKPOINT_EVERY = 2_000_000

BATCH = 50_000

# The kinds of set a store holds. Anything else was read as a text list, so a
# misspelled kind imported a Project VIC set with its categories thrown away.
KINDS = ("nsrl", "projectvic", "custom")

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    -- Kept here so the admin pages and Verify never have to join the
    -- whole hashes table to work out what each set holds.
    hash_count INTEGER,
    -- Who a case source belongs to: the person whose contribution made it.
    -- Without it any contributor could name another examiner's case and
    -- regrade every hash in it. '' is a case made without a signed-in
    -- person (the GUI writing to the file directly); only an administrator
    -- may contribute to one over the server. NULL is an imported set.
    owner TEXT,
    -- What an imported set holds, by category, worked out when it was
    -- imported: {"records": {cat: n}, "hashes": {cat: n}} as JSON. Kept so
    -- the Sets page can say "212,004 files, all Non-Pertinent" or "33 CAM"
    -- without grouping tens of millions of rows each time it is opened.
    -- NULL for a case source, whose gradings move with every contribution.
    categories TEXT);

-- Every grade a contribution changed, old and new, in the same transaction
-- as the change. A regrade replaces the only record of the earlier grade,
-- and a grade that feeds a court case must be able to say what it was
-- before, who changed it and when.
CREATE TABLE IF NOT EXISTS grade_changes (
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    digest TEXT NOT NULL,
    old_category INTEGER,
    old_label TEXT NOT NULL DEFAULT '',
    new_category INTEGER,
    new_label TEXT NOT NULL DEFAULT '',
    by TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS hashes (
    digest TEXT NOT NULL,
    algo TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    category INTEGER,
    note TEXT NOT NULL DEFAULT '',
    -- The rest of the record the set carried, as JSON. A Project VIC entry
    -- is not just a hash and a category: MediaID, size, when it was last
    -- updated, whether a victim or offender was identified, the tags and the
    -- series are the reasons an examiner consults the set at all, and
    -- throwing them away leaves a match that cannot be acted on.
    meta TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (digest, source_id)) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS hashes_digest ON hashes(digest);
CREATE INDEX IF NOT EXISTS hashes_source ON hashes(source_id);
"""


# Defined once, in hashsets, which both the store and a set loaded for the
# session read records through.
VIC_FIELDS = hashsets.VIC_FIELDS


def _algo_of(digest: str) -> str:
    return {32: "md5", 40: "sha1", 64: "sha256"}.get(len(digest), "")


def _now() -> str:
    # With the offset written in, as the server's own log does. A regrade's
    # time (grade_changes.at) without one is ambiguous for the hour the
    # clocks go back, in the record of who changed a hash's grade and when.
    import datetime as _dt
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


# How long to wait for another writer before giving up. An import of a
# national set is the thing being waited for, and those take minutes.
LOCK_WAIT = 300.0


class StoreMissing(FileNotFoundError):
    """There is no store at that path, and none was asked to be made."""


class StoreUnreachable(RuntimeError):
    """The store could not be opened, with a reason a person can act on."""


class NotYourSource(ValueError):
    """A contribution named a case source another person owns.

    A ValueError, so a caller that already turns refusals into a message
    still does; the server answers it with 403 rather than 400.
    """


def is_network_path(path: str) -> bool:
    """Is this file on a share rather than a disk in this machine?

    A UNC path is obvious. A mapped drive letter is not, and is the usual
    way an examiner reaches a share on Windows, so the drive type is asked
    for directly.
    """
    # Checked as given, before any normalising: a UNC path put through
    # abspath on a machine that does not understand one comes out as
    # something else entirely, and the share is then invisible.
    raw = str(path)
    if raw.startswith("\\\\") or raw.startswith("//"):
        return True
    path = os.path.abspath(path)
    if path.startswith("\\\\") or path.startswith("//"):
        return True
    if os.name != "nt":
        return False
    try:
        import ctypes

        drive = os.path.splitdrive(path)[0]
        if not drive:
            return False
        # DRIVE_REMOTE is 4.
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4
    except Exception:  # noqa: BLE001 - a failed guess is not a failure
        return False


def _missing_message(path: str) -> str:
    """Say there is no store there -- and name the ones that are.

    The usual cause is two names for the same intention: the import wrote
    hash_store.db and the path typed here says hashes.db. Listing what is
    actually in that folder turns a dead end into an answer.
    """
    folder = os.path.dirname(path) or "."
    nearby = []
    try:
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".db"):
                continue
            candidate = os.path.join(folder, name)
            if not os.path.isfile(candidate) or candidate == path:
                continue
            if looks_like_store(candidate):
                nearby.append((candidate, os.path.getsize(candidate)))
    except OSError:
        pass

    message = [f"There is no hash store at {path}."]
    if nearby:
        message.append("")
        message.append("These are in that folder and are hash stores:")
        for candidate, size in nearby[:6]:
            message.append(f"  {candidate}  ({size / 1048576:.1f} MB)")
        message.append("")
        message.append("A store is the file named when its first set was "
                       "imported (in the Hash store tab, or with --store on "
                       "the command line), so hash_store.db and hashes.db "
                       "are two different stores.")
    else:
        message.append("")
        message.append("Nothing has been imported there. Import a set to "
                       "start a store at this path, or check the path the "
                       "import actually wrote to -- the name is whatever was "
                       "passed to --store.")
    return "\n".join(message)


def looks_like_store(path: str) -> bool:
    """Is this file one of ours, rather than any other SQLite database?"""
    try:
        with open(path, "rb") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                return False
        con = sqlite3.connect(ro_uri(path), uri=True)
        try:
            names = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        return {"hashes", "sources"} <= names
    except (OSError, sqlite3.Error):
        return False


def _explain_open_failure(path: str, exc: Exception) -> str:
    """Say what went wrong in terms of what to do about it."""
    text = str(exc).lower()
    where = path if is_network_path(path) else os.path.abspath(path)
    if "database is locked" in text or "database is busy" in text:
        return (
            f"The store at {where} is busy and stayed busy for "
            f"{int(LOCK_WAIT / 60)} minutes.\n\n"
            f"SQLite allows one writer at a time, and an import of a large "
            f"set holds it for as long as the import runs. If one is "
            f"running, let it finish and try again -- nothing is lost by "
            f"waiting.\n\n"
            f"If nothing is importing, something else has the store open "
            f"and is not letting go: a second copy of this program, or a "
            f"database tool left open on the file.")
    if "disk i/o error" in text:
        if is_network_path(where):
            return (
                f"The store at {where} could not be opened: {exc}.\n\n"
                f"That path is on a network share. A hash store is a SQLite "
                f"database, and SQLite needs file locking that shares "
                f"implement badly or not at all -- which is what this error "
                f"usually is. Two files open it at once and it can corrupt "
                f"quietly.\n\n"
                f"Keep the store on a disk in the machine that holds it, and "
                f"reach it from other machines with the hash store server "
                f"(fme hashserver), which exists for exactly this.")
        return (
            f"The store at {where} could not be opened: {exc}.\n\n"
            f"The file may be open elsewhere, on a full or failing disk, or "
            f"an import may be writing to it. If an import is running, let "
            f"it finish.")
    return f"The store at {where} could not be opened: {exc}"


def _rolls_back(method):
    """Undo this thread's uncommitted writes when a write method fails.

    Each thread keeps its own connection, and the threaded servers keep
    their threads. A write that raised after its first INSERT left that
    connection inside a transaction holding SQLite's one write lock, and
    every other writer -- the GUI contributing a case, another client --
    waited out the busy timeout and failed with "database is locked" until
    that one thread happened to commit again.
    """
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            con = getattr(self._local, "con", None)
            if con is not None and con.in_transaction:
                try:
                    con.rollback()
                except sqlite3.Error:
                    pass
            raise

    return wrapper


class HashStore:
    """A hash store on disk, shared by every case."""

    def __init__(self, path: str, create: bool = True,
                 lock_wait: float = None):
        """Open a store. With create=False, refuse to invent one.

        Opening a path that holds no store used to make an empty one there
        and report it as the store -- so a mistyped path, or the wrong one
        of two similar names, answered "nothing in store yet" instead of
        "there is no store there", and an examiner went looking for a fault
        in a store that was never the one they imported into.
        """
        self.path = os.path.abspath(path)
        if not create and not os.path.isfile(self.path):
            raise StoreMissing(_missing_message(self.path))
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.on_network = is_network_path(self.path)
        try:
            # Wait for a writer rather than giving up on one. SQLite allows
            # one writer at a time, and an import of a national set holds
            # that for minutes -- long enough that a second connection
            # failing instantly is the normal case, not a rare one. Without
            # this, contributing a case while a set imports returns
            # "database is locked" after no wait at all, which reads as a
            # fault rather than as a queue.
            wait = LOCK_WAIT if lock_wait is None else lock_wait
            self._wait = wait
            # One connection per thread. The store was opened once and that
            # one connection shared by every thread of the hash server and
            # the GUI, with nothing serializing them. On one connection
            # there is no isolation between threads: one thread's commit
            # committed another's half-finished import, a lookup during an
            # import saw rows that a failed import never kept, and the
            # running totals lost updates. Separate connections get what
            # SQLite gives connections -- one writer at a time, queued by the
            # busy timeout above, and readers that see only committed rows.
            self._local = threading.local()
            self._by_thread: dict = {}
            self._by_thread_lock = threading.Lock()
            self._connect()
            # Write-ahead logging needs file locking that a network share
            # implements badly or not at all. On a local disk it is what
            # makes the store fast and safe to read while it is written; on
            # a mapped drive it is how a store gets a "disk I/O error", and
            # worse, how one gets quietly corrupted.
            if self.on_network:
                self._con.execute("PRAGMA journal_mode=DELETE")
            else:
                self._con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as exc:
            raise StoreUnreachable(_explain_open_failure(self.path, exc)) from exc
        self._con.executescript(SCHEMA)
        columns = {row[1] for row in self._con.execute("PRAGMA table_info(hashes)")}
        source_columns = {row[1] for row in
                          self._con.execute("PRAGMA table_info(sources)")}
        if "hash_count" not in source_columns:
            # A store made before this counted its sets by joining. The
            # column is added empty and filled in the first time each set
            # is asked about.
            self._con.execute("ALTER TABLE sources ADD COLUMN hash_count INTEGER")
        if "owner" not in source_columns:
            self._con.execute("ALTER TABLE sources ADD COLUMN owner TEXT")
        if "categories" not in source_columns:
            # Filled in the first time each set's breakdown is asked for.
            self._con.execute("ALTER TABLE sources ADD COLUMN categories TEXT")
        # A case source from before owners were kept. The server wrote its
        # contributor into the note ("contributed by NAME") when it made the
        # source, so that person is its owner; anything else is left to an
        # administrator ('') rather than to whoever contributes to it first
        # after the upgrade -- "first to claim it" would hand another
        # examiner's case to the first person to name it.
        for row in self._con.execute(
                "SELECT id, note FROM sources WHERE kind='case' AND owner IS NULL"
                ).fetchall():
            found = re.fullmatch(r"contributed by (.+)", row["note"] or "")
            self._con.execute("UPDATE sources SET owner=? WHERE id=?",
                              (found.group(1) if found else "", row["id"]))

        if "meta" not in columns:
            # A store built before this version keeps its hashes; re-import a
            # Project VIC set to fill in the records.
            self._con.execute(
                "ALTER TABLE hashes ADD COLUMN meta TEXT NOT NULL DEFAULT ''")
        self._con.commit()

    # -- reading ---------------------------------------------------------
    def sources(self) -> list:
        """The sets in this store, with how many hashes each holds.

        The count is kept per source rather than worked out. Joining the
        whole hashes table to the sources table and grouping it is a full
        scan of tens of millions of rows -- and this is asked by the admin
        pages and by every client pressing Verify, so a store of any size
        stopped answering either.

        A source whose count has never been stored is counted once, here,
        and remembered.
        """
        rows = [dict(row) for row in self._con.execute("""
            SELECT id, name, kind, path, added_at, note, hash_count, owner
            FROM sources ORDER BY name""")]
        for row in rows:
            stored = row.pop("hash_count", None)
            if stored is None:
                stored = self._con.execute(
                    "SELECT COUNT(*) AS n FROM hashes WHERE source_id=?",
                    (row["id"],)).fetchone()["n"]
                self._con.execute(
                    "UPDATE sources SET hash_count=? WHERE id=?",
                    (stored, row["id"]))
                self._con.commit()
            row["hashes"] = int(stored)
        return rows

    def _adjust_source_count(self, source_id: int, added: int) -> None:
        if not added or source_id is None:
            return
        self._con.execute(
            "UPDATE sources SET hash_count = MAX(0, COALESCE(hash_count, 0) + ?) "
            "WHERE id = ?", (int(added), source_id))
        self._con.commit()

    def size(self) -> int:
        """How many hashes the store holds.

        Kept as a running total rather than counted. COUNT(*) over a store
        of tens of millions is a full table scan -- a third of a minute on
        a national set, every time -- and this is asked on every lookup,
        every page of the admin pages and every status call. A server doing
        that per request stops answering anything, which is what it did.

        The total is corrected whenever rows are added or a source is
        removed, and worked out once if it has never been stored.
        """
        row = self._con.execute(
            "SELECT value FROM store_meta WHERE key = 'hash_count'").fetchone()
        if row is not None:
            try:
                return int(row["value"])
            except (TypeError, ValueError):
                pass
        return self.recount()

    def recount(self) -> int:
        """Count the rows and remember the answer. Slow, and done once."""
        total = self._con.execute(
            "SELECT COUNT(*) AS n FROM hashes").fetchone()["n"]
        self._remember_count(total)
        return int(total)

    def _remember_count(self, total: int) -> None:
        self._con.execute(
            "INSERT INTO store_meta(key, value) VALUES('hash_count', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(int(total)),))
        self._con.commit()

    def _adjust_count(self, added: int) -> None:
        """Move the running total by what was just written.

        One UPDATE, not a read and then a write. Read and written as two
        statements, two connections could each read the same total and the
        second write erased the first one's change -- remove_source read it
        outside any transaction, so a contribution landing between its read
        and its write was lost from the total for good.
        """
        if not added:
            return
        changed = self._con.execute(
            "UPDATE store_meta SET value = CAST(MAX(0, CAST(value AS INTEGER) "
            "+ ?) AS TEXT) WHERE key = 'hash_count' AND value GLOB '[0-9]*'",
            (int(added),)).rowcount
        if changed == 0:
            self.recount()      # never counted, or not a number: count it
            return
        self._con.commit()

    # -- the grading scheme the store's own codes are in -----------------
    @property
    def region(self) -> str:
        """The region whose scheme a contributed or custom code is in.

        A Project VIC set's categories are VICS's and the NSRL has none; the
        rest of a store holds bare codes, and a code means something only
        under one scheme -- a UK case's 0 is Uncategorised, a US case's is
        Non-Pertinent. A store made before regions existed is a US store.
        """
        from fme.review import regions
        row = self._con.execute(
            "SELECT value FROM store_meta WHERE key = 'category_region'"
        ).fetchone()
        return regions.normalize(row[0] if row else "US")

    @_rolls_back
    def set_region(self, region: str) -> str:
        """Choose the scheme for a store that holds no codes of its own yet."""
        from fme.review import regions
        if not regions.is_region(region):
            raise ValueError(f"No such region: {region}")
        region = regions.normalize(region)
        if region == self.region:
            return region
        held = self._con.execute(
            "SELECT COUNT(*) FROM sources WHERE kind NOT IN ('projectvic', 'nsrl')"
        ).fetchone()[0]
        if held:
            raise ValueError(
                f"This store already holds {held} source(s) graded under "
                f"{regions.get(self.region).label}; their codes would change "
                f"meaning. Start a new store for {regions.get(region).label}.")
        self._con.execute(
            "INSERT INTO store_meta(key, value) VALUES('category_region', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (region,))
        self._con.commit()
        return region

    def check_region(self, region) -> None:
        """Refuse codes graded under another scheme than the store's."""
        from fme.review import regions
        mine, theirs = self.region, regions.normalize(region)
        if mine != theirs:
            raise ValueError(
                f"This store holds gradings under "
                f"{regions.get(mine).label}; the case was graded under "
                f"{regions.get(theirs).label}. The same number means "
                f"different things in the two, so it was not added.")

    def lookup(self, digests, per_digest: bool = False) -> list:
        """Every source a digest appears in.

        By default the digests are taken to be one file's (its MD5, SHA-1 and
        SHA-256), so a set is reported once however many of them it holds.
        A batch of many files -- the server's /lookup -- must pass
        per_digest=True: merged per set across the batch, only the first
        file a set matched came back and every other was reported unmatched.
        """
        wanted = [d.lower() for d in digests if d]
        if not wanted:
            return []
        # One row per source, not per algorithm: a file whose MD5, SHA-1 and
        # SHA-256 are all in one set has been found once, not three times.
        found: dict = {}
        for chunk in (wanted[i:i + 500] for i in range(0, len(wanted), 500)):
            marks = ",".join("?" * len(chunk))
            for row in self._con.execute(f"""
                    SELECT h.digest, h.algo, h.category, h.note, h.meta,
                           s.name AS source, s.kind
                    FROM hashes h JOIN sources s ON s.id = h.source_id
                    WHERE h.digest IN ({marks})""", chunk):
                entry = dict(row)
                try:
                    entry["record"] = json.loads(entry.pop("meta") or "{}")
                except (json.JSONDecodeError, TypeError):
                    entry["record"] = {}
                key = ((entry["source"], entry["digest"]) if per_digest
                       else entry["source"])
                if key in found:
                    # Record which algorithms agreed, which is worth knowing:
                    # three matching is a stronger statement than one.
                    found[key]["matched_on"].append(entry["algo"])
                    continue
                entry["matched_on"] = [entry["algo"]]
                found[key] = entry
        region = self.region
        for entry in found.values():
            entry["matched_on"] = ", ".join(sorted(set(entry["matched_on"])))
            # Which scheme a bare code is in, so a case under another region's
            # does not take it as its own (media_gui.apply_set_categories).
            entry["region"] = region
        return list(found.values())

    # -- importing -------------------------------------------------------
    def import_set(self, path: str, kind: str = "", name: str = "",
                   progress=None) -> dict:
        """Read a hash set into the store.

        Streamed in batches of BATCH rows per commit, which is where the
        speed comes from: a national set is tens of millions of rows, and a
        commit per row is an import that takes an afternoon.
        """
        kind = (kind or hashsets.detect_kind(path)).strip().lower()
        if kind not in KINDS:
            raise ValueError(
                f"{kind!r} is not a kind of hash set this store knows. Use one "
                f"of: {', '.join(KINDS)}.")
        if kind != "projectvic" and hashsets.looks_like_vics(path):
            # Never as anything else. Read as the NSRL, every hash in a
            # Project VIC set -- CAM included -- was found by the salvage scan
            # and stored as known software, which grades a file Non-Pertinent
            # wherever no other set names it. Read as a custom list, every
            # category was thrown away.
            raise ValueError(
                f"{os.path.basename(path)} is a Project VIC (VICS) file. Import "
                f"it as Project VIC, so each hash keeps the category the set "
                f"gives it; imported as {kind.upper() if kind == 'nsrl' else kind} "
                f"its categories would be lost.")
        name = name or os.path.basename(path)
        started = time.time()

        existing = self._con.execute(
            "SELECT id FROM sources WHERE name=?", (name,)).fetchone()
        if existing:
            raise ValueError(
                f"A source named {name!r} is already in this store. Remove it "
                f"first, or import under another name.")

        self._con.execute(
            "INSERT INTO sources (name, kind, path, added_at) VALUES (?,?,?,?)",
            (name, kind, os.path.abspath(path), _now()))
        source_id = self._con.execute(
            "SELECT id FROM sources WHERE name=?", (name,)).fetchone()["id"]
        # A pragma cannot change durability inside a transaction, and the
        # insert above opened one.
        self._con.commit()
        # Never OFF. The import ran with synchronous=OFF on the reasoning
        # that a set can be imported again -- but the same file holds the
        # unit's contributed gradings, which exist nowhere else, and OFF lets
        # a power cut or a dropped share corrupt the whole file, not just
        # lose the set. In WAL, NORMAL cannot corrupt the store (a crash
        # loses at most the last commits), and fsyncs only at checkpoints,
        # so the batching keeps nearly all the speed. With a rollback
        # journal (a network share) only FULL is safe, and it is the
        # default, so it is left alone.
        # Asked of SQLite rather than assumed from on_network: a local file
        # system that cannot do WAL leaves the store in DELETE.
        mode = str(self._con.execute("PRAGMA journal_mode").fetchone()[0])
        if mode.lower() == "wal":
            self._con.execute("PRAGMA synchronous=NORMAL")
        rows, written, seen = [], 0, 0
        skipped = {"not a hash": 0, "empty file": 0}
        records: dict = {}      # category -> records (files) read with a hash
        try:
            for digest, category, note in _stream(path, kind, records):
                seen += 1
                meta = ""
                if isinstance(note, tuple):      # (note, record)
                    note, record = note
                    meta = json.dumps(record, separators=(",", ":"))
                algo = _algo_of(digest)
                if not algo:
                    skipped["not a hash"] += 1
                    continue
                if digest in hashsets.EMPTY_FILE_HASHES:
                    skipped["empty file"] += 1
                    continue
                rows.append((digest, algo, source_id, category, note, meta))
                if len(rows) >= BATCH:
                    written += self._write(rows, fill=True)
                    rows = []
                    if progress:
                        progress(seen, written)
            written += self._write(rows, fill=True)
            self._con.commit()
        except BaseException:
            # Batches commit as they go, so a failure part way left a set
            # holding the first batches and looking complete -- and blocked
            # importing it again, since the name was taken. The partial set
            # is taken out again before the error is passed on.
            try:
                if self._con.in_transaction:
                    self._con.rollback()
                removed = self._con.execute(
                    "DELETE FROM hashes WHERE source_id=?",
                    (source_id,)).rowcount
                self._con.execute("DELETE FROM sources WHERE id=?",
                                  (source_id,))
                if removed:
                    self._adjust_count(-removed)
                self._con.commit()
            except sqlite3.Error as undo:
                LOG.error("A failed import of %s could not be taken out of the "
                          "store (%s); remove the source %r before importing "
                          "it again", path, undo, name)
            raise
        finally:
            self._con.commit()
            self._con.execute("PRAGMA synchronous=FULL")

        note = ""
        if written == 0 and kind == "nsrl":
            # No salvage for a known-good set. Whatever hash-shaped strings a
            # file that is not the NSRL holds would all be stored as known
            # software, and a match on one grades a file Non-Pertinent.
            note = ("nothing was found reading it as the NSRL (an RDS SQLite "
                    "database, or NSRLFile.txt with a header naming its hash "
                    "columns), and nothing was imported. Check this is the "
                    "file you meant.")
        elif written == 0:
            # Reading it as the kind it was taken for found nothing. Rather
            # than report a bare zero, scan the file for hashes whatever its
            # structure -- a VICS variant this reader does not expect still
            # has the hashes in it -- and say that is what happened.
            recovered, scanned = self._salvage(path, source_id)
            written += recovered
            note = (f"nothing was found reading it as {kind}; scanning the "
                    f"file for hashes of any kind found {recovered:,} in "
                    f"{scanned:,} line(s). Check this is the file you meant."
                    if recovered else
                    f"nothing was found reading it as {kind}, and scanning "
                    f"the whole file for anything hash-shaped found nothing "
                    f"either. Is this a hash set?")
            self._con.commit()

        if written == 0:
            # An empty source would sit in the list looking like a set that
            # happens to have no hashes in it.
            self._con.execute("DELETE FROM sources WHERE id=?", (source_id,))
            self._con.commit()

        self._checkpoint()
        if kind == "nsrl" and hashsets._is_sqlite(path) and not note:
            # Which table and columns were used, so an import can be checked
            # instead of taken on trust.
            try:
                con = sqlite3.connect(ro_uri(path), uri=True)
                table, columns, extra = rds_table(con)
                con.close()
                note = (f"read from \"{table}\" using {', '.join(columns)}"
                        + (f"; also kept {', '.join(extra)}" if extra else ""))
            except sqlite3.Error:
                pass

        breakdown = {}
        if written:
            if kind == "nsrl":
                # The NSRL is known software, and a hit on it grades a file 0
                # (media_gui._apply_hash_categories) -- unless a categorized
                # set names the file too. Its rows keep a NULL category, as
                # they always have, so a client of any version reads them the
                # same way; the breakdown says what they mean.
                records = {0: sum(records.values())}
            breakdown = self._remember_breakdown(source_id, kind, records)

        # The import is the biggest single change to the total, so it is
        # the one that must not leave it stale.
        return {"source": name, "kind": kind, "read": seen, "stored": written,
                "skipped": {k: v for k, v in skipped.items() if v},
                "note": note, "seconds": round(time.time() - started, 1),
                "categories": breakdown}

    # -- what a set holds, by category -------------------------------------
    def _count_by_category(self, source_id: int, kind: str) -> dict:
        counts = {}
        for row in self._con.execute(
                "SELECT category, COUNT(*) AS n FROM hashes WHERE source_id=? "
                "GROUP BY category", (source_id,)):
            category = row["category"]
            if kind == "nsrl":
                category = 0
            key = "none" if category is None else str(category)
            counts[key] = counts.get(key, 0) + int(row["n"])
        return counts

    def _remember_breakdown(self, source_id: int, kind: str,
                            records: dict) -> dict:
        """Count what landed, by category, and keep it with the source.

        Hashes are counted from the database, not from what was sent: a set
        that lists a file twice, or lists one at two categories, lands once,
        at the more severe (_write). Records are the files the set listed,
        counted as they were read, which is the number an examiner compares
        with the set's own documentation.
        """
        breakdown = {
            "records": {("none" if k is None else str(k)): v
                        for k, v in records.items()},
            "hashes": self._count_by_category(source_id, kind)}
        self._con.execute("UPDATE sources SET categories=? WHERE id=?",
                          (json.dumps(breakdown), source_id))
        self._con.commit()
        return breakdown

    def category_breakdown(self, name: str) -> dict:
        """{"records": {...}, "hashes": {...}} for one source, keyed by
        category as a string ("none" for no category).

        An imported set's is kept from its import. A set imported before
        this was kept is counted once, now, and remembered -- without its
        records, which only the import saw. A case source is counted every
        time, because a contribution can regrade any of its hashes.
        """
        row = self._con.execute(
            "SELECT id, kind, categories FROM sources WHERE name=?",
            (name,)).fetchone()
        if row is None:
            raise ValueError(f"There is no source named {name!r} in this store.")
        if row["kind"] != "case" and row["categories"]:
            try:
                return json.loads(row["categories"])
            except (TypeError, ValueError):
                pass
        if row["kind"] == "case":
            return {"records": {},
                    "hashes": self._count_by_category(row["id"], row["kind"])}
        return self._remember_breakdown(row["id"], row["kind"], {})

    def _salvage(self, path: str, source_id: int) -> tuple:
        """Last resort: pull hash-shaped strings out of a file of any shape.

        Formats drift, and a set that cannot be read is worse than one read
        crudely. This finds 32, 40 and 64-character hex strings anywhere in
        the file, which is every MD5, SHA-1 and SHA-256 in it whatever the
        structure around them.
        """
        pattern = re.compile(r"\b([0-9a-fA-F]{32}|[0-9a-fA-F]{40}|"
                             r"[0-9a-fA-F]{64})\b")
        rows, found, lines = [], 0, 0
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                lines += 1
                for digest in pattern.findall(line):
                    digest = digest.lower()
                    if digest in hashsets.EMPTY_FILE_HASHES:
                        continue
                    rows.append((digest, _algo_of(digest), source_id, None,
                                 "found by scanning the file"))
                    if len(rows) >= BATCH:
                        found += self._write(rows)
                        rows = []
                        # Every so often, fold what has been written so far
                        # into the database. The other tool lets the log
                        # grow for the whole import and truncates once at
                        # the end, which is faster -- and leaves nothing
                        # recoverable in reasonable time when the import is
                        # interrupted. A national set killed part way should
                        # cost minutes, not an afternoon.
                        if found % CHECKPOINT_EVERY < BATCH:
                            self._con.commit()
                            self._checkpoint()
        found += self._write(rows)
        return found, lines

    def _checkpoint(self) -> None:
        """Fold the write-ahead log back into the database file.

        With WAL on, recent writes live in a separate -wal file until SQLite
        decides to fold them in. That is fine while this process is the only
        reader, but a store is something people copy to a share, hand to a
        colleague, or open in a database viewer -- and a .db taken without its
        -wal is a .db missing whatever had not been folded in yet, which looks
        exactly like an import that did nothing.
        """
        # Tried more than once. A checkpoint fails when another connection
        # holds a read, which during an import is exactly when it matters:
        # giving up at the first refusal is how a store ends an import with
        # a write-ahead log larger than itself, and then takes an hour to
        # answer its first question.
        for attempt in range(8):
            try:
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                return
            except sqlite3.Error:
                if attempt == 7:
                    return   # a checkpoint is an optimization, never a failure
                time.sleep(1.5)

    def wal_bytes(self) -> int:
        """How much is waiting to be folded in."""
        try:
            return os.path.getsize(self.path + "-wal")
        except OSError:
            return 0

    def checkpoint(self, progress=None) -> dict:
        """Fold the log in, and say what happened.

        Worth having on its own because an import killed part way leaves a
        log that every later read has to walk. Folding it in is a single
        slow operation, and knowing that is what is happening is better
        than a program that looks hung.
        """
        before = self.wal_bytes()
        if progress:
            progress(before, 0)
        started = time.time()
        try:
            self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            failed = ""
        except sqlite3.Error as exc:
            failed = str(exc)
        after = self.wal_bytes()
        return {"before": before, "after": after,
                "folded_in": max(0, before - after),
                "seconds": round(time.time() - started, 1),
                "error": failed,
                "complete": after < max(4096, before * 0.01)}

    def _write(self, rows: list, fill: bool = False) -> int:
        """Insert rows and keep the running totals right.

        Every path that adds hashes comes through here -- an import, a
        contribution, the salvage scan -- so the totals are corrected here
        rather than at each caller. Correcting them at the callers is how
        one path gets missed and the store reports fewer hashes than it
        holds, which is worse than counting slowly.

        INSERT OR IGNORE means what was asked for is not always what landed,
        so the count comes from the database rather than from len(rows).
        """
        if not rows:
            return 0
        # Rows written before the record column existed are five long.
        padded = [row if len(row) == 6 else (*row, "") for row in rows]
        # Inserted one source at a time, and counted from the database for
        # each. With more than one source in a batch, each source's count
        # was moved by the rows asked for rather than the rows that landed,
        # and INSERT OR IGNORE skips every row a source already holds -- so
        # writing the same rows again inflated every source in the batch.
        by_source: dict = {}
        for row in padded:
            by_source.setdefault(row[2], []).append(row)
        inserted = 0
        landed: dict = {}
        for source_id, group in by_source.items():
            before = self._con.total_changes
            self._con.executemany(
                "INSERT OR IGNORE INTO hashes (digest, algo, source_id, "
                "category, note, meta) VALUES (?,?,?,?,?,?)", group)
            landed[source_id] = self._con.total_changes - before
            inserted += landed[source_id]
            if fill:
                # A set that lists one hash twice: OR IGNORE kept the first,
                # so an uncategorized entry ahead of a category 1 for the
                # same file lost the category. A later entry fills a missing
                # category, or replaces a less severe one (0 is the least).
                self._con.executemany(
                    "UPDATE hashes SET category=?, note=?, meta=? "
                    "WHERE digest=? AND source_id=? AND ? IS NOT NULL AND "
                    "(category IS NULL OR (category = 0 AND ? <> 0) OR "
                    "(? <> 0 AND category <> 0 AND ? < category))",
                    [(r[3], r[4], r[5], r[0], r[2], r[3], r[3], r[3], r[3])
                     for r in group if r[3] is not None])

        if inserted:
            self._adjust_count(inserted)
            # Per source, so the admin pages and Verify never have to join
            # the whole table to say what each set holds.
            for source_id, count in landed.items():
                self._adjust_source_count(source_id, count)
        # What landed, not what was sent: INSERT OR IGNORE skips a digest the
        # source already holds, and reporting len(rows) told an examiner a
        # contribution or an import had stored rows it had not.
        return inserted

    @_rolls_back
    def remove_source(self, name: str) -> int:
        row = self._con.execute(
            "SELECT id FROM sources WHERE name=?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"No source named {name!r} in this store")
        # The rows removed are the rows the DELETE removed. A COUNT taken
        # first, in its own snapshot, could disagree with the DELETE that
        # followed when a contribution to this source landed in between, and
        # the running total then moved by the wrong amount. The deletes and
        # the total are committed together, by _adjust_count.
        count = self._con.execute(
            "DELETE FROM hashes WHERE source_id=?", (row["id"],)).rowcount
        self._con.execute("DELETE FROM sources WHERE id=?", (row["id"],))
        if count:
            self._adjust_count(-count)
        self._con.commit()
        self._checkpoint()
        return count

    # -- matching and contributing ---------------------------------------
    def scan_manifest(self, manifest_path: str,
                      include_duplicates: bool = True) -> dict:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        records = list(manifest.get("files", []))
        if include_duplicates:
            records += manifest.get("duplicates", [])

        # Counted by file, looked up once per file. Every duplicate location
        # was looked up again and counted again: a picture stored once and
        # found in three places was "3 in DC25-320", so the totals an examiner
        # read were locations, not files -- and a case with 242,815 duplicate
        # locations made as many needless queries of the store. Every location
        # is still a row, so the CSV lists each one.
        by_source: dict = {}
        rows, seen = [], {}
        for rec in records:
            digests = tuple(sorted({(rec.get(k) or "").lower()
                                    for k in ("md5", "sha1", "sha256")} - {""}))
            key = (rec.get("sha256") or "").lower() or digests
            if key not in seen:
                hits = self.lookup(list(digests)) if digests else []
                seen[key] = hits
                for hit in hits:
                    by_source[hit["source"]] = by_source.get(hit["source"], 0) + 1
            rows.append({"record": rec, "matches": seen[key]})
        matched = sum(1 for hits in seen.values() if hits)
        return {"checked": len(seen), "matched": matched,
                "unmatched": len(seen) - matched, "by_source": by_source,
                "locations": len(records),
                "locations_matched": sum(1 for row in rows if row["matches"]),
                "rows": rows}

    @_rolls_back
    def contribute_rows(self, rows, source_name: str,
                        contributor: str = "", admin: bool = False,
                        changes: list = None, region: str = "US") -> int:
        """Add already-prepared gradings to the store.

        The server uses this: a client across a network sends what its case
        decided, as hashes and labels, rather than a folder the server
        cannot see. Each row is {md5, sha1, sha256, category, label}; only
        the digests present are stored, and a row with no label is refused,
        because an unlabelled contribution is the bare "seen before" this
        store exists to avoid.

        `contributor` is the signed-in person, and with one the source must
        be theirs (NotYourSource otherwise) unless `admin`. Every grade this
        changed is appended to `changes`, when given, for the caller's log.
        """
        # A client from before regions sends none, and graded only US cases.
        self.check_region(region or "US")
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "").strip()
            code = row.get("category")
            if not label:
                continue
            for key in ("md5", "sha1", "sha256"):
                digest = str(row.get(key) or "").lower().strip()
                if digest and hashsets.HASH_RE.match(digest) \
                        and digest not in hashsets.EMPTY_FILE_HASHES:
                    prepared.append((digest, key,
                                     code if isinstance(code, int) else None,
                                     label))
        if not prepared:
            raise ValueError("Nothing usable to contribute: every row needs a "
                             "hash and the label it was graded with.")

        note = f"contributed by {contributor}" if contributor else "contributed"
        source_id = self._case_source(source_name, "", note,
                                      contributor=contributor, admin=admin,
                                      check_owner=bool(contributor))
        rows = [(digest, algo, source_id, code, label, "")
                for digest, algo, code, label in prepared]
        # Regraded first, inside the write lock _case_source took: _write
        # commits as it counts, which ends that transaction, and a regrade
        # after it ran unprotected from a removal landing in between.
        written = self._regrade(rows, source_name, contributor, changes)
        written += self._write(rows)
        self._con.commit()
        self._checkpoint()
        return written

    def _case_source(self, source_name: str, path: str, note: str,
                     contributor: str = "", admin: bool = False,
                     check_owner: bool = False) -> int:
        """The id of the case source a contribution goes into, made if new.

        Taken inside a write transaction (BEGIN IMMEDIATE), which the caller
        commits with the rows. Read outside one, a removal of the source
        could land between the read and the insert -- the reply said
        "added" and the rows sat under an id no source had, never found
        again -- and two first contributions under one new name both found
        no source and the second failed on the name's UNIQUE constraint.

        Only into a case source. Any name was reused whatever its kind, so
        a contributor could add hashes to "NSRL" or a Project VIC set and
        have a picture reported as known software and set aside.

        And, with `check_owner`, only into the contributor's own case. A
        contributor naming another examiner's case in "source" regraded
        every hash that case had sent -- CAM to Non-Pertinent, silently.
        A new source belongs to whoever made it.
        """
        if not self._con.in_transaction:
            self._con.execute("BEGIN IMMEDIATE")
        existing = self._con.execute(
            "SELECT id, kind, owner FROM sources WHERE name=?",
            (source_name,)).fetchone()
        if existing:
            if (existing["kind"] or "") != "case":
                raise ValueError(
                    f"{source_name!r} is an imported {existing['kind'] or ''} "
                    f"hash set, not a case. Contributions go into a set of "
                    f"their own: contribute under the case's name.")
            owner = existing["owner"] or ""
            if check_owner and not admin and owner != contributor:
                raise NotYourSource(
                    f"{source_name!r} is a case contributed by "
                    f"{owner or 'someone without a store account'}, not by "
                    f"{contributor}. Contribute under a name of your own; an "
                    f"administrator can add to another examiner's case.")
            return existing["id"]
        self._con.execute(
            "INSERT INTO sources (name, kind, path, added_at, note, owner) "
            "VALUES (?,?,?,?,?,?)",
            (source_name, "case", path, _now(), note, contributor or ""))
        return self._con.execute(
            "SELECT id FROM sources WHERE name=?",
            (source_name,)).fetchone()["id"]

    def _regrade(self, rows, source_name: str = "", by: str = "",
                 changes: list = None) -> int:
        """Bring a case's earlier gradings up to date with what it sends now.

        A file contributed as Non-Pertinent and later regraded CAM was
        contributed again, INSERT OR IGNORE skipped it because the case
        already held that digest, the reply said "added", and the store went
        on saying Non-Pertinent. Returns how many rows changed.

        Each change is kept in grade_changes, in the caller's transaction,
        and appended to `changes` for the server's access log: the earlier
        grade is otherwise gone without trace.
        """
        changed = 0
        when = _now()
        for digest, _algo, source_id, code, label, _meta in rows:
            before = self._con.execute(
                "SELECT category, note FROM hashes WHERE digest=? AND "
                "source_id=? AND (category IS NOT ? OR note IS NOT ?)",
                (digest, source_id, code, label)).fetchone()
            if before is None:
                continue
            changed += self._con.execute(
                "UPDATE hashes SET category=?, note=? WHERE digest=? AND "
                "source_id=?", (code, label, digest, source_id)).rowcount
            entry = {"at": when, "source": source_name, "digest": digest,
                     "old_category": before["category"],
                     "old_label": before["note"] or "",
                     "new_category": code, "new_label": label or "",
                     "by": by or ""}
            self._con.execute(
                "INSERT INTO grade_changes (at, source, digest, old_category, "
                "old_label, new_category, new_label, by) "
                "VALUES (:at,:source,:digest,:old_category,:old_label,"
                ":new_category,:new_label,:by)", entry)
            if changes is not None:
                changes.append(entry)
        return changed

    def grade_changes(self, limit: int = 500) -> list:
        """The most recent regrades, newest first."""
        return [dict(r) for r in self._con.execute(
            "SELECT * FROM grade_changes ORDER BY rowid DESC LIMIT ?",
            (int(limit),))]

    @_rolls_back
    def contribute(self, out_dir: str, source_name: str,
                   categories=None, note: str = "") -> dict:
        """Put what this case decided into the store, for the next one.

        Only files an examiner graded are contributed, and the grading travels
        with the hash: a later case matching one of these learns what was
        decided here and by which case, rather than being told a bare "seen
        before". Uncategorized files are not contributed -- a hash nobody has
        looked at teaches the next case nothing.
        """
        from fme.review import casedb

        manifest_path = os.path.join(out_dir, "manifest.json")
        case_path = os.path.join(out_dir, "case.db")
        if not os.path.isfile(manifest_path):
            raise FileNotFoundError(f"No manifest in {out_dir}")
        if not os.path.isfile(case_path):
            raise FileNotFoundError(
                f"No case.db in {out_dir}: nothing has been categorized, so "
                f"there is nothing worth contributing")

        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        case = casedb.CaseDB(case_path)
        decisions = case.decisions()
        names = {c["code"]: c["name"] for c in case.categories()}
        uncategorized, region = case.uncategorized, case.region
        case.close()
        self.check_region(region)

        wanted = set(categories) if categories else None
        rows, contributed, skipped, by_sets = [], 0, 0, 0
        for rec in manifest.get("files", []):
            digest_sha = (rec.get("sha256") or "").lower()
            decided = decisions.get(digest_sha)
            code = decided["category"] if decided else uncategorized
            if code == uncategorized or (wanted and code not in wanted):
                skipped += 1
                continue
            if decided and decided.get("auto"):
                # A hash set's match, not anyone's judgment: contributed, it
                # came back to every case as "case X graded this".
                by_sets += 1
                continue
            label = names.get(code, str(code))
            for key in ("md5", "sha1", "sha256"):
                digest = (rec.get(key) or "").lower()
                if digest and digest not in hashsets.EMPTY_FILE_HASHES:
                    rows.append((digest, key, None, code, label))
            contributed += 1

        if not rows:
            raise ValueError(
                "No categorized files to contribute. Grade some first: an "
                "uncategorized hash teaches the next case nothing"
                + (f", and the {by_sets} graded by a hash set are its "
                   f"match, not this case's finding." if by_sets else "."))

        # No owner check: this is the examiner's own machine writing the
        # store file directly, which no account stands between anyway.
        source_id = self._case_source(
            source_name, os.path.abspath(out_dir),
            note or f"contributed from {os.path.basename(out_dir)}")
        prepared = [(d, a, source_id, c, n, "") for d, a, _, c, n in rows]
        self._regrade(prepared, source_name)
        self._write(prepared)
        self._con.commit()
        self._checkpoint()
        return {"source": source_name, "files": contributed,
                "hashes": len(rows), "skipped_uncategorized": skipped,
                "skipped_hash_set": by_sets}

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, check_same_thread=False,
                              timeout=self._wait)
        con.execute(f"PRAGMA busy_timeout={int(self._wait * 1000)}")
        con.row_factory = sqlite3.Row
        me = threading.current_thread()
        with self._by_thread_lock:
            # A server that starts a thread per request would otherwise keep
            # a connection for every request it ever answered.
            for ident, (thread, old) in list(self._by_thread.items()):
                if not thread.is_alive():
                    try:
                        old.close()
                    except sqlite3.Error:
                        pass
                    del self._by_thread[ident]
            self._by_thread[me.ident] = (me, con)
        self._local.con = con
        return con

    @property
    def _con(self) -> sqlite3.Connection:
        """This thread's connection to the store."""
        con = getattr(self._local, "con", None)
        return con if con is not None else self._connect()

    def close(self) -> None:
        self._con.commit()
        self._checkpoint()
        # This thread's connection, and those of threads that have ended.
        # A live thread's connection may be in the middle of a query -- a
        # server answering a lookup while the GUI switches stores -- and
        # closing it under that thread failed the query with "Cannot operate
        # on a closed database". It is dropped from this store instead, and
        # closes when its thread lets go of it.
        me = threading.get_ident()
        with self._by_thread_lock:
            mine = [con for ident, (thread, con) in self._by_thread.items()
                    if ident == me or not thread.is_alive()]
            self._by_thread.clear()
        for con in mine:
            try:
                con.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()


# --------------------------------------------------------------------------
# Streaming readers: one row at a time, so a national set never has to fit in
# memory the way the per-run loaders required.
# --------------------------------------------------------------------------
# Where an NSRL RDS keeps its hashes. The modern release ships the raw rows
# in FILE and a joined view in METADATA; older and reduced releases differ
# again, and some distributions carry the hashes in a view rather than a
# table. Preferring the known names and falling back to anything that has
# hash columns covers all of them.
RDS_PREFERRED = ("METADATA", "FILE", "FILES", "DISTINCT_HASH", "HASHES")
RDS_HASH_COLUMNS = ("md5", "sha1", "sha256")
RDS_EXTRA_COLUMNS = ("file_name", "filename", "file_size", "filesize",
                     "package_id", "product_code", "op_system_code",
                     "special_code", "crc32", "name", "version",
                     "product_name", "os_name", "mfg_name")


def rds_table(con) -> tuple:
    """The table or view holding the hashes, and which columns are usable.

    A column existing is not the same as a column holding anything: some
    releases ship a sha256 column that is empty throughout, and choosing it
    would import a set of nothing. Each candidate column is sampled before
    it is accepted, and views are searched as well as tables because some
    distributions present the hashes that way.
    """
    names = [row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")]
    ordered = [n for n in RDS_PREFERRED if n in names]
    ordered += [n for n in names if n not in ordered]

    for name in ordered:
        try:
            columns = {row[1].lower(): row[1]
                       for row in con.execute(f'PRAGMA table_info("{name}")')}
        except sqlite3.Error:
            continue
        present = [columns[c] for c in RDS_HASH_COLUMNS if c in columns]
        if not present:
            continue
        usable = []
        for column in present:
            try:
                row = con.execute(
                    f'SELECT "{column}" FROM "{name}" '
                    f'WHERE "{column}" IS NOT NULL AND "{column}" != "" '
                    f'LIMIT 1').fetchone()
            except sqlite3.Error:
                continue
            if row and hashsets.HASH_RE.match(str(row[0]).strip()):
                usable.append(column)
        if usable:
            extra = [columns[c] for c in RDS_EXTRA_COLUMNS if c in columns]
            return name, usable, extra
    return "", [], []


# Yielded by _stream_rds between rows, so a caller can count files as well as
# hashes; _stream consumes it and never passes it on.
_NEXT_RECORD = object()


def _stream_rds(path: str):
    """Every hash in an NSRL RDS database, with what it says about the file."""
    con = sqlite3.connect(ro_uri(path), uri=True)
    try:
        table, hashes, extra = rds_table(con)
        if not table:
            return
        selected = ", ".join(f'"{c}"' for c in hashes + extra)
        for row in con.execute(f'SELECT {selected} FROM "{table}"'):
            record = {name: row[len(hashes) + i]
                      for i, name in enumerate(extra)
                      if row[len(hashes) + i] not in (None, "")}
            if any(row[:len(hashes)]):
                yield _NEXT_RECORD
            for value in row[:len(hashes)]:
                if value:
                    yield (str(value).strip().lower(), None,
                           ("NSRL", record) if record else "NSRL")
    finally:
        con.close()


def _tally(records, category) -> None:
    if records is not None:
        records[category] = records.get(category, 0) + 1


def _stream(path: str, kind: str, records: dict = None):
    """Every (digest, category, note) in a set. `records`, if given, counts
    the records (files) read that carried a hash, by category."""
    if kind == "projectvic":
        from fme.exports import vic_import
        for entry in vic_import.stream_media(path):
            # vic_category, not isinstance(int): a set that wrote "1" lost
            # every category it held.
            category = hashsets.vic_category(entry.get("Category"))
            raw = entry.get("Category")
            label = ("" if raw in (None, "") else f"category {raw}")
            # Carry the whole record, not only the hash: everything the set
            # recorded is what makes a later match worth having.
            kept = hashsets.vic_record(entry)
            digests = hashsets.vic_digests(entry)
            if digests:
                _tally(records, category)
            for key, value in digests.items():
                yield value, category, (label, kept)
        return

    if kind == "nsrl" and hashsets._is_sqlite(path):
        for item in _stream_rds(path):
            if item is _NEXT_RECORD:
                _tally(records, None)
                continue
            yield item
        return

    # Text: NSRLFile.txt, a CSV with a hash column, or one hash per line.
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        if "," in sample or "\t" in sample:
            reader = csv.reader(fh)
            header = next(reader, [])
            lowered = [h.strip().strip('"').lower().replace("-", "")
                       for h in header]
            indexes = [i for i, h in enumerate(lowered)
                       if h in ("md5", "sha1", "sha256")]
            if not indexes:
                fh.seek(0)
                reader = csv.reader(fh)
            # The rest of an NSRL row says what the file is -- its name, its
            # size, and which product and operating system it shipped with.
            # That is the whole value of a match: "known software" is a
            # conclusion, "msvcrt.dll, Windows 10, 42 KB" is a finding.
            labels = [h.strip().strip('"') for h in header]
            keep = {i: labels[i] for i, name in enumerate(lowered)
                    if name in ("filename", "filesize", "productcode",
                                "opsystemcode", "specialcode", "crc32",
                                "productname", "productversion", "opsystemname")}
            for row in reader:
                record = {}
                for position, label in keep.items():
                    if position < len(row):
                        value = row[position].strip().strip('"')
                        if value:
                            record[label] = value
                cells = ([row[i] for i in indexes if i < len(row)]
                         if indexes else row)
                counted = False
                for cell in cells:
                    cell = cell.strip().strip('"')
                    if hashsets.HASH_RE.match(cell):
                        if not counted:
                            _tally(records, None)
                            counted = True
                        yield cell.lower(), None, ("NSRL", record) if record else ""
            return
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[,;\t ]+", line, maxsplit=1)
            value = parts[0].strip().strip('"')
            if hashsets.HASH_RE.match(value):
                _tally(records, None)
                yield value.lower(), None, (parts[1].strip() if len(parts) > 1 else "")


def verify(path: str) -> dict:
    """Open a store read-only and report what is actually in the file.

    Deliberately separate from the running program: this is what a colleague,
    a viewer, or a copy on a share would see.
    """
    result = {"path": os.path.abspath(path), "exists": os.path.isfile(path)}
    if not result["exists"]:
        return result
    result["size"] = os.path.getsize(path)
    for suffix in ("-wal", "-shm"):
        side = result["path"] + suffix
        if os.path.isfile(side):
            result.setdefault("side_files", {})[suffix] = os.path.getsize(side)
    con = sqlite3.connect(ro_uri(result['path']), uri=True)
    con.row_factory = sqlite3.Row
    try:
        result["total"] = con.execute(
            "SELECT COUNT(*) AS n FROM hashes").fetchone()["n"]
        result["sources"] = [dict(row) for row in con.execute("""
            SELECT s.name, s.kind, s.added_at, COUNT(h.digest) AS hashes
            FROM sources s LEFT JOIN hashes h ON h.source_id = s.id
            GROUP BY s.id ORDER BY s.name""")]
        result["by_algorithm"] = {row["algo"]: row["n"] for row in con.execute(
            "SELECT algo, COUNT(*) AS n FROM hashes GROUP BY algo")}
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    finally:
        con.close()
    return result


def inspect_set(path: str, sample: int = 5) -> dict:
    """Say what a hash set file actually is, before importing it.

    Written because "0 hashes added" is not a diagnosis. This reports the
    size, what the detector makes of it, how the structured reader gets on,
    and what a blind scan finds -- which between them identify almost any
    file that will not import.
    """
    out = {"path": os.path.abspath(path), "exists": os.path.isfile(path)}
    if not out["exists"]:
        return out
    out["size"] = os.path.getsize(path)
    out["kind"] = hashsets.detect_kind(path)

    with open(path, "rb") as fh:
        head = fh.read(400)
    out["starts_with"] = head[:160].decode("utf-8", "replace").replace("\r", " ")\
                                   .replace("\n", " ")
    out["byte_order_mark"] = head[:3] == b"\xef\xbb\xbf"

    structured = []
    try:
        for digest, category, note in _stream(path, out["kind"]):
            structured.append(digest)
            if len(structured) >= sample:
                break
    except Exception as exc:  # noqa: BLE001 - the point is to report it
        out["reader_error"] = f"{type(exc).__name__}: {exc}"
    out["structured_sample"] = structured

    pattern = re.compile(r"\b([0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\b")
    scanned = []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for _ in range(4000):
            line = fh.readline()
            if not line:
                break
            scanned.extend(pattern.findall(line))
            if len(scanned) >= sample:
                break
    out["scanned_sample"] = [d.lower() for d in scanned[:sample]]

    if out["kind"] == "projectvic" and not structured:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            start = fh.read(200000)
        out["has_media_array"] = '"Media"' in start
        out["top_level_keys"] = re.findall(r'"(\w+)"\s*:', start)[:12]
    return out


def move_store(source: str, destination: str) -> dict:
    """Copy a store to another path and prove the copy is complete.

    Copying the file with Explorer is how a store arrives missing its most
    recent imports: SQLite keeps recent writes in a journal beside the
    database, and a copy taken without it is older than it looks -- silently.

    This copies through SQLite itself, which includes everything committed,
    then counts both and refuses to call it done unless they agree. The
    original is left alone: moving evidence is something to do by hand,
    after seeing the copy is good.
    """
    source = os.path.abspath(source)
    destination = os.path.abspath(destination)
    if not os.path.isfile(source):
        raise StoreMissing(_missing_message(source))
    if os.path.exists(destination):
        raise ValueError(f"{destination} already exists. Choose a path that "
                         f"does not, so nothing is overwritten.")

    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    before = verify(source)

    # Through SQLite's own backup, which takes a consistent copy even while
    # something else is reading the store.
    origin = sqlite3.connect(ro_uri(source), uri=True)
    try:
        copy = sqlite3.connect(destination)
        try:
            origin.backup(copy)
        finally:
            copy.close()
    finally:
        origin.close()

    after = verify(destination)
    same = (after.get("total") == before.get("total")
            and len(after.get("sources", [])) == len(before.get("sources", [])))
    return {"source": source, "destination": destination,
            "hashes": after.get("total", 0),
            "sources": len(after.get("sources", [])),
            "complete": same,
            "was": before.get("total", 0),
            "destination_on_network": is_network_path(destination),
            "size": os.path.getsize(destination)}


def check_integrity(path: str) -> int:
    """Ask SQLite whether the store's pages are sound.

    A hash store is one large SQLite file that several people write to
    over a network, on a machine that may lose power mid-import. When
    it goes wrong the symptom is not an error: lookups simply stop
    matching things they used to match, which in a case looks like the
    file not being in the set.

    Read-only. It reports; it does not repair, because repairing a
    forensic reference store in place is not something to do quietly.
    What it prints is what to do instead.
    """
    import sqlite3 as _sqlite

    if not os.path.isfile(path):
        print(f"No store at {path}")
        return 1

    size = os.path.getsize(path)
    print(f"{os.path.basename(path)} -- {size / (1024 ** 3):.2f} GiB")

    wal = path + "-wal"
    if os.path.isfile(wal) and os.path.getsize(wal) > 64 * 1024 * 1024:
        print(f"  write-ahead log: "
              f"{os.path.getsize(wal) / (1024 ** 3):.2f} GiB -- large. An "
              f"import that was interrupted leaves this behind and it "
              f"slows every read until it is folded in:")
        print(f"      python -m fme store --store {path} --checkpoint")

    try:
        con = _sqlite.connect(ro_uri(path), uri=True)
    except _sqlite.Error as exc:
        print(f"  the file will not open at all: {exc}")
        print("  Restore the most recent copy and re-import anything "
              "added since.")
        return 1

    print("  checking the pages (this reads the whole file; on a large "
          "store it takes a while)")
    try:
        # quick_check first: it finds most damage in a fraction of the
        # time, and on a 150-million-row store that difference is an
        # afternoon.
        quick = con.execute("PRAGMA quick_check(20)").fetchall()
        verdict = [row[0] for row in quick]
    except _sqlite.Error as exc:
        print(f"  the check itself failed: {exc}")
        verdict = ["failed"]

    if verdict == ["ok"]:
        rows = con.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        print(f"  the pages are sound, and the store names {rows:,} "
              f"source(s)")
        print()
        print("  This says the file is structurally intact. It does not")
        print("  say the hashes in it are the right hashes: that is what")
        print("  the sets they came from say, and re-importing a set is")
        print("  how a doubt about one is settled.")
        con.close()
        return 0

    print("  DAMAGE FOUND:")
    for line in verdict[:10]:
        print(f"      {line}")
    print()
    print("  What to do, in order:")
    print("    1. Stop the server. Every further write makes this worse.")
    print("    2. Copy the file, the -wal and the -shm somewhere safe")
    print("       before anything else. They are the evidence of what")
    print("       went wrong.")
    print("    3. Restore the most recent good copy of the store and")
    print("       re-import the sets. A reference store is rebuildable:")
    print("       the sets are files you still have.")
    print("    4. Case contributions since that copy are not in it.")
    print("       Those cases are still on disk and can contribute")
    print("       again -- 'Add this case' on each.")
    print()
    print("  Do not repair in place. A salvaged store is a store whose")
    print("  contents nobody can account for, and this one decides what")
    print("  an examiner is shown about a file.")
    con.close()
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="A hash store shared by every case: import sets once, "
                    "match against them from then on, and contribute what a "
                    "case decided back.")
    ap.add_argument("--store", default="", help="path to the store database")
    ap.add_argument("--import", dest="import_path", default="",
                    help="a hash set to read in")
    ap.add_argument("--kind", default="",
                    choices=["", "projectvic", "nsrl", "custom"])
    ap.add_argument("--name", default="", help="name this source in the store")
    ap.add_argument("--list", action="store_true", help="what is in the store")
    ap.add_argument("--region", default="",
                    help="the grading scheme a new store's contributed codes "
                         "are in (US, UK, AU, ...); refused once it holds any")
    ap.add_argument("--remove", default="", help="remove a source by name")
    ap.add_argument("--scan", default="", help="match a run's manifest.json")
    ap.add_argument("--contribute", default="",
                    help="a run's output folder whose graded files to add")
    ap.add_argument("--source", default="",
                    help="name for the contributed source, usually the case")
    ap.add_argument("--csv", default="", help="write scan results here")
    ap.add_argument("--inspect", default="",
                    help="report what a hash set file is, without importing it")
    ap.add_argument("--checkpoint", action="store_true",
                    help="fold a store's write-ahead log back into it. An "
                         "import stopped part way leaves one, and every read "
                         "afterwards has to walk it")
    ap.add_argument("--move-to", default="",
                    help="copy this store to another path, check the copy is "
                         "complete, and say what to do next. For getting a "
                         "store off a network share onto a local disk")
    ap.add_argument("--integrity", action="store_true",
                    help="ask SQLite whether the store's own pages are "
                         "sound, and report what can be done if they are "
                         "not. Read-only: it changes nothing")
    ap.add_argument("--verify", action="store_true",
                    help="open the store read-only and report what the file "
                         "itself holds, as a colleague or a viewer would see it")
    args = ap.parse_args(argv)
    if not args.store and not args.inspect:
        ap.error("--store is required unless --inspect is used")

    if args.inspect:
        found = inspect_set(args.inspect)
        if not found["exists"]:
            print(f"No such file: {found['path']}", file=sys.stderr)
            return 2
        print(f"File   : {found['path']}")
        print(f"Size   : {found['size']:,} bytes")
        print(f"Looks  : {found['kind']}"
              f"{' (with a byte order mark)' if found['byte_order_mark'] else ''}")
        print(f"Starts : {found['starts_with'][:120]}")
        if found.get("reader_error"):
            print(f"Reader : failed -- {found['reader_error']}")
        print(f"Reader : {len(found['structured_sample'])} hash(es) read "
              f"structurally{': ' + ', '.join(d[:12] + '…' for d in found['structured_sample']) if found['structured_sample'] else ''}")
        print(f"Scanner: {len(found['scanned_sample'])} hash(es) found by "
              f"scanning{': ' + ', '.join(d[:12] + '…' for d in found['scanned_sample']) if found['scanned_sample'] else ''}")
        if "has_media_array" in found:
            print(f"\nThis looks like a Project VIC file, but the reader found "
                  f"no records.")
            print(f"  a \"Media\" array in the first 200 KB: "
                  f"{found['has_media_array']}")
            print(f"  keys near the start: {', '.join(found['top_level_keys'])}")
        return 0

    if getattr(args, "integrity", False):
        return check_integrity(args.store)

    if args.verify:
        found = verify(args.store)
        if not found["exists"]:
            print(f"No such store: {found['path']}", file=sys.stderr)
            return 2
        print(f"Store  : {found['path']}")
        print(f"Size   : {found['size']:,} bytes")
        for suffix, size in (found.get("side_files") or {}).items():
            print(f"         {suffix}: {size:,} bytes "
                  f"{'(unfolded writes live here; copy it alongside the .db)' if suffix == '-wal' and size > 4096 else ''}")
        if found.get("error"):
            print(f"Error  : {found['error']}")
            return 2
        print(f"Hashes : {found['total']:,}  {found.get('by_algorithm', {})}")
        print(f"\n  {'source':34} {'kind':11} {'hashes':>12}  added")
        for src in found["sources"]:
            print(f"  {src['name'][:34]:34} {src['kind']:11} "
                  f"{src['hashes']:>12,}  {src['added_at'][:10]}")
        return 0

    store = HashStore(args.store)
    try:
        if args.region:
            print(f"Scheme : {store.set_region(args.region)}")
        if args.import_path:
            def progress(read, written):
                print(f"\r  {written:,} stored ({read:,} read)", end="", flush=True)
            result = store.import_set(args.import_path, args.kind, args.name,
                                      progress)
            print(f"\r  {result['stored']:,} hash(es) from {result['read']:,} "
                  f"row(s) in {result['seconds']}s as {result['source']!r} "
                  f"({result['kind']})")
            for reason, count in (result.get("skipped") or {}).items():
                print(f"     {count:,} skipped: {reason}")
            if result.get("note"):
                print(f"     {result['note']}")

        if args.remove:
            print(f"removed {store.remove_source(args.remove):,} hash(es)")

        if args.list or not (args.import_path or args.scan or args.contribute
                             or args.remove):
            print(f"Store: {store.path}")
            print(f"Total: {store.size():,} hash(es)\n")
            print(f"  {'source':34} {'kind':11} {'hashes':>12}  added")
            for source in store.sources():
                print(f"  {source['name'][:34]:34} {source['kind']:11} "
                      f"{source['hashes']:>12,}  {source['added_at'][:10]}")

        if args.scan:
            result = store.scan_manifest(args.scan)
            print(f"\nChecked  : {result['checked']:,}")
            print(f"Matched  : {result['matched']:,}")
            print(f"Unmatched: {result['unmatched']:,}  (unknown, which is "
                  f"not the same as cleared)")
            for name, count in sorted(result["by_source"].items(),
                                      key=lambda kv: -kv[1]):
                print(f"   {count:>10,}  {name}")
            if args.csv:
                hashsets.write_csv(
                    {"rows": [{"record": r["record"],
                               "matches": [{"set": m["source"], "kind": m["kind"],
                                            "note": m["note"] or ""}
                                           for m in r["matches"]]}
                              for r in result["rows"]]}, args.csv)
                print(f"\nWrote {os.path.abspath(args.csv)}")

        if args.contribute:
            if not args.source:
                ap.error("--contribute needs --source to name what is added")
            result = store.contribute(args.contribute, args.source)
            print(f"\nAdded {result['hashes']:,} hash(es) from "
                  f"{result['files']:,} categorized file(s) as "
                  f"{result['source']!r}")
            print(f"  {result['skipped_uncategorized']:,} uncategorized "
                  f"file(s) were not contributed")
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
