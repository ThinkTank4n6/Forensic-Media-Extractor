"""
report_html.py -- a contact sheet you can hand to someone, and an MD5 list.

One HTML file, no server, nothing fetched. Thumbnails are embedded so the
report survives being emailed or copied to a stick, and the tables carry what
an examiner needs to act on a row: where the file came from, when it was
taken, the hashes, whether it was deleted, and what any hash set said about
it.

    python3 report_html.py -m out/manifest.json -o out/report.html
    python3 report_html.py -m out/manifest.json -o out/report.html --no-embed
    python3 report_html.py -m out/manifest.json --md5 out/md5_list.csv

Thumbnails need Pillow. Without it the report is written with the pictures
linked rather than embedded, which works while the report sits beside the
media folder, and says so at the top rather than looking broken.
"""

from __future__ import annotations

# Allow this file to be run directly as well as imported. Run as a script
# there is no package context, so the project root goes on the path first.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import base64
import csv
import datetime as _dt
import html
import json
import os
import sys

__version__ = "1.0.0"

THUMB_SIZE = (220, 220)
MAX_EMBED_SOURCE = 64 * 1024 * 1024
BROWSER_SAFE = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "ico", "svg"}
# Shown as a picture through <img>, where a browser runs no script, but
# never linked: a link opens the suspect's file as a page from the case
# folder, and an SVG, an HTML or an XML file runs whatever script it holds.
ACTIVE_TYPES = {"svg", "svgz", "html", "htm", "xhtml", "xht", "shtml", "xml",
                "mht", "mhtml", "js"}

from fme.engines import imaging
from fme.engines import timeutil

try:
    from PIL import Image
    PILLOW = True
    try:
        import pillow_heif          # HEIC, which Pillow cannot read alone
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
except ImportError:                  # pragma: no cover
    Image = None
    PILLOW = False


def make_thumbnail(path: str) -> str:
    """A small JPEG as a data URI, or "" when one cannot be made."""
    if not PILLOW:
        return ""
    try:
        if os.path.getsize(path) > MAX_EMBED_SOURCE:
            return ""
        with imaging.open_image(path) as im:
            im.draft("RGB", THUMB_SIZE)
            im = im.convert("RGB")
            im.thumbnail(THUMB_SIZE, Image.LANCZOS)
            import io as _io
            buffer = _io.BytesIO()
            im.save(buffer, "JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(
            buffer.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - a truncated carve is not a failure here
        return ""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _tile(rec: dict, source_dir: str, embed: bool, matches: dict,
          zone: str = "") -> str:
    rel = rec.get("out_path", "")
    full = os.path.join(source_dir, rel)
    thumb = make_thumbnail(full) if embed else ""
    if not thumb and rec.get("ext", "").lower() in BROWSER_SAFE:
        thumb = rel.replace(os.sep, "/")        # linked, not embedded

    if thumb:
        preview = f'<img src="{_esc(thumb)}" alt="" loading="lazy">'
    else:
        preview = (f'<div class="noprev">{_esc(rec.get("ext", "?")).upper()}'
                   f'<br><span>no preview</span></div>')

    tags = []
    if rec.get("deleted"):
        tags.append('<span class="tag del">deleted</span>')
    if rec.get("engine") == "carve":
        tags.append('<span class="tag">carved</span>')
    if rec.get("engine") == "unlisted-after-crash":
        tags.append('<span class="tag del">origin unknown</span>')
    if rec.get("latitude") is not None:
        tags.append('<span class="tag gps">GPS</span>')
    hits = matches.get(rec.get("sha256"), [])
    for hit in hits:
        tags.append(f'<span class="tag hash">{_esc(hit["set"])}</span>')

    rows = [
        ("Path", rec.get("source_path") or (f"offset {rec['offset']}"
                                            if rec.get("offset") is not None else "")),
        ("Size", f"{rec.get('size', 0):,} bytes"),
        # Through timeutil like every other export: a UTC time is converted
        # and carries its offset, a device-clock time is labeled as one.
        # Cutting the string to 19 characters dropped the zone, so an NTFS
        # time in UTC printed exactly like a FAT time in local wall clock.
        ("Captured", timeutil.show(timeutil.stored(rec, "captured"), zone)),
        ("Camera", " ".join(x for x in (rec.get("camera_make", ""),
                                        rec.get("camera_model", "")) if x)),
        ("Coordinates", "" if rec.get("latitude") is None
         else f"{rec['latitude']}, {rec['longitude']}"),
        ("Modified", timeutil.show(timeutil.stored(rec, "mtime"), zone)),
        ("Engine", f"{rec.get('engine', '')} ({rec.get('signature', '')})"),
        ("MD5", rec.get("md5", "")),
        ("SHA-256", rec.get("sha256", "")),
        ("Copies", "" if rec.get("instances", 1) < 2
         else f"{rec['instances']} locations"),
    ]
    table = "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
                    for k, v in rows if v not in ("", None))
    if rec.get("ext", "").lower() in ACTIVE_TYPES:
        opened = (f'{preview}<div class="noprev"><span>not linked: opening it '
                  f'would run any script it holds</span></div>')
    else:
        opened = f'<a href="{_esc(rel.replace(os.sep, "/"))}">{preview}</a>'
    return f"""<figure class="tile">
  {opened}
  <figcaption>
    <div class="nm">{_esc(os.path.basename(rel))}</div>
    <div class="tags">{''.join(tags)}</div>
    <table>{table}</table>
  </figcaption>
</figure>"""


def build(manifest_path: str, embed: bool = True, limit: int = 0,
          category: str = "", matches_path: str = "",
          timezone: str = "") -> str:
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    records = manifest.get("files", [])
    summary = manifest.get("summary", {})
    source_dir = os.path.dirname(os.path.abspath(manifest_path))

    if category:
        records = [r for r in records if r.get("category") == category]
    if limit:
        records = records[:limit]

    matches: dict = {}
    matches_path = matches_path or os.path.join(source_dir, "hash_matches.csv")
    if os.path.isfile(matches_path):
        try:
            with open(matches_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row.get("in_sets"):
                        matches.setdefault(row.get("sha256", ""), []).append(
                            {"set": row["in_sets"].split(";")[0].strip()})
        except (OSError, csv.Error):
            matches = {}

    counts: dict = {}
    for rec in records:
        counts[rec.get("category", "other")] = counts.get(
            rec.get("category", "other"), 0) + 1
    breakdown = ", ".join(f"{count:,} {name}" for name, count
                          in sorted(counts.items(), key=lambda kv: -kv[1]))

    note = ""
    from fme.exports.report import EMBED_LIMIT
    if embed and len(records) > EMBED_LIMIT:
        # Every picture inside one page was built as one string in memory:
        # a case of 100,000 pictures was a page of gigabytes, made by the
        # interface's own process. Past the limit the report uses, linked.
        embed = False
        note = (f"<p class=\"warn\">{len(records):,} pictures are more than "
                f"{EMBED_LIMIT:,} that are embedded in one page, so they are "
                f"linked: this sheet works while it sits beside the media "
                f"folders.</p>")
    if embed and not PILLOW:
        note = ("<p class=\"warn\">Pillow is not installed, so the pictures are "
                "linked rather than embedded: this report works while it sits "
                "beside the media folders, but will not survive being moved on "
                "its own. <code>pip install pillow</code> to embed them.</p>")

    # What the run could not read, said on the sheet as in every report:
    # a contact sheet of a BitLocker laptop's system partition is not a
    # contact sheet of the laptop.
    from fme.exports.report import coverage_limits, decryption_notes
    for opened in decryption_notes(manifest):
        note += f"<p class=\"warn\">{_esc(opened)}</p>"
    for limit in coverage_limits(manifest):
        note += f"<p class=\"warn\">{_esc(limit)}</p>"

    tiles = "\n".join(_tile(rec, source_dir, embed, matches, timezone)
                      for rec in records)
    generated = timeutil.show(
        _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), timezone)
    zone_note = timeutil.describe(timezone or "UTC")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Recovered media — {_esc(os.path.basename(summary.get('image', '')))}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 0; background: #f4f5f7; color: #1d2129; }}
  header {{ background: #fff; border-bottom: 1px solid #d8dbe0; padding: 18px 24px; }}
  h1 {{ margin: 0 0 4px; font-size: 20px; }}
  .meta {{ color: #5a6472; font-size: 13px; }}
  .meta code {{ background: #eef0f3; padding: 1px 5px; border-radius: 3px; }}
  .warn {{ background: #fff6e5; border: 1px solid #f0c674; padding: 10px 14px;
          border-radius: 6px; margin: 12px 24px 0; }}
  .sheet {{ display: grid; gap: 14px; padding: 18px 24px;
           grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }}
  .tile {{ margin: 0; background: #fff; border: 1px solid #d8dbe0;
          border-radius: 8px; overflow: hidden; }}
  .tile a {{ display: block; background: #0d1117; text-align: center; }}
  .tile img {{ max-width: 100%; max-height: 220px; vertical-align: middle; }}
  .noprev {{ color: #8a94a6; padding: 60px 0; font-size: 12px; }}
  .noprev span {{ font-size: 11px; }}
  figcaption {{ padding: 8px 10px 10px; }}
  .nm {{ font-weight: 600; font-size: 12px; word-break: break-all; }}
  .tags {{ margin: 5px 0; display: flex; gap: 4px; flex-wrap: wrap; }}
  .tag {{ font-size: 10px; background: #eef0f3; border-radius: 3px;
         padding: 1px 6px; color: #444c59; }}
  .tag.del {{ background: #ffe3e3; color: #a61b1b; }}
  .tag.gps {{ background: #e3f1ff; color: #14508c; }}
  .tag.hash {{ background: #efe3ff; color: #5b2ea6; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 11px;
          font-family: ui-monospace, Menlo, Consolas, monospace; }}
  td {{ padding: 2px 0; vertical-align: top; word-break: break-all; }}
  td:first-child {{ color: #7a8394; width: 74px; padding-right: 6px;
                   font-family: inherit; }}
  footer {{ padding: 14px 24px 28px; color: #7a8394; font-size: 12px; }}
</style></head><body>
<header>
  <h1>Recovered media</h1>
  <div class="meta">
    Source <code>{_esc(summary.get('image', 'unknown'))}</code>
    &middot; {len(records):,} file(s) shown{f' &middot; {_esc(breakdown)}' if breakdown else ''}
    &middot; generated {_esc(generated)}
    &middot; times shown in {_esc(zone_note)}; times recorded with no zone are
    shown as recorded ({_esc(timeutil.AS_RECORDED)})
  </div>
</header>
{note}
<div class="sheet">
{tiles}
</div>
<footer>
  Categories are not set by this tool; it recovers, hashes and records
  provenance. A file matching no hash set is unknown, which is not the same
  as cleared.
</footer>
</body></html>
"""


def write_md5_list(manifest_path: str, out_path: str,
                   include_duplicates: bool = False, *,
                   manifest: dict = None) -> dict:
    """One distinct hash per row, the format a hash list is usually wanted in.

    `manifest`, when given, is used instead of reading the file: a report
    scoped to one category passes only the records in its scope, so the hash
    list beside it covers the same files rather than the whole run.
    """
    if manifest is None:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        hashes = write_md5_rows(fh, manifest, include_duplicates)
    return {"path": os.path.abspath(out_path), "hashes": hashes}


def write_md5_rows(fh, manifest: dict, include_duplicates: bool = False) -> int:
    """The MD5 list's rows, one per distinct MD5, written to an open file.

    The one writer for both the report's hash list and the interface's
    "export MD5s of these files": the second was a hand-joined string that
    quoted one column, left the rest raw, and let a file name beginning
    with = reach Excel as a formula. Returns the number of hashes written.
    """
    records = list(manifest.get("files", []))
    if include_duplicates:
        records += manifest.get("duplicates", [])

    seen = {}
    for rec in records:
        digest = (rec.get("md5") or "").lower()
        if digest and digest not in seen:
            seen[digest] = rec
    writer = csv.writer(fh)
    writer.writerow(["md5", "sha1", "sha256", "size", "category",
                     "file_name"])
    for digest, rec in seen.items():
        writer.writerow([digest, rec.get("sha1", ""), rec.get("sha256", ""),
                         rec.get("size", 0),
                         _safe_cell(rec.get("category", "")),
                         _safe_cell(os.path.basename(rec.get("out_path", "")))])
    return len(seen)


def _safe_cell(value):
    from fme.exports.report import spreadsheet_safe   # one rule for both

    return spreadsheet_safe(value)


def export(manifest_path: str, out_path: str, **kwargs) -> dict:
    html_text = build(manifest_path, **kwargs)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return {"path": os.path.abspath(out_path),
            "bytes": len(html_text.encode("utf-8")),
            "embedded": PILLOW and kwargs.get("embed", True)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write an HTML contact sheet, an MD5 list, or both.")
    ap.add_argument("-m", "--manifest", required=True)
    ap.add_argument("-o", "--out", default="", help="HTML output path")
    ap.add_argument("--md5", default="", help="also write an MD5 list here")
    ap.add_argument("--no-embed", dest="embed", action="store_false",
                    help="link the pictures instead of embedding them")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N files, for a quick look")
    ap.add_argument("--images", action="store_true")
    ap.add_argument("--videos", action="store_true")
    ap.add_argument("--timezone", default="",
                    help="zone to show times in, e.g. America/New_York "
                         "(default UTC)")
    args = ap.parse_args(argv)

    if not args.out and not args.md5:
        ap.error("give -o for the contact sheet, --md5 for a hash list, or both")

    category = "image" if args.images else "video" if args.videos else ""
    try:
        if args.out:
            result = export(args.manifest, args.out, embed=args.embed,
                            limit=args.limit, category=category,
                            timezone=args.timezone)
            size = result["bytes"] / (1024 * 1024)
            print(f"Wrote {result['path']} ({size:.1f} MB, "
                  f"{'thumbnails embedded' if result['embedded'] else 'pictures linked'})")
        if args.md5:
            listed = write_md5_list(args.manifest, args.md5)
            print(f"Wrote {listed['path']} ({listed['hashes']:,} distinct hash(es))")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
