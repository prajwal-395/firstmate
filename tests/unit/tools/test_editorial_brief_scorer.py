"""Defect-naming contracts for Level 4 creative-brief scoring."""

import hashlib

from tests.live import editorial_brief_scorer


def _brief():
    return {"acceptance_criteria": [
        {"id": "opening-hook", "statement": "Open with the clearest hook."},
        {"id": "meaning", "statement": "Preserve the speaker's meaning."},
    ]}


def _render():
    return {"sha256": hashlib.sha256(b"render").hexdigest(),
            "evidence": [{"kind": "frame", "artifact": "frame-001.png"}],
            "technical_verification": {"audio_present": True,
                                       "timeline_continuity": True}}


def test_editorial_score_uses_the_brief_and_final_evidence_only():
    observed = []

    def judge(brief, render):
        observed.append((brief, render))
        return {"judge": "independent-panel-v1", "criteria": [
            {"id": "opening-hook", "status": "pass",
             "rationale": "The first shot introduces the central tension.",
             "evidence": [{"artifact": "frame-001.png",
                           "observation": "The speaker begins on the marked hook."}]},
            {"id": "meaning", "status": "fail",
             "rationale": "The cut removes a qualifying phrase.",
             "evidence": [{"artifact": "transcript.json#row-12",
                           "observation": "The qualification is absent."}]},
        ]}

    result = editorial_brief_scorer.score_brief(
        request_id="req-42", brief=_brief(), render=_render(), judge=judge)
    assert observed == [(_brief(), {
        "sha256": _render()["sha256"],
        "evidence": _render()["evidence"],
    })]
    assert result["evaluation_level"] == 4
    assert result["editorial"]["status"] == "fail"
    assert result["editorial"]["score"] == 0.5
    assert result["technical"]["status"] == "pass"


def test_missing_judgment_is_not_measured_not_a_zero_score():
    result = editorial_brief_scorer.score_brief(
        request_id="req-43", brief=_brief(), render=_render(),
        judge=lambda _brief, _render: {
            "judge": "independent-panel-v1", "criteria": []})
    assert result["editorial"]["status"] == "not_measured"
    assert result["editorial"]["score"] is None
    assert result["editorial"]["not_measured"] == 2


def test_duplicate_brief_criteria_refuse_to_produce_a_score():
    brief = {"acceptance_criteria": [
        {"id": "same", "statement": "First."},
        {"id": "same", "statement": "Second."},
    ]}
    try:
        editorial_brief_scorer.score_brief(
            request_id="req-44", brief=brief, render=_render(),
            judge=lambda *_args: {})
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate criterion IDs must be refused")


def test_brief_score_batch_keeps_unmeasured_deliveries_out_of_the_mean():
    def judge(brief, _render):
        criterion = brief["acceptance_criteria"][0]
        return {"judge": "independent-panel-v1", "criteria": [{
            "id": criterion["id"], "status": "pass",
            "rationale": "Visible in the final render.",
            "evidence": [{"artifact": "frame-001.png",
                          "observation": "Criterion satisfied."}],
        }]}

    result = editorial_brief_scorer.score_batch([
        {"request_id": "measured", "brief": {
            "acceptance_criteria": [_brief()["acceptance_criteria"][0]]},
         "render": _render()},
        {"request_id": "missing", "brief": _brief(), "render": {}},
    ], judge)
    assert result["evaluation_level"] == 4
    assert result["outcomes"] == {"pass": 1, "fail": 0, "not_measured": 1}
    assert result["measured_request_scores"] == 1
    assert result["mean_measured_request_score"] == 1.0
