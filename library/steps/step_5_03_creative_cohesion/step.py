#!/usr/bin/env python3
"""
Step 5.03: Creative Cohesion Review
Reviews creative decisions across plans for cohesion and consistency.
"""
import json
import sys

from library.tools.energy_reading import read_energy
from library.tools.timeline_duration import measure_timeline_duration
from library.tools.transition_vocabulary import canonical_type, is_cut

# Transitions that read as slow and soft, so a calm edit wants them long.
SOFT_TRANSITIONS = ("fade_to_black", "defocus")


def map_energy(energy_str):
    """Read the energy the way every other reader does.

    This used to carry its own word list, which matched "building" to
    "high" while `transition_selector` did not - and this is the reader
    that ACTS, so project 001's deliberate "building" had three defocus
    transitions cut from 500 ms to 333 ms and a demand for denser SFX.
    One vocabulary now: library/tools/energy_reading.py.
    """
    return read_energy(energy_str)

def engagement_score(passage) -> float:
    """The composite engagement of one passage, or None if it has none.

    `speech_sequence` writes `engagement` as the dict
    `engagement_scorer.compute_engagement` returns, and the gate below
    used to accept only an int or a float - so it read every real passage
    as having no score at all. A bare number is still accepted: nothing
    emits one today, but rejecting it would be the same mistake in the
    other direction.
    """
    if not isinstance(passage, dict):
        return None
    value = passage.get("engagement")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        composite = value.get("composite")
        if isinstance(composite, (int, float)) and not isinstance(composite, bool):
            return float(composite)
    return None


def review_creative_cohesion(inputs: dict) -> dict:
    creative_direction = inputs.get("creative_direction", {})
    transition_spec_raw = inputs.get("transition_spec", [])
    if isinstance(transition_spec_raw, dict):
        transitions = transition_spec_raw.get("transitions", transition_spec_raw.get("transition_spec", []))
    else:
        transitions = transition_spec_raw if isinstance(transition_spec_raw, list) else []

    sfx_spec_raw = inputs.get("sfx_spec", [])
    if isinstance(sfx_spec_raw, dict):
        sfx = sfx_spec_raw.get("sfx_list", sfx_spec_raw.get("sfx_plan", sfx_spec_raw.get("sfx_events", sfx_spec_raw.get("sfx_spec", []))))
    else:
        sfx = sfx_spec_raw if isinstance(sfx_spec_raw, list) else []

    color_grade_spec = inputs.get("color_grade_spec", {})
    speech_sequence = inputs.get("speech_sequence", {})
    project_config = inputs.get("project_config", {})

    warnings = []
    adjustments = []
    score = 100

    # 1. Energy Alignment Check
    #
    # The energy comes from the creative direction or the checks below do
    # not run. This used to read `.get("target_energy", "moderate")`, so a
    # direction that declared no energy was scored against one this file
    # made up - and the transition adjustments below are APPLIED by
    # compile_manifest, so an invented word reached the picture.
    raw_energy = creative_direction.get("target_energy")
    energy = map_energy(raw_energy) if raw_energy else None
    if not energy:
        warnings.append(
            "Energy not checked: the creative direction declares no "
            "target_energy, so there is nothing to judge the transitions "
            "and SFX density against"
        )

    # Transition Check
    for t in transitions if energy else []:
        # A transition that declares no duration is not measured. Assuming
        # 15 frames invented the very number this check then judged.
        dur_frames = t.get("duration_frames")
        if not isinstance(dur_frames, (int, float)) or isinstance(dur_frames, bool):
            warnings.append(
                f"Transition at {t.get('cut_point_timeline', '?')}s declares "
                f"no duration_frames, so its pace was not checked"
            )
            continue
        # assuming 30fps -> ms = (frames / 30) * 1000
        dur_ms = (dur_frames / 30.0) * 1000
        ttype = t.get("transition_type", t.get("type", ""))

        if energy == "high":
            if dur_ms >= 500 and not is_cut(ttype):
                warnings.append(f"High energy but found slow transition ({dur_ms}ms)")
                score -= 5
                adjustments.append({
                    "target_step": "transition_spec",
                    "field": "duration_frames",
                    "current_value": dur_frames,
                    "suggested_value": 10, # ~333ms
                    "reason": "High energy requires faster transitions (<500ms)",
                    "target_index": transitions.index(t)
                })
        elif energy == "calm":
            # The soft transitions a calm edit should hold on. Named from
            # the one vocabulary rather than a list of types that no
            # longer exist - "cross_dissolve" here matched nothing the
            # planner could emit.
            if dur_ms <= 1000 and canonical_type(ttype) in SOFT_TRANSITIONS:
                warnings.append(f"Calm energy but found fast dissolve ({dur_ms}ms)")
                score -= 5
                adjustments.append({
                    "target_step": "transition_spec",
                    "field": "duration_frames",
                    "current_value": dur_frames,
                    "suggested_value": 30, # 1000ms
                    "reason": "Calm energy should have slower dissolves (>=1000ms)",
                    "target_index": transitions.index(t)
                })

    # The timeline's real length, measured the way review_rough_cut
    # measures it: the last spine block's timeline_end. See
    # library/tools/timeline_duration.py for what this replaced.
    timeline_duration = measure_timeline_duration(inputs)

    # SFX Check (density estimation)
    #
    # Measured against the REAL timeline or not at all. `timeline_duration
    # or 60.0` made a minute up when nothing had been measured, and
    # `measure_timeline_duration` returns 0.0 to mean "no evidence"
    # precisely so a reader says so instead (AGENTS.md 10.1).
    total_duration = timeline_duration
    if energy and not total_duration:
        warnings.append(
            "SFX density not checked: the timeline length is unknown, so "
            "there is nothing to measure a density against"
        )

    sfx_density_per_min = (len(sfx) / total_duration) * 60 if total_duration > 0 else 0
    if energy == "high" and total_duration and sfx_density_per_min < 10:
        warnings.append(f"High energy but sparse SFX ({sfx_density_per_min:.1f} per min)")
        score -= 5
        adjustments.append({
            "target_step": "sfx_spec",
            "field": "density",
            "current_value": "sparse",
            "suggested_value": "dense",
            "reason": "High energy requires dense SFX"
        })
    elif energy == "calm" and total_duration and sfx_density_per_min > 15:
        warnings.append(f"Calm energy but dense SFX ({sfx_density_per_min:.1f} per min)")
        score -= 5
        adjustments.append({
            "target_step": "sfx_spec",
            "field": "density",
            "current_value": "dense",
            "suggested_value": "sparse",
            "reason": "Calm energy requires sparse SFX"
        })

    # Color Grade Check
    color_mood = str(color_grade_spec.get("mood", color_grade_spec.get("grade_name", ""))).lower()
    if energy == "high" and any(w in color_mood for w in ["soft", "muted", "faded", "calm"]):
        warnings.append("High energy but color grade is soft/muted")
        score -= 5
    elif energy == "calm" and any(w in color_mood for w in ["high contrast", "vibrant", "punchy", "saturated"]):
        warnings.append("Calm energy but color grade is highly saturated/contrasty")
        score -= 5

    # The pacing consistency check used to sit here. It is REMOVED, not
    # disabled - see docs/PIPELINE_PLAN.md P4.2. Three reasons, any one of
    # which is sufficient:
    #
    #   1. It read `creative_direction["pacing"]["cuts_per_minute"]`, a
    #      singular key no producer has ever emitted, so the target was
    #      always None and the check never ran. The tests that covered it
    #      supplied the key themselves.
    #   2. The brand templates' pacing blocks - the thing a target would
    #      have come from - had no reader anywhere in the repository, and
    #      spelled themselves two different ways across four files.
    #   3. Even had it run, it emitted no adjustment, and it runs at 5.03,
    #      after the spine, the speech sequence and the transitions have
    #      fixed the cut. Changing pacing means re-cutting, which is a
    #      re-plan; `apply_cohesion_adjustments` refuses those by design
    #      and the DAG has no edge back to the planning steps.
    #
    # A score nobody can act on reads as coverage. If pacing control is
    # wanted it is a re-cut loop, and that is a design job.

    # Engagement score check
    #
    # `engagement` is the DICT `engagement_scorer.compute_engagement`
    # emits - {hook, flow, value, composite, rationale} - and this gate
    # tested `isinstance(..., (int, float))` on it, so `hook_eng` was
    # permanently 0 and `eng_values` permanently empty: it could not fire
    # on any real speech_sequence. The composite is the score the scorer
    # exists to produce, so that is what is compared.
    if isinstance(speech_sequence, dict):
        hook = speech_sequence.get("hook_segment") or {}
        body = speech_sequence.get("body_sequence", [])

        hook_eng = engagement_score(hook)
        eng_values = [engagement_score(b) for b in body
                      if engagement_score(b) is not None]
        max_body_eng = max(eng_values) if eng_values else 0
        hook_eng = hook_eng if hook_eng is not None else 0

        if max_body_eng > hook_eng + 10:
            warnings.append(
                f"Hook engagement ({hook_eng:g}) is lower than peak body "
                f"engagement ({max_body_eng:g})")
            score -= 10
            adjustments.append({
                "target_step": "speech_sequence",
                "field": "segment_order",
                "current_value": "current",
                "suggested_value": "front_loaded",
                "reason": "Highest engagement segments should be front-loaded for hooks"
            })

    # 3. Duration Warning
    # Check the TIMELINE against the target duration zone. This used to
    # read `body_sequence[-1]["end_time"]`, a source timestamp, and so
    # warned about a length the video never had.
    from library.tools.duration_targets import get_target_duration_zone
    min_dur, target_dur, max_dur = get_target_duration_zone(inputs)

    if not timeline_duration:
        # No spine and no A-roll reached this step, so there is nothing
        # to measure. Saying so beats warning about 0.0 seconds.
        warnings.append(
            "Duration not checked: neither audio_spine nor "
            "a_roll_assignments reached creative_cohesion, so the "
            "timeline length is unknown"
        )
    elif timeline_duration > max_dur:
        warnings.append(
            f"Duration warning: actual duration ({timeline_duration:.1f}s) "
            f"exceeds the maximum target zone ({max_dur:.1f}s)"
        )
        score -= 3
    elif timeline_duration < min_dur:
        warnings.append(
            f"Duration warning: actual duration ({timeline_duration:.1f}s) "
            f"is below the minimum target zone ({min_dur:.1f}s)"
        )
        score -= 3

    # 4. Output Adjustments
    score = max(0, score)

    # 5. Apply Adjustments (optional)
    applied_adjustments = []
    if score < 70:
        for adj in adjustments:
            if adj["target_step"] == "transition_spec" and "target_index" in adj:
                idx = adj["target_index"]
                transitions[idx][adj["field"]] = adj["suggested_value"]
                applied_adjustments.append(f"Adjusted transition {idx} {adj['field']} to {adj['suggested_value']}")
            elif adj["target_step"] == "sfx_spec" and adj["field"] == "density":
                # We can't really generate new SFX here, but we can set a flag
                applied_adjustments.append("Flagged SFX density adjustment needed")
            elif adj["target_step"] == "speech_sequence" and adj["field"] == "segment_order":
                applied_adjustments.append("Flagged speech sequence for reordering (too destructive to auto-apply)")

    # Construct review output
    cohesion_review = {
        "cohesion_score": score,
        "timeline_duration_seconds": round(timeline_duration, 2),
        "warnings": warnings,
        "adjustments": adjustments,
        "applied_adjustments": applied_adjustments,
        # This step's edits above are a local preview: it emits only
        # cohesion_review, so nothing it changes reaches pipeline state.
        # `applied_adjustments` therefore said nothing was applied while
        # compile_manifest was applying three things. The authoritative
        # record of what happened to each adjustment is
        # assembly_manifest.cohesion_adjustments.
        "applied_by": "step_5_04_compile_manifest",
    }

    return cohesion_review

def main():
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")

    review = review_creative_cohesion(data)

    json.dump({
        "step": "5.03_creative_cohesion",
        "cohesion_review": review
    }, sys.stdout, indent=2)

if __name__ == "__main__":
    main()
