"""The independent editorial assessment, and what a judge is allowed to see.

The defect: an editorial assessment that was told the answer. A judge
that sees the operations the request implies, the semantic target roles
or the source moments is not reading the film - it is reading the
evaluator's plan back, and the assessment measures the plan rather than
the result.

So the judge's view is ONE construction, built from the brief's public
half and the delivered file's own digest, evidence and length. And a
panel of judges that disagrees has established nothing, which is not a
pass and not a failure.
"""

from __future__ import annotations

import json

import pytest

from library.tools.editorial_acceptance import briefs, judgement


def _brief() -> dict:
    return {
        "id": "open-with-the-cost",
        "title": "Open on the cost",
        "instruction": "Open on the moment she says what it costs.",
        "project_state": {
            "footage_profile": "two_camera_interview",
            "existing_adjustments": ["a hand-made pan"],
            "media_conditions": ["a jump cut at 00:12"],
        },
        "acceptance_criteria": [
            {"id": "hook-first", "statement": "it opens on the claim"},
            {"id": "nothing-lost", "statement": "nothing she said is gone"},
        ],
    }


def _render() -> dict:
    return {
        "sha256": "b" * 64,
        "evidence": [{"kind": "frame", "artifact": "/tmp/frame-00.png",
                      "observation": "frame at 0.10s of the delivered file"}],
        "duration_seconds": 42.0,
    }


def _judge(statuses: dict[str, str], name: str = "an independent editor"):
    def _judge_fn(brief, render):
        _judge_fn.seen.append({"brief": brief, "render": render})
        criteria = []
        for criterion in brief["acceptance_criteria"]:
            status = statuses.get(criterion["id"], "pass")
            criteria.append({
                "id": criterion["id"],
                "status": status,
                "rationale": "read off the delivered file",
                "evidence": [{"artifact": "/tmp/frame-00.png",
                              "observation": "the first frame"}],
            })
        return {"judge": name, "criteria": criteria}
    _judge_fn.seen = []
    return _judge_fn


def test_a_judge_sees_the_brief_and_the_file_and_nothing_else():
    """The defect: the judge is handed the answer key, and the
    assessment becomes an echo of the plan."""
    judge = _judge({})
    judgement.assess(task_id="t", brief=_brief(), render=_render(),
                     judge=judge)
    seen = judge.seen[0]
    serialised = json.dumps(seen)
    assert set(seen["brief"]) == {"instruction", "project_state",
                                   "acceptance_criteria"}
    assert set(seen["render"]) == {"sha256", "evidence", "duration_seconds"}
    for hidden in briefs.ORACLE_KEYS:
        assert hidden not in seen["brief"]
    assert "expected_operations" not in serialised
    assert "opening_section" not in serialised


def test_a_brief_carrying_the_answer_key_is_refused():
    """The defect: a brief that reaches the judge with the key in it, so
    the independence claim on the record is a lie."""
    leaky = _brief()
    leaky["expected_operations"] = ["reel.opening"]
    with pytest.raises(judgement.JudgementError):
        judgement.assert_independent(leaky)
    with pytest.raises(judgement.JudgementError):
        judgement.judge_input(leaky, _render())


def test_the_record_says_what_the_judge_was_shown():
    """The defect: an assessment with no provenance, so nobody can tell
    later whether it was independent."""
    record = judgement.assess(task_id="t", brief=_brief(), render=_render(),
                              judge=_judge({}))
    independence = record["independence"]
    assert "render.sha256" in independence["judge_saw"]
    assert "expected_operations" in independence["judge_did_not_see"]
    assert record["brief_sha256"]
    assert record["render_sha256"] == "b" * 64


def test_a_judge_that_measures_nothing_is_not_measured_rather_than_passed():
    """The defect: a criterion nobody judged counts as a pass, and the
    brief is satisfied by a judge that said nothing."""
    def _silent(brief, render):
        return {"judge": "quiet", "criteria": []}
    record = judgement.assess(task_id="t", brief=_brief(), render=_render(),
                              judge=_silent)
    assert record["editorial"]["status"] == "not_measured"
    assert record["editorial"]["score"] is None
    assert record["editorial"]["not_measured"] == 2


def test_two_judges_that_agree_are_reported_as_agreeing():
    """The defect: a panel with no agreement measure, so a single
    opinion reads as a consensus."""
    first = judgement.assess(task_id="t", brief=_brief(), render=_render(),
                              judge=_judge({}, name="editor one"))
    second = judgement.assess(task_id="t", brief=_brief(), render=_render(),
                               judge=_judge({}, name="editor two"))
    report = judgement.panel([first, second])
    assert report["judge_count"] == 2
    assert report["verdict"] == "pass"
    assert report["agreement_rate"] == 1.0
    assert report["disagreements"] == []


def test_two_judges_that_disagree_establish_nothing():
    """The defect: one judge says pass and the other says fail, and the
    panel averages them into a half-credit."""
    first = judgement.assess(
        task_id="t", brief=_brief(), render=_render(),
        judge=_judge({"hook-first": "pass"}, name="editor one"))
    second = judgement.assess(
        task_id="t", brief=_brief(), render=_render(),
        judge=_judge({"hook-first": "fail"}, name="editor two"))
    report = judgement.panel([first, second])
    assert report["verdict"] == "disagreement"
    assert report["criteria"]["hook-first"]["status"] == "disagreement"
    assert report["disagreements"][0]["id"] == "hook-first"
    assert report["agreement_rate"] == 0.5


def test_a_panel_that_agrees_on_a_failure_is_a_failure():
    """The defect: a criterion both judges failed is softened because
    the panel wants a number."""
    first = judgement.assess(
        task_id="t", brief=_brief(), render=_render(),
        judge=_judge({"hook-first": "fail"}, name="editor one"))
    second = judgement.assess(
        task_id="t", brief=_brief(), render=_render(),
        judge=_judge({"hook-first": "fail"}, name="editor two"))
    report = judgement.panel([first, second])
    assert report["verdict"] == "fail"
