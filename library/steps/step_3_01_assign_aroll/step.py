#!/usr/bin/env python3
"""
Step 3.1: Assign A-Roll Video to Speech Blocks

For each speech block in the audio spine, assigns the corresponding A-roll
video — the video from the same source file and timestamps as the speech audio.
Also assigns the hook block's video.

Classification: Deterministic / Data Transformation
Input:  { "audio_spine": {...}, "clip_catalog": [...] }
Output: { "a_roll_assignments": [...], "hook_assignment": {...} }
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from library.tools.delivery_format import resolve_delivery_format  # noqa: E402

# Fallback output specs, used only by callers that import the helpers
# directly. The step itself resolves the delivery format per project.
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FRAME_RATE = 30.0


def needs_conform(clip: dict, target_width: int, target_height: int, target_fps: float) -> bool:
    """
    Check if a clip needs conforming to target output specs.
    True if resolution, frame rate, or rotation differs from target.
    """
    w = clip.get("width", 0)
    h = clip.get("height", 0)
    rotation = clip.get("rotation", 0)
    fps = clip.get("frame_rate", 0)

    # Account for rotation: a 1920x1080 clip with -90 rotation IS portrait
    if abs(rotation) in (90, 270, -90, -270):
        # Swap width/height for rotated clips
        w, h = h, w

    if w != target_width or h != target_height:
        return True
    if fps and abs(fps - target_fps) > 0.1:
        return True
    return False


def assign_a_roll(audio_spine: dict, clip_catalog: list, target_width: int = 1080, target_height: int = 1920, target_fps: float = 30.0) -> dict:
    """
    Map speech blocks and hook to their A-roll video source files.
    """
    # Build clip lookup
    clip_lookup = {c["clip_id"]: c for c in clip_catalog}

    structure = audio_spine.get("structure", [])
    a_roll_assignments = []
    hook_assignment = None

    for block in structure:
        block_type = block["block_type"]
        if block_type not in ("hook", "speech"):
            continue

        # The spine contract puts the clip reference and the source range
        # on the BLOCK. Reading content.start_time / content.end_time -
        # keys mesh_spine does not write - gave the hook video_in ==
        # video_out == 0.0, i.e. a zero-length opening shot.
        clip_id = block["clip_id"]
        if not clip_id:
            raise ValueError(
                f"{block_type} block {block['position']} has no clip_id"
            )

        clip = clip_lookup.get(clip_id)
        if not clip:
            raise ValueError(
                f"Block {block['position']} references clip_id "
                f"'{clip_id}' not found in catalog"
            )

        video_in = float(block["source_start"])
        video_out = float(block["source_end"])

        clip_duration = float(clip.get("duration_seconds") or 0.0)
        if video_out > clip_duration + 0.5:  # 0.5s tolerance
            raise ValueError(
                f"Block {block['position']}: source range "
                f"{video_in:.3f}-{video_out:.3f}s runs past the end of "
                f"{clip_id} ({clip_duration:.3f}s)"
            )

        segment = {
            "clip_id": clip_id,
            "source_file": clip.get("source_file") or clip.get("path", ""),
            "video_in": video_in,
            "video_out": video_out,
            "duration_seconds": round(video_out - video_in, 3),
            # Explicit A/V link for this segment
            "link_group_id": block.get("link_group_id", str(uuid.uuid4())),
            "width": clip.get("width"),
            "height": clip.get("height"),
            "frame_rate": clip.get("frame_rate"),
            "rotation": clip.get("rotation", 0),
            "needs_conform": needs_conform(clip, target_width, target_height, target_fps),
        }

        if block_type == "hook":
            hook_assignment = {
                "spine_block_position": block["position"],
                "clip_id": clip_id,
            }

        a_roll_assignments.append({
            "spine_block_position": block["position"],
            "block_type": block_type,
            "timeline_start": block["timeline_start"],
            "timeline_end": block["timeline_end"],
            "timeline_start_frame": block.get("timeline_start_frame"),
            "timeline_end_frame": block.get("timeline_end_frame"),
            "duration_frames": block.get("duration_frames"),
            "video_segments": [segment],
        })


    # --- Verification ---
    # Every speech and hook block has a video assignment
    speech_blocks = [b for b in structure if b["block_type"] in ("speech", "hook")]
    assert len(a_roll_assignments) == len(speech_blocks), \
        f"Assignment count ({len(a_roll_assignments)}) != speech and hook blocks ({len(speech_blocks)})"

    # Hook has a video assignment
    if any(b["block_type"] == "hook" for b in structure):
        assert hook_assignment is not None, "Hook block exists but no hook_assignment created"

    # All source files exist in catalog
    all_clip_ids = set()
    for asgn in a_roll_assignments:
        for vs in asgn["video_segments"]:
            all_clip_ids.add(vs["clip_id"])
    if hook_assignment:
        all_clip_ids.add(hook_assignment["clip_id"])

    for cid in all_clip_ids:
        assert cid in clip_lookup, f"clip_id '{cid}' not found in catalog"

    # --- Duration invariant ---
    # For each A-roll assignment, the total source duration of its video
    # segments should match the timeline allocation. If they don't match,
    # warn loudly — this will cause speech truncation downstream.
    DURATION_TOLERANCE = 0.15  # seconds
    for asgn in a_roll_assignments:
        tl_dur = round(
            asgn.get("timeline_end", 0) - asgn.get("timeline_start", 0), 3
        )
        src_dur = round(
            sum(vs["duration_seconds"] for vs in asgn["video_segments"]), 3
        )
        delta = abs(src_dur - tl_dur)
        if delta > DURATION_TOLERANCE:
            print(
                f"WARNING: Duration invariant violation in block "
                f"{asgn['spine_block_position']}: "
                f"source={src_dur}s, timeline={tl_dur}s, delta={delta}s. "
                f"Speech will be truncated or have dead air. "
                f"Fix in step 2.5 (mesh spine).",
                file=sys.stderr,
            )

    return {
        "a_roll_assignments": a_roll_assignments,
        "hook_assignment": hook_assignment,
    }


def main():
    input_data = json.loads(sys.stdin.read())
    audio_spine = input_data["audio_spine"]
    clip_catalog = input_data["clip_catalog"]
    
    target_fps = input_data.get("project_fps", 30.0)
    # The frame the product ships in, not the frame the footage arrived
    # in. See library/tools/delivery_format.py.
    target_width, target_height = resolve_delivery_format(
        input_data.get("project_folder"))

    try:
        result = assign_a_roll(audio_spine, clip_catalog, target_width, target_height, target_fps)
    except ValueError as e:
        print(json.dumps({"error": str(e), "step": "3.1_assign_aroll"}))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
