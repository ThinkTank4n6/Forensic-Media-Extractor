"""
registry.py -- the list of cases this program has processed.

An extraction is expensive and a review is long, so the second thing anyone
wants from the program is the work they did yesterday. This keeps a small
index of every run: where its output is, what case it belongs to, and how far
the review got, so a case is reopened by clicking it rather than carved
again.

The registry holds no evidence and no decisions. It is a table of contents:
the manifest and `case.db` in each output folder remain the record, and a
registry that is lost or deleted costs nothing but the list. That is why
`scan` exists -- point it at a drive full of old output folders and it finds
them by their manifests.

    python -m fme cases --list
    python -m fme cases --scan D:\\cases
    python -m fme cases --forget "DC26-253"
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import sys
import threading

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))
from fme.sqlite_uri import ro_uri

__version__ = "1.0.0"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    out_dir TEXT PRIMARY KEY,
    case_number TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    examiner TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_opened TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    files INTEGER NOT NULL DEFAULT 0,
    images INTEGER NOT NULL DEFAULT 0,
    videos INTEGER NOT NULL DEFAULT 0,
    categorized INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '');

CREATE INDEX IF NOT EXISTS cases_number ON cases(case_number);
"""


def _now() -> str:
    # Local time with its offset written in ("...T14:05:00-05:00"). Without
    # the offset a time is ambiguous for the hour the clocks go back, in a
    # record of who decided what, and when; with it, it still reads as the
    # examiner's own clock.
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


class CaseRegistry:
    """Which cases exist, and where their output folders are."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        # As case.db and the hash store: WAL on a network share is how a
        # SQLite file gets "disk I/O error" or quietly corrupted, and a unit
        # that keeps its case list on a share is the case this is for.
        from fme.review.hashstore import is_network_path
        self.on_network = is_network_path(self.path)
        self._con.execute("PRAGMA journal_mode=DELETE" if self.on_network
                          else "PRAGMA journal_mode=WAL")
        self._con.executescript(SCHEMA)
        columns = {row[1] for row in self._con.execute("PRAGMA table_info(cases)")}
        if "fingerprint" not in columns:
            # A list written before cases could be recognized after a move.
            self._con.execute(
                "ALTER TABLE cases ADD COLUMN fingerprint TEXT NOT NULL "
                "DEFAULT ''")
        self._con.commit()

    # -- reading ---------------------------------------------------------
    def cases(self, include_missing: bool = True) -> list:
        """Every registered case, most recently touched first.

        Each is checked against the disk as it is listed: an output folder
        that has been moved or deleted is reported as missing rather than
        offered as something to open.
        """
        out = []
        # The rows are read under the lock every writer takes, then checked
        # against the disk outside it. One registry is shared by every
        # thread of the GUI, and a sqlite3 connection is not safe to read on
        # while another thread writes on it -- a listing during a register
        # could see the cursor reset under it. The disk checks (and the
        # fingerprint, which reads files) are kept out of the lock so a
        # slow share does not hold up every writer.
        with self._lock:
            rows = [dict(row) for row in self._con.execute("""
                SELECT * FROM cases
                ORDER BY CASE WHEN last_opened = '' THEN created_at
                         ELSE last_opened END DESC""").fetchall()]
        for case in rows:
            manifest = os.path.join(case["out_dir"], "manifest.json")
            case["present"] = os.path.isfile(manifest)
            # "Missing" used to mean only that manifest.json was not
            # there, which is also what an interrupted run leaves -- so a
            # case whose folder and files were sitting on disk was
            # reported as missing, and an examiner had no reason to look
            # inside it. The three states are different and are told
            # apart here.
            case["folder_there"] = os.path.isdir(case["out_dir"])
            case["unfinished"] = bool(
                not case["present"] and case["folder_there"]
                and os.path.isfile(os.path.join(case["out_dir"],
                                                "manifest.csv")))
            case["has_review"] = os.path.isfile(
                os.path.join(case["out_dir"], "case.db"))
            case["fingerprint"] = (case.get("fingerprint")
                                   or (fingerprint(case["out_dir"])
                                       if case["present"] else ""))
            if case["present"] or include_missing:
                out.append(case)
        return out

    def get(self, out_dir: str) -> dict:
        with self._lock:
            row = self._con.execute("SELECT * FROM cases WHERE out_dir=?",
                                    (os.path.abspath(out_dir),)).fetchone()
        return dict(row) if row else {}

    # -- writing ---------------------------------------------------------
    def register(self, out_dir: str, *, case_number: str = "",
                 evidence: str = "", examiner: str = "", source: str = "",
                 note: str = "") -> dict:
        """Record a case, or update what is known about one."""
        out_dir = os.path.abspath(out_dir)
        with self._lock:
            existing = self._con.execute(
                "SELECT out_dir FROM cases WHERE out_dir=?", (out_dir,)).fetchone()
            if existing:
                # Only fill in what is now known: a later run should not blank
                # a case number somebody typed earlier.
                self._con.execute("""
                    UPDATE cases SET
                        case_number = CASE WHEN ?1 != '' THEN ?1 ELSE case_number END,
                        evidence    = CASE WHEN ?2 != '' THEN ?2 ELSE evidence END,
                        examiner    = CASE WHEN ?3 != '' THEN ?3 ELSE examiner END,
                        source      = CASE WHEN ?4 != '' THEN ?4 ELSE source END,
                        note        = CASE WHEN ?5 != '' THEN ?5 ELSE note END
                    WHERE out_dir = ?6""",
                    (case_number, evidence, examiner, source, note, out_dir))
            else:
                self._con.execute(
                    "INSERT INTO cases (out_dir, case_number, evidence, "
                    "examiner, source, created_at, note) VALUES (?,?,?,?,?,?,?)",
                    (out_dir, case_number, evidence, examiner, source,
                     _now(), note))
            self._con.commit()
        self.refresh(out_dir)
        return self.get(out_dir)

    def refresh(self, out_dir: str) -> dict:
        """Re-read the counts from the case's own files.

        The registry is a cache of what the manifest and case.db say, so it
        is rebuilt from them rather than kept in step by hand -- a count that
        drifts from the evidence is worse than no count.
        """
        out_dir = os.path.abspath(out_dir)
        manifest_path = os.path.join(out_dir, "manifest.json")
        files = images = videos = 0
        source = ""
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as fh:
                    manifest = json.load(fh)
                records = manifest.get("files", [])
                files = len(records)
                images = sum(1 for r in records if r.get("category") == "image")
                videos = sum(1 for r in records if r.get("category") == "video")
                source = manifest.get("summary", {}).get("image", "")
            except (OSError, json.JSONDecodeError, ValueError):
                pass

        categorized = 0
        case_path = os.path.join(out_dir, "case.db")
        if os.path.isfile(case_path):
            try:
                from fme.review import regions
                con = sqlite3.connect(ro_uri(case_path), uri=True)
                try:
                    row = con.execute("SELECT value FROM meta WHERE "
                                      "key='category_region'").fetchone()
                except sqlite3.Error:
                    row = None
                categorized = con.execute(
                    "SELECT COUNT(*) FROM decisions WHERE category != ?",
                    (regions.get(row[0] if row else "US").uncategorized,)
                ).fetchone()[0]
                con.close()
            except sqlite3.Error:
                pass

        mark = fingerprint(out_dir)
        with self._lock:
            self._con.execute(
                "UPDATE cases SET files=?, images=?, videos=?, categorized=?, "
                "fingerprint = CASE WHEN ? != '' THEN ? ELSE fingerprint END, "
                "source = CASE WHEN source = '' THEN ? ELSE source END "
                "WHERE out_dir=?",
                (files, images, videos, categorized, mark, mark, source,
                 out_dir))
            self._con.commit()
        return self.get(out_dir)

    def touch(self, out_dir: str) -> None:
        with self._lock:
            self._con.execute("UPDATE cases SET last_opened=? WHERE out_dir=?",
                              (_now(), os.path.abspath(out_dir)))
            self._con.commit()

    def relink(self, old_dir: str, new_dir: str) -> dict:
        """Point a case at where it is now.

        Moving a case to a share is ordinary housekeeping, and the list
        records where output was rather than holding a copy of it -- so the
        row is updated in place. Its history travels with it: the case
        number somebody typed, who examined it, when it was last opened.
        """
        old_dir = os.path.abspath(old_dir)
        new_dir = os.path.abspath(new_dir)
        if not os.path.isfile(os.path.join(new_dir, "manifest.json")):
            raise ValueError(f"{new_dir} holds no manifest.json, so it is not "
                             f"a case folder this program wrote.")
        with self._lock:
            existing = self._con.execute(
                "SELECT * FROM cases WHERE out_dir=?", (old_dir,)).fetchone()
            if existing is None:
                self._con.execute(
                    "INSERT OR IGNORE INTO cases (out_dir, created_at) "
                    "VALUES (?,?)", (new_dir, _now()))
            elif os.path.normcase(old_dir) == os.path.normcase(new_dir):
                # Relinked to where it already is (or the same path spelled
                # another way): the DELETE below removed the case's own row
                # and the UPDATE then found nothing, erasing it from the list.
                pass
            else:
                # A row may already exist at the new path from an earlier
                # scan; the older row's details are the ones worth keeping.
                self._con.execute("DELETE FROM cases WHERE out_dir=?",
                                  (new_dir,))
                self._con.execute("UPDATE cases SET out_dir=? WHERE out_dir=?",
                                  (new_dir, old_dir))
            self._con.commit()
        self.refresh(new_dir)
        return self.get(new_dir)

    def forget(self, out_dir: str) -> bool:
        """Remove a case from the list. The output folder is not touched."""
        with self._lock:
            cur = self._con.execute("DELETE FROM cases WHERE out_dir=?",
                                    (os.path.abspath(out_dir),))
            self._con.commit()
        return cur.rowcount > 0

    def scan(self, root: str, depth: int = 3) -> list:
        """Find output folders under a path and register what is found.

        A folder is a case if it holds a manifest.json this program wrote.
        Details come from the manifest and, where one exists, the Project VIC
        file, which is where the case number was recorded.

        A case that has **moved** is recognized rather than added twice. Its
        fingerprint -- what it was taken from, how many files it holds and
        the hashes at each end of the manifest -- identifies the same case in
        its new place, so the old row is pointed there and everything the
        list knows about it, including when it was last opened, comes along.
        """
        root = os.path.abspath(root)
        found = []
        root_depth = root.rstrip(os.sep).count(os.sep)

        missing = {}
        for case in self.cases():
            if case["present"]:
                continue
            mark = case.get("fingerprint") or ""
            if mark:
                missing.setdefault(mark, case)

        for folder, subfolders, names in os.walk(root):
            if folder.count(os.sep) - root_depth >= depth:
                subfolders[:] = []
            if "manifest.json" not in names:
                continue
            details = _details_from(folder)
            if details is None:
                continue

            mark = fingerprint(folder)
            moved = missing.pop(mark, None) if mark else None
            if moved and os.path.abspath(moved["out_dir"]) != os.path.abspath(folder):
                self.relink(moved["out_dir"], folder)
                entry = self.get(folder)
                entry["relinked_from"] = moved["out_dir"]
                found.append(entry)
                subfolders[:] = [s for s in subfolders
                                 if s not in ("images", "videos", "audio",
                                              "other", "lava", "export",
                                              "backups")]
                continue

            self.register(folder, **details)
            found.append(self.get(folder))
            subfolders[:] = [s for s in subfolders
                             if s not in ("images", "videos", "audio", "other",
                                          "lava", "export", "backups")]
        return found

    def close(self) -> None:
        with self._lock:
            self._con.commit()
            try:
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._con.close()


def fingerprint(case_dir: str) -> str:
    """What identifies this case wherever it is.

    Taken from the manifest rather than from the folder: what the run was
    taken from, how many files it recovered, and the hashes of the first and
    last of them. A path is not an identity -- the whole point here is that
    the case has moved.
    """
    manifest_path = os.path.join(case_dir, "manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return ""
    records = manifest.get("files") or []
    if not records:
        return ""
    source = (manifest.get("summary", {}) or {}).get("image", "")
    parts = [str(source), str(len(records)),
             str(records[0].get("sha256", "")),
             str(records[-1].get("sha256", ""))]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _details_from(folder: str):
    """Case number, evidence and examiner for a found output folder."""
    manifest_path = os.path.join(folder, "manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if "summary" not in manifest or "files" not in manifest:
        return None            # some other program's manifest.json

    details = {"source": manifest.get("summary", {}).get("image", "")}

    vic = os.path.join(folder, "ProjectVIC.json")
    if os.path.isfile(vic):
        try:
            with open(vic, "r", encoding="utf-8-sig") as fh:
                head = json.load(fh)
            case = (head.get("value") or [{}])[0]
            details["case_number"] = case.get("CaseNumber", "") or ""
        except (OSError, json.JSONDecodeError, ValueError, IndexError):
            pass

    case_db = os.path.join(folder, "case.db")
    if os.path.isfile(case_db):
        try:
            con = sqlite3.connect(ro_uri(case_db), uri=True)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            con.close()
            details.setdefault("case_number", meta.get("case_number", "") or "")
            details["examiner"] = meta.get("examiner", "") or ""
        except sqlite3.Error:
            pass
    return details


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="The list of cases this program has processed.")
    ap.add_argument("--registry", default="",
                    help="path to the registry (default: cases.db beside the "
                         "program)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scan", default="", help="find output folders under here")
    ap.add_argument("--forget", default="", help="remove a case by output folder")
    ap.add_argument("--relink", nargs=2, metavar=("OLD", "NEW"), default=None,
                    help="a case has moved: point the list at where it is now. "
                         "OLD may be the folder it was in or its case number")
    args = ap.parse_args(argv)

    path = args.registry or os.path.join(os.getcwd(), "cases.db")
    registry_ = CaseRegistry(path)
    try:
        if args.scan:
            found = registry_.scan(args.scan)
            relinked = [c for c in found if c.get("relinked_from")]
            print(f"Found {len(found)} case(s) under "
                  f"{os.path.abspath(args.scan)}")
            for entry in relinked:
                print(f"  moved: {entry['case_number'] or entry['out_dir']}")
                print(f"     was {entry['relinked_from']}")
                print(f"     now {entry['out_dir']}")
        if args.relink:
            old, new = args.relink
            if not os.path.isdir(old):
                matched = [c for c in registry_.cases()
                           if (c["case_number"] or "").lower() == old.lower()]
                if len(matched) == 1:
                    old = matched[0]["out_dir"]
                elif len(matched) > 1:
                    print(f"More than one case is numbered {old}; give the "
                          f"old folder instead.", file=sys.stderr)
                    return 2
            moved = registry_.relink(old, new)
            print(f"{moved['case_number'] or 'the case'} now points at "
                  f"{moved['out_dir']}")

        if args.forget:
            print("removed" if registry_.forget(args.forget) else "not in the list")

        cases = registry_.cases()
        print(f"\nRegistry: {registry_.path}")
        print(f"Cases   : {len(cases)}\n")
        print(f"  {'case':16} {'evidence':16} {'files':>8} {'graded':>8}  folder")
        for case in cases:
            mark = "" if case["present"] else "  (folder missing)"
            print(f"  {(case['case_number'] or '-')[:16]:16} "
                  f"{(case['evidence'] or '-')[:16]:16} "
                  f"{case['files']:>8,} {case['categorized']:>8,}  "
                  f"{case['out_dir']}{mark}")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 2
    finally:
        registry_.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
