#!/usr/bin/env python3
"""
Step 5.03: Creative Cohesion Review
Reviews creative decisions across plans for cohesion and consistency.
"""
import json
import sys

from library.tools import cohesion_scope
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

    speech_sequence = inputs.get("speech_sequence", {})

    warnings = []
    # Every finding this step makes, before it is routed. A proposal is
    # either something `compile_manifest` really applies or something a
    # step upstream owns, and `cohesion_scope.split` is what decides
    # which - see the module docstring for why the two cannot be one
    # list.
    proposals = []

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
    #
    # The one finding this step can act on where it runs: a duration is a
    # number the compiler rewrites, and nothing that is timed off the
    # timeline moves when it does.
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
                finding = f"High energy but found slow transition ({dur_ms}ms)"
                warnings.append(finding)
                proposals.append({
                    "target_step": "transition_spec",
                    "field": "duration_frames",
                    "current_value": dur_frames,
                    "suggested_value": 10, # ~333ms
                    "finding": finding,
                    "reason": "High energy requires faster transitions (<500ms)",
                    "target_index": transitions.index(t)
                })
        elif energy == "calm":
            # The soft transitions a calm edit should hold on. Named from
            # the one vocabulary rather than a list of types that no
            # longer exist - "cross_dissolve" here matched nothing the
            # planner could emit.
            if dur_ms <= 1000 and canonical_type(ttype) in SOFT_TRANSITIONS:
                finding = f"Calm energy but found fast dissolve ({dur_ms}ms)"
                warnings.append(finding)
                proposals.append({
                    "target_step": "transition_spec",
                    "field": "duration_frames",
                    "current_value": dur_frames,
                    "suggested_value": 30, # 1000ms
                    "finding": finding,
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
        finding = f"High energy but sparse SFX ({sfx_density_per_min:.1f} per min)"
        warnings.append(finding)
        proposals.append({
            "target_step": "sfx_spec",
            "field": "density",
            "current_value": "sparse",
            "suggested_value": "dense",
            "finding": finding,
            "reason": "High energy requires dense SFX"
        })
    elif energy == "calm" and total_duration and sfx_density_per_min > 15:
        finding = f"Calm energy but dense SFX ({sfx_density_per_min:.1f} per min)"
        warnings.append(finding)
        proposals.append({
            "target_step": "sfx_spec",
            "field": "density",
            "current_value": "dense",
            "suggested_value": "sparse",
            "finding": finding,
            "reason": "Calm energy requires sparse SFX"
        })

    # The colour grade check used to sit here, and it is REMOVED for the
    # same three reasons the pacing check was (#238, and P4.2 in
    # docs/PIPELINE_PLAN.md):
    #
    #   1. It read `color_grade_spec["mood"]` and `["grade_name"]`.
    #      Step 5.01 emits neither. Its real spec carries
    #      `grade_pipeline`, `per_clip_adjustments`, `fusion_look`,
    #      `house_look`, `house_look_title`, `look_notes`, `withdrawn`,
    #      `output_color_space` and `consistency_notes` - measured on
    #      project 001's own 2026-08-26 output - so the substring match
    #      ran against "" on every real run and the check never fired.
    #      The tests that covered it supplied `mood` themselves.
    #   2. The values that ARE there are numbers, not moods:
    #      `library/tools/house_look.py` gives every look a `saturation`
    #      and a `contrast`. Turning either into "soft" or "punchy"
    #      against an energy word means choosing a threshold, and a
    #      threshold nobody measured is a creative value this file would
    #      be inventing (AGENTS.md 10.5).
    #   3. It emitted no adjustment even when it fired, so there was
    #      never a route from the finding to the picture.
    #
    # The grade IS still changeable at 5.03 - it is a CDL and a set of
    # Fusion values, and moves no frame. A check on it is welcome; it
    # needs a declared target to judge against, which no step emits yet.

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
    #
    # It is an OBSERVATION, not an adjustment. Re-ordering the narrative
    # is step 2.02's decision and eleven steps of timing rest on it; see
    # library/tools/cohesion_scope.py.
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
            # Stated, not silent: an absent judgement is not a defect in
            # the edit, and it produces no finding of any kind.
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
                finding = (
                    f"Hook ranks {hook_rank} of the sequence; the passage "
                    f"the model ranked strongest ({top_rank}) is "
                    f"{where}{_composite_note(top_passage, hook)}")
                warnings.append(finding)
                proposals.append({
                    "target_step": "speech_sequence",
                    "field": "segment_order",
                    "finding": finding,
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
    elif timeline_duration < min_dur:
        warnings.append(
            f"Duration warning: actual duration ({timeline_duration:.1f}s) "
            f"is below the minimum target zone ({min_dur:.1f}s)"
        )

    # 4. Route every finding.
    #
    # A finding is an ADJUSTMENT only where the manifest compiler has a
    # branch that applies it; everything else is an OBSERVATION naming the
    # step that owns the decision. `split` raises on a pair declared in
    # neither list, so a new finding cannot quietly become an adjustment
    # that is then quietly dropped.
    adjustments, observations = cohesion_scope.split(proposals)

    # `cohesion_score` used to be reported here. It is REMOVED (#238), and
    # not because it was hard to compute:
    #
    #   1. It was 100 minus a hand-picked weight per finding - 5 for a
    #      transition, 5 for SFX density, 5 for the grade, 10 for the
    #      hook, 3 for the duration. Nothing measured those weights and
    #      nothing could; the 90 on project 001's 2026-08-26 run was
    #      100 minus one 10.
    #   2. Nothing outside this step read it. Not compile_manifest, not
    #      render_qa, not the dashboard, not the step exporter.
    #   3. Its one internal reader, `if score < 70`, gated a block that
    #      mutated `transitions` in place and appended sentences to an
    #      `applied_adjustments` list - and this step emits only
    #      `cohesion_review`, so neither the mutation nor the list ever
    #      left the process. It reported "applied" about nothing.
    #
    # A number nobody acts on, made of weights nobody measured, reads as
    # a verdict on the edit (AGENTS.md 10.4). What this step really has
    # is a list of findings; that is what it reports.
    cohesion_review = {
        "timeline_duration_seconds": round(timeline_duration, 2),
        "warnings": warnings,
        # Every entry here is one `apply_cohesion_adjustments` applies.
        "adjustments": adjustments,
        # Findings a step upstream owns: stated, with who owns them and
        # what re-run would act on them. Never presented as changes.
        "observations": observations,
        # The authoritative record of what happened to each adjustment is
        # assembly_manifest.cohesion_adjustments, written by the applier.
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
