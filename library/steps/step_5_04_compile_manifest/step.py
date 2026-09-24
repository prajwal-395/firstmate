#!/usr/bin/env python3
"""
Manifest Compiler (Step 5.04)

Reads pipeline step outputs and compiles them into an assembly_manifest
compatible with the existing resolve_full_assembly.py.

Usage:
  python step.py <pipeline_output_dir>
  # writes assembly_manifest.json to the same directory

All step output files follow the convention: step_X_YY.json
No version suffixes.


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**`compile_manifest` reads `pipeline_data.json`, not just files.**
The per-step `*.json` files in `pipeline_output/` are a best-effort dashboard export; a missing file reads as `{}`. [why](docs/RULE_EVIDENCE.md#compile-manifest-read-an-empty-catalog)


Rules relocated from AGENTS.md 10.2
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.2 keeps the headline
and points here.

**An overlay segment the manifest names and disk does not have REFUSES the compile.**
`compile_manifest.assert_overlay_segments_on_disk`, over `OVERLAY_TRACKS` - subtitles, motion graphics and timed text, all three at the same severity.
[why](docs/RULE_EVIDENCE.md#the-motion-graphics-that-were-planned-and-absent)

**Every frame of the timeline must show a clip.**
`compile_manifest._assert_timeline_fully_covered` fails on any stretch of V1+V2 with nothing on it.
The one exception is a hole the plan deliberately declared, via the optional `intentional_black_beat`/`black_beat_reason` spine keys documented in `library/tools/spine_contract.py`; an undeclared hole always fails.
Step 6.02 honours the same declaration by passing `declared_black_beat_ranges` into `render_qa`, and both gates bound a beat by `MAX_DECLARED_BLACK_BEAT_SECONDS` from the spine contract - keep that bound in one place. [why](docs/RULE_EVIDENCE.md#undeclared-black)


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**The bed is bounded by the PICTURE, not by V1.**
`compile_manifest` clamps a music clip that runs past the last picture, and the bound is V1 AND V2. It was V1 alone; the gap measured at -91.0 dB. [why](docs/RULE_EVIDENCE.md#the-bed-was-trimmed-to-the-last-v1-clip)
"""
import json
import os
import sys
import logging

logger = logging.getLogger(__name__)

# Instantaneous transitions - a cut has no duration by definition.
# The list itself lives in tools.transition_vocabulary; this module reads
# it through is_cut() so there is one place a type can be classified.

# Tracks that are a logical BUCKET rather than one physical lane: their
# clips may overlap, and the timeline builder spreads the overlaps across
# A3, A4, ... (`resolve_build_timeline._allocate_audio_tracks`). Every
# other track is a lane, where two clips at one position means one of
# them is invisible.
#
# This used to be spelled `SINGLE_LANE_TRACKS` and read `if track_name
# not in SINGLE_LANE_TRACKS`, which is a double negative naming the
# opposite of what the tuple holds - and the duplicate-position check
# below sat one indent outside it, so a legitimate layer of two sounds
# killed the run. The name now says what membership means.
LOGICAL_BUCKET_TRACKS = ("A3",)

# A hole shorter than one frame is float noise between two abutting
# clips, not something the viewer can see.
COVERAGE_TOLERANCE_FRAMES = 1

# Add parent directories to path so we can import shared tools.
# Both `library/` (for `tools.x`) and the repo root (for `library.tools.x`,
# which the shared tools use to import each other).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from tools.frame_utils import seconds_to_frame, convert_clip_to_frames, convert_subtitle_to_frames
from tools.manifest_validator import validate_manifest
from tools.pipeline_validation import require_keys
from tools.sfx_library import load_sfx_catalog, resolve_sfx_id
from tools.beat_grid import assert_music_offset_is_the_chosen_section
from library.tools.music_bed import bed_clips
from library.tools.music_audit_trail import assert_audit_trail_present
from library.tools.sfx_level import read_volume_db
from library.tools.music_bed import describe as describe_music_bed
from library.tools.music_bed import resolve_bed
from tools.bookends import block_bookend
from library.tools import cohesion_scope
from library.tools.music_behavior import resolve_music_behavior
from library.tools.transition_carriers import block_reaches_v1
from library.tools.vfx_plan_basis import (
    DroppedEntry, amend_with_drops, basis_summary,
)
from tools.spine_contract import (
    MAX_DECLARED_BLACK_BEAT_SECONDS,
    is_speech_block,
)
from tools.semantic_index import build_semantic_lookup
from tools.subject_framing import (
    SUBJECT_HEADROOM, subject_box,
    subject_center_x, subject_centers_by_clip,
)
from tools.transition_vocabulary import canonical_type, is_cut, withdrawal_reason
from tools.vision_schema_adapter import camera_prose, stability_summary
from tools.brand_registry import (
    project_template_name, resolve_project_template, project_timeline_name)
from tools.framing_intent import (DEFAULT_FRAMING_INTENT, FILL,
                                  delivered_framing_intent,
                                  resolve_framing_intent, source_covers_frame,
                                  resolve_crop_factor, DEFAULT_CROP_FACTOR)
from tools.tv_frame import (
    LAYER_TRACKS, TV_FRAME_LAYERS, assert_frameable,
    resolve_tv_frame, v1_zoom_for_look,
)
from tools.tv_power import switch_shape
from tools.delivery_format import resolve_delivery_format
from library.tools.subject_grade import apply_subject_grades
from tools.project_layout import (
    STEP_OUTPUT_FILE, Area, ProjectLayout, ProjectLayoutViolation,
)

# Wording that means the camera was not locked off. Read from the vision
# analysis's own stability verdict and motion prose, which is where both
# the v3 schema and the retired one put it.
_UNSTABLE_CAMERA_WORDS = (
    "handheld", "hand-held", "shaky", "shake", "unstable", "jitter",
    "wobble", "bumpy", "walking",
)

def apply_cohesion_adjustments(transitions_raw: list, cohesion_review: dict) -> dict:
    """Apply the cohesion review's adjustments and record every one of them.

    Every adjustment is either applied or recorded as not applied WITH a
    reason.  It used to apply `transition_spec` entries carrying a
    `target_index` and drop everything else without a word - in the
    reference run an sfx density adjustment vanished that way - while
    creative_cohesion's own `applied_adjustments` stayed empty, so neither
    the acting step nor the recording step told the truth.

    Step 5.03 now routes its findings through
    `library/tools/cohesion_scope.py` and sends only the applicable ones
    here, so a refusal below is a backstop rather than the normal path: a
    `cohesion_review` recorded by an older run, or supplied from outside,
    still gets refused out loud instead of vanishing.  The refusal
    SENTENCES come from that same enumeration, so what the review tells a
    reader and what this function logs cannot drift apart.

    Returns {"applied": [...], "not_applied": [{"adjustment", "reason"}],
    "observed": [...]}, where `observed` is what the review found and did
    NOT ask for - carried through so the manifest's record is the whole
    picture rather than half of it.
    """
    record = {"applied": [], "not_applied": [], "observed": [], "basis": {}}
    if not cohesion_review:
        return record
    observed = cohesion_review.get("observations")
    if isinstance(observed, list):
        record["observed"] = list(observed)
    # WHY the review asked for nothing, carried through so the manifest's
    # record says which absence this is. An empty `applied` beside an
    # empty `not_applied` reads as a clean bill of health otherwise.
    basis = cohesion_review.get("adjustments_basis")
    if isinstance(basis, dict):
        record["basis"] = dict(basis)
        logger.info("Cohesion adjustments: %s - %s",
                    basis.get("basis"), basis.get("means"))
    if not cohesion_review.get("adjustments"):
        return record

    def skip(adj, reason):
        record["not_applied"].append({"adjustment": adj, "reason": reason})
        print(
            f"  Cohesion adjustment NOT applied "
            f"({adj.get('target_step')}.{adj.get('field')}): {reason}",
            file=sys.stderr,
        )

    for adj in cohesion_review["adjustments"]:
        target = adj.get("target_step")
        field = adj.get("field")

        if target == "transition_spec":
            if "target_index" not in adj:
                skip(adj, "no target_index, so no transition to change")
                continue
            idx = adj["target_index"]
            if not 0 <= idx < len(transitions_raw):
                skip(adj, f"target_index {idx} is outside the {len(transitions_raw)} transitions")
                continue
            old_val = transitions_raw[idx].get(field)
            transitions_raw[idx][field] = adj["suggested_value"]
            record["applied"].append(
                f"transition[{idx}].{field}: {old_val} -> {adj['suggested_value']}"
            )
            print(
                f"  Applied Cohesion Adjustment: Transition {idx} {field} "
                f"{old_val} -> {adj['suggested_value']}",
                file=sys.stderr,
            )
        else:
            # One enumeration of what this function can and cannot act on
            # (library/tools/cohesion_scope.py), so the reason a reader
            # is given upstream is the reason logged here.
            skip(adj, cohesion_scope.refusal_reason(target, field))

    return record


def _assert_no_doubled_sound(track_name: str, clips: list) -> None:
    """On a logical bucket track, the same sound twice at one span.

    A3 is a bucket, not a lane: the timeline builder spreads overlapping
    clips across A3, A4, ... (`_allocate_audio_tracks`), so a riser under
    a whoosh is sound design and TWO SOUNDS AT ONE SPAN IS LAYERING - the
    obvious thing to do on a short block, and what step 4.04's craft role
    invites in as many words. `manifest_validator._check_sfx_distributed`
    reads the `spine_block_position` carried on each A3 clip and refuses
    only a collapse: entries planned for several positions landing on one.

    What is left is the case layering cannot explain. The same file,
    entered at the same point, over the same span, is one waveform played
    twice: it adds level and nothing else, and no lane allocation makes
    the second audible as a separate sound. That is a duplicated entry,
    not a stack, so it is still refused by name.
    """
    seen = {}
    for clip in clips:
        key = (
            clip.get('source_file'),
            round(float(clip.get('source_in') or 0.0), 3),
            clip.get('timeline_in', 0),
            clip.get('timeline_out', 0),
        )
        if key in seen:
            raise ValueError(
                f"Track {track_name}: {clip.get('label', '?')} and "
                f"{seen[key]} are the same sound ({os.path.basename(str(key[0]))}) "
                f"entered at the same point over the same span "
                f"({key[2]}, {key[3]}) - that is one waveform played twice, "
                f"not a layer. Layering two DIFFERENT sounds here is fine "
                f"and the builder gives them their own tracks."
            )
        seen[key] = clip.get('label', '?')


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
        if track_name not in LOGICAL_BUCKET_TRACKS:
            for i in range(len(clips) - 1):
                curr_out = clips[i].get('timeline_out', clips[i].get('timeline_out_seconds', 0))
                next_in = clips[i+1].get('timeline_in', clips[i+1].get('timeline_in_seconds', 0))
                # A2 overlaps itself exactly where the plan declared a
                # CROSSFADE, and nowhere else: two pieces of music have
                # to play at once for one to fade into the other, and the
                # renderer spreads them across lanes the way it already
                # does for SFX. An overlap larger than the declared fade
                # is still a collision. See library/tools/music_bed.py.
                declared_fade = float(
                    clips[i + 1].get('crossfade_in_seconds') or 0.0)
                if declared_fade and \
                        curr_out <= next_in + declared_fade + 0.01:
                    continue
                if curr_out > next_in + 0.01:
                    raise ValueError(
                        f"Track {track_name}: clip {i} "
                        f"({clips[i].get('label', '?')}) ends at {curr_out:.3f}s "
                        f"and overlaps clip {i+1} "
                        f"({clips[i+1].get('label', '?')}) at {next_in:.3f}s"
                    )
            # Two clips at the IDENTICAL span are the extreme case of the
            # overlap above, so this sits inside the same guard. It used
            # to sit one indent out and therefore ran for A3 too, which
            # defeated that exemption exactly where two sounds were
            # layered: `sfx_005 and sfx_004 both occupy (55.001, 58.001)`
            # killed a run whose plan was correct, and the same two
            # sounds compiled the moment their durations differed. On a
            # single-lane track only one clip can be seen or heard, which
            # is what this catches - nine B-roll assignments arriving at
            # (0, 0) because the reader used keys the planner never wrote.
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
        else:
            _assert_no_doubled_sound(track_name, clips)

    # Check 3: Transition Type+Duration Enforcement
    resolved_transitions = manifest.get('transitions', [])
    empty_count = sum(1 for t in resolved_transitions if not t.get('transition_type'))
    if empty_count == len(resolved_transitions) and len(resolved_transitions) > 0:
        raise ValueError(f"All {len(resolved_transitions)} transitions have empty type - transition key mapping failed. Check plan_transitions output uses 'transition_type' key.")

    for t in resolved_transitions:
        # A cut IS zero-length. Only overlapping transitions need a
        # duration, and giving a cut 15 frames invented an effect the
        # editor never planned.
        if is_cut(t.get('transition_type')):
            t['duration_frames'] = 0
        elif t.get('duration_frames', 0) <= 0:
            # REFUSED, never completed. A drawn effect with no hold is
            # an undecided plan reaching the compiler: neither the
            # plan's `duration_feel` nor a selected brand template said
            # how long to hold it, and inventing 15 frames here put a
            # half-second effect on the timeline nothing chose
            # (AGENTS.md 10.5). The fusion emit path below downgrades
            # such entries to a hard cut with the reason recorded, so
            # this fires only for data that reached validation some
            # other way - and that data stops here rather than
            # compiling past an undecided effect.
            raise ValueError(
                f"Transition {t.get('transition_id', '?')} "
                f"({t.get('transition_type', '?')}) at "
                f"{t.get('cut_point_timeline', '?')}s is a drawn effect "
                f"with no duration_frames. No hold is invented - re-run "
                f"plan_transitions (manage_project.py run <slug> --rerun "
                f"plan_transitions), which drops such entries with the "
                f"reason recorded."
            )

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
    """Read step_outputs from the project's pipeline_data.json.

    `out_dir` is the project's output root, so the project is its
    parent and the layout owner names the state file from there.
    """
    state_path = str(
        ProjectLayout(os.path.dirname(os.path.abspath(out_dir))).pipeline_data_path)
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

    pipeline_data.json is authoritative. The exported files are written
    best-effort by the dashboard exporter and that export is allowed to
    fail while the state save succeeds, so a stale file must never win
    over the state that produced this run.

    Three places, in order: the state, the step's own directory under
    `pipeline_output/steps/`, and - for a project that predates the
    by-step layout - the flat `<step_id>.json` at the output root.
    """
    stem = filename.replace(".json", "")
    if stem in _STATE_OUTPUTS:
        return _STATE_OUTPUTS[stem]

    if out_dir and out_dir != ".":
        try:
            layout = ProjectLayout(os.path.dirname(os.path.abspath(out_dir)))
            step_file = layout.step_dir(stem) / STEP_OUTPUT_FILE
            if step_file.exists():
                return _read_json(str(step_file))
        except (ProjectLayoutViolation, KeyError):
            pass  # `stem` is not a step id; the flat lookups below still apply

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
    vfx_collisions: list = (),
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

    if vfx_collisions:
        problems.append(
            f"VFX: {len(vfx_collisions)} collision(s) - two VFX landed on "
            f"the same clip, overwriting the first: "
            + "; ".join(vfx_collisions)
        )

    if problems:
        raise ValueError(
            "Planner output did not survive compilation:\n  - "
            + "\n  - ".join(problems)
        )


def _assert_subtitle_overlay_matches_plan(manifest: dict) -> None:
    """The rendered overlay must cover the subtitles the plan produced.

    `manifest.subtitles` is not what reaches the picture - the render path
    places `subtitle_overlay`, the pre-rendered Remotion segments, and
    reads the subtitle list not at all.  That made the two impossible to
    disagree usefully: a stale or partial Remotion render shipped a video
    missing captions while the manifest still listed all of them.

    Comparing them is what the subtitle list is FOR. Each spine block with
    captions must have overlay coverage, and that coverage must span the
    captions in it. One block may be covered by SEVERAL segments - step
    4.05 renders one segment per caption card, so a block with three
    cards reaches the timeline as three clips - and what is checked is
    the union: the earliest segment must start where the captions start
    and the latest must end where they end.
    """
    subtitles = manifest.get("subtitles", [])
    overlay = manifest.get("subtitle_overlay", {}) or {}
    segments = overlay.get("segments", [])

    if not subtitles or not segments:
        # Nothing planned, or no overlay step ran. Coverage of the picture
        # itself is asserted separately.
        return

    by_block = {}
    for sub in subtitles:
        position = sub.get("spine_block_position")
        if position is None:
            continue
        start = sub.get("timeline_start", 0)
        end = sub.get("timeline_end", 0)
        lo, hi = by_block.get(position, (start, end))
        by_block[position] = (min(lo, start), max(hi, end))

    segments_by_block = {}
    for seg in segments:
        if seg.get("block_position") is None:
            continue
        segments_by_block.setdefault(seg["block_position"], []).append(seg)

    problems = []
    for position, (lo, hi) in sorted(by_block.items(), key=lambda kv: str(kv[0])):
        segs = segments_by_block.get(position, [])
        if not segs:
            problems.append(
                f"block {position}: {lo:.3f}-{hi:.3f}s has captions but no "
                f"rendered overlay segment"
            )
            continue
        seg_start = min(seg.get("timeline_start", 0) for seg in segs)
        seg_end = max(seg.get("timeline_end", 0) for seg in segs)
        tolerance = COVERAGE_TOLERANCE_FRAMES / max(
            manifest.get("project", {}).get("frame_rate", 30.0), 1.0)
        if seg_start > lo + tolerance or seg_end < hi - tolerance:
            problems.append(
                f"block {position}: captions span {lo:.3f}-{hi:.3f}s but the "
                f"overlay segment(s) only cover "
                f"{seg_start:.3f}-{seg_end:.3f}s"
            )

    if problems:
        raise ValueError(
            "The rendered subtitle overlay does not match the subtitle "
            "plan - re-run step_4_05_render_subtitles:\n  - "
            + "\n  - ".join(problems)
        )


def _video_coverage_gaps(manifest: dict) -> list:
    """Stretches of timeline where no video track shows anything.

    Returned as ``(start_seconds, end_seconds)`` pairs, quoting the clip
    boundaries the gap sits between so the range can be found in the
    manifest.  Whether a gap *counts* is decided in frames: a hole is only
    real if it lasts at least one frame, and comparing seconds would turn
    float noise between abutting clips into findings.
    """
    project = manifest.get("project", {})
    fps = project.get("frame_rate") or 30.0
    duration = project.get("duration_seconds") or 0.0
    total_frames = int(round(duration * fps))
    if total_frames <= 0:
        return []

    spans = []
    for track_name, track_data in manifest.get("tracks", {}).items():
        if not track_name.startswith("V"):
            continue
        for clip in track_data.get("clips", []):
            start_s = clip.get("timeline_in", 0.0)
            end_s = clip.get("timeline_out", 0.0)
            start = clip.get("timeline_in_frame")
            end = clip.get("timeline_out_frame")
            if start is None:
                start = int(round(start_s * fps))
            if end is None:
                end = int(round(end_s * fps))
            if end > start:
                spans.append((start, end, start_s, end_s))

    spans.sort()
    gaps = []
    cursor, cursor_s = 0, 0.0
    for start, end, start_s, end_s in spans:
        if _is_real_gap(start - cursor, start_s - cursor_s, fps):
            gaps.append((cursor_s, start_s))
        if end > cursor:
            cursor, cursor_s = end, end_s
    if _is_real_gap(total_frames - cursor, duration - cursor_s, fps):
        gaps.append((cursor_s, duration))
    return gaps


def _is_real_gap(frame_gap: int, second_gap: float, fps: float) -> bool:
    """A hole counts only if it is a frame wide in BOTH clocks.

    Frames alone are not enough, and seconds alone are not either.

    Seconds alone turn float noise between abutting clips into findings,
    which is why this check moved to frames in the first place. But the
    frame numbers of two abutting clips are rounded INDEPENDENTLY, so an
    abutment can straddle a frame boundary and read as a one-frame hole:
    on project 001, clips meeting at 43.646s and 43.650s rounded to
    frames 1309 and 1310 and failed the build with "1 uncovered range
    totalling 0.004s". Four milliseconds is an eighth of a frame at
    30fps; it cannot render as black, and it stopped a render one step
    from the end.

    Requiring both clocks keeps the real case - the 6.4s hole this
    assertion exists for is 192 frames and 6.4 seconds, and fails either
    way - while a sub-frame abutment passes, as it must.
    """
    if frame_gap < COVERAGE_TOLERANCE_FRAMES:
        return False
    # The epsilon is for the threshold itself, not for the tolerance: a
    # hole of exactly one frame computes as 0.03333333333333297 against a
    # bound of 0.03333333333333333 and would be missed by float noise.
    # It is six orders of magnitude below the artefact being excluded.
    frame_seconds = COVERAGE_TOLERANCE_FRAMES / max(fps, 1.0)
    return second_gap >= frame_seconds - 1e-6


def _spine_block_entry(block: dict) -> dict:
    """One `_spine_blocks` entry: what downstream needs from a spine block.

    The optional black-beat declaration is carried through only when the
    spine actually made it, so an absent flag keeps meaning "nobody chose
    this hole" by the time the coverage assertion reads it.

    `music_behavior` is CARRIED, not recomputed.  This entry used to
    derive a two-word `full`/`ducked` value from `block_type` and discard
    what the spine planned, which meant a block planned `silent` reached
    the manifest saying the bed plays - see
    docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary.  The
    one true thing that reduction knew - a block with no speech under it
    has nothing to duck for - is now the default in
    `library/tools/music_behavior.py`, and it applies only to a block
    that planned nothing.
    """
    entry = {
        "position": block.get("position", ""),
        "timeline_start": block.get("timeline_start", 0),
        "timeline_end": block.get("timeline_end", 0),
        "block_type": block.get("block_type", ""),
        "music_behavior": resolve_music_behavior(
            block.get("music_behavior"),
            block_carries_speech=is_speech_block(block)),
    }
    for key in ("intentional_black_beat", "black_beat_reason"):
        if key in block:
            entry[key] = block[key]
    return entry


def _undeclared_black_beat_reason(start: float, end: float,
                                  spine_blocks: list):
    """Why an uncovered range is not a legitimate black beat, or None.

    A hole in the picture is legitimate only when the plan says so.  The
    declaration lives on the spine block that owns the stretch - see
    `library/tools/spine_contract.py` - and never on the absence of
    coverage, so "nobody placed a clip here" stays distinguishable from
    "hold on black here".  A declared beat must additionally sit outside
    speech, carry a reason, and be short.
    """
    declared = [
        b for b in spine_blocks
        if isinstance(b, dict) and b.get("intentional_black_beat")
    ]
    containing = [
        b for b in declared
        if b["timeline_start"] - 1e-6 <= start
        and end <= b["timeline_end"] + 1e-6
    ]
    if not containing:
        return ("no spine block declares an intentional black beat "
                "covering it")

    block = containing[0]
    label = f"block {block.get('position', '?')}"
    if is_speech_block(block):
        return (f"{label} declares a black beat but is a "
                f"{block['block_type']} block - speech is never held on "
                f"black")

    reason = block.get("black_beat_reason")
    if not isinstance(reason, str) or not reason.strip():
        return (f"{label} declares a black beat but carries no "
                f"black_beat_reason")

    if end - start > MAX_DECLARED_BLACK_BEAT_SECONDS:
        return (f"{label} declares a black beat of {end - start:.3f}s, "
                f"longer than the {MAX_DECLARED_BLACK_BEAT_SECONDS}s a "
                f"deliberate beat may run")

    return None


def _assert_nothing_covers_a_bookend(v1_clips: list, v2_clips: list) -> None:
    """B-roll must not be laid over a declared card.

    V2 sits above V1, so a cutaway that overlaps an end card hides it
    completely - and the card is the one clip on the timeline whose whole
    job is to be seen. Nothing upstream stops it: `select_broll` picks
    spine block positions, and a card block is a non-speech block like any
    other from where it stands. This is the one place that can see both.
    """
    cards = [c for c in v1_clips if c.get("bookend")]
    if not cards:
        return
    problems = []
    for card in cards:
        for broll in v2_clips:
            overlap = (min(card["timeline_out"], broll["timeline_out"])
                       - max(card["timeline_in"], broll["timeline_in"]))
            if overlap > 0.001:
                problems.append(
                    f"{broll.get('label', '?')} covers "
                    f"{card['label']} for {overlap:.3f}s "
                    f"({card['timeline_in']}s-{card['timeline_out']}s)"
                )
    if problems:
        raise ValueError(
            "B-roll is laid over a declared card, which hides it "
            "completely:\n  - " + "\n  - ".join(problems)
            + "\nA card is not a stretch of timeline to fill - remove the "
              "B-roll assignment on that spine block."
        )


def _assert_timeline_fully_covered(manifest: dict) -> None:
    """Every frame of the timeline must show a clip on some video track.

    The shipped export carried 7.2s of black, 6.4s of it starting at
    4.3s: an intro block was covered with a 3.567s clip and the B-roll
    post-bridge shortened the clip's timeline_end rather than the block,
    leaving nothing underneath.  Nothing upstream could see it - the
    rough-cut review records only negative gaps by design, and the
    B-roll duration invariant was satisfied by the very shortening that
    made the hole.  Only the final ffmpeg probe caught it, one step
    before the run ended, with nothing left to do but fail.

    This is the same check, moved to where it can still be acted on.  A
    black beat the plan deliberately declared is allowed through, within
    the bounds `_undeclared_black_beat_reason` enforces; an undeclared
    hole - which is what shipped - still fails.
    """
    spine_blocks = manifest.get("_spine_blocks") or []
    problems = []
    for start, end in _video_coverage_gaps(manifest):
        reason = _undeclared_black_beat_reason(start, end, spine_blocks)
        if reason:
            problems.append((start, end, reason))
    if not problems:
        return
    total = sum(end - start for start, end, _ in problems)
    raise ValueError(
        f"Timeline has {len(problems)} uncovered range(s) totalling "
        f"{total:.3f}s - these render as black frames:\n  - "
        + "\n  - ".join(
            f"{start:.3f}s to {end:.3f}s ({end - start:.3f}s): {reason}"
            for start, end, reason in problems
        )
        + "\nEvery frame must show a clip on V1 or V2 unless a spine "
          "block declares an intentional black beat."
    )


def _v1_label_at(v1_clips: list, timeline_time: float):
    """Label of the V1 clip covering a timeline position, or None.

    When timeline_time is near a clip boundary, the clip that STARTS
    closest to it wins over the one that ends near it.  This prevents a
    float-precision near-miss (e.g. VFX at 8.38s landing on the clip
    ending at 8.382000000000001s instead of the one starting at 8.382s)
    from stealing a VFX assignment.
    """
    # Tolerance: a VFX start is placed by the planner from rounded
    # timeline positions, so up to ~5ms drift is normal.
    BOUNDARY_TOL = 0.005

    # First: find the clip whose interior clearly contains the point,
    # with a safety margin on the upper bound.
    for clip in v1_clips:
        if (clip["timeline_in"] <= timeline_time
                and timeline_time < clip["timeline_out"] - BOUNDARY_TOL):
            return clip["label"]

    # If no interior match, the point is near a cut. Find the clip
    # whose timeline_in is closest - the point belongs to the clip
    # that starts there, not the one that ends there.
    best_label = None
    best_dist = BOUNDARY_TOL
    for clip in v1_clips:
        dist = abs(clip["timeline_in"] - timeline_time)
        if dist < best_dist:
            best_dist = dist
            best_label = clip["label"]
    return best_label


def _picture_label_at(v1_clips: list, v2_clips: list, timeline_time: float):
    """Label of the clip an effect at this position draws on, or None.

    A visual effect is a per-clip Fusion comp and the renderer walks
    `fusion_tracks.FUSION_COMP_TRACKS` - V1 AND V2 - so a comp reaches a
    B-roll cutaway exactly as it reaches an A-roll clip.  This used to
    read V1 alone and the compile RAISED when nothing matched, which
    killed a whole run over two effects the planner was invited to put on
    cutaway blocks (library/tools/vfx_carriers.py).

    V1 is asked first, which keeps every existing placement exactly where
    it was: where a cutaway covers a speech block both tracks carry a
    clip, and the effect still lands on the V1 clip underneath.  Which
    picture an effect belongs on when two are stacked is a creative
    question nobody has answered, so nothing here answers it.
    """
    return (_v1_label_at(v1_clips, timeline_time)
            or _v1_label_at(v2_clips, timeline_time))


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


def _content_runs(v1_clips: list) -> list:
    """Contiguous runs of non-bookend V1 clips, in timeline order.

    The TV frame dresses the show, not the logo cards: one frame clip
    spans each run of content between (or around) declared cards, so no
    V2 frame clip ever overlaps a card and
    `_assert_nothing_covers_a_bookend` keeps reading true.  A reel with
    no cards yields a single run.
    """
    runs = []
    current = None
    for clip in sorted(v1_clips, key=lambda c: c["timeline_in"]):
        if clip.get("bookend"):
            current = None
            continue
        if current is None:
            current = {
                "index": len(runs),
                "timeline_in": clip["timeline_in"],
                "timeline_out": clip["timeline_out"],
            }
            runs.append(current)
        else:
            current["timeline_out"] = max(
                current["timeline_out"], clip["timeline_out"])
    return runs


def _conform_fields(clip_metadata: dict, clip_id, proj_res,
                    framing_intent: float = None,
                    framing_pan_x: float = None,
                    subject_center_x: float = None,
                    subject_width: float = None,
                    framing_crop_factor: float = None) -> dict:
    """Scale factor needed to fill the output frame, if any.

    ``framing_intent`` is a normalised scalar spanning the continuum from
    full letterbox (0.0) through partial punch-in to complete fill (1.0).
    *None* means nothing declared one, which resolves to
    ``framing_intent.DEFAULT_FRAMING_INTENT`` - see that module for why
    the default is fill and what the inverted heuristic it replaced did to
    project 001.  Callers that resolve the intent themselves (compile_manifest
    does, through ``framing_intent.resolve_framing_intent``) always pass a
    number.

    ``framing_crop_factor`` is an additional zoom multiplier applied on
    top of the fill conform.  1.0 means no extra crop; 1.3 means 30%
    tighter.  Declared per project as ``pipeline.framing_crop_factor``
    or per template as ``style.framing_crop_factor``.  The factor is
    applied BEFORE the subject-safety check, so a crop factor that would
    cut a face still triggers the backdrop route.  *None* resolves to
    ``DEFAULT_CROP_FACTOR`` (1.0).

    ``framing_pan_x`` is a normalised horizontal pan (-1.0 to 1.0) that
    shifts the crop window within the zoomed source.  0 = centred (default).
    At a given zoom the available pan range is
    ``(zoomed_source_width - target_width) / 2`` pixels, and the normalised
    value maps linearly into that range.  Vertical pan is not currently
    exposed - for landscape-in-portrait the crop is always width-limited,
    so Y has no slack.  (The renderer writes these as Resolve's ``Pan`` and
    ``Tilt``; there is no ``PanX``.)

    ``subject_center_x`` is where the subject actually is, 0.0 to 1.0
    across the SOURCE width, from
    ``library/tools/subject_framing.subject_center_x``.  When it is given
    and no explicit ``framing_pan_x`` was set, the pan is derived so the
    subject lands in the middle of the crop window rather than wherever
    dead centre happens to put them.  An explicit ``framing_pan_x`` always
    wins: a per-clip creative choice outranks a measurement.

    ``subject_width`` is how much room the subject NEEDS, as a fraction of
    the source width, from ``subject_framing.subject_box``.  A fill crop of
    landscape source into a portrait frame keeps exactly ``1 / zoom`` of
    the source width, so a subject wider than that is cropped by the frame
    edge no matter where the pan points - which is what happened to
    project 001 (see ``library/tools/subject_framing.py``).  When the fill
    crop is too narrow for them, the conform switches to the BACKDROP
    route below rather than delivering a cropped face.

    This is the only place the subject position becomes a pixel offset.
    The conversion needs the source width, the fit scale, the zoom and the
    target width, all of which are local here - computing it anywhere else
    means two copies of the geometry that can disagree.  The backdrop's
    two Transform values are computed here for the same reason, even
    though a Fusion node is what applies them.

    The backdrop route
    ------------------

    Filling a 1080x1920 frame from 1920x1080 source keeps 31.6% of the
    width.  A talking head does not fit in 31.6% of a selfie, and no zoom
    between letterbox and fill both fills the frame and holds the face -
    the arithmetic is closed, because any zoom below fill leaves bars.  So
    the missing picture is SYNTHESISED: the source is scaled down until
    the subject fits, and the rest of the frame is the same frame again,
    scaled to cover and blurred.  The frame is still entirely picture, the
    face is whole, and the geometry is the same from the first clip to the
    last.

    Resolve's own transform still crops the central 9:16 column at full
    fill zoom.  The composition happens UPSTREAM of it, in the clip's
    Fusion comp, which is why the values handed over are a scale and a
    centre in the comp's own canvas rather than a pan in output pixels.
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

    # The resolved intent travels WITH the clip. Nothing downstream could
    # tell a deliberate letterbox from the removed heuristic's accidental
    # one, because the manifest carried only the RESULT (`fill_zoom`) and
    # never the declaration - so `render_qa`'s occupancy gate would have
    # had to guess what the picture was supposed to look like.
    #
    # `framing_delivered` is the other half, and the two are not always
    # the same number: a source that already covers the frame has no bars
    # to give and fills at every intent. See
    # library/tools/framing_intent.py, "Declared is not delivered".
    resolved_intent = (DEFAULT_FRAMING_INTENT if framing_intent is None
                       else max(0.0, min(1.0, framing_intent)))
    covers = source_covers_frame(width, height, target_w, target_h)

    if covers:
        # Source already matches the target aspect ratio - no conform needed
        # regardless of framing_intent (there are no bars to remove).
        return {"needs_conform": False,
                "framing_intent": resolved_intent,
                "framing_delivered": delivered_framing_intent(
                    resolved_intent, covers)}

    max_zoom = round(fill_scale / fit_scale, 4)

    # ── Apply framing_intent ──
    # There is no second branch.  The one this replaced ran whenever
    # nothing declared an intent and letterboxed any clip whose primary
    # subject was visible - which on a talking head is every clip, so
    # project 001 shipped its A-roll in a 608-row strip of a 1920-row
    # frame.  See library/tools/framing_intent.py.
    intent = resolved_intent
    if intent == 0.0:
        return {"needs_conform": False, "framing_intent": intent,
                "framing_delivered": intent}
    zoom = round(1.0 + (max_zoom - 1.0) * intent, 4)
    # Apply the crop factor: additional tightening on top of the fill
    # conform.  The factor is applied here, before the subject-safety
    # check, so a crop factor that would cut a face still triggers the
    # backdrop route rather than silently cropping the speaker.
    resolved_cf = (DEFAULT_CROP_FACTOR if framing_crop_factor is None
                   else max(DEFAULT_CROP_FACTOR,
                            min(float(framing_crop_factor), 2.0)))
    if resolved_cf > DEFAULT_CROP_FACTOR:
        zoom = round(zoom * resolved_cf, 4)
    result = {
        "needs_conform": True,
        "framing_intent": intent,
        # A partial punch-in delivers exactly what it was told: the frame
        # is covered only at FILL, and below it the bars are narrower
        # rather than gone.
        "framing_delivered": intent,
        "source_width": width,
        "source_height": height,
        "fill_zoom": zoom,
    }
    if resolved_cf > DEFAULT_CROP_FACTOR:
        result["framing_crop_factor"] = resolved_cf

    # ── Does the crop this zoom implies still hold the subject? ──
    # Only in the width-limited case. Portrait source in a portrait frame
    # crops the HEIGHT, and the full source width is always shown, so a
    # face can never be lost off the sides there.
    width_limited = (target_w / width) < (target_h / height)
    subject_safe_zoom = None
    if width_limited and subject_width:
        required = min(1.0, float(subject_width) * (1.0 + 2.0 * SUBJECT_HEADROOM))
        if required > 0:
            result["subject_width"] = round(float(subject_width), 4)
            subject_safe_zoom = round(1.0 / required, 4)
            result["subject_safe_zoom"] = subject_safe_zoom

    # An explicit framing_pan_x is a per-clip creative choice and outranks
    # a measurement, here as everywhere else in this function: it says
    # "aim the crop here", and the backdrop route has nothing to aim.
    explicit_pan = framing_pan_x is not None and framing_pan_x != 0.0

    if (subject_safe_zoom is not None
            and not explicit_pan
            and subject_safe_zoom < zoom - 1e-6):
        # The crop cannot hold them. Show `required` of the source width,
        # centred on the subject, over a blurred copy of the same frame.
        required = 1.0 / subject_safe_zoom
        centre = (float(subject_center_x)
                  if subject_center_x is not None else 0.5)
        column = 1.0 / max_zoom          # of the canvas width, in comp units

        def _centre_for(scale):
            """Transform Center.x that puts `centre` in the column's middle.

            Clamped so the scaled image never uncovers the column: past
            this bound its own edge would slide into frame.
            """
            bound = max(0.0, (scale - column) / 2.0)
            offset = (0.5 - centre) * scale
            return round(0.5 + max(-bound, min(bound, offset)), 4)

        picture_scale = round(column / required, 4)
        result["fill_zoom"] = max_zoom
        # The backdrop route puts the same frame again, scaled to cover
        # and blurred, behind the inset picture. Every row of the frame
        # is picture, so what it DELIVERS is a full frame however far the
        # foreground had to shrink to hold the subject.
        result["framing_delivered"] = FILL
        result["framing_backdrop"] = {
            "visible_source_width": round(required, 4),
            "picture_scale": picture_scale,
            "picture_center_x": _centre_for(picture_scale),
            "backdrop_scale": 1.0,
            "backdrop_center_x": _centre_for(1.0),
        }
        return result

    # Pan: convert normalised (-1..1) to pixel offset.
    zoomed_w = width * fit_scale * zoom
    max_pan_px = (zoomed_w - target_w) / 2.0

    pan_norm = None
    if framing_pan_x is not None and framing_pan_x != 0.0:
        pan_norm = max(-1.0, min(1.0, framing_pan_x))
    elif subject_center_x is not None and max_pan_px > 0:
        # Move the crop window so the subject sits in its middle. A
        # subject left of centre needs the window to travel left,
        # which means the PICTURE travels right - a positive Pan.
        delta_src_px = (0.5 - float(subject_center_x)) * width
        delta_disp_px = delta_src_px * fit_scale * zoom
        pan_norm = max(-1.0, min(1.0, delta_disp_px / max_pan_px))
        # Clamped to the edge means the subject cannot be centred at
        # this zoom; the crop still moves as far toward them as it can.
        if pan_norm == 0.0:
            pan_norm = None

    if pan_norm is not None:
        result["framing_pan_x"] = round(pan_norm * max_pan_px, 2)
    return result


# Every overlay track the manifest can carry, and what it costs when a
# segment the pipeline said it rendered is not on disk.
#
# All three are ADDITIVE: the picture underneath an absent overlay is
# intact, the build succeeds, and the render is a valid video of the
# right length with one capability silently missing. Nothing downstream
# can tell the difference, so this is the only gate that can - which is
# why it refuses rather than warning.
#
# Motion graphics used to be a `logger.warning` here while timed text
# raised, and that asymmetry is the same "planned but absent" silence
# that let project 001's V4 read as delivered.
OVERLAY_TRACKS = {
    "subtitle_overlay": "subtitle overlay",
    "motion_graphics_overlay": "motion graphics",
    "timed_text_overlay": "timed text overlay",
}


class OverlaySegmentMissing(ValueError):
    """A rendered overlay segment the manifest names is not on disk."""


def assert_overlay_segments_on_disk(manifest: dict) -> None:
    """Refuse a manifest that names an overlay file nothing wrote."""
    for key, what in OVERLAY_TRACKS.items():
        for seg in manifest.get(key, {}).get("segments", []):
            path = seg.get("overlay_path")
            if path and not os.path.exists(path):
                raise OverlaySegmentMissing(
                    f"{what} segment declared but missing on disk: "
                    f"{path}. The step that renders it recorded it as "
                    f"rendered. Nothing downstream notices a missing "
                    f"overlay - the picture underneath is intact - so "
                    f"this is the only gate that can.")
            # A sequence names a directory, not a file, and the same
            # rule holds: every frame must be there, because a
            # half-written sequence places short and nothing downstream
            # notices that either.
            frames_info = seg.get("frames") or {}
            frame_dir = frames_info.get("dir", "")
            if frame_dir:
                expected = int(frames_info.get("count", 0))
                try:
                    have = sum(1 for name in os.listdir(frame_dir)
                               if name.endswith(".png"))
                except OSError:
                    have = -1
                if have != expected:
                    raise OverlaySegmentMissing(
                        f"{what} sequence declared with {expected} "
                        f"frames but {frame_dir} holds {have}.")


def compile_manifest(out_dir: str) -> dict:
    global _STATE_OUTPUTS
    _STATE_OUTPUTS = _load_state_outputs(out_dir)

    # Load pipeline step outputs
    # Pipeline writes files as <step_name>.json; fall back to legacy step_X_YY.json
    spine_data = load(out_dir, "mesh_spine.json") or load(out_dir, "step_2_05.json")
    aroll_data = load(out_dir, "assign_aroll.json") or load(out_dir, "step_3_01.json")
    broll_data = load(out_dir, "select_broll.json") or load(out_dir, "step_3_02.json")
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
    # THE RENDER TARGET. It comes from the product - the brand template,
    # with a per-project override - and never from the footage. Captain's
    # ruling, 2026-08-19; see library/tools/delivery_format.py for what
    # reading the source here cost.
    #
    # This used to end "the catalog's `source_resolution` ... is used for
    # conform decisions only", and NOTHING read it: `_conform_fields`
    # takes each clip's OWN `width`/`height` off `clip_metadata`, which
    # is the right grain, and the catalog's `source_resolution` is one
    # project-wide modal number. The sentence was a claim about a read
    # that does not happen. See library/tools/output_contract.py.
    _project_root = os.path.dirname(out_dir) if out_dir != "." else "."
    proj_res = resolve_delivery_format(_project_root)
    
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

    # ── Semantic analysis → neural engine directives ──
    # step_1_03 emits {"semantic_analysis_documents": [...]} and keys its
    # documents by FILE STEM while the catalog uses clip_XXX. This used to
    # read `semantic_data["semantic_analysis"]["clips"]` - a key nothing
    # writes - so the lookup was empty on every run and no clip was ever
    # stabilised. build_semantic_lookup does the id join in one place.
    semantic_lookup = build_semantic_lookup(
        semantic_data, catalog_data.get("clip_catalog", []))

    # Index the documents key directly rather than trusting truthiness:
    # step 1.03's output is what has to join, and a join that produces
    # nothing from real documents is a failure, not a quiet skip.
    if isinstance(semantic_data, list):
        semantic_docs = semantic_data
    elif isinstance(semantic_data, dict):
        semantic_docs = (semantic_data.get("semantic_analysis_documents")
                         or semantic_data.get("semantic_analysis"))
    else:
        semantic_docs = None
    if semantic_docs and not semantic_lookup:
        raise ValueError(
            f"semantic_analysis carries {len(semantic_docs)} document(s) but "
            f"none of them joined to a catalog clip. Stabilisation and Super "
            f"Scale decide off this lookup, so an empty join means no clip "
            f"gets either."
        )

    neural_engine_directives = {}

    def compute_neural_directives(clip_id, clip_entry):
        directives = {}
        sem = semantic_lookup.get(clip_id) or {}
        meta = clip_metadata.get(clip_id, {})

        # Read the adapter's derived view, which exists for both the v3
        # schema (scene/camera/actions/objects) and the retired one. The
        # old code read `tags`/`description`, which neither schema has.
        analysis = sem.get("analysis") or {}
        assessment = sem.get("assessment") or {}
        text_data = " ".join(str(part).lower() for part in (
            stability_summary(sem),
            analysis.get("motion") or camera_prose(sem),
            analysis.get("scene") or "",
            " ".join(str(k) for k in (assessment.get("keywords") or [])),
        ) if part)

        if text_data:
            unstable = any(w in text_data for w in _UNSTABLE_CAMERA_WORDS)
            if unstable:
                directives["stabilize"] = True

        # Magic Mask is NOT emitted. DaVinci's CreateMagicMask returns
        # False for every mode on the supported build, so a directive for
        # it could only ever be a promise nothing kept.

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

    # ── Framing intent ──
    # One enumeration, library/tools/framing_intent.py: spine block >
    # project.yaml pipeline.framing_intent > brand template
    # style.framing_intent > the default (fill). The template file and the
    # project.yaml are static config, not pipeline step outputs, so this
    # reads them directly rather than threading a DAG edge - the same
    # reasoning delivery_format uses.
    #
    # This is NOT wrapped in a try/except any more. It used to be, and a
    # malformed template silently degraded to "no framing at all"; a
    # framing declaration that is dropped is a frame the editor believes
    # shipped.
    _template = resolve_project_template(
        project_template_name(_project_root), project_folder=_project_root)

    def _resolve_framing(block_or_clip):
        """Return (framing_intent, framing_pan_x, framing_crop_factor) for a clip."""
        fi = resolve_framing_intent(
            block_intent=block_or_clip.get("framing_intent"),
            project_folder=_project_root,
            template=_template,
        )
        pan = block_or_clip.get("framing_pan_x")
        if pan is not None:
            try:
                pan = float(pan)
            except (TypeError, ValueError):
                pan = None
        cf = resolve_crop_factor(
            block_crop_factor=block_or_clip.get("framing_crop_factor"),
            project_folder=_project_root,
            template=_template,
        )
        return fi, pan, cf

    # ── Subject-aware framing (P1.2) ──
    # Where the subject actually is, so a crop follows them instead of
    # centring blindly. The only subject geometry the pipeline measures is
    # the face-centre track in the temporal index; see
    # library/tools/subject_framing.py for why, and for why silence is a
    # legitimate answer.
    #
    # This bites ONLY on clips already pushed toward fill by a template or
    # a spine block. The default framing is unchanged: a clip with no
    # framing_intent still runs the legacy letterbox heuristic and never
    # reaches the branch that reads this.
    #
    # Reads the per-clip index FILES directly rather than the in-state
    # full_indices, so the heavy 2.35 MB state copy is not needed.
    _project_dir = os.path.dirname(os.path.abspath(out_dir))
    _subject_faces = subject_centers_by_clip(_project_dir)

    def _face_track(clip_id):
        """This clip's `face_presence` block, or None."""
        if not _subject_faces or clip_id is None:
            return None
        face = _subject_faces.get(clip_id)
        if face is None:
            # The temporal index is keyed by clip id in some runs and by
            # file stem in others - the same split semantic_index bridges.
            meta = clip_metadata.get(clip_id) or {}
            stem = meta.get("file_stem")
            if not stem:
                src = meta.get("source_file") or meta.get("path") or ""
                stem = os.path.splitext(os.path.basename(src))[0] if src else None
            if stem:
                face = _subject_faces.get(stem)
        return face

    def _subject_framing(clip_id, source_in, source_out):
        """The two subject arguments `_conform_fields` takes.

        Kept as one call so the position and the width can never be read
        off different clips, and so a new call site cannot pick up the
        aim without the size - which is the pair whose separation cropped
        001's face.
        """
        face = _face_track(clip_id)
        if face is None:
            return {"subject_center_x": None, "subject_width": None}
        box = subject_box(face, source_in, source_out)
        return {
            "subject_center_x": subject_center_x(face, source_in, source_out),
            "subject_width": box.width if box else None,
        }

    # ── V1: A-Roll clips (from spine speech blocks) ──
    v1_clips = []
    for block in structure:
        # A declared intro / outro / end card plays a finished clip, so it
        # goes on V1 like any other picture: inside the coverage
        # assertion, inside project.duration_seconds, and visible to the
        # render QA in step 6.02. The retired import_endcard.py appended
        # one to the timeline AFTER the manifest was written, which left
        # every gate describing a video that no longer existed.
        bookend = block_bookend(block)
        if bookend:
            clip = {
                "source_file": bookend["asset_path"],
                # A rendered card starts at its own frame 0 and runs its
                # declared length; there is no footage to trim into.
                "source_in": 0.0,
                "source_out": bookend["duration_seconds"],
                "timeline_in": block.get("timeline_start", 0.0),
                "timeline_out": block.get("timeline_end", 0.0),
                "timeline_in_frame": block.get("timeline_start_frame"),
                "timeline_out_frame": block.get("timeline_end_frame"),
                "link_group_id": None,
                "label": f"bookend_{bookend['slot']}",
                # Read by the renderer to skip the A1 placement, and by
                # manifest_validator to exempt the card from the checks
                # that only make sense for cut footage.
                "bookend": bookend["slot"],
                "video_only": not bookend.get("has_audio", False),
            }
            if clip["timeline_in_frame"] is None:
                convert_clip_to_frames(clip, fps)
            # No _conform_fields: a card is authored at the project
            # resolution, so there is no framing decision to make and no
            # catalog entry to make it from.
            v1_clips.append(clip)
            continue

        # `block_reaches_v1` is the one statement of V1 membership, and
        # step 4.02's bridge reads the same predicate off the spine to
        # tell the model which cuts can carry a drawn transition at all.
        # The two must not drift: a transition planned where no V1 clip
        # ends fails this step outright (see the fusion transition loop).
        if block_reaches_v1(block):
            content = block.get("content") or {}
            lgid = (block.get("link_group_id")
                    or content.get("link_group_id"))

            assignment = a_roll_dict.get(block.get("position"))
            _fi, _fp, _cf = _resolve_framing(block)

            # A V1 clip cut from a picture-led block carries ranges the
            # model chose, not word alignments - whole seconds are what
            # a correct one looks like, so the fabricated-range check in
            # manifest_validator exempts what this marks. Same shape as
            # the `bookend` marker card clips carry.
            picture_led = block.get("block_type") == "picture"

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
                    if picture_led:
                        clip["picture_led"] = True
                    convert_clip_to_frames(clip, fps)
                    clip.update(_conform_fields(clip_metadata, get_clip_id(seg), proj_res,
                        framing_intent=_fi, framing_pan_x=_fp,
                        framing_crop_factor=_cf,
                        **_subject_framing(
                            get_clip_id(seg), clip.get("source_in", 0.0),
                            clip.get("source_out", 0.0))))
                    v1_clips.append(clip)
                    compute_neural_directives(get_clip_id(seg), clip)
                    current_tl_in += dur
            elif assignment:
                clip = {
                    "source_file": resolve_source(assignment),
                    "source_in": block.get("source_start", 0.0),
                    "source_out": block.get("source_end", 0.0),
                    "timeline_in": block.get("timeline_start", 0.0),
                    "timeline_out": block.get("timeline_end", 0.0),
                    "timeline_in_frame": block.get("timeline_start_frame"),
                    "timeline_out_frame": block.get("timeline_end_frame"),
                    "link_group_id": lgid,
                    "label": f"{block['block_type']}_{block['position']}",
                }
                if picture_led:
                    clip["picture_led"] = True
                if clip["timeline_in_frame"] is None:
                    convert_clip_to_frames(clip, fps)
                clip.update(_conform_fields(clip_metadata, get_clip_id(assignment), proj_res,
                    framing_intent=_fi, framing_pan_x=_fp,
                    framing_crop_factor=_cf,
                    **_subject_framing(
                        get_clip_id(assignment), clip.get("source_in", 0.0),
                        clip.get("source_out", 0.0))))
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
                if picture_led:
                    clip["picture_led"] = True
                if clip["timeline_in_frame"] is None:
                    convert_clip_to_frames(clip, fps)
                clip.update(_conform_fields(clip_metadata, get_clip_id(block), proj_res,
                    framing_intent=_fi, framing_pan_x=_fp,
                    framing_crop_factor=_cf,
                    **_subject_framing(
                        get_clip_id(block), clip.get("source_in", 0.0),
                        clip.get("source_out", 0.0))))
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
        _fi, _fp, _cf = _resolve_framing(broll)
        v2_clip.update(_conform_fields(clip_metadata, get_clip_id(broll), proj_res,
            framing_intent=_fi, framing_pan_x=_fp,
            framing_crop_factor=_cf,
            **_subject_framing(
                get_clip_id(broll), v2_clip.get("source_in", 0.0),
                v2_clip.get("source_out", 0.0))))
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
        _fi, _fp, _cf = _resolve_framing(interj)
        v2_clip.update(_conform_fields(clip_metadata, get_clip_id(assigned), proj_res,
            framing_intent=_fi, framing_pan_x=_fp,
            framing_crop_factor=_cf,
            **_subject_framing(
                get_clip_id(assigned), v2_clip.get("source_in", 0.0),
                v2_clip.get("source_out", 0.0))))
        v2_clips.append(v2_clip)
        v2_kinds[v2_clip["label"]] = "interjection"
        compute_neural_directives(get_clip_id(assigned), v2_clip)

    broll_dropped_by_overlap = _resolve_v2_overlaps(v2_clips, fps, v2_kinds)
    _assert_nothing_covers_a_bookend(v1_clips, v2_clips)

    # ── The TV-frame look (2026-09-09, captain's Reel 20 marker) ──
    # The shot punched in on V1, the frame asset at native 1:1 on V2,
    # captions above on V3.  The declaration (asset, factor, power
    # timings) is library/tools/tv_frame.py; this is where it lands.
    # Not wrapped in try/except: like the framing template read above,
    # a malformed declaration raises rather than degrading to a look
    # the editor believes shipped.
    tv_look = resolve_tv_frame(_project_root, _template)
    tv_power_clips = {}
    if tv_look is not None:
        # CHECKED against the frame this product ships in, before a
        # single clip carries the look: a frame with no transparent
        # window is a slate over the picture, and one that covering
        # would upscale is a bezel drawn softer than its own pixels.
        # A mismatched ASPECT is not refused - it is cover-scaled
        # (tv_frame.cover_zoom), which is what the captain set by hand
        # on 2026-09-09.
        assert_frameable(tv_look, proj_res[0], proj_res[1])
        if broll_assignments or broll_interjections:
            raise ValueError(
                "tv_frame is declared but the plan also carries B-roll "
                f"({len(broll_assignments)} assignments, "
                f"{len(broll_interjections)} interjections): the frame "
                "spans the reel on V2, which is the track B-roll plays "
                "on, so the two cannot share a reel.  A reel under this "
                "look carries no B-roll cutaways - the window is the "
                "variety."
            )
        punch = tv_look["punch_in"]
        content_clips = [c for c in v1_clips if not c.get("bookend")]
        for clip in content_clips:
            # ABSOLUTE, not over the conform: under the frame the bezel
            # is the framing (tv_frame.v1_zoom_for_look).  The conform
            # pan/tilt still apply, so subject tracking survives.
            clip["fill_zoom"] = v1_zoom_for_look(punch)
            clip["needs_conform"] = True
            clip["tv_punch_in"] = punch
        # One frame clip per contiguous run of content: the set dresses
        # the show, not the logo cards, and a V2 clip over a card would
        # trip _assert_nothing_covers_a_bookend above.
        for run in _content_runs(v1_clips):
            frame_clip = {
                "source_file": tv_look["asset"],
                "source_in": 0.0,
                "source_out": run["timeline_out"] - run["timeline_in"],
                "timeline_in": run["timeline_in"],
                "timeline_out": run["timeline_out"],
                "video_only": True,
                "label": f"tv_frame_{run['index']}",
                # No conform fields and no subject fields: the asset
                # plays at native 1:1, exactly as the reference (V2 Zoom
                # 1.00), so there is no zoom for the P8 subject check to
                # read and no crop to judge.
            }
            convert_clip_to_frames(frame_clip, fps)
            v2_clips.append(frame_clip)
        # The power animation runs on the picture, not the set: switch
        # on over the first content clip, switch off over the last.
        if content_clips:
            tv_power_clips = {
                content_clips[0]["label"]: "head",
                content_clips[-1]["label"]: "tail",
            }

    # ── A2: Music ──
    ms = music_data.get("music_selection", {})
    # The audit trail is a sidecar, not a spine key (captain's ruling,
    # 2026-09-16: the record is KEPT, only the carrier changes), and
    # step 2.04 writes it warn-and-continue - one WARNING line in a
    # long stderr stream nobody is looking for. So the pre-render
    # check asserts it here, whenever a selection was resolved: a run
    # that kept the choice but lost the record refuses loudly instead
    # of quietly reverting half of that ruling while reporting
    # success. See library/tools/music_audit_trail.py.
    assert_audit_trail_present(_project_root, ms)
    # WHICH PARTS of WHICH TRACKS play, and WHERE, is the plan's decision
    # and this is where it lands. `source_in: 0.0` used to be a literal
    # here, so the `splices` step 2.04's handoff has always asked for
    # reached nothing (AGENTS.md 10.2); then one `section` landed, and a
    # bed that is several sections of several tracks still could not be
    # expressed at all. `music_bed.resolve_bed` is the whole of it: the
    # spine conducts the pieces, this places them, and a selection that
    # declares no bed still gets exactly the one-section placement it
    # always got. It RAISES rather than sliding a segment to fit, because
    # moving a start is choosing which part plays.
    music_bed = resolve_bed(ms, spine, total_duration)
    print("  " + describe_music_bed(music_bed), file=sys.stderr)
    music_clips = bed_clips(music_bed, fps=fps)

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
    cohesion_record = apply_cohesion_adjustments(
        transitions, cohesion_data.get("cohesion_review", {}))
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

    sfx_catalog = load_sfx_catalog()

    # Compile SFX into builder-compatible format.
    # There is ONE representation of SFX in the manifest: tracks.A3.clips.
    # The old top-level `sfx` list held only the entries that failed to
    # resolve, so a healthy run wrote `sfx: []` next to a populated A3 and
    # nothing could tell whether SFX had been planned at all.
    #
    # WHICH FILE PLAYS IS CARRIED, NOT RE-DERIVED. This step used to call
    # `match_sfx_file(sfx_type, ...)` and keyword-match the library all
    # over again, so the sound step 4.04 chose and the sound that reached
    # A3 were two separate answers to two different questions. It now
    # reads the `source_file` step 4.04 resolved, and checks it is still
    # on disk.
    a3_clips = []
    sfx_unresolved = []
    for si, sfx_entry in enumerate(sfx_list):
        # Which sound plays is a decision step 4.04 makes, not one this
        # step fills in. Defaulting to "whoosh" here put a sound on A3
        # that nothing had chosen.
        sfx_id = sfx_entry.get("sfx_id")
        source_file = sfx_entry.get("source_file")
        if not source_file and sfx_id:
            catalog_entry = resolve_sfx_id(sfx_id, sfx_catalog)
            if catalog_entry:
                source_file = catalog_entry["path"]
        if not source_file:
            raise ValueError(
                f"SFX entry {sfx_entry.get('label', si)} names no sound: it "
                f"carries neither `source_file` nor an `sfx_id` in the SFX "
                f"library catalogue. No sound is substituted. A plan "
                f"written before the catalogue existed carries an "
                f"`sfx_type` instead - re-run it with: "
                f"manage_project.py run <slug> --rerun plan_sfx"
            )
        # step_4_04 emits timeline_in/timeline_out (seconds).
        tl_start_sec = sfx_entry.get("timeline_in",
                                     sfx_entry.get("timeline_start"))
        if tl_start_sec is None:
            raise ValueError(
                f"SFX entry {sfx_entry.get('label', si)} has no timeline "
                f"position (timeline_in / timeline_start)"
            )
        # No default. A -14 dB fallback used to sit here, on top of a
        # track level of -12 dB annotated "Subtle - felt more than
        # heard", and the one sound of the run of record was inaudible in
        # the finished video. How loud a sound plays is the plan's
        # decision and nothing substitutes one - see
        # library/tools/sfx_level.py.
        vol_db, vol_reason = read_volume_db(sfx_entry)
        if vol_db is None:
            raise ValueError(
                f"SFX entry {sfx_entry.get('label', si)} names no level: "
                f"{vol_reason}. Re-run step 4.04 (plan_sfx) so the plan "
                f"says how loud each sound plays: "
                f"manage_project.py run <slug> --rerun plan_sfx"
            )

        tl_end_sec = sfx_entry.get("timeline_out",
                                   sfx_entry.get("timeline_end"))
        if not tl_end_sec:
            raise ValueError(
                f"SFX entry {sfx_entry.get('label', si)} has no timeline_out. "
                f"How long a sound runs is its own measured length, read in "
                f"step 4.04 - nothing here invents one."
            )

        if os.path.exists(source_file):
            src_in = sfx_entry.get("source_in", 0.0)
            if src_in in (None, "unknown"):
                src_in = 0.0

            a3_clips.append({
                "source_file": source_file,
                "source_in": float(src_in),
                "timeline_in": round(tl_start_sec, 3),
                "timeline_out": round(tl_end_sec, 3),
                "timeline_in_frame": seconds_to_frame(tl_start_sec, fps),
                "timeline_out_frame": seconds_to_frame(tl_end_sec, fps),
                "volume_db": vol_db,
                # A sound the plan cut short stops mid-waveform, and that
                # step to silence clicks. Step 4.04 measured the ramp;
                # `otio_mix` is what turns it into volume keyframes. 0.0
                # where the sound plays to its own end.
                "fade_out_seconds": float(
                    sfx_entry.get("fade_out_seconds") or 0.0),
                "label": sfx_entry.get("label", f"sfx_{si+1:03d}"),
                "sfx_id": sfx_id or os.path.basename(source_file),
            })
            # The plan position travels with the clip so the manifest
            # validator can tell a layer from a collapse the same way
            # step 4.04 does: entries naming one spine_block_position
            # are a layered moment, entries naming several that landed
            # on one timeline position are a placement collapse. Only
            # carried when the plan named one - a None here would read
            # as provenance where there is none.
            if sfx_entry.get("spine_block_position") is not None:
                a3_clips[-1]["spine_block_position"] = sfx_entry.get(
                    "spine_block_position")
        else:
            sfx_unresolved.append(
                f"{sfx_entry.get('label', si)} (sfx_id={sfx_id}): the file "
                f"step 4.04 chose is no longer on disk: {source_file}"
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

    # Why the plan is the length it is.  An empty `visual_effects` used
    # to read the same whether step 4.03's planner chose stillness or
    # named effects the post-bridge could not build, and the drop
    # reasons went only to that step's stderr.  This is where the
    # absence becomes final, so it is where it is said.
    # See `library/tools/vfx_plan_basis.py`.
    vfx_planning_basis = {}
    if isinstance(vfx_container, dict):
        vfx_basis = vfx_container.get("planning_basis")
        if isinstance(vfx_basis, dict) and vfx_basis.get("basis"):
            vfx_planning_basis = dict(vfx_basis)
            logger.info("%s", basis_summary(vfx_basis))
            if vfx_basis["basis"] == "every_entry_dropped":
                for drop in vfx_basis.get("dropped") or []:
                    logger.warning(
                        "  VFX not built: block %s asked for %r - %s",
                        drop.get("target_block_position"),
                        drop.get("effect_type"), drop.get("reason"),
                    )

    # ── Generator overlays ──
    # Generator presets (particles, backgrounds, etc.) produce content from
    # nothing and are routed to the overlay track. They travel inside
    # enhancement_spec.generator_overlays from step 4.03's post_bridge.
    generator_overlays = []
    if isinstance(vfx_container, dict):
        generator_overlays = vfx_container.get("generator_overlays", [])
    for go in generator_overlays:
        if go.get("timeline_end", 0) > total_duration:
            go["timeline_end"] = total_duration

    # ── Build fusion_effects section ──
    # Per-clip VFX: every planned VFX becomes a Fusion comp on the V1 clip
    # it covers.  This used to require the planner to emit a `preset` key
    # that step_4_03 never wrote, so per_clip came out empty on every run
    # and the whole polish layer silently vanished.
    per_clip_effects = {}
    unplaced_vfx = []
    vfx_assigned_labels = set()
    vfx_collisions = []
    for v in vfx:
        label = _picture_label_at(v1_clips, v2_clips, v["timeline_start"])
        if label is None:
            unplaced_vfx.append(v)
            continue
        if label in vfx_assigned_labels:
            vfx_collisions.append(
                f"{v.get('effect_type', '?')}@{v['timeline_start']}s "
                f"collided on {label}"
            )
        vfx_assigned_labels.add(label)
        effect = per_clip_effects.setdefault(label, {})
        effect["_preset"] = v.get("preset", v.get("fusion_preset")) or v["effect_type"]
        effect.update(v.get("params", {}))

    # ── The designed film look (color_grade node_4) ──
    # Glow, grain and vignette are Fusion nodes, so the only route to the
    # picture is the per-clip effects the comp engine reads. Without this
    # merge, four of the grade's five nodes were designed and discarded.
    # A VFX entry's own value wins: the planner asked for that one.
    fusion_look = color_data.get("color_grade_spec", {}).get("fusion_look", {})
    if fusion_look:
        for clip in v1_clips + v2_clips:
            # Not the cards. A declared intro / outro / end card is a
            # finished graphic - often a client's own brand asset - and
            # putting the house glow, grain and vignette over it would
            # regrade artwork that was delivered the way it is meant to
            # look. The CDL half already skips it: it is not in the
            # catalog, so step 5.01 writes no adjustment for it.
            if clip.get("bookend"):
                continue
            effect = per_clip_effects.setdefault(clip["label"], {})
            for key, value in fusion_look.items():
                effect.setdefault(key, value)

    # ── Subject-scoped grades (5.01 subject_grades) ──
    # Each entry grounds against its clip's 1.06 segmentation, writes a
    # timeline-rate matte under this step's own directory (the layout
    # answers "which step wrote this" by where the file is), and merges
    # the masked-grade keys onto every placement cut from that source.
    # What does not ground is a drop with a reason, never a
    # whole-frame grade in its place.
    subject_mattes = []
    subject_grade_drops = list(
        color_data.get("color_grade_spec", {}).get(
            "subject_grade_drops", []) or [])
    subject_entries = (
        color_data.get("color_grade_spec", {}).get("subject_grades", [])
        or [])
    if subject_entries:
        _seg_dir = os.path.join(out_dir, "1_06_object_segmentation")
        _matte_dir = os.path.join(out_dir, "subject_mattes")
        _source_to_cid = {path: cid
                          for cid, path in clip_lookup.items()}
        _seg_cache: dict = {}

        def _seg_for(cid):
            if cid not in _seg_cache:
                _seg_cache[cid] = None
                _seg_path = os.path.join(
                    _seg_dir, f"{cid}_segmentation.json")
                if os.path.exists(_seg_path):
                    with open(_seg_path, encoding="utf-8") as f:
                        _seg_cache[cid] = json.load(f)
            return _seg_cache[cid]

        _seen_drop_keys = {
            (d.get("clip_id"), d.get("reason")) for d in subject_grade_drops}
        for clip in v1_clips + v2_clips:
            if clip.get("bookend"):
                continue
            cid = _source_to_cid.get(clip.get("source_file"))
            if cid is None:
                continue
            wanted = [e for e in subject_entries
                      if e.get("clip_id") == cid]
            if not wanted:
                continue
            meta = clip_metadata.get(cid, {})
            _cw, _ch = meta.get("width"), meta.get("height")
            dur = max(int(round((clip.get("timeline_out", 0)
                                 - clip.get("timeline_in", 0)) * fps)), 0)
            seg = _seg_for(cid)
            _sample_fps = float((seg or {}).get("sample_fps") or 2.0)
            patch, drops, mattes = apply_subject_grades(
                wanted, {cid: seg}, matte_dir=_matte_dir,
                timeline_fps=float(fps),
                played_frames={cid: dur},
                source_resolution=(
                    {cid: (_ch, _cw)} if _cw and _ch else {}),
                start_samples={cid: int(float(
                    clip.get("source_in", 0.0)) * _sample_fps)},
                matte_stems={cid: f"{cid}_{clip['label']}"})
            for drop in drops:
                _drop_key = (drop.get("clip_id"), drop.get("reason"))
                if _drop_key not in _seen_drop_keys:
                    _seen_drop_keys.add(_drop_key)
                    subject_grade_drops.append(drop)
                    logger.warning("  Subject grade not built: %s",
                                   drop.get("detail"))
            if cid in patch:
                effect = per_clip_effects.setdefault(clip["label"], {})
                effect.update(patch[cid])
            subject_mattes.extend(mattes)

    # ── The subject-safe conform's own comp (§10.3) ──
    # `_conform_fields` decided the geometry; this is the only thing that
    # carries it to the picture. A clip whose fill crop is too narrow for
    # its subject gets a comp whether or not any VFX or house look put one
    # there, because without it the clip renders as the cropped face the
    # conform declined to deliver.
    for clip in v1_clips + v2_clips:
        backdrop = clip.get("framing_backdrop")
        if not backdrop:
            continue
        effect = per_clip_effects.setdefault(clip["label"], {})
        effect["backdrop_picture_scale"] = backdrop["picture_scale"]
        effect["backdrop_picture_center_x"] = backdrop["picture_center_x"]
        effect["backdrop_scale"] = backdrop["backdrop_scale"]
        effect["backdrop_center_x"] = backdrop["backdrop_center_x"]

    # ── The TV power animation (§tv_power) ──
    # Switch on over the first content clip, switch off over the last.
    # The keys ride the same per-clip effects the comp builder reads,
    # so the animation is a comp on the picture, not a second system -
    # and the timings travel with them, resolved from the declaration
    # (project over template over the module defaults).
    # ONE shape, two directions (library/tools/tv_power.py): the head
    # and the tail read the same declaration, so a project re-timing
    # the switch re-times both halves of it and cannot re-time one.
    for label, half in tv_power_clips.items():
        effect = per_clip_effects.setdefault(label, {})
        timing = dict(switch_shape())
        timing.update((tv_look or {}).get("power", {}) or {})
        key = "tv_power_head" if half == "head" else "tv_power_tail"
        effect[key] = True
        effect[f"{key}_timing"] = timing

    # An entry over a stretch of timeline with no clip on V1 OR V2 cannot
    # be drawn - there is no picture to put a comp on.  It used to RAISE,
    # and that refusal killed a run over effects the planner was invited
    # to place: step 4.03's table lists every block, cutaways included,
    # and nothing told it a comp only reached V1.  It reaches both, so a
    # cutaway effect now lands (`_picture_label_at`), and what is left
    # here is the genuine case.  AGENTS.md §10.5: an entry that cannot be
    # delivered is DROPPED with the reason, in the shape
    # `vfx_plan_basis` already carries, so the reviewer reads it in the
    # output rather than in a log line forty minutes into a run.
    if unplaced_vfx:
        unplaced_ids = {id(v) for v in unplaced_vfx}
        entries_seen = len(vfx)
        vfx = [v for v in vfx if id(v) not in unplaced_ids]
        drops = [
            DroppedEntry(
                target_block_position=v.get("target_block_position"),
                effect_type=v.get("effect_type", "") or "",
                reason="no_clip_at_that_position",
                detail=(
                    f"Dropped VFX {v.get('effect_type', '?')!r} at "
                    f"{v.get('timeline_start')}s: no clip on V1 or V2 "
                    f"covers that position, so there is no picture to "
                    f"draw the comp on."
                ),
            )
            for v in unplaced_vfx
        ]
        vfx_planning_basis = amend_with_drops(
            vfx_planning_basis, drops, entries_seen)
        for drop in drops:
            logger.warning("  VFX not built: %s", drop.detail)

    # Fusion transitions: convert step_4_02 transitions to .comp format.
    # `after_clip` is the index of the OUTGOING V1 clip - the one whose
    # tail the transition sits on.
    fusion_transitions = []
    transitions_downgraded = []
    for t in transitions:
        raw_type = t.get("transition_type", "hard_cut")
        comp_type = canonical_type(raw_type)
        if comp_type is None:
            # step_4_02 is the gate that enforces the vocabulary; state
            # written before it existed can still carry a withdrawn type.
            # Downgrade to the hard cut it will actually look like and put
            # that on the record - what must never happen is shipping a
            # different creative transition in its place, or a comp with
            # nothing in it.
            reason = withdrawal_reason(raw_type)
            transitions_downgraded.append({
                "transition_id": t.get("transition_id", "?"),
                "requested_type": raw_type,
                "shipped_type": "hard_cut",
                "reason": reason,
            })
            print(
                f"  Transition {t.get('transition_id', '?')} requested "
                f"{raw_type!r}, shipping a hard cut: {reason}",
                file=sys.stderr,
            )
            t["transition_type"] = "hard_cut"
            t["duration_frames"] = 0
            continue
        if is_cut(comp_type):
            continue

        # A drawn transition whose hold nobody stated is the same shape
        # as a withdrawn type above: the boundary the plan named is
        # real, but neither the plan's `duration_feel` nor a selected
        # brand template said how long to hold it, so there is nothing
        # to render the effect for. Emitting it at zero frames leaves a
        # comp that draws nothing (the unread-parameter failure of
        # AGENTS.md 10.2); completing it from a constant is the
        # invented-taste failure of 10.5. What ships is the hard cut
        # the boundary already is - the absence of decoration, never a
        # choice of effect - recorded in `transitions_downgraded`
        # beside the withdrawn types, never silent. Step 4.02's
        # post-bridge drops such entries at plan time with the same
        # reasoning; this path serves only state that reached the
        # compile some other way (stale pipeline_data, a revised
        # review gate), and it serves it honestly rather than stopping
        # a run whose picture is still fully decided.
        dur = t.get("duration_frames")
        if (not isinstance(dur, (int, float)) or isinstance(dur, bool)
                or dur <= 0):
            transitions_downgraded.append({
                "transition_id": t.get("transition_id", "?"),
                "requested_type": raw_type,
                "shipped_type": "hard_cut",
                "reason": (
                    "the plan states no duration_feel and no selected "
                    "brand template states a single transition duration, "
                    "so nothing has said how long to hold it"
                ),
            })
            print(
                f"  Transition {t.get('transition_id', '?')} requested "
                f"{raw_type!r} with no duration, shipping a hard cut: "
                f"nothing stated how long to hold it",
                file=sys.stderr,
            )
            t["transition_type"] = "hard_cut"
            t["duration_frames"] = 0
            continue

        cut_time = t.get("cut_point_timeline", t.get("cut_point_original"))
        after_clip = _v1_index_ending_at(v1_clips, cut_time)
        if after_clip is None:
            raise ValueError(
                f"Transition {t.get('transition_id', '?')} at {cut_time}s "
                f"does not sit at the end of any V1 clip"
            )

        # The effect is a tail on the outgoing clip AND a head on the
        # incoming one, so there has to be an incoming clip. Without this
        # a transition on the last clip drew half a transition into
        # nothing.
        if after_clip + 1 >= len(v1_clips):
            raise ValueError(
                f"Transition {t.get('transition_id', '?')} sits at the end "
                f"of the last V1 clip ({after_clip}); there is no incoming "
                f"clip for its head effect"
            )

        # The key is read directly, never defaulted: the downgrade
        # above guarantees every drawn transition reaching here carries
        # a stated hold, and a missing key is a programming error that
        # must fail loudly rather than invent one (AGENTS.md 10.5).
        fusion_transitions.append({
            "type": comp_type,
            "after_clip": after_clip,
            "duration_frames": t["duration_frames"],
        })

    # Audio config
    audio_preset = sfx_preset or audio_mix_data.get("audio_mix_spec", {}).get("fairlight_preset", "")
    audio_config = {
        "fairlight_preset": audio_preset,
    }

    # A bed running past the last PICTURE pads the export with black, so it
    # is clamped.  The bound is the end of the picture - V1 AND V2 - not the
    # end of V1.  It used to be V1 alone, and an outro cutaway sits on V2:
    # 001's four-second ending declared `music_behavior: "fade_out"` at
    # -12 dB, `audio_mix` wrote the automation for it, and this clamp then
    # deleted the bed the automation was written for.  The shipped render
    # ends on 4.0s measured at -91.0 dB - absolute digital silence - and the
    # only notice was a WARNING on stderr forty minutes into an unattended
    # run.  See AGENTS.md 10.5: a plan's declared sound is not dropped
    # quietly.
    picture_clips = v1_clips + v2_clips
    if picture_clips and music_clips:
        last_picture_end = max(
            (c.get("timeline_out", 0) for c in picture_clips), default=0)
        for mc in music_clips:
            if mc.get("timeline_out", 0) > last_picture_end:
                trimmed_from = mc["timeline_out"]
                mc["timeline_out"] = max(mc.get("timeline_in", 0), last_picture_end)
                mc["source_out"] = mc.get("source_in", 0) + (mc["timeline_out"] - mc.get("timeline_in", 0))
                logger.warning(
                    "Music bed ran to %.3fs, past the %.3fs of picture - "
                    "clamped. Any music_automation past %.3fs has no bed.",
                    trimmed_from, last_picture_end, last_picture_end,
                )

    # ── Compile ──
    manifest = {
        "project": {
            # The project says where its own build goes; a project that
            # declares nothing gets the name this pipeline has always
            # used.  It was a literal here, paired with delete_existing
            # in step 6.01, so a second render destroyed whatever already
            # carried the name - including a timeline the captain had
            # been annotating.  See brand_registry.project_timeline_name.
            "name": project_timeline_name(_project_root),
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
                # The audio that comes with the A-roll. A silent card -
                # an end card, a logo animation - is deliberately absent:
                # listing it here would claim an audio stream the file
                # does not have.
                "clips": sorted(
                    (c for c in v1_clips if not c.get("video_only")),
                    key=lambda c: c["timeline_in"]),
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
        # Why the VFX layer is the length it is, INCLUDING what this step
        # dropped. Step 4.03 records its own drops; a block with no clip
        # on either video track is one only the compiler can see, and
        # both accounts belong in one record rather than two halves in
        # two places. See library/tools/vfx_plan_basis.py.
        "vfx_planning_basis": vfx_planning_basis,
        "generator_overlays": generator_overlays,
        "fusion_effects": {
            "per_clip": per_clip_effects,
            # THE authoritative transition list for the renderer: each
            # entry carries the index of the outgoing V1 clip. The
            # top-level "transitions" key above is the planner's record
            # and carries no index - reading it is what put every
            # transition on clip 0.
            "transitions": fusion_transitions,
        },
        # Types the plan asked for that nothing can draw, and what shipped
        # instead. Empty on any run whose plan came from step_4_02 at or
        # after the vocabulary was unified.
        "transitions_downgraded": transitions_downgraded,
        "neural_engine_directives": neural_engine_directives,
        # The authoritative record of what creative_cohesion asked for and
        # what actually happened to each request.
        "cohesion_adjustments": cohesion_record,
        "audio": audio_config,
        "color_grade": color_data.get("color_grade_spec", {}),
        # Subject-scoped grades: the matte records (which files gate
        # which clip's grade) and every entry that did not survive, in
        # one place. The validator holds the records to disk.
        "subject_mattes": subject_mattes,
        "subject_grade_drops": subject_grade_drops,
        "audio_mix": audio_mix_data.get("audio_mix_spec", {}),
        "_spine_blocks": [_spine_block_entry(b) for b in structure],
        "subtitle_overlay": subtitle_overlay_data.get(
            "subtitle_overlay", {}),
        "motion_graphics_overlay": motion_graphics_overlay_data.get(
            "motion_graphics_overlay", {}),
        # Timed text moments the brand template declared, already
        # rendered by 4.06. Carried here so the segments are inside
        # manifest validation and the renderer can place them on V6 -
        # the slot spent four months with no reader at all, which is
        # what library/tools/timed_text_overlay.py is about.
        "timed_text_overlay": motion_graphics_overlay_data.get(
            "timed_text_overlay", {}),
        # The TV-frame look this reel renders under (or None): the
        # asset, the punch-in factor, the power timings and where the
        # declaration came from.  The V1 punch, the V2 frame clips and
        # the per-clip power keys above are this record made picture.
        "tv_frame": tv_look,
    }

    # ── Captain-placed cards (external/placed_assets.json) ──
    # A card the captain laid by hand is a decision about THIS video, so
    # it is declared per project and carried here - after assembly, so
    # the card rides the same V1 clip shape a declared bookend rides,
    # and BEFORE validation, so every gate judges it (coverage,
    # no-B-roll-over-cards, render QA). A head card shifts every
    # timeline-anchored position later (`shift_all_timelines`); the A1
    # chorus is re-derived from V1 beside it, for the same reason it
    # was derived the first time. No declaration and this is the
    # manifest it always built, byte for byte.
    try:
        from library.tools import placed_assets as _placed
        _declared_cards = _placed.load_assets(_project_root)
    except _placed.PlacedAssetError as exc:
        raise ValueError(
            f"placed_assets cannot be honoured: {exc}. A declared card "
            f"the compile cannot read must refuse, never compile past "
            f"it.") from exc
    except (KeyError, ValueError):
        # No external-inputs area the layout knows: no declaration, and
        # the manifest below is the one this always built.
        _declared_cards = []
    if _declared_cards:
        _carry = _placed.carry_into_manifest(
            manifest, _declared_cards, fps=fps)
        for row in _carry["carried"]:
            print(f"  placed {row['slot']} card {row['label']!r} "
                  f"{row['span'][0]:.3f}-{row['span'][1]:.3f}s - "
                  f"{row['reason']}", file=sys.stderr)
        if _carry["refused"]:
            raise ValueError(
                "placed_assets refused:\n  - " + "\n  - ".join(
                    f"{row.get('label', '?')}: {row.get('reason', '')}"
                    for row in _carry["refused"]))
        manifest["tracks"]["A1"]["clips"] = sorted(
            (c for c in manifest["tracks"]["V1"]["clips"]
             if not c.get("video_only")),
            key=lambda c: c["timeline_in"])

    _apply_manifest_qa_checks(manifest)

    # No frame of the finished video may be black for want of a clip.
    _assert_timeline_fully_covered(manifest)

    # Beat-snapped cuts and SFX are placed using beat times measured in the
    # MUSIC file's clock, mapped to timeline time by subtracting the
    # chosen section's offset. The bed has to be placed at that same
    # offset or every snap moves by the difference - so say it out loud
    # rather than leave it implicit.
    assert_music_offset_is_the_chosen_section(manifest, ms, spine)

    # The captions in the manifest and the captions on screen must agree.
    _assert_subtitle_overlay_matches_plan(manifest)

    # Everything the planners produced must survive into the manifest.
    # Nine B-roll assignments becoming one invisible clip, and five SFX
    # becoming an empty list, both passed every check the pipeline had.
    _assert_planner_output_preserved(
        manifest,
        broll_planned=len(broll_assignments) + len(broll_interjections),
        sfx_planned=len(sfx_list),
        vfx_planned=len(vfx),
        broll_dropped_by_overlap=broll_dropped_by_overlap,
        vfx_collisions=vfx_collisions,
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
    gen_count = len(manifest.get("generator_overlays", []))
    if gen_count:
        print(f"  Generator overlays: {gen_count} on overlay track", file=sys.stderr)
    print(f"  Duration:    {total_duration:.1f}s", file=sys.stderr)

    A1_clips = manifest["tracks"]["A1"]["clips"]
    V1_clips = [c for c in manifest["tracks"]["V1"]["clips"]
                if not c.get("video_only")]
    assert len(A1_clips) == len(V1_clips), f"A1/V1 parity failed: A1={len(A1_clips)} V1={len(V1_clips)}"

    assert_overlay_segments_on_disk(manifest)

    errors = validate_manifest(manifest)
    if errors:
        raise ValueError(f"Manifest validation failed with {len(errors)} errors:\n" + "\n".join(errors))

    return manifest


def main():
    if len(sys.argv) >= 2:
        # Standalone mode: read from filesystem directory
        out_dir = sys.argv[1]
        manifest = compile_manifest(out_dir)

        out_path = str(ProjectLayout(
            os.path.dirname(os.path.abspath(out_dir))
        ).write_path(Area.ASSEMBLY_MANIFEST, "assembly_manifest.json",
                     step="compile_manifest"))
        with open(out_path, "w", encoding="utf-8") as f:
            # Canonical spelling (library/tools/stable_json.py): the
            # manifest is the document a variant merge reads, so the
            # same plan is the same bytes. Stdout below keeps its old
            # spelling - it is piped, never merged.
            from library.tools.stable_json import dump_stable
            dump_stable(manifest, f)
        print(f"\nWrote: {out_path}", file=sys.stderr)

        json.dump(manifest, sys.stdout, indent=2)
    else:
        # Orchestrator mode: read from stdin JSON
        inputs = json.loads(sys.stdin.read())

        if "project_folder" in inputs:
            out_dir = str(
                ProjectLayout(inputs["project_folder"]).write_dir(Area.OUTPUT_ROOT))
            if os.path.isdir(out_dir):
                manifest = compile_manifest(out_dir)
                json.dump({"assembly_manifest": manifest}, sys.stdout, indent=2)
                return
        
        raise RuntimeError("compile_manifest requires project_folder in inputs and a valid pipeline_output dir")

if __name__ == "__main__":
    main()
