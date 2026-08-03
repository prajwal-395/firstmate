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

def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")


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
    cut_position: int,
    block_lookup: dict,
    ti_lookup: dict,
    beat_grid: list,
) -> dict:
    """Find the precise cut point for a transition.

    Examines the OUTGOING block (the one before the cut) to find
    the natural end point based on signal data.

    Args:
        cut_position: Spine block position the transition leads INTO
        block_lookup: {position: block_dict}
        ti_lookup: {clip_id: temporal_index_dict}
        beat_grid: List of beat positions in timeline domain

    Returns:
        {
            "cut_time": float,
            "method": str,
            "word_beat_coincidence": bool
        }
    """
    incoming = block_lookup.get(cut_position, {})
    incoming_start = incoming.get("timeline_start", 0.0)

    # Find the outgoing block (position - 1)
    outgoing = block_lookup.get(cut_position - 1, {})
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
) -> list:
    """Resolve creative transition plan to execution specs.

    For each transition:
    1. Find the outgoing spine block
    2. If speech: cut at last word's end time
    3. Beat-snap (prefer word-end + beat coincidence)
    4. Resolve duration_feel to frame count
    """
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

    # Duration feel → frame count mapping
    duration_map = {
        "instant": 0,
        "quick": int(6 * (frame_rate / 30)),
        "medium": int(10 * (frame_rate / 30)),
        "slow": int(15 * (frame_rate / 30)),
    }

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
                dist = abs(b["timeline_start"] - original_tl)
                if dist < best_dist:
                    best_dist = dist
                    best_pos = b["position"]
            pos = best_pos

        block = block_lookup.get(pos, {})
        original_tl = block.get("timeline_start", 0.0)

        ttype = trans.get("transition_type", "hard_cut")
        feel = trans.get("duration_feel", "instant")
        dur_frames = duration_map.get(feel, 0)

        # If hard cut, override frames to 0
        if ttype in ("hard_cut", "cut", "jump_cut"):
            dur_frames = 0

        # Resolve the precise cut point
        cut_info = resolve_cut_point(
            pos, block_lookup, ti_lookup, beat_positions,
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

        resolved.append({
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
        })

    return resolved


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    _require_keys(data, ["transition_creative", "music_selection"], "Input data")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")
    if not isinstance(data.get("transition_creative", []), list):
        raise ValueError("transition_creative must be a list")
    for trans in data.get("transition_creative", []):
        if not isinstance(trans, dict):
            raise ValueError("Items in transition_creative must be dictionaries")

    creative = data.get("transition_creative", [])
    spine = data.get("timed_spine", {})
    music = data.get("music_selection", {})
    temporal_raw = data.get("temporal_event_indices", [])
    temporal = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    fps = data.get("frame_rate", 30.0)

    result = resolve_transitions(creative, spine, music, temporal, fps)
    json.dump({"transition_spec": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
