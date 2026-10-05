"""Text measurement for the IR lowering.

The lowering emits semantic nodes; the renderer draws them. Where a
composition's layout depends on how wide a word is (a caption's flex-wrap,
a card run wrapping inside its column), the lowering measures here and
writes the result into the scene as ``Bounds`` and as lines broken with
``\\n``, so every engine draws the same lines in the same place.

A text node's ``bounds`` is its CSS box: ``x``/``y`` the top-left corner,
``width`` the box width the text aligns inside (``text_align``), and
``height`` the stacked line boxes. Each line box is ``line_height`` ems
tall (``None`` is CSS ``normal``), and the baseline sits where CSS puts
it: half the leading above the content area, then the ascent. The
renderer computes that baseline from the face it actually draws.

Measurement reads the same static instance the renderer draws
(``fonts.advances``): advances plus the face's pair kerning, so a width
here is the width a browser or the renderer lays out.
Without a font file there is nothing to measure; the per-character
estimate the caption fitter falls back to stands in, and the scene says
nothing it cannot back.
"""

from __future__ import annotations

from typing import Optional

from .fonts import advances, vertical_metrics

MEAN_ADVANCE_EM = 0.60
"""Mean advance of a lowercase alphabet plus space, in ems - the estimate
used only when no font file is declared."""

NORMAL_LINE_HEIGHT_EM = 1.2
"""CSS ``normal`` when no font file is declared to read it from."""


def apply_text_transform(text: str, transform: Optional[str]) -> str:
    """CSS ``text-transform`` applied to *text*."""
    if transform == "uppercase":
        return text.upper()
    if transform == "lowercase":
        return text.lower()
    if transform == "capitalize":
        return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))
    return text


def measure_text(text: str, font_size: float,
                 font_file: Optional[str] = None,
                 font_weight: Optional[float] = None,
                 letter_spacing: float = 0.0,
                 text_transform: Optional[str] = None) -> float:
    """Rendered advance width of one line of *text*, in pixels.

    Letter spacing is added after every character, the last included,
    the way CSS does.
    """
    shown = apply_text_transform(text, text_transform)
    spacing = letter_spacing * len(shown)
    if font_file:
        return advances(font_file, font_weight).width(shown, float(font_size)) + spacing
    return len(shown) * MEAN_ADVANCE_EM * font_size + spacing


def line_box_height(font_size: float, line_height: Optional[float],
                    font_file: Optional[str] = None) -> float:
    """One CSS line box: ``line_height`` ems, or ``normal`` from the face."""
    if line_height is not None:
        return float(line_height) * font_size
    if font_file:
        return vertical_metrics(font_file).normal_line_height * font_size
    return NORMAL_LINE_HEIGHT_EM * font_size


def wrap_text(text: str, max_width: Optional[float], **style) -> list[str]:
    """Break *text* into lines the way CSS ``pre-line``/``normal`` wraps.

    Hard breaks (``\\n``) are kept; inside a paragraph, words are greedily
    filled up to *max_width*. A word wider than the line stays whole on
    its own line. ``style`` is passed to ``measure_text``.
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if max_width is not None and measure_text(candidate, **style) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def block_width(lines: list[str], **style) -> float:
    """The widest of *lines*."""
    return max((measure_text(line, **style) for line in lines), default=0.0)
