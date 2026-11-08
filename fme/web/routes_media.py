"""
routes_media.py -- serving a file, its thumbnail, a frame and its details to the page.

Split out of media_gui.py, which held eighty-five routes, the page, the
stylesheet and the script in one file. The bodies are unchanged; what
they lean on is bound from media_gui when this module is imported at
the end of that file, so every name resolves as it did inside it.
"""

from fme.web import media_gui as _core
from fme.engines import timeutil

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



@app.route("/api/output", methods=["GET", "POST"])
def api_output():
    """Read or change where results are written."""
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        path = (data.get("path") or "").strip()
        if not path:
            return error_response(ValueError("No output folder given"))
        try:
            set_output_root(path)
        except (OSError, PermissionError) as exc:
            return error_response(exc)
    with ACTIVE_LOCK:
        info = ACTIVE["info"]
    size = (info or {}).get("size", 0)
    return jsonify({"ok": True, "path": _core.OUT_ROOT,
                    "space": output_space(_core.OUT_ROOT, size),
                    "default": os.path.join(BASE_DIR, "media_out")})


@app.route("/api/files")
def api_files():
    """One page of a job's files, filtered, sorted and counted on the server.

    The page used to hold every file and slice it in the browser, which
    works until a case has two hundred thousand of them. This hands over the
    page being looked at and the true total, so the cost of a screenful does
    not depend on the size of the case.

    It is a query (fme/review/fileindex.py) rather than a walk over a copy
    of the list. The walk copied every row under the job lock on every
    request, and its category filter read a `grade` field the rows never
    have -- gradings are in case.db -- so it matched nothing. The index
    joins case.db itself, so a category filter sees the examiner's gradings
    the moment they are saved.

    Parameters: kind (image/video/audio), category (a grading code),
    deleted=1, q (text in the path or source), sort (fileindex.SORTS),
    dir=desc, limit (1-5000), offset.
    """
    from fme.review import fileindex

    started = time.perf_counter()
    job_id = request.args.get("job_id", "")
    try:
        limit = min(max(int(request.args.get("limit", 500)), 1), 5000)
        offset = max(int(request.args.get("offset", 0)), 0)
        raw_grade = (request.args.get("category") or "").strip()
        grade = int(raw_grade) if raw_grade else None
    except ValueError:
        return error_response(ValueError("limit, offset and category must be "
                                         "whole numbers"))
    sort = (request.args.get("sort") or "order").strip()
    if sort not in fileindex.SORTS:
        return error_response(ValueError(
            f"cannot sort by {sort!r}; one of: {', '.join(sorted(fileindex.SORTS))}"))

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return error_response(KeyError("Unknown job_id"), status=404)
        # A reference, not a copy: the list is replaced, never edited, so
        # this one stays what it was while the lock is released.
        rows = job.get("files") or []
        index = job.get("_index")
        if index is None:
            index = job["_index"] = fileindex.FileIndex()
        out_dir = job.get("out_dir") or ""

    page = index.page(
        rows, os.path.join(out_dir, "case.db") if out_dir else "",
        kind=(request.args.get("kind") or "").strip(), grade=grade,
        deleted=(request.args.get("deleted") or "").strip() in ("1", "true", "yes"),
        text=(request.args.get("q") or "").strip(), sort=sort,
        descending=(request.args.get("dir") or "").lower() == "desc",
        limit=limit, offset=offset)

    return jsonify({"ok": True, "total": page["total"], "offset": page["offset"],
                    "limit": limit, "sort": sort,
                    "files": page["files"],
                    "server_ms": round((time.perf_counter() - started) * 1000, 1)})


@app.route("/api/gallery/query", methods=["POST"])
def api_gallery_query():
    """One page of the gallery, decided here rather than in the browser.

    Takes the page's filter state as it stands (fme/review/galleryquery.py
    says what each part means) and answers with that page of files, the true
    total, and -- when asked -- the facet counts the Filters pane shows. The
    rows are the job's own, with the derived fields the page already gets;
    the gradings are case.db's, read as the page reads them.

    Held to exact agreement with the page's visible() by
    tests/test_gallery_parity.py.

    A case too large to send whole (v2026.11.309) is browsed through this
    alone, so it also answers what the page used to work out from its copy:

      counts: true        the category bar, Classify pane and toggle counts
      ids: true           [rel_path, sha256] of every file the filter shows
                          (Select all matching, a report of what is shown)
      groups: [...]       every SHA-256 in these look-alike groups
      paths: [...]        these files as they stand now (a held page)
      after: [...]        start at the files after these (leaving a held page)
    """
    from fme.review import galleryquery

    started = time.perf_counter()
    data = request.get_json(force=True) or {}
    job_id = str(data.get("job") or "")
    try:
        limit = min(max(int(data.get("limit", 500)), 1), 5000)
        offset = max(int(data.get("offset", 0)), 0)
    except (TypeError, ValueError):
        return error_response(ValueError("limit and offset must be whole numbers"))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return error_response(KeyError("Unknown job_id"), status=404)
        rows = job.get("files") or []          # replaced, never edited in place
    case = _case_for(job_id)
    version, decisions = case.decisions_cached() if case else (None, {})
    raw = data.get("state") or {}
    state = galleryquery.State(raw, uncategorized=case.uncategorized if case
                               else casedb.UNCATEGORIZED)
    answer = {"ok": True}

    if data.get("groups") is not None:
        answer["sha256"] = galleryquery.group_members(rows, data.get("groups") or [])
        answer["server_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return jsonify(answer)
    if data.get("paths") is not None:
        held = galleryquery.by_paths(rows, data.get("paths") or [])
        answer.update(files=held, held=True, case_total=len(rows))

    shown = galleryquery.CACHE.visible(rows, decisions, version, raw, state)
    total = len(shown)
    if data.get("paths") is not None:
        # Which of a held page's files the filter still shows: grading the
        # page may have taken them all out of it.
        wanted = set(data.get("paths") or [])
        answer["matching"] = [f.get("rel_path") for f in shown
                              if f.get("rel_path") in wanted]
    if data.get("ids"):
        answer["ids"] = [[f.get("rel_path"), f.get("sha256")] for f in shown]
    if data.get("after"):
        offset = galleryquery.offset_after(shown, rows, data.get("after") or [])
        offset -= offset % limit
    if offset >= total and total:
        offset = max(0, ((total - 1) // limit) * limit)
    answer.update(total=total, offset=offset, limit=limit)
    if "files" not in answer:
        answer["files"] = shown[offset:offset + limit]
    if data.get("facets"):
        answer["facets"] = galleryquery.CACHE.facets(rows, decisions, version, raw, state)
    if data.get("counts"):
        answer["counts"] = galleryquery.CACHE.aggregates(rows, decisions, version, raw, state)
    answer["server_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return jsonify(answer)


@app.route("/api/file")
def api_file():
    """Serve a recovered file — inline for the gallery preview, or as a
    download when ?download=1."""
    full, err = _job_file(request.args.get("job", ""), request.args.get("file", ""))
    if err:
        return err
    # Only what the page can show inline is served inline; anything else
    # (an SVG, an HTML page, a format the gallery cannot draw) is a
    # download, so it never opens as a document of this program's origin.
    ext = os.path.splitext(full)[1].lstrip(".").lower()
    inline = ext in PREVIEWABLE or ext in media_core.AUDIO_EXTS
    if request.args.get("download") or not inline:
        return send_file(full, as_attachment=True)
    mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
    return send_file(full, mimetype=mime)


@app.route("/api/open-file", methods=["POST"])
def api_open_file():
    """Open a recovered file in whatever the operating system uses for it.

    The GUI runs on the examiner's own machine, so handing a path to the
    desktop is exactly what "Open with..." means here: video goes to the
    default player, images to the default viewer, and the choice of
    application stays with the operating system rather than being reinvented
    in a browser tab.
    """
    data = request.get_json(force=True) or {}
    reveal = bool(data.get("reveal"))
    if not (data.get("file") or "").strip():
        # No file: the case's own folder, which is what the ribbon's Output
        # folder asks for. It went through _job_file, which takes files only,
        # so that button answered "File not found" every time it was pressed.
        with JOBS_LOCK:
            job = JOBS.get(data.get("job", ""))
        if job is None:
            return error_response(KeyError("Unknown job_id"), status=404)
        full, reveal = os.path.abspath(job["out_dir"]), False
        if not os.path.isdir(full):
            return error_response(FileNotFoundError(
                f"The case folder is not there: {full}"), status=404)
    else:
        full, err = _job_file(data.get("job", ""), data.get("file", ""))
        if err:
            return err
    try:
        _desktop_open(full, reveal=reveal)
    except (OSError, RuntimeError) as exc:
        return error_response(exc)
    return jsonify({"ok": True, "opened": full, "reveal": reveal})


@app.route("/api/thumb")
def api_thumb():
    """A gallery-sized copy, made once and kept with the case.

    A tile is about 180 pixels across; a phone photograph is 4,000. Serving
    the original to every tile means a page of fifty asks for perhaps two
    hundred megabytes and the browser throws almost all of it away after
    scaling. On a case of a hundred thousand files that is most of why
    scrolling feels slow -- and it is work repeated every time the page is
    drawn, because nothing was kept.

    These are kept in the case's own thumbs folder: made once, reused on
    every later visit, and thrown away with the case.
    """
    job_id = request.args.get("job", "")
    rel_path = (request.args.get("file") or "").strip()
    full, err = _job_file(job_id, rel_path)
    if err:
        return err
    with JOBS_LOCK:
        job = JOBS.get(job_id) or {}
    out_dir = job.get("out_dir") or os.path.dirname(full)

    digest = hashlib.sha256(rel_path.encode("utf-8", "replace")).hexdigest()
    cache_dir = os.path.join(out_dir, "thumbs", digest[:2])
    cached = os.path.join(cache_dir, digest + ".jpg")
    if os.path.isfile(cached):
        return send_file(cached, mimetype="image/jpeg",
                         max_age=86400, conditional=True)

    if not _make_thumb(out_dir, full, rel_path):
        # The original rather than a broken tile: a missing thumbnail is a
        # speed problem, not a reason to hide evidence from the examiner.
        # But only what /api/file would serve inline -- an SVG or HTML file
        # that will not thumbnail went out here as a document of this
        # program's origin, the thing /api/file was changed to refuse.
        ext = os.path.splitext(full)[1].lstrip(".").lower()
        if ext not in PREVIEWABLE:
            return error_response(ValueError("No picture to show for this file"),
                                  status=404)
        return send_file(full, conditional=True)

    return send_file(cached, mimetype="image/jpeg", max_age=86400,
                     conditional=True)


@app.route("/api/preview")
def api_preview():
    """A JPEG of a file the browser cannot display.

    Converted on request and cached beside the run. The converted image is a
    rendering, not evidence -- it is never hashed, never counted, and never
    written among the recovered files.
    """
    job_id = request.args.get("job", "")
    rel = request.args.get("file", "")
    full, err = _job_file(job_id, rel)
    if err:
        return err
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    record = next((f for f in (job or {}).get("files", [])
                   if f.get("rel_path") == rel), None)
    digest = (record or {}).get("sha256") or ""
    if not job or not digest:
        return error_response(KeyError("Unknown file"), status=404)

    cache_dir = os.path.join(job["out_dir"], "_previews", digest[:2])
    cached = os.path.join(cache_dir, digest + ".jpg")
    if os.path.isfile(cached):
        return send_file(cached, mimetype="image/jpeg")

    if not imaging.PILLOW:
        return error_response(RuntimeError(
            "Showing this format needs Pillow: pip install pillow"))
    from PIL import Image

    try:
        os.makedirs(cache_dir, exist_ok=True)
        with imaging.open_image(full) as opened:
            im = opened.convert("RGB")
            im.thumbnail((1600, 1600), Image.LANCZOS)
            save_jpeg_atomically(im, cached, quality=82)
    except Exception as exc:  # noqa: BLE001 - a truncated carve is expected
        return error_response(ValueError(
            f"Could not render this file: {type(exc).__name__}. "
            f"Open with… hands it to a native application."))
    return send_file(cached, mimetype="image/jpeg")


@app.route("/api/storyboard")
def api_storyboard():
    """A video as a grid of evenly spaced frames, each with its time.

    Generated, not recovered: decoded from the stored file in a process of
    its own (video_frames), for any video, playable or not."""
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
    result = video_frames.extract(full, out_dir, digest, count=video_frames.BOARD_COUNT,
                                  width=video_frames.BOARD_WIDTH, board=True)
    info = video_frames.board_info(out_dir, digest)
    count = int(info.get("count") or video_frames.BOARD_COUNT)
    duration = float(info.get("duration") or result.get("duration") or 0)
    indexes = info.get("indexes") or list(range(len(result.get("frames", []))))
    frames = [{"src": f"/api/frame?job={job_id}&sha={digest}&n={i}&board=1",
               "at": round(duration * (index + 0.5) / count, 2) if duration else None}
              for i, index in enumerate(indexes[:len(result.get("frames", []))])]
    return jsonify({"ok": result.get("ok", False), "available": True,
                    "frames": frames, "duration": duration,
                    "error": result.get("error", "")})


@app.route("/api/frame")
def api_frame():
    """One extracted frame. Generated, not recovered: see video_frames."""
    job_id = request.args.get("job", "")
    digest = request.args.get("sha", "")
    try:
        index = int(request.args.get("n", "0"))
    except ValueError:
        return error_response(ValueError("n must be a number"))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return error_response(KeyError("Unknown file"), status=404)
    frames = video_frames.existing(job["out_dir"], digest,
                                   board=request.args.get("board") == "1")
    if not 0 <= index < len(frames):
        return error_response(FileNotFoundError("No such frame"), status=404)
    return send_file(frames[index], mimetype="image/jpeg")


@app.route("/api/details")
def api_details():
    """Everything known about one recovered file, for the details pane."""
    job_id = request.args.get("job", "")
    rel = request.args.get("file", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        job = dict(job) if job else None
    if job is None:
        return error_response(KeyError("Unknown job_id"), status=404)

    # During a run from manifest.csv, after it from manifest.json.
    try:
        recorded, duplicates = case_records(job["out_dir"])
    except (OSError, json.JSONDecodeError) as exc:
        return error_response(exc)

    record = next((f for f in recorded if f.get("out_path") == rel), None)
    if record is None:
        return error_response(FileNotFoundError("No such file in this run"))

    # Exact copies: the other places these same bytes were found.
    copies = [d for d in duplicates if d.get("sha256") == record.get("sha256")]

    full = os.path.join(job["out_dir"], rel.replace("/", os.sep))
    matches = (job.get("hash_matches") or {}).get(record.get("sha256"), [])
    case = _case_for(job_id)
    decision = case.decision(record.get("sha256", "")) if case else {}

    sim = job.get("similarity") or {}
    similar = []
    group_id = None
    if sim:
        group_id = sim.get("membership", {}).get(rel)
        hashes = {path: int(value, 16) for path, value in sim.get("hashes", {}).items()}
        similar = similarity.similar_to(rel, hashes, sim.get("max_distance",
                                                             similarity.DEFAULT_DISTANCE))
    # By extension, except where the bytes say otherwise. A CgBI PNG from an
    # iOS app bundle is named .png and no browser can draw it: shown as-is,
    # the pane displays a black square as though that were the picture.
    # Looked at here, for the one file opened, not for every tile in a list.
    ext = record.get("ext") or ""
    previewable = ext in PREVIEWABLE
    convertible = ext in CONVERTIBLE
    if imaging.PILLOW and os.path.isfile(full) and imaging.needs_rendering(full):
        previewable, convertible = False, True
    return jsonify({
        "similar": similar,
        "similar_group": group_id,
        "similarity_ran": bool(sim),
        "similarity_available": similarity.available(),
        "ok": True,
        # For showing: each time as its text, instant or reading. The page
        # converts an instant and marks a reading by whether it has a zone.
        "record": timeutil.merged(record),
        "stored_at": full,
        # The job's own record of the image: a run's manifest.json, and so
        # its summary, is not written until the run ends.
        "source": os.path.basename(job.get("path") or
                                   (job.get("summary") or {}).get("image", "")),
        "source_full": job.get("path") or (job.get("summary") or {}).get("image", ""),
        "exact_copies": [timeutil.merged(c) for c in copies],
        "hash_sets": matches,
        "decision": decision,
        "previewable": previewable,
        "convertible": convertible,
    })


@app.route("/api/manifest")
def api_manifest():
    job_id = request.args.get("job", "")
    full, err = _job_file(job_id, "manifest.csv")
    if err:
        return err
    return send_file(full, as_attachment=True,
                     download_name=f"manifest_{job_id[:8]}.csv")




@app.route("/api/basemap", methods=["GET", "POST"])
def api_basemap():
    """The offline basemaps on this machine; POST {path} imports one and
    makes it the one maps are drawn on. Nothing is fetched from anywhere."""
    from fme.engines import basemaps
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        source = (data.get("path") or "").strip().strip('"')
        if not source:
            return jsonify({"ok": False, "error": "Give the path of a .pmtiles "
                                                  "or .mbtiles file."})
        try:
            rec = basemaps.import_basemap(source)
            basemaps.set_active(rec["name"])
        except (OSError, ValueError) as exc:
            return jsonify({"ok": False, "error": f"Not imported: {exc}"})
    rows = [{k: r.get(k) for k in ("name", "format", "size", "sha256",
                                   "min_zoom", "max_zoom", "active", "present",
                                   "attribution")}
            for r in basemaps.list_basemaps()]
    return jsonify({"ok": True, "basemaps": rows,
                    "active": basemaps.get_active() or ""})


@app.route("/maps/<path:asset>")
def map_asset(asset):
    """MapLibre, pmtiles.js, the layer styles, fonts and sprites for the case
    map, from fme/web/ui/maps. send_from_directory refuses a path that leaves
    the folder."""
    from flask import send_from_directory
    from fme.engines import basemaps
    kinds = {".pbf": "application/x-protobuf", ".json": "application/json",
             ".js": "text/javascript", ".css": "text/css", ".png": "image/png"}
    ext = os.path.splitext(asset)[1].lower()
    if ext not in kinds:
        return Response("not a map asset", status=404)
    response = send_from_directory(str(basemaps.map_assets_dir()), asset,
                                   mimetype=kinds[ext], max_age=86400)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/api/basemap/style")
def api_basemap_style():
    """A MapLibre style for the active basemap, every URL in it local."""
    from fme.engines import basemaps
    name = basemaps.get_active()
    if not name:
        return jsonify({"ok": False, "error": "No basemap has been imported. "
                        "Import a .pmtiles or .mbtiles file under Settings."}), 404
    try:
        drawn = basemaps.style(name, flavor=request.args.get("flavor", "dark"))
    except (OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    return jsonify({"ok": True, "style": drawn,
                    "basemap": basemaps.provenance(name)})


@app.route("/api/basemap/file/<filename>")
def api_basemap_file(filename):
    """The active PMTiles file itself, by byte range, which is all pmtiles.js
    asks for. Only a file the basemap list names; never an MBTiles whole."""
    from fme.engines import basemaps
    from pathlib import Path as _Path
    rec = basemaps.get(_Path(filename).stem)
    if (not rec or rec["format"] != basemaps.FORMAT_PMTILES
            or rec["file"] != filename or not os.path.isfile(rec["path"])):
        return Response("no such basemap", status=404)
    return send_file(rec["path"], mimetype="application/octet-stream",
                     conditional=True)


@app.route("/api/basemap/tile/<name>/<int:z>/<int:x>/<int:y>")
def api_basemap_tile(name, z, x, y):
    """One raster tile from an MBTiles basemap, addressed the XYZ way."""
    from fme.engines import basemaps
    rec = basemaps.get(name)
    if not rec or rec["format"] != basemaps.FORMAT_MBTILES:
        return Response("no such basemap", status=404)
    got = basemaps.mbtiles_tile(rec["path"], z, x, y)
    if got is None:
        return Response("no tile", status=404)
    data, mime = got
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.route("/api/case/map", methods=["POST"])
def api_case_map():
    """Every geolocated file the gallery's filter shows, as map points.

    The same filter the gallery answers with (galleryquery), so the map is
    of what the examiner is looking at, in a case of any size. Names,
    positions and grades only: no thumbnail is put on the map, so panning a
    map of a case is not a way round the review blur.
    """
    from fme.review import galleryquery
    data = request.get_json(force=True) or {}
    job_id = str(data.get("job") or "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return error_response(KeyError("Unknown job_id"), status=404)
        rows = job.get("files") or []
    case = _case_for(job_id)
    version, decisions = case.decisions_cached() if case else (None, {})
    raw = data.get("state") or {}
    state = galleryquery.State(raw, uncategorized=case.uncategorized if case
                               else casedb.UNCATEGORIZED)
    shown = galleryquery.CACHE.visible(rows, decisions, version, raw, state)
    points, outside = [], 0
    for f in shown:
        lat, lon = f.get("latitude"), f.get("longitude")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            outside += 1        # a corrupt GPS tag; listed, never drawn
            continue
        grade = (decisions.get(f.get("sha256")) or {}).get("category")
        points.append({"rel_path": f.get("rel_path"), "sha256": f.get("sha256"),
                       "name": f.get("name") or os.path.basename(f.get("rel_path") or ""),
                       "lat": lat, "lon": lon, "category": grade,
                       "kind": f.get("category")})
        if len(points) >= MAP_POINT_LIMIT:
            break
    return jsonify({"ok": True, "points": points, "shown": len(shown),
                    "truncated": len(points) >= MAP_POINT_LIMIT,
                    "out_of_range": outside})


@app.route("/api/locator")
def api_locator():
    """A locator map of one position, drawn from the active offline basemap.
    404 when there is none or it does not cover the position: the page then
    shows no map rather than a marker on an empty square."""
    from fme.exports import report_maps
    try:
        lat = float(request.args.get("lat", ""))
        lon = float(request.args.get("lon", ""))
    except ValueError:
        return Response("bad position", status=400)
    maps = report_maps.active()
    if maps is None or not maps.covers(lat, lon):
        return Response("no basemap covers this position", status=404)
    png = maps.png([(lat, lon)], report_maps.LOCATOR)
    if not png:
        return Response("the basemap could not draw this position", status=404)
    response = Response(png, mimetype="image/png")
    response.headers["X-FME-Basemap"] = maps.provenance["sha256"]
    return response
