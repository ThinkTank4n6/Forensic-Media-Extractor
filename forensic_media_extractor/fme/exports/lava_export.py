"""
lava_export.py -- write a LAVA-compliant output set from a media_core run.

LAVA (the LEAPP Artifact Viewer App) reads a folder containing:

    _lava_artifacts.db      SQLite: one table per artifact, plus LAVA's own
                            _lava_media_items / _lava_media_references and
                            the artifact-pattern tables
    _lava_data.lava         JSON index: parser info, and per-category
                            artifact descriptors naming each table, its
                            column labels, its typed columns and its icon
    media/                  the media files themselves, named by id
    _lava_work.db           tags and annotations -- created by LAVA itself,
                            not written here

Everything below was derived from a real iLEAPP 2.5.0 output set: the table
shapes, the id scheme (SHA-1 hex), epoch-second timestamps, the
`object_columns` types (datetime, media, phonenumber) and the way a media
cell holds a media item id rather than a path.

    python3 lava_export.py -m out/manifest.json -o ./lava -c DC26-253

The recovered files are hard-linked into media/ where the filesystem allows
it, so a large export costs no extra disk.
"""

from __future__ import annotations

# Allow this file to be run directly as well as imported. Run as a script
# there is no package context, so the project root goes on the path first.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import sys
import time
from fme.sqlite_uri import ro_uri
from fme.engines import timeutil

__version__ = "1.0.0"

# LAVA reads timestamps as epoch seconds.
def epoch(iso: str) -> int | None:
    if not iso:
        return None
    # As in the C4All export: no epoch for a time with no recorded zone,
    # rather than one in whatever zone the examining machine is set to.

    when = timeutil.aware(iso)
    if when is None:
        return None
    try:
        return int(when.timestamp())
    except (ValueError, TypeError, OSError, OverflowError):
        return None


# Each engine becomes its own artifact, because the provenance differs and an
# examiner filters on exactly that: a file listed in a directory entry is a
# different kind of evidence from one carved out of unallocated space.
ARTIFACTS = {
    "filesystem": {
        "name": "Recovered Media - File System",
        "tablename": "recovered_media_filesystem",
        "icon": "hard-drive",
        "category": "Recovered Media",
        "module": "recoveredMediaFilesystem",
        "description": "Media recovered from file system entries, allocated "
                       "and deleted, with their recorded paths and timestamps",
    },
    "carve": {
        "name": "Recovered Media - Carved",
        "tablename": "recovered_media_carved",
        "icon": "search",
        "category": "Recovered Media",
        "module": "recoveredMediaCarved",
        "description": "Media carved from unallocated space by file signature, "
                       "independent of any file system",
    },
    "database": {
        "name": "Recovered Media - Database Blobs",
        "tablename": "recovered_media_database",
        "icon": "database",
        "category": "Recovered Media",
        "module": "recoveredMediaDatabase",
        "description": "Media extracted from SQLite BLOB columns, including "
                       "blobs split across overflow pages",
    },
    "package": {
        "name": "Recovered Media - Packages",
        "tablename": "recovered_media_package",
        "icon": "package",
        "category": "Recovered Media",
        "module": "recoveredMediaPackage",
        "description": "Media read out of report packages and archives "
                       "(UFDR, zip, tar)",
    },
    "folder": {
        "name": "Recovered Media - Folder Scan",
        "tablename": "recovered_media_folder",
        "icon": "folder",
        "category": "Recovered Media",
        "module": "recoveredMediaFolder",
        "description": "Media found by walking a mounted volume or export "
                       "folder file by file",
    },
    "embedded": {
        "name": "Recovered Media - Embedded",
        "tablename": "recovered_media_embedded",
        "icon": "layers",
        "category": "Recovered Media",
        "module": "recoveredMediaEmbedded",
        "description": "Media found inside other files: documents, archives, "
                       "compressed streams, base64 in text, EXIF thumbnails",
    },
    "projectvic": {
        "name": "Recovered Media - Project VIC Import",
        "tablename": "recovered_media_projectvic",
        "icon": "download",
        "category": "Recovered Media",
        "module": "recoveredMediaProjectVic",
        "description": "Media imported from a Project VIC export",
    },
}

# db column -> display label, in the order LAVA should show them.
COLUMN_MAP = {
    "capture_timestamp": "Captured (EXIF)",
    "modified_timestamp": "Modified Timestamp",
    "accessed_timestamp": "Accessed Timestamp",
    "created_timestamp": "Created Timestamp",
    "media": "Media",
    "filename": "File Name",
    "camera": "Camera",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "media_type": "Type",
    "extension": "Extension",
    "size_bytes": "Size (bytes)",
    "deleted": "Deleted",
    "source_path": "Source Path",
    "file_system": "File System",
    "partition": "Partition",
    "byte_offset": "Byte Offset",
    "signature": "Signature",
    "md5": "MD5",
    "sha1": "SHA-1",
    "sha256": "SHA-256",
    "notes": "Notes",
}

OBJECT_COLUMNS = [
    {"name": "capture_timestamp", "type": "datetime"},
    {"name": "modified_timestamp", "type": "datetime"},
    {"name": "accessed_timestamp", "type": "datetime"},
    {"name": "created_timestamp", "type": "datetime"},
    {"name": "media", "type": "media"},
]

SUMMARY_TABLE = "media_extraction_summary"


def sha1_hex(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
    return h.hexdigest()


# Formats no browser-based viewer can display. LAVA is Electron, so these
# resolve correctly and then show a broken-image icon; saying so in the row
# is better than leaving the examiner to wonder whether the export failed.
NOT_BROWSER_RENDERABLE = {"emf", "wmf", "tif", "tiff", "psd", "dng", "cr2",
                          "cr3", "nef", "arw", "orf", "rw2", "raf", "srw",
                          "pef", "x3f", "mrw", "3fr", "erf", "jp2", "jxr",
                          "jxl", "mng", "icns", "pcx", "art", "dds", "exr",
                          "qoi", "ktx", "mxf", "m2ts", "ts", "mpg", "mpeg", "vob",
                          "wmv", "asf", "avi", "flv", "rm", "swf", "dv"}


def mime_for(path: str, ext: str) -> str:
    guess = mimetypes.guess_type(path)[0]
    if guess:
        return guess
    return {"heic": "image/heic", "avif": "image/avif", "webp": "image/webp",
            "mov": "video/quicktime", "mkv": "video/x-matroska",
            "webm": "video/webm", "flv": "video/x-flv", "ts": "video/mp2t",
            "cr2": "image/x-canon-cr2", "nef": "image/x-nikon-nef",
            }.get(ext.lower(), "application/octet-stream")


def stage_media(src: str, dest_dir: str, media_id: str, ext: str,
                mode: str = "hardlink", html_root: str = "") -> str:
    """Put one recovered file where LAVA will look for it.

    **The file is written twice, on purpose.** A media item records its
    location as ``media/<id>.<ext>``. The viewer joins that onto
    ``<report>/media/``, producing ``<report>/media/media/<id>.<ext>``, which
    no report contains; it then falls through to
    ``<report>/_HTML/<extraction_path>``, which is the copy it actually
    serves. Its "open externally" handler resolves only against ``_HTML/``
    and has no fallback at all.

    That is why previews were blank while every path in the interface looked
    correct: the database, the manifest and the staged file were all right,
    and the viewer was reading somewhere else. iLEAPP writes both copies for
    the same reason.

    The second copy is hard-linked where the file system allows it, so it
    costs a directory entry rather than the bytes again.
    """
    os.makedirs(dest_dir, exist_ok=True)
    name = f"{media_id}.{ext.lower()}" if ext else media_id
    relative = f"media/{name}"

    targets = [os.path.join(dest_dir, name)]
    if html_root:
        html_media = os.path.join(html_root, "media")
        os.makedirs(html_media, exist_ok=True)
        targets.append(os.path.join(html_media, name))

    for index, dest in enumerate(targets):
        if os.path.exists(dest):
            continue
        source = src if index == 0 else targets[0]
        if mode == "copy" and index == 0:
            shutil.copy2(source, dest)
            continue
        try:
            os.link(source, dest)
        except (OSError, NotImplementedError):
            shutil.copy2(source, dest)
    return relative


def export(manifest_path: str, out_dir: str, *, case_number: str = "",
           examiner: str = "", evidence: str = "", mode: str = "hardlink",
           include_duplicates: bool = True, absolute_media: bool = False,
           tool_name: str = "media_core", tool_version: str = "") -> dict:
    """Write the LAVA output set. Returns a summary."""
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    records = list(manifest.get("files", []))
    if include_duplicates:
        # Every place a file was found is its own finding. The bytes are
        # stored once; these rows point at the same copy.
        records += manifest.get("duplicates", [])
    summary = manifest.get("summary", {})
    if not records:
        raise ValueError("The manifest contains no recovered files to export.")

    source_dir = os.path.dirname(os.path.abspath(manifest_path))
    os.makedirs(out_dir, exist_ok=True)
    media_dir = os.path.join(out_dir, "media")
    html_root = os.path.join(out_dir, "_HTML")
    db_path = os.path.join(out_dir, "_lava_artifacts.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    con = sqlite3.connect(db_path)
    _create_lava_tables(con)

    used, counts, staged, missing = {}, {}, 0, 0
    for rec in records:
        engine = rec.get("engine", "carve")
        spec = ARTIFACTS.get(engine, ARTIFACTS["carve"])
        table = spec["tablename"]
        if table not in used:
            _create_artifact_table(con, table)
            used[table] = spec
            counts[table] = 0

        src = os.path.join(source_dir, rec.get("out_path", ""))
        if not os.path.isfile(src):
            missing += 1
            continue

        # LAVA ids are SHA-1 hex. The content hash is already computed, and
        # using it means identical files share one media item, which is what
        # the reference set does.
        media_id = (rec.get("sha1") or "").lower() or sha1_hex(rec["out_path"])
        ext = (rec.get("ext") or "").lower()
        rel = stage_media(src, media_dir, media_id, ext, mode,
                          html_root=html_root)
        staged += 1
        # extraction_path is normally relative to the folder holding the
        # project file, which is what a real iLEAPP output records. If a
        # viewer resolves it against something else, previews come back
        # blank while every other field looks correct -- absolute paths
        # remove the question at the cost of the folder no longer being
        # movable.
        recorded_path = (os.path.join(os.path.abspath(out_dir),
                                      rel.replace("/", os.sep))
                         if absolute_media else rel)

        now = int(time.time())
        con.execute(
            "INSERT OR IGNORE INTO _lava_media_items "
            "(id, source_path, extraction_path, type, metadata, created_at, "
            " updated_at, is_embedded) VALUES (?,?,?,?,?,?,?,?)",
            (media_id, rec.get("source_path") or rec.get("out_path"),
             recorded_path,
             mime_for(src, ext), "not parsed yet", now, now,
             # iLEAPP distinguishes media read from a file in the extraction
             # from bytes recovered in memory. Carved, decoded and blob media
             # never existed as a file on the evidence.
             1 if engine in ("carve", "database", "embedded") else 0))
        # The media CELL holds the reference id, not the media item id. A
        # reference is the link between one row and one media item, and LAVA
        # resolves the cell through it: cell -> reference -> item ->
        # extraction_path. Storing the item id here instead produced an
        # artifact with a media column that displayed nothing at all.
        reference_id = sha1_hex(media_id, table, rec.get("out_path", ""))
        # The reference name is the file as it exists in media/, not the name
        # this tool gave the recovered copy. Those differed -- the column
        # showed "recovered_24826_embedded_412372.jpg" while the folder held
        # "<sha1>.jpg" -- which is at best confusing and, if a viewer resolves
        # the preview by name rather than by extraction_path, fatal. The
        # human-readable name is still in the row's File Name column.
        con.execute(
            "INSERT OR IGNORE INTO _lava_media_references "
            "(id, media_item_id, module_name, artifact_name, name) "
            "VALUES (?,?,?,?,?)",
            (reference_id, media_id, spec.get("module", tool_name),
             spec["name"], os.path.basename(rel)))

        con.execute(
            f'INSERT INTO "{table}" (capture_timestamp, camera, latitude, '
            f'longitude, modified_timestamp, accessed_timestamp, '
            f'created_timestamp, media, filename, media_type, extension, '
            f'size_bytes, deleted, source_path, file_system, partition, '
            f'byte_offset, signature, md5, sha1, sha256, notes) '
            f'VALUES ({",".join("?" * 22)})',
            (epoch(timeutil.stored(rec, "captured")),
             " ".join(x for x in (rec.get("camera_make", ""),
                                  rec.get("camera_model", "")) if x),
             rec.get("latitude"), rec.get("longitude"),
             epoch(timeutil.stored(rec, "mtime")), epoch(timeutil.stored(rec, "atime")),
             epoch(timeutil.stored(rec, "crtime") or timeutil.stored(rec, "ctime")),
             reference_id, os.path.basename(rec.get("out_path", "")),
             rec.get("category", ""), ext, rec.get("size", 0),
             "Yes" if rec.get("deleted") else "No",
             rec.get("source_path", ""), rec.get("fs_type", ""),
             rec.get("partition", ""),
             "" if rec.get("offset") is None else str(rec["offset"]),
             rec.get("signature", ""), (rec.get("md5") or "").upper(),
             (rec.get("sha1") or "").upper(), (rec.get("sha256") or "").upper(),
             _note_for(rec)))
        counts[table] += 1

    summary_rows = _write_summary_table(con, summary, case_number, examiner,
                                        evidence, staged, missing)
    _write_search_patterns(con, summary, tool_name)
    con.commit()
    con.close()

    lava = _build_index(used, counts, summary, case_number, examiner,
                        evidence, tool_name, tool_version, staged,
                        summary_rows, os.path.abspath(out_dir))
    # The two pages LAVA shows as tabs, under the _HTML tree it serves from.
    write_html_logs(out_dir, manifest.get("summary", {}),
                    case_number=case_number, examiner=examiner,
                    evidence=evidence,
                    tool=f"{tool_name} {tool_version}".strip())

    with open(os.path.join(out_dir, "_lava_data.lava"), "w",
              encoding="utf-8") as fh:
        json.dump(lava, fh, indent=1)

    return {
        "output_dir": os.path.abspath(out_dir),
        "artifacts": len(used) + 1,
        "rows": sum(counts.values()) + summary_rows,
        "media_staged": staged,
        "missing": missing,
        "tables": sorted(used),
    }


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
def _note_for(rec: dict) -> str:
    """The row's note, with a word about formats the viewer cannot draw."""
    note = rec.get("note", "") or ""
    if (rec.get("ext") or "").lower() in NOT_BROWSER_RENDERABLE:
        hint = ("no preview: this format cannot be displayed in a browser-based "
                "viewer; open the file from media/ in a native application")
        note = f"{note}; {hint}" if note else hint
    return note


def _create_lava_tables(con: sqlite3.Connection) -> None:
    """LAVA's own tables, matching the shapes in a real iLEAPP output."""
    con.executescript("""
        CREATE TABLE _artifact_search_patterns (
            id INTEGER PRIMARY KEY,
            module_name TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            regex TEXT NOT NULL);
        CREATE TABLE _file_path_list (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL);
        CREATE TABLE _artifact_pattern_to_file (
            id INTEGER PRIMARY KEY,
            artifact_search_pattern_id INTEGER NOT NULL,
            file_path_id INTEGER NOT NULL,
            FOREIGN KEY (artifact_search_pattern_id)
                REFERENCES _artifact_search_patterns(id),
            FOREIGN KEY (file_path_id) REFERENCES _file_path_list(id));
        CREATE TABLE _lava_media_items (
            id TEXT PRIMARY KEY,
            source_path TEXT,
            extraction_path TEXT,
            type TEXT,
            metadata TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            is_embedded INTEGER);
        CREATE TABLE _lava_media_references (
            id TEXT PRIMARY KEY,
            media_item_id TEXT,
            module_name TEXT,
            artifact_name TEXT,
            name TEXT,
            FOREIGN KEY (media_item_id) REFERENCES _lava_media_items(id));
    """)


def _create_artifact_table(con: sqlite3.Connection, table: str) -> None:
    con.execute(f'''CREATE TABLE "{table}" (
        capture_timestamp INTEGER, camera TEXT, latitude REAL, longitude REAL,
        modified_timestamp INTEGER, accessed_timestamp INTEGER,
        created_timestamp INTEGER, media TEXT, filename TEXT,
        media_type TEXT, extension TEXT, size_bytes INTEGER, deleted TEXT,
        source_path TEXT, file_system TEXT, partition TEXT,
        byte_offset TEXT, signature TEXT, md5 TEXT, sha1 TEXT,
        sha256 TEXT, notes TEXT)''')


def _write_summary_table(con, summary: dict, case_number: str, examiner: str,
                         evidence: str, staged: int, missing: int) -> int:
    con.execute(f'CREATE TABLE "{SUMMARY_TABLE}" '
                f'(property TEXT, property_value TEXT)')
    rows = [
        ("Case Number", case_number),
        ("Examiner", examiner),
        ("Evidence Item", evidence),
        ("Source", summary.get("image", "")),
        ("Source Size", summary.get("image_size_human", "")),
        ("Mode", summary.get("mode", "")),
        ("Started (UTC)", summary.get("started_utc", "")),
        ("Elapsed (seconds)", str(summary.get("elapsed_seconds", ""))),
        ("Files Recovered", str(summary.get("total_files", ""))),
        ("From File System", str(summary.get("fs_files", 0))),
        ("Deleted Entries Recovered", str(summary.get("fs_deleted", 0))),
        ("Carved", str(summary.get("carved_files", 0))),
        ("From Database Blobs", str(summary.get("db_files", 0))),
        ("Duplicates Collapsed (SHA-256)",
         str(summary.get("duplicates_suppressed", 0))),
        ("Blank Files Dropped", str(summary.get("blank_dropped", 0))),
        ("Not Recovered: Output Write Failed", str(summary.get("write_failed", 0))),
        ("Not Recovered: Evidence Read Failed", str(summary.get("read_failed", 0))),
        ("Oversized Entries Skipped", str(summary.get("skipped_large", 0))),
        ("Media Staged For LAVA", str(staged)),
        ("Manifest Rows Without A File", str(missing)),
        ("Extraction Tool", summary.get("tool", "")),
        ("Engine", summary.get("engine", "")),
    ]
    written = [(k, v) for k, v in rows if v not in ("", None)]
    con.executemany(f'INSERT INTO "{SUMMARY_TABLE}" VALUES (?,?)', written)
    return len(written)


def _write_search_patterns(con, summary: dict, tool_name: str) -> None:
    """LAVA lists what each artifact looked at. For an image-level tool that
    is the image itself rather than a set of glob patterns."""
    source = summary.get("image", "")
    if not source:
        return
    con.execute("INSERT INTO _artifact_search_patterns "
                "(module_name, artifact_name, regex) VALUES (?,?,?)",
                (tool_name, "recovered_media", source))
    con.execute("INSERT INTO _file_path_list (file_path) VALUES (?)", (source,))
    con.execute("INSERT INTO _artifact_pattern_to_file "
                "(artifact_search_pattern_id, file_path_id) VALUES (1, 1)")


def _build_index(used: dict, counts: dict, summary: dict, case_number: str,
                 examiner: str, evidence: str, tool_name: str,
                 tool_version: str, staged: int, summary_rows: int,
                 out_dir: str) -> dict:
    """The _lava_data.lava index."""
    import platform

    artifacts: dict = {}
    meta_artifacts = []
    for table, spec in used.items():
        artifacts.setdefault(spec["category"], []).append({
            "name": spec["name"],
            "tablename": table,
            "module": spec.get("module", tool_name),
            "column_map": dict(COLUMN_MAP),
            "artifact_icon": spec["icon"],
            "record_count": counts.get(table, 0),
            "source_path": summary.get("image", ""),
            "object_columns": [dict(c) for c in OBJECT_COLUMNS],
        })
        meta_artifacts.append({
            "artifact_key": table,
            "tablename": table,
            "name": spec["name"],
            "description": spec["description"],
            "author": tool_name,
            "created_date": "",
            "last_updated_date": "",
            "notes": "Category is not set by this tool; grade in your review "
                     "platform.",
            "category": spec["category"],
        })

    artifacts["Extraction Summary"] = [{
        "name": "Media Extraction Summary",
        "tablename": SUMMARY_TABLE,
        "module": tool_name,
        "column_map": {"property": "Property", "property_value": "Value"},
        "artifact_icon": "settings",
        "record_count": summary_rows,
        "source_path": summary.get("image", ""),
        "object_columns": [],
    }]
    meta_artifacts.append({
        "artifact_key": SUMMARY_TABLE,
        "tablename": SUMMARY_TABLE,
        "name": "Media Extraction Summary",
        "description": "Parameters and counts for the extraction run",
        "author": tool_name,
        "created_date": "",
        "last_updated_date": "",
        "notes": "",
        "category": "Extraction Summary",
    })

    # parser_info is kept to exactly the key set a real iLEAPP output uses.
    # Case number, examiner and evidence item live in the summary artifact
    # instead, where LAVA will display them, rather than as extra keys a
    # strict reader might not expect.
    started = summary.get("started_utc", "")
    return {
        "parser_info": {
            "leapp_name": tool_name,
            "leapp_version": tool_version or __version__,
            "leapp_mode": "CLI",
            "package": "Python",
            "OS": platform.platform(),
            "start_timestamp": str(epoch(started) or int(time.time())),
            "end_timestamp": str(int(time.time())),
        },
        "param_input": summary.get("image", ""),
        # This must be the folder that holds _lava_data.lava, the database and
        # media/ -- that is what the reference iLEAPP output records, and a
        # viewer resolving extraction_path against it will look here. Pointing
        # it at the parent directory leaves every preview blank.
        "param_output": out_dir,
        "param_type": summary.get("mode", ""),
        # "cancelled" is how versions before v36 spelled it; an old manifest
        # should still report the run as stopped rather than complete.
        "processing_status": "Canceled" if (summary.get("canceled")
                                            or summary.get("cancelled"))
                             else "Complete",
        "lava_db_name": "_lava_artifacts.db",
        "modules": [],
        "artifacts": artifacts,
        "meta": {"modules": [{
            "module_name": tool_name,
            "module_filename": f"{tool_name}.py",
            "artifacts": meta_artifacts,
        }]},
    }


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _escape(text: str) -> str:
    import html as _html
    return _html.escape(text or "")


def write_html_logs(out_dir: str, summary: dict, *, case_number: str = "",
                    examiner: str = "", evidence: str = "",
                    tool: str = "") -> list:
    """The two pages LAVA shows as its Device Info and Screen Output tabs.

    They are where a reader finds what produced the project and over what,
    which matters more in a viewer that mixes output from several tools.
    """
    logs = os.path.join(out_dir, "_HTML", "_Script_Logs")
    os.makedirs(logs, exist_ok=True)

    def page(title, rows, note=""):
        body = "".join(
            f"<tr><td>{_escape(str(k))}</td><td>{_escape(str(v))}</td></tr>"
            for k, v in rows if v not in ("", None))
        return (f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{_escape(title)}</title><style>"
                "body{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;"
                "margin:22px;color:#1d2129}"
                "h1{font-size:19px;margin:0 0 12px}"
                "table{border-collapse:collapse}"
                "td{padding:4px 16px 4px 0;border-bottom:1px solid #e3e6ea;"
                "vertical-align:top}"
                "td:first-child{color:#5a6472;white-space:nowrap}"
                "p{color:#5a6472}</style></head><body>"
                f"<h1>{_escape(title)}</h1><table>{body}</table>"
                f"<p>{_escape(note)}</p></body></html>")

    device = [
        ("Case number", case_number), ("Evidence item", evidence),
        ("Examiner", examiner),
        ("Source", summary.get("image", "")),
        ("Source size", summary.get("image_size_human", "")),
        ("Container", summary.get("container", "")),
        ("File systems", ", ".join(
            v.get("fs", "") for v in (summary.get("volumes") or []) if v.get("fs"))),
        ("Produced by", tool),
        ("Run started", summary.get("started_utc", "")),
        ("Elapsed", f"{summary.get('elapsed_seconds', 0)}s"),
    ]
    screen = [
        ("Files recovered", summary.get("total_files", 0)),
        ("From the file system", summary.get("fs_files", 0)),
        ("Deleted among them", summary.get("fs_deleted", 0)),
        ("Carved", summary.get("carved_files", 0)),
        ("From databases", summary.get("db_files", 0)),
        ("Embedded in other files", summary.get("embedded_files", 0)),
        ("Further copies of the same files", summary.get("duplicates_suppressed", 0)),
        ("Blank files dropped", summary.get("blank_dropped", 0)),
        ("Not recovered: output write failed", summary.get("write_failed", 0)),
        ("Not recovered: evidence read failed", summary.get("read_failed", 0)),
        ("Stopped because the output failed", summary.get("output_failure") or "no"),
        ("Carve alignment", summary.get("align", "")),
        ("Canceled early", "yes" if (summary.get("canceled")
                                     or summary.get("cancelled")) else "no"),
    ]
    written = []
    for name, title, rows, note in (
            ("DeviceInfo.html", "Source and run", device,
             "Recorded by the tool named above when this project was written."),
            ("Screen_Output.html", "What the run found", screen,
             "Counts are of files recovered, not of files on the evidence. "
             "Identical copies are collapsed; every location is recorded in "
             "duplicates.csv beside the run.")):
        path = os.path.join(logs, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page(title, rows, note))
        written.append(path)
    return written


def validate(out_dir: str) -> dict:
    """Check a LAVA output set the way LAVA would read it."""
    result = {"path": os.path.abspath(out_dir), "ok": False, "problems": [],
              "artifacts": 0, "rows": 0, "media": 0}
    index_path = os.path.join(out_dir, "_lava_data.lava")
    db_path = os.path.join(out_dir, "_lava_artifacts.db")
    for needed in (index_path, db_path):
        if not os.path.isfile(needed):
            result["problems"].append(f"missing {os.path.basename(needed)}")
    if result["problems"]:
        return result

    try:
        index = json.load(open(index_path, encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result["problems"].append(f"_lava_data.lava is not valid JSON: {exc}")
        return result

    for key in ("parser_info", "lava_db_name", "artifacts", "meta"):
        if key not in index:
            result["problems"].append(f"index is missing {key}")

    # param_output has to name this folder: a viewer resolves media paths
    # against it, and a wrong value shows every preview as blank while
    # everything else looks correct.
    declared = (index.get("param_output") or "").rstrip("/\\")
    here = os.path.abspath(out_dir).rstrip("/\\")
    if os.path.normcase(declared) != os.path.normcase(here):
        result["problems"].append(
            f"param_output is {declared!r} but this set is in {here!r}; media "
            f"previews will be blank because the viewer resolves media/ "
            f"against param_output")
    db_name = index.get("lava_db_name", "")
    if db_name and not os.path.isfile(os.path.join(out_dir, db_name)):
        result["problems"].append(
            f"lava_db_name names {db_name}, which is not in this folder")

    con = sqlite3.connect(ro_uri(db_path), uri=True)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for needed in ("_lava_media_items", "_lava_media_references",
                   "_artifact_search_patterns"):
        if needed not in tables:
            result["problems"].append(f"database is missing {needed}")

    for category, arts in index.get("artifacts", {}).items():
        for art in arts:
            result["artifacts"] += 1
            table = art["tablename"]
            if table not in tables:
                result["problems"].append(
                    f"{category}/{art['name']}: table {table} does not exist")
                continue
            cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
            for col in art["column_map"]:
                if col not in cols:
                    result["problems"].append(
                        f"{table}: column_map names {col}, which is not in the table")
            for oc in art.get("object_columns", []):
                if oc["name"] not in cols:
                    result["problems"].append(
                        f"{table}: object_column {oc['name']} is not in the table")
            n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            result["rows"] += n
            if n != art["record_count"]:
                result["problems"].append(
                    f"{table}: record_count says {art['record_count']}, "
                    f"table holds {n}")

    items = list(con.execute(
        "SELECT id, extraction_path FROM _lava_media_items"))
    result["media"] = len(items)
    def _at(base, relative):
        return (relative if os.path.isabs(relative)
                else os.path.join(base, relative.replace("/", os.sep)))

    absent = [p for _, p in items if not os.path.isfile(_at(out_dir, p))]
    # LAVA serves the copy under _HTML. A set without it shows every image as
    # a broken icon while every path in it is correct, which is the hardest
    # kind of fault to find from inside the viewer.
    html_root = os.path.join(out_dir, "_HTML")
    html_absent = [p for _, p in items
                   if not os.path.isabs(p) and not os.path.isfile(_at(html_root, p))]
    if html_absent:
        result["problems"].append(
            f"{len(html_absent)} media file(s) are missing from _HTML/, which "
            f"is where the viewer reads them (e.g. {html_absent[0]}). Every "
            f"image will show as a broken icon. Re-export with this version.")
    logs = os.path.join(out_dir, "_HTML", "_Script_Logs")
    for name in ("DeviceInfo.html", "Screen_Output.html"):
        if not os.path.isfile(os.path.join(logs, name)):
            result["problems"].append(
                f"_HTML/_Script_Logs/{name} is missing; the viewer's tab for "
                f"it will be empty.")
    if absent:
        result["problems"].append(
            f"{len(absent)} media item(s) point at files that are not present, "
            f"e.g. {absent[0]}")
    orphan = con.execute(
        "SELECT COUNT(*) FROM _lava_media_references r "
        "LEFT JOIN _lava_media_items i ON i.id = r.media_item_id "
        "WHERE i.id IS NULL").fetchone()[0]
    if orphan:
        result["problems"].append(f"{orphan} media reference(s) with no item")

    mismatched = [
        (n, e) for n, e in con.execute(
            "SELECT r.name, i.extraction_path FROM _lava_media_references r "
            "JOIN _lava_media_items i ON i.id = r.media_item_id")
        if n and e and n != os.path.basename(e)]
    if mismatched:
        name, path = mismatched[0]
        result["problems"].append(
            f"{len(mismatched)} media reference name(s) do not match the file "
            f"in media/, e.g. {name!r} vs {os.path.basename(path)!r}; a viewer "
            f"resolving the preview by name will find nothing")

    # Follow the chain LAVA follows: cell -> reference -> item -> file. A
    # cell holding the item id instead of the reference id leaves the column
    # visible and the preview empty, which is silent unless checked here.
    for category, arts in index.get("artifacts", {}).items():
        for art in arts:
            table = art["tablename"]
            if table not in tables:
                continue
            for oc in art.get("object_columns", []):
                if oc["type"] != "media":
                    continue
                col = oc["name"]
                unresolved = con.execute(
                    f'SELECT COUNT(*) FROM "{table}" t '
                    f'LEFT JOIN _lava_media_references r ON r.id = t."{col}" '
                    f'WHERE t."{col}" IS NOT NULL AND t."{col}" != "" '
                    f'AND r.id IS NULL').fetchone()[0]
                if unresolved:
                    looks_like_item = con.execute(
                        f'SELECT COUNT(*) FROM "{table}" t '
                        f'JOIN _lava_media_items i ON i.id = t."{col}"').fetchone()[0]
                    hint = (" (they match _lava_media_items.id -- the cell must "
                            "hold the _lava_media_references.id instead)"
                            if looks_like_item else "")
                    result["problems"].append(
                        f"{table}.{col}: {unresolved} cell(s) resolve to no "
                        f"media reference{hint}")
    # A media reference is keyed by (module_name, artifact_name) and has to
    # match what the manifest declares, or the viewer cannot tie the two
    # together.
    ref_pairs = {(m, a) for m, a in con.execute(
        "SELECT DISTINCT module_name, artifact_name FROM _lava_media_references")}
    for category, arts in index.get("artifacts", {}).items():
        for art in arts:
            if not any(c["type"] == "media" for c in art.get("object_columns", [])):
                continue
            pair = (art.get("module"), art.get("name"))
            if ref_pairs and pair not in ref_pairs:
                result["problems"].append(
                    f"{art['tablename']}: manifest declares module/name "
                    f"{pair}, which appears in no media reference")

    con.close()
    result["ok"] = not result["problems"]
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write a LAVA-compliant output set from a media_core run.")
    ap.add_argument("-m", "--manifest", help="manifest.json from a run")
    ap.add_argument("-o", "--out", help="output folder for LAVA")
    ap.add_argument("-c", "--case", default="", help="case number")
    ap.add_argument("-e", "--examiner", default="")
    ap.add_argument("--evidence", default="", help="evidence item name")
    ap.add_argument("--copy", action="store_true",
                    help="copy media instead of hard-linking")
    ap.add_argument("--abs-media", action="store_true",
                    help="record absolute paths to the media files instead of "
                         "paths relative to the project folder. Try this if "
                         "previews are blank while the paths in the viewer "
                         "look right -- it removes any question of what the "
                         "viewer resolves them against. The folder can no "
                         "longer be moved afterwards")
    ap.add_argument("--no-duplicates", action="store_true",
                    help="list each file once instead of once per place it "
                         "was found")
    ap.add_argument("--validate", metavar="FOLDER",
                    help="check an existing LAVA output set instead")
    args = ap.parse_args(argv)

    if args.validate:
        report = validate(args.validate)
        print(f"Folder    : {report['path']}")
        print(f"Artifacts : {report['artifacts']}")
        print(f"Rows      : {report['rows']}")
        print(f"Media     : {report['media']}")
        if report["ok"]:
            print("Verdict   : valid LAVA output set")
            return 0
        print("Verdict   : NOT loadable by LAVA")
        for problem in report["problems"]:
            print(f"  - {problem}")
        return 1

    if not (args.manifest and args.out):
        ap.error("-m and -o are required unless --validate is used")
    try:
        result = export(args.manifest, args.out, case_number=args.case,
                        examiner=args.examiner, evidence=args.evidence,
                        mode="copy" if args.copy else "hardlink",
                        include_duplicates=not args.no_duplicates,
                        absolute_media=args.abs_media)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {result['output_dir']}")
    print(f"  artifacts : {result['artifacts']} ({', '.join(result['tables'])})")
    print(f"  rows      : {result['rows']}")
    print(f"  media     : {result['media_staged']} staged")
    if result["missing"]:
        print(f"  ! {result['missing']} manifest row(s) had no file on disk")
    print("Open the folder in LAVA (File > Open Project).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
