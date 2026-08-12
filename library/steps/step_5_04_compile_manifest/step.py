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

# Add parent directories to path so we can import shared tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from tools.frame_utils import seconds_to_frame, convert_clip_to_frames, convert_subtitle_to_frames
from tools.manifest_validator import validate_manifest
from tools.pipeline_validation import require_keys

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
            logger.error(f"Subtitle overlap: sub {i} ends at {curr_end:.3f}s but sub {i+1} starts at {next_start:.3f}s (overlap: {curr_end - next_start:.3f}s)")
            # Clamp: set curr subtitle's end to next subtitle's start
            subtitles[i]['timeline_end'] = next_start

    # Check 2: Track Clip Overlap/Duplicate Detection
    for track_name, track_data in manifest.get('tracks', {}).items():
        clips = track_data.get('clips', [])
        for i in range(len(clips) - 1):
            curr_out = clips[i].get('timeline_out', clips[i].get('timeline_out_seconds', 0))
            next_in = clips[i+1].get('timeline_in', clips[i+1].get('timeline_in_seconds', 0))
            if curr_out > next_in + 0.01:
                logger.error(f"Track {track_name}: clip {i} ends at {curr_out:.3f}s overlaps clip {i+1} at {next_in:.3f}s")
        # Also check for exact duplicate positions
        positions = [(c.get('timeline_in',0), c.get('timeline_out',0)) for c in clips]
        seen = set()
        deduped = []
        for j, pos in enumerate(positions):
            if pos not in seen:
                seen.add(pos)
                deduped.append(clips[j])
            else:
                logger.warning(f"Track {track_name}: removing duplicate clip at position {pos}")
        track_data['clips'] = deduped

    # Check 3: Transition Type+Duration Enforcement
    resolved_transitions = manifest.get('transitions', [])
    empty_count = sum(1 for t in resolved_transitions if not t.get('transition_type'))
    if empty_count == len(resolved_transitions) and len(resolved_transitions) > 0:
        raise ValueError(f"All {len(resolved_transitions)} transitions have empty type - transition key mapping failed. Check plan_transitions output uses 'transition_type' key.")
    
    for t in resolved_transitions:
        if t.get('duration_frames', 0) <= 0:
            t['duration_frames'] = 15  # default 15 frames (~0.5s at 30fps)
            logger.warning(f"Transition at {t.get('cut_point_timeline', '?')}s had zero duration, defaulted to 15 frames")

    # Check 4: VFX Position Field Validation
    valid_vfx = []
    for v in manifest.get('vfx', []):
        has_position = any(v.get(k) is not None for k in ['timeline_in', 'start', 'timeline_start', 'start_seconds'])
        has_duration = any(v.get(k) is not None for k in ['duration', 'duration_seconds', 'end', 'timeline_end'])
        if has_position and has_duration:
            valid_vfx.append(v)
        else:
            logger.warning(f"VFX '{v.get('effect_type', v.get('type', '?'))}' dropped - missing position or duration")
    manifest['vfx'] = valid_vfx



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


def load(out_dir, filename):
    """Load a JSON file from the pipeline output directory.

    Tries the canonical filename first, then falls back to legacy
    suffixed versions (_v3, _v2) for backward compatibility during
    the transition period.
    """
    path = os.path.join(out_dir, filename)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)

    # Legacy fallback: try _v3 then _v2 suffixed versions
    stem = filename.replace(".json", "")
    for suffix in ("_v3", "_v2"):
        legacy_path = os.path.join(out_dir, f"{stem}{suffix}.json")
        if os.path.exists(legacy_path):
            print(
                f"  NOTE: Loading legacy file {stem}{suffix}.json "
                f"(rename to {filename})",
                file=sys.stderr,
            )
            with open(legacy_path) as f:
                return json.load(f)

    return {}


def _match_sfx_file(sfx_type, sfx_index_entries):
    """Match sfx_type to an actual audio file from the library.

    Uses keyword matching against descriptions, folder categories,
    and filenames. Returns (file_path, duration) or (None, None).
    """
    if not sfx_index_entries:
        return None, None

    # Type → search keywords mapping
    type_keywords = {
        "whoosh":         ["whoosh", "swish", "air", "wind", "sweep"],
        "swish":          ["swish", "whoosh", "sweep"],
        "bass_impact":    ["impact", "bass", "hit", "boom", "thud",
                           "punch", "slam"],
        "riser":          ["riser", "rise", "swell", "build", "tension"],
        "click":          ["click", "tick", "tap", "snap"],
        "tick":           ["tick", "click", "tap"],
        "reverse_cymbal": ["reverse", "cymbal", "crash"],
        "swell":          ["swell", "pad", "atmosphere", "rise"],
    }
    keywords = type_keywords.get(sfx_type, [sfx_type])

    # Score each candidate
    best_score = 0
    best_entry = None
    for entry in sfx_index_entries:
        score = 0
        desc = (entry.get("description", "") or "").lower()
        cat = (entry.get("folder_category", "") or "").lower()
        fname = (entry.get("file", "") or "").lower()
        searchable = f"{desc} {cat} {fname}"

        for kw in keywords:
            if kw in searchable:
                score += 1

        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score > 0:
        path = best_entry.get("path", "")
        dur = (best_entry.get("technical", {})
               .get("basic", {}).get("duration", 0.5))
        return path, dur

    return None, None

def compile_manifest(out_dir: str) -> dict:
    # Load pipeline step outputs
    spine_data = load(out_dir, "step_2_05.json")
    aroll_data = load(out_dir, "step_3_01.json")
    broll_data = load(out_dir, "step_3_02.json")
    speech_data = load(out_dir, "step_2_02.json")
    transition_data = load(out_dir, "step_4_02.json")
    sfx_data = load(out_dir, "step_4_04.json")
    music_data = load(out_dir, "step_2_04.json")
    subtitle_data = load(out_dir, "step_4_01.json")

    # Optional enhancement specs
    vfx_data = load(out_dir, "step_4_03.json")
    color_data = load(out_dir, "step_5_01.json")
    audio_mix_data = load(out_dir, "step_5_02.json")
    semantic_data = load(out_dir, "step_1_03.json")

    # Subtitle overlay from step 4.05 (Remotion render)
    subtitle_overlay_data = load(out_dir, "step_4_05.json")

    # Motion graphics overlay from step 4.06 (Remotion render)
    motion_graphics_overlay_data = load(out_dir, "step_4_06.json")
    
    # Cohesion review
    cohesion_data = load(out_dir, "step_5_03.json")

    spine = spine_data.get("audio_spine", {})
    structure = spine.get("structure", [])
    total_duration = structure[-1]["timeline_end"] if structure else 60.0

    # Build clip_id → source_file lookup from catalog
    catalog_data = load(out_dir, "step_1_02.json")
    fps = catalog_data.get("project_fps", spine.get("frame_rate", 30.0))
    proj_res = catalog_data.get("project_resolution", [1080, 1920])
    
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
                v1_clips.append(clip)
                compute_neural_directives(get_clip_id(block), clip)

    # ── V2: B-Roll clips ──
    v2_clips = []
    for broll in broll_data.get("b_roll_assignments", []):
        v2_clip = {
            "source_file": resolve_source(broll),
            "source_in": broll.get("source_in", 0),
            "source_out": broll.get("source_out", 0),
            "timeline_in": broll.get("timeline_in", 0),
            "timeline_out": broll.get("timeline_out", 0),
            "video_only": True,
            "label": f"broll_{broll.get('spine_block_position', 0)}",
        }
        convert_clip_to_frames(v2_clip, fps)
        v2_clips.append(v2_clip)
        compute_neural_directives(get_clip_id(broll), v2_clip)
    # B-roll interjections
    for interj in broll_data.get("b_roll_interjections", []):
        v2_clip = {
            "source_file": resolve_source(interj),
            "source_in": interj.get("source_in", 0),
            "source_out": interj.get("source_out", 0),
            "timeline_in": interj.get("timeline_in", 0),
            "timeline_out": interj.get("timeline_out", 0),
            "video_only": True,
            "label": f"interjection_{interj.get('over_spine_block_position', 0)}",
        }
        convert_clip_to_frames(v2_clip, fps)
        v2_clips.append(v2_clip)
        compute_neural_directives(get_clip_id(interj), v2_clip)

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
    subtitles = subtitle_data if isinstance(subtitle_data, list) else subtitle_data.get("subtitles", [])
    subtitles.sort(key=lambda s: s.get("timeline_start", 0))
    for sub in subtitles:
        if "timeline_start_frame" not in sub:
            convert_subtitle_to_frames(sub, fps)

    # ── Transitions ──
    transitions = transition_data if isinstance(transition_data, list) else transition_data.get("transitions", [])
    apply_cohesion_adjustments(transitions, cohesion_data.get("cohesion_review", {}))
    for t in transitions:
        # Provide frames/seconds if not set, but do not override canonical keys
        if "duration_seconds" not in t and "duration" in t:
            t["duration_seconds"] = t["duration"]
        if "duration_frames" not in t and "duration" in t:
            t["duration_frames"] = int(t["duration"] * fps)

    # ── SFX ──
    sfx_list = sfx_data if isinstance(sfx_data, list) else sfx_data.get("sfx", [])
    sfx_preset = sfx_data.get("fairlight_preset") if isinstance(sfx_data, dict) else None
    sfx_ducking = sfx_data.get("music_ducking") if isinstance(sfx_data, dict) else None
    if isinstance(sfx_ducking, list):
        sfx_ducking = {"ducking_curves": sfx_ducking}

    sfx_library_path = os.environ.get("PIPELINE_SFX_LIBRARY", "")
    if not sfx_library_path:
        try:
            from tools.paths import sfx_library_path as _sfx_path_fn
            sfx_library_path = _sfx_path_fn()
        except ImportError:
            sfx_library_path = ""

    # Load SFX library index for type → file resolution
    sfx_index = []
    sfx_index_path = os.path.join(sfx_library_path, "sfx_index.json")
    if os.path.exists(sfx_index_path):
        try:
            with open(sfx_index_path) as f:
                sfx_index = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    elif os.path.isdir(os.path.join(sfx_library_path, "profiles")):
        # Fallback: load individual profiles
        import glob
        for pf in sorted(glob.glob(
                os.path.join(sfx_library_path, "profiles", "*.json"))):
            if os.path.basename(pf) in (
                    "sfx_index.json", "library_analysis.json",
                    "library_semantic.json"):
                continue
            try:
                with open(pf) as f:
                    sfx_index.append(json.load(f))
            except (json.JSONDecodeError, IOError):
                pass



    # Compile SFX into builder-compatible format
    a3_clips = []
    sfx_passthrough = []  # Keep raw SFX for reference
    for si, sfx_entry in enumerate(sfx_list):
        sfx_type = sfx_entry.get("sfx_type", "whoosh")
        tl_start_sec = sfx_entry.get("timeline_start", 0.0)
        vol_db = sfx_entry.get("volume_db", -14)

        source_file, lib_dur = _match_sfx_file(sfx_type, sfx_index)
        
        tl_end_sec = sfx_entry.get("timeline_end")
        if not tl_end_sec:
            tl_end_sec = tl_start_sec + (lib_dur if lib_dur else sfx_entry.get("duration_seconds", 0.5))

        if source_file and os.path.exists(source_file):
            a3_clips.append({
                "source_file": source_file,
                "source_in": 0.0,
                "timeline_in_frame": seconds_to_frame(tl_start_sec, fps),
                "timeline_out_frame": seconds_to_frame(tl_end_sec, fps),
                "volume_db": vol_db,
                "label": sfx_entry.get("sfx_id", f"sfx_{si+1:03d}"),
                "sfx_type": sfx_type,
            })
        else:
            # File not found — keep as warning in passthrough
            sfx_passthrough.append({
                **sfx_entry,
                "_warning": f"No audio file resolved for sfx_type={sfx_type}",
            })

    if sfx_passthrough:
        print(f"  WARNING: {len(sfx_passthrough)} SFX entries "
              f"could not be resolved to audio files",
              file=sys.stderr)

    # ── VFX ──
    vfx = vfx_data if isinstance(vfx_data, list) else vfx_data.get("vfx", [])
    for v in vfx:
        if v.get("timeline_end", 0) > total_duration:
            v["timeline_end"] = total_duration

    # ── Build fusion_effects section ──
    # Per-clip VFX: map V1 clip labels to segment presets
    per_clip_effects = {}
    for v in vfx:
        # If VFX spec names a preset (e.g. "HOOK", "EMOTIONAL_PEAK")
        preset_name = v.get("preset", v.get("fusion_preset", ""))
        if preset_name:
            target = v.get("target", "")
            # Find matching V1 clip label
            if target.startswith("block_"):
                try:
                    block_num = int(target.replace("block_", ""))
                    for clip in v1_clips:
                        label = clip.get("label", "")
                        if label.endswith(f"_{block_num}") or f"_{block_num}_seg" in label:
                            per_clip_effects[label] = {"_preset": preset_name}
                except ValueError:
                    pass

    # Fusion transitions: convert step_4_02 transitions to .comp format
    fusion_transitions = []
    for ti, t in enumerate(transitions):
        comp_type = t.get("transition_type", "cut")
        if comp_type in ("cut", "hard_cut", ""):
            continue
            
        after_clip = t.get("from_block", ti)
        if "cut_time" in t:
            cut_time = t["cut_time"]
            closest_diff = 999
            closest_idx = ti
            for i, clip in enumerate(v1_clips):
                diff = abs(clip.get("timeline_out", 0) - cut_time)
                if diff < closest_diff:
                    closest_diff = diff
                    closest_idx = i
            if closest_diff < 0.2:
                after_clip = closest_idx
        else:
            if "from_block" in t:
                from_block = t["from_block"]
                last_match_idx = None
                for i, clip in enumerate(v1_clips):
                    if clip.get("label", "").endswith(f"_{from_block}") or f"_{from_block}_seg" in clip.get("label", ""):
                        last_match_idx = i
                if last_match_idx is not None:
                    after_clip = last_match_idx

        dur_frames = t.get("duration_frames")
        if dur_frames is None:
            dur_frames = int(t.get("duration", 0.5) * fps)

        trans_obj = {
            "type": comp_type,
            "after_clip": after_clip,
            "duration_frames": dur_frames,
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
            "duration_seconds": round(total_duration, 2),
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
        "sfx": sfx_passthrough,  # unresolved SFX entries (for reference)
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
    sfx_unresolved = len(sfx_passthrough)
    print(f"  SFX:         {sfx_resolved} resolved, "
          f"{sfx_unresolved} unresolved", file=sys.stderr)
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
