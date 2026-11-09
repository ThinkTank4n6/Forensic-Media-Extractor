"""
routes_setup.py -- the things set up once: signatures, the scheme, settings, the agency logo, the personal stash and C4All.

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


@app.route("/api/signature/list")
def api_signature_list():
    with SIG_LOCK:
        return jsonify({"ok": True, "signatures": list(CUSTOM_SIGNATURES)})


@app.route("/api/signature/save", methods=["POST"])
def api_signature_save():
    """Validate and store one signature. Validation is done by building the
    real object, so bad hex or a missing extension fails here rather than
    halfway through a carve."""
    data = request.get_json(force=True) or {}
    try:
        max_size = int(data.get("max_size") or 64 * 1024 * 1024)
        sig = _build_signature(data.get("ext", ""), data.get("header_hex", ""),
                               data.get("footer_hex", ""), max_size)
    except (ValueError, TypeError) as exc:
        return error_response(exc)

    record = {
        "id": uuid.uuid4().hex,
        "ext": sig.ext,
        "header_hex": sig.header.hex(),
        "header_ascii": _printable(sig.header),
        "footer_hex": sig.footer.hex() if sig.footer else "",
        "footer_ascii": _printable(sig.footer) if sig.footer else "",
        "max_size": sig.max_size,
        "max_size_human": media_core.human(sig.max_size),
    }
    with SIG_LOCK:
        CUSTOM_SIGNATURES.append(record)
        return jsonify({"ok": True, "signatures": list(CUSTOM_SIGNATURES)})


@app.route("/api/signature/delete", methods=["POST"])
def api_signature_delete():
    data = request.get_json(force=True) or {}
    sig_id = data.get("id", "")
    with SIG_LOCK:
        CUSTOM_SIGNATURES[:] = [s for s in CUSTOM_SIGNATURES if s["id"] != sig_id]
        return jsonify({"ok": True, "signatures": list(CUSTOM_SIGNATURES)})


@app.route("/api/signature/analyze", methods=["POST"])
def api_signature_analyze():
    """Derive header/footer bytes from one or more sample files, so a
    signature can be built from an example rather than by hand."""
    data = request.get_json(force=True) or {}
    paths = [p for p in (data.get("paths") or []) if p]
    if not paths:
        return error_response(ValueError("Provide at least one sample file"))
    scan_len = max(1, min(int(data.get("scan_len") or 16), 64))

    if fat32_core is not None and hasattr(fat32_core, "analyze_samples"):
        try:
            return jsonify({"ok": True, **fat32_core.analyze_samples(paths, scan_len)})
        except Exception as exc:  # noqa: BLE001
            return error_response(exc)

    samples, heads, feet = [], [], []
    for path in paths:
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                head = fh.read(scan_len)
                if size > scan_len:
                    fh.seek(max(0, size - scan_len))
                    foot = fh.read(scan_len)
                else:
                    foot = head
        except OSError as exc:
            samples.append({"path": path, "error": str(exc)})
            continue
        heads.append(head)
        feet.append(foot)
        samples.append({"path": path, "size": size, "error": None,
                        "header_hex": head.hex(" "), "header_ascii": _printable(head),
                        "footer_hex": foot.hex(" "), "footer_ascii": _printable(foot)})
    common_head = _common_prefix(heads)
    common_foot = _common_suffix(feet)
    return jsonify({"ok": True, "samples": samples,
                    "sample_count": len(heads),
                    "common_header_hex": common_head.hex(),
                    "common_footer_hex": common_foot.hex()})


@app.route("/api/logo", methods=["GET", "POST"])
def api_logo():
    """The agency logo put on report headers."""
    if request.method == "GET":
        path = settings.load().get("agency_logo", "")
        details = {"path": path if path and os.path.isfile(path) else ""}
        if details["path"]:
            try:
                from fme.engines import imaging
                with imaging.open_image(details["path"]) as picture:
                    details["width"], details["height"] = picture.size
            except Exception:  # noqa: BLE001 - a size is a nicety
                pass
        return jsonify({"ok": True, **details})

    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    if path:
        if not os.path.isfile(path):
            return error_response(FileNotFoundError(path), status=404)
        # Refuse anything that will not open: a report header is a poor
        # place to discover that a file was not an image.
        try:
            from fme.engines import imaging
            with imaging.open_image(path) as picture:
                picture.load()
        except Exception as exc:  # noqa: BLE001
            return error_response(
                ValueError(f"that file did not open as an image: {exc}"))
    try:
        settings.save({"agency_logo": path})
    except OSError as exc:
        return error_response(exc)
    return jsonify({"ok": True, "path": path})


@app.route("/api/logo/image")
def api_logo_image():
    """The logo itself, for the dialog's preview."""
    path = settings.load().get("agency_logo", "")
    if not path or not os.path.isfile(path):
        return error_response(FileNotFoundError("no logo set"), status=404)
    return send_file(path)


@app.route("/api/c4all/inspect", methods=["POST"])
def api_c4all_inspect():
    """What a C4All index holds, before anything is taken from it."""
    from fme.exports import c4all_import

    path = ((request.get_json(force=True) or {}).get("path") or "").strip()
    if not path or not os.path.isfile(path):
        return error_response(FileNotFoundError(path or "no path given"),
                              status=404)
    if not c4all_import.looks_like_index(path):
        return error_response(ValueError(
            "That is not a C4All index. C4All writes \"C4P Index.xml\" for "
            "pictures and \"C4M Index.xml\" for movies, beside the files it "
            "exported."))
    try:
        return jsonify({"ok": True, **c4all_import.summarize(path)})
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)


@app.route("/api/c4all/import", methods=["POST"])
def api_c4all_import():
    """Bring a C4All index's gradings into this case, by hash."""
    from fme.exports import c4all_import

    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    path = (data.get("path") or "").strip()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    if not os.path.isfile(path):
        return error_response(FileNotFoundError(path), status=404)

    case = _case_for(job_id)
    if case is None:
        return error_response(ValueError("This case has no decisions file."))
    try:
        result = c4all_import.apply_to_case(
            case, job.get("files", []), path,
            overwrite=bool(data.get("overwrite")),
            index_region=data.get("index_region"))
    except ValueError as exc:
        return error_response(exc)
    case.log("import_c4all", "", json.dumps(
        {k: v for k, v in result.items() if k != "note"}))
    return jsonify({"ok": True, **result})


@app.route("/api/stash")
def api_stash():
    """What this examiner's own stash holds."""
    from fme.review import stash as _stash

    own = _stash.Stash(settings.load().get("stash_path", ""))
    try:
        return jsonify({
            "ok": True, "hashes": own.size(),
            "by_category": own.by_category(), "path": own.path,
            "note": ("Your own gradings from earlier cases, kept on this "
                     "machine. A match is what you decided before, not what "
                     "the unit has agreed."),
        })
    finally:
        own.close()


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """What this machine remembers about its examiner and their defaults.

    The store's address and token are handled separately, where they are
    used; these are the things typed into every case header.
    """
    if request.method == "GET":
        kept = settings.load()
        return jsonify({"ok": True, "where": settings.path(),
                        **{key: kept.get(key, "")
                           for key in ("examiner", "agency", "output_root",
                                       "timezone", "case_number",
                                       "content_model", "face_detector",
                                       "face_recognizer", "c2pa_trust_list")},
                        "category_region": regions.normalize(
                            kept.get("category_region")),
                        "token_saved": bool(kept.get("store_token"))})

    data = request.get_json(force=True) or {}
    if "category_region" in data and not regions.is_region(data["category_region"]):
        return error_response(ValueError(
            f"No such region: {data['category_region']}"))
    try:
        # Only what the request sent: a key it left out was saved as blank,
        # so a page that posted two fields erased the other six.
        values = {key: (data.get(key) or "").strip()
                  for key in ("examiner", "agency", "output_root", "timezone",
                              "content_model", "face_detector",
                              "face_recognizer", "c2pa_trust_list")
                  if key in data}
        if "category_region" in data:
            values["category_region"] = regions.normalize(data["category_region"])
        settings.save(values)
    except OSError as exc:
        return error_response(exc)
    return jsonify({"ok": True})


@app.route("/api/regions")
def api_regions():
    """The regional grading schemes a new case can be created under."""
    return jsonify({"ok": True, "regions": regions.choices(),
                    "chosen": regions.normalize(
                        settings.load().get("category_region"))})


@app.route("/api/scheme", methods=["GET", "POST"])
def api_scheme():
    """The examiner's own categories and flags, kept between cases."""
    path = os.path.join(BASE_DIR, savedset.DEFAULT_NAME)
    if request.method == "GET":
        scheme = savedset.load(path)
        return jsonify({"ok": True, "path": path, "scheme": scheme,
                        "saved": bool(scheme)})

    data = request.get_json(force=True) or {}
    action = (data.get("action") or "").strip()
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
    if job is None:
        return error_response(ValueError("Open or run a case first."))
    try:
        if action == "save":
            scheme = savedset.save(job["out_dir"], path)
            return jsonify({"ok": True, "saved": True,
                            "categories": len(scheme["categories"]),
                            "flags": len(scheme["flags"]), "path": path})
        if action == "apply":
            return jsonify({"ok": True, **savedset.apply(job["out_dir"], path)})
    except (OSError, ValueError, sqlite3.Error) as exc:
        return error_response(exc)
    return error_response(ValueError("action must be save or apply"))


