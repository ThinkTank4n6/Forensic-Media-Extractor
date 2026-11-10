"""
manual.py -- the manual, rendered for the interface.

A small Markdown subset is enough for a document written for this purpose:
headings, paragraphs, lists, tables, code blocks, bold, inline code and
links. Converting it here rather than depending on a Markdown library keeps
the manual readable as a text file for anyone who opens it in an editor, and
keeps the program's one required dependency at one.
"""

from __future__ import annotations

import html
import os
import re

__version__ = "1.0.0"


def document_path(name: str = "MANUAL.md") -> str:
    """Find a shipped document, wherever the program was unpacked."""
    from fme import resources

    return resources.document(name)


def manual_path() -> str:
    return document_path("MANUAL.md")


def _inline(text: str) -> str:
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    return out


def to_html(markdown: str) -> str:
    """The subset the manual uses, and nothing else."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    out, index = [], 0
    in_list = in_table = False

    def close():
        nonlocal in_list, in_table
        if in_list:
            out.append("</ul>")
            in_list = False
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            close()
            index += 1
            block = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(html.escape(lines[index]))
                index += 1
            out.append("<pre><code>" + "\n".join(block) + "</code></pre>")
            index += 1
            continue

        if not stripped:
            close()
            index += 1
            continue

        if stripped.startswith("#"):
            close()
            level = len(stripped) - len(stripped.lstrip("#"))
            text = _inline(stripped[level:].strip())
            anchor = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            out.append(f'<h{level} id="{anchor}">{text}</h{level}>')
            index += 1
            continue

        if stripped == "---":
            close()
            out.append("<hr>")
            index += 1
            continue

        # A table: a header row, a separator of dashes, then the body.
        if (stripped.startswith("|") and index + 1 < len(lines)
                and set(lines[index + 1].strip()) <= set("|-: ")):
            close()
            headers = [c.strip() for c in stripped.strip("|").split("|")]
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{_inline(h)}</th>" for h in headers)
                       + "</tr></thead><tbody>")
            in_table = True
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [c.strip() for c in lines[index].strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells)
                           + "</tr>")
                index += 1
            close()
            continue

        if re.match(r"^[-*] ", stripped) or re.match(r"^\d+\. ", stripped):
            if not in_list:
                out.append("<ul>")
                in_list = True
            text = re.sub(r"^([-*]|\d+\.)\s+", "", stripped)
            out.append(f"<li>{_inline(text)}</li>")
            index += 1
            continue

        close()
        paragraph = [stripped]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(
                r"^(#|\||```|[-*] |\d+\. |---$)", lines[index].strip()):
            paragraph.append(lines[index].strip())
            index += 1
        out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")

    close()
    return "\n".join(out)


def contents(markdown: str) -> str:
    """A list of the numbered sections, for jumping about in a long page."""
    items = []
    for line in markdown.split("\n"):
        if line.startswith("## "):
            text = line[3:].strip()
            anchor = re.sub(r"[^a-z0-9]+", "-", html.escape(text).lower()).strip("-")
            items.append(f'<li><a href="#{anchor}">{html.escape(text)}</a></li>')
    return "<ul class='toc'>" + "".join(items) + "</ul>"


STYLE = """
 body { font: 15px/1.65 -apple-system, Segoe UI, Roboto, sans-serif;
        margin: 0; background: #11151c; color: #c9d1d9; }
 .page { max-width: 900px; margin: 0 auto; padding: 28px 30px 80px; }
 h1 { font-size: 26px; margin: 0 0 6px; color: #fff; }
 h2 { font-size: 20px; margin: 34px 0 10px; padding-top: 14px;
      border-top: 1px solid #263040; color: #fff; }
 h3 { font-size: 16px; margin: 22px 0 6px; color: #fff; }
 p { margin: 10px 0; }
 ul { margin: 8px 0; padding-left: 22px; }
 li { margin: 3px 0; }
 code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px;
        background: #1b212b; padding: 1px 5px; border-radius: 3px; }
 pre { background: #0b0e13; border: 1px solid #263040; border-radius: 8px;
       padding: 12px 14px; overflow-x: auto; }
 pre code { background: none; padding: 0; font-size: 12.5px; line-height: 1.5; }
 table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
 th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #263040;
          vertical-align: top; }
 th { color: #8b949e; font-weight: 600; font-size: 12px;
      text-transform: uppercase; letter-spacing: .04em; }
 hr { border: none; border-top: 1px solid #263040; margin: 26px 0; }
 a { color: #4a9eff; }
 .toc { columns: 2; margin: 14px 0 6px; padding-left: 20px; }
 strong { color: #fff; }
 @media print { body { background: #fff; color: #000; }
                pre, code { background: #f2f3f5; } a { color: #000; } }
"""


def render(release: str = "", name: str = "MANUAL.md") -> str:
    """A shipped document as a standalone page."""
    path = document_path(name)
    if not path:
        return (f"<!doctype html><meta charset='utf-8'><p>{name} was not "
                f"found beside the program.</p>")
    with open(path, "r", encoding="utf-8") as fh:
        markdown = fh.read()
    body = to_html(markdown)
    # Put the contents after the opening section rather than at the very top,
    # so someone who opens the manual to read it is not met by an index.
    anchor = "<hr>"
    if anchor in body:
        body = body.replace(anchor, "<h2>Contents</h2>" + contents(markdown)
                            + "<hr>", 1)
    return (f"<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
            f"<title>Manual — Forensic Media Extractor {html.escape(release)}</title>"
            f"<style>{STYLE}</style></head><body><div class='page'>{body}"
            f"</div></body></html>")
