from library.tools import edit_continuity as ec
from library.tools import shot_reject as sr

import pytest


def test_soft_ranges_become_reject_reasons():
    reasons = sr.from_soft_ranges(
        [{"start": 1.0, "end": 2.0, "reason": "soft_picture"}],
        evidence_id="pq1")
    assert reasons == [{"reason": "soft_picture", "start_seconds": 1.0,
                        "end_seconds": 2.0, "evidence_id": "pq1"}]


def test_unmeasurable_softness_converts_to_nothing():
    assert sr.from_soft_ranges(None) == []
    assert sr.from_soft_ranges([]) == []


def test_only_unstable_rejects():
    assert sr.from_stability_label("stable", "optical_flow_residual") == []
    assert sr.from_stability_label("handheld", "motion_energy_std") == []
    assert sr.from_stability_label("unknown", "unmeasured") == []
    reasons = sr.from_stability_label("unstable", "optical_flow_residual",
                                      clip_start=0.0, clip_end=10.0,
                                      evidence_id="cs1")
    assert len(reasons) == 1
    assert "unstable_camera" in reasons[0]["reason"]


def test_reject_keep_and_unmeasured_are_distinct():
    intervals = [
        {"interval_id": "soft", "start_seconds": 1.0, "end_seconds": 2.0},
        {"interval_id": "fine", "start_seconds": 5.0, "end_seconds": 6.0},
        {"interval_id": "dark", "start_seconds": 9.0, "end_seconds": 10.0},
    ]
    reasons = sr.from_soft_ranges(
        [{"start": 1.0, "end": 2.0, "reason": "soft_picture"}],
        evidence_id="pq1")
    coverage = [{"start_seconds": 0.0, "end_seconds": 7.0}]
    verdicts = sr.reject_intervals(intervals, reasons,
                                   measured_coverage=coverage)
    by_id = {v["interval_id"]: v for v in verdicts}
    assert by_id["soft"]["verdict"] == "reject"
    assert by_id["soft"]["evidence_ids"] == ["pq1"]
    assert by_id["fine"]["verdict"] == "keep"
    assert by_id["dark"]["verdict"] == "unmeasured"


def test_continuity_surviving_tracks_are_continuous():
    tracks = [
        {"track_id": "host", "label": "person:host",
         "spans": [{"start_seconds": 0.0, "end_seconds": 10.0}]},
    ]
    verdict = ec.check_track_continuity(5.0, tracks)
    assert verdict["status"] == "continuous"
    assert [t["track_id"] for t in verdict["surviving"]] == ["host"]


def test_clean_exit_is_cutaway_safe_not_broken():
    tracks = [
        {"track_id": "cup", "label": "object:cup",
         "spans": [{"start_seconds": 0.0, "end_seconds": 4.9}]},
    ]
    verdict = ec.check_track_continuity(5.0, tracks, window_seconds=1.0)
    assert verdict["status"] == "cutaway_safe"
    assert [t["track_id"] for t in verdict["lost"]] == ["cup"]


def test_lost_track_beside_survivor_breaks_continuity():
    tracks = [
        {"track_id": "host", "label": "person:host",
         "spans": [{"start_seconds": 0.0, "end_seconds": 10.0}]},
        {"track_id": "cup", "label": "object:cup",
         "spans": [{"start_seconds": 0.0, "end_seconds": 4.9}]},
    ]
    verdict = ec.check_track_continuity(5.0, tracks, window_seconds=1.0)
    assert verdict["status"] == "broken"
    assert [t["track_id"] for t in verdict["lost"]] == ["cup"]
    assert [t["track_id"] for t in verdict["surviving"]] == ["host"]


def test_source_timeline_map_verifies_order_and_bounds():
    mappings = [
        {"clip_id": "a", "source_start": 10.0, "source_end": 15.0,
         "timeline_start": 0.0, "timeline_end": 5.0},
        {"clip_id": "b", "source_start": 3.0, "source_end": 7.0,
         "timeline_start": 5.0, "timeline_end": 9.0},
    ]
    assert ec.verify_source_timeline_map(
        mappings, {"a": 60.0, "b": 60.0}) == {"valid": True, "problems": []}


def test_source_timeline_map_refuses_overlap_and_overrun():
    mappings = [
        {"clip_id": "a", "source_start": 10.0, "source_end": 15.0,
         "timeline_start": 0.0, "timeline_end": 6.0},
        {"clip_id": "b", "source_start": 50.0, "source_end": 70.0,
         "timeline_start": 5.0, "timeline_end": 9.0},
    ]
    verdict = ec.verify_source_timeline_map(mappings, {"a": 60.0,
                                                       "b": 60.0})
    assert not verdict["valid"]
    assert any("overlaps" in problem for problem in verdict["problems"])
    assert any("outside clip" in problem for problem in verdict["problems"])


def test_av_sync_bound_is_one_frame_by_default():
    assert ec.verify_av_sync(10.0, 15.0, 10.0, 15.0)["in_sync"]
    broken = ec.verify_av_sync(10.0, 15.0, 10.1, 15.0)
    assert not broken["in_sync"]
    assert broken["start_offset_seconds"] == pytest.approx(-0.1)
