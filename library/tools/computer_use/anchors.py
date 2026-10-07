"""Anchor generality: a recipe survives moves, layouts, scale, another Mac.

A recipe step names its target with anchors in priority order
(`scripts.ANCHOR_VIAS`): menu, ax_path, ocr, template, offset,
position, key, type, wait. The first four plus key/type/wait resolve
relative to what is on screen - the window may have moved, the panel
layout changed, the display scale doubled, and the step still lands.
A raw `position` in absolute screen coordinates survives none of
that: it is allowed only as a last resort with a `why`, and this
module flags every one - including the taught ones the LLM fallback
records (`scripts._record_learned_anchor` writes `position` with
`why: llm`) - so a mapping run replaces them with a measured anchor.

Window-relative geometry is the other half: a click point derived
from a measured rect (the qualifier's image rect, an OCR frame, a
template hit divided by the capture scale) is expressed in window
points at run time, never stored from the mapping run. `window_point`
is the one helper that maps normalized picture coordinates onto a
measured rect, refusing the unmappable instead of clicking chrome.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from library.tools.computer_use.scripts import Script

#: Anchor kinds that resolve relative to what is on screen. A step
#: using only these survives window moves, panel layouts, display
#: scale and another Mac. `offset` counts only where its base anchor
#: is itself relative; `key`/`type`/`wait` anchor to nothing on
#: screen (focus, settle), so they are general by construction.
RELATIVE_VIAS = frozenset({
    "menu", "ax_path", "ocr", "template", "offset", "key", "type",
    "wait",
})

#: A raw coordinate that survived audit anyway must say why - the
#: `position` anchor's own rule (`scripts` module docstring). A
#: taught coordinate (`why: llm`) is flagged regardless: the next
#: mapping run owes it a measured anchor.
RAW_VIA = "position"


@dataclass
class AnchorFinding:
    """One generality defect in a recipe step."""

    step: int
    via: str
    defect: str
    fix: str


@dataclass
class TemplateProvenance:
    """Where a template crop came from, so it can be re-cut.

    A template without provenance is a frozen crop of one layout on
    one display: it matches until the UI moves, then fails with no
    record of what it showed. The provenance names the source
    capture (page + layout + scale), the crop rect in capture
    pixels, the match threshold with its measured margin (best
    true hit vs best false), and the date it was cut - everything
    a re-cut needs.
    """

    image: str
    page: str = ""
    layout: str = ""
    scale: float = 0.0
    crop: tuple = ()
    threshold: float = 0.9
    best_hit: float = 0.0
    best_false: float = 0.0
    cut: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "image": self.image,
            "page": self.page,
            "layout": self.layout,
            "scale": self.scale,
            "crop": list(self.crop),
            "threshold": self.threshold,
            "best_hit": self.best_hit,
            "best_false": self.best_false,
            "cut": self.cut,
            "extra": dict(self.extra),
        }

    def margin_ok(self) -> bool:
        """The threshold sits strictly between impostor and hit."""
        return self.best_false < self.threshold <= self.best_hit


def audit_anchors(script: Script) -> list[AnchorFinding]:
    """Every generality defect in a script's anchors.

    Flags: a `position` anchor with no `why` (the anchor's own rule);
    a taught `position` (`why: llm`) the step leads with - one kept
    as the last resort behind a measured anchor resolves
    measured-first and passes; a `template` naming neither a
    registry entry nor a measured crop (an unmeasurable match);
    an `offset` whose base anchor is itself raw. Relative anchors
    (menu, ax_path, ocr, key, type, wait) and a measured `position`
    with a non-llm `why` pass clean.
    """
    findings: list[AnchorFinding] = []
    for index, step in enumerate(script.steps):
        for position, anchor in enumerate(step.anchors):
            via = anchor.via
            params = anchor.params
            if via == "position":
                why = params.get("why", "")
                if not why:
                    findings.append(AnchorFinding(
                        step=index, via=via,
                        defect="raw coordinate with no why",
                        fix="replace with an AX/menu path, OCR label or "
                            "template crop measured on the live UI; a "
                            "position survives only with a why",
                    ))
                elif why == "llm" and not _measured_first(step, position):
                    findings.append(AnchorFinding(
                        step=index, via=via,
                        defect="taught coordinate from the LLM fallback",
                        fix="re-map the step on the live UI and replace "
                            "with an OCR/template anchor plus an expect",
                    ))
            elif via == "template":
                _audit_template(index, params, findings)
            elif via == "offset":
                base = params.get("anchor") or {}
                base_via = base.get("via", "")
                if base_via == "position" or base_via not in RELATIVE_VIAS:
                    findings.append(AnchorFinding(
                        step=index, via=via,
                        defect=f"offset off a non-relative base {base_via!r}",
                        fix="anchor the offset to an OCR label, AX path "
                            "or template hit measured at run time",
                    ))
            elif via not in RELATIVE_VIAS:
                findings.append(AnchorFinding(
                    step=index, via=via,
                    defect=f"unknown anchor via {via!r}",
                    fix="use one of menu, ax_path, ocr, template, "
                        "offset, position, key, type or wait",
                ))
    return findings


def _audit_template(index: int, params: dict,
                    findings: list) -> None:
    """One template anchor's generality defects.

    Two vocabularies (2072): `template:` names a registry entry in
    `templates.py` - provenance, measured threshold and cut scale
    travel with the name, so the anchor is general by construction
    and the audit checks the REGISTRY (unknown name, unmeasured
    threshold, missing file each flag); `image:` is the legacy
    direct crop - general only with a threshold and a scale story,
    so a missing threshold flags. An anchor naming neither is not
    an anchor at all.
    """
    name = params.get("template")
    if name:
        try:
            from library.tools.computer_use.templates import (
                ICON_TEMPLATES,
                templates_dir,
            )

            entry = ICON_TEMPLATES[name]
        except (ImportError, KeyError):
            findings.append(AnchorFinding(
                step=index, via="template",
                defect=f"template anchor names unknown registry {name!r}",
                fix="register it in templates.py with its provenance, "
                    "or name the crop it should match",
            ))
            return
        if entry.threshold is None:
            findings.append(AnchorFinding(
                step=index, via="template",
                defect=f"registry template {name!r} is unmeasured",
                fix="measure the hit-vs-false margin live and record "
                    "the threshold in templates.py",
            ))
        if not (templates_dir() / entry.filename).exists():
            findings.append(AnchorFinding(
                step=index, via="template",
                defect=f"registry template {name!r} has no file",
                fix="capture the icon from the live UI into "
                    "templates/ and record it",
            ))
        return
    if not params.get("image"):
        findings.append(AnchorFinding(
            step=index, via="template",
            defect="template anchor names no template or image",
            fix="name a templates.py registry entry, or cut the crop "
                "from the live UI and record its TemplateProvenance",
        ))
    elif "threshold" not in params:
        findings.append(AnchorFinding(
            step=index, via="template",
            defect="template anchor names no threshold",
            fix="measure the hit-vs-false margin live and "
                "set the threshold between them",
        ))


def _measured_first(step, position: int) -> bool:
    """Does a relative anchor precede this position on the step.

    The runner tries anchors in declared order, so a taught
    coordinate kept as the LAST resort behind a measured anchor
    (what `reanchor_with_ocr` builds) resolves measured-first -
    the raw point only fires where the measurement moved. Only a
    taught coordinate the step leads with is flagged.
    """
    return any(anchor.via in RELATIVE_VIAS
               for anchor in step.anchors[:position])


def window_point(rect: tuple, x: float, y: float) -> tuple:
    """Map normalized picture (x, y) onto a measured rect, in points.

    The rect is `(left, top, right, bottom)` in the capture's own
    pixels divided by the capture scale - window points, the space
    clicks take. A point outside 0..1 names no picture pixel and is
    refused: the click would land on chrome. The one helper every
    measured-geometry recipe maps through, so a scale or rect bug
    fails in one place, not one per recipe.
    """
    left, top, right, bottom = rect
    for name, value in (("x", x), ("y", y)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"a window point needs numbers, got {name}={value!r}")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(
                f"a window point lives in 0..1, got {name}={value!r} - "
                f"the click would land on chrome")
    return (left + float(x) * (right - left),
            top + float(y) * (bottom - top))


def reanchor_with_ocr(step, text: str):
    """Replace a raw coordinate with the label that names it.

    The flagged-then-replaced half of the audit: a mapping run (or
    the distillation repair agent) saw the UI, read the label beside
    the control the raw coordinate clicked, and calls this to put
    the measured `ocr` anchor FIRST - ahead of the raw position,
    which stays as the last-resort fallback with its `why`. A step
    with no raw coordinate, or a blank label, is refused: the
    replacement must name something the screen shows, never a guess
    about what sits near the point.
    """
    from library.tools.computer_use.scripts import Anchor

    if not str(text or "").strip():
        raise ValueError(
            "a replacement anchor needs the label the screen shows - "
            "a blank label would resolve nowhere")
    raw = [anchor for anchor in step.anchors if anchor.via == "position"]
    if not raw:
        raise ValueError(
            "the step holds no raw coordinate to replace - a measured "
            "step needs no replacement")
    measured = Anchor(via="ocr", params={"text": str(text).strip()})
    step.anchors[:] = [measured] + [anchor for anchor in step.anchors
                                    if anchor.via != "position"] + raw
    return step
