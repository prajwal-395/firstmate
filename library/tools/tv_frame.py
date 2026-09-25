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
    unknown = set(declared) - {"asset", "punch_in", "power", "rotate",
                               "offset_y"}
    if unknown:
        raise ValueError(
            f"tv_frame declaration in {source} names unknown "
            f"keys {sorted(unknown)}; known: ['asset', 'punch_in', "
            f"'power', 'rotate', 'offset_y']"
        )
    if declared.get("asset") is None:
        return None
    asset = resolve_asset_path(declared["asset"], project_folder, source)
    punch = (
        validate_punch_in(declared["punch_in"], source)
        if declared.get("punch_in") is not None
        else DEFAULT_PUNCH_IN
    )
    power = _validate_power_timing(declared.get("power"), source)
    rotate = validate_rotation(declared.get("rotate", AUTO_ROTATE), source)
    offset_y = validate_offset_y(declared.get("offset_y", 0), source)
    return {
        "asset": asset,
        "punch_in": punch,
        "power": power,
        "rotate": rotate,
        "offset_y": offset_y,
        "origin": source,
    }


def validate_offset_y(value, source: str) -> int:
    """Where the frame and its picture sit, as DELIVERY pixels down.

    0 is the frame centred on the delivery, which is every look before
    this existed. A positive value moves the frame, its window and the
    picture inside it down together - the captain, 2026-09-25, wanted
    the post header inside the platforms' safe zone "and move the video
    itself down", letting the picture's bottom (the table) sit in the
    bottom zones. Not a creative default: an unstated offset is no
    offset. A non-integer or an offset that pushes the window off the
    frame is refused by the caller that knows the frame.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"tv_frame.offset_y in {source} must be a number of "
                         f"delivery pixels, got {value!r}")
    if float(value) != int(value):
        raise ValueError(f"tv_frame.offset_y in {source} must be whole "
                         f"pixels, got {value!r}")
    return int(value)


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


AUTO_ROTATE = "auto"
"""Turn the frame upright for this delivery when that is what fits.

The captain's ``TV 4k.png`` is a LANDSCAPE television and a reel is
PORTRAIT, and on 2026-09-09 they said plainly what to do with it:
*"the asset is horizontal, u have to rotate it to vertical and align it
to the frame"*.

Rotating is not a guess about the artwork - it is the measurement that
makes the two shapes the same one.  Rotated, the asset is 2160x3840
against a 1080x1920 frame: the aspects match EXACTLY (0.5625 both), so
it needs no cover zoom at all, it downscales by half, and its
transparent window lands at y 260..1661 of the reel while the 2.30
punch-in draws its picture at y 261..1658.  The bezel was drawn to
frame that punch-in, and unrotated it never could - which is also why
2.30 is "the new standard zoom" and not an arbitrary number.

``auto`` takes a quarter turn only when doing so brings the asset's
aspect CLOSER to the delivery's, so a frame already drawn upright is
left exactly as it is.  A declaration may state 0 to refuse rotation
outright, or a quarter turn of its own.
"""

ROTATE_QUARTER = 90
"""Which quarter turn ``auto`` takes, and why it is stated not derived.

Measured on the captain's asset, 2026-09-09: the two opaque rails are
723px and 749px wide, both PURE BLACK (mean RGB 0,0,0 across every
pixel), both fully opaque, with no logo, stand, control or brightness
variation anywhere in either.  The two directions are therefore
visually identical on this asset and nothing in the artwork picks one.
A project whose frame HAS a top and a bottom declares ``rotate``
rather than relying on this.
"""

ROTATIONS = (0, 90, 180, 270)


def validate_rotation(value, source: str):
    """A declared rotation as ``auto`` or a quarter turn, or raise."""
    if value is None or value == AUTO_ROTATE:
        return AUTO_ROTATE
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"rotate in {source} must be {AUTO_ROTATE!r} or one of "
            f"{list(ROTATIONS)}, got {type(value).__name__}: {value!r}")
    turn = int(value) % 360
    if turn not in ROTATIONS:
        raise ValueError(
            f"rotate in {source} must be {AUTO_ROTATE!r} or one of "
            f"{list(ROTATIONS)}, got {value!r}. A frame is turned in "
            f"quarters or not at all: anything else leaves the bezel's "
            f"edges off the delivery's edges, which is the one thing a "
            f"frame has to get right.")
    return turn


def applied_rotation(look, asset_size, frame_width: int,
                     frame_height: int) -> int:
    """The quarter turn this frame is really drawn with.

    Resolves ``auto`` against the two shapes: a quarter turn is taken
    only when it brings the asset's aspect closer to the delivery's.
    """
    declared = (look or {}).get("rotate", AUTO_ROTATE)
    if declared != AUTO_ROTATE:
        return int(declared)
    asset_width, asset_height = int(asset_size[0]), int(asset_size[1])
    if not asset_width or not asset_height or not frame_height:
        return 0
    frame_aspect = frame_width / frame_height
    upright = abs(asset_width / asset_height - frame_aspect)
    turned = abs(asset_height / asset_width - frame_aspect)
    return ROTATE_QUARTER if turned < upright else 0


def oriented_size(asset_size, rotation: int) -> tuple:
    """The asset's (width, height) after *rotation*."""
    width, height = int(asset_size[0]), int(asset_size[1])
    return (height, width) if int(rotation) % 180 == 90 else (width, height)


def cover_zoom(asset_size, frame_width: int, frame_height: int) -> float:
    """The zoom that makes a frame asset COVER the delivery frame.

    Resolve conforms a mismatched still by fitting it inside the frame
    (`timelineInputResMismatchBehavior = scaleToFit`), so a landscape
    bezel in a portrait reel lands as a band across the middle.  The
    zoom that turns that band back into a full-frame cover is the ratio
    of the two conform scales::

        fit   = min(frame_w / asset_w, frame_h / asset_h)
        cover = max(frame_w / asset_w, frame_h / asset_h)
        zoom  = cover / fit

    For the captain's ``TV 4k.png`` (3840x2160) in a 1080x1920 reel that
    is 0.8889 / 0.28125 = **3.1605**, which is the value they set by hand
    on the timeline on 2026-09-09 after rejecting the fitted band.  Their
    own statement of it - ``(frame_h/frame_w) / (asset_h/asset_w)`` -
    gives the same number for an asset wider than the frame; this form
    is written the way it is because it also answers the other direction,
    where the fit is by height and the cover is by width.

    The number is DERIVED here and nowhere else.  Nothing in the engine
    holds 3.16.
    """
    asset_width, asset_height = int(asset_size[0]), int(asset_size[1])
    if asset_width <= 0 or asset_height <= 0:
        raise ValueError(
            f"a frame asset cannot be {asset_width}x{asset_height}")
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError(
            f"the delivery frame cannot be {frame_width}x{frame_height}")
    by_width = frame_width / asset_width
    by_height = frame_height / asset_height
    return max(by_width, by_height) / min(by_width, by_height)


def cover_size(asset_size, frame_width: int, frame_height: int) -> tuple:
    """The pixel size a frame asset is DRAWN at when it covers the frame.

    What the overlay should be rendered at, so the picture Resolve shows
    is the asset's own pixels rather than a resample of a smaller
    render.  Both dimensions are made even, because ffmpeg's encoders
    reject an odd one.
    """
    asset_width, asset_height = int(asset_size[0]), int(asset_size[1])
    cover = max(frame_width / asset_width, frame_height / asset_height)
    width = int(round(asset_width * cover)) // 2 * 2
    height = int(round(asset_height * cover)) // 2 * 2
    return (max(width, 2), max(height, 2))


def screen_window_rect(look, frame_width: int, frame_height: int,
                       asset_size=None) -> tuple:
    """The frame's transparent window in DELIVERY-FRAME pixels.

    Measured off the asset's alpha, turned by the rotation this delivery
    applies, and put through the same conform the overlay is placed
    under - so the answer is where the window really lands on the
    timeline, not where it sits in the artwork.

    This is the rectangle the PICTURE has to cover.  Covering the
    delivery frame is not the same thing and is the wrong target: on
    2026-09-09 the picture covered neither, and the 2.30 punch-in left
    black bands inside the television's own screen.
    """
    from PIL import Image

    asset = look["asset"]
    if asset_size is None:
        with Image.open(asset) as image:
            asset_size = image.size
    rotation = applied_rotation(look, asset_size, frame_width, frame_height)
    x0, y0, x1, y1 = screen_window(asset)
    width, height = int(asset_size[0]), int(asset_size[1])

    # A quarter turn clockwise sends (x, y) to (H-1-y, x); two of them
    # invert both axes; three send it to (y, W-1-x).
    turn = int(rotation) % 360
    if turn == 90:
        x0, y0, x1, y1 = height - 1 - y1, x0, height - 1 - y0, x1
    elif turn == 180:
        x0, y0, x1, y1 = width - 1 - x1, height - 1 - y1, width - 1 - x0, height - 1 - y0
    elif turn == 270:
        x0, y0, x1, y1 = y0, width - 1 - x1, y1, width - 1 - x0

    oriented = oriented_size(asset_size, rotation)
    drawn = cover_size(oriented, frame_width, frame_height)
    zoom = cover_zoom(drawn, frame_width, frame_height)
    fit = min(frame_width / oriented[0], frame_height / oriented[1])
    scale = fit * zoom
    origin_x = frame_width / 2.0 - oriented[0] * scale / 2.0
    origin_y = (frame_height / 2.0 - oriented[1] * scale / 2.0
                + int(look.get("offset_y") or 0))
    return (origin_x + x0 * scale, origin_y + y0 * scale,
            origin_x + x1 * scale, origin_y + y1 * scale)


def window_cover_zoom(source_width: int, source_height: int,
                      window: tuple, frame_width: int,
                      frame_height: int) -> float:
    """The smallest picture zoom that COVERS the frame's screen window.

    Derived exactly as :func:`cover_zoom` derives the frame's own scale,
    and for the same reason - the picture inside a television has to
    reach the edges of the screen or the viewer sees black inside the
    set.  The target is the WINDOW, never the delivery frame: those are
    different rectangles and using the wrong one is what left 228px
    bands inside the screen on 2026-09-09.

    On the captain's asset TURNED UPRIGHT this answers 2.3070 against
    their declared 2.30 - which is what says the bezel was drawn for
    that punch-in.  On the same asset UNROTATED it answers 3.05, and the
    difference between those two numbers is the whole of what rotating
    the frame fixed.
    """
    fit = min(frame_width / source_width, frame_height / source_height)
    window_width = window[2] - window[0]
    window_height = window[3] - window[1]
    return max(window_width / (source_width * fit),
               window_height / (source_height * fit))


def assert_frameable(look, frame_width: int, frame_height: int,
                     asset_size=None) -> None:
    """Refuse a declared frame that genuinely cannot frame this delivery.

    A frame whose ASPECT is not the delivery's is not one of those cases,
    and an earlier version of this function said it was.  The captain
    disproved that by hand on 2026-09-09: they took the same 3840x2160
    asset in the same 1080x1920 reel, set its zoom to 3.16, and it
    framed.  A mismatched aspect is COVER-SCALED (:func:`cover_zoom`),
    not unframeable, and refusing it refused a working configuration.

    Two things really do stop a frame, and both are measured:

    1. **No transparent window.**  A frame with no transparent region is
       a full-cover slate: put over the picture it hides it entirely.
       Measured off the alpha channel, not assumed from the filename.
    2. **A cover that would UPSCALE the asset.**  Covering draws the
       asset at ``max(frame_w/asset_w, frame_h/asset_h)`` of its native
       size; above 1.0 that is a resample larger than the pixels that
       exist, and a bezel is hard geometry whose thin highlights are
       exactly what upscaling softens.  The captain's asset is 3840
       across a 1080 frame, so covering DOWNSCALES it to 0.889 and this
       does not fire.

    `look` None returns without looking at anything.
    """
    if look is None:
        return
    if not frame_width or not frame_height:
        raise ValueError(
            "assert_frameable needs the delivery frame it is checking "
            f"against, got {frame_width!r}x{frame_height!r}")

    asset = look["asset"]
    if asset_size is None:
        from PIL import Image
        with Image.open(asset) as im:
            asset_size = im.size
    # Measured on the asset AS IT WILL BE DRAWN. A landscape frame for a
    # portrait reel is turned upright first (`applied_rotation`), and
    # judging its cover before the turn would refuse an asset that fits
    # perfectly once rotated.
    rotation = applied_rotation(look, asset_size, frame_width, frame_height)
    asset_width, asset_height = oriented_size(asset_size, rotation)
    if not asset_width or not asset_height:
        raise ValueError(
            f"tv_frame asset {asset!r} reports a zero dimension "
            f"({asset_width}x{asset_height}); it cannot be a frame.")

    # 1. A window, or it is a slate.  `screen_window` raises with its own
    #    message for an asset with no alpha and for one with no
    #    transparent pixel; both are the same refusal from here.
    try:
        window = screen_window(asset)
    except ValueError as exc:
        raise ValueError(
            f"tv_frame asset {os.path.basename(asset)} cannot frame "
            f"anything: {exc}. A frame is a window with a border around "
            f"it; one with no window is a slate, and putting a slate "
            f"over the picture hides it. Declared by {look['origin']}."
        ) from exc

    # 2. Covering must not upscale.
    cover = max(frame_width / asset_width, frame_height / asset_height)
    if cover > 1.0:
        drawn_w = int(round(asset_width * cover))
        drawn_h = int(round(asset_height * cover))
        turned = f" (turned {rotation} degrees)" if rotation else ""
        raise ValueError(
            f"tv_frame asset {os.path.basename(asset)} is "
            f"{asset_width}x{asset_height}{turned} and covering a "
            f"{frame_width}x{frame_height} frame would draw it at "
            f"{drawn_w}x{drawn_h} - an upscale of {cover:.2f}x beyond "
            f"the pixels that exist. A bezel is hard geometry with thin "
            f"highlights and upscaling softens exactly the edges that "
            f"make it read as a set. Declared by {look['origin']}; "
            f"supply the asset at {frame_width}x{frame_height} or "
            f"larger. (Its window measures {window}.)")


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
