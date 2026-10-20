"""Typed temporal events: what happened, when, and how sure.

The defect each test names:

* a vocabulary that drifts - an unknown event type or an unknown action
  class is accepted, so a renamed type reads as a working one;
* a boundary whose uncertainty is invented, so a coarse edge passes as
  a sharp one;
* an event whose class is borrowed from its neighbour, so text that
  names no action reads as SOME action;
* a repeat that is not numbered, so "the second time" has no answer;
* a STATIC look-alike reported as an occurrence, so a frame that only
  looks like the action is offered as the action;
* an unanalysed span reported as static, so "we did not look" reads as
  "nothing happened";
* a verification that passes on missing evidence or on a boundary too
  coarse for its type, so a cut is approved on a guess;
* an untyped Project Model event kind guessed into a type, so an
  unknown event is presented as a known one.
"""

import json

import pytest

from library.tools import edit_critical_events, temporal_events
from library.tools.temporal_events import Boundary, TemporalEvent


def _event(**overrides):
    base = {
        "event_id": "clip_001:action_state_change:000",
        "event_type": "action_state_change",
        "action": "opening",
        "action_text": "she opens the box",
        "asset_id": "clip:clip_001",
        "clip_id": "clip_001",
        "source_file": "/tmp/clip_001.mp4",
        "clock_id": "clock:clip_001",
        "start_seconds": 1.0,
        "end_seconds": 3.0,
        "onset": Boundary(1.0, "estimated", 0.05, "measured"),
        "intermediate_states": (),
        "completion": Boundary(3.0, "estimated", 0.05, "measured"),
        "status": "candidate",
        "certainty": "inferred",
    }
    base.update(overrides)
    return TemporalEvent(**base)


# ── The vocabularies fail loudly ───────────────────────────────────


def test_an_unknown_event_type_is_refused():
    """A renamed event type must fail at the record, not read as a
    working one: a type outside the edit-critical vocabulary has no
    fidelity requirement, so nothing could verify it."""
    with pytest.raises(ValueError) as caught:
        _event(event_type="gesture")
    assert "gesture" in str(caught.value)


def test_an_unknown_action_class_is_refused():
    with pytest.raises(ValueError) as caught:
        _event(action="box_opening")
    assert "box_opening" in str(caught.value)


def test_an_exact_boundary_cannot_carry_uncertainty():
    """An edge that claims to be exact and also claims a radius is
    claiming two different things."""
    with pytest.raises(ValueError) as caught:
        Boundary(1.0, "exact", 0.2, "measured")
    assert "uncertainty" in str(caught.value)


def test_an_unranked_event_type_is_refused():
    """An event type with no edit priority cannot be ranked, and a
    silent default would rank it as if it were a cut point."""
    with pytest.raises(KeyError) as caught:
        temporal_events.event_priority("gesture")
    assert "gesture" in str(caught.value)


def test_every_edit_critical_type_has_an_edit_priority():
    """The priority table and the vocabulary are two lists about the
    same events; one missing a row is a type the machinery cannot
    rank."""
    assert sorted(temporal_events.EVENT_PRIORITY) == sorted(
        edit_critical_events.event_types())


# ── Classification never borrows a class ───────────────────────────


def test_text_that_names_no_action_classifies_as_none():
    """Nothing happened is not "opening": an unclassifiable event says
    it could not be classified rather than wearing a class."""
    action, basis = temporal_events.classify_action("she sits by the window")
    assert action is None
    assert "no lexicon trigger" in basis


def test_the_longer_trigger_wins_over_a_shorter_one():
    """"covers her mouth" is one action, not a shorter trigger that
    starts inside it."""
    action, _ = temporal_events.classify_action("she covers her mouth")
    assert action == "hand_to_mouth"


def test_a_classification_is_stable_for_the_same_text():
    action, _ = temporal_events.classify_action("she opens the box")
    again, _ = temporal_events.classify_action("she opens the box")
    assert action == again == "opening"


# ── Repetition is numbered (VI-014) ────────────────────────────────


def test_repeats_are_numbered_and_a_unique_event_is_not():
    """The second time is the second time: three openings in one clip
    are numbered 1, 2 and 3."""
    first = _event(event_id="a", start_seconds=1.0, end_seconds=2.0)
    second = _event(event_id="b", start_seconds=5.0, end_seconds=6.0)
    third = _event(event_id="c", start_seconds=9.0, end_seconds=10.0)
    numbered = temporal_events.assign_occurrences([third, first, second])
    assert [event.occurrence for event in numbered] == [1, 2, 3]
    assert numbered[0].repeat_group_id == (
        "clip_001:action_state_change:opening")


def test_an_event_with_no_action_class_is_not_numbered():
    """Two events of the same type that cannot be told apart are not
    "the first" and "the second" of anything: a number here would be a
    lie about which repetition it names."""
    first = _event(event_id="a", action=None, start_seconds=1.0,
                   end_seconds=2.0)
    second = _event(event_id="b", action=None, start_seconds=5.0,
                    end_seconds=6.0)
    numbered = temporal_events.assign_occurrences([first, second])
    assert [event.occurrence for event in numbered] == [None, None]


# ── Motion separates an occurrence from a look-alike (VI-016) ───────


def _index_with_subject_energy(samples):
    """A 1.04 index whose optical-flow decomposition carries subject
    energy at the given ``(seconds, energy)`` points."""
    return {"camera_motion_decomposition": {
        "sample_rate_hz": 5,
        "values": [{"time": time, "subject_energy": energy,
                    "residual": energy}
                   for time, energy in samples],
    }}


def test_a_span_where_the_subject_moved_is_an_occurrence():
    reading = temporal_events.read_motion(
        _index_with_subject_energy([(1.0, 0.0), (1.5, 0.4)]), 1.0, 3.0)
    assert reading.status == "motion_measured"
    assert reading.sample_count == 2


def test_a_span_where_only_the_camera_moved_is_a_static_look_alike():
    """The frame looks like the action because the camera moved, not
    because anything was done.  It must not be offered as the
    occurrence."""
    reading = temporal_events.read_motion(
        _index_with_subject_energy([(1.0, 0.0), (1.5, 0.0)]), 1.0, 3.0)
    assert reading.status == "static_measured"


def test_a_span_the_motion_pass_never_analysed_is_unmeasured_not_static():
    """Absence of analysis is not absence of motion: reporting it as
    static would claim the action did not happen."""
    reading = temporal_events.read_motion(
        _index_with_subject_energy([(1.0, 0.4)]), 5.0, 6.0)
    assert reading.status == "unmeasured"


def test_a_clip_with_no_motion_decomposition_is_unmeasured():
    reading = temporal_events.read_motion(None, 1.0, 2.0)
    assert reading.status == "unmeasured"


# ── Intermediate states are the measured ones (VI-012) ──────────────


def test_only_the_motion_peaks_inside_the_span_are_intermediate_states():
    index_doc = {
        "motion_energy": {
            "sample_rate_hz": 10,
            "peak_motion_times": [0.5, 1.5, 4.0],
            "high_motion_times": [],
        },
    }
    states = temporal_events.read_intermediate_states(index_doc, 1.0, 3.0)
    assert [state.seconds for state in states] == [1.5]
    assert states[0].uncertainty_seconds == pytest.approx(0.05)


# ── Verification refuses what it cannot support (VI-017) ────────────


def test_a_boundary_coarser_than_its_type_tolerates_is_uncertain():
    """A scene transition must be located to a frame.  An edge located
    to half a second cannot ground a cut on it."""
    event = _event(
        event_type="scene_transition",
        onset=Boundary(1.0, "estimated", 0.5, "measured"),
        completion=Boundary(1.5, "estimated", 0.5, "measured"))
    verdict = temporal_events.verify_event(
        event, {"video.scene_boundary": [1]})
    assert verdict["status"] == "uncertain"
    assert any("exceeds tolerance" in reason for reason in verdict["reasons"])


def test_missing_required_evidence_is_uncertain():
    event = _event(
        event_type="scene_transition",
        onset=Boundary(1.0, "estimated", 0.01, "measured"),
        completion=Boundary(1.03, "estimated", 0.01, "measured"))
    verdict = temporal_events.verify_event(event, {})
    assert verdict["status"] == "uncertain"
    assert any("missing required evidence" in reason
               for reason in verdict["reasons"])


def test_a_well_measured_event_with_its_evidence_verifies():
    event = _event(
        event_type="scene_transition",
        onset=Boundary(1.0, "estimated", 0.01, "measured"),
        completion=Boundary(1.03, "estimated", 0.01, "measured"))
    verdict = temporal_events.verify_event(
        event, {"video.scene_boundary": [1]})
    assert verdict["status"] == "verified"
    assert verdict["reasons"] == []


def test_an_event_type_whose_evidence_no_measurement_type_carries_is_uncertain():
    """A technical defect's declared evidence - a soft picture range and
    camera stability - is measured by NOTHING in the evidence contract.
    The event must say so rather than verify against a neighbouring
    measurement that proves a different claim."""
    event = _event(
        event_type="technical_defect",
        onset=Boundary(1.0, "estimated", 0.01, "measured"),
        completion=Boundary(3.0, "estimated", 0.01, "measured"))
    verdict = temporal_events.verify_event(
        event, {"image.sharpness.laplacian_variance": [1]})
    assert verdict["status"] == "uncertain"
    assert any("no measurement type carries" in reason
               for reason in verdict["reasons"])


def test_verifying_against_the_model_reads_the_event_s_own_clock():
    """A cut on a scene transition is verified against the measurements
    on THAT shot's clock, and against nothing else: another shot's
    scene boundary must not satisfy it."""
    model = {"observations": [
        {"id": "obs_1",
         "interval": {"clock_id": "clock:clip_001", "start": 0, "end": 100},
         "modality": "scene_boundary",
         "evidence": {"measurement_type": "video.scene_boundary"}},
        {"id": "obs_2",
         "interval": {"clock_id": "clock:other", "start": 0, "end": 100},
         "modality": "scene_boundary",
         "evidence": {"measurement_type": "video.scene_boundary"}},
    ]}
    event = _event(event_type="scene_transition",
                   onset=Boundary(1.0, "estimated", 0.01, "measured"),
                   completion=Boundary(1.03, "estimated", 0.01, "measured"))
    verdict = temporal_events.verify_event_in_model(event, model)
    assert verdict["status"] == "verified"
    assert verdict["evidence_on_clock"] == {"video.scene_boundary": 1}


def test_a_clock_with_no_measurement_of_the_required_kind_is_uncertain():
    model = {"observations": [
        {"id": "obs_1",
         "interval": {"clock_id": "clock:clip_001", "start": 0, "end": 100},
         "modality": "face_frame",
         "evidence": {"measurement_type": "person.face_frame"}},
    ]}
    event = _event(event_type="scene_transition",
                   onset=Boundary(1.0, "estimated", 0.01, "measured"),
                   completion=Boundary(1.03, "estimated", 0.01, "measured"))
    verdict = temporal_events.verify_event_in_model(event, model)
    assert verdict["status"] == "uncertain"
    assert any("missing required evidence" in reason
               for reason in verdict["reasons"])


def test_an_observation_with_no_evidence_contract_contributes_nothing():
    """A row that carries no contract has no measurement type, and
    guessing one would verify a different claim."""
    model = {"observations": [
        {"id": "obs_1",
         "interval": {"clock_id": "clock:clip_001", "start": 0, "end": 100},
         "modality": "scene_boundary"},
    ]}
    event = _event(event_type="scene_transition",
                   onset=Boundary(1.0, "estimated", 0.01, "measured"),
                   completion=Boundary(1.03, "estimated", 0.01, "measured"))
    verdict = temporal_events.verify_event_in_model(event, model)
    assert verdict["evidence_on_clock"] == {}
    assert verdict["status"] == "uncertain"


# ── An untyped kind is reported, not guessed ───────────────────────


def test_an_untyped_project_model_event_kind_is_reported_not_guessed():
    """A kind the table does not name has no known type.  It must be
    REPORTED, never presented as a known event."""
    model = {
        "clocks": [{"id": "clock_1", "ticks_per_second": 1000,
                    "domain": {"kind": "source", "asset_id": "asset_1",
                               "stream_id": "stream_1"}}],
        "assets": [{"id": "asset_1", "kind": "video", "uri": "/tmp/a.mp4"}],
        "entities": [],
        "observations": [],
        "tracks": [],
        "events": [{"id": "event_1",
                    "interval": {"clock_id": "clock_1", "start": 0, "end": 500},
                    "kind": "brand_new_predicate",
                    "participant_ids": [], "status": "candidate",
                    "certainty": "inferred", "evidence_ids": []}],
    }
    events, unmapped = temporal_events._model_events(model)
    assert events == []
    assert unmapped == ["brand_new_predicate"]


def test_a_typed_project_model_event_names_its_participants():
    model = {
        "clocks": [{"id": "clock_1", "ticks_per_second": 1000,
                    "domain": {"kind": "source", "asset_id": "asset_1",
                               "stream_id": "stream_1"}}],
        "assets": [{"id": "asset_1", "kind": "video", "uri": "/tmp/a.mp4"}],
        "entities": [{"id": "person_1", "kind": "person", "label": "Ana"},
                     {"id": "object_1", "kind": "object", "label": "box"}],
        "observations": [],
        "tracks": [{"id": "track_1", "entity_id": "person_1",
                    "clock_id": "clock_1", "samples": []}],
        "events": [{"id": "event_1",
                    "interval": {"clock_id": "clock_1", "start": 0, "end": 2500},
                    "kind": "object_pickup",
                    "participant_ids": ["person_1", "object_1"],
                    "status": "verified", "certainty": "inferred",
                    "evidence_ids": ["obs_1"]}],
    }
    events, unmapped = temporal_events._model_events(model)
    assert unmapped == []
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "action_state_change"
    assert event.action == "pick_up"
    assert event.subjects == ("person_1",)
    assert event.subject_labels == ("Ana",)
    assert event.objects == ("object_1",)
    assert event.start_seconds == pytest.approx(0.0)
    assert event.end_seconds == pytest.approx(2.5)


# ── A subject tracked across shots (VI-018) ────────────────────────


def test_a_subject_tracked_in_two_shots_is_one_subject_across_the_cut():
    """Cross-shot continuity is what lets an edit say "the same person".
    A subject tracked in two shots carries that; one tracked in one
    shot does not, and says so rather than claiming either way."""
    shots = {"person_1": ("clock_1", "clock_2")}
    reading = temporal_events.read_continuity(shots, "person_1")
    assert reading.status == "cross_shot"
    assert reading.shots == ("clock_1", "clock_2")


def test_a_subject_tracked_in_one_shot_is_not_claimed_to_continue():
    reading = temporal_events.read_continuity({"person_1": ("clock_1",)},
                                               "person_1")
    assert reading.status == "single_shot"
    assert reading.shots == ("clock_1",)


def test_an_entity_with_no_track_is_untracked_not_continued():
    reading = temporal_events.read_continuity({}, "person_1")
    assert reading.status == "untracked"


def test_a_project_model_event_carries_its_subject_continuity():
    model = {
        "clocks": [{"id": "clock_1", "ticks_per_second": 1000,
                    "domain": {"kind": "source", "asset_id": "asset_1",
                               "stream_id": "stream_1"}},
                   {"id": "clock_2", "ticks_per_second": 1000,
                    "domain": {"kind": "source", "asset_id": "asset_2",
                               "stream_id": "stream_2"}}],
        "assets": [{"id": "asset_1", "kind": "video", "uri": "/tmp/a.mp4"},
                   {"id": "asset_2", "kind": "video", "uri": "/tmp/b.mp4"}],
        "entities": [{"id": "person_1", "kind": "person", "label": "Ana"}],
        "observations": [],
        "tracks": [{"id": "track_1", "entity_id": "person_1",
                    "clock_id": "clock_1", "samples": []},
                   {"id": "track_2", "entity_id": "person_1",
                    "clock_id": "clock_2", "samples": []}],
        "events": [{"id": "event_1",
                    "interval": {"clock_id": "clock_1", "start": 0, "end": 2500},
                    "kind": "object_pickup",
                    "participant_ids": ["person_1"],
                    "status": "verified", "certainty": "inferred",
                    "evidence_ids": ["obs_1"]}],
    }
    events, _unmapped = temporal_events._model_events(model)
    assert events[0].continuity.status == "cross_shot"
    assert events[0].continuity.shots == ("clock_1", "clock_2")


# ── The index reads the project's own material ─────────────────────


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project(tmp_path):
    """A project with one clip's 1.02 catalog, 1.03 vision profile,
    1.04 temporal index and 1.07 OCR tracks."""
    root = tmp_path / "project"
    _write_json(root / "pipeline_output" / "steps" / "1_02_catalog_footage"
                / "output.json",
                {"clip_catalog": [{"clip_id": "clip_001",
                                   "source_file": "/tmp/clip_001.mp4",
                                   "filename": "clip_001.mp4",
                                   "duration_seconds": 20.0,
                                   "frame_rate_nominal": 30.0}]})
    _write_json(root / "pipeline_output" / "steps" / "1_04_temporal_index"
                / "index" / "clip_001.json",
                {"clip_id": "clip_001",
                 "scene_boundaries": [{"time": 5.0, "score": 0.6}],
                 "sound_events": [{"label": "Door slam", "start": 2.0,
                                   "end": 2.5, "confidence": 0.9}],
                 "speech_regions": [{"start": 1.0, "end": 3.0,
                                     "text": "hello there"}],
                 "camera_motion_decomposition": {
                     "sample_rate_hz": 5,
                     "values": [{"time": 1.0, "subject_energy": 0.0},
                                {"time": 1.5, "subject_energy": 0.0},
                                {"time": 2.0, "subject_energy": 0.0}]},
                 "motion_energy": {"sample_rate_hz": 10,
                                   "peak_motion_times": [1.5],
                                   "high_motion_times": []}})
    _write_json(root / "pipeline_output" / "steps" / "1_03_semantic_analysis"
                / "clip_profile_clip_001.json",
                {"file_path": "/tmp/clip_001.mp4",
                 "actions": [{"window": [1.0, 2.0],
                              "actions": [{"start": 1.0, "end": 2.0,
                                           "action": "she opens the box",
                                           "body_language": "leans in"}]}],
                 "objects": [{"label": "Ana", "role": "primary_subject",
                              "category": "person",
                              "appearances": [[0.0, 4.0]]}]})
    _write_json(root / "pipeline_output" / "steps" / "1_07_ocr_extraction"
                / "clip_001" / "ocr_result.json",
                {"detections": [{"track_id": "text-0000", "text": "OPEN",
                                 "appearance_intervals": [
                                     {"start_seconds": 6.0,
                                      "end_seconds": 7.5}]}]})
    return root


def test_the_index_reads_every_typed_event_the_project_carries(tmp_path):
    index = temporal_events.build_temporal_events(str(_project(tmp_path)))
    by_type = {}
    for event in index.events:
        by_type.setdefault(event.event_type, []).append(event)
    assert set(by_type) == {"scene_transition", "audio_event",
                            "speech_start_end", "action_state_change",
                            "onscreen_text_change"}
    assert by_type["scene_transition"][0].certainty == "measured"
    assert by_type["action_state_change"][0].certainty == "inferred"
    assert by_type["action_state_change"][0].action == "opening"
    assert by_type["action_state_change"][0].subject_labels == ("ana",)
    assert by_type["onscreen_text_change"][0].action_text == "OPEN"


def test_the_index_reports_a_static_look_alike_as_static(tmp_path):
    """The synthetic action sits over a span whose subject energy is
    zero: it must read as a look-alike, not as the occurrence."""
    index = temporal_events.build_temporal_events(str(_project(tmp_path)))
    actions = [event for event in index.events
               if event.event_type == "action_state_change"]
    assert actions[0].motion.status == "static_measured"


def test_building_the_index_creates_no_state_in_the_project(tmp_path):
    """Reading the index must not create a Project Model in a project
    that has none: the index is derived, so it can never disagree with
    the footage it describes."""
    root = _project(tmp_path)
    temporal_events.build_temporal_events(str(root))
    assert not (root / "project_model").exists()
