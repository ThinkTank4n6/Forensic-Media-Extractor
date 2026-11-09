"""
media_gui.py — standalone GUI for media_core.

A self-contained desktop app: run it and a window opens in your browser
pointed at a local server that only this machine can reach. Nothing is
uploaded anywhere; the image is read directly off your disk.

    python3 media_gui.py                    # opens the GUI
    python3 media_gui.py --port 5050        # different port
    python3 media_gui.py --no-browser       # don't auto-open a browser
    python3 media_gui.py -i case.E01        # pre-fill the image path

This is separate from app.py on purpose — app.py is the FAT32 tool with a
Media Extraction tab bolted on, whereas this is just the media extractor,
with a gallery view of what it recovered. Both can run at once on different
ports; they share media_core and neither needs the other.

Same trust model as app.py: a local single-user tool with no auth, whose
"open image" box takes an arbitrary local path by design. Don't expose the
port to an untrusted network.
"""

# Allow this file to be run directly -- "python fme/engines/media_core.py" --
# as well as imported. Run as a script there is no package context, so the
# project root goes on the path before the package imports below.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import inspect as _inspect
import csv
import io
import time
import json
import hashlib
import logging
import mimetypes
import re
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import traceback
import uuid
import webbrowser
from dataclasses import asdict
from typing import Optional

from flask import Flask, Response, jsonify, request, send_file

from fme.exports import c4all_export
from fme.review import casedb
from fme.web import compression
from fme.web import request_guard
from fme.review import hashsets
from fme.review import regions
from fme.review import hashstore
from fme.review import remotestore
from fme.review import settings
from fme.review import registry
from fme.review import savedset
from fme.web import manual
from fme.exports import lava_export
from fme.exports import export_files
from fme.exports import report
from fme.exports import report_html
from fme.engines import content_search
from fme.engines import face_match
from fme.engines import models
from fme.engines import timeutil
from fme.engines import credentials
from fme.engines import edit_match
from fme.engines import frame_match
from fme.engines import imaging
from fme.engines import screening
from fme.engines import similarity
from fme.engines import video_frames
from fme.exports import lava_launch
from fme.engines import media_core
from fme.engines import mount_detect
from fme.exports import vic_import

try:
    from fme.engines import fat32_core
except ImportError:  # pragma: no cover - media_gui works without it
    fat32_core = None

# media_gui and media_core must come from the same release. Rather than
# letting a stale module surface as a cryptic "Unknown option(s): cancel_cb"
# halfway through a job, detect it up front and degrade cleanly.
CORE_VERSION = getattr(media_core, "__version__", "unknown")
CORE_FEATURES = getattr(media_core, "FEATURES", frozenset())
_RUN_PARAMS = _inspect.signature(media_core.run_extraction).parameters
SUPPORTS_CANCEL = "cancel_cb" in _RUN_PARAMS
SUPPORTS_INSPECT = hasattr(media_core, "inspect_image")
SUPPORTS_CUSTOM_SIGS = "extra_signatures" in _RUN_PARAMS

CORE_WARNING = ""
if not (SUPPORTS_CANCEL and SUPPORTS_INSPECT and SUPPORTS_CUSTOM_SIGS):
    missing = [n for n, ok in (("cancel", SUPPORTS_CANCEL),
                               ("image inspection", SUPPORTS_INSPECT),
                               ("custom signatures", SUPPORTS_CUSTOM_SIGS)) if not ok]
    CORE_WARNING = (
        f"media_core.py reports version {CORE_VERSION} and does not support "
        f"{', '.join(missing)}. Those controls are disabled. Replace "
        f"media_core.py with the copy from the same release as this file.")

__version__ = "2.1.0"

import sys


def _app_dir() -> str:
    """Where results go by default.

    Frozen, that is the folder holding the executable. Running from source it
    is the current working directory, not the package: `fme/web/` is library
    code and may be inside site-packages, which is no place for evidence.
    """
    # FME_DATA_DIR puts the program's own state -- the case list, the hash
    # store, media_out/ -- somewhere else: a folder on an evidence drive, a
    # read-only install, or a test that must not write beside the code.
    chosen = os.environ.get("FME_DATA_DIR", "").strip()
    if chosen:
        os.makedirs(chosen, exist_ok=True)
        return os.path.abspath(chosen)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def _bundle_dir() -> str:
    """Where this module's own data files live (templates and the like)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _app_dir()
# Where results are written. Defaults to media_out/ beside this script, but
# a 128 GB image can easily produce more than the system drive has spare, so
# it is settable from the command line (--output) and from the page.
OUT_ROOT = os.path.join(BASE_DIR, "media_out")
# Created on first use, not at import: passing --output should not leave an
# empty media_out/ sitting beside the script.


def checked_output_root(path: str) -> str:
    """The absolute folder `path` names, created and writable, or an error.
    Changes nothing about where other jobs go."""
    resolved = os.path.abspath(os.path.expanduser(path))
    os.makedirs(resolved, exist_ok=True)
    if not os.access(resolved, os.W_OK):
        raise PermissionError(f"Cannot write to {resolved}")
    return resolved


def set_output_root(path: str) -> str:
    """Point new jobs at a different folder. Returns the absolute path."""
    return _remember_output_root(checked_output_root(path))


def _remember_output_root(resolved: str) -> str:
    """Make an already-checked folder the one later jobs use."""
    global OUT_ROOT
    OUT_ROOT = resolved
    return OUT_ROOT


def output_space(root: str, image_size: int = 0) -> dict:
    """Free space where results will land, and whether it looks sufficient.

    Recovered files can approach the size of the source, so a run started on
    a drive with a few gigabytes free will fail hours in. Better to say so
    before it starts.
    """
    try:
        probe = root
        while probe and not os.path.isdir(probe):   # not created yet
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        usage = shutil.disk_usage(probe or root)
    except OSError as exc:
        return {"path": root, "error": str(exc)}
    info = {"path": root, "free": usage.free,
            "free_human": media_core.human(usage.free),
            "total_human": media_core.human(usage.total)}
    if image_size:
        info["image_size"] = image_size
        info["tight"] = usage.free < image_size * 0.25
    return info

LOG = logging.getLogger("fme.gui")

app = Flask(__name__)
# Only this program's own page may drive it: see request_guard.
request_guard.install(app)
# A case's file list goes to the page gzipped: 51 MB was 4 MB (compression).
compression.install(app)


class _QuietRequests(logging.Filter):
    """Keep the console readable.

    The page polls job status about twice a second and pulls a thumbnail for
    every recovered file, and Werkzeug logs a line for each. After a run
    finishes, that scroll of GET lines looks exactly like the tool is still
    working when it is not. Successful requests to those two endpoints are
    dropped; everything else -- errors, and every request that changes state
    -- still prints.
    """

    NOISY = ("/api/status", "/api/active", "/api/file", "/api/capabilities",
             "/api/browse", "/api/sources")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if ' 200 -' not in message and ' 304 -' not in message:
            return True
        return not any(path in message for path in self.NOISY)


logging.getLogger("werkzeug").addFilter(_QuietRequests())

JOBS = {}
JOBS_LOCK = threading.Lock()
# Case folders something is writing to outside a job: a reprocess runs in
# its request, and two at once appended to one manifest.csv.
WRITING: set = set()


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a or "")) == \
        os.path.normcase(os.path.abspath(b or ""))


def folder_busy(out_dir: str) -> str:
    """Why this case folder must not be written to now, or "".

    A run in progress has no manifest.json yet, so the case list offered to
    "rebuild" it, and rebuilding wrote a partial manifest into a folder the
    run was still writing; a reprocess could start beside another. Asked
    under JOBS_LOCK so the answer and the claim that follows are one step.
    """
    for job in JOBS.values():
        if job.get("status") == "running" and _same_dir(job.get("out_dir"), out_dir):
            return "an extraction is still writing to this case folder"
    if any(_same_dir(d, out_dir) for d in WRITING):
        return "this case is already being reprocessed"
    return ""

# The image the examiner has loaded and accepted. Extraction runs against
# this and nothing else, so a half-typed path in the box can never become the
# thing that gets carved.
ACTIVE = {"path": None, "info": None}
ACTIVE_LOCK = threading.Lock()

# The id of the most recently started job. A browser reload throws away the
# page's copy, but the extraction itself runs in a thread here and carries on
# regardless -- so the page can ask what it was working on and rejoin it.
LAST_JOB = {"id": None}

# Hash sets loaded for this run of the program. Held in memory because a
# national set is hundreds of megabytes and reloading it per job would make
# the feature unusable.
HASH_SETS: list = []
HASH_LOCK = threading.Lock()

# The store kept between cases. Default beside the program so it is found
# without configuration; point it at a share to use one across an office.
# The list of cases processed here. Beside the program by default, so it is
# found without configuration; put it on a share to see an office's cases.
REGISTRY_PATH = os.path.join(BASE_DIR, "cases.db")
REGISTRY: dict = {"db": None}
REGISTRY_LOCK = threading.Lock()


def _registry():
    with REGISTRY_LOCK:
        if REGISTRY["db"] is None:
            REGISTRY["db"] = registry.CaseRegistry(REGISTRY_PATH)
        return REGISTRY["db"]


STORE_PATH = (settings.load().get("store_path")
              or os.path.join(BASE_DIR, "hash_store.db"))
STORE: dict = {"db": None}
STORE_LOCK = threading.Lock()


# What a shared store needs beyond its address: who is asking, and which
# certificate to trust. Held for the session only -- a token is the holder's
# identity on a store full of this material, and an examination machine is
# the wrong place to write one down.
STORE_TOKEN = {"token": "", "cert": ""}

# What this machine remembered last time. Retyping an address, a
# certificate path and a token every morning is not security: it is how a
# token ends up in a text file on somebody's desktop.
_REMEMBERED = settings.load()
if _REMEMBERED.get("store_token"):
    STORE_TOKEN["token"] = _REMEMBERED["store_token"]
if _REMEMBERED.get("store_cert"):
    STORE_TOKEN["cert"] = _REMEMBERED["store_cert"]

# The results folder, which was saved and never read back.
#
# Settings took it, wrote it to the settings file, and the next launch
# ignored it: the store path, the token and the certificate were
# restored here and this was not. An examiner set a central folder,
# saw it in Settings, and every run still went to the default beside
# the program.
#
# Restored defensively, because the folder may be on a drive that is
# not attached today: if it cannot be used, the program says so once
# and keeps the default rather than refusing to start.
if _REMEMBERED.get("output_root"):
    try:
        set_output_root(_REMEMBERED["output_root"])
    except (OSError, PermissionError) as _exc:
        LOG.warning("The saved results folder %s cannot be used (%s). "
                    "Results will go to %s until it is changed in "
                    "Settings.", _REMEMBERED["output_root"], _exc, OUT_ROOT)


def _store(path: str = "", create: bool = True):
    """The hash store in use: a file here, or a server on another machine.

    A URL gives a client to the shared store rather than a local database.
    The rest of the program calls the same methods either way, so nothing
    else has to know which it is holding.
    """
    global STORE_PATH
    where = path or STORE_PATH
    if remotestore.looks_remote(where):
        with STORE_LOCK:
            if path:
                STORE_PATH = path.rstrip("/")
            # Built per call rather than cached: a changed token or address
            # should take effect at once, and there is no file to hold open.
            return remotestore.RemoteHashStore(
                STORE_PATH, STORE_TOKEN["token"],
                ca_cert=STORE_TOKEN.get("cert", ""))
    with STORE_LOCK:
        if path and os.path.abspath(path) != STORE_PATH:
            if STORE["db"] is not None:
                STORE["db"].close()
                STORE["db"] = None
            STORE_PATH = os.path.abspath(path)
        if STORE["db"] is None:
            # Opening is not creating. A store is made by importing into it,
            # which is deliberate; opening a path that holds nothing should
            # say so rather than quietly producing an empty one.
            STORE["db"] = hashstore.HashStore(STORE_PATH, create=create)
        return STORE["db"]


def _store_warning(path: str) -> str:
    """What to say about where somebody has just pointed the store."""
    if remotestore.looks_remote(path):
        return ""
    if hashstore.is_network_path(path):
        return ("That path is on a network share. A hash store is a SQLite "
                "database and needs file locking that shares implement badly "
                "or not at all: it will be slow, it can fail with a disk I/O "
                "error, and two machines writing to it can corrupt it "
                "quietly. Keep the store on a disk in the machine that holds "
                "it and reach it from here with its address instead — that "
                "is what the hash store server is for.")
    return ""

# One open case database per job output folder. Review decisions live beside
# the recovered files, keyed by hash, so they survive the program closing.
CASES: dict = {}
CASE_LOCK = threading.Lock()


_DIGEST_INDEX: dict = {}


def _foreign_digests(job_id: str, digests) -> list:
    """The digests among these that are not files of this job's case.

    A grading is written into the case of the job it names. When the page
    showed one case's files under another's job -- a slow open overtaken by
    a newer one -- a grading of one of them went into the wrong case.db and
    was answered "ok" (v2026.11.319). Refused instead.
    """
    wanted = {str(d).lower() for d in digests if d}
    if not wanted:
        return []
    with JOBS_LOCK:
        rows = (JOBS.get(job_id) or {}).get("files") or []
        # Kept beside the job, not in it: the job is sent to the page whole.
        held = _DIGEST_INDEX.get(job_id)
        if held is None or held[0] is not rows or held[1] != len(rows):
            held = (rows, len(rows),
                    {str(f.get("sha256") or "").lower() for f in rows})
            _DIGEST_INDEX[job_id] = held
    return sorted(wanted - held[2])


def _refuse_foreign(job_id: str, digests):
    """An error response if any digest is not this case's, else None."""
    stray = _foreign_digests(job_id, digests)
    if not stray:
        return None
    return error_response(ValueError(
        f"{len(stray)} of these file(s) are not in this case (the page may be "
        f"showing another case). Nothing was changed; reopen the case."),
        status=409)


def _sync_stash(case, job_id: str, digests) -> None:
    """Make the examiner's stash say what the case now says about these files.

    A person's grade in the case's own scheme goes in; anything else -- an
    undone grade, an uncategorized file, a hash set's grade, an examiner's
    own category (10 and up, which means something only in its case) --
    comes out. The stash only ever added: an undone CAM grade stayed, and
    was applied to the next case holding the file (v2026.11.319).
    """
    if settings.load().get("stash_on", "1") == "0" or not digests:
        return
    try:
        from fme.review import stash as _stash
        with JOBS_LOCK:
            rows = (JOBS.get(job_id) or {}).get("files") or []
            by_digest = {f.get("sha256"): f for f in rows}
        names = {c["code"]: c["name"] for c in case.categories()}
        keep, drop = [], []
        for digest in digests:
            decided = case.decision(digest)
            file = by_digest.get(digest) or {}
            ids = [digest, file.get("md5", ""), file.get("sha1", "")]
            code = decided.get("category")
            if (code is None or code == case.uncategorized or decided.get("auto")
                    or code not in case.scheme.codes):
                drop += [d for d in ids if d]
            else:
                keep.append({"sha256": digest, "md5": ids[1], "sha1": ids[2],
                             "category": code, "label": names.get(code, "")})
        own = _stash.Stash(settings.load().get("stash_path", ""))
        try:
            if drop:
                own.forget(drop)
            if keep:
                own.add(keep, note=getattr(case, "case_number", "") or "",
                        region=case.region)
        finally:
            own.close()
    except Exception as exc:  # noqa: BLE001
        # A grading is never lost over the stash -- but said where it is seen.
        LOG.warning("could not update the stash: %s", exc)
        traceback.print_exc()


def _case_for(job_id: str):
    """The case database for a job, opened on first use."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        out_dir = job["out_dir"] if job else None
        examiner = (job or {}).get("examiner", "")
        case_number = (job or {}).get("case_number", "")
    if not out_dir:
        return None
    with CASE_LOCK:
        held = CASES.get(out_dir)
        if held is not None and not _still_its_file(held):
            # The case.db this handle has open is no longer the one in the
            # folder -- deleted, or replaced by a restore from backup. Kept,
            # every grading went into the old file and was answered "ok"
            # (v2026.11.319).
            CASES.pop(out_dir, None)
            try:
                held.close(force=True)
            except Exception:  # noqa: BLE001 - it is being let go of
                pass
        if not os.path.isdir(out_dir):
            return None         # the case folder is gone; nothing to open
        if out_dir not in CASES:
            # A new case takes the region chosen in Settings; one that
            # exists keeps the region it was made under.
            CASES[out_dir] = casedb.open_case(
                out_dir, examiner, case_number,
                region=settings.load().get("category_region") or "US")
            # Every request borrows this one handle; none of them may close it.
            CASES[out_dir].shared = True
            try:
                # An examiner's own categories and flags should be there from
                # the first file, not re-created by hand in every case.
                savedset.apply(out_dir,
                               os.path.join(BASE_DIR, savedset.DEFAULT_NAME))
            except (OSError, ValueError, sqlite3.Error):
                pass    # a scheme is a convenience; never fail a case over it
            CASES[out_dir]._identity = _file_identity(CASES[out_dir].path)
        case = CASES[out_dir]
    # Who is grading: the examiner this machine says, not whoever ran the
    # extraction -- a reviewer's grades were credited to the extractor, and
    # a case not in the registry recorded no name at all (v2026.11.319).
    case.examiner = (settings.load().get("examiner") or "").strip() or examiner \
        or case.examiner
    return case


def _file_identity(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _still_its_file(case) -> bool:
    """Is the case.db this handle opened still the file at its path?"""
    now = _file_identity(case.path)
    return now is not None and now == getattr(case, "_identity", now)


def _release_case(out_dir: str) -> bool:
    """Close a case's handle, unless a run is still writing to it."""
    with JOBS_LOCK:
        busy = any(j.get("out_dir") == out_dir and j.get("status") == "running"
                   for j in JOBS.values())
    if busy:
        return False
    with CASE_LOCK:
        held = CASES.pop(out_dir, None)
    if held is not None:
        try:
            held.close(force=True)
        except Exception:  # noqa: BLE001
            pass
    return True

# Custom carve signatures for this run of the program, mirroring the FAT32
# tool's per-session list. Held server-side so every job picks them up
# without the browser having to resend them.
CUSTOM_SIGNATURES = []
SIG_LOCK = threading.Lock()

IMAGE_EXTENSIONS = {".img", ".dd", ".raw", ".e01", ".e02", ".ex01", ".l01",
                    ".001", ".bin", ".vmdk", ".vhd", ".vhdx", ".qcow", ".qcow2"}

DEFAULT_IMAGE_PATH = ""


def error_response(exc, status=400):
    return jsonify({"ok": False, "error": str(exc)}), status


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def _vendor_fs_status() -> dict:
    """What the extra reader adds, and under what terms."""
    try:
        from fme.engines import vendor_fs
        return vendor_fs.status()
    except Exception:  # noqa: BLE001 - its absence is not a failure
        return {"available": False, "kinds": []}


@app.route("/api/capabilities")
def api_capabilities():
    return jsonify({
        "ok": True,
        "pytsk3": media_core.tsk_enabled(),
        "pyewf": media_core.pyewf is not None,
        "vendor_fs": _vendor_fs_status(),
        "filesystems": (["FAT12/16/32", "exFAT", "NTFS", "HFS+"] + (["APFS (TSK)"] if "apfs" in media_core.tsk_kinds() else ["APFS (vendored reader)"])
                        if media_core.tsk_enabled()
                        else ["FAT32 (via fat32_core)"]),
        "containers": ["raw/dd/img", "split raw (.001…)"]
                      + ([".E01/.Ex01"] if media_core.pyewf is not None else []),
        "engine": media_core._engine_description(),
        "default_path": DEFAULT_IMAGE_PATH,
        "release": media_core.RELEASE,
        "remembered": {
            "store_path": _REMEMBERED.get("store_path", ""),
            "store_cert": _REMEMBERED.get("store_cert", ""),
            "token_saved": bool(_REMEMBERED.get("store_token")),
            "where": settings.path(),
        },
        "gui_version": __version__,
        "gui_path": os.path.abspath(__file__),
        "core_path": getattr(media_core, "__file__", "?"),
        "core_version": CORE_VERSION,
        "core_warning": CORE_WARNING,
        "supports": {"cancel": SUPPORTS_CANCEL, "inspect": SUPPORTS_INSPECT,
                     "custom_signatures": SUPPORTS_CUSTOM_SIGS},
        "has_fat32_core": fat32_core is not None,
        "mixed_install": media_core.mixed_install(),
        "similarity": similarity.status(),
        "video_frames": video_frames.status(),
        "imaging": imaging.status(),
        "decode_notes": imaging.noticed(),
        "lava_installed": bool(lava_launch.find_lava()),
        "lava_website": lava_launch.LAVA_WEBSITE,
        "cpu_count": os.cpu_count() or 1,
        "default_workers": (media_core.default_workers()
                            if hasattr(media_core, "default_workers") else 1),
        "supports_workers": "workers" in getattr(media_core, "DEFAULT_OPTIONS", {}),
    })


@app.route("/api/inspect")
def api_inspect():
    """Open the image and report what it is, before any long job starts."""
    if not SUPPORTS_INSPECT:
        return error_response(RuntimeError(CORE_WARNING))
    path = (request.args.get("path") or "").strip()
    if not path:
        return error_response(ValueError("No image path provided"))
    try:
        return jsonify({"ok": True, "info": media_core.inspect_image(path)})
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)


@app.route("/api/vic/import", methods=["POST"])
def api_vic_import():
    """Import a Project VIC export's media, or match a hash set against a
    finished job."""
    data = request.get_json(force=True) or {}
    vic_path = (data.get("path") or "").strip()
    if not vic_path or not os.path.isfile(vic_path):
        return error_response(FileNotFoundError(f"No such file: {vic_path}"))

    job_id = data.get("job", "")
    if job_id:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job is None:
            return error_response(KeyError("Unknown job_id"), status=404)
        manifest = os.path.join(job["out_dir"], "manifest.json")
        if not os.path.isfile(manifest):
            return error_response(FileNotFoundError("That job has no manifest"))
        csv_path = os.path.join(job["out_dir"], "project_vic_matches.csv")
        try:
            result = vic_import.match_manifest(vic_path, manifest, csv_path)
        except (OSError, ValueError) as exc:
            return error_response(exc)
        result.pop("rows", None)
        return jsonify({"ok": True, "mode": "match", "result": result})

    job_id = uuid.uuid4().hex
    # Read once: OUT_ROOT is rebound by other requests.
    job_root = OUT_ROOT
    os.makedirs(job_root, exist_ok=True)
    out_dir = os.path.join(job_root, job_id)
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "done": 0, "total": 0,
                        "recovered": 0, "phase": "projectvic", "files": [],
                        "summary": None, "error": None, "path": vic_path,
                        "out_dir": out_dir, "_cancel": threading.Event(),
                        # import_media takes no cancel callback: /api/cancel
                        # must say so rather than report a cancel that the
                        # import, still writing, never sees.
                        "_uncancelable": True}

    def run():
        try:
            summary = vic_import.import_media(
                vic_path, out_dir, files_root=(data.get("files_root") or "").strip(),
                verify=not data.get("no_verify"))
            with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
                files = json.load(fh)["files"]
            with JOBS_LOCK:
                JOBS[job_id].update(
                    status="done", phase="finished", summary=summary,
                    recovered=len(files),
                    files=[_job_file_entry(f) for f in files])
        except Exception as exc:  # noqa: BLE001
            with JOBS_LOCK:
                JOBS[job_id].update(status="error", error=str(exc))
            traceback.print_exc()

    with JOBS_LOCK:
        LAST_JOB["id"] = job_id   # so a page reload can rejoin this run
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "mode": "import", "job_id": job_id})


def _print_finished(summary: dict, files: list) -> None:
    """Say plainly, on the console, that the work is over.

    Anything printed after this is the browser fetching thumbnails, not the
    extraction continuing.
    """
    canceled = summary.get("canceled") or summary.get("cancelled")
    line = "=" * 66
    print(f"\n{line}")
    print("  CANCELED" if canceled else "  FINISHED")
    print(line)
    print(f"  Source        : {summary.get('image', '')}")
    print(f"  Files recovered: {len(files)}")
    for key, label in (("fs_files", "from the file system"),
                       ("carved_files", "carved"),
                       ("db_files", "from database blobs"),
                       ("embedded_files", "embedded in other files"),
                       ("folder_files", "from the folder scan"),
                       ("package_files", "from the package")):
        if summary.get(key):
            print(f"     {summary[key]:>8}  {label}")
    print(f"  Duplicates    : {summary.get('duplicates_suppressed', 0)} collapsed")
    if summary.get("output_failure"):
        print(f"  STOPPED       : the output failed ({summary['output_failure']})"
              " -- this case is INCOMPLETE")
    if summary.get("write_failed") or summary.get("read_failed"):
        print(f"  NOT RECOVERED : {summary.get('write_failed', 0)} write failure(s),"
              f" {summary.get('read_failed', 0)} evidence read failure(s)"
              " -- listed under 'failures' in manifest.json")
    print(f"  Elapsed       : {summary.get('elapsed_seconds', 0)}s")
    print(f"  Output        : {summary.get('output_dir', '')}")
    if summary.get("project_vic"):
        print(f"  Project VIC   : {summary['project_vic']}")
    if summary.get("lava"):
        print(f"  LAVA          : {summary['lava']}{os.sep}_lava_data.lava")
    print(f"{line}")
    print("  Nothing further is running. Log lines after this point are the")
    print("  browser loading previews.\n")


class _ManifestTail:
    """The rows appended to a manifest.csv since the last look.

    The run appends to the manifest as it goes, so each look reads from
    where the last one stopped and returns only what is new. A row can be
    caught half-written -- the last line without its newline, or a quoted
    field whose line break has not arrived -- so a look takes only complete
    lines, and if what it took does not parse into whole rows it takes
    nothing and tries again at the next look, once the writer has finished.
    """

    def __init__(self, path: str):
        self.path = path
        self.pos = 0
        self.header = None
        # Set when the file turned out shorter than what was already read
        # -- rewritten, not appended to -- so the caller starts its list
        # again from what this look returns.
        self.restarted = False
        self.identity = None
        self.skipped = 0            # damaged rows dropped, for whoever asks

    def read_new(self) -> list:
        self.restarted = False
        try:
            with open(self.path, "rb") as fh:
                # Replaced, not appended to: OutputManager rewrites the
                # manifest whole and renames it into place. Shorter was the
                # only sign looked for, and a rewrite is usually as long or
                # longer -- so the tail went on reading the new file from
                # the old one's byte offset, part way through some row.
                st = os.fstat(fh.fileno())
                identity = (st.st_dev, st.st_ino)
                replaced = self.identity is not None and identity != self.identity
                self.identity = identity
                fh.seek(0, os.SEEK_END)
                if replaced or fh.tell() < self.pos:
                    self.pos, self.header, self.restarted = 0, None, True
                fh.seek(self.pos)
                chunk = fh.read()
        except OSError:
            return []
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return []
        text = chunk[:cut + 1].decode("utf-8", "replace")
        try:
            rows = [r for r in csv.reader(io.StringIO(text, newline="")) if r]
        except csv.Error:
            return []
        header = self.header
        if header is None:
            if not rows:
                return []
            header, rows = rows[0], rows[1:]
        if rows and len(rows[-1]) != len(header):
            return []               # a row still being written; next look
        # A wrong-length row with whole rows after it is not being written:
        # it is damaged, and waiting for it -- as this once did for any bad
        # row -- would hold the live gallery and Details at that row for the
        # rest of the run. It is dropped, and said so.
        bad = sum(1 for row in rows if len(row) != len(header))
        if bad:
            self.skipped += bad
            rows = [row for row in rows if len(row) == len(header)]
            LOG.warning("%s: %d row(s) with the wrong number of fields were "
                        "skipped; manifest.json, written at the end, is not "
                        "affected", self.path, bad)
        self.header = header
        self.pos += cut + 1
        return [dict(zip(header, row)) for row in rows]


def _live_row(row: dict) -> Optional[dict]:
    """A manifest.csv row as the same record manifest.json would hold.

    CSV holds text. This used to convert a few fields by hand -- size,
    deleted, instances, the coordinates -- and pass the rest on as text, so
    `container` arrived as "False", which is true: every file in the live
    gallery counted as a container and was hidden unless Show containers
    was on, and `animated` was true for all of them. Read through the same
    function a resume uses, every field gets the type the Record declares,
    and the live view cannot disagree with the finished one. None for a row
    that does not read as a record.
    """
    rec = media_core._record_from_row(row)
    return asdict(rec) if rec is not None else None


_RECORDS_CACHE: dict = {}
_RECORDS_LOCK = threading.Lock()


class _CaseRecords:
    """One case's records: read once, then only what has been appended.

    Keyed on (mtime, size), a cache of the whole parse missed on every
    click during a run -- the file grows between any two -- so each click
    parsed the whole manifest again: 10.9 s at 200,000 rows, with the GIL
    held, stalling the status poll and the extraction thread with it.
    """

    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        # _ManifestTail keeps a byte offset; two clicks reading one tail at
        # once would each advance it past rows the other never parsed.
        self.lock = threading.Lock()
        self.json_key = None
        self.files, self.dups = [], []
        self.tails = (_ManifestTail(os.path.join(out_dir, "manifest.csv")),
                      _ManifestTail(os.path.join(out_dir, "duplicates.csv")))

    def refresh(self) -> tuple:
        json_path = os.path.join(self.out_dir, "manifest.json")
        try:
            st = os.stat(json_path)
        except FileNotFoundError:
            st = None
        if st is not None:
            key = (st.st_mtime_ns, st.st_size)
            if key != self.json_key:
                with open(json_path, encoding="utf-8") as fh:
                    manifest = json.load(fh)
                self.files = manifest.get("files", [])
                self.dups = manifest.get("duplicates", [])
                self.json_key = key
            return self.files, self.dups
        if not os.path.isfile(self.tails[0].path):
            raise FileNotFoundError(
                f"Nothing has been recorded in {self.out_dir} yet: neither "
                f"manifest.json nor manifest.csv is there.")
        held = [self.files, self.dups]
        for i, tail in enumerate(self.tails):
            fresh = [rec for rec in map(_live_row, tail.read_new())
                     if rec is not None]
            # Rebound, never appended to: another request may still be
            # walking the list it was handed.
            if tail.restarted:
                held[i] = fresh
            elif fresh:
                held[i] = held[i] + fresh
        self.files, self.dups = held
        return self.files, self.dups


def _feed_case_records(out_dir: str, tail: "_ManifestTail", before: int,
                       records: list) -> None:
    """Hand rows the run has just parsed to the case's Details cache.

    Only when the cache stands exactly where the run's look began: if a
    Details click has read on its own since, the two are not at the same
    place, and adding these would list some rows twice. Skipping costs only
    the parse the cache will then do itself.
    """
    with _RECORDS_LOCK:
        entry = _RECORDS_CACHE.get(out_dir)
        if entry is None:
            entry = _RECORDS_CACHE[out_dir] = _CaseRecords(out_dir)
            while len(_RECORDS_CACHE) > 8:
                _RECORDS_CACHE.pop(next(iter(_RECORDS_CACHE)))
    with entry.lock:
        mine = entry.tails[0]
        if tail.restarted:
            entry.files = list(records)
        elif mine.pos == before and mine.header in (None, tail.header):
            entry.files = entry.files + records
        else:
            return
        mine.pos, mine.header, mine.identity = tail.pos, tail.header, tail.identity


def case_records(out_dir: str) -> tuple:
    """(files, duplicates) for a case folder, as manifest.json holds them.

    From manifest.json once a run has written it, and from manifest.csv and
    duplicates.csv while the run is still going -- manifest.json is written
    only at the end, so the details pane, which read it on every click,
    answered "[Errno 2] No such file or directory" for any file clicked
    during a run. Each case keeps its parse and reads only what was
    appended since (_CaseRecords).
    """
    with _RECORDS_LOCK:
        entry = _RECORDS_CACHE.pop(out_dir, None) or _CaseRecords(out_dir)
        _RECORDS_CACHE[out_dir] = entry         # reinserted: least recent first
        while len(_RECORDS_CACHE) > 8:
            _RECORDS_CACHE.pop(next(iter(_RECORDS_CACHE)))
    with entry.lock:
        return entry.refresh()


def _job_file_entry(f: dict) -> dict:
    return {
        "name": os.path.basename(f["out_path"]), "rel_path": f["out_path"],
        "ext": f["ext"], "category": f["category"], "engine": f["engine"],
        "size": f["size"], "size_human": media_core.human(f["size"]),
        "deleted": f["deleted"], "sha256": f["sha256"], "md5": f["md5"],
        "source": f["source_path"] or (f"offset {f['offset']}"
                                       if f["offset"] is not None else ""),
        "fs_type": f["fs_type"],
        # Whole, zone included. Cut to 19 characters, an NTFS time in UTC
        # could not be told from a FAT time on the device's clock, and the
        # year filters counted it in UTC: see galleryquery.year.
        "mtime": timeutil.stored(f, "mtime"),
        "previewable": f["ext"] in PREVIEWABLE,
        # Not drawable as-is, but renderable on request.
        "convertible": f["ext"] in CONVERTIBLE,
        # From the file's own metadata, so the gallery can filter on it
        "captured": timeutil.stored(f, "captured"),
        "latitude": f.get("latitude"), "longitude": f.get("longitude"),
        # Carried for the search box, which matches on all of these.
        "source_path": f.get("source_path", ""),
        "md5": f.get("md5", ""), "sha1": f.get("sha1", ""),
        "camera_make": f.get("camera_make", ""),
        "camera_model": f.get("camera_model", ""),
        "artist": f.get("artist", ""),
        "body_serial": f.get("body_serial", ""),
        "city": f.get("city", ""), "state": f.get("state", ""),
        "country": f.get("country", ""), "animated": bool(f.get("animated")),
        "atime": timeutil.stored(f, "atime"),
        "crtime": timeutil.stored(f, "crtime") or timeutil.stored(f, "ctime"),
        "body_serial_present": bool(f.get("body_serial")),
        # Other media came out of this file: it is in the manifest and the
        # reports, but it is not a picture, so the gallery leaves it out.
        "container": bool(f.get("container")),
        # What this tile stands for: identical copies found elsewhere, and
        # how many files look like it.
        "copies": int(f.get("instances") or 1),
        "similar_group": f.get("similar_group"),
        "similar_count": int(f.get("similar_count") or 0),
    }


@app.route("/api/use", methods=["POST"])
def api_use():
    """Accept an inspected image as the one to work on — the equivalent of
    opening a volume in the FAT32 tool. Re-inspects rather than trusting the
    client, so what gets accepted is what the server could actually read."""
    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return error_response(ValueError("No image path provided"))
    if not SUPPORTS_INSPECT:
        if not os.path.exists(path):
            return error_response(FileNotFoundError(f"No such file: {path}"))
        with ACTIVE_LOCK:
            ACTIVE.update(path=os.path.abspath(path), info=None)
        return jsonify({"ok": True, "accepted": True, "info": None,
                        "path": os.path.abspath(path)})

    info = media_core.inspect_image(path)
    if not info["ok"]:
        reason = info["warnings"][0] if info["warnings"] else "image could not be opened"
        return error_response(ValueError(f"Not accepted: {reason}"))
    with ACTIVE_LOCK:
        ACTIVE.update(path=info["path"], info=info)
    return jsonify({"ok": True, "accepted": True, "info": info, "path": info["path"]})


@app.route("/api/job/current")
def api_job_current():
    """The job this server is working on, or last worked on.

    Refreshing the page does not stop an extraction -- it runs in a thread
    here, not in the browser -- but the page used to lose the job id and look
    as though the run had been abandoned. This is how it finds its way back.
    """
    with JOBS_LOCK:
        job_id = LAST_JOB["id"]
        job = JOBS.get(job_id) if job_id else None
        if job is None:
            return jsonify({"ok": True, "job_id": None, "status": None})
        payload = {k: v for k, v in job.items() if not k.startswith("_")}
    held = payload.get("files") or []
    payload["files_known"] = len(held)
    if _paged(len(held)):
        payload["files"] = []
        payload["paged"] = True
    return jsonify({"ok": True, "job_id": job_id, **payload})


@app.route("/api/active")
def api_active():
    with ACTIVE_LOCK:
        return jsonify({"ok": True, "path": ACTIVE["path"], "info": ACTIVE["info"]})


@app.route("/api/release", methods=["POST"])
def api_release():
    """Let go of the image: the choice, and anything still holding it open.

    An examiner finishing a case wants the drive back. Clearing the label
    is not enough on Windows, where an open handle keeps a device busy and
    the program looks like it is still using an image it has finished with.
    """
    import gc

    # A run still reading the image holds it open, and nothing here can make
    # it stop: releasing used to clear the label and answer "the program is
    # no longer holding it" while an extraction went on reading the drive.
    # It also walked the job table closing each job's "reader" -- a key no
    # job has ever had, so it closed nothing and said so in a field nobody
    # read. The run's own reader is closed by run_extraction when it ends.
    with ACTIVE_LOCK:
        was = ACTIVE.get("path")
        busy = _running_job_for(was) if was else ""
        if busy:
            return error_response(RuntimeError(
                f"An extraction is still reading this image (job {busy}). "
                f"Cancel it, or let it finish, and the program lets go of "
                f"the image by itself."), status=409)
        ACTIVE.update(path=None, info=None)
    gc.collect()        # a reader nothing refers to any more is closed now

    return jsonify({"ok": True, "released": was or "",
                    "note": ("The program is no longer holding it. If the "
                             "drive still will not detach, something else "
                             "has it open -- Windows' own indexer and "
                             "antivirus both do this.")})


# --------------------------------------------------------------------------
# Custom carve signatures (same model as the FAT32 tool's Custom Signatures)
# --------------------------------------------------------------------------
def _build_signature(ext: str, header_hex: str, footer_hex: str, max_size: int):
    """Turn user hex into a signature object. Uses fat32_core's builder when
    it is importable so both tools validate input identically."""
    if fat32_core is not None:
        return fat32_core.signature_from_hex(ext, header_hex, footer_hex, max_size)

    ext = (ext or "").strip().lstrip(".").lower()
    if not ext:
        raise ValueError("File extension is required")
    header_hex = (header_hex or "").strip().replace(" ", "")
    if not header_hex:
        raise ValueError("Header bytes are required")
    try:
        header = bytes.fromhex(header_hex)
    except ValueError as exc:
        raise ValueError(f"Header is not valid hex: {exc}") from exc
    footer = None
    footer_hex = (footer_hex or "").strip().replace(" ", "")
    if footer_hex:
        try:
            footer = bytes.fromhex(footer_hex)
        except ValueError as exc:
            raise ValueError(f"Footer is not valid hex: {exc}") from exc
    if max_size <= 0:
        raise ValueError("Maximum size must be positive")
    return _LocalSignature(ext, header, footer, int(max_size))


class _LocalSignature:
    """Stand-in for fat32_core.FileSignature when running without it.
    media_core reads these attributes by name, so the shape is all that
    matters."""

    def __init__(self, ext, header, footer, max_size):
        self.ext, self.header, self.footer, self.max_size = ext, header, footer, max_size






def _printable(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data or b"")


def _common_prefix(items):
    if not items:
        return b""
    out = bytearray()
    for i in range(min(len(x) for x in items)):
        byte = items[0][i]
        if all(x[i] == byte for x in items):
            out.append(byte)
        else:
            break
    return bytes(out)


def _common_suffix(items):
    if not items:
        return b""
    out = bytearray()
    for i in range(1, min(len(x) for x in items) + 1):
        byte = items[0][-i]
        if all(x[-i] == byte for x in items):
            out.insert(0, byte)
        else:
            break
    return bytes(out)


@app.route("/api/sources")
def api_sources():
    """Mounted volumes and raw devices on this machine, so a USB stick can be
    picked from a list instead of typed as a path."""
    try:
        sources = mount_detect.list_attached_sources()
    except Exception as exc:  # noqa: BLE001 - platform quirks are not fatal
        return jsonify({"ok": True, "sources": [], "error": str(exc)})
    for src in sources:
        size = src.get("size")
        src["size_human"] = media_core.human(size) if size else ""
    return jsonify({"ok": True, "sources": sources,
                    "elevated": _is_elevated()})


def _is_elevated() -> bool:
    """Raw device reads need root or Administrator; say so before the read
    fails with a bare permission error."""
    try:
        if os.name == "nt":
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:  # noqa: BLE001
        return False



def _drive_roots() -> list:
    """Every place worth starting a browse from.

    On Windows that is the drive letters; anywhere else it is the filesystem
    root, the home directory, and the usual mount points. Without this the
    picker could only reach the drive the program happens to be running
    from, because the parent of a drive root is itself.
    """
    roots = []
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            path = f"{letter}:\\"
            if os.path.exists(path):
                label = path
                try:
                    usage = shutil.disk_usage(path)
                    label = f"{path}  ({media_core.human(usage.free)} free)"
                except OSError:
                    pass          # empty optical drive, disconnected share
                roots.append({"name": label, "path": path})
        return roots

    roots.append({"name": "/", "path": "/"})
    home = os.path.expanduser("~")
    if home and os.path.isdir(home):
        roots.append({"name": home, "path": home})
    for mount_base in ("/media", "/mnt", "/Volumes", "/run/media"):
        if not os.path.isdir(mount_base):
            continue
        try:
            for name in sorted(os.listdir(mount_base))[:12]:
                full = os.path.join(mount_base, name)
                if os.path.isdir(full):
                    roots.append({"name": full, "path": full})
        except OSError:
            continue
    return roots


def _parent_of(path: str):
    """The folder above this one.

    Returns "" at a drive root, meaning "the list of drives" rather than
    "nowhere" -- that distinction is what lets the picker leave the drive it
    started on. None means there really is nothing above.
    """
    absolute = os.path.abspath(path)
    parent = os.path.dirname(absolute.rstrip(os.sep) or os.sep)
    if parent and parent != absolute:
        return parent
    if os.name == "nt":
        return ""          # at a drive root: go up to the drive list
    return None            # at "/": nothing above it


@app.route("/api/browse")
def api_browse():
    """Directory listing for the file picker, on the machine running this."""
    path = request.args.get("path", "").strip()
    roots = _drive_roots()

    if not path:
        if os.name == "nt":
            return jsonify({"ok": True, "path": "", "parent": None,
                            "roots": roots, "at_drive_list": True,
                            "entries": [{"name": r["name"], "path": r["path"],
                                         "is_dir": True, "size_human": None,
                                         "likely_image": False}
                                        for r in roots]})
        path = os.path.expanduser("~") or "/"

    if not os.path.isdir(path):
        return error_response(NotADirectoryError(f"Not a directory: {path}"))
    try:
        names = os.listdir(path)
    except OSError as exc:
        return error_response(exc, status=403)

    entries = []
    for name in names:
        if name.startswith("."):
            continue
        full = os.path.join(path, name)
        try:
            is_dir = os.path.isdir(full)
            size = None if is_dir else os.path.getsize(full)
        except OSError:
            continue
        entries.append({
            "name": name, "path": full, "is_dir": is_dir,
            "size_human": None if size is None else media_core.human(size),
            "likely_image": (not is_dir)
                            and os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS,
        })
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    return jsonify({"ok": True, "path": os.path.abspath(path),
                    "parent": _parent_of(path), "roots": roots,
                    "at_drive_list": False, "entries": entries,
                    "media_files": sum(1 for e in entries
                                       if not e["is_dir"]
                                       and os.path.splitext(e["name"])[1].lower()
                                       .lstrip(".") in (media_core.IMAGE_EXTS
                                                        | media_core.VIDEO_EXTS))})


def _running_job_for(path: str, locked: bool = False, ignore: str = ""):
    """The id of a job still running on this image, if there is one."""
    wanted = os.path.abspath(path or "")

    def find():
        for job_id, job in JOBS.items():
            if job_id != ignore and job.get("status") == "running" and \
                    os.path.abspath(job.get("path") or "") == wanted:
                return job_id
        return ""

    if locked:
        return find()
    with JOBS_LOCK:
        return find()


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(force=True) or {}
    with ACTIVE_LOCK:
        active = ACTIVE["path"]
    path = (data.get("path") or "").strip() or (active or "")
    if not path:
        return error_response(ValueError(
            "No image accepted yet — load an image and press Use This Image."))
    if active and os.path.abspath(path) != active:
        return error_response(ValueError(
            f"That is not the accepted image. Currently in use: {active}. "
            f"Press Change to switch."))
    if not active:
        return error_response(ValueError(
            "Load the image and press Use This Image before extracting."))
    if not os.path.exists(path):
        return error_response(FileNotFoundError(f"No such file: {path}"))

    # Encrypted volumes: passwords and key-file paths for this run only. Held
    # by the run's own thread and nowhere else -- not in JOBS, which the page
    # reads back, and not in anything remembered for the next run.
    from fme.engines import unlock
    passwords = data.get("passwords") or []
    if isinstance(passwords, str):
        passwords = passwords.splitlines()
    key_files = [k for k in (str(x).strip() for x in data.get("key_files") or []) if k]
    for key_file in key_files:
        if not os.path.isfile(key_file):
            return error_response(FileNotFoundError(f"Key file not found: {key_file}"))
    credentials = unlock.Credentials(passwords, key_files)
    try:
        unlock.require(credentials)
    except unlock.UnlockUnavailable as exc:
        return error_response(exc)
    data.pop("passwords", None)

    options = {}
    mode = data.get("mode", "both")
    if mode not in ("both", "fs", "carve"):
        return error_response(ValueError("mode must be both, fs or carve"))
    options["mode"] = mode
    for key in ("images_only", "videos_only", "no_deleted", "include_system",
                "no_dedupe"):
        if key in data:
            options[key] = bool(data[key])
    case_number = (data.get("case_number") or "").strip()
    examiner_name = (data.get("examiner") or "").strip()
    if case_number:
        supported = getattr(media_core, "DEFAULT_OPTIONS", {})
        if data.get("auto_vic", True) and "vic_case" in supported:
            options["vic_case"] = case_number
            options["vic_evidence"] = (data.get("evidence") or "").strip()
        if data.get("auto_lava", True) and "lava_case" in supported:
            options["lava_case"] = case_number
            options["lava_examiner"] = (data.get("examiner") or "").strip()

    for key in ("carve_files", "scan_inside", "artifacts"):
        if key in data and key in getattr(media_core, "DEFAULT_OPTIONS", {}):
            options[key] = bool(data[key])
    for key in ("databases", "db_freespace", "hash_source", "find_members"):
        if key in data and key in getattr(media_core, "DEFAULT_OPTIONS", {}):
            options[key] = bool(data[key])
    if data.get("workers") is not None:
        workers = data["workers"]
        if workers == "auto":
            workers = media_core.default_workers() if hasattr(
                media_core, "default_workers") else 1
        try:
            options["workers"] = max(1, int(workers))
        except (TypeError, ValueError):
            return error_response(ValueError("workers must be a number or 'auto'"))
        if "workers" not in getattr(media_core, "DEFAULT_OPTIONS", {}):
            options.pop("workers")  # older core: run single-process
    if data.get("align"):
        try:
            options["align"] = max(1, int(data["align"]))
        except (TypeError, ValueError):
            return error_response(ValueError("align must be an integer"))

    with SIG_LOCK:
        stored = list(CUSTOM_SIGNATURES)
    extra_signatures = []
    for raw in stored + list(data.get("custom_signatures") or []):
        try:
            extra_signatures.append(_build_signature(
                raw.get("ext", ""), raw.get("header_hex", ""),
                raw.get("footer_hex", ""),
                int(raw.get("max_size") or 64 * 1024 * 1024)))
        except (ValueError, TypeError) as exc:
            return error_response(ValueError(f"Custom signature: {exc}"))

    # One run per image at a time. A second Start -- a double click is
    # enough -- began a second extraction of the same image into a second
    # case, and the first to finish released the image while the other was
    # still reading it. Checked here, before anything changes, and again
    # where the job is recorded, under the lock, so two requests at the same
    # moment cannot both pass.
    busy = _running_job_for(path)
    if busy:
        return error_response(RuntimeError(
            f"This image is already being extracted (job {busy}). Wait for "
            f"it to finish, or cancel it, before starting another."), status=409)

    # This job's folder is decided here, from this request, and held in a
    # local. It was taken from the module-wide OUT_ROOT after this request
    # had rebound it, so two Starts at once with different folders could
    # each put its case in the other's -- and a Start refused below as busy
    # had already moved every later job's folder.
    root = (data.get("output_root") or "").strip()
    try:
        job_root = checked_output_root(root) if root else checked_output_root(OUT_ROOT)
    except (OSError, PermissionError) as exc:
        return error_response(exc)

    job_id = uuid.uuid4().hex
    out_dir = os.path.join(job_root, job_id)
    cancel = threading.Event()

    # Taken with the image lock held, in the order /api/release takes them:
    # the accepted image was read at the top of this request and the lock let
    # go, so a release could land in between -- it found no running job,
    # told the examiner the image was let go, and this run then started on it.
    with ACTIVE_LOCK, JOBS_LOCK:
        if not ACTIVE["path"] or \
                os.path.abspath(ACTIVE["path"]) != os.path.abspath(path):
            return error_response(ValueError(
                "The image was released before the run could start. Load it "
                "and press Use This Image again."), status=409)
        busy = _running_job_for(path, locked=True)
        if busy:
            return error_response(RuntimeError(
                f"This image is already being extracted (job {busy})."),
                status=409)
        if root:
            # Remembered for the next job only once this one is accepted.
            _remember_output_root(job_root)
        JOBS[job_id] = {"status": "running", "done": 0, "total": 0,
                        "examiner": examiner_name, "case_number": case_number,
                        "recovered": 0, "phase": "starting", "files": [],
                        "summary": None, "error": None, "path": path,
                        "out_dir": out_dir, "_cancel": cancel}

    def run():
        # Files are read back from the manifest as the run writes it, so the
        # gallery fills during a run instead of staying empty until the end.
        # On a large exhibit that is the difference between watching a run
        # work and wondering whether it has hung.
        live = {"at": 0.0}
        tail = _ManifestTail(os.path.join(out_dir, "manifest.csv"))

        def refresh_files():
            # Only the rows written since the last look. Re-reading the whole
            # manifest every few seconds, as this once did, ran on the
            # extraction thread and cost more with every file found: measured
            # at 0.5 s a refresh with 25,000 files and 3.0 s with 200,000 --
            # 43% of a large run spent re-reading what it had already shown.
            before = tail.pos
            fresh = tail.read_new()
            if not fresh and not tail.restarted:
                return
            records = [rec for rec in map(_live_row, fresh) if rec is not None]
            entries = [_job_file_entry(rec) for rec in records]
            # Details reads the same rows. Parsed here already, they are handed
            # over rather than parsed again on the first click -- 7 s at
            # 200,000 rows, with the GIL held.
            _feed_case_records(out_dir, tail, before, records)
            with JOBS_LOCK:
                before = [] if tail.restarted else JOBS[job_id].get("files", [])
                JOBS[job_id]["files"] = before + entries

        def progress(done, total, recovered, phase, stats=None):
            with JOBS_LOCK:
                JOBS[job_id].update(done=done, total=total,
                                    recovered=recovered, phase=phase)
                # What the run has actually done so far, so a long run can
                # be watched rather than guessed at.
                if stats:
                    JOBS[job_id]["counts"] = media_core.live_counts(stats)
            # Re-reading a growing manifest is cheap but not free, so at most
            # once every few seconds.
            now = time.time()
            if now - live["at"] >= 4.0:
                live["at"] = now
                refresh_files()

        try:
            kwargs = dict(progress_cb=progress, **options)
            if SUPPORTS_CANCEL:
                kwargs["cancel_cb"] = cancel.is_set
            if SUPPORTS_CUSTOM_SIGS and extra_signatures:
                kwargs["extra_signatures"] = extra_signatures
            if credentials:
                kwargs["credentials"] = credentials
            summary = media_core.run_extraction(path, out_dir, **kwargs)
            files = summary.pop("files", [])
            try:
                _registry().refresh(out_dir)
            except (OSError, sqlite3.Error):
                pass
            _print_finished(summary, files)
            with JOBS_LOCK:
                JOBS[job_id].update(
                    status="canceled" if (summary.get("canceled")
                                         or summary.get("cancelled")) else "done",
                    phase="finished", summary=summary, recovered=len(files),
                    files=[_job_file_entry(f) for f in files])
            # The run is over, so let go of the source. An examiner who has
            # finished with an image wants the drive back, and a program
            # that holds it until it closes is one they have to close.
            #
            # Only if the accepted image is still this one. The examiner can
            # accept another image while a run goes on; clearing regardless
            # threw that choice away, and the next Start said no image had
            # been accepted.
            with ACTIVE_LOCK:
                still_ours = (ACTIVE["path"] or "") and \
                    os.path.abspath(ACTIVE["path"]) == os.path.abspath(path)
                if still_ours and not _running_job_for(path, ignore=job_id):
                    ACTIVE.update(path=None, info=None)

            # And make the gallery tiles now, while nobody is waiting.
            threading.Thread(target=_prefill_thumbs, args=(job_id,),
                             daemon=True).start()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            with JOBS_LOCK:
                JOBS[job_id].update(status="error", error=str(exc))
            traceback.print_exc()

    try:
        # Record the case as soon as it starts, so a run that is cancelled or
        # crashes still appears in the list with whatever it produced.
        _registry().register(out_dir, case_number=case_number,
                             evidence=(data.get("evidence") or "").strip(),
                             examiner=examiner_name, source=path)
    except (OSError, sqlite3.Error):
        pass        # the list is a convenience; never fail a run over it

    with JOBS_LOCK:
        LAST_JOB["id"] = job_id   # so a page reload can rejoin this run
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


PREVIEWABLE = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "ico", "avif",
               "mp4", "webm", "mov", "ogv"}


@app.route("/api/status")
def api_status():
    job_id = request.args.get("job_id", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        job = {k: v for k, v in job.items() if not k.startswith("_")} if job else None
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    answer = {"ok": True, **job}
    held = job.get("files") or []
    answer["files_known"] = len(held)
    if _paged(len(held)):
        # The page asks for what it shows instead (see PAGED_THRESHOLD).
        answer["files"] = []
        answer["paged"] = True
        return jsonify(answer)

    # While a run is going, send a window of the files rather than all
    # of them.
    #
    # This endpoint is polled every few hundred milliseconds and used
    # to return the entire list each time. On a case of two hundred
    # thousand files that is a ninety-nine megabyte response, several
    # times a second: the browser stops drawing while the console
    # keeps counting, which is what an examiner sees as a gallery that
    # stays empty while the recovered count climbs.
    #
    # Finished jobs are untouched -- opening a case hands the page the
    # whole list once, which is what the gallery browses -- so this
    # changes only the live view, where a window is all anyone can
    # look at anyway.
    #
    # A page that says how many it already holds (files_from) is sent the
    # files after those instead, in batches, so it builds the whole list
    # while the run goes on. The window alone meant a page that only ever
    # had the newest 500: every page size past 500 showed the same 500,
    # the pager said "of 500" and the All count never moved, however many
    # thousands had been recovered.
    if job.get("status") in ("running", "starting"):
        start = request.args.get("files_from")
        if start is not None and start.isdigit():
            start = int(start)
            if start > len(held):
                # The list was rebuilt under the page: start it again.
                start = 0
                answer["files_reset"] = True
            answer["files"] = held[start:start + LIVE_BATCH]
            answer["files_from"] = start
        else:
            answer["files"] = held[-LIVE_WINDOW:]
            answer["files_windowed"] = len(held) > LIVE_WINDOW
    return jsonify(answer)


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job_id", ""))
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    # Said as it is. Every job answered "ok", so the page reported a cancel
    # for a run that had already finished or that never reads the flag.
    if job.get("status") != "running":
        return jsonify({"ok": False, "status": job.get("status"),
                        "error": "This run is not running, so there is "
                                 "nothing to cancel."}), 409
    if job.get("_uncancelable"):
        return jsonify({"ok": False, "status": "running",
                        "error": "This import cannot be stopped part way; "
                                 "it is still running and will finish."}), 409
    job["_cancel"].set()
    return jsonify({"ok": True})


def _job_file(job_id: str, rel: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return None, error_response(KeyError("Unknown job_id"), status=404)
    base = os.path.abspath(job["out_dir"])
    full = os.path.abspath(os.path.join(base, rel))
    if not request_guard.contained(base, full):
        return None, error_response(ValueError("Invalid path"))
    if not os.path.isfile(full):
        return None, error_response(FileNotFoundError("File not found"), status=404)
    return full, None




def _desktop_open(path: str, reveal: bool = False) -> None:
    """Hand a file to the desktop: open it, or show it in the file manager."""
    if sys.platform == "win32":
        if reveal:
            explorer = shutil.which("explorer.exe") or "explorer.exe"
            # /select, needs the path as one argument, quoted by Windows itself
            subprocess.Popen([explorer, "/select,", os.path.normpath(path)])
        else:
            os.startfile(path)      # noqa: S606 - the point of the feature
        return

    if sys.platform == "darwin":
        opener = shutil.which("open")
        if not opener:
            raise RuntimeError("The 'open' command is not available")
        subprocess.Popen([opener, "-R", path] if reveal else [opener, path])
        return

    opener = shutil.which("xdg-open")
    if not opener:
        raise RuntimeError(
            "xdg-open is not installed, so this machine has no way to say "
            "which application should handle the file")
    subprocess.Popen([opener, os.path.dirname(path) if reveal else path])




@app.errorhandler(hashstore.StoreMissing)
def _store_missing(exc):
    return jsonify({"ok": False, "error": str(exc)}), 404


@app.errorhandler(hashstore.StoreUnreachable)
def _store_unreachable(exc):
    """A store that cannot be opened, said in terms of what to do about it."""
    return jsonify({"ok": False, "error": str(exc)}), 400


@app.errorhandler(remotestore.RemoteStoreError)
def _remote_store_error(exc):
    """A shared store that cannot be reached is not a fault in this program.

    Reported as its own thing, with the server's own words, so an examiner
    sees "could not reach the hash store" rather than an internal error --
    and so a failed check is never mistaken for a clean one.
    """
    return jsonify({"ok": False, "error": str(exc),
                    "remote_store": True}), 502


@app.errorhandler(Exception)
def _any_error(exc):
    """Answer an API call with JSON even when something unforeseen breaks.

    Flask's own error page is HTML, so a route that threw reached the page as
    "Unexpected token '<'" -- a message about JSON parsing that says nothing
    about the actual fault. The traceback still goes to the console; this
    makes sure the interface shows the real reason instead of a riddle.
    """
    from werkzeug.exceptions import HTTPException

    status = exc.code if isinstance(exc, HTTPException) else 500
    if not request.path.startswith("/api/"):
        # Re-raising from inside an error handler is what turned a browser's
        # routine request for /favicon.ico into a 500 and a pair of
        # tracebacks in the console. An HTTP error already carries its own
        # response; anything else is ours and worth printing once.
        if isinstance(exc, HTTPException):
            return exc
        traceback.print_exc()
        return Response(f"{type(exc).__name__}: {exc}", status=500,
                        mimetype="text/plain")

    if not isinstance(exc, HTTPException):
        traceback.print_exc()
    return jsonify({
        "ok": False,
        "error": (f"{type(exc).__name__}: {exc}" if not isinstance(exc, HTTPException)
                  else f"{status} {exc.name}: {exc.description}"),
        "where": request.path,
        "hint": ("The console window this program was started from has the "
                 "full traceback." if status >= 500 else ""),
    }), status


@app.route("/favicon.ico")
def route_favicon():
    """A tab icon, so the browser stops asking for one that is not there."""
    icon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#11151c"/>'
        '<circle cx="14" cy="14" r="7" fill="none" stroke="#4a9eff" '
        'stroke-width="3"/>'
        '<path d="M19 19 L26 26" stroke="#4a9eff" stroke-width="3" '
        'stroke-linecap="round"/></svg>')
    return Response(icon, mimetype="image/svg+xml")


@app.route("/api/health")
def api_health():
    """Enough to tell a running program from a confused one."""
    import platform
    return jsonify({
        "ok": True, "release": media_core.RELEASE,
        "engine": media_core.__version__,
        "python": platform.python_version(),
        "module": os.path.abspath(__file__),
        "mixed_install": media_core.mixed_install(),
    })


# The comparison used to be a page in the program, reachable from Help.
# It is a working document about another tool, written for the people
# building this one -- not something an examiner should meet while working
# a case, and not something that belongs in a program a defence expert may
# be shown. It ships as docs/AUDIT.md in the release instead.


@app.route("/server-setup")
def route_server_setup():
    """The step-by-step guide to running a shared store."""
    try:
        return Response(manual.render(media_core.RELEASE, "SERVER_SETUP.md"),
                        mimetype="text/html")
    except OSError as exc:
        return error_response(exc)


# Every document that ships with the program, readable from inside it.
#
# They were in the folder and nowhere an examiner would look. A
# limitations statement nobody opens is not a limitations statement,
# and an examiner asked in a hearing what the tool does not do should
# be able to find the answer in the tool.
#
# docs/AUDIT.md is deliberately not here: it names another tool, it is
# a working document for whoever builds this, and it has no business in
# front of an examiner working a case or a defence expert reading over
# their shoulder.
SHIPPED_DOCUMENTS = {
    "validation": ("docs/VALIDATION.md", "What this does, and does not, do"),
    "tasks": ("docs/EXAMINER_TASKS.md", "Checks only this machine can run"),
    "deployment": ("docs/DEPLOYMENT.md", "Installing and keeping in step"),
    "changes": ("CHANGELOG.md", "What changed, and when"),
    "building": ("BUILDING.md", "Building the standalone program"),
    "handbook": ("CLAUDE.md", "How this program is put together"),
    "licenses": ("THIRD_PARTY_NOTICES.md", "Licenses, notices and warranty"),
}


@app.route("/doc/<name>")
def route_document(name: str):
    """One of the shipped documents, as a page."""
    found = SHIPPED_DOCUMENTS.get(name)
    if not found:
        return error_response(
            KeyError(f"No document called {name}. There is "
                     f"{', '.join(sorted(SHIPPED_DOCUMENTS))}."), status=404)
    try:
        return Response(manual.render(media_core.RELEASE, found[0]),
                        mimetype="text/html")
    except OSError as exc:
        return error_response(exc)


@app.route("/manual")
def route_manual():
    """The manual, rendered from MANUAL.md beside the program."""
    try:
        return Response(manual.render(media_core.RELEASE), mimetype="text/html")
    except OSError as exc:
        return error_response(exc)












def _match_region(match: dict):
    """The region a hash-set match's bare code is in, or None if unknown.

    The store says (a server from before regions is a US store, and so is a
    set loaded for the session, whose lists predate regions); the stash
    says from v2026.11.318. A stash row from before then does not, and a
    code nobody can place in a scheme is never applied.
    """
    region = match.get("region")
    if region:
        return regions.normalize(region)
    if match.get("via") == "stash" or match.get("kind") == "stash":
        return None
    return "US"


def _apply_hash_categories(job_id: str, lookup: dict, hold=()) -> dict:
    """Let hash sets categorize files nobody has looked at yet.

    The rules matter more than the code:

    * **Only a file that is still uncategorized.** A set never overrides an
      examiner. If somebody has graded a file, that stands.
    * **A set that asserts a category wins over one that does not**, and
      where several assert, the most severe is taken. A file in both a
      Project VIC set and a local list is not made less serious by the
      second.
    * **A known-good hit (the NSRL) moves a file to Non-Pertinent only when
      nothing notable flags it too.** Software is in the NSRL; so is a
      picture somebody shipped in an installer, and if a Project VIC set
      names it, that is the finding.
    * **Every decision made this way is recorded as made by the set**, not
      by the examiner, and is visible as such in the gallery, the details
      pane and the exports. A machine saying "this hash is in a list" is not
      a person saying "I looked at this".
    """
    case = _case_for(job_id)
    if case is None:
        return {"categorized": 0}

    decisions = case.decisions()
    scheme = case.scheme
    known = {c["code"] for c in case.categories()}
    changed = 0
    counts: dict = {}
    other_scheme = 0
    held = {d for d in hold if d}
    for digest, matches in lookup.items():
        decided = decisions.get(digest)
        if decided and decided.get("category") != case.uncategorized:
            continue            # somebody has already looked at this one
        if digest in held:
            # Sources the examiner was told disagree: reported, and left for
            # them to decide, as the scan's answer says.
            continue

        asserted, source = None, ""
        known_good = False
        for match in matches:
            kind = match.get("kind", "")
            record = match.get("record") or {}
            # Through vic_category: a set that wrote Category as "1" was
            # read as no category, and its CAM matches stayed Uncategorized.
            category = hashsets.vic_category(record.get("Category"))
            if category is None:
                category = hashsets.vic_category(match.get("category"))
            if kind == "nsrl":
                known_good = True
                continue
            if isinstance(category, int):
                if kind == "projectvic":
                    # VICS's categories, mapped onto the case's own scheme;
                    # outside the US only Non-Pertinent carries over.
                    local = scheme.vic_in.get(category)
                elif _match_region(match) != case.region:
                    # A bare code graded under another region's scheme, or
                    # one whose scheme is not known: the same number means
                    # something else here, so it is shown, never applied.
                    other_scheme += 1
                    continue
                else:
                    local = category
                if local is None or local not in known:
                    continue
                if kind != "projectvic" and local not in scheme.codes:
                    # An examiner's own category (10 and up) means something
                    # only in the case that made it: "Series X" there is
                    # "Vacation photos" here (v2026.11.319).
                    other_scheme += 1
                    continue
                # The most severe in the case's scheme; Non-Pertinent the
                # least (regions.Scheme.severity_rank).
                if asserted is None or case.severity_rank(local) < \
                        case.severity_rank(asserted):
                    asserted, source = local, match.get("set", kind)

        # Written only if the file is still undecided when the write is made,
        # under the case's own lock (CaseDB.set_category): the examiner may
        # have graded it since `decisions` was read.
        if asserted is not None:
            wrote = case.set_category(digest, asserted, by=f"hash set: {source}",
                                      only_if_undecided=True)
        elif known_good and case.non_pertinent is not None:
            wrote = case.set_category(digest, case.non_pertinent,
                                      by="hash set: NSRL (known software)",
                                      only_if_undecided=True)
            asserted = case.non_pertinent
        else:
            continue
        if not wrote:
            continue
        changed += 1
        counts[asserted] = counts.get(asserted, 0) + 1

    if changed:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            out_dir = job["out_dir"] if job else None
        if out_dir:
            try:
                _registry().refresh(out_dir)
            except (OSError, sqlite3.Error):
                pass
    answer = {"categorized": changed, "by_category": counts}
    if other_scheme:
        answer["other_scheme"] = other_scheme
    return answer




@app.route("/api/editmatch", methods=["POST"])
def api_edit_match():
    """Find this picture again, however it was edited.

    A perceptual hash finds a re-saved copy. This finds one that was
    cropped, rotated, mirrored, captioned or pasted into a screenshot, by
    matching what is inside the picture rather than describing the frame.
    """
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    rel_path = (data.get("file") or "").strip()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)

    target = os.path.join(job["out_dir"], rel_path)
    if not os.path.isfile(target):
        return error_response(FileNotFoundError(rel_path), status=404)

    ready, why = edit_match.available()
    if not ready:
        return jsonify({"ok": False, "error": why}), 400

    # The fingerprints of the case, worked out once and kept with it: the
    # second search costs the comparisons, not the whole case again.
    case = _case_for(job_id)
    library = (case.derived("edit_fingerprints") or {}) if case else {}
    images = [f for f in job["files"] if f.get("category") == "image"]
    fresh = 0
    for entry in images:
        digest = entry.get("sha256", "")
        if not digest or digest in library:
            continue
        found = edit_match.fingerprints(
            os.path.join(job["out_dir"], entry.get("rel_path", "")))
        if found:
            found["signature"] = list(found["signature"])
            library[digest] = found
            fresh += 1
    if case is not None and fresh:
        # Merged into what is saved now, not saved over it: two searches at
        # once each saved their own library and the second dropped the first's.
        case.merge_derived("edit_fingerprints", lambda saved: {**saved, **library})

    for entry in library.values():
        entry["signature"] = tuple(entry["signature"])
    paths = {f.get("sha256"): os.path.join(job["out_dir"], f.get("rel_path", ""))
             for f in images}

    result = edit_match.find_copies(target, library, paths,
                                    limit=int(data.get("limit") or 40))
    if not result.get("ok"):
        return jsonify(result), 400

    by_digest = {f.get("sha256"): f for f in job["files"]}
    for match in result["matches"]:
        file = by_digest.get(match["sha256"]) or {}
        match["rel_path"] = file.get("rel_path", "")
        match["name"] = os.path.basename(file.get("rel_path", ""))
    return jsonify({"ok": True, "fingerprinted": len(library),
                    "considered": result["considered"],
                    "matches": result["matches"]})







# When each case folder last had its upkeep. One clock for every case let
# grading in one case hold off the next case's snapshot, and the check and
# the set were not under a lock, so two gradings could both start upkeep.
HOUSEKEEPING: dict = {}
HOUSEKEEPING_LOCK = threading.Lock()
HOUSEKEEPING_EVERY = 90.0


def _housekeeping(out_dir, case) -> None:
    """Occasional upkeep, off the path a keystroke takes.

    The case list's counts and the periodic snapshot are both worth having
    and neither is worth a wait: they run at most every minute and a half,
    on a thread, so grading never queues behind them.
    """
    now = time.time()
    with HOUSEKEEPING_LOCK:
        if now - HOUSEKEEPING.get(out_dir, 0.0) < HOUSEKEEPING_EVERY:
            return
        HOUSEKEEPING[out_dir] = now

    def run():
        if out_dir:
            try:
                _registry().refresh(out_dir)
            except (OSError, sqlite3.Error):
                pass
        try:
            case.autosnapshot()
        except (OSError, sqlite3.Error):
            pass

    threading.Thread(target=run, daemon=True).start()







# How many of a running job's files go to the page on each poll. The
# gallery can only show a screenful; the rest arrive when the case is
# opened.
# Past this many files the page is not sent the case; it asks the server
# for the page it is showing (/api/gallery/query). Below it the page holds
# the list as it always has, which every older behavior was built and
# tested on. FME_PAGED_THRESHOLD=0 pages every case: the browser tests run
# that way too, so the paged path is not the untested one.
try:
    PAGED_THRESHOLD = int(os.environ.get("FME_PAGED_THRESHOLD", "20000"))
except ValueError:
    PAGED_THRESHOLD = 20000


def _paged(count: int) -> bool:
    return count > PAGED_THRESHOLD


# Points the case map is sent at once. MapLibre clusters them, so this is a
# bound on the answer's size rather than on what a map can show; the map
# says when a filter matched more.
MAP_POINT_LIMIT = 100_000


LIVE_WINDOW = 500

# How many files a page that is catching up is sent per poll: small enough
# that no single response is large, and at a poll every 600 ms a case of
# fifty thousand files is caught up in under a minute.
LIVE_BATCH = 2000

REPORTS: dict = {}
REPORTS_LOCK = threading.Lock()






def _build_report(data, job, dest, progress, cancel):
    """The report itself, run on a thread so the browser can watch it."""
    try:
        result = report.build(
            job["out_dir"], dest,
            header={**{k: (data.get(k) or "").strip() for k in
                       ("title", "agency", "case_number", "evidence",
                        "examiner", "notes")},
                    # The unit's logo unless this case gave its own.
                    "logo": ((data.get("logo") or "").strip()
                             or settings.load().get("agency_logo", ""))},
            scope={"categories": data.get("categories") or None,
                   "flags": data.get("flags") or None,
                   "sha256": data.get("sha256") or None,
                   "include_uncategorized": bool(
                       data.get("include_uncategorized", True))},
            formats=data.get("formats") or ["html"],
            thumbnails=bool(data.get("thumbnails", True)),
            blur=bool(data.get("blur")),
            full_copies=bool(data.get("full_copies")),
            fields=data.get("fields") or None,
            group_by_flag=bool(data.get("group_by_flag")),
            timezone=(data.get("timezone") or "").strip(),
            progress=progress, cancel=cancel)
    except report.Canceled:
        raise
    return result




def _thumb_path(out_dir: str, rel_path: str) -> str:
    digest = hashlib.sha256(rel_path.encode("utf-8", "replace")).hexdigest()
    return os.path.join(out_dir, "thumbs", digest[:2], digest + ".jpg")


def save_jpeg_atomically(picture, cached: str, **options) -> None:
    """Write a JPEG beside its name, then rename it into place.

    Saved straight to its name, a tile existed half-written for as long as
    the encoder ran, and /api/thumb serves whatever exists: the gallery
    showed a broken tile and the browser kept it for a day (max_age). A
    save that failed part way left that file to be served for good.
    """
    tmp = f"{cached}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        picture.save(tmp, "JPEG", **options)
        try:
            os.replace(tmp, cached)
        except PermissionError:
            # Windows: another thread is sending the finished copy of this
            # same picture; that one stands.
            if not os.path.isfile(cached):
                raise
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def _make_thumb(out_dir: str, full: str, rel_path: str) -> bool:
    """One tile-sized copy, or False where the file will not decode."""
    cached = _thumb_path(out_dir, rel_path)
    if os.path.isfile(cached):
        return True
    try:
        from fme.engines import imaging

        with imaging.open_image(full) as picture:
            picture.load()
            picture = picture.convert("RGB")
            picture.thumbnail((THUMB_EDGE, THUMB_EDGE))
            os.makedirs(os.path.dirname(cached), exist_ok=True)
            save_jpeg_atomically(picture, cached, quality=78, optimize=True)
        return True
    except Exception:  # noqa: BLE001 - a file that will not decode
        return False


def _prefill_thumbs(job_id: str) -> None:
    """Make the tiles before anybody scrolls to them.

    Done on a thread at low priority after a run, because the first visit
    to a gallery of a hundred thousand files is otherwise a hundred
    thousand decodes spread across the examiner's scrolling. The work is
    the same either way; doing it now means it is finished before it is
    wanted.
    """
    # Lowered for this thread only, and only where that is what nice() does.
    # On Linux it applies to the calling thread; on macOS and the BSDs to the
    # whole process, so every run lowered the server itself -- the page,
    # grading, the next run -- by ten more, until the program was restarted.
    if sys.platform.startswith("linux"):
        try:
            os.nice(10)
        except OSError:
            pass
    made = 0
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        files = list(job.get("files", [])) if job else []
        out_dir = (job or {}).get("out_dir", "")
    for entry in files:
        with JOBS_LOCK:
            if job_id not in JOBS:          # the case was closed
                return
        if entry.get("category") != "image":
            continue
        rel = entry.get("rel_path") or ""
        full = os.path.join(out_dir, rel)
        if rel and os.path.isfile(full) and _make_thumb(out_dir, full, rel):
            made += 1
    if made:
        LOG.info("thumbnails ready for %d picture(s)", made)





def _frame_matches_for(job_id: str, digest: str) -> list:
    """Videos holding a frame that looks like this still."""
    with JOBS_LOCK:
        job = JOBS.get(job_id) or {}
    found = job.get("frame_matches")
    if found is None:
        case = _case_for(job_id)
        found = case.derived("frame_matches") if case is not None else {}
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["frame_matches"] = found
    return found.get(digest, []) if digest else []


def _stills_from_video(job_id: str, digest: str) -> list:
    """Stills that match a frame of this video -- the same link, reversed."""
    if not digest:
        return []
    with JOBS_LOCK:
        job = JOBS.get(job_id) or {}
    found = job.get("frame_matches")
    if found is None:
        found = _frame_matches_for(job_id, "") or {}
        with JOBS_LOCK:
            job = JOBS.get(job_id) or {}
            found = job.get("frame_matches") or {}
    names = {}
    with JOBS_LOCK:
        for entry in (JOBS.get(job_id) or {}).get("files", []):
            names[entry.get("sha256")] = entry
    out = []
    for still, hits in (found or {}).items():
        for hit in hits:
            if hit.get("sha256") == digest:
                entry = names.get(still) or {}
                out.append({"sha256": still, "name": entry.get("name", ""),
                            "rel_path": entry.get("rel_path", ""),
                            "frame": hit.get("frame"),
                            "distance": hit.get("distance"),
                            "confidence": hit.get("confidence", "")})
    return sorted(out, key=lambda item: item.get("distance", 99))




@app.route("/")
def index():
    # The release number is substituted here rather than baked into PAGE, so
    # editing VERSION is enough to change what the page reports.
    nonce = request_guard.new_nonce()
    page = PAGE.replace("__RELEASE__", media_core.RELEASE).replace(
        "<script>", f'<script nonce="{nonce}">', 1)
    response = Response(page, mimetype="text/html")
    response.headers["Content-Security-Policy"] = request_guard.page_policy(nonce)
    return response


# --------------------------------------------------------------------------
# The page (kept inline so this file runs on its own, no templates dir)
# --------------------------------------------------------------------------
def _ui_part(name: str) -> str:
    """One piece of the interface, read from beside this module.

    The page used to be a 307,000-character string in the middle of this
    file, which put the markup, the stylesheet, the script and eighty-five
    routes in one place where an edit to any of them could break the rest.
    They are three files now; this reads them back.
    """
    import fme.resources as _resources

    for candidate in (_resources.find(os.path.join("fme", "web", "ui", name)),
                      os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "ui", name)):
        if candidate and os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as fh:
                return fh.read()
    raise FileNotFoundError(
        f"{name} is missing: the interface ships as three files beside "
        f"media_gui.py and one of them did not come with this copy.")


def _ui_page() -> str:
    """The interface, assembled."""
    return _ui_part("page.html").replace(
        "{styles}", _ui_part("styles.css")).replace(
        "{script}", _ui_part("app.js"))


PAGE = _ui_page()


def doctor() -> None:
    """Diagnose a split installation.

    Python imports media_core from the first directory on sys.path that has
    one -- usually the folder media_gui.py was launched from, which is not
    necessarily where you unpacked the latest release. When the two files
    come from different releases the symptom is an error about an argument
    one of them does not know about, so print exactly which files are in
    play and where every other copy on disk is.
    """
    print("media_gui.py")
    print(f"  version : {__version__}")
    print(f"  path    : {os.path.abspath(__file__)}")
    print("media_core.py (the one actually imported)")
    print(f"  version : {CORE_VERSION}")
    print(f"  path    : {getattr(media_core, '__file__', '?')}")
    print(f"  features: {', '.join(sorted(CORE_FEATURES)) or 'none advertised'}")
    print("  run_extraction accepts: "
          + ", ".join(n for n in _RUN_PARAMS if n != "options"))
    print(f"fat32_core: {'importable' if fat32_core else 'not importable'}")
    print(f"working directory: {os.getcwd()}")

    if CORE_WARNING:
        print(f"\n  ** {CORE_WARNING}")
    else:
        print("\n  Both files are from the same release.")

    others = []
    roots = {os.getcwd(), BASE_DIR, os.path.expanduser("~")}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith((".", "__")) and d != "node_modules"]
            if dirpath.count(os.sep) - root.count(os.sep) > 3:
                dirnames[:] = []
                continue
            if "media_core.py" in filenames:
                others.append(os.path.join(dirpath, "media_core.py"))
    others = sorted(set(others))
    imported = os.path.abspath(getattr(media_core, "__file__", ""))
    if len(others) > 1:
        print("\n  ** More than one media_core.py exists. Delete or rename the "
              "ones you are not using:")
        for path in others:
            mark = "  <-- imported" if os.path.abspath(path) == imported else ""
            try:
                ver = "?"
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if line.startswith("__version__"):
                            ver = line.split("=", 1)[1].strip().strip('"\'')
                            break
            except OSError:
                ver = "unreadable"
            print(f"     {ver:<8} {path}{mark}")
    caches = []
    for root in roots:
        cache = os.path.join(root, "__pycache__")
        if os.path.isdir(cache):
            caches.append(cache)
    if caches:
        print("\n  Stale bytecode can also shadow an update. Safe to delete:")
        for cache in caches:
            print(f"     {cache}")


# Where Chrome and Edge usually are, by platform. Both are Chromium, and
# either will do.
APP_BROWSERS = {
    "nt": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "posix": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome", "/usr/bin/chromium",
        "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge",
    ],
}


def find_app_browser() -> str:
    """A Chromium to open the interface in a window of its own."""
    import shutil as _shutil

    for candidate in APP_BROWSERS.get(os.name, APP_BROWSERS["posix"]):
        if os.path.isfile(candidate):
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser",
                 "microsoft-edge", "chrome", "msedge"):
        found = _shutil.which(name)
        if found:
            return found
    return ""


def open_as_app(url: str, profile_dir: str = "") -> dict:
    """Open the interface in its own window, separate from the examiner's browser.

    Not a different rendering engine -- it is the same Chromium. What it
    changes is everything around it:

    **No extensions.** A profile of its own means ad blockers, password
    managers and corporate policy extensions are not in the page. An
    extension injecting script into a gallery of five thousand tiles is a
    common cause of a renderer being killed, and it is not something this
    program can defend against from the inside.

    **Nothing else competing.** The examiner's ordinary browser has other
    tabs, and they share the memory and the cores. This window has neither.

    **A crash costs this window only**, not the dozen tabs somebody had open.

    The run and every decision are on disk either way, so a window dying has
    never cost work -- but it costs time, and this removes most of the
    reasons for it.
    """
    import subprocess
    import tempfile

    browser = find_app_browser()
    if not browser:
        return {"ok": False, "error": "No Chrome or Edge was found to open "
                                      "a window with."}
    profile = profile_dir or os.path.join(tempfile.gettempdir(),
                                          "fme_review_profile")
    os.makedirs(profile, exist_ok=True)
    command = [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        # The gallery decodes a lot of images; give it room before the
        # renderer starts discarding and re-decoding them.
        "--js-flags=--max-old-space-size=2048",
    ]
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"ok": False, "error": f"Could not start {os.path.basename(browser)}: {exc}"}
    return {"ok": True, "browser": browser, "profile": profile}


def serve_in_window(flask_app, host: str, port: int, title: str) -> bool:
    """Run the interface in a window of its own rather than a browser tab.

    Worth having for a tool that is used all day: no address bar, no risk of
    the tab being closed with a run going, and it appears in the task bar as
    a program rather than as one of thirty browser tabs. The server still
    runs the same way; only the thing displaying it changes.

    Returns False when pywebview is not installed, so the caller falls back
    to a browser.
    """
    try:
        import webview
    except ImportError:
        return False

    threading.Thread(
        target=lambda: serve(flask_app, host, port, quiet=True),
        daemon=True).start()
    # Give the server a moment to bind before the window asks for a page.
    import time as _time
    for _ in range(80):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            _time.sleep(0.25)
    webview.create_window(title, f"http://{host}:{port}/",
                          width=1500, height=950, resizable=True)
    webview.start()
    return True


def serve(flask_app, host: str, port: int, quiet: bool = False) -> None:
    """Run the web server, preferring waitress over Flask's own.

    Flask's built-in server prints "This is a development server. Do not use
    it in a production deployment." That warning is about exposing it to the
    internet: it is single-threaded by default, has no request limits, and
    was never hardened for hostile traffic. Bound to 127.0.0.1 on an
    examiner's own machine that is not a security problem -- but it does
    stall when several thumbnails are requested at once, which a gallery does
    constantly.

    Waitress is a small pure-Python WSGI server with neither limitation. When
    it is installed it is used, the warning disappears and galleries load
    faster. When it is not, Flask's server still works.
    """
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        print("Serving with Flask's built-in server, which prints a warning")
        print("about production use. Bound to this machine that is not a")
        print("concern; 'pip install waitress' removes it and loads galleries")
        print("faster.\n")
        flask_app.run(host=host, port=port, debug=False)
        return
    # A gallery asks for a few hundred thumbnails at once, and some of those
    # requests are slow on purpose -- converting a HEIC, decoding video
    # frames, reading a file off an evidence drive. Eight threads is enough
    # for the browser's own six-connection limit and not enough for the
    # background work that overlaps it, which is what "Task queue depth"
    # reports: requests waiting for a free thread. It is a queue, not an
    # error, but there is no reason to make people watch it.
    threads = max(16, min(48, (os.cpu_count() or 4) * 4))
    waitress_serve(flask_app, host=host, port=port, threads=threads,
                   channel_timeout=120, ident="Forensic Media Extractor")


def main() -> None:
    global DEFAULT_IMAGE_PATH
    ap = argparse.ArgumentParser(description="GUI for the forensic media extractor")
    ap.add_argument("-i", "--image", default="", help="pre-fill the image path")
    ap.add_argument("-o", "--output", default="",
                    help="folder to write results into (default: media_out/ "
                         "beside this script). Each run gets its own "
                         "subfolder inside it")
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (leave as localhost unless you know why)")
    ap.add_argument("--window", action="store_true",
                    help="open in a window of its own instead of a browser "
                         "tab (needs pywebview)")
    ap.add_argument("--app", dest="app_window", action="store_true",
                    help="open the interface in a window of its own: Chrome "
                         "or Edge with a separate profile, no extensions and "
                         "nothing else competing for memory. The same engine, "
                         "but nothing in the page except this program")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not open a browser window automatically")
    ap.add_argument("--doctor", action="store_true",
                    help="report which media_core.py is actually being loaded, "
                         "list any other copies on disk, and exit")
    args = ap.parse_args()

    if args.doctor:
        try:
            doctor()
        except BrokenPipeError:  # piping into head/less closes stdout early
            os._exit(0)
        return

    DEFAULT_IMAGE_PATH = os.path.abspath(args.image) if args.image else ""
    request_guard.allow(app, args.host)
    access_key = ""
    if request_guard._hostname(args.host) not in request_guard.LOOPBACK:
        access_key = request_guard.require_key(app)
        print(f"Listening on {args.host}, so other machines can reach this "
              f"program. It answers only requests carrying this run's access "
              f"key -- open the address below, which carries it -- and only "
              f"for localhost, the address given to --host and the names in "
              f"FME_ALLOWED_HOSTS "
              f"({os.environ.get('FME_ALLOWED_HOSTS') or 'none set'}).",
              file=sys.stderr)
    if args.output:
        try:
            set_output_root(args.output)
        except (OSError, PermissionError) as exc:
            print(f"Cannot use {args.output} for output: {exc}", file=sys.stderr)
            return
    # Bound to every address, the browser on this machine still reaches it
    # as localhost; "0.0.0.0" is not a name it is asked by.
    shown_host = "127.0.0.1" if args.host.strip() in request_guard.WILDCARD else args.host
    url = f"http://{shown_host}:{args.port}" + (f"/?key={access_key}" if access_key else "")
    print(f"Forensic Media Extractor {media_core.RELEASE} — {url}")
    mixed = media_core.mixed_install()
    if mixed:
        print(f"\n  WARNING: {mixed}\n")
    print(f"media_gui {__version__} | media_core {CORE_VERSION} "
          f"({getattr(media_core, '__file__', '?')})")
    print(f"Engine: {media_core._engine_description()}")
    space = output_space(OUT_ROOT)
    print(f"Output: {OUT_ROOT} ({space.get('free_human', '?')} free)")
    if CORE_WARNING:
        print(f"\n  ** {CORE_WARNING}\n     Run: python3 media_gui.py --doctor\n")
    print("Press Ctrl-C to quit.")
    if not args.no_browser:
        if getattr(args, "app_window", False):
            def open_window():
                result = open_as_app(url)
                if result["ok"]:
                    print(f"  opened in its own window: "
                          f"{os.path.basename(result['browser'])}")
                    print(f"  (a separate profile, no extensions: {result['profile']})")
                else:
                    print(f"  {result['error']} Opening the usual browser instead.")
                    webbrowser.open(url)
            threading.Timer(1.0, open_window).start()
        else:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if args.window and serve_in_window(
            app, args.host, args.port,
            f"Forensic Media Extractor {media_core.RELEASE}"):
        return
    if args.window:
        print("A window needs pywebview: pip install pywebview. "
              "Opening a browser instead.\n")
    serve(app, args.host, args.port)


if __name__ == "__main__":
    main()


CONVERTIBLE = {"heic", "heif", "hif", "tif", "tiff", "psd", "dng", "cr2",
               "cr3", "nef", "arw", "orf", "rw2", "raf", "srw", "pef", "jp2",
               "jxr", "jxl", "pcx", "tga", "dds", "icns", "webp", "avif",
               "ktx"}
THUMB_EDGE = 360          # what a gallery tile actually needs


# The routes that live in their own files now. Imported last, so every
# name they bind from this module already exists.
from fme.web import routes_case  # noqa: E402,F401
from fme.web import routes_media  # noqa: E402,F401
from fme.web import routes_setup  # noqa: E402,F401
from fme.web import routes_store      # noqa: E402,F401  (registers routes)
from fme.web import routes_apps  # noqa: E402,F401
from fme.web import routes_report  # noqa: E402,F401
