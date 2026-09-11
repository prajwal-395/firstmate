"""Structural and semantic validation of a compiled assembly manifest.

The structural half (schema, file existence, timecode sanity) has always
been here.  The semantic half is new, and exists because four audits in a
row fixed real bugs while a fresh silent collapse took their place: nine
B-roll clips compiled into one zero-length clip, ten transitions stacked
on one frame, five SFX at 0.000s, and none of it produced a single error.

Every semantic assertion below corresponds to a defect that actually
shipped.  They are deliberately strict: a manifest that trips one of them
describes a video nobody would watch.


Rules relocated from AGENTS.md 10.2
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.2 keeps the headline
and points here.

**Manifest validation has a semantic half.**
`library/tools/manifest_validator.py` validates distinct cut points, distributed SFX, distinct VFX ranges, no overlaps, no zero-duration clips, no fabricated source ranges.
Regression fixtures live in `tests/fixtures/captured_run/` and come from a real broken run - never replace them with empty-list fixtures. [why](docs/RULE_EVIDENCE.md#manifest-validator-semantic-half)
Never replace regression fixtures with empty-list fixtures. [why](docs/RULE_EVIDENCE.md#manifest-validator-semantic-half)
"""

import os
import json
import jsonschema

from library.tools.transition_vocabulary import CUT_TYPES

# Two clips whose ranges differ by less than this are the same position.
POSITION_EPSILON = 0.001

# Timeline overlap is judged against a FRAME, not against POSITION_EPSILON.
#
# Clip boundaries are computed independently in seconds and land on
# slightly different floats where clips abut: project 001 produced
# `in=8.38 < prev_out=8.382000000000001` and failed the build. Two
# milliseconds is a sixteenth of a frame at 30fps - there is no such
# thing as an overlap the renderer could show. A real collision, which
# is what this check exists for, is frames or seconds wide.
DEFAULT_FRAME_RATE = 30.0

# A clip shorter than this is invisible - most often the fingerprint of a
# key-mapping bug that zeroed its in/out points.
MIN_CLIP_DURATION_S = 0.02

# Source ranges that are suspiciously round. Real WhisperX word boundaries
# land on values like 17.666; a passage running exactly 0.0-5.0 was made
# up, not measured.
ROUND_RANGE_TOLERANCE = 1e-6

# Tracks whose clips must lie end-to-end. A3 is deliberately absent: it is
# a logical SFX bucket that the timeline builder spreads across A3, A4, ...
# so overlapping SFX are sound design, not a collision. A2 IS checked, and
# a declared crossfade is the one overlap it allows - see check_clip.
NON_OVERLAPPING_TRACKS = ("V1", "V2", "V3", "V4", "A2")

# ── P6: no caption card flashes ──
#
# A card below this reads as a flicker rather than as text. Nothing here
# is invented: 0.5s is the threshold `render_qa`'s own
# `subtitle_too_short` metric already uses, and 0.7s is
# `step_4_01_plan_subtitles.MIN_DISPLAY_DURATION`, the pipeline's own
# declared floor. The hard failure is at the lower one; cards between the
# two are counted in the message so the softer floor stays visible.
#
# Why the existing floor cannot catch this, which is the finding:
# `enforce_min_duration` extends a short card only up to the next card's
# start, and on project 001 six of the eight short cards abut their
# neighbour with a gap of exactly 0.0 - there is nowhere to extend into.
# The floor is structurally unreachable in the normal case, so the fix is
# in the GROUPING (do not emit a one-word card) and this is the check that
# says whether a grouping change worked.
#
# It worked, and it also found the one case grouping CANNOT reach.
# `step_4_01_plan_subtitles` now partitions each block's words to minimise
# the cards below this floor rather than filling greedily, which on 001
# took the count from 76 of 96 to 2 of 41. Both survivors are the LAST
# card of a spine block: a card is on screen until the next card's first
# word, and the last card of a block has no next word - it leaves when the
# block does. Block 2 is "today is march 25th, 2026." and its final word
# is spoken for 0.21s with the next block's captions starting in the same
# frame, so no partition of those five words, at any width, makes that
# card longer.
#
# A BLOCK too short to carry a card is a different thing, and it is not
# exempt - it is repaired.  A reel spine makes one block per transcript
# row, so a row the transcriber split mid-sentence becomes a block whose
# whole length is one card; on the field test's Reel 23 that was `them.`
# at 0.181s.  Such a card passes both clauses trivially (it is the last
# card of its block because it is the only one), so the exemption used
# to swallow it while saying nothing could lengthen it - which was
# false.  `reel_spine._merge_fragment_blocks` gives the row back to its
# sentence before the grouping runs, and the exemption is left for what
# it was written for.
#
# So a card that ends WITH ITS BLOCK is counted and named, and does not
# fail the build; everything else still does. The exemption is deliberately
# the narrowest one that is provable from the manifest - it needs the card
# to be last in its block AND to end at the block's end - because a floor
# that exempts the general case is a gate that cannot fail.
MIN_CAPTION_DISPLAY_SECONDS = 0.5
SOFT_CAPTION_DISPLAY_SECONDS = 0.7

# How close a card's end must be to its block's end to count as ending
# with it. One frame at 30fps, rounded up: the plan rounds to 3dp.
BLOCK_END_TOLERANCE_SECONDS = 0.034

def ends_with_its_block(card_end, block_last_card_end,
                        block_end) -> bool:
    """BOTH clauses of the exemption on `MIN_CAPTION_DISPLAY_SECONDS`.

    A short caption card is held rather than failed when it is the LAST
    card of its spine block AND it ends AT that block's end.  Both, never
    one: the last card of a block that still has room to extend into is
    an ordinary short card, and a card ending on the block's end that has
    a card after it is not the one nothing can lengthen.  "A floor that
    exempts the general case is a gate that cannot fail."

    This is the ONE place the predicate lives.  `_check_no_flash_captions`
    holds the manifest to it and `reel_conformance_verifier`'s F7 holds a
    reel to it; a second copy is how the two would drift.
    """
    if card_end is None or block_last_card_end is None or block_end is None:
        return False
    # Last in its block: no card of the block ends later.
    if abs(card_end - block_last_card_end) > 1e-6:
        return False
    # And it ends where the block does.
    return abs(card_end - block_end) <= BLOCK_END_TOLERANCE_SECONDS


# ── P7: no discretionary effect applied to everything ──
#
# An effect on 100% of eligible items with almost no parameter variation
# is not a decision, it is a default, and the viewer reads it as either
# wallpaper or as trying. 100% is the exact boundary between "chosen for
# these shots" and "applied to all shots", so no arbitrary number is
# needed for the coverage half.
#
# This is NOT a creative ceiling and must not become one. The 2026-08-20
# ruling removed the B-roll and SFX floors outright, and a maximum
# DENSITY would be the same mistake in the opposite direction. P7 says
# nothing about how many effects a piece gets; it says an effect on
# literally all of them, in two flavours, was not chosen.
MAX_UNIFORM_PARAMETER_SETS = 2

# Below this many eligible items, "applied to everything" cannot be
# distinguished from "applied to the two shots that wanted it". Three is
# the smallest count at which uniformity is a statement.
MIN_ITEMS_FOR_UNIFORMITY = 3


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
    fps = manifest.get("project", {}).get("frame_rate") or DEFAULT_FRAME_RATE
    overlap_tolerance = 1.0 / max(float(fps), 1.0)

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
        # A2 overlaps itself exactly where the plan declared a CROSSFADE
        # and nowhere else: two pieces of a spliced bed have to play at
        # once for one to fade into the other, and the timeline builder
        # spreads them across lanes the way it already does for SFX. An
        # overlap LARGER than the declared fade is still a collision, and
        # an undeclared one still is. See library/tools/music_bed.py.
        declared_crossfade = float(clip.get("crossfade_in_seconds") or 0.0)
        if track_name in NON_OVERLAPPING_TRACKS and prev_clip:
            prev_out = prev_clip.get("timeline_out", 0)
            if prev_out - timeline_in > declared_crossfade + overlap_tolerance:
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
    errors.extend(_check_no_flash_captions(manifest))
    errors.extend(_check_no_effect_on_everything(manifest))
    errors.extend(_check_subject_survives_the_conform(manifest))
    errors.extend(_check_subject_mattes_cover_windows(manifest))
    return errors


def _check_subject_mattes_cover_windows(manifest: dict) -> list[str]:
    """P9: a subject grade's matte must exist and cover its window.

    An overlay segment the manifest names and disk does not have
    refuses the compile (AGENTS.md 10.2); a subject matte is held to
    the same. `compile_manifest` already validates each matte at write
    time - this is the half that catches a hand-edited manifest, or a
    matte directory moved after the compile.
    """
    from library.tools.subject_grade import validate_matte

    errors = []
    for record in (manifest.get("subject_mattes", []) or []):
        for error in validate_matte(
                record, played_frames=record.get("frame_count", 0)):
            errors.append(f"subject_mattes: {error}")
    return errors


def _check_subject_survives_the_conform(manifest: dict) -> list[str]:
    """P8: a clip's crop must be wide enough for the subject it measured.

    Filling a portrait frame from landscape source keeps exactly
    ``1 / fill_zoom`` of the source WIDTH.  When the pipeline measured a
    subject on that clip and the subject needs more width than that, the
    speaker is cut off by the frame edge no matter where the pan points.

    **This is the half that carries the verdict.**  The render-side
    counterpart (`render_qa.measure_face_intact`) can only judge a face
    the cascade still finds, and a face cropped hard enough stops being
    detectable at all - measured on 001's own master, where 118 sampled
    frames of a talking-head video yielded 9 detections and none of them
    touching an edge, for a render whose A-roll was cut through the
    middle of the speaker's face throughout.  Here the question is
    arithmetic and has an exact answer, and it is answered before a
    40-minute render rather than after it.

    `compile_manifest._conform_fields` avoids this by switching such a
    clip to the backdrop route, which shows the subject whole over a
    blurred plate.  So an error here means the geometry and the plan
    disagree - a hand-edited manifest, or a conform that ran before this
    existed.
    """
    from library.tools.subject_framing import SUBJECT_HEADROOM

    errors = []
    tracks = manifest.get("tracks", {}) or {}
    for track_key in ("V1", "V2"):
        for clip in (tracks.get(track_key, {}) or {}).get("clips", []) or []:
            if clip.get("framing_backdrop"):
                # The whole subject is composited into the frame; the
                # crop no longer decides what is visible.
                continue
            subject_width = clip.get("subject_width")
            zoom = clip.get("fill_zoom")
            if not subject_width or not zoom or zoom <= 1.0:
                continue
            crop = 1.0 / float(zoom)
            needed = float(subject_width) * (1.0 + 2.0 * SUBJECT_HEADROOM)
            if crop + 1e-6 < needed:
                errors.append(
                    f"{track_key} clip '{clip.get('label', '?')}' crops the "
                    f"subject: the conform keeps {crop:.1%} of the source "
                    f"width at zoom {zoom}, and the measured subject needs "
                    f"{needed:.1%} (a face box of {float(subject_width):.1%} "
                    f"plus {SUBJECT_HEADROOM:.0%} clear on each side). The "
                    f"speaker is cut by the frame edge wherever the pan "
                    f"points."
                )
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
    """SFX planned for several moments must not collapse onto one frame.

    Layering - two or more sounds planned onto the same spine block - is
    legitimate sound design and the timeline builder already spreads the
    layers across A3, A4, .... The plan tells them apart: `compile_manifest`
    carries each entry's `spine_block_position` onto its A3 clip, so clips
    naming several positions that landed on one timeline position are a
    placement collapse, and clips naming one are a layer - even when the
    layered moment is the only sound in the plan. Clips with no provenance
    (legacy or hand-supplied manifests) keep the old strictness: every
    clip on one frame with nothing saying they were planned together is
    still refused.
    """
    clips = manifest.get("tracks", {}).get("A3", {}).get("clips", [])
    if len(clips) < 2:
        return []
    positions = {round(c.get("timeline_in", 0.0), 3) for c in clips}
    if len(positions) > 1:
        return []
    pos = next(iter(positions))
    planned = {c.get("spine_block_position") for c in clips
               if c.get("spine_block_position") is not None}
    if len(planned) == 1:
        return []
    if len(planned) > 1:
        return [
            f"{len(clips)} SFX planned for {len(planned)} distinct spine "
            f"positions ({sorted(planned, key=repr)}) all occupy the same "
            f"timeline position ({pos}s) - placement collapsed. A layer "
            f"shares one spine_block_position; these do not."
        ]
    return [
        f"{len(clips)} SFX all occupy the same timeline position "
        f"({pos}s)"
    ]


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
    """Two overlay clips on ONE ROW hide each other, so none may overlap.

    Per LANE, because a lane IS a row (`motion_graphics_plan.plan_segments`
    packs segments onto lanes and `timeline_layout` turns each lane into
    a Resolve row). Overlapping segments on DIFFERENT lanes are the
    captain's own Reel 26 request - two tight-box animations layered on
    separate rows - and reading them as one row would refuse exactly the
    thing that was asked for. A segment that names no lane is lane 0,
    which is every manifest written before lanes existed.
    """
    errors = []
    for key in ("subtitle_overlay", "motion_graphics_overlay",
                "timed_text_overlay"):
        by_lane: dict = {}
        for segment in _overlay_segments(manifest, key):
            by_lane.setdefault(int(segment.get("lane", 0) or 0),
                               []).append(segment)
        for lane, lane_segments in sorted(by_lane.items()):
            segments = sorted(lane_segments,
                              key=lambda s: s.get("timeline_start", 0))
            for prev, curr in zip(segments, segments[1:]):
                gap = (curr.get("timeline_start", 0)
                       - prev.get("timeline_end", 0))
                if gap < -POSITION_EPSILON:
                    errors.append(
                        f"{key}: segment at "
                        f"{curr.get('timeline_start')}s starts "
                        f"{-gap:.3f}s before the previous one ends "
                        f"({prev.get('timeline_end')}s) on lane {lane}"
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



def _check_no_flash_captions(manifest: dict) -> list[str]:
    """P6: no caption is on screen too briefly to register as text.

    Measured on the plan rather than the render because this is where a
    fix is possible and where it costs nothing: the durations are already
    in `subtitles[*]`, and catching it here saves a full render.

    A card that ends with its spine block is reported and not failed - see
    the note on `MIN_CAPTION_DISPLAY_SECONDS` for why no grouping can
    lengthen one.
    """
    subtitles = manifest.get("subtitles", []) or []
    block_end = {
        str(block.get("position")): block.get("timeline_end")
        for block in (manifest.get("_spine_blocks", []) or [])
        if block.get("timeline_end") is not None
    }

    # The last card of each block, by the end it reaches.
    last_end_in_block = {}
    for sub in subtitles:
        position = str(sub.get("spine_block_position"))
        end = sub.get("timeline_end")
        if end is None:
            continue
        if end > last_end_in_block.get(position, float("-inf")):
            last_end_in_block[position] = end

    def _ends_with_its_block(sub) -> bool:
        position = str(sub.get("spine_block_position"))
        end = sub.get("timeline_end")
        if position not in block_end:
            return False
        return ends_with_its_block(
            end, last_end_in_block.get(position), block_end[position])

    flashes = []
    held_by_block = []
    soft = 0
    for sub in subtitles:
        start = sub.get("timeline_start")
        end = sub.get("timeline_end")
        if start is None or end is None:
            continue
        duration = end - start
        if duration <= 0:
            continue
        if duration < MIN_CAPTION_DISPLAY_SECONDS:
            row = (sub.get("id", "?"), sub.get("text", ""), duration)
            if _ends_with_its_block(sub):
                held_by_block.append(row)
            else:
                flashes.append(row)
        elif duration < SOFT_CAPTION_DISPLAY_SECONDS:
            soft += 1
    if not flashes:
        return []
    worst = min(flashes, key=lambda f: f[2])
    listed = ", ".join(f"{i} {d:.3f}s {t!r}" for i, t, d in flashes[:5])
    if len(flashes) > 5:
        listed += f", +{len(flashes) - 5} more"
    message = (
        f"{len(flashes)} of {len(subtitles)} caption cards are shorter than "
        f"{MIN_CAPTION_DISPLAY_SECONDS}s and flash rather than read "
        f"(shortest {worst[2]:.3f}s = {worst[2] * 30:.1f} frames at 30fps, "
        f"{worst[1]!r}); {soft} more sit under the "
        f"{SOFT_CAPTION_DISPLAY_SECONDS}s display floor the subtitle planner "
        f"declares. Group fewer one-word cards - extending them is not "
        f"possible where they abut their neighbour. Offenders: {listed}"
    )
    if held_by_block:
        message += (
            f". {len(held_by_block)} further card(s) are short because they "
            f"end with their spine block and no grouping can lengthen them; "
            f"those are reported, not counted here"
        )
    return [message]


def _uniformity_error(family: str, kind: str, counts: dict,
                      param_sets: int, total: int) -> str:
    breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    return (
        f"{family} covers all {total} {kind} ({breakdown}) with only "
        f"{param_sets} distinct parameter set(s) - an effect on everything "
        f"is a default, not a decision. Leave some {kind} alone, or vary "
        f"what the ones that keep it are doing."
    )


def _check_no_effect_on_everything(manifest: dict) -> list[str]:
    """P7: no discretionary effect family covers every eligible item.

    Two families, judged the same way. VFX are counted against the V1
    clips they could sit on; transitions against the cuts between them.
    A family fires only when it covers 100% AND carries at most
    `MAX_UNIFORM_PARAMETER_SETS` distinct parameter sets - a type on every
    clip with genuinely different parameters each time IS a decision, made
    many times.
    """
    errors = []
    tracks = manifest.get("tracks", {})
    v1_clips = tracks.get("V1", {}).get("clips", []) or []

    # ── VFX, against the V1 clips ──
    vfx = manifest.get("vfx", []) or []
    if vfx and len(v1_clips) >= MIN_ITEMS_FOR_UNIFORMITY:
        families = {}
        for item in vfx:
            effect = item.get("effect_type")
            if not effect:
                continue
            family = _effect_family(effect)
            entry = families.setdefault(family, {"counts": {}, "params": set()})
            entry["counts"][effect] = entry["counts"].get(effect, 0) + 1
            entry["params"].add(
                json.dumps(item.get("params", {}), sort_keys=True))
        for family, entry in sorted(families.items()):
            covered = sum(entry["counts"].values())
            if covered < len(v1_clips):
                continue
            if len(entry["params"]) > MAX_UNIFORM_PARAMETER_SETS:
                continue
            errors.append(_uniformity_error(
                f"VFX family '{family}'", "V1 clips", entry["counts"],
                len(entry["params"]), len(v1_clips)))

    # ── Transitions, against the cuts that were planned ──
    #
    # The denominator is the transitions the plan actually wrote, not
    # `len(v1_clips) - 1`. A spine with transition slots has more cut
    # points than V1 has clips - 001 plans 13 transitions across 9 V1
    # clips - so counting a type against the V1 gaps declared 8 hard cuts
    # out of 13 transitions to be "all of them".
    #
    # `CUT_TYPES` are excluded outright. They draw NOTHING - the
    # vocabulary says so in as many words - so a video whose every cut is
    # a hard cut is not an effect applied to everything, it is the absence
    # of one, and the transition handoff asks for exactly that ("hard cuts
    # dominate - use hard_cut as the default for most cuts"). Failing a
    # restrained edit here would be the creative ceiling this check's own
    # note forbids it from becoming.
    transitions = [t for t in (manifest.get("transitions", []) or [])
                   if t.get("transition_type")]
    if len(transitions) >= MIN_ITEMS_FOR_UNIFORMITY:
        counts = {}
        params = {}
        for t in transitions:
            kind = t["transition_type"]
            if kind in CUT_TYPES:
                continue
            counts[kind] = counts.get(kind, 0) + 1
            params.setdefault(kind, set()).add(
                json.dumps({"duration_frames": t.get("duration_frames")},
                           sort_keys=True))
        for kind, count in sorted(counts.items()):
            if count < len(transitions):
                continue
            if len(params[kind]) > MAX_UNIFORM_PARAMETER_SETS:
                continue
            errors.append(_uniformity_error(
                f"Transition type '{kind}'", "planned cuts", {kind: count},
                len(params[kind]), len(transitions)))

    return errors


def _effect_family(effect_type: str) -> str:
    """`slow_zoom_in` and `slow_zoom_out` are one decision, mirrored.

    Counting them as two types would let a planner defeat P7 by
    alternating direction, which is exactly what project 001 did: five
    `slow_zoom_in` and three `slow_zoom_out` over eight clips, two
    parameter sets, one of them the other's mirror.
    """
    name = str(effect_type)
    for suffix in ("_in", "_out", "_up", "_down", "_left", "_right"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


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
        # A declared intro / outro / end card is not cut from footage: it
        # is a rendered card that starts at its own frame 0 and runs the
        # length the template declared, so whole seconds are what a
        # correct one looks like. See library/tools/bookends.py.
        if clip.get("bookend"):
            continue
        if _is_round(src_in) and _is_round(src_out):
            errors.append(
                f"V1 clip {clip.get('label', '?')} has a fabricated-looking "
                f"source range ({src_in}-{src_out}): speech cut to word "
                f"boundaries never lands on whole seconds"
            )
    return errors
