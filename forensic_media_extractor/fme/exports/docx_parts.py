"""
docx_parts.py -- the pieces a Word document is made of.

A .docx is a zip of XML, so these are built directly rather than through a
library that would have to be installed on every examination machine. The
format is verbose but not complicated, and what it buys is a report that
opens in Word, LibreOffice and Google Docs from a program whose only
requirement is Flask.

Only what a forensic report actually uses is here: headings, paragraphs,
tables with a header row, page breaks and embedded pictures. Anything
fancier belongs in the HTML report, which has no such constraints.
"""

from __future__ import annotations

import re

__version__ = "1.0.0"

NAMESPACES = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)

# Word measures in twentieths of a point, and a page of US Letter with one
# inch margins is 9360 of them wide.
PAGE_WIDTH = 9360


# Characters XML 1.0 cannot carry at all, escaped or not. A file name from
# the evidence can hold any of them, and one was enough for Word to call the
# whole document unreadable.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")


def xml_text(value) -> str:
    """Text for an XML document: escaped, and with any character XML 1.0
    forbids written visibly as \\xNN -- shown, not silently dropped, since
    it is part of what the evidence recorded."""
    text = str("" if value is None else value)
    text = _NOT_XML.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def escape(value) -> str:
    return xml_text(value)


def paragraph(text="", *, bold=False, italic=False, size=20, color="000000",
              space_after=60, align="", style="") -> str:
    """One paragraph. Size is in half-points, as Word counts them."""
    properties = [f'<w:spacing w:after="{space_after}"/>']
    if align:
        properties.append(f'<w:jc w:val="{align}"/>')
    if style:
        properties.insert(0, f'<w:pStyle w:val="{escape(style)}"/>')
    runs = ""
    if text != "":
        runs = (f'<w:r><w:rPr>{"<w:b/>" if bold else ""}'
                f'{"<w:i/>" if italic else ""}'
                f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
                f'<w:color w:val="{color}"/></w:rPr>'
                f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>')
    return (f'<w:p><w:pPr>{"".join(properties)}</w:pPr>{runs}</w:p>')


def heading(text, level=1) -> str:
    """A heading, sized by level and bold, with space above it."""
    sizes = {1: 32, 2: 26, 3: 22}
    return (f'<w:p><w:pPr><w:spacing w:before="{240 if level == 1 else 200}" '
            f'w:after="100"/></w:pPr>'
            f'<w:r><w:rPr><w:b/><w:sz w:val="{sizes.get(level, 22)}"/>'
            f'<w:szCs w:val="{sizes.get(level, 22)}"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def _cell(text, width, *, bold=False, shade="", align="left", size=18) -> str:
    properties = [f'<w:tcW w:w="{width}" w:type="dxa"/>']
    if shade:
        properties.append(f'<w:shd w:val="clear" w:color="auto" '
                          f'w:fill="{shade}"/>')
    properties.append('<w:vAlign w:val="center"/>')
    return (f'<w:tc><w:tcPr>{"".join(properties)}</w:tcPr>'
            + paragraph(text, bold=bold, size=size, space_after=20,
                        align="right" if align == "right" else "")
            + '</w:tc>')


def table(rows, *, widths=None, header=True, shade="E8EAED",
          total_row=False) -> str:
    """A table. The first row is a header unless told otherwise.

    Numbers are right-aligned by giving a cell as ("123", "right"); plain
    strings are left-aligned. A total row is shaded and bold, because a
    reader looks for it first.
    """
    if not rows:
        return ""
    columns = max(len(row) for row in rows)
    if not widths:
        first = int(PAGE_WIDTH * 0.34)
        rest = (PAGE_WIDTH - first) // max(1, columns - 1)
        widths = [first] + [rest] * (columns - 1)

    body = []
    for index, row in enumerate(rows):
        is_header = header and index == 0
        is_total = total_row and index == len(rows) - 1
        cells = []
        for column in range(columns):
            value = row[column] if column < len(row) else ""
            align = "left"
            if isinstance(value, (tuple, list)):
                value, align = value[0], (value[1] if len(value) > 1 else "left")
            elif column > 0:
                align = "right"
            cells.append(_cell(
                value, widths[column] if column < len(widths) else widths[-1],
                bold=is_header or is_total,
                shade=shade if is_header else ("F3F4F6" if is_total else ""),
                align=align))
        body.append(f'<w:tr>{"".join(cells)}</w:tr>')

    borders = "".join(
        f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="C8CCD2"/>'
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"))
    # The column grid. Word renders a table without it, and other readers
    # refuse the document outright -- a report that opens on the examiner's
    # machine and not on the prosecutor's is worse than no report.
    grid = "".join(
        f'<w:gridCol w:w="{widths[column] if column < len(widths) else widths[-1]}"/>'
        for column in range(columns))
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{PAGE_WIDTH}" w:type="dxa"/>'
            f'<w:tblBorders>{borders}</w:tblBorders></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>'
            + "".join(body) + '</w:tbl>' + paragraph("", space_after=120))


def document(body: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document {NAMESPACES}><w:body>{body}'
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" '
            'w:left="1440" w:header="720" w:footer="720"/></w:sectPr>'
            '</w:body></w:document>')


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
    'package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="jpeg" ContentType="image/jpeg"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>')

ROOT_RELATIONSHIPS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.'
    'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>')


def write(path: str, body: str, media=None, relationships=None) -> str:
    """Write the document to disk as a .docx."""
    import os
    import zipfile

    media = media or []
    relationships = relationships or []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELATIONSHIPS)
        archive.writestr("word/document.xml", document(body))
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships">' + "".join(relationships) +
            '</Relationships>')
        for name, blob in media:
            archive.writestr(f"word/{name}", blob)
    return os.path.abspath(path)
