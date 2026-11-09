"""
protocol.py -- the report a case is handed over with.

The shape follows what examiners in this field already read: what was
examined, what was found, by category and by device, then the signature and
the appendices. An examiner moving between tools should not have to learn a
new document, and a prosecutor should not have to be taught one.

The wording is this program's own, and two things in it are deliberate.

**Every count says what it counts.** Images and Video count files found,
copies included; Unique counts how many different files those are. Twenty
copies of one picture is twenty files and one picture, and a report giving
only one of those numbers invites the wrong reading of both.

**A finding is attributed.** A category applied by a hash set says so, and
is counted separately from one an examiner applied. "A hash is in a list" is
not "I looked at this", and a report that blurs the two is making a claim
nobody checked.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os

from fme.exports import docx_parts as dp

__version__ = "1.0.0"

DEFINITION = (
    "The categories used in this report are the examining unit's own. They "
    "describe what an examiner judged a file to be; they are not legal "
    "conclusions, and the decision as to what any file is remains with the "
    "court."
)


LOG = logging.getLogger("fme.protocol")


def _now() -> str:
    return _dt.datetime.now().replace(microsecond=0).strftime("%d %B %Y")


def _counts_table(rows, totals) -> list:
    table = [["Category", "Images", "Unique", "Video", "Unique", "Total"]]
    for entry in rows:
        table.append([entry["category"], f"{entry['images']:,}",
                      f"{entry['unique_images']:,}", f"{entry['videos']:,}",
                      f"{entry['unique_videos']:,}", f"{entry['total']:,}"])
    table.append(["Total", f"{totals['images']:,}",
                  f"{totals['unique_images']:,}", f"{totals['videos']:,}",
                  f"{totals['unique_videos']:,}", f"{totals['total']:,}"])
    return table


def _length_table(rows, totals, hms) -> list:
    table = [["Category", "Longest (hh:mm:ss)", "Shortest (hh:mm:ss)",
              "Total (hh:mm:ss)"]]
    for entry in rows:
        if not (entry["videos"] or entry["unreadable"]):
            continue
        table.append([entry["category"], hms(entry["longest"]),
                      hms(entry["shortest"]), hms(entry["sum"])])
    if len(table) == 1:
        return []
    table.append(["Total", hms(totals["longest"]), hms(totals["shortest"]),
                  hms(totals["sum"])])
    return table


def by_source(data: dict, lengths: dict, conclusion) -> list:
    """The same counts, per device, for a case with more than one exhibit."""
    def device(rec) -> str:
        return rec.get("source") or rec.get("partition") or ""

    # Every copy counted on the device it was found on. The case's whole
    # duplicate list went with each device's rows, so every copy of a file
    # was counted on the device of its first copy: a picture once on phone A
    # and twice on phone B read A = 3, B = 1. Each duplicate is a row of its
    # own here, on its own device, and none is added again.
    manifest = data.get("manifest", {}) or {}
    kept = {(r.get("sha256") or "").lower(): r for r in data["rows"]}
    sources = {}
    for row in data["rows"]:
        sources.setdefault(device(row), []).append(row)
    for extra in manifest.get("duplicates", []) or []:
        base = kept.get((extra.get("sha256") or "").lower())
        if base is not None:
            sources.setdefault(device(extra), []).append(base)
    if len(sources) <= 1:
        return []
    alone = {**manifest, "duplicates": []}
    out = []
    for where, rows in sorted(sources.items()):
        summary = conclusion({"rows": rows, "manifest": alone}, lengths)
        out.append((where or "(not recorded)", summary))
    return out


def by_flag(data: dict) -> list:
    """Counts per flag, within each category.

    A flag is the thing an examiner adds when a category is not enough --
    an age band, a series, a victim known to the unit -- and it is usually
    the part a reviewer turns to first.
    """
    found = {}
    for row in data["rows"]:
        category = row["category_name"]
        for flag in (row.get("flag_names") or "").split(", "):
            flag = flag.strip()
            if not flag:
                continue
            entry = found.setdefault((category, flag), {
                "category": category, "flag": flag, "images": 0,
                "videos": 0, "image_hashes": set(), "video_hashes": set()})
            digest = (row.get("sha256") or "").lower()
            if row.get("kind") == "video":
                entry["videos"] += 1
                entry["video_hashes"].add(digest)
            else:
                entry["images"] += 1
                entry["image_hashes"].add(digest)

    by_category = {}
    for entry in found.values():
        by_category.setdefault(entry["category"], []).append({
            "flag": entry["flag"], "images": entry["images"],
            "unique_images": len(entry["image_hashes"]),
            "videos": entry["videos"],
            "unique_videos": len(entry["video_hashes"]),
            "total": entry["images"] + entry["videos"]})
    for rows in by_category.values():
        rows.sort(key=lambda r: (-r["total"], r["flag"]))
    return sorted(by_category.items())


def write(data: dict, out_path: str, *, header: dict, scope: dict,
          conclusion, durations, hms, thumbnails: bool = False,
          blur: bool = False, thumbnail_of=None, file_list: bool = True,
          introduction: str = "", definition: str = "",
          progress=None, cancel=None) -> dict:
    """Write the protocol report."""
    lengths = durations(data["out_dir"], data["rows"], progress=progress)
    summary = conclusion(data, lengths)
    rows, totals = summary["rows"], summary["totals"]

    media, relationships, body = [], [], []

    # The unit's logo, where one is set. A prosecutor should be able to see
    # whose work they are reading without finding the agency line.
    logo = header.get("logo") or ""
    if logo and os.path.isfile(logo):
        try:
            from fme.engines import imaging

            with imaging.open_image(logo) as picture:
                picture = picture.convert("RGB")
                width, height = picture.size
                # Sized to the page, not to whatever the file happens to be.
                scale = min(180.0 / max(1, width), 60.0 / max(1, height), 1.0)
                import io as _io

                buffer = _io.BytesIO()
                picture.save(buffer, format="JPEG", quality=90)
                media.append((f"media/logo.jpeg", buffer.getvalue()))
                relationships.append(
                    f'<Relationship Id="rIdLogo" Type="http://schemas.'
                    f'openxmlformats.org/officeDocument/2006/relationships/'
                    f'image" Target="media/logo.jpeg"/>')
                body.append(_picture("rIdLogo", 1_000_000, int(width * scale),
                                     int(height * scale)))
        except Exception:  # noqa: BLE001 - a report without a logo is fine
            pass

    body.append(dp.heading(header.get("title") or "Investigation protocol", 1))
    body.append(dp.table([
        ["Agency", header.get("agency", "")],
        ["Case number", header.get("case_number", "")],
        ["Evidence item", header.get("evidence", "")],
        ["Examiner", header.get("examiner", "")],
        ["Source examined", data["manifest"].get("summary", {}).get("image", "")],
        ["Report written", _now()],
        # Which build did the work, taken from the manifest the run
        # wrote rather than from the program writing this report: a
        # case can be reopened under a newer version, and the one that
        # matters is the one that produced the findings.
        ["Produced by", _built_by(data)],
    ], header=False, widths=[int(dp.PAGE_WIDTH * 0.28),
                             int(dp.PAGE_WIDTH * 0.72)]))

    # What the run could not read, before any count: a locked partition or
    # an unreadable volume means the numbers below are not the whole
    # source, and a protocol that leaves that out says the opposite.
    if data.get("evidence"):
        body.append(dp.heading("The evidence", 2))
        for note in data["evidence"]:
            body.append(dp.paragraph(note))
    if data.get("coverage_limits"):
        body.append(dp.heading("What this report does not cover", 2))
        for limit in data["coverage_limits"]:
            body.append(dp.paragraph(limit))

    # What this report covers, from the same count the HTML uses, with
    # the same donut. Three documents from one set of numbers: an HTML
    # saying ninety-six and a Word document saying ninety-five turns a
    # hearing about pictures into a hearing about the tool.
    try:
        from fme.exports import report_contents

        contents = report_contents.compute(
            data["rows"], data.get("scheme") or [],
            data.get("total_in_run") or len(data["rows"]))
        if contents["files"]:
            body.append(dp.heading("Report contents", 2))

            picture = report_contents.donut(contents, size=340)
            if picture:
                media.append(("media/contents.png", picture))
                relationships.append(
                    '<Relationship Id="rIdDonut" Type="http://schemas.'
                    'openxmlformats.org/documentDocument/2006/relationships/'
                    'image" Target="media/contents.png"/>'
                    .replace("documentDocument", "officeDocument"))
                body.append(_picture("rIdDonut", 1_000_001, 190, 190))

            body.append(dp.table(
                [[label.strip(), value, note]
                 for label, value, note in report_contents.as_rows(contents)],
                header=False,
                widths=[int(dp.PAGE_WIDTH * 0.60),
                        int(dp.PAGE_WIDTH * 0.22),
                        int(dp.PAGE_WIDTH * 0.18)]))
    except Exception as exc:  # noqa: BLE001 - a report is not lost over it
        LOG.warning("the contents block was left out: %s", exc)

    body.append(dp.heading("Introduction", 2))
    body.append(dp.paragraph(
        introduction or
        f"The media described in this report were recovered from the source "
        f"named above and reviewed by the examiner named above. Each file was "
        f"placed in a category by that examiner, or by a hash set where one "
        f"recognized it; categories applied by a hash set are identified as "
        f"such. The categories are the unit's own and are not legal "
        f"conclusions."))
    if header.get("notes"):
        body.append(dp.paragraph(header["notes"]))

    body.append(dp.heading("Conclusion", 2))
    body.append(dp.table(_counts_table(rows, totals), total_row=True))
    body.append(dp.paragraph(
        "Images and Video count every file found, copies included. Unique "
        "counts how many different files those are, by SHA-256.",
        size=16, color="5A6472"))

    lengths_table = _length_table(rows, totals, hms)
    if lengths_table:
        body.append(dp.heading("Overview of video length", 2))
        body.append(dp.table(lengths_table, total_row=True))
        if totals.get("unreadable"):
            body.append(dp.paragraph(
                f"{totals['unreadable']:,} video(s) could not be decoded and "
                f"have no length here; they are counted as files above. A "
                f"carved clip often starts mid-stream, which is enough to "
                f"recover the content and not enough to measure it.",
                size=16, color="5A6472"))

    devices = by_source(data, lengths, conclusion)
    if devices:
        body.append(dp.heading("Overview of seized objects", 2))
        for where, device in devices:
            body.append(dp.paragraph(where, bold=True, size=20,
                                     space_after=40))
            body.append(dp.table(_counts_table(device["rows"],
                                               device["totals"]),
                                 total_row=True))

    flags = by_flag(data)
    if flags:
        body.append(dp.heading("Overview of flags", 2))
        for category, entries in flags:
            body.append(dp.paragraph(f"Category: {category}", bold=True,
                                     size=20, space_after=40))
            table = [["Flag", "Images", "Unique", "Video", "Unique", "Total"]]
            for entry in entries:
                table.append([entry["flag"], f"{entry['images']:,}",
                              f"{entry['unique_images']:,}",
                              f"{entry['videos']:,}",
                              f"{entry['unique_videos']:,}",
                              f"{entry['total']:,}"])
            body.append(dp.table(table))

    # The signature block, left for the examiner to sign.
    body.append(dp.paragraph("", space_after=240))
    body.append(dp.paragraph(
        f"{header.get('place', '')}{', ' if header.get('place') else ''}"
        f"{_now()}", size=20))
    body.append(dp.paragraph("", space_after=320))
    body.append(dp.paragraph("_______________________________", size=20,
                             space_after=40))
    body.append(dp.paragraph(header.get("examiner", ""), size=20,
                             space_after=20))
    body.append(dp.paragraph(header.get("agency", ""), size=18,
                             color="5A6472"))

    appendix = 1
    if thumbnails and thumbnail_of is not None:
        body.append(dp.page_break())
        body.append(dp.heading(f"Appendix {appendix}. Representative images "
                               f"and videos", 2))
        body.append(dp.paragraph(
            "One image per file in the categories this report covers."
            + (" Shown blurred, which hides detail for a reader and is not a "
               "safeguard: the files themselves are unchanged."
               if blur else ""),
            size=16, color="5A6472"))
        shown = 0
        for row in data["rows"]:
            if cancel is not None and cancel():
                break
            picture = thumbnail_of(row, data, blur=blur)
            if not picture:
                continue
            name = f"media/image{len(media) + 1}.jpeg"
            media.append((name, picture["bytes"]))
            reference = f"rIdImage{len(media)}"
            relationships.append(
                f'<Relationship Id="{reference}" Type="http://schemas.'
                f'openxmlformats.org/officeDocument/2006/relationships/image" '
                f'Target="{name}"/>')
            body.append(dp.paragraph(row.get("name", ""), bold=True, size=16,
                                     space_after=20))
            body.append(_picture(reference, len(media), picture["width"],
                                 picture["height"]))
            body.append(dp.paragraph(
                f"{row.get('category_name', '')} · SHA-256 "
                f"{row.get('sha256', '')}", size=14, color="5A6472",
                space_after=140))
            shown += 1
            if progress and shown % 25 == 0:
                progress(shown, len(data["rows"]))
        appendix += 1

    body.append(dp.page_break())
    body.append(dp.heading(f"Appendix {appendix}. Definitions", 2))
    body.append(dp.paragraph(definition or DEFINITION))
    appendix += 1

    if file_list:
        body.append(dp.page_break())
        body.append(dp.heading(f"Appendix {appendix}. File list", 2))
        listing = [["File", "Category", "Size", "SHA-256"]]
        for row in data["rows"]:
            listing.append([
                row.get("name", ""), row.get("category_name", ""),
                f"{int(row.get('size') or 0):,}",
                (row.get("sha256") or "")[:32] + "…"])
        body.append(dp.table(listing, widths=[
            int(dp.PAGE_WIDTH * 0.30), int(dp.PAGE_WIDTH * 0.22),
            int(dp.PAGE_WIDTH * 0.12), int(dp.PAGE_WIDTH * 0.36)]))

    dp.write(out_path, "".join(body), media, relationships)
    return {"path": os.path.abspath(out_path), "files": len(data["rows"]),
            "bytes": os.path.getsize(out_path), "images": len(media)}


def _built_by(data: dict) -> str:
    """The build that produced the case, for the header table."""
    summary = ((data or {}).get("manifest") or {}).get("summary") or {}
    if not summary:
        summary = (data or {}).get("summary") or {}
    version = summary.get("tool_version") or ""
    mark = (summary.get("code_fingerprint") or "")[:12]
    if not version:
        return "not recorded (the case predates version stamping)"
    return version + (f" \u00b7 {mark}" if mark else "")


def _picture(reference: str, index: int, width: int, height: int) -> str:
    """Word's drawing element for an embedded image.

    `index` must be unique in the document. The logo and the donut were 900
    and 901, which the appendix's thumbnails (numbered from 1) reach in a
    report of 900 pictures -- and Word refuses a document with two drawings
    of one id. They sit far past any count of thumbnails now."""
    emu_width = int(width * 9525)
    emu_height = int(height * 9525)
    return (
        f'<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" '
        f'distR="0"><wp:extent cx="{emu_width}" cy="{emu_height}"/>'
        f'<wp:docPr id="{index}" name="Picture {index}"/><a:graphic>'
        f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/'
        f'2006/picture"><pic:pic><pic:nvPicPr>'
        f'<pic:cNvPr id="{index}" name="Picture {index}"/><pic:cNvPicPr/>'
        f'</pic:nvPicPr><pic:blipFill><a:blip r:embed="{reference}"/>'
        f'<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr>'
        f'<a:xfrm><a:off x="0" y="0"/>'
        f'<a:ext cx="{emu_width}" cy="{emu_height}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        f'</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'
        f'</w:r></w:p>')
