"""Pacing windows: the plan's numeric or feel target and what was delivered.

Rung 7 (K1, PA3.2/PA2.2): a numeric average shot length rides the spine
as `pacing` windows with `asl_seconds`; a feel request such as "faster
cuts as it builds" carries `feel` in the same addressed window. Step
2.05's post-bridge validates either shape. This module measures the
BUILT picture beside them: the visible cuts in each
window (every V1 and V2 clip edge - a b-roll covering speech IS the
shot the viewer sees) and the delivered average shot length.

Report-only, by design: a target is a target, and failing a build on
a missed one would be a gate failing correct output (AGENTS.md 10.4).
`compile_manifest` records the rows as `pacing_report` and prints
them; no verdict, no threshold - the delta is the finding, and the
reader (the run log, the reviewer) judges it.
"""

from library.tools.frame_utils import seconds_to_frame


def _clip_edge_frames(clip: dict, fps: float) -> tuple:
    """(in_frame, out_frame) for a compiled picture clip."""
    try:
        start = int(clip["timeline_in_frame"])
    except (KeyError, TypeError, ValueError):
        start = seconds_to_frame(float(clip.get("timeline_in", 0.0)), fps)
    try:
        end = int(clip["timeline_out_frame"])
    except (KeyError, TypeError, ValueError):
        end = seconds_to_frame(float(clip.get("timeline_out", 0.0)), fps)
    return start, end


def measure_pacing(spine_blocks: list, v1_clips: list, v2_clips: list,
                   pacing: list, fps: float) -> list:
    """One row per planned window: target ASL beside delivered ASL.

    The window spans its start block's start to its end block's end.
    Every picture edge strictly inside the window is a cut; the window
    holds cuts + 1 shots, so delivered ASL is the span over that
    count. Windows naming blocks the spine does not carry are skipped
    with the reason on the row - measured, never refused, because the
    picture is already built and the report is what says the target
    missed its address.
    """
    by_position = {}
    for block in spine_blocks or []:
        if isinstance(block, dict):
            by_position.setdefault(str(block.get("position")), block)
    edges = []
    for clip in list(v1_clips or []) + list(v2_clips or []):
        if not isinstance(clip, dict):
            continue
        start, end = _clip_edge_frames(clip, fps)
        if end > start:
            edges.append((start, end))

    report = []
    for window in pacing or []:
        start_block = by_position.get(str(window.get("start_block")))
        end_block = by_position.get(str(window.get("end_block")))
        target = window.get("asl_seconds")
        if start_block is None or end_block is None:
            report.append({
                "start_block": window.get("start_block"),
                "end_block": window.get("end_block"),
                "target_asl_seconds": target,
                "target_feel": window.get("feel"),
                "delivered_asl_seconds": None,
                "cuts": None,
                "window_seconds": None,
                "note": "window names a block the built spine does "
                        "not carry - nothing measured",
            })
            continue
        try:
            win_start = int(start_block["timeline_start_frame"])
        except (KeyError, TypeError, ValueError):
            win_start = seconds_to_frame(
                float(start_block.get("timeline_start", 0.0)), fps)
        try:
            win_end = int(end_block["timeline_end_frame"])
        except (KeyError, TypeError, ValueError):
            win_end = seconds_to_frame(
                float(end_block.get("timeline_end", 0.0)), fps)
        # One cut per frame: abutting clips share the edge frame,
        # and counting both sides would double every straight cut.
        interior = {edge for start, end in edges
                    for edge in (start, end)
                    if win_start < edge < win_end}
        cuts = len(interior)
        # Each cut inside the window opens one more shot: the window
        # holds cuts + 1 of them.
        span_seconds = max(0.0, (win_end - win_start) / float(fps))
        delivered = (span_seconds / (cuts + 1)) if span_seconds else 0.0
        report.append({
            "start_block": window.get("start_block"),
            "end_block": window.get("end_block"),
            "target_asl_seconds": target,
            "target_feel": window.get("feel"),
            "delivered_asl_seconds": round(delivered, 3),
            "cuts": cuts,
            "window_seconds": round(span_seconds, 3),
        })
    return report
