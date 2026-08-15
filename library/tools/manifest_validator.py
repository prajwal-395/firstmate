"""Structural and semantic validation of a compiled assembly manifest.

The structural half (schema, file existence, timecode sanity) has always
been here.  The semantic half is new, and exists because four audits in a
row fixed real bugs while a fresh silent collapse took their place: nine
B-roll clips compiled into one zero-length clip, ten transitions stacked
on one frame, five SFX at 0.000s, and none of it produced a single error.

Every semantic assertion below corresponds to a defect that actually
shipped.  They are deliberately strict: a manifest that trips one of them
describes a video nobody would watch.
"""

import os
import json
import jsonschema

# Two clips whose ranges differ by less than this are the same position.
POSITION_EPSILON = 0.001

# A clip shorter than this is invisible - most often the fingerprint of a
# key-mapping bug that zeroed its in/out points.
MIN_CLIP_DURATION_S = 0.02

# Source ranges that are suspiciously round. Real WhisperX word boundaries
# land on values like 17.666; a passage running exactly 0.0-5.0 was made
# up, not measured.
ROUND_RANGE_TOLERANCE = 1e-6

# Tracks whose clips must lie end-to-end. A3 is deliberately absent: it is
# a logical SFX bucket that the timeline builder spreads across A3, A4, ...
# so overlapping SFX are sound design, not a collision.
NON_OVERLAPPING_TRACKS = ("V1", "V2", "V3", "V4", "A2")


def validate_manifest(manifest: dict) -> list[str]:
    """Returns list of errors. Empty = valid."""
    errors = []
    errors.extend(_validate_structure(manifest))
    errors.extend(validate_manifest_semantics(manifest))
    return errors


# ─── Structural checks ───────────────────────────────────────

def _validate_structure(manifest: dict) -> list[str]:
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

    tracks = manifest.get("tracks", {})

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

        # Overlap detection on every sequential track, not just V1. A
        # B-roll clip sitting on top of another is just as broken as an
        # A-roll collision, and V2 was never checked.
        if track_name in NON_OVERLAPPING_TRACKS and prev_clip:
            prev_out = prev_clip.get("timeline_out", 0)
            if prev_out - timeline_in > POSITION_EPSILON:
                errors.append(
                    f"Track {track_name} clip {index} "
                    f"({clip.get('label', '?')}): overlaps the previous clip "
                    f"(in={timeline_in} < prev_out={prev_out})"
                )
        return clip

    # 5. Track assignment validity
    for track_name, track_data in tracks.items():
        if str(track_name) == "0" or str(track_name).startswith("-"):
            errors.append(f"Invalid track assignment: {track_name}")

        clips = track_data.get("clips", [])
        sorted_clips = sorted(clips, key=lambda c: c.get("timeline_in", 0))
        prev = None
        for i, clip in enumerate(sorted_clips):
            prev = check_clip(clip, track_name, i, prev)

    proj_duration = manifest.get("project", {}).get("duration_seconds", 0)
    if proj_duration < 0:
        errors.append("Project duration cannot be negative")

    if proj_duration > 0:
        for i, sub in enumerate(manifest.get("subtitles", [])):
            end_time = sub.get("timeline_end", 0)
            if end_time > proj_duration + POSITION_EPSILON:
                errors.append(f"Subtitle {i} ends at {end_time}s which exceeds project duration {proj_duration}s")

        # The subtitle overlay is a TOP-LEVEL key with a `segments` list -
        # this used to look for tracks["subtitle_overlay"]["clips"], a
        # shape the compiler has never written, so the check was dead.
        for i, seg in enumerate(_overlay_segments(manifest, "subtitle_overlay")):
            end_time = seg.get("timeline_end", 0)
            if end_time > proj_duration + POSITION_EPSILON:
                errors.append(
                    f"Subtitle overlay segment {i} ends at {end_time}s "
                    f"which exceeds project duration {proj_duration}s"
                )

    # 4. CDL parameter bounds
    color_grade = manifest.get("color_grade", {})
    for adj in color_grade.get("per_clip_adjustments", []):
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
            from_block = t.get("from_block", t.get("after_clip", -1))
            to_block = t.get("to_block", from_block + 1 if from_block != -1 else -1)

            if from_block != -1 and from_block > max_block_idx:
                errors.append(f"Transition references invalid from_block {from_block}")
            if to_block != -1 and to_block > max_block_idx:
                errors.append(f"Transition references invalid to_block {to_block}")

    return errors


# ─── Semantic checks ─────────────────────────────────────────

def validate_manifest_semantics(manifest: dict) -> list[str]:
    """Assertions about whether the manifest describes a watchable video.

    Split out from the structural pass so tests (and the render step) can
    run it against a captured manifest on its own.
    """
    errors = []
    errors.extend(_check_no_zero_duration_clips(manifest))
    errors.extend(_check_distinct_cut_points(manifest))
    errors.extend(_check_sfx_distributed(manifest))
    errors.extend(_check_vfx_distinct(manifest))
    errors.extend(_check_broll_differs_from_aroll(manifest))
    errors.extend(_check_overlay_segments_do_not_overlap(manifest))
    errors.extend(_check_no_fabricated_source_ranges(manifest))
    errors.extend(_check_no_repeated_source_audio(manifest))
    return errors


def _overlay_segments(manifest: dict, key: str) -> list:
    """Segments of a top-level overlay block, as the compiler writes it."""
    overlay = manifest.get(key) or {}
    if not isinstance(overlay, dict):
        return []
    return overlay.get("segments", []) or []


def _check_no_zero_duration_clips(manifest: dict) -> list[str]:
    """A clip with in == out is invisible, and always a bug upstream."""
    errors = []
    for track_name, track_data in manifest.get("tracks", {}).items():
        for i, clip in enumerate(track_data.get("clips", [])):
            tl_dur = clip.get("timeline_out", 0) - clip.get("timeline_in", 0)
            if tl_dur < MIN_CLIP_DURATION_S:
                errors.append(
                    f"Track {track_name} clip {i} "
                    f"({clip.get('label', '?')}): zero-length on the "
                    f"timeline ({clip.get('timeline_in')} -> "
                    f"{clip.get('timeline_out')}) - it would not be visible"
                )
                continue
            if "source_out" in clip and "source_in" in clip:
                src_dur = clip["source_out"] - clip["source_in"]
                if src_dur < MIN_CLIP_DURATION_S:
                    errors.append(
                        f"Track {track_name} clip {i} "
                        f"({clip.get('label', '?')}): zero-length source "
                        f"range ({clip['source_in']} -> {clip['source_out']})"
                    )
    return errors


def _check_distinct_cut_points(manifest: dict) -> list[str]:
    """Transitions must each sit at their own cut."""
    transitions = manifest.get("transitions", [])
    if len(transitions) < 2:
        return []
    seen = {}
    errors = []
    for t in transitions:
        cut = round(t.get("cut_point_timeline", 0.0), 3)
        if cut in seen:
            errors.append(
                f"Transition {t.get('transition_id', '?')} shares cut point "
                f"{cut}s with {seen[cut]} - only one cut exists there"
            )
        else:
            seen[cut] = t.get("transition_id", "?")
    return errors


def _check_sfx_distributed(manifest: dict) -> list[str]:
    """SFX must be spread across the timeline, not stacked on one frame."""
    clips = manifest.get("tracks", {}).get("A3", {}).get("clips", [])
    if len(clips) < 2:
        return []
    positions = {round(c.get("timeline_in", 0.0), 3) for c in clips}
    if len(positions) < len(clips):
        return [
            f"{len(clips)} SFX occupy only {len(positions)} distinct "
            f"timeline position(s): {sorted(positions)}"
        ]
    return []


def _check_vfx_distinct(manifest: dict) -> list[str]:
    """Several VFX covering the identical range means all but one are dead."""
    vfx = manifest.get("vfx", [])
    if len(vfx) < 2:
        return []
    ranges = {}
    errors = []
    for v in vfx:
        key = (round(v.get("timeline_start", 0.0), 3),
               round(v.get("timeline_end", 0.0), 3))
        if key in ranges:
            errors.append(
                f"VFX {v.get('vfx_id', '?')} ({v.get('effect_type', '?')}) "
                f"covers the same range {key} as {ranges[key]}"
            )
        else:
            ranges[key] = v.get("vfx_id", "?")
    return errors


def _check_broll_differs_from_aroll(manifest: dict) -> list[str]:
    """B-roll must show something other than the A-roll it covers."""
    v1 = manifest.get("tracks", {}).get("V1", {}).get("clips", [])
    v2 = manifest.get("tracks", {}).get("V2", {}).get("clips", [])
    errors = []
    for clip in v2:
        for aroll in v1:
            overlap = (
                min(clip.get("timeline_out", 0), aroll.get("timeline_out", 0))
                - max(clip.get("timeline_in", 0), aroll.get("timeline_in", 0))
            )
            if overlap <= POSITION_EPSILON:
                continue
            if clip.get("source_file") == aroll.get("source_file"):
                errors.append(
                    f"B-roll {clip.get('label', '?')} covers A-roll "
                    f"{aroll.get('label', '?')} with the SAME source file "
                    f"({os.path.basename(clip.get('source_file', ''))}) - "
                    f"that is not a cutaway"
                )
    return errors


def _check_overlay_segments_do_not_overlap(manifest: dict) -> list[str]:
    """Overlay clips share a track, so overlapping segments hide each other."""
    errors = []
    for key in ("subtitle_overlay", "motion_graphics_overlay"):
        segments = sorted(
            _overlay_segments(manifest, key),
            key=lambda s: s.get("timeline_start", 0),
        )
        for prev, curr in zip(segments, segments[1:]):
            gap = curr.get("timeline_start", 0) - prev.get("timeline_end", 0)
            if gap < -POSITION_EPSILON:
                errors.append(
                    f"{key}: segment at "
                    f"{curr.get('timeline_start')}s starts "
                    f"{-gap:.3f}s before the previous one ends "
                    f"({prev.get('timeline_end')}s)"
                )
    return errors



def _check_no_repeated_source_audio(manifest: dict) -> list[str]:
    """Back-to-back A-roll from one clip must not reuse the same audio.

    Two consecutive V1 clips cut from the same source file whose source
    ranges overlap lay that overlap down twice in a row, so the viewer
    hears the phrase, the cut, and then the phrase again. It is the
    fingerprint of a passage that anchored onto its predecessor's tail
    during alignment. Clips from different sources are unrelated, and a
    later block revisiting an earlier, non-overlapping part of the same
    clip repeats nothing.
    """
    clips = sorted(
        manifest.get("tracks", {}).get("V1", {}).get("clips", []),
        key=lambda c: c.get("timeline_in", 0),
    )
    errors = []
    for prev, curr in zip(clips, clips[1:]):
        source = curr.get("source_file", curr.get("clip_id"))
        if source is None or source != prev.get(
                "source_file", prev.get("clip_id")):
            continue
        if any(c.get("source_in") is None or c.get("source_out") is None
               for c in (prev, curr)):
            continue
        overlap = (min(prev["source_out"], curr["source_out"])
                   - max(prev["source_in"], curr["source_in"]))
        if overlap > POSITION_EPSILON:
            errors.append(
                f"V1 clip {curr.get('label', '?')} "
                f"({curr['source_in']}-{curr['source_out']}) repeats "
                f"{overlap:.3f}s of {prev.get('label', '?')} "
                f"({prev['source_in']}-{prev['source_out']}) from the same "
                f"source {os.path.basename(str(source))} - the cut plays "
                f"that audio twice in a row"
            )
    return errors


def _is_round(value: float) -> bool:
    return abs(value - round(value)) < ROUND_RANGE_TOLERANCE


def _check_no_fabricated_source_ranges(manifest: dict) -> list[str]:
    """Catch invented source timings masquerading as aligned ones.

    Speech A-roll is cut to WhisperX word boundaries, which are never
    whole seconds.  A V1 clip running exactly 60.0-65.0 was guessed, and
    the footage behind it will not match its transcript.
    """
    errors = []
    for clip in manifest.get("tracks", {}).get("V1", {}).get("clips", []):
        src_in = clip.get("source_in")
        src_out = clip.get("source_out")
        if src_in is None or src_out is None:
            continue
        if _is_round(src_in) and _is_round(src_out):
            errors.append(
                f"V1 clip {clip.get('label', '?')} has a fabricated-looking "
                f"source range ({src_in}-{src_out}): speech cut to word "
                f"boundaries never lands on whole seconds"
            )
    return errors
