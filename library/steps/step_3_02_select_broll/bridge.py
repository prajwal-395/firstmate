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

def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")


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
                    preferred_moment, clip_analysis, i, len(scene_times) - 1
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
            video_in = best["start"]
            video_out = best["end"]

        # Snap to nearest scene boundary (within 0.2s tolerance)
        video_in = _snap_to_boundary(video_in, scene_times, tolerance=0.2)
        video_out = _snap_to_boundary(video_out, scene_times, tolerance=0.2)

        return round(video_in, 3), round(video_out, 3)

    # ── Strategy 2: Energy/motion-based selection (no scene boundaries) ──
    if energy.get("values") or motion.get("values"):
        peak = _peak_energy_in_range(energy, 0, clip_duration)
        if peak is not None:
            half = target_duration / 2
            video_in = max(0, peak - half)
            video_out = min(clip_duration, video_in + target_duration)
            return round(video_in, 3), round(video_out, 3)

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

        total = len(blocks)
        block_start = (best_idx / total) * clip_duration
        video_in = block_start
        video_out = min(block_start + target_duration, clip_duration)
        return round(video_in, 2), round(video_out, 2)

    # ── Ultimate fallback: start of clip ──
    return 0.0, round(min(target_duration, clip_duration), 2)


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


def _text_match_score(
    preferred_moment: str,
    clip_analysis: dict,
    segment_idx: int,
    total_segments: int,
) -> float:
    """Score how well a segment matches the preferred moment description."""
    blocks = clip_analysis.get("blocks", [])
    if not blocks:
        return 0.0

    # Map segment index to approximate block index
    block_idx = int(segment_idx / total_segments * len(blocks))
    block_idx = min(block_idx, len(blocks) - 1)
    block = blocks[block_idx]

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


def resolve_broll(
    broll_creative: list,
    clip_catalog: list,
    semantic_docs: list,
    temporal_indices: list,
    timed_spine: dict,
    target_resolution: tuple = (1080, 1920),
) -> dict:
    """Resolve B-roll creative selections to execution data."""

    catalog_lookup = {c["clip_id"]: c for c in clip_catalog}
    analysis_lookup = {a["clip_id"]: a for a in semantic_docs}
    index_lookup = {i["clip_id"]: i for i in temporal_indices}

    # Build spine block lookup for timeline positions
    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))
    block_lookup = {b["position"]: b for b in spine_blocks}

    assignments = []

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

        spine_block = block_lookup.get(spine_pos, {})
        block_duration = spine_block.get("duration_seconds", 2.0)
        timeline_start = spine_block.get("timeline_start", 0.0)
        timeline_end = spine_block.get(
            "timeline_end", timeline_start + block_duration
        )

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

        # Check if clip needs resolution conform
        clip_res = (
            clip.get("resolution_width", target_resolution[0]),
            clip.get("resolution_height", target_resolution[1]),
        )
        needs_conform = clip_res != target_resolution

        assignments.append({
            "spine_block_position": spine_pos,
            "block_type": spine_block.get("block_type", "transition_slot"),
            "clip_id": clip_id,
            "source_file": clip["path"],
            "video_in": video_in,
            "video_out": video_out,
            "duration_seconds": round(video_out - video_in, 3),
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
            "needs_conform": needs_conform,
            "selection_rationale": rationale,
            "video_only": True,  # B-roll audio should NOT be linked
        })

    return {"b_roll_assignments": assignments}


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    _require_keys(data, ["broll_creative", "clip_catalog", "semantic_analysis_documents", "temporal_event_indices"], "Input data")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")
    if not isinstance(data["broll_creative"], list):
        raise ValueError("broll_creative must be a list")
    for broll in data["broll_creative"]:
        if not isinstance(broll, dict):
            raise ValueError("Items in broll_creative must be dictionaries")
        _require_keys(broll, ["clip_id", "spine_block_position"], "broll_creative item")

    broll_creative = data.get("broll_creative", [])
    clip_catalog = data.get("clip_catalog", [])
    semantic_docs = data.get("semantic_analysis_documents", [])
    temporal_indices = data.get("temporal_event_indices", [])
    timed_spine = data.get("timed_spine", {})

    if not broll_creative:
        print(json.dumps({
            "error": "Missing: broll_creative",
            "step": "3.2_bridge",
        }))
        sys.exit(1)

    result = resolve_broll(
        broll_creative, clip_catalog, semantic_docs,
        temporal_indices, timed_spine,
    )
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
