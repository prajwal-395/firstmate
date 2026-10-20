"""The source-quality profile: one named aggregate over measured evidence.

The defect these tests name, one at a time:

- a dimension no lane measured read as a DEFAULT (0.0, 0.5, absent
  without a reason), so a filter could not tell "measured clean" from
  "never measured";
- a quality number that drifted from the evidence contract's registry,
  so a unit or a kind could be wrong;
- the index's flat facets and the profile disagreeing, because two
  modules reduced the same signal;
- a span that took the sample falling past its end, because one reader
  selected samples by index and another by their own timestamp;
- a whole-source statistic published as if it described the span;
- a profile that grew a verdict, which is taste filed as a measurement.

The measurements here are synthetic shapes the lanes emit - no media, no
models, no project. Every test builds its project under `tmp_path`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import evidence_contract
from library.tools import source_quality


def _image_defects(**signals):
    return {
        "method": "image_defects_v1",
        "sampling": {"rate_hz": 5, "sample_size": [320, 180],
                     "sample_count": 3},
        "signals": signals,
    }


def _sharpness(values):
    return {"sharpness_proxy_laplacian_variance": values}


def test_an_unmeasured_dimension_is_absent_with_a_reason_not_a_default():
    profile = source_quality.profile_over_span(
        image_defects=_image_defects(**_sharpness([100.0])),
        start_seconds=0.0, end_seconds=0.2)
    focus = profile["dimensions"]["focus"]
    assert focus["status"] == "measured"
    assert focus["measures"][0]["value"] == 100.0
    # Every dimension the lanes did not measure says so, and says why.
    for name in ("exposure", "motion", "framing", "noise", "intelligibility",
                 "compression"):
        dimension = profile["dimensions"][name]
        assert dimension["status"] == "absent"
        assert dimension["measures"] == []
        assert dimension["absent_reason"]
    assert "noise" in profile["absent_dimensions"]
    assert "focus" in profile["measured_dimensions"]


def test_a_dimension_with_no_measurement_never_invents_a_number():
    profile = source_quality.profile_over_span(start_seconds=0.0,
                                                end_seconds=1.0)
    assert profile["measured_dimensions"] == []
    for name in source_quality.DIMENSIONS:
        assert profile["dimensions"][name]["measures"] == []
    assert source_quality.facets_of(profile) == {}


def test_every_measure_is_typed_by_the_evidence_contract_registry():
    profile = source_quality.profile_over_span(
        image_defects=_image_defects(
            **_sharpness([100.0, 200.0]),
            **{"temporal_noise": {"values": [0.01, 0.03]},
               "compression_blockiness": {"values": [0.02, 0.04]},
               "clipping_underexposed_fraction": {"values": [0.0, 0.5]},
               "clipping_overexposed_fraction": {"values": [0.01, 0.0]}}),
        temporal_index={
            "optical_flow_direction": {"method": "farneback"},
            "camera_motion_decomposition": {
                "sample_rate_hz": 5,
                "values": [{"time": 0.2, "translation_x": 0.3,
                            "translation_y": 0.4, "subject_energy": 0.1},
                           {"time": 0.4, "translation_x": 0.0,
                            "translation_y": 0.0, "subject_energy": 0.5}]}},
        audio_quality={
            "method": "m11-v2",
            "channels": [{"snr_db": 18.5, "duration_seconds": 2.0,
                          "intervals": [{"start_seconds": 0.0,
                                         "end_seconds": 1.0,
                                         "speech_band_energy_fraction": 0.4}]}]},
        start_seconds=0.0, end_seconds=0.4)
    seen = 0
    for name in source_quality.DIMENSIONS:
        for measure in profile["dimensions"][name]["measures"]:
            entry = evidence_contract.registry_entry(
                measure["measurement_type"])
            assert measure["unit"] == entry["unit"]
            assert measure["kind"] == entry["kind"].value
            assert measure["kind"] == "measurement"
            assert measure["certainty"] == "measured"
            assert measure["method"]
            assert measure["lane"]
            seen += 1
    assert seen >= 8


def test_the_facets_the_index_filters_on_come_from_the_profile():
    profile = source_quality.profile_over_span(
        image_defects=_image_defects(
            **_sharpness([100.0, 200.0, 300.0]),
            **{"temporal_noise": {"values": [0.01, 0.03]},
               "compression_blockiness": {"values": [0.02, 0.04, 0.06]}}),
        start_seconds=0.0, end_seconds=0.4)
    facets = source_quality.facets_of(profile)
    assert facets == {
        "sharpness_laplacian_variance": 150.0,
        "temporal_noise_residual": 0.02,
        "compression_blockiness_proxy": 0.03,
    }
    # Every published facet is a number the profile actually measured.
    for name, value in facets.items():
        assert any(measure["value"] == value
                   for dimension in profile["dimensions"].values()
                   for measure in dimension["measures"])


def test_a_span_does_not_take_the_sample_falling_past_its_end():
    """The motion reading selects samples by their own timestamp.

    The old index-side reduction cut the sample array by INDEX, so a span
    ending at 0.4 s took the sample at 0.4 s - a moment outside the span.
    The owner module selects by time, and the profile uses it.
    """
    index = {
        "optical_flow_direction": {"method": "farneback"},
        "camera_motion_decomposition": {
            "sample_rate_hz": 5,
            "values": [{"time": 0.2, "translation_x": 1.0,
                        "translation_y": 0.0, "subject_energy": 0.0},
                       {"time": 0.4, "translation_x": 9.0,
                        "translation_y": 0.0, "subject_energy": 0.0}]},
    }
    profile = source_quality.profile_over_span(
        temporal_index=index, start_seconds=0.0, end_seconds=0.4)
    translation = profile["dimensions"]["motion"]["measures"][0]
    assert translation["value"] == 1.0
    assert translation["samples"] == 1


def test_a_whole_source_statistic_carries_the_source_interval_not_the_span():
    profile = source_quality.profile_over_span(
        audio_quality={"method": "m11-v2",
                       "channels": [{"snr_db": 18.5, "duration_seconds": 90.0}]},
        start_seconds=10.0, end_seconds=12.0)
    snr = profile["dimensions"]["intelligibility"]["measures"][0]
    assert snr["measurement_type"] == "audio.snr_db"
    assert snr["start_seconds"] == 0.0
    assert snr["end_seconds"] == 90.0


def test_the_framing_reading_comes_from_the_module_that_owns_it():
    """`subject_framing` decides which samples count; the profile says so.

    A track with too few detections is UNMEASURABLE, and the profile must
    say that rather than publish the one detection it did find.
    """
    index = {"face_presence": {
        "sample_rate_hz": 5,
        "values": [1.0, 1.0, 1.0],
        "face_center_x": [0.2, 0.2, 0.2],
        "face_width": [0.1, 0.1, 0.1]}}
    profile = source_quality.profile_over_span(
        temporal_index=index, start_seconds=0.0, end_seconds=0.8)
    framing = profile["dimensions"]["framing"]
    assert framing["status"] == "absent"
    assert framing["absent_reason"] == "too_few_detections"

    index["face_presence"]["face_center_x"] = [0.2] * 12
    index["face_presence"]["face_width"] = [0.1] * 12
    index["face_presence"]["values"] = [1.0] * 12
    profile = source_quality.profile_over_span(
        temporal_index=index, start_seconds=0.0, end_seconds=2.4)
    framing = profile["dimensions"]["framing"]
    assert framing["status"] == "measured"
    assert framing["measures"][0]["value"] == 0.2
    assert source_quality.facets_of(profile)["subject_center_x"] == 0.2


def test_a_profile_carries_no_verdict_and_no_threshold():
    """A profile is measurement. What passes or fails is a versioned
    evaluation policy, and a threshold written here would be taste
    dressed as a measurement."""
    profile = source_quality.profile_over_span(
        image_defects=_image_defects(**_sharpness([0.0, 0.0, 0.0])),
        start_seconds=0.0, end_seconds=0.6)
    forbidden = ("verdict", "usable", "grade", "threshold", "pass",
                 "fail", "recommend", "rating", "score")
    for name, dimension in profile["dimensions"].items():
        assert not set(dimension) & set(forbidden)
        for measure in dimension["measures"]:
            assert not set(measure) & set(forbidden)
            assert isinstance(measure["value"], (int, float))
    # Every dimension's status is one of the two words, and a dimension
    # with measures is measured while one without is absent.
    for name, dimension in profile["dimensions"].items():
        assert dimension["status"] in ("measured", "absent")
        assert bool(dimension["measures"]) == (dimension["status"] == "measured")


def test_an_empty_span_is_refused_rather_than_read_as_a_clean_profile():
    with pytest.raises(source_quality.SourceQualityError):
        source_quality.profile_over_span(start_seconds=1.0, end_seconds=1.0)
    with pytest.raises(source_quality.SourceQualityError):
        source_quality.profile_over_span(start_seconds=2.0, end_seconds=1.0)
    with pytest.raises(source_quality.SourceQualityError):
        source_quality.profile_over_span(start_seconds=-1.0, end_seconds=1.0)


def test_an_inference_cannot_be_filed_as_a_profile_measure():
    """A defocus LIKELIHOOD is an inference, and the contract types it so.

    The profile carries measurements. A profile that could hold a
    likelihood would be a place where a guess could pass as evidence.
    """
    with pytest.raises(source_quality.SourceQualityError):
        source_quality._measure(
            "image.defocus_likelihood", 0.5, "some-model", "m3",
            start_seconds=0.0, end_seconds=1.0)


# ── The index reads the profile, and publishes the facets from it ────


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_the_index_publishes_the_facets_the_profile_measured(tmp_path):
    """One reading: the segment's facets ARE the profile's measures."""
    from library.tools.analysis import footage_segments

    root = tmp_path / "proj"
    steps = root / "pipeline_output" / "steps"
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": "A.MOV",
             "source_file": str(root / "raw" / "A.MOV"),
             "path": str(root / "raw" / "A.MOV"),
             "duration_seconds": 4.0}]}}})
    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001", "duration": 4.0,
        "speech_regions": [{"start": 0.0, "end": 2.0, "text": "hello there"}],
    })
    segments = footage_segments.build_segments(str(root))
    assert segments
    for segment in segments:
        profile = segment.quality_profile
        assert profile["format"] == "source_quality/1"
        assert profile["start_seconds"] == segment.start
        assert profile["end_seconds"] == segment.end
        for name, value in source_quality.facets_of(profile).items():
            assert segment.facets[name] == value


def test_a_quality_bound_fails_closed_when_the_measurement_is_missing(
        tmp_path):
    """An unmeasured SNR is not evidence that the audio is good enough."""
    from library.tools.analysis import footage_query

    index_dir = tmp_path / "index"
    index_dir.mkdir(parents=True)
    _write(index_dir / footage_query.SEGMENTS_FILE, {
        "segments": [{"segment_id": "clip_001#speech#000",
                      "clip_id": "clip_001", "filename": "A.MOV",
                      "kind": "speech", "start": 0.0, "end": 1.0,
                      "duration": 1.0, "timecode": "00:00:00-00:00:01",
                      "text": "hello", "facets": {}}]})
    index = footage_query.FootageIndex(str(tmp_path), index_dir=index_dir)
    assert index.filter(min_snr_db=0.0) == []
    index.segments[0]["facets"]["snr_db"] = 12.0
    assert [row["segment_id"] for row in index.filter(min_snr_db=10.0)] == [
        "clip_001#speech#000"]
    assert index.filter(min_snr_db=20.0) == []

