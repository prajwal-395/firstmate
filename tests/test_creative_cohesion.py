"""What the cohesion review reports, and to whom.

Step 5.03 runs next to last.  Every plan it reads is made in steps 4.02
to 5.01, so it cannot be moved upstream of the decisions it reviews - it
would arrive before its own inputs exist.  What it can do is stop
presenting a recommendation it has no route to apply as though it were
one, which is #238.

Two things these tests used to assert, and no longer can:

- **`cohesion_score`.**  100 minus a hand-picked weight per finding, read
  by nothing outside the step.  Removed; see the comment at the end of
  `review_creative_cohesion` for the three reasons.
- **`color_grade_spec["mood"]`.**  Step 5.01 emits no such key - project
  001's real spec carries `grade_pipeline`, `fusion_look`, `house_look`,
  `look_notes` and five more - so the substring match ran against "" on
  every real run.  The old fixtures supplied `mood` themselves, which is
  a fixture proving a fixture.  The check is removed with the same three
  reasons the pacing check was (docs/PIPELINE_PLAN.md P4.2).
"""
import json
from pathlib import Path

from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion
from library.tools.passage_engagement import NO_ENGAGEMENT_BASIS

REPO = Path(__file__).resolve().parents[1]

# The timeline the duration gate measures. It used to read
# `body_sequence[-1]["end_time"]` - a SOURCE timestamp - so these fixtures
# expressed the length as a speech passage ending at 60s. The spine is the
# timeline; see library/tools/timeline_duration.py.
SPINE_60S = {
    "structure": [
        {"block_type": "hook", "position": 0,
         "timeline_start": 0.0, "timeline_end": 3.0},
        {"block_type": "speech", "position": 1,
         "timeline_start": 3.0, "timeline_end": 60.0},
    ]
}


def test_a_high_energy_mismatch_is_reported_and_is_applicable_here():
    """Both findings are real; only one of them is an adjustment."""
    inputs = {
        "creative_direction": {"target_energy": "high"},
        "transition_spec": {
            "transitions": [
                {"transition_type": "defocus", "duration_frames": 30}
            ]
        },
        "sfx_spec": [],  # sparse
        "speech_sequence": {"body_sequence": [{"start_time": 0, "end_time": 60}]},
        "audio_spine": SPINE_60S,
    }

    review = review_creative_cohesion(inputs)

    warnings = review["warnings"]
    assert any("High energy but found slow transition" in w for w in warnings)
    assert any("High energy but sparse SFX" in w for w in warnings)

    # The transition duration is the one thing the compiler rewrites
    # without moving a frame, so it is the one adjustment.
    assert [(a["target_step"], a["field"], a["suggested_value"])
            for a in review["adjustments"]] == [
        ("transition_spec", "duration_frames", 10)]

    # SFX are chosen and placed at 4.04. The finding stays, as an
    # observation naming who owns it - not as an adjustment that is then
    # silently dropped.
    assert [o["state_key"] for o in review["observations"]] == ["sfx_spec"]
    assert review["observations"][0]["owner_step"] == "step_4_04_plan_sfx"
    assert "plan_sfx" in review["observations"][0]["how_to_act"]


# Project 001's REAL `transition_spec`, as step 4.02 emitted it on the
# 2026-08-26 run: fifteen entries, eleven `hard_cut` and two `jump_cut` at
# zero frames, and two `defocus` at 15 frames - 500 ms exactly, which is
# the high-energy trigger. The rows are the real ones, keys and all, so
# this covers the one actionable finding against the shape the planner
# really produces rather than against `{"type": "cross_dissolve"}`, which
# is a withdrawn type under a key alias and is what the removed test used.
#
# 001 itself declares `target_energy: "building"`, which reads as
# `moderate` (AGENTS.md 10.1), so the check is inert on 001 as it stands.
# The energy is the ONE value this fixture supplies.
TRANSITIONS_001 = (
    [{"transition_id": f"trans_{i:03d}", "transition_type": "hard_cut",
      "duration_frames": 0, "cut_point_timeline": 2.398 + i} for i in range(1, 8)]
    + [{"transition_id": "trans_008", "transition_type": "defocus",
        "duration_frames": 15, "cut_point_timeline": 34.394}]
    + [{"transition_id": f"trans_{i:03d}", "transition_type": "hard_cut",
        "duration_frames": 0, "cut_point_timeline": 2.398 + i} for i in range(9, 13)]
    + [{"transition_id": "trans_013", "transition_type": "defocus",
        "duration_frames": 15, "cut_point_timeline": 46.147}]
    + [{"transition_id": f"trans_{i:03d}", "transition_type": "jump_cut",
        "duration_frames": 0, "cut_point_timeline": 2.398 + i} for i in (14, 15)]
)


def test_the_actionable_finding_fires_on_001s_real_transition_spec():
    """The one thing 5.03 can still change where it runs, driven end to
    end: 5.03 finds it, 5.04 applies it, and the durations move.

    `duration_frames` is the whole of `ACTIONABLE_AT_COHESION`, so if this
    stops firing the review has nothing left that it can act on and the
    step is a withdrawal rather than a rescope.
    """
    from library.steps.step_5_04_compile_manifest.step import (
        apply_cohesion_adjustments,
    )
    transitions = [dict(t) for t in TRANSITIONS_001]
    review = review_creative_cohesion({
        "creative_direction": {"target_energy": "high"},
        "transition_spec": transitions,
        "sfx_spec": [],
        "audio_spine": SPINE_60S,
    })

    # The two real defocus transitions, at their real indices.
    assert [(a["target_index"], a["current_value"], a["suggested_value"])
            for a in review["adjustments"]] == [(7, 15, 10), (12, 15, 10)]
    assert all(a["target_step"] == "transition_spec"
               and a["field"] == "duration_frames"
               for a in review["adjustments"])
    # The eleven hard cuts and two jump cuts draw nothing, so they are not
    # findings (transition_vocabulary.CUT_TYPES).
    assert sum(1 for w in review["warnings"]
               if "slow transition" in w) == 2

    record = apply_cohesion_adjustments(transitions, review)
    assert record["not_applied"] == [], record["not_applied"]
    assert record["applied"] == [
        "transition[7].duration_frames: 15 -> 10",
        "transition[12].duration_frames: 15 -> 10"]
    assert [t["duration_frames"] for t in transitions
            if t["transition_id"] in ("trans_008", "trans_013")] == [10, 10]


def test_an_aligned_edit_reports_only_what_it_could_not_measure():
    inputs = {
        "creative_direction": {"target_energy": "calm"},
        "transition_spec": {
            "transitions": [
                {"transition_type": "fade_to_black", "duration_frames": 45},
                {"transition_type": "defocus", "duration_frames": 45},
                {"transition_type": "hard_cut", "duration_frames": 0},
            ]
        },
        # 5 SFX over 60s is 5 per minute, inside the calm ceiling of 15.
        "sfx_spec": [{"sfx_type": "swell"} for _ in range(5)],
        "speech_sequence": {"body_sequence": [{"start_time": 0, "end_time": 60}]},
        "audio_spine": SPINE_60S,
    }

    review = review_creative_cohesion(inputs)

    # The passages carry no engagement judgement, and nothing invents one
    # - see library/tools/passage_engagement.py. The review states that
    # rather than comparing zeros.
    assert review["warnings"] == [
        "Engagement not compared: " + NO_ENGAGEMENT_BASIS,
    ]
    assert review["adjustments"] == []
    assert review["observations"] == []


def test_missing_inputs_are_stated_not_judged():
    review = review_creative_cohesion({})
    # Each gate says it could not measure, rather than judging the edit
    # against an energy and a duration this step invented.
    assert review["warnings"] == [
        "Energy not checked: the creative direction declares no "
        "target_energy, so there is nothing to judge the transitions and "
        "SFX density against",
        "Engagement not compared: " + NO_ENGAGEMENT_BASIS,
        "Duration not checked: neither audio_spine nor a_roll_assignments "
        "reached creative_cohesion, so the timeline length is unknown",
    ]
    assert review["adjustments"] == []
    assert review["observations"] == []


def test_the_review_no_longer_reports_a_score():
    """`cohesion_score` was 100 minus weights nobody measured, and nothing
    outside this step ever read it."""
    review = review_creative_cohesion({})
    assert "cohesion_score" not in review


def test_the_review_no_longer_claims_to_have_applied_anything():
    """`applied_adjustments` was structurally always empty: the step emits
    only `cohesion_review`, so the mutations it recorded never left the
    process."""
    review = review_creative_cohesion({})
    assert "applied_adjustments" not in review
    assert review["applied_by"] == "step_5_04_compile_manifest"


def test_the_grade_check_that_could_not_fire_is_gone():
    """`mood` and `grade_name` are keys step 5.01 does not emit.

    Asserted on the source rather than on a call, because a check that
    reads a key nothing writes passes every behavioural test by being
    silent - which is exactly how it survived.
    """
    source = (REPO / "library" / "steps" / "step_5_03_creative_cohesion"
              / "step.py").read_text(encoding="utf-8")
    live = [line for line in source.splitlines()
            if not line.strip().startswith("#")]
    assert not any("grade_name" in line for line in live), (
        "the colour check is reading a key step 5.01 does not emit again")
    assert not any('color_grade_spec.get(' in line for line in live)


def test_step_501_really_emits_neither_key():
    """The evidence for the removal, held where it goes stale.

    Driven against step 5.01's own builder, so this fails the day the
    grade starts declaring a mood and the check can come back with
    something real to read.
    """
    from library.steps.step_5_01_color_grade.step import define_color_grade
    spec = define_color_grade({})["color_grade_spec"]
    assert "mood" not in spec and "grade_name" not in spec, spec.keys()
    # What it does carry, so a reader knows what a future check has.
    assert {"house_look", "fusion_look", "look_notes"} <= set(spec)


def test_the_cohesion_step_no_longer_asks_for_the_grade():
    """A required input nothing reads is the declaration half of the same
    defect (AGENTS.md section 3, "A declaration must be true")."""
    manifest = json.loads(
        (REPO / "library" / "steps" / "step_5_03_creative_cohesion"
         / "manifest.json").read_text(encoding="utf-8"))
    names = {i["name"] for i in manifest["interface"]["inputs"]}
    assert "color_grade_spec" not in names

    dag = json.loads(
        (REPO / "library" / "processes" / "edit_video" / "dag.json")
        .read_text(encoding="utf-8"))
    assert not [e for e in dag["edges"]
                if e["from"] == "color_grade" and e["to"] == "creative_cohesion"]
