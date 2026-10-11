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
from typing import Any, Optional

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
    StepsEasing,
    SVGContent,
    TextContent,
    TextRun,
    Timing,
    Track,
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


def _greedy_rows(widths: list[float], limit: Optional[float]) -> list[list[int]]:
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
    stepped colour tweens) and the entry scale (0.95 to 1 over 4 frames,
    the template's `Easing.out(Easing.ease)` as `cubic-bezier(0,0,0.58,1)`).
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
                    animations.append(Animation(
                        target=word_id, property="transform.scale",
                        from_value=[0.95, 0.95], to_value=[1.0, 1.0],
                        start=ws, duration=4.0 / fps,
                        # The template's entry ramp: Remotion
                        # `Easing.out(Easing.ease)` where `ease` is
                        # `bezier(0.42, 0, 1, 1)`. Reversing a cubic
                        # bezier (1-x2, 1-y2, 1-x1, 1-y1) is its
                        # `out()` form, so `cubic-bezier(0, 0, 0.58, 1)`
                        # draws the template's curve to float precision
                        # (max deviation ~6e-17, measured 2026-10-10).
                        # A power ease-out (power=2) reads close but sits
                        # ~0.06 low at mid-ramp - visibly smaller ink.
                        easing=CubicBezierEasing(x1=0.0, y1=0.0, x2=0.58, y2=1.0),
                    ))
                else:
                    animations.append(Animation(
                        target=word_id, property="transform.scale",
                        from_value=[0.95, 0.95], to_value=[1.0, 1.0],
                        start=max(0.0, we - EPS), duration=EPS, easing=LinearEasing(),
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
        measure = dict(font_size=size, font_file=font_file, font_weight=weight)
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
        measure = dict(font_size=size, font_file=font_file, font_weight=_run_weight(run))
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
    the element's arm (title_lockup, quote_card, lower_third, channel_bug).
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
        return [Animation(
            target=target, property="transform.rotate",
            from_value=90.0, to_value=0.0,
            start=0.0, duration=t_in, easing=easing,
        )]
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
        return [Animation(
            target=target, property="transform.rotate",
            from_value=0.0, to_value=-90.0,
            start=t1, duration=t_out, easing=easing,
        )]
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
    (*_MG_TEXT_ARMS, "channel_bug")
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
    return dict(
        font_size=TYPE_SIZE.get(role, TYPE_SIZE["supporting"]) * scale,
        font_file=ctx.font_file,
        font_weight=TYPE_WEIGHT.get(role, TYPE_WEIGHT["supporting"]),
        letter_spacing=MG_DISPLAY_LETTER_SPACING if display else 0.0,
        text_transform="uppercase" if display else None,
    )


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


def _mg_natural_width(el: dict[str, Any], ctx: _MGContext) -> float:
    if el.get("element") == "channel_bug":
        return _channel_bug_width(el, ctx)
    if el.get("element") not in _MG_TEXT_ARMS:
        return 0.0
    scale = _mg_scale(el)
    widest = 0.0
    for run in el.get("runs", []):
        style = _mg_run_style(run, scale, ctx)
        widest = max(widest, block_width(run.get("text", "").split("\n"), **style))
    return widest + _mg_inset(el)


def _mg_height(el: dict[str, Any], box_width: float, ctx: _MGContext) -> float:
    if el.get("element") == "channel_bug":
        size = _channel_bug_size(el, ctx)
        return size[1] if size is not None else 0.0
    if el.get("element") not in _MG_TEXT_ARMS:
        return 0.0
    heights = [h for *_, h in _mg_run_lines(el, box_width, ctx)]
    gap = MG_ELEMENT_GAPS.get(el.get("element"), 12)
    return sum(heights) + gap * max(0, len(heights) - 1)


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


def _build_mg_element(
    el: dict[str, Any],
    ctx: _MGContext,
    box: Optional[Bounds],
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
    from .layout import measure_text
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
        baseline = top + above
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
    angle0 = angle_keys[0]["value"] if angle_keys else 0.0
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
