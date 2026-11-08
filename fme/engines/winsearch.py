"""
winsearch.py -- naming a cached thumbnail, using the Windows Search index.

A thumbnail recovered from `thumbcache_*.db` is real content with no
provenance: the cache records a 64-bit identifier, not a path. Windows
Search stores that same identifier against each item it has indexed, as
`System.ThumbnailCacheId`, alongside the item's path. Joining the two turns
"a picture that was on this machine" into "a picture that was at
C:\\Users\\...\\holiday.jpg", which is the difference between an exhibit and
a curiosity.

Windows 11 22H2 and later keep the index in a SQLite database, `Windows.db`,
which is read here with the standard library. Windows 10 and earlier use an
ESE database, `Windows.edb`. That is read with the ESE reader vendored at
`fme/vendor/impacket_ese.py` (Apache 1.1; adapted from Fortra impacket by
DLEAPP and taken verbatim from GLEAPP 2026.5.7), and always in a child process
(`fme/engines/isolated.py`): the reader spins forever on a truncated file,
retrying a short read at end of file, so in-process it would hang the
request that asked. The table and byte-order handling below follow GLEAPP's
`winsearch.edb_map`, which cites libyal's esedb-kb documentation of the
schema; used under GLEAPP's MIT licence (fme/vendor/LICENSE-gleapp).

Three limits worth stating, because a reader will otherwise over-read a hit:

* The index records what Windows Search happened to index, not every file
  that ever existed. A miss means "not in the index", never "ruled out".
* A hit resolves what the thumbnail *was*, not whether the file still
  exists, nor who put it there, nor who looked at it.
* The path comes from the index, not from the file system of the exhibit;
  it is as old as the last time Windows indexed that item.

    python -m fme winsearch -o <run folder> --index Windows.db
"""

from __future__ import annotations

import argparse
import binascii
import csv
import json
import os
import re
import sqlite3
import time
import struct
import sys
from typing import Optional

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))
from fme.sqlite_uri import ro_uri

__version__ = "1.0.0"

CACHE_ID_IN_NOTE = re.compile(r"cache id ([0-9a-f]{1,16})", re.I)


def _as_cache_id(value):
    """The identifier as an integer, whatever the index stored it as."""
    if value is None:
        return None
    if isinstance(value, int):
        return value & 0xFFFFFFFFFFFFFFFF
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if len(raw) >= 8:
            return struct.unpack("<Q", raw[:8])[0]
        if raw:
            return int.from_bytes(raw, "little")
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 16 if text.lower().startswith("0x") else 10)
        except ValueError:
            return None
    return None


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        for encoding in ("utf-16-le", "utf-8"):
            try:
                text = raw.decode(encoding).strip("\x00").strip()
            except UnicodeDecodeError:
                continue
            if text:
                return text
        return ""
    return str(value).strip()


# How long the whole index read may take before it is abandoned. A
# healthy index of any size is read in seconds; this is a guard against
# a damaged one, not a budget.
INDEX_TIME_LIMIT = 60.0


def is_ese(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(8)[4:8] == b"\xef\xcd\xab\x89"
    except OSError:
        return False


# Where Windows Search keeps item properties in an ESE index, and the byte
# order of the ThumbnailCacheId stored there (libyal esedb-kb, "Windows
# Search"; GLEAPP winsearch.py). Windows 8 and later: SystemIndex_PropertyStore,
# column names prefixed with a numeric tag that differs between machines
# ("4454-System_ThumbnailCacheId"), little-endian values. Windows 7:
# SystemIndex_0A, plain names, big-endian values -- read little-endian, none
# of a Windows 7 index's ids match its thumbcache files. Vista stores
# compressed strings in SystemIndex_0A and is not handled. First present wins.
EDB_TABLES = (("SystemIndex_PropertyStore", "<"), ("SystemIndex_0A", ">"))
_EDB_ID = "System_ThumbnailCacheId"
_EDB_PATH = "System_ItemPathDisplay"
_EDB_NAME = "System_ItemNameDisplay"

# A healthy index has far fewer rows than this; a cursor that stops advancing
# does not. Both bounds turn "spins forever" into "stops and says so".
EDB_ROW_CAP = 5_000_000
EDB_ERROR_RUN = 1_000      # this many failed rows in a row ends the read
EDB_TIME_LIMIT = 120       # seconds the child may take before it is killed


def _edb_cache_id(value, byte_order: str):
    """The identifier from an ESE column, which the reader hands back hex-encoded."""
    if isinstance(value, int):
        return value & 0xFFFFFFFFFFFFFFFF
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        try:
            raw = binascii.unhexlify(raw)
        except (binascii.Error, ValueError):
            pass
        if len(raw) == 8:
            return struct.unpack(byte_order + "Q", raw)[0]
    return None


def _edb_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.rstrip("\x00")
    if isinstance(value, (bytes, bytearray)):
        try:
            return binascii.unhexlify(bytes(value)).decode(
                "utf-16-le", "replace").rstrip("\x00")
        except (binascii.Error, ValueError):
            return ""
    return str(value)


def _ese_reader():
    """The vendored ESE module, imported from fme/vendor on first use."""
    from fme import resources
    vendor = resources.vendor_dir()
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    import impacket_ese  # noqa: PLC0415 - vendored, imported by name
    return impacket_ese


def edb_map(path: str) -> tuple:
    """`({cache id: (path, name)}, stats)` from a Windows.edb, in this process.

    **Do not call this from the program.** It is what the isolated child
    runs; everything else calls `edb_map_isolated`. The reader it uses can
    loop without end on a damaged file, and only a process boundary with a
    deadline is guaranteed to stop that.

    `stats` says what was read and what was not: rows read, rows the reader
    could not decode, and whether the read stopped early. GLEAPP's version of
    this loop skipped an undecodable row with a bare `continue` and kept no
    count -- so a read that lost half an index looked like one that lost
    nothing, and a cursor failing at one position retried five million times.
    A row that could not be read is evidence that was not examined, and the
    examiner is told how many.
    """
    reader = _ese_reader()
    result: dict = {}
    stats = {"table": "", "rows": 0, "row_errors": 0, "stopped": ""}
    database = reader.ESENT_DB(str(path))
    try:
        database.mountDB()
        cursor = byte_order = None
        for table, byte_order in EDB_TABLES:
            cursor = database.openTable(table)
            if cursor is not None:
                stats["table"] = table
                break
        if cursor is None:
            stats["stopped"] = "no Windows Search property table"
            return result, stats
        id_col = path_col = name_col = None
        error_run = 0
        while stats["rows"] + stats["row_errors"] < EDB_ROW_CAP:
            try:
                row = database.getNextRow(cursor)
            except Exception:  # noqa: BLE001 - the reader raises anything
                stats["row_errors"] += 1
                error_run += 1
                if error_run >= EDB_ERROR_RUN:
                    stats["stopped"] = (f"{EDB_ERROR_RUN:,} unreadable rows in "
                                        f"a row; the rest of the table was "
                                        f"not read")
                    break
                continue
            error_run = 0
            if row is None:
                break
            stats["rows"] += 1
            row = {(k.decode("latin-1") if isinstance(k, (bytes, bytearray))
                    else k): v for k, v in row.items()}
            if id_col is None:
                # Column names carry a per-machine numeric tag, so they are
                # matched by suffix off the first row rather than assumed.
                for key in row:
                    if key.endswith(_EDB_ID):
                        id_col = key
                    elif key.endswith(_EDB_PATH):
                        path_col = key
                    elif key.endswith(_EDB_NAME):
                        name_col = key
                if id_col is None:
                    stats["stopped"] = "the index records no ThumbnailCacheId"
                    return result, stats
            cache_id = _edb_cache_id(row.get(id_col), byte_order)
            if cache_id is not None:
                result[cache_id] = (_edb_text(row.get(path_col)),
                                    _edb_text(row.get(name_col)))
        else:
            stats["stopped"] = f"stopped at {EDB_ROW_CAP:,} rows"
        return result, stats
    finally:
        try:
            database.close()
        except Exception:  # noqa: BLE001 - closing a damaged file
            pass


def edb_worker(args: list) -> int:
    """The child's side: read one Windows.edb, answer as JSON on stdout."""
    try:
        found, stats = edb_map(args[0])
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    json.dump({"ids": {f"{k:016x}": list(v) for k, v in found.items()},
               "stats": stats}, sys.stdout)
    return 0


def edb_map_isolated(path: str, timeout: Optional[float] = None) -> tuple:
    """`edb_map` in a child process. Returns `(mapping, stats)`; never hangs.

    A child that hangs, crashes or fails says so in `stats["stopped"]` and
    returns an empty mapping -- which `resolve` reports as an index that
    could not be read, never as an index that named nothing.
    """
    from fme.engines import isolated
    # Read at call time, not bound as a default, so a caller (or a test)
    # that changes the limit is obeyed.
    limit = EDB_TIME_LIMIT if timeout is None else timeout
    outcome = isolated.run("edb", [os.path.abspath(path)], limit)
    if not outcome.ok:
        said = {"timeout": "did not finish", "crashed": "crashed",
                "failed": "failed"}.get(outcome.status, outcome.status)
        return {}, {"table": "", "rows": 0, "row_errors": 0,
                    "stopped": f"the ESE reader {said} ({outcome.detail})",
                    "failed": outcome.status}
    try:
        answer = json.loads(outcome.stdout.decode("utf-8", "replace") or "{}")
        mapping = {int(k, 16): tuple(v) for k, v in answer["ids"].items()}
        return mapping, answer.get("stats", {})
    except (ValueError, KeyError, TypeError) as exc:
        return {}, {"table": "", "rows": 0, "row_errors": 0,
                    "stopped": f"the ESE reader's answer was unreadable: {exc}",
                    "failed": "failed"}


def index_map(path: str) -> dict:
    """`{cache id: (path, name)}` from a SQLite Windows Search index.

    The property store's table name carries a catalog number that differs
    between machines, so it is found by pattern rather than assumed, and the
    column numbers are read from the metadata table rather than hard-coded:
    both change, and a hard-coded guess would silently join the wrong
    column.
    """
    found = {}
    try:
        con = sqlite3.connect(ro_uri(path, immutable=True), uri=True)
    except sqlite3.Error:
        return found

    # A time limit on the whole read.
    #
    # This index comes off a seized machine: it may be truncated, it may
    # have been written while the machine was pulled, and `immutable=1`
    # tells SQLite to trust it rather than check it. A damaged b-tree can
    # send a query round a loop that does not end, and a run that hangs
    # with no message is the worst way for this to fail -- an examiner
    # waits, then kills it, and has no idea which part stalled.
    #
    # The progress handler is called every few thousand virtual-machine
    # instructions and aborts the query by returning non-zero. Sixty
    # seconds is far more than a healthy index of any size needs.
    deadline = time.monotonic() + INDEX_TIME_LIMIT

    def give_up_if_slow():
        return 1 if time.monotonic() > deadline else 0

    con.set_progress_handler(give_up_if_slow, 10000)
    try:
        cursor = con.cursor()
        row = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "name LIKE 'SystemIndex%PropertyStore' "
            "AND name NOT LIKE '%Metadata' LIMIT 1").fetchone()
        if not row:
            return found
        store = row[0]
        metadata = store + "_Metadata"

        def column(name):
            try:
                got = cursor.execute(
                    f'SELECT Id FROM "{metadata}" WHERE Name = ?',
                    (name,)).fetchone()
            except sqlite3.Error:
                return None
            return got[0] if got else None

        id_column = column("System.ThumbnailCacheId")
        path_column = column("System.ItemPathDisplay")
        name_column = column("System.ItemNameDisplay")
        if id_column is None:
            return found

        wanted = tuple(c for c in (id_column, path_column, name_column)
                       if c is not None)
        items = {}
        for work_id, column_id, value in cursor.execute(
                f'SELECT WorkId, ColumnId, Value FROM "{store}" '
                f'WHERE ColumnId IN ({",".join("?" * len(wanted))})', wanted):
            items.setdefault(work_id, {})[column_id] = value

        for properties in items.values():
            cache_id = _as_cache_id(properties.get(id_column))
            if cache_id is None:
                continue
            found[cache_id] = (_text(properties.get(path_column)),
                               _text(properties.get(name_column)))
        return found
    except sqlite3.Error:
        return found
    finally:
        con.close()


def cache_id_of(record: dict):
    """The thumbnail cache identifier a recovered file was carved under."""
    for field in (record.get("note") or "", record.get("source_path") or ""):
        match = CACHE_ID_IN_NOTE.search(field)
        if match:
            try:
                return int(match.group(1), 16)
            except ValueError:
                continue
    return None


def resolve(run_dir: str, index_path: str) -> dict:
    """Name every cached thumbnail in a run that the index knows about."""
    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    ese_stats = None
    if is_ese(index_path):
        index, ese_stats = edb_map_isolated(index_path)
        if ese_stats.get("failed"):
            # A reader that hung or crashed has said nothing about the
            # index. Reporting "no identifiers found" here would read as a
            # finding about the exhibit when it is a failure of the tool.
            raise ValueError(
                f"{os.path.basename(index_path)} could not be read: "
                f"{ese_stats['stopped']}. Nothing was matched. The file may be "
                f"damaged or incomplete; a copy exported from the exhibit "
                f"with esentutl /p may read where this one does not.")
    else:
        index = index_map(index_path)
    if not index:
        why = f" ({ese_stats['stopped']})" if ese_stats and ese_stats.get("stopped") else ""
        raise ValueError(
            f"No thumbnail identifiers found in {os.path.basename(index_path)}"
            f"{why}. It may not be a Windows Search index, or may not record "
            f"System.ThumbnailCacheId.")

    rows, resolved = [], {}
    cached = 0
    for record in manifest.get("files", []):
        cache_id = cache_id_of(record)
        if cache_id is None:
            continue
        cached += 1
        where = index.get(cache_id)
        if not where:
            continue
        path, name = where
        rows.append({"out_path": record.get("out_path", ""),
                     "sha256": record.get("sha256", ""),
                     "cache_id": f"{cache_id:016x}",
                     "indexed_path": path, "indexed_name": name})
        resolved[record.get("sha256", "")] = {"path": path, "name": name,
                                              "cache_id": f"{cache_id:016x}"}

    out_csv = os.path.join(run_dir, "thumbnail_sources.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "out_path", "sha256", "cache_id", "indexed_path", "indexed_name"])
        writer.writeheader()
        writer.writerows(rows)

    # Kept with the case so the interface can show it, like any other result
    # of a pass that costs time to produce.
    case_path = os.path.join(run_dir, "case.db")
    if os.path.isfile(case_path) and resolved:
        try:
            from fme.review import casedb
            case = casedb.CaseDB(case_path)
            case.save_derived("thumbnail_sources", resolved)
            case.close()
        except (OSError, sqlite3.Error):
            pass

    result = {"index": os.path.abspath(index_path), "indexed_items": len(index),
              "cached_thumbnails": cached, "named": len(rows), "csv": out_csv,
              "format": "ESE" if ese_stats is not None else "SQLite"}
    if ese_stats is not None:
        result["ese"] = ese_stats
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Name cached thumbnails using a Windows Search index.")
    ap.add_argument("-o", "--run", required=True, help="a run's output folder")
    ap.add_argument("--index", required=True,
                    help="Windows.db or Windows.edb from the exhibit")
    args = ap.parse_args(argv)

    try:
        result = resolve(args.run, args.index)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 2

    print(f"Index          : {result['index']} ({result['format']})")
    if result.get("ese"):
        ese = result["ese"]
        print(f"ESE table      : {ese.get('table') or '-'}, "
              f"{ese.get('rows', 0):,} rows read")
        if ese.get("row_errors"):
            print(f"Unreadable rows: {ese['row_errors']:,} -- items in those "
                  f"rows could not be matched")
        if ese.get("stopped"):
            print(f"Stopped early  : {ese['stopped']}")
    print(f"Items indexed  : {result['indexed_items']:,}")
    print(f"Cached thumbs  : {result['cached_thumbnails']:,} in this run")
    print(f"Named          : {result['named']:,}")
    print(f"Written        : {result['csv']}")
    if result["cached_thumbnails"] and not result["named"]:
        print("\nNone matched. The index records what Windows Search happened\n"
              "to index, so a miss means the item was not in it -- not that\n"
              "the thumbnail has been ruled out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
