"""
timeutil.py -- what time it was, where it happened.

Every timestamp this program records is kept with the zone it was recorded
in -- UTC wherever the source says UTC, which is right: it is unambiguous,
it survives being copied between machines, and it never shifts under
daylight saving. A time the evidence recorded with no zone (a FAT file time,
a camera's clock) is kept with none and never given one; see parse() and
aware(). A UTC time is also not what anybody wants to read. A warrant
is executed at a local time, a witness remembers a local time, and a
timeline presented in UTC has to be converted in somebody's head -- which
is where mistakes enter.

So a case records the zone its times should be *shown* in, and every place
a time is shown converts to it. The stored value never changes.

**Daylight saving is the point.** A picture taken in New York in July and
one taken in December are four and five hours from UTC respectively.
Converting with a fixed offset puts one of them an hour wrong, and an hour
is the difference between somebody being at work and being at home. This
uses the zone database, which knows the rules and their history.

**And the zone is always stated.** A time shown without its zone is a time
somebody will read in their own. Every converted time carries its offset,
and a report says in its header which zone it used -- including when that
zone is UTC, because "UTC" written down is a fact and a bare time is an
assumption.
"""

from __future__ import annotations

import datetime as _dt

__version__ = "1.1.0"

UTC = _dt.timezone.utc


def available_zones() -> list:
    """The zones this machine knows, for an examiner to choose from."""
    try:
        import zoneinfo
        return sorted(zoneinfo.available_timezones())
    except Exception:  # noqa: BLE001 - no tz database on this machine
        return []


def zone(name: str):
    """A zone by name, or None where it is not one this machine knows."""
    if not name or name.upper() == "UTC":
        return UTC
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return None


def known(name: str) -> bool:
    return zone(name) is not None


def parse(value) -> _dt.datetime:
    """A stored timestamp, whatever shape it was kept in.

    One that carries its zone comes back aware. One that does not comes back
    naive, as it is: it was not recorded in UTC, or anywhere. A FAT time or
    an EXIF capture time is what the device's clock said, in whatever zone
    that clock was set to, and this used to read every such time as UTC --
    so the report shifted a camera's 14:00 to 10:00 EDT and printed the zone
    as though it were known. Use aware() for a time that must be placed on
    the UTC line, and expect None for these.
    """
    if value in (None, "", 0):
        return None
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        found = _dt.datetime.fromisoformat(text)
    except ValueError:
        for shape in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S",
                      "%Y-%m-%dT%H:%M:%S"):
            try:
                found = _dt.datetime.strptime(text, shape)
                break
            except ValueError:
                continue
        else:
            return None
    return found


def zoned(value) -> bool:
    """Does this stored time say which zone it was recorded in?"""
    when = parse(value)
    return when is not None and when.tzinfo is not None


def aware(value):
    """A stored time on the UTC line, or None where it cannot be placed
    there: absent, unreadable, or recorded with no zone. Every export that
    writes an absolute moment -- an epoch, an offset -- goes through this,
    so none of them invents a zone. C4All and LAVA used the examining
    machine's own zone for a zone-less time, so the same evidence exported
    on two lab machines gave two different moments."""
    when = parse(value)
    return when if when is not None and when.tzinfo is not None else None


# How a time with no recorded zone is marked wherever it is shown. The page
# says "(as recorded)"; a report, read by people who never saw the page,
# says what that means.
AS_RECORDED = "device clock; zone not recorded"


def show(value, name: str = "", with_zone: bool = True) -> str:
    """A stored time, written in the zone the case chose.

    An unreadable or absent time comes back empty rather than as a guess:
    an invented timestamp in a report is worse than a blank one.
    """
    when = parse(value)
    if when is None:
        return ""
    if isinstance(value, str) and len(value.strip()) == 10 \
            and when.tzinfo is None:
        # A date and no time (a FAT access date): shown as the date, not as
        # midnight, which is a time nothing recorded.
        text = f"{when.year:04d}" + when.strftime("-%m-%d")
        return f"{text} (date only; {AS_RECORDED})" if with_zone else text
    if when.tzinfo is None:
        # Shown as the device recorded it, never converted: converting needs
        # a zone, and the evidence did not give one.
        text = f"{when.year:04d}" + when.strftime("-%m-%d %H:%M:%S")
        return f"{text} ({AS_RECORDED})" if with_zone else text
    where = zone(name) or UTC
    local = _converted(when, where)
    text = f"{local.year:04d}" + local.strftime("-%m-%d %H:%M:%S")
    if not with_zone:
        return text
    # The offset, so the reader is never converting in their head, and the
    # abbreviation where the zone has one that day -- EST and EDT are four
    # letters that save an hour of argument.
    offset = local.strftime("%z")
    # +HHMM, or +HHMMSS for an old local mean time (+09:18:59 in Tokyo
    # before 1888): each pair separated, not "+09:1859".
    offset = (":".join([offset[:3]] + [offset[i:i + 2]
                                        for i in range(3, len(offset), 2)])
              if offset else "+00:00")
    label = local.tzname() or ""
    return f"{text} {offset}" + (f" ({label})" if label and label != "UTC"
                                 else (" (UTC)" if offset == "+00:00" else ""))


def describe(name: str) -> str:
    """How a report should say which zone it used."""
    where = zone(name)
    if where is None:
        return (f"{name} is not a zone this machine knows, so times are "
                f"shown in UTC")
    if where is UTC:
        return "UTC"
    now = _dt.datetime.now(where)
    offset = now.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
    return f"{name} ({now.tzname()}, UTC{offset} at the time of writing)"


# --------------------------------------------------------------------------
# Where a record keeps its times
# --------------------------------------------------------------------------
# A record's time fields hold instants only: a time that says which zone it
# was recorded in. What the evidence recorded with no zone -- a FAT or exFAT
# file time, a camera's clock with no offset -- is a reading, not an instant,
# and is kept apart in `recorded_times` (JSON text, field name -> reading).
#
# Kept together, the two could only be told apart by the end of a string, and
# three releases in a row found code that cut that end off: the contact sheet
# printed UTC as local time, the report's summary and the gallery's filters
# dated evening videos a day late. Kept apart, a reading cannot be converted,
# exported as an epoch or bucketed as UTC by mistake, because it is not where
# an instant is looked for. (The separation follows the comparison tool's
# design, recorded in the internal comparison document.)
#
# settle() is the one gate on the way in (OutputManager.store). Everything
# that reads a time goes through stored(), instant() or reading(), which also
# read manifests written before the split, where a reading sat in the field
# itself. tests/test_time_model.py refuses a reader that goes around them.

TIME_FIELDS = ("captured", "crtime", "mtime", "atime", "ctime")


def _field(rec, name):
    if isinstance(rec, dict):
        return rec.get(name)
    return getattr(rec, name, None)


def readings(rec) -> dict:
    """The zone-less readings a record carries, by field name."""
    raw = _field(rec, "recorded_times")
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        import json
        found = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def instant(rec, name: str):
    """The field as an instant (aware datetime), or None."""
    return aware(_field(rec, name))


def reading(rec, name: str):
    """The field as a zone-less reading (naive datetime), or None.

    From `recorded_times`; in a manifest written before it existed, from the
    field itself where the value there carries no zone.
    """
    when = parse(readings(rec).get(name))
    if when is not None and when.tzinfo is None:
        return when
    legacy = parse(_field(rec, name))
    return legacy if legacy is not None and legacy.tzinfo is None else None


def stored(rec, name: str) -> str:
    """The text of whichever this field holds -- instant or reading -- or "".

    An instant keeps its offset and a reading has none, so show(), aware()
    and the page's own checks still tell them apart. This is the text every
    reader used to take from the field directly.
    """
    value = _field(rec, name)
    if value not in (None, ""):
        return str(value)
    return str(readings(rec).get(name) or "")


def merged(rec: dict) -> dict:
    """A copy of a record with each time field holding stored()'s text.

    For handing a record to something that shows it -- the page's details
    pane, a gallery row -- rather than to code that should know the
    difference.
    """
    out = dict(rec)
    for name in TIME_FIELDS:
        out[name] = stored(rec, name)
    return out


def settle(rec) -> None:
    """Put each of a record's times where it belongs, in place.

    An instant stays in its field, written as ISO 8601 with its offset (an
    epoch number becomes UTC). A reading moves to `recorded_times`, written as
    ISO 8601 with no zone. A value that is not a time at all moves there as
    the text it was, so nothing the evidence said is lost -- and nothing reads
    it as an instant.
    """
    import json

    kept = readings(rec)
    for name in TIME_FIELDS:
        value = _field(rec, name)
        if value in (None, ""):
            continue
        when = parse(value)
        if when is not None and when.tzinfo is not None:
            text = when.isoformat()
        elif when is not None and isinstance(value, str) \
                and len(value.strip()) == 10:
            kept[name] = value.strip()     # a date with no time: kept so
            text = ""
        else:
            kept[name] = when.isoformat() if when is not None else str(value)
            text = ""
        if isinstance(rec, dict):
            rec[name] = text
        else:
            setattr(rec, name, text)
    encoded = json.dumps(kept, sort_keys=True) if kept else ""
    if isinstance(rec, dict):
        rec["recorded_times"] = encoded
    else:
        rec.recorded_times = encoded


def day_in(value, name: str = "") -> str:
    """The date a time falls on: an instant in zone `name`, a reading as
    recorded. "" for nothing or not a time."""
    when = parse(value)
    if when is None:
        return ""
    if when.tzinfo is not None:
        when = _converted(when, zone(name) or UTC)
    # Padded by hand: %Y gives "1", not "0001", on Linux.
    return f"{when.year:04d}" + when.strftime("-%m-%d")


def _converted(when, where):
    """`when` in zone `where`; in UTC where that would leave the calendar.

    0001-01-01T00:00:00Z -- Project VIC's "unknown time" -- is a day before
    year 1 anywhere west of Greenwich, and astimezone raised OverflowError,
    which stopped every report that displayed it. Shown in UTC instead, with
    its offset, it is still exactly the instant recorded.
    """
    try:
        return when.astimezone(where)
    except (OverflowError, ValueError):
        pass
    try:
        return when.astimezone(UTC)
    except (OverflowError, ValueError):
        # Not even UTC lies inside the calendar (00:30 on 1 January of
        # year 1 at +01:00): shown as recorded, with the offset it carries.
        return when
