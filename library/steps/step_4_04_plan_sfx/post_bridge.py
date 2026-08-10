#!/usr/bin/env python3
"""
Step 4.4 Bridge: Resolve SFX Creative Plan to Execution Data

Takes the LLM's creative SFX selections and resolves them to precise
timeline positions using signal data from the temporal index.

Placement strategy by SFX type:
  bass_impact  → snap to nearest onset (transient) within ±200ms
  whoosh/swish → align to scene boundary or block edge
  riser        → end at the nearest energy peak
  click/tick   → align to nearest word_end_time (subtitle appearance)
  reverse_cymbal/swell → place at last word's end in the outgoing block

All placements are cross-referenced against:
  1. Onset times (transient anchors, ~23ms precision)
  2. Energy peaks (30Hz, ~33ms precision)
  3. Scene boundaries (~33ms precision)
  4. Word end times (speech boundaries, ~10ms precision)
  5. Beat grid (when music BPM is available)

Speech collision avoidance: SFX are shifted to the nearest gap if they
would overlap with active speech.

Classification: Deterministic / Data Transformation
Idempotent: Yes
"""
import json
import sys

from library.tools.fairlight_presets import select_preset_for_content
from library.tools.audio_ducker import compute_ducking_curves, compute_sfx_ducking
from library.tools.audio_reactive_sfx import align_sfx_to_prosody, scale_sfx_density
from library.tools.pipeline_validation import require_keys, require_type


# Volume level → dB mapping
VOLUME_MAP = {
    "subtle": -18,
    "low": -14,
    "medium": -10,
    "prominent": -6,
}

# Default SFX durations by type (seconds)
DURATION_DEFAULTS = {
    "whoosh": 0.3,
    "swish": 0.25,
    "bass_impact": 0.5,
    "riser": 5.0,
    "click": 0.1,
    "tick": 0.1,
    "reverse_cymbal": 2.0,
    "swell": 3.0,
}


def _find_nearest(target: float, candidates: list, max_dist: float = None) -> float:
    """Find the nearest value in candidates to target.

    Returns the nearest candidate, or target if no candidates are
    within max_dist (or if candidates is empty).
    """
    if not candidates:
        return target
    nearest = min(candidates, key=lambda c: abs(c - target))
    if max_dist is not None and abs(nearest - target) > max_dist:
        return target
    return nearest


def _find_block_for_time(timeline_time: float, spine_blocks: list) -> dict:
    """Find the spine block that contains a given timeline position."""
    for block in spine_blocks:
        tl_start = block.get("timeline_start", 0)
        tl_end = block.get("timeline_end", 0)
        if tl_start - 0.1 <= timeline_time <= tl_end + 0.1:
            return block
    # Fallback: find the nearest block
    if spine_blocks:
        return min(spine_blocks,
                   key=lambda b: abs(b.get("timeline_start", 0) - timeline_time))
    return {}


def _source_to_timeline(source_time: float, block: dict) -> float:
    """Convert a source-domain time to timeline-domain for a given block."""
    src_start = block.get("source_start", 0)
    tl_start = block.get("timeline_start", 0)
    return source_time - src_start + tl_start


def _get_word_times_in_block(block: dict, temporal_index: dict) -> list:
    """Get all word start/end times in timeline domain for a block."""
    clip_id = block.get("clip_id", "")
    if not clip_id:
        return []

    src_start = block.get("source_start", 0)
    src_end = block.get("source_end", 0)
    word_ends = temporal_index.get("word_end_times", [])

    # Filter to words in this block's source range, convert to timeline
    result = []
    for we in word_ends:
        if src_start - 0.05 <= we <= src_end + 0.05:
            result.append(_source_to_timeline(we, block))
    return result


def _avoid_speech_collision(
    sfx_time: float,
    sfx_duration: float,
    word_times_tl: list,
    block_start: float,
    block_end: float,
) -> float:
    """Shift SFX placement to avoid overlapping with speech.

    Checks if the SFX would overlap with any word boundary.
    If so, finds the nearest gap between words.
    """
    if not word_times_tl:
        return sfx_time

    sfx_end = sfx_time + sfx_duration

    # Check for collision with any word boundary
    collision = False
    for wt in word_times_tl:
        if sfx_time <= wt <= sfx_end:
            collision = True
            break

    if not collision:
        return sfx_time

    # Find the nearest gap between word boundaries
    # Gaps are the spaces between consecutive word end times
    sorted_times = sorted(word_times_tl)
    best_gap_start = sfx_time
    best_gap_dist = float("inf")

    for i in range(len(sorted_times) - 1):
        gap_start = sorted_times[i]
        gap_end = sorted_times[i + 1]
        gap_size = gap_end - gap_start

        # Gap must be large enough for the SFX
        if gap_size >= sfx_duration * 0.8:
            dist = abs(gap_start - sfx_time)
            if dist < best_gap_dist:
                best_gap_dist = dist
                best_gap_start = gap_start

    # Also check before the first word and after the last word
    if sorted_times[0] - block_start >= sfx_duration:
        dist = abs(block_start - sfx_time)
        if dist < best_gap_dist:
            best_gap_start = block_start

    if block_end - sorted_times[-1] >= sfx_duration:
        dist = abs(sorted_times[-1] - sfx_time)
        if dist < best_gap_dist:
            best_gap_start = sorted_times[-1]

    return max(block_start, min(best_gap_start, block_end - sfx_duration))


def find_sfx_placement(
    sfx_type: str,
    timeline_start: float,
    sfx_duration: float,
    block: dict,
    temporal_index: dict,
    beat_grid: list = None,
) -> float:
    """Signal-driven SFX placement using temporal index data.

    Strategy per SFX type:
    1. Select candidate times from the primary signal
    2. Snap to nearest onset for tighter sync
    3. Beat-quantize if music is present
    4. Avoid speech collision

    Args:
        sfx_type: Type of SFX (bass_impact, whoosh, riser, etc.)
        timeline_start: Initial timeline position from creative plan
        sfx_duration: Duration of the SFX
        block: Spine block dict containing this SFX
        temporal_index: Temporal index for the block's clip
        beat_grid: Optional list of beat positions in timeline domain

    Returns:
        Refined timeline position for the SFX
    """
    clip_id = block.get("clip_id", "")
    src_start = block.get("source_start", 0)
    src_end = block.get("source_end", 0)
    tl_start = block.get("timeline_start", 0)
    tl_end = block.get("timeline_end", 0)

    # If no clip_id (transition_slot, outro), keep original position
    if not clip_id:
        return timeline_start

    # Convert temporal index data to timeline domain
    onset_times_src = temporal_index.get("onset_times", [])
    energy_peaks_src = temporal_index.get("energy_curve", {}).get("peak_times", [])
    scene_boundaries_src = [s["time"] for s in temporal_index.get("scene_boundaries", [])]
    word_end_times_src = temporal_index.get("word_end_times", [])

    # Filter to this block's source range and convert to timeline
    def in_block_src(t):
        return src_start - 0.1 <= t <= src_end + 0.1

    onsets_tl = [_source_to_timeline(t, block) for t in onset_times_src if in_block_src(t)]
    peaks_tl = [_source_to_timeline(t, block) for t in energy_peaks_src if in_block_src(t)]
    scenes_tl = [_source_to_timeline(t, block) for t in scene_boundaries_src if in_block_src(t)]
    word_ends_tl = [_source_to_timeline(t, block) for t in word_end_times_src if in_block_src(t)]

    beat_grid = beat_grid or []

    # ── Type-specific placement strategies ──

    if sfx_type == "bass_impact":
        # Snap to the nearest onset (transient) — impacts feel best
        # when they land exactly on a natural audio transient.
        # Search within ±200ms of the intended position.
        placed = _find_nearest(timeline_start, onsets_tl, max_dist=0.2)

        # If no onset nearby, try energy peak
        if placed == timeline_start and peaks_tl:
            placed = _find_nearest(timeline_start, peaks_tl, max_dist=0.5)

        # Beat-snap if available
        if beat_grid:
            placed = _find_nearest(placed, beat_grid, max_dist=0.05)

        return placed

    elif sfx_type in ("whoosh", "swish"):
        # Whooshes guide attention across cuts. Align to scene boundary
        # or block edge — whichever is nearest.
        candidates = scenes_tl + [tl_start, tl_end]
        placed = _find_nearest(timeline_start, candidates, max_dist=0.5)

        # Snap to nearest onset for tighter sync
        placed = _find_nearest(placed, onsets_tl, max_dist=0.1)

        return placed

    elif sfx_type == "riser":
        # Risers build tension — they END at an energy peak.
        # Place the start so that start + duration = peak.
        if peaks_tl:
            target_end = _find_nearest(timeline_start + sfx_duration, peaks_tl, max_dist=2.0)
            placed = max(tl_start, target_end - sfx_duration)
        else:
            placed = timeline_start

        return placed

    elif sfx_type in ("click", "tick"):
        # Clicks pair with subtitle appearances — snap to word end times.
        if word_ends_tl:
            placed = _find_nearest(timeline_start, word_ends_tl, max_dist=0.3)
        else:
            placed = timeline_start

        return placed

    elif sfx_type in ("reverse_cymbal", "swell"):
        # Swells mark section endings — place at the last word's end
        # in the block, or at the block boundary.
        if word_ends_tl:
            last_word_end = max(word_ends_tl)
            placed = last_word_end
        else:
            placed = max(tl_start, tl_end - sfx_duration)

        # Beat-snap if available
        if beat_grid:
            placed = _find_nearest(placed, beat_grid, max_dist=0.1)

        return placed

    else:
        # Unknown type — keep original position, snap to onset if close
        return _find_nearest(timeline_start, onsets_tl, max_dist=0.1)


def resolve_sfx(
    creative_plan: list,
    timed_spine: dict,
    temporal_indices: list = None,
    music_analysis: dict = None,
    frame_rate: float = 30.0,
    creative_direction: dict = None,
    prosody_analysis: dict = None,
    engagement_scores: dict = None,
    brand_audio: dict = None,
) -> dict:
    """Resolve creative SFX plan to execution specs.

    For each SFX in the creative plan:
    1. Find the spine block it belongs to
    2. Load the temporal index for that clip
    3. Apply type-specific signal-driven placement
    4. Check for speech collision and shift if needed
    """
    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))
    temporal_indices = temporal_indices or []

    # Build temporal index lookup by clip_id
    ti_lookup = {}
    for ti in temporal_indices:
        cid = ti.get("clip_id", "")
        if cid:
            ti_lookup[cid] = ti

    # Build beat grid from music analysis
    beat_grid = []
    if music_analysis:
        bars = music_analysis.get("beat_grid", {}).get("bars", [])
        beat_grid = [b["start"] for b in bars]

    # Apply SFX density scaling based on energy
    energy_level = "moderate"
    if creative_direction:
        energy_level = creative_direction.get("energy_level", "moderate")
    creative_plan = scale_sfx_density(creative_plan, energy_level)

    # Align to prosody if available
    if prosody_analysis:
        creative_plan = align_sfx_to_prosody(creative_plan, prosody_analysis, engagement_scores)

    resolved = []
    for sfx in creative_plan:
        # Find the spine block for this SFX
        tl_start = sfx.get("timeline_start", 0.0)
        block = _find_block_for_time(tl_start, spine_blocks)
        clip_id = block.get("clip_id", "")

        # Get temporal index for this clip
        ti = ti_lookup.get(clip_id, {})

        sfx_type = sfx.get("sfx_type", "whoosh")
        volume = sfx.get("volume_level", "subtle")
        volume_db = VOLUME_MAP.get(volume, -14)
        duration = DURATION_DEFAULTS.get(sfx_type, 0.5)

        # Signal-driven placement
        refined_start = find_sfx_placement(
            sfx_type, tl_start, duration, block, ti, beat_grid,
        )

        # Speech collision avoidance
        word_times_tl = _get_word_times_in_block(block, ti)
        tl_end = block.get("timeline_end", refined_start + duration)
        tl_block_start = block.get("timeline_start", refined_start)

        refined_start = _avoid_speech_collision(
            refined_start, duration, word_times_tl,
            tl_block_start, tl_end,
        )

        shift = abs(refined_start - tl_start)
        resolved.append({
            "sfx_id": f"sfx_{len(resolved)+1:03d}",
            "sfx_type": sfx_type,
            "timeline_start": round(refined_start, 3),
            "timeline_end": round(refined_start + duration, 3),
            "duration_seconds": duration,
            "volume_db": volume_db,
            "volume_level": volume,
            "target_track": "A3",
            "rationale": sfx.get("rationale", ""),
            "placement_method": _describe_placement(sfx_type),
            "shift_from_original": round(shift, 3),
        })

    # Apply SFX ducking
    speech_segments = []
    if prosody_analysis and "speech_segments" in prosody_analysis:
        speech_segments = prosody_analysis["speech_segments"]
    elif spine_blocks:
        # Fallback to spine blocks to ensure ducking is applied
        for block in spine_blocks:
            if block.get("block_type") in ("speech", "hook"):
                speech_segments.append({
                    "start_time": block.get("timeline_start", 0.0),
                    "end_time": block.get("timeline_end", 0.0)
                })

    if speech_segments:
        # compute_sfx_ducking expects {"start_time": x, "end_time": y}
        # bridge currently outputs {"timeline_start": x, "timeline_end": y}
        # Let's map it temporarily
        mapped_resolved = [{"start_time": s["timeline_start"], "end_time": s["timeline_end"], **s} for s in resolved]
        ducked_sfx = compute_sfx_ducking(mapped_resolved, speech_segments)
        # map back
        for i, s in enumerate(ducked_sfx):
            resolved[i]["volume_db"] = s.get("volume_db", resolved[i]["volume_db"])

    # Determine Fairlight preset
    content_type = "vlog"
    if creative_direction:
        content_type = creative_direction.get("content_type", "vlog")
    preset_name = select_preset_for_content(content_type, brand_audio)

    # Compute music ducking
    music_dur = 60.0
    if music_analysis and "duration" in music_analysis:
        music_dur = music_analysis["duration"]
    music_ducking = compute_ducking_curves(speech_segments, music_dur)
    
    require_type(music_ducking, list, "music_ducking", "step_4_04_plan_sfx/post_bridge.py")

    return {
        "sfx_list": resolved,
        "fairlight_preset": preset_name,
        "music_ducking": music_ducking,
    }



def _describe_placement(sfx_type: str) -> str:
    """Human-readable description of the placement method used."""
    methods = {
        "bass_impact": "onset-snap (±200ms) → energy-peak → beat-snap",
        "whoosh": "scene-boundary/block-edge → onset-snap (±100ms)",
        "swish": "scene-boundary/block-edge → onset-snap (±100ms)",
        "riser": "end-at-energy-peak → backfill duration",
        "click": "word-end-snap (±300ms)",
        "tick": "word-end-snap (±300ms)",
        "reverse_cymbal": "last-word-end → beat-snap",
        "swell": "last-word-end → beat-snap",
    }
    return methods.get(sfx_type, "onset-snap fallback")


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    require_keys(data, ["music_analysis"], "step_4_04_plan_sfx/post_bridge.py")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")

    creative = data.get("sfx_creative")
    if not creative and "llm_raw_response" in data:
        try:
            parsed = json.loads(data["llm_raw_response"])
            creative = parsed if isinstance(parsed, list) else parsed.get("sfx_creative", [])
        except Exception:
            creative = data["llm_raw_response"]
        
    if not isinstance(creative, list):
        print(f"  Warning: LLM returned invalid response for plan_sfx. Defaulting to empty list. Response was: {str(creative)[:100]}", file=sys.stderr)
        creative = []
        
    creative = [v for v in creative if isinstance(v, dict)]

    spine = data.get("timed_spine", {})
    temporal_raw = data.get("temporal_event_indices", [])
    temporal = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    music = data.get("music_analysis", {})
    fps = data.get("project_fps", data.get("frame_rate", 30.0))
    
    cd = data.get("creative_direction", {})
    prosody = data.get("prosody_analysis", {})
    eng = data.get("engagement_scores", {})
    brand_audio = data.get("brand_audio", {})
    
    if len(creative) < 3 or len(creative) > 15:
        print(json.dumps({"error": f"Planned {len(creative)} SFX, but you MUST plan between 5 and 10 SFX.", "step": "4.04_bridge"}))
        sys.exit(1)

    result = resolve_sfx(creative, spine, temporal, music, fps, cd, prosody, eng, brand_audio)
    
    json.dump({"sfx_spec": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
