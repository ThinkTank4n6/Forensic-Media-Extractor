"""
routes_apps.py -- the per-file tools: faces, frames, similarity, credentials.

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


@app.route("/api/credentials/verify", methods=["POST"])
def api_credentials_verify():
    """Check whether a file's Content Credential signature holds."""
    from fme.engines import credentials_verify

    data = request.get_json(force=True) or {}
    with JOBS_LOCK:
        job = JOBS.get(data.get("job", ""))
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    # Through _job_file, as every other route that takes a file: joined
    # bare, an absolute path or ../ reached any file on the machine.
    target, refused = _job_file(data.get("job", ""), (data.get("file") or "").strip())
    if refused is not None:
        return refused

    result = credentials_verify.verify(
        target, settings.load().get("c2pa_trust_list", ""))
    return jsonify({"ok": True, **result})


@app.route("/api/faces/scan", methods=["POST"])
def api_faces_scan():
    """Find the faces in a case, so one of them can be searched for."""
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)

    kept = settings.load()
    # Settings first, then the models that ship with the program (models.py).
    detector, _, why_d = models.resolve("face_detector", kept.get("face_detector", ""))
    recognizer, _, why_r = models.resolve("face_recognizer",
                                          kept.get("face_recognizer", ""))
    if not detector or not recognizer:
        return jsonify({"ok": False, "error": why_d or why_r}), 400
    result = face_match.scan(job["out_dir"], job["files"], detector, recognizer)
    if not result.get("ok"):
        return jsonify(result), 400

    case = _case_for(job_id)
    if case is not None:
        # Under the models that found them: searched later with another
        # pair, these would be compared with numbers they do not match.
        pair = face_match.model_id(detector, recognizer)
        case.save_derived("faces_" + pair, result["by_sha256"])
        # That the case was searched at all: a scan that found no face
        # keeps no rows, and "none here" must not read as "never looked".
        case.save_derived("faces_scanned_" + pair,
                          {"scan": {"pictures": result["pictures"],
                                    "faces": result["faces"]}})
    return jsonify({"ok": True, "pictures": result["pictures"],
                    "with_faces": result["with_faces"],
                    "faces": result["faces"], "caveat": result["caveat"],
                    "lead": models.LEAD})


@app.route("/api/faces/of")
def api_faces_of():
    """The faces Find faces found in one picture, for choosing one to search.

    Until v2026.11.318 nothing in the page could start a face search: the
    toast told the examiner to use "Others like this face", and no such
    button existed.
    """
    job_id = request.args.get("job", "")
    digest = (request.args.get("sha256") or "").lower()
    with JOBS_LOCK:
        known = job_id in JOBS
    if not known:
        return error_response(KeyError("Unknown job_id"), status=404)
    kept = settings.load()
    detector, _, _ = models.resolve("face_detector", kept.get("face_detector", ""))
    recognizer, _, _ = models.resolve("face_recognizer", kept.get("face_recognizer", ""))
    case = _case_for(job_id)
    pair = face_match.model_id(detector, recognizer) if detector and recognizer else ""
    scanned = bool(case and pair and case.derived("faces_scanned_" + pair))
    library = case.derived("faces_" + pair) if scanned else {}
    faces = library.get(digest) or []
    return jsonify({"ok": True, "scanned": scanned,
                    "faces": [{"at": f.get("at"), "confidence": f.get("confidence")}
                              for f in faces]})


@app.route("/api/faces/search", methods=["POST"])
def api_faces_search():
    """Order the case by how alike each face is to the one chosen."""
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    digest = data.get("sha256", "")
    which = int(data.get("face") or 0)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)

    kept = settings.load()
    detector, _, _ = models.resolve("face_detector", kept.get("face_detector", ""))
    recognizer, _, _ = models.resolve("face_recognizer", kept.get("face_recognizer", ""))
    case = _case_for(job_id)
    library = ((case.derived("faces_" + face_match.model_id(detector, recognizer))
                or {}) if case and detector and recognizer else {})
    faces = library.get(digest) or []
    if which >= len(faces):
        return jsonify({"ok": False,
                        "error": "that face is not in this case's faces. "
                                 "Run Find faces first."}), 400

    result = face_match.search(faces[which]["vector"], library,
                               limit=int(data.get("limit") or 200))
    by_digest = {f.get("sha256"): f for f in job["files"]}
    for row in result["results"]:
        file = by_digest.get(row["sha256"]) or {}
        row["rel_path"] = file.get("rel_path", "")
        row["name"] = os.path.basename(file.get("rel_path", ""))
    result["lead"] = models.LEAD
    return jsonify(result)


@app.route("/api/content/search", methods=["POST"])
def api_content_search():
    """Order the case by what is in the pictures, against this one.

    The first search over a case describes every picture, which is the
    slow part; the descriptions are kept with the case, so later searches
    cost only the comparisons.
    """
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)

    model, _, why = models.resolve("content", settings.load().get("content_model", ""))
    if not model:
        return jsonify({"ok": False, "error": why}), 400
    ready, why = content_search.available(model)
    if not ready:
        return jsonify({"ok": False, "error": why}), 400

    # Through _job_file, as every other route that takes a file: joined
    # bare, an absolute path or ../ reached any file on the machine.
    target, refused = _job_file(data.get("job", ""), (data.get("file") or "").strip())
    if refused is not None:
        return refused

    case = _case_for(job_id)
    # Kept per model (content_search.cache_name): descriptions made by one
    # model are never ranked against another's.
    cache = content_search.cache_name(model)
    library = (case.derived(cache) or {}) if case else {}
    images = [f for f in job["files"] if f.get("category") == "image"]

    fresh = 0
    for entry in images:
        digest = entry.get("sha256", "")
        if not digest or digest in library:
            continue
        described = content_search.describe(
            os.path.join(job["out_dir"], entry.get("rel_path", "")), model)
        if described:
            library[digest] = described
            fresh += 1
    if case is not None and fresh:
        # Merged into what is saved now, not saved over it: two searches at
        # once each saved their own library and the second dropped the first's.
        case.merge_derived(cache, lambda saved: {**saved, **library})

    result = content_search.search(target, model, library,
                                   limit=int(data.get("limit") or 200))
    if not result.get("ok"):
        return jsonify(result), 400

    by_digest = {f.get("sha256"): f for f in job["files"]}
    for row in result["results"]:
        file = by_digest.get(row["sha256"]) or {}
        row["rel_path"] = file.get("rel_path", "")
        row["name"] = os.path.basename(file.get("rel_path", ""))
    result["described"] = len(library)
    result["lead"] = models.LEAD
    return jsonify(result)


@app.route("/api/content/model")
def api_content_model():
    """What a chosen image model is, and whether it can run here."""
    # Blank is the bundled model, which is what a search would use.
    path, source, _ = models.resolve(
        "content", (request.args.get("path") or "").strip())
    details = content_search.model_details(path) if path else {}
    ready, why = content_search.available(path)
    return jsonify({"ok": True, "ready": ready, "why": why,
                    "name": details.get("name", ""),
                    "licence": details.get("licence", ""),
                    "recognized": details.get("recognized", False),
                    "bundled": source == "bundled",
                    "bytes": details.get("bytes", 0)})


@app.route("/api/credentials/scan", methods=["POST"])
def api_credentials_scan():
    """Read what each file says about itself: Content Credentials (C2PA)."""
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)

    rows = [f for f in job["files"]
            if f.get("category") in ("image", "video")]
    try:
        result = credentials.scan(job["out_dir"], rows)
    except (OSError, ValueError) as exc:
        return error_response(exc)

    for entry in job["files"]:
        found = result["by_sha256"].get(entry.get("sha256"))
        if found:
            sources = found.get("source_types") or []
            entry["credentials"] = {
                "generator": found.get("claim_generator", ""),
                "source": sources[0]["means"] if sources else "",
                "signer": found.get("signer", ""),
                "ai": found.get("says_ai_generated", False),
            }

    case = _case_for(job_id)
    if case is not None:
        case.save_derived("credentials", result["by_sha256"])
    return jsonify({"ok": True, "checked": result["checked"],
                    "with_credentials": result["with_credentials"],
                    "say_ai_generated": result["say_ai_generated"],
                    "not_established": result["not_established"]})


@app.route("/api/screening/scan", methods=["POST"])
def api_screening_scan():
    """Score images by skin-tone proportion, to order a backlog.

    Explicitly not a nudity classifier: it is arithmetic over pixel colors,
    and it categorizes nothing. See fme/engines/screening.py.
    """
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    if not screening.available():
        return jsonify({"ok": False, "error": screening.UNAVAILABLE})

    try:
        result = screening.run(job["out_dir"], faces=bool(data.get("faces", True)))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        return error_response(exc)

    case = _case_for(job_id)
    scores = case.derived("screening") if case is not None else {}
    with JOBS_LOCK:
        for entry in JOBS[job_id]["files"]:
            found = scores.get(entry.get("sha256"))
            if found:
                entry["skin"] = found.get("skin")
                entry["skin_band"] = found.get("band") or ""
                entry["faces"] = found.get("faces", -1)
    return jsonify({"ok": True, **result})


@app.route("/api/frames/match", methods=["POST"])
def api_frames_match():
    """Match stills to the videos they came from.

    A picture and the video it was taken from share no bytes, so no hash
    this program computes relates them -- but a frame of that video and the
    still are the same picture, and a perceptual hash says so.
    """
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)

    state = frame_match.available()
    if not state["ready"]:
        missing = []
        if not state["hashing"]:
            missing.append("Pillow (pip install pillow)")
        if not state["frames"]:
            missing.append("OpenCV (pip install opencv-python-headless)")
        return jsonify({"ok": False,
                        "error": "This needs " + " and ".join(missing)})

    try:
        result = frame_match.analyze(
            job["out_dir"], int(data.get("distance",
                                         frame_match.DEFAULT_DISTANCE)))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        return error_response(exc)

    with JOBS_LOCK:
        JOBS[job_id]["frame_matches"] = result["matches"]
    return jsonify({"ok": True, **{k: v for k, v in result.items()
                                   if k != "matches"}})


@app.route("/api/similarity/scan", methods=["POST"])
def api_similarity_scan():
    """Group visually similar images in a finished job.

    Optional: without Pillow this reports why it cannot run rather than
    quietly finding nothing.
    """
    if not similarity.available():
        return error_response(RuntimeError(similarity.UNAVAILABLE))
    data = request.get_json(force=True) or {}
    job_id = data.get("job", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)
    manifest = os.path.join(job["out_dir"], "manifest.json")
    if not os.path.isfile(manifest):
        return error_response(FileNotFoundError("That job has no manifest"))

    try:
        max_distance = max(0, min(32, int(data.get("distance") or
                                          similarity.DEFAULT_DISTANCE)))
    except (TypeError, ValueError):
        return error_response(ValueError("distance must be a number"))

    def progress(done, total, hashed):
        with JOBS_LOCK:
            JOBS[job_id].update(done=done, total=total, recovered=hashed,
                                phase="similarity")

    # The scan borrows the job's progress fields so the page can show it;
    # they are the run's own figures and are put back afterwards. Left as
    # the scan set them, "recovered" said how many images were hashed, not
    # how many files the run recovered.
    with JOBS_LOCK:
        kept = {k: JOBS[job_id].get(k) for k in ("done", "total", "recovered",
                                                  "phase")}
    try:
        result = similarity.analyze(manifest, max_distance, progress)
    except (OSError, ValueError, RuntimeError) as exc:
        with JOBS_LOCK:
            JOBS[job_id].update(kept)
        return error_response(exc)
    with JOBS_LOCK:
        JOBS[job_id].update(kept)

    csv_path = os.path.join(job["out_dir"], "similar_groups.csv")
    similarity.write_csv(result, csv_path)

    # How many files are in each group, so a tile can stand for its whole
    # stack and say how many that is.
    group_sizes = {}
    for group in result["membership"].values():
        if group:
            group_sizes[group] = group_sizes.get(group, 0) + 1

    case = _case_for(job_id)
    if case is not None:
        # Hashing a hundred thousand images takes a quarter of an hour; doing
        # it again after every restart is a waste nobody should repeat.
        by_file = {}
        for entry in JOBS[job_id]["files"]:
            path = entry.get("rel_path")
            if path in result.get("hashes", {}):
                by_file[entry["sha256"]] = {
                    "dhash": result["hashes"][path],
                    "phash": result.get("phashes", {}).get(path, ""),
                    "group": result.get("membership", {}).get(path),
                    "stack": result.get("stack_membership", {}).get(path),
                    "out_path": path,
                }
        case.save_derived("similarity", by_file)

    with JOBS_LOCK:
        JOBS[job_id]["similarity"] = result
        for entry in JOBS[job_id]["files"]:
            entry["similar_group"] = result["membership"].get(entry["rel_path"])
            entry["similar_count"] = group_sizes.get(entry["similar_group"], 1)
    noticed = imaging.noticed()
    return jsonify({"ok": True, "csv": csv_path, "decode_notes": noticed,
                    **{k: result[k] for k in ("images", "hashed", "undecodable",
                                              "featureless", "groups",
                                              "grouped_files", "stacks",
                                              "stacked_files",
                                              "max_distance")}})


@app.route("/api/frames")
def api_frames():
    """Frames for a video the browser cannot play.

    Extracted on demand rather than for every video in a run: most are
    playable, and decoding the rest up front would spend minutes on files
    nobody opens.
    """
    job_id = request.args.get("job", "")
    rel = request.args.get("file", "")
    full, err = _job_file(job_id, rel)
    if err:
        return err
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        out_dir = job["out_dir"] if job else None
    record = next((f for f in (job or {}).get("files", [])
                   if f.get("rel_path") == rel), None)
    digest = (record or {}).get("sha256") or ""
    if not digest or not out_dir:
        return error_response(KeyError("Unknown file"), status=404)

    if not video_frames.available():
        return jsonify({"ok": False, "available": False,
                        "message": video_frames.UNAVAILABLE, "frames": []})

    result = video_frames.extract(full, out_dir, digest)
    frames = [f"/api/frame?job={job_id}&sha={digest}&n={i}"
              for i in range(len(result.get("frames", [])))]
    return jsonify({"ok": result.get("ok", False), "available": True,
                    "frames": frames, "duration": result.get("duration", 0),
                    "error": result.get("error", "")})


@app.route("/api/hex")
def api_hex():
    """The first part of a file as hex, for looking at what it really is."""
    job_id = request.args.get("job", "")
    rel = request.args.get("file", "")
    full, err = _job_file(job_id, rel)
    if err:
        return err
    try:
        offset = max(0, int(request.args.get("offset", "0")))
        length = min(64 * 1024, max(16, int(request.args.get("length", "1024"))))
    except ValueError:
        return error_response(ValueError("offset and length must be numbers"))
    try:
        size = os.path.getsize(full)
        with open(full, "rb") as fh:
            fh.seek(offset)
            blob = fh.read(length)
    except OSError as exc:
        return error_response(exc)

    lines = []
    for position in range(0, len(blob), 16):
        chunk = blob[position:position + 16]
        hexed = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset + position:08x}  {hexed:<47}  {text}")
    return jsonify({"ok": True, "size": size, "offset": offset,
                    "length": len(blob), "lines": lines})


