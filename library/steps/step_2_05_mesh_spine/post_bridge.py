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

# Add parent directories to path so we can import shared tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from tools.frame_utils import seconds_to_frame


def enrich_spine(spine: dict, speech_sequence: dict, music: dict) -> dict:
    """
    Enrich the LLM's creative spine with execution-layer data.
    """
    structure = spine.get("structure", [])

    # Build lookup: passage position → resolved passage data
    passage_lookup = {}
    if speech_sequence.get("hook_segment"):
        passage_lookup["hook"] = speech_sequence["hook_segment"]
    for p in speech_sequence.get("body_sequence", []):
        passage_lookup[p["position"]] = p

    enriched_blocks = []

    for block in structure:
        enriched = dict(block)  # shallow copy
        block_type = block.get("block_type")
        content = block.get("content") or {}

        if block_type in ("hook", "speech") and content:
            # Generate link_group_id for A/V synchronization.
            # This allows the XMEML generator to pair video and audio
            # clipitems using reciprocal <link> blocks.
            enriched["link_group_id"] = str(uuid.uuid4())

            passage_ref = content.get("passage_ref")
            if passage_ref is not None:
                passage = passage_lookup.get(passage_ref)
                if passage:
                    enriched["content"] = {
                        "passage_ref": passage_ref,
                        "text": passage.get("text", ""),
                        "clip_id": passage.get("clip_id"),
                        "source_start": passage.get("start_time"),
                        "source_end": passage.get("end_time"),
                        "v1_source_in": passage.get("start_time"),
                        "v1_source_out": passage.get("end_time"),
                        "source_duration": passage.get("duration_seconds"),
                        "alignment_method": passage.get("alignment_method"),
                        "word_timestamps": passage.get("word_timestamps", []),
                    }
                    # CRITICAL: Update block-level source boundaries to
                    # match the re-enriched passage data. Without this,
                    # the manifest compiler picks up stale pre-WhisperX
                    # values from the original LLM spine output, causing
                    # V1 clips to cut at wrong source positions.
                    enriched["source_start"] = passage.get("start_time")
                    enriched["source_end"] = passage.get("end_time")
                    # Also update source_file from the passage's clip
                    if passage.get("clip_id"):
                        enriched["source_clip_id"] = passage["clip_id"]
                    # ALWAYS sync block duration to actual speech.
                    # The word timestamps define the exact duration —
                    # the LLM's creative target should never override.
                    # If block is longer, unselected audio bleeds through.
                    # If block is shorter, speech gets clipped.
                    passage_end = passage.get("end_time")
                    passage_start = passage.get("start_time")
                    if passage_end is not None and passage_start is not None:
                        src_dur = passage_end - passage_start
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
                    print(
                        f"WARNING: passage_ref {passage_ref} not found "
                        f"in speech_sequence (block position "
                        f"{block.get('position')})",
                        file=sys.stderr,
                    )
            else:
                # Content has text but no passage ref — keep as-is
                enriched["content"] = dict(content)
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

    # Recalculate timeline positions from (potentially extended) durations.
    # Block extensions shift all subsequent blocks forward.
    cursor = 0.0
    for b in enriched_blocks:
        b["timeline_start"] = round(cursor, 3)
        dur = b.get("duration_seconds", 0)
        b["timeline_end"] = round(cursor + dur, 3)
        cursor += dur

    # ── Frame conversion ──
    # Add integer frame positions using a cumulative frame cursor.
    # This is the authoritative boundary where float seconds become
    # integer frames. All steps from Phase 3 onward should read
    # the _frame fields for timeline positions.
    fps = spine.get("frame_rate", 30.0)
    frame_cursor = 0
    for b in enriched_blocks:
        dur_frames = seconds_to_frame(b.get("duration_seconds", 0), fps)
        b["timeline_start_frame"] = frame_cursor
        b["timeline_end_frame"] = frame_cursor + dur_frames
        b["duration_frames"] = dur_frames
        frame_cursor += dur_frames

    # Recalculate total duration from enriched blocks
    total_dur = sum(b.get("duration_seconds", 0) for b in enriched_blocks)

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

    result = enrich_spine(spine, speech, music)
    result["timed_spine"] = result["audio_spine"]
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
