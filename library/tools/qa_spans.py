"""The windows a rendered file is graded OVER.

One enumeration of the measurement spans, shared by the step that
validates a render (`step_6_02_validate_output`) and the capability
that verifies a delivered export (`library/tools/export_verification.py`).

Why it moved
------------
The span builders lived inside the validate step's bridge, which made
them reachable only from that step.  A second reader - the export
verification - needs the SAME windows: two definitions of "the
graphical element" would be two verdicts on one file, and the export
would grade an overlay the build's own gate never looked at.

So the builders live here, and the step imports them.  The behaviour
is unchanged: the same functions, the same arguments, the same spans.

What a span is
--------------
A `render_qa` span names a WINDOW on the timeline, the source media
that plays under it, and the artefact that was drawn over it.  The
render checks compare the two: the window in the export against the
window in the untreated source.  A span that cannot be built is
SKIPPED, never faked - a check that grades a window nobody can name is
grading nothing.
"""

from __future__ import annotations

#: The manifest keys carrying a rendered overlay, and the fps each track
#: declares its `source_in_frame` in. Kept in step with
#: `step_5_04_compile_manifest.OVERLAY_TRACKS`, which is the enumeration
#: of what the manifest may carry: an overlay track this list forgets is
#: an overlay whose ink reads as picture, which is the defect
#: `measure_frame_occupancy` reopened three times.
OVERLAY_MANIFEST_KEYS = ("subtitle_overlay", "motion_graphics_overlay",
                         "timed_text_overlay")


def clip_at(assembly_manifest: dict, timeline_time: float):
    """The picture clip playing at `timeline_time`, or None.

    V1 is asked first, which keeps every existing placement exactly
    where it was: where a cutaway covers a speech block both tracks
    carry a clip, and the effect still lands on the V1 clip underneath.
    """
    for track in ("V1", "V2"):
        for clip in assembly_manifest.get("tracks", {}).get(track, {}).get("clips", []):
            tin = float(clip.get("timeline_in", 0.0) or 0.0)
            tout = float(clip.get("timeline_out", 0.0) or 0.0)
            if tin <= timeline_time < tout:
                return clip
    return None


def source_range_for(clip: dict, tl_start: float, tl_end: float):
    """(source_start, source_end) inside `clip`'s source for a timeline window.

    Linear interpolation within the clip's own timeline range, so a
    VFX window that covers part of a clip maps to the part of the
    source that plays there.  None bounds when the clip carries no
    readable source range.
    """
    tin = clip.get("timeline_in")
    tout = clip.get("timeline_out")
    sin = clip.get("source_in")
    sout = clip.get("source_out")
    if tin is None or tout is None or sin is None or sout is None:
        return None, None
    try:
        tin, tout, sin, sout = float(tin), float(tout), float(sin), float(sout)
    except (TypeError, ValueError):
        return None, None
    if tout <= tin:
        return sin, sout
    span = tout - tin
    src_start = sin + (tl_start - tin) / span * (sout - sin)
    src_end = sin + (tl_end - tin) / span * (sout - sin)
    return src_start, src_end


def transition_spans(assembly_manifest: dict):
    """One `render_qa.TransitionSpan` per drawn native transition.

    The manifest's `native_transitions` rows carry the cut point and
    the duration in frames; the duration is converted to seconds at
    the project's own rate.  Only drawn transitions are included - a
    cut (`duration_frames` 0) draws nothing and has no blend to verify.
    """
    from library.tools.render_qa import TransitionSpan

    fps = float((assembly_manifest.get("project") or {}).get("frame_rate") or 30.0)
    spans = []
    for t in assembly_manifest.get("native_transitions", []) or []:
        cut = t.get("cut_point_timeline")
        dur_frames = t.get("duration_frames")
        if cut is None or dur_frames is None:
            continue
        try:
            cut = float(cut)
            dur_frames = float(dur_frames)
        except (TypeError, ValueError):
            continue
        if dur_frames <= 0:
            continue
        spans.append(TransitionSpan(
            transition_type=t.get("transition_type", "?"),
            cut_point_timeline=cut,
            duration_seconds=dur_frames / fps,
        ))
    return spans


def vfx_spans(assembly_manifest: dict):
    """One `render_qa.VfxSpan` per planned VFX effect.

    Each VFX entry carries its timeline window; the picture clip
    playing at that window provides the untreated source the effect was
    drawn over.  A VFX whose clip or source cannot be found is
    skipped - the check reports it as unmeasured, never as passed.
    """
    from library.tools.render_qa import VfxSpan

    spans = []
    for vfx in assembly_manifest.get("vfx", []) or []:
        ts = vfx.get("timeline_start")
        te = vfx.get("timeline_end")
        if ts is None or te is None:
            continue
        try:
            ts, te = float(ts), float(te)
        except (TypeError, ValueError):
            continue
        if te <= ts:
            continue
        clip = clip_at(assembly_manifest, ts)
        if clip is None:
            continue
        src = clip.get("source_file")
        if not src:
            continue
        src_start, src_end = source_range_for(clip, ts, te)
        if src_start is None:
            continue
        spans.append(VfxSpan(
            label=clip.get("label") or "?",
            effect_type=vfx.get("effect_type", "?"),
            timeline_start=ts, timeline_end=te,
            source_path=src,
            source_start=src_start, source_end=src_end,
        ))
    return spans


def graphics_spans(assembly_manifest: dict):
    """One `render_qa.GraphicsSpan` per placed overlay segment.

    Each overlay segment carries its timeline window and the overlay
    file; the picture clip playing at that window provides the
    untreated picture the overlay was composited over.  Only non-caption
    overlays are included - captions are judged by the caption-quality
    checks (E4), not here.
    """
    from library.tools.render_qa import GraphicsSpan

    spans = []
    for key in OVERLAY_MANIFEST_KEYS:
        if key == "subtitle_overlay":
            continue
        track = assembly_manifest.get(key) or {}
        fps = float(track.get("fps") or 0.0) or float(
            (assembly_manifest.get("project") or {}).get("frame_rate") or 30.0)
        for seg in track.get("segments") or []:
            path = seg.get("overlay_path")
            start = seg.get("timeline_start")
            end = seg.get("timeline_end")
            if not path or start is None or end is None:
                continue
            try:
                start, end = float(start), float(end)
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            clip = clip_at(assembly_manifest, start)
            if clip is None:
                continue
            src = clip.get("source_file")
            if not src:
                continue
            src_start, src_end = source_range_for(clip, start, end)
            if src_start is None:
                continue
            spans.append(GraphicsSpan(
                label=seg.get("label") or "?",
                kind=key,
                timeline_start=start, timeline_end=end,
                source_path=src,
                source_start=src_start, source_end=src_end,
                overlay_path=str(path),
                source_in=float(seg.get("source_in_frame") or 0.0) / fps,
            ))
    return spans
