"""
c4all_export.py -- write C4All-style XML reports for a media_core run.

media_core's manifest.json is a working record, not an interchange format:
Griffeye will not read it. This turns a completed run into the report set the
C4All EnScript produces, which Griffeye and the C4All viewer already ingest:

    export/
      DVD01(CASE)/01/00000001.MP4      files renamed and batched for disc
      DVD01(CASE)/01/00000002.JPG
      DVD02(CASE)/01/...
      C4M_Index.xml                    movie index   (<Movie> records)
      C4P_Index.xml                    picture index (<Picture> records)
      Case_Report.xml
      Victim_Report.xml
      Results.txt
      ProjectVIC.json                  optional, with --vic

Everything is written UTF-16 little-endian with a BOM and CRLF line endings,
matching C4All byte for byte in structure -- the indentation, the CDATA on
path fields, the empty-tag style and the epoch-second timestamps are all as
that script emits them.

    python3 c4all_export.py --manifest out/manifest.json --out ./export \\
        --case DC23-282 --analyst "J. Smith" --evidence "1TB WD from HP 20 AIO"

Files are hard-linked into the batch layout where the filesystem allows it,
so the export costs no extra disk; pass --copy to force real copies (needed
when the export lands on a different volume, which is detected and handled
automatically) or --move to relocate them.

One caveat, stated plainly: the <Movie> record layout here was derived from a
real C4All C4M_Index.xml, field for field. The <Picture> record layout is
inferred by analogy -- no picture index was available to check against. If
Griffeye rejects the picture index, run with --all-as-movies to put every
file in the verified movie format, and send a real C4All picture index to
have the mapping corrected.
"""

from __future__ import annotations

# Allow this file to be run directly -- "python fme/engines/media_core.py" --
# as well as imported. Run as a script there is no package context, so the
# project root goes on the path before the package imports below.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import datetime as _dt
import json
import logging
import os
import re
import shutil
import sys
import uuid
from typing import Optional

from fme.engines import timeutil
from fme.exports.docx_parts import _NOT_XML, xml_text

LOG = logging.getLogger(__name__)

# media_core.ORPHAN_ENGINE, written out so this exporter keeps running
# standalone without importing the engine.
ORPHAN_ENGINE = "unlisted-after-crash"

__version__ = "1.0.0"

# C4All writes UTF-16 LE with a BOM and CRLF throughout.
ENCODING = "utf-16-le"
BOM = "\ufeff"
NL = "\r\n"

DEFAULT_MEDIA_BYTES = 4_300_000_000      # a single-layer DVD
DEFAULT_FILES_PER_FOLDER = 1000

VIDEO_CATEGORY = "video"
IMAGE_CATEGORY = "image"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def epoch(iso: str) -> str:
    """C4All writes timestamps as epoch seconds, and an empty tag when the
    value is unknown -- not a zero, which would read as 1970."""
    if not iso:
        return ""
    # Only a time that carries its zone has an epoch. A zone-less one -- a
    # FAT time, a camera's clock -- was read in the examining machine's own
    # zone, so two lab machines exported the same file at two moments.

    when = timeutil.aware(iso)
    if when is None:
        return ""
    try:
        return str(int(when.timestamp()))
    except (ValueError, TypeError, OSError, OverflowError):
        return ""


def cdata(text: str) -> str:
    # A CDATA section escapes markup, not characters: a control character
    # XML 1.0 forbids is as fatal inside one as anywhere. A file named with
    # \x01 on a FAT volume made C4P_Index.xml a document Griffeye refused
    # outright. Such characters are written visibly as \xNN, the way the
    # DOCX writers show them (docx_parts.xml_text), not dropped: they are
    # part of what the evidence recorded.
    text = _NOT_XML.sub(lambda m: f"\\x{ord(m.group()):02x}", str(text or ""))
    # "]]>" cannot appear inside a CDATA section; split it across two.
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def describe(rec: dict) -> str:
    """Build the myDescription string using C4All's own vocabulary, so the
    values look native to anyone reading the index."""
    if rec.get("engine") == ORPHAN_ENGINE:
        return "File, Origin Unknown"
    if rec.get("engine") == "carve":
        return "File, Unallocated Clusters"
    parts = ["File"]
    if rec.get("deleted"):
        parts.append("Deleted")
    parts.append("Archive")
    return ", ".join(parts)


def evidence_path(rec: dict, case: str, evidence: str) -> str:
    """The fullpath field is where the file came from inside the evidence,
    rooted at the case name the way EnCase presents it."""
    root = "\\".join(p for p in (case, evidence) if p)
    source = rec.get("source_path") or ""
    # A file whose row a crash lost has no known origin. Called unallocated
    # space here, it would claim a place in the evidence nobody recorded.
    if rec.get("engine") == ORPHAN_ENGINE and not source:
        return f"{root}\\Origin Unknown (manifest row lost in a crash)"
    if rec.get("engine") == "carve" or not source:
        return f"{root}\\Unallocated Clusters"
    return root + source.replace("/", "\\")


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"


# --------------------------------------------------------------------------
# Batching: lay files out the way C4All stages them for burning
# --------------------------------------------------------------------------
def assign_batches(records: list, case: str, media_bytes: int,
                   files_per_folder: int) -> list:
    """Walk the records in order, starting a new DVD folder whenever the next
    file would overflow the disc, and a new numbered subfolder every
    files_per_folder files."""
    out = []
    disc, folder, disc_bytes, folder_files = 1, 1, 0, 0
    for index, rec in enumerate(records, start=1):
        size = int(rec.get("size") or 0)
        if disc_bytes and disc_bytes + size > media_bytes:
            disc += 1
            folder, disc_bytes, folder_files = 1, 0, 0
        elif folder_files >= files_per_folder:
            folder += 1
            folder_files = 0
        disc_bytes += size
        folder_files += 1
        ext = (rec.get("ext") or "bin").upper()
        out.append({
            "record": rec,
            "id": index,
            "folder": f"DVD{disc:02d}({case})\\{folder:02d}\\",
            "rel_dir": os.path.join(f"DVD{disc:02d}({case})", f"{folder:02d}"),
            "name": f"{index:08d}.{ext}",
        })
    return out


def place_file(src: str, dest: str, mode: str) -> str:
    """Hard-link, copy or move one file into the batch layout. Hard links
    keep the export free; they silently become copies across filesystems.

    Nothing is ever written *into* an existing dest. A re-export into the
    same folder found dest already there, os.link failed, and copy2 then
    wrote through it -- and when dest was a hard link from the earlier
    export, that was the case's own recovered file, which no longer matched
    its manifest hash. The new file is made under a scratch name and renamed
    over dest, which replaces the directory entry and leaves whatever dest
    used to point at untouched.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if mode == "hardlink":
        try:
            if os.path.samefile(src, dest):
                return "linked"  # an earlier export already linked it here
        except OSError:
            pass
    if mode == "move":
        # Never through a scratch name: a rename that failed after the move
        # had the cleanup below delete the only copy of the case's file. The
        # move goes straight to dest, which must not already be there.
        if os.path.lexists(dest):
            raise FileExistsError(
                f"{dest} is already there; a move will not replace it")
        shutil.move(src, dest)
        return "moved"
    # Unique per call, not per process: the interface runs exports on
    # request threads that share one pid, and two placing the same dest
    # dropped each other's scratch file mid-copy.
    scratch = f"{dest}.placing-{uuid.uuid4().hex}"
    _drop(scratch)
    try:
        if mode == "hardlink":
            try:
                os.link(src, scratch)
                os.replace(scratch, dest)
                return "linked"
            except (OSError, NotImplementedError):
                _drop(scratch)  # different volume, or no hard links here
        shutil.copy2(src, scratch)
        os.replace(scratch, dest)
        return "copied"
    except BaseException:
        _drop(scratch)
        raise


def _drop(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# XML writers
# --------------------------------------------------------------------------
def write_utf16(path: str, text: str) -> None:
    with open(path, "wb") as fh:
        fh.write((BOM + text).encode(ENCODING))


def record_xml(item: dict, case: str, evidence: str, element: str,
               name_tag: str, include_length: bool) -> str:
    rec = item["record"]
    offset = rec.get("offset")
    offset = "" if offset is None else str(offset)
    lines = [
        f"  <{element}>",
        f"    <path>{cdata(item['folder'])}</path>",
        f"    <{name_tag}>{xml_text(item['name'])}</{name_tag}>",
        "    <category>0</category>",
        f"    <id>{item['id']}</id>",
        f"    <fileoffset>{offset or '0'}</fileoffset>",
        f"    <fullpath>{cdata(evidence_path(rec, case, evidence))}</fullpath>",
        f"    <created>{epoch(timeutil.stored(rec, 'crtime'))}</created>",
        f"    <accessed>{epoch(timeutil.stored(rec, 'atime'))}</accessed>",
        f"    <written>{epoch(timeutil.stored(rec, 'mtime'))}</written>",
        "    <deleted></deleted>",
        f"    <hash>{(rec.get('md5') or '').upper()}</hash>",
        f"    <sha1Hash>{(rec.get('sha1') or '').upper()}</sha1Hash>",
        "    <encaseHash></encaseHash>",
        f"    <myDescription>{describe(rec)}</myDescription>",
        f"    <physicalLocation>{offset}</physicalLocation>",
        "    <myUnique>0</myUnique>",
        "    <tagged>0</tagged>",
        "    <subCat> </subCat>",
        "    <notes> </notes>",
        f"    <fileSize>{rec.get('size', 0)}</fileSize>",
    ]
    if include_length:
        # -1 is C4All's "duration not determined", which is honest here:
        # nothing in this pipeline decodes the container to time it.
        lines.append("    <movieLength>-1</movieLength>")
    lines += [
        "    <monikers></monikers>",
        f"  </{element}>",
    ]
    return NL.join(lines)


def write_index(path: str, items: list, case: str, evidence: str,
                element: str, name_tag: str, include_length: bool) -> None:
    body = (NL + NL).join(
        record_xml(item, case, evidence, element, name_tag, include_length)
        for item in items)
    text = (f'<?xml version="1.0" encoding="utf-16"?>{NL}'
            f'  <ReportIndex version="2.0">{NL}{NL}'
            f'{body}{NL}{NL}'
            f'  </ReportIndex>{NL}')
    write_utf16(path, text)


def write_case_report(path: str, case: str, analyst: str, comment: str,
                      started: _dt.datetime, excluded: int, tool: str) -> None:
    text = (
        f'<?xml version="1.0" encoding="utf-16"?>{NL}'
        f'  <CaseReport>{NL}'
        f'    <CaseNumber>{cdata(case)}</CaseNumber>{NL}'
        f'    <Analyst>{cdata(analyst or "<Define Value>")}</Analyst>{NL}'
        f'    <Date>{cdata(started.strftime("%d-%b-%Y"))}</Date>{NL}'
        f'    <Time>{cdata(started.strftime("%H-%M-%S"))}</Time>{NL}'
        f'    <Comment>{cdata(comment)}</Comment>{NL}'
        f'    <ScriptVersion>{cdata(tool)}</ScriptVersion>{NL}'
        f'    <EnCaseVersion>{cdata(OUR_MARK)}</EnCaseVersion>{NL}'
        f'    <Excluded>{cdata(str(excluded))}</Excluded>\n'
        f'  </CaseReport>\n')
    write_utf16(path, text)


def write_victim_report(path: str) -> None:
    """C4All emits an empty victim index for the analyst to populate later;
    the container has to exist for the report set to load."""
    text = (f'<?xml version="1.0" encoding="utf-16"?>{NL}'
            f'  <VictimIndex>{NL}{NL}'
            f'  </VictimIndex>\n')
    write_utf16(path, text)


def write_results(path: str, case: str, started: _dt.datetime,
                  ended: _dt.datetime, total: int, tool: str) -> None:
    text = (
        "C4M Results:\n"
        "=====================================\n"
        f"Script Compiled: {_dt.date.today().strftime('%d-%b-%Y')}\n"
        f"Case Name:       {case}\n"
        f"Start Time:      {started.strftime('%d-%b-%Y %H-%M-%S')}\n"
        f"End Time:        {ended.strftime('%d-%b-%Y %H-%M-%S')}\n"
        "\n"
        "===============================\n"
        f"Total Extracted:          {total}\n"
        "\n"
        f"Exported by:     {tool}\n"
    )
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def export(manifest_path: str, out_dir: str, *, case: str, analyst: str = "",
           comment: str = "", evidence: str = "", mode: str = "hardlink",
           media_bytes: int = DEFAULT_MEDIA_BYTES,
           files_per_folder: int = DEFAULT_FILES_PER_FOLDER,
           all_as_movies: bool = False, tool: str = "",
           include_duplicates: bool = True,
           project_vic: bool = False, organization: str = "",
           vic_media_id: str = "sequential",
           vic_files_layout: str = "inplace") -> dict:
    """Turn a media_core manifest into a C4All report set. Returns a summary."""
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    records = list(manifest.get("files", []))
    if include_duplicates:
        # Every place a file was found is its own finding. The bytes are
        # stored once; these rows point at the same copy.
        records += manifest.get("duplicates", [])
    summary = manifest.get("summary", {})
    source_dir = os.path.dirname(os.path.abspath(manifest_path))
    if not records:
        raise ValueError("The manifest contains no recovered files to export.")

    tool = tool or f"media_core / c4all_export v{__version__}"
    started = _dt.datetime.now().astimezone()
    try:
        started = _dt.datetime.fromisoformat(summary["started_utc"]).astimezone()
    except (KeyError, ValueError, TypeError):
        pass

    os.makedirs(out_dir, exist_ok=True)
    # Asked before anything is written: once this export writes its own
    # Case_Report.xml, every folder would look like ours.
    ours = _is_our_export(out_dir)
    _drop_stale_indexes(out_dir)
    items = assign_batches(records, case, media_bytes, files_per_folder)
    placed_log: dict = {}

    placed, missing, placement = 0, [], set()
    # The items that failed, by identity. They were found again by cutting
    # each message at its first colon, which kept in the index any file
    # whose name has a colon -- and, matching by out_path, dropped a file
    # that was placed whenever a duplicate pointing at it failed.
    unplaced = set()
    for item in items:
        src = os.path.join(source_dir, item["record"]["out_path"])
        if not os.path.isfile(src):
            missing.append(item["record"]["out_path"])
            unplaced.add(id(item))
            continue
        dest = os.path.join(out_dir, item["rel_dir"], item["name"])
        try:
            how = place_file(src, dest, mode)
            placement.add(how)
            placed += 1
            placed_log[_batch_key(os.path.join(item["rel_dir"], item["name"]))] = {
                "how": how, "src": os.path.abspath(src)}
        except OSError as exc:
            missing.append(f"{item['record']['out_path']}: {exc}")
            unplaced.add(id(item))

    # Anything that could not be placed must not appear in the index.
    if unplaced:
        items = [i for i in items if id(i) not in unplaced]

    # A re-export with fewer files left the earlier export's DVD folders in
    # place: a disc burned from this folder, or a review tool pointed at
    # it, then carried files the new index no longer lists -- material a
    # narrower export was made to leave out.
    stale_removed, stale_kept = _drop_stale_batches(out_dir, set(placed_log), ours)
    _write_placed_log(out_dir, placed_log)

    movies = [i for i in items if i["record"].get("category") == VIDEO_CATEGORY]
    pictures = [i for i in items if i["record"].get("category") == IMAGE_CATEGORY]
    others = [i for i in items
              if i["record"].get("category") not in (VIDEO_CATEGORY, IMAGE_CATEGORY)]
    if all_as_movies:
        movies, pictures = items, []
    else:
        movies = movies + others   # custom-signature hits ride with the movies

    written = []
    if movies:
        path = os.path.join(out_dir, "C4M_Index.xml")
        write_index(path, movies, case, evidence, "Movie", "movie", True)
        written.append(path)
    if pictures:
        path = os.path.join(out_dir, "C4P_Index.xml")
        write_index(path, pictures, case, evidence, "Picture", "picture", False)
        written.append(path)

    case_path = os.path.join(out_dir, "Case_Report.xml")
    write_case_report(case_path, case, analyst, comment, started,
                      summary.get("duplicates_suppressed", 0), tool)
    victim_path = os.path.join(out_dir, "Victim_Report.xml")
    write_victim_report(victim_path)
    results_path = os.path.join(out_dir, "Results.txt")
    write_results(results_path, case, started, _dt.datetime.now().astimezone(),
                  len(items), tool)
    written += [case_path, victim_path, results_path]

    vic = None
    if project_vic:
        # The VICS file sits beside the batch folders and points into them, so
        # the report set and the JSON describe the same files.
        try:
            from fme.exports import projectvic_export
            vic_path = os.path.join(out_dir, "ProjectVIC.json")
            vic = projectvic_export.export(
                manifest_path, vic_path, case_number=case,
                organization=organization, contact_name=analyst,
                evidence=evidence, relative_root="", source_app="media_core",
                source_version=tool, media_id=vic_media_id,
                files_layout=vic_files_layout)
            if vic_files_layout != "hash":
                # Point at the batched copies rather than the working
                # directory, so the XML and the JSON describe the same files.
                _point_vic_at_batches(vic_path, items)
            written.append(vic_path)
        except (ImportError, OSError, ValueError) as exc:
            vic = {"error": str(exc)}

    discs = sorted({i["folder"].split("\\")[0] for i in items})
    return {
        "project_vic": vic,
        "output_dir": os.path.abspath(out_dir),
        "total": len(items),
        "movies": len(movies),
        "pictures": len(pictures),
        "placed": placed,
        "placement": sorted(placement),
        "missing": missing,
        "discs": discs,
        "files_written": [os.path.basename(p) for p in written],
        "case": case,
        "stale_removed": stale_removed,
        "stale_kept": stale_kept,
    }


# The index files an export writes at the top of its folder. A re-export
# writes only those it has records for, so one from an earlier export --
# a C4P_Index.xml beside the C4M_Index.xml of an --all-as-movies re-export --
# stayed behind and was loaded with the new one, listing every picture
# twice under two numbering schemes.
# What this exporter writes in Case_Report.xml where C4All writes the EnCase
# version; a folder whose report says this was exported by this program.
OUR_MARK = "N/A - not an EnCase export"
OWNED_INDEXES = ("C4M_Index.xml", "C4P_Index.xml", "ProjectVIC.json")


def _is_our_export(out_dir: str) -> bool:
    """Whether Case_Report.xml says this program exported into the folder.
    The folder may be one an examiner chose, and a file there that this
    program did not write is not ours to delete."""
    try:
        with open(os.path.join(out_dir, "Case_Report.xml"), "rb") as fh:
            return OUR_MARK in fh.read(65536).decode(ENCODING, "replace")
    except OSError:
        return False


def _drop_stale_indexes(out_dir: str) -> None:
    """Remove the index files an earlier export of this folder wrote."""
    if not _is_our_export(out_dir):
        return
    for name in OWNED_INDEXES:
        _drop(os.path.join(out_dir, name))


# What each export placed and how, so the next export into the folder knows
# which of the files it no longer lists it may remove. A copy or a link of
# a recovered file that is still in the case can go; a moved file is the
# case's only copy of it and never does.
PLACED_LOG = "C4All_placed.json"
_DISC_DIR = re.compile(r"^DVD\d{2}\(.*\)$")
_FOLDER_DIR = re.compile(r"^\d{2}$")
_BATCH_FILE = re.compile(r"^\d{8}\.[A-Za-z0-9]+$")


def _batch_key(rel: str) -> str:
    return rel.replace("\\", "/")


def _write_placed_log(out_dir: str, placed: dict) -> None:
    path = os.path.join(out_dir, PLACED_LOG)
    scratch = f"{path}.{uuid.uuid4().hex}"
    try:
        with open(scratch, "w", encoding="utf-8") as fh:
            json.dump({"placed": placed}, fh, indent=1, sort_keys=True)
        os.replace(scratch, path)
    except OSError as exc:
        _drop(scratch)
        LOG.warning("could not write %s: %s -- a later re-export will leave "
                    "this export's files in place", path, exc)


def _read_placed_log(out_dir: str) -> dict:
    try:
        with open(os.path.join(out_dir, PLACED_LOG), encoding="utf-8") as fh:
            placed = json.load(fh).get("placed", {})
        return placed if isinstance(placed, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _safe_to_remove(full: str, entry: Optional[dict]) -> bool:
    """A batch file may go only when removing it cannot remove evidence."""
    if entry:
        if entry.get("how") not in ("linked", "copied"):
            return False            # moved: the only copy
        src = entry.get("src") or ""
        try:
            return ((os.path.isfile(src) and not os.path.samefile(src, full))
                    or os.stat(full).st_nlink > 1)
        except OSError:
            return False
    # An export from before the log: a hard link with another name still
    # pointing at its bytes is safe to unlink; anything else may be a moved
    # file, and stays.
    try:
        return os.stat(full).st_nlink > 1
    except OSError:
        return False


def _drop_stale_batches(out_dir: str, keep: set, ours: bool):
    """Remove batch files an earlier export placed that this one does not
    list, and the folders they leave empty. Returns (removed, kept), where
    kept names the ones left because removing them could lose evidence."""
    if not ours:
        return 0, []
    log = _read_placed_log(out_dir)
    removed, kept = 0, []
    try:
        discs = [d for d in os.listdir(out_dir)
                 if _DISC_DIR.match(d) and os.path.isdir(os.path.join(out_dir, d))]
    except OSError:
        return 0, []
    for disc in discs:
        disc_path = os.path.join(out_dir, disc)
        for folder in os.listdir(disc_path):
            folder_path = os.path.join(disc_path, folder)
            if not _FOLDER_DIR.match(folder) or not os.path.isdir(folder_path):
                continue
            for name in os.listdir(folder_path):
                rel = _batch_key(os.path.join(disc, folder, name))
                if not _BATCH_FILE.match(name) or rel in keep:
                    continue
                full = os.path.join(folder_path, name)
                if _safe_to_remove(full, log.get(rel)):
                    try:
                        os.unlink(full)
                        removed += 1
                        continue
                    except OSError:
                        pass
                kept.append(rel)
            _rmdir_if_empty(folder_path)
        _rmdir_if_empty(disc_path)
    if kept:
        LOG.warning("%d file(s) from an earlier export of %s were left in "
                    "place: they may be the only copy (a moved file)",
                    len(kept), out_dir)
    return removed, sorted(kept)


def _rmdir_if_empty(path: str) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass


def _point_vic_at_batches(vic_path: str, items: list) -> None:
    """Rewrite RelativeFilePath in the VICS file to the batched copies."""
    import json as _json
    with open(vic_path, "r", encoding="utf-8") as fh:
        doc = _json.load(fh)
    by_out = {i["record"]["out_path"].replace("/", "\\"): i["folder"] + i["name"]
              for i in items}
    for entry in doc["value"][0]["Media"]:
        batched = by_out.get(entry["RelativeFilePath"])
        if batched:
            entry["RelativeFilePath"] = batched
    with open(vic_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(_json.dumps(doc, indent=2, ensure_ascii=True))


def parse_size(text) -> int:
    if isinstance(text, int):
        return text
    text = str(text).strip().lower()
    mult = 1
    for suffix, factor in (("t", 1 << 40), ("g", 1 << 30), ("m", 1 << 20), ("k", 1 << 10)):
        if text.endswith(suffix) or text.endswith(suffix + "b"):
            mult = factor
            text = text.rstrip("b").rstrip(suffix)
            break
    return int(float(text) * mult)


PRESETS = {"cd": 700_000_000, "dvd": 4_300_000_000,
           "dvd-dl": 8_500_000_000, "bd": 25_000_000_000, "none": 1 << 60}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write C4All-style XML reports from a media_core manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("-m", "--manifest", required=True,
                    help="manifest.json from a media_core run")
    ap.add_argument("-o", "--out", required=True, help="export directory")
    ap.add_argument("-c", "--case", required=True, help="case number, e.g. DC23-282")
    ap.add_argument("-a", "--analyst", default="", help="analyst name")
    ap.add_argument("--comment", default="", help="free-text comment for the case report")
    ap.add_argument("--evidence", default="",
                    help="evidence item name as it should appear in fullpath")
    ap.add_argument("--media", default="dvd",
                    help="disc size for batching: cd, dvd, dvd-dl, bd, none, or a "
                         "size like 8G")
    ap.add_argument("--files-per-folder", type=int, default=DEFAULT_FILES_PER_FOLDER)
    ap.add_argument("--copy", action="store_true", help="copy files instead of hard-linking")
    ap.add_argument("--move", action="store_true", help="move files instead of hard-linking")
    ap.add_argument("--no-duplicates", action="store_true",
                    help="list each file once instead of once per place it "
                         "was found")
    ap.add_argument("--vic", action="store_true",
                    help="also write ProjectVIC.json (VICS 2.0) alongside the "
                         "XML reports")
    ap.add_argument("--org", default="", help="contact organization for the VICS file")
    ap.add_argument("--vic-media-id", choices=("sequential", "zero"),
                    default="sequential",
                    help="MediaID numbering in the VICS file")
    ap.add_argument("--vic-files-layout", choices=("inplace", "hash"),
                    default="inplace",
                    help="inplace points the VICS file at the DVD batch copies; "
                         "hash stages a Cellebrite-style <name>Files folder")
    ap.add_argument("--all-as-movies", action="store_true",
                    help="put every file in C4M_Index.xml using the <Movie> layout, "
                         "instead of splitting images into C4P_Index.xml")
    args = ap.parse_args(argv)

    media = PRESETS.get(args.media.lower(), None)
    if media is None:
        try:
            media = parse_size(args.media)
        except ValueError:
            print(f"Bad --media value: {args.media}", file=sys.stderr)
            return 2

    try:
        result = export(
            args.manifest, args.out, case=args.case, analyst=args.analyst,
            comment=args.comment, evidence=args.evidence,
            mode="move" if args.move else "copy" if args.copy else "hardlink",
            media_bytes=media, files_per_folder=args.files_per_folder,
            all_as_movies=args.all_as_movies, project_vic=args.vic,
            include_duplicates=not args.no_duplicates,
            organization=args.org, vic_media_id=args.vic_media_id,
            vic_files_layout=args.vic_files_layout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 2

    print(f"Exported {result['total']} file(s) to {result['output_dir']}")
    print(f"  movies   : {result['movies']}")
    print(f"  pictures : {result['pictures']}")
    print(f"  discs    : {len(result['discs'])} ({', '.join(result['discs'][:6])}"
          f"{'...' if len(result['discs']) > 6 else ''})")
    print(f"  files    : {', '.join(result['files_written'])}")
    print(f"  placement: {', '.join(result['placement']) or 'none'}")
    if result.get("stale_removed"):
        print(f"  removed  : {result['stale_removed']} file(s) an earlier "
              f"export of this folder placed and this one does not list")
    for rel in result.get("stale_kept") or []:
        print(f"  ! left from an earlier export (moved there, the only copy; "
              f"not in the new index): {rel}")
    if result.get("project_vic"):
        vic = result["project_vic"]
        if vic.get("error"):
            print(f"  ! Project VIC export failed: {vic['error']}")
        else:
            print(f"  ProjectVIC.json: {vic['total']} media record(s), "
                  f"category null on all of them")
    for miss in result["missing"][:5]:
        print(f"  ! missing: {miss}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
