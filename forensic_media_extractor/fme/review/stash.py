"""
stash.py -- the examiner's own notable hashes, on this machine.

The shared store holds what the unit knows: Project VIC, NSRL, and every
case anyone has contributed. This holds what *you* know, and it is not the
same thing.

Three reasons it exists, and only one of them is the server being down.

**Your own decisions are available immediately.** Contributing to the
shared store is a deliberate act, usually after a case is finished and
often after somebody has reviewed it. Until then your gradings help nobody,
including you: grade a file in one case on Monday and meet it again on
Thursday, and without this you grade it twice. Every file you put in a
notable category goes in here as you work.

**It works where the server does not.** A scene, a laptop, a server being
restarted.

**It is yours.** Work in progress is not a unit-wide assertion, and there
are good reasons an examiner might not want every intermediate judgment
published to colleagues the moment it is made.

**What it must never do is quietly disagree with the store.** Two sources
that both say what a file is, with no sign of which said what, is worse
than one source. So a match always says where it came from, and where the
two disagree the disagreement is reported rather than resolved: an examiner
who graded something CAM last month and finds the shared store calling it
something else needs to know that, not be handed one of the two answers.

The stash holds hashes and a category. Never a picture, never a path,
never anything about a case.
"""

from __future__ import annotations

import os
import sqlite3
import time

__version__ = "1.0.0"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stash (
    digest   TEXT NOT NULL,
    algo     TEXT NOT NULL,
    category INTEGER,
    label    TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL,
    note     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (digest, algo)
);
CREATE INDEX IF NOT EXISTS idx_stash_digest ON stash(digest);
CREATE TABLE IF NOT EXISTS stash_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def default_path() -> str:
    """Where this machine keeps it, beside the other per-user settings."""
    base = (os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_DATA_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(base, "ForensicMediaExtractor", "stash.db")


class Stash:
    """One examiner's notable hashes."""

    def __init__(self, path: str = ""):
        self.path = os.path.abspath(path or default_path())
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._con = sqlite3.connect(self.path, timeout=30)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        # The region whose scheme a row's code is in (fme/review/regions.py).
        # A row from before v2026.11.318 has none, and is never taken for a
        # grade in any scheme: a Swedish 1 was applied as US 1, CAM.
        columns = {r[1] for r in self._con.execute("PRAGMA table_info(stash)")}
        if "region" not in columns:
            self._con.execute("ALTER TABLE stash ADD COLUMN region TEXT")
        self._con.commit()
        if os.name != "nt":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def close(self) -> None:
        try:
            self._con.close()
        except sqlite3.Error:
            pass

    # -- writing ---------------------------------------------------------
    def add(self, rows, note: str = "", region: str = None) -> int:
        """Remember these gradings. Only hashes and categories are kept.

        A row already here is updated rather than ignored: an examiner who
        changes their mind about a file means the later decision, and a
        stash that kept the first one would quietly argue with them.
        """
        prepared = []
        now = time.time()
        for row in rows:
            category = row.get("category")
            if category is None:
                continue
            for algo in ("md5", "sha1", "sha256"):
                digest = (row.get(algo) or "").strip().lower()
                if digest:
                    prepared.append((digest, algo, int(category),
                                     row.get("label", ""), now, note,
                                     row.get("region") or region))
        if not prepared:
            return 0
        self._con.executemany(
            "INSERT INTO stash (digest, algo, category, label, added_at, note,"
            " region) VALUES (?,?,?,?,?,?,?) ON CONFLICT(digest, algo) DO "
            "UPDATE SET category=excluded.category, label=excluded.label, "
            "added_at=excluded.added_at, note=excluded.note, "
            "region=excluded.region", prepared)
        self._con.commit()
        return len(prepared)

    def forget(self, digests) -> int:
        """Remove these. An examiner's own store should be theirs to empty."""
        wanted = [d.strip().lower() for d in digests if d]
        if not wanted:
            return 0
        marks = ",".join("?" * len(wanted))
        cursor = self._con.execute(
            f"DELETE FROM stash WHERE digest IN ({marks})", wanted)
        self._con.commit()
        return cursor.rowcount

    # -- reading ---------------------------------------------------------
    def size(self) -> int:
        return self._con.execute(
            "SELECT COUNT(*) AS n FROM stash").fetchone()["n"]

    def by_category(self) -> dict:
        return {row["category"]: row["n"] for row in self._con.execute(
            "SELECT category, COUNT(*) AS n FROM stash GROUP BY category")}

    def lookup(self, digests) -> dict:
        """What this stash says about these files."""
        wanted = [d.strip().lower() for d in digests if d]
        if not wanted:
            return {}
        found = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self._con.execute(
                    f"SELECT digest, category, label, added_at, region FROM "
                    f"stash WHERE digest IN ({marks})", chunk):
                found[row["digest"]] = {
                    "category": row["category"], "label": row["label"],
                    "added_at": row["added_at"], "region": row["region"],
                    "source": "your stash"}
        return found


def check(files, stash_path: str = "", store_matches: dict = None) -> dict:
    """What the stash says, and where it disagrees with the shared store.

    `store_matches` is what the shared store said about the same files, so
    the two can be compared. A disagreement is reported, never resolved:
    one of the two answers handed over silently is how an examiner comes to
    rely on a category nobody chose.
    """
    stash = Stash(stash_path)
    try:
        digests = []
        for entry in files:
            for algo in ("sha256", "md5", "sha1"):
                if entry.get(algo):
                    digests.append(entry[algo])
        found = stash.lookup(digests)
        total = stash.size()
    finally:
        stash.close()

    matched, disagreements = [], []
    store_matches = store_matches or {}
    for entry in files:
        hit = None
        for algo in ("sha256", "md5", "sha1"):
            digest = (entry.get(algo) or "").lower()
            if digest in found:
                hit = found[digest]
                break
        if hit is None:
            continue
        matched.append({**entry, **hit})

        # The part that matters. Both sources speaking, differently.
        other = None
        for algo in ("sha256", "md5", "sha1"):
            digest = (entry.get(algo) or "").lower()
            if digest in store_matches:
                other = store_matches[digest]
                break
        # Compared only within one scheme: a Europol 3 and a US 3 are
        # different findings that happen to share a number.
        same_scheme = (other or {}).get("region", "US") == hit.get("region")
        if other and other.get("category") is not None and same_scheme \
                and other["category"] != hit["category"]:
            disagreements.append({
                "sha256": entry.get("sha256", ""),
                "your_stash": hit["category"],
                "shared_store": other["category"],
                "why": ("you graded this differently from the shared store. "
                        "Neither has been applied: decide which is right."),
            })

    return {"stash_holds": total, "matched": matched,
            "disagreements": disagreements,
            "note": ("Your own gradings from earlier cases, kept on this "
                     "machine. A match here is what you decided before, not "
                     "what the unit has agreed.")}
