"""FI-07 keeps activity lanes separate and evidence coverage explicit."""

import pytest

from library.tools.content_activity import build_content_activity_map


def test_absent_coverage_is_unknown_not_quiet_activity():
    result = build_content_activity_map(2.0)

    assert len(result["bins"]) == 2
    for row in result["bins"]:
        for lane in ("novelty", "motion", "scene", "audio", "speech"):
            signal = row["signals"][lane]
            assert signal["coverage_status"] == "not_analyzed"
            assert signal["coverage_fraction"] == 0
            assert signal["value"] is None
            assert signal["normalized"] is None
        assert row["signals"]["uncertainty"]["value"] == 1.0


def test_existing_fi14_fi12_measurements_are_joined_without_collapsing_lanes():
    result = build_content_activity_map(
        2.0,
        motion_evidence={"pairs": [
            {"start": 0, "end": 1, "status": "measured", "change": 0.2, "novelty": 0.01},
            {"start": 1, "end": 2, "status": "measured", "change": 8.0, "novelty": 0.8},
        ]},
        audio_quality={"channels": [{"intervals": [
            {"start_seconds": 0, "end_seconds": 1, "rms_dbfs": -40},
            {"start_seconds": 1, "end_seconds": 2, "rms_dbfs": -10},
        ]}]},
        scene_evidence={"intervals": [{"start": 0, "end": 1, "value": 0.1},
                                     {"start": 1, "end": 2, "value": 0.9}]},
        speech_evidence={"intervals": [{"start": 0, "end": 1, "value": 0.0},
                                       {"start": 1, "end": 2, "value": 1.0}]},
    )

    first, second = result["bins"]
    assert first["signals"]["novelty"]["value"] == 0.01
    assert second["signals"]["motion"]["value"] == 8.0
    assert first["signals"]["audio"]["value"] == pytest.approx(1 / 3)
    assert first["signals"]["speech"]["normalized"] < second["signals"]["speech"]["normalized"]
    assert first["signals"]["scene"]["value"] != first["signals"]["motion"]["value"]
    assert all(first["signals"][lane]["coverage_status"] == "measured"
               for lane in ("novelty", "motion", "audio", "scene", "speech"))


def test_partial_upstream_motion_coverage_stays_partial():
    result = build_content_activity_map(
        2.0,
        motion_evidence={"pairs": [
            {"start": 0, "end": 1, "status": "measured", "change": 2, "novelty": 0.2},
            {"start": 1, "end": 2, "status": "unmeasured", "change": None, "novelty": None},
        ]},
    )

    assert result["bins"][0]["signals"]["motion"]["coverage_status"] == "measured"
    assert result["bins"][1]["signals"]["motion"]["coverage_status"] == "not_analyzed"
    assert result["bins"][1]["signals"]["motion"]["value"] is None


def test_overlapping_measurement_intervals_do_not_fake_complete_coverage():
    result = build_content_activity_map(
        1.0,
        scene_evidence={
            "intervals": [
                {"start": 0, "end": 0.6, "value": 0.2},
                {"start": 0.4, "end": 0.8, "value": 0.4},
            ]
        },
    )

    signal = result["bins"][0]["signals"]["scene"]
    assert signal["coverage_fraction"] == 0.8
    assert signal["coverage_status"] == "partial"
