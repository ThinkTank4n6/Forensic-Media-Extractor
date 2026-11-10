"""
recover.py -- putting a case back after a grading went wrong.

The undo in the interface only knows about gradings made since it existed.
This is for the other case: a key pressed with the wrong files selected in
an earlier version, with nothing recorded to step back to.

It works because the audit log has always recorded every grading --- which
file, which category, by whom, when. That is a complete history, so the
state before any single event can be reconstructed by replaying everything
that came before it. Nothing is guessed: a file whose only entry is the
mistaken one had no decision before it, and gets none back.

    python -m fme recover --case D:\\cases\\DC26-253        # what happened
    python -m fme recover --case D:\\cases\\DC26-253 --undo 3

The listing groups the log into events -- one keystroke, however many files
it touched -- so a bulk grading of a hundred thousand files shows as one
line, which is the thing you want to take back.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sqlite3
import sys
from collections import OrderedDict

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

from fme.sqlite_uri import ro_uri
from fme.review import casedb

__version__ = "1.1.0"

CATEGORY_IN_DETAIL = re.compile(r"category (\d+)")
# Entries written within this many seconds of each other, by the same person,
# to the same category, are one action. A bulk grading writes thousands of
# rows with the same timestamp; a person grading by hand cannot produce two
# in the same second and would not mind them being grouped if they did.
SAME_EVENT_SECONDS = 2


def _moment(text: str):
    try:
        return _dt.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def events(case_dir: str) -> list:
    """Every grading in the case's history, grouped into the acts that made
    them.

    Read from the audit log, which records each file individually. Grouping
    turns three hundred thousand rows into the handful of decisions a person
    actually made.
    """
    path = os.path.join(case_dir, "case.db")
    con = sqlite3.connect(ro_uri(path), uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, at, who, action, sha256, detail FROM audit "
            "WHERE (action='categorize' AND sha256 != '') OR action IN "
            "('undo', 'undo-restore', 'clear', 'recover', 'recover-restore', "
            "'uncategorize', 'category-deleted') ORDER BY id").fetchall()
    finally:
        con.close()

    grouped = []
    broken = False
    for row in rows:
        if row["action"] != "categorize":
            # Something else changed files in between: grading 1, undoing
            # and grading 1 again inside two seconds is two acts, and one
            # event spanning the undo would be replayed from before both.
            broken = True
            continue
        match = CATEGORY_IN_DETAIL.search(row["detail"] or "")
        if not match:
            continue
        code = int(match.group(1))
        when = _moment(row["at"])
        last = grouped[-1] if grouped and not broken else None
        broken = False
        same = (last is not None and last["category"] == code
                and last["who"] == row["who"]
                and when is not None and last["_last"] is not None
                and abs((when - last["_last"]).total_seconds()) <= SAME_EVENT_SECONDS)
        if same:
            last["files"].append(row["sha256"])
            last["_last"] = when
            last["last_id"] = row["id"]
        else:
            grouped.append({"first_id": row["id"], "last_id": row["id"],
                            "at": row["at"], "who": row["who"],
                            "category": code, "files": [row["sha256"]],
                            "_last": when})

    for index, event in enumerate(grouped, start=1):
        event["number"] = index
        event["count"] = len(event["files"])
        event.pop("_last", None)
    return grouped


CATEGORIZE_DETAIL = re.compile(r"^category (\d+)(?: by (.*))?$", re.S)
DELETED_CATEGORY = re.compile(r"^(\d+),")
# What the two scheme migrations did to every decision in the case at once,
# recorded only as one summary row each (casedb._migrate_scheme and
# _migrate_ai_categories). Replayed as they ran, at the point they ran: a
# version-1 case logged "category 0" for what is now 8 and "category 5" for
# what is now 0, and replaying those codes as written restored files to the
# wrong grade.
MIGRATIONS = {
    "ai-categories-to-flags": {10: 1, 11: 2, 12: 5},
    "scheme-migrated": {0: casedb.UNCATEGORIZED, 5: 0},
}
PER_FILE_UNDO = " [per file]"


class History:
    """Each file's decision as the audit log says it stood, row by row.

    A state is None (no decision) or {"category", "by", "at"}. Every action
    that changes a decision is replayed, not only "categorize": an undo, a
    clear, a note that made a row, a deleted category and the scheme
    migrations all moved files, and a replay that skipped them answered
    with a grade the file did not have.
    """

    def __init__(self, con):
        self.con = con
        # The case's own Uncategorized: 8 in a US case, 0 in most others.
        try:
            row = con.execute("SELECT value FROM meta WHERE "
                              "key='category_region'").fetchone()
        except Exception:  # noqa: BLE001 -- a case too old for meta is US
            row = None
        from fme.review import regions
        self.uncategorized = regions.get(row[0] if row else "US").uncategorized
        self.state: dict = {}
        self.unattributed: list = []     # legacy undos nobody can place
        self._used_batches: set = set()

    def apply(self, row) -> None:
        action, sha256, detail = row["action"], row["sha256"] or "", row["detail"] or ""
        if action == "categorize" and sha256:
            match = CATEGORIZE_DETAIL.match(detail)
            if match:
                self.state[sha256] = {"category": int(match.group(1)),
                                      "by": match.group(2) or row["who"] or "",
                                      "at": row["at"]}
        elif action == "clear" and sha256:
            self.state[sha256] = None
        elif action == "note" and sha256:
            # set_note makes an uncategorized row for a file with none.
            if self.state.get(sha256) is None:
                self.state[sha256] = {"category": self.uncategorized,
                                      "by": None, "at": None}
        elif action in ("undo-restore", "recover-restore") and sha256:
            parsed, state = casedb.parse_restore_detail(detail)
            if parsed:
                self.state[sha256] = state
        elif action == "uncategorize" and sha256:
            if self.state.get(sha256) is not None:
                self.state[sha256] = dict(self.state[sha256],
                                          category=self.uncategorized)
        elif action == "category-deleted" and not sha256:
            # Exact without per-file rows: every file then in the category
            # went to uncategorized, and the replay knows which those were.
            match = DELETED_CATEGORY.match(detail)
            if match:
                self._remap({int(match.group(1)): self.uncategorized})
        elif action in MIGRATIONS and not sha256:
            self._remap(MIGRATIONS[action])
        elif action == "undo" and not sha256 and \
                not detail.endswith(PER_FILE_UNDO):
            self._legacy_undo(row)

    def _remap(self, codes: dict) -> None:
        for sha256, state in self.state.items():
            if state is not None and state["category"] in codes:
                self.state[sha256] = dict(state, category=codes[state["category"]])

    def _legacy_undo(self, row) -> None:
        """An undo from before v2026.11.310 logged only "N file(s): <what>".

        Which files it put back is still in undo_batches/undo_items if the
        batch has not been pruned: one undone batch with that description,
        that many files, made before the undo. Anything less certain than
        exactly one such batch is recorded as unattributed rather than
        guessed at.
        """
        match = re.match(r"^(\d+) file\(s\): (.*)$", row["detail"] or "", re.S)
        if not match:
            self.unattributed.append(row["at"])
            return
        count, what = int(match.group(1)), match.group(2)
        try:
            candidates = [
                r["batch_id"] for r in self.con.execute(
                    "SELECT b.batch_id FROM undo_batches b WHERE b.undone=1 "
                    "AND b.detail=? AND b.at<=? AND (SELECT COUNT(*) FROM "
                    "undo_items i WHERE i.batch_id=b.batch_id)=?",
                    (what, row["at"], count))
                if r["batch_id"] not in self._used_batches]
        except sqlite3.Error:
            candidates = []
        if len(candidates) != 1:
            self.unattributed.append(row["at"])
            return
        batch = candidates[0]
        self._used_batches.add(batch)
        for item in self.con.execute(
                "SELECT sha256, had_row, category, categorized_by, "
                "categorized_at FROM undo_items WHERE batch_id=?", (batch,)):
            self.state[item["sha256"]] = (
                {"category": item["category"], "by": item["categorized_by"],
                 "at": item["categorized_at"]} if item["had_row"] else None)


def _replay(case_dir: str, before_id: int) -> History:
    """The history of every file, replayed up to (not including) a row."""
    path = os.path.join(case_dir, "case.db")
    # Read-only, and only SELECTs, so no transaction is ever held open.
    con = sqlite3.connect(ro_uri(path), uri=True)
    con.row_factory = sqlite3.Row
    try:
        history = History(con)
        for row in con.execute(
                "SELECT id, at, who, action, sha256, detail FROM audit "
                "WHERE id < ? ORDER BY id", (before_id,)):
            history.apply(row)
    finally:
        con.close()
    return history


def state_before(case_dir: str, event_number: int,
                 accept_unattributed: bool = False) -> dict:
    """What each file touched by this event was, immediately before it.

    Replays the whole log up to the event. A file with no earlier entry had
    no decision at all, and is recorded as None rather than as
    uncategorized: those are different claims, and a report distinguishes
    them. "before" is each file's category; "states" adds who graded it
    and when, which is what recovery puts back.

    An undo from an older version that cannot be tied to its files makes
    the answer uncertain, and that is refused unless `accept_unattributed`.
    """
    history = events(case_dir)
    target = next((e for e in history if e["number"] == event_number), None)
    if target is None:
        raise ValueError(f"There is no event {event_number} in this case.")

    replayed = _replay(case_dir, target["first_id"])
    if replayed.unattributed and not accept_unattributed:
        raise ValueError(
            f"This case's history has {len(replayed.unattributed)} undo(s) "
            f"before event {event_number} (the first at "
            f"{replayed.unattributed[0][:19]}) made by a version that did not "
            f"record which files they changed, and their record has since "
            f"been pruned or is ambiguous. What event {event_number}'s files "
            f"were before it cannot be worked out with certainty, so nothing "
            f"was changed. To restore as if those undos had not happened, "
            f"run again with --accept-unattributed-undo.")
    states = OrderedDict()
    for sha256 in target["files"]:
        if sha256 not in states:
            state = replayed.state.get(sha256)
            states[sha256] = dict(state) if state is not None else None
    before = OrderedDict((sha256, None if state is None else state["category"])
                         for sha256, state in states.items())
    return {"event": target, "before": before, "states": states,
            "unattributed": list(replayed.unattributed)}


def undo(case_dir: str, event_number: int, examiner: str = "",
         accept_unattributed: bool = False) -> dict:
    """Put every file this event touched back to what it was.

    The whole target is worked out first, then written in one transaction
    through the case database (CaseDB.restore), and only then is a
    snapshot taken: it used to grade category by category and then clear,
    each committing, so a failure part way -- or in the snapshot -- left
    the case half recovered. Each file gets back the examiner and time of
    the grading it returns to; the recovery itself is in the audit log
    ("recover-restore" per file) and is one undo batch.
    """
    found = state_before(case_dir, event_number,
                         accept_unattributed=accept_unattributed)
    event, states = found["event"], found["states"]

    case = casedb.CaseDB(os.path.join(case_dir, "case.db"))
    try:
        if examiner:
            case.examiner = examiner
        done = case.restore(
            states, f"recovered to what it was before event {event['number']} "
                    f"({event['at'][:19]})")
        snapshot = case.snapshot("after-recovery")
    finally:
        case.close()

    return {"event": event["number"], "at": event["at"],
            "category": event["category"], "files": event["count"],
            "restored": done["restored"], "cleared": done["cleared"],
            "snapshot": snapshot, "unattributed": found["unattributed"]}


def _known_cases() -> list:
    """Every case the program has run, from the list beside it.

    Looked for where the program keeps it and where it was started from,
    because a person running this from a command line is rarely standing in
    the same folder as the program.
    """
    from fme.review import registry

    seen, out = set(), []
    here = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for folder in (os.getcwd(), here):
        path = os.path.join(folder, "cases.db")
        if not os.path.isfile(path) or path in seen:
            continue
        seen.add(path)
        try:
            book = registry.CaseRegistry(path)
            for entry in book.cases():
                # The list marks a case present when its manifest is there.
                # Recovery needs case.db and nothing else: a folder whose
                # media has been moved away still holds the decisions, and
                # those are what is being put back.
                entry["present"] = os.path.isfile(
                    os.path.join(entry["out_dir"], "case.db"))
                out.append(entry)
            book.close()
        except Exception:  # noqa: BLE001 - a missing list is not a failure
            continue
    return out


def _wrong_folder(where: str) -> str:
    """Explain what was expected, and find the right folder if it is near.

    Two files in this program have names a keystroke apart: `cases.db` is
    the list of cases and lives beside the program; `case.db` is the review
    of one case and lives in that run's output folder. This wants the
    second. Saying "no case.db here" to somebody looking at a folder with
    `cases.db` in it is not an explanation.
    """
    where = os.path.abspath(where)
    lines = [f"There is no case.db in {where}."]

    registry = os.path.join(where, "cases.db")
    if os.path.isfile(registry):
        lines.append("")
        lines.append("That folder has cases.db, which is the list of cases "
                     "this program has run -- not a case itself. What this "
                     "needs is the output folder of one case, which holds "
                     "case.db, manifest.json and the recovered media.")
        try:
            con = sqlite3.connect(ro_uri(registry), uri=True)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT out_dir, case_number, files FROM cases "
                "ORDER BY CASE WHEN last_opened='' THEN created_at "
                "ELSE last_opened END DESC LIMIT 15").fetchall()
            con.close()
        except sqlite3.Error:
            rows = []
        if rows:
            lines.append("")
            lines.append("The cases it knows about:")
            for row in rows:
                here = os.path.isfile(os.path.join(row["out_dir"], "case.db"))
                lines.append(f"  {row['case_number'] or '(no number)':16} "
                             f"{row['files']:>8,} file(s)  {row['out_dir']}"
                             + ("" if here else "   (folder not there now)"))

    # Look one level down as well: people point this at the folder that
    # holds their cases rather than at a case.
    nearby = []
    try:
        for name in sorted(os.listdir(where)):
            candidate = os.path.join(where, name)
            if os.path.isfile(os.path.join(candidate, "case.db")):
                nearby.append(candidate)
    except OSError:
        pass
    if nearby:
        lines.append("")
        lines.append("Cases inside that folder:")
        for candidate in nearby[:15]:
            lines.append(f"  {candidate}")

    lines.append("")
    lines.append("Run it again with one of those folders:")
    example = nearby[0] if nearby else os.path.join(where, "<case folder>")
    lines.append(f'    python -m fme recover --case "{example}"')
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Put a case back after a grading went wrong.")
    ap.add_argument("--case", default="",
                    help="a case's output folder, or its case number")
    ap.add_argument("--list-cases", action="store_true",
                    help="show every case this program has run")
    ap.add_argument("--undo", type=int, default=0,
                    help="the event number to take back")
    ap.add_argument("--accept-unattributed-undo", action="store_true",
                    help="restore even when an undo from an older version "
                         "cannot be tied to the files it changed")
    ap.add_argument("--examiner", default="")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)

    if args.list_cases or not args.case:
        known = _known_cases()
        if not known:
            print("No cases are recorded. Give --case the output folder of "
                  "one, the folder that holds case.db.", file=sys.stderr)
            return 2
        print(f"{len(known)} case(s) this program has run:\n")
        print(f"  {'case':18} {'files':>9}  folder")
        for entry in known:
            mark = "" if entry["present"] else "   (folder not there now)"
            print(f"  {entry['case_number'] or '(no number)':18} "
                  f"{entry['files']:>9,}  {entry['out_dir']}{mark}")
        print("\nThen:  python -m fme recover --case \"<folder>\"")
        return 0

    # A case number is easier to remember than a path, so take either.
    case_dir = args.case
    if not os.path.isfile(os.path.join(case_dir, "case.db")):
        matched = [entry for entry in _known_cases()
                   if entry["case_number"]
                   and entry["case_number"].lower() == args.case.strip().lower()
                   and entry["present"]]
        if len(matched) == 1:
            case_dir = matched[0]["out_dir"]
            print(f"Using {case_dir}\n")
        elif len(matched) > 1:
            print(f"More than one case is numbered {args.case}:",
                  file=sys.stderr)
            for entry in matched:
                print(f"  {entry['out_dir']}", file=sys.stderr)
            return 2

    if not os.path.isfile(os.path.join(case_dir, "case.db")):
        print(_wrong_folder(args.case), file=sys.stderr)
        return 2
    args.case = case_dir

    try:
        if args.undo:
            result = undo(args.case, args.undo, args.examiner,
                          accept_unattributed=args.accept_unattributed_undo)
            print(f"Event {result['event']} ({result['at'][:19]}, "
                  f"{result['files']:,} file(s) set to category "
                  f"{result['category']}) has been taken back.")
            print(f"  {result['restored']:,} file(s) put back, of which "
                  f"{result['cleared']:,} returned to having no decision.")
            print(f"  A snapshot of the case as it now stands: "
                  f"{result['snapshot']}")
            return 0

        history = events(args.case)
        if not history:
            print("No gradings are recorded in this case's audit log.")
            return 0
        print(f"{len(history)} grading event(s) in this case. "
              f"The number on the left is what --undo takes.\n")
        print(f"  {'#':>4}  {'when':19}  {'files':>9}  {'category':>8}  who")
        for event in history[-args.limit:]:
            print(f"  {event['number']:>4}  {event['at'][:19]}  "
                  f"{event['count']:>9,}  {event['category']:>8}  "
                  f"{event['who'][:20]}")
        biggest = max(history, key=lambda e: e["count"])
        if biggest["count"] > 1000:
            print(f"\nThe largest is event {biggest['number']}: "
                  f"{biggest['count']:,} file(s) set to category "
                  f"{biggest['category']} at {biggest['at'][:19]}.")
            print("If that is the one that went wrong, take it back with:")
            print(f'    python -m fme recover --case "{args.case}" '
                  f'--undo {biggest["number"]}')
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# A run that was interrupted
#
# manifest.json is written at the end of a run; manifest.csv is written row
# by row as it goes. So a crash, a power cut or a killed process leaves the
# recovered files on disk and listed, with only the summary missing -- and
# the case list, which looks for manifest.json, calls that "folder
# missing". The folder is there and so is the work.

def unfinished(out_dir: str) -> dict:
    """What an interrupted run left behind."""
    import csv as _csv

    import json as _json

    manifest = os.path.join(out_dir, "manifest.json")
    rows_path = os.path.join(out_dir, "manifest.csv")

    # A manifest that will not parse is not a manifest. A run killed while
    # writing one leaves an empty file, and treating that as "this case is
    # fine" is how an examiner is told their work is unrecoverable when it
    # is sitting next to it.
    readable = False
    if os.path.isfile(manifest):
        try:
            with open(manifest, encoding="utf-8") as fh:
                readable = bool(_json.load(fh).get("files") is not None)
        except (OSError, ValueError):
            readable = False

    state = {
        "out_dir": os.path.abspath(out_dir),
        "folder_there": os.path.isdir(out_dir),
        "has_manifest": readable,
        "manifest_unreadable": os.path.isfile(manifest) and not readable,
        "has_rows": os.path.isfile(rows_path) or _has_db(out_dir),
        "rows": 0,
        "files_on_disk": 0,
    }
    if not state["has_rows"]:
        return state
    if _has_db(out_dir):
        # manifest.db is the record since v2026.11.306: every row in it was
        # committed whole, with what later duplicates taught it.
        from fme.engines import manifestdb
        try:
            rows, _ = manifestdb.read(out_dir)
        except manifestdb.ManifestDBError:
            return state
    else:
        try:
            from fme.engines.media_core import _CSV_FIELD_LIMIT

            # A long also_seen_at or note passed csv's 128 KiB default and
            # the case was reported as having nothing to recover.
            _csv.field_size_limit(_CSV_FIELD_LIMIT)
            with open(rows_path, newline="", encoding="utf-8") as fh:
                rows = list(_csv.DictReader(fh))
        except (OSError, _csv.Error):
            return state
    state["rows"] = len(rows)
    state["files_on_disk"] = sum(
        1 for row in rows
        if row.get("out_path")
        and os.path.isfile(os.path.join(out_dir, row["out_path"])))
    state["recoverable"] = bool(
        state["folder_there"] and not state["has_manifest"] and state["rows"])
    return state


def _has_db(out_dir: str) -> bool:
    from fme.engines import manifestdb
    return manifestdb.exists(out_dir)


def _provenance() -> dict:
    """Which build is doing this, from the engine's own record."""
    try:
        from fme.engines.media_core import tool_provenance

        return tool_provenance()
    except Exception:  # noqa: BLE001
        return {"tool": "Forensic Media Extractor"}


def _with_duplicates(rows: list, duplicates: list) -> list:
    """The kept rows as the run would have closed them: each one's instance
    count, and what a duplicate's directory entry told a carved file (its
    name, path, deleted flag). Those reached manifest.csv only at close, so
    a rebuild that copied the rows said "found once, not deleted" of files
    duplicates.csv says otherwise about -- as a resume once did."""
    from dataclasses import asdict

    from fme.engines.media_core import OutputManager, Record

    kept = [Record(**row) for row in rows]
    by_digest: dict = {}
    for rec in kept:
        by_digest.setdefault(rec.sha256, rec)
    copies: dict = {}
    for row in duplicates:
        target = by_digest.get(row.get("sha256"))
        if target is None:
            continue
        copies[target.sha256] = copies.get(target.sha256, 0) + 1
        OutputManager._merge_into(target, Record(**row))
    for digest, count in copies.items():
        target = by_digest[digest]
        target.instances = max(target.instances or 1, 1 + count)
    return [asdict(rec) for rec in kept]


def _typed_rows(path: str) -> list:
    """A case CSV's rows as manifest records, with their types restored.

    Only rows that reached their line end are kept (a crash tears the last
    one), and each goes through media_core._record_from_row, which refuses
    a row that will not convert rather than listing it with wrong values.
    A missing file is no rows: cases older than duplicates.csv have none.
    """
    import csv as _csv
    import io as _io
    from dataclasses import asdict

    from fme.engines.media_core import _CSV_FIELD_LIMIT, _record_from_row

    _csv.field_size_limit(_CSV_FIELD_LIMIT)
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []
    if text and not text.endswith(("\n", "\r")):
        text = text[:max(text.rfind("\n"), text.rfind("\r")) + 1]
    out = []
    for row in _csv.DictReader(_io.StringIO(text, newline="")):
        if None in row.values() or None in row:
            continue
        if not (row.get("sha256") or "").strip():
            continue
        rec = _record_from_row(row)
        if rec is not None:
            out.append(asdict(rec))
    return out


def rebuild_manifest(out_dir: str) -> dict:
    """Write manifest.json back from the rows the run had already written.

    The summary is rebuilt from the rows rather than invented: counts and
    totals are what the rows say, and the fields a run records that a row
    cannot carry -- how long it took, what was scanned -- are left out
    rather than guessed. The manifest says it was rebuilt, because a
    document that claims to be the record of a run that did not finish
    should say so.
    """
    import csv as _csv
    import datetime as _dt
    import json as _json

    state = unfinished(out_dir)
    if state["has_manifest"]:
        return {"ok": False, "why": "this case already has a manifest"}
    if not state["has_rows"]:
        return {"ok": False,
                "why": "there is no manifest.csv here, so there is nothing "
                       "to rebuild from"}

    # Read the way a resume reads them (OutputManager._load_existing). Raw
    # csv text went straight into manifest.json: every "deleted" was the
    # string "False", which every report and export takes as true, so a
    # rebuilt case called every file deleted; sizes stayed strings, which
    # the case page could not open; a row a crash tore off came back as a
    # file with no hash; and duplicates.csv was left out altogether.
    from fme.engines import manifestdb
    from_db = manifestdb.exists(out_dir)
    try:
        if from_db:
            # Typed already, and each kept row already carries what its
            # duplicates taught it: nothing to convert, nothing to replay.
            rows, duplicates = manifestdb.read(out_dir)
        else:
            rows = _typed_rows(os.path.join(out_dir, "manifest.csv"))
            duplicates = _typed_rows(os.path.join(out_dir, "duplicates.csv"))
    except manifestdb.ManifestDBError as exc:
        return {"ok": False,
                "why": f"manifest.db could not be read ({exc}); nothing "
                       f"was rebuilt"}
    except (OSError, _csv.Error) as exc:
        return {"ok": False,
                "why": f"manifest.csv could not be read ({exc}); nothing "
                       f"was rebuilt"}

    def number(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    kept = [row for row in rows if row.get("out_path")]
    if not from_db:
        kept = _with_duplicates(kept, duplicates)
    by_category = {}
    total_bytes = 0
    for row in kept:
        by_category[row.get("category", "")] = by_category.get(
            row.get("category", ""), 0) + 1
        total_bytes += number(row.get("size"))

    summary = {
        # The rebuild names the build that rebuilt it, which is not
        # necessarily the one that made the run: a case can be rebuilt
        # months later under a newer program, and the record should
        # not pretend otherwise.
        **_provenance(),
        "rebuilt_by_version": _provenance().get("tool_version", ""),
        "output_dir": os.path.abspath(out_dir),
        "total_files": len(kept),
        "total_bytes": total_bytes,
        "images": by_category.get("image", 0),
        "videos": by_category.get("video", 0),
        "audio": by_category.get("audio", 0),
        "other": by_category.get("other", 0),
        "rebuilt_from_rows": True,
        "rebuilt_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "note": ("This run did not finish: its manifest was rebuilt from "
                 "the rows it had already written. The files listed are on "
                 "disk and their hashes are the ones recorded as each was "
                 "written. What is missing is whatever the run had not "
                 "reached when it stopped -- treat this case as partial, "
                 "and run the source again if completeness matters."),
    }

    summary["duplicate_instances"] = len(duplicates)
    manifest = {"summary": summary, "files": kept, "duplicates": duplicates}
    # Written beside and renamed into place, so a failure here cannot
    # leave half a manifest where a whole one was -- which is what the
    # first version of this function did.
    path = os.path.join(out_dir, "manifest.json")
    scratch = path + ".rebuilding"
    with open(scratch, "w", encoding="utf-8") as fh:
        _json.dump(manifest, fh, indent=1)
    os.replace(scratch, path)

    return {"ok": True, "path": path, "files": len(kept),
            "bytes": total_bytes, "note": summary["note"]}
