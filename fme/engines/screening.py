"""
screening.py -- a triage signal, deliberately not a nudity classifier.

Griffeye's nudity levels come from a trained classifier. This is not that,
and calling it that would be the dangerous kind of wrong: an examiner who
believes a machine has judged nudity will look less carefully at what it
scored low.

What this measures is **the proportion of a picture in a broad skin-tone
band**, plus **how many faces a detector finds**. Both are arithmetic over
pixels. Neither knows what it is looking at.

That proportion is useful anyway, for one job: ordering a backlog. In a
hundred and sixty thousand recovered files, looking at the ones with a high
skin-tone proportion first surfaces relevant material sooner than looking at
them in the order they were carved. That is its whole value.

What it is not, stated plainly because a number in a column invites being
read as a verdict:

* **A low score is not "cleared."** A clothed child, a dark or blown-out
  photograph, a small subject in a large frame and a picture of a face all
  score low. So does anything the decoder could not read.
* **A high score is not "nudity."** Sand, wood, brick, a close-up of a face,
  a dog, a sunset and a hand all sit in the skin-tone band.
* **It is biased by skin tone and lighting**, because it is a color rule.
  The band is broad, and it is still a color rule.
* **It categorizes nothing.** No file is ever graded by this. It sorts and
  filters; an examiner grades.

Faces are counted with OpenCV's bundled cascade when OpenCV is installed. A
count of zero means the detector found none, which is not the same as there
being none.

    python -m fme screen -o <run folder>
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

from fme.engines import imaging

__version__ = "1.0.0"

# Named for what they measure. The ranges are arbitrary divisions of a
# continuous number, chosen to spread a typical run across them rather than
# to mean anything in themselves.
BANDS = [
    (0.00, 0.08, "Very little skin tone"),
    (0.08, 0.20, "Low"),
    (0.20, 0.35, "Low–medium"),
    (0.35, 0.50, "Medium"),
    (0.50, 0.70, "Medium–high"),
    (0.70, 1.01, "High"),
]

SAMPLE = 160          # the longest side a picture is reduced to before counting
UNAVAILABLE = ("Screening needs Pillow: pip install pillow")


def available() -> bool:
    return imaging.PILLOW


def band_of(score: float) -> str:
    for low, high, name in BANDS:
        if low <= score < high:
            return name
    return BANDS[-1][2]


def skin_proportion(path: str) -> float:
    """The fraction of a picture inside a broad skin-tone band.

    Two rules are intersected -- one in HSV, one in YCbCr -- because either
    alone takes in far too much. The picture is reduced first: this is a
    proportion, and a thumbnail gives the same proportion as the original
    for a fraction of the work.
    """
    if not imaging.PILLOW:
        raise RuntimeError(UNAVAILABLE)
    from PIL import Image

    with imaging.open_image(path) as opened:
        image = opened.convert("RGB")
        image.thumbnail((SAMPLE, SAMPLE), Image.BILINEAR)
        flat = getattr(image, "get_flattened_data", None)   # getdata() is
        pixels = list(flat() if flat else image.getdata())  # deprecated

    if not pixels:
        return 0.0

    skin = 0
    for red, green, blue in pixels:
        # YCbCr, the rule most of the literature uses.
        chroma_b = 128 - 0.168736 * red - 0.331264 * green + 0.5 * blue
        chroma_r = 128 + 0.5 * red - 0.418688 * green - 0.081312 * blue
        if not (77 <= chroma_b <= 127 and 133 <= chroma_r <= 173):
            continue
        # And a coarse RGB sanity rule, which throws out much of the wood,
        # sand and brick the chroma rule alone accepts.
        if not (red > 95 and green > 40 and blue > 20
                and red > green and red > blue
                and abs(red - green) > 15
                and max(red, green, blue) - min(red, green, blue) > 15):
            continue
        skin += 1
    return skin / len(pixels)


def face_count(path: str) -> int:
    """How many faces a detector finds, or -1 when there is no detector.

    -1 rather than 0, because "no detector ran" and "the detector found
    none" are different statements and a column of zeros would hide the
    difference.
    """
    try:
        import cv2
    except ImportError:
        return -1
    try:
        cascade_path = os.path.join(cv2.data.haarcascades,
                                    "haarcascade_frontalface_default.xml")
        if not os.path.isfile(cascade_path):
            return -1
        cascade = cv2.CascadeClassifier(cascade_path)
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            return -1
        found = cascade.detectMultiScale(image, 1.2, 5, minSize=(24, 24))
        return len(found)
    except Exception:  # noqa: BLE001 - a damaged file is not a failure here
        return -1


def score(path: str, faces: bool = True) -> dict:
    try:
        proportion = skin_proportion(path)
    except Exception as exc:  # noqa: BLE001 - truncated carves are routine
        return {"skin": None, "band": "Could not be read",
                "faces": -1, "error": type(exc).__name__}
    return {"skin": round(proportion, 4), "band": band_of(proportion),
            "faces": face_count(path) if faces else -1, "error": ""}


def run(run_dir: str, faces: bool = True, progress=None) -> dict:
    """Score every image in a run, and keep the result with the case."""
    if not available():
        raise RuntimeError(UNAVAILABLE)

    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    images = [f for f in manifest.get("files", [])
              if f.get("category") == "image"]
    results, unreadable = {}, 0
    rows = []
    for index, record in enumerate(images, start=1):
        path = os.path.join(run_dir, record.get("out_path", ""))
        result = score(path, faces=faces)
        if result["skin"] is None:
            unreadable += 1
        results[record.get("sha256", "")] = result
        rows.append({"out_path": record.get("out_path", ""),
                     "sha256": record.get("sha256", ""),
                     "skin_proportion": result["skin"],
                     "band": result["band"], "faces": result["faces"]})
        if progress and index % 25 == 0:
            progress(index, len(images))
    if progress:
        progress(len(images), len(images))

    out_csv = os.path.join(run_dir, "screening.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "out_path", "sha256", "skin_proportion", "band", "faces"])
        writer.writeheader()
        writer.writerows(rows)

    case_path = os.path.join(run_dir, "case.db")
    if os.path.isfile(case_path) and results:
        try:
            from fme.review import casedb
            case = casedb.CaseDB(case_path)
            case.save_derived("screening", results)
            case.close()
        except Exception:  # noqa: BLE001 - the CSV is written either way
            pass

    bands = {}
    for result in results.values():
        bands[result["band"]] = bands.get(result["band"], 0) + 1
    return {"images": len(images), "scored": len(results) - unreadable,
            "unreadable": unreadable, "bands": bands, "csv": out_csv,
            "faces_available": any(r["faces"] >= 0 for r in results.values())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Score images by skin-tone proportion, to order a "
                    "backlog. Not a nudity classifier.")
    ap.add_argument("-o", "--run", required=True, help="a run's output folder")
    ap.add_argument("--no-faces", dest="faces", action="store_false")
    args = ap.parse_args(argv)

    if not available():
        print(UNAVAILABLE, file=sys.stderr)
        return 2

    def progress(done, total):
        print(f"\r  {done:,}/{total:,} images", end="", flush=True)

    try:
        result = run(args.run, faces=args.faces, progress=progress)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 2

    print(f"\n\nImages     : {result['images']:,}")
    print(f"Scored     : {result['scored']:,}")
    print(f"Unreadable : {result['unreadable']:,}")
    for low, high, name in BANDS:
        count = result["bands"].get(name, 0)
        if count:
            print(f"  {name:24} {count:>8,}   ({low:.0%}–{high:.0%} of the frame)")
    print(f"\nWritten    : {result['csv']}")
    print("\nThis is the proportion of each picture in a broad skin-tone band,"
          "\nnot a judgement about what is in it. A low score is not 'cleared'"
          "\nand a high score is not 'nudity'. Nothing has been categorized.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
