"""
routes_report.py -- reports and exports.

Split out of media_gui.py. The bodies are unchanged; what they lean on is
bound from media_gui when this module is imported at the end of that
file, so the names resolve exactly as they did inside it.
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


@app.route("/api/export", methods=["POST"])
def api_export():
    """Write a C4All-style report set for a finished job, so the results can
    be loaded in Griffeye or the C4All viewer. manifest.json is a working
    record, not an interchange format."""
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    if job["status"] not in ("done", "canceled"):
        return error_response(ValueError("That job has not finished yet"))

    case = (data.get("case") or "").strip()
    if not case:
        return error_response(ValueError("A case number is required"))
    if any(ch in case for ch in '\\/:*?"<>|'):
        return error_response(ValueError(
            "The case number becomes a folder name, so it cannot contain "
            r'\ / : * ? " < > |'))

    manifest = os.path.join(job["out_dir"], "manifest.json")
    if not os.path.isfile(manifest):
        return error_response(FileNotFoundError("That job has no manifest"))

    media = data.get("media", "dvd")
    try:
        media_bytes = c4all_export.PRESETS.get(str(media).lower()) \
            or c4all_export.parse_size(media)
    except ValueError:
        return error_response(ValueError(f"Unrecognized disc size: {media}"))

    out_dir = os.path.join(job["out_dir"], "export")
    # One export per case at a time. Two at once (a double click, two tabs)
    # wrote the same index files and placed the same batch names together,
    # and the summary each returned described a folder the other was still
    # changing. The flag lives in the job, under the lock that guards it.
    with JOBS_LOCK:
        if JOBS[job_id].get("exporting"):
            return error_response(ValueError(
                "An export of this case is already being written. Wait for "
                "it to finish, then export again if needed."), status=409)
        JOBS[job_id]["exporting"] = True
    try:
        result = c4all_export.export(
            manifest, out_dir, case=case,
            analyst=(data.get("analyst") or "").strip(),
            comment=(data.get("comment") or "").strip(),
            evidence=(data.get("evidence") or "").strip(),
            mode="copy" if data.get("copy") else "hardlink",
            media_bytes=media_bytes,
            all_as_movies=bool(data.get("all_as_movies")),
            project_vic=bool(data.get("project_vic")),
            organization=(data.get("organization") or "").strip(),
            vic_media_id=("zero" if data.get("vic_media_id") == "zero"
                          else "sequential"),
            vic_files_layout=("hash" if data.get("vic_files_layout") == "hash"
                              else "inplace"),
            tool=f"media_core {CORE_VERSION} / c4all_export "
                 f"v{c4all_export.__version__}")
    except (OSError, ValueError) as exc:
        return error_response(exc)
    finally:
        with JOBS_LOCK:
            JOBS.get(job_id, {}).pop("exporting", None)

    with JOBS_LOCK:
        JOBS[job_id]["export"] = result
    return jsonify({"ok": True, "export": result})


@app.route("/api/export/lava", methods=["POST"])
def api_export_lava():
    """Write a LAVA output set for a finished job."""
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    if job["status"] not in ("done", "canceled"):
        return error_response(ValueError("That job has not finished yet"))
    manifest = os.path.join(job["out_dir"], "manifest.json")
    if not os.path.isfile(manifest):
        return error_response(FileNotFoundError("That job has no manifest"))
    try:
        result = lava_export.export(
            manifest, os.path.join(job["out_dir"], "lava"),
            case_number=(data.get("case") or "").strip(),
            examiner=(data.get("examiner") or "").strip(),
            evidence=(data.get("evidence") or "").strip(),
            absolute_media=bool(data.get("absolute_media")),
            tool_name="media_core", tool_version=CORE_VERSION)
        check = lava_export.validate(result["output_dir"])
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)
    return jsonify({"ok": True, "lava": result, "valid": check["ok"],
                    "problems": check["problems"]})


@app.route("/api/export/lava/open", methods=["POST"])
def api_export_lava_open():
    """Open a written LAVA project in a locally installed LAVA.

    LAVA is handed the .lava project file, not the folder -- the same thing
    iLEAPP's GUI does.
    """
    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    project = lava_launch.project_file(os.path.join(job["out_dir"], "lava"))
    if not os.path.isfile(project):
        return error_response(FileNotFoundError(
            "No LAVA output set for this job yet -- write one first"))
    try:
        kind = lava_launch.open_project(project)
    except (RuntimeError, OSError, ValueError) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "opened": project, "via": kind})


@app.route("/api/export/file")
def api_export_file():
    """Download one of the generated report files."""
    job_id = request.args.get("job", "")
    name = request.args.get("name", "")
    if name not in ("C4M_Index.xml", "C4P_Index.xml", "Case_Report.xml",
                    "Victim_Report.xml", "Results.txt", "ProjectVIC.json"):
        return error_response(ValueError("Unknown report file"))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    path = os.path.join(job["out_dir"], "export", name)
    if not os.path.isfile(path):
        return error_response(FileNotFoundError(f"{name} has not been generated"))
    return send_file(path, as_attachment=True)


@app.route("/api/report/status")
def api_report_status():
    """How a report is getting on.

    Writing a report for a large case takes minutes, almost all of it
    thumbnails. Doing that inside the request meant the browser sat on
    "Writing…" with no way to tell a working report from a stuck one, and no
    way to stop it.
    """
    with REPORTS_LOCK:
        job = REPORTS.get(request.args.get("id", ""))
    if job is None:
        return error_response(KeyError("Unknown report"), status=404)
    return jsonify({"ok": True, **{k: v for k, v in job.items()
                                   if k != "_cancel"}})


@app.route("/api/report/cancel", methods=["POST"])
def api_report_cancel():
    data = request.get_json(force=True) or {}
    with REPORTS_LOCK:
        job = REPORTS.get(data.get("id", ""))
    if job is None:
        return error_response(KeyError("Unknown report"), status=404)
    job["_cancel"].set()
    return jsonify({"ok": True})


@app.route("/api/export/files", methods=["POST"])
def api_export_files():
    """Copy the files themselves out, by category.

    Separate from the report on purpose: handing somebody the material is a
    different act from describing it, and an examiner should not have to
    write a report to do it.
    """
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)

    dest = (data.get("dest") or "").strip()
    if not dest:
        return error_response(ValueError("Say where the files should go."))

    try:
        rows = report.gather(
            job["out_dir"],
            categories=data.get("categories") or None,
            sha256=data.get("sha256") or None,
            include_uncategorized=bool(data.get("include_uncategorized", False)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return error_response(exc)

    maker = None
    if data.get("thumbnails"):
        def maker(row, blur=bool(data.get("blur"))):
            picture = report._docx_thumbnail(row, rows, blur=blur)
            return picture["bytes"] if picture else None

    try:
        result = export_files.export(
            rows["rows"], rows["out_dir"], dest, thumbnails=maker,
            by_category=bool(data.get("by_category", True)),
            by_kind=bool(data.get("by_kind", True)))
    except (OSError, ValueError) as exc:
        return error_response(exc)
    return jsonify({"ok": True, **result})


@app.route("/api/report/build", methods=["POST"])
def api_report_build():
    """Write a report over a chosen scope, in the formats asked for."""
    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
    if job is None:
        return error_response(ValueError("There is no run to report on."))
    dest = (data.get("dest") or "").strip() or os.path.join(
        job["out_dir"], "report")

    report_id = uuid.uuid4().hex
    cancel = threading.Event()
    with REPORTS_LOCK:
        REPORTS[report_id] = {"status": "running", "done": 0, "total": 0,
                              "phase": "starting", "written": [],
                              "error": "", "_cancel": cancel}

    def note(done, total):
        with REPORTS_LOCK:
            entry = REPORTS[report_id]
            entry["done"] = done
            entry["total"] = total
            entry["phase"] = ("making thumbnails" if total and done < total / 2
                              else "writing pages")

    def run():
        try:
            result = _build_report(data, job, dest, note, cancel.is_set)
        except report.Canceled:
            with REPORTS_LOCK:
                REPORTS[report_id].update(status="canceled",
                                          phase="canceled by you")
            return
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            with REPORTS_LOCK:
                REPORTS[report_id].update(status="error", error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the thread's last word
            # Anything else -- a TypeError from bad input, sqlite3.Error, a
            # decoder's own exception -- ended the thread and left the report
            # "running" for ever, with nothing on the page to say why.
            LOG.exception("report %s failed", report_id)
            with REPORTS_LOCK:
                REPORTS[report_id].update(
                    status="error", error=f"{type(exc).__name__}: {exc}")
            return
        with REPORTS_LOCK:
            REPORTS[report_id].update(status="done", phase="finished", **result)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "id": report_id})


@app.route("/api/report/fields")
def api_report_fields():
    return jsonify({"ok": True,
                    "fields": [{"key": k, "label": label} for k, label in report.FIELDS],
                    "default": report.DEFAULT_FIELDS})


@app.route("/api/report/html", methods=["POST"])
def api_report_html():
    """The contact sheet and the MD5 list."""
    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    manifest = os.path.join(job["out_dir"], "manifest.json")
    if not os.path.isfile(manifest):
        return error_response(FileNotFoundError("That job has no manifest"))
    try:
        written = []
        if data.get("html", True):
            result = report_html.export(
                manifest, os.path.join(job["out_dir"], "report.html"),
                embed=bool(data.get("embed", True)),
                timezone=(data.get("timezone") or "").strip())
            written.append({"path": result["path"], "bytes": result["bytes"],
                            "embedded": result["embedded"]})
        if data.get("md5", True):
            listed = report_html.write_md5_list(
                manifest, os.path.join(job["out_dir"], "md5_list.csv"))
            written.append({"path": listed["path"], "hashes": listed["hashes"]})
    except (OSError, ValueError) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "written": written})


# Formats a browser will not draw but Pillow can, so the gallery can show
# them as pictures rather than as grey rectangles.




@app.route("/api/export/md5", methods=["POST"])
def api_export_md5():
    """An MD5 list of chosen files, or of the whole run."""
    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    wanted = set(data.get("sha256") or [])
    # From the case's own records, not the page's file list, which is a copy
    # of them that can lag behind. (v2026.11.310 said that list was empty
    # past PAGED_THRESHOLD; only the status answer is, the server's is not.)
    # Written by the report's own MD5 writer, so the two lists are the same
    # format and both are safe to open in Excel.
    try:
        files, _duplicates = case_records(job["out_dir"])
    except (OSError, ValueError) as exc:
        return error_response(exc)
    rows = [f for f in files if not wanted or f.get("sha256") in wanted]
    if not rows:
        return error_response(ValueError("No files to export"))

    out = io.StringIO()
    report_html.write_md5_rows(out, {"files": rows})
    body = out.getvalue()
    return Response(body, mimetype="text/csv", headers={
        "Content-Disposition": 'attachment; filename="md5_list.csv"'})


