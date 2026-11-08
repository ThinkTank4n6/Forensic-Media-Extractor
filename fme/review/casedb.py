"""
casedb.py -- the review state that outlives the run.

An extraction produces files. A review produces decisions about them, and
those decisions are the work: what category a file is, what it is flagged
for, what the examiner noted, and when each of those happened. They have to
survive closing the program, and they have to be defensible later, which is
why every change is written immediately and recorded in an audit log rather
than held in memory and saved at the end.

One SQLite file, `case.db`, in the run's output folder. It keys decisions by
**SHA-256**, not by path, so:

  * re-running an extraction into a fresh folder keeps every decision,
  * a file found in three places is categorized once,
  * and moving the output does not lose anything.

The Project VIC categories (0-5) are seeded in every case and cannot be
edited, because they are the scheme the exchange format defines. An examiner
adds their own from 6 up.

Nothing here decides a category. It records the one a person chose.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import sqlite3
import threading
from typing import Optional

__version__ = "1.0.0"

# The Project VIC 2.0 (US) scheme. Locked: these codes mean the same thing in
# every case and in the exchange format, so they are not the examiner's to
# rename.
# The grading scheme, with the key that applies each one. Codes 0-9 take the
# number keys; the three AI categories take the keys an examiner can reach
# without a modifier.
#
# Uncategorized is 8, not 0 -- 0 means Non-Pertinent here, which is a
# decision, not the absence of one. UNCATEGORIZED below is what everything
# starts as and what the backlog filter looks for; nothing should assume 0.
# The rows live in fme/review/regions.py with the other regions' schemes;
# this is the US one, which every case made before v2026.11.317 uses.
from fme.review import regions as _regions  # noqa: E402

CATEGORIES = _regions.get("US").rows()

# What a file carries before anyone has decided anything -- in a US case.
# A case under another region's scheme has its own: ask the case
# (CaseDB.uncategorized), never this, about a case you have open.
UNCATEGORIZED = 8


def severity_rank(code: int) -> tuple:
    """Sort key: the most severe category first.

    Lower codes are more severe -- except 0, Non-Pertinent, which is the
    least. Comparing bare codes made Non-Pertinent "the most severe", so a
    file one set graded 0 and another graded 1 (CAM) was auto-graded
    Non-Pertinent and left the backlog.
    """
    return (code == NON_PERTINENT, code)


NON_PERTINENT = 0

# How the local scheme maps onto Project VIC's own categories when exporting.
# Only the codes VICS defines are carried; the rest have no equivalent, and
# writing a number VICS does not define -- or guessing the nearest one --
# would misstate a finding in a file other agencies read. Those export with a
# null Category, which is what VICS means by "not categorized", and the local
# grading stays in case.db and in this tool's own reports.
# Reading a Project VIC category back onto the local scheme. The inverse of
# VIC_CATEGORY_MAP: a set that asserts a category is asserting it in VICS
# terms, and it has to land on the local code that means the same thing.
VIC_CATEGORY_IN = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}

VIC_CATEGORY_MAP = {
    0: 0,     # Non-Pertinent
    1: 1,     # Child Abuse Material
    2: 2,     # Child Exploitive (non-CAM) / Age Difficult
    3: 3,     # CGI Animation - Child Exploitive
    4: 4,     # Comparison Images
}

# Whether a file was AI-generated now travels as a flag, so it survives the
# export as a grading of the material itself: an AI CSAM file exports as
# category 1, which is what it is, with the flag recorded in this tool's own
# reports alongside.

# Flags every case starts with. Unlike categories these are not locked: a
# case can delete one it does not need and add whatever it does. They are
# seeded once, the first time a case is created, so deleting one does not
# bring it back the next time the case is opened.
# Name, color, and the key that toggles it. Whether an image is
# AI-generated is a property of the material, not a grade of it: a picture is
# CAM or it is not, and separately it was generated or it was not. As a flag
# it combines with any category and several can apply at once, which a
# category cannot do.
DEFAULT_FLAGS = [
    ("S&M Violence", "#b91c1c", ""),
    ("Infant/Toddler", "#be123c", ""),
    ("Bestiality", "#a21caf", ""),
    ("AI CSAM", "#c2410c", "-"),
    ("AI Age Difficult", "#ea580c", "."),
    ("AI Adult", "#6d28d9", "*"),
]

# Examiners' own categories start above the scheme.
FIRST_CUSTOM_CODE = 10

# Bumped when the scheme changes, so an existing case can be migrated rather
# than silently reinterpreted under new meanings.
SCHEME_VERSION = 3

# How many gradings can be taken back. Enough to recover from a mistake made
# a few steps ago; not so many that a long review grows the case database
# with history nobody will ever use.
UNDO_DEPTH = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS categories (
    code INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#6b7280',
    locked INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    key TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#6b7280',
    key TEXT NOT NULL DEFAULT '');

-- Decisions are keyed by content, not by location.
CREATE TABLE IF NOT EXISTS decisions (
    sha256 TEXT PRIMARY KEY,
    category INTEGER NOT NULL DEFAULT 8,
    note TEXT NOT NULL DEFAULT '',
    categorized_at TEXT,
    categorized_by TEXT,
    updated_at TEXT);

CREATE TABLE IF NOT EXISTS file_flags (
    sha256 TEXT NOT NULL,
    flag_id INTEGER NOT NULL,
    added_at TEXT,
    PRIMARY KEY (sha256, flag_id));

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    who TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    sha256 TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '');

-- Results of passes that cost real time to produce: perceptual hashes and
-- hash-set matches. They are not decisions, but recomputing them after every
-- restart wastes an examiner's afternoon, so they live beside the decisions
-- and are rebuilt only when asked for.
-- Regions of a picture the examiner chose to cover while viewing it: a
-- wellness aid, as in Griffeye. Each region is [left, top, width, height]
-- as fractions of the picture. Never applied to the file, an export or a
-- report; only to how this program shows it.
CREATE TABLE IF NOT EXISTS masks (
    sha256 TEXT PRIMARY KEY,
    regions TEXT NOT NULL,
    updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS derived (
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    value TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (kind, sha256));

-- What each file was before a grading, so a grading can be taken back.
-- The audit log records what was done; this records what it replaced, which
-- is the part you need when a key was pressed with the wrong files
-- selected. Rows are grouped into batches: one keystroke is one batch,
-- whether it touched one file or a hundred thousand.
CREATE TABLE IF NOT EXISTS undo_batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    files INTEGER NOT NULL DEFAULT 0,
    undone INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS undo_items (
    batch_id INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    had_row INTEGER NOT NULL,
    category INTEGER,
    categorized_by TEXT NOT NULL DEFAULT '',
    categorized_at TEXT,
    note TEXT,
    PRIMARY KEY (batch_id, sha256));

CREATE INDEX IF NOT EXISTS decisions_category ON decisions(category);
CREATE INDEX IF NOT EXISTS audit_at ON audit(at);
"""


# How a restored decision is written in the audit log: "restored to " and the
# state as JSON -- {"category": 1, "by": "...", "at": "..."}, or null for no
# decision at all. recover.py replays the log into each file's history, and an
# undo that logged one summary line ("12 file(s): category 1") left it no way
# to know which files went back to what: a file graded 1, undone and graded 5
# was "recovered" to 1, a grade nobody had left on it.
RESTORED = "restored to "


def restore_detail(state, reason: str = "") -> str:
    """The audit detail for a file put back to `state` (None: no decision)."""
    text = RESTORED + json.dumps(state, separators=(",", ":"))
    return f"{text} -- {reason}" if reason else text


def parse_restore_detail(detail: str):
    """(True, state) from restore_detail()'s text, (False, None) otherwise."""
    if not (detail or "").startswith(RESTORED):
        return False, None
    try:
        state, _end = json.JSONDecoder().raw_decode(detail[len(RESTORED):])
    except ValueError:
        return False, None
    if state is not None and not (isinstance(state, dict)
                                  and isinstance(state.get("category"), int)):
        return False, None
    return True, state


def _now() -> str:
    # Local time with its offset written in ("...T14:05:00-05:00"). Without
    # the offset a time is ambiguous for the hour the clocks go back, in a
    # record of who decided what, and when; with it, it still reads as the
    # examiner's own clock.
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


class CaseDB:
    # One SQLite connection, used from the server's request threads and from
    # the occasional background snapshot. Every use of it -- reads included
    # -- goes through one lock. Reading without it looks harmless right up
    # until something else is in the middle of a backup on another thread,
    # and then SQLite reports "bad parameter or other API misuse" and the
    # read fails for reasons nothing in the query explains.
    """Review state for one case. Every write commits immediately."""

    def __init__(self, path: str, examiner: str = "", case_number: str = "",
                 region: str = None):
        self.path = os.path.abspath(path)
        self.examiner = examiner
        self._lock = threading.Lock()
        # Set by whoever caches this handle for many callers (the GUI's
        # _case_for). A borrower cannot close it -- see close().
        self.shared = False
        # Held for the whole of an automatic snapshot: two gradings arriving
        # together each read "none for thirty minutes" and each made one.
        self._snapshotting = threading.Lock()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        # Write-ahead logging, so a hard kill loses at most the last statement
        # rather than the case -- on a local disk. WAL needs shared-memory
        # locking a network share does not give, and a case folder on a
        # share (the shared store's reason for this check, and the GUI's
        # cases on a mapped drive) got "disk I/O error" or worse, a case.db
        # two machines wrote past each other. There, a rollback journal.
        from fme.review.hashstore import is_network_path
        self.on_network = is_network_path(self.path)
        self._con.execute("PRAGMA journal_mode=DELETE" if self.on_network
                          else "PRAGMA journal_mode=WAL")
        self._con.executescript(SCHEMA)
        self.region = self._settle_region(region)
        self._refuse_if_too_new()
        self.scheme = _regions.get(self.region)
        self.uncategorized = self.scheme.uncategorized
        self.non_pertinent = self.scheme.nonpertinent
        self.first_custom = self.scheme.first_custom
        self._seed(case_number)

    @contextlib.contextmanager
    def _guard(self):
        """The connection's lock, and a rollback if what ran under it failed.

        Every method shares one connection. A write that raised part way --
        a bad digest in a list -- left its transaction open: the undo batch
        it had begun was committed by the next, unrelated write (so the
        examiner's next undo undid nothing), and case.db's write lock was
        held until then, so every other connection got "database is locked"
        (v2026.11.317).
        """
        with self._lock:
            try:
                yield
            except BaseException:
                if self._con.in_transaction:
                    self._con.rollback()
                raise

    def _settle_region(self, wanted) -> str:
        """The region this case grades under, fixed when it was created.

        A case that already exists keeps its own, whatever is asked: its
        stored codes mean something only under that scheme. One made before
        regions existed is a US case. Only a new case takes `wanted`.
        """
        with self._guard():
            row = self._con.execute(
                "SELECT value FROM meta WHERE key='category_region'").fetchone()
            if row is not None:
                return _regions.normalize(row["value"])
            existed = self._con.execute(
                "SELECT 1 FROM meta WHERE key='created'").fetchone() is not None
            region = "US" if existed else _regions.normalize(wanted)
            self._con.execute(
                "INSERT INTO meta (key, value) VALUES ('category_region', ?)",
                (region,))
            if not existed and region != "US":
                self._con.execute(
                    "INSERT INTO audit (at, who, action, sha256, detail) "
                    "VALUES (?,?,?,?,?)",
                    (_now(), self.examiner, "region", "",
                     f"graded under {self.region_label(region)}"))
            self._con.commit()
            return region

    # The first build that reads a case graded under another region's
    # scheme correctly. Written into every such case, and refused by any
    # build below it from v2026.11.318 on.
    MIN_READER_FOR_REGIONS = 318

    def _refuse_if_too_new(self) -> None:
        """Refuse a case that says it needs a newer build than this one."""
        from fme import build_number, RELEASE as program
        with self._guard():
            if self.region != "US":
                self._con.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES "
                    "('min_reader_build', ?)", (str(self.MIN_READER_FOR_REGIONS),))
                self._con.commit()
            row = self._con.execute(
                "SELECT value FROM meta WHERE key='min_reader_build'").fetchone()
        try:
            needed = int(row["value"]) if row else 0
        except (TypeError, ValueError):
            needed = 0
        if needed and build_number(program) and build_number(program) < needed:
            self._con.close()
            raise RuntimeError(
                f"This case needs Forensic Media Extractor build {needed} or "
                f"newer to be read correctly; this is {program}. Open it with "
                f"the newer program.")

    @staticmethod
    def region_label(region) -> str:
        return _regions.get(region).label

    def severity_rank(self, code: int) -> tuple:
        """The most severe grade first, in this case's scheme."""
        return self.scheme.severity_rank(code)

    def _seed(self, case_number: str) -> None:
        with self._guard():
            # A case made before the key column existed needs it added; SQLite
            # has no "add column if missing", so ask first.
            columns = {row[1] for row in
                       self._con.execute("PRAGMA table_info(categories)")}
            if "key" not in columns:
                self._con.execute(
                    "ALTER TABLE categories ADD COLUMN key TEXT NOT NULL DEFAULT ''")
            # Undo keeps a file's note and grading time too (v2026.11.304).
            undo_columns = {row[1] for row in
                            self._con.execute("PRAGMA table_info(undo_items)")}
            for column in ("categorized_at", "note"):
                if column not in undo_columns:
                    self._con.execute(
                        f"ALTER TABLE undo_items ADD COLUMN {column} TEXT")
            flag_columns = {row[1] for row in
                            self._con.execute("PRAGMA table_info(flags)")}
            if "key" not in flag_columns:
                self._con.execute(
                    "ALTER TABLE flags ADD COLUMN key TEXT NOT NULL DEFAULT ''")

            version = self._con.execute(
                "SELECT value FROM meta WHERE key='scheme_version'").fetchone()
            version = int(version["value"]) if version else 0
            # The older numberings were US ones; no other region's case
            # predates its own scheme.
            if version and version < SCHEME_VERSION and self.region == "US":
                self._migrate_scheme(version)

            existing = {row["code"] for row in
                        self._con.execute("SELECT code FROM categories")}
            # Another region's locked categories are marked 2, not 1. A build
            # from before regions rewrites every "locked=1" row with the US
            # names when it opens a case -- a UK case's "SC Category A"
            # became "Child Abuse Material (CAM)" -- and it leaves a 2 alone.
            # Everything here treats any non-zero value as locked.
            lock = 1 if self.region == "US" else 2
            for position, (code, key, name, color) in enumerate(self.scheme.rows()):
                if code in existing:
                    # Keep the scheme's wording current without touching an
                    # examiner's own categories or their decisions.
                    self._con.execute(
                        "UPDATE categories SET name=?, color=?, key=?, locked=?, "
                        "position=? WHERE code=? AND locked!=0",
                        (name, color, key, lock, position, code))
                else:
                    self._con.execute(
                        "INSERT INTO categories (code, name, color, locked, "
                        "position, key) VALUES (?,?,?,?,?,?)",
                        (code, name, color, lock, position, key))

            for key, value in (("created", _now()), ("case_number", case_number),
                               ("examiner", self.examiner),
                               ("version", __version__)):
                if value:
                    self._con.execute(
                        "INSERT OR IGNORE INTO meta (key, value) VALUES (?,?)",
                        (key, value))
            self._con.execute(
                "INSERT INTO meta (key, value) VALUES ('scheme_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEME_VERSION),))

            # Once per case, not once per open: a flag an examiner deleted
            # should stay deleted.
            seeded = self._con.execute(
                "SELECT value FROM meta WHERE key='flags_seeded'").fetchone()
            if not seeded:
                for name, color, key in DEFAULT_FLAGS:
                    self._con.execute(
                        "INSERT OR IGNORE INTO flags (name, color, key) "
                        "VALUES (?,?,?)", (name, color, key))
                self._con.execute(
                    "INSERT INTO meta (key, value) VALUES ('flags_seeded', ?)",
                    (_now(),))
                self._con.execute(
                    "INSERT INTO audit (at, who, action, sha256, detail) "
                    "VALUES (?,?,?,?,?)",
                    (_now(), self.examiner, "flags-seeded", "",
                     ", ".join(name for name, _, _ in DEFAULT_FLAGS)))
            self._con.commit()

    # Where a file graded as one of the old AI categories belongs now: the
    # grading it always implied, plus the flag that records how it was made.
    AI_CATEGORY_TO_FLAG = {
        10: (1, "AI CSAM"),            # AI CSAM          -> CAM + flag
        11: (2, "AI Age Difficult"),   # AI Age Difficult -> Age Difficult + flag
        12: (5, "AI Adult"),           # AI Adult         -> Adult Porn + flag
    }

    def _migrate_scheme(self, from_version: int) -> None:
        """Move decisions made under an older scheme onto the current codes.

        Version 1 numbered things differently in the two places it matters:
        0 meant Uncategorized and 5 meant Non-pertinent, where now 0 means
        Non-Pertinent and 8 means Uncategorized. Renaming the rows without
        moving the decisions would turn every untouched file into a positive
        finding of "not pertinent" -- a decision nobody made.
        """
        if from_version < 3:
            self._migrate_ai_categories()
        if from_version >= 2:
            return
        moved = self._con.execute(
            "SELECT COUNT(*) AS n FROM decisions WHERE category IN (0, 5)"
        ).fetchone()["n"]
        self._con.execute("""
            UPDATE decisions SET category = CASE category
                WHEN 0 THEN 8      -- was Uncategorized, now 8
                WHEN 5 THEN 0      -- was Non-pertinent, now 0
                ELSE category      -- 1-4 mean the same in both schemes
            END, updated_at = ?
            WHERE category IN (0, 5)""", (_now(),))
        self._con.execute(
            "INSERT INTO audit (at, who, action, sha256, detail) "
            "VALUES (?,?,?,?,?)",
            (_now(), self.examiner, "scheme-migrated", "",
             f"v{from_version} to v{SCHEME_VERSION}: {moved} decision(s) "
             f"remapped (0 to 8, 5 to 0)"))

    def _migrate_ai_categories(self) -> None:
        """Move files graded under the old AI categories onto a grade and a flag.

        Nothing is lost and nothing is invented: the old category said both
        things at once, so it becomes the grading it implied plus the flag
        that records the rest. The alternative -- dropping them back to
        uncategorized -- would discard a decision an examiner actually made.
        """
        moved = 0
        for old_code, (new_code, flag_name) in self.AI_CATEGORY_TO_FLAG.items():
            rows = [r["sha256"] for r in self._con.execute(
                "SELECT sha256 FROM decisions WHERE category=?", (old_code,))]
            if not rows:
                continue
            self._con.execute(
                "INSERT OR IGNORE INTO flags (name, color, key) VALUES (?,?,?)",
                (flag_name, "#c2410c", ""))
            flag_id = self._con.execute(
                "SELECT id FROM flags WHERE name=?", (flag_name,)).fetchone()["id"]
            for sha256 in rows:
                self._con.execute(
                    "UPDATE decisions SET category=?, updated_at=? WHERE sha256=?",
                    (new_code, _now(), sha256))
                self._con.execute(
                    "INSERT OR IGNORE INTO file_flags (sha256, flag_id, added_at) "
                    "VALUES (?,?,?)", (sha256, flag_id, _now()))
                moved += 1
        self._con.execute("DELETE FROM categories WHERE code IN (10, 11, 12)")
        if moved:
            self._con.execute(
                "INSERT INTO audit (at, who, action, sha256, detail) "
                "VALUES (?,?,?,?,?)",
                (_now(), self.examiner, "ai-categories-to-flags", "",
                 f"{moved} file(s): AI categories became a grading plus a flag"))

    # -- reading ---------------------------------------------------------
    def categories(self) -> list:
        with self._guard():
            rows = self._con.execute(
                "SELECT code, name, color, locked, key FROM categories "
                "ORDER BY position, code")
            return [dict(row) for row in rows]

    def flags(self) -> list:
        with self._guard():
            # Seeded flags keep the order they were defined in, which is the order
            # an examiner reads them in the interface; anything added later
            # follows alphabetically.
            order = {name: index for index, (name, _, _) in enumerate(DEFAULT_FLAGS)}
            rows = [dict(row) for row in
                    self._con.execute("SELECT id, name, color, key FROM flags")]
            return sorted(rows, key=lambda row: (order.get(row["name"], len(order)),
                                                 row["name"].lower()))

    def _record_undo(self, sha256_list, action: str, detail: str) -> int:
        """Remember what these files were, before they are changed.

        Called with the lock already held. Keeps the last UNDO_DEPTH
        batches: enough to take back a mistake made a few steps ago, not so
        many that the case database grows without bound on a long review.
        """
        cursor = self._con.execute(
            "INSERT INTO undo_batches (at, action, detail, files) "
            "VALUES (?,?,?,?)", (_now(), action, detail, len(sha256_list)))
        batch_id = cursor.lastrowid

        existing = {}
        for chunk_start in range(0, len(sha256_list), 500):
            chunk = sha256_list[chunk_start:chunk_start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self._con.execute(
                    f"SELECT sha256, category, categorized_by, categorized_at, "
                    f"note FROM decisions WHERE sha256 IN ({marks})", chunk):
                existing[row["sha256"]] = (row["category"],
                                           row["categorized_by"] or "",
                                           row["categorized_at"], row["note"])

        blank = (None, "", None, None)
        self._con.executemany(
            "INSERT OR REPLACE INTO undo_items "
            "(batch_id, sha256, had_row, category, categorized_by, "
            "categorized_at, note) VALUES (?,?,?,?,?,?,?)",
            [(batch_id, sha256, 1 if sha256 in existing else 0,
              *existing.get(sha256, blank))
             for sha256 in sha256_list])

        old = self._con.execute(
            "SELECT batch_id FROM undo_batches ORDER BY batch_id DESC "
            "LIMIT -1 OFFSET ?", (UNDO_DEPTH,)).fetchall()
        for row in old:
            self._con.execute("DELETE FROM undo_items WHERE batch_id=?",
                              (row["batch_id"],))
            self._con.execute("DELETE FROM undo_batches WHERE batch_id=?",
                              (row["batch_id"],))
        return batch_id

    def undoable(self, limit: int = 10) -> list:
        """The gradings that can still be taken back, newest first."""
        with self._guard():
            return [dict(row) for row in self._con.execute(
                "SELECT batch_id, at, action, detail, files FROM undo_batches "
                "WHERE undone=0 ORDER BY batch_id DESC LIMIT ?", (limit,))]

    def undo(self, batch_id: int = 0) -> dict:
        """Put a grading back the way it was.

        Restores every file in the batch to the category it held before --
        including back to having no decision at all, which is the case that
        matters when a key is pressed with everything selected by accident.
        """
        with self._guard():
            if batch_id:
                row = self._con.execute(
                    "SELECT batch_id, at, action, detail, files FROM "
                    "undo_batches WHERE batch_id=? AND undone=0",
                    (batch_id,)).fetchone()
            else:
                row = self._con.execute(
                    "SELECT batch_id, at, action, detail, files FROM "
                    "undo_batches WHERE undone=0 ORDER BY batch_id DESC "
                    "LIMIT 1").fetchone()
            if row is None:
                return {"undone": 0, "reason": "There is nothing to undo."}

            batch = row["batch_id"]
            # Two things an undo must not do (v2026.11.317): put a file into
            # a category that has since been deleted -- graded with a code no
            # category has -- and, for an older batch named by number, wipe
            # out what was decided about its files after it.
            known = {r["code"] for r in self._con.execute("SELECT code FROM categories")}
            gone = sorted({r["category"] for r in self._con.execute(
                "SELECT category FROM undo_items WHERE batch_id=? AND had_row=1 "
                "AND category IS NOT NULL", (batch,))} - known)
            if gone:
                # Set aside, so the undo stack is not stuck behind it: the
                # next undo goes on to the batch before.
                self._con.execute("UPDATE undo_batches SET undone=1 WHERE batch_id=?",
                                  (batch,))
                self._log("undo-refused", "", f"batch {batch}: category "
                          f"{', '.join(map(str, gone))} no longer exists")
                self._con.commit()
                return {"undone": 0, "batch": batch,
                        "reason": (f"This would put files back into category "
                                   f"{', '.join(map(str, gone))}, which this case "
                                   f"no longer has. Nothing was changed, and "
                                   f"the next undo goes on to the one before.")}
            later = self._con.execute(
                "SELECT COUNT(DISTINCT i.sha256) FROM undo_items i JOIN undo_batches b "
                "ON b.batch_id = i.batch_id WHERE i.batch_id > ? AND b.undone = 0 "
                "AND i.sha256 IN (SELECT sha256 FROM undo_items WHERE batch_id=?)",
                (batch, batch)).fetchone()[0]
            if later:
                return {"undone": 0, "batch": batch,
                        "reason": (f"{later} of these files were graded again "
                                   f"since. Undo the later gradings first; "
                                   f"nothing was changed.")}
            restored = 0
            stamp = _now()
            per_file = []
            # The grading goes back; a note does not go with it. Undo kept
            # only the category, so undoing a grading deleted the whole row
            # -- a note written since included -- and undoing a "clear" put
            # the category back without the note the clear had removed. The
            # time a file was graded is the original one, not the undo's.
            for item in self._con.execute(
                    "SELECT sha256, had_row, category, categorized_by, "
                    "categorized_at, note FROM undo_items WHERE batch_id=?",
                    (batch,)):
                if item["had_row"]:
                    self._con.execute(
                        "INSERT INTO decisions (sha256, category, note, "
                        "categorized_at, categorized_by, updated_at) "
                        "VALUES (?,?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET "
                        "category=excluded.category, "
                        "categorized_at=excluded.categorized_at, "
                        "categorized_by=excluded.categorized_by, "
                        "updated_at=excluded.updated_at",
                        (item["sha256"], item["category"], item["note"] or "",
                         item["categorized_at"], item["categorized_by"], stamp))
                    per_file.append((item["sha256"], {
                        "category": item["category"],
                        "by": item["categorized_by"],
                        "at": item["categorized_at"]}))
                else:
                    per_file.append((item["sha256"], None))
                    current = self._con.execute(
                        "SELECT note FROM decisions WHERE sha256=?",
                        (item["sha256"],)).fetchone()
                    if current is not None and (current["note"] or ""):
                        # Ungraded again, the note kept.
                        self._con.execute(
                            "UPDATE decisions SET category=?, categorized_at="
                            "NULL, categorized_by=NULL, updated_at=? "
                            "WHERE sha256=?",
                            (self.uncategorized, stamp, item["sha256"]))
                    else:
                        # It had no decision at all before; it should have
                        # none again, not a row saying "uncategorized".
                        self._con.execute("DELETE FROM decisions WHERE sha256=?",
                                          (item["sha256"],))
                restored += 1

            self._con.execute("UPDATE undo_batches SET undone=1 WHERE batch_id=?",
                              (batch,))
            # Each file and what it went back to, then the summary. The
            # summary alone was all an undo used to leave, and recovery
            # replaying the log could not see it (recover.py). " [per file]"
            # tells recovery the rows above it are there.
            for sha256, state in per_file:
                self._log("undo-restore", sha256, restore_detail(state),
                          at=stamp)
            self._log("undo", "", f"{restored} file(s): {row['detail']} "
                                  f"[per file]", at=stamp)
            self._con.commit()
            self._checkpoint() if hasattr(self, "_checkpoint") else None
        return {"undone": restored, "batch": batch, "detail": row["detail"],
                "at": row["at"], "files": [sha256 for sha256, _ in per_file]}

    def restore(self, states: dict, reason: str) -> dict:
        """Put files back to given states, all or none, in one transaction.

        `states` is {sha256: None or {"category", "by", "at"}}: None is no
        decision at all; otherwise the grading, the person or hash set that
        made it and when, as they were -- not the restorer's name and the
        time of the restoration, which made a recovered case say that the
        examiner who ran recovery graded every file in it that minute.
        A note is never touched; a file with a note and no decision keeps
        the note as an uncategorized row. Each file is logged with what it
        went back to, and the whole is one undo batch, so it can be taken
        back from the interface like a grading.
        """
        stamp = _now()
        restored = cleared = 0
        with self._guard():
            known = {r["code"] for r in
                     self._con.execute("SELECT code FROM categories")}
            missing = sorted({st["category"] for st in states.values()
                              if st is not None} - known)
            if missing:
                raise ValueError(
                    f"The history puts files back into category "
                    f"{', '.join(map(str, missing))}, which this case no "
                    f"longer has. Nothing was changed.")
            try:
                self._record_undo(list(states), "recover", reason)
                for sha256, state in states.items():
                    if state is None:
                        current = self._con.execute(
                            "SELECT note FROM decisions WHERE sha256=?",
                            (sha256,)).fetchone()
                        if current is not None and (current["note"] or ""):
                            self._con.execute(
                                "UPDATE decisions SET category=?, "
                                "categorized_at=NULL, categorized_by=NULL, "
                                "updated_at=? WHERE sha256=?",
                                (self.uncategorized, stamp, sha256))
                        else:
                            self._con.execute(
                                "DELETE FROM decisions WHERE sha256=?", (sha256,))
                        cleared += 1
                    else:
                        self._con.execute(
                            "INSERT INTO decisions (sha256, category, "
                            "categorized_at, categorized_by, updated_at) "
                            "VALUES (?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE "
                            "SET category=excluded.category, "
                            "categorized_at=excluded.categorized_at, "
                            "categorized_by=excluded.categorized_by, "
                            "updated_at=excluded.updated_at",
                            (sha256, state["category"], state.get("at"),
                             state.get("by"), stamp))
                    self._log("recover-restore", sha256,
                              restore_detail(state, reason), at=stamp)
                    restored += 1
                self._log("recover", "", f"{restored} file(s): {reason}",
                          at=stamp)
                self._con.commit()
            except BaseException:
                self._con.rollback()
                raise
        return {"restored": restored, "cleared": cleared}

    def clear_decisions(self, sha256_list) -> int:
        """Remove these files' decisions entirely.

        Not the same as setting them to uncategorized: a file with no row
        has never been looked at, and a file set to uncategorized was looked
        at and left. Recovery has to be able to restore the first state as
        well as the second.
        """
        if isinstance(sha256_list, str):
            sha256_list = [sha256_list]
        with self._guard():
            self._record_undo(sha256_list, "clear", "decision removed")
            for sha256 in sha256_list:
                self._con.execute("DELETE FROM decisions WHERE sha256=?",
                                  (sha256,))
                self._log("clear", sha256, "decision removed")
            self._con.commit()
        return len(sha256_list)

    def decision(self, sha256: str) -> dict:
        with self._guard():
            row = self._con.execute(
                "SELECT category, note, categorized_at, categorized_by "
                "FROM decisions WHERE sha256=?", (sha256,)).fetchone()
            flags = [r["flag_id"] for r in self._con.execute(
                "SELECT flag_id FROM file_flags WHERE sha256=?", (sha256,))]
            if row is None:
                return {"category": self.uncategorized, "note": "", "flags": flags,
                        "categorized_at": None, "categorized_by": None,
                        "auto": False}
            result = dict(row)
            result["auto"] = (result.get("categorized_by") or "").startswith("hash set:")
            result["flags"] = flags
            return result

    def decisions(self) -> dict:
        """Everything decided, for painting a gallery in one query."""
        with self._guard():
            out = {}
            for row in self._con.execute(
                    "SELECT sha256, category, note, categorized_by FROM decisions"):
                out[row["sha256"]] = {"category": row["category"],
                                      "note": row["note"],
                                      "by": row["categorized_by"] or "",
                                      # A category a hash set asserted, rather
                                      # than one a person chose.
                                      "auto": (row["categorized_by"] or "")
                                              .startswith("hash set:"),
                                      "flags": []}
            for row in self._con.execute("SELECT sha256, flag_id FROM file_flags"):
                out.setdefault(row["sha256"],
                               {"category": self.uncategorized, "note": "", "by": "",
                                "auto": False, "flags": []})
                out[row["sha256"]]["flags"].append(row["flag_id"])
            return out

    def version(self) -> tuple:
        """Changes when any decision may have: this handle's own writes
        (total_changes) and any other connection's commits (data_version).
        What a cached view of the decisions is keyed by."""
        with self._guard():
            other = self._con.execute("PRAGMA data_version").fetchone()[0]
            return (self._con.total_changes, other)

    def decisions_cached(self) -> tuple:
        """(version, decisions()), read again only when version() moves. A
        large case's gallery asks for a page on every scroll, and reading
        every decision for each was the cost of the request."""
        now = self.version()
        held = getattr(self, "_decisions_held", None)
        if held is None or held[0] != now:
            held = (now, self.decisions())
            self._decisions_held = held
        return held

    def counts(self) -> dict:
        with self._guard():
            return {row["category"]: row["n"] for row in self._con.execute(
                "SELECT category, COUNT(*) AS n FROM decisions GROUP BY category")}

    def audit(self, limit: int = 500) -> list:
        with self._guard():
            return [dict(row) for row in self._con.execute(
                "SELECT at, who, action, sha256, detail FROM audit "
                "ORDER BY id DESC LIMIT ?", (limit,))]

        # -- writing ---------------------------------------------------------
    def log(self, action: str, sha256: str = "", detail: str = "") -> None:
        """Write one audit entry and commit it.

        For callers outside this class. They used _log, which leaves the
        INSERT uncommitted: on the case handle the interface shares, that
        held case.db's write lock after a reprocess or a C4All import, and
        every other connection to the case -- a report, a contribution --
        failed with "database is locked".
        """
        with self._guard():
            self._log(action, sha256, detail)
            self._con.commit()

    def _log(self, action: str, sha256: str = "", detail: str = "",
             at: str = "") -> None:
        self._con.execute(
            "INSERT INTO audit (at, who, action, sha256, detail) VALUES (?,?,?,?,?)",
            (at or _now(), self.examiner, action, sha256, detail))

    def set_category(self, sha256_list, code: int, by: str = "",
                     only_if_undecided: bool = False) -> int:
        """Categorize one file or many. Returns how many were changed.

        ``by`` records what made the decision. An examiner's name and "hash
        set: VIC_US_2026" are different claims, and a report that cannot tell
        them apart is misleading: one is a judgement, the other is a
        statement that a file's hash appears in somebody else's list.
        """
        if isinstance(sha256_list, str):
            sha256_list = [sha256_list]
        stamp = _now()
        with self._guard():
            # Checked under the same lock as the write. Read before it, on
            # the shared connection, a category removed in between left
            # files graded with a code no category had.
            if self._con.execute("SELECT 1 FROM categories WHERE code=?",
                                 (code,)).fetchone() is None:
                raise ValueError(f"No category with code {code}")
            if only_if_undecided:
                # For a hash set's grade: only a file nobody has graded, and
                # asked here, under the write's own lock. Asked once before a
                # scan's writes, a grade the examiner made during the scan
                # was replaced by the set's -- 2 became 1 (CAM) "by hash
                # set" while the page showed 2 (v2026.11.319).
                sha256_list = [d for d in sha256_list if (lambda row: row is None
                               or row["category"] == self.uncategorized)(
                    self._con.execute("SELECT category FROM decisions WHERE sha256=?",
                                      (d,)).fetchone())]
                if not sha256_list:
                    return 0
            self._record_undo(sha256_list, "categorize",
                              f"category {code}" + (f" by {by}" if by else ""))
            for sha256 in sha256_list:
                self._con.execute(
                    "INSERT INTO decisions (sha256, category, categorized_at, "
                    "categorized_by, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(sha256) DO UPDATE SET category=excluded.category, "
                    "categorized_at=excluded.categorized_at, "
                    "categorized_by=excluded.categorized_by, "
                    "updated_at=excluded.updated_at",
                    (sha256, code, stamp, by or self.examiner, stamp))
                # Logged at the time the decision holds, so recovery can put
                # back the grading's own time, not one a second later.
                self._log("categorize", sha256,
                          f"category {code}" + (f" by {by}" if by else ""),
                          at=stamp)
            self._con.commit()
        return len(sha256_list)

    def set_note(self, sha256: str, note: str) -> None:
        with self._guard():
            self._con.execute(
                "INSERT INTO decisions (sha256, category, note, updated_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(sha256) DO UPDATE SET note=excluded.note, "
                "updated_at=excluded.updated_at",
                (sha256, self.uncategorized, note, _now()))
            self._log("note", sha256, f"{len(note)} character(s)")
            self._con.commit()

    MAX_MASKS = 64

    @staticmethod
    def _clean_regions(regions) -> list:
        """[[left, top, width, height]] as fractions of the picture, each
        kept inside it; anything else is refused rather than stored."""
        clean = []
        for region in list(regions or [])[:CaseDB.MAX_MASKS]:
            try:
                left, top, width, height = (float(v) for v in region)
            except (TypeError, ValueError):
                raise ValueError("a mask is [left, top, width, height]") from None
            left, top = min(max(left, 0.0), 1.0), min(max(top, 0.0), 1.0)
            width, height = min(max(width, 0.0), 1.0 - left), min(max(height, 0.0), 1.0 - top)
            if width > 0.002 and height > 0.002:
                clean.append([round(left, 5), round(top, 5), round(width, 5), round(height, 5)])
        return clean

    def masks(self, sha256: str) -> list:
        with self._guard():
            row = self._con.execute("SELECT regions FROM masks WHERE sha256=?",
                                    (sha256,)).fetchone()
        return json.loads(row[0]) if row else []

    def set_masks(self, sha256: str, regions) -> list:
        if not sha256:
            raise ValueError("which file?")
        clean = self._clean_regions(regions)
        with self._guard():
            if clean:
                self._con.execute(
                    "INSERT INTO masks (sha256, regions, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(sha256) DO UPDATE SET regions=excluded.regions, "
                    "updated_at=excluded.updated_at",
                    (sha256, json.dumps(clean), _now()))
            else:
                self._con.execute("DELETE FROM masks WHERE sha256=?", (sha256,))
            self._log("mask", sha256, f"{len(clean)} region(s) covered when viewed")
            self._con.commit()
        return clean

    def add_flag(self, name: str, color: str = "#6b7280", key: str = "") -> int:
        name = name.strip()
        if not name:
            raise ValueError("A flag needs a name")
        with self._guard():
            made = self._con.execute(
                "INSERT OR IGNORE INTO flags (name, color, key) VALUES (?,?,?)",
                (name, color, key)).rowcount
            # Logged only when a flag was made: re-adding an existing one
            # wrote "flag-created" for a flag that already was.
            if made:
                self._log("flag-created", "", name)
            self._con.commit()
            row = self._con.execute("SELECT id FROM flags WHERE name=?",
                                    (name,)).fetchone()
        return row["id"]

    def remove_flag(self, flag_id: int) -> None:
        with self._guard():
            self._con.execute("DELETE FROM file_flags WHERE flag_id=?", (flag_id,))
            self._con.execute("DELETE FROM flags WHERE id=?", (flag_id,))
            self._log("flag-deleted", "", str(flag_id))
            self._con.commit()

    def set_file_flag(self, sha256: str, flag_id: int, on: bool) -> None:
        with self._guard():
            if on:
                self._con.execute(
                    "INSERT OR IGNORE INTO file_flags (sha256, flag_id, added_at) "
                    "VALUES (?,?,?)", (sha256, flag_id, _now()))
            else:
                self._con.execute(
                    "DELETE FROM file_flags WHERE sha256=? AND flag_id=?",
                    (sha256, flag_id))
            self._log("flag" if on else "unflag", sha256, str(flag_id))
            self._con.commit()

    def add_category(self, name: str, color: str = "#6b7280",
                     code: int = None) -> int:
        """An examiner's own category, numbered above the scheme's own codes.

        `code`, when given and free, is the number it gets -- so a saved set
        applied to several cases numbers "Series X" alike in all of them.
        """
        name = name.strip()
        if not name:
            raise ValueError("A category needs a name")
        with self._guard():
            taken = code is None or code < self.first_custom or \
                self._con.execute("SELECT 1 FROM categories WHERE code=?",
                                  (code,)).fetchone() is not None
            if taken:
                row = self._con.execute(
                    "SELECT MAX(code) AS top FROM categories").fetchone()
                code = max(self.first_custom - 1, row["top"] or 0) + 1
            self._con.execute(
                "INSERT INTO categories (code, name, color, locked, position) "
                "VALUES (?,?,?,0,?)", (code, name, color, code))
            self._log("category-created", "", f"{code}: {name}")
            self._con.commit()
        return code

    def rename_category(self, code: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("A category needs a name")
        # The check and the change under one lock: read outside it, the
        # category could be deleted between the two.
        with self._guard():
            row = self._con.execute("SELECT locked FROM categories WHERE code=?",
                                    (code,)).fetchone()
            if row is None:
                raise ValueError(f"No category with code {code}")
            if row["locked"]:
                raise ValueError(
                    f"Category {code} is part of the grading scheme and means the "
                    f"same thing in every case, so it cannot be renamed")
            self._con.execute("UPDATE categories SET name=? WHERE code=?",
                              (name, code))
            self._log("category-renamed", "", f"{code}: {name}")
            self._con.commit()

    def remove_category(self, code: int) -> int:
        """Delete an examiner's category; its files become uncategorized."""
        with self._guard():
            row = self._con.execute("SELECT locked FROM categories WHERE code=?",
                                    (code,)).fetchone()
            if row is None:
                raise ValueError(f"No category with code {code}")
            if row["locked"]:
                raise ValueError("Scheme categories cannot be deleted")
            files = [r["sha256"] for r in self._con.execute(
                "SELECT sha256 FROM decisions WHERE category=?", (code,))]
            affected = len(files)
            stamp = _now()
            self._con.execute(
                "UPDATE decisions SET category=?, updated_at=? WHERE category=?",
                (self.uncategorized, stamp, code))
            # Which files, not only how many: the history of each file is
            # what recovery replays, and a count names none of them.
            for sha256 in files:
                self._log("uncategorize", sha256,
                          f"category {code} deleted", at=stamp)
            self._con.execute("DELETE FROM categories WHERE code=?", (code,))
            self._log("category-deleted", "",
                      f"{code}, {affected} file(s) returned to uncategorized")
            self._con.commit()
        return affected

    # -- derived results -------------------------------------------------
    def save_derived(self, kind: str, values: dict) -> int:
        """Store a pass's results, replacing anything of that kind."""
        with self._guard():
            self._replace_derived(kind, values)
        return len(values)

    def merge_derived(self, kind: str, merge) -> dict:
        """Read one kind, merge into it and save it, as one step.

        derived() then save_derived() took the lock twice, and between the
        two another request could save: the store check and the hash-set
        check each merged into what they had read, and whichever saved
        second erased the other's matches from the case -- and so from every
        report -- while the page, merged separately, still showed them.
        Returns what was saved.
        """
        with self._guard():
            values = merge(self._read_derived(kind))
            self._replace_derived(kind, values)
        return values

    def derived(self, kind: str) -> dict:
        with self._guard():
            return self._read_derived(kind)

    # The two below expect self._lock to be held.
    def _read_derived(self, kind: str) -> dict:
        rows = self._con.execute(
            "SELECT sha256, value FROM derived WHERE kind=?", (kind,))
        out = {}
        for sha256, value in rows:
            try:
                out[sha256] = json.loads(value)
            except json.JSONDecodeError:
                continue
        return out

    def _replace_derived(self, kind: str, values: dict) -> None:
        stamp = _now()
        self._con.execute("DELETE FROM derived WHERE kind=?", (kind,))
        self._con.executemany(
            "INSERT OR REPLACE INTO derived (kind, sha256, value, at) "
            "VALUES (?,?,?,?)",
            [(kind, sha256, json.dumps(value), stamp)
             for sha256, value in values.items()])
        self._log("derived-saved", "", f"{kind}: {len(values)} file(s)")
        self._con.commit()

    def derived_at(self, kind: str) -> str:
        with self._guard():
            row = self._con.execute(
                "SELECT MAX(at) AS at FROM derived WHERE kind=?", (kind,)).fetchone()
            return (row["at"] or "") if row else ""

    def autosnapshot(self, every_minutes: int = 30) -> str:
        """A snapshot if enough time has passed since the last one.

        Called as the case is worked rather than on a timer thread: a case
        nobody is touching does not need snapshots, and a thread that keeps
        running after the window closes is its own problem.
        """
        if not self._snapshotting.acquire(blocking=False):
            return ""           # one is being made now; that one will do
        try:
            return self._autosnapshot(every_minutes)
        finally:
            self._snapshotting.release()

    def _autosnapshot(self, every_minutes: int) -> str:
        # Under the lock like every other read of the shared connection: a
        # read here could interleave with another thread's write on it.
        with self._guard():
            last = self._con.execute(
                "SELECT value FROM meta WHERE key='last_snapshot'").fetchone()
        if last:
            try:
                # Older cases stored naive local time; astimezone() reads
                # those as local, so both kinds compare.
                when = _dt.datetime.fromisoformat(last["value"]).astimezone()
                now = _dt.datetime.now().astimezone()
                if (now - when).total_seconds() < every_minutes * 60:
                    return ""
            except (ValueError, TypeError):
                pass
        path = self.snapshot("auto")
        with self._guard():
            self._con.execute(
                "INSERT INTO meta (key, value) VALUES ('last_snapshot', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_now(),))
            self._con.commit()
        return path

    def snapshot(self, label: str = "") -> str:
        """A standalone copy of the case, for rolling back to."""
        folder = os.path.join(os.path.dirname(self.path), "backups")
        os.makedirs(folder, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        clean = "".join(c for c in label if c.isalnum() or c in "-_")[:40]
        # Named to the second, so two in one second shared a name and the
        # second backup replaced the first. Claimed by creating it.
        base = os.path.join(folder, f"case_{stamp}{'_' + clean if clean else ''}")
        target, n = base + ".db", 1
        while True:
            try:
                os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                n += 1
                target = f"{base}_{n}.db"
        # Backed up through a second, read-only connection rather than the
        # shared one. Two reasons, and the first cost an examiner an
        # afternoon: SQLite's backup takes over the connection it reads
        # from, so anything else using that connection at the time fails
        # with an error that says nothing about backups. The second is that
        # copying a case of any size takes a moment, and grading should not
        # wait behind it.
        with self._guard():
            self._con.commit()
        from fme.sqlite_uri import ro_uri  # lazy: nothing else here imports fme
        source = sqlite3.connect(ro_uri(self.path), uri=True)
        try:
            backup = sqlite3.connect(target)
            source.backup(backup)
            backup.close()
        finally:
            source.close()
        with self._guard():
            self._log("snapshot", "", os.path.basename(target))
            self._con.commit()
        return target

    def close(self, force: bool = False) -> None:
        """Commit and close -- unless this handle is shared.

        The GUI keeps one handle per case for every request. Five routes once
        closed it when they finished, and from then until a restart every
        request on that case -- Details, grading, opening it -- failed with
        "Cannot operate on a closed database". A borrower's close() now only
        commits; the owner that shares the handle closes it with force=True.
        """
        with self._guard():
            self._con.commit()
            if self.shared and not force:
                return
            self._con.close()


def open_case(out_dir: str, examiner: str = "", case_number: str = "",
              region: str = None) -> CaseDB:
    """The case in `out_dir`; a new one is graded under `region`."""
    return CaseDB(os.path.join(out_dir, "case.db"), examiner, case_number,
                  region)
