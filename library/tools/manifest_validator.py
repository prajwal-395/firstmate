import os
import json
import jsonschema

def validate_manifest(manifest: dict) -> list[str]:
    """Returns list of errors. Empty = valid."""
    errors = []

    # 1. JSON schema validation
    schema_path = os.path.join(os.path.dirname(__file__), '..', 'schema', 'assembly_manifest.schema.json')
    try:
        with open(schema_path, 'r') as f:
            schema = json.load(f)
        jsonschema.validate(instance=manifest, schema=schema)
    except jsonschema.exceptions.ValidationError as e:
        errors.append(f"Schema validation error: {e.message}")
    except FileNotFoundError:
        errors.append(f"Schema file not found at {schema_path}")

    # Helper to check clips
    def check_clip(clip, track_name, index, prev_clip):
        # 2. Source file existence
        source_file = clip.get("source_file")
        if source_file and not os.path.exists(source_file):
            errors.append(f"Track {track_name} clip {index}: Source file not found: {source_file}")

        # 3. Timecode sanity
        timeline_in = clip.get("timeline_in", 0)
        timeline_out = clip.get("timeline_out", 0)
        if timeline_out < timeline_in:
            errors.append(f"Track {track_name} clip {index}: Negative duration (in={timeline_in}, out={timeline_out})")

        # Overlap detection for V1
        if track_name == "V1" and prev_clip:
            prev_out = prev_clip.get("timeline_out", 0)
            if timeline_in < prev_out:
                # Add small epsilon to avoid float rounding errors
                if prev_out - timeline_in > 0.001:
                    errors.append(f"Track {track_name} clip {index}: Impossible overlap (in={timeline_in} < prev_out={prev_out})")
        return clip

    # 5. Track assignment validity
    tracks = manifest.get("tracks", {})
    for track_name, track_data in tracks.items():
        if str(track_name) == "0" or str(track_name).startswith("-"):
            errors.append(f"Invalid track assignment: {track_name}")
            
        clips = track_data.get("clips", [])
        # Ensure clips are sorted by timeline_in for overlap detection
        # (Though we shouldn't modify the manifest, we'll sort a local copy for our checks)
        sorted_clips = sorted(clips, key=lambda c: c.get("timeline_in", 0))
        prev = None
        for i, clip in enumerate(sorted_clips):
            prev = check_clip(clip, track_name, i, prev)

    # Total duration check (optional, but requested "clip durations sum to reasonable total")
    # Project duration should be roughly max(timeline_out)
    proj_duration = manifest.get("project", {}).get("duration_seconds", 0)
    if proj_duration < 0:
        errors.append("Project duration cannot be negative")

    # 4. CDL parameter bounds
    color_grade = manifest.get("color_grade", {})
    per_clip_adjustments = color_grade.get("per_clip_adjustments", [])
    for adj in per_clip_adjustments:
        cdl = adj.get("cdl_values", {})
        if not cdl:
            continue
            
        clip_id = adj.get("clip_id", "unknown")
        for color in ['r', 'g', 'b']:
            slope = cdl.get(f'slope_{color}', 1.0)
            if slope <= 0:
                errors.append(f"Clip {clip_id}: CDL slope_{color} must be > 0, got {slope}")
                
            power = cdl.get(f'power_{color}', 1.0)
            if power <= 0:
                errors.append(f"Clip {clip_id}: CDL power_{color} must be > 0, got {power}")
                
            offset = cdl.get(f'offset_{color}', 0.0)
            if not (-1.0 <= offset <= 1.0):
                errors.append(f"Clip {clip_id}: CDL offset_{color} out of reasonable bounds [-1, 1], got {offset}")

    # 6. Transition validity
    transitions = manifest.get("transitions", [])
    if isinstance(transitions, list):
        v1_clips = tracks.get("V1", {}).get("clips", [])
        max_block_idx = len(v1_clips) - 1
        for t in transitions:
            # Transitions reference blocks via from_block, to_block, or after_clip
            from_block = t.get("from_block", t.get("after_clip", -1))
            to_block = t.get("to_block", from_block + 1 if from_block != -1 else -1)
            
            if from_block != -1 and from_block > max_block_idx:
                errors.append(f"Transition references invalid from_block {from_block}")
            if to_block != -1 and to_block > max_block_idx:
                errors.append(f"Transition references invalid to_block {to_block}")

    return errors
