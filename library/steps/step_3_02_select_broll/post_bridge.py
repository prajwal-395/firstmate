#!/usr/bin/env python3
"""
Step 3.2 Bridge: Resolve B-Roll Creative Selections to Execution Data

Takes the LLM's creative B-roll selections (clip_id + preferred_moment +
rationale) and resolves them to execution-ready data using the temporal
event index for precision placement:
  - clip_id → source_file (from catalog)
  - preferred_moment → video_in/video_out (from temporal index scene
    boundaries, energy peaks, and motion energy)
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

def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")


def check_needs_conform(clip: dict, target_width: int, target_height: int) -> bool:
    """
    Check if a clip needs conforming to target output specs.
    True if resolution or rotation differs from target.
    """
    w = clip.get("width", clip.get("resolution_width", 0))
    h = clip.get("height", clip.get("resolution_height", 0))
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
    """
    Find the best video_in/video_out for a B-roll clip using the temporal
    index (scene boundaries, energy curve, motion energy) and semantic
    analysis blocks.

    Strategy:
    1. If temporal index has scene boundaries, select the scene segment
       that best matches the preferred moment + has highest energy/motion
    2. If no temporal index, fall back to block-based estimation from
       semantic analysis
    3. Snap in/out points to scene boundaries for clean cuts

    Returns (video_in, video_out).
    """
    scenes = temporal_index.get("scene_boundaries", [])
    energy = temporal_index.get("energy_curve", {})
    motion = temporal_index.get("motion_energy", {})

    # ── Strategy 1: Scene-boundary-aware selection ──
    if len(scenes) >= 2:
        # Build scene segments as (start, end) pairs
        scene_times = sorted(s["time"] for s in scenes)
        # Add clip end as final boundary
        if scene_times[-1] < clip_duration - 0.5:
            scene_times.append(clip_duration)

        segments = []
        for i in range(len(scene_times) - 1):
            seg_start = scene_times[i]
            seg_end = scene_times[i + 1]
            seg_dur = seg_end - seg_start

            # Score segment by energy + motion
            energy_score = _avg_energy_in_range(
                energy, seg_start, seg_end
            )
            motion_score = _avg_energy_in_range(
                motion, seg_start, seg_end
            )

            # Score by keyword match with preferred moment
            text_score = 0.0
            if preferred_moment:
                text_score = _text_match_score(
                    preferred_moment, clip_analysis, i, len(scene_times) - 1,
                    seg_start, seg_end,
                )

            # Combined score: energy + motion + text relevance
            combined = (
                energy_score * 0.3
                + motion_score * 0.3
                + text_score * 0.4
            )

            segments.append({
                "start": seg_start,
                "end": seg_end,
                "duration": seg_dur,
                "score": combined,
                "energy": energy_score,
                "motion": motion_score,
            })

        # Filter segments that can fit the target duration
        # First: try segments >= target_duration
        viable = [s for s in segments if s["duration"] >= target_duration]
        if not viable:
            # Fall back to all segments, we'll use what we can
            viable = segments

        # Pick the highest-scoring segment
        best = max(viable, key=lambda s: s["score"])

        # Trim to target duration, centered on the highest-energy moment
        if best["duration"] >= target_duration:
            # Find the peak energy point within this segment
            peak_time = _peak_energy_in_range(
                energy, best["start"], best["end"]
            )
            if peak_time is not None:
                # Center around peak
                half = target_duration / 2
                video_in = max(best["start"], peak_time - half)
                video_out = min(best["end"], video_in + target_duration)
                # Adjust if we hit the end
                if video_out - video_in < target_duration:
                    video_in = max(
                        best["start"], video_out - target_duration
                    )
            else:
                video_in = best["start"]
                video_out = best["start"] + target_duration
        else:
            # The best-scoring scene is shorter than the slot. Start there
            # and run past the boundary rather than returning a clip that
            # cannot fill its slot - a short B-roll clip leaves a hole on
            # V2 and trips the rough-cut duration invariant.
            video_in = best["start"]
            video_out = best["start"] + target_duration

        # Snap to nearest scene boundary (within 0.2s tolerance)
        video_in = _snap_to_boundary(video_in, scene_times, tolerance=0.2)
        video_out = _snap_to_boundary(video_out, scene_times, tolerance=0.2)

        return _fit_to_clip(video_in, video_out, target_duration, clip_duration)

    # ── Strategy 2: Energy/motion-based selection (no scene boundaries) ──
    if energy.get("values") or motion.get("values"):
        peak = _peak_energy_in_range(energy, 0, clip_duration)
        if peak is not None:
            half = target_duration / 2
            return _fit_to_clip(
                max(0, peak - half), max(0, peak - half) + target_duration,
                target_duration, clip_duration,
            )

    # ── Strategy 3: Fallback — block-based estimation from semantic analysis ──
    blocks = clip_analysis.get("blocks", [])
    if blocks and preferred_moment:
        moment_words = set(preferred_moment.lower().split())
        best_idx = 0
        best_score = -1
        for i, block in enumerate(blocks):
            block_text = " ".join([
                block.get("label", ""),
                block.get("visual", ""),
            ]).lower()
            overlap = len(moment_words & set(block_text.split()))
            if overlap > best_score:
                best_score = overlap
                best_idx = i

        block_start = _block_start_seconds(
            blocks, best_idx, clip_duration
        )
        return _fit_to_clip(
            block_start, block_start + target_duration,
            target_duration, clip_duration,
        )

    # ── Ultimate fallback: start of clip ──
    return _fit_to_clip(0.0, target_duration, target_duration, clip_duration)


def _fit_to_clip(
    video_in: float, video_out: float,
    target_duration: float, clip_duration: float,
) -> tuple:
    """Clamp a window into the clip while keeping it target_duration long.

    A B-roll clip must be able to fill the slot it covers. Returning a
    window shorter than the slot leaves a gap on V2 where the A-roll shows
    through mid-cutaway.
    """
    if clip_duration <= 0:
        return 0.0, round(target_duration, 3)

    span = min(target_duration, clip_duration)
    video_in = max(0.0, video_in)
    video_out = video_in + span
    if video_out > clip_duration:
        video_out = clip_duration
        video_in = max(0.0, video_out - span)
    return round(video_in, 3), round(video_out, 3)


def _avg_energy_in_range(
    curve_data: dict, start: float, end: float
) -> float:
    """Compute average energy/motion in a time range from a curve."""
    values = curve_data.get("values", [])
    sr = curve_data.get("sample_rate_hz", 2)
    if not values or sr <= 0:
        return 0.0

    i_start = max(0, int(start * sr))
    i_end = min(len(values), int(end * sr))
    segment = values[i_start:i_end]
    return sum(segment) / len(segment) if segment else 0.0


def _peak_energy_in_range(
    curve_data: dict, start: float, end: float
) -> float | None:
    """Find the time of peak energy within a range."""
    values = curve_data.get("values", [])
    sr = curve_data.get("sample_rate_hz", 2)
    if not values or sr <= 0:
        return None

    i_start = max(0, int(start * sr))
    i_end = min(len(values), int(end * sr))
    segment = values[i_start:i_end]
    if not segment:
        return None

    peak_idx = segment.index(max(segment))
    return (i_start + peak_idx) / sr


def _block_time_bounds(block: dict) -> tuple:
    """A block's measured (start, end), or (None, None) when it has none.

    Blocks derived from the v3 analyser carry the time window the action
    was actually observed in; blocks from the retired schema carry only
    their position in the list.
    """
    start, end = block.get("start"), block.get("end")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return float(start), float(end)
    return None, None


def _block_start_seconds(
    blocks: list, block_idx: int, clip_duration: float
) -> float:
    """Where in the clip a block sits.

    Reads the block's measured start when it has one; only a block with
    no time bounds falls back to estimating its position from how far
    down the list it is.
    """
    start, _ = _block_time_bounds(blocks[block_idx])
    if start is not None:
        return start
    return (block_idx / len(blocks)) * clip_duration


def _block_for_segment(
    blocks: list,
    segment_idx: int,
    total_segments: int,
    seg_start=None,
    seg_end=None,
) -> dict:
    """The block describing a scene segment.

    Picked by time overlap when the blocks carry measured bounds, so the
    segment is scored against what was observed while it was on screen.
    Blocks without bounds keep the index-proportion mapping, which is all
    a retired-schema document supports.
    """
    if seg_start is not None and seg_end is not None:
        best, best_overlap = None, 0.0
        for block in blocks:
            start, end = _block_time_bounds(block)
            if start is None:
                continue
            overlap = min(seg_end, end) - max(seg_start, start)
            if overlap > best_overlap:
                best, best_overlap = block, overlap
        if best is not None:
            return best

    block_idx = int(segment_idx / max(total_segments, 1) * len(blocks))
    return blocks[min(block_idx, len(blocks) - 1)]


def _text_match_score(
    preferred_moment: str,
    clip_analysis: dict,
    segment_idx: int,
    total_segments: int,
    seg_start=None,
    seg_end=None,
) -> float:
    """Score how well a segment matches the preferred moment description."""
    blocks = clip_analysis.get("blocks", [])
    if not blocks:
        return 0.0

    block = _block_for_segment(
        blocks, segment_idx, total_segments, seg_start, seg_end
    )

    block_text = " ".join([
        block.get("label", ""),
        block.get("visual", ""),
        block.get("broll_context", ""),
    ]).lower()

    moment_words = set(preferred_moment.lower().split())
    block_words = set(block_text.split())
    overlap = len(moment_words & block_words)
    return min(1.0, overlap / max(len(moment_words), 1))


def _snap_to_boundary(
    time_point: float, boundaries: list, tolerance: float = 0.2
) -> float:
    """Snap a time point to the nearest boundary if within tolerance."""
    if not boundaries:
        return time_point
    nearest = min(boundaries, key=lambda b: abs(b - time_point))
    if abs(nearest - time_point) <= tolerance:
        return nearest
    return time_point


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

        # Resolve preferred moment to video in/out using temporal index
        video_in, video_out = find_best_segment(
            preferred_moment,
            clip_analysis,
            clip_index,
            clip_duration,
            block_duration,
        )

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
            "source_file": clip.get("source_file", clip.get("path", clip.get("file_path"))),
            "video_in": video_in,
            "video_out": video_out,
            "duration_seconds": round(video_out - video_in, 3),
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
            "needs_conform": needs_conform,
            "selection_rationale": rationale,
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

        video_in, video_out = find_best_segment(
            preferred_moment, clip_analysis, clip_index, clip_duration, block_duration
        )

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
                "source_file": clip.get("source_file", clip.get("path", clip.get("file_path"))),
                "video_in": video_in,
                "video_out": video_out,
                "duration_seconds": round(video_out - video_in, 3),
                "needs_conform": needs_conform,
                "selection_rationale": rationale,
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
        print(json.dumps({
            "error": (
                "Missing: broll_creative. The B-roll selection step "
                "produced no assignments; it must choose clips from "
                "broll_candidates_toon."
            ),
            "step": "3.2_bridge",
        }))
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
    
    target_width = data.get("project_resolution", [1080, 1920])[0]
    target_height = data.get("project_resolution", [1080, 1920])[1]

    interjections = data.get("b_roll_interjections", [])

    result = resolve_broll(
        broll_creative, interjections, clip_catalog, semantic_docs,
        temporal_indices, timed_spine,
        target_resolution=(target_width, target_height),
    )
        
    total_broll = len(result.get("b_roll_assignments", [])) + len(interjections)
    if total_broll < 5 or total_broll > 20:
        print(json.dumps({"error": f"Only {total_broll} B-roll clips selected. You MUST select 5-15 B-roll clips for a 60-second video.", "step": "3.02_bridge"}))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
