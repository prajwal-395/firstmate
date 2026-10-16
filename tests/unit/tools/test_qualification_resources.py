"""Fast contract tests for the qualification resource recorder.

No model, media, or Resolve here: phases are trivial callables, so these run
in the ordinary unit suite on every code change.
"""

from __future__ import annotations

import pytest

from library.tools import model_qualification as qualification
from library.tools.qualification_resources import (
    PhaseTimings,
    ResourceMeasurementError,
    ResourceSample,
    measure_implementation,
    peak_rss_mb,
    time_phase,
)


def _sample(**overrides):
    fields = {
        "cold_start_ms": 1200.0,
        "steady_state_ms": 800.0,
        "phases": PhaseTimings(
            decode_preprocess_ms=100.0,
            inference_ms=500.0,
            postprocess_serialize_ms=200.0,
            end_to_end_ms=800.0,
        ),
        "peak_rss_mb_value": 2048.0,
        "memory_pressure": "normal",
        "resolve_open_concurrency": "passed",
    }
    fields.update(overrides)
    return ResourceSample(**fields)


def test_record_block_matches_qualification_gate_resource_fields():
    block = _sample().to_record_block()
    missing = {
        "cold_start_ms", "steady_state_ms", "decode_preprocess_ms",
        "inference_ms", "postprocess_serialize_ms", "end_to_end_ms",
        "peak_rss_mb", "memory_pressure", "cpu_utilization_pct",
        "gpu_utilization_pct", "ane_utilization_pct",
        "throughput_source_hours_per_wall_hour",
        "useful_evidence_per_compute_second", "resolve_open_concurrency",
    } - set(block)
    assert not missing


def test_record_block_passes_gate_resource_shape_check():
    block = _sample(
        throughput_source_hours_per_wall_hour=1.5,
        useful_evidence_per_compute_second=0.5,
    ).to_record_block()
    record = {
        "format": "ren_model_qualification/1",
        "revision": "test",
        "capability": "visual.detect",
        "hardware": {"machine": "M5 Pro", "unified_memory_gb": 24,
                      "os_revision": "test", "resolve_revision": "test"},
        "dataset": {"manifest_id": "x", "manifest_path": "x",
                    "manifest_sha256": "0" * 64, "splits": ["calibration"],
                    "inputs_deterministic": True},
        "baseline": {"id": "b", "adapter_id": "a", "model_revision": "r",
                     "publisher_revision": "p", "report_ids": []},
        "candidate": {"id": "c", "adapter_id": "a", "model_revision": "r",
                      "publisher_revision": "p", "report_ids": []},
        "comparison": {"resources": {"baseline": block, "candidate": block}},
    }
    try:
        qualification.evaluate_record(record)
    except qualification.QualificationError as exc:
        # The gate must reach report verification (later) rather than refuse
        # the resource block shape produced by this recorder.
        assert "resources." not in str(exc), str(exc)
    else:  # pragma: no cover - shape-valid records fail later without reports
        raise AssertionError("expected report verification to follow")


def test_end_to_end_cannot_hide_phase_cost():
    with pytest.raises(ResourceMeasurementError, match="end_to_end_ms"):
        _sample(phases=PhaseTimings(
            decode_preprocess_ms=100.0, inference_ms=500.0,
            postprocess_serialize_ms=200.0, end_to_end_ms=799.0))


def test_unmeasured_stays_explicit():
    block = _sample(memory_pressure="unknown",
                    resolve_open_concurrency="not_run").to_record_block()
    assert block["memory_pressure"] == "unknown"
    assert block["resolve_open_concurrency"] == "not_run"
    assert block["cpu_utilization_pct"] is None


def test_utilization_out_of_range_refused():
    with pytest.raises(ResourceMeasurementError, match="between 0 and 100"):
        _sample(cpu_utilization_pct=101.0)


def test_measure_implementation_derives_rates():
    sample = measure_implementation(
        load_model=None,
        decode_preprocess=lambda: None,
        infer=lambda: "evidence",
        postprocess_serialize=lambda: None,
        source_seconds=3600.0,
        useful_evidence_count=10,
        memory_pressure="normal",
        resolve_open_concurrency="not_run",
    )
    assert sample.cold_start_ms is None
    assert sample.steady_state_ms == pytest.approx(
        sample.phases.decode_preprocess_ms
        + sample.phases.inference_ms
        + sample.phases.postprocess_serialize_ms)
    assert sample.throughput_source_hours_per_wall_hour is not None
    assert sample.throughput_source_hours_per_wall_hour > 0
    assert sample.useful_evidence_per_compute_second is not None
    assert sample.useful_evidence_per_compute_second >= 0
    assert sample.to_record_block()["peak_rss_mb"] == pytest.approx(
        peak_rss_mb(), rel=0.5)


def test_measure_implementation_times_cold_load():
    calls = []

    def _load():
        calls.append("load")

    sample = measure_implementation(
        load_model=_load,
        decode_preprocess=lambda: None,
        infer=lambda: None,
        postprocess_serialize=lambda: None,
        source_seconds=60.0,
        useful_evidence_count=0,
    )
    assert calls == ["load"]
    assert sample.cold_start_ms is not None
    assert sample.cold_start_ms >= 0


def test_time_phase_returns_wall_ms_and_result():
    elapsed_ms, result = time_phase(lambda: 42)
    assert result == 42
    assert elapsed_ms >= 0


def test_negative_source_seconds_refused():
    with pytest.raises(ResourceMeasurementError, match="source_seconds"):
        measure_implementation(
            load_model=None,
            decode_preprocess=lambda: None,
            infer=lambda: None,
            postprocess_serialize=lambda: None,
            source_seconds=-1.0,
            useful_evidence_count=0,
        )
