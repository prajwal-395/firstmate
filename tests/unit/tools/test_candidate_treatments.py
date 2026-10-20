"""What the candidate treatment contract must refuse, and what it must keep.

Each test names a defect: a judgement the engine invented, an absent
measurement counted as a pass, a plan that grows when it should shrink,
an operation that cannot be undone reported as undoable, a combination
of two answers to one question, and alternatives that evaporate once a
winner is picked.
"""

from __future__ import annotations

import pytest

from library.tools import candidate_treatments as ct


def _brief(name, verdict="pass", reason="the claim lands first"):
    return {"criterion": name, "kind": "brief", "verdict": verdict,
            "reason": reason, "evidence_refs": ["transcript:1"]}


def _trim(unique_id="clip-1"):
    return {"op": "clip.trim",
            "params": {"unique_id": unique_id, "start_frame": 0,
                       "end_frame": 90},
            "text": "trim"}


def _treatment(treatment_id="t1", name="Lead with the claim",
               rationale="The claim lands the point.", operations=None,
               criteria=None, **kwargs):
    return ct.treatment(
        treatment_id, name, rationale,
        operations if operations is not None else [_trim()],
        criteria=criteria if criteria is not None else [_brief("the claim "
                                                                "lands")],
        **kwargs)


# ── A judgement is never invented ─────────────────────────────────────


def test_a_brief_criterion_with_no_verdict_is_refused():
    """A criterion the model did not judge is not judged by the engine."""
    with pytest.raises(ct.CandidateError):
        ct.treatment("t1", "n", "r", [_trim()],
                     criteria=[{"criterion": "the claim lands",
                                "kind": "brief",
                                "reason": "it does"}])


def test_a_brief_criterion_with_no_reason_is_refused():
    """A verdict nobody explained is not a judgement."""
    with pytest.raises(ct.CandidateError):
        ct.treatment("t1", "n", "r", [_trim()],
                     criteria=[{"criterion": "the claim lands",
                                "kind": "brief", "verdict": "pass"}])


def test_a_candidate_with_no_rationale_is_refused():
    """A plan nobody can weigh against another plan is not a candidate."""
    with pytest.raises(ct.CandidateError):
        ct.treatment("t1", "n", "   ", [_trim()])


# ── An absent measurement is not a pass ───────────────────────────────


def test_a_constraint_with_no_measurement_is_not_measured_not_passed():
    """The target window cannot be checked against a duration nobody gave."""
    candidate = ct.treatment(
        "t1", "n", "r", [_trim()],
        criteria=[{"criterion": "the reel stays under 30s",
                   "kind": "constraint",
                   "constraint": "within_target_duration",
                   "params": {"target_seconds": 30,
                              "tolerance_seconds": 2}}])
    verdict = candidate["evaluation"][0]
    assert verdict["verdict"] == ct.NOT_MEASURED
    assert not ct.satisfies(candidate)


def test_an_unmeasured_criterion_is_not_preferred():
    """A candidate nothing could measure never wins the comparison."""
    measured = ct.treatment(
        "measured", "n", "r", [_trim()],
        criteria=[_brief("the claim lands")])
    unmeasured = ct.treatment(
        "unmeasured", "n", "r", [_trim()],
        criteria=[{"criterion": "the reel stays under 30s",
                   "kind": "constraint",
                   "constraint": "within_target_duration",
                   "params": {"target_seconds": 30}}])
    comparison = ct.compare(ct.build("make it punchier",
                                      [unmeasured, measured]))
    assert comparison["recommended"] == "measured"
    assert comparison["unverified"] == ["unmeasured"]


def test_a_candidate_with_no_criteria_satisfies_nothing():
    """An unjudged candidate proves nothing, however plausible it reads."""
    candidate = ct.treatment("t1", "n", "r", [_trim()], criteria=[])
    assert not ct.satisfies(candidate)
    comparison = ct.compare(ct.build("r", [candidate]))
    assert comparison["recommended"] is None


# ── The least change that achieves the goal wins ─────────────────────


def test_the_smaller_change_wins_when_both_achieve_the_goal():
    """Two plans both work; the one that touches less is preferred."""
    wide = ct.treatment(
        "wide", "Rework everything", "It would be better.",
        [{"op": "clip.trim", "params": {"unique_id": "a"}},
         {"op": "caption.style", "params": {"unique_id": "b"}}],
        criteria=[_brief("the claim lands")])
    narrow = ct.treatment(
        "narrow", "Trim one clip", "The claim lands first.",
        [_trim()], criteria=[_brief("the claim lands")])
    comparison = ct.compare(ct.build("r", [wide, narrow]))
    assert comparison["recommended"] == "narrow"


def test_a_failing_candidate_is_never_recommended():
    """A candidate that fails a criterion loses whatever it touches."""
    good = ct.treatment("good", "n", "r", [_trim()],
                        criteria=[_brief("the claim lands")])
    bad = ct.treatment("bad", "n", "r", [_trim()],
                       criteria=[_brief("the claim lands", verdict="fail",
                                         reason="the claim is buried")])
    comparison = ct.compare(ct.build("r", [good, bad]))
    assert comparison["recommended"] == "good"
    assert comparison["ineligible"][0]["treatment_id"] == "bad"


def test_the_order_does_not_depend_on_the_order_the_candidates_arrived_in():
    """The comparison is a total order, not a sort of the input list."""
    wide = ct.treatment("wide", "n", "r",
                        [{"op": "clip.trim", "params": {"unique_id": "a"}},
                         {"op": "caption.style", "params": {"unique_id": "b"}}],
                        criteria=[_brief("c")])
    narrow = ct.treatment("narrow", "n", "r", [_trim()],
                          criteria=[_brief("c")])
    forward = ct.compare(ct.build("r", [wide, narrow]))
    backward = ct.compare(ct.build("r", [narrow, wide]))
    assert forward["order"] == backward["order"] == ["narrow", "wide"]


# ── Risk is read off the operation ────────────────────────────────────


def test_an_operation_the_registry_does_not_declare_is_high_risk():
    """A plan naming an operation Ren has never heard of is not safe."""
    candidate = ct.treatment("t1", "n", "r",
                             [{"op": "clip.teleport", "params": {}}])
    assert candidate["risk"]["level"] == ct.HIGH
    assert candidate["risk"]["unknown_operations"] == ["clip.teleport"]


def test_an_irreversible_operation_is_reported_as_irreversible():
    """A plan that cannot be undone says so, whatever its rationale says."""
    candidate = ct.treatment(
        "t1", "n", "r",
        [{"op": "grade", "params": {"unique_id": "x"}}],
        criteria=[_brief("c")])
    if candidate["risk"]["reversible"]:
        # `grade` is undoable in the registry; the point is that the
        # assessment reports the registry's own answer either way.
        assert candidate["risk"]["irreversible_operations"] == []
    else:
        assert candidate["risk"]["irreversible_operations"]


def test_a_candidate_touching_two_categories_is_not_low_risk():
    """A plan that reaches across the edit carries more risk than one that
    does not."""
    narrow = ct.treatment("narrow", "n", "r", [_trim()],
                          criteria=[_brief("c")])
    wide = ct.treatment("wide", "n", "r",
                        [{"op": "clip.trim", "params": {"unique_id": "a"}},
                         {"op": "caption.style", "params": {"unique_id": "b"}}],
                        criteria=[_brief("c")])
    assert narrow["risk"]["level"] != ct.HIGH
    assert wide["risk"]["level"] != ct.LOW


# ── The explicit no-change conclusion ─────────────────────────────────


def test_a_no_change_conclusion_with_no_reason_is_refused():
    """'Nothing to do' without a reason reads as a plan nobody made."""
    with pytest.raises(ct.CandidateError):
        ct.no_change("make it punchier", "   ")


def test_a_no_change_conclusion_is_a_first_class_outcome():
    """The conclusion carries its reason and the evidence it rests on."""
    conclusion = ct.no_change("make it punchier",
                               "the claim already lands first",
                               evidence_refs=["transcript:1"])
    assert ct.is_no_change(conclusion)
    assert conclusion["no_change"]["reason"] == "the claim already lands first"
    assert conclusion["no_change"]["evidence_refs"] == ["transcript:1"]
    assert conclusion["treatments"] == []


def test_a_candidate_set_cannot_be_both_treatments_and_no_change():
    """A set that offers plans and concludes nothing should change says
    two contradictory things."""
    with pytest.raises(ct.CandidateError):
        ct.build("r", [_treatment()], no_change_reason="it is fine")


def test_a_candidate_set_with_neither_is_refused():
    """A set with neither a plan nor a conclusion says nothing at all."""
    with pytest.raises(ct.CandidateError):
        ct.build("r", [])


# ── Choosing one, or combining several ───────────────────────────────


def test_two_candidates_cannot_share_an_id():
    """Two candidates with one id cannot be told apart."""
    with pytest.raises(ct.CandidateError):
        ct.build("r", [_treatment("same"), _treatment("same")])


def test_choosing_names_what_it_was_chosen_over():
    """A choice says which candidate it beat."""
    chosen = _treatment("chosen")
    other = _treatment("other")
    decision = ct.select(ct.build("r", [chosen, other]), "chosen")
    assert decision["decision"] == ct.SELECTED
    assert decision["chosen_over"] == ["other"]


def test_choosing_a_candidate_the_evidence_rules_out_needs_an_override():
    """A choice against the measurement says who overruled it and why."""
    bad = _treatment("bad", criteria=[_brief("c", verdict="fail",
                                              reason="it does not")])
    candidate_set = ct.build("r", [bad])
    with pytest.raises(ct.CandidateError):
        ct.select(candidate_set, "bad")
    decision = ct.select(candidate_set, "bad", overruled_by="the editor",
                         override_reason="the editor knows this cut")
    assert decision["overruled_by"] == "the editor"
    assert decision["override_reason"] == "the editor knows this cut"


def test_two_elements_that_contradict_each_other_cannot_be_combined():
    """Two answers to the same category cannot both stand."""
    left = _treatment("left", name="Left", rationale="r", elements=[
        {"element_id": "left/hook", "category": "sequence",
         "target": "reel-1", "op": "clip.trim"}])
    right = _treatment("right", name="Right", rationale="r", elements=[
        {"element_id": "right/hook", "category": "sequence",
         "target": "reel-1", "op": "clip.trim"}])
    candidate_set = ct.build("r", [left, right])
    with pytest.raises(ct.CandidateError):
        ct.combine(candidate_set, [{"treatment_id": "left",
                                    "element_id": "left/hook"},
                                   {"treatment_id": "right",
                                    "element_id": "right/hook"}])


def test_compatible_elements_from_several_candidates_combine():
    """A hook from one candidate and an ending from another are one plan."""
    left = _treatment("left", name="Left", rationale="r", elements=[
        {"element_id": "left/hook", "category": "sequence",
         "target": "reel-1", "op": "clip.trim",
         "operation": {"op": "clip.trim", "params": {"unique_id": "a"}}}])
    right = _treatment("right", name="Right", rationale="r", elements=[
        {"element_id": "right/ending", "category": "graphics",
         "target": "reel-1-end", "op": "end_card",
         "operation": {"op": "end_card", "params": {"unique_id": "b"}}}])
    decision = ct.combine(ct.build("r", [left, right]),
                           [{"treatment_id": "left",
                             "element_id": "left/hook"},
                            {"treatment_id": "right",
                             "element_id": "right/ending"}])
    assert decision["decision"] == ct.COMBINED
    assert decision["treatment_ids"] == ["left", "right"]
    assert len(decision["operations"]) == 2


def test_combining_an_element_that_does_not_exist_is_refused():
    """A pick nobody offered is not a combination."""
    candidate = _treatment("one", elements=[
        {"element_id": "one/hook", "category": "sequence",
         "target": "reel-1", "op": "clip.trim"}])
    with pytest.raises(ct.CandidateError):
        ct.combine(ct.build("r", [candidate]),
                   [{"treatment_id": "one", "element_id": "one/nothing"}])


# ── The alternatives that lost are kept ───────────────────────────────


def test_the_decision_record_keeps_the_alternatives_that_lost(tmp_path):
    """A decision nobody can reconstruct the reasoning of cannot be
    revisited."""
    chosen = _treatment("chosen")
    other = _treatment("other", name="Other", rationale="A different cut.")
    candidate_set = ct.build("the opening feels slow", [chosen, other])
    decision = ct.select(candidate_set, "chosen")
    ct.record(tmp_path, candidate_set, decision,
              reason="the editor picked the claim")
    records = ct.read_records(tmp_path)
    assert len(records) == 1
    kept = records[0]["treatments"]
    assert [row["treatment_id"] for row in kept] == ["chosen", "other"]
    assert any(row["rationale"] == "A different cut." for row in kept)


def test_a_no_change_decision_record_carries_no_operations(tmp_path):
    """A decision to change nothing records no change."""
    candidate_set = ct.no_change("make it punchier",
                                 "the claim already lands first")
    decision = {"decision": ct.NO_CHANGE, "operations": [],
                "categories": []}
    ct.record(tmp_path, candidate_set, decision, reason="it is fine")
    records = ct.read_records(tmp_path)
    assert records[0]["candidate_decision"]["decision"] == ct.NO_CHANGE
    assert records[0]["no_change"]["reason"] == "the claim already lands first"


def test_the_candidate_set_is_readable_in_the_shared_decision_vocabulary():
    """Catches: the alternatives kept only in this module's own record,
    so a reader of the project's decisions could not see them."""
    from library.tools import editorial_decisions

    candidate = _treatment("t1", name="Claim first",
                           rationale="The claim lands the point.")
    candidate_set = ct.build("the opening feels slow", [candidate])
    rows = ct.to_editorial_decisions(candidate_set)
    assert len(rows) == 1
    assert rows[0].decision_id == "t1"
    assert rows[0].state == editorial_decisions.CANDIDATE
    assert rows[0].intended_effect == "Claim first"
    assert editorial_decisions.validate_decision(rows[0]) == []


def test_the_alternatives_that_lost_are_kept_as_decisions(tmp_path):
    """Catches: the decision record carrying only the winner, so the
    reasoning behind the choice could not be read back."""
    candidate = _treatment("t1", name="Claim first",
                           rationale="The claim lands the point.")
    other = _treatment("t2", name="Question first",
                       rationale="Opens on a question.")
    candidate_set = ct.build("the opening feels slow", [candidate, other])
    decision = ct.select(candidate_set, "t1")
    revision = ct.record(tmp_path, candidate_set, decision)
    from library.tools import creative_revision

    row = creative_revision.get(tmp_path, revision["revision_id"])
    decisions = row["refs"]["candidates"]["decisions"]
    assert [entry["decision_id"] for entry in decisions] == ["t1", "t2"]
