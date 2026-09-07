#!/usr/bin/env python3
"""
Step 3.2 Bridge: Resolve B-Roll Creative Selections to Execution Data

Takes the LLM's creative B-roll selections (clip_id + preferred_moment +
rationale) and resolves them to execution-ready data using the temporal
event index for precision placement:
  - clip_id → source_file (from catalog)
  - preferred_moment → video_in/video_out, resolved against what the
    vision pass OBSERVED in the picture - see
    `library/tools/cutaway_window.py`.  A cutaway plays `video_only`, so
    its audio is never heard and may never choose its window.
  - Resolution comparison → needs_conform flag

Classification: Deterministic / Data Transformation
Idempotent: Yes

Input:  {
    "broll_creative": <LLM output from 3.2>,
    "clip_catalog": [{ clip_id, path, ... }],
    "semantic_analysis_documents": [{ clip_id, blocks, ... }],
    "temporal_event_indices": [{ clip_id, scene_boundaries, energy_curve, ... }],
    "timed_spine": <output from 2.6 with timeline_start/end>
}
Output: {
    "b_roll_assignments": [{ spine_block_position, source_file, video_in, video_out, ... }]
}
"""
import json
import os
import sys

from library.tools.semantic_index import build_semantic_lookup, describe_clip
from library.tools.delivery_format import resolve_delivery_format
from library.tools.cutaway_window import choose_window

def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")


def check_needs_conform(clip: dict, target_width: int, target_height: int) -> bool:
    """
    Check if a clip needs conforming to target output specs.
    True if resolution or rotation differs from target.
    """
    w = clip.get("width", 0)
    h = clip.get("height", 0)
    rotation = clip.get("rotation", 0)

    # Account for rotation: a 1920x1080 clip with -90 rotation IS portrait
    if abs(rotation) in (90, 270, -90, -270):
        # Swap width/height for rotated clips
        w, h = h, w

    if w and h and (w != target_width or h != target_height):
        return True
    return False


def find_best_segment(
    preferred_moment: str,
    clip_analysis: dict,
    temporal_index: dict,
    clip_duration: float,
    target_duration: float,
) -> tuple:
    """The (video_in, video_out) of a cutaway, chosen from the PICTURE.

    Thin wrapper over `library.tools.cutaway_window.choose_window`, kept
    because callers only want the pair.  `resolve_broll` calls the chooser
    directly so it can record WHAT chose the window.

    This function used to hold three strategies of its own, and the one
    that ran centred the window on the clip's **audio energy peak** - on
    a clip placed `video_only: True`, whose audio is never heard.
    """
    return choose_window(
        preferred_moment, clip_analysis, temporal_index,
        clip_duration, target_duration,
    ).as_tuple()


# A cutaway shorter than this is a flicker, not a shot. An interjection
# that cannot be placed in a free window at least this long is dropped
# rather than allowed to swallow a block's B-roll assignment.
MIN_INTERJECTION_SECONDS = 0.5


def _free_windows(start: float, end: float, occupied: list) -> list:
    """Sub-intervals of [start, end) not covered by any occupied range."""
    windows = [(start, end)]
    for occ_start, occ_end in sorted(occupied):
        remaining = []
        for w_start, w_end in windows:
            if occ_end <= w_start or occ_start >= w_end:
                remaining.append((w_start, w_end))
                continue
            if occ_start > w_start:
                remaining.append((w_start, occ_start))
            if occ_end < w_end:
                remaining.append((occ_end, w_end))
        windows = remaining
    return windows


def _place_without_overlap(start: float, end: float, occupied: list):
    """Largest free sub-window of the requested range, or None.

    V2 can only show one clip at a time. An interjection whose LLM-chosen
    range swallows a block's B-roll assignment used to survive to
    compilation, where overlap resolution silently deleted the assignment.
    Trimming the interjection here keeps both clips.
    """
    windows = [
        w for w in _free_windows(start, end, occupied)
        if w[1] - w[0] >= MIN_INTERJECTION_SECONDS
    ]
    if not windows:
        return None
    return max(windows, key=lambda w: w[1] - w[0])


def _pick_alternative_clip(
    excluded_clip_id: str,
    catalog_lookup: dict,
    analysis_lookup: dict,
) -> str | None:
    """Pick a different catalog clip that has a real visual description."""
    for clip_id in sorted(catalog_lookup):
        if clip_id == excluded_clip_id:
            continue
        if describe_clip(analysis_lookup.get(clip_id, {})):
            return clip_id
    return None


def resolve_broll(
    broll_creative: list,
    broll_interjections: list,
    clip_catalog: list,
    semantic_docs: list,
    temporal_indices: list,
    timed_spine: dict,
    target_resolution: tuple = (1080, 1920),
) -> dict:
    """Resolve B-roll creative selections to execution data."""

    catalog_lookup = {c["clip_id"]: c for c in clip_catalog}
    analysis_lookup = build_semantic_lookup(semantic_docs, clip_catalog)
    index_lookup = {i["clip_id"]: i for i in temporal_indices}

    # Build spine block lookup for timeline positions
    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))
    block_lookup = {str(b["position"]): b for b in spine_blocks if "position" in b}

    assignments = []
    covered_positions = set()

    for broll in broll_creative:
        clip_id = broll["clip_id"]
        spine_pos = broll["spine_block_position"]
        preferred_moment = broll.get("preferred_moment", "")
        rationale = broll.get("selection_rationale", "")

        clip = catalog_lookup.get(clip_id)
        if not clip:
            print(
                f"WARNING: clip_id '{clip_id}' not in catalog, skipping",
                file=sys.stderr,
            )
            continue

        spine_block = block_lookup.get(str(spine_pos))
        if spine_block is None:
            print(
                f"WARNING: B-roll targets spine block {spine_pos!r}, which "
                f"does not exist - skipping",
                file=sys.stderr,
            )
            continue

        # One cutaway per block. A second selection takes its timeline
        # range from the same spine block, so both would claim the same
        # stretch of V2 - the collapse compile_manifest refuses. First
        # selection that resolves wins.
        if str(spine_pos) in covered_positions:
            print(
                f"  Dropped duplicate B-roll on block {spine_pos!r} "
                f"({clip_id}) - the block already has a cutaway",
                file=sys.stderr,
            )
            continue

        # B-roll must show something OTHER than the A-roll it covers.
        # Cutting to the same source clip is not a cutaway; on screen it
        # reads as a glitch in the same shot.
        covered_clip = spine_block["clip_id"]
        if covered_clip and clip_id == covered_clip:
            substitute = _pick_alternative_clip(
                clip_id, catalog_lookup, analysis_lookup,
            )
            if substitute is None:
                print(
                    f"WARNING: B-roll for block {spine_pos} selected "
                    f"{clip_id}, the same clip as its A-roll, and no "
                    f"alternative clip has a description - skipping",
                    file=sys.stderr,
                )
                continue
            print(
                f"  Block {spine_pos}: B-roll {clip_id} matched its own "
                f"A-roll; substituted {substitute}",
                file=sys.stderr,
            )
            clip_id = substitute
            clip = catalog_lookup[clip_id]
            rationale = (
                f"{rationale} (auto-substituted: original selection "
                f"duplicated the A-roll clip)"
            ).strip()

        timeline_start = spine_block["timeline_start"]
        timeline_end = spine_block["timeline_end"]
        block_duration = timeline_end - timeline_start

        clip_analysis = analysis_lookup.get(clip_id, {})
        clip_index = index_lookup.get(clip_id, {})
        clip_duration = clip.get("duration_seconds", 10.0)

        # Resolve the preferred moment to video in/out from the picture.
        choice = choose_window(
            preferred_moment,
            clip_analysis,
            clip_index,
            clip_duration,
            block_duration,
        )
        video_in, video_out = choice.as_tuple()

        # A cutaway can be shorter than the block it covers - we simply
        # return to A-roll early. It must never CLAIM more timeline than
        # its source can fill, or V2 shows a hole mid-cutaway.
        available = round(video_out - video_in, 3)
        if available < block_duration - 0.001:
            print(
                f"  Block {spine_pos}: {clip_id} can only supply "
                f"{available:.3f}s of the {block_duration:.3f}s slot - "
                f"shortening the cutaway",
                file=sys.stderr,
            )
            timeline_end = round(timeline_start + available, 3)

        # Check if clip needs resolution conform
        needs_conform = check_needs_conform(clip, target_resolution[0], target_resolution[1])

        assignments.append({
            "spine_block_position": spine_pos,
            "block_type": spine_block.get("block_type", "transition_slot"),
            "clip_id": clip_id,
            "source_file": clip.get("source_file") or clip.get("path"),
            "video_in": video_in,
            "video_out": video_out,
            "duration_seconds": round(video_out - video_in, 3),
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
            "needs_conform": needs_conform,
            "selection_rationale": rationale,
            # What chose these seconds, recorded beside them. A window
            # picked on `undiscriminated` is the absence of a decision,
            # not a decision, and a reviewer must be able to tell.
            "window_basis": choice.basis,
            "window_basis_detail": choice.basis_detail,
            "video_only": True,  # B-roll audio should NOT be linked
        })
        covered_positions.add(str(spine_pos))

    resolved_interjections = []
    # Everything already claiming a stretch of V2. Interjections are placed
    # around it, never over it.
    occupied = [(a["timeline_start"], a["timeline_end"]) for a in assignments]
    for interj in broll_interjections:
        clip_id = interj.get("clip_id")
        if not clip_id:
            continue
        spine_pos = interj.get("over_spine_block_position")
        preferred_moment = interj.get("preferred_moment", "")
        rationale = interj.get("selection_rationale", "")
        purpose = interj.get("purpose", "")

        clip = catalog_lookup.get(clip_id)
        if not clip:
            print(f"WARNING: interjection clip_id '{clip_id}' not in catalog, skipping", file=sys.stderr)
            continue

        spine_block = block_lookup.get(str(spine_pos), {})
        covered_clip = spine_block.get("clip_id")
        if covered_clip and clip_id == covered_clip:
            substitute = _pick_alternative_clip(
                clip_id, catalog_lookup, analysis_lookup,
            )
            if substitute is None:
                print(
                    f"WARNING: interjection over block {spine_pos} selected "
                    f"{clip_id}, the same clip as its A-roll - skipping",
                    file=sys.stderr,
                )
                continue
            clip_id = substitute
            clip = catalog_lookup[clip_id]

        default_tl_start = spine_block.get("timeline_start", 0.0)

        requested_start = interj.get("timeline_start", default_tl_start)
        requested_end = interj.get("timeline_end", requested_start + 2.0)

        placement = _place_without_overlap(
            requested_start, requested_end, occupied,
        )
        if placement is None:
            print(
                f"WARNING: interjection over block {spine_pos} "
                f"({requested_start:.3f}-{requested_end:.3f}s) is fully "
                f"covered by B-roll already placed on V2 and leaves no "
                f"free window of {MIN_INTERJECTION_SECONDS}s - dropping it "
                f"rather than displacing an assignment",
                file=sys.stderr,
            )
            continue

        timeline_start, timeline_end = (
            round(placement[0], 3), round(placement[1], 3),
        )
        if (timeline_start, timeline_end) != (requested_start, requested_end):
            print(
                f"  Interjection over block {spine_pos}: trimmed "
                f"{requested_start:.3f}-{requested_end:.3f}s to "
                f"{timeline_start:.3f}-{timeline_end:.3f}s so the B-roll "
                f"it overlapped survives",
                file=sys.stderr,
            )
        block_duration = timeline_end - timeline_start

        clip_analysis = analysis_lookup.get(clip_id, {})
        clip_index = index_lookup.get(clip_id, {})
        clip_duration = clip.get("duration_seconds", 10.0)

        choice = choose_window(
            preferred_moment, clip_analysis, clip_index, clip_duration,
            block_duration,
        )
        video_in, video_out = choice.as_tuple()

        # Same invariant the assignment path enforces: a cutaway may end
        # early, but it must never claim more timeline than its source can
        # fill, or V2 freezes mid-cutaway.
        available = round(video_out - video_in, 3)
        if available < block_duration - 0.001:
            print(
                f"  Interjection over block {spine_pos}: {clip_id} can only "
                f"supply {available:.3f}s of the {block_duration:.3f}s slot "
                f"- shortening the cutaway",
                file=sys.stderr,
            )
            timeline_end = round(timeline_start + available, 3)
            if timeline_end - timeline_start < MIN_INTERJECTION_SECONDS:
                print(
                    f"WARNING: interjection over block {spine_pos} would be "
                    f"only {timeline_end - timeline_start:.3f}s of usable "
                    f"source - dropping it",
                    file=sys.stderr,
                )
                continue

        occupied.append((timeline_start, timeline_end))

        needs_conform = check_needs_conform(clip, target_resolution[0], target_resolution[1])

        resolved_interjections.append({
            "over_spine_block_position": spine_pos,
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
            "purpose": purpose,
            "assigned_clip": {
                "clip_id": clip_id,
                "source_file": clip.get("source_file") or clip.get("path"),
                "video_in": video_in,
                "video_out": video_out,
                "duration_seconds": round(video_out - video_in, 3),
                "needs_conform": needs_conform,
                "selection_rationale": rationale,
                "window_basis": choice.basis,
                "window_basis_detail": choice.basis_detail,
                "video_only": True,
            }
        })

    return {
        "b_roll_assignments": assignments,
        "b_roll_interjections": resolved_interjections,
    }


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    _require_keys(data, ["clip_catalog", "semantic_analysis_documents"], "Input data")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")

    broll_creative = data.get("broll_creative", [])
    if not isinstance(broll_creative, list):
        broll_creative = []

    if not broll_creative:
        err_msg = json.dumps({
            "error": (
                "Missing: broll_creative. The B-roll selection step "
                "produced no assignments; it must choose clips from "
                "broll_candidates_toon."
            ),
            "step": "3.2_bridge",
        })
        print(err_msg, file=sys.stderr)
        print(err_msg)
        sys.exit(1)

    for broll in broll_creative:
        if not isinstance(broll, dict):
            raise ValueError("Items in broll_creative must be dictionaries")
        _require_keys(broll, ["clip_id", "spine_block_position"], "broll_creative item")

    clip_catalog = data.get("clip_catalog", [])
    semantic_docs = data.get("semantic_analysis_documents", [])
    temporal_raw = data.get("temporal_event_indices", [])
    temporal_indices = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    timed_spine = data.get("timed_spine", {})
    
    # The frame the product ships in, not the frame the footage arrived
    # in. See library/tools/delivery_format.py.
    target_width, target_height = resolve_delivery_format(
        data.get("project_folder"))

    interjections = data.get("b_roll_interjections", [])

    result = resolve_broll(
        broll_creative, interjections, clip_catalog, semantic_docs,
        temporal_indices, timed_spine,
        target_resolution=(target_width, target_height),
    )

    # There is NO minimum B-roll count. The creative direction decides how
    # many cutaways a piece gets; nothing is padded to satisfy a number.
    # Captain's ruling 2026-08-20 - the floor that rejected fewer than 5
    # clips (while its message said 5-15 and its check said 5-20) is
    # removed outright, not reconciled and not downgraded to a warning.
    # The accepted consequence: a thin edit is no longer caught
    # mechanically. Do not reintroduce an equivalent check here or
    # elsewhere. Guarded by tests/test_no_creative_floors.py.

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
