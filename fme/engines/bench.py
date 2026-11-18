"""
bench.py -- how much faster the graphics cards make this machine, measured
on the examiner's own files (v2026.11.332).

    ForensicMediaExtractor bench --case D:\\cases\\DC26-0001
    python -m fme bench --folder D:\\samples -n 300 -v 12

Times, side by side, each way this program can do the three jobs that the
cards change, on the same files:

  pictures  the content model ("search by what is in a picture"): OpenCV one
            at a time, as before v2026.11.332, against what this machine
            uses now (fme/engines/accel.py), and each other engine that is
            here -- the cards through CUDA or DirectML, ONNX Runtime on the
            processor.
  faces     finding and describing faces: one thread against several.
  video     a dozen scrub frames per clip: OpenCV, FFmpeg on the processor,
            FFmpeg on the cards.

Nothing is written into the case. Frames go to a temporary folder that is
removed; descriptions are made and thrown away. The figures, where each job
ran, and the projection for the whole case are printed and kept as JSON
(--out, or a file named in the output).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

__version__ = "1.0.0"

PICTURES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
VIDEOS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".webm", ".ts")


def _gather(args) -> tuple:
    pictures, videos, total_pictures, total_videos = [], [], 0, 0
    if args.case:
        manifest = os.path.join(args.case, "manifest.json")
        with open(manifest, encoding="utf-8") as fh:
            files = json.load(fh).get("files", [])
        for f in files:
            path = os.path.join(args.case, f.get("out_path", ""))
            if f.get("category") == "image":
                total_pictures += 1
                if path.lower().endswith(PICTURES):
                    pictures.append(path)
            elif f.get("category") == "video":
                total_videos += 1
                videos.append(path)
    else:
        for here, _dirs, names in os.walk(args.folder):
            for name in sorted(names):
                path = os.path.join(here, name)
                if name.lower().endswith(PICTURES):
                    pictures.append(path)
                elif name.lower().endswith(VIDEOS):
                    videos.append(path)
        total_pictures, total_videos = len(pictures), len(videos)
    pictures = [p for p in pictures if os.path.isfile(p)][:args.pictures]
    videos = [v for v in videos if os.path.isfile(v)][:args.videos]
    return pictures, videos, total_pictures, total_videos


def _rate(count: int, seconds: float) -> float:
    return round(count / seconds, 2) if seconds > 0 else 0.0


def _pictures(pictures: list, total: int, say) -> dict:
    from fme.engines import accel, content_search, models

    model, _source, why = models.resolve("content", "")
    if not model:
        return {"skipped": why}
    rows = []
    choices = ["opencv"]
    status = accel.status()
    if status["runtime"]:
        if "CUDAExecutionProvider" in status["providers"]:
            choices.append("cuda")
        if "DmlExecutionProvider" in status["providers"]:
            choices.append("dml")
        choices.append("cpu")
    was = os.environ.get(accel.ENV)
    try:
        for choice in choices:
            os.environ[accel.ENV] = choice
            engines = accel.plan_for(model)
            if choice != "opencv" and not engines:
                rows.append({"engine": choice, "skipped": "no card opened for it"})
                continue
            label = accel.describe_label(engines)
            say(f"  pictures: {label} ...")
            # One picture first, so loading the model is not counted.
            content_search.describe_many(pictures[:1], model)
            started = time.perf_counter()
            made = content_search.describe_many(pictures, model)
            seconds = time.perf_counter() - started
            rows.append({"engine": choice, "where": label, "pictures": len(pictures),
                         "described": sum(1 for v in made.values() if v),
                         "seconds": round(seconds, 2),
                         "per_second": _rate(len(pictures), seconds)})
    finally:
        if was is None:
            os.environ.pop(accel.ENV, None)
        else:
            os.environ[accel.ENV] = was
    base = next((r for r in rows if r.get("engine") == "opencv" and r.get("per_second")), None)
    for row in rows:
        if base and row.get("per_second"):
            row["times_faster"] = round(row["per_second"] / base["per_second"], 1)
            row["whole_case_minutes"] = round(total / row["per_second"] / 60, 1)
    return {"model": os.path.basename(model), "case_pictures": total, "rows": rows}


def _faces(pictures: list, total: int, say) -> dict:
    from fme.engines import face_match, models

    detector, _s, why = models.resolve("face_detector", "")
    recognizer, _s2, why2 = models.resolve("face_recognizer", "")
    if not detector or not recognizer:
        return {"skipped": why or why2}
    rows = []
    rows_in = [{"category": "image", "out_path": p, "sha256": str(n)}
               for n, p in enumerate(pictures)]
    for workers in sorted({1, face_match.scan_workers()}):
        say(f"  faces: {workers} thread(s) ...")
        started = time.perf_counter()
        found = face_match.scan("", rows_in, detector, recognizer, workers=workers)
        seconds = time.perf_counter() - started
        rows.append({"threads": workers, "pictures": len(pictures),
                     "with_faces": found.get("with_faces", 0),
                     "seconds": round(seconds, 2),
                     "per_second": _rate(len(pictures), seconds),
                     "whole_case_minutes": round(total / max(1e-9, _rate(len(pictures), seconds)) / 60, 1)
                     if seconds else 0})
    if rows and rows[0]["per_second"]:
        for row in rows:
            row["times_faster"] = round(row["per_second"] / rows[0]["per_second"], 1)
    return {"case_pictures": total, "rows": rows}


def _videos(videos: list, total: int, say) -> dict:
    from fme.engines import gpu_video, video_frames

    found = gpu_video.info()
    ways = []
    if video_frames.available():
        ways.append(("OpenCV on the processor", "opencv"))
    if found.get("ffmpeg"):
        ways.append(("FFmpeg on the processor", "cpu"))
        if found.get("cuda"):
            ways.append((f"FFmpeg on {found['gpus']} NVIDIA card(s)", "gpu"))
    rows = []
    scratch = tempfile.mkdtemp(prefix="fme_bench_")
    try:
        for label, how in ways:
            say(f"  video: {label} ...")
            started = time.perf_counter()
            made = 0
            for n, path in enumerate(videos):
                folder = os.path.join(scratch, f"{how}_{n}")
                os.makedirs(folder)
                if how == "opencv":
                    got = video_frames._extract_opencv(path, folder, f"{n:064x}", 12,
                                                       video_frames.FRAME_WIDTH, False)
                    made += bool(got.get("ok"))
                else:
                    written, _length, _why = gpu_video.frames(
                        path, folder, 12, video_frames.FRAME_WIDTH,
                        video_frames.JPEG_QUALITY, gpu=how == "gpu")
                    made += bool(written)
            seconds = time.perf_counter() - started
            rows.append({"where": label, "clips": len(videos), "decoded": made,
                         "seconds": round(seconds, 2),
                         "clips_per_second": _rate(len(videos), seconds),
                         "whole_case_minutes": round(total * seconds / max(1, len(videos)) / 60, 1)})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if rows and rows[0]["clips_per_second"]:
        for row in rows:
            row["times_faster"] = round(row["clips_per_second"] / rows[0]["clips_per_second"], 1)
    return {"ffmpeg": found.get("ffmpeg", ""), "why": found.get("why", ""),
            "case_videos": total, "rows": rows}


def _table(title: str, rows: list, keys: list) -> str:
    lines = [title]
    for row in rows:
        if "skipped" in row:
            lines.append(f"  {row.get('engine', '')}: skipped -- {row['skipped']}")
            continue
        lines.append("  " + "  |  ".join(f"{k.replace('_', ' ')}: {row.get(k, '')}"
                                         for k in keys if k in row))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="bench", description="Measure the graphics cards against the "
        "processor on your own files. Writes nothing into the case.")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", help="a case folder (the one holding manifest.json)")
    source.add_argument("--folder", help="any folder of pictures and videos")
    ap.add_argument("-n", "--pictures", type=int, default=200,
                    help="pictures to time (default 200)")
    ap.add_argument("-v", "--videos", type=int, default=10,
                    help="videos to time (default 10)")
    ap.add_argument("--only", choices=("pictures", "faces", "video"), action="append",
                    help="time only this (may be given more than once)")
    ap.add_argument("--out", help="where to keep the figures as JSON")
    args = ap.parse_args(argv)

    from fme import RELEASE
    from fme.engines import accel, gpu_video

    try:        # the face detector's start-up notes are not results
        import cv2
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:  # noqa: BLE001
        pass

    def say(text):
        print(text, flush=True)

    pictures, videos, total_pictures, total_videos = _gather(args)
    say(f"Forensic Media Extractor {RELEASE} -- bench")
    say(f"  {len(pictures)} picture(s) and {len(videos)} video(s) to time; "
        f"{total_pictures} and {total_videos} in all")
    status = accel.status()
    say(f"  models: {status['runtime'] or 'no ONNX Runtime'} "
        f"{', '.join(status['providers'])} {status['why']}")
    found = gpu_video.info()
    say(f"  FFmpeg: {found.get('ffmpeg') or 'none'} "
        f"{'(' + str(found['gpus']) + ' NVIDIA card(s))' if found.get('cuda') else found.get('why', '')}")
    wanted = set(args.only or ("pictures", "faces", "video"))
    result = {"release": RELEASE, "cpu_count": os.cpu_count(), "models": status,
              "ffmpeg": found}
    if "pictures" in wanted and pictures:
        result["pictures"] = _pictures(pictures, total_pictures, say)
    if "faces" in wanted and pictures:
        result["faces"] = _faces(pictures, total_pictures, say)
    if "video" in wanted and videos:
        result["video"] = _videos(videos, total_videos, say)

    print()
    if "pictures" in result and "rows" in result["pictures"]:
        print(_table("Search by what is in a picture (describing each picture once):",
                     result["pictures"]["rows"],
                     ["where", "per_second", "times_faster", "whole_case_minutes"]))
    if "faces" in result and "rows" in result["faces"]:
        print(_table("Finding faces:", result["faces"]["rows"],
                     ["threads", "per_second", "times_faster", "whole_case_minutes"]))
    if "video" in result and result["video"].get("rows"):
        print(_table("Video posters and scrub frames (12 a clip):", result["video"]["rows"],
                     ["where", "clips_per_second", "times_faster", "whole_case_minutes"]))
    out = args.out or os.path.join(tempfile.gettempdir(),
                                   f"fme_bench_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"\nThe figures are kept in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
