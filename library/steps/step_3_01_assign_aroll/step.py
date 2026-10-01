#!/usr/bin/env python3
"""
Step 3.1: Assign A-Roll Video to Speech Blocks

For each speech block in the audio spine, assigns the corresponding A-roll
video — the video from the same source file and timestamps as the speech audio.
Also assigns the hook block's video, and any picture-led block's video -
a picture block names its own clip span, so its picture is placed here
rather than left for B-roll.

Classification: Deterministic / Data Transformation
Input:  { "audio_spine": {...}, "clip_catalog": [...] }
Output: { "a_roll_assignments": [...], "hook_assignment": {...} }

A speech block sourced from a catalogued voiceover file (its `clip_id`
is an `audio_001` id) carries words but no picture: it gets a
VOICEOVER assignment - the audio span that plays on A1 - and no video
assignment. Its picture is B-roll's job, placed by step 3.02 over the
block's timeline range like any other block with no V1 picture.
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from library.tools.delivery_format import resolve_delivery_format  # noqa: E402
from library.tools.duration_tolerance import DURATION_TOLERANCE  # noqa: E402
from library.tools.footage_identity import is_audio_id  # noqa: E402
from library.tools.spine_contract import PICTURE_BLOCK_TYPES, SPEECH_BLOCK_TYPES  # noqa: E402

# TARGET_WIDTH / TARGET_HEIGHT / TARGET_FRAME_RATE were here, described as
# "used only by callers that import the helpers directly".  There were no
# such callers - zero references in library, tests, docs or scripts - so
# the comment was the only thing keeping a hardcoded 1080x1920@30 in a
# step whose own delivery format is resolved per project
# (`resolve_delivery_format` above; AGENTS.md 10.1: the delivery format is
# a property of the PRODUCT, not of the footage).  Removed 2026-09-12.
# `needs_conform` takes the target as arguments, which is the whole point.


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


def assign_a_roll(audio_spine: dict, clip_catalog: list, target_width: int, target_height: int, target_fps: float = 30.0, audio_catalog: list = None) -> dict:
    """
    Map speech blocks, hook and picture blocks to their A-roll video source files.

    The target frame is REQUIRED - the delivery format the caller
    resolved (`resolve_delivery_format`), never a shape literal here.

    `audio_catalog` is step 1.02's voiceover/music intake. A speech
    block whose `clip_id` names one of those files is voiceover: its
    words play from the audio file on A1 (a `voiceover_assignments`
    entry) and no video is placed here. Absent means the run predates
    the edge, and a voiceover-sourced block is refused naming the
    missing intake rather than misread as footage.
    """
    # Build clip lookup
    clip_lookup = {c["clip_id"]: c for c in clip_catalog}
    audio_lookup = {a.get("audio_id"): a for a in (audio_catalog or [])
                    if isinstance(a, dict) and a.get("audio_id")}

    structure = audio_spine.get("structure", [])
    a_roll_assignments = []
    voiceover_assignments = []
    hook_assignment = None

    for block in structure:
        block_type = block["block_type"]
        if block_type not in SPEECH_BLOCK_TYPES + PICTURE_BLOCK_TYPES:
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

        if is_audio_id(clip_id):
            # Voiceover over B-roll: words from the audio file, picture
            # from step 3.02's cover. A picture block naming an audio
            # file has no picture to cut - that plan is refused here,
            # where the cause is visible, not at compile as a hole.
            if block_type in PICTURE_BLOCK_TYPES:
                raise ValueError(
                    f"picture block {block['position']} names audio file "
                    f"'{clip_id}' - a picture-led moment with no picture "
                    f"to cut: cut it from footage, not from a sound-only "
                    f"file"
                )
            audio_entry = audio_lookup.get(clip_id)
            if audio_entry is None:
                raise ValueError(
                    f"Block {block['position']} references audio file "
                    f"'{clip_id}' not found in the audio catalog"
                )
            audio_in = float(block["source_start"])
            audio_out = float(block["source_end"])
            audio_duration = float(
                audio_entry.get("duration_seconds") or 0.0)
            if audio_duration and audio_out > audio_duration + 0.5:
                raise ValueError(
                    f"Block {block['position']}: source range "
                    f"{audio_in:.3f}-{audio_out:.3f}s runs past the end "
                    f"of {clip_id} ({audio_duration:.3f}s)"
                )
            voiceover_assignments.append({
                "spine_block_position": block["position"],
                "block_type": block_type,
                "audio_id": clip_id,
                "source_file": audio_entry.get("source_file")
                or audio_entry.get("path", ""),
                "audio_in": audio_in,
                "audio_out": audio_out,
                "duration_seconds": round(audio_out - audio_in, 3),
                "timeline_start": block["timeline_start"],
                "timeline_end": block["timeline_end"],
                "timeline_start_frame": block.get("timeline_start_frame"),
                "timeline_end_frame": block.get("timeline_end_frame"),
                "duration_frames": block.get("duration_frames"),
            })
            if block_type == "hook":
                hook_assignment = {
                    "spine_block_position": block["position"],
                    "clip_id": clip_id,
                }
            continue

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
    # Every speech, hook and picture block has a video assignment -
    # unless it is voiceover-sourced, in which case it has a voiceover
    # assignment instead and B-roll covers its picture.
    # Music blocks and transition slots carry no A-roll - their picture
    # is B-roll's job, placed by step 3.02.
    speech_blocks = [b for b in structure if b["block_type"] in SPEECH_BLOCK_TYPES + PICTURE_BLOCK_TYPES]
    assert len(a_roll_assignments) + len(voiceover_assignments) == len(speech_blocks), \
        f"Assignment count ({len(a_roll_assignments)} video + {len(voiceover_assignments)} voiceover) != speech, hook and picture blocks ({len(speech_blocks)})"

    # Hook has a video assignment
    if any(b["block_type"] == "hook" for b in structure):
        assert hook_assignment is not None, "Hook block exists but no hook_assignment created"

    # All source files exist in catalog
    all_clip_ids = set()
    for asgn in a_roll_assignments:
        for vs in asgn["video_segments"]:
            all_clip_ids.add(vs["clip_id"])

    for cid in all_clip_ids:
        assert cid in clip_lookup, f"clip_id '{cid}' not found in catalog"
    if hook_assignment:
        # A voiceover hook's words come from the audio intake, not
        # from footage - its id lives in the other catalog.
        hook_clip = hook_assignment["clip_id"]
        if is_audio_id(hook_clip):
            assert hook_clip in audio_lookup, f"audio_id '{hook_clip}' not found in audio catalog"
        else:
            assert hook_clip in clip_lookup, f"clip_id '{hook_clip}' not found in catalog"
    for asgn in voiceover_assignments:
        assert asgn["audio_id"] in audio_lookup, f"audio_id '{asgn['audio_id']}' not found in audio catalog"

    # --- Duration invariant ---
    # For each A-roll assignment, the total source duration of its video
    # segments should match the timeline allocation. If they don't match,
    # warn loudly — this will cause speech truncation downstream.
    # The slack is the pipeline's one duration tolerance
    # (library/tools/duration_tolerance.py), shared with step 3.03's
    # refusing gate so a warning here and a verdict there can never
    # disagree about what counts.
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
    for asgn in voiceover_assignments:
        tl_dur = round(
            asgn.get("timeline_end", 0) - asgn.get("timeline_start", 0), 3
        )
        src_dur = round(asgn.get("duration_seconds", 0.0), 3)
        delta = abs(src_dur - tl_dur)
        if delta > DURATION_TOLERANCE:
            print(
                f"WARNING: Duration invariant violation in voiceover "
                f"block {asgn['spine_block_position']}: "
                f"source={src_dur}s, timeline={tl_dur}s, delta={delta}s. "
                f"Speech will be truncated or have dead air. "
                f"Fix in step 2.5 (mesh spine).",
                file=sys.stderr,
            )

    return {
        "a_roll_assignments": a_roll_assignments,
        "hook_assignment": hook_assignment,
        "voiceover_assignments": voiceover_assignments,
    }


# ── Region-scoped re-assignment, and putting it back ────────────────

def splice_region_aroll(audio_spine: dict, clip_catalog: list,
                        stored_assignments: dict, scope,
                        project_folder: str = "",
                        project_fps: float = 30.0,
                        audio_catalog: list = None) -> dict:
    """Re-assign the blocks a region touches and splice them into
    `stored_assignments` (this step's recorded output).

    For a spine whose region was re-anchored - a re-indexed passage, a
    corrected source range - so the region's footage changes and every
    other block's assignment, `link_group_id` included, stays exactly as
    it was.  The report MEASURES that.

    Refuses rather than doing something surprising:

    - a region touching no block;
    - a block whose TIMELINE span moved: an assignment is placed at its
      block's `timeline_start`/`timeline_end`, and a moved block moves
      every block after it - that is a re-plan from `mesh_spine`, not a
      splice of A-roll;
    - everything `assign_a_roll` refuses for the region's blocks.

    Returns `{"a_roll_assignments", "hook_assignment",
    "voiceover_assignments", "splice": <report>}`.
    """
    from library.tools.plan_splice import (
        SpliceRefused,
        splice_entries,
        splice_report,
    )
    from library.tools.spine_contract import blocks_overlapping

    span = scope.region_span
    touched = blocks_overlapping(audio_spine.get("structure", []),
                                 span.start, span.end)
    if not touched:
        raise SpliceRefused(
            f"region {span} touches no spine block",
            "there is nothing in it to re-assign",
            "address a region inside the timeline")
    positions = [b["position"] for b in touched]

    width, height = resolve_delivery_format(project_folder)
    fresh = assign_a_roll(dict(audio_spine, structure=touched),
                          clip_catalog, width, height, project_fps,
                          audio_catalog=audio_catalog)

    key = "spine_block_position"
    stored = {k: stored_assignments.get(k) or []
              for k in ("a_roll_assignments", "voiceover_assignments")}
    moved = []
    for name, entries in stored.items():
        was = {str(e[key]): e for e in entries}
        for entry in fresh[name]:
            old = was.get(str(entry[key]))
            if old is not None and (
                    old["timeline_start"], old["timeline_end"]) != (
                    entry["timeline_start"], entry["timeline_end"]):
                moved.append(
                    f"block {entry[key]}: {old['timeline_start']}-"
                    f"{old['timeline_end']}s -> {entry['timeline_start']}-"
                    f"{entry['timeline_end']}s")
    if moved:
        raise SpliceRefused(
            "this splice would move blocks on the timeline",
            "an A-roll assignment sits at its block's timeline span, and "
            "a block that moved moves every block after it:\n  - "
            + "\n  - ".join(moved),
            "a re-timed spine is a re-plan: re-run assign_aroll at "
            "project scope, then the steps downstream of it")

    out = {}
    report = {}
    for name, entries in stored.items():
        out[name] = splice_entries(entries, fresh[name], positions, key,
                                   key)
        report[name] = splice_report(entries, out[name], positions, key)

    hook = stored_assignments.get("hook_assignment")
    targets = {str(p) for p in positions}
    if hook is None or str(hook[key]) in targets:
        hook = fresh["hook_assignment"]
    out["hook_assignment"] = hook

    out["splice"] = {
        "region": span.as_address(),
        "positions": report["a_roll_assignments"]["positions"],
        "outside_unchanged": all(r["outside_unchanged"]
                                 for r in report.values()),
        **report,
    }
    return out


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
        result = assign_a_roll(audio_spine, clip_catalog, target_width, target_height, target_fps, audio_catalog=input_data.get("audio_catalog"))
    except ValueError as e:
        print(json.dumps({"error": str(e), "step": "3.1_assign_aroll"}))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
