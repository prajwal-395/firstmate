"""PLAN-007: the evidence-driven replanning loop.

Each test names the DEFECT the rule is about, and the rule is about a
defect that was measured: a plan was refused, the refusal stopped
there, and the planner was never told what a narrower plan would have
been - so the whole plan was re-planned, or nothing was, and the work
that had already been verified was paid for again.
"""

from __future__ import annotations

import pytest

from library.tools import plan_revision


def _plan() -> list[dict]:
    return [
        {"op": "overlay.position", "target_role": "name_lower_third"},
        {"op": "caption_fix", "target_role": "wrong_caption_card"},
        {"op": "audio.gain", "target_role": "music_bed"},
    ]


def _impossible(op: str) -> plan_revision.Evidence:
    return plan_revision.Evidence(
        kind=plan_revision.KIND_IMPOSSIBLE,
        reason=f"{op} has no production-enabled backend",
        op=op, source=plan_revision.SOURCE_FEASIBILITY,
        detail={"check": "route_eligible", "capability_gap": True})


def _resolved(role: str, unique_id: str) -> plan_revision.Evidence:
    return plan_revision.Evidence(
        kind=plan_revision.KIND_RESOLVED, role=role,
        source=plan_revision.SOURCE_GROUNDING,
        detail={"identity": {"unique_id": unique_id, "name": "bar.mov",
                              "row": "V6"}})


def _ungrounded(role: str, *, premise_absent: bool) -> plan_revision.Evidence:
    return plan_revision.Evidence(
        kind=plan_revision.KIND_NOT_GROUNDED,
        reason=f"no surface carries {role!r}",
        role=role, source=plan_revision.SOURCE_GROUNDING,
        detail={"state": "unresolved", "surfaces_read": premise_absent,
                "premise_absent": premise_absent,
                "missing_evidence": "" if premise_absent else
                "the evidence surface was not read"})


def test_a_refused_operation_is_dropped_and_the_rest_of_the_plan_survives():
    # Defect: one refused operation took the whole plan down with it.
    receipt = plan_revision.replan(
        _plan(), [_impossible("caption_fix")])

    assert [row["op"] for row in receipt["operations"]] == [
        "overlay.position", "audio.gain"]
    assert [row["op"] for row in receipt["dropped"]] == ["caption_fix"]
    assert receipt["dropped"][0]["covers"]


def test_a_verified_target_re_targets_the_operation_that_names_the_role():
    # Defect: the grounding stage answered for every target role and
    # the answer never reached the plan.
    receipt = plan_revision.replan(
        _plan(), [_resolved("name_lower_third", "clip-7")])

    bound = receipt["operations"][0]
    assert bound["unique_id"] == "clip-7"
    assert receipt["retargeted"][0]["identity"]["unique_id"] == "clip-7"


def test_a_target_the_footage_does_not_carry_ends_in_a_question_not_a_drop():
    # Defect: an operation whose target does not exist was dropped in
    # silence, and the user was never asked what to do instead.
    receipt = plan_revision.replan(
        _plan(), [_ungrounded("music_bed", premise_absent=True)])

    assert receipt["status"] == plan_revision.STATUS_NEEDS_JUDGEMENT
    assert len(receipt["questions"]) == 1
    assert "music_bed" in receipt["questions"][0]["question"]


def test_a_target_nobody_has_looked_for_is_asked_of_the_planner_once():
    # Defect: an unlooked-for target was treated as a missing one, and
    # the model was asked about each affected operation in turn.
    asked: list[dict] = []

    def planner(request):
        asked.append(request)
        return {"operations": [dict(row) for row in request["operations"]]}

    receipt = plan_revision.replan(
        _plan(), [_ungrounded("music_bed", premise_absent=False)],
        planner=planner)

    assert len(asked) == 1
    assert [row["op"] for row in asked[0]["operations"]] == ["audio.gain"]
    assert receipt["model_calls"]["made"] == 1
    # The plan did not change: the target it named was established, and
    # the receipt says so.
    assert receipt["status"] == plan_revision.STATUS_NO_CHANGE
    assert receipt["established"][0]["op"] == "audio.gain"


def test_an_unchanged_context_costs_no_model_call():
    # Defect: the loop re-planned the same context over and over, at a
    # model call each time.
    calls: list[dict] = []

    def planner(request):
        calls.append(request)
        return {"operations": []}

    cache = plan_revision.RevisionCache()
    evidence = [_ungrounded("music_bed", premise_absent=False)]
    first = plan_revision.replan(_plan(), evidence, planner=planner,
                                 cache=cache)
    second = plan_revision.replan(_plan(), evidence, planner=planner,
                                  cache=cache)

    assert len(calls) == 1
    assert first["model_calls"]["made"] == 1
    assert second["model_calls"]["made"] == 0
    assert second["model_calls"]["saved"] == 1


def test_a_planner_that_rewrites_the_whole_plan_is_refused():
    # Defect: the "revision" could rewrite operations no evidence bore
    # on, and the narrow revision was not narrow.
    def planner(request):
        return {"operations": [{"op": "clip.trim", "target_role": "elsewhere"}]}

    with pytest.raises(ValueError) as raised:
        plan_revision.replan(_plan(),
                             [_ungrounded("music_bed", premise_absent=False)],
                             planner=planner)

    assert "only change the affected part" in str(raised.value)


def test_a_measurement_nobody_took_never_removes_an_operation():
    # Defect: an undetermined check was read as a refusal, and the
    # operation it named was removed.
    receipt = plan_revision.replan(_plan(), [plan_revision.Evidence(
        kind=plan_revision.KIND_MISSING,
        reason="the state was not read", op="caption_fix",
        source=plan_revision.SOURCE_FEASIBILITY,
        detail={"check": "target_identity"})])

    assert [row["op"] for row in receipt["operations"]] == [
        "overlay.position", "caption_fix", "audio.gain"]
    assert receipt["missing_evidence"][0]["op"] == "caption_fix"


def test_work_that_was_already_verified_survives_every_revision():
    # Defect: a refusal that named an operation the run had already
    # carried removed it from the plan.
    receipt = plan_revision.replan(
        _plan(),
        [_impossible("caption_fix"),
         plan_revision.Evidence(
             kind=plan_revision.KIND_PRESERVED, op="caption_fix",
             reason="the operation was already carried and verified")])

    assert "caption_fix" in [row["op"] for row in receipt["operations"]]
    assert receipt["dropped"] == []


def test_a_missing_route_is_named_as_a_capability_gap_and_not_as_a_question():
    # Defect: a missing engine route was reported as a question for the
    # user, and the user cannot answer it.
    receipt = plan_revision.replan(_plan(), [_impossible("caption_fix")])

    assert receipt["status"] == plan_revision.STATUS_NEEDS_CAPABILITY
    assert receipt["capability_gaps"][0]["op"] == "caption_fix"
    assert receipt["questions"] == []


def test_the_loop_stops_when_the_revision_settles():
    # Defect: the loop asked the planner again with the same context
    # after the answer changed nothing.
    calls: list[dict] = []

    def planner(request):
        calls.append(request)
        return {"operations": [dict(row) for row in request["operations"]]}

    def gather(plan):
        return [_ungrounded("music_bed", premise_absent=False)]

    receipt = plan_revision.loop(_plan(), gather, planner=planner)

    assert len(calls) == 1
    assert receipt["turn"] == 1


def test_the_receipt_names_which_evidence_bore_on_which_operation():
    # Defect: the receipt said "revised" and a reader could not tell
    # which operation the evidence was about.
    receipt = plan_revision.replan(
        _plan(), [_impossible("caption_fix"), _resolved("name_lower_third",
                                                        "clip-7")])

    assert receipt["format"] == plan_revision.FORMAT
    # Only the operation a PROBLEM bears on is affected; the operation
    # whose role was verified is preserved and re-targeted.
    assert receipt["affected"] == [
        {"op": "caption_fix", "target_role": "wrong_caption_card"}]
    assert [row["op"] for row in receipt["preserved"]] == [
        "overlay.position", "audio.gain"]


def test_the_planner_may_answer_with_another_operation_for_the_same_role():
    # Defect: a revision that could only keep the same operation could
    # not reach the same goal by another way.
    def planner(request):
        return {"operations": [{"op": "overlay.resize",
                                "target_role": "name_lower_third"}]}

    receipt = plan_revision.replan(
        _plan(), [_ungrounded("name_lower_third", premise_absent=False)],
        planner=planner)

    assert [row["op"] for row in receipt["operations"]] == [
        "overlay.resize", "caption_fix", "audio.gain"]
    assert receipt["replaced"][0]["operation"] == (
        "overlay.resize:name_lower_third")
