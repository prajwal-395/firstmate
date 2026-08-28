#!/usr/bin/env python3
"""
Step 5.03: Creative Cohesion Review
Reviews creative decisions across plans for cohesion and consistency.
"""
import json
import sys

from library.tools.energy_reading import read_energy
from library.tools.passage_engagement import (
    NO_ENGAGEMENT_BASIS,
    engagement_of,
    engagement_rank,
    unjudged_summary,
)
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

    One reader: library/tools/passage_engagement.py. Kept as a name here
    because this step is where the gate lives.
    """
    return engagement_of(passage)


# Sub-millisecond slop is float noise, not a shared moment.
_SOURCE_EPSILON = 1e-3


def _is_the_hooks_moment(hook, passage) -> bool:
    """Is this body passage the moment the hook was cut from?

    The hook is meant to tease a body passage - `_drop_hook_duplicates`
    in step 2.02 allows exactly that and only forbids the hook BEING the
    passage. So a hook teasing the strongest line is the technique
    working, and reporting it as a buried peak would be a finding that
    fires on a correct edit (AGENTS.md 10.4).

    Same clip, overlapping source audio. Anything the passages do not
    carry answers no, which leaves the finding to fire - an unknown is
    not an exemption.
    """
    if not isinstance(hook, dict) or not isinstance(passage, dict):
        return False
    if not hook.get("clip_id") or hook.get("clip_id") != passage.get("clip_id"):
        return False
    try:
        h_start, h_end = float(hook["source_start"]), float(hook["source_end"])
        p_start, p_end = float(passage["source_start"]), float(passage["source_end"])
    except (KeyError, TypeError, ValueError):
        return False
    return h_start < p_end - _SOURCE_EPSILON and h_end > p_start + _SOURCE_EPSILON


def _passage_label(passage, index) -> str:
    """Where in the edit the passage sits, in terms a reader can find it.

    The timeline position is what #246 is about - the strongest content
    landing at 48s - so name the seconds when the passage carries them.
    """
    clip_id = passage.get("clip_id") if isinstance(passage, dict) else None
    where = f"body passage {index + 1}"
    if clip_id:
        where += f" ({clip_id}"
        try:
            where += f" at {float(passage['source_start']):.3f}s)"
        except (KeyError, TypeError, ValueError):
            where += ")"
    return where


def _composite_note(top_passage, hook) -> str:
    """The two composites, reported beside the finding and never firing it.

    A number the model wrote is worth showing a reader; measured spread
    is why it is not the thing compared.
    """
    top, hook_score = engagement_of(top_passage), engagement_of(hook)
    if top is None or hook_score is None:
        return ""
    return f" (composite {top:g} against the hook's {hook_score:g})"


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

    # Engagement check
    #
    # This compares judgements that are THERE. It used to coerce both
    # sides to 0 when they were absent, which is how it read a passage
    # that was never measured as one that scored nothing - and since the
    # withdrawn `engagement_scorer` gave nine of project 001's eleven
    # passages an identical 49, the one finding this step reported was
    # arithmetic on a constant.
    #
    # Step 2.02 now asks the model that already reads every passage to
    # place them in ONE ordering, so there is a real signal to compare.
    # The finding is derived from the ORDERING and not from the number,
    # and only from the TOP of it: measured across three answers to the
    # identical prompt at one revision, the two strongest passages came
    # back in the same order every time, while ranks in the middle moved
    # by two places and the composite on those same passages moved by up
    # to twenty points (MEASURED_SPREAD in
    # library/tools/passage_engagement.py). So "which passage is
    # strongest" is a signal and "is passage five better than seven" is
    # not. The composite is REPORTED beside the finding for magnitude and
    # is never what fires it.
    #
    # A hook that teases the top-ranked passage is the shortform
    # technique working, not a defect, so the finding fires only when the
    # strongest passage is a DIFFERENT moment from the hook - which is
    # the case #246 describes, the piece's own emotional floor landing at
    # 48.065s of a 59.437s cut with nothing able to weigh it.
    if isinstance(speech_sequence, dict):
        hook = speech_sequence.get("hook_segment") or {}
        body = speech_sequence.get("body_sequence") or []
        if not isinstance(body, list):
            body = []

        # State what was NOT judged, on the run that carries it - the
        # same rule `view:prosody` follows. A passage the model declined
        # to judge is a stated absence, never a low score.
        not_judged = unjudged_summary([hook] + list(body))
        if not_judged:
            warnings.append(not_judged)

        hook_rank = engagement_rank(hook)
        ranked_body = [(engagement_rank(b), i, b)
                       for i, b in enumerate(body)
                       if engagement_rank(b) is not None]

        if hook_rank is None or not ranked_body:
            # Stated, not silent, and it costs no score: an absent
            # judgement is not a defect in the edit.
            warnings.append(
                "Engagement not compared: " + NO_ENGAGEMENT_BASIS)
        else:
            # Keyed, not a bare tuple compare: the handoff asks for no
            # ties and a model can still write one, and comparing the
            # passage dicts that follow would raise rather than choose.
            top_rank, top_index, top_passage = min(
                ranked_body, key=lambda entry: (entry[0], entry[1]))
            if top_rank < hook_rank and not _is_the_hooks_moment(hook, top_passage):
                where = _passage_label(top_passage, top_index)
                warnings.append(
                    f"Hook ranks {hook_rank} of the sequence; the passage "
                    f"the model ranked strongest ({top_rank}) is "
                    f"{where}{_composite_note(top_passage, hook)}")
                score -= 10
                adjustments.append({
                    "target_step": "speech_sequence",
                    "field": "segment_order",
                    "current_value": "current",
                    "suggested_value": "front_loaded",
                    "reason": (
                        f"The strongest passage by the model's own ranking "
                        f"is {where}, not the hook"),
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
