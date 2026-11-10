"""
report_contents.py -- what a report covers, counted once.

A report is written in three forms and an examiner may hand over any of
them. If the HTML says ninety-six CAM and the Word document says
ninety-five, the question in the witness box is no longer about the
pictures.

So the counting happens here, once, and the three writers lay out the
same numbers. They cannot disagree, because there is only one of them.

What is counted:

- how many files the report covers, split into images, videos and
  anything else,
- how many in each category, with the share of the report each is,
- how many carry each flag, and how many carry any,
- how many matched a hash set somebody imported,
- and how much media the case holds in total, so a reader can see what
  share of it this report is.

The donut is drawn here too, as a PNG, in the colours the gallery uses
for the same categories -- a reader who has seen the screen should not
have to learn a second colour scheme to read the paper.
"""

from __future__ import annotations

import io
import math

__version__ = "1.0.0"

# The gallery's own colours, so the paper and the screen agree.
FALLBACK_COLORS = [
    "#dc2626", "#ea580c", "#d97706", "#2563eb", "#7c3aed",
    "#16a34a", "#0891b2", "#be185d", "#4d7c0f", "#9333ea",
]


def _kind_of(row: dict) -> str:
    # A row says "kind" where the run classified it, and "category" means
    # the examiner's grading code -- which is a number. Reading one as
    # the other is how this first fell over.
    kind = str(row.get("kind") or "").lower()
    if kind in ("image", "video"):
        return kind
    category = str(row.get("category") or "").lower()
    if category in ("image", "video"):
        return category
    extension = (row.get("ext") or "").lower().lstrip(".")
    if extension in ("jpg", "jpeg", "png", "gif", "webp", "heic", "bmp",
                     "tif", "tiff"):
        return "image"
    if extension in ("mp4", "mov", "avi", "mkv", "wmv", "3gp", "m4v"):
        return "video"
    return "other"


def compute(rows, scheme=None, total_in_case: int = 0) -> dict:
    """Count what this report covers.

    `rows` are the files in the report, `scheme` the case's categories
    (each with code, name and colour), `total_in_case` every piece of
    media the case holds -- which is not the same number and is worth
    showing beside it.
    """
    rows = list(rows or [])
    scheme = list(scheme or [])
    by_code = {entry.get("code"): entry for entry in scheme}

    kinds = {"image": 0, "video": 0, "other": 0}
    categories, flags, names = {}, {}, {}
    with_any_flag = known_hash = 0

    for row in rows:
        kinds[_kind_of(row)] += 1

        # The grading: its code where there is one, so the scheme can
        # supply the name and the colour the gallery uses.
        code = row.get("category_code")
        if code is None and isinstance(row.get("category"), int):
            code = row["category"]
        if code is None:
            code = row.get("decision_code")
        if code is not None:
            categories[code] = categories.get(code, 0) + 1
            if row.get("category_name"):
                names.setdefault(code, row["category_name"])
        elif row.get("category_name"):
            categories[row["category_name"]] = categories.get(
                row["category_name"], 0) + 1

        named = row.get("flag_names") or ""
        if isinstance(named, str):
            # Split as report.py and protocol.py split it (", ", the join
            # gather() uses): on "," alone a flag named "Victim, known" was
            # two flags here and one there, in the same report.
            named = [flag.strip() for flag in named.split(", ") if flag.strip()]
        if named:
            with_any_flag += 1
        for flag in named:
            flags[flag] = flags.get(flag, 0) + 1

        if row.get("hash_sets") or row.get("hash_set") or row.get("known"):
            known_hash += 1

    total = len(rows)

    def share(count: int) -> float:
        return round(100.0 * count / total, 1) if total else 0.0

    ordered = []
    for index, (code, count) in enumerate(sorted(
            categories.items(),
            key=lambda pair: (-pair[1], str(pair[0])))):
        entry = by_code.get(code) or {}
        ordered.append({
            "code": code,
            "name": (entry.get("name") or names.get(code)
                     or (code if isinstance(code, str)
                         else f"category {code}")),
            "color": entry.get("color")
                     or FALLBACK_COLORS[index % len(FALLBACK_COLORS)],
            "count": count,
            "percent": share(count),
        })

    return {
        "files": total,
        "images": kinds["image"],
        "videos": kinds["video"],
        "other": kinds["other"],
        "categories": ordered,
        "flags": [{"name": name, "count": count, "percent": share(count)}
                  for name, count in sorted(flags.items(),
                                            key=lambda p: -p[1])],
        "with_any_flag": with_any_flag,
        "with_any_flag_percent": share(with_any_flag),
        "known_hash": known_hash,
        "total_in_case": total_in_case,
    }


def donut(contents: dict, size: int = 420) -> bytes:
    """The categories as a ring, as a PNG, or empty where Pillow is absent.

    Drawn rather than charted by a library: one ring of a handful of
    slices needs no dependency, and a report that will not build because
    a charting library is missing is worse than a report without a
    picture.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return b""

    slices = [entry for entry in contents.get("categories") or []
              if entry["count"] > 0]
    if not slices:
        return b""

    # Drawn at four times the size and shrunk, which is the cheapest way
    # to get edges that do not look like stairs.
    scale = 4
    side = size * scale
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    total = sum(entry["count"] for entry in slices)
    start = -90.0
    for entry in slices:
        sweep = 360.0 * entry["count"] / total
        draw.pieslice([0, 0, side - 1, side - 1], start, start + sweep,
                      fill=entry["color"])
        start += sweep

    # The hole, and the count in it.
    hole = side * 0.30
    draw.ellipse([hole, hole, side - hole, side - hole],
                 fill=(255, 255, 255, 0))

    image = image.resize((size, size), Image.LANCZOS)
    draw = ImageDraw.Draw(image)
    label = f"{contents.get('files', 0):,}"
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * 0.16))
        small = ImageFont.truetype("DejaVuSans.ttf", int(size * 0.07))
    except OSError:
        font = small = ImageFont.load_default()

    box = draw.textbbox((0, 0), label, font=font)
    draw.text(((size - (box[2] - box[0])) / 2,
               size / 2 - (box[3] - box[1]) - size * 0.02),
              label, fill=(120, 120, 130, 255), font=font)
    word = "FILES"
    box = draw.textbbox((0, 0), word, font=small)
    draw.text(((size - (box[2] - box[0])) / 2, size / 2 + size * 0.04),
              word, fill=(140, 140, 150, 255), font=small)

    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def as_rows(contents: dict) -> list:
    """The block as plain (label, value, note) rows.

    One shape the three writers each lay out in their own way, so that a
    change to what is reported is made once rather than three times --
    and so the Word document cannot quietly drift from the HTML.
    """
    rows = [("Files in this report", f"{contents['files']:,}", "")]
    for label, key in (("Images", "images"), ("Videos", "videos"),
                       ("Other files", "other")):
        if contents.get(key):
            rows.append((f"    {label}", f"{contents[key]:,}", ""))

    if contents.get("categories"):
        rows.append(("BY CATEGORY", "", ""))
        for entry in contents["categories"]:
            rows.append((f"    {entry['name']}", f"{entry['count']:,}",
                         f"{entry['percent']}%"))

    if contents.get("flags"):
        rows.append(("BY FLAG", "", ""))
        for entry in contents["flags"]:
            rows.append((f"    {entry['name']}", f"{entry['count']:,}",
                         f"{entry['percent']}%"))
        rows.append(("    Files with at least one flag",
                     f"{contents['with_any_flag']:,}",
                     f"{contents['with_any_flag_percent']}%"))

    rows.append(("KNOWN-HASH", "", ""))
    rows.append(("    Files matching a known set",
                 f"{contents.get('known_hash', 0):,}", ""))

    if contents.get("total_in_case"):
        rows.append(("Total media in the case",
                     f"{contents['total_in_case']:,}", ""))
    return rows
