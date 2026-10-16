"""Lowering functions: composition props to IR scenes.

Each function takes the same props the steps already build for the
HyperFrames and Remotion compositions and produces an IR scene whose
timing, easing, layout and style match what the HyperFrames composition
draws today.

The lowering is deliberately mechanical: every tween in the HyperFrames
composition becomes an Animation, every ``.clip`` window becomes a
Timing, every styled element becomes a Node, and every CSS box the
template lays out becomes ``Bounds`` computed here with the same numbers
(``layout``). A value the template states - a gap, a line height, an
outline built from shadows - is stated here once more with the template
named, because the template is what this must draw like. Where a
composition has behaviour the IR cannot yet express, the gap is listed
in the PR body rather than approximated.

Font files arrive as paths the caller resolved (a project's brand face,
or the bundled one); the lowering measures with them and never resolves
a project file itself.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .layout import (
    block_width,
    line_box_height,
    measure_text,
    wrap_text,
)
from .model import (
    Animation,
    BlurFilter,
    Bounds,
    CameraContent,
    CubicBezierEasing,
    DropShadowFilter,
    EaseEasing,
    ImageContent,
    Keyframe,
    LinearEasing,
    Node,
    NodeType,
    PaintStop,
    RadialGradient,
    Scene,
    ShapeContent,
    SpringEasing,
    StepsEasing,
    SVGContent,
    TextContent,
    TextRun,
    Timing,
    Track,
    VideoContent,
)

EPS = 0.001
"""The 1ms step the HyperFrames compositions use for stepped tweens."""

RAMP_FRAMES: dict[str, int] = {
    "cut": 0,
    "fade": 8,
    "slide": 8,
    "scale": 8,
    "mask": 8,
    "draw": 12,
    "blur": 10,
    "typewriter": 20,
    "glitch": 10,
    "flip": 12,
}
"""The entrance/exit ramp lengths, shared by FullFrameCard and MotionGraphics."""

FLIP_REFUSAL = (
    "flip needs 3D perspective FFrames has no lowering for; "
    "refusing rather than approximating it as a flat 2D rotate"
)
"""Why the FFrames lowering refuses flip by name, at plan time."""

TYPE_SIZE: dict[str, float] = {"display": 56, "supporting": 36, "micro": 24}
TYPE_WEIGHT: dict[str, float] = {"display": 900, "supporting": 700, "micro": 600}

STAGE_STAGGER = 5
STAGED_PARTS = 3

CAPTION_LINE_HEIGHT = 1.2
"""SubtitleOverlay's ``.box`` line height."""
CAPTION_WORD_GAP_EM = 0.24
"""SubtitleOverlay's ``.word`` trailing margin, in the word's own ems."""
CAPTION_PAD_Y = 24
"""SubtitleOverlay's ``.box`` vertical padding."""
CAPTION_EMPHASIS_EM = 1.14
"""SubtitleOverlay's emphasis-word size."""
CAPTION_KARAOKE_SCALE = 1.0
"""SubtitleOverlay's maximum scale, at the word's measured layout size."""
CAPTION_KARAOKE_REST_SCALE = 0.95
"""SubtitleOverlay's resting scale outside the word's highlight window."""
CAPTION_KARAOKE_RAMP_FRAMES = 4
"""Frames used to ease the karaoke scale up and back to its base size."""
CAPTION_DROP_SHADOW = "0 10px 20px rgba(0,0,0,0.8)"
"""The drop shadow SubtitleOverlay appends after its eight outline shadows."""

DEFAULT_TEXT_SHADOW = "0px 4px 12px rgba(0,0,0,0.6)"
"""The shadow TimedTextOverlay and MotionGraphics runs draw."""

CARD_LINE_HEIGHT = 1.18
CARD_RUN_GAP = 20
"""FullFrameCard's ``.run`` line height and ``#motion`` gap."""

MG_LINE_HEIGHT = 1.1
MG_STACK_GAP = 24
MG_DISPLAY_LETTER_SPACING = 3.0
MG_ELEMENT_GAPS = {"title_lockup": 12, "quote_card": 16, "lower_third": 12}
"""MotionGraphics: run line height, anchor-stack gap, the display role's
tracking, and each text arm's run gap."""

MG_STAMP_PAD_V = 8
MG_STAMP_PAD_H = 16
MG_STAMP_RADIUS = 4
MG_STAMP_TEXT_SCALE = 0.8
"""context_stamp's pill: vertical/horizontal padding, corner radius, and
the copy scale inside it (``scl={scale * 0.8}``). The ink is white on the
plan's colour with no text shadow of its own - the pill carries the one
box shadow (``0 4px 12px rgba(0,0,0,0.4)``)."""

MG_BAR_GAP = 16
MG_BAR_LABEL_GAP = 4
MG_BAR_TRACK_HEIGHT = 12
MG_BAR_RADIUS = 6
MG_BAR_LABEL_LINE_HEIGHT = 1.5
"""comparison_bars' stack gap, label-to-track gap, track height and radii,
and the label line box. The label states no line-height in Remotion, so
both browser sides read ``normal``; HyperFrames pins 1.5 explicitly
because the two bundled Chromiums resolve Montserrat's normal
differently (measured ~10px short with it unset on a 120-frame stack).
The lowering quotes the pin."""

MG_COUNTER_FIGURE_SCALE = 1.5
MG_COUNTER_LABEL_SCALE = 0.7
MG_COUNTER_GAP = 8
MG_COUNTER_ROLL_SPAN = 0.8
MG_COUNTER_LETTER_SPACING = 2.0
"""counter_roll's figure scale (``scale * 1.5`` at weight 900), label
scale, figure-to-label gap, the fraction of the life the roll runs over
(``durationFrames * 0.8``, cubic ease-out), and the figure tracking."""

MG_STEP_GAP = 8
MG_STEP_NUMBER_GAP = 4
MG_STEP_OF_LINE_HEIGHT = 1.5
MG_STEP_OF_OPACITY = 0.6
"""step_counter's figure-to-label gap, the number-to-"of" gap, the "of"
span's line box (same unset-``normal`` pin as the bars labels), and its
faded opacity."""

MG_POINTER_SIZE = 80
MG_POINTER_BORDER = 8
MG_POINTER_GLOW = 16
"""pointer_annotation's ring outer size, border thickness, and glow
spread (``box-shadow: 0 0 16px`` outer plus an inset twin the IR cannot
express - the lowering draws the outer only). The 80px is the BORDER
BOX: Tailwind's preflight (imported by the Remotion app's index.css)
sizes the div border-box, so the 8px border lives INSIDE the 80 and the
content is 64 (measured 80x80 ink on the Remotion side, not 96)."""

MG_WEBSITE_RADIUS = 16
MG_WEBSITE_BORDER = 3
MG_WEBSITE_BACKGROUND = "rgba(0, 0, 0, 0.85)"
MG_WEBSITE_ADDRESS_PAD_V = 10
MG_WEBSITE_ADDRESS_PAD_H = 16
"""website_panel's chrome: corner radius, border thickness, panel fill,
and the address bar's padding. The bar carries the plan's first copy run
as the address (micro, white, 1.5 line box); no copy stated, no bar. The
capture is a project-supplied file the plan names - the engine never
screenshots the web itself."""

MG_BOX_SHADOW_HALF = 0.5
"""Where a template ``box-shadow`` blur lands in a ``DropShadowFilter``.

Measured on the review card's ``0 10px 30px`` falloff (alpha 56 vs 44 at
15px out unhalved, 73/74-43/44-11/10 halved): the SVG filter's gaussian
spreads about twice as far per unit as the CSS shadow's. ``filter:
drop-shadow()`` still maps 1:1 (the StagedScene parity gate passes on
it) - the halving is the box-shadow spelling only."""
MG_BAR_HEIGHT = 12.0
MG_BAR_TRACK = "rgba(255, 255, 255, 0.15)"
MG_BAR_GLOW = 16.0
"""`progress_bar`'s drawing, quoted from `DrawnElement`'s arm in
`remotion-subtitles/src/compositions/MotionGraphics/index.tsx` (and the
same arm in `hyperframes/compositions/MotionGraphics.html`): a 12px bar
spanning the safe box at the top inset when the anchor starts with
`top`, else at the bottom inset, over a white-at-15% track, the fill in
the plan's colour with an unscaled `0 0 16px` glow in its own colour.
The fill lerps `timelineProgressStart` to `timelineProgressEnd` (whole
fractions of 100) across the window minus one frame, clamped, ease
none. The arm reads neither the entrance nor the exit transform, so the
lowering carries no character animations for it - opacity only. The
glow's 16px goes in as sigma 8: a `box-shadow` blur radius draws like
half the `drop-shadow()` filter blur of the same number (measured
30px -> 15 on the real review card's falloff)."""

MG_ACCENT_SIZE = 80.0
MG_ACCENT_STROKE = 6.0
"""`frame_accents`' drawing, quoted from the same files: four corner
brackets, each an 80px square carrying two 6px borders in the plan's
colour (top+left, top+right, bottom+left, bottom+right), pinned to the
four safe insets. Chrome, like the bar: opacity only, no character."""

MG_COUNTER_SCALE = 1.5
MG_COUNTER_TRACKING = 2.0
MG_COUNTER_DIGIT_WIDTH = 0.65
MG_COUNTER_LINE_HEIGHT = 1.2
MG_COUNTER_GLOW = "0 0 40px {color}40, 0 4px 16px rgba(0,0,0,0.5)"
"""`digit_counter`'s drawing, quoted from `DigitRoll` /
`buildDigitCounter` / `armDigitRoll` in the same files: the figure at
the display size times 1.5 in weight 900, each digit rolling in its own
`fontSize * 0.65` by `fontSize * 1.2` cell on a ten-high strip, the row
tracked at 2px under the figure glow; the plan's label runs underneath
at 0.7 scale through the shared run style, with an 8px gap. Non-digit
characters (grouping commas, decimals, affixes) draw static."""

MG_DIGIT_STIFFNESS = 80.0
MG_DIGIT_DAMPING = 15.0
MG_DIGIT_MASS = 0.8
MG_DIGIT_STAGGER = 0.06
"""The digit roll's spring, quoted from `DigitRoll`'s `spring()` call in
the Remotion composition: damping 15, stiffness 80, mass 0.8, each digit
staggered 0.06s per reverse index so the least significant settles
first, over the window minus its stagger. The lowering emits one IR
`SpringEasing` animation per rail with that same config - the IR
evaluator and the FFrames renderer integrate the same analytic
damped-harmonic-oscillator physics Remotion's `spring()` uses, so the
curve matches by shared integrator rather than by baked samples."""
MG_EMBLEM_PLATE = 200.0
MG_EMBLEM_RADIUS = 48.0
MG_EMBLEM_GAP = 16.0
MG_EMBLEM_MARK_SIZE = 112.0
MG_EMBLEM_LABEL_SCALE = 0.8
MG_EMBLEM_MARK_WEIGHT = 900.0
MG_EMBLEM_MARK_SHADOW = "0 4px 16px rgba(0,0,0,0.5)"
MG_EMBLEM_PLATE_SHADOW = (0.0, 8.0, 16.0, "rgba(0,0,0,0.45)")
"""`subject_emblem`'s drawing, quoted from `DrawnElement`'s arm in
`remotion-subtitles/src/compositions/MotionGraphics/index.tsx`: the plate
is a 200px square at radius 48 carrying the display run as its mark at
twice the display size in weight 900, white with a dark shadow, over the
plan's colour with an unscaled 0 8px 32px shadow; the supporting runs
label it underneath at 0.8 scale through the shared run style, with a
16px gap and no gap between the label runs themselves (a fragment, not
a column). The mark is the run's raw text: the arm sets no
`textTransform` on it, so a display run's 3px tracking does not apply
either. Every `box-shadow` blur below reaches the IR halved: a
`box-shadow` blur radius draws like half the `drop-shadow()` filter
blur of the same number (measured 30px -> 15 on the real review card's
falloff, 74/73 44/43 10/11 at 5/15/30px out), so the plate's 32px goes
in as 16."""

MG_BEAT_SIZE = 120.0
MG_BEAT_GROWTH = (0.5, 1.5)
"""`beat_accent`'s drawing, quoted from the same file: a disc in the
plan's colour with an unscaled `0 0 24px` glow in its own colour, whose
diameter is `round(120 * scale * (0.5 + progress))` and whose opacity is
the element's ramp times `(1 - progress)`. The arm reads neither the
entrance nor the exit transform, so the lowering carries no character
animations for it. Its unscaled `0 0 24px` glow goes in at half blur,
like every `box-shadow` below."""

MG_REVIEW_PANEL = {
    "width": 660,
    "pad": 26,
    "radius": 20,
    "rule": 2,
    "nameSize": 34,
    "nameLine": 1.25,
    "nameToScore": 12,
    "scoreSize": 30,
    "scoreLine": 1.2,
    "scoreGap": 10,
    "headerStar": 26,
    "labelSize": 24,
    "scoreToRows": 20,
    "rowPadY": 18,
    "avatar": 46,
    "avatarLetter": 22,
    "avatarGap": 16,
    "authorSize": 26,
    "authorLine": 1.2,
    "authorToBody": 8,
    "rowStar": 20,
    "whenSize": 22,
    "bodySize": 26,
    "bodyLine": 1.35,
    "bodyLines": 2,
    "nameWeight": 600,
    "scoreWeight": 700,
    "labelWeight": 500,
    "authorWeight": 600,
    "whenWeight": 500,
    "bodyWeight": 400,
}
"""`review_panel`'s drawing, quoted from `REVIEW_PANEL` in the same file:
a review-listing mockup whose every box carries an explicit height, so
the drawn height is arithmetic rather than browser line boxes. The card
states its own left alignment and an unscaled `0 10px 30px
rgba(0,0,0,0.45)` shadow, which goes in at half blur like every
`box-shadow` below; the stars are drawn paths (see
`MG_REVIEW_STAR_PATH`), never glyphs."""

MG_REVIEW_STAR_PATH = (
    "M12 2.1l3.06 6.2 6.84 1-4.95 4.82 1.17 6.81L12 17.72 "
    "5.88 20.93l1.17-6.81L2.1 9.3l6.84-1z"
)
MG_REVIEW_STAR_VIEWBOX = "0 0 24 24"
"""The drawn star's path and viewBox, quoted from `STAR_PATH` beside the
arm: a five-star rating draws five of these at the header or row size,
each filled star clipped to its fraction over a muted one."""

MG_LIST_LINE_HEIGHT = 1.3
"""list_build's ``LINE_HEIGHT``: every item sits on a 1.3 line box, not the
1.1 the text arms use."""

MG_STAT_SCALE = 1.5
"""stat_callout's ``scl={scale * 1.5}``: the figure and its label draw at one
and a half times the plan's footprint, because the callout is the moment's
dominant graphic."""

MG_STAT_GAP = 12
"""stat_callout's container gap: a literal ``12px`` in the template, NOT
scaled by the footprint the way list_build's gaps are."""

MG_LIST_GAP = 12
"""list_build's container gap (``gap: 12 * scale``) and row gap (the row's
``gap: 12 * scale`` between marker and copy): both scale."""

MG_LIST_DOT = 8
"""list_build's marker diameter (``8 * scale``), a circle in the plan's own
colour."""

MG_LIST_LIFT = 20
"""How far a list item rises as it arrives (``translateY`` 20 to 0)."""

POST_NAME_COLOR = "#FFFFFF"
POST_HANDLE_COLOR = "#8B98A5"
POST_BADGE_FILL = "#1D9BF0"
POST_BADGE_CHECK_FILL = "#FFFFFF"
POST_BADGE_PATH = (
    "M22.25 12c0-1.43-.88-2.67-2.19-3.34.46-1.39.2-2.9-.81-3.91s-2.52-1.27-3.91-.81"
    "c-.66-1.31-1.91-2.19-3.34-2.19s-2.67.88-3.33 2.19c-1.4-.46-2.91-.2-3.92.81"
    "s-1.26 2.52-.8 3.91C2.63 9.33 1.75 10.57 1.75 12s.88 2.67 2.19 3.34"
    "c-.46 1.39-.2 2.9.81 3.91s2.52 1.26 3.91.81c.67 1.31 1.91 2.19 3.34 2.19"
    "s2.68-.88 3.34-2.19c1.39.46 2.9.2 3.91-.81s1.27-2.52.81-3.91"
    "c1.31-.67 2.19-1.91 2.19-3.34z"
)
POST_BADGE_CHECK_PATH = "M10.54 16.2l-3.74-3.74 1.41-1.41 2.33 2.33 5.66-5.66 1.41 1.41z"
"""PostHeader's template constants: the header's text colours and the
verified seal, two paths in a 24-unit viewBox."""


def _outline_shadow(width: float, colour: str) -> str:
    """SubtitleOverlay's eight-direction outline plus its drop shadow."""
    w = float(width)
    offsets = [(-w, -w), (0, -w), (w, -w), (w, 0), (w, w), (0, w), (-w, w), (-w, 0)]
    parts = [f"{_num(dx)}px {_num(dy)}px 0 {colour}" for dx, dy in offsets]
    return ", ".join(parts + [CAPTION_DROP_SHADOW])


def _num(value: float) -> str:
    return f"{value:g}"


def _greedy_rows(widths: list[float], limit: float | None) -> list[list[int]]:
    """Flex-wrap: items fill a row until the next would overflow *limit*."""
    rows: list[list[int]] = []
    current: list[int] = []
    used = 0.0
    for i, width in enumerate(widths):
        if current and limit is not None and used + width > limit + 0.01:
            rows.append(current)
            current, used = [], 0.0
        current.append(i)
        used += width
    if current:
        rows.append(current)
    return rows


def lower_subtitle_overlay(props: dict[str, Any]) -> Scene:
    """Lower SubtitleOverlay props to an IR scene.

    Each subtitle block becomes a Group windowed to its span. Each word
    with timing becomes a Text node carrying the accent sweep (two
    stepped colour tweens) and a 0.95 to 1.0 to 0.95 scale pulse during
    the word's highlight window. The transform never exceeds the
    font-measured layout size and does not change layout width or line breaks.
    Words flow the way the template's flex-wrap box flows
    them: each word an inline-block with a 0.24em trailing margin, rows
    centred on the card, the box's bottom on the safe area.
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    style = props.get("style", {})
    subtitles = props.get("subtitles", [])

    font_color = style.get("fontColor", "#FFFFFF")
    accent_color = style.get("accentColor", "#FBF0B8")
    font_family = style.get("fontFamily", "Montserrat")
    font_file = style.get("fontFile") or props.get("fontFile")
    base_size = float(style.get("fontSize", 58))
    font_weight = float(style.get("fontWeight", 800))
    outline_width = float(style.get("outlineWidth") or 4)
    outline_color = style.get("outlineColor", "#000000")
    position = style.get("position", "bottom")
    safe_area = style.get("safeArea", {"top": 0, "right": 0, "bottom": 0, "left": 0})
    max_width = float(style.get("captionMaxWidth", width))
    shadow = _outline_shadow(outline_width, outline_color)
    content_limit = max_width - 2 * outline_width

    def text_content(text: str, size: float, align: str) -> TextContent:
        return TextContent(
            text=text, font_family=font_family, font_file=font_file,
            font_size=size, font_weight=font_weight, color=font_color,
            text_align=align, line_height=CAPTION_LINE_HEIGHT,
            text_shadow=shadow,
        )

    def box_top(box_height: float) -> float:
        if position == "top":
            return float(safe_area.get("top", 0))
        if position == "center":
            return (height - box_height) / 2.0
        return height - float(safe_area.get("bottom", 0)) - box_height

    nodes: list[Node] = []

    for i, sub in enumerate(subtitles):
        card_id = f"subtitle_{i}"
        start_frame = int(sub["startFrame"])
        end_frame = int(sub["endFrame"])
        card_timing = Timing(start=start_frame / fps,
                             duration=max(1, end_frame - start_frame) / fps)

        fit_scale = sub.get("fitScale")
        px = round(base_size * fit_scale) if fit_scale is not None and fit_scale < 1 else base_size
        words = sub.get("words", [])
        emphasis_words = sub.get("emphasisWords", [])
        children: list[str] = []

        if words:
            sizes = [px * (CAPTION_EMPHASIS_EM if _is_emphasis(w["word"], emphasis_words) else 1.0)
                     for w in words]
            advances = [measure_text(w["word"], size, font_file, font_weight)
                        for w, size in zip(words, sizes)]
            outers = [adv + CAPTION_WORD_GAP_EM * size for adv, size in zip(advances, sizes)]
            rows = _greedy_rows(outers, content_limit)
            row_heights = [max(CAPTION_LINE_HEIGHT * sizes[j] for j in row) for row in rows]
            box_height = sum(row_heights) + 2 * CAPTION_PAD_Y
            top = box_top(box_height)
            card_bounds = Bounds(x=0, y=top, width=width, height=box_height)
            y = top + CAPTION_PAD_Y
            placed: dict[int, Bounds] = {}
            for row, row_height in zip(rows, row_heights):
                x = width / 2.0 - sum(outers[j] for j in row) / 2.0
                for j in row:
                    item_height = CAPTION_LINE_HEIGHT * sizes[j]
                    placed[j] = Bounds(x=x, y=y + (row_height - item_height) / 2.0,
                                       width=advances[j], height=item_height)
                    x += outers[j]
                y += row_height

            for j, word_timing in enumerate(words):
                word_id = f"{card_id}_word_{j}"
                ws = int(word_timing["startFrame"]) / fps
                we = int(word_timing["endFrame"]) / fps
                animations: list[Animation] = []
                if we > ws:
                    animations.append(Animation(
                        target=word_id, property="color",
                        from_value=font_color, to_value=accent_color,
                        start=max(0.0, ws - EPS), duration=EPS, easing=LinearEasing(),
                    ))
                    animations.append(Animation(
                        target=word_id, property="color",
                        from_value=accent_color, to_value=font_color,
                        start=max(0.0, we - EPS), duration=EPS, easing=LinearEasing(),
                    ))
                    ramp = min(CAPTION_KARAOKE_RAMP_FRAMES / fps,
                               (we - ws) / 2.0)
                    pulse_ease = CubicBezierEasing(
                        x1=0.42, y1=0.0, x2=0.58, y2=1.0)
                    animations.append(Animation(
                        target=word_id, property="transform.scale",
                        from_value=[CAPTION_KARAOKE_REST_SCALE,
                                    CAPTION_KARAOKE_REST_SCALE],
                        to_value=[CAPTION_KARAOKE_SCALE, CAPTION_KARAOKE_SCALE],
                        start=ws, duration=ramp, easing=pulse_ease,
                    ))
                    animations.append(Animation(
                        target=word_id, property="transform.scale",
                        from_value=[CAPTION_KARAOKE_SCALE, CAPTION_KARAOKE_SCALE],
                        to_value=[CAPTION_KARAOKE_REST_SCALE,
                                  CAPTION_KARAOKE_REST_SCALE],
                        start=we - ramp, duration=ramp, easing=pulse_ease,
                    ))
                nodes.append(Node(
                    id=word_id, type=NodeType.TEXT,
                    text=text_content(word_timing["word"], sizes[j], "left"),
                    bounds=placed[j], animations=animations,
                ))
                children.append(word_id)
        else:
            text = sub.get("text", "")
            lines = wrap_text(text, content_limit, font_size=px,
                              font_file=font_file, font_weight=font_weight)
            text_width = min(content_limit, block_width(lines, font_size=px, font_file=font_file,
                                                        font_weight=font_weight))
            text_height = len(lines) * CAPTION_LINE_HEIGHT * px
            box_height = text_height + 2 * CAPTION_PAD_Y
            top = box_top(box_height)
            card_bounds = Bounds(x=0, y=top, width=width, height=box_height)
            text_id = f"{card_id}_text"
            nodes.append(Node(
                id=text_id, type=NodeType.TEXT,
                text=text_content("\n".join(lines), px, "center"),
                bounds=Bounds(x=(width - text_width) / 2.0, y=top + CAPTION_PAD_Y,
                              width=text_width, height=text_height),
            ))
            children.append(text_id)

        nodes.append(Node(
            id=card_id, type=NodeType.GROUP, bounds=card_bounds,
            timing=card_timing, children=children,
        ))

    return Scene(version=1, fps=fps, width=width, height=height,
                 duration=duration_in_frames / fps, nodes=nodes)


def lower_timed_text_overlay(props: dict[str, Any]) -> Scene:
    """Lower TimedTextOverlay props to an IR scene.

    Each moment becomes a Text node windowed to its span, carrying the
    fade-in and fade-out opacity ramps (linear, clamped to fit). The box
    is the template's: ``max-content`` wide up to the safe-area-bounded
    max width, ``pre-line``, centred on ``(x, y)``, ``line-height: normal``.
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    moments = props.get("moments", [])
    font_family = props.get("fontFamily", "Helvetica")
    font_file = props.get("fontFile")
    safe_area = props.get("safeArea", {"top": 0, "right": 0, "bottom": 0, "left": 0})

    nodes: list[Node] = []

    for i, moment in enumerate(moments):
        dur = int(moment.get("durationFrames", 0))
        if dur <= 0:
            continue

        moment_id = f"moment_{i}"
        t0 = int(moment["startFrame"]) / fps
        fade_in = max(0, min(int(moment.get("fadeInFrames", 0)), dur))
        fade_out = max(0, min(int(moment.get("fadeOutFrames", 0)), dur - fade_in))

        animations: list[Animation] = []
        if fade_in > 0:
            animations.append(Animation(
                target=moment_id, property="opacity", from_value=0.0, to_value=1.0,
                start=t0, duration=fade_in / fps, easing=LinearEasing(),
            ))
        if fade_out > 0:
            animations.append(Animation(
                target=moment_id, property="opacity", from_value=1.0, to_value=0.0,
                start=t0 + (dur - fade_out) / fps, duration=fade_out / fps,
                easing=LinearEasing(),
            ))

        cx = float(moment.get("x", 0.5)) * width
        cy = float(moment.get("y", 0.5)) * height
        size = float(moment.get("fontSize", 48))
        weight = float(moment.get("fontWeight", 400))
        text = str(moment.get("text", ""))
        limit = max(0.0, 2 * min(cx - safe_area.get("left", 0),
                                 width - safe_area.get("right", 0) - cx))
        measure = {"font_size": size, "font_file": font_file, "font_weight": weight}
        lines = wrap_text(text, limit, **measure)
        natural = block_width(text.split("\n"), **measure)
        box_width = min(natural, limit)
        box_height = len(lines) * line_box_height(size, None, font_file)

        nodes.append(Node(
            id=moment_id, type=NodeType.TEXT,
            text=TextContent(
                text="\n".join(lines), font_family=font_family, font_file=font_file,
                font_size=size, font_weight=weight,
                color=moment.get("color", "#FFFFFF"),
                text_align=moment.get("textAlign", "center"),
                text_shadow=moment.get("textShadow") or DEFAULT_TEXT_SHADOW,
            ),
            bounds=Bounds(x=cx - box_width / 2.0, y=cy - box_height / 2.0,
                          width=box_width, height=box_height),
            timing=Timing(start=t0, duration=dur / fps),
            animations=animations,
        ))

    return Scene(version=1, fps=fps, width=width, height=height,
                 duration=duration_in_frames / fps, nodes=nodes)


def _run_size(run: dict[str, Any]) -> float:
    """FullFrameCard's ``sizeOf``: the run's own size, else its role's."""
    return float(run.get("font_size") or TYPE_SIZE.get(run.get("type_role"), TYPE_SIZE["supporting"]))


def _run_weight(run: dict[str, Any]) -> float:
    return float(TYPE_WEIGHT.get(run.get("type_role"), TYPE_WEIGHT["supporting"]))


def _image_height(src: str, box_width: float) -> float:
    """``height: auto`` of an image drawn *box_width* wide."""
    try:
        from PIL import Image

        with Image.open(src) as image:
            w, h = image.size
    except (OSError, ValueError, ImportError):
        return 0.0
    return box_width * h / w if w else 0.0


def lower_full_frame_card(props: dict[str, Any]) -> Scene:
    """Lower FullFrameCard props to an IR scene.

    The card becomes a Ground with a Group carrying the opacity ramp and
    the entrance/exit character. Runs become Text nodes stacked the way
    the template's ``#motion`` column stacks them (20px gap, 1.18 line
    height, each run centred and wrapped in the safe column). Word cues
    become per-word animations on words placed inside their run's lines.
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    duration = duration_in_frames / fps
    runs = props.get("runs", [])
    entrance = props.get("entrance", "cut")
    exit_char = props.get("exit", "cut")
    font_family = props.get("fontFamily", "Montserrat")
    font_file = props.get("fontFile")
    safe_area = props.get("safeArea", {"top": 0, "right": 0, "bottom": 0, "left": 0})
    image = props.get("image")
    image_width = props.get("imageWidth")
    word_cues = props.get("wordCues", [])
    emphasis_colour = props.get("emphasisColour")
    hold_frames = props.get("holdFrames")

    in_frames = RAMP_FRAMES.get(entrance, 0)
    out_frames = RAMP_FRAMES.get(exit_char, 0)
    t_in = in_frames / fps
    t_out = out_frames / fps

    hold_ease: Any = LinearEasing()
    if hold_frames is not None and hold_frames > 1:
        hold_ease = StepsEasing(steps=math.ceil(duration_in_frames / hold_frames), direction="end")

    column_x = float(safe_area["left"])
    column_width = float(width - safe_area["left"] - safe_area["right"])

    def run_lines(text: str, run: dict[str, Any]) -> list[str]:
        return wrap_text(text, column_width, font_size=_run_size(run),
                         font_file=font_file, font_weight=_run_weight(run))

    def run_height(lines: list[str], run: dict[str, Any]) -> float:
        return len(lines) * CARD_LINE_HEIGHT * _run_size(run)

    def run_text(text: str, run: dict[str, Any]) -> TextContent:
        return TextContent(
            text=text, font_family=font_family, font_file=font_file,
            font_size=_run_size(run), font_weight=_run_weight(run),
            color=run.get("colour", "#FFFFFF"), text_align="center",
            line_height=CARD_LINE_HEIGHT,
        )

    image_height = 0.0
    image_box_width = 0.0
    if image:
        image_box_width = min(column_width, float(image_width)) if image_width else column_width
        image_height = _image_height(image, image_box_width)

    # The column's height decides where it starts; it is laid out from
    # the WHOLE runs, the way the block-level template lays them out.
    full_lines = [run_lines(run.get("text", ""), run) for run in runs]
    items = ([image_height] if image else []) + [run_height(lines, run)
                                                 for lines, run in zip(full_lines, runs)]
    content_height = sum(items) + CARD_RUN_GAP * max(0, len(items) - 1)
    y_prop = props.get("y")
    if y_prop is None:
        top = safe_area["top"] + (height - safe_area["top"] - safe_area["bottom"] - content_height) / 2.0
    else:
        # The template pads the full-height stack down by H*y, then
        # shifts the stack up by half its own (full) height.
        top = max(0.0, height * float(y_prop)) - height / 2.0

    run_tops: list[float] = []
    cursor = top
    if image:
        cursor += image_height + CARD_RUN_GAP
    for lines, run in zip(full_lines, runs):
        run_tops.append(cursor)
        cursor += run_height(lines, run) + CARD_RUN_GAP

    nodes: list[Node] = [Node(
        id="ground", type=NodeType.GROUND,
        bounds=Bounds(x=0, y=0, width=width, height=height),
    )]

    card_animations: list[Animation] = []
    if in_frames > 0:
        card_animations.append(Animation(
            target="card", property="opacity", from_value=0.0, to_value=1.0,
            start=0.0, duration=t_in, easing=hold_ease,
        ))
    if out_frames > 0:
        card_animations.append(Animation(
            target="card", property="opacity", from_value=1.0, to_value=0.0,
            start=max(0.0, duration - t_out), duration=t_out, easing=hold_ease,
        ))
    if in_frames > 0 and t_in > 0:
        card_animations.extend(_entrance_animations("card", entrance, t_in, hold_ease))
    if out_frames > 0 and t_out > 0:
        card_animations.extend(_exit_animations("card", exit_char, duration, t_out, hold_ease))

    card_children: list[str] = []

    if image:
        image_id = "card_image"
        nodes.append(Node(
            id=image_id, type=NodeType.IMAGE, image=ImageContent(src=image),
            bounds=Bounds(x=column_x + (column_width - image_box_width) / 2.0, y=top,
                          width=image_box_width, height=image_height),
        ))
        card_children.append(image_id)

    def whole_run(node_id: str, text: str, ri: int) -> Node:
        run = runs[ri]
        lines = run_lines(text, run)
        return Node(
            id=node_id, type=NodeType.TEXT, text=run_text("\n".join(lines), run),
            bounds=Bounds(x=column_x, y=run_tops[ri], width=column_width,
                          height=run_height(lines, run)),
        )

    cued = word_cues and len(word_cues) > 0 and entrance in ("typewriter", "mask", "draw")

    if not cued and entrance != "typewriter":
        for i, run in enumerate(runs):
            node = whole_run(f"run_{i}", run.get("text", ""), i)
            nodes.append(node)
            card_children.append(node.id)
    elif entrance == "typewriter":
        run_lengths = [len(r.get("text", "")) for r in runs]
        total = sum(run_lengths)
        if total > 0 and in_frames > 0:
            states = _typewriter_states(run_lengths, total, in_frames, duration_in_frames, hold_frames)
            for si, st in enumerate(states):
                layer_id = f"type_layer_{si}"
                f0 = st["f0"]
                f1 = st["f1"] if st["f1"] is not None else duration_in_frames
                layer_children: list[str] = []
                for ri, run in enumerate(runs):
                    shown = st["shown"][ri]
                    if shown == 0 and st["cursor_run"] != ri:
                        continue
                    node = whole_run(f"{layer_id}_run_{ri}", run.get("text", "")[:shown], ri)
                    nodes.append(node)
                    layer_children.append(node.id)
                nodes.append(Node(
                    id=layer_id, type=NodeType.GROUP,
                    timing=Timing(start=f0 / fps, duration=(f1 - f0) / fps),
                    children=layer_children,
                ))
                card_children.append(layer_id)
    else:
        joined = "".join(r.get("text", "") for r in runs)
        words = _split_words(joined)
        ordered = sorted(word_cues, key=lambda c: c.get("start", 0))
        ok = len(words) == len(ordered)
        if ok:
            for w, c in zip(words, ordered):
                if w["text"].lower() != str(c.get("word", "")).lower():
                    ok = False
                    break

        if not ok:
            for i, run in enumerate(runs):
                node = whole_run(f"run_{i}", run.get("text", ""), i)
                nodes.append(node)
                card_children.append(node.id)
        else:
            texts = [r.get("text", "") for r in runs]
            word_boxes = _word_boxes_in_runs(
                texts, full_lines, runs, run_tops, column_x, column_width, font_file)
            for wi, word in enumerate(words):
                at = word["at"]
                run_index = len(texts) - 1
                acc = 0
                for q, text in enumerate(texts):
                    acc += len(text)
                    if at < acc:
                        run_index = q
                        break
                run = runs[run_index] if run_index < len(runs) else runs[0]
                cue = ordered[wi]
                lead = cue.get("lead", 0)
                t0 = max(0.0, cue.get("start", 0) - lead)
                t1 = max(t0, cue.get("end", 0) - lead)
                word_id = f"word_{wi}"

                word_animations: list[Animation] = []
                if entrance == "draw":
                    for prop, frm, to in (("transform.scale", [0.85, 0.85], [1.0, 1.0]),
                                          ("blur", 4.0, 0.0), ("opacity", 0.0, 1.0)):
                        word_animations.append(Animation(
                            target=word_id, property=prop, from_value=frm, to_value=to,
                            start=t0, duration=max(EPS, t1 - t0), easing=EaseEasing(power=3),
                        ))
                else:
                    word_animations.append(Animation(
                        target=word_id, property="transform.translate",
                        from_value=[0.0, 120.0], to_value=[0.0, 0.0],
                        start=t0, duration=max(EPS, t1 - t0), easing=EaseEasing(power=3),
                    ))

                if emphasis_colour:
                    word_animations.append(Animation(
                        target=word_id, property="color",
                        from_value=run.get("colour", "#FFFFFF"), to_value=emphasis_colour,
                        start=max(0.0, cue.get("start", 0) - EPS), duration=EPS,
                        easing=LinearEasing(),
                    ))
                    word_animations.append(Animation(
                        target=word_id, property="color",
                        from_value=emphasis_colour, to_value=run.get("colour", "#FFFFFF"),
                        start=max(0.0, cue.get("end", 0) - EPS), duration=EPS,
                        easing=LinearEasing(),
                    ))

                content = run_text(word["text"], run)
                content.text_align = "left"
                nodes.append(Node(
                    id=word_id, type=NodeType.TEXT, text=content,
                    bounds=word_boxes[wi] if wi < len(word_boxes) else None,
                    animations=word_animations,
                ))
                card_children.append(word_id)

    nodes.append(Node(
        id="card", type=NodeType.GROUP,
        bounds=Bounds(x=column_x, y=top, width=column_width, height=content_height),
        animations=card_animations, children=card_children,
    ))

    return Scene(version=1, fps=fps, width=width, height=height,
                 duration=duration, nodes=nodes)


def _word_boxes_in_runs(texts, full_lines, runs, run_tops, column_x, column_width,
                        font_file) -> list[Bounds]:
    """Each word's box where the whole run's centred lines put it."""
    boxes: list[Bounds] = []
    for ri, (lines, run) in enumerate(zip(full_lines, runs)):
        size = _run_size(run)
        measure = {"font_size": size, "font_file": font_file, "font_weight": _run_weight(run)}
        line_height = CARD_LINE_HEIGHT * size
        for li, line in enumerate(lines):
            line_x = column_x + (column_width - measure_text(line, **measure)) / 2.0
            y = run_tops[ri] + li * line_height
            offset = 0
            for word in line.split():
                at = line.index(word, offset)
                x = line_x + measure_text(line[:at], **measure)
                boxes.append(Bounds(x=x, y=y, width=measure_text(word, **measure),
                                    height=line_height))
                offset = at + len(word)
    return boxes


def _element_window(el: dict[str, Any]) -> tuple[int, int]:
    start = int(el.get("startFrame", 0))
    return start, start + int(el.get("durationFrames", 1))


def lower_motion_graphics(props: dict[str, Any]) -> Scene:
    """Lower MotionGraphics props to an IR scene.

    Each element becomes a Group windowed to its span, carrying the
    opacity ramp and the entrance/exit character. Child nodes depend on
    the element's arm (title_lockup, quote_card, lower_third,
    channel_bug, context_stamp, comparison_bars, counter_roll,
    step_counter, pointer_annotation, website_panel,
    subject_emblem, review_panel, beat_accent).
    Text arms sit in their anchor's stack the way the template stacks
    them: a 24px-gap column at the safe area's edge, rows ordered, and
    only the elements live together laid out together.
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    elements = props.get("elements", [])
    safe_area = props.get("safeArea", {"top": 0, "right": 0, "bottom": 0, "left": 0})
    layout_width = props.get("layoutWidth")
    font_family = props.get("fontFamily", "Montserrat")
    font_file = props.get("fontFile")
    context = _MGContext(fps, width, height, safe_area, layout_width, font_family, font_file)

    nodes: list[Node] = []
    self_positioning = {"progress_bar", "frame_accents"}

    for el in elements:
        if el.get("element") in self_positioning:
            built = _build_mg_element(el, context, None)
            if built:
                group_node, child_nodes = built
                nodes.append(group_node)
                nodes.extend(child_nodes)

    anchors: list[str] = []
    for el in elements:
        if el.get("element") in self_positioning:
            continue
        anchor = el.get("anchor", "centre")
        if anchor not in anchors:
            anchors.append(anchor)

    for anchor in anchors:
        stack_children: list[str] = []
        anchor_elements = sorted(
            [el for el in elements
             if el.get("element") not in self_positioning and el.get("anchor", "centre") == anchor],
            key=lambda e: e.get("row", 0),
        )
        for el in anchor_elements:
            start, _ = _element_window(el)
            live = [other for other in anchor_elements
                    if _element_window(other)[0] <= start < _element_window(other)[1]]
            boxes = _stack_boxes(live, anchor, context)
            built = _build_mg_element(el, context, boxes.get(id(el)))
            if built:
                group_node, child_nodes = built
                nodes.append(group_node)
                nodes.extend(child_nodes)
                stack_children.append(group_node.id)

        nodes.append(Node(id=f"stack_{anchor}", type=NodeType.GROUP, children=stack_children))

    return Scene(version=1, fps=fps, width=width, height=height,
                 duration=duration_in_frames / fps, nodes=nodes)


def lower_post_header(props: dict[str, Any]) -> Scene:
    """Lower PostHeader props to an IR scene.

    A static composition laid out the way the template's flexbox lays it
    out: the avatar (a filled, rounded box with the image contained at
    72%), the identity column centred beside it (name and verified seal
    on one row, handle under it), and the hook wrapped across the box
    below. No animations.
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    box = props.get("box", {"left": 0, "top": 0, "width": width})
    font_family = props.get("fontFamily", "Montserrat")
    font_file = props.get("fontFile")
    name_size = float(props.get("nameSize", 32))
    handle_size = float(props.get("handleSize", 24))
    hook_size = float(props.get("hookSize", 28))
    avatar_size = round(name_size * 1.3 + handle_size * 1.3)
    gap = round(name_size * 0.55)
    left, top, box_width = float(box["left"]), float(box["top"]), float(box["width"])

    def text(value: str, size: float, weight: float, colour: str, line_height: float,
             align: str = "left") -> TextContent:
        return TextContent(text=value, font_family=font_family, font_file=font_file,
                           font_size=size, font_weight=weight, color=colour,
                           text_align=align, line_height=line_height)

    name_row = max(1.2 * name_size, round(name_size * 1.05) if props.get("verified") else 0)
    identity_height = name_row + 1.25 * handle_size
    account_height = max(avatar_size, identity_height)
    identity_x = left + avatar_size + gap
    identity_y = top + (account_height - identity_height) / 2.0
    avatar_y = top + (account_height - avatar_size) / 2.0

    nodes: list[Node] = []
    children: list[str] = []

    radius = avatar_size / 2.0 if props.get("avatarShape") == "round" else avatar_size * 0.08
    if props.get("avatarBackground"):
        nodes.append(Node(
            id="avatar_ground", type=NodeType.SHAPE,
            shape=ShapeContent(shape="rect", fill=props["avatarBackground"], corner_radius=radius),
            bounds=Bounds(x=left, y=avatar_y, width=avatar_size, height=avatar_size),
        ))
        children.append("avatar_ground")
    inner = avatar_size * 0.72
    nodes.append(Node(
        id="avatar", type=NodeType.IMAGE,
        image=ImageContent(src=props.get("avatarSrc", "")),
        bounds=Bounds(x=left + (avatar_size - inner) / 2.0, y=avatar_y + (avatar_size - inner) / 2.0,
                      width=inner, height=inner),
    ))
    children.append("avatar")

    name_value = props.get("displayName", "")
    name_width = measure_text(name_value, name_size, font_file, 700)
    nodes.append(Node(
        id="name", type=NodeType.TEXT,
        text=text(name_value, name_size, 700, POST_NAME_COLOR, 1.2),
        bounds=Bounds(x=identity_x, y=identity_y + (name_row - 1.2 * name_size) / 2.0,
                      width=name_width, height=1.2 * name_size),
    ))
    children.append("name")

    if props.get("verified"):
        badge_size = round(name_size * 1.05)
        badge_bounds = Bounds(x=identity_x + name_width + name_size * 0.25,
                              y=identity_y + (name_row - badge_size) / 2.0,
                              width=badge_size, height=badge_size)
        for badge_id, path, fill in (("badge", POST_BADGE_PATH, POST_BADGE_FILL),
                                     ("badge_check", POST_BADGE_CHECK_PATH, POST_BADGE_CHECK_FILL)):
            nodes.append(Node(
                id=badge_id, type=NodeType.SVG,
                svg=SVGContent(viewBox="0 0 24 24", path=path, fill=fill),
                bounds=badge_bounds,
            ))
            children.append(badge_id)

    handle_value = "@" + props.get("handle", "")
    nodes.append(Node(
        id="handle", type=NodeType.TEXT,
        text=text(handle_value, handle_size, 500, POST_HANDLE_COLOR, 1.25),
        bounds=Bounds(x=identity_x, y=identity_y + name_row,
                      width=measure_text(handle_value, handle_size, font_file, 500),
                      height=1.25 * handle_size),
    ))
    children.append("handle")

    hook_lines = wrap_text(props.get("hook", ""), box_width, font_size=hook_size,
                           font_file=font_file, font_weight=600)
    hook_top = top + account_height + round(hook_size * 0.6)
    nodes.append(Node(
        id="hook", type=NodeType.TEXT,
        text=text("\n".join(hook_lines), hook_size, 600, POST_NAME_COLOR, 1.3),
        bounds=Bounds(x=left, y=hook_top, width=box_width,
                      height=len(hook_lines) * 1.3 * hook_size),
    ))
    children.append("hook")

    nodes.append(Node(
        id="header", type=NodeType.GROUP,
        bounds=Bounds(x=left, y=top, width=box_width,
                      height=hook_top + len(hook_lines) * 1.3 * hook_size - top),
        children=children,
    ))

    return Scene(version=1, fps=fps, width=width, height=height,
                 duration=duration_in_frames / fps, nodes=nodes)


def _is_emphasis(word: str, emphasis_words: list[str]) -> bool:
    if not emphasis_words:
        return False
    normalised = word.lower().strip(".,!?;:'\"")
    if not normalised:
        return False
    return any(normalised == e.lower().strip(".,!?;:'\"") for e in emphasis_words)


def _entrance_animations(
    target: str, entrance: str, t_in: float, easing: Any
) -> list[Animation]:
    """The entrance character's animations, matching the HyperFrames tweens."""
    if entrance == "slide":
        return [Animation(
            target=target, property="transform.translate",
            from_value=[0.0, 40.0], to_value=[0.0, 0.0],
            start=0.0, duration=t_in, easing=easing,
        )]
    if entrance == "scale":
        return [Animation(
            target=target, property="transform.scale",
            from_value=[0.7, 0.7], to_value=[1.0, 1.0],
            start=0.0, duration=t_in, easing=easing,
        )]
    if entrance == "mask":
        return [Animation(
            target=target, property="clip",
            from_value="inset(0 100% 0 0)", to_value="inset(0 0% 0 0)",
            start=0.0, duration=t_in, easing=easing,
        )]
    if entrance == "draw":
        return [
            Animation(
                target=target, property="transform.scale",
                from_value=[0.85, 0.85], to_value=[1.0, 1.0],
                start=0.0, duration=t_in, easing=easing,
            ),
            Animation(
                target=target, property="blur",
                from_value=4.0, to_value=0.0,
                start=0.0, duration=t_in, easing=easing,
            ),
        ]
    if entrance == "blur":
        return [
            Animation(
                target=target, property="blur",
                from_value=8.0, to_value=0.0,
                start=0.0, duration=t_in, easing=easing,
            ),
            Animation(
                target=target, property="transform.translate",
                from_value=[0.0, 12.0], to_value=[0.0, 0.0],
                start=0.0, duration=t_in, easing=easing,
            ),
        ]
    if entrance == "flip":
        raise ValueError(f"flip entrance refused: {FLIP_REFUSAL}")
    if entrance == "glitch":
        anims: list[Animation] = []
        quarter = t_in / 4
        for qi, (frm, to) in enumerate([(0.0, 6.0), (-4.0, -4.0), (2.0, 2.0), (0.0, 0.0)]):
            anims.append(Animation(
                target=target, property="transform.translate",
                from_value=[frm, 0.0], to_value=[to, 0.0],
                start=qi * quarter, duration=quarter, easing=easing,
            ))
        return anims
    return []


def _exit_animations(
    target: str, exit_char: str, duration: float, t_out: float, easing: Any
) -> list[Animation]:
    """The exit character's animations, matching the HyperFrames tweens."""
    t1 = max(0.0, duration - t_out)
    if exit_char == "slide":
        return [Animation(
            target=target, property="transform.translate",
            from_value=[0.0, 0.0], to_value=[0.0, -40.0],
            start=t1, duration=t_out, easing=easing,
        )]
    if exit_char == "scale":
        return [Animation(
            target=target, property="transform.scale",
            from_value=[1.0, 1.0], to_value=[0.7, 0.7],
            start=t1, duration=t_out, easing=easing,
        )]
    if exit_char == "mask":
        return [Animation(
            target=target, property="clip",
            from_value="inset(0 0% 0 0)", to_value="inset(0 0 0 100%)",
            start=t1, duration=t_out, easing=easing,
        )]
    if exit_char == "draw":
        return [
            Animation(
                target=target, property="transform.scale",
                from_value=[1.0, 1.0], to_value=[0.85, 0.85],
                start=t1, duration=t_out, easing=easing,
            ),
            Animation(
                target=target, property="blur",
                from_value=0.0, to_value=4.0,
                start=t1, duration=t_out, easing=easing,
            ),
        ]
    if exit_char == "blur":
        return [
            Animation(
                target=target, property="blur",
                from_value=0.0, to_value=8.0,
                start=t1, duration=t_out, easing=easing,
            ),
            Animation(
                target=target, property="transform.translate",
                from_value=[0.0, 0.0], to_value=[0.0, -12.0],
                start=t1, duration=t_out, easing=easing,
            ),
        ]
    if exit_char == "flip":
        raise ValueError(f"flip exit refused: {FLIP_REFUSAL}")
    if exit_char == "glitch":
        anims: list[Animation] = []
        third = t_out / 3
        for qi, to in enumerate([5.0, -3.0, 0.0]):
            anims.append(Animation(
                target=target, property="transform.translate",
                from_value=[0.0, 0.0], to_value=[to, 0.0],
                start=t1 + qi * third, duration=third, easing=easing,
            ))
        return anims
    return []


def _typewriter_states(
    run_lengths: list[int],
    total: int,
    in_frames: int,
    duration_in_frames: int,
    hold_frames: int | None,
) -> list[dict[str, Any]]:
    """Group typewriter reveal states, matching the HyperFrames build."""
    states: list[dict[str, Any]] = []
    last_key: str | None = None
    for f in range(duration_in_frames):
        hf = (f // hold_frames) * hold_frames if hold_frames and hold_frames > 1 else f
        if in_frames > 0:
            prog = min(1.0, max(0.0, hf / in_frames))
        else:
            prog = 1.0
        remaining = round(total * min(1.0, max(0.0, prog)))
        shown: list[int] = []
        cursor = -1
        for i, length in enumerate(run_lengths):
            take = min(length, max(0, remaining))
            shown.append(take)
            remaining -= take
            if cursor == -1 and prog < 1 and take < length:
                cursor = i
        key = ",".join(str(s) for s in shown) + "|" + str(cursor if prog < 1 else -1)
        if key != last_key:
            states.append({"f0": f, "f1": None, "shown": shown, "cursor_run": cursor, "prog": prog})
            last_key = key
    for i, st in enumerate(states):
        if i + 1 < len(states):
            st["f1"] = states[i + 1]["f0"]
    return states


def _split_words(text: str) -> list[dict[str, Any]]:
    """Split text into words with their character offsets."""
    words: list[dict[str, Any]] = []
    import re
    for m in re.finditer(r"\S+", text):
        words.append({"text": m.group(0), "at": m.start()})
    return words




class _MGContext:
    """What every MotionGraphics element is laid out against."""

    def __init__(self, fps, width, height, safe_area, layout_width, font_family, font_file):
        self.fps = fps
        self.width = width
        self.height = height
        self.safe_area = safe_area
        self.layout_width = layout_width
        self.font_family = font_family
        self.font_file = font_file


_MG_TEXT_ARMS = ("title_lockup", "quote_card", "lower_third")

MG_FFRAMES_COVERED_ARMS = frozenset(
    (*_MG_TEXT_ARMS, "channel_bug", "list_build", "stat_callout")
)
"""MotionGraphics arms FFrames can draw yet (census G2).

An element naming any other arm has no lowering: `_build_mg_element`
returns None for it, and the 4.06 dispatch routes such a card to the
Remotion render explicitly rather than shipping FFrames blanks."""


def _anchor_parts(anchor: str) -> tuple[str, str]:
    """The template's ``anchorParts``: (vertical, horizontal)."""
    return {
        "top_left": ("top", "left"), "top_centre": ("top", "centre"),
        "top_right": ("top", "right"), "middle_left": ("middle", "left"),
        "middle_right": ("middle", "right"), "bottom_left": ("bottom", "left"),
        "bottom_centre": ("bottom", "centre"), "bottom_right": ("bottom", "right"),
    }.get(anchor, ("middle", "centre"))


def _mg_scale(el: dict[str, Any]) -> float:
    footprint = el.get("footprint")
    return footprint if footprint and footprint > 0 else 1.0


def _channel_bug_width(el: dict[str, Any], ctx: _MGContext) -> float:
    """The bug's drawn width, in pixels.

    The composition's rule (`anchorStyle` + `safeUsableWidth` in
    `remotion-subtitles/src/compositions/MotionGraphics/index.tsx`,
    mirrored in `hyperframes/compositions/MotionGraphics.html`):
    the usable width between the safe-area insets times the plan's
    footprint share, defaulting to the whole usable width when the
    plan states none - there is no default size, because a bug drawn
    at a size the engine chose is the engine deciding how loud a
    client's mark is.
    """
    left = float(ctx.safe_area.get("left", 0))
    right = float(ctx.safe_area.get("right", 0))
    usable = max(0.0, ctx.width - left - right)
    return float(round(usable * _mg_scale(el)))


def _channel_bug_aspect(src: str) -> float | None:
    """The image's width/height ratio, or None when it cannot be read.

    A `data:` URI or a path nothing can open has no measurable
    geometry here; the caller treats that as uncoverable (no bounds)
    rather than guessing, so the dispatch falls back to Remotion
    instead of shipping a blank.
    """
    if not src or src.startswith("data:"):
        return None
    try:
        from PIL import Image

        with Image.open(src) as image:
            width, height = image.size
        if height > 0:
            return width / height
    except (OSError, ValueError):
        return None
    return None


def _channel_bug_size(el: dict[str, Any], ctx: _MGContext) -> tuple[float, float] | None:
    """The bug's `(width, height)`, or None when the height is unknowable."""
    width = _channel_bug_width(el, ctx)
    aspect = _channel_bug_aspect(str(el.get("asset") or ""))
    if aspect is None or aspect <= 0 or width <= 0:
        return None
    return width, width / aspect


def _mg_run_style(run: dict[str, Any], scale: float, ctx: _MGContext) -> dict[str, Any]:
    """The template's ``runStyle``, as measurement arguments."""
    role = run.get("type_role", "supporting")
    display = role == "display"
    return {
        "font_size": TYPE_SIZE.get(role, TYPE_SIZE["supporting"]) * scale,
        "font_file": ctx.font_file,
        "font_weight": TYPE_WEIGHT.get(role, TYPE_WEIGHT["supporting"]),
        "letter_spacing": MG_DISPLAY_LETTER_SPACING if display else 0.0,
        "text_transform": "uppercase" if display else None,
    }


def _mg_inset(el: dict[str, Any]) -> float:
    """The quote card's left border plus padding; zero for other arms."""
    if el.get("element") != "quote_card":
        return 0.0
    scale = _mg_scale(el)
    return float(round(8 * scale) + round(28 * scale))


def _mg_run_lines(el: dict[str, Any], box_width: float, ctx: _MGContext) -> list[tuple]:
    """``(run, style, lines, height)`` for each run, wrapped in *box_width*."""
    scale = _mg_scale(el)
    text_width = box_width - _mg_inset(el)
    out = []
    for run in el.get("runs", []):
        style = _mg_run_style(run, scale, ctx)
        lines = wrap_text(run.get("text", ""), text_width, **style)
        out.append((run, style, lines, len(lines) * MG_LINE_HEIGHT * style["font_size"]))
    return out


def _mg_emblem_runs(el: dict[str, Any]) -> tuple[dict | None, list[dict]]:
    """The emblem's mark (the display run) and its label runs."""
    runs = el.get("runs") or []
    mark = next((r for r in runs if r.get("type_role") == "display"), None)
    label = [r for r in runs if r.get("type_role") != "display"]
    return mark, label


def _mg_emblem_label_style(run: dict[str, Any], scale: float,
                           ctx: _MGContext) -> dict[str, Any]:
    """The shared run style at the emblem label's 0.8 scale."""
    role = run.get("type_role", "supporting")
    return {
        "font_size": TYPE_SIZE.get(role, TYPE_SIZE["supporting"]) * scale * MG_EMBLEM_LABEL_SCALE,
        "font_file": ctx.font_file,
        "font_weight": TYPE_WEIGHT.get(role, TYPE_WEIGHT["supporting"]),
        "letter_spacing": MG_DISPLAY_LETTER_SPACING if role == "display" else 0.0,
        "text_transform": "uppercase" if run.get("uppercase") else None,
    }


def _mg_emblem_label_lines(el: dict[str, Any], box_width: float,
                           ctx: _MGContext) -> list[tuple]:
    """``(run, style, lines)`` for the label, wrapped at *box_width*."""
    scale = _mg_scale(el)
    out = []
    for run in _mg_emblem_runs(el)[1]:
        style = _mg_emblem_label_style(run, scale, ctx)
        out.append((run, style, wrap_text(run.get("text", ""), box_width, **style)))
    return out


def _mg_emblem_mark_width(el: dict[str, Any], ctx: _MGContext) -> float:
    """The mark's unwrapped width at twice the display size."""
    mark, _ = _mg_emblem_runs(el)
    if mark is None:
        return 0.0
    scale = _mg_scale(el)
    style = {"font_size": MG_EMBLEM_MARK_SIZE * scale, "font_file": ctx.font_file,
                 "font_weight": MG_EMBLEM_MARK_WEIGHT}
    return block_width(str(mark.get("text", "")).split("\n"), **style)


def _mg_review_rows(el: dict[str, Any]) -> list[dict]:
    data = el.get("data") or {}
    rows = data.get("rows")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _mg_review_palette(el: dict[str, Any]) -> dict | None:
    data = el.get("data") or {}
    palette = data.get("palette")
    if not isinstance(palette, dict):
        return None
    if any(role not in palette for role in ("surface", "ink", "muted", "star", "rule")):
        return None
    return palette


def _mg_review_card_size(el: dict[str, Any], ctx: _MGContext) -> tuple[float, float]:
    """The card's drawn size, quoting the arm's piece-by-piece arithmetic.

    Mirrors `review_panel_size` in `library/tools/mg_tight_box.py`, which
    quotes the same table: every box carries an explicit height, so no
    font is measured here.
    """
    panel = MG_REVIEW_PANEL
    scale = _mg_scale(el)

    def px(value: float) -> int:
        return _mg_px(value, scale)

    name_h = px(panel["nameSize"] * panel["nameLine"])
    score_h = max(px(panel["scoreSize"] * panel["scoreLine"]), px(panel["headerStar"]))
    body_line = px(panel["bodySize"] * panel["bodyLine"])
    row_inner = max(
        px(panel["avatar"]),
        px(panel["authorSize"] * panel["authorLine"])
        + px(panel["authorToBody"])
        + panel["bodyLines"] * body_line)
    # The row div is border-box: its top rule lives INSIDE `row_h`, so the
    # rule is not added here. Counting it again draws every row 2px too
    # tall and the card 2px too tall per row (measured 457 vs 453 on the
    # real two-row panel).
    row_h = 2 * px(panel["rowPadY"]) + row_inner
    height = (2 * px(panel["pad"]) + name_h + px(panel["nameToScore"])
              + score_h + px(panel["scoreToRows"])
              + len(_mg_review_rows(el)) * row_h)
    return float(px(panel["width"])), float(height)


def _mg_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    if el.get("element") == "stat_callout":
        return _mg_stat_natural_width(el, ctx)
    if el.get("element") == "list_build":
        return _mg_list_natural_width(el, ctx)
    if el.get("element") == "channel_bug":
        return _channel_bug_width(el, ctx)
    if el.get("element") == "digit_counter":
        return _mg_counter_natural_width(el, ctx)
    kind = el.get("element", "")
    if kind == "context_stamp":
        return _mg_stamp_natural_width(el, ctx)
    if kind == "comparison_bars":
        return _mg_bars_natural_width(el, ctx)
    if kind == "counter_roll":
        return _mg_roll_natural_width(el, ctx)
    if kind == "step_counter":
        return _mg_step_natural_width(el, ctx)
    if kind == "pointer_annotation":
        return MG_POINTER_SIZE * _mg_scale(el)
    if kind == "website_panel":
        return _mg_website_size(el, ctx)[0]
    if kind == "subject_emblem":
        scale = _mg_scale(el)
        widest = MG_EMBLEM_PLATE * scale
        widest = max(widest, _mg_emblem_mark_width(el, ctx))
        for run in _mg_emblem_runs(el)[1]:
            style = _mg_emblem_label_style(run, scale, ctx)
            widest = max(widest, block_width(str(run.get("text", "")).split("\n"), **style))
        return widest
    if kind == "review_panel":
        return _mg_review_card_size(el, ctx)[0]
    if kind == "beat_accent":
        return MG_BEAT_SIZE * _mg_scale(el) * MG_BEAT_GROWTH[1]
    if el.get("element") not in _MG_TEXT_ARMS:
        return 0.0
    scale = _mg_scale(el)
    widest = 0.0
    for run in el.get("runs", []):
        style = _mg_run_style(run, scale, ctx)
        widest = max(widest, block_width(run.get("text", "").split("\n"), **style))
    return widest + _mg_inset(el)


def _mg_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    if el.get("element") == "stat_callout":
        return _mg_stat_height(el, box_width, ctx)
    if el.get("element") == "list_build":
        return _mg_list_height(el, box_width, ctx)
    if el.get("element") == "channel_bug":
        size = _channel_bug_size(el, ctx)
        return size[1] if size is not None else 0.0
    if el.get("element") == "digit_counter":
        return _mg_counter_height(el, box_width, ctx)
    kind = el.get("element", "")
    if kind == "context_stamp":
        return _mg_stamp_height(el, box_width, ctx)
    if kind == "comparison_bars":
        return _mg_bars_height(el, box_width, ctx)
    if kind == "counter_roll":
        return _mg_roll_height(el, box_width, ctx)
    if kind == "step_counter":
        return _mg_step_height(el, box_width, ctx)
    if kind == "pointer_annotation":
        return MG_POINTER_SIZE * _mg_scale(el)
    if kind == "website_panel":
        return _mg_website_size(el, ctx)[1]
    if kind == "subject_emblem":
        scale = _mg_scale(el)
        height = MG_EMBLEM_PLATE * scale
        label = _mg_emblem_label_lines(el, box_width, ctx)
        if label:
            label_h = sum(len(lines) * MG_LINE_HEIGHT * style["font_size"]
                          for _, style, lines in label)
            height += MG_EMBLEM_GAP * scale + label_h
        return height
    if kind == "review_panel":
        return _mg_review_card_size(el, ctx)[1]
    if kind == "beat_accent":
        return MG_BEAT_SIZE * _mg_scale(el) * MG_BEAT_GROWTH[1]
    if el.get("element") not in _MG_TEXT_ARMS:
        return 0.0
    heights = [h for *_, h in _mg_run_lines(el, box_width, ctx)]
    gap = MG_ELEMENT_GAPS.get(el.get("element"), 12)
    return sum(heights) + gap * max(0, len(heights) - 1)


def _mg_px(value: float, scale: float) -> int:
    """JavaScript `Math.round(n * scale)`, as each arm computes its pieces.

    Every geometric piece below is rounded before it is laid out, so the
    lowering rounds each piece too rather than rounding a total: rounding
    once at the end disagrees by a pixel wherever the composition rounds
    a .5 up. Python's `round` is banker's (half to even) and would round
    those same .5s down.
    """
    return math.floor(float(value) * scale + 0.5)


def _mg_number(value: Any) -> float:
    """A plan fraction as a float: missing or unparseable reads as 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mg_bar_geometry(el: dict[str, Any], ctx: _MGContext) -> tuple[float, float, float, float]:
    """The bar's `(left, y, track_width, height)`, from the safe box alone.

    The bar spans the safe box left to right at the top inset when the
    anchor starts with `top`, else at the bottom inset - the template's
    `startsWith("top")` rule, so a centre anchor draws at the bottom.
    """
    scale = _mg_scale(el)
    left = float(ctx.safe_area.get("left", 0))
    right = float(ctx.safe_area.get("right", 0))
    track_width = max(0.0, ctx.width - left - right)
    bar_h = float(_mg_px(MG_BAR_HEIGHT, scale))
    if str(el.get("anchor", "")).startswith("top"):
        y = float(ctx.safe_area.get("top", 0))
    else:
        y = ctx.height - float(ctx.safe_area.get("bottom", 0)) - bar_h
    return left, y, track_width, bar_h


def _mg_counter_figure(el: dict[str, Any], ctx: _MGContext) -> tuple[float, float, str]:
    """The figure's `(font_size, digit_height, display_value)`.

    `decimals > 0` fixes that many places, else the value rounds to a
    whole grouped en-US (`12,345`) - the template's `toFixed` /
    `toLocaleString("en-US")` contract, with JavaScript's half-up
    rounding rather than Python's banker's.
    """
    data = el.get("data") or {}
    end_value = data.get("end_value")
    end = float(end_value) if isinstance(end_value, (int, float)) else 100.0
    decimals = data.get("decimals")
    places = int(decimals) if isinstance(decimals, (int, float)) else 0
    if places > 0:
        value = f"{end:.{places}f}"
    else:
        value = f"{math.floor(end + 0.5):,}"
    font_size = TYPE_SIZE["display"] * _mg_scale(el) * MG_COUNTER_SCALE
    return font_size, font_size * MG_COUNTER_LINE_HEIGHT, value


def _mg_counter_affix(el: dict[str, Any], name: str) -> str:
    """The figure's prefix or suffix: a string the plan stated, else none."""
    data = el.get("data") or {}
    affix = data.get(name)
    return affix if isinstance(affix, str) else ""


def _mg_counter_chars(el: dict[str, Any], ctx: _MGContext) -> list[tuple[str, Any, float]]:
    """The figure left to right: `(kind, payload, width)` per character.

    A digit rolls in a `fontSize * 0.65` cell; anything else (grouping
    commas, the decimal point, no digit at all) draws static at its
    measured width. Tracking is measured with the width: the row is set
    at 2px, the way the composition sets it.
    """
    font_size, _, value = _mg_counter_figure(el, ctx)
    chars: list[tuple[str, Any, float]] = []
    prefix = _mg_counter_affix(el, "prefix")
    if prefix:
        chars.append(("affix", prefix, measure_text(
            prefix, font_size, ctx.font_file, 900.0,
            letter_spacing=MG_COUNTER_TRACKING)))
    for char in value:
        if char in "0123456789":
            chars.append(("digit", int(char), font_size * MG_COUNTER_DIGIT_WIDTH))
        else:
            chars.append(("static", char, measure_text(
                char, font_size, ctx.font_file, 900.0,
                letter_spacing=MG_COUNTER_TRACKING)))
    suffix = _mg_counter_affix(el, "suffix")
    if suffix:
        chars.append(("affix", suffix, measure_text(
            suffix, font_size, ctx.font_file, 900.0,
            letter_spacing=MG_COUNTER_TRACKING)))
    return chars


def _mg_counter_label_style(run: dict[str, Any], scale: float, ctx: _MGContext) -> dict[str, Any]:
    """The label's run style at the counter's 0.7 scale.

    The Remotion arm draws the label through `runStyle` with each run's
    own `uppercase` flag - copy renders as written unless the run
    declares it, the same rule the stat_callout lowering quotes.
    """
    role = run.get("type_role", "supporting")
    display = role == "display"
    return {
        "font_size": TYPE_SIZE.get(role, TYPE_SIZE["supporting"]) * scale * MG_COUNTER_LABEL_SCALE,
        "font_file": ctx.font_file,
        "font_weight": TYPE_WEIGHT.get(role, TYPE_WEIGHT["supporting"]),
        "letter_spacing": MG_DISPLAY_LETTER_SPACING if display else 0.0,
        "text_transform": "uppercase" if run.get("uppercase") else None,
    }


def _mg_counter_label_lines(el: dict[str, Any], box_width: float,
                            ctx: _MGContext) -> list[tuple]:
    """``(run, style, lines, height)`` for the label, wrapped in *box_width*."""
    scale = _mg_scale(el)
    out = []
    for run in el.get("runs", []):
        style = _mg_counter_label_style(run, scale, ctx)
        lines = wrap_text(run.get("text", ""), box_width, **style)
        out.append((run, style, lines, len(lines) * MG_LINE_HEIGHT * style["font_size"]))
    return out


def _mg_counter_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    figure = sum(width for _, _, width in _mg_counter_chars(el, ctx))
    widest = figure
    scale = _mg_scale(el)
    for run in el.get("runs", []):
        style = _mg_counter_label_style(run, scale, ctx)
        widest = max(widest, block_width(str(run.get("text", "")).split("\n"), **style))
    return widest


def _mg_counter_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    _, digit_height, _ = _mg_counter_figure(el, ctx)
    height = digit_height
    label = _mg_counter_label_lines(el, box_width, ctx)
    if label:
        height += round(MG_COUNTER_GAP * _mg_scale(el)) + sum(h for *_, h in label)
    return height


def _mg_digit_spring() -> SpringEasing:
    """One digit rail's easing: `DigitRoll`'s own `spring()` config.

    The IR evaluator and the FFrames renderer integrate this with the
    same analytic damped-harmonic-oscillator step Remotion's `spring()`
    uses, so the rail draws the same curve from the same numbers.
    """
    return SpringEasing(stiffness=MG_DIGIT_STIFFNESS,
                        damping=MG_DIGIT_DAMPING, mass=MG_DIGIT_MASS)


def _mg_stat_run_style(run: dict[str, Any], scale: float, ctx: _MGContext) -> dict[str, Any]:
    """The template's ``runStyle`` at stat_callout's scale, as measurement arguments.

    ``runStyle(run.type_role, scale * 1.5, element.color, run.uppercase)``:
    the display role tracks at 3px, and copy renders uppercased only when
    the run declares it - the case the plan stated. This is the template's
    rule, not ``_mg_run_style``'s (which uppercases every display run);
    every real plan states its display copy already cased, so the two agree
    wherever it matters, and this one draws what the template draws where
    they do not.
    """
    role = run.get("type_role", "supporting")
    display = role == "display"
    return {
        "font_size": TYPE_SIZE.get(role, TYPE_SIZE["supporting"]) * scale * MG_STAT_SCALE,
        "font_file": ctx.font_file,
        "font_weight": TYPE_WEIGHT.get(role, TYPE_WEIGHT["supporting"]),
        "letter_spacing": MG_DISPLAY_LETTER_SPACING if display else 0.0,
        "text_transform": "uppercase" if run.get("uppercase") else None,
    }


def _mg_stat_run_lines(el: dict[str, Any], box_width: float, ctx: _MGContext) -> list[tuple]:
    """``(run, style, lines, height)`` for each stat_callout run, wrapped in *box_width*."""
    scale = _mg_scale(el)
    out = []
    for run in el.get("runs", []):
        style = _mg_stat_run_style(run, scale, ctx)
        lines = wrap_text(run.get("text", ""), box_width, **style)
        out.append((run, style, lines, len(lines) * MG_LINE_HEIGHT * style["font_size"]))
    return out


def _mg_stat_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    widest = 0.0
    for run in el.get("runs", []):
        style = _mg_stat_run_style(run, _mg_scale(el), ctx)
        widest = max(widest, block_width(run.get("text", "").split("\n"), **style))
    return widest


def _mg_stat_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    heights = [h for *_, h in _mg_stat_run_lines(el, box_width, ctx)]
    # The template's gap is a literal 12px: unscaled by the footprint.
    return sum(heights) + MG_STAT_GAP * max(0, len(heights) - 1)


def _mg_list_lead_size(el: dict[str, Any], ctx: _MGContext) -> float:
    """The list's ONE size: the lead item's type_role governs every item.

    Peer items share one size; weights stay per-run. Quoted from the arm
    (and from ``mg_tight_box._list_item_width``, which measures the same
    rule): a display-first / supporting-rest plan declares emphasis, not a
    size ladder.
    """
    del ctx
    runs = el.get("runs", [])
    lead_role = runs[0].get("type_role", "supporting") if runs else "supporting"
    return TYPE_SIZE.get(lead_role, TYPE_SIZE["supporting"])


def _mg_list_text_width(box_width: float, scale: float) -> float:
    """The copy column: the box minus the marker and its gap (8 + 12, scaled)."""
    return max(0.0, box_width - (MG_LIST_DOT + MG_LIST_GAP) * scale)


def _mg_list_item_lines(el: dict[str, Any], box_width: float, ctx: _MGContext) -> list[tuple]:
    """``(run, size, weight, lines, height)`` per item, wrapped in the copy column."""
    scale = _mg_scale(el)
    size = _mg_list_lead_size(el, ctx) * scale
    text_width = _mg_list_text_width(box_width, scale)
    out = []
    for run in el.get("runs", []):
        # Items draw as written: no uppercase, no tracking, whatever the role.
        style = {"font_size": size, "font_file": ctx.font_file,
                     "font_weight": TYPE_WEIGHT.get(run.get("type_role", "supporting"),
                                                 TYPE_WEIGHT["supporting"])}
        lines = wrap_text(run.get("text", ""), text_width, **style)
        out.append((run, size, style["font_weight"], lines,
                    len(lines) * MG_LIST_LINE_HEIGHT * size))
    return out


def _mg_list_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    scale = _mg_scale(el)
    size = _mg_list_lead_size(el, ctx) * scale
    widest = 0.0
    for run in el.get("runs", []):
        weight = TYPE_WEIGHT.get(run.get("type_role", "supporting"),
                                 TYPE_WEIGHT["supporting"])
        widest = max(widest, measure_text(run.get("text", ""), size, ctx.font_file, weight))
    return widest + (MG_LIST_DOT + MG_LIST_GAP) * scale


def _mg_list_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    rows = _mg_list_item_lines(el, box_width, ctx)
    heights = [h for *_, h in rows]
    gap = round(MG_LIST_GAP * _mg_scale(el))
    return sum(heights) + gap * max(0, len(heights) - 1)


def _mg_stage_starts(el: dict[str, Any], count: int, fps: float) -> tuple[list[int], float, bool]:
    """When each staged item begins, in frames local to the element.

    The template's ``stageStarts``: the plan's own per-stage offsets
    (``data.stage_offsets``, seconds from the element's start, clamped
    into the hold and forced non-decreasing) where it states enough of
    them, else an equal share of the hold. The reveal window is the
    entrance ramp on anchored stages, the stagger interval otherwise.
    """
    duration_frames = int(el.get("durationFrames", 1))
    entrance = el.get("entrance", "cut")
    exit_char = el.get("exit", "cut")
    hold_frames = max(1, duration_frames - (RAMP_FRAMES.get(exit_char, 0) or 0))
    data = el.get("data") or {}
    raw = data.get("stage_offsets")
    offsets = raw if isinstance(raw, list) else []

    def _num(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    if len(offsets) >= count > 0:
        starts: list[int] = []
        previous = 0
        for i in range(count):
            frame = round(max(0.0, _num(offsets[i])) * fps)
            clamped = max(previous, min(frame, hold_frames - 1))
            starts.append(clamped)
            previous = clamped
        window = max(1.0, float(RAMP_FRAMES.get(entrance, RAMP_FRAMES["fade"])))
        return starts, window, True

    stagger = hold_frames / max(1, count)
    return [round(i * stagger) for i in range(count)], stagger, False


def _mg_stamp_run_lines(el: dict[str, Any], box_width: float, ctx: _MGContext) -> list[tuple]:
    """``(run, style, lines, height)`` for each stamp run at 0.8 scale."""
    scale = _mg_scale(el) * MG_STAMP_TEXT_SCALE
    text_width = max(0.0, box_width - 2 * MG_STAMP_PAD_H * _mg_scale(el))
    out = []
    for run in el.get("runs", []):
        style = _mg_run_style(run, scale, ctx)
        lines = wrap_text(run.get("text", ""), text_width, **style)
        out.append((run, style, lines, len(lines) * MG_LINE_HEIGHT * style["font_size"]))
    return out


def _mg_stamp_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    scale = _mg_scale(el) * MG_STAMP_TEXT_SCALE
    widest = 0.0
    for run in el.get("runs", []):
        style = _mg_run_style(run, scale, ctx)
        widest = max(widest, block_width(str(run.get("text", "")).split("\n"), **style))
    return widest + 2 * MG_STAMP_PAD_H * _mg_scale(el)


def _mg_stamp_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    # The pill is a fragment of run divs: no gap between runs, padding
    # top and bottom inside the coloured box.
    heights = [h for *_, h in _mg_stamp_run_lines(el, box_width, ctx)]
    return sum(heights) + 2 * MG_STAMP_PAD_V * _mg_scale(el)


def _mg_bars_items(el: dict[str, Any]) -> tuple[list[dict], list[float]]:
    """The bars the arm draws: runs paired with values, either cut short.

    The template reads ``min(runs, values)`` - extra of either is
    ignored - and draws nothing when either side is empty.
    """
    runs = el.get("runs") or []
    data = el.get("data") or {}
    raw_values = data.get("values")
    values = raw_values if isinstance(raw_values, list) else []
    count = min(len(runs), len(values))
    if count <= 0:
        return [], []
    numbers: list[float] = []
    for value in values[:count]:
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            numbers.append(0.0)
    return list(runs[:count]), numbers


def _mg_bars_label_style(run: dict[str, Any], scale: float, ctx: _MGContext) -> dict[str, Any]:
    """The bars label style: the run's size and weight, drawn as written.

    No tracking, no uppercase, whatever the role - the template states
    neither, and the 1.5 line box is HyperFrames' pin for the ``normal``
    both browser sides otherwise resolve differently.
    """
    role = run.get("type_role", "micro")
    return {
        "font_size": TYPE_SIZE.get(role, TYPE_SIZE["micro"]) * scale,
        "font_file": ctx.font_file,
        "font_weight": TYPE_WEIGHT.get(role, TYPE_WEIGHT["micro"]),
    }


def _mg_bars_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    """The widest bars label, unwrapped: what a side-anchored stack
    sizes to. A side stack shrink-wraps its content, so the ``width:
    100%`` track resolves against the widest label (measured 292px on
    a side-anchored Remotion stack, not the 960px usable width) - while
    a centre stack spans the insets and the track spans it. The stack,
    not this number, picks which: centre stacks never consult it."""
    runs, _ = _mg_bars_items(el)
    if not runs:
        return 0.0
    scale = _mg_scale(el)
    widest = 0.0
    for run in runs:
        style = _mg_bars_label_style(run, scale, ctx)
        widest = max(widest, block_width(
            str(run.get("text", "")).split("\n"), **style))
    return widest


def _mg_bars_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    runs, _ = _mg_bars_items(el)
    if not runs:
        return 0.0
    scale = _mg_scale(el)
    height = 0.0
    for run in runs:
        style = _mg_bars_label_style(run, scale, ctx)
        lines = wrap_text(str(run.get("text", "")), box_width, **style)
        height += len(lines) * MG_BAR_LABEL_LINE_HEIGHT * style["font_size"]
        height += MG_BAR_LABEL_GAP * scale + round(MG_BAR_TRACK_HEIGHT * scale)
    if len(runs) > 1:
        height += round(MG_BAR_GAP * scale) * (len(runs) - 1)
    return height


def _mg_counter_data(el: dict[str, Any]) -> tuple[float, float, str, str, int]:
    """The roll's drive: start, end, affixes and decimals, with the
    template's defaults (0 to 100, no affixes, whole figures)."""
    data = el.get("data") or {}

    def _number(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    start = _number(data.get("start_value"), 0.0)
    end = _number(data.get("end_value"), 100.0)
    prefix = data.get("prefix") if isinstance(data.get("prefix"), str) else ""
    suffix = data.get("suffix") if isinstance(data.get("suffix"), str) else ""
    decimals = data.get("decimals")
    decimals = int(decimals) if isinstance(decimals, (int, float)) else 0
    return start, end, prefix, suffix, max(0, decimals)


def _mg_counter_format(value: float, prefix: str, suffix: str, decimals: int) -> str:
    """One roll figure, as drawn: grouped thousands (en-US, the locale
    both browser sides render under) or fixed decimals."""
    if decimals > 0:
        body = f"{value:.{decimals}f}"
    else:
        body = f"{round(value):,}"
    return f"{prefix}{body}{suffix}"


def _mg_counter_value_at(el: dict[str, Any], local_frame: int) -> float:
    """The figure on one local frame: cubic ease-out over the first 80%
    of the life, held past it - the template's ``rollProgress``."""
    start, end, _, _, _ = _mg_counter_data(el)
    duration_frames = max(1, int(el.get("durationFrames", 1)))
    progress = min(1.0, max(0.0, local_frame / max(1.0, duration_frames * MG_COUNTER_ROLL_SPAN)))
    return start + (end - start) * (1 - (1 - progress) ** 3)


def _mg_counter_states(el: dict[str, Any]) -> list[tuple[int, int, str]]:
    """``(f0, f1, text)`` figure layers: consecutive frames showing one
    figure grouped, the way the HyperFrames arm groups its ``.clip``
    layers by displayed text."""
    _, _, prefix, suffix, decimals = _mg_counter_data(el)
    duration_frames = max(1, int(el.get("durationFrames", 1)))
    states: list[tuple[int, int, str]] = []
    last_key: str | None = None
    for frame in range(duration_frames):
        text = _mg_counter_format(_mg_counter_value_at(el, frame), prefix, suffix, decimals)
        if text != last_key:
            states.append((frame, duration_frames, text))
            if len(states) > 1:
                states[-2] = (states[-2][0], frame, states[-2][2])
            last_key = text
    return states


def _mg_counter_figure_style(scale: float, ctx: _MGContext) -> dict[str, Any]:
    return {
        "font_size": TYPE_SIZE["display"] * scale * MG_COUNTER_FIGURE_SCALE,
        "font_file": ctx.font_file,
        "font_weight": 900.0,
        "letter_spacing": MG_COUNTER_LETTER_SPACING,
    }


def _mg_roll_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    scale = _mg_scale(el)
    style = _mg_counter_figure_style(scale, ctx)
    widest = 0.0
    for _, _, text in _mg_counter_states(el):
        widest = max(widest, measure_text(text, **style))
    label_w = _mg_counter_label_size(el, scale, ctx)[0]
    return max(widest, label_w)


def _mg_roll_label_lines(el: dict[str, Any], box_width: float, ctx: _MGContext) -> list[tuple]:
    """The label runs under the figure at 0.7 scale, drawn as Runs are."""
    scale = _mg_scale(el) * MG_COUNTER_LABEL_SCALE
    out = []
    for run in el.get("runs", []):
        style = _mg_run_style(run, scale, ctx)
        lines = wrap_text(str(run.get("text", "")), box_width, **style)
        out.append((run, style, lines, len(lines) * MG_LINE_HEIGHT * style["font_size"]))
    return out


def _mg_counter_label_size(el: dict[str, Any], scale: float, ctx: _MGContext) -> tuple[float, float]:
    widest = 0.0
    height = 0.0
    for run in el.get("runs", []):
        style = _mg_run_style(run, scale * MG_COUNTER_LABEL_SCALE, ctx)
        widest = max(widest, block_width(str(run.get("text", "")).split("\n"), **style))
        height += MG_LINE_HEIGHT * style["font_size"]
    return widest, height


def _mg_roll_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    scale = _mg_scale(el)
    height = TYPE_SIZE["display"] * scale * MG_COUNTER_FIGURE_SCALE * 1.0
    if el.get("runs"):
        label_h = sum(h for *_, h in _mg_roll_label_lines(el, box_width, ctx))
        height += round(MG_COUNTER_GAP * scale) + label_h
    return height


def _mg_step_numbers(el: dict[str, Any]) -> tuple[str, str]:
    """The position and total as drawn (``2`` and ``of 5``), defaulting
    the way the template does (1 of 1)."""
    data = el.get("data") or {}
    position = data.get("position")
    total = data.get("total")
    try:
        position_text = str(int(position))
    except (TypeError, ValueError):
        position_text = "1"
    try:
        total_text = str(int(total))
    except (TypeError, ValueError):
        total_text = "1"
    return position_text, f"of {total_text}"


def _mg_step_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    scale = _mg_scale(el)
    position_text, of_text = _mg_step_numbers(el)
    number_w = measure_text(position_text, font_size=TYPE_SIZE["display"] * scale,
                            font_file=ctx.font_file, font_weight=900.0)
    of_w = measure_text(of_text, font_size=TYPE_SIZE["micro"] * scale,
                        font_file=ctx.font_file, font_weight=TYPE_WEIGHT["micro"])
    row_w = number_w + round(MG_STEP_NUMBER_GAP * scale) + of_w
    label_w = _mg_counter_label_size(el, scale, ctx)[0] if el.get("runs") else 0.0
    return max(row_w, label_w)


def _mg_step_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    scale = _mg_scale(el)
    height = TYPE_SIZE["display"] * scale * 1.0
    if el.get("runs"):
        label_h = sum(h for *_, h in _mg_roll_label_lines(el, box_width, ctx))
        height += round(MG_STEP_GAP * scale) + label_h
    return height


def _mg_image_size(src: str) -> tuple[float, float] | None:
    """A project-supplied capture's pixel size, or None when it cannot
    be read. The website panel's shot height is derived from this, so an
    unreadable file draws nothing rather than a guessed box."""
    try:
        from PIL import Image
        with Image.open(src) as im:
            return float(im.width), float(im.height)
    except (OSError, ValueError):
        return None


def _mg_website_address(el: dict[str, Any]) -> str:
    """The address bar copy: the plan's first run, or nothing (no copy
    stated, no bar - the roster refuses a stated one the capture was not
    taken of, so the engine never invents one)."""
    runs = el.get("runs") or []
    if not runs:
        return ""
    return str(runs[0].get("text", ""))


def _mg_website_size(el: dict[str, Any], ctx: _MGContext) -> tuple[float, float]:
    """The panel's drawn size: the safe usable width at the footprint,
    wrapping the address bar (where stated) and the contained capture."""
    scale = _mg_scale(el)
    safe = ctx.safe_area
    usable = max(0.0, ctx.width - float(safe.get("left", 0)) - float(safe.get("right", 0)))
    panel_w = float(round(usable * scale))
    asset = el.get("asset")
    if not asset:
        return 0.0, 0.0
    size = _mg_image_size(asset)
    if size is None:
        return 0.0, 0.0
    src_w, src_h = size
    if src_w <= 0 or src_h <= 0:
        return 0.0, 0.0
    border = round(MG_WEBSITE_BORDER * scale)
    height = 2 * border
    if _mg_website_address(el) != "":
        address_h = TYPE_SIZE["micro"] * scale * MG_STEP_OF_LINE_HEIGHT
        height += 2 * round(MG_WEBSITE_ADDRESS_PAD_V * scale) + address_h + border
    inner_w = max(0.0, panel_w - 2 * border)
    height += inner_w * src_h / src_w
    return panel_w, height


def _mg_anchor_align(el: dict[str, Any]) -> str:
    """The copy alignment inside one element's box, from its anchor's
    horizontal half - the rule the text arms already draw by."""
    _, horizontal = _anchor_parts(str(el.get("anchor", "centre")))
    return {"left": "left", "right": "right"}.get(horizontal, "center")


def _build_mg_stamp_children(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    color: str,
) -> tuple[list[str], list[Node]]:
    """The ``context_stamp`` arm: a coloured pill with centred copy.

    The ink is white with the runs' own shadow. HyperFrames recolors the
    runs to white explicitly; Remotion leaves the runs in the plan's
    colour on the plan's own colour - invisible wherever the two meet -
    so the lowering quotes HyperFrames and the vocabulary's readability
    contract, and the evidence page records the divergence.
    """
    scale = _mg_scale(el)
    children: list[str] = []
    child_nodes: list[Node] = []
    pill_id = f"{element_id}_pill"
    child_nodes.append(Node(
        id=pill_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill=color,
                           corner_radius=float(round(MG_STAMP_RADIUS * scale))),
        bounds=Bounds(x=box.x, y=box.y, width=box.width, height=box.height),
        filters=[DropShadowFilter(kind="drop_shadow", dx=0.0, dy=4.0,
                                  sigma=12.0 * MG_BOX_SHADOW_HALF,
                                  color="rgba(0,0,0,0.4)")],
    ))
    children.append(pill_id)
    align = _mg_anchor_align(el)
    y = box.y + round(MG_STAMP_PAD_V * scale)
    for i, (run, style, lines, run_height) in enumerate(
            _mg_stamp_run_lines(el, box.width, ctx)):
        del run
        run_id = f"{element_id}_run_{i}"
        child_nodes.append(Node(
            id=run_id, type=NodeType.TEXT,
            text=TextContent(
                text="\n".join(lines),
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=style["font_size"], font_weight=style["font_weight"],
                color="#FFFFFF", text_align=align, line_height=MG_LINE_HEIGHT,
                letter_spacing=style["letter_spacing"],
                text_transform=style["text_transform"],
                text_shadow=DEFAULT_TEXT_SHADOW,
            ),
            bounds=Bounds(x=box.x + round(MG_STAMP_PAD_H * scale), y=y,
                           width=box.width - 2 * round(MG_STAMP_PAD_H * scale),
                           height=run_height),
        ))
        children.append(run_id)
        y += run_height
    return children, child_nodes


def _build_mg_bars_children(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    color: str,
    t0: float,
) -> tuple[list[str], list[Node]] | None:
    """The ``comparison_bars`` arm: labelled bars growing to their share.

    Each item arrives on its staged start (the plan's offsets where
    stated, an equal share of the hold where not) with its wrap fading
    in and its fill growing left-to-right over 70% of the reveal window,
    cubic ease-out both. The growth is a scale-about-centre plus the
    exact compensating translate that pins the left edge - two linear
    tweens the renderer draws, rather than a width the renderer does
    not animate. Labels draw as written at the 1.5 pin; no runs or no
    values draws nothing, the arm's null.
    """
    fps = ctx.fps
    scale = _mg_scale(el)
    runs, numbers = _mg_bars_items(el)
    if not runs:
        return None
    max_value = max(numbers + [1.0])
    starts, window, _ = _mg_stage_starts(el, len(runs), fps)
    span_frames = max(1.0, window * 0.7)
    span = span_frames / fps
    cubic = EaseEasing(power=3)
    align = _mg_anchor_align(el)
    track_h = round(MG_BAR_TRACK_HEIGHT * scale)
    radius = float(round(MG_BAR_RADIUS * scale))

    children: list[str] = []
    child_nodes: list[Node] = []
    y = box.y
    for i, run in enumerate(runs):
        style = _mg_bars_label_style(run, scale, ctx)
        lines = wrap_text(str(run.get("text", "")), box.width, **style)
        label_h = len(lines) * MG_BAR_LABEL_LINE_HEIGHT * style["font_size"]
        item_id = f"{element_id}_bar_{i}"
        at = t0 + (starts[i] if i < len(starts) else 0) / fps
        label_id = f"{item_id}_label"
        child_nodes.append(Node(
            id=label_id, type=NodeType.TEXT,
            text=TextContent(
                text="\n".join(lines),
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=style["font_size"], font_weight=style["font_weight"],
                color=color, text_align=align,
                line_height=MG_BAR_LABEL_LINE_HEIGHT,
                text_shadow="0px 2px 8px rgba(0,0,0,0.5)",
            ),
            bounds=Bounds(x=box.x, y=y, width=box.width, height=label_h),
        ))
        track_id = f"{item_id}_track"
        track_y = y + label_h + round(MG_BAR_LABEL_GAP * scale)
        child_nodes.append(Node(
            id=track_id, type=NodeType.SHAPE,
            shape=ShapeContent(shape="rect", fill="rgba(255, 255, 255, 0.1)",
                               corner_radius=radius),
            bounds=Bounds(x=box.x, y=track_y, width=box.width, height=track_h),
        ))
        target_w = max(0.0, numbers[i] / max_value) * box.width
        fill_id = f"{item_id}_fill"
        child_nodes.append(Node(
            id=fill_id, type=NodeType.SHAPE,
            shape=ShapeContent(shape="rect", fill=color, corner_radius=radius),
            bounds=Bounds(x=box.x, y=track_y, width=target_w, height=track_h),
            filters=[DropShadowFilter(kind="drop_shadow", dx=0.0, dy=0.0,
                                      sigma=12.0 * MG_BOX_SHADOW_HALF,
                                      color=f"{color}60")],
            animations=[
                Animation(target=fill_id, property="transform.scale",
                          from_value=[0.0, 1.0], to_value=[1.0, 1.0],
                          start=at, duration=span, easing=cubic),
                Animation(target=fill_id, property="transform.translate",
                          from_value=[-target_w / 2.0, 0.0], to_value=[0.0, 0.0],
                          start=at, duration=span, easing=cubic),
            ],
        ))
        item_h = label_h + round(MG_BAR_LABEL_GAP * scale) + track_h
        child_nodes.append(Node(
            id=item_id, type=NodeType.GROUP,
            bounds=Bounds(x=box.x, y=y, width=box.width, height=item_h),
            animations=[Animation(
                target=item_id, property="opacity",
                from_value=0.0, to_value=1.0,
                start=at, duration=span, easing=cubic,
            )],
            children=[label_id, track_id, fill_id],
        ))
        children.append(item_id)
        y += item_h + round(MG_BAR_GAP * scale)
    return children, child_nodes


def _build_mg_counter_children(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    color: str,
    start_frame: int,
) -> tuple[list[str], list[Node]]:
    """The ``counter_roll`` arm: the figure baked per frame, label under.

    The figure cannot ride a track (text has no numeric channel), so
    each distinct displayed figure becomes a timed layer, the way the
    HyperFrames arm groups its ``.clip`` layers by displayed text and
    the way typewriter reveals group by shown characters. The label is
    the plan's runs at 0.7 scale. ``tabular-nums`` is not in the IR, so
    a rolling figure recentres as its width changes - recorded on the
    evidence page, not approximated.
    """
    fps = ctx.fps
    scale = _mg_scale(el)
    children: list[str] = []
    child_nodes: list[Node] = []
    style = _mg_counter_figure_style(scale, ctx)
    fig_h = style["font_size"] * 1.0
    glow = f"0 0 40px {color}40, 0 4px 16px rgba(0,0,0,0.5)"
    for si, (f0, f1, text) in enumerate(_mg_counter_states(el)):
        text_id = f"{element_id}_fig_{si}"
        child_nodes.append(Node(
            id=text_id, type=NodeType.TEXT,
            text=TextContent(
                text=text,
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=style["font_size"], font_weight=style["font_weight"],
                color=color, text_align="center", line_height=1.0,
                letter_spacing=style["letter_spacing"],
                text_shadow=glow,
            ),
            bounds=Bounds(x=box.x, y=box.y, width=box.width, height=fig_h),
        ))
        layer_id = f"{element_id}_fig_layer_{si}"
        child_nodes.append(Node(
            id=layer_id, type=NodeType.GROUP,
            timing=Timing(start=(start_frame + f0) / fps,
                          duration=(f1 - f0) / fps),
            children=[text_id],
        ))
        children.append(layer_id)
    align = _mg_anchor_align(el)
    y = box.y + fig_h + round(MG_COUNTER_GAP * scale)
    for i, (run, run_style, lines, run_height) in enumerate(
            _mg_roll_label_lines(el, box.width, ctx)):
        del run
        run_id = f"{element_id}_label_{i}"
        child_nodes.append(Node(
            id=run_id, type=NodeType.TEXT,
            text=TextContent(
                text="\n".join(lines),
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=run_style["font_size"],
                font_weight=run_style["font_weight"],
                color=color, text_align=align, line_height=MG_LINE_HEIGHT,
                letter_spacing=run_style["letter_spacing"],
                text_transform=run_style["text_transform"],
                text_shadow=DEFAULT_TEXT_SHADOW,
            ),
            bounds=Bounds(x=box.x, y=y, width=box.width, height=run_height),
        ))
        children.append(run_id)
        y += run_height
    return children, child_nodes


def _build_mg_step_children(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    color: str,
) -> tuple[list[str], list[Node]]:
    """The ``step_counter`` arm: position over total, label under.

    The row is centred in the box (the container's ``alignItems``); the
    number sits on line box 1.0 with its glow, the "of" span on the 1.5
    pin at 60% opacity, bottom-aligned the way the baseline row sits.
    """
    scale = _mg_scale(el)
    children: list[str] = []
    child_nodes: list[Node] = []
    position_text, of_text = _mg_step_numbers(el)
    num_size = TYPE_SIZE["display"] * scale
    of_size = TYPE_SIZE["micro"] * scale
    num_w = measure_text(position_text, font_size=num_size,
                         font_file=ctx.font_file, font_weight=900.0)
    of_w = measure_text(of_text, font_size=of_size,
                        font_file=ctx.font_file,
                        font_weight=TYPE_WEIGHT["micro"])
    num_h = num_size * 1.0
    of_h = of_size * MG_STEP_OF_LINE_HEIGHT
    row_w = num_w + round(MG_STEP_NUMBER_GAP * scale) + of_w
    row_x = box.x + (box.width - row_w) / 2.0
    num_id = f"{element_id}_position"
    child_nodes.append(Node(
        id=num_id, type=NodeType.TEXT,
        text=TextContent(
            text=position_text,
            font_family=ctx.font_family, font_file=ctx.font_file,
            font_size=num_size, font_weight=900.0,
            color=color, text_align="left", line_height=1.0,
            text_shadow=f"0 0 24px {color}40, 0 4px 12px rgba(0,0,0,0.5)",
        ),
        bounds=Bounds(x=row_x, y=box.y, width=num_w, height=num_h),
    ))
    children.append(num_id)
    of_id = f"{element_id}_total"
    of_node = Node(
        id=of_id, type=NodeType.TEXT,
        text=TextContent(
            text=of_text,
            font_family=ctx.font_family, font_file=ctx.font_file,
            font_size=of_size, font_weight=TYPE_WEIGHT["micro"],
            color=color, text_align="left",
            line_height=MG_STEP_OF_LINE_HEIGHT,
            text_shadow="0px 2px 8px rgba(0,0,0,0.4)",
        ),
        bounds=Bounds(x=row_x + num_w + round(MG_STEP_NUMBER_GAP * scale),
                       y=box.y + num_h - of_h, width=of_w, height=of_h),
        opacity=MG_STEP_OF_OPACITY,
    )
    child_nodes.append(of_node)
    children.append(of_id)
    align = _mg_anchor_align(el)
    y = box.y + num_h + round(MG_STEP_GAP * scale)
    for i, (run, run_style, lines, run_height) in enumerate(
            _mg_roll_label_lines(el, box.width, ctx)):
        del run
        run_id = f"{element_id}_label_{i}"
        child_nodes.append(Node(
            id=run_id, type=NodeType.TEXT,
            text=TextContent(
                text="\n".join(lines),
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=run_style["font_size"],
                font_weight=run_style["font_weight"],
                color=color, text_align=align, line_height=MG_LINE_HEIGHT,
                letter_spacing=run_style["letter_spacing"],
                text_transform=run_style["text_transform"],
                text_shadow=DEFAULT_TEXT_SHADOW,
            ),
            bounds=Bounds(x=box.x, y=y, width=box.width, height=run_height),
        ))
        children.append(run_id)
        y += run_height
    return children, child_nodes


def _build_mg_pointer_children(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    color: str,
) -> tuple[list[str], list[Node]]:
    """The ``pointer_annotation`` arm: a stroked ring where the speech
    points. The template's 80px is the border box (Tailwind preflight),
    so the SVG stroke - which centres on its path where the CSS border
    sits outside the content - draws on the 64px content circle: the
    ellipse insets half the stroke inside the outer edge and both outer
    edges agree. The glow spills past the layout box the way every text
    shadow does (``MG_PAD`` carries it); only the outer glow is drawn,
    the inset twin has no IR spelling."""
    del ctx
    scale = _mg_scale(el)
    stroke = float(round(MG_POINTER_BORDER * scale))
    outer = MG_POINTER_SIZE * scale
    ring_id = f"{element_id}_ring"
    node = Node(
        id=ring_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="circle", fill=None,
                           stroke=color, stroke_width=stroke),
        bounds=Bounds(x=box.x + stroke / 2.0,
                       y=box.y + stroke / 2.0,
                       width=outer - stroke, height=outer - stroke),
        filters=[DropShadowFilter(kind="drop_shadow", dx=0.0, dy=0.0,
                                  sigma=MG_POINTER_GLOW * MG_BOX_SHADOW_HALF,
                                  color=color)],
    )
    return [ring_id], [node]


def _build_mg_website_children(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    color: str,
) -> tuple[list[str], list[Node]] | None:
    """The ``website_panel`` arm: bordered chrome around the capture.

    The address bar carries the plan's first run where stated; the shot
    fills the panel width and holds its file's aspect (``object-fit:
    contain`` in a tight box). No asset, or one that cannot be read,
    draws nothing - the arm's null, and ``resolve_plan`` already refuses
    the missing file upstream. Square capture corners over the rounded
    panel is a known fringe, recorded on the evidence page.
    """
    scale = _mg_scale(el)
    asset = el.get("asset")
    if not asset:
        return None
    size = _mg_image_size(asset)
    if size is None:
        return None
    src_w, src_h = size
    if src_w <= 0 or src_h <= 0:
        return None
    children: list[str] = []
    child_nodes: list[Node] = []
    border = round(MG_WEBSITE_BORDER * scale)
    radius = float(round(MG_WEBSITE_RADIUS * scale))
    panel_id = f"{element_id}_panel"
    child_nodes.append(Node(
        id=panel_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill=MG_WEBSITE_BACKGROUND,
                           stroke=color, stroke_width=float(border),
                           corner_radius=radius),
        bounds=Bounds(x=box.x, y=box.y, width=box.width, height=box.height),
        filters=[DropShadowFilter(kind="drop_shadow", dx=0.0, dy=8.0,
                                  sigma=32.0 * MG_BOX_SHADOW_HALF,
                                  color="rgba(0,0,0,0.5)")],
    ))
    children.append(panel_id)
    shot_y = box.y + border
    address = _mg_website_address(el)
    if address != "":
        addr_size = TYPE_SIZE["micro"] * scale
        addr_h = addr_size * MG_STEP_OF_LINE_HEIGHT
        addr_id = f"{element_id}_address"
        child_nodes.append(Node(
            id=addr_id, type=NodeType.TEXT,
            text=TextContent(
                text=address,
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=addr_size, font_weight=TYPE_WEIGHT["micro"],
                color="#FFFFFF", text_align="left",
                line_height=MG_STEP_OF_LINE_HEIGHT,
            ),
            bounds=Bounds(x=box.x + border + round(MG_WEBSITE_ADDRESS_PAD_H * scale),
                           y=box.y + border + round(MG_WEBSITE_ADDRESS_PAD_V * scale),
                           width=(box.width - 2 * border
                                  - 2 * round(MG_WEBSITE_ADDRESS_PAD_H * scale)),
                           height=addr_h),
        ))
        children.append(addr_id)
        rule_id = f"{element_id}_rule"
        rule_y = (box.y + border + round(MG_WEBSITE_ADDRESS_PAD_V * scale)
                  + addr_h + round(MG_WEBSITE_ADDRESS_PAD_V * scale))
        child_nodes.append(Node(
            id=rule_id, type=NodeType.SHAPE,
            shape=ShapeContent(shape="rect", fill=color),
            bounds=Bounds(x=box.x + border, y=rule_y,
                           width=box.width - 2 * border, height=border),
        ))
        children.append(rule_id)
        shot_y = rule_y + border
    inner_w = box.width - 2 * border
    shot_h = inner_w * src_h / src_w
    shot_id = f"{element_id}_shot"
    child_nodes.append(Node(
        id=shot_id, type=NodeType.IMAGE, image=ImageContent(src=asset),
        bounds=Bounds(x=box.x + border, y=shot_y, width=inner_w, height=shot_h),
    ))
    children.append(shot_id)
    return children, child_nodes


def _stack_boxes(live: list[dict[str, Any]], anchor: str, ctx: _MGContext) -> dict[int, Bounds]:
    """Each live element's box inside its anchor's stack."""
    vertical, horizontal = _anchor_parts(anchor)
    left = float(ctx.safe_area.get("left", 0))
    right = float(ctx.safe_area.get("right", 0))
    available = max(0.0, ctx.width - left - right)
    if horizontal == "centre":
        stack_width = min(available, float(ctx.layout_width)) if ctx.layout_width is not None else available
        stack_x = left + (available - stack_width) / 2.0
    else:
        stack_width = min(available, max((_mg_natural_width(el, ctx) for el in live), default=0.0))
        stack_x = left if horizontal == "left" else ctx.width - right - stack_width

    heights = [_mg_height(el, stack_width, ctx) for el in live]
    total = sum(heights) + MG_STACK_GAP * max(0, len(heights) - 1)
    boxes: dict[int, Bounds] = {}
    if vertical == "bottom":
        bottom = ctx.height - float(ctx.safe_area.get("bottom", 0))
        for el, h in zip(live, heights):
            boxes[id(el)] = Bounds(x=stack_x, y=bottom - h, width=stack_width, height=h)
            bottom -= h + MG_STACK_GAP
        return boxes
    y = float(ctx.safe_area.get("top", 0)) if vertical == "top" else (ctx.height - total) / 2.0
    for el, h in zip(live, heights):
        boxes[id(el)] = Bounds(x=stack_x, y=y, width=stack_width, height=h)
        y += h + MG_STACK_GAP
    return boxes


def _mg_chrome_opacity(element_id: str, entrance: str, exit_char: str,
                       t0: float, tt: float, fps: float) -> list[Animation]:
    """The opacity ramp chrome carries: the entrance/exit ramps, linearly.

    Both the bar and the brackets fade with the element's own ramps and
    read no transform character - the arms return no `entTransform` /
    `extTransform`, so there is nothing to lower beyond this.
    """
    anims: list[Animation] = []
    in_frames = RAMP_FRAMES.get(entrance, 0)
    out_frames = RAMP_FRAMES.get(exit_char, 0)
    if in_frames > 0:
        anims.append(Animation(
            target=element_id, property="opacity",
            from_value=0.0, to_value=1.0,
            start=t0, duration=in_frames / fps, easing=LinearEasing(),
        ))
    if out_frames > 0:
        anims.append(Animation(
            target=element_id, property="opacity",
            from_value=1.0, to_value=0.0,
            start=max(t0, t0 + tt - out_frames / fps),
            duration=out_frames / fps, easing=LinearEasing(),
        ))
    return anims


def _build_mg_bar(
    el: dict[str, Any],
    ctx: _MGContext,
    element_id: str,
    t0: float,
    tt: float,
) -> tuple[Node, list[Node]]:
    """The `progress_bar` arm: a track and a fill lerping start to end.

    The fill is drawn at the track's full width and shown through a
    scale-plus-translate pair: `scaleX` carries the lerped fraction and
    the translate re-pins the left edge the centre-origin scale would
    otherwise drag. Both are linear in the fraction, and the fraction is
    linear in time, so two linear animations draw exactly the width lerp
    the template tweens - a property the renderer never has to grow. The
    glow sits on the wrap, outside the scaled rect, the way the
    composition's `box-shadow` sits outside the fill div.
    """
    fps = ctx.fps
    color = el.get("color", "#FFFFFF")
    duration_frames = max(1, int(el.get("durationFrames", 1)))
    left, y, track_w, bar_h = _mg_bar_geometry(el, ctx)
    start_pct = _mg_number(el.get("timelineProgressStart")) * 100.0
    end_pct = _mg_number(el.get("timelineProgressEnd")) * 100.0
    span = max(1.0 / fps, (duration_frames - 1) / fps)

    track_id = f"{element_id}_track"
    track = Node(
        id=track_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill=MG_BAR_TRACK),
        bounds=Bounds(x=left, y=y, width=track_w, height=bar_h),
    )

    shown0, shown1 = track_w * start_pct / 100.0, track_w * end_pct / 100.0
    fill_id = f"{element_id}_fill"
    fill = Node(
        id=fill_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill=color),
        bounds=Bounds(x=left, y=y, width=track_w, height=bar_h),
        animations=[
            Animation(
                target=fill_id, property="transform.scale",
                from_value=[start_pct / 100.0, 1.0], to_value=[end_pct / 100.0, 1.0],
                start=t0, duration=span, easing=LinearEasing(),
            ),
            Animation(
                target=fill_id, property="transform.translate",
                from_value=[-(track_w - shown0) / 2.0, 0.0],
                to_value=[-(track_w - shown1) / 2.0, 0.0],
                start=t0, duration=span, easing=LinearEasing(),
            ),
        ],
    )
    wrap_id = f"{element_id}_fill_wrap"
    wrap = Node(
        id=wrap_id, type=NodeType.GROUP,
        filters=[DropShadowFilter(kind="drop_shadow", dx=0.0, dy=0.0,
                                  sigma=MG_BAR_GLOW / 2.0, color=color)],
        children=[fill_id],
    )

    group = Node(
        id=element_id, type=NodeType.GROUP,
        timing=Timing(start=t0, duration=tt),
        animations=_mg_chrome_opacity(element_id, el.get("entrance", "cut"),
                                      el.get("exit", "cut"), t0, tt, fps),
        children=[track_id, wrap_id],
    )
    return group, [track, fill, wrap]


def _build_mg_accents(
    el: dict[str, Any],
    ctx: _MGContext,
    element_id: str,
    t0: float,
    tt: float,
) -> tuple[Node, list[Node]]:
    """The `frame_accents` arm: four corner brackets as eight filled bars.

    A bracket is two borders of a square div; the lowering draws each
    border as the filled bar it is - a horizontal `size` by `stroke` bar
    and a vertical `stroke` by `size` bar - at the safe inset the corner
    names. No glow: the template draws none.
    """
    fps = ctx.fps
    scale = _mg_scale(el)
    color = el.get("color", "#FFFFFF")
    size = float(_mg_px(MG_ACCENT_SIZE, scale))
    stroke = float(_mg_px(MG_ACCENT_STROKE, scale))
    left = float(ctx.safe_area.get("left", 0))
    right = float(ctx.safe_area.get("right", 0))
    top = float(ctx.safe_area.get("top", 0))
    bottom = float(ctx.safe_area.get("bottom", 0))

    bars = [
        (left, top, size, stroke),
        (left, top, stroke, size),
        (ctx.width - right - size, top, size, stroke),
        (ctx.width - right - stroke, top, stroke, size),
        (left, ctx.height - bottom - stroke, size, stroke),
        (left, ctx.height - bottom - size, stroke, size),
        (ctx.width - right - size, ctx.height - bottom - stroke, size, stroke),
        (ctx.width - right - stroke, ctx.height - bottom - size, stroke, size),
    ]
    children: list[str] = []
    child_nodes: list[Node] = []
    for i, (x, y, width, height) in enumerate(bars):
        bar_id = f"{element_id}_bar_{i}"
        child_nodes.append(Node(
            id=bar_id, type=NodeType.SHAPE,
            shape=ShapeContent(shape="rect", fill=color),
            bounds=Bounds(x=x, y=y, width=width, height=height),
        ))
        children.append(bar_id)

    group = Node(
        id=element_id, type=NodeType.GROUP,
        timing=Timing(start=t0, duration=tt),
        animations=_mg_chrome_opacity(element_id, el.get("entrance", "cut"),
                                      el.get("exit", "cut"), t0, tt, fps),
        children=children,
    )
    return group, child_nodes


def _build_mg_digit(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    t0: float,
    tt: float,
    animations: list[Animation],
) -> tuple[Node, list[Node]] | None:
    """The `digit_counter` arm: an odometer figure over the label runs.

    The figure row is centred in the element box: each digit rolls in
    its own cell on a ten-high strip whose rail carries one IR
    `SpringEasing` animation with `DigitRoll`'s own config (see
    `_mg_digit_spring`); statics and affixes draw beside the cells.
    The label runs sit underneath at the counter's 0.7 scale with the
    element's anchor alignment, the way the text arms align theirs.
    The outer group keeps the standard opacity-plus-character
    animations: unlike the chrome arms, this one spreads both
    transforms.
    """
    fps = ctx.fps
    scale = _mg_scale(el)
    color = el.get("color", "#FFFFFF")
    duration_frames = max(1, int(el.get("durationFrames", 1)))
    font_size, digit_height, _ = _mg_counter_figure(el, ctx)
    glow = MG_COUNTER_GLOW.format(color=color)

    figure_width = sum(width for _, _, width in _mg_counter_chars(el, ctx))
    x = box.x + (box.width - figure_width) / 2.0
    y = box.y

    children: list[str] = []
    child_nodes: list[Node] = []
    chars = _mg_counter_chars(el, ctx)
    # The stagger counts back over the value's own characters, the way
    # `DigitRoll` maps `value.split("")` - the prefix sits before the
    # roll and the suffix after it, and neither may shift the count.
    _, _, display_value = _mg_counter_figure(el, ctx)
    total = len(display_value)
    value_pos = 0
    for index, (kind, payload, width) in enumerate(chars):
        if kind == "digit":
            cell_id = f"{element_id}_cell_{index}"
            rail_id = f"{element_id}_rail_{index}"
            reverse = total - 1 - value_pos
            stagger = math.floor(reverse * fps * MG_DIGIT_STAGGER + 0.5)
            span = max(1, duration_frames - stagger)
            rail_anims = [
                Animation(
                    target=rail_id, property="transform.translate",
                    from_value=[0.0, 0.0],
                    to_value=[0.0, -payload * digit_height],
                    start=t0 + stagger / fps, duration=span / fps,
                    easing=_mg_digit_spring(),
                )
            ]
            for digit in range(10):
                digit_id = f"{rail_id}_{digit}"
                child_nodes.append(Node(
                    id=digit_id, type=NodeType.TEXT,
                    text=TextContent(
                        text=str(digit),
                        font_family=ctx.font_family, font_file=ctx.font_file,
                        font_size=font_size, font_weight=900.0,
                        color=color, text_align="center",
                        line_height=MG_COUNTER_LINE_HEIGHT,
                        letter_spacing=MG_COUNTER_TRACKING,
                        text_shadow=glow,
                    ),
                    bounds=Bounds(x=x, y=y + digit * digit_height,
                                   width=width, height=digit_height),
                ))
            child_nodes.append(Node(
                id=rail_id, type=NodeType.GROUP,
                bounds=Bounds(x=x, y=y, width=width, height=10 * digit_height),
                animations=rail_anims,
                children=[f"{rail_id}_{digit}" for digit in range(10)],
            ))
            child_nodes.append(Node(
                id=cell_id, type=NodeType.GROUP,
                bounds=Bounds(x=x, y=y, width=width, height=digit_height),
                clip="inset(0 0 0 0)",
                children=[rail_id],
            ))
            children.append(cell_id)
        else:
            static_id = f"{element_id}_static_{index}"
            child_nodes.append(Node(
                id=static_id, type=NodeType.TEXT,
                text=TextContent(
                    text=str(payload),
                    font_family=ctx.font_family, font_file=ctx.font_file,
                    font_size=font_size, font_weight=900.0,
                    color=color, text_align="center",
                    # An affix span sets its own lineHeight: 1.0 in the
                    # composition; a static char inherits the roll's 1.2.
                    line_height=1.0 if kind == "affix" else MG_COUNTER_LINE_HEIGHT,
                    letter_spacing=MG_COUNTER_TRACKING,
                    text_shadow=glow,
                ),
                bounds=Bounds(x=x, y=y, width=width, height=digit_height),
            ))
            children.append(static_id)
        if kind in ("digit", "static"):
            value_pos += 1
        x += width

    _, horizontal = _anchor_parts(str(el.get("anchor", "centre")))
    align = {"left": "left", "right": "right"}.get(horizontal, "center")
    label_y = y + digit_height + round(MG_COUNTER_GAP * scale)
    for i, (run, style, lines, run_height) in enumerate(
            _mg_counter_label_lines(el, box.width, ctx)):
        run_id = f"{element_id}_label_{i}"
        child_nodes.append(Node(
            id=run_id, type=NodeType.TEXT,
            text=TextContent(
                text="\n".join(lines),
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=style["font_size"], font_weight=style["font_weight"],
                color=color, text_align=align, line_height=MG_LINE_HEIGHT,
                letter_spacing=style["letter_spacing"],
                text_transform=style["text_transform"],
                text_shadow=DEFAULT_TEXT_SHADOW,
            ),
            bounds=Bounds(x=box.x, y=label_y, width=box.width, height=run_height),
        ))
        children.append(run_id)
        label_y += run_height

    group = Node(
        id=element_id, type=NodeType.GROUP,
        bounds=box,
        timing=Timing(start=t0, duration=tt),
        animations=animations,
        children=children,
    )
    return group, child_nodes


def _typewriter_cursor_on(progress: float) -> bool:
    """Whether the reveal caret draws, quoting `typewriterCursorOn`."""
    return math.floor(progress * 30) % 2 == 0


def _build_mg_emblem_children(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    color: str,
) -> tuple[list[str], list[Node]] | None:
    """The `subject_emblem` arm's nodes: plate, mark, label.

    Returns the group children and child nodes, or None when the arm has
    no mark to draw. The label may be absent; the plate is always drawn.
    """
    fps = ctx.fps
    scale = _mg_scale(el)
    children: list[str] = []
    child_nodes: list[Node] = []

    plate = MG_EMBLEM_PLATE * scale
    plate_x = box.x + (box.width - plate) / 2.0
    plate_y = box.y
    shadow_dx, shadow_dy, shadow_sigma, shadow_color = MG_EMBLEM_PLATE_SHADOW
    plate_id = f"{element_id}_plate"
    child_nodes.append(Node(
        id=plate_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill=color,
                           corner_radius=float(_mg_px(MG_EMBLEM_RADIUS, scale))),
        bounds=Bounds(x=plate_x, y=plate_y, width=plate, height=plate),
        filters=[DropShadowFilter(kind="drop_shadow", dx=shadow_dx, dy=shadow_dy,
                                  sigma=shadow_sigma, color=shadow_color)],
    ))
    children.append(plate_id)

    mark, _ = _mg_emblem_runs(el)
    mark_text = str(mark.get("text", "")) if mark else ""
    mark_size = MG_EMBLEM_MARK_SIZE * scale
    entrance = el.get("entrance", "cut")
    in_frames = RAMP_FRAMES.get(entrance, 0)
    duration_frames = int(el.get("durationFrames", 1))

    def mark_node(node_id: str, text: str) -> Node:
        lines = text.split("\n")
        height = len(lines) * mark_size
        return Node(
            id=node_id, type=NodeType.TEXT,
            text=TextContent(
                text=text,
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=mark_size, font_weight=MG_EMBLEM_MARK_WEIGHT,
                color="#FFFFFF", text_align="center", line_height=1.0,
                text_shadow=MG_EMBLEM_MARK_SHADOW,
            ),
            bounds=Bounds(x=box.x, y=plate_y + (plate - height) / 2.0,
                           width=box.width, height=height),
        )

    if mark_text and entrance == "typewriter" and in_frames > 0:
        states = _typewriter_states([len(mark_text)], len(mark_text),
                                    in_frames, duration_frames, None)
        for si, st in enumerate(states):
            text = mark_text[: st["shown"][0]]
            if st["cursor_run"] == 0 and _typewriter_cursor_on(st["prog"]):
                text += "|"
            if not text:
                continue
            node = mark_node(f"{element_id}_mark_{si}", text)
            child_nodes.append(node)
            f1 = st["f1"] if st["f1"] is not None else duration_frames
            start_frame = int(el.get("startFrame", 0))
            child_nodes.append(Node(
                id=f"{element_id}_mark_layer_{si}", type=NodeType.GROUP,
                timing=Timing(start=(start_frame + st["f0"]) / fps,
                              duration=(f1 - st["f0"]) / fps),
                children=[node.id],
            ))
            children.append(f"{element_id}_mark_layer_{si}")
    elif mark_text:
        node = mark_node(f"{element_id}_mark", mark_text)
        child_nodes.append(node)
        children.append(node.id)

    _, horizontal = _anchor_parts(str(el.get("anchor", "centre")))
    align = {"left": "left", "right": "right"}.get(horizontal, "center")
    y = plate_y + plate + MG_EMBLEM_GAP * scale
    for i, (run, style, lines) in enumerate(_mg_emblem_label_lines(el, box.width, ctx)):
        run_height = len(lines) * MG_LINE_HEIGHT * style["font_size"]
        run_id = f"{element_id}_label_{i}"
        child_nodes.append(Node(
            id=run_id, type=NodeType.TEXT,
            text=TextContent(
                text="\n".join(lines),
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=style["font_size"], font_weight=style["font_weight"],
                color=color, text_align=align, line_height=MG_LINE_HEIGHT,
                letter_spacing=style["letter_spacing"],
                text_transform=style["text_transform"],
                text_shadow=DEFAULT_TEXT_SHADOW,
            ),
            bounds=Bounds(x=box.x, y=y, width=box.width, height=run_height),
        ))
        children.append(run_id)
        y += run_height
    return children, child_nodes


def _mg_review_star_nodes(
    element_id: str,
    index: int,
    x: float,
    y: float,
    size: float,
    value: float,
    palette: dict,
) -> tuple[list[str], list[Node]]:
    """One drawn star: a muted path, with the filled fraction clipped over it."""
    children: list[str] = []
    child_nodes: list[Node] = []
    muted_id = f"{element_id}_star_{index}"
    child_nodes.append(Node(
        id=muted_id, type=NodeType.SVG,
        svg=SVGContent(viewBox=MG_REVIEW_STAR_VIEWBOX, path=MG_REVIEW_STAR_PATH,
                       fill=palette["rule"]),
        bounds=Bounds(x=x, y=y, width=size, height=size),
    ))
    children.append(muted_id)
    filled = max(0.0, min(1.0, value - index))
    if filled > 0:
        fill_id = f"{element_id}_star_{index}_fill"
        node = Node(
            id=fill_id, type=NodeType.SVG,
            svg=SVGContent(viewBox=MG_REVIEW_STAR_VIEWBOX, path=MG_REVIEW_STAR_PATH,
                           fill=palette["star"]),
            bounds=Bounds(x=x, y=y, width=size, height=size),
        )
        if filled < 1:
            node.clip = f"inset(0 {(1.0 - filled) * 100.0}% 0 0)"
        child_nodes.append(node)
        children.append(fill_id)
    return children, child_nodes


def _build_mg_review_children(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
) -> tuple[list[str], list[Node]] | None:
    """The `review_panel` arm's nodes: card, header, one block per row.

    Returns the group children and child nodes, or None when the panel
    has no rows or no palette - the arm the composition draws nothing for.
    """
    panel = MG_REVIEW_PANEL
    scale = _mg_scale(el)
    rows = _mg_review_rows(el)
    palette = _mg_review_palette(el)
    if not rows or palette is None:
        return None

    def px(value: float) -> int:
        return _mg_px(value, scale)

    def text_node(node_id: str, text: str, size: float, weight: float,
                  color: str, width: float, height: float, x: float, y: float,
                  line_height: float | None, align: str = "left") -> Node:
        return Node(
            id=node_id, type=NodeType.TEXT,
            text=TextContent(
                text=text.replace("\n", " "),
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=size, font_weight=weight, color=color,
                text_align=align, line_height=line_height,
            ),
            bounds=Bounds(x=x, y=y, width=width, height=height),
        )

    children: list[str] = []
    child_nodes: list[Node] = []
    card_w, card_h = _mg_review_card_size(el, ctx)
    card_x, card_y = box.x, box.y
    surface_id = f"{element_id}_surface"
    child_nodes.append(Node(
        id=surface_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill=palette["surface"],
                           corner_radius=float(px(panel["radius"]))),
        bounds=Bounds(x=card_x, y=card_y, width=card_w, height=card_h),
        filters=[DropShadowFilter(kind="drop_shadow", dx=0.0, dy=10.0,
                                  sigma=15.0, color="rgba(0,0,0,0.45)")],
    ))
    children.append(surface_id)

    inner_x = card_x + px(panel["pad"])
    inner_w = card_w - 2 * px(panel["pad"])

    place = next((r.get("text", "") for r in (el.get("runs") or [])
                  if r.get("type_role") == "display"), "")
    name_size = float(px(panel["nameSize"]))
    name_h = float(px(panel["nameSize"] * panel["nameLine"]))
    name_id = f"{element_id}_name"
    child_nodes.append(text_node(
        name_id, str(place), name_size, float(panel["nameWeight"]), palette["ink"],
        inner_w, name_h, inner_x, card_y + px(panel["pad"]),
        name_h / name_size if name_size > 0 else None))
    children.append(name_id)

    score_y = card_y + px(panel["pad"]) + name_h + px(panel["nameToScore"])
    score_h = float(max(px(panel["scoreSize"] * panel["scoreLine"]),
                        px(panel["headerStar"])))
    data = el.get("data") or {}
    score_size = float(px(panel["scoreSize"]))
    score_lb = line_box_height(score_size, None, ctx.font_file)
    score_text = str(data.get("rating", ""))
    score_w = measure_text(score_text, font_size=score_size, font_file=ctx.font_file,
                           font_weight=float(panel["scoreWeight"]))
    score_id = f"{element_id}_score"
    child_nodes.append(text_node(
        score_id, score_text, score_size, float(panel["scoreWeight"]), palette["ink"],
        score_w, score_lb, inner_x, score_y + (score_h - score_lb) / 2.0, None))
    children.append(score_id)

    star_size = float(px(panel["headerStar"]))
    star_y = score_y + (score_h - star_size) / 2.0
    try:
        rating_value = float(data.get("rating", 0))
    except (TypeError, ValueError):
        rating_value = 0.0
    star_x = inner_x + score_w + px(panel["scoreGap"])
    for i in range(5):
        ids, nodes = _mg_review_star_nodes(
            f"{element_id}_header", i, star_x + i * star_size, star_y,
            star_size, rating_value, palette)
        child_nodes.extend(nodes)
        children.extend(ids)

    label_runs = [str(r.get("text", "")) for r in (el.get("runs") or [])
                  if r.get("type_role") != "display"]
    label_text = " ".join([str(p) for p in [data.get("count", ""), *label_runs]
                           if str(p) != ""])
    label_size = float(px(panel["labelSize"]))
    label_lb = line_box_height(label_size, None, ctx.font_file)
    label_x = star_x + 5 * star_size + px(panel["scoreGap"])
    label_id = f"{element_id}_label"
    child_nodes.append(text_node(
        label_id, label_text, label_size, float(panel["labelWeight"]), palette["muted"],
        max(0.0, inner_x + inner_w - label_x), label_lb, label_x,
        score_y + (score_h - label_lb) / 2.0, None))
    children.append(label_id)

    row_y = score_y + score_h + px(panel["scoreToRows"])
    body_lh = float(px(panel["bodySize"] * panel["bodyLine"]))
    body_size = float(px(panel["bodySize"]))
    author_h = float(px(panel["authorSize"] * panel["authorLine"]))
    avatar = float(px(panel["avatar"]))
    avatar_gap = float(px(panel["avatarGap"]))
    content_w = inner_w - avatar - avatar_gap
    content_x = inner_x + avatar + avatar_gap
    for ri, row in enumerate(rows):
        row_h = float(2 * px(panel["rowPadY"]) + max(
            px(panel["avatar"]),
            px(panel["authorSize"] * panel["authorLine"])
            + px(panel["authorToBody"])
            + panel["bodyLines"] * body_lh))
        rule_id = f"{element_id}_row_{ri}_rule"
        child_nodes.append(Node(
            id=rule_id, type=NodeType.SHAPE,
            shape=ShapeContent(shape="rect", fill=palette["rule"]),
            bounds=Bounds(x=inner_x, y=row_y, width=inner_w, height=float(px(panel["rule"]))),
        ))
        children.append(rule_id)
        # Border-box: the rule occupies the row's first pixels, then the
        # top padding, before the content starts.
        top = row_y + px(panel["rule"]) + px(panel["rowPadY"])

        avatar_id = f"{element_id}_row_{ri}_avatar"
        child_nodes.append(Node(
            id=avatar_id, type=NodeType.SHAPE,
            shape=ShapeContent(shape="circle", fill=palette["muted"]),
            bounds=Bounds(x=inner_x, y=top, width=avatar, height=avatar),
        ))
        children.append(avatar_id)
        letter = str(row.get("initial") or row.get("author") or "").strip()[:1]
        if letter:
            letter_size = float(px(panel["avatarLetter"]))
            letter_lb = line_box_height(letter_size, None, ctx.font_file)
            letter_id = f"{element_id}_row_{ri}_initial"
            child_nodes.append(text_node(
                letter_id, letter, letter_size, float(panel["authorWeight"]),
                palette["surface"], avatar, letter_lb,
                inner_x, top + (avatar - letter_lb) / 2.0, None, align="center"))
            children.append(letter_id)

        ax = content_x
        author_size = float(px(panel["authorSize"]))
        author_lb = line_box_height(author_size, None, ctx.font_file)
        author_text = str(row.get("author", ""))
        author_w = measure_text(author_text, font_size=author_size,
                                font_file=ctx.font_file,
                                font_weight=float(panel["authorWeight"]))
        author_id = f"{element_id}_row_{ri}_author"
        child_nodes.append(text_node(
            author_id, author_text, author_size, float(panel["authorWeight"]),
            palette["ink"], author_w, author_lb,
            ax, top + (author_h - author_lb) / 2.0, None))
        children.append(author_id)
        ax += author_w + px(panel["scoreGap"])
        stars = row.get("stars")
        if isinstance(stars, bool):
            stars = None
        if isinstance(stars, (int, float)):
            row_star = float(px(panel["rowStar"]))
            for i in range(5):
                ids, nodes = _mg_review_star_nodes(
                    f"{element_id}_row_{ri}", i, ax + i * row_star,
                    top + (author_h - row_star) / 2.0, row_star,
                    float(stars), palette)
                child_nodes.extend(nodes)
                children.extend(ids)
            ax += 5 * row_star + px(panel["scoreGap"])
        when = row.get("when")
        if when:
            when_size = float(px(panel["whenSize"]))
            when_lb = line_box_height(when_size, None, ctx.font_file)
            when_w = measure_text(str(when), font_size=when_size,
                                  font_file=ctx.font_file,
                                  font_weight=float(panel["whenWeight"]))
            when_id = f"{element_id}_row_{ri}_when"
            child_nodes.append(text_node(
                when_id, str(when), when_size, float(panel["whenWeight"]),
                palette["muted"], when_w, when_lb,
                ax, top + (author_h - when_lb) / 2.0, None))
            children.append(when_id)

        body_top = top + author_h + px(panel["authorToBody"])
        body_lines = wrap_text(str(row.get("body", "")), content_w,
                               font_size=body_size, font_file=ctx.font_file,
                               font_weight=float(panel["bodyWeight"]))[: panel["bodyLines"]]
        body_id = f"{element_id}_row_{ri}_body"
        child_nodes.append(Node(
            id=body_id, type=NodeType.TEXT,
            text=TextContent(
                text="\n".join(body_lines),
                font_family=ctx.font_family, font_file=ctx.font_file,
                font_size=body_size, font_weight=float(panel["bodyWeight"]),
                color=palette["ink"], text_align="left",
                line_height=body_lh / body_size if body_size > 0 else None,
            ),
            bounds=Bounds(x=content_x, y=body_top, width=content_w,
                           height=panel["bodyLines"] * body_lh),
        ))
        children.append(body_id)
        row_y += row_h
    return children, child_nodes


def _build_mg_beat(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds,
    element_id: str,
    t0: float,
    tt: float,
) -> tuple[Node, list[Node]]:
    """The `beat_accent` arm: a growing, fading disc on baked per-frame tracks.

    The arm defines its geometry per frame - diameter
    `round(120 * scale * (0.5 + localFrame / durationFrames))` at the
    stack's flow position, opacity the element ramp times
    `(1 - localFrame / durationFrames)` - and reads neither the entrance
    nor the exit transform. The static bounds hold the full-grown disc;
    a linear scale animation grows it and a linear translate animation
    re-pins the corner the stack flow holds (both exact up to the arm's
    own per-frame rounding); the opacity product is baked as one
    single-frame animation per frame, because overlapping animations on
    one property do not multiply - the later starter wins.
    """
    fps = ctx.fps
    scale = _mg_scale(el)
    color = el.get("color", "#FFFFFF")
    duration_frames = max(1, int(el.get("durationFrames", 1)))
    entrance = el.get("entrance", "cut")
    exit_char = el.get("exit", "cut")
    in_frames = RAMP_FRAMES.get(entrance, 0)
    out_frames = RAMP_FRAMES.get(exit_char, 0)

    full = MG_BEAT_SIZE * scale * MG_BEAT_GROWTH[1]
    sizes = [_mg_px(MG_BEAT_SIZE * (MG_BEAT_GROWTH[0] + f / duration_frames), scale)
             for f in range(duration_frames)]

    vertical, horizontal = _anchor_parts(str(el.get("anchor", "centre")))
    right = float(ctx.safe_area.get("right", 0))

    def corner(size: float) -> tuple[float, float]:
        if horizontal == "right":
            left = ctx.width - right - size
        else:
            left = box.x
        if vertical == "top":
            top = box.y
        elif vertical == "bottom":
            top = box.y + box.height - size
        else:
            top = (box.y + box.height / 2.0) - size / 2.0
        return left, top

    def shift(size: float) -> list[float]:
        left, top = corner(size)
        return [left - (box.x + (full - size) / 2.0),
                top - (box.y + (full - size) / 2.0)]

    disc_id = f"{element_id}_disc"
    first, last = shift(float(sizes[0])), shift(float(sizes[-1]))
    animations: list[Animation] = [
        Animation(target=disc_id, property="transform.scale",
                  from_value=[sizes[0] / full, sizes[0] / full],
                  to_value=[sizes[-1] / full, sizes[-1] / full],
                  start=t0, duration=tt, easing=LinearEasing()),
        Animation(target=disc_id, property="transform.translate",
                  from_value=first, to_value=last,
                  start=t0, duration=tt, easing=LinearEasing()),
    ]

    def opacity_at(frame: int) -> float:
        ramp_in = min(1.0, frame / in_frames) if in_frames > 0 else 1.0
        ramp_out = min(1.0, max(0.0, (duration_frames - frame) / out_frames)) \
            if out_frames > 0 else 1.0
        return ramp_in * ramp_out * (1.0 - frame / duration_frames)

    for f in range(duration_frames):
        animations.append(Animation(
            target=disc_id, property="opacity",
            from_value=opacity_at(f), to_value=opacity_at(f + 1),
            start=t0 + f / fps, duration=1.0 / fps, easing=LinearEasing()))

    disc = Node(
        id=disc_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="circle", fill=color),
        bounds=Bounds(x=box.x, y=box.y, width=full, height=full),
        filters=[DropShadowFilter(kind="drop_shadow", dx=0.0, dy=0.0,
                                  sigma=12.0, color=color)],
        animations=animations,
    )
    group = Node(
        id=element_id, type=NodeType.GROUP, bounds=box,
        timing=Timing(start=t0, duration=tt), children=[disc_id],
    )
    return group, [disc]


def _build_mg_element(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Bounds | None,
) -> tuple[Node, list[Node]] | None:
    """Build one MotionGraphics element as an IR node tree.

    Returns the group node and its child nodes, or None when the element
    has no arm the IR can draw yet.
    """
    fps = ctx.fps
    kind = el.get("element", "")
    start_frame = int(el.get("startFrame", 0))
    duration_frames = int(el.get("durationFrames", 1))
    t0 = start_frame / fps
    tt = duration_frames / fps
    entrance = el.get("entrance", "cut")
    exit_char = el.get("exit", "cut")
    in_frames = RAMP_FRAMES.get(entrance, 0)
    out_frames = RAMP_FRAMES.get(exit_char, 0)
    t_in = in_frames / fps
    t_out = out_frames / fps

    element_id = f"element_{kind}_{start_frame}"
    if kind in ("progress_bar", "frame_accents"):
        if kind == "progress_bar":
            return _build_mg_bar(el, ctx, element_id, t0, tt)
        return _build_mg_accents(el, ctx, element_id, t0, tt)
    if kind == "beat_accent" and box is not None:
        return _build_mg_beat(el, ctx, box, element_id, t0, tt)
    animations: list[Animation] = []

    if in_frames > 0:
        animations.append(Animation(
            target=element_id, property="opacity",
            from_value=0.0, to_value=1.0,
            start=t0, duration=t_in, easing=LinearEasing(),
        ))
    if out_frames > 0:
        animations.append(Animation(
            target=element_id, property="opacity",
            from_value=1.0, to_value=0.0,
            start=max(t0, t0 + tt - t_out), duration=t_out, easing=LinearEasing(),
        ))
    if in_frames > 0 and t_in > 0:
        animations.extend(_entrance_animations(element_id, entrance, t_in, LinearEasing()))
    if out_frames > 0 and t_out > 0:
        animations.extend(_exit_animations(element_id, exit_char, t0 + tt, t_out, LinearEasing()))

    children: list[str] = []
    child_nodes: list[Node] = []
    color = el.get("color", "#FFFFFF")

    if kind == "digit_counter" and box is not None:
        return _build_mg_digit(el, ctx, box, element_id, t0, tt, animations)
    if kind in _MG_TEXT_ARMS and box is not None:
        _, horizontal = _anchor_parts(str(el.get("anchor", "centre")))
        align = {"left": "left", "right": "right"}.get(horizontal, "center")
        inset = _mg_inset(el)
        if inset:
            bar_id = f"{element_id}_rule"
            child_nodes.append(Node(
                id=bar_id, type=NodeType.SHAPE,
                shape=ShapeContent(shape="rect", fill=color),
                bounds=Bounds(x=box.x, y=box.y, width=round(8 * _mg_scale(el)), height=box.height),
            ))
            children.append(bar_id)
        gap = MG_ELEMENT_GAPS.get(kind, 12)
        y = box.y
        for i, (run, style, lines, run_height) in enumerate(_mg_run_lines(el, box.width, ctx)):
            run_id = f"{element_id}_run_{i}"
            child_nodes.append(Node(
                id=run_id, type=NodeType.TEXT,
                text=TextContent(
                    text="\n".join(lines),
                    font_family=ctx.font_family, font_file=ctx.font_file,
                    font_size=style["font_size"], font_weight=style["font_weight"],
                    color=color, text_align=align, line_height=MG_LINE_HEIGHT,
                    letter_spacing=style["letter_spacing"],
                    text_transform=style["text_transform"],
                    text_shadow=DEFAULT_TEXT_SHADOW,
                ),
                bounds=Bounds(x=box.x + inset, y=y, width=box.width - inset, height=run_height),
            ))
            children.append(run_id)
            y += run_height + gap
    elif kind == "stat_callout" and box is not None:
        # The template's stat_callout arm: a centred column of runs at 1.5x,
        # drawn through runStyle with each run's own uppercase flag.
        _, horizontal = _anchor_parts(str(el.get("anchor", "centre")))
        align = {"left": "left", "right": "right"}.get(horizontal, "center")
        y = box.y
        for i, (run, style, lines, run_height) in enumerate(
                _mg_stat_run_lines(el, box.width, ctx)):
            run_id = f"{element_id}_run_{i}"
            child_nodes.append(Node(
                id=run_id, type=NodeType.TEXT,
                text=TextContent(
                    text="\n".join(lines),
                    font_family=ctx.font_family, font_file=ctx.font_file,
                    font_size=style["font_size"], font_weight=style["font_weight"],
                    color=color, text_align=align, line_height=MG_LINE_HEIGHT,
                    letter_spacing=style["letter_spacing"],
                    text_transform=style["text_transform"],
                    text_shadow=DEFAULT_TEXT_SHADOW,
                ),
                bounds=Bounds(x=box.x, y=y, width=box.width, height=run_height),
            ))
            children.append(run_id)
            y += run_height + MG_STAT_GAP
    elif kind == "list_build" and box is not None:
        # The template's list_build arm: one row per run - an 8*scale dot
        # in the plan's colour beside copy at the lead item's size - each
        # row arriving on its staged start with a cubic ease-out over 60%
        # of the reveal window, rising 20px as it fades in.
        runs = el.get("runs", [])
        if not runs:
            return None
        scale = _mg_scale(el)
        dot = round(MG_LIST_DOT * scale)
        row_gap = round(MG_LIST_GAP * scale)
        # WHEN each item appears is stageStarts: the plan's own offsets
        # where it has them, an equal share of the hold where not.
        starts, reveal_window, _ = _mg_stage_starts(el, len(el.get("runs", [])), fps)
        reveal_frames = max(1.0, reveal_window * 0.6)
        cubic = EaseEasing(power=3)
        y = box.y
        for i, (run, size, weight, lines, row_height) in enumerate(
                _mg_list_item_lines(el, box.width, ctx)):
            item_id = f"{element_id}_item_{i}"
            item_start = starts[i] if i < len(starts) else 0
            at = t0 + item_start / fps
            span = reveal_frames / fps
            dot_id, text_id = f"{item_id}_dot", f"{item_id}_text"
            # The marker sits on the item's optical centre, DERIVED from
            # the single line box: (listSize * 1.3 - 8) * scale * 0.5.
            dot_top = y + (size * MG_LIST_LINE_HEIGHT - dot) / 2.0
            child_nodes.append(Node(
                id=dot_id, type=NodeType.SHAPE,
                shape=ShapeContent(shape="circle", fill=color),
                bounds=Bounds(x=box.x, y=dot_top, width=dot, height=dot),
            ))
            child_nodes.append(Node(
                id=text_id, type=NodeType.TEXT,
                text=TextContent(
                    text="\n".join(lines),
                    font_family=ctx.font_family, font_file=ctx.font_file,
                    font_size=size, font_weight=weight,
                    color=color, text_align="left",
                    line_height=MG_LIST_LINE_HEIGHT,
                    text_shadow=DEFAULT_TEXT_SHADOW,
                ),
                bounds=Bounds(x=box.x + (MG_LIST_DOT + MG_LIST_GAP) * scale, y=y,
                              width=_mg_list_text_width(box.width, scale),
                              height=row_height),
            ))
            child_nodes.append(Node(
                id=item_id, type=NodeType.GROUP,
                bounds=Bounds(x=box.x, y=y, width=box.width, height=row_height),
                animations=[
                    Animation(
                        target=item_id, property="opacity",
                        from_value=0.0, to_value=1.0,
                        start=at, duration=span, easing=cubic,
                    ),
                    Animation(
                        target=item_id, property="transform.translate",
                        from_value=[0.0, float(MG_LIST_LIFT)], to_value=[0.0, 0.0],
                        start=at, duration=span, easing=cubic,
                    ),
                ],
                children=[dot_id, text_id],
            ))
            children.append(item_id)
            y += row_height + row_gap
    elif kind == "channel_bug":
        if not el.get("asset") or box is None:
            return None
        size = _channel_bug_size(el, ctx)
        if size is None:
            # No readable image geometry, so no bounds to draw in.
            # Returning None routes the card to the Remotion render
            # explicitly (never a boundless image node), and the
            # renderer refuses a boundless image by name if one ever
            # reaches it another way.
            return None
        bug_width, bug_height = size
        _, horizontal = _anchor_parts(str(el.get("anchor", "centre")))
        bug_x = (box.x + (box.width - bug_width) / 2.0
                 if horizontal == "centre" else box.x)
        image_id = f"{element_id}_image"
        child_nodes.append(Node(
            id=image_id, type=NodeType.IMAGE, image=ImageContent(src=el["asset"]),
            bounds=Bounds(x=bug_x, y=box.y, width=bug_width, height=bug_height),
        ))
        children.append(image_id)
    elif kind == "context_stamp" and box is not None:
        stamp_children, stamp_nodes = _build_mg_stamp_children(
            el, ctx, box, element_id, color)
        children.extend(stamp_children)
        child_nodes.extend(stamp_nodes)
    elif kind == "comparison_bars" and box is not None:
        built = _build_mg_bars_children(el, ctx, box, element_id, color, t0)
        if built is None:
            return None
        bars_children, bars_nodes = built
        children.extend(bars_children)
        child_nodes.extend(bars_nodes)
    elif kind == "counter_roll" and box is not None:
        counter_children, counter_nodes = _build_mg_counter_children(
            el, ctx, box, element_id, color, start_frame)
        children.extend(counter_children)
        child_nodes.extend(counter_nodes)
    elif kind == "step_counter" and box is not None:
        step_children, step_nodes = _build_mg_step_children(
            el, ctx, box, element_id, color)
        children.extend(step_children)
        child_nodes.extend(step_nodes)
    elif kind == "pointer_annotation" and box is not None:
        pointer_children, pointer_nodes = _build_mg_pointer_children(
            el, ctx, box, element_id, color)
        children.extend(pointer_children)
        child_nodes.extend(pointer_nodes)
    elif kind == "website_panel" and box is not None:
        built = _build_mg_website_children(el, ctx, box, element_id, color)
        if built is None:
            return None
        website_children, website_nodes = built
        children.extend(website_children)
        child_nodes.extend(website_nodes)
    elif kind == "subject_emblem" and box is not None:
        built = _build_mg_emblem_children(el, ctx, box, element_id, color)
        if built is None:
            return None
        emblem_children, emblem_nodes = built
        children.extend(emblem_children)
        child_nodes.extend(emblem_nodes)
    elif kind == "review_panel" and box is not None:
        built = _build_mg_review_children(el, ctx, box, element_id)
        if built is None:
            return None
        review_children, review_nodes = built
        children.extend(review_children)
        child_nodes.extend(review_nodes)
    else:
        return None

    group_node = Node(
        id=element_id,
        type=NodeType.GROUP,
        bounds=box,
        timing=Timing(start=t0, duration=tt),
        animations=animations,
        children=children,
    )
    return group_node, child_nodes


def _staged_easing(name: str | None):
    if name is None or name == "linear":
        return LinearEasing()
    power = {"in": 2.0, "out": 2.0, "inOut": 2.0}.get(name, 2.0)
    return EaseEasing(power=power)


def _staged_track(target: str, prop: str, keys: list[dict]) -> Track | None:
    if len(keys) < 2:
        return None
    kfs = [Keyframe(at=k["at"], value=k["value"], ease=k.get("ease")) for k in keys]
    return Track(target=target, property=prop, keys=kfs)


def _staged_animations(node_id: str, prop: str, keys: list[dict]) -> list[Animation]:
    if len(keys) < 2:
        return []
    anims = []
    for i in range(len(keys) - 1):
        t0, v0 = keys[i]["at"], keys[i]["value"]
        t1, v1 = keys[i + 1]["at"], keys[i + 1]["value"]
        ease = keys[i + 1].get("ease")
        anims.append(Animation(
            target=node_id, property=prop,
            from_value=v0, to_value=v1,
            start=t0, duration=t1 - t0,
            easing=_staged_easing(ease),
        ))
    return anims


def lower_staged_scene(props: dict[str, Any]) -> Scene:
    """Lower StagedScene props to an IR scene.

    StagedScene is the animation-first declaration the Remotion and
    HyperFrames adapters render: a ground, a camera, and layers of
    discs, rules, images, text and lights, each with keyframed
    animations. This lowers the same declaration to the IR so FFrames
    draws it.

    Text runs are laid out with CSS's rules (the two Blink rounding
    rules), measured with the static instance at each run's declared
    weight. Every text node's face is a static instance because usvg
    cannot apply a variable font's ``wght`` axis.
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    duration = duration_in_frames / fps
    hold_frames = props.get("holdFrames")
    font_family = props.get("fontFamily", "Montserrat")
    font_file = props.get("fontFile")

    nodes: list[Node] = []

    ground = props.get("ground")
    if ground:
        ground_node, vignette_node = _lower_staged_ground(ground, width, height, font_file)
        if ground_node:
            nodes.append(ground_node)

    world_ids: list[str] = []
    for layer in props.get("layers", []):
        if _staged_space(layer) == "frame":
            continue
        node = _lower_staged_layer(layer, width, height, font_family, font_file, fps)
        if node:
            nodes.append(node)
            world_ids.append(node.id)

    camera = props.get("camera")
    if camera:
        cam_node = _lower_staged_camera(camera, width, height, world_ids)
        if cam_node:
            nodes.append(cam_node)

    for layer in props.get("layers", []):
        if _staged_space(layer) != "frame":
            continue
        node = _lower_staged_layer(layer, width, height, font_family, font_file, fps)
        if node:
            nodes.append(node)

    if ground and vignette_node is not None:
        nodes.append(vignette_node)

    return Scene(
        version=1, fps=fps, width=width, height=height,
        duration=duration, hold_frames=hold_frames, nodes=nodes,
    )


def _staged_space(layer: dict) -> str:
    """Which space a layer is placed in: ``frame`` holds still on the lens,
    everything else rides the world under the camera (the template's
    ``layerSpace``)."""
    return "frame" if layer.get("space") == "frame" else "world"


def _lower_staged_ground(ground: dict, width: int, height: int,
                         font_file: str | None) -> tuple[Node | None, Node | None]:
    """The ground colour and its lens vignette, if declared.

    The template draws the vignette as a radial-gradient overlay that
    holds still on the lens while the world moves
    (``radial-gradient(ellipse RX% RY% at 50% 50%, transparent 0%,
    black@strength 100%)``), so the lowering emits the same overlay
    rather than a shader the adapter would have to invent.
    """
    colour = ground.get("colour", "#000000")
    shape = ShapeContent(shape="rect", fill=colour)
    pattern = None
    texture = ground.get("texture")
    if texture:
        from .model import PatternFill
        pattern = PatternFill(
            src=texture,
            tile_width=None,
            tile_height=None,
            opacity=ground.get("textureOpacity", 1.0),
        )
        shape.pattern = pattern
    ground_node: Node | None = Node(
        id="ground", type=NodeType.SHAPE, shape=shape,
        bounds=Bounds(x=0, y=0, width=width, height=height),
    )
    vignette = ground.get("vignette")
    if not vignette:
        return ground_node, None
    strength = float(vignette.get("strength", 0.5))
    if strength <= 0:
        return ground_node, None
    overlay = Node(
        id="vignette", type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill=RadialGradient(
            kind="radial",
            radius_x=float(vignette.get("radiusX", 0.8)) * 100.0,
            radius_y=float(vignette.get("radiusY", 0.55)) * 100.0,
            stops=[
                PaintStop(offset=0.0, color="#000000", opacity=0.0),
                PaintStop(offset=1.0, color="#000000", opacity=strength),
            ],
        )),
        bounds=Bounds(x=0, y=0, width=width, height=height),
    )
    return ground_node, overlay


def _lower_staged_camera(camera: dict, width: int, height: int,
                         world_ids: list[str]) -> Node | None:
    """The camera node, carrying the world layers as children and the
    keyframed tracks on itself (the adapter samples a node's own tracks).
    """
    tracks: list[Track] = []
    cam = CameraContent()
    # The template translates by unit-scaled pixels about the frame centre
    # (`translate(${-camX * unit}px, ...)`), so the tracks carry pixels,
    # the IR's unit for every other spatial value.
    for prop, track_name, scale in (("zoom", "camera.scale", 1.0), ("x", "camera.translateX", width),
                                    ("y", "camera.translateY", width), ("roll", "camera.rotate", 1.0),
                                    ("focus", "camera.focus", width)):
        keys = camera.get(prop)
        if keys and len(keys) >= 2:
            scaled = [dict(k, value=k["value"] * scale) for k in keys]
            t = _staged_track("camera", track_name, scaled)
            if t:
                tracks.append(t)
    if not tracks:
        return None
    return Node(id="camera", type=NodeType.CAMERA, camera=cam,
                children=list(world_ids), tracks=tracks)


def _lower_staged_layer(layer: dict, width: int, height: int,
                         font_family: str, font_file: str | None,
                         fps: float) -> Node | None:
    kind = layer.get("kind")
    layer_id = layer.get("id", "layer")
    if kind == "disc":
        return _lower_staged_disc(layer, layer_id, width, height)
    if kind == "rule":
        return _lower_staged_rule(layer, layer_id, width, height)
    if kind == "image":
        return _lower_staged_image(layer, layer_id, width, height)
    if kind == "text":
        return _lower_staged_text(layer, layer_id, width, height, font_family, font_file, fps)
    if kind == "light":
        return _lower_staged_light(layer, layer_id, width, height)
    return None


def _lower_staged_disc(layer: dict, layer_id: str, width: int, height: int) -> Node:
    unit = width
    cx = width / 2.0 + layer.get("x", 0.0) * unit
    cy = height / 2.0 + layer.get("y", 0.0) * unit
    size = layer.get("size", 1.0) * unit
    r = size / 2
    bounds = Bounds(x=cx - r, y=cy - r, width=size, height=size)
    opacity_keys = layer.get("opacity")
    anims = _staged_animations(layer_id, "opacity", opacity_keys) if opacity_keys else []
    blur = layer.get("blur")
    filters = []
    if blur:
        filters.append(BlurFilter(kind="blur", sigma=blur * unit))
    return Node(
        id=layer_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="circle", fill=layer.get("colour", "#000000")),
        bounds=bounds, opacity=1.0, animations=anims, filters=filters,
    )


def _lower_staged_rule(layer: dict, layer_id: str, width: int, height: int) -> Node:
    unit = width
    cx = width / 2.0 + layer.get("x", 0.0) * unit
    # The template centres the bar horizontally on its anchor but hangs
    # its TOP from the anchor's height - only x is halved.
    length = layer.get("length", 1.0) * unit
    thickness = layer.get("thickness", 0.01) * unit
    top = height / 2.0 + layer.get("y", 0.0) * unit
    bounds = Bounds(x=cx - length / 2, y=top, width=length, height=thickness)
    return Node(
        id=layer_id, type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill=layer.get("colour", "#000000")),
        bounds=bounds, opacity=layer.get("opacity", 1.0),
    )


def _lower_staged_image(layer: dict, layer_id: str, width: int, height: int) -> Node:
    unit = width
    placement = layer.get("placement", {})
    w = placement.get("width", 0.5) * unit
    x_keys = placement.get("x") if isinstance(placement.get("x"), list) else None
    y_keys = placement.get("y") if isinstance(placement.get("y"), list) else None
    rotate_keys = placement.get("rotate") if isinstance(placement.get("rotate"), list) else None
    opacity_keys = placement.get("opacity") if isinstance(placement.get("opacity"), list) else None
    x0 = (x_keys[0]["value"] if isinstance(x_keys, list) and x_keys else 0.0)
    y_val = placement.get("y", 0.0)
    y0 = (y_keys[0]["value"] if isinstance(y_keys, list) and y_keys else y_val)
    cx = width / 2.0 + x0 * unit
    cy = height / 2.0 + y0 * unit
    bounds = Bounds(x=cx - w / 2, y=cy - w / 2, width=w, height=w)
    anims = []
    if x_keys:
        anims.extend(_staged_animations(layer_id, "transform.translateX", x_keys))
    if y_keys:
        anims.extend(_staged_animations(layer_id, "transform.translateY", y_keys))
    if rotate_keys:
        anims.extend(_staged_animations(layer_id, "transform.rotate", rotate_keys))
    if opacity_keys:
        anims.extend(_staged_animations(layer_id, "opacity", opacity_keys))
    clip = placement.get("clipPath")
    shadow = layer.get("shadow")
    filters = []
    if shadow:
        filters.append(DropShadowFilter(
            kind="drop_shadow",
            dx=shadow.get("dx", 0) * unit,
            dy=shadow.get("dy", 0) * unit,
            sigma=shadow.get("blur", 0) * unit,
            color=shadow.get("colour", "rgba(0,0,0,0.5)"),
        ))
    return Node(
        id=layer_id, type=NodeType.IMAGE,
        image=ImageContent(src=layer.get("src", "")),
        bounds=bounds, animations=anims, filters=filters,
        clip=clip,
    )


def _lower_staged_text(layer: dict, layer_id: str, width: int, height: int,
                        font_family: str, font_file: str | None,
                        fps: float) -> Node:
    unit = width
    placement = layer.get("placement", {})
    box_w = placement.get("width", 0.8) * unit
    px = width / 2.0 + placement.get("x", 0.0) * unit - box_w / 2
    # The template centres the box on its anchor (translate(-50%,-50%)),
    # so the top sits half the box height above the anchored point. box_h
    # is measured below; py is placed after it.
    py_anchor = height / 2.0 + placement.get("y", 0.0) * unit
    align = layer.get("align", "left")
    text_align = {"left": "left", "center": "center", "right": "right"}.get(align, "left")
    runs_out: list[TextRun] = []
    lines: list[list[dict]] = [[]]
    for run in layer.get("runs", []):
        if run.get("newLine") and lines[-1]:
            lines.append([])
        lines[-1].append(run)
    from . import fonts as fonts_mod
    font_path = font_file
    if font_path is None:
        font_path = str(Path(__file__).resolve().parent.parent.parent /
                        "remotion-subtitles" / "public" / "fonts" / "Montserrat-Variable.ttf")
    vm = fonts_mod.vertical_metrics(font_path)
    A, D = vm.ascent, vm.descent
    STRUT_A, STRUT_D = 0.891, 0.216
    STRUT_SIZE = 16.0
    leading = layer.get("leading")
    reveal_ramp = layer.get("revealRamp", 0.067)
    shadow = layer.get("shadow")
    text_shadow = None
    if shadow:
        text_shadow = f"{shadow.get('dx', 0) * unit:g}px {shadow.get('dy', 0) * unit:g}px {shadow.get('blur', 0) * unit:g}px {shadow.get('colour', 'rgba(0,0,0,0.5)')}"
    top = 0.0
    for li, line in enumerate(lines):
        def inline(size, a, d):
            lh = leading * unit if leading is not None else size
            ra, rd = round(a * size), round(d * size)
            leading_px = lh - (ra + rd)
            up = math.floor(leading_px / 2)
            return ra + up, rd + (leading_px - up)
        above, below = inline(STRUT_SIZE, STRUT_A, STRUT_D)
        widths = []
        for ri, run in enumerate(line):
            size = run["size"] * unit
            ab, be = inline(size, A, D)
            above, below = max(above, ab), max(below, be)
            ls = run.get("letterSpacing", 0) * unit
            adv = fonts_mod.advances(font_path, run["weight"]).width(run["text"], size)
            widths.append(adv + ls * len(run["text"]))
        line_w = sum(widths)
        x = 0.0 if align == "left" else (box_w - line_w) if align == "right" else (box_w - line_w) / 2
        top + above
        for ri, run in enumerate(line):
            size = run["size"] * unit
            runs_out.append(TextRun(
                text=run["text"], font_size=size, font_weight=run["weight"],
                color=run["colour"], letter_spacing=run.get("letterSpacing", 0) * unit,
                reveal_at=run.get("revealAt"),
                # The run that opened this line broke it in the
                # declaration; the adapter breaks there too.
                new_line=li > 0 and ri == 0,
            ))
            x += widths[ri]
        top += above + below
    box_h = top
    py = py_anchor - box_h / 2.0
    bounds = Bounds(x=px, y=py, width=box_w, height=box_h)
    return Node(
        id=layer_id, type=NodeType.TEXT,
        text=TextContent(
            text="", font_family=font_family, font_file=font_path,
            text_align=text_align, runs=runs_out,
            reveal_ramp=reveal_ramp, text_shadow=text_shadow,
        ),
        bounds=bounds,
    )


def _lower_staged_light(layer: dict, layer_id: str, width: int, height: int) -> Node:
    unit = width
    cx = width / 2.0 + layer.get("x", 0.0) * unit
    cy = height / 2.0 + layer.get("y", 0.0) * unit
    angle_keys = layer.get("angle")
    length_keys = layer.get("length")
    spread_keys = layer.get("spreadDeg")
    opacity_keys = layer.get("opacity")
    angle_keys[0]["value"] if angle_keys else 0.0
    length0 = length_keys[0]["value"] if length_keys else 0.1
    spread0 = spread_keys[0]["value"] if spread_keys else 45.0
    half_spread = math.radians(spread0 / 2)
    cone_len = length0 * unit
    x0 = cx - cone_len * math.sin(half_spread)
    x1 = cx + cone_len * math.sin(half_spread)
    y0 = cy - cone_len * math.cos(half_spread)
    bounds = Bounds(x=x0, y=y0, width=x1 - x0, height=cone_len)
    anims = []
    if angle_keys:
        anims.extend(_staged_animations(layer_id, "transform.rotate", angle_keys))
    if length_keys:
        anims.extend(_staged_animations(layer_id, "width", length_keys))
    if spread_keys:
        anims.extend(_staged_animations(layer_id, "width", spread_keys))
    if opacity_keys:
        anims.extend(_staged_animations(layer_id, "opacity", opacity_keys))
    falloff = layer.get("falloff", 0.5)
    colour = layer.get("colour", "#FFFFFF")
    return Node(
        id=layer_id, type=NodeType.SHAPE,
        shape=ShapeContent(
            shape="path",
            fill=RadialGradient(
                kind="radial", radius_x=50.0, radius_y=100.0,
                stops=[
                    PaintStop(offset=0.0, color=colour, opacity=1.0),
                    PaintStop(offset=falloff, color=colour, opacity=0.5),
                    PaintStop(offset=1.0, color=colour, opacity=0.0),
                ],
            ),
            path=f"M {cx} {cy} L {x0} {y0} L {x1} {y0} Z",
        ),
        bounds=bounds, animations=anims,
    )


def lower_brand_motion(props: dict[str, Any]) -> Scene:
    """Lower BrandMotion props to an IR scene.

    The composition plays one project-supplied file whole, full frame,
    with its own alpha (``remotion-subtitles/src/compositions/
    BrandMotion``: an ``AbsoluteFill`` ``OffthreadVideo`` with
    ``objectFit: fill``). The lowering is one Video node over the whole
    delivery frame - no trim (``brand_motion_props`` renders the whole
    file and offers no trim parameter), no fit (a geometry mismatch is
    refused upstream by ``GeometryMismatch`` rather than letterboxed or
    cropped), no conform prop (the renderer draws ``native_sample``:
    wall-clock sampling, each composition frame seeking the source
    timestamp, nothing blended).

    ``src`` names the staged file the composition wraps in
    ``staticFile`` (``brand/<file>`` under Remotion's ``public/``); the
    caller resolves it to an absolute path before lowering (see
    ``fframes_render.resolve_props``), because the scene carries file
    paths, never URLs. An empty ``src`` - which the composition draws as
    nothing - is refused here rather than lowered to a node that draws
    nothing, because an overlay that draws nothing is not rendered
    (AGENTS.md 10.2).
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    src = str(props.get("src") or "")
    if not src:
        raise ValueError(
            "BrandMotion props name no src - the composition draws "
            "nothing without one, and an overlay that draws nothing is "
            "not rendered.")
    return Scene(
        version=1, fps=fps, width=width, height=height,
        duration=duration_in_frames / fps,
        nodes=[Node(
            id="brand_motion", type=NodeType.VIDEO,
            video=VideoContent(src=src),
            bounds=Bounds(x=0, y=0, width=width, height=height),
        )],
    )
