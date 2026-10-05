"""Lowering functions: composition props to IR scenes.

Each function takes the same props the steps already build for the
HyperFrames and Remotion compositions and produces an IR scene whose
timing and easing match what the HyperFrames composition's GSAP timeline
does today.

The lowering is deliberately mechanical: every tween in the HyperFrames
composition becomes an Animation, every ``.clip`` window becomes a
Timing, every styled element becomes a Node. Where a composition has
behaviour the IR cannot yet express, the gap is listed in the PR body
rather than approximated.
"""

from __future__ import annotations

import math
from typing import Any

from .model import (
    Animation,
    Bounds,
    EaseEasing,
    ImageContent,
    LinearEasing,
    Node,
    NodeType,
    Scene,
    ShapeContent,
    StepsEasing,
    SVGContent,
    TextContent,
    Timing,
    Transform,
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


def lower_subtitle_overlay(props: dict[str, Any]) -> Scene:
    """Lower SubtitleOverlay props to an IR scene.

    Each subtitle block becomes a Group windowed to its span. Each word
    with timing becomes a Text node carrying the accent sweep (two
    stepped colour tweens) and the entry scale (0.95 to 1 over 4 frames,
    power1.out).
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
    font_file = style.get("fontFile")
    base_size = float(style.get("fontSize", 58))
    font_weight = float(style.get("fontWeight", 800))
    position = style.get("position", "bottom")
    safe_area = style.get("safeArea", {"top": 0, "right": 0, "bottom": 0, "left": 0})
    max_width = float(style.get("captionMaxWidth", width))

    nodes: list[Node] = []

    for i, sub in enumerate(subtitles):
        card_id = f"subtitle_{i}"
        start_frame = int(sub["startFrame"])
        end_frame = int(sub["endFrame"])
        card_start = start_frame / fps
        card_duration = max(1, end_frame - start_frame) / fps

        card_timing = Timing(start=card_start, duration=card_duration)
        card_bounds = Bounds(
            x=0,
            y=0,
            width=width,
            height=height,
        )

        children: list[str] = []
        words = sub.get("words", [])
        emphasis_words = sub.get("emphasisWords", [])
        fit_scale = sub.get("fitScale")

        if words:
            for j, word_timing in enumerate(words):
                word_id = f"{card_id}_word_{j}"
                ws = int(word_timing["startFrame"]) / fps
                we = int(word_timing["endFrame"]) / fps
                word_text = word_timing["word"]

                is_emphasis = _is_emphasis(word_text, emphasis_words)
                word_size = base_size * (1.14 if is_emphasis else 1.0)
                if fit_scale is not None and fit_scale < 1:
                    word_size = round(base_size * fit_scale) * (1.14 if is_emphasis else 1.0)

                animations: list[Animation] = []

                if we > ws:
                    animations.append(Animation(
                        target=word_id,
                        property="color",
                        from_value=font_color,
                        to_value=accent_color,
                        start=max(0.0, ws - EPS),
                        duration=EPS,
                        easing=LinearEasing(),
                    ))
                    animations.append(Animation(
                        target=word_id,
                        property="color",
                        from_value=accent_color,
                        to_value=font_color,
                        start=max(0.0, we - EPS),
                        duration=EPS,
                        easing=LinearEasing(),
                    ))
                    animations.append(Animation(
                        target=word_id,
                        property="transform.scale",
                        from_value=[0.95, 0.95],
                        to_value=[1.0, 1.0],
                        start=ws,
                        duration=4.0 / fps,
                        easing=EaseEasing(power=2),
                    ))
                else:
                    animations.append(Animation(
                        target=word_id,
                        property="transform.scale",
                        from_value=[0.95, 0.95],
                        to_value=[1.0, 1.0],
                        start=max(0.0, we - EPS),
                        duration=EPS,
                        easing=LinearEasing(),
                    ))

                word_node = Node(
                    id=word_id,
                    type=NodeType.TEXT,
                    text=TextContent(
                        text=word_text,
                        font_family=font_family,
                        font_file=font_file,
                        font_size=word_size,
                        font_weight=font_weight,
                        color=font_color,
                    ),
                    animations=animations,
                )
                nodes.append(word_node)
                children.append(word_id)
        else:
            text_id = f"{card_id}_text"
            text_size = base_size
            if fit_scale is not None and fit_scale < 1:
                text_size = round(base_size * fit_scale)
            text_node = Node(
                id=text_id,
                type=NodeType.TEXT,
                text=TextContent(
                    text=sub.get("text", ""),
                    font_family=font_family,
                    font_file=font_file,
                    font_size=text_size,
                    font_weight=font_weight,
                    color=font_color,
                ),
            )
            nodes.append(text_node)
            children.append(text_id)

        card_node = Node(
            id=card_id,
            type=NodeType.GROUP,
            bounds=card_bounds,
            timing=card_timing,
            children=children,
        )
        nodes.append(card_node)

    return Scene(
        version=1,
        fps=fps,
        width=width,
        height=height,
        duration=duration_in_frames / fps,
        nodes=nodes,
    )


def lower_timed_text_overlay(props: dict[str, Any]) -> Scene:
    """Lower TimedTextOverlay props to an IR scene.

    Each moment becomes a Text node windowed to its span, carrying the
    fade-in and fade-out opacity ramps (linear, clamped to fit).
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    moments = props.get("moments", [])
    font_family = props.get("fontFamily", "Helvetica")
    font_file = props.get("fontFile")

    nodes: list[Node] = []

    for i, moment in enumerate(moments):
        dur = int(moment.get("durationFrames", 0))
        if dur <= 0:
            continue

        moment_id = f"moment_{i}"
        start_frame = int(moment["startFrame"])
        t0 = start_frame / fps
        dur_seconds = dur / fps

        fade_in = max(0, min(int(moment.get("fadeInFrames", 0)), dur))
        fade_out = max(0, min(int(moment.get("fadeOutFrames", 0)), dur - fade_in))

        animations: list[Animation] = []
        if fade_in > 0:
            animations.append(Animation(
                target=moment_id,
                property="opacity",
                from_value=0.0,
                to_value=1.0,
                start=t0,
                duration=fade_in / fps,
                easing=LinearEasing(),
            ))
        if fade_out > 0:
            animations.append(Animation(
                target=moment_id,
                property="opacity",
                from_value=1.0,
                to_value=0.0,
                start=t0 + (dur - fade_out) / fps,
                duration=fade_out / fps,
                easing=LinearEasing(),
            ))

        x = float(moment.get("x", 0.5))
        y = float(moment.get("y", 0.5))

        node = Node(
            id=moment_id,
            type=NodeType.TEXT,
            text=TextContent(
                text=moment.get("text", ""),
                font_family=font_family,
                font_file=font_file,
                font_size=float(moment.get("fontSize", 48)),
                font_weight=float(moment.get("fontWeight", 400)),
                color=moment.get("color", "#FFFFFF"),
                text_align=moment.get("textAlign", "center"),
                text_shadow=moment.get("textShadow", "0px 4px 12px rgba(0,0,0,0.6)"),
            ),
            transform=Transform(translate=[x * width, y * height]),
            timing=Timing(start=t0, duration=dur_seconds),
            animations=animations,
        )
        nodes.append(node)

    return Scene(
        version=1,
        fps=fps,
        width=width,
        height=height,
        duration=duration_in_frames / fps,
        nodes=nodes,
    )


def lower_full_frame_card(props: dict[str, Any]) -> Scene:
    """Lower FullFrameCard props to an IR scene.

    The card becomes a Ground with a Group carrying the opacity ramp and
    the entrance/exit character. Runs become Text nodes. Word cues become
    per-word animations on masked or drawn spans.
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    duration = duration_in_frames / fps
    runs = props.get("runs", [])
    background = props["background"]
    entrance = props.get("entrance", "cut")
    exit_char = props.get("exit", "cut")
    font_family = props.get("fontFamily", "Montserrat")
    font_file = props.get("fontFile")
    safe_area = props.get("safeArea", {"top": 0, "right": 0, "bottom": 0, "left": 0})
    y = props.get("y")
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

    nodes: list[Node] = []

    ground = Node(
        id="ground",
        type=NodeType.GROUND,
        bounds=Bounds(x=0, y=0, width=width, height=height),
    )
    nodes.append(ground)

    card_animations: list[Animation] = []

    if in_frames > 0:
        card_animations.append(Animation(
            target="card",
            property="opacity",
            from_value=0.0,
            to_value=1.0,
            start=0.0,
            duration=t_in,
            easing=hold_ease,
        ))
    if out_frames > 0:
        card_animations.append(Animation(
            target="card",
            property="opacity",
            from_value=1.0,
            to_value=0.0,
            start=max(0.0, duration - t_out),
            duration=t_out,
            easing=hold_ease,
        ))

    if in_frames > 0 and t_in > 0:
        card_animations.extend(_entrance_animations("card", entrance, t_in, hold_ease))
    if out_frames > 0 and t_out > 0:
        card_animations.extend(_exit_animations("card", exit_char, duration, t_out, hold_ease))

    card_children: list[str] = []

    if image:
        image_id = "card_image"
        image_node = Node(
            id=image_id,
            type=NodeType.IMAGE,
            image=ImageContent(src=image),
            bounds=Bounds(
                x=safe_area["left"],
                y=safe_area["top"],
                width=float(image_width) if image_width else width - safe_area["left"] - safe_area["right"],
                height=0,
            ),
        )
        nodes.append(image_node)
        card_children.append(image_id)

    cued = word_cues and len(word_cues) > 0 and entrance in ("typewriter", "mask", "draw")

    if not cued and entrance != "typewriter":
        for i, run in enumerate(runs):
            run_id = f"run_{i}"
            run_node = Node(
                id=run_id,
                type=NodeType.TEXT,
                text=TextContent(
                    text=run.get("text", ""),
                    font_family=font_family,
                    font_file=font_file,
                    font_size=float(run.get("fontSize", TYPE_SIZE.get(run.get("typeRole", "supporting"), 36))),
                    font_weight=TYPE_WEIGHT.get(run.get("typeRole", "supporting"), 700),
                    color=run.get("colour", "#FFFFFF"),
                    line_height=1.18,
                ),
            )
            nodes.append(run_node)
            card_children.append(run_id)
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
                    run_id = f"{layer_id}_run_{ri}"
                    run_node = Node(
                        id=run_id,
                        type=NodeType.TEXT,
                        text=TextContent(
                            text=run.get("text", "")[:shown],
                            font_family=font_family,
                            font_file=font_file,
                            font_size=float(run.get("fontSize", TYPE_SIZE.get(run.get("typeRole", "supporting"), 36))),
                            font_weight=TYPE_WEIGHT.get(run.get("typeRole", "supporting"), 700),
                            color=run.get("colour", "#FFFFFF"),
                            line_height=1.18,
                        ),
                    )
                    nodes.append(run_node)
                    layer_children.append(run_id)
                layer_node = Node(
                    id=layer_id,
                    type=NodeType.GROUP,
                    timing=Timing(start=f0 / fps, duration=(f1 - f0) / fps),
                    children=layer_children,
                )
                nodes.append(layer_node)
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
                run_id = f"run_{i}"
                run_node = Node(
                    id=run_id,
                    type=NodeType.TEXT,
                    text=TextContent(
                        text=run.get("text", ""),
                        font_family=font_family,
                        font_file=font_file,
                        font_size=float(run.get("fontSize", TYPE_SIZE.get(run.get("typeRole", "supporting"), 36))),
                        font_weight=TYPE_WEIGHT.get(run.get("typeRole", "supporting"), 700),
                        color=run.get("colour", "#FFFFFF"),
                        line_height=1.18,
                    ),
                )
                nodes.append(run_node)
                card_children.append(run_id)
        else:
            texts = [r.get("text", "") for r in runs]
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
                    word_animations.append(Animation(
                        target=word_id,
                        property="transform.scale",
                        from_value=[0.85, 0.85],
                        to_value=[1.0, 1.0],
                        start=t0,
                        duration=max(EPS, t1 - t0),
                        easing=EaseEasing(power=3),
                    ))
                    word_animations.append(Animation(
                        target=word_id,
                        property="blur",
                        from_value=4.0,
                        to_value=0.0,
                        start=t0,
                        duration=max(EPS, t1 - t0),
                        easing=EaseEasing(power=3),
                    ))
                    word_animations.append(Animation(
                        target=word_id,
                        property="opacity",
                        from_value=0.0,
                        to_value=1.0,
                        start=t0,
                        duration=max(EPS, t1 - t0),
                        easing=EaseEasing(power=3),
                    ))
                else:
                    word_animations.append(Animation(
                        target=word_id,
                        property="transform.translate",
                        from_value=[0.0, 120.0],
                        to_value=[0.0, 0.0],
                        start=t0,
                        duration=max(EPS, t1 - t0),
                        easing=EaseEasing(power=3),
                    ))

                if emphasis_colour:
                    word_animations.append(Animation(
                        target=word_id,
                        property="color",
                        from_value=run.get("colour", "#FFFFFF"),
                        to_value=emphasis_colour,
                        start=max(0.0, cue.get("start", 0) - EPS),
                        duration=EPS,
                        easing=LinearEasing(),
                    ))
                    word_animations.append(Animation(
                        target=word_id,
                        property="color",
                        from_value=emphasis_colour,
                        to_value=run.get("colour", "#FFFFFF"),
                        start=max(0.0, cue.get("end", 0) - EPS),
                        duration=EPS,
                        easing=LinearEasing(),
                    ))

                word_node = Node(
                    id=word_id,
                    type=NodeType.TEXT,
                    text=TextContent(
                        text=word["text"],
                        font_family=font_family,
                        font_file=font_file,
                        font_size=float(run.get("fontSize", TYPE_SIZE.get(run.get("typeRole", "supporting"), 36))),
                        font_weight=TYPE_WEIGHT.get(run.get("typeRole", "supporting"), 700),
                        color=run.get("colour", "#FFFFFF"),
                        line_height=1.18,
                    ),
                    animations=word_animations,
                )
                nodes.append(word_node)
                card_children.append(word_id)

    card_node = Node(
        id="card",
        type=NodeType.GROUP,
        bounds=Bounds(
            x=safe_area["left"],
            y=safe_area["top"],
            width=width - safe_area["left"] - safe_area["right"],
            height=height - safe_area["top"] - safe_area["bottom"],
        ),
        animations=card_animations,
        children=card_children,
    )
    nodes.append(card_node)

    return Scene(
        version=1,
        fps=fps,
        width=width,
        height=height,
        duration=duration,
        nodes=nodes,
    )


def lower_motion_graphics(props: dict[str, Any]) -> Scene:
    """Lower MotionGraphics props to an IR scene.

    Each element becomes a Group windowed to its span, carrying the
    opacity ramp and the entrance/exit character. Child nodes depend on
    the element's arm (title_lockup, quote_card, progress_bar, etc.).
    """
    fps = float(props["fps"])
    width = int(props["width"])
    height = int(props["height"])
    duration_in_frames = int(props["durationInFrames"])
    elements = props.get("elements", [])
    safe_area = props.get("safeArea", {"top": 0, "right": 0, "bottom": 0, "left": 0})
    layout_width = props.get("layoutWidth")

    nodes: list[Node] = []

    self_positioning = {"progress_bar", "frame_accents"}

    for el in elements:
        if el.get("element") in self_positioning:
            built = _build_mg_element(el, fps, width, height, safe_area, layout_width)
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
        stack_id = f"stack_{anchor}"
        stack_children: list[str] = []
        anchor_elements = sorted(
            [el for el in elements if el.get("element") not in self_positioning and el.get("anchor", "centre") == anchor],
            key=lambda e: e.get("row", 0),
        )
        for el in anchor_elements:
            built = _build_mg_element(el, fps, width, height, safe_area, layout_width)
            if built:
                group_node, child_nodes = built
                nodes.append(group_node)
                nodes.extend(child_nodes)
                stack_children.append(group_node.id)

        stack_node = Node(
            id=stack_id,
            type=NodeType.GROUP,
            children=stack_children,
        )
        nodes.append(stack_node)

    return Scene(
        version=1,
        fps=fps,
        width=width,
        height=height,
        duration=duration_in_frames / fps,
        nodes=nodes,
    )


def lower_post_header(props: dict[str, Any]) -> Scene:
    """Lower PostHeader props to an IR scene.

    A static composition: a Group with the header layout, Text nodes for
    name/handle/hook, an Image for the avatar and an SVG for the verified
    badge. No animations.
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

    nodes: list[Node] = []
    children: list[str] = []

    avatar_id = "avatar"
    avatar_node = Node(
        id=avatar_id,
        type=NodeType.IMAGE,
        image=ImageContent(src=props.get("avatarSrc", "")),
        bounds=Bounds(
            x=box["left"],
            y=box["top"],
            width=avatar_size,
            height=avatar_size,
        ),
    )
    nodes.append(avatar_node)
    children.append(avatar_id)

    name_id = "name"
    name_node = Node(
        id=name_id,
        type=NodeType.TEXT,
        text=TextContent(
            text=props.get("displayName", ""),
            font_family=font_family,
            font_file=font_file,
            font_size=name_size,
            font_weight=700,
            color="#FFFFFF",
            line_height=1.2,
        ),
        transform=Transform(translate=[box["left"] + avatar_size + gap, box["top"]]),
    )
    nodes.append(name_node)
    children.append(name_id)

    if props.get("verified"):
        badge_id = "badge"
        badge_size = round(name_size * 1.05)
        badge_node = Node(
            id=badge_id,
            type=NodeType.SVG,
            svg=SVGContent(
                viewBox="0 0 24 24",
                path="M22.25 12c0-1.43-.88-2.67-2.19-3.34.46-1.39.2-2.9-.81-3.91s-2.52-1.27-3.91-.81c-.66-1.31-1.91-2.19-3.34-2.19s-2.67.88-3.33 2.19c-1.4-.46-2.91-.2-3.92.81s-1.26 2.52-.8 3.91C2.63 9.33 1.75 10.57 1.75 12s.88 2.67 2.19 3.34c-.46 1.39-.2 2.9.81 3.91s2.52 1.26 3.91.81c.67 1.31 1.91 2.19 3.34 2.19s2.68-.88 3.34-2.19c1.39.46 2.9.2 3.91-.81s1.27-2.52.81-3.91c1.31-.67 2.19-1.91 2.19-3.34z",
                fill="#1D9BF0",
            ),
            bounds=Bounds(
                x=box["left"] + avatar_size + gap + name_size * 0.25,
                y=box["top"],
                width=badge_size,
                height=badge_size,
            ),
        )
        nodes.append(badge_node)
        children.append(badge_id)

    handle_id = "handle"
    handle_node = Node(
        id=handle_id,
        type=NodeType.TEXT,
        text=TextContent(
            text="@" + props.get("handle", ""),
            font_family=font_family,
            font_file=font_file,
            font_size=handle_size,
            font_weight=500,
            color="#8B98A5",
            line_height=1.25,
        ),
        transform=Transform(translate=[box["left"] + avatar_size + gap, box["top"] + name_size * 1.2]),
    )
    nodes.append(handle_node)
    children.append(handle_id)

    hook_id = "hook"
    hook_node = Node(
        id=hook_id,
        type=NodeType.TEXT,
        text=TextContent(
            text=props.get("hook", ""),
            font_family=font_family,
            font_file=font_file,
            font_size=hook_size,
            font_weight=600,
            color="#FFFFFF",
            line_height=1.3,
        ),
        transform=Transform(translate=[box["left"], box["top"] + avatar_size + round(hook_size * 0.6)]),
    )
    nodes.append(hook_node)
    children.append(hook_id)

    header_node = Node(
        id="header",
        type=NodeType.GROUP,
        bounds=Bounds(x=box["left"], y=box["top"], width=box["width"], height=avatar_size + hook_size * 2),
        children=children,
    )
    nodes.append(header_node)

    return Scene(
        version=1,
        fps=fps,
        width=width,
        height=height,
        duration=duration_in_frames / fps,
        nodes=nodes,
    )


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


def _build_mg_element(
    el: dict[str, Any],
    fps: float,
    width: int,
    height: int,
    safe_area: dict[str, float],
    layout_width: float | None,
) -> tuple[Node, list[Node]] | None:
    """Build one MotionGraphics element as an IR node tree.

    Returns the group node and its child nodes, or None when the element
    has no asset the IR can draw.
    """
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
    runs = el.get("runs", [])
    color = el.get("color", "#FFFFFF")
    footprint = el.get("footprint")
    scale = footprint if footprint and footprint > 0 else 1.0

    if kind in ("title_lockup", "quote_card", "lower_third"):
        for i, run in enumerate(runs):
            run_id = f"{element_id}_run_{i}"
            run_node = Node(
                id=run_id,
                type=NodeType.TEXT,
                text=TextContent(
                    text=run.get("text", ""),
                    font_size=TYPE_SIZE.get(run.get("type_role", "supporting"), 36) * scale,
                    font_weight=TYPE_WEIGHT.get(run.get("type_role", "supporting"), 700),
                    color=color,
                    line_height=1.1,
                ),
            )
            child_nodes.append(run_node)
            children.append(run_id)
    elif kind == "channel_bug":
        if not el.get("asset"):
            return None
        image_id = f"{element_id}_image"
        image_node = Node(
            id=image_id,
            type=NodeType.IMAGE,
            image=ImageContent(src=el["asset"]),
        )
        child_nodes.append(image_node)
        children.append(image_id)
    else:
        return None

    group_node = Node(
        id=element_id,
        type=NodeType.GROUP,
        timing=Timing(start=t0, duration=tt),
        animations=animations,
        children=children,
    )
    return group_node, child_nodes
