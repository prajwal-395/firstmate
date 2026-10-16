from library.tools.analysis.ocr_combined_query import combined_query
from library.tools.analysis.ocr_temporal import (
    detect_changes,
    frame_changed,
    link_detections,
    plan_skips,
    region_changed_fraction,
)


def test_link_keeps_one_track_across_stable_frames():
    frames = [
        {"at_seconds": 0.0, "detections": [
            {"text": "SCUFFLEWA", "bbox": [0.1, 0.1, 0.3, 0.1]}]},
        {"at_seconds": 1.0, "detections": [
            {"text": "scufflewa", "bbox": [0.11, 0.1, 0.3, 0.1]}]},
    ]
    tracks = link_detections(frames)
    assert len(tracks) == 1
    assert tracks[0].span_start_seconds == 0.0
    assert tracks[0].span_end_seconds == 1.0


def test_change_events_name_appear_and_disappear():
    prev = [{"text": "HELLO", "bbox": [0, 0, 0.2, 0.1]}]
    curr = [{"text": "WORLD", "bbox": [0.7, 0.7, 0.2, 0.1]}]
    events = detect_changes(prev, curr, at_seconds=2.0)
    kinds = {event.kind for event in events}
    assert kinds == {"appeared", "disappeared"}


def test_changed_text_at_same_box_is_changed_event():
    box = [0.1, 0.1, 0.3, 0.1]
    events = detect_changes([{"text": "$5.00", "bbox": box}],
                            [{"text": "$7.00", "bbox": box}],
                            at_seconds=1.0)
    assert [event.kind for event in events] == ["changed"]


def test_skip_plan_runs_first_frame_and_changes():
    plan = plan_skips([True, False, False, True])
    assert plan.run_index == [0, 3]
    assert plan.saved_passes == 2


def test_frame_changed_fails_open_without_signal():
    assert frame_changed(10.0, 10.0, None) is True


def test_region_signal_measures_identical_frames_as_stable():
    try:
        import numpy as np
    except ImportError:
        return
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    signal = region_changed_fraction(frame, frame.copy())
    assert signal is not None
    assert frame_changed(signal["prev_mean"], signal["curr_mean"],
                         signal["changed_fraction"]) is False


def test_combined_query_joins_speech_and_screen():
    speech = [{"segment_id": "s1", "clip_id": "c1", "text": "our price is five",
               "start_seconds": 10.0, "end_seconds": 12.0}]
    tracks = [{"track_id": "text-0001", "clip_id": "c1", "text": "$5.00",
               "appearances": [[10.5, 13.0]],
               "bbox_normalized": [0.1, 0.8, 0.2, 0.1]}]
    rows = combined_query(speech, tracks, said="price", shown="$5.00")
    assert len(rows) == 1
    assert rows[0]["overlap_seconds"] > 0
    assert rows[0]["text_provenance"]["track_id"] == "text-0001"


def test_combined_query_requires_overlap_by_default():
    speech = [{"segment_id": "s1", "clip_id": "c1", "text": "price five",
               "start_seconds": 0.0, "end_seconds": 1.0}]
    tracks = [{"track_id": "text-0001", "clip_id": "c1", "text": "$5.00",
               "appearances": [[10.0, 11.0]]}]
    assert combined_query(speech, tracks, said="price", shown="$5.00") == []
