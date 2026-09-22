"""Timed text moments: one enumeration, declared per project, read by 4.06.

A declaration gets N text moments composited over the finished picture -
an episode number, a chapter title, a date stamp. Declare nothing and get
nothing, the same opt-in shape as ``effect.motion_accents`` (P3.1/Q3) and
``content.bookends`` (Q7, 2026-08-16).

Two places may declare it, and the PROJECT wins - see
:func:`resolve_declaration` for why a card belongs to the project and not
to the engine's brand template.

What a declaration looks like::

    effect:
      timed_text_overlay:
        font_family: "Nanum Pen Script"          # the project's typeface
        font_file: "NanumPenScript-Regular.ttf"  # in <project>/brand_assets/
        moments:
          - text: "Night 1"
            color: "#D4A34A"
            font_size: 64
            font_weight: 400
            text_shadow: "0px 2px 6px rgba(18,11,7,0.9)"
            block: hook             # spine block position ("hook" or an int)
            anchor: end             # or `start`; default `start`
            offset_seconds: 0.15
            duration_seconds: 2.0
            x: 0.5
            y: 0.545
            fade_in_frames: 9
            fade_out_frames: 12

    Every style key above is REQUIRED: size, fade, weight, shadow and
    position are artwork the project states (AGENTS.md 10.5, 14), and a
    moment omitting one raises rather than rendering in an engine
    constant. Only ``text_align`` keeps an engine default - the shipped
    Night card omits it (see ``STYLE_MOMENT_KEYS``).

A moment is timed from the SPINE - ``docs/ASSET_LIBRARY_PLAN.md``
section 5, "no absolute frames, no assumed total" - so re-cutting the
edit moves it with the block it belongs to.  ``start_frame`` +
``duration_frames`` remain available for a caller that genuinely means a
position in the finished timeline, and are bounded by the spine's real
length; the 4th Wall card's frame numbers came from a 60.000s cut that
no longer existed, and nothing checked.

Geometry is normalised against the whole delivery frame, NOT the picture
area inside any letterbox bars a landscape source produces.  There is no
picture-area enumeration to resolve against yet, so a declaration that
must clear the bars states its own ``y``.  Measured on the only finished
render on disk - project 001, 1080x1920, a 16:9 landscape source - the
picture occupies rows **656..1263** and the burnt-in captions rows
~1699..1765, so ``y`` in ``0.35..0.65`` is over picture in both the
letterboxed and the full-bleed case.  ``tests/test_night_card_delivery.py``
asserts a real card's ink against those rows.

The typeface must be one that will really draw the glyphs:
``library/tools/render_fonts.py`` says which, and a family that is
neither bundled nor accepted as a system font must name the ``font_file``
the project carries.  ``TimedTextOverlay`` blocks the render on that face
and throws if it cannot load it, because a substituted font is a valid
picture of the right size that nothing downstream can tell apart.

How a declaration reaches the picture, in order - this chain IS the
capability, and until 2026-08-20 it stopped at step 1:

1. ``generate_timed_text_overlay_props`` (here) turns the declaration
   into Remotion props for the ``TimedTextOverlay`` composition.
2. ``plan_timed_text_segments`` (here) groups the moments into
   non-overlapping SEGMENTS and rebases each moment's frame numbers
   against the segment it lands in, so nothing renders transparent
   frames between two moments a minute apart.
3. ``library/tools/timed_text_render.py`` renders one overlay artefact
   per segment (``library/tools/overlay_carriage.py`` owns what one IS),
   called by ``render_motion_graphics`` (4.06).
4. ``compile_manifest`` (5.04) carries the segments as the top-level
   ``timed_text_overlay`` manifest key.
5. ``resolve_build_timeline`` (6.01) places each segment on **V6**.

Why segments rather than one full-length overlay: a moment's span is
exactly ``[start_frame, start_frame + duration_frames)`` - the fades live
inside it - so the frames between two moments carry nothing. Rendering
them would burn a full-length ProRes 4444 alpha pass to produce empty
pixels. Moments whose spans touch or overlap must stay in ONE segment,
because two clips cannot occupy the same frames of V6 and because
overlapping text is a legitimate thing to declare; Remotion composites
them in a single pass.

Frame numbers are TIMELINE frames, counted from the first frame of the
finished edit. That is the same clock ``_spine_blocks`` and every
``timeline_start`` in the manifest use.

A malformed declaration raises :class:`TimedTextDeclarationError` rather
than being dropped. The 4th Wall card survived four months precisely
because a declaration that renders nothing is indistinguishable from no
declaration at all - see ``docs/ASSET_LIBRARY_PLAN.md`` and the artwork
rule in section 14 of ``CLAUDE.md``. A template may set the PARAMETERS of
a moment; artwork belongs to the project.
"""
from __future__ import annotations

import os
from typing import Any

from library.tools.safe_area import resolve_safe_area
from library.tools.render_fonts import (
    font_is_deliverable,
    primary_family,
    static_font_path,
)

# The composition these props drive, registered in
# remotion-subtitles/src/Root.tsx.
TIMED_TEXT_COMPOSITION = "TimedTextOverlay"

# Keys a moment must carry itself: what it says and when it says it.
# Everything else about its look is ARTWORK the project declares (AGENTS.md
# 14), so the look has no engine default: a default here would be the
# engine stating a look the project never chose (AGENTS.md 10.5).
REQUIRED_MOMENT_KEYS = ("text", "color", "start_frame", "duration_frames")

# The look a moment states for itself - size, fade, weight, shadow and
# position. Each decides what the viewer sees, so each is required and
# `generate_timed_text_overlay_props` reads it directly rather than
# completing it from a constant.
#
# Deliberately absent: `text_align`. The one shipped declaration on file
# - the Through the 4th Wall Night card
# (`tests/fixtures/night_card_project/project.yaml`, mirroring the real
# per-episode project.yaml outside this repo) - states every key above
# and omits only the alignment, and it renders today. Requiring the
# alignment would stop that project's render over one line, so the
# `"center"` default below stays until the captain either states the
# alignment on the real declaration or accepts the default as the
# series' declared look. Reported, not silently kept: this paragraph is
# the record, and a test pins that an omitted alignment still renders
# centred rather than raising.
STYLE_MOMENT_KEYS = ("font_size", "fade_in_frames", "fade_out_frames",
                     "font_weight", "text_shadow", "x", "y")


class TimedTextDeclarationError(ValueError):
    """A template's ``effect.timed_text_overlay`` declaration is malformed."""


def _require_moment_style(moment: dict, index: int) -> None:
    """A moment states its own look, or it states nothing renderable.

    Size, fade, weight, shadow and position are artwork: the project
    declares them and the engine substitutes none (AGENTS.md 10.5, 14).
    A moment omitting one raises rather than rendering in a constant
    nobody chose - the same refusal ``bookends.py`` makes on a malformed
    declaration. (``text_align`` is exempt: see ``STYLE_MOMENT_KEYS``.)
    """
    label = f"timed_text_overlay moment {index}"
    missing = [k for k in STYLE_MOMENT_KEYS if moment.get(k) is None]
    if missing:
        raise TimedTextDeclarationError(
            f"{label} omits its look ({', '.join(missing)}); size, fade, "
            f"weight, shadow and position are artwork the project "
            f"declares, and the engine states none of them (AGENTS.md "
            f"10.5, 14)")


def generate_timed_text_overlay_props(
    template_effect: dict[str, Any],
    fps: int = 30,
    *,
    width: int,
    height: int,
    duration_in_frames: int = 1800,
    safe_area=None,
) -> dict[str, Any] | None:
    """Convert a template's ``timed_text_overlay`` declaration to Remotion props.

    `width`/`height` are the DECLARED delivery frame and have no default:
    a default is what let these render vertical onto a landscape timeline
    (project 001's lighter central band). The caller states the frame -
    see library/tools/delivery_format.py.

    Returns ``None`` if the template does not declare an overlay, so
    callers can skip the render entirely.
    """
    declaration = template_effect.get("timed_text_overlay")
    if not declaration:
        return None

    raw_moments = declaration.get("moments")
    if not raw_moments:
        return None

    # The same refusals `plan_timed_text_segments` makes, so a direct
    # caller gets a named missing key rather than a KeyError on the
    # reads below. The look is required here too: these reads index the
    # style keys directly, and an omission must fail loudly rather than
    # render in a constant (AGENTS.md 10.5).
    for index, moment in enumerate(raw_moments):
        if not isinstance(moment, dict):
            raise TimedTextDeclarationError(
                f"timed_text_overlay moment {index} must be a mapping, "
                f"got {type(moment).__name__}")
        _require_moment_style(moment, index)
    _validate_font(declaration)

    moments = [
        {
            "text": m["text"],
            "color": m["color"],
            "fontSize": m["font_size"],
            "startFrame": m["start_frame"],
            "durationFrames": m["duration_frames"],
            "x": m["x"],
            "y": m["y"],
            "fadeInFrames": m["fade_in_frames"],
            "fadeOutFrames": m["fade_out_frames"],
            "fontWeight": m["font_weight"],
            # The one surviving engine default: `text_align` is
            # load-bearing for the shipped Night card, which omits it
            # (see STYLE_MOMENT_KEYS). Reported there, not silently kept.
            "textAlign": m.get("text_align", "center"),
            "textShadow": m["text_shadow"],
        }
        for m in raw_moments
    ]

    safe_area = safe_area or resolve_safe_area(width=width, height=height)
    props = {
        "moments": moments,
        "fontFamily": declaration["font_family"],
        "fps": fps,
        "width": width,
        "height": height,
        # A card is centred on its own (x, y) and wraps against the frame.
        # The frame is the wrong bound: the outer 90-320px of it belongs
        # to the platform's interface. `TimedTextOverlay` wraps against
        # these insets instead. See library/tools/safe_area.py.
        "safeArea": safe_area.as_props(),
        "durationInFrames": duration_in_frames,
    }
    # The exact file the family is to be drawn from, when the project
    # carries its own typeface. `TimedTextOverlay` blocks the render on
    # this face and throws if it cannot load it, which is what stops a
    # per-series typeface substituting silently - see
    # library/tools/render_fonts.py.
    font_file = declaration.get("font_file")
    if font_file:
        props["fontFile"] = static_font_path(str(font_file))
    return props


def _resolve_moment(moment: dict, index: int, spine_structure: list | None,
                    fps: int) -> dict:
    """Fill in a moment's absolute ``start_frame``/``duration_frames``.

    Two ways to say WHEN, and a moment must use exactly one:

    ``block`` + ``duration_seconds``
        Anchored to the spine, which is the shape
        ``docs/ASSET_LIBRARY_PLAN.md`` section 5 requires: "It is timed
        from the spine. No absolute frames, no assumed total." ``anchor``
        picks the block's start (default) or end, ``offset_seconds``
        shifts from there. Re-cut the edit and the moment moves with the
        block it belongs to.

    ``start_frame`` + ``duration_frames``
        Absolute TIMELINE frames, for a caller that genuinely means a
        position in the finished edit. Still bounded by the real spine
        length by :func:`_validate_moment`, which is what the 4th Wall
        card's frame numbers - baked to a 60.000s cut that no longer
        existed - would have failed on.
    """
    label = f"timed_text_overlay moment {index}"
    if not isinstance(moment, dict):
        raise TimedTextDeclarationError(
            f"{label} must be a mapping, got {type(moment).__name__}")

    anchored = moment.get("block") is not None
    absolute = moment.get("start_frame") is not None
    if anchored and absolute:
        raise TimedTextDeclarationError(
            f"{label} declares both block and start_frame; a moment is "
            f"timed from the spine or from the timeline, not both")
    if not anchored:
        if not absolute:
            raise TimedTextDeclarationError(
                f"{label} says when it appears neither way: give it a "
                f"`block` (+ optional offset_seconds) or a `start_frame`")
        return dict(moment)

    if spine_structure is None:
        raise TimedTextDeclarationError(
            f"{label} anchors to spine block {moment['block']!r}, but no "
            f"spine was supplied to resolve it against")

    position = moment["block"]
    matches = [b for b in spine_structure if b.get("position") == position]
    if not matches:
        raise TimedTextDeclarationError(
            f"{label} anchors to spine block {position!r}, which is not in "
            f"the edit. Blocks present: "
            f"{[b.get('position') for b in spine_structure]}")
    block = matches[0]

    anchor = moment.get("anchor", "start")
    if anchor not in ("start", "end"):
        raise TimedTextDeclarationError(
            f"{label} has anchor={anchor!r}; a moment anchors to the "
            f"'start' or the 'end' of its block")
    base = block["timeline_start" if anchor == "start" else "timeline_end"]

    duration_seconds = moment.get("duration_seconds")
    if duration_seconds is None:
        raise TimedTextDeclarationError(
            f"{label} anchors to a spine block but has no "
            f"duration_seconds; a spine-timed moment is measured in "
            f"seconds, not in frames of some other cut")
    if (not isinstance(duration_seconds, (int, float))
            or isinstance(duration_seconds, bool) or duration_seconds <= 0):
        raise TimedTextDeclarationError(
            f"{label} has duration_seconds={duration_seconds!r}; a moment "
            f"that lasts no time appears in no frame")

    offset_seconds = moment.get("offset_seconds", 0.0)
    if (not isinstance(offset_seconds, (int, float))
            or isinstance(offset_seconds, bool)):
        raise TimedTextDeclarationError(
            f"{label} has offset_seconds={offset_seconds!r}; the offset "
            f"from the block's anchor is a number of seconds")

    start = base + offset_seconds
    if start < 0:
        raise TimedTextDeclarationError(
            f"{label} offsets {offset_seconds}s from the {anchor} of block "
            f"{position!r} at {base}s, which lands at {start}s - before "
            f"the first frame of the edit")

    resolved = dict(moment)
    resolved["start_frame"] = int(round(start * fps))
    resolved["duration_frames"] = max(1, int(round(duration_seconds * fps)))
    return resolved


def _validate_font(declaration: dict) -> None:
    """A declared family must really be the one that draws the glyphs.

    `docs/ASSET_LIBRARY_PLAN.md` section 5, property 1: "Its typeface is
    bundled, with the licence recorded. An unbundled family renders on
    the machine that added it and substitutes silently everywhere else."
    A per-series typeface is not bundled and must not be - it lives with
    its project - so the declaration names the staged FILE and the
    composition loads that file or throws.

    The failure this refuses is silent: Chromium substitutes, the frames
    are valid pictures of the right size, and the card ships in the wrong
    typeface with nothing to notice.
    """
    family = declaration.get("font_family")
    if family is None:
        raise TimedTextDeclarationError(
            "effect.timed_text_overlay states no font_family; the "
            "typeface a card is drawn in is artwork the project declares "
            "(AGENTS.md 10.5, 14), and the engine substitutes none")
    if not isinstance(family, str) or not family.strip():
        raise TimedTextDeclarationError(
            f"effect.timed_text_overlay has font_family={family!r}; a "
            f"family is a non-empty CSS font name")

    font_file = declaration.get("font_file")
    if font_file is not None and (
            not isinstance(font_file, str) or not font_file.strip()):
        raise TimedTextDeclarationError(
            f"effect.timed_text_overlay has font_file={font_file!r}; a "
            f"font file is a filename staged into Remotion's public/brand/")

    if not font_is_deliverable(family, font_file):
        raise TimedTextDeclarationError(
            f"effect.timed_text_overlay names font_family={family!r}, "
            f"which this repository does not bundle and has not accepted "
            f"as a system font. A per-series typeface lives with its "
            f"project: put the file in <project>/brand_assets/ and name "
            f"it with `font_file`, or the render substitutes Chromium's "
            f"fallback sans and nothing downstream can tell. See "
            f"library/tools/render_fonts.py. Primary family read as "
            f"{primary_family(family)!r}.")


def _validate_moment(moment: dict, index: int,
                     timeline_frames: int | None,
                     safe_area=None) -> None:
    """Everything about one moment that would render a wrong picture."""
    label = f"timed_text_overlay moment {index}"

    if not isinstance(moment, dict):
        raise TimedTextDeclarationError(
            f"{label} must be a mapping, got {type(moment).__name__}")

    missing = [k for k in REQUIRED_MOMENT_KEYS if moment.get(k) is None]
    if missing:
        raise TimedTextDeclarationError(
            f"{label} is missing {missing}; a moment with no {missing[0]} "
            f"has nothing to draw or no time to draw it at")

    # The look, before the values: a moment that omits its size, fade,
    # weight, shadow or position raises here, naming the key, rather
    # than rendering downstream in an engine constant.
    _require_moment_style(moment, index)

    if not str(moment["text"]).strip():
        raise TimedTextDeclarationError(
            f"{label} has empty text; declare the moment or remove it")

    size = moment["font_size"]
    if (not isinstance(size, (int, float)) or isinstance(size, bool)
            or size <= 0):
        raise TimedTextDeclarationError(
            f"{label} has font_size={size!r}; a moment's size is a "
            f"positive number of pixels the declaration states")

    if (not isinstance(moment["text_shadow"], str)):
        raise TimedTextDeclarationError(
            f"{label} has text_shadow={moment['text_shadow']!r}; the "
            f"shadow is a CSS text-shadow string the declaration states "
            f"(`none` draws no shadow)")

    start = moment["start_frame"]
    duration = moment["duration_frames"]
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        raise TimedTextDeclarationError(
            f"{label} has start_frame={start!r}; timeline frames are "
            f"non-negative integers")
    if (not isinstance(duration, int) or isinstance(duration, bool)
            or duration < 1):
        raise TimedTextDeclarationError(
            f"{label} has duration_frames={duration!r}; a moment that "
            f"lasts no frames appears in no frame")

    fade_in = moment["fade_in_frames"]
    fade_out = moment["fade_out_frames"]
    for name, value in (("fade_in_frames", fade_in),
                        ("fade_out_frames", fade_out)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TimedTextDeclarationError(
                f"{label} has {name}={value!r}; fades are non-negative "
                f"integer frame counts")
    if fade_in + fade_out > duration:
        raise TimedTextDeclarationError(
            f"{label} fades for {fade_in}+{fade_out} frames inside a "
            f"{duration}-frame moment, so it never reaches full opacity")

    for axis in ("x", "y"):
        value = moment[axis]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TimedTextDeclarationError(
                f"{label} has {axis}={value!r}; position is a number "
                f"between 0 and 1")
        if not 0.0 <= float(value) <= 1.0:
            raise TimedTextDeclarationError(
                f"{label} has {axis}={value}, which is outside the frame; "
                f"positions are normalised 0-1")

    # Inside the frame is not enough. The outer band of the frame is the
    # platform's own interface - its caption, its handle, its audio bar,
    # its like/comment/share rail - and a card under it is a card the
    # viewer never reads. Until `library/tools/safe_area.py` existed
    # there was nothing to resolve this against, which is why the module
    # docstring said the geometry was normalised against the whole frame.
    if safe_area is not None:
        x = float(moment["x"])
        y = float(moment["y"])
        if not safe_area.contains_normalised(x, y):
            raise TimedTextDeclarationError(
                f"{label} is centred at x={x}, y={y} - pixel "
                f"({round(x * safe_area.width)}, "
                f"{round(y * safe_area.height)}) of a "
                f"{safe_area.width}x{safe_area.height} frame - which is "
                f"inside the platform's own interface. The safe area is "
                f"{safe_area.left}px in from the left, {safe_area.right}px "
                f"from the right, {safe_area.top}px from the top and "
                f"{safe_area.bottom}px from the bottom "
                f"(profile {safe_area.profile!r}). One master serves "
                f"Reels, TikTok and Shorts and obeys the strictest of "
                f"them - captain's ruling, 2026-08-25. See "
                f"library/tools/safe_area.py.")

    if timeline_frames is not None and start + duration > timeline_frames:
        raise TimedTextDeclarationError(
            f"{label} runs to frame {start + duration} but the edit is "
            f"{timeline_frames} frames long; a moment past the last frame "
            f"reaches no picture. Frame numbers are TIMELINE frames - see "
            f"library/tools/timed_text_overlay.py")


def plan_timed_text_segments(
    template_effect: dict[str, Any],
    fps: int = 30,
    *,
    width: int,
    height: int,
    spine_structure: list | None = None,
    project_folder: str | None = None,
) -> list[dict[str, Any]]:
    """Group a declaration's moments into renderable, placeable segments.

    Returns ``[]`` for a template that declares nothing. Otherwise one
    entry per cluster of moments whose spans touch, each carrying the
    Remotion props for that cluster - with every moment's ``startFrame``
    rebased to the segment - and the timeline position the renderer
    places it at.

    ``spine_structure`` is ``audio_spine["structure"]``. It resolves
    spine-anchored moments and bounds absolute ones by the real length of
    the edit; without it, only absolute moments can be planned and
    nothing bounds them.

    Segments are returned in timeline order and are guaranteed not to
    overlap, which is what lets them share one video track.
    """
    declaration = template_effect.get("timed_text_overlay")
    if not declaration:
        return []
    if not isinstance(declaration, dict):
        raise TimedTextDeclarationError(
            f"effect.timed_text_overlay must be a mapping, got "
            f"{type(declaration).__name__}")
    if not declaration.get("moments"):
        return []
    if not isinstance(declaration["moments"], list):
        raise TimedTextDeclarationError(
            f"effect.timed_text_overlay.moments must be a list, got "
            f"{type(declaration['moments']).__name__}")

    _validate_font(declaration)

    timeline_frames = None
    if spine_structure:
        timeline_end = max(
            (b.get("timeline_end", 0) for b in spine_structure), default=0)
        timeline_frames = int(round(timeline_end * fps))

    safe_area = resolve_safe_area(project_folder, width=width, height=height)

    resolved = []
    for index, moment in enumerate(declaration["moments"]):
        moment = _resolve_moment(moment, index, spine_structure, fps)
        _validate_moment(moment, index, timeline_frames, safe_area)
        resolved.append(moment)

    # Only now, with every moment known well-formed, is it safe to build
    # props: the generator indexes required keys directly by design.
    props = generate_timed_text_overlay_props(
        {"timed_text_overlay": {**declaration, "moments": resolved}},
        fps=fps, width=width, height=height, safe_area=safe_area)

    ordered = sorted(props["moments"], key=lambda m: m["startFrame"])

    clusters: list[list[dict]] = []
    for moment in ordered:
        if clusters and moment["startFrame"] <= _cluster_end(clusters[-1]):
            clusters[-1].append(moment)
        else:
            clusters.append([moment])

    font_props = {"fontFamily": props["fontFamily"]}
    if "fontFile" in props:
        font_props["fontFile"] = props["fontFile"]

    segments = []
    for index, cluster in enumerate(clusters):
        start_frame = cluster[0]["startFrame"]
        end_frame = _cluster_end(cluster)
        total_frames = end_frame - start_frame
        segments.append({
            "index": index,
            "timeline_start": round(start_frame / fps, 3),
            "timeline_end": round(end_frame / fps, 3),
            "total_frames": total_frames,
            "moment_count": len(cluster),
            "props": {
                "moments": [
                    {**m, "startFrame": m["startFrame"] - start_frame}
                    for m in cluster
                ],
                **font_props,
                "fps": fps,
                "width": width,
                "height": height,
                "safeArea": props["safeArea"],
                "durationInFrames": total_frames,
            },
        })
    return segments


def _cluster_end(cluster: list[dict]) -> int:
    """Last frame + 1 of the whole cluster, which may not be its last moment."""
    return max(m["startFrame"] + m["durationFrames"] for m in cluster)


# ─────────────────────────────────────────────────────────
# Where the declaration comes from
# ─────────────────────────────────────────────────────────

def resolve_declaration(brand_effect: dict[str, Any] | None,
                        project_folder: str | None) -> dict[str, Any]:
    """The effect dict 4.06 plans from: the PROJECT's card wins.

    A timed text moment is copy the viewer reads on screen, and
    `docs/ASSET_LIBRARY_PLAN.md` section 3 - ratified 2026-08-20 - says
    that is ARTWORK: "A brand template may name general components and
    set per-series parameters ... It may not contain artwork - copy the
    viewer reads on screen ... Artwork is a project asset and is declared
    by reference, never inlined."

    The bookend route already honours that: `content.bookends` names a
    `source:` and the .tsx lives with the project. Timed text had no such
    route, because a line of copy has no file to point at - so the ONLY
    place a card could be written was a brand template, which is the
    corner the 4th Wall end card died in. This is the missing half: a
    project declares its own `effect.timed_text_overlay` in its
    `project.yaml`, and the engine stays series-neutral.

    Precedence is project over template, which is the rule
    `delivery_format_name` already uses for the same reason - a project
    may differ from its series without forking the series' template.
    The whole slot is replaced rather than merged key by key: half a card
    from each of two sources is a card nobody designed.

        # <project>/project.yaml
        effect:
          timed_text_overlay:
            font_family: "Nanum Pen Script"
            font_file: "NanumPenScript-Regular.ttf"
            moments: [...]

    A `project.yaml` that declares nothing leaves the template's slot
    exactly as it was, so this is invisible to every existing project.
    A malformed one raises here rather than at render time, because a
    declaration that renders nothing is indistinguishable from no
    declaration at all.
    """
    effect = dict(brand_effect or {})
    declaration = _project_declaration(project_folder)
    if declaration is None:
        return effect
    effect["timed_text_overlay"] = declaration
    return effect


def _project_declaration(project_folder: str | None) -> dict | None:
    """`effect.timed_text_overlay` out of a project.yaml, or None."""
    if not project_folder:
        return None
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return None

    import yaml

    with open(project_yaml, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if not isinstance(config, dict):
        raise TimedTextDeclarationError(
            f"{project_yaml} does not parse as a mapping")

    effect = config.get("effect")
    if effect is None:
        return None
    if not isinstance(effect, dict):
        raise TimedTextDeclarationError(
            f"{project_yaml} has an `effect:` block that is not a mapping, "
            f"got {type(effect).__name__}")

    declaration = effect.get("timed_text_overlay")
    if declaration is None:
        return None
    if not isinstance(declaration, dict):
        raise TimedTextDeclarationError(
            f"{project_yaml} declares effect.timed_text_overlay as "
            f"{type(declaration).__name__}, which is not a declaration")
    return declaration
