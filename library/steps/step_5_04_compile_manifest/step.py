#!/usr/bin/env python3
"""
Manifest Compiler (Step 5.04)

Reads pipeline step outputs and compiles them into an assembly_manifest
compatible with the existing resolve_full_assembly.py + fcpxml_generator.py.

Usage:
  python step.py <pipeline_output_dir>
  # writes assembly_manifest.json to the same directory

All step output files follow the convention: step_X_YY.json
No version suffixes.
"""
import json
import os
import sys
import logging

logger = logging.getLogger(__name__)

# Instantaneous transitions - a cut has no duration by definition.
CUT_TRANSITION_TYPES = ("cut", "hard_cut", "jump_cut")

# Tracks whose clips must lie end-to-end. A3 is excluded: SFX are allowed
# to overlap and the timeline builder allocates extra audio tracks for them.
SINGLE_LANE_TRACKS = ("A3",)

# Add parent directories to path so we can import shared tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from tools.frame_utils import seconds_to_frame, convert_clip_to_frames, convert_subtitle_to_frames
from tools.manifest_validator import validate_manifest
from tools.pipeline_validation import require_keys
from tools.sfx_library import load_sfx_index, match_sfx_file

def apply_cohesion_adjustments(transitions_raw: list, cohesion_review: dict):
    if not cohesion_review or not cohesion_review.get("adjustments"):
        return
    for adj in cohesion_review["adjustments"]:
        if adj.get("target_step") == "transition_spec" and "target_index" in adj:
            idx = adj["target_index"]
            if 0 <= idx < len(transitions_raw):
                old_val = transitions_raw[idx].get(adj["field"])
                transitions_raw[idx][adj["field"]] = adj["suggested_value"]
                print(f"  Applied Cohesion Adjustment: Transition {idx} {adj['field']} {old_val} -> {adj['suggested_value']}", file=sys.stderr)


def _apply_manifest_qa_checks(manifest: dict):
    # Check 1: Subtitle Overlap Detection
    subtitles = manifest.get('subtitles', [])
    for i in range(len(subtitles) - 1):
        curr_end = subtitles[i].get('timeline_end', subtitles[i].get('timeline_end_seconds', 0))
        next_start = subtitles[i+1].get('timeline_start', subtitles[i+1].get('timeline_start_seconds', 0))
        if curr_end > next_start + 0.01:  # 10ms tolerance
            logger.warning(f"Subtitle overlap: sub {i} ends at {curr_end:.3f}s but sub {i+1} starts at {next_start:.3f}s (overlap: {curr_end - next_start:.3f}s)")
            # Clamp: set curr subtitle's end to next subtitle's start
            subtitles[i]['timeline_end'] = next_start

    # Check 1b: Clamp subtitle end times to project duration
    proj_dur = manifest.get("project", {}).get("duration_seconds", 0)
    if proj_dur > 0:
        for sub in subtitles:
            if sub.get("timeline_end", 0) > proj_dur:
                sub["timeline_end"] = proj_dur

    # Check 2: Track Clip Overlap / Duplicate Detection.
    # This used to silently DELETE clips that shared a timeline position.
    # When a key-mapping bug gave nine B-roll clips the position (0, 0),
    # eight of them were quietly dropped and the manifest looked healthy.
    # Colliding clips are now a compile error - the upstream step has to
    # place them properly.
    for track_name, track_data in manifest.get('tracks', {}).items():
        clips = track_data.get('clips', [])
        # A3 is a logical SFX bucket, not one physical track: the timeline
        # builder spreads overlapping SFX across A3, A4, ... so a riser
        # running under a whoosh is sound design, not a collision.
        if track_name not in SINGLE_LANE_TRACKS:
            for i in range(len(clips) - 1):
                curr_out = clips[i].get('timeline_out', clips[i].get('timeline_out_seconds', 0))
                next_in = clips[i+1].get('timeline_in', clips[i+1].get('timeline_in_seconds', 0))
                if curr_out > next_in + 0.01:
                    raise ValueError(
                        f"Track {track_name}: clip {i} "
                        f"({clips[i].get('label', '?')}) ends at {curr_out:.3f}s "
                        f"and overlaps clip {i+1} "
                        f"({clips[i+1].get('label', '?')}) at {next_in:.3f}s"
                    )
        positions = {}
        for clip in clips:
            pos = (clip.get('timeline_in', 0), clip.get('timeline_out', 0))
            if pos in positions:
                raise ValueError(
                    f"Track {track_name}: {clip.get('label', '?')} and "
                    f"{positions[pos]} both occupy {pos} - only one would "
                    f"be visible"
                )
            positions[pos] = clip.get('label', '?')

    # Check 3: Transition Type+Duration Enforcement
    resolved_transitions = manifest.get('transitions', [])
    empty_count = sum(1 for t in resolved_transitions if not t.get('transition_type'))
    if empty_count == len(resolved_transitions) and len(resolved_transitions) > 0:
        raise ValueError(f"All {len(resolved_transitions)} transitions have empty type - transition key mapping failed. Check plan_transitions output uses 'transition_type' key.")

    for t in resolved_transitions:
        # A cut IS zero-length. Only overlapping transitions need a
        # duration, and giving a cut 15 frames invented an effect the
        # editor never planned.
        if t.get('transition_type') in CUT_TRANSITION_TYPES:
            t['duration_frames'] = 0
        elif t.get('duration_frames', 0) <= 0:
            t['duration_frames'] = 15  # default 15 frames (~0.5s at 30fps)
            logger.warning(f"Transition at {t.get('cut_point_timeline', '?')}s had zero duration, defaulted to 15 frames")

    # Check 4: VFX Position Field Validation
    malformed_vfx = [
        v.get('effect_type', v.get('type', '?'))
        for v in manifest.get('vfx', [])
        if v.get('timeline_start') is None or v.get('timeline_end') is None
    ]
    if malformed_vfx:
        raise ValueError(
            f"{len(malformed_vfx)} VFX entries have no timeline position: "
            f"{malformed_vfx}"
        )



def _resolve_source(block_or_clip: dict, clip_lookup: dict) -> str:
    """Resolve source_file from source_file field or clip_id lookup.

    Used by compile_manifest().

    Resolution order:
      1. Direct 'source_file' field (with filename-only catalog fallback)
      2. 'source_clip_id' or 'clip_id' from clip_lookup
      3. Nested 'content.clip_id' from clip_lookup
    """
    if "source_file" in block_or_clip:
        sf = block_or_clip["source_file"]
        # If it's just a filename (no path separator), try catalog lookup
        if "/" not in sf:
            for path in clip_lookup.values():
                if path.endswith(sf):
                    return path
        return sf
    # Try source_clip_id or clip_id
    for key in ("source_clip_id", "clip_id"):
        cid = block_or_clip.get(key)
        if cid and cid in clip_lookup:
            return clip_lookup[cid]
    # Try content.clip_id
    content = block_or_clip.get("content") or {}
    cid = content.get("clip_id")
    if cid and cid in clip_lookup:
        return clip_lookup[cid]
    return ""


# Step outputs read from pipeline_data.json, keyed by step id. The
# per-step *.json files in pipeline_output are a dashboard export written
# best-effort; a step that ran before that export existed leaves no file.
# compile_manifest silently treated an absent file as an empty result -
# which is why it spent entire runs compiling against an empty catalog,
# and why every clip came out with no width, no height and no conform.
_STATE_OUTPUTS: dict = {}


def _load_state_outputs(out_dir: str) -> dict:
    """Read step_outputs from the project's pipeline_data.json."""
    state_path = os.path.join(os.path.dirname(out_dir), "pipeline_data.json")
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path) as f:
            return json.load(f).get("step_outputs", {})
    except (json.JSONDecodeError, IOError) as e:
        print(f"  WARNING: could not read {state_path}: {e}", file=sys.stderr)
        return {}


def load(out_dir, filename):
    """Load a step's output: pipeline state first, then the exported file.

    pipeline_data.json is authoritative. The per-step files in
    pipeline_output are written best-effort by the dashboard exporter and
    that export is allowed to fail while the state save succeeds, so a
    stale file must never win over the state that produced this run.
    """
    stem = filename.replace(".json", "")
    if stem in _STATE_OUTPUTS:
        return _STATE_OUTPUTS[stem]

    path = os.path.join(out_dir, filename)
    if os.path.exists(path):
        return _read_json(path)

    # Legacy fallback: try _v3 then _v2 suffixed versions
    for suffix in ("_v3", "_v2"):
        legacy_path = os.path.join(out_dir, f"{stem}{suffix}.json")
        if os.path.exists(legacy_path):
            print(
                f"  NOTE: Loading legacy file {stem}{suffix}.json "
                f"(rename to {filename})",
                file=sys.stderr,
            )
            return _read_json(legacy_path)

    return {}


def _read_json(path: str):
    with open(path) as f:
        return json.load(f)


def _assert_broll_positions_distinct(
    broll_assignments: list, broll_interjections: list,
) -> None:
    """Fail when the B-roll planner collapsed onto one timeline position.

    Overlap resolution deletes a clip that another fully covers, so a
    collapse would otherwise be indistinguishable from a deliberate drop
    by the time the manifest is counted.  This runs on the planner's own
    output, before anything has been resolved away, so nine assignments
    landing on one range is caught whatever the arithmetic downstream
    says.
    """
    seen = {}
    problems = []
    entries = [
        (f"broll_{a['spine_block_position']}",
         a["timeline_start"], a["timeline_end"])
        for a in broll_assignments
    ] + [
        (f"interjection_{i['over_spine_block_position']}",
         i["timeline_start"], i["timeline_end"])
        for i in broll_interjections
    ]

    for label, start, end in entries:
        position = (round(start, 3), round(end, 3))
        if position in seen:
            problems.append(
                f"{label} and {seen[position]} both claim "
                f"{position[0]}-{position[1]}s"
            )
        seen[position] = label

    if problems:
        raise ValueError(
            f"B-roll planner collapsed {len(entries)} clips onto repeated "
            f"timeline positions:\n  - " + "\n  - ".join(problems)
        )


def _assert_planner_output_preserved(
    manifest: dict,
    broll_planned: int,
    sfx_planned: int,
    vfx_planned: int,
    broll_dropped_by_overlap: list,
) -> None:
    """Fail when compilation silently loses what the planners produced.

    Only a drop the compiler made for a legitimate reason - an
    interjection deliberately covering a block's assignment - is
    discounted.  A clip lost because two of the same kind collapsed onto
    each other counts as a loss, because that is the failure this check
    exists for: nine assignments arriving as one invisible clip because
    the reader used keys the planner never wrote.
    """
    problems = []

    accounted = [
        d["dropped"] for d in broll_dropped_by_overlap
        if d["dropped_kind"] != d["covered_by_kind"]
    ]
    v2_count = len(manifest["tracks"]["V2"]["clips"])
    broll_expected = broll_planned - len(accounted)
    if broll_planned and v2_count < broll_expected:
        problems.append(
            f"B-roll: {broll_planned} planned, {len(accounted)} dropped as "
            f"deliberately covered ({', '.join(accounted) or 'none'}), so "
            f"{broll_expected} were expected but only {v2_count} clips "
            f"reached V2"
        )

    a3_count = len(manifest["tracks"]["A3"]["clips"])
    if sfx_planned and a3_count != sfx_planned:
        problems.append(
            f"SFX: {sfx_planned} planned but {a3_count} reached A3"
        )

    fusion_count = len(manifest["fusion_effects"]["per_clip"])
    if vfx_planned and fusion_count == 0:
        problems.append(
            f"VFX: {vfx_planned} planned but zero Fusion comps were built"
        )

    if problems:
        raise ValueError(
            "Planner output did not survive compilation:\n  - "
            + "\n  - ".join(problems)
        )


def _v1_label_at(v1_clips: list, timeline_time: float):
    """Label of the V1 clip covering a timeline position, or None."""
    for clip in v1_clips:
        if clip["timeline_in"] - 1e-6 <= timeline_time < clip["timeline_out"]:
            return clip["label"]
    return None


def _v1_index_ending_at(v1_clips: list, cut_time, tolerance: float = 0.25):
    """Index of the V1 clip whose tail sits at a cut point, or None."""
    if cut_time is None:
        return None
    best_idx = None
    best_diff = tolerance
    for i, clip in enumerate(v1_clips):
        diff = abs(clip["timeline_out"] - cut_time)
        if diff <= best_diff:
            best_diff = diff
            best_idx = i
    return best_idx


def _resolve_v2_overlaps(v2_clips: list, fps: float, kinds: dict) -> list:
    """Trim overlapping B-roll clips so V2 reads as a sequence, in place.

    A block assignment and an interjection can both claim the same stretch
    of timeline.  Only one can be visible, so trim the earlier clip to end
    where the later one starts (dropping it if nothing is left).

    Returns a record per drop naming what was dropped and what covered it,
    so the planner-preservation assertion can tell a deliberate drop from
    B-roll that vanished into a key-mapping bug.
    """
    v2_clips.sort(key=lambda c: c["timeline_in"])
    dropped = []
    keep = []
    for clip in v2_clips:
        if keep and clip["timeline_in"] < keep[-1]["timeline_out"] - 1e-6:
            prev = keep[-1]
            trimmed_out = clip["timeline_in"]
            if trimmed_out - prev["timeline_in"] < 1.0 / fps:
                logger.warning(
                    "V2: dropping %s - fully covered by %s",
                    prev["label"], clip["label"],
                )
                dropped.append({
                    "dropped": prev["label"],
                    "dropped_kind": kinds[prev["label"]],
                    "covered_by": clip["label"],
                    "covered_by_kind": kinds[clip["label"]],
                })
                keep.pop()
            else:
                logger.warning(
                    "V2: trimming %s to %.3fs so %s can start",
                    prev["label"], trimmed_out, clip["label"],
                )
                prev["source_out"] -= prev["timeline_out"] - trimmed_out
                prev["timeline_out"] = trimmed_out
                convert_clip_to_frames(prev, fps)
        keep.append(clip)
    v2_clips[:] = keep
    return dropped


def _conform_fields(clip_metadata: dict, clip_id, proj_res) -> dict:
    """Scale factor needed to fill the output frame, if any.

    Every A-roll assignment already carries a needs_conform flag and
    nothing has ever consumed it, so landscape source dropped into a
    vertical timeline rendered as a thin strip between black bars.
    """
    meta = clip_metadata.get(clip_id) or {}
    width = meta.get("width")
    height = meta.get("height")
    if not width or not height:
        return {}

    # A rotated clip is displayed with its axes swapped.
    if abs(meta.get("rotation", 0) or 0) in (90, 270):
        width, height = height, width

    target_w, target_h = proj_res[0], proj_res[1]
    if width <= 0 or height <= 0 or target_w <= 0 or target_h <= 0:
        return {}

    # Resolve fits the source inside the frame by default. To FILL it we
    # scale by whichever axis falls short.
    fit_scale = min(target_w / width, target_h / height)
    fill_scale = max(target_w / width, target_h / height)
    if fill_scale <= fit_scale * (1 + 1e-6):
        return {"needs_conform": False}

    return {
        "needs_conform": True,
        "source_width": width,
        "source_height": height,
        "fill_zoom": round(fill_scale / fit_scale, 4),
    }


def compile_manifest(out_dir: str) -> dict:
    global _STATE_OUTPUTS
    _STATE_OUTPUTS = _load_state_outputs(out_dir)

    # Load pipeline step outputs
    # Pipeline writes files as <step_name>.json; fall back to legacy step_X_YY.json
    spine_data = load(out_dir, "mesh_spine.json") or load(out_dir, "step_2_05.json")
    aroll_data = load(out_dir, "assign_aroll.json") or load(out_dir, "step_3_01.json")
    broll_data = load(out_dir, "select_broll.json") or load(out_dir, "step_3_02.json")
    speech_data = load(out_dir, "speech_sequence.json") or load(out_dir, "step_2_02.json")
    transition_data = load(out_dir, "plan_transitions.json") or load(out_dir, "step_4_02.json")
    sfx_data = load(out_dir, "plan_sfx.json") or load(out_dir, "step_4_04.json")
    music_data = load(out_dir, "music_selection.json") or load(out_dir, "step_2_04.json")
    subtitle_data = load(out_dir, "plan_subtitles.json") or load(out_dir, "step_4_01.json")

    # Optional enhancement specs
    vfx_data = load(out_dir, "plan_vfx.json") or load(out_dir, "step_4_03.json")
    color_data = load(out_dir, "color_grade.json") or load(out_dir, "step_5_01.json")
    audio_mix_data = load(out_dir, "audio_mix.json") or load(out_dir, "step_5_02.json")
    semantic_data = load(out_dir, "semantic_analysis.json") or load(out_dir, "step_1_03.json")

    # Subtitle overlay from step 4.05 (Remotion render)
    subtitle_overlay_data = load(out_dir, "render_subtitles.json") or load(out_dir, "step_4_05.json")

    # Motion graphics overlay from step 4.06 (Remotion render)
    motion_graphics_overlay_data = load(out_dir, "render_motion_graphics.json") or load(out_dir, "step_4_06.json")
    
    # Cohesion review
    cohesion_data = load(out_dir, "creative_cohesion.json") or load(out_dir, "step_5_03.json")

    spine = spine_data.get("audio_spine", {})
    structure = spine.get("structure", [])
    total_duration = structure[-1]["timeline_end"] if structure else 60.0

    # Build clip_id → source_file lookup from catalog
    catalog_data = load(out_dir, "catalog.json") or load(out_dir, "step_1_02.json")
    fps = catalog_data.get("project_fps", spine.get("frame_rate", 30.0))
    proj_res = catalog_data.get("project_resolution", [1080, 1920])
    
    if not catalog_data.get("clip_catalog"):
        raise ValueError(
            "No clip_catalog available - neither catalog.json in "
            f"{out_dir} nor a 'catalog' entry in pipeline_data.json. "
            "Without it every clip compiles with no dimensions and no "
            "conform decision."
        )

    clip_lookup = {}
    clip_metadata = {}
    for clip in catalog_data.get("clip_catalog", []):
        clip_lookup[clip["clip_id"]] = clip["path"]
        clip_metadata[clip["clip_id"]] = clip

    def resolve_source(block_or_clip):
        return _resolve_source(block_or_clip, clip_lookup)

    def get_clip_id(block_or_clip):
        if "source_clip_id" in block_or_clip: return block_or_clip["source_clip_id"]
        if "clip_id" in block_or_clip: return block_or_clip["clip_id"]
        content = block_or_clip.get("content") or {}
        if "clip_id" in content: return content["clip_id"]
        # Fallback to finding by source_file
        sf = _resolve_source(block_or_clip, clip_lookup)
        for cid, path in clip_lookup.items():
            if path == sf: return cid
        return None

    # Process semantic analysis for neural engine directives
    semantic_clips = semantic_data.get("semantic_analysis", {}).get("clips", [])
    if isinstance(semantic_data.get("semantic_analysis"), list):
        semantic_clips = semantic_data["semantic_analysis"]
    semantic_lookup = {c.get("clip_id"): c for c in semantic_clips}

    neural_engine_directives = {}

    def compute_neural_directives(clip_id, clip_entry):
        directives = {}
        sem = semantic_lookup.get(clip_id, {})
        meta = clip_metadata.get(clip_id, {})
        
        # Determine tags/description
        tags = sem.get("tags", []) + sem.get("keywords", [])
        description = sem.get("description", "")
        text_data = " ".join(tags).lower() + " " + description.lower()
        
        if "handheld" in text_data or "shaky" in text_data:
            directives["stabilize"] = True
            
        if "interview" in text_data or "speaker" in text_data or "subject" in text_data:
            directives["magic_mask"] = True
            
        width = meta.get("width", proj_res[0])
        height = meta.get("height", proj_res[1])
        proj_w, proj_h = proj_res[0], proj_res[1]
        proj_max = max(proj_w, proj_h)
        clip_max = max(width, height)
        # If low res
        if clip_max < proj_max * 0.8:
            directives["super_scale"] = 2
            
        if directives:
            neural_engine_directives[clip_entry["label"]] = directives

    a_roll_dict = {}
    for assignment in aroll_data.get("a_roll_assignments", []):
        pos = assignment.get("spine_block_position")
        if pos is not None:
            a_roll_dict[pos] = assignment
    hook_assignment = aroll_data.get("hook_assignment", {})
    if hook_assignment and "spine_block_position" in hook_assignment:
        a_roll_dict[hook_assignment["spine_block_position"]] = hook_assignment

    # ── V1: A-Roll clips (from spine speech blocks) ──
    v1_clips = []
    for block in structure:
        if block["block_type"] in ("speech", "hook"):
            content = block.get("content") or {}
            lgid = (block.get("link_group_id")
                    or content.get("link_group_id"))

            assignment = a_roll_dict.get(block.get("position"))
            
            if assignment and assignment.get("video_segments"):
                current_tl_in = block.get("timeline_start", 0.0)
                for seg_idx, seg in enumerate(assignment["video_segments"]):
                    dur = seg.get("duration_seconds", seg.get("video_out", 0) - seg.get("video_in", 0))
                    clip = {
                        "source_file": resolve_source(seg),
                        "source_in": seg.get("video_in", 0.0),
                        "source_out": seg.get("video_out", 0.0),
                        "timeline_in": current_tl_in,
                        "timeline_out": current_tl_in + dur,
                        "timeline_in_frame": None,
                        "timeline_out_frame": None,
                        "link_group_id": seg.get("link_group_id", lgid),
                        "label": f"{block['block_type']}_{block['position']}_seg{seg_idx}",
                    }
                    convert_clip_to_frames(clip, fps)
                    clip.update(_conform_fields(
                        clip_metadata, get_clip_id(seg), proj_res))
                    v1_clips.append(clip)
                    compute_neural_directives(get_clip_id(seg), clip)
                    current_tl_in += dur
            elif assignment:
                clip = {
                    "source_file": resolve_source(assignment),
                    "source_in": assignment.get("video_in", block.get("source_start", 0.0)),
                    "source_out": assignment.get("video_out", block.get("source_end", 0.0)),
                    "timeline_in": block.get("timeline_start", 0.0),
                    "timeline_out": block.get("timeline_end", 0.0),
                    "timeline_in_frame": block.get("timeline_start_frame"),
                    "timeline_out_frame": block.get("timeline_end_frame"),
                    "link_group_id": lgid,
                    "label": f"{block['block_type']}_{block['position']}",
                }
                if clip["timeline_in_frame"] is None:
                    convert_clip_to_frames(clip, fps)
                clip.update(_conform_fields(
                    clip_metadata, get_clip_id(assignment), proj_res))
                v1_clips.append(clip)
                compute_neural_directives(get_clip_id(assignment), clip)
            else:
                clip = {
                    "source_file": resolve_source(block),
                    "source_in": block.get("source_start", 0.0),
                    "source_out": block.get("source_end", 0.0),
                    "timeline_in": block.get("timeline_start", 0.0),
                    "timeline_out": block.get("timeline_end", 0.0),
                    "timeline_in_frame": block.get("timeline_start_frame"),
                    "timeline_out_frame": block.get("timeline_end_frame"),
                    "link_group_id": lgid,
                    "label": f"{block['block_type']}_{block['position']}",
                }
                if clip["timeline_in_frame"] is None:
                    convert_clip_to_frames(clip, fps)
                clip.update(_conform_fields(
                    clip_metadata, get_clip_id(block), proj_res))
                v1_clips.append(clip)
                compute_neural_directives(get_clip_id(block), clip)

    # ── V2: B-Roll clips ──
    # step_3_02 emits video_in/video_out (source domain) and
    # timeline_start/timeline_end (timeline domain).  Reading source_in /
    # timeline_in here instead - keys that assignment never had - made
    # every B-roll clip zero-length and invisible, and the duplicate-position
    # sweep below then collapsed all nine into one.
    broll_assignments = broll_data.get("b_roll_assignments", [])
    broll_interjections = broll_data.get("b_roll_interjections", [])
    _assert_broll_positions_distinct(broll_assignments, broll_interjections)
    v2_kinds = {}
    v2_clips = []
    for broll in broll_assignments:
        v2_clip = {
            "source_file": resolve_source(broll),
            "source_in": broll["video_in"],
            "source_out": broll["video_out"],
            "timeline_in": broll["timeline_start"],
            "timeline_out": broll["timeline_end"],
            "video_only": True,
            "label": f"broll_{broll['spine_block_position']}",
        }
        convert_clip_to_frames(v2_clip, fps)
        v2_clip.update(_conform_fields(
            clip_metadata, get_clip_id(broll), proj_res))
        v2_clips.append(v2_clip)
        v2_kinds[v2_clip["label"]] = "assignment"
        compute_neural_directives(get_clip_id(broll), v2_clip)
    # B-roll interjections carry their clip under `assigned_clip`
    for interj in broll_interjections:
        assigned = interj["assigned_clip"]
        v2_clip = {
            "source_file": resolve_source(assigned),
            "source_in": assigned["video_in"],
            "source_out": assigned["video_out"],
            "timeline_in": interj["timeline_start"],
            "timeline_out": interj["timeline_end"],
            "video_only": True,
            "label": f"interjection_{interj['over_spine_block_position']}",
        }
        convert_clip_to_frames(v2_clip, fps)
        v2_clip.update(_conform_fields(
            clip_metadata, get_clip_id(assigned), proj_res))
        v2_clips.append(v2_clip)
        v2_kinds[v2_clip["label"]] = "interjection"
        compute_neural_directives(get_clip_id(assigned), v2_clip)

    broll_dropped_by_overlap = _resolve_v2_overlaps(v2_clips, fps, v2_kinds)

    # ── A2: Music ──
    ms = music_data.get("music_selection", {})
    music_path = ms.get("audio_path", "")
    # Also check tracks[0].audio_path (step_2_04 nests it there)
    if not music_path and ms.get("tracks"):
        music_path = ms["tracks"][0].get("audio_path", "")
    music_clips = []
    if music_path:
        music_clips.append({
            "source_file": music_path,
            "source_in": 0.0,
            "source_out": total_duration,
            "timeline_in": 0.0,
            "timeline_out": total_duration,
            "label": "background_music",
        })

    # ── Subtitles (from Step 4.01 — single source of truth) ──
    subtitles = subtitle_data if isinstance(subtitle_data, list) else (
        subtitle_data.get("subtitles", []) or
        subtitle_data.get("subtitle_plan", {}).get("subtitle_entries", []) or
        subtitle_data.get("subtitle_plan", {}).get("subtitles", []) or
        (subtitle_data.get("subtitle_plan", []) if isinstance(subtitle_data.get("subtitle_plan"), list) else [])
    )
    subtitles.sort(key=lambda s: s.get("timeline_start", 0))
    for sub in subtitles:
        if "timeline_start_frame" not in sub:
            convert_subtitle_to_frames(sub, fps)

    # ── Transitions ──
    transitions = transition_data if isinstance(transition_data, list) else (
        transition_data.get("transitions", []) or
        transition_data.get("transition_spec", []) or
        (transition_data.get("transition_spec", {}).get("transitions", []) if isinstance(transition_data.get("transition_spec"), dict) else [])
    )
    apply_cohesion_adjustments(transitions, cohesion_data.get("cohesion_review", {}))
    for t in transitions:
        # Provide frames/seconds if not set, but do not override canonical keys
        if "duration_seconds" not in t and "duration" in t:
            t["duration_seconds"] = t["duration"]
        if "duration_frames" not in t and "duration" in t:
            t["duration_frames"] = int(t["duration"] * fps)

    # ── SFX ──
    sfx_container = sfx_data.get("sfx_spec", sfx_data) if isinstance(sfx_data, dict) else sfx_data
    sfx_list = sfx_container if isinstance(sfx_container, list) else (
        sfx_container.get("sfx", []) or sfx_container.get("sfx_list", [])
    )
    sfx_preset = (sfx_container if isinstance(sfx_container, dict) else sfx_data if isinstance(sfx_data, dict) else {}).get("fairlight_preset")
    sfx_ducking = (sfx_container if isinstance(sfx_container, dict) else sfx_data if isinstance(sfx_data, dict) else {}).get("music_ducking")
    if isinstance(sfx_ducking, list):
        sfx_ducking = {"ducking_curves": sfx_ducking}

    sfx_index = load_sfx_index()

    # Compile SFX into builder-compatible format.
    # There is ONE representation of SFX in the manifest: tracks.A3.clips.
    # The old top-level `sfx` list held only the entries that failed to
    # resolve, so a healthy run wrote `sfx: []` next to a populated A3 and
    # nothing could tell whether SFX had been planned at all.
    a3_clips = []
    sfx_unresolved = []
    for si, sfx_entry in enumerate(sfx_list):
        sfx_type = sfx_entry.get("sfx_type", "whoosh")
        # step_4_04 emits timeline_in/timeline_out (seconds).
        tl_start_sec = sfx_entry.get("timeline_in",
                                     sfx_entry.get("timeline_start"))
        if tl_start_sec is None:
            raise ValueError(
                f"SFX entry {sfx_entry.get('label', si)} has no timeline "
                f"position (timeline_in / timeline_start)"
            )
        vol_db = sfx_entry.get("volume_db", -14)

        source_file, lib_dur = match_sfx_file(sfx_type, sfx_index)

        tl_end_sec = sfx_entry.get("timeline_out",
                                   sfx_entry.get("timeline_end"))
        if not tl_end_sec:
            tl_end_sec = tl_start_sec + (lib_dur if lib_dur else sfx_entry.get("duration_seconds", 0.5))

        if source_file and os.path.exists(source_file):
            a3_clips.append({
                "source_file": source_file,
                "source_in": 0.0,
                "timeline_in": round(tl_start_sec, 3),
                "timeline_out": round(tl_end_sec, 3),
                "timeline_in_frame": seconds_to_frame(tl_start_sec, fps),
                "timeline_out_frame": seconds_to_frame(tl_end_sec, fps),
                "volume_db": vol_db,
                "label": sfx_entry.get("label",
                                       sfx_entry.get("sfx_id", f"sfx_{si+1:03d}")),
                "sfx_type": sfx_type,
            })
        else:
            sfx_unresolved.append(
                f"{sfx_entry.get('label', si)} (type={sfx_type}): no audio "
                f"file in the SFX library matched"
            )

    # An SFX tail running past the last picture pads the export with
    # black. Clamp to the edit's length.
    for clip in a3_clips:
        if clip["timeline_out"] > total_duration:
            logger.warning(
                "SFX %s ends at %.3fs, past the %.3fs edit - clamping",
                clip["label"], clip["timeline_out"], total_duration,
            )
            clip["timeline_out"] = round(total_duration, 3)
            clip["timeline_out_frame"] = seconds_to_frame(total_duration, fps)

    if sfx_unresolved:
        raise ValueError(
            f"{len(sfx_unresolved)} of {len(sfx_list)} planned SFX could "
            f"not be resolved to an audio file:\n  - "
            + "\n  - ".join(sfx_unresolved)
        )

    # ── VFX ──
    vfx_container = vfx_data.get("enhancement_spec", vfx_data) if isinstance(vfx_data, dict) else vfx_data
    vfx = vfx_container if isinstance(vfx_container, list) else (
        vfx_container.get("vfx", []) or vfx_container.get("visual_effects", [])
    )
    for v in vfx:
        if v.get("timeline_end", 0) > total_duration:
            v["timeline_end"] = total_duration

    # ── Build fusion_effects section ──
    # Per-clip VFX: every planned VFX becomes a Fusion comp on the V1 clip
    # it covers.  This used to require the planner to emit a `preset` key
    # that step_4_03 never wrote, so per_clip came out empty on every run
    # and the whole polish layer silently vanished.
    per_clip_effects = {}
    unplaced_vfx = []
    for v in vfx:
        label = _v1_label_at(v1_clips, v["timeline_start"])
        if label is None:
            unplaced_vfx.append(v)
            continue
        effect = per_clip_effects.setdefault(label, {})
        effect["_preset"] = v.get("preset", v.get("fusion_preset")) or v["effect_type"]
        effect.update(v.get("params", {}))

    if unplaced_vfx:
        raise ValueError(
            f"{len(unplaced_vfx)} VFX entries do not overlap any V1 clip: "
            + ", ".join(
                f"{v['effect_type']}@{v['timeline_start']}s"
                for v in unplaced_vfx
            )
        )

    # Fusion transitions: convert step_4_02 transitions to .comp format.
    # `after_clip` is the index of the OUTGOING V1 clip - the one whose
    # tail the transition sits on.
    fusion_transitions = []
    for t in transitions:
        comp_type = t.get("transition_type", "cut")
        if comp_type in CUT_TRANSITION_TYPES or not comp_type:
            continue

        cut_time = t.get("cut_point_timeline", t.get("cut_point_original"))
        after_clip = _v1_index_ending_at(v1_clips, cut_time)
        if after_clip is None:
            raise ValueError(
                f"Transition {t.get('transition_id', '?')} at {cut_time}s "
                f"does not sit at the end of any V1 clip"
            )

        trans_obj = {
            "type": comp_type,
            "after_clip": after_clip,
            "duration_frames": t.get("duration_frames", int(0.5 * fps)),
        }
        if "macro_preset" in t:
            trans_obj["macro_preset"] = t["macro_preset"]
        fusion_transitions.append(trans_obj)

    # Audio config
    audio_preset = sfx_preset or audio_mix_data.get("audio_mix_spec", {}).get("fairlight_preset", "")
    audio_config = {
        "fairlight_preset": audio_preset,
    }

    if v1_clips and music_clips:
        last_v1_end = max((c.get("timeline_out", 0) for c in v1_clips), default=0)
        for mc in music_clips:
            if mc.get("timeline_out", 0) > last_v1_end:
                mc["timeline_out"] = max(mc.get("timeline_in", 0), last_v1_end)
                mc["source_out"] = mc.get("source_in", 0) + (mc["timeline_out"] - mc.get("timeline_in", 0))
                print(f"  WARNING: Trimmed music to match last V1 clip end ({last_v1_end}s)", file=sys.stderr)

    # ── Compile ──
    manifest = {
        "project": {
            "name": "Pipeline_Edit",
            "resolution": proj_res,
            "frame_rate": fps,
            # 3dp, matching clip precision. Rounding the authoritative
            # duration COARSER than the clips it must contain is what made
            # a 3ms artifact the validator's only complaint for four runs.
            "duration_seconds": round(total_duration, 3),
        },
        "tracks": {
            "V1": {
                "label": "A-Roll",
                "clips": sorted(v1_clips, key=lambda c: c["timeline_in"]),
            },
            "V2": {
                "label": "B-Roll",
                "clips": sorted(v2_clips, key=lambda c: c["timeline_in"]),
            },
            "A1": {
                "label": "Speech",
                "clips": sorted(v1_clips, key=lambda c: c["timeline_in"]),
            },
            "A2": {
                "label": "Music",
                "clips": music_clips,
            },
            "A3": {
                "label": "SFX",
                "clips": sorted(a3_clips,
                                key=lambda c: c["timeline_in_frame"]),
            },
        },
        "subtitles": subtitles,
        "transitions": transitions,
        "vfx": vfx,
        "fusion_effects": {
            "per_clip": per_clip_effects,
            "transitions": fusion_transitions,
        },
        "neural_engine_directives": neural_engine_directives,
        "audio": audio_config,
        "color_grade": color_data.get("color_grade_spec", {}),
        "audio_mix": audio_mix_data.get("audio_mix_spec", {}),
        "music_ducking": sfx_ducking or {
            "speech_volume_db": -18,
            "gap_volume_db": -10,
        },
        "_spine_blocks": [
            {
                "timeline_start": b.get("timeline_start", 0),
                "timeline_end": b.get("timeline_end", 0),
                "block_type": b.get("block_type", ""),
                "music_behavior": "full" if b.get("block_type") in
                    ("transition_slot", "outro") else "ducked",
            }
            for b in structure
        ],
        "subtitle_overlay": subtitle_overlay_data.get(
            "subtitle_overlay", {}),
        "motion_graphics_overlay": motion_graphics_overlay_data.get(
            "motion_graphics_overlay", {}),
    }

    _apply_manifest_qa_checks(manifest)

    # Everything the planners produced must survive into the manifest.
    # Nine B-roll assignments becoming one invisible clip, and five SFX
    # becoming an empty list, both passed every check the pipeline had.
    _assert_planner_output_preserved(
        manifest,
        broll_planned=len(broll_assignments) + len(broll_interjections),
        sfx_planned=len(sfx_list),
        vfx_planned=len(vfx),
        broll_dropped_by_overlap=broll_dropped_by_overlap,
    )

    # Print summary
    v1_count = len(manifest["tracks"]["V1"]["clips"])
    v2_count = len(manifest["tracks"]["V2"]["clips"])
    sub_count = len(manifest["subtitles"])
    sub_source = "step_4_01" if subtitles else "none"
    print(f"Manifest compiled:", file=sys.stderr)
    print(f"  V1 (A-Roll): {v1_count} clips", file=sys.stderr)
    print(f"  V2 (B-Roll): {v2_count} clips", file=sys.stderr)
    print(f"  A2 (Music):  {len(music_clips)} clips", file=sys.stderr)
    print(f"  Subtitles:   {sub_count} (source: {sub_source})", file=sys.stderr)
    print(f"  Transitions: {len(transitions)}", file=sys.stderr)
    sfx_resolved = len(manifest["tracks"]["A3"]["clips"])
    print(f"  SFX:         {sfx_resolved} placed on A3", file=sys.stderr)
    print(f"  Duration:    {total_duration:.1f}s", file=sys.stderr)

    A1_clips = manifest["tracks"]["A1"]["clips"]
    V1_clips = manifest["tracks"]["V1"]["clips"]
    assert len(A1_clips) == len(V1_clips), f"A1/V1 parity failed: A1={len(A1_clips)} V1={len(V1_clips)}"

    sub_overlay = manifest.get("subtitle_overlay", {})
    for seg in sub_overlay.get("segments", []):
        if "overlay_path" in seg and not os.path.exists(seg["overlay_path"]):
            logger.warning(f"Subtitle overlay missing on disk: {seg['overlay_path']}")
            
    mg_overlay = manifest.get("motion_graphics_overlay", {})
    for seg in mg_overlay.get("segments", []):
        if "overlay_path" in seg and not os.path.exists(seg["overlay_path"]):
            logger.warning(f"MG overlay missing on disk: {seg['overlay_path']}")

    errors = validate_manifest(manifest)
    if errors:
        raise ValueError(f"Manifest validation failed with {len(errors)} errors:\n" + "\n".join(errors))

    return manifest


def main():
    if len(sys.argv) >= 2:
        # Standalone mode: read from filesystem directory
        out_dir = sys.argv[1]
        manifest = compile_manifest(out_dir)

        out_path = os.path.join(out_dir, "assembly_manifest.json")
        with open(out_path, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"\nWrote: {out_path}", file=sys.stderr)

        json.dump(manifest, sys.stdout, indent=2)
    else:
        # Orchestrator mode: read from stdin JSON
        inputs = json.loads(sys.stdin.read())

        if "project_folder" in inputs:
            out_dir = os.path.join(inputs["project_folder"], "pipeline_output")
            if os.path.isdir(out_dir):
                manifest = compile_manifest(out_dir)
                json.dump({"assembly_manifest": manifest}, sys.stdout, indent=2)
                return
        
        raise RuntimeError("compile_manifest requires project_folder in inputs and a valid pipeline_output dir")

if __name__ == "__main__":
    main()
