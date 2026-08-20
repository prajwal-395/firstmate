"""Timed text moments: one enumeration, declared per template, read by 4.06.

A brand template declares ``effect.timed_text_overlay`` and gets N text
moments composited over the finished picture - an episode number, a
chapter title, a date stamp. A template that declares nothing gets
nothing, the same per-template opt-in shape as ``effect.motion_accents``
(P3.1/Q3) and ``content.bookends`` (Q7, 2026-08-16).

What a declaration looks like::

    effect:
      timed_text_overlay:
        font_family: Montserrat
        moments:
          - text: "EPISODE 001"
            color: "#D4A34A"
            font_size: 64
            block: 1                # spine block position
            anchor: start           # or `end`; default `start`
            offset_seconds: 0.5
            duration_seconds: 2.0
            y: 0.35

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
must clear the bars states its own ``y``.

How a declaration reaches the picture, in order - this chain IS the
capability, and until 2026-08-20 it stopped at step 1:

1. ``generate_timed_text_overlay_props`` (here) turns the declaration
   into Remotion props for the ``TimedTextOverlay`` composition.
2. ``plan_timed_text_segments`` (here) groups the moments into
   non-overlapping SEGMENTS and rebases each moment's frame numbers
   against the segment it lands in, so nothing renders transparent
   frames between two moments a minute apart.
3. ``library/tools/timed_text_render.py`` renders one ProRes 4444 file
   per segment, called by ``render_motion_graphics`` (4.06).
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

from typing import Any

# The composition these props drive, registered in
# remotion-subtitles/src/Root.tsx.
TIMED_TEXT_COMPOSITION = "TimedTextOverlay"

# Keys a moment must carry itself. Everything else has a default, because
# a default cannot be wrong in a way that renders the wrong picture.
REQUIRED_MOMENT_KEYS = ("text", "color", "start_frame", "duration_frames")


class TimedTextDeclarationError(ValueError):
    """A template's ``effect.timed_text_overlay`` declaration is malformed."""


def generate_timed_text_overlay_props(
    template_effect: dict[str, Any],
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    duration_in_frames: int = 1800,
) -> dict[str, Any] | None:
    """Convert a template's ``timed_text_overlay`` declaration to Remotion props.

    Returns ``None`` if the template does not declare an overlay, so
    callers can skip the render entirely.
    """
    declaration = template_effect.get("timed_text_overlay")
    if not declaration:
        return None

    raw_moments = declaration.get("moments")
    if not raw_moments:
        return None

    moments = [
        {
            "text": m["text"],
            "color": m["color"],
            "fontSize": m.get("font_size", 42),
            "startFrame": m["start_frame"],
            "durationFrames": m["duration_frames"],
            "x": m.get("x", 0.5),
            "y": m.get("y", 0.5),
            "fadeInFrames": m.get("fade_in_frames", 10),
            "fadeOutFrames": m.get("fade_out_frames", 10),
            "fontWeight": m.get("font_weight", 400),
            "textAlign": m.get("text_align", "center"),
            "textShadow": m.get(
                "text_shadow", "0px 4px 12px rgba(0,0,0,0.6)"),
        }
        for m in raw_moments
    ]

    return {
        "moments": moments,
        "fontFamily": declaration.get("font_family", "Helvetica"),
        "fps": fps,
        "width": width,
        "height": height,
        "durationInFrames": duration_in_frames,
    }


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


def _validate_moment(moment: dict, index: int,
                     timeline_frames: int | None) -> None:
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

    if not str(moment["text"]).strip():
        raise TimedTextDeclarationError(
            f"{label} has empty text; declare the moment or remove it")

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

    fade_in = moment.get("fade_in_frames", 10)
    fade_out = moment.get("fade_out_frames", 10)
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
        value = moment.get(axis, 0.5)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TimedTextDeclarationError(
                f"{label} has {axis}={value!r}; position is a number "
                f"between 0 and 1")
        if not 0.0 <= float(value) <= 1.0:
            raise TimedTextDeclarationError(
                f"{label} has {axis}={value}, which is outside the frame; "
                f"positions are normalised 0-1")

    if timeline_frames is not None and start + duration > timeline_frames:
        raise TimedTextDeclarationError(
            f"{label} runs to frame {start + duration} but the edit is "
            f"{timeline_frames} frames long; a moment past the last frame "
            f"reaches no picture. Frame numbers are TIMELINE frames - see "
            f"library/tools/timed_text_overlay.py")


def plan_timed_text_segments(
    template_effect: dict[str, Any],
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    spine_structure: list | None = None,
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

    timeline_frames = None
    if spine_structure:
        timeline_end = max(
            (b.get("timeline_end", 0) for b in spine_structure), default=0)
        timeline_frames = int(round(timeline_end * fps))

    resolved = []
    for index, moment in enumerate(declaration["moments"]):
        moment = _resolve_moment(moment, index, spine_structure, fps)
        _validate_moment(moment, index, timeline_frames)
        resolved.append(moment)

    # Only now, with every moment known well-formed, is it safe to build
    # props: the generator indexes required keys directly by design.
    props = generate_timed_text_overlay_props(
        {"timed_text_overlay": {**declaration, "moments": resolved}},
        fps=fps, width=width, height=height)

    ordered = sorted(props["moments"], key=lambda m: m["startFrame"])

    clusters: list[list[dict]] = []
    for moment in ordered:
        if clusters and moment["startFrame"] <= _cluster_end(clusters[-1]):
            clusters[-1].append(moment)
        else:
            clusters.append([moment])

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
                "fontFamily": props["fontFamily"],
                "fps": fps,
                "width": width,
                "height": height,
                "durationInFrames": total_frames,
            },
        })
    return segments


def _cluster_end(cluster: list[dict]) -> int:
    """Last frame + 1 of the whole cluster, which may not be its last moment."""
    return max(m["startFrame"] + m["durationFrames"] for m in cluster)
