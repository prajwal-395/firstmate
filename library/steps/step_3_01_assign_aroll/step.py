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
import sys
import uuid

# Target output specs (from style spec)
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
        block_type = block.get("block_type")

        if block_type == "hook":
            # Hook is a speech snippet — assign its video
            content = block.get("content", {})
            clip_id = content.get("clip_id")

            if not clip_id:
                raise ValueError("Hook block has no clip_id")

            clip = clip_lookup.get(clip_id)
            if not clip:
                raise ValueError(f"Hook references clip_id '{clip_id}' not found in catalog")

            hook_assignment = {
                "spine_block_position": block["position"],
                "clip_id": clip_id,
                "source_file": clip["source_file"],
                "video_in": content.get("start_time", 0.0),
                "video_out": content.get("end_time", 0.0),
                "duration_seconds": block.get("duration_seconds", 0.0),
                "timeline_start_frame": block.get("timeline_start_frame"),
                "timeline_end_frame": block.get("timeline_end_frame"),
                "duration_frames": block.get("duration_frames"),
                # Explicit A/V link: the XMEML generator uses this to
                # emit reciprocal <link> blocks between the video
                # clipitem on V1 and its audio partner on A1.
                "link_group_id": str(uuid.uuid4()),
                "width": clip.get("width"),
                "height": clip.get("height"),
                "frame_rate": clip.get("frame_rate"),
                "rotation": clip.get("rotation", 0),
                "needs_conform": needs_conform(clip, target_width, target_height, target_fps),
            }

        elif block_type == "speech":
            # Speech block — assign video for each segment
            content = block.get("content", {})
            segments = content.get("segments", [])
            if not segments and "clip_id" in content:
                segments = [content]

            video_segments = []
            for seg in segments:
                clip_id = seg.get("clip_id")
                if not clip_id:
                    raise ValueError(
                        f"Speech segment in block {block['position']} has no clip_id"
                    )

                clip = clip_lookup.get(clip_id)
                if not clip:
                    raise ValueError(
                        f"Speech segment references clip_id '{clip_id}' not found in catalog"
                    )

                # Verify timestamps are within source file duration
                video_in = seg.get("start_time", seg.get("source_start", 0.0))
                video_out = seg.get("end_time", seg.get("source_end", 0.0))
                clip_duration = clip.get("duration_seconds", 0)

                if video_out > clip_duration + 0.5:  # 0.5s tolerance
                    print(
                        f"WARNING: Segment end ({video_out}s) exceeds clip "
                        f"duration ({clip_duration}s) for {clip_id}",
                        file=sys.stderr,
                    )

                video_segments.append({
                    "clip_id": clip_id,
                    "source_file": clip["source_file"],
                    "video_in": video_in,
                    "video_out": video_out,
                    "duration_seconds": round(video_out - video_in, 3),
                    # Explicit A/V link for this segment
                    "link_group_id": str(uuid.uuid4()),
                    "width": clip.get("width"),
                    "height": clip.get("height"),
                    "frame_rate": clip.get("frame_rate"),
                    "rotation": clip.get("rotation", 0),
                    "needs_conform": needs_conform(clip, target_width, target_height, target_fps),
                })

            # --- Hook overlap guard ---
            # If the hook is a teaser of the first body segment (common
            # shortform technique), the body should start AFTER the hook
            # ends to avoid repeating the same audio.
            if hook_assignment and video_segments:
                for vs in video_segments:
                    if (vs["clip_id"] == hook_assignment["clip_id"]
                            and vs["video_in"] < hook_assignment["video_out"]
                            and vs["video_out"] > hook_assignment["video_in"]):
                        old_in = vs["video_in"]
                        vs["video_in"] = hook_assignment["video_out"]
                        vs["duration_seconds"] = round(
                            vs["video_out"] - vs["video_in"], 3
                        )
                        if vs["duration_seconds"] <= 0:
                            print(
                                f"WARNING: Hook overlap consumed entire "
                                f"segment from {clip_id}",
                                file=sys.stderr,
                            )
                        else:
                            print(
                                f"INFO: Adjusted body segment {vs['clip_id']} "
                                f"start from {old_in}s to {vs['video_in']}s "
                                f"to avoid hook repetition",
                                file=sys.stderr,
                            )

            a_roll_assignments.append({
                "spine_block_position": block["position"],
                "block_type": "speech",
                "timeline_start": block.get("timeline_start", 0.0),
                "timeline_end": block.get("timeline_end", 0.0),
                "timeline_start_frame": block.get("timeline_start_frame"),
                "timeline_end_frame": block.get("timeline_end_frame"),
                "duration_frames": block.get("duration_frames"),
                "video_segments": video_segments,
            })

    # --- Verification ---
    # Every speech block has a video assignment
    speech_blocks = [b for b in structure if b["block_type"] == "speech"]
    assert len(a_roll_assignments) == len(speech_blocks), \
        f"Assignment count ({len(a_roll_assignments)}) != speech blocks ({len(speech_blocks)})"

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
        "total_a_roll_segments": sum(
            len(a["video_segments"]) for a in a_roll_assignments
        ),
    }


def main():
    input_data = json.loads(sys.stdin.read())
    audio_spine = input_data["audio_spine"]
    clip_catalog = input_data["clip_catalog"]
    
    target_fps = input_data.get("project_fps", 30.0)
    target_width = input_data.get("project_resolution", [1080, 1920])[0]
    target_height = input_data.get("project_resolution", [1080, 1920])[1]

    try:
        result = assign_a_roll(audio_spine, clip_catalog, target_width, target_height, target_fps)
    except ValueError as e:
        print(json.dumps({"error": str(e), "step": "3.1_assign_aroll"}))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
