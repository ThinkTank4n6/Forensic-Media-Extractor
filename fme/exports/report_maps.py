"""
report_maps.py -- locator maps for the report, drawn from an offline basemap.

The report used to show located files as a scatter of dots with no map
under them, because no map data is shipped and none may be fetched. An
examiner who imports a basemap (`fme basemap import region.pmtiles`) now
gets real maps: one overview of every located file the basemap covers, and
a small locator on each located file's card. Every map names the basemap it
was drawn on by name and SHA-256, so the picture can be reproduced.

A file outside the basemap gets no locator rather than a marker on an empty
square: a map that shows nothing around a point says nothing true.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

OVERVIEW = (640, 400)
LOCATOR = (240, 160)


class Maps:
    """The active basemap, with a tile cache shared by every map in one report."""

    def __init__(self, record: dict, provenance: dict):
        self.record = record
        self.provenance = provenance
        self.cache: dict = {}
        self.drawn = 0

    def covers(self, lat, lon) -> bool:
        from fme.engines import staticmap
        try:
            return staticmap.covers(self.record, float(lon), float(lat))
        except Exception:  # noqa: BLE001 - a bad coordinate is not a map
            return False

    def png(self, points, size) -> Optional[bytes]:
        """A PNG of these (lat, lon) points, or None if it could not be drawn."""
        from fme.engines import staticmap
        try:
            image = staticmap.render(self.record, [(lon, lat) for lat, lon in points],
                                     width=size[0], height=size[1],
                                     cache=self.cache)
        except Exception:  # noqa: BLE001 - one damaged tile must not stop a report
            return None
        if isinstance(image, tuple):
            image = image[0]
        self.drawn += 1
        return image

    def caption(self) -> str:
        p = self.provenance
        return (f"Drawn on the offline basemap “{p['name']}” "
                f"({p['format']}, SHA-256 {p['sha256']}). {p.get('attribution') or ''}")


def active() -> Optional[Maps]:
    """The basemap this machine draws on, or None (none imported, the file
    gone, or Pillow missing)."""
    try:
        from fme.engines import basemaps
        import PIL  # noqa: F401 - the renderer needs it
    except ImportError:
        return None
    provenance = basemaps.provenance()
    if provenance is None:
        return None
    record = basemaps.get(provenance["name"])
    return Maps(record, provenance) if record else None


def data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def locator_src(maps: Maps, row: dict, folder: str = "") -> str:
    """The src of a file's locator: a file beside the report when there is a
    folder for them, embedded otherwise. Empty when the basemap does not
    cover the file's position."""
    lat, lon = row.get("latitude"), row.get("longitude")
    if lat is None or lon is None or not maps.covers(lat, lon):
        return ""
    png = maps.png([(lat, lon)], LOCATOR)
    if not png:
        return ""
    if folder:
        name = f"map_{(row.get('sha256') or 'x')[:16]}.png"
        try:
            with open(os.path.join(folder, name), "wb") as fh:
                fh.write(png)
            return f"{os.path.basename(folder)}/{name}"
        except OSError:
            pass
    return data_uri(png)
