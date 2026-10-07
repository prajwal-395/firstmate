"""Measured icon templates for icon-only Qt controls.

Some Resolve controls are neither AX-reachable nor OCR-readable - the
Color page toolbar buttons are icons with no text (measured live
2026-10-07: OCR finds no `Magic Mask` text on the settled page, and the
probe measured page buttons as not OCR-readable). The route to such a
control is an image template cut from a live capture: `cv2.matchTemplate`
against a fresh capture, scaled to the capture's backing scale.

A template is a measurement, so it carries provenance: what it shows,
which capture it was cut from and at what backing scale, the crop rect
in that capture's backing pixels, and the match threshold measured on
held-out captures of the same control. A threshold is never a default
to tune later - an entry whose threshold is not yet measured carries
`None`, and its anchor resolves to nothing until the measurement
lands. A missing file is the same: the anchor falls through to the
next one instead of crashing the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from library.tools.ren_refusal import RenRefusal


class TemplateMissing(RenRefusal):
    """A named template has no usable file - refused with the capture
    that would produce it, never an invented stand-in."""


@dataclass(frozen=True)
class IconTemplate:
    """One measured icon: what it shows and where it came from.

    `threshold` is set inside the measured gap between the true peak
    and the strongest false peak on held-out captures of the same
    control, with both numbers recorded in `source` - never a tuned
    default, never the true peak itself (a 1.0 threshold fails on any
    repaint noise). `None` means unmeasured - the anchor stays inert.
    `cut_scale` is the source capture's backing scale; the resolver
    rescales the crop to the live capture's scale before matching, so
    one crop serves every display.
    """

    name: str
    filename: str
    threshold: float | None
    cut_scale: float
    source: str
    crop: tuple[int, int, int, int]


#: The registry. Entries with `threshold=None` (and no file yet) are
#: declarations of intent: their anchors resolve to nothing until the
#: live capture and the threshold measurement land.
ICON_TEMPLATES: dict[str, IconTemplate] = {
    "magic-mask-toolbar": IconTemplate(
        name="magic-mask-toolbar",
        filename="magic_mask_toolbar.png",
        threshold=0.8,
        cut_scale=2.0,
        source=("Color page toolbar, Ren Qualification, captures "
                "2026-10-07 (3 x 3024x1898 @2x, 12 s span): true peak "
                "1.0000 at (1472, 1162) on all three, strongest false "
                "peak 0.5437 elsewhere; threshold sits in that gap"),
        crop=(1472, 1162, 40, 36),
    ),
}


def templates_dir() -> Path:
    """The templates directory - resolved from the engine root, never
    the cwd, so verbs work from anywhere (the qualifier precedent)."""
    from ren.engine_root import require_engine_root

    return Path(require_engine_root()) / "library" / "tools" / "computer_use" / "templates"


def template_path(name: str) -> Path:
    """The absolute path of a registered template's file."""
    try:
        entry = ICON_TEMPLATES[name]
    except KeyError:
        raise TemplateMissing(
            f"no icon template named {name!r}",
            f"registered: {sorted(ICON_TEMPLATES)}",
            "register it in templates.py with its provenance, then retry",
            code="TEMPLATE_UNKNOWN",
        ) from None
    return templates_dir() / entry.filename


def load_template(name: str) -> tuple[object, IconTemplate]:
    """The template pixels plus its provenance.

    Refuses when the entry is unmeasured or the file is absent or
    unreadable - the anchor falls through to whatever it names next,
    so a missing measurement reads as no anchor, never a crash.
    """
    try:
        entry = ICON_TEMPLATES[name]
    except KeyError:
        raise TemplateMissing(
            f"no icon template named {name!r}",
            f"registered: {sorted(ICON_TEMPLATES)}",
            "register it in templates.py with its provenance, then retry",
            code="TEMPLATE_UNKNOWN",
        ) from None
    import numpy as np
    from PIL import Image

    path = templates_dir() / entry.filename
    try:
        with Image.open(path) as template:
            pixels = np.array(template.convert("RGB"))
    except (OSError, ValueError) as exc:
        raise TemplateMissing(
            f"icon template {name!r} has no usable file at {path}",
            f"{type(exc).__name__}: {exc}",
            "capture the icon from the live palette into templates/, "
            "measure its threshold, and record both in templates.py",
            code="TEMPLATE_UNMEASURED",
        ) from exc
    return pixels, entry
