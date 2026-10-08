"""Offline assignment QA contract over recorded render measurements."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from library.tools import creative_qa as qa
from library.tools.render_qa import RenderQAResult

INTENT = {"key": "delivery.caption_readability",
          "provenance": "creator brief: captions must be readable"}


def _measurement(metric="caption_legibility", passed=False):
    return RenderQAResult(metric, passed, {"observed": 14}, {"minimum": 18},
                          "error" if not passed else "info", "measured caption height")


def test_report_links_measurement_to_actual_span_intent_and_action():
    report = qa.from_measurements(
        "assignment-1", "render-7", [_measurement()], duration=12,
        intent_by_metric={"caption_legibility": INTENT},
        spans_by_metric={"caption_legibility": {"start": 3.0, "end": 4.2,
                                                "label": "caption card 2"}})
    finding = report.as_dict()["deterministic"][0]
    assert finding["span"] == {"start": 3.0, "end": 4.2, "label": "caption card 2"}
    assert finding["intent"] == INTENT
    assert finding["action"] == "repair_once"
    assert report.blocks_delivery


def test_deterministic_failures_block_but_subjective_calls_go_to_creator():
    report = qa.from_measurements(
        "assignment-1", "render-7", [_measurement("black_frames")],
        duration=12, intent_by_metric={"black_frames": INTENT},
        subjective=[{"metric": "opening_tone", "detail": "The opening feels too abrupt.",
                     "span": {"start": 0, "end": 2, "label": "opening"},
                     "intent": {"key": "story.pacing", "provenance": "intent beat intro"}}])
    encoded = report.as_dict()
    assert encoded["blocks_delivery"] is True
    assert encoded["status"] == "blocked"
    observation = encoded["subjective"][0]
    assert observation["action"] == "ask_creator"
    assert observation["kind"] == "subjective"


def test_clean_recorded_render_passes():
    report = qa.from_measurements(
        "assignment-1", "render-clean", [_measurement("duration", True)],
        duration=12, intent_by_metric={"duration": {"key": "delivery.duration",
                                                      "provenance": "delivery spec"}})
    assert report.as_dict()["status"] == "clean"
    assert report.passed


def test_repair_is_reversible_bounded_and_only_for_measurable_defects():
    report = qa.from_measurements(
        "assignment-1", "render-7", [_measurement()], duration=12,
        intent_by_metric={"caption_legibility": INTENT}, repair_budget=1)
    finding = report.findings[0]
    receipt = qa.attempt_repair(
        report, finding.id,
        lambda row: {"receipt": {"operation": "caption_scale", "span": row.span},
                     "inverse": {"operation": "restore_caption_scale"}})
    assert receipt["finding_id"] == finding.id
    assert report.blocks_delivery
    with pytest.raises(qa.CreativeQAError, match="budget"):
        qa.attempt_repair(report, finding.id,
                          lambda row: {"receipt": {}, "inverse": {}})
    updated = qa.record_repair_measurement(report, finding.id,
                                           _measurement(passed=True))
    assert updated.passed
    assert not report.blocks_delivery

    editorial = qa.from_measurements(
        "assignment-1", "render-8", [_measurement("black_frames")], duration=12,
        intent_by_metric={"black_frames": INTENT})
    with pytest.raises(qa.CreativeQAError, match="creator"):
        qa.attempt_repair(editorial, editorial.findings[0].id,
                          lambda row: {"receipt": {}, "inverse": {}})


def test_report_only_measurements_do_not_become_delivery_gates():
    report = qa.from_measurements(
        "assignment-1", "render-7", [_measurement("grade", False)], duration=12,
        intent_by_metric={"grade": {"key": "picture.grade",
                                     "provenance": "creator brief"}})
    assert not report.blocks_delivery
    assert report.as_dict()["deterministic"][0]["action"] == "report"


def test_deliverable_qc_and_render_review_feed_the_assignment_report(monkeypatch):
    from library.tools import deliverable_qc

    monkeypatch.setattr(deliverable_qc, "run_deliverable_qc", lambda *a, **kw:
                        SimpleNamespace(duration=_measurement("duration", True),
                                        black_frames=_measurement("black_frames", True),
                                        loudness=_measurement("loudness", True),
                                        grade=_measurement("grade", True),
                                        captions=None))
    refs = {name: {"key": f"delivery.{name}", "provenance": "delivery spec"}
            for name in ("duration", "black_frames", "loudness", "grade")}
    report = qa.from_deliverable(
        "assignment-1", "render-7", "fixture.mp4", object(),
        expected_duration=12, intent_by_metric=refs,
        render_review={"overall": "concerns", "notes": [{
            "metric": "opening_tone", "detail": "Opening feels abrupt",
            "span": {"start": 0, "end": 2, "label": "opening"},
            "intent": {"key": "story.pacing", "provenance": "intent_model.intro"},
            "passed": False}]})
    assert len(report.findings) == 5
    assert report.subjective_observations[0].action == "ask_creator"
    assert report.passed is False


def test_findings_require_intent_and_subjective_span():
    with pytest.raises(qa.CreativeQAError, match="intent"):
        qa.from_measurements("a", "r", [_measurement("duration")], duration=12,
                             intent_by_metric={})
    with pytest.raises(qa.CreativeQAError, match="missing"):
        qa.from_measurements("a", "r", [], duration=12, intent_by_metric={},
                             subjective=[{"metric": "tone", "detail": "hmm",
                                          "span": {"start": 0, "end": 1}}])
