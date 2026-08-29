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
"""
import json
import os
import sys
import logging

logger = logging.getLogger(__name__)

# Instantaneous transitions - a cut has no duration by definition.
# The list itself lives in tools.transition_vocabulary; this module reads
# it through is_cut() so there is one place a type can be classified.

# Tracks whose clips must lie end-to-end. A3 is excluded: SFX are allowed
# to overlap and the timeline builder allocates extra audio tracks for them.
SINGLE_LANE_TRACKS = ("A3",)

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
from tools.beat_grid import assert_music_starts_at_timeline_zero
from tools.bookends import block_bookend
from library.tools import cohesion_scope
from library.tools.music_behavior import resolve_music_behavior
from library.tools.transition_carriers import block_reaches_v1
from tools.spine_contract import (
    MAX_DECLARED_BLACK_BEAT_SECONDS,
    is_speech_block,
)
from tools.semantic_index import build_semantic_lookup
from tools.subject_framing import (
    SUBJECT_HEADROOM, subject_box, subject_center_x, subject_centers_by_clip,
)
from tools.transition_vocabulary import canonical_type, is_cut, withdrawal_reason
from tools.vision_schema_adapter import camera_prose, stability_summary
from tools.brand_registry import project_template_name, resolve_project_template
from tools.framing_intent import DEFAULT_FRAMING_INTENT, resolve_framing_intent
from tools.delivery_format import resolve_delivery_format
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
    record = {"applied": [], "not_applied": [], "observed": []}
    if not cohesion_review:
        return record
    observed = cohesion_review.get("observations")
    if isinstance(observed, list):
        record["observed"] = list(observed)
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
        if is_cut(t.get('transition_type')):
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
    captions must have an overlay segment, and that segment must span the
    captions in it.
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

    segment_by_block = {
        seg.get("block_position"): seg for seg in segments
        if seg.get("block_position") is not None
    }

    problems = []
    for position, (lo, hi) in sorted(by_block.items(), key=lambda kv: str(kv[0])):
        seg = segment_by_block.get(position)
        if seg is None:
            problems.append(
                f"block {position}: {lo:.3f}-{hi:.3f}s has captions but no "
                f"rendered overlay segment"
            )
            continue
        seg_start = seg.get("timeline_start", 0)
        seg_end = seg.get("timeline_end", 0)
        tolerance = COVERAGE_TOLERANCE_FRAMES / max(
            manifest.get("project", {}).get("frame_rate", 30.0), 1.0)
        if seg_start > lo + tolerance or seg_end < hi - tolerance:
            problems.append(
                f"block {position}: captions span {lo:.3f}-{hi:.3f}s but the "
                f"overlay segment only covers {seg_start:.3f}-{seg_end:.3f}s"
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


def _conform_fields(clip_metadata: dict, clip_id, proj_res,
                    framing_intent: float = None,
                    framing_pan_x: float = None,
                    subject_center_x: float = None,
                    subject_width: float = None) -> dict:
    """Scale factor needed to fill the output frame, if any.

    ``framing_intent`` is a normalised scalar spanning the continuum from
    full letterbox (0.0) through partial punch-in to complete fill (1.0).
    *None* means nothing declared one, which resolves to
    ``framing_intent.DEFAULT_FRAMING_INTENT`` - see that module for why
    the default is fill and what the inverted heuristic it replaced did to
    project 001.  Callers that resolve the intent themselves (compile_manifest
    does, through ``framing_intent.resolve_framing_intent``) always pass a
    number.

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
    resolved_intent = (DEFAULT_FRAMING_INTENT if framing_intent is None
                       else max(0.0, min(1.0, framing_intent)))

    if fill_scale <= fit_scale * (1 + 1e-6):
        # Source already matches the target aspect ratio - no conform needed
        # regardless of framing_intent (there are no bars to remove).
        return {"needs_conform": False, "framing_intent": resolved_intent}

    max_zoom = round(fill_scale / fit_scale, 4)

    # ── Apply framing_intent ──
    # There is no second branch.  The one this replaced ran whenever
    # nothing declared an intent and letterboxed any clip whose primary
    # subject was visible - which on a talking head is every clip, so
    # project 001 shipped its A-roll in a 608-row strip of a 1920-row
    # frame.  See library/tools/framing_intent.py.
    intent = resolved_intent
    if intent == 0.0:
        return {"needs_conform": False, "framing_intent": intent}
    zoom = round(1.0 + (max_zoom - 1.0) * intent, 4)
    result = {
        "needs_conform": True,
        "framing_intent": intent,
        "source_width": width,
        "source_height": height,
        "fill_zoom": zoom,
    }

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
    # THE RENDER TARGET. It comes from the product - the brand template,
    # with a per-project override - and never from the footage. The
    # catalog's `source_resolution` describes the source and is used for
    # conform decisions only. Captain's ruling, 2026-08-19; see
    # library/tools/delivery_format.py for what reading the source here
    # cost.
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
    _template = resolve_project_template(project_template_name(_project_root))

    def _resolve_framing(block_or_clip):
        """Return (framing_intent, framing_pan_x) for a clip."""
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
        return fi, pan

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
    temporal_data = (load(out_dir, "temporal_index.json")
                     or load(out_dir, "step_1_04.json") or {})
    _subject_faces = subject_centers_by_clip(temporal_data)

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
            _fi, _fp = _resolve_framing(block)

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
                    clip.update(_conform_fields(clip_metadata, get_clip_id(seg), proj_res,
                        framing_intent=_fi, framing_pan_x=_fp,
                        **_subject_framing(
                            get_clip_id(seg), clip.get("source_in", 0.0),
                            clip.get("source_out", 0.0))))
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
                clip.update(_conform_fields(clip_metadata, get_clip_id(assignment), proj_res,
                    framing_intent=_fi, framing_pan_x=_fp,
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
                if clip["timeline_in_frame"] is None:
                    convert_clip_to_frames(clip, fps)
                clip.update(_conform_fields(clip_metadata, get_clip_id(block), proj_res,
                    framing_intent=_fi, framing_pan_x=_fp,
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
        _fi, _fp = _resolve_framing(broll)
        v2_clip.update(_conform_fields(clip_metadata, get_clip_id(broll), proj_res,
            framing_intent=_fi, framing_pan_x=_fp,
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
        _fi, _fp = _resolve_framing(interj)
        v2_clip.update(_conform_fields(clip_metadata, get_clip_id(assigned), proj_res,
            framing_intent=_fi, framing_pan_x=_fp,
            **_subject_framing(
                get_clip_id(assigned), v2_clip.get("source_in", 0.0),
                v2_clip.get("source_out", 0.0))))
        v2_clips.append(v2_clip)
        v2_kinds[v2_clip["label"]] = "interjection"
        compute_neural_directives(get_clip_id(assigned), v2_clip)

    broll_dropped_by_overlap = _resolve_v2_overlaps(v2_clips, fps, v2_kinds)
    _assert_nothing_covers_a_bookend(v1_clips, v2_clips)

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
        vol_db = sfx_entry.get("volume_db", -14)

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
                "label": sfx_entry.get("label", f"sfx_{si+1:03d}"),
                "sfx_id": sfx_id or os.path.basename(source_file),
            })
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
        label = _v1_label_at(v1_clips, v["timeline_start"])
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

        fusion_transitions.append({
            "type": comp_type,
            "after_clip": after_clip,
            "duration_frames": t.get("duration_frames", int(0.5 * fps)),
        })

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
    }

    _apply_manifest_qa_checks(manifest)

    # No frame of the finished video may be black for want of a clip.
    _assert_timeline_fully_covered(manifest)

    # Beat-snapped cuts and SFX are placed using beat times measured in the
    # MUSIC file's clock. That is only the timeline's clock while music
    # starts at source_in 0 / timeline_in 0, which is how it is placed
    # above - so say it out loud rather than leave it implicit.
    assert_music_starts_at_timeline_zero(manifest)

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

    sub_overlay = manifest.get("subtitle_overlay", {})
    for seg in sub_overlay.get("segments", []):
        if "overlay_path" in seg and not os.path.exists(seg["overlay_path"]):
            logger.warning(f"Subtitle overlay missing on disk: {seg['overlay_path']}")
            
    mg_overlay = manifest.get("motion_graphics_overlay", {})
    for seg in mg_overlay.get("segments", []):
        if "overlay_path" in seg and not os.path.exists(seg["overlay_path"]):
            logger.warning(f"MG overlay missing on disk: {seg['overlay_path']}")

    timed_text = manifest.get("timed_text_overlay", {})
    for seg in timed_text.get("segments", []):
        if "overlay_path" in seg and not os.path.exists(seg["overlay_path"]):
            raise ValueError(
                f"timed text overlay declared but missing on disk: "
                f"{seg['overlay_path']}. Nothing downstream notices a "
                f"missing overlay - the picture underneath is intact - so "
                f"this is the only gate that can.")

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
        with open(out_path, "w") as f:
            json.dump(manifest, f, indent=2)
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
