"""
kml_export.py -- put geolocated media on a map.

A photograph that records where it was taken answers a question no hash can:
the exhibit places a device somewhere, at a time. This writes those files out
as KML for Google Earth, or KMZ with the pictures embedded so the file can be
handed to someone who has no access to the evidence.

    python3 kml_export.py -m out/manifest.json -o out/locations.kmz --thumbs
    python3 kml_export.py -m out/manifest.json -o out/locations.kml

Only files that actually carry coordinates appear. Nothing is geocoded,
guessed, or looked up: a location here came out of the file itself, and the
placemark says which field it came from.
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
import json
import os
import sys
import zipfile
from fme.exports.docx_parts import xml_text as escape  # control chars, quotes
from fme.engines import timeutil

__version__ = "1.0.0"

MAX_EMBED_BYTES = 4 * 1024 * 1024       # per picture, when embedding


def _shown(value) -> str:
    """A time with its zone stated, or marked as having none recorded."""

    return timeutil.show(value) if value else ""


def _placemark(rec: dict, index: int, media_href: str = "") -> str:
    name = escape(os.path.basename(rec.get("out_path", f"file {index}")))
    rows = [
        ("Source path", rec.get("source_path", "")),
        ("Recovered by", rec.get("engine", "")),
        ("Captured", _shown(timeutil.stored(rec, "captured"))),
        ("Camera", " ".join(x for x in (rec.get("camera_make", ""),
                                        rec.get("camera_model", "")) if x)),
        ("Coordinates", f"{rec.get('latitude')}, {rec.get('longitude')}"),
        ("Altitude", "" if rec.get("altitude") is None
         else f"{rec['altitude']} m"),
        ("Size", f"{rec.get('size', 0):,} bytes"),
        ("MD5", rec.get("md5", "")),
        ("SHA-256", rec.get("sha256", "")),
        ("Deleted", "yes" if rec.get("deleted") else "no"),
    ]
    table = "".join(
        f"<tr><td><b>{escape(label)}</b></td><td>{escape(str(value))}</td></tr>"
        for label, value in rows if value not in ("", None))
    image = f'<img src="{escape(media_href)}" width="480"/><br/>' if media_href else ""
    description = f"<![CDATA[{image}<table>{table}</table>]]>"

    # A TimeStamp places the point on the map's time slider, which is a
    # moment. A capture time with no recorded zone is not one -- a viewer
    # reads it as UTC -- so it gets no TimeStamp; the description above
    # still carries it, as the camera recorded it.

    when = timeutil.stored(rec, "captured")
    timestamp = (f"<TimeStamp><when>{escape(when)}</when></TimeStamp>"
                 if when and timeutil.zoned(when) else "")
    return f"""    <Placemark>
      <name>{name}</name>
      <description>{description}</description>
      {timestamp}
      <styleUrl>#{'deleted' if rec.get('deleted') else 'recovered'}</styleUrl>
      <Point><coordinates>{rec['longitude']},{rec['latitude']},{rec.get('altitude') or 0}</coordinates></Point>
    </Placemark>"""


def build_kml(records: list, title: str, hrefs: dict) -> str:
    placemarks = "\n".join(_placemark(rec, i, hrefs.get(rec.get("out_path"), ""))
                           for i, rec in enumerate(records, start=1))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{escape(title)}</name>
    <description>{escape(f"{len(records)} recovered file(s) carrying coordinates")}</description>
    <Style id="recovered">
      <IconStyle><color>ff00aaff</color>
        <Icon><href>http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png</href></Icon>
      </IconStyle>
    </Style>
    <Style id="deleted">
      <IconStyle><color>ff0000ff</color>
        <Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href></Icon>
      </IconStyle>
    </Style>
{placemarks}
  </Document>
</kml>
"""


def export(manifest_path: str, out_path: str, *, embed: bool = False,
           include_duplicates: bool = False, title: str = "",
           manifest: dict = None) -> dict:
    """Write KML, or KMZ when the path ends in .kmz.

    Embedding puts the pictures inside the KMZ, which makes it self-contained
    at the cost of its size. Without it the placemarks carry the metadata and
    the paths, and nothing from the evidence travels.

    `manifest`, when given, replaces reading `manifest_path` (which still
    locates the case folder): a scoped report passes only its own records, so
    the map does not place files the report beside it left out.
    """
    if manifest is None:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    records = list(manifest.get("files", []))
    if include_duplicates:
        records += manifest.get("duplicates", [])

    located = [r for r in records
               if r.get("latitude") is not None and r.get("longitude") is not None]
    if not located:
        raise ValueError(
            "No recovered file carries coordinates. Most media has none: a "
            "camera records them only when location services were on.")

    base_dir = os.path.dirname(os.path.abspath(manifest_path))
    title = title or manifest.get("summary", {}).get("image", "Recovered media")
    kmz = out_path.lower().endswith(".kmz")

    hrefs, payloads = {}, []
    if embed and kmz:
        for rec in located:
            if rec.get("category") != "image":
                continue
            source = os.path.join(base_dir, rec.get("out_path", ""))
            try:
                if os.path.getsize(source) > MAX_EMBED_BYTES:
                    continue
                with open(source, "rb") as fh:
                    payloads.append((f"files/{os.path.basename(source)}", fh.read()))
                hrefs[rec["out_path"]] = payloads[-1][0]
            except OSError:
                continue

    kml = build_kml(located, title, hrefs)
    if kmz:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("doc.kml", kml)
            for name, blob in payloads:
                archive.writestr(name, blob)
    else:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(kml)

    return {"path": os.path.abspath(out_path), "placemarks": len(located),
            "embedded": len(payloads), "checked": len(records),
            "format": "kmz" if kmz else "kml"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write geolocated recovered media as KML or KMZ.")
    ap.add_argument("-m", "--manifest", required=True)
    ap.add_argument("-o", "--out", required=True,
                    help="output path; .kmz writes a zipped archive")
    ap.add_argument("--thumbs", "--embed", dest="embed", action="store_true",
                    help="embed the pictures in a KMZ so it is self-contained")
    ap.add_argument("--duplicates", action="store_true",
                    help="place every copy, not just one per unique file")
    ap.add_argument("--title", default="")
    args = ap.parse_args(argv)

    try:
        result = export(args.manifest, args.out, embed=args.embed,
                        include_duplicates=args.duplicates, title=args.title)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote {result['path']}")
    print(f"  placemarks : {result['placemarks']:,} of {result['checked']:,} file(s)")
    if result["embedded"]:
        print(f"  embedded   : {result['embedded']:,} picture(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
