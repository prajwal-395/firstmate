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


# A word end counts as landing on a beat within this many seconds.
BEAT_COINCIDENCE_TOLERANCE = 0.05

# How far back from the end of a block's speech a beat-coincident word end
# may be taken. About one short word: the point is to nudge a cut onto the
# music, not to choose a different place to cut. See resolve_cut_point.
MAX_WORD_END_BACKTRACK = 0.35


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

            # Check for word-end + beat coincidence (the ideal cut).
            #
            # Only word ends NEAR the end of the block are candidates,
            # and the latest one wins. This loop used to scan the whole
            # block from its FIRST word and take whichever word end
            # happened to land on a beat, which is a relocation, not a
            # snap: on project 001 the cut planned for the end of the
            # 2.4s hook was placed at 0.196s - the end of its first word
            # - and the cut at 18.37s moved to 11.33s, dropping seven
            # seconds of speech. compile_manifest then failed with
            # "Transition trans_001 at 0.196s does not sit at the end of
            # any V1 clip", which is the only reason it was caught: the
            # record said `snap_delta_seconds: 0.0` throughout, because
            # the delta is only measured on the snap_to_beat path.
            #
            # A beat coincidence is a sub-word adjustment to a cut that
            # is already at the end of the speech. It must never be able
            # to move the cut somewhere else in the block.
            word_beat_coincidence = False
            if beat_grid:
                candidates = sorted(
                    (we for we in block_word_ends_tl
                     if 0 <= cut_time - we <= MAX_WORD_END_BACKTRACK),
                    reverse=True,
                )
                for we in candidates:
                    if any(abs(we - beat) < BEAT_COINCIDENCE_TOLERANCE
                           for beat in beat_grid):
                        cut_time = min(we, tl_end)
                        word_beat_coincidence = True
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
    music_analysis: dict = None,
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

    from library.tools.beat_grid import beat_positions as real_beat_positions

    # The REAL beat grid, from step 2.06's analysis of the actual track.
    # This used to synthesise [i * 60/bpm for i in ...] starting at t=0,
    # and no track's first beat lands at 0.000s - so every "beat-snapped"
    # cut was snapped to a grid offset from the music by the track's
    # lead-in. See library/tools/beat_grid.py.
    beat_positions = real_beat_positions(music_analysis)

    from library.tools.transition_selector import select_transition
    from library.tools.transition_vocabulary import is_cut

    resolved = []
    seen_block_indices = set()

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
        )

        ttype = selected_trans["type"]

        # Duration frame calculation
        if is_cut(ttype):
            dur_frames = 0
        else:
            # For dissolve/wipe, we might fall back to LLM feel if needed, but selector returns duration_ms
            dur_frames = int((selected_trans.get("duration_ms", 500) / 1000.0) * frame_rate)
            if dur_frames == 0:
                # How long a transition holds is pace, so it comes from
                # the plan or from the brand template - never from a word
                # this file picks. `duration_feel` used to default to
                # "medium", which handed every undeclared transition the
                # same third of a second on the pipeline's say-so.
                duration_map = {
                    "instant": 0,
                    "quick": int(6 * (frame_rate / 30)),
                    "medium": int(10 * (frame_rate / 30)),
                    "slow": int(15 * (frame_rate / 30)),
                }
                feel = trans.get("duration_feel")
                if feel in duration_map:
                    dur_frames = duration_map[feel]
                else:
                    print(
                        f"  Transition at spine boundary "
                        f"{block.get('position')!r} resolves to under one "
                        f"frame and the plan declares no duration_feel "
                        f"({feel!r}); leaving it at 0 frames rather than "
                        f"choosing a pace for it",
                        file=sys.stderr,
                    )

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
            # How far the resolved cut ended up from the block boundary
            # the plan named. `snap_delta_seconds` only measures the
            # snap_to_beat path, so a cut relocated by word-end matching
            # recorded 0.0 while having moved 2.2 seconds. Always
            # recorded, so a relocation is visible in the manifest
            # without re-deriving it.
            "displacement_seconds": round(cut_time - original_tl, 3),
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

    # There is NO minimum transition count, and no transition is ever
    # added to a plan that did not ask for one.
    #
    # This is where the third creative floor lived, and it outlived the
    # captain's ruling of 2026-08-20 for the same reason
    # `inject_default_ken_burns` did (#192): it was written in CODE, and
    # `tests/test_no_creative_floors.py` only read prompts. It did all
    # three of the things the ruling forbids at once:
    #
    #   * `min_trans = max(1, total_cuts // 3)` - a floor of one drawn
    #     transition per three cuts, chosen by a constant;
    #   * an injection loop that appended `{"type": "defocus",
    #     "duration_feel": "medium", "rationale": "Default defocus added
    #     at scene boundary due to mood/topic shift"}` to the model's plan
    #     wherever the semantic mood or the keyword tags differed;
    #   * and, if the padded plan still fell short, `sys.exit(1)` with
    #     "You MUST plan at least N transitions at DISTINCT cut points" -
    #     word for word the guard removed from plan_vfx.
    #
    # How many transitions a piece gets is a creative decision. An empty
    # plan is a legitimate answer: `transition_vocabulary.CUT_TYPES` draw
    # nothing, so an edit of nothing but hard cuts is the absence of
    # decoration (AGENTS.md 10.4), not a defect. Guarded by
    # tests/test_no_creative_floors.py.

    spine = data.get("timed_spine", {})
    music = data.get("music_selection", {})
    temporal_raw = data.get("temporal_event_indices", [])
    temporal = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    fps = data.get("frame_rate", 30.0)

    # Extract new inputs
    creative_direction = data.get("creative_direction", {})
    brand_effect = data.get("brand_effect", {})

    music_analysis = data.get("music_analysis", {})
    result = resolve_transitions(creative, spine, music, temporal, fps,
                                 creative_direction, brand_effect,
                                 music_analysis=music_analysis)
    json.dump({"transition_spec": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
