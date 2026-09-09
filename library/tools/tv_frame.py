"""The punched-in TV-frame look. One enumeration.

The captain's marker on ``Reel 20 - search-didnt-change-the-question-did
(selector redraw)`` (frame 538, 2026-09-09), verbatim: "this is kind of
the new standard zoom i want you to set it to because i want to be able
to use the TV 4k.png to be able to add like that border effect on the
video."

Firstmate measured the reference before the timeline was deleted, and the
capture at ``data/vep-field-test-reset/reel20-standard-zoom.json`` is now
the only record:

    V1  LC4932.MXF   ZoomX/ZoomY = 2.30, Pan 0, Tilt 0, no crop
    V2  TV 4k.png    ZoomX/ZoomY = 1.00, full frame, Opacity 100
    V3  captions     ZoomX/ZoomY = 1.00

So the look is three layers: the shot punched in underneath, the TV
asset at native 1:1 on top, captions above that.  This module declares
that - the punch-in factor, the frame asset, and the layer order - and
nothing else.  How the layers reach the timeline is ``compile_manifest``
(V1 zoom, V2 frame clips) and ``resolve_build_timeline`` (placement);
when the power animation runs is ``library/tools/tv_power.py``.

2.30 is the captain's chosen standard, so it is a DECLARED DEFAULT the
project or the template can change - never a constant compiled into the
engine.  A project that declares no frame asset gets no look at all:
there is no fallback bezel, because a fallback bezel would be exactly
the house look the captain refused ("there are no house glow looks,
there are no settled house grain or anything", 2026-08-28).

Whether the frame should scale with the punch-in or stay 1:1 is the
brief's open question.  The reference has it at 1:1 and the module keeps
it there: the frame is the SET and the set does not breathe with the
performance.  A declaration that wants it to breathe can say so later;
today there is one sample and it says 1:1.
"""

from __future__ import annotations

import os
from typing import Optional

from library.tools.tv_power import validate_timing as _validate_power_timing

# The captain's chosen standard punch-in, measured off the reference:
# V1 ZoomX/ZoomY = 2.300000219345092 on the Reel 20 capture.  A project
# changes it through its own ``tv_frame`` declaration; nothing in the
# engine hardcodes it.
DEFAULT_PUNCH_IN = 2.30
"""V1 zoom under the frame when the declaration names no factor."""

# A configuration guard, not taste: below 1.0 would zoom OUT from the
# conform (which is framing_intent's own control), and past 4.0 the
# source falls apart on most footage.  Same shape as
# framing_intent.MAX_CROP_FACTOR.
MIN_PUNCH_IN = 1.0
MAX_PUNCH_IN = 4.0

# The layer order, bottom to top.  Fixed because it IS the look: the
# footage plays under the bezel, the bezel's transparent window shows
# it, the captions sit above both.  A caption under the frame would be
# hidden by the opaque bezel wherever the two overlap.
TV_FRAME_LAYERS = ("footage", "frame", "captions")
"""V1 footage, V2 frame asset, V3 captions."""

# Which timeline tracks the layers land on.  V2 already carries B-roll,
# and the frame spans the WHOLE reel while B-roll comes and goes - so a
# reel under this look carries no B-roll cutaways: the window is the
# variety.  compile_manifest refuses a reel declaring both.
LAYER_TRACKS = {"footage": "V1", "frame": "V2", "captions": "V3"}

# The measured screen window of the captain's asset (``TV 4k.png``,
# 3840x2160 RGBA, 68.9% transparent): the opaque bezel surrounds a
# near-fully-transparent rectangle at x 523-3316, y 42-2117
# (2794x2076), feathered ~1 px at the edge.  Measured 2026-09-09 off
# the alpha channel; recorded here so a future asset can be checked
# against it, not so this one can be redrawn from it.
REFERENCE_ASSET_SIZE = (3840, 2160)
REFERENCE_SCREEN_WINDOW = (523, 42, 3316, 2117)
"""(x0, y0, x1, y1) of the transparent window in the reference asset."""


def validate_punch_in(value, source: str) -> float:
    """A declared punch-in factor as a float, or raise naming *source*.

    Out of range is a mistake, not something to clamp: clamping would
    accept ``punch_in: 100`` as "very tight" and the next reader of
    that project.yaml would believe 100 meant something.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"punch_in in {source} must be a number between "
            f"{MIN_PUNCH_IN} and {MAX_PUNCH_IN}, got "
            f"{type(value).__name__}: {value!r}"
        )
    factor = float(value)
    if not (MIN_PUNCH_IN <= factor <= MAX_PUNCH_IN):
        raise ValueError(
            f"punch_in in {source} must be between {MIN_PUNCH_IN} and "
            f"{MAX_PUNCH_IN}, got {factor}"
        )
    return factor


def resolve_asset_path(declared: str, project_folder: Optional[str],
                       source: str) -> str:
    """A declared frame asset as an absolute path, or raise.

    An absolute path is used as-is; a relative one resolves against the
    project folder, because the asset is project artwork (§14): it lives
    with the project that owns the series, never in the engine.  A path
    that is not on disk raises - a frame the manifest names and disk
    does not have would refuse the compile anyway, and earlier is
    kinder.
    """
    if not isinstance(declared, str) or not declared.strip():
        raise TypeError(
            f"tv_frame asset in {source} must be a path string, got "
            f"{declared!r}"
        )
    ref = declared.strip()
    candidate = ref if os.path.isabs(ref) else os.path.join(project_folder or "", ref)
    if not os.path.exists(candidate):
        raise FileNotFoundError(
            f"tv_frame asset in {source} not found: {ref!r}"
        )
    return os.path.abspath(candidate)


def _read_declaration(project_folder: Optional[str], template) -> tuple:
    """The raw ``tv_frame`` mappings, (project, template), each or None."""
    project_decl = None
    if project_folder:
        try:
            from library.tools.brand_registry import project_pipeline_block
        except ImportError:  # pragma: no cover - importable in practice
            project_pipeline_block = None
        if project_pipeline_block is not None:
            block = project_pipeline_block(project_folder) or {}
            project_decl = block.get("tv_frame")
    template_decl = None
    if template is not None:
        style = getattr(template, "style", None)
        template_decl = getattr(style, "tv_frame", None)
    return project_decl, template_decl


def resolve_tv_frame(project_folder: Optional[str] = None,
                     template=None) -> Optional[dict]:
    """The TV-frame look this run renders under, or None for no look.

    Precedence is project over template - the same project-over-template
    rule framing_intent uses, for the same reason: one video may wear
    the frame without forking its template.  The DEFAULT punch-in
    applies only once an asset is declared somewhere: a bare
    ``punch_in`` with no asset is not the look, it is a zoom with no
    frame, and resolves to None rather than punching the footage for
    no reason.

    A malformed declaration RAISES.  A frame declaration that is
    silently dropped is a look the editor believes shipped.
    """
    project_decl, template_decl = _read_declaration(project_folder, template)
    declared = project_decl if project_decl is not None else template_decl
    if declared is None:
        return None
    source = (
        "project.yaml pipeline.tv_frame"
        if project_decl is not None else
        f"template {getattr(template, 'series_id', '') or 'brand template'} style.tv_frame"
    )
    if not isinstance(declared, dict):
        raise TypeError(
            f"tv_frame declaration in {source} must be a mapping, got "
            f"{type(declared).__name__}: {declared!r}"
        )
    unknown = set(declared) - {"asset", "punch_in", "power"}
    if unknown:
        raise ValueError(
            f"tv_frame declaration in {source} names unknown "
            f"keys {sorted(unknown)}; known: ['asset', 'punch_in', 'power']"
        )
    if declared.get("asset") is None:
        return None
    asset = resolve_asset_path(declared["asset"], project_folder, source)
    punch = (
        validate_punch_in(declared["punch_in"], source)
        if declared.get("punch_in") is not None
        else DEFAULT_PUNCH_IN
    )
    power = _validate_power_timing(declared.get("power"), source, half="both")
    return {
        "asset": asset,
        "punch_in": punch,
        "power": power,
        "origin": source,
    }


def v1_zoom_for_look(punch_in: float) -> float:
    """The absolute V1 ZoomX/ZoomY a clip plays under the frame.

    ABSOLUTE, not multiplied over the conform fill: under the frame the
    bezel is the framing - letterbox-versus-fill has nothing left to
    decide, because the window, not the delivery edges, bounds the
    picture.  This is also what the reference shows: Zoom 2.30 flat, no
    crop, Pan 0.  The conform pan/tilt still apply, so subject tracking
    survives the look.
    """
    return float(punch_in)


def screen_window(asset_path: str, threshold: int = 8) -> tuple:
    """The transparent window of a frame asset as (x0, y0, x1, y1).

    Measures the bounding box of near-transparent pixels (alpha at or
    below *threshold*, which skips the ~1 px feathered edge without
    moving the reading).  For the captain's ``TV 4k.png`` this answers
    (523, 42, 3316, 2117) - see REFERENCE_SCREEN_WINDOW.  Requires
    Pillow, which the pipeline already carries for the vision steps.
    """
    from PIL import Image

    with Image.open(asset_path) as im:
        alpha = im.getchannel("A") if "A" in im.getbands() else None
        if alpha is None:
            raise ValueError(
                f"tv_frame asset has no alpha channel: {asset_path!r} - "
                f"a frame with no window is a full-cover slate"
            )
        mask = alpha.point(lambda v: 255 if v <= threshold else 0)
        bbox = mask.getbbox()
    if bbox is None:
        raise ValueError(
            f"tv_frame asset has no transparent window: {asset_path!r}"
        )
    return bbox
