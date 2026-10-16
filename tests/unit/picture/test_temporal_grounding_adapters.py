"""Unit tests for temporal-grounding adapters (no models, no footage)."""

import pytest

from library.tools import temporal_grounding_adapters as tga


def test_parse_interval_pairs_accepts_valid():
    assert tga.parse_interval_pairs([[1.0, 2.5], [3, 4]], duration_seconds=10.0) == [
        [1.0, 2.5], [3.0, 4.0]]


def test_parse_interval_pairs_rejects_bad_shapes():
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.parse_interval_pairs({"start": 1}, duration_seconds=10.0)
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.parse_interval_pairs([[1.0]], duration_seconds=10.0)
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.parse_interval_pairs([["a", 2.0]], duration_seconds=10.0)


def test_parse_interval_pairs_rejects_out_of_range():
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.parse_interval_pairs([[-1.0, 2.0]], duration_seconds=10.0)
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.parse_interval_pairs([[2.0, 2.0]], duration_seconds=10.0)
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.parse_interval_pairs([[9.0, 11.0]], duration_seconds=10.0)


def test_record_malformed_never_repairs():
    record = tga.record_malformed(
        query="someone waves", raw_text="no json here", reason="no array")
    assert record["intervals"] == []
    assert record["repaired"] is False


def test_challenger_proposals_default_to_detection_not_run():
    rows = tga.challenger_intervals_to_proposals(
        [[1.0, 2.0]], source_id="LCATL0012", event_class="waving",
        proposal_method="timelens2_4b:abc123")
    assert len(rows) == 1
    row = rows[0]
    assert row["stage"] == "event_detection"
    assert row["verifier"]["decision"] == "not_run"
    assert "refines_proposal_id" not in row


def test_challenger_proposals_reject_unknown_class():
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.challenger_intervals_to_proposals(
            [[1.0, 2.0]], source_id="s", event_class="dancing",
            proposal_method="m")


def test_challenger_refinement_requires_parent():
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.challenger_intervals_to_proposals(
            [[1.1, 1.9]], source_id="s", event_class="reach",
            proposal_method="m", stage="boundary_refinement")
    rows = tga.challenger_intervals_to_proposals(
        [[1.1, 1.9]], source_id="s", event_class="reach",
        proposal_method="m", stage="boundary_refinement",
        refines_proposal_id="s:m:00000")
    assert rows[0]["refines_proposal_id"] == "s:m:00000"


def test_incumbent_spans_carry_predicate_method():
    rows = tga.incumbent_spans_to_proposals(
        [{"start": 1.0, "end": 2.0, "face_track": "track-1"}],
        source_id="LCATL0012", predicate="hand_near_mouth",
        event_class="contact")
    assert rows[0]["proposal_method"] == "m7_geometry:hand_near_mouth"
    assert rows[0]["identity_id"] == "track-1"
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.incumbent_spans_to_proposals(
            [{"start": 2.0, "end": 1.0}], source_id="s",
            predicate="p", event_class="contact")


def _run_record(**overrides):
    params = {
        "model_id": "MCG-NJU/TimeLens2-4B",
        "publisher_revision": "f5dbceb56fb3060c16bfb24bb9d84bf8902dc0ac",
        "precision": "bf16",
        "license_class": "research_only",
        "query": "someone waves",
        "source_id": "IMG_1816",
        "duration_seconds": 188.578333,
    }
    params.update(overrides)
    return tga.new_run_record(**params)


def test_run_record_rejects_unknown_license():
    with pytest.raises(tga.TemporalGroundingAdapterError):
        _run_record(license_class="apache-2.0")


def test_close_run_record_needs_exactly_one_outcome():
    record = _run_record()
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.close_run_record(record, decode_seconds=1.0, infer_seconds=2.0,
                             serialize_seconds=0.1,
                             intervals=[[1.0, 2.0]],
                             malformed={"reason": "x"})
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.close_run_record(record, decode_seconds=1.0, infer_seconds=2.0,
                             serialize_seconds=0.1)


def test_close_run_record_sums_end_to_end():
    record = tga.close_run_record(
        _run_record(), decode_seconds=1.0, infer_seconds=2.0,
        serialize_seconds=0.5, intervals=[[1.0, 2.0]], device="mps")
    assert tga.run_end_to_end_seconds(record) == pytest.approx(3.5)
    assert record["peak_memory_gb"] is not None


def test_end_to_end_null_when_leg_unmeasured():
    record = tga.close_run_record(
        _run_record(), decode_seconds=None, infer_seconds=2.0,
        serialize_seconds=0.5, intervals=[])
    assert tga.run_end_to_end_seconds(record) is None


def test_metrics_carry_dimensions_without_quality():
    record = tga.close_run_record(
        _run_record(), decode_seconds=1.0, infer_seconds=2.0,
        serialize_seconds=0.5, intervals=[])
    metrics = tga.to_evaluation_metrics(record)
    assert metrics["end_to_end_seconds"]["dimension"] == "latency"
    assert metrics["peak_memory_gb"]["dimension"] == "memory"
    assert not any(m["dimension"] == "quality" for m in metrics.values())


def test_metrics_include_quality_only_when_labels_exist():
    record = tga.close_run_record(
        _run_record(), decode_seconds=1.0, infer_seconds=2.0,
        serialize_seconds=0.5, intervals=[])
    metrics = tga.to_evaluation_metrics(record, quality={"t_iou_mean": 0.6})
    assert metrics["t_iou_mean"] == {
        "value": 0.6, "unit": "ratio", "dimension": "quality"}


def test_build_report_refuses_held_out_selection_basis():
    with pytest.raises(tga.TemporalGroundingAdapterError):
        tga.build_evaluation_report(
            version_id="v", role="candidate", models={"m": "r"},
            split="calibration", metrics={}, evidence=[], revision="1",
            selection_basis=["held_out"])
    report = tga.build_evaluation_report(
        version_id="v", role="candidate", models={"m": "r"},
        split="calibration", metrics={}, evidence=[], revision="1",
        selection_basis=["calibration"])
    assert report["format"] == "ren_evaluation_report/1"


def test_detection_refinement_delta_reports_nulls():
    delta = tga.detection_refinement_delta(
        {"predictions": 3, "temporal_iou_mean": 0.6,
         "boundary_error_mean_seconds": 0.4},
        {"predictions": 0, "temporal_iou_mean": None,
         "boundary_error_mean_seconds": None})
    assert delta["refinement_predictions"] == 0
    assert delta["deltas_refinement_minus_detection"][
        "temporal_iou_mean"] is None


def test_commercial_eligibility_is_fail_closed():
    assert tga.commercial_eligibility(
        "research_only", True)["eligible_for_commercial"] is False
    assert tga.commercial_eligibility(
        "quarantine", True)["eligible_for_commercial"] is False
    assert tga.commercial_eligibility(
        "commercial", None)["eligible_for_commercial"] is False
    assert tga.commercial_eligibility(
        "commercial", False)["eligible_for_commercial"] is False
    assert tga.commercial_eligibility(
        "commercial", True)["eligible_for_commercial"] is True


def test_timed_block_measures():
    with tga.timed_block() as timer:
        pass
    assert timer.seconds is not None and timer.seconds >= 0
