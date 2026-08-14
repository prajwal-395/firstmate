#!/usr/bin/env python3
"""
Step 2.5 Bridge: Enrich Audio Spine with Execution Data

Takes the LLM's creative spine output (which uses passage_ref, text_snippet,
and semantic-level decisions) and enriches it with technical execution data:
- Resolves passage_ref → clip_id, start_time, end_time, word_timestamps
- Injects resolved data from Step 2.3 into each speech block's content

This bridge sits between the LLM's creative output (Step 2.5) and the
deterministic timing calculator (Step 2.6).

Classification: Deterministic / Data Transformation
Idempotent: Yes

Input:  {
    "spine": <LLM output from 2.5>,
    "speech_sequence": <resolved output from 2.3>,
    "music_selection": <output from 2.4>
}
Output: {
    "audio_spine": <enriched spine ready for 2.6 calc_timing>
}
"""
import json
import sys
import uuid
import os
from library.tools.pipeline_validation import require_keys
from library.tools.spine_contract import validate_spine_blocks

# Add parent directories to path so we can import shared tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from tools.frame_utils import seconds_to_frame


def enrich_spine(spine: dict, speech_sequence: dict, music: dict, data: dict = None) -> dict:
    """
    Enrich the LLM's creative spine with execution-layer data.
    """
    structure = spine.get("structure", [])

    # Build lookup: passage position → resolved passage data
    passage_lookup = {}
    if speech_sequence.get("hook_segment"):
        passage_lookup["hook"] = speech_sequence["hook_segment"]
    for p in speech_sequence.get("body_sequence", []):
        pos = p.get("position")
        if pos:
            passage_lookup[pos] = p

    enriched_blocks = []

    unresolved = []

    for block in structure:
        enriched = dict(block)  # shallow copy
        block_type = block.get("block_type")
        content = block.get("content") or {}

        # Every block carries the full contract key set (see
        # library/tools/spine_contract.py). Non-speech blocks carry None -
        # the keys are never absent, so consumers can read them directly
        # instead of guessing where the clip reference lives.
        enriched["clip_id"] = None
        enriched["source_clip_id"] = None
        enriched["source_start"] = None
        enriched["source_end"] = None
        enriched["word_timestamps"] = []
        enriched["alignment_method"] = None

        if block_type in ("hook", "speech"):
            # Generate link_group_id for A/V synchronization.
            # This allows the XMEML generator to pair video and audio
            # clipitems using reciprocal <link> blocks.
            enriched["link_group_id"] = str(uuid.uuid4())

            passage_ref = content.get("passage_ref")
            passage = passage_lookup.get(passage_ref) if passage_ref is not None else None
            if passage is None:
                unresolved.append(
                    f"block position {block.get('position')!r} references "
                    f"passage_ref {passage_ref!r}, which is not in the "
                    f"speech_sequence (available: "
                    f"{sorted(passage_lookup, key=str)})"
                )
                continue

            clip_id = passage["clip_id"]
            src_start = passage["source_start"]
            src_end = passage["source_end"]
            words = passage["word_timestamps"]

            enriched["content"] = {
                "passage_ref": passage_ref,
                "text": passage.get("text", ""),
                "clip_id": clip_id,
                "source_start": src_start,
                "source_end": src_end,
                "v1_source_in": src_start,
                "v1_source_out": src_end,
                "source_duration": passage.get("duration_seconds"),
                "alignment_method": passage["alignment_method"],
                "word_timestamps": words,
            }
            # Block-level contract fields. These are what every downstream
            # step reads; `content` is kept for prompt/context rendering.
            enriched["clip_id"] = clip_id
            enriched["source_clip_id"] = clip_id
            enriched["source_start"] = src_start
            enriched["source_end"] = src_end
            enriched["word_timestamps"] = words
            enriched["alignment_method"] = passage["alignment_method"]

            # ALWAYS sync block duration to actual speech.
            # The word timestamps define the exact duration —
            # the LLM's creative target should never override.
            # If block is longer, unselected audio bleeds through.
            # If block is shorter, speech gets clipped.
            src_dur = src_end - src_start
            block_dur = enriched.get("duration_seconds", 0)
            if abs(src_dur - block_dur) > 0.05:
                enriched["duration_seconds"] = round(src_dur, 3)
                print(
                    f"  Block [{enriched.get('position')}]: "
                    f"synced duration {block_dur:.2f}s → "
                    f"{src_dur:.2f}s (word boundaries)",
                    file=sys.stderr,
                )
        else:
            enriched["content"] = dict(content) if content else None

        # Inject music reference
        if music and not enriched.get("music_track"):
            track_id = "music_01"
            tracks = music.get("tracks", [])
            if tracks and isinstance(tracks, list) and len(tracks) > 0:
                track_id = tracks[0].get("track_id", "music_01")
            elif music.get("track_id"):
                track_id = music.get("track_id")
            enriched["music_track"] = track_id

        enriched_blocks.append(enriched)

    if unresolved:
        raise ValueError(
            "mesh_spine could not resolve "
            f"{len(unresolved)} spine block(s) to speech passages:\n  - "
            + "\n  - ".join(unresolved)
        )

    # Recalculate timeline positions from (potentially extended) durations.
    # Block extensions shift all subsequent blocks forward.
    cursor = 0.0
    for b in enriched_blocks:
        b["timeline_start"] = round(cursor, 3)
        dur = b.get("duration_seconds", 0)
        b["timeline_end"] = round(cursor + dur, 3)
        cursor += dur

    # ── Frame conversion ──
    # Add integer frame positions using global timeline seconds.
    # This prevents frame rounding drift from accumulating over many blocks.
    # All steps from Phase 3 onward should read the _frame fields.
    fps = spine.get("frame_rate", 30.0)
    for b in enriched_blocks:
        start_f = seconds_to_frame(b["timeline_start"], fps)
        end_f = seconds_to_frame(b["timeline_end"], fps)
        b["timeline_start_frame"] = start_f
        b["timeline_end_frame"] = end_f
        b["duration_frames"] = end_f - start_f
        
    frame_cursor = enriched_blocks[-1]["timeline_end_frame"] if enriched_blocks else 0

    # Recalculate total duration from enriched blocks
    total_dur = sum(b.get("duration_seconds", 0) for b in enriched_blocks)

    from library.tools.duration_targets import get_target_duration_zone
    target_duration_zone = get_target_duration_zone(data or {})

    # The single gate on the spine contract. Every creative step downstream
    # reads these blocks directly, so a malformed spine stops here rather
    # than degrading silently in five different consumers.
    validate_spine_blocks(enriched_blocks, total_dur, target_duration_zone)

    return {
        "audio_spine": {
            "total_estimated_duration_seconds": round(total_dur, 2),
            "total_duration_frames": frame_cursor,
            "frame_rate": fps,
            "structure": enriched_blocks,
            "music_selection": music,
        }
    }


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    # C3 fix: Accept LLM output format. The LLM outputs {structure: [...],
    # total_estimated_duration_seconds: ...} directly, not nested under a
    # "spine" key. Support both formats for robustness.
    require_keys(data, ["speech_sequence", "music_selection"], "step_2_05_mesh_spine/post_bridge.py")
    if "spine" in data:
        spine_data = data["spine"]
    elif "structure" in data:
        # LLM output: structure is at root level
        spine_data = data
    else:
        # Generate dummy structure for auto mode
        seq = data.get("speech_sequence", {})
        dummy_struct = []
        if seq.get("hook_segment"):
            dummy_struct.append({
                "position": "hook",
                "block_type": "hook",
                "content": {"passage_ref": "hook"}
            })
        for i, p in enumerate(seq.get("body_sequence", [])):
            dummy_struct.append({
                "position": p.get("position", f"body_{i+1}"),
                "block_type": "speech",
                "content": {"passage_ref": p.get("position", f"body_{i+1}")}
            })
        spine_data = {"structure": dummy_struct}
    if not isinstance(spine_data, dict):
        raise ValueError("spine data must be a dictionary")
    require_keys(spine_data, ["structure"], "spine data")
    if not isinstance(spine_data["structure"], list):
        raise ValueError("spine.structure must be a list")

    # C3 fix: Use spine_data resolved above (handles both LLM direct output
    # and wrapped formats).
    spine = spine_data

    speech = data.get("speech_sequence", {})
    music = data.get("music_selection", {})

    result = enrich_spine(spine, speech, music, data)
    result["timed_spine"] = result["audio_spine"]
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
