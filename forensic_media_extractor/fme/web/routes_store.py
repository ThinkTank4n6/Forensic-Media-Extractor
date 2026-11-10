"""
routes_store.py -- the hash store and hash set endpoints.

Split out of media_gui.py, which held eighty-five routes, the page, the
stylesheet and the script in one file where an edit to any of them could
break the rest.

The route bodies are unchanged. What they lean on -- the Flask app, the
job table, the helpers -- still lives in media_gui and is bound here when
this module is imported, at the end of that file, so the names resolve
exactly as they did when the routes sat inside it.
"""

from fme.web import media_gui as _core

# Everything the moved routes referred to when they lived in media_gui.
# Bound by name rather than rewritten, because a refactor that also
# edits the code it moves cannot be checked by comparing behaviour.
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

# Three values media_gui rebinds at run time, read through the module
# rather than from the copy taken at import.
#
# These route files copy media_gui's namespace when they load. That is
# right for everything shared by reference -- the Flask app, the job
# table -- and wrong for anything rebound later: the copy keeps the
# value it had at import. OUT_ROOT is rebound whenever an examiner
# chooses a results folder, so this route handed back the old folder
# and the field in the page snapped straight back to it.



@app.route("/api/hashsets", methods=["GET", "POST"])
def api_hashsets():
    """List the loaded hash sets, or load another."""
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        path = (data.get("path") or "").strip()
        if not path:
            return error_response(ValueError("No hash set path given"))
        if not os.path.isfile(path):
            return error_response(FileNotFoundError(f"No such file: {path}"))
        try:
            loaded = hashsets.load(path, (data.get("kind") or "").strip(),
                                   (data.get("name") or "").strip())
        except Exception as exc:  # noqa: BLE001 - reported to the user
            return error_response(exc)
        if not loaded.size:
            return error_response(ValueError(
                f"{os.path.basename(path)} produced no hashes -- is it the "
                f"format you expected?"))
        with HASH_LOCK:
            HASH_SETS[:] = [s for s in HASH_SETS if s.path != loaded.path]
            HASH_SETS.append(loaded)
    with HASH_LOCK:
        return jsonify({"ok": True, "sets": [s.summary() for s in HASH_SETS]})


@app.route("/api/hashsets/remove", methods=["POST"])
def api_hashsets_remove():
    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    with HASH_LOCK:
        HASH_SETS[:] = [s for s in HASH_SETS if s.path != path]
        return jsonify({"ok": True, "sets": [s.summary() for s in HASH_SETS]})


@app.route("/api/store/forget", methods=["POST"])
def api_store_forget():
    """Stop this machine remembering the token, or everything."""
    data = request.get_json(force=True) or {}
    if data.get("all"):
        settings.forget()
        STORE_TOKEN["token"] = ""
        STORE_TOKEN["cert"] = ""
    else:
        settings.forget_token()
        STORE_TOKEN["token"] = ""
    return jsonify({"ok": True})


@app.route("/api/store/token", methods=["POST"])
def api_store_token():
    """The token for a shared store. Held in memory for this session only.

    Not written to disk: a token is the holder's identity on a store full of
    this material, and a file on an examination machine is the wrong place
    for it.
    """
    data = request.get_json(force=True) or {}
    STORE_TOKEN["token"] = (data.get("token") or "").strip()
    cert = (data.get("cert") or "").strip()
    if cert and not os.path.isfile(cert):
        return jsonify({"ok": False,
                        "error": f"No certificate file at {cert}. This is the "
                                 f"server.crt your administrator gave you; "
                                 f"copy it somewhere permanent and give that "
                                 f"path."})
    STORE_TOKEN["cert"] = cert
    remember = bool(data.get("remember"))
    try:
        settings.save({"store_token": STORE_TOKEN["token"],
                       "store_cert": cert}, remember_token=remember)
    except OSError:
        pass
    return jsonify({"ok": True, "set": bool(STORE_TOKEN["token"]),
                    "cert": bool(cert), "remembered": remember})


@app.route("/api/store", methods=["GET", "POST"])
def api_store():
    """What is in the cross-case store, or point at a different one."""
    try:
        path = ""
        if request.method == "POST":
            path = ((request.get_json(force=True) or {}).get("path") or "").strip()
        # Asking what the store is (GET) must answer even when there is
        # none yet: "no store yet" is a state, not a failure, and the tab
        # has to be able to show it. Choosing one (POST) is a deliberate
        # act, and a path with nothing at it is refused there.
        if request.method == "GET" and not path:
            where = _core.STORE_PATH
            if (not remotestore.looks_remote(where)
                    and not os.path.isfile(where)):
                return jsonify({"ok": True, "path": where, "total": 0,
                                "sources": [], "none_yet": True,
                                "warning": _store_warning(where)})
        store = _store(path, create=False)
        if path:
            # It opened, so it is worth remembering. The token is saved only
            # if the examiner asked for it, which is handled separately.
            try:
                settings.save({"store_path": store.path,
                               "store_cert": STORE_TOKEN.get("cert", "")})
            except OSError:
                pass
        return jsonify({"ok": True, "path": store.path,
                        "warning": _store_warning(store.path),
                        "total": store.size(),
                        "sources": store.sources()})
    except (OSError, sqlite3.Error, ValueError) as exc:
        return error_response(exc)


@app.route("/api/store/import", methods=["POST"])
def api_store_import():
    """Read a hash set into the store, once, for every case from now on."""
    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return error_response(ValueError("Name the hash set file to import."))
    try:
        store = _store()
        # A shared store imports from a path the server can see -- a share
        # on the server, usually not a file on this machine at all. Checking
        # it here refused every remote import of a set this machine did not
        # also hold, at the same path, before the server was ever asked.
        if not getattr(store, "remote", False) and not os.path.isfile(path):
            return error_response(FileNotFoundError(f"No such file: {path}"))
        result = store.import_set(path, (data.get("kind") or "").strip(),
                                  (data.get("name") or "").strip())
    except (OSError, ValueError, sqlite3.Error,
            remotestore.RemoteStoreError) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "imported": result, "total": store.size(),
                    "sources": store.sources()})


@app.route("/api/store/verify")
def api_store_verify():
    """What the store holds, asked of the store itself rather than remembered.

    For a file that means opening it read-only, as a colleague or a copy on
    a share would see it. For a server it means asking the server, which is
    the equivalent question and the one worth asking: it confirms the
    address, the token and the certificate all work before a case depends
    on them.
    """
    try:
        store = _store()
        if getattr(store, "remote", False):
            who = store.whoami()
            sources = store.sources()
            # A unit runs one server and many copies of this program, and
            # the server is the one nobody restarts. Verify is where an
            # examiner looks when something is wrong, so it says.
            version = store.server_is_current()
            return jsonify({"ok": True, "report": {
                "remote": True,
                "path": store.url,
                "exists": True,
                "total": store.size(),
                "sources": sources,
                "by_algorithm": {},
                "size": 0,
                "who": who.get("name", ""),
                "role": who.get("role", ""),
                "may": who.get("may", []),
                "server_version": version.get("running", ""),
                "server_current": version.get("current", True),
                "server_note": version.get("why", ""),
            }})
        return jsonify({"ok": True, "report": hashstore.verify(store.path)})
    except remotestore.RemoteStoreError as exc:
        return jsonify({"ok": True, "report": {"error": str(exc)}})
    except (OSError, sqlite3.Error, ValueError) as exc:
        return error_response(exc)


@app.route("/api/store/inspect", methods=["POST"])
def api_store_inspect():
    """What a hash set file is, without importing it."""
    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    if not os.path.isfile(path):
        return error_response(FileNotFoundError(f"No such file: {path}"))
    try:
        return jsonify({"ok": True, "report": hashstore.inspect_set(path)})
    except (OSError, ValueError) as exc:
        return error_response(exc)


@app.route("/api/store/remove", methods=["POST"])
def api_store_remove():
    data = request.get_json(force=True) or {}
    try:
        store = _store()
        removed = store.remove_source((data.get("name") or "").strip())
    except (ValueError, sqlite3.Error) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "removed": removed, "total": store.size(),
                    "sources": store.sources()})


@app.route("/api/store/scan", methods=["POST"])
def api_store_scan():
    """Check a finished run against everything in the store."""
    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
    if job is None:
        return error_response(ValueError(
            "There is no finished run to check. Run an extraction first."))
    manifest = os.path.join(job["out_dir"], "manifest.json")
    if not os.path.isfile(manifest):
        return error_response(FileNotFoundError("That job has no manifest"))
    try:
        store = _store()
        result = store.scan_manifest(manifest)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return error_response(exc)

    # The examiner's own stash, checked in the same pass. It holds what
    # they decided in earlier cases, which is the whole reason it exists:
    # filling it as they work and never consulting it means grading the
    # same file twice. Where it disagrees with the shared store the
    # disagreement is reported and neither category is applied.
    stash_found, stash_clash = {}, []
    if (data.get("use_stash", True)
            and settings.load().get("stash_on", "1") != "0"):
        try:
            from fme.review import stash as _stash

            store_said = {}
            for row in result["rows"]:
                digest = (row.get("record") or {}).get("sha256", "")
                for match in row.get("matches") or []:
                    if match.get("category") is not None:
                        store_said[digest] = dict(
                            match, region=match.get("region") or "US")
                        break
            checked = _stash.check(
                [{"sha256": (row.get("record") or {}).get("sha256", ""),
                  "md5": (row.get("record") or {}).get("md5", "")}
                 for row in result["rows"]],
                settings.load().get("stash_path", ""), store_said)
            stash_found = {row["sha256"]: row for row in checked["matched"]
                           if row.get("sha256")}
            stash_clash = checked["disagreements"]
        except Exception as exc:  # noqa: BLE001
            LOG.warning("the stash could not be checked: %s", exc)

    csv_path = os.path.join(job["out_dir"], "hash_store_matches.csv")
    hashsets.write_csv(
        {"rows": [{"record": r["record"],
                   "matches": [{"set": m["source"], "kind": m["kind"],
                                "note": m["note"] or "",
                                "record": m.get("record") or {}}
                               for m in r["matches"]]}
                  for r in result["rows"]]}, csv_path)

    lookup = {}
    for row in result["rows"]:
        digest = row["record"].get("sha256")
        if digest and row["matches"]:
            lookup.setdefault(digest, [
                {"set": m["source"], "kind": m["kind"],
                 # Carried so a set that asserts a category can be acted on,
                 # not only displayed.
                 "category": m.get("category"),
                 "note": " · ".join(x for x in (
                     m["note"] or (f"category {m['category']}"
                                   if m["category"] is not None else ""),
                     f"matched on {m['matched_on']}") if x),
                 # Everything the set recorded about this file, so a match
                 # can be acted on rather than only noticed.
                 "record": m.get("record") or {},
                 # The scheme the store's bare codes are in. Dropped here
                 # before v2026.11.318, so every store match read as US.
                 # A server from before regions sends none: a US store.
                 "region": m.get("region") or "US",
                 "via": "store"}
                for m in row["matches"]])

    # The examiner's own earlier decisions, alongside the unit's. Added
    # here so one file carries both and each says which it came from -- a
    # match with no source is how somebody comes to rely on a category
    # nobody chose.
    for digest, entry in stash_found.items():
        if not digest:
            continue
        lookup.setdefault(digest, []).append({
            "set": "your stash", "kind": "stash",
            "category": entry.get("category"),
            "note": "you graded this in an earlier case",
            # None for a row from before v2026.11.318: never applied.
            "region": entry.get("region"),
            "record": {}, "via": "stash"})

    case = _case_for(data.get("job", ""))
    if case is not None:
        # Same as the per-session sets: a match made now should still be
        # there tomorrow, and in a report written next week. Merged by
        # origin: this replaces the store's and the stash's earlier matches
        # and keeps the session sets'. A dict update replaced each file's
        # whole list, session matches included.
        case.merge_derived("hash_matches", lambda saved: hashsets.merge_matches(
            saved, lookup, {"store", "stash"}))

    with JOBS_LOCK:
        merged = hashsets.merge_matches(
            JOBS[data["job"]].get("hash_matches") or {}, lookup,
            {"store", "stash"})
        JOBS[data["job"]]["hash_matches"] = merged
        for entry in JOBS[data["job"]]["files"]:
            entry["hash_sets"] = merged.get(entry.get("sha256"), [])
    # A file the stash and the store grade differently is reported as a
    # disagreement "neither has been applied" -- and was then auto-graded
    # anyway, by whichever code sorted first.
    applied = _apply_hash_categories(
        data["job"], lookup, hold=[c.get("sha256", "") for c in stash_clash]) \
        if data.get("autocategorize", True) else {"categorized": 0}
    return jsonify({"ok": True, "csv": csv_path, "checked": result["checked"],
                    "locations": result.get("locations", result["checked"]),
                    "matched": result["matched"],
                    "unmatched": result["unmatched"],
                    "by_source": result["by_source"], "applied": applied,
                    "your_stash": len(stash_found),
                    "stash_disagreements": stash_clash})


@app.route("/api/store/contribute", methods=["POST"])
def api_store_contribute():
    """Put this case's graded files into the store for the next one."""
    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
    if job is None:
        return error_response(ValueError("There is no finished run to add."))
    name = (data.get("source") or job.get("case_number") or "").strip()
    if not name:
        return error_response(ValueError(
            "Name what is being added -- usually the case number -- so a "
            "later match says where the decision came from."))
    try:
        store = _store()
        result = store.contribute(job["out_dir"], name,
                                  note=(data.get("note") or "").strip())
    except (OSError, ValueError, sqlite3.Error,
            remotestore.RemoteStoreError) as exc:
        # A shared store refuses a case another examiner owns (403); that
        # message is the examiner's to read, not a server error.
        return error_response(exc)
    return jsonify({"ok": True, "added": result, "total": store.size(),
                    "sources": store.sources()})


@app.route("/api/hashsets/scan", methods=["POST"])
def api_hashsets_scan():
    """Check a finished job's files against every loaded set."""
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(ValueError(
            "There is no finished run to check. Run an extraction on the "
            "Extract Media tab first -- the sets you have loaded stay loaded."))
    manifest = os.path.join(job["out_dir"], "manifest.json")
    if not os.path.isfile(manifest):
        return error_response(FileNotFoundError("That job has no manifest"))
    with HASH_LOCK:
        sets = list(HASH_SETS)
    if not sets:
        return error_response(ValueError("No hash sets are loaded"))

    result = hashsets.scan_manifest(manifest, sets)
    csv_path = os.path.join(job["out_dir"], "hash_matches.csv")
    hashsets.write_csv(result, csv_path)

    # Keep the per-file answer so the details pane can show it without
    # rescanning hundreds of megabytes of hashes.
    lookup = {}
    for row in result["rows"]:
        digest = row["record"].get("sha256")
        if digest and row["matches"]:
            lookup.setdefault(digest, row["matches"])
    case = _case_for(job_id)
    if case is not None:
        # Merged, not saved over: this list was written in place of the
        # case's whole saved list, so checking against a set loaded for the
        # session erased every match the store had made, Project VIC detail
        # and all. It replaces the session sets' own earlier matches only.
        case.merge_derived("hash_matches", lambda saved: hashsets.merge_matches(
            saved, lookup, {"session"}))

    with JOBS_LOCK:
        merged = hashsets.merge_matches(
            JOBS[job_id].get("hash_matches") or {}, lookup, {"session"})
        JOBS[job_id]["hash_matches"] = merged
        for entry in JOBS[job_id]["files"]:
            entry["hash_sets"] = merged.get(entry.get("sha256"), [])
    applied = _apply_hash_categories(job_id, lookup) \
        if data.get("autocategorize", True) else {"categorized": 0}
    return jsonify({"ok": True, "csv": csv_path,
                    "checked": result["checked"], "matched": result["matched"],
                    "unmatched": result["unmatched"], "by_set": result["by_set"],
                    "applied": applied})


