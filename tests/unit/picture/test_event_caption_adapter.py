"""Unit tests for the Gemma-window-to-event adapter (SEM-023/024)."""

import pytest

from library.tools.event_caption_adapter import (
    ADAPTER_REVISION,
    build_evaluation_report,
    build_prediction_doc,
    compare_modes,
    gemma_windows_to_events,
)

MODEL = {"model_id": "mlx-community/gemma-4-12b-it-4bit",
         "model_revision": "test-rev",
         "prompt_id": "compact-test"}


def _window(start, end, actions, frames=20):
    return {"window": [start, end],
            "actions": actions,
            "sampling": {"window_s": end - start,
                         "frames": frames,
                         "decode_fps": 2.0,
                         "effective_fps": 2.0}}


def test_one_event_per_action_with_evidence():
    windows = [_window(0.0, 10.0, [
        {"start": 1.2, "end": 2.7, "action": "lifts cup",
         "speech_cue": None, "body_language": "leans forward"},
    ])]
    events, stats = gemma_windows_to_events(windows, source_id="src-a",
                                            **MODEL)
    assert stats["actions"] == 1
    (event,) = events
    assert event["source_id"] == "src-a"
    assert event["interval"] == {"start_seconds": 1.2,
                                 "end_seconds": 2.7}
    assert event["action"] == "lifts cup"
    (evidence,) = event["evidence"]
    assert evidence["source_id"] == "src-a"
    assert all(1.2 <= t < 2.7
               for t in evidence["frame_times_seconds"])
    assert evidence["frame_times_seconds"] == [1.5, 2.0, 2.5]
    assert (evidence["frame_time_basis"]
            == "reconstructed_uniform_plan")
    assert event["provenance"]["adapter_revision"] == ADAPTER_REVISION


def test_scene_prose_is_never_an_event():
    windows = [_window(0.0, 10.0, [], frames=20)]
    windows[0]["scene"] = [{"start": 0.0, "end": 10.0,
                            "location": "kitchen"}]
    windows[0]["camera"] = [{"start": 0.0, "end": 10.0,
                             "mode": "handheld"}]
    events, _ = gemma_windows_to_events(windows, source_id="src-a",
                                        **MODEL)
    assert events == []


def test_missing_actions_key_is_not_an_empty_answer():
    window = _window(0.0, 10.0, [])
    del window["actions"]
    events, stats = gemma_windows_to_events([window],
                                            source_id="src-a", **MODEL)
    assert events == []
    assert stats["actions"] == 0


def test_invalid_actions_are_dropped_and_counted():
    windows = [_window(0.0, 10.0, [
        {"start": 5.0, "end": 4.0, "action": "inverted"},
        {"start": 1.0, "end": 2.0, "action": "  "},
        "not-a-dict",
        {"start": 1.0, "end": 2.0, "action": "nods"},
    ])]
    events, stats = gemma_windows_to_events(windows, source_id="src-a",
                                            **MODEL)
    assert [e["action"] for e in events] == ["nods"]
    assert stats["dropped_invalid"] == 3


def test_event_without_in_span_frame_keeps_empty_evidence():
    windows = [_window(0.0, 10.0, [
        {"start": 1.21, "end": 1.29, "action": "blinks"},
    ], frames=20)]
    events, stats = gemma_windows_to_events(windows, source_id="src-a",
                                            **MODEL)
    (event,) = events
    assert event["evidence"] == []
    assert stats["no_in_span_frame"] == 1


def test_adapter_output_round_trips_through_prediction_doc():
    windows = [_window(0.0, 10.0, [
        {"start": 1.0, "end": 3.0, "action": "lifts cup",
         "speech_cue": None, "body_language": "still"},
    ])]
    events, _ = gemma_windows_to_events(windows, source_id="src-a",
                                        **MODEL)
    doc = build_prediction_doc(events)
    assert doc["format"] == "ren_event_caption_events/1"
    assert doc["kind"] == "predictions"
    assert doc["events"] == events


def test_report_builder_refuses_held_out_selection_basis():
    with pytest.raises(ValueError):
        build_evaluation_report(
            version_id="v1", role="baseline", models={"vision": "m@r"},
            split="calibration", metrics={}, evidence=[],
            revision="r1", selection_basis=["held_out"])


def _scored_docs():
    def event(event_id, action, start, end, frames):
        return {
            "event_id": event_id, "source_id": "src-a",
            "interval": {"start_seconds": start, "end_seconds": end},
            "action": action, "entities": [], "relations": [],
            "evidence": ([{
                "source_id": "src-a",
                "interval": {"start_seconds": start,
                             "end_seconds": end},
                "frame_times_seconds": frames}] if frames else []),
            "provenance": {"method": "model"},
        }
    references = {"format": "ren_event_caption_events/1",
                  "kind": "references",
                  "events": [event("r1", "lifts cup", 1.0, 3.0, None)]}
    baseline = {"format": "ren_event_caption_events/1",
                "kind": "predictions",
                "events": [event("b1", "lifts cup", 1.0, 3.0, [1.5, 2.5])]}
    candidate = {"format": "ren_event_caption_events/1",
                 "kind": "predictions",
                 "events": [event("c1", "lifts cup", 1.5, 2.5, [2.0]),
                            event("c2", "opens door", 5.0, 6.0, [5.5])]}
    return references, baseline, candidate


def test_compare_modes_reports_deltas_and_cost_ratios():
    references, baseline, candidate = _scored_docs()
    result = compare_modes(
        references, baseline, candidate,
        baseline_run={"wall_seconds": 100.0, "peak_memory_gb": 8.0},
        candidate_run={"wall_seconds": 25.0, "peak_memory_gb": 8.0})
    assert (result["score_deltas_candidate_minus_baseline"]
            ["event_recall"] == 0.0)
    assert (result["score_deltas_candidate_minus_baseline"]
            ["unsupported_claim_rate"] == pytest.approx(0.5))
    assert result["latency_ratio_candidate_over_baseline"] == pytest.approx(
        0.25)


def test_compare_modes_refuses_out_of_scope_sources():
    references, baseline, candidate = _scored_docs()
    candidate["events"][0]["source_id"] = "elsewhere"
    candidate["events"][0]["evidence"][0]["source_id"] = "elsewhere"
    with pytest.raises(ValueError):
        compare_modes(
            references, baseline, candidate,
            baseline_run={}, candidate_run={})
