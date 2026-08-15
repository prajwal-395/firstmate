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
from library.tools.spine_contract import (
    block_word_end_times_timeline,
    is_speech_block,
)


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
    beat_grid: list,
) -> dict:
    """Find the precise cut point for a transition.

    Examines the OUTGOING block (the one before the cut) to find the
    natural end point from its own word timings.  The spine carries those
    timings directly (see library/tools/spine_contract.py), so this reads
    `outgoing["word_timestamps"]` rather than re-deriving them from a
    temporal index keyed by a clip_id the block used not to expose.

    Args:
        incoming: The incoming spine block
        outgoing: The outgoing spine block
        beat_grid: List of beat positions in timeline domain

    Returns:
        {
            "cut_time": float,
            "method": str,
            "word_beat_coincidence": bool
        }
    """
    incoming_start = incoming["timeline_start"]

    # ── Speech blocks: cut at the last word's end ──
    if is_speech_block(outgoing):
        tl_end = outgoing["timeline_end"]
        block_word_ends_tl = block_word_end_times_timeline(outgoing)

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


def _resolve_cut_block_index(trans: dict, spine_blocks: list):
    """Resolve a creative transition entry to the INCOMING block's index.

    A cut sits at the boundary before a block, so index 0 (the first
    block) is never a valid cut point.  Returns None when the entry names
    no boundary at all - previously a missing position silently defaulted
    to timeline 0.0, which collapsed every transition onto one boundary.
    """
    pos = trans.get("cut_point_position")
    if pos is not None:
        for i, b in enumerate(spine_blocks):
            if str(b["position"]) == str(pos):
                return i if i > 0 else None
        # Position didn't match any spine label - fall through to
        # timeline-based matching using it as a timeline timestamp.
        if isinstance(pos, (int, float)):
            candidates = [
                (abs(b["timeline_start"] - float(pos)), i)
                for i, b in enumerate(spine_blocks)
                if i > 0
            ]
            if candidates:
                return min(candidates)[1]

    for key in ("cut_point_original", "cut_point_timeline", "cut_time"):
        if trans.get(key) is not None:
            target = trans[key]
            candidates = [
                (abs(b["timeline_start"] - target), i)
                for i, b in enumerate(spine_blocks)
                if i > 0
            ]
            if not candidates:
                return None
            return min(candidates)[1]

    return None


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

    # Build beat grid if BPM available
    bpm = music_selection.get("tracks", [{}])[0].get("bpm", 0)
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

    from library.tools.transition_selector import select_transition
    from library.tools.transition_vocabulary import is_cut

    resolved = []
    seen_block_indices = set()
    last_drawn_time = -999.0
    
    for trans in creative_plan:
        block_idx = _resolve_cut_block_index(trans, spine_blocks)
        if block_idx is None:
            print(
                f"  Dropped transition {trans!r}: it names no spine "
                f"boundary (needs cut_point_position or cut_point_timeline)",
                file=sys.stderr,
            )
            continue

        # A cut is a boundary between two blocks. Two plan entries landing
        # on the same boundary are the same cut - stacking them is what
        # produced ten transitions all sitting at one timeline position.
        if block_idx in seen_block_indices:
            print(
                f"  Dropped duplicate transition at spine boundary "
                f"{spine_blocks[block_idx].get('position')!r}",
                file=sys.stderr,
            )
            continue
        seen_block_indices.add(block_idx)

        block = spine_blocks[block_idx]
        original_tl = block["timeline_start"]
        outgoing = spine_blocks[block_idx - 1]

        # Use the content-aware transition selector
        selected_trans = select_transition(
            from_clip=outgoing,
            to_clip=block,
            brand_effect=brand_effect,
            creative_direction=creative_direction,
            requested_type=trans.get("type", trans.get("transition_type", "")),
            last_drawn_time=last_drawn_time,
        )

        ttype = selected_trans["type"]
        if not is_cut(ttype):
            last_drawn_time = original_tl

        # Duration frame calculation
        if is_cut(ttype):
            dur_frames = 0
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
            # What the plan asked for, and why it is not what shipped.
            # The rationale used to be carried through unchanged onto a
            # transition it no longer described - "Standard dialogue cut"
            # sitting on a 15-frame dissolve.
            "requested_type": selected_trans["requested_type"],
            "downgrade_reason": selected_trans["downgrade_reason"],
        }
        resolved.append(trans_dict)

    resolved.sort(key=lambda t: t["cut_point_timeline"])
    for i, t in enumerate(resolved, start=1):
        t["transition_id"] = f"trans_{i:03d}"

    _assert_transitions_distinct(resolved)
    return resolved


def _assert_transitions_distinct(resolved: list) -> None:
    """Fail when every cut lands on the same timeline position.

    Ten transitions at 2.682s is not a plan; it is a collapse.  It used to
    pass because nothing downstream compared cut points to each other.
    """
    if len(resolved) < 2:
        return
    cut_points = {round(t["cut_point_timeline"], 3) for t in resolved}
    if len(cut_points) < len(resolved):
        raise ValueError(
            f"{len(resolved)} transitions resolved to only "
            f"{len(cut_points)} distinct cut point(s): "
            f"{sorted(cut_points)}. Each transition must sit at its own "
            f"spine boundary."
        )


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
    
    min_trans = max(1, total_cuts // 3) if total_cuts > 0 else 0

    # Count the DISTINCT boundaries the plan actually covers. Counting raw
    # entries let a plan of ten stacked duplicates look fully covered.
    covered = {
        idx for idx in (
            _resolve_cut_block_index(t, spine_blocks) for t in creative
        ) if idx is not None
    }

    if total_cuts > 0 and len(covered) < min_trans:
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
        
        existing_cuts = {
            str(spine_blocks[idx]["position"]) for idx in covered
        }

        for i in range(1, len(spine_blocks)):
            prev_block = spine_blocks[i-1]
            curr_block = spine_blocks[i]
            
            if str(curr_block["position"]) in existing_cuts:
                continue

            # The spine contract guarantees a top-level clip_id on every
            # block (None for non-speech), so there is one place to read it.
            prev_cid = prev_block["clip_id"]
            curr_cid = curr_block["clip_id"]


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
                    # A drawable type, not cross_dissolve: nothing on the
                    # Fusion route can mix two clips (see
                    # library/tools/transition_vocabulary.py).
                    "type": "defocus",
                    "duration_feel": "medium",
                    "rationale": "Default defocus added at scene boundary due to mood/topic shift"
                })

    spine = data.get("timed_spine", {})
    music = data.get("music_selection", {})
    temporal_raw = data.get("temporal_event_indices", [])
    temporal = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    fps = data.get("frame_rate", 30.0)
    
    covered = {
        idx for idx in (
            _resolve_cut_block_index(t, spine_blocks) for t in creative
        ) if idx is not None
    }
    if len(covered) < min_trans:
        print(json.dumps({"error": f"Planned {len(creative)} transitions covering only {len(covered)} of {total_cuts} spine boundaries. You MUST plan at least {min_trans} transitions at DISTINCT cut points, each naming its cut_point_position.", "step": "4.02_bridge"}))
        sys.exit(1)

    # Extract new inputs
    creative_direction = data.get("creative_direction", {})
    brand_effect = data.get("brand_effect", {})

    result = resolve_transitions(creative, spine, music, temporal, fps, creative_direction, brand_effect)
    json.dump({"transition_spec": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
