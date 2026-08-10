#!/usr/bin/env python3
"""
Step 4.2 Bridge: Resolve Transition Creative Plan to Execution Data

Takes the LLM's creative transition selections and resolves them to
precise timeline cut points using signal data from the temporal index.

Cut point refinement strategy:
  1. Find the OUTGOING block (the one before the cut)
  2. For speech blocks: cut at the last word's end time
  3. For non-speech blocks: use block boundary
  4. Beat-snap if within tolerance
  5. Prefer positions where a word end AND a beat coincide

Classification: Deterministic / Data Transformation
Idempotent: Yes
"""
import json
import os
import sys
import math
from library.tools.pipeline_validation import require_keys


def snap_to_beat(
    cut_time: float,
    beat_grid: list,
    tolerance: float = 0.10,
) -> tuple:
    """
    Snap a cut point to the nearest musical beat if within tolerance.

    Returns (snapped_time, was_snapped, snap_delta).
    """
    if not beat_grid:
        return cut_time, False, 0.0

    closest = min(beat_grid, key=lambda b: abs(b - cut_time))
    delta = closest - cut_time
    if abs(delta) <= tolerance:
        return closest, True, round(delta, 4)
    return cut_time, False, 0.0


def resolve_cut_point(
    incoming: dict,
    outgoing: dict,
    ti_lookup: dict,
    beat_grid: list,
) -> dict:
    """Find the precise cut point for a transition.

    Examines the OUTGOING block (the one before the cut) to find
    the natural end point based on signal data.

    Args:
        incoming: The incoming spine block
        outgoing: The outgoing spine block
        ti_lookup: {clip_id: temporal_index_dict}
        beat_grid: List of beat positions in timeline domain

    Returns:
        {
            "cut_time": float,
            "method": str,
            "word_beat_coincidence": bool
        }
    """
    incoming_start = incoming.get("timeline_start", 0.0)

    outgoing_type = outgoing.get("block_type", "")
    outgoing_clip_id = outgoing.get("clip_id", "")

    # ── Speech blocks: cut at the last word's end ──
    if outgoing_type in ("speech", "hook") and outgoing_clip_id:
        ti = ti_lookup.get(outgoing_clip_id, {})
        word_end_times = ti.get("word_end_times", [])

        if word_end_times:
            src_start = outgoing.get("source_start", 0)
            src_end = outgoing.get("source_end", 0)
            tl_start = outgoing.get("timeline_start", 0)
            tl_end = outgoing.get("timeline_end", 0)

            # Filter word ends to this block's source range
            block_word_ends_tl = []
            for we in word_end_times:
                if src_start - 0.05 <= we <= src_end + 0.05:
                    tl_we = we - src_start + tl_start
                    block_word_ends_tl.append(tl_we)

            if block_word_ends_tl:
                last_word_end = max(block_word_ends_tl)

                # Don't exceed the block's timeline_end
                cut_time = min(last_word_end, tl_end)

                # Check for word-end + beat coincidence (the ideal cut)
                word_beat_coincidence = False
                if beat_grid:
                    for we in block_word_ends_tl:
                        for beat in beat_grid:
                            if abs(we - beat) < 0.05:
                                # A word end lands on a beat — use it
                                cut_time = min(we, tl_end)
                                word_beat_coincidence = True
                                break
                        if word_beat_coincidence:
                            break

                # If no coincidence, still beat-snap the word-end cut
                if not word_beat_coincidence and beat_grid:
                    snapped, was_snapped, _ = snap_to_beat(cut_time, beat_grid, tolerance=0.08)
                    if was_snapped:
                        cut_time = min(snapped, tl_end)

                return {
                    "cut_time": cut_time,
                    "method": "word-end" + (" + beat" if word_beat_coincidence else ""),
                    "word_beat_coincidence": word_beat_coincidence,
                }

    # ── Non-speech blocks: use incoming block's timeline_start ──
    return {
        "cut_time": incoming_start,
        "method": "block-boundary",
        "word_beat_coincidence": False,
    }


def resolve_transitions(
    creative_plan: list,
    timed_spine: dict,
    music_selection: dict,
    temporal_indices: list = None,
    frame_rate: float = 30.0,
    creative_direction: dict = None,
    brand_effect: dict = None,
) -> list:
    """Resolve creative transition plan to execution specs.

    For each transition:
    1. Find the outgoing spine block
    2. If speech: cut at last word's end time
    3. Beat-snap (prefer word-end + beat coincidence)
    4. Resolve duration_feel to frame count
    """
    if creative_direction is None:
        creative_direction = {}
    if brand_effect is None:
        brand_effect = {}

    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))
    block_lookup = {b["position"]: b for b in spine_blocks}

    # Build temporal index lookup
    ti_lookup = {}
    for ti in (temporal_indices or []):
        cid = ti.get("clip_id", "")
        if cid:
            ti_lookup[cid] = ti

    # Build beat grid if BPM available
    bpm = music_selection.get("bpm", 0)
    beat_positions = []
    if bpm > 0:
        beat_interval = 60.0 / bpm
        total_dur = timed_spine.get("total_estimated_duration_seconds", timed_spine.get("audio_spine", {}).get(
            "total_estimated_duration_seconds", 60
        ))
        beat_positions = [
            round(i * beat_interval, 4)
            for i in range(int(total_dur / beat_interval) + 1)
        ]

    # Initialize preset index
    from library.tools.preset_indexer import scan_library
    import os
    try:
        preset_dir = os.path.join(os.path.dirname(__file__), "..", "..", "presets")
        preset_index = scan_library(os.path.abspath(preset_dir))
    except Exception:
        preset_index = None

    from library.tools.transition_selector import select_transition

    resolved = []
    for trans in creative_plan:
        pos = trans.get("cut_point_position")

        # If position is missing, find the block by matching timeline_start
        if pos is None:
            original_tl = trans.get("cut_point_original",
                                    trans.get("cut_point_timeline", 0.0))
            # Find the incoming block whose timeline_start is closest
            best_pos = None
            best_dist = float("inf")
            for b in spine_blocks:
                dist = abs(b.get("timeline_start", 0.0) - original_tl)
                if dist < best_dist:
                    best_dist = dist
                    best_pos = b.get("position")
            pos = best_pos

        # Find block index for robust incoming/outgoing resolution
        block_idx = -1
        for i, b in enumerate(spine_blocks):
            if str(b.get("position")) == str(pos):
                block_idx = i
                break
                
        block = spine_blocks[block_idx] if block_idx >= 0 else {}
        original_tl = block.get("timeline_start", 0.0)
        
        outgoing = spine_blocks[block_idx - 1] if block_idx > 0 else {}

        # Use the content-aware transition selector
        selected_trans = select_transition(
            from_clip=outgoing,
            to_clip=block,
            brand_effect=brand_effect,
            preset_index=preset_index,
            creative_direction=creative_direction
        )
        
        ttype = selected_trans["type"]
        macro_preset = selected_trans.get("macro_preset")
        
        # Duration frame calculation
        if ttype in ("hard_cut", "cut", "jump_cut"):
            dur_frames = 0
        elif ttype == "macro":
            dur_frames = int((selected_trans.get("duration_ms", 500) / 1000.0) * frame_rate)
        else:
            # For dissolve/wipe, we might fall back to LLM feel if needed, but selector returns duration_ms
            dur_frames = int((selected_trans.get("duration_ms", 500) / 1000.0) * frame_rate)
            if dur_frames == 0:
                duration_map = {
                    "instant": 0,
                    "quick": int(6 * (frame_rate / 30)),
                    "medium": int(10 * (frame_rate / 30)),
                    "slow": int(15 * (frame_rate / 30)),
                }
                feel = trans.get("duration_feel", "medium")
                dur_frames = duration_map.get(feel, int(10 * (frame_rate / 30)))

        # Resolve the precise cut point
        cut_info = resolve_cut_point(
            incoming=block,
            outgoing=outgoing,
            ti_lookup=ti_lookup,
            beat_grid=beat_positions,
        )
        cut_time = cut_info["cut_time"]

        # Final beat-snap for non-word-end cuts
        beat_aligned = False
        snap_delta = 0.0
        if cut_info["method"] == "block-boundary":
            snapped, beat_aligned, snap_delta = snap_to_beat(
                cut_time, beat_positions,
            )
            cut_time = snapped
        elif "beat" in cut_info["method"]:
            beat_aligned = True

        trans_dict = {
            "transition_id": f"trans_{len(resolved)+1:03d}",
            "cut_point_timeline": round(cut_time, 3),
            "cut_point_original": round(original_tl, 3),
            "transition_type": ttype,
            "duration_frames": dur_frames,
            "beat_aligned": beat_aligned,
            "snap_delta_seconds": snap_delta,
            "placement_method": cut_info["method"],
            "word_beat_coincidence": cut_info["word_beat_coincidence"],
            "rationale": trans.get("rationale", ""),
        }
        if macro_preset:
            trans_dict["macro_preset"] = {
                "name": macro_preset.name,
                "file_path": macro_preset.file_path,
                "category": macro_preset.category,
                "tags": macro_preset.tags
            }
        resolved.append(trans_dict)

    return resolved


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    require_keys(data, ["music_selection"], "step_4_02_plan_transitions/post_bridge.py")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")
        
    creative = data.get("transition_creative")
    if not creative and "llm_raw_response" in data:
        try:
            parsed = json.loads(data["llm_raw_response"])
            creative = parsed if isinstance(parsed, list) else parsed.get("transition_creative", [])
        except Exception:
            creative = data["llm_raw_response"]
        
    if not isinstance(creative, list):
        print(f"  Warning: LLM returned invalid response for plan_transitions. Defaulting to empty list. Response was: {str(creative)[:100]}", file=sys.stderr)
        creative = []
        
    creative = [v for v in creative if isinstance(v, dict)]

    spine = data.get("timed_spine", {})
    spine_blocks = spine.get("structure", spine.get("audio_spine", {}).get("structure", []))
    total_cuts = max(0, len(spine_blocks) - 1)
    
    if total_cuts > 0 and len(creative) / total_cuts < 0.05:
        # We need semantic_analysis to detect scene boundaries
        semantic_data = data.get("semantic_analysis", {})
        if isinstance(semantic_data, dict) and "semantic_analysis" in semantic_data:
            sem_inner = semantic_data["semantic_analysis"]
            if isinstance(sem_inner, list):
                semantic_clips = sem_inner
            else:
                semantic_clips = sem_inner.get("clips", [])
        elif isinstance(semantic_data, list):
            semantic_clips = semantic_data
        else:
            semantic_clips = semantic_data.get("clips", [])
            
        semantic_lookup = {c.get("clip_id"): c for c in semantic_clips}
        
        existing_cuts = set()
        for t in creative:
            pos = t.get("cut_point_position")
            if pos is None:
                original_tl = t.get("cut_point_original", t.get("cut_point_timeline", 0.0))
                best_pos = None
                best_dist = float("inf")
                for b in spine_blocks:
                    dist = abs(b.get("timeline_start", 0.0) - original_tl)
                    if dist < best_dist:
                        best_dist = dist
                        best_pos = b.get("position")
                pos = best_pos
                t["cut_point_position"] = pos
            if pos is not None:
                existing_cuts.add(str(pos))
        
        for i in range(1, len(spine_blocks)):
            prev_block = spine_blocks[i-1]
            curr_block = spine_blocks[i]
            
            if str(curr_block.get("position", i)) in existing_cuts:
                continue
                
            def _get_cid(b):
                cid = b.get("clip_id") or b.get("content", {}).get("clip_id")
                if not cid and b.get("content", {}).get("segments"):
                    cid = b.get("content", {}).get("segments")[0].get("clip_id")
                return cid
                
            prev_cid = _get_cid(prev_block)
            curr_cid = _get_cid(curr_block)
            
            if not prev_cid or not curr_cid or prev_cid == curr_cid:
                continue
                
            prev_sem = semantic_lookup.get(prev_cid, {})
            curr_sem = semantic_lookup.get(curr_cid, {})
            
            prev_mood = prev_sem.get("mood", "")
            curr_mood = curr_sem.get("mood", "")
            prev_tags = set(prev_sem.get("tags", []) + prev_sem.get("keywords", []))
            curr_tags = set(curr_sem.get("tags", []) + curr_sem.get("keywords", []))
            
            mood_changed = prev_mood and curr_mood and prev_mood.lower() != curr_mood.lower()
            topic_shift = len(prev_tags & curr_tags) == 0 if prev_tags and curr_tags else False
            
            if mood_changed or topic_shift:
                creative.append({
                    "cut_point_position": curr_block.get("position", i),
                    "type": "cross_dissolve",
                    "duration_feel": "medium",
                    "rationale": "Default cross_dissolve added at scene boundary due to mood/topic shift"
                })

    spine = data.get("timed_spine", {})
    music = data.get("music_selection", {})
    temporal_raw = data.get("temporal_event_indices", [])
    temporal = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    fps = data.get("frame_rate", 30.0)
    
    min_trans = max(1, total_cuts // 3) if total_cuts > 0 else 0
    if len(creative) < min_trans:
        print(json.dumps({"error": f"Planned {len(creative)} transitions for {total_cuts} cuts. You MUST plan at least {min_trans} transitions.", "step": "4.02_bridge"}))
        sys.exit(1)
    
    # Extract new inputs
    creative_direction = data.get("creative_direction", {})
    brand_effect = data.get("brand_effect", {})

    result = resolve_transitions(creative, spine, music, temporal, fps, creative_direction, brand_effect)
    json.dump({"transition_spec": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
