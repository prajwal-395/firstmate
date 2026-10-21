"""The structured editorial review contract (QA-006).

The defect this file names, in one sentence: a review that folds an
objective failure into a subjective score, so a file that fails its
audio spec reads as a seven-out-of-eight edit.

Every test here names a defect the contract refuses. They are not
coverage for its own sake - each one is a way the record could have
been shaped wrongly, and the contract's whole value is that it cannot
be:

- a judgment with no evidence is an opinion, and is refused;
- a judgment that does not say what could change it reads as a
  certainty it does not have, and is refused;
- a subjective row may not claim an objective basis, and an objective
  row may not claim a subjective one;
- a leg nobody read is `not_measured`, never a pass;
- an objective failure is not cleared by a subjective score.
"""

from __future__ import annotations

import pytest

from library.tools.editorial_acceptance import judgement


def _brief() -> dict:
    return {
        "instruction": "Open on the moment she says what it costs.",
        "project_state": {},
        "acceptance_criteria": [
            {"id": "hook-first", "statement": "it opens on the claim"},
        ],
    }


def _render() -> dict:
    return {
        "sha256": "c" * 64,
        "evidence": [{"kind": "frame", "artifact": "/tmp/frame-00.png",
                      "observation": "frame at 0.10s of the delivered file"}],
        "duration_seconds": 42.0,
    }


def _leg_row(leg: str, status: str = "pass", **overrides) -> dict:
    """One subjective row for one leg, valid unless a test breaks it."""
    row = {
        "leg": leg,
        "status": status,
        "basis": judgement._basis_for(judgement.SUBJECTIVE, status),
        "rationale": "read off the delivered file's own evidence",
        "evidence": [{"artifact": "/tmp/frame-00.png",
                      "observation": "the delivered frame at the cut"}],
        "uncertainty": {"level": "low",
                        "note": "a second reader might weigh the pause "
                                "differently"},
    }
    row.update(overrides)
    return row


def _all_legs(status: str = "pass", **overrides) -> list[dict]:
    return [_leg_row(leg, status, **overrides)
            for leg in judgement.REVIEW_LEG_IDS]


def _objective_row(leg: str = "shot_quality", status: str = "fail",
                   **overrides) -> dict:
    """One objective row, valid unless a test breaks it."""
    row = {
        "leg": leg,
        "status": status,
        "basis": judgement._basis_for(judgement.OBJECTIVE, status),
        "rationale": "the engine's own gate on the delivered file",
        "evidence": [{"artifact": "/tmp/delivered.mp4",
                      "observation": "the delivered file's own picture"}],
        "uncertainty": {"level": "none",
                        "note": "the gate reads the file, not the plan"},
        "measurements": [{
            "name": judgement.BLACK_FRAME_CITATION,
            "citation": judgement.BLACK_FRAME_CITATION,
            "status": status,
            "detail": "the file holds an undeclared black frame",
        }],
    }
    row.update(overrides)
    return row


def test_the_review_reads_the_eight_named_legs():
    """The defect: a review that quietly judged five of the eight legs,
    so its coverage was nobody's to read."""
    assert len(judgement.REVIEW_LEGS) == 8
    assert len(set(judgement.REVIEW_LEG_IDS)) == 8
    for leg in judgement.REVIEW_LEGS:
        assert leg.statement.strip()


def test_a_measured_judgment_without_evidence_is_refused():
    """The defect: a judgment with no evidence behind it - an opinion
    wearing a verdict's clothes."""
    with pytest.raises(judgement.ReviewError) as caught:
        judgement.review(task_id="t", brief=_brief(), render=_render(),
                         judgments=_all_legs(status="pass",
                                            evidence=[]))
    assert "carries no evidence" in str(caught.value)


def test_a_judgment_that_states_no_uncertainty_is_refused():
    """The defect: a reading that does not say what could change it
    reads as a certainty it does not have."""
    rows = _all_legs()
    del rows[0]["uncertainty"]
    with pytest.raises(judgement.ReviewError) as caught:
        judgement.review(task_id="t", brief=_brief(), render=_render(),
                         judgments=rows)
    assert "states no uncertainty" in str(caught.value)


def test_a_subjective_row_may_not_claim_an_objective_basis():
    """The defect: a preference posing as a technical failure, so the
    two kinds of claim could not be told apart in the record."""
    rows = _all_legs()
    rows[0]["status"] = "fail"
    rows[0]["basis"] = judgement.OBJECTIVE_FAILURE
    with pytest.raises(judgement.ReviewError) as caught:
        judgement.review(task_id="t", brief=_brief(), render=_render(),
                         judgments=rows)
    assert "objective failure and a subjective preference are different" \
        in str(caught.value)


def test_an_objective_row_may_not_claim_a_subjective_basis():
    """The defect: a measured failure dressed as an opinion, so the
    verdict could be argued away."""
    with pytest.raises(judgement.ReviewError) as caught:
        judgement.review(task_id="t", brief=_brief(), render=_render(),
                         judgments=_all_legs(),
                         objective=[_objective_row(
                             status="fail",
                             basis=judgement.SUBJECTIVE_PREFERENCE)])
    assert "objective failure and a subjective preference are different" \
        in str(caught.value)


def test_an_objective_row_must_name_the_measurement_it_came_from():
    """The defect: an objective row with no measurement behind it - a
    technical verdict nobody can trace to a gate."""
    row = _objective_row()
    del row["measurements"]
    with pytest.raises(judgement.ReviewError) as caught:
        judgement.review(task_id="t", brief=_brief(), render=_render(),
                         judgments=_all_legs(), objective=[row])
    assert "names no measurement" in str(caught.value)


def test_an_objective_failure_is_not_cleared_by_a_passing_subjective_score():
    """The defect this contract exists for: a file that fails its audio
    spec reading as a seven-out-of-eight edit because every leg the
    judge scored well outweighed the one the gate failed."""
    record = judgement.review(
        task_id="t", brief=_brief(), render=_render(),
        judgments=_all_legs(),
        objective=[_objective_row(leg="audio_continuity", status="fail")])
    assert record["verdict"] == "fail"
    assert record["subjective_score"] == 1.0
    assert [row["leg"] for row in record["objective_failures"]] == [
        "audio_continuity"]
    assert record["subjective_score_cannot_clear"] == ["objective_failure"]
    assert "not cleared by a subjective score" in record["verdict_reason"]


def test_a_subjective_failure_is_a_preference_and_not_a_technical_failure():
    """The defect: an editorial opinion reported as a technical defect,
    so nobody could tell which failures a re-render would fix."""
    record = judgement.review(
        task_id="t", brief=_brief(), render=_render(),
        judgments=_all_legs(status="fail"))
    assert record["verdict"] == "preference"
    assert record["objective_failures"] == []
    assert len(record["subjective_failures"]) == 8


def test_a_leg_nobody_read_is_not_measured_and_never_a_pass():
    """The defect: a leg the judge could not read, left out of the
    record, so the coverage read as complete."""
    rows = _all_legs()
    unmeasured = rows[0]
    unmeasured["status"] = "not_measured"
    unmeasured["basis"] = judgement.NOT_MEASURED
    unmeasured["evidence"] = []
    record = judgement.review(task_id="t", brief=_brief(),
                               render=_render(), judgments=rows)
    assert record["verdict"] == "not_measured"
    assert record["counts"]["subjective_not_measured"] == 1


def test_a_leg_left_out_entirely_is_refused():
    """The defect: a review with a leg MISSING rather than a leg read
    as not measured - the difference between a gap that is visible and
    a gap that is not."""
    rows = _all_legs()[1:]
    with pytest.raises(judgement.ReviewError) as caught:
        judgement.review(task_id="t", brief=_brief(), render=_render(),
                         judgments=rows)
    assert "does not carry a row for" in str(caught.value)


def test_two_rows_for_one_leg_are_refused():
    """The defect: two readings of one leg, and the count that came out
    of them depended on which one a reader found first."""
    rows = _all_legs()
    rows.append(_leg_row(judgement.REVIEW_LEG_IDS[0]))
    with pytest.raises(judgement.ReviewError) as caught:
        judgement.review(task_id="t", brief=_brief(), render=_render(),
                         judgments=rows)
    assert "more than one row" in str(caught.value)


def test_the_review_cites_the_engine_own_delivered_file_measurements():
    """The defect: a review that re-measured the file its own way, so
    the harness and the product graded one file two ways."""
    cited = {citation for leg in judgement.REVIEW_LEGS
             for citation in leg.objective_citations}
    assert judgement.AUDIO_SPEC_CITATION in cited
    assert judgement.CAPTION_SPEC_CITATION in cited
    assert judgement.PICTURE_SPEC_CITATION in cited
    record = judgement.review(task_id="t", brief=_brief(),
                               render=_render(), judgments=_all_legs(),
                               objective=[_objective_row()])
    for leg in record["legs"]:
        assert leg["read"] is True
    shot = next(leg for leg in record["legs"]
                if leg["leg"] == "shot_quality")
    assert shot["objective_citations"]


def test_a_judge_is_handed_the_same_independent_view_as_the_scorer():
    """The defect: a review judge handed the plan, so its readings were
    an echo of the plan rather than a reading of the film."""
    seen = {}

    def _judge(brief, render):
        seen["brief"] = brief
        seen["render"] = render
        return {"legs": _all_legs()}

    judgement.review(task_id="t", brief=_brief(), render=_render(),
                     judge=_judge)
    assert set(seen["brief"]) == {"instruction", "project_state",
                                  "acceptance_criteria"}
    assert set(seen["render"]) == {"sha256", "evidence", "duration_seconds"}
