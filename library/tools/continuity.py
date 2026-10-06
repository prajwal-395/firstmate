"""Screen-direction continuity: find cuts that may cross the line.

The 180-degree line is the first rule of continuity editing: the subject
stays on the same side of the frame across a cut, or the viewer loses
where they are. The measured signal is the subject's horizontal centre
(`face_presence.face_center_x` in the temporal index), read through
`subject_framing.subject_center_reading` - the same reader every
consumer has, with its measured/unmeasurable distinction kept intact.

What this module flags
----------------------
At a block boundary where BOTH sides carry a measurable subject
position, a jump from one side of the frame to the other is recorded as
a potential line cross. The flag is deliberately conservative in what
it measures and honest about what it cannot know: a flip is also what
a shot/reverse-shot dialogue cut looks like, so the finding says
"potential" and carries both measured positions - the reviewer
confirms whether the flip was intentional.

What it does not flag
--------------------
- A boundary with an unmeasurable subject position on either side: no
  measurement, no finding (the same fail-closed rule as dead sections).
- A subject at centre on either side: centred is not a side, so no flip.
- A cut between two different sources where the scene may have changed:
  still measured and still flagged if the flip is there - the positions
  are the fact, the scene change is the reviewer's to weigh.

This module imports nothing from steps or processes: it reads the
measurements a state carries, the same reader every consumer has.
"""
from __future__ import annotations

from library.tools.subject_framing import CENTRE_DEADBAND, subject_center_reading


def _side(position: float) -> str:
    """Which side of the frame a measured centre sits on."""
    if position < 0.5 - CENTRE_DEADBAND:
        return "left"
    if position > 0.5 + CENTRE_DEADBAND:
        return "right"
    return "centre"


def _block_subject_reading(block: dict, temporal_index: dict):
    """The subject's measured screen side and centre for a block.

    Returns (side, position), ("centre", None) when the subject is
    MEASURED centred - a centred subject is not a side, but it is a
    measurement, not a shrug - or (None, None) when the block carries
    no clip, the clip has no temporal index, or the position is
    unmeasurable.
    """
    clip_id = str(block.get("clip_id", ""))
    if not clip_id:
        return None, None
    face_presence = temporal_index.get("face_presence") or {}
    try:
        source_in = float(block.get("source_start", 0.0))
        source_out = float(block.get("source_end", 0.0))
    except (TypeError, ValueError):
        return None, None
    reading = subject_center_reading(face_presence, source_in, source_out)
    if reading.position is None:
        if reading.status == "centred":
            return "centre", None
        return None, None
    return _side(reading.position), round(reading.position, 4)


def direction_cell_for_cut(outgoing: dict, incoming: dict,
                            outgoing_idx: dict, incoming_idx: dict) -> str:
    """The screen-direction cell for one cut, as bridge context.

    Pure measurement, no conclusion: a flip reads
    `"left -> right (potential line cross)"`, a held side reads
    `"left -> left"`, a centred subject on either side reads with
    `"centre"` (centred is not a side, so never a flip), and an
    unmeasurable position on either side reads `"unmeasured"` - the
    absence of a measurement, never a held side.
    """
    outgoing_side, _ = _block_subject_reading(
        outgoing, outgoing_idx or {})
    incoming_side, _ = _block_subject_reading(
        incoming, incoming_idx or {})
    if outgoing_side is None or incoming_side is None:
        return "unmeasured"
    cell = f"{outgoing_side} -> {incoming_side}"
    if (outgoing_side != incoming_side
            and outgoing_side != "centre"
            and incoming_side != "centre"):
        cell += " (potential line cross)"
    return cell


def direction_cells_for_cuts(spine_blocks: list,
                             temporal_indices: dict) -> dict:
    """The screen-direction cell per cut, keyed by cut_point_position.

    The same readings `find_screen_direction_issues` flags, rendered
    for every boundary - not just the flips - so a planner sees held
    sides and unmeasured cuts too, beside the cut it is choosing for.
    Keys are `str(position)`; the end of the piece is not a cut and
    gets no cell.
    """
    cells = {}
    blocks = [b for b in spine_blocks or [] if isinstance(b, dict)]
    for outgoing, incoming in zip(blocks, blocks[1:]):
        outgoing_idx = (temporal_indices or {}).get(
            str(outgoing.get("clip_id", "")), {})
        incoming_idx = (temporal_indices or {}).get(
            str(incoming.get("clip_id", "")), {})
        cells[str(incoming.get("position"))] = direction_cell_for_cut(
            outgoing, incoming, outgoing_idx, incoming_idx)
    return cells


def find_screen_direction_issues(spine_blocks: list,
                                 temporal_indices: dict) -> list:
    """Find cuts where the subject may cross the line.

    Args:
        spine_blocks: the spine blocks, each with clip_id,
            source_start/source_end, timeline_start/timeline_end.
        temporal_indices: {clip_id: temporal index} - the project's
            measured per-clip face tracks.

    Returns a list of potential line-cross records, one per boundary
    where both sides carry a measurable subject position on opposite
    sides of the frame, each with:
        - cut_point_position: the incoming block's position
        - timeline_position: where the boundary sits on the timeline
        - outgoing_clip_id, incoming_clip_id: the sources it joins
        - outgoing_side, incoming_side: the measured sides
        - outgoing_position, incoming_position: the measured centres
        - reason: what was measured and what the reviewer confirms
    """
    issues = []

    blocks = [b for b in spine_blocks or [] if isinstance(b, dict)]
    for outgoing, incoming in zip(blocks, blocks[1:]):
        outgoing_idx = (temporal_indices or {}).get(
            str(outgoing.get("clip_id", "")), {})
        incoming_idx = (temporal_indices or {}).get(
            str(incoming.get("clip_id", "")), {})
        outgoing_side, outgoing_pos = _block_subject_reading(
            outgoing, outgoing_idx)
        incoming_side, incoming_pos = _block_subject_reading(
            incoming, incoming_idx)
        if outgoing_side is None or incoming_side is None:
            continue
        if outgoing_side == "centre" or incoming_side == "centre":
            continue
        if outgoing_side == incoming_side:
            continue

        try:
            timeline_position = round(
                float(incoming.get("timeline_start", 0.0)), 3)
        except (TypeError, ValueError):
            continue

        issues.append({
            "cut_point_position": incoming.get("position"),
            "timeline_position": timeline_position,
            "outgoing_clip_id": str(outgoing.get("clip_id", "")),
            "incoming_clip_id": str(incoming.get("clip_id", "")),
            "outgoing_side": outgoing_side,
            "incoming_side": incoming_side,
            "outgoing_position": outgoing_pos,
            "incoming_position": incoming_pos,
            "reason": (
                f"subject jumps from {outgoing_side} to {incoming_side} "
                f"at this cut - a potential line cross; the reviewer "
                "confirms whether it is an intentional shot/reverse-shot"
            ),
        })

    return issues
