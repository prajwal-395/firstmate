"""FI-08 scheduling keeps measured event recall while exposing gaps."""

from library.tools.semantic_window_planner import (
    activity_map_from_temporal_index,
    plan_semantic_windows,
)


def _activity(duration, event_bins):
    bins = []
    for second in range(duration):
        active = second in event_bins
        value = 1.0 if active else 0.0
        bins.append({
            "start": float(second), "end": float(second + 1),
            "signals": {
                "novelty": {"normalized": value, "coverage_status": "measured"},
                "motion": {"normalized": value, "coverage_status": "measured"},
                "scene": {"normalized": value, "coverage_status": "measured"},
                "audio": {"normalized": value, "coverage_status": "measured"},
                "speech": {"normalized": value, "coverage_status": "measured"},
                "uncertainty": {"value": 0.0, "coverage_status": "derived"},
            },
        })
    return {"bins": bins}


def test_adaptive_windows_retain_sparse_event_recall_against_exhaustive_control():
    # These sparse event spans stand in for weak, measured podcast proxies.
    # They test scheduling recall, not broad action-recognition accuracy.
    events = [15, 45, 75]
    plan = plan_semantic_windows(
        100, _activity(100, events), task="actions", max_fraction=0.7,
        coverage_stride_seconds=30)
    selected = plan["window_starts_s"]
    recovered = sum(
        any(start <= event < start + plan["window_seconds"] for start in selected)
        for event in events)
    control_starts = plan["exhaustive_control"]["window_starts_s"]
    control_recovered = sum(
        any(start <= event < start + plan["window_seconds"]
            for start in control_starts)
        for event in events)

    assert control_recovered == len(events)
    assert recovered / control_recovered >= 0.9
    assert plan["expected_cost"]["model_calls"] < plan["exhaustive_control"]["model_calls"]
    assert plan["coverage"]["fraction"] >= 0.65


def test_missing_upstream_coverage_becomes_refinement_candidates_not_events():
    plan = plan_semantic_windows(80, activity_map_from_temporal_index(80, None))

    assert plan["refinement_candidates"]
    assert all(row["reason"] == "unmeasured_activity"
               for row in plan["refinement_candidates"])
    assert "events" not in plan
    assert plan["unseen_event_policy"].startswith("no interpolation")


def test_temporal_energy_curve_is_a_normalized_audio_activity_proxy():
    activity = activity_map_from_temporal_index(2.0, {
        "energy_curve": {"sample_rate_hz": 2, "values": [0.1, 0.9, 0.2, 0.8]},
    })

    assert activity["bins"][0]["signals"]["audio"]["value"] == 0.5
    assert activity["bins"][0]["signals"]["audio"]["coverage_status"] == "measured"
    assert "not absolute loudness" in activity["audio_activity_source"]


def test_temporal_motion_and_optical_flow_are_accepted_from_published_fields():
    activity = activity_map_from_temporal_index(2.0, {
        "motion_energy": {"sample_rate_hz": 1, "values": [0.1, 0.9]},
        "optical_flow_direction": {"sample_rate_hz": 1,
                                    "values": [{"magnitude": .2},
                                               {"magnitude": .8}]},
    })

    assert activity["bins"][0]["signals"]["motion"]["coverage_status"] == "measured"
    assert activity["bins"][1]["signals"]["motion"]["coverage_status"] == "measured"
    assert activity["bins"][1]["signals"]["novelty"]["coverage_status"] == "measured"
    assert activity["bins"][0]["signals"]["motion"]["value"] == .1


def test_task_profile_changes_window_priority_deterministically():
    activity = _activity(100, [15, 45])
    activity["bins"][15]["signals"]["motion"]["normalized"] = 0.0
    activity["bins"][15]["signals"]["speech"]["normalized"] = 1.0
    activity["bins"][45]["signals"]["motion"]["normalized"] = 1.0
    activity["bins"][45]["signals"]["speech"]["normalized"] = 0.0

    speech = plan_semantic_windows(100, activity, task="speech_visual",
                                   max_fraction=0.5, coverage_stride_seconds=30)
    actions = plan_semantic_windows(100, activity, task="actions",
                                    max_fraction=0.5, coverage_stride_seconds=30)

    assert speech == plan_semantic_windows(
        100, activity, task="speech_visual", max_fraction=0.5,
        coverage_stride_seconds=30)
    assert speech["window_starts_s"] != actions["window_starts_s"]
