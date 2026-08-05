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

# Add parent directories to path so we can import shared tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from tools.frame_utils import seconds_to_frame, convert_clip_to_frames, convert_subtitle_to_frames


def _resolve_source(block_or_clip: dict, clip_lookup: dict) -> str:
    """Resolve source_file from source_file field or clip_id lookup.

    Shared by both compile_manifest() and compile_manifest_from_inputs()
    to avoid logic duplication.

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


def compile_manifest(out_dir: str) -> dict:
    # Load pipeline step outputs
    spine_data = load(out_dir, "step_2_05.json")
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

    # Subtitle overlay from step 4.05 (Remotion render)
    subtitle_overlay_data = load(out_dir, "step_4_05.json")

    spine = spine_data.get("audio_spine", {})
    structure = spine.get("structure", [])
    total_duration = structure[-1]["timeline_end"] if structure else 60.0
    fps = spine.get("frame_rate", 30.0)

    # Build clip_id → source_file lookup from catalog
    catalog_data = load(out_dir, "step_1_02.json")
    clip_lookup = {}
    for clip in catalog_data.get("clip_catalog", []):
        clip_lookup[clip["clip_id"]] = clip["path"]

    def resolve_source(block_or_clip):
        return _resolve_source(block_or_clip, clip_lookup)

    # ── V1: A-Roll clips (from spine speech blocks) ──
    v1_clips = []
    for block in structure:
        if block["block_type"] in ("speech", "hook"):
            # Extract link_group_id from the block's content or from
            # the A-roll assignment data that was merged in.
            content = block.get("content") or {}
            lgid = (block.get("link_group_id")
                    or content.get("link_group_id"))

            clip = {
                "source_file": resolve_source(block),
                "source_in": block["source_start"],
                "source_out": block["source_end"],
                "timeline_in": block["timeline_start"],
                "timeline_out": block["timeline_end"],
                "timeline_in_frame": block.get("timeline_start_frame"),
                "timeline_out_frame": block.get("timeline_end_frame"),
                "link_group_id": lgid,
                "label": f"{block['block_type']}_{block['position']}",
            }
            # Fill frame fields from seconds if spine didn't provide them
            if clip["timeline_in_frame"] is None:
                convert_clip_to_frames(clip, fps)
            v1_clips.append(clip)

    # ── V2: B-Roll clips ──
    v2_clips = []
    for broll in broll_data.get("b_roll_assignments", []):
        clip = broll.get("assigned_clip", broll)
        v2_clip = {
            "source_file": resolve_source(clip),
            "source_in": clip.get("video_in", broll.get("video_in", 0)),
            "source_out": clip.get("video_out", broll.get("video_out", 0)),
            "timeline_in": broll["timeline_start"],
            "timeline_out": broll["timeline_end"],
            "video_only": True,
            "label": f"broll_{broll['spine_block_position']}",
        }
        convert_clip_to_frames(v2_clip, fps)
        v2_clips.append(v2_clip)
    # B-roll interjections
    for interj in broll_data.get("b_roll_interjections", []):
        clip = interj["assigned_clip"]
        v2_clip = {
            "source_file": resolve_source(clip),
            "source_in": clip["video_in"],
            "source_out": clip["video_out"],
            "timeline_in": interj["timeline_start"],
            "timeline_out": interj["timeline_end"],
            "video_only": True,
            "label": f"interjection_{interj['over_spine_block_position']}",
        }
        convert_clip_to_frames(v2_clip, fps)
        v2_clips.append(v2_clip)

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
    subtitles = subtitle_data.get("subtitle_entries", [])

    # Normalize field names for manifest schema compatibility.
    # Step 4.01 uses "entry_id"; the FCPXML generator expects "id".
    for sub in subtitles:
        if "entry_id" in sub and "id" not in sub:
            sub["id"] = sub.pop("entry_id")

    # Sort and re-number
    subtitles.sort(key=lambda s: s["timeline_start"])
    for i, sub in enumerate(subtitles, 1):
        sub["id"] = i
        # Ensure frame fields exist
        if "timeline_start_frame" not in sub:
            convert_subtitle_to_frames(sub, fps)

    # ── Transitions ──
    # Step 4.02 uses position: "between_X_Y"; the XMEML generator needs
    # cut_point_timeline (seconds) and a normalized transition_type.
    transitions_raw = transition_data.get(
        "transitions", transition_data.get("transition_spec", []))

    # Build spine-block-end lookup: position → timeline_end seconds
    block_end_by_pos = {}
    for b in structure:
        block_end_by_pos[b["position"]] = b.get(
            "timeline_end", b.get("timeline_end_frame", 0) / fps)

    transitions = []
    for t in transitions_raw:
        enriched = dict(t)
        pos = t.get("position", "")
        # Parse "between_X_Y" → cut point is block X's end time
        if pos.startswith("between_"):
            parts = pos.replace("between_", "").split("_")
            try:
                from_block = int(parts[0])
                to_block = int(parts[1])
                enriched["from_block"] = from_block
                enriched["to_block"] = to_block
                enriched["cut_point_timeline"] = block_end_by_pos.get(
                    from_block, 0.0)
            except (ValueError, IndexError):
                pass

        # Normalize type names to what the XMEML fade builder expects
        ttype = t.get("type", "")
        type_map = {
            "cross_dissolve": "cross_dissolve",
            "dip_to_black": "dip_to_black",
            "fade_in": "fade_in",
            "fade_out": "fade_out",
        }
        enriched["transition_type"] = type_map.get(ttype, ttype)

        # Convert duration_frames to duration_seconds for the builder
        dur_frames = t.get("duration_frames", 15)
        enriched["duration_seconds"] = dur_frames / fps
        enriched["duration_frames"] = dur_frames

        transitions.append(enriched)

    # ── SFX ──
    # The SFX bridge (step 4.04) outputs creative placement data with
    # sfx_type, timeline_start (seconds), volume_db. We need to:
    #   1. Resolve sfx_type → actual audio file from the SFX library
    #   2. Convert timeline_start/end (seconds) → timeline_in_frame/out_frame
    #   3. Format as tracks.A3.clips for the builder
    sfx_raw = sfx_data.get("sfx_plan",
                sfx_data.get("sfx_events",
                  sfx_data.get("sfx_spec", [])))

    sfx_library_path = os.environ.get(
        "SFX_LIBRARY",
        "/Users/prajwal/Documents/content_stuff/"
        "assets i used (just copied here for convenience)/sfx library"
    )

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

    # Compile SFX into builder-compatible format
    a3_clips = []
    sfx_passthrough = []  # Keep raw SFX for reference
    for si, sfx_entry in enumerate(sfx_raw):
        sfx_type = sfx_entry.get("sfx_type", "whoosh")
        tl_start_sec = sfx_entry.get("timeline_start", 0.0)
        tl_end_sec = sfx_entry.get("timeline_end",
                                    tl_start_sec + sfx_entry.get(
                                        "duration_seconds", 0.5))
        vol_db = sfx_entry.get("volume_db", -14)

        # Resolve to actual file
        source_file, lib_dur = _match_sfx_file(sfx_type, sfx_index)

        if source_file and os.path.exists(source_file):
            sfx_dur_sec = tl_end_sec - tl_start_sec
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
    # Step 4.03 uses target: "block_N"; attach timeline range from V1 clip.
    vfx_raw = vfx_data.get("vfx_plan", vfx_data.get("vfx_spec", []))
    vfx = []
    for v in vfx_raw:
        enriched = dict(v)
        target = v.get("target", "")
        # Parse "block_N" → find the V1 clip at that spine position
        if target.startswith("block_"):
            try:
                block_num = int(target.replace("block_", ""))
                # Find the matching V1 clip
                for clip in v1_clips:
                    label = clip.get("label", "")
                    if label.endswith(f"_{block_num}"):
                        enriched["timeline_start"] = clip["timeline_in"]
                        enriched["timeline_end"] = clip["timeline_out"]
                        break
            except ValueError:
                pass
        if enriched.get("timeline_end", 0) > total_duration:
            enriched["timeline_end"] = total_duration
        vfx.append(enriched)

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
                        if label.endswith(f"_{block_num}"):
                            per_clip_effects[label] = {"_preset": preset_name}
                            break
                except ValueError:
                    pass

    # Fusion transitions: convert step_4_02 transitions to .comp format
    fusion_transitions = []
    for ti, t in enumerate(transitions):
        ttype = t.get("transition_type", t.get("type", "cut"))
        # Map legacy names to our .comp transition types
        type_map = {
            "cross_dissolve": "fade_to_black",  # fade out + fade in
            "dip_to_black": "fade_to_black",
            "fade_in": "fade_to_black",
            "fade_out": "fade_to_black",
            "zoom_blur": "zoom_blur",
            "defocus": "defocus",
            "slide_left": "slide_left",
            "flash": "flash",
        }
        comp_type = type_map.get(ttype, ttype)
        if comp_type in ("cut", "hard_cut", ""):
            continue
        fusion_transitions.append({
            "type": comp_type,
            "after_clip": t.get("from_block", ti),
            "duration_frames": t.get("duration_frames", 15),
        })

    # Audio config
    audio_config = {
        "fairlight_preset": audio_mix_data.get(
            "audio_mix_spec", {}).get("fairlight_preset", ""),
    }

    # ── Compile ──
    manifest = {
        "project": {
            "name": "Pipeline_Edit",
            "resolution": [1080, 1920],
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
        "audio": audio_config,
        "color_grade": color_data.get("color_grade_spec", {}),
        "audio_mix": audio_mix_data.get("audio_mix_spec", {}),
        "music_ducking": {
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
    }

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

    return manifest


def compile_manifest_from_inputs(inputs: dict) -> dict:
    """Compile manifest from pre-gathered pipeline inputs (orchestrator mode).

    Accepts the same data that compile_manifest() loads from files,
    but passed directly as a dict from the DAG orchestrator.

    IMPORTANT: Produces the same manifest shape as compile_manifest()
    so resolve_build_timeline.py can consume it identically.
    """
    spine = inputs.get("audio_spine", {})
    structure = spine.get("structure", [])
    total_duration = structure[-1]["timeline_end"] if structure else 60.0
    fps = spine.get("frame_rate", 30.0)

    # Build clip lookup from a_roll_assignments
    clip_lookup = {}
    for assignment in inputs.get("a_roll_assignments", []):
        cid = assignment.get("source_clip_id", "")
        path = assignment.get("source_file", "")
        if cid and path:
            clip_lookup[cid] = path

    def resolve_source(block_or_clip):
        return _resolve_source(block_or_clip, clip_lookup)

    # V1: A-Roll clips from spine speech blocks
    v1_clips = []
    for block in structure:
        if block.get("block_type") in ("speech", "hook"):
            content = block.get("content") or {}
            lgid = block.get("link_group_id") or content.get("link_group_id")
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

    # V2: B-Roll clips
    v2_clips = []
    for broll in inputs.get("b_roll_assignments", []):
        assigned = broll.get("assigned_clip", broll)
        v2_clip = {
            "source_file": resolve_source(assigned),
            "source_in": assigned.get("video_in", broll.get("video_in", 0)),
            "source_out": assigned.get("video_out", broll.get("video_out", 0)),
            "timeline_in": broll.get("timeline_start", 0.0),
            "timeline_out": broll.get("timeline_end", 0.0),
            "video_only": True,
            "label": f"broll_{broll.get('spine_block_position', 0)}",
        }
        convert_clip_to_frames(v2_clip, fps)
        v2_clips.append(v2_clip)

    # A2: Music
    ms = inputs.get("music_selection", {})
    music_path = ms.get("audio_path", "")
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

    # Subtitles
    subtitles = inputs.get("subtitle_plan", {}).get("subtitle_entries",
                    inputs.get("subtitle_plan", {}).get("subtitles", []))

    # Transitions
    transition_raw = inputs.get("transition_spec", [])
    if isinstance(transition_raw, dict):
        transitions = transition_raw.get("transitions", transition_raw.get("transition_spec", []))
    else:
        transitions = transition_raw if isinstance(transition_raw, list) else []

    # VFX: Extract and integrate enhancement specs into per-clip effects.
    # BUG FIX C6: Previously hardcoded per_clip to {} and transitions to [],
    # discarding all VFX and transition data from upstream pipeline steps.
    vfx_raw = inputs.get("enhancement_spec", [])
    if isinstance(vfx_raw, dict):
        vfx = vfx_raw.get("vfx_plan", vfx_raw.get("vfx_spec", []))
    else:
        vfx = vfx_raw if isinstance(vfx_raw, list) else []

    # Enrich VFX entries with timeline ranges from V1 clips (same as file mode)
    for v in vfx:
        target = v.get("target", "")
        if target.startswith("block_"):
            try:
                block_num = int(target.replace("block_", ""))
                for clip in v1_clips:
                    label = clip.get("label", "")
                    if label.endswith(f"_{block_num}"):
                        v["timeline_start"] = clip["timeline_in"]
                        v["timeline_end"] = clip["timeline_out"]
                        break
            except ValueError:
                pass
        if v.get("timeline_end", 0) > total_duration:
            v["timeline_end"] = total_duration

    # Build per-clip effects from VFX preset assignments
    per_clip_effects = {}
    for v in vfx:
        preset_name = v.get("preset", v.get("fusion_preset", ""))
        if preset_name:
            target = v.get("target", "")
            if target.startswith("block_"):
                try:
                    block_num = int(target.replace("block_", ""))
                    for clip in v1_clips:
                        label = clip.get("label", "")
                        if label.endswith(f"_{block_num}"):
                            per_clip_effects[label] = {"_preset": preset_name}
                            break
                except ValueError:
                    pass

    # Transitions: extract and convert to fusion .comp format
    transition_raw = inputs.get("transition_spec", [])
    if isinstance(transition_raw, dict):
        transitions = transition_raw.get("transitions", transition_raw.get("transition_spec", []))
    else:
        transitions = transition_raw if isinstance(transition_raw, list) else []

    # Convert transitions to fusion comp format (same mapping as file mode)
    fusion_transitions = []
    for ti, t in enumerate(transitions):
        ttype = t.get("transition_type", t.get("type", "cut"))
        type_map = {
            "cross_dissolve": "fade_to_black",
            "dip_to_black": "fade_to_black",
            "fade_in": "fade_to_black",
            "fade_out": "fade_to_black",
            "zoom_blur": "zoom_blur",
            "defocus": "defocus",
            "slide_left": "slide_left",
            "flash": "flash",
        }
        comp_type = type_map.get(ttype, ttype)
        if comp_type in ("cut", "hard_cut", ""):
            continue
        fusion_transitions.append({
            "type": comp_type,
            "after_clip": t.get("from_block", ti),
            "duration_frames": t.get("duration_frames", 15),
        })

    # Audio config
    audio_mix_data = inputs.get("audio_mix_spec", {})
    audio_config = {
        "fairlight_preset": audio_mix_data.get("fairlight_preset", ""),
    }

    # SFX: Resolve SFX file paths using the SFX library.
    # BUG FIX C6: Previously skipped SFX resolution entirely, passing raw
    # unresolved specs that lack source_file paths needed by the render step.
    sfx_raw = inputs.get("sfx_spec", [])
    if isinstance(sfx_raw, dict):
        sfx_list = sfx_raw.get("sfx_placements", sfx_raw.get("sfx_spec", []))
    else:
        sfx_list = sfx_raw if isinstance(sfx_raw, list) else []

    sfx_library_path = os.environ.get(
        "SFX_LIBRARY",
        "/Users/prajwal/Documents/content_stuff/"
        "assets i used (just copied here for convenience)/sfx library"
    )

    # Load SFX library index (same logic as file-based compile_manifest)
    sfx_index = []
    sfx_index_path = os.path.join(sfx_library_path, "sfx_index.json")
    if os.path.exists(sfx_index_path):
        try:
            with open(sfx_index_path) as f:
                sfx_index = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    elif os.path.isdir(os.path.join(sfx_library_path, "profiles")):
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

    a3_clips = []
    sfx_passthrough = []
    for si, sfx_entry in enumerate(sfx_list):
        sfx_type = sfx_entry.get("sfx_type", "whoosh")
        tl_start_sec = sfx_entry.get("timeline_start", 0.0)
        tl_end_sec = sfx_entry.get("timeline_end",
                                    tl_start_sec + sfx_entry.get(
                                        "duration_seconds", 0.5))
        vol_db = sfx_entry.get("volume_db", -14)

        source_file, lib_dur = _match_sfx_file(sfx_type, sfx_index)

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
            sfx_passthrough.append({
                **sfx_entry,
                "_warning": f"No audio file resolved for sfx_type={sfx_type}",
            })

    if sfx_passthrough:
        print(f"  WARNING: {len(sfx_passthrough)} SFX entries "
              f"could not be resolved to audio files",
              file=sys.stderr)

    # Build manifest with the SAME shape as compile_manifest()
    manifest = {
        "project": {
            "name": "Pipeline_Edit",
            "resolution": [1080, 1920],
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
        "audio": audio_config,
        "color_grade": inputs.get("color_grade_spec", {}),
        "audio_mix": audio_mix_data,
        "music_ducking": {
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
        "subtitle_overlay": inputs.get("subtitle_overlay", {}),
    }

    return {"assembly_manifest": manifest}


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
            # If project_folder is provided, try filesystem mode first
            out_dir = os.path.join(inputs["project_folder"], "pipeline_output")
            if os.path.isdir(out_dir):
                manifest = compile_manifest(out_dir)
                json.dump({"assembly_manifest": manifest}, sys.stdout, indent=2)
                return

        # Otherwise compile from the input data directly
        result = compile_manifest_from_inputs(inputs)
        json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
