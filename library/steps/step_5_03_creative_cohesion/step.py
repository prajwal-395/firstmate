#!/usr/bin/env python3
"""
Step 5.03: Creative Cohesion Review
Reviews creative decisions across plans for cohesion and consistency.
"""
import json
import sys

from library.tools import cohesion_scope
from library.tools.creative_direction import direction_value
from library.tools.passage_engagement import (
    NO_ENGAGEMENT_BASIS,
    engagement_of,
    engagement_rank,
    unjudged_summary,
)
from library.tools.timeline_duration import measure_timeline_duration
from library.tools.transition_vocabulary import is_cut

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

    # The ENERGY ALIGNMENT checks used to sit here, and they are REMOVED.
    #
    # There were four, and each was a creative threshold this file chose:
    #
    #   1. a "high" energy edit must hold every drawn transition under
    #      500 ms, and the fix was `duration_frames: 10`;
    #   2. a "calm" edit must hold a soft transition for at least
    #      1000 ms, and the fix was `duration_frames: 30`;
    #   3. a "high" edit must carry at least 10 SFX per minute;
    #   4. a "calm" edit must carry at most 15 SFX per minute.
    #
    # Three reasons they are gone rather than rewritten:
    #
    #   * 3 and 4 are CREATIVE FLOORS, in code, in the plainest sense of
    #     AGENTS.md 10.5 - "the creative direction decides how many
    #     cutaways and how many sounds a piece gets".  They survived the
    #     ruling that deleted `scale_sfx_density` and step 4.02's
    #     `min_trans` only because `tests/test_no_creative_floors.py`
    #     reads the planning steps and 5.03 is not one.
    #   * 1 and 2 reached the PICTURE.  `duration_frames` is the one field
    #     `cohesion_scope.ACTIONABLE_AT_COHESION` lets the compiler
    #     rewrite, so `suggested_value: 10` was a number nobody chose
    #     landing on a transition an editor had timed.
    #   * There is nothing to derive a replacement from.  A creative
    #     direction declares a `target_energy` in prose and no pace, no
    #     duration and no density, so any threshold here would be one
    #     this file invented.  Reporting that, rather than picking a
    #     number, is the rule (AGENTS.md 10.5).
    #
    # On project 001's run of record none of the four could fire at all:
    # the direction said "building", which `energy_reading` correctly
    # reads as neither high nor calm, so the whole block read as coverage
    # while doing nothing (AGENTS.md 10.4).
    #
    # A check on pace against energy is welcome here.  It needs a
    # DECLARED target to judge against - a pace the creative direction
    # actually states - which no step emits today.
    #
    # What the transitions and the SFX plan CAN say without a threshold
    # is how many there are.  That is reported below, under
    # `measurements`, and it fires nothing: a number beside a finding is
    # the shape the render-QA chroma and mix checks already use.

    # The timeline's real length, measured the way review_rough_cut
    # measures it: the last spine block's timeline_end. See
    # library/tools/timeline_duration.py for what this replaced.
    timeline_duration = measure_timeline_duration(inputs)

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
    from library.tools.duration_targets import (
        NO_TARGET_DECLARED, get_target_duration_zone)
    zone = get_target_duration_zone(inputs)
    min_dur, target_dur, max_dur = zone if zone else (None, None, None)

    if zone is None:
        # `get_target_duration_zone` used to answer 54/60/66 when nothing
        # declared a length, and nothing ever did reach this step, so
        # every run of this gate measured against a made-up minute.
        warnings.append(f"Duration not checked: {NO_TARGET_DECLARED}")
    elif not timeline_duration:
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
    # Counted, never judged.  The density this step used to demand of a
    # "high" energy edit (>= 10 per minute) is the captain's and the
    # creative direction's to decide; what this step can honestly do is
    # say what the plan came to.
    drawn = [t for t in transitions
             if isinstance(t, dict) and not is_cut(
                 t.get("transition_type", t.get("type", "")))]
    measurements = {
        # The word the direction actually wrote, reported beside the
        # counts and judged against nothing.  A reader can see what was
        # asked for and what the plan came to; turning the pair into a
        # verdict needs a declared pace, which no step emits.
        "declared_target_energy": direction_value(
            creative_direction, "target_energy"),
        "transitions_planned": len(transitions),
        "transitions_drawn": len(drawn),
        "sfx_events": len(sfx),
        "sfx_per_minute": (round((len(sfx) / timeline_duration) * 60, 1)
                           if timeline_duration else None),
    }

    cohesion_review = {
        "timeline_duration_seconds": round(timeline_duration, 2),
        "measurements": measurements,
        "warnings": warnings,
        # Every entry here is one `apply_cohesion_adjustments` applies.
        "adjustments": adjustments,
        # WHY that list is the length it is. An empty `adjustments` used
        # to read the same whether the review proposed nothing or
        # proposed only things a step upstream owns - and on every run
        # this project has made it has been the first, so the array read
        # as a clean bill of health on the edit. See
        # library/tools/cohesion_scope.adjustments_basis.
        "adjustments_basis": cohesion_scope.adjustments_basis(
            proposals, adjustments, observations),
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
