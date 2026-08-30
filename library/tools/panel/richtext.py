"""The Qt 5.15 rich-text subset the panel draws in, in one place.

Resolve's `UIManager` is Qt Widgets, so a read-only `TextEdit` renders a
SUBSET of HTML - headings, tables, colour, monospace, `<img>` - with no
browser, no CSS grid and no JavaScript.  "Clean" inside that subset means
restraint and hierarchy, not a web design, and the things that bite are
specific enough to be worth writing down once:

* **Qt decides a string is rich text by looking for a TAG.**  A line of
  nothing but `&nbsp;` renders literally, which is what the scout's panel
  put on screen.  Every helper here emits a real tag.
* **`<pre>` does not wrap** and the panel is narrower than a browser
  window, so a preformatted block runs off the right edge behind a
  scrollbar.  A `div` with `white-space: pre-wrap` wraps but Qt collapses
  its newlines and leading spaces.  :func:`block` does both: explicit
  `<br>`, non-breaking spaces for the indent.
* **A read-only `TextEdit` is the only scrolling container this toolkit
  has** - there is no `ScrollArea` and no `ScrollBar` - so every long
  document goes in one, and it renders `<img>` too, which is why the
  timeline picture and the prose around it share a widget.

Colours are named once, as a small palette, because a panel that picks a
colour per call ends up with four greens.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

# The palette. One name per ROLE, so a caller asks for "a failure" rather
# than for "#f7768e" - and changing what a failure looks like is one edit.
INK = "#c0caf5"
INK_DIM = "#565f89"
HEADING = "#7aa2f7"
SUBHEADING = "#7dcfff"
GOOD = "#9ece6a"
BAD = "#f7768e"
WARN = "#e0af68"
BACKGROUND = "#16161e"
PANEL = "#1a1b26"

CSS = """
<style>
 body { font-family: -apple-system, Helvetica, sans-serif; font-size: 12px; color: %(ink)s; }
 h2   { color: %(heading)s; font-size: 15px; margin: 2px 0 6px 0; }
 h3   { color: %(sub)s; font-size: 13px; margin: 12px 0 3px 0; }
 h4   { color: %(ink)s; font-size: 12px; margin: 8px 0 2px 0; }
 table { border-collapse: collapse; margin: 2px 0 6px 0; }
 td, th { padding: 2px 10px 2px 2px; vertical-align: top; }
 th   { color: %(dim)s; text-align: left; font-weight: normal; }
 .ok  { color: %(good)s; }
 .bad { color: %(bad)s; }
 .warn{ color: %(warn)s; }
 .dim { color: %(dim)s; }
 code { font-family: 'SF Mono', Menlo, monospace; color: %(warn)s; }
 .w   { white-space: pre-wrap; font-family: 'SF Mono', Menlo, monospace;
        color: %(warn)s; margin: 4px 0; }
 .p   { white-space: pre-wrap; margin: 4px 0; }
 .lead{ color: %(dim)s; margin: 0 0 8px 0; }
</style>
""" % {"ink": INK, "dim": INK_DIM, "heading": HEADING, "sub": SUBHEADING,
       "good": GOOD, "bad": BAD, "warn": WARN}


def esc(value) -> str:
    return (str(value).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def block(text: str) -> str:
    """Preformatted AND wrapping, which no single Qt construct gives.

    See the module docstring: explicit `<br>` for the line breaks and
    non-breaking spaces for the indent, so the wrap comes free.
    """
    lines = []
    for line in esc(text).split("\n"):
        stripped = line.lstrip(" ")
        lines.append("&#160;" * (len(line) - len(stripped)) + stripped)
    return '<div class="w">%s</div>' % "<br>".join(lines)


def paragraph(text: str, css_class: str = "p") -> str:
    return '<div class="%s">%s</div>' % (css_class, esc(text))


def lead(text: str) -> str:
    """The one dim line under a heading that says what a view IS."""
    return '<div class="lead">%s</div>' % esc(text)


def heading(text: str, level: int = 2) -> str:
    return "<h%d>%s</h%d>" % (level, esc(text), level)


def table(headers: Sequence[str], rows: Iterable[Sequence[str]],
          classes: Optional[Iterable[Optional[Sequence[str]]]] = None) -> str:
    """A table whose cells are ALREADY escaped by this function.

    `classes` gives an optional per-row list of css classes, one per
    cell, so a failing value can be red without the caller writing HTML.
    """
    out = ["<table>"]
    if headers:
        out.append("<tr>%s</tr>"
                   % "".join("<th>%s</th>" % esc(h) for h in headers))
    class_rows = list(classes or [])
    for index, row in enumerate(rows):
        row_classes = (class_rows[index] if index < len(class_rows) else None) or []
        cells = []
        for column, cell in enumerate(row):
            css = row_classes[column] if column < len(row_classes) else ""
            cells.append("<td%s>%s</td>"
                         % (' class="%s"' % css if css else "", esc(cell)))
        out.append("<tr>%s</tr>" % "".join(cells))
    out.append("</table>")
    return "".join(out)


def markdown(text: str) -> str:
    """The smallest useful reading of what a model writes back.

    Bold, inline code, fenced code and bullets.  Without it `**GRADE**`
    reaches the captain as four asterisks, which is the model's markup
    leaking into the product.
    """
    out: List[str] = []
    fenced = False
    for line in str(text).split("\n"):
        if line.strip().startswith("```"):
            out.append("</div>" if fenced else '<div class="w">')
            fenced = not fenced
            continue
        if fenced:
            out.append(esc(line) + "<br>")
            continue
        html = esc(line)
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
        html = re.sub(r"`([^`]+?)`", r"<code>\1</code>", html)
        if re.match(r"\s*[-*]\s+", line):
            html = "&#8226; " + re.sub(r"^\s*[-*]\s+", "", html)
        out.append('<div class="p">%s</div>' % html)
    if fenced:
        out.append("</div>")
    return "".join(out)


def document(*parts: str) -> str:
    """One rendered page. The stylesheet goes on every one, because Qt
    re-parses each `TextEdit.HTML` assignment from scratch."""
    return CSS + "".join(p for p in parts if p)


def image(path: str, width: int) -> str:
    """The image route that WORKS.

    `Label.Pixmap = "/path.png"` is accepted and draws nothing - the
    silent no-op AGENTS.md section 5 is about.  `<img>` inside a rich
    text widget draws, and scrolls with the prose around it.
    """
    return '<img src="%s" width="%d">' % (esc(path), int(width))


def bytes_human(count) -> str:
    try:
        count = float(count)
    except (TypeError, ValueError):
        return "-"
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("kB", 1 << 10)):
        if count >= size:
            return "%.1f %s" % (count / size, unit)
    return "%d B" % int(count)


def status_class(status: str) -> str:
    """One reading of a step status, so no view invents a second."""
    return {"failed": "bad", "done": "ok", "held": "warn",
            "pending": "dim", "gate_pending": "warn"}.get(status, "dim")
