from library.tools import project_model_acquisition
from library.tools.temporal_action_candidates import (
    candidates_from_model,
    detect_named_object_in_intervals,
    nominate_hand_motion_intervals,
    query_status,
)


def _model():
    model = {
        "entities": [{"id": "obj-1", "kind": "object", "label": "cup",
                      "object_class_id": "class-cup"}],
        "tracks": [{"id": "track-1", "entity_id": "obj-1",
                    "clock_id": "clock-1", "samples": [
                        {"tick": 1000, "geometry": {"bbox_pixels": [400, 400, 500, 500]}},
                        {"tick": 1500, "geometry": {"bbox_pixels": [400, 350, 500, 450]}},
                        {"tick": 2000, "geometry": {"bbox_pixels": [400, 400, 500, 500]}},
                    ]}],
        "observations": [
            {"id": "object-1", "modality": "object_track_sample",
             "interval": {"clock_id": "clock-1", "start": 1000, "end": 1500},
             "payload": {"track_id": "track-1", "coordinate_space": "source_pixels_1000x1000"}},
            {"id": "object-2", "modality": "object_track_sample",
             "interval": {"clock_id": "clock-1", "start": 1500, "end": 2000},
             "payload": {"track_id": "track-1", "coordinate_space": "source_pixels_1000x1000"}},
            {"id": "object-3", "modality": "object_track_sample",
             "interval": {"clock_id": "clock-1", "start": 2000, "end": 2500},
             "payload": {"track_id": "track-1", "coordinate_space": "source_pixels_1000x1000"}},
            {"id": "pose-1", "modality": "face_frame",
             "interval": {"clock_id": "clock-1", "start": 1000, "end": 1001},
             "payload": {"face": {"outer_lips": [[.45, .45]]},
                         "hands": [{"joints": {"wrist": [.45, .45, .9]}}]}},
            {"id": "pose-2", "modality": "face_frame",
             "interval": {"clock_id": "clock-1", "start": 1500, "end": 1501},
             "payload": {"face": {"outer_lips": [[.45, .4]]},
                         "hands": [{"joints": {"wrist": [.45, .4, .9]}}]}},
            {"id": "pose-3", "modality": "face_frame",
             "interval": {"clock_id": "clock-1", "start": 2000, "end": 2001},
             "payload": {"face": {"outer_lips": [[.45, .45]]},
                         "hands": [{"joints": {"wrist": [.45, .45, .9]}}]}},
        ],
    }
    return model


def test_drink_candidate_cites_measurements_and_stays_unverified():
    [event] = candidates_from_model(_model())
    assert event["kind"] == "drink_from_cup"
    assert event["status"] == "candidate"
    assert event["certainty"] == "inferred"
    assert event["evidence_ids"] == ["object-1", "object-2", "object-3",
                                     "pose-1", "pose-2", "pose-3"]
    assert event["interval"] == {"clock_id": "clock-1", "start": 1000, "end": 2001}


def test_drink_candidate_requires_ordered_raise_and_lower_transition():
    model = _model()
    model["tracks"][0]["samples"][2]["geometry"]["bbox_pixels"] = [
        400, 350, 500, 450]
    assert candidates_from_model(model) == []


def test_pick_up_put_down_uses_ordered_raise_and_lower_transition():
    model = _model()
    model["tracks"][0]["samples"][2]["geometry"]["bbox_pixels"] = [
        650, 400, 750, 500]
    [event] = candidates_from_model(model, rule="pick_up_put_down")
    assert event["kind"] == "pick_up_put_down"


def test_pick_up_candidate_requires_stable_rest_then_lift_and_cites_state_change():
    model = _model()
    model["tracks"][0]["samples"] = [
        {"tick": tick, "geometry": {"bbox_pixels": [400, 400, 500, 500]}}
        for tick in (1000, 1500, 2000)
    ] + [
        {"tick": 2500, "geometry": {"bbox_pixels": [400, 300, 500, 400]}}
    ]
    model["observations"] = [
        {"id": f"object-{index}", "modality": "object_track_sample",
         "interval": {"clock_id": "clock-1", "start": tick,
                      "end": tick + 500},
         "payload": {"track_id": "track-1",
                     "coordinate_space": "source_pixels_1000x1000"}}
        for index, tick in enumerate((1000, 1500, 2000, 2500), 1)
    ] + [
        {"id": f"pose-{tick}", "modality": "face_frame",
         "interval": {"clock_id": "clock-1", "start": tick,
                      "end": tick + 1},
         "payload": {"hands": [{"joints": {"wrist": [.45, .35, .9]}}]}}
        for tick in (2000, 2500)
    ]
    [event] = candidates_from_model(model, rule="pick_up")
    assert event["kind"] == "pick_up"
    assert event["status"] == "candidate"
    assert event["state_changes"] == [{
        "entity_id": "obj-1", "property": "motion_state",
        "from": "at_rest", "to": "lifted",
        "evidence_ids": ["object-1", "object-2", "object-3",
                         "object-4", "pose-2000", "pose-2500"],
    }]


def test_hand_object_contact_is_a_measured_candidate_with_phases():
    [event] = candidates_from_model(_model(), rule="hand_object_contact")
    assert event["kind"] == "hand_object_contact"
    assert event["status"] == "candidate"
    assert event["phases"]["execution"] == {"start": 1000, "end": 2001}
    assert event["phases"]["preparation"] is None
    assert event["phases"]["completion"] is None


def test_pointing_uses_sustained_index_extension_without_inventing_target():
    model = {"entities": [], "tracks": [], "observations": [
        {"id": f"pose-{tick}", "modality": "face_frame",
         "interval": {"clock_id": "clock-1", "start": tick, "end": tick + 1},
         "payload": {"hands": [{"joints": {
             "wrist": [.2, .2, .9], "indexTip": [.5, .2, .9]}}]}}
        for tick in (1000, 1500, 3000)
    ]}
    [event] = candidates_from_model(model, rule="pointing")
    assert event["kind"] == "pointing"
    assert event["evidence_ids"] == ["pose-1000", "pose-1500"]
    assert event["participant_ids"] == []
    assert event["certainty"] == "inferred"


def test_hand_to_face_requires_repeated_measured_landmark_contact():
    model = {"entities": [], "tracks": [], "observations": [
        {"id": f"pose-{tick}", "modality": "face_frame",
         "interval": {"clock_id": "clock-1", "start": tick,
                      "end": tick + 1},
         "payload": {"face": {"box": [.4, .2, .6, .5],
                               "outer_lips": [[.5, .4]]},
                     "hands": [{"joints": {"wrist": [x, .4, .9]}}]}}
        for tick, x in ((1000, .52), (1500, .54), (2000, .9))
    ]}
    [event] = candidates_from_model(model, rule="hand_to_face")
    assert event["kind"] == "hand_to_face"
    assert event["evidence_ids"] == ["pose-1000", "pose-1500"]


def test_unknown_rule_is_refused():
    try:
        candidates_from_model(_model(), rule="magic")
    except ValueError as exc:
        assert "unknown temporal action rule" in str(exc)
    else:
        raise AssertionError("unknown rule was accepted")


def test_incomplete_interval_is_not_reported_absent():
    assert query_status(analysed_intervals=[{"start": 1000, "end": 1500}],
                        start_tick=1000, end_tick=2000) == "not-yet-analysed"
    assert query_status(analysed_intervals=[{"start": 1000, "end": 2000}],
                        start_tick=1000, end_tick=2000) == "analysed"


def test_l0_motion_nominates_intervals_without_deciding_action():
    frames = [
        {"t": 1.0, "hands": [{"joints": {"wrist": [.1, .1, .9]}}]},
        {"t": 1.5, "hands": [{"joints": {"wrist": [.3, .1, .9]}}]},
    ]
    [interval] = nominate_hand_motion_intervals(frames)
    assert interval["start_s"] == .5
    assert interval["end_s"] == 2.0
    assert "action" not in interval


def test_named_object_detection_runs_only_on_nominated_frames():
    calls = []

    class Box:
        box, score, detector = [.2, .3, .4, .5], .9, "owlv2"

    result = detect_named_object_in_intervals(
        [{"start_s": 1, "end_s": 2}], frames_by_interval=[["frame-a"]],
        object_name="cup",
        detect_image=lambda *args: (calls.append(args) or [Box()]))
    assert len(calls) == 1
    assert calls[0][0:2] == ("owlv2", "frame-a")
    assert calls[0][2] == ["cup"]
    assert result[0]["score"] == .9


def test_verification_sends_only_bounded_candidate_intervals(monkeypatch, tmp_path):
    from library.tools import person_measurements, span_verification

    monkeypatch.setattr(person_measurements, "read_m3", lambda *_args: {
        "frames": [{"t": .9}, {"t": 1.0}, {"t": 1.5}, {"t": 2.0}, {"t": 2.1}]})
    sent = {}

    def verify(digest, spans, statement, **kwargs):
        sent.update(digest=digest, spans=spans, statement=statement,
                    root=kwargs["root"])
        return [{"answer": "yes"} for _ in spans]

    monkeypatch.setattr(span_verification, "verify_spans", verify)
    candidate = {"id": "candidate-1", "kind": "drink_from_cup",
                 "evidence_ids": ["a", "b"],
                 "interval": {"clock_id": "c", "start": 1000, "end": 2000}}
    original = {**candidate, "interval": dict(candidate["interval"])}
    result = project_model_acquisition.verify_action_candidates(
        "d" * 64, [candidate], max_candidates=1, memory_root=tmp_path)
    assert result[0]["candidate_id"] == "candidate-1"
    assert len(sent["spans"][0]["frames"]) == 3
    assert sent["statement"] == "drink from a cup"
    assert sent["root"] == tmp_path
    assert candidate == original
