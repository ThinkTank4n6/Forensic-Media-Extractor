"""
routes_case.py -- opening, listing and reworking cases.

Split out of media_gui.py, which held eighty-five routes, the page, the
stylesheet and the script in one file. The bodies are unchanged; what
they lean on is bound from media_gui when this module is imported at
the end of that file, so every name resolves as it did inside it.
"""

from fme.web import media_gui as _core

# Everything media_gui holds, except the handful it rebinds.
#
# Copying a rebound value leaves this module with the one it had at
# import, and the symptom is not an error: the route answers 200 with
# the old value. That is how "the results folder cannot be changed"
# reached an examiner. They are left out entirely, so a bare
# reference here raises NameError at once instead of quietly using a
# stale copy -- read them as _core.OUT_ROOT and so on.
REBOUND = ("OUT_ROOT", "STORE_PATH", "DEFAULT_IMAGE_PATH")

globals().update({name: value for name, value in vars(_core).items()
                  if not name.startswith("__") and name not in REBOUND})


@app.route("/api/cases")
def api_cases():
    """Every case this program has processed."""
    try:
        book = _registry()
        return jsonify({"ok": True, "path": book.path, "cases": book.cases()})
    except (OSError, sqlite3.Error) as exc:
        return error_response(exc)


@app.route("/api/cases/scan", methods=["POST"])
def api_cases_scan():
    """Find output folders under a path and add them to the list."""
    data = request.get_json(force=True) or {}
    root = (data.get("path") or "").strip()
    if not os.path.isdir(root):
        return error_response(NotADirectoryError(f"Not a folder: {root}"))
    try:
        book = _registry()
        found = book.scan(root)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "found": len(found), "cases": book.cases()})


@app.route("/api/cases/close", methods=["POST"])
def api_cases_close():
    """Let go of a case: its database handle is closed, so the folder can be
    moved or backed up. Close used to take a snapshot and keep the file open
    until the program ended (v2026.11.319)."""
    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", "")) or {}
    out_dir = job.get("out_dir")
    if not out_dir:
        return error_response(KeyError("Unknown job_id"), status=404)
    return jsonify({"ok": True, "released": _release_case(out_dir)})


@app.route("/api/cases/forget", methods=["POST"])
def api_cases_forget():
    data = request.get_json(force=True) or {}
    try:
        book = _registry()
        out_dir = (data.get("out_dir") or "").strip()
        row = book.get(out_dir) or {}
        book.forget(out_dir)
    except (OSError, sqlite3.Error) as exc:
        return error_response(exc)
    # What was removed, so the page can offer to put it back.
    forgotten = {k: row.get(k, "") for k in
                 ("out_dir", "case_number", "evidence", "examiner", "source", "note")} \
        if row else None
    return jsonify({"ok": True, "cases": book.cases(), "forgotten": forgotten})


@app.route("/api/cases/restore", methods=["POST"])
def api_cases_restore():
    """Put a case just removed from the list back on it (Undo)."""
    data = request.get_json(force=True) or {}
    out_dir = (data.get("out_dir") or "").strip()
    if not out_dir:
        return error_response(ValueError("No case folder given."))
    try:
        book = _registry()
        book.register(out_dir, **{k: str(data.get(k) or "") for k in
                                  ("case_number", "evidence", "examiner", "source", "note")})
    except (OSError, sqlite3.Error) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "cases": book.cases()})


@app.route("/api/cases/open", methods=["POST"])
def api_cases_open():
    """Reopen a finished case from its output folder.

    No carving, no hashing: the manifest already records what was found and
    the case database already holds what was decided. This turns them back
    into a job so the rest of the interface works exactly as it does after a
    run.
    """
    data = request.get_json(force=True) or {}
    out_dir = os.path.abspath((data.get("out_dir") or "").strip())
    manifest_path = os.path.join(out_dir, "manifest.json")
    with JOBS_LOCK:
        busy = folder_busy(out_dir)
    if busy.startswith("an extraction"):
        # Opened as "done", it unlocked reports and exports over a case a
        # run is still adding to.
        return error_response(ValueError(
            f"Not opened: {busy}. Follow it on the Run tab; it opens here "
            f"when it finishes."))
    if not os.path.isfile(manifest_path):
        return error_response(FileNotFoundError(
            f"No manifest in {out_dir}. The folder may have been moved or "
            f"deleted; the list only records where output was, not a copy "
            f"of it."))
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return error_response(exc)

    book = _registry()
    known = book.get(out_dir)
    summary = dict(manifest.get("summary", {}))
    # What is on disk now, not what the run wrote then: a ProjectVIC.json
    # written by an export after the run was reported as missing on every
    # reopen, and one since deleted as present (v2026.11.320, U11).
    summary["reopened"] = True
    summary.pop("project_vic", None)
    for where in (out_dir, os.path.join(out_dir, "export"), os.path.join(out_dir, "report")):
        try:
            found = sorted(n for n in os.listdir(where)
                           if n.lower().startswith("projectvic") and n.lower().endswith(".json"))
        except OSError:
            continue
        if found:
            summary["project_vic"] = os.path.join(where, found[0])
            break
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "done", "done": 1, "total": 1,
            "recovered": len(manifest.get("files", [])), "phase": "finished",
            "files": [_job_file_entry(f) for f in manifest.get("files", [])],
            "summary": summary, "error": None,
            "path": summary.get("image", ""), "out_dir": out_dir,
            "examiner": known.get("examiner", ""),
            "case_number": known.get("case_number", ""),
            "_cancel": threading.Event(), "reopened": True,
        }
        LAST_JOB["id"] = job_id
    # Bring back what earlier sessions worked out, so a reopened case is
    # where it was left rather than where it started.
    case = _case_for(job_id)
    restored = {}
    if case is not None:
        matches = case.derived("hash_matches")
        similarity_rows = case.derived("similarity")
        screened = case.derived("screening")
        if screened:
            for entry in JOBS[job_id]["files"]:
                found = screened.get(entry.get("sha256"))
                if found:
                    entry["skin"] = found.get("skin")
                    entry["skin_band"] = found.get("band") or ""
                    entry["faces"] = found.get("faces", -1)
        restored = {"hash_matches": len(matches),
                    "similarity": len(similarity_rows)}
        with JOBS_LOCK:
            if matches:
                JOBS[job_id]["hash_matches"] = matches
                for entry in JOBS[job_id]["files"]:
                    if entry.get("sha256") in matches:
                        entry["hash_sets"] = matches[entry["sha256"]]
            if similarity_rows:
                by_path = {row["out_path"]: row for row in similarity_rows.values()
                           if row.get("out_path")}
                JOBS[job_id]["similarity"] = {
                    "hashes": {p: r["dhash"] for p, r in by_path.items() if r.get("dhash")},
                    "phashes": {p: r["phash"] for p, r in by_path.items() if r.get("phash")},
                    "membership": {p: r["group"] for p, r in by_path.items()
                                   if r.get("group")},
                    "stack_membership": {p: r["stack"] for p, r in by_path.items()
                                         if r.get("stack")},
                    "max_distance": 8, "restored": True,
                }
                sizes = {}
                for row in similarity_rows.values():
                    group = row.get("group")
                    if group:
                        sizes[group] = sizes.get(group, 0) + 1
                for entry in JOBS[job_id]["files"]:
                    row = similarity_rows.get(entry.get("sha256"))
                    if row:
                        entry["similar_group"] = row.get("group")
                        entry["similar_count"] = sizes.get(row.get("group"), 1)

    book.touch(out_dir)
    book.refresh(out_dir)
    return jsonify({"ok": True, "job_id": job_id, "out_dir": out_dir,
                    "files": len(manifest.get("files", [])),
                    "restored": restored, "case": book.get(out_dir)})


@app.route("/api/case/rebuild", methods=["POST"])
def api_case_rebuild():
    """Put a manifest back on a run that was interrupted."""
    from fme.review import recover as _recover

    out_dir = ((request.get_json(force=True) or {}).get("out_dir") or "").strip()
    if not out_dir or not os.path.isdir(out_dir):
        return error_response(FileNotFoundError(out_dir or "no folder given"),
                              status=404)
    with JOBS_LOCK:
        busy = folder_busy(out_dir)
    if busy:
        return jsonify({"ok": False,
                        "error": f"Not rebuilt: {busy}. Its list is written "
                                 f"when it finishes."}), 409
    state = _recover.unfinished(out_dir)
    if not state.get("recoverable"):
        return jsonify({"ok": False, "state": state,
                        "error": ("There is nothing to rebuild here: a run "
                                  "writes manifest.csv as it goes, and this "
                                  "folder has none.")}), 400
    result = _recover.rebuild_manifest(out_dir)
    if not result.get("ok"):
        return jsonify({"ok": False, **result}), 400
    # Was registry().remember -- a module called as a function, an error
    # swallowed every time, so a rebuilt case never reached the case list.
    try:
        _registry().refresh(out_dir)
    except Exception as exc:  # noqa: BLE001 - the rebuild is what matters
        LOG.warning("the rebuilt case could not be added to the list: %s", exc)
    return jsonify({"ok": True, **result})


@app.route("/api/case/reprocess", methods=["POST"])
def api_case_reprocess():
    """Do more to a case that is already finished, without redoing it.

    Changing one option used to mean carving a 300 GiB image again from
    the start -- hours, to answer a question that only concerns the files
    already recovered. These run over the case's own output instead.

    Nothing already recovered is removed and no decision is touched: this
    only adds. Every run is written to the audit log with why it happened,
    so a case can show what was done to it and when.
    """
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    reason = (data.get("reason") or "").strip()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    if job.get("status") == "running":
        return error_response(ValueError(
            "This case is still being processed. Let it finish first."))

    if reason not in ("rebuild-thumbs", "expand-containers"):
        return error_response(ValueError(
            "Use rebuild-thumbs or expand-containers."))

    out_dir = job["out_dir"]
    started = time.time()
    with JOBS_LOCK:
        busy = folder_busy(out_dir)
        if not busy:
            WRITING.add(out_dir)
    if busy:
        return error_response(ValueError(f"Not now: {busy}."))

    def run():
        if reason == "expand-containers":
            from fme.engines import media_core as engine

            # Appends: the manifest of a case somebody has spent a week
            # grading is never truncated, and the digests already in it are
            # read back so nothing is recovered twice.
            try:
                out = engine.OutputManager(out_dir, resume=True)
            except engine.ManifestUnreadable as exc:
                # The case is untouched; say why rather than answer with a 500.
                return error_response(exc)
            signatures = engine.active_signatures(False, False, [])
            stats = {"embedded_files": 0, "embedded_bytes": 0, "errors": 0}
            looked, found, opened = 0, 0, []

            # Every file in the case, from its manifest -- the record --
            # rather than the page's list of it, which is built from the
            # manifest but is a copy that can lag behind it.
            try:
                entries_in_case, _dups = case_records(out_dir)
            except (OSError, ValueError, KeyError):
                entries_in_case = list(job.get("files", []))
            for entry in entries_in_case:
                rel = entry.get("rel_path") or entry.get("out_path") or ""
                full = os.path.join(out_dir, rel)
                if not rel or not os.path.isfile(full):
                    continue
                try:
                    with open(full, "rb") as fh:
                        head = fh.read(4096)
                    if engine._container_kind(head) is None:
                        continue
                    looked += 1
                    with open(full, "rb") as fh:
                        blob = fh.read()
                    got = engine.scan_embedded(
                        blob, out=out, stats=stats, signatures=signatures,
                        source_label=os.path.basename(rel))
                    found += int(got or 0)
                    if got:
                        opened.append(os.path.basename(rel))
                except Exception as exc:  # noqa: BLE001 - one file, not the run
                    stats["errors"] += 1
                    LOG.warning("reprocess could not open %s: %s", rel, exc)

            # The case's own summary, carried through. close({}) rewrote
            # manifest.json with an empty one: the source image, its hashes, the
            # run's times and the program version that made the case were gone.
            try:
                with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
                    summary = dict(json.load(fh).get("summary") or {})
            except (OSError, ValueError):
                summary = {}
            summary["total_files"] = len(out.records)
            summary.setdefault("reprocessed", []).append(
                {"reason": reason, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "recovered": found})
            try:
                out.close(summary)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("reprocess could not close the output: %s", exc)

            # The page's list of the case, from the manifest just written.
            # It was left as it was, so what this recovered was in the case
            # and its reports but not in the gallery, the filters or the
            # paged query (which reads this list too) until the case was
            # closed and opened again. A new list, never edited in place:
            # the gallery query may be walking the old one.
            if found:
                try:
                    files, _dups = case_records(out_dir)
                    entries = [_job_file_entry(f) for f in files]
                    with JOBS_LOCK:
                        if job_id in JOBS:
                            JOBS[job_id]["files"] = entries
                            JOBS[job_id]["recovered"] = len(entries)
                except (OSError, ValueError, KeyError) as exc:
                    LOG.warning("reprocess could not reread the case: %s", exc)

            case = _case_for(job_id)
            if case is not None:
                # Not closed: the handle is shared, and closing it here left
                # every later call working on a closed database.
                case.log("reprocess", "", json.dumps(
                    {"reason": reason, "looked_inside": looked,
                     "recovered": found,
                     "seconds": round(time.time() - started, 1)}))

            note = ("Nothing already in the case was changed; this added what "
                    "was inside those files.")
            if looked == 0:
                note = ("This case holds no archives, documents or databases to "
                        "look inside. Containers are kept only when a run has "
                        "artifacts on; without them there is nothing here to "
                        "open.")
            return jsonify({"ok": True, "reason": reason, "looked_inside": looked,
                            "opened": opened, "recovered": found,
                            "errors": stats["errors"], "note": note})

        shutil.rmtree(os.path.join(out_dir, "thumbs"), ignore_errors=True)
        threading.Thread(target=_prefill_thumbs, args=(job_id,),
                         daemon=True).start()
        case = _case_for(job_id)
        if case is not None:
            case.log("reprocess", "", json.dumps({"reason": reason}))
        return jsonify({"ok": True, "reason": reason,
                        "note": "The gallery tiles are being made again."})

    try:
        return run()
    finally:
        with JOBS_LOCK:
            WRITING.discard(out_dir)


@app.route("/api/case")
def api_case():
    """Categories, flags and every decision made so far."""
    case = _case_for(request.args.get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    return jsonify({"ok": True, "categories": case.categories(),
                    "flags": case.flags(), "decisions": case.decisions(),
                    "counts": case.counts(), "examiner": case.examiner,
                    # The case's own: 8 in a US case, where 0 is
                    # Non-Pertinent, a finding; 0 in most other regions'.
                    "uncategorized": case.uncategorized,
                    "region": {"id": case.region, "label": case.scheme.label,
                               "nonpertinent": case.non_pertinent,
                               "notable": list(case.scheme.notable_codes),
                               "vics_mapped": sorted(case.scheme.vic_out)}})


@app.route("/api/case/categorize", methods=["POST"])
def api_case_categorize():
    data = request.get_json(force=True) or {}
    case = _case_for(data.get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    digests = data.get("sha256") or []
    if isinstance(digests, str):
        digests = [digests]
    refused = _refuse_foreign(data.get("job", ""), digests)
    if refused is not None:
        return refused
    try:
        code = int(data.get("category", 0))
        changed = case.set_category(digests, code)

        # Into the examiner's own stash as they work, so the next case
        # meets these already known (_sync_stash).
        if data.get("use_stash", True):
            _sync_stash(case, data.get("job", ""), digests)
    except (ValueError, TypeError) as exc:
        return error_response(exc)
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
        out_dir = job["out_dir"] if job else None
    # Refreshing the case list re-reads the whole manifest, and snapshotting
    # copies the case database. Doing either on every grading turned a
    # keystroke into seconds of work on a large case -- and grading is the
    # one thing that happens thousands of times. Both are now occasional.
    _housekeeping(out_dir, case)
    return jsonify({"ok": True, "changed": changed, "counts": case.counts(),
                    # What the page should patch into its own copy, so it
                    # does not have to ask for every decision in the case
                    # after every keystroke.
                    "applied": {"sha256": digests, "category": code,
                                "by": case.examiner}})


@app.route("/api/case/note", methods=["POST"])
def api_case_note():
    data = request.get_json(force=True) or {}
    case = _case_for(data.get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    refused = _refuse_foreign(data.get("job", ""), [data.get("sha256", "")])
    if refused is not None:
        return refused
    case.set_note(data.get("sha256", ""), data.get("note", ""))
    return jsonify({"ok": True})


@app.route("/api/case/masks", methods=["GET", "POST"])
def api_case_masks():
    """The regions of one picture covered when it is shown (a viewing aid)."""
    data = request.get_json(force=True) if request.method == "POST" else request.args
    case = _case_for((data or {}).get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    sha256 = (data.get("sha256") or "").strip().lower()
    try:
        if request.method == "POST":
            refused = _refuse_foreign(data.get("job", ""), [sha256])
            if refused is not None:
                return refused
            return jsonify({"ok": True, "masks": case.set_masks(sha256, data.get("masks"))})
        return jsonify({"ok": True, "masks": case.masks(sha256)})
    except ValueError as exc:
        return error_response(exc)


@app.route("/api/case/flag", methods=["POST"])
def api_case_flag():
    """Create a flag, delete one, or put one on a file."""
    data = request.get_json(force=True) or {}
    case = _case_for(data.get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    try:
        if data.get("create"):
            case.add_flag(data["create"], data.get("color", "#6b7280"),
                          (data.get("key") or "")[:1])
        elif data.get("delete"):
            case.remove_flag(int(data["delete"]))
        else:
            # One file or many: flagging three hundred at once is the same
            # action repeated, and the examiner should not have to do it
            # three hundred times.
            digests = data.get("sha256") or []
            if isinstance(digests, str):
                digests = [digests]
            refused = _refuse_foreign(data.get("job", ""), digests)
            if refused is not None:
                return refused
            for digest in digests:
                case.set_file_flag(digest, int(data.get("flag_id")),
                                   bool(data.get("on")))
    except (ValueError, TypeError, KeyError) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "flags": case.flags()})


@app.route("/api/case/category", methods=["POST"])
def api_case_category():
    """Add, rename or delete an examiner's own category."""
    data = request.get_json(force=True) or {}
    case = _case_for(data.get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    try:
        if data.get("create"):
            case.add_category(data["create"], data.get("color", "#6b7280"))
        elif data.get("rename"):
            case.rename_category(int(data["code"]), data["rename"])
        elif data.get("delete") is not None:
            case.remove_category(int(data["delete"]))
    except (ValueError, TypeError, KeyError) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "categories": case.categories(),
                    "counts": case.counts()})


@app.route("/api/case/undo", methods=["GET", "POST"])
def api_case_undo():
    """What can be taken back, and taking it back."""
    if request.method == "GET":
        case = _case_for(request.args.get("job", ""))
        if case is None:
            return error_response(KeyError("Unknown job_id"), status=404)
        return jsonify({"ok": True, "batches": case.undoable(10)})

    data = request.get_json(force=True) or {}
    case = _case_for(data.get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    try:
        result = case.undo(int(data.get("batch", 0) or 0))
    except (ValueError, sqlite3.Error) as exc:
        return error_response(exc)
    # The stash follows: an undone grade left there was applied to the next
    # case holding the file, as "your stash" (v2026.11.319).
    undone = result.pop("files", [])
    _sync_stash(case, data.get("job", ""), undone)
    # A small batch names its files, so the page can say which file went
    # back to what; a large one is a count.
    if len(undone) <= 50:
        result["files"] = list(undone)
    return jsonify({"ok": True, **result, "counts": case.counts()})


@app.route("/api/case/snapshot", methods=["POST"])
def api_case_snapshot():
    data = request.get_json(force=True) or {}
    case = _case_for(data.get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    try:
        path = case.snapshot(data.get("label", ""))
    except (OSError, sqlite3.Error) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "path": path})


@app.route("/api/case/audit")
def api_case_audit():
    case = _case_for(request.args.get("job", ""))
    if case is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    return jsonify({"ok": True, "audit": case.audit(300)})


