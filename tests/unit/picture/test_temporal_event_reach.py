"""The typed temporal events REACH the footage they describe.

The defect each test names:

* a Project Model event kind the index cannot type, so the model's own
  events - every kind of them, on every real project - are reported as
  unmapped and the typed surface reads as empty;
* a Project Model whose MEASURED observations the index ignores, so a
  model holding a whole measurement (the sound events the temporal lane
  measured, the words the transcriber timed, the voice turns the
  diarizer measured) reads as an empty surface;
* a boundary measured twice, so one cut is listed as two events;
* a sub-shot boundary that reports no state change, so a state
  transition the footage measured is lost;
* a person whose TRACKED presence is never reported, so the index
  cannot say who was in the picture;
* a project whose measurements live in the source memory, so a project
  that carries footage reads as empty because its model was never
  migrated.
"""

import json

import pytest

from library.tools import temporal_events
from library.tools.temporal_events import Boundary, TemporalEvent

DIGEST = "d" * 64
ASSET_ID = f"asset-{DIGEST}"


def _catalog(clip_id: str = "clip_001"):
    return [{"clip_id": clip_id, "frame_rate_nominal": 25.0,
             "duration_seconds": 20.0,
             "source_file": f"/tmp/{clip_id}.mp4"}]


def _model(*, events=(), observations=(), tracks=(), entities=()):
    return {
        "clocks": [{"id": "clock_1", "ticks_per_second": 1000,
                    "domain": {"kind": "source", "asset_id": ASSET_ID,
                               "stream_id": "stream_1"}}],
        "assets": [{"id": ASSET_ID, "kind": "video", "uri": "/tmp/a.mp4",
                    "metadata": {"clip_ids": ["clip_001"]}}],
        "entities": list(entities),
        "observations": list(observations),
        "tracks": list(tracks),
        "events": list(events),
    }


def _cinematography_observation(observation_id: str, level: str,
                                 start: float, end: float,
                                 attributes: dict | None = None):
    """A VI-09 interval observation, as the model carries it."""
    record = {"id": observation_id, "start": start, "end": end,
              "certainty": "interpreted"}
    if attributes is not None:
        record["attributes"] = attributes
    return {"id": observation_id, "asset_id": ASSET_ID,
            "modality": "cinematography_interval",
            "interval": {"clock_id": "clock_1",
                         "start": int(start * 1000), "end": int(end * 1000)},
            "payload": {"level": level, "record": record}}


def _attribute(value: str, evidence: str):
    return {"value": value, "status": "analyzed", "certainty": "interpreted",
            "evidence_ids": [evidence]}


# ── The model's own event kinds are typed ───────────────────────────


def test_the_models_own_cinematography_kind_is_typed_not_reported_unmapped():
    """The model holds a scene, a shot, a sub-shot and a sequence for
    every source it measured.  A kind the table cannot name is
    REPORTED - and on real footage that report is the whole surface."""
    model = _model(
        events=[{
            "id": "event-scene-1",
            "interval": {"clock_id": "clock_1", "start": 0, "end": 5000},
            "kind": "cinematography_scene",
            "participant_ids": [], "status": "verified",
            "certainty": "interpreted", "evidence_ids": ["obs-1"],
            "start_boundary": {"status": "estimated", "uncertainty_ticks": None},
            "end_boundary": {"status": "open", "uncertainty_ticks": None},
        }],
        observations=[_cinematography_observation("obs-1", "scene", 0.0, 5.0)])
    events, unmapped = temporal_events._model_events(model, _catalog())
    assert unmapped == []
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "scene_transition"
    assert event.clip_id == "clip_001"
    assert event.source_file == "/tmp/a.mp4"
    # A scene is a SEGMENT; the cut is its first frame, and the cut's
    # extent is one frame of the source.
    assert event.start_seconds == pytest.approx(0.0)
    assert event.end_seconds == pytest.approx(1 / 25.0)


def test_a_subshot_boundary_reports_the_measured_attribute_that_changed():
    """A sub-shot boundary is where the picture's measured attributes
    changed.  Reporting no change there loses a state transition the
    footage measured."""
    model = _model(
        events=[
            {"id": "event-sub-1",
             "interval": {"clock_id": "clock_1", "start": 0, "end": 2600},
             "kind": "cinematography_subshot", "participant_ids": [],
             "status": "verified", "certainty": "interpreted",
             "evidence_ids": ["obs-1"],
             "start_boundary": {"status": "exact", "uncertainty_ticks": None},
             "end_boundary": {"status": "open", "uncertainty_ticks": None}},
            {"id": "event-sub-2",
             "interval": {"clock_id": "clock_1", "start": 2600, "end": 3000},
             "kind": "cinematography_subshot", "participant_ids": [],
             "status": "verified", "certainty": "interpreted",
             "evidence_ids": ["obs-2"],
             "start_boundary": {"status": "estimated",
                                "uncertainty_ticks": None},
             "end_boundary": {"status": "open", "uncertainty_ticks": None}},
        ],
        observations=[
            _cinematography_observation("obs-1", "subshot", 0.0, 2.6, {
                "framing": _attribute("wide", "measure-1"),
                "camera_movement": _attribute("static", "measure-2")}),
            _cinematography_observation("obs-2", "subshot", 2.6, 3.0, {
                "framing": _attribute("wide", "measure-3"),
                "camera_movement": _attribute("pan", "measure-4")}),
        ])
    events, unmapped = temporal_events._model_events(model, _catalog())
    assert unmapped == []
    by_id = {event.event_id: event for event in events}
    assert by_id["event-sub-2"].event_type == "action_state_change"
    changes = by_id["event-sub-2"].state_changes
    assert len(changes) == 1
    assert changes[0].property == "camera_movement"
    assert changes[0].before == "static"
    assert changes[0].after == "pan"
    # The transition is evidenced by the state it left and the state it
    # reached.
    assert set(changes[0].evidence_ids) == {"measure-2", "measure-4"}
    # The first sub-shot has no before, so it reports no change.
    assert by_id["event-sub-1"].state_changes == ()


# ── The model's MEASURED observations are typed ─────────────────────


def test_the_sound_events_the_temporal_lane_measured_are_typed():
    """Step 1.04's own output is a measurement the model carries.  An
    index that reads only the model's events reports nothing at all."""
    model = _model(observations=[{
        "id": "obs-m5-1", "asset_id": ASSET_ID, "modality": "sound_event",
        "interval": {"clock_id": "clock_1", "start": 1500, "end": 2000},
        "payload": {"label": "Clicking"},
        "confidence_detail": {
            "producer": "library.steps.step_1_04_temporal_index"}}])
    events = temporal_events._model_observation_events(model, _catalog())
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "audio_event"
    assert event.action_text == "Clicking"
    assert event.start_seconds == pytest.approx(1.5)
    assert event.end_seconds == pytest.approx(2.0)
    assert event.evidence_ids == ("obs-m5-1",)
    assert event.certainty == "measured"


def test_a_voice_turn_names_its_speaker_and_the_state_change_it_carries():
    """A diarized turn is the measurement of who spoke.  The speaker is
    the participant, and the turn is the evidence that they took the
    floor."""
    model = _model(
        entities=[{"id": f"speaker-{DIGEST}-voice_001", "kind": "speaker",
                   "label": None}],
        observations=[{
            "id": "obs-m3b-1", "asset_id": ASSET_ID,
            "modality": "voice_turn",
            "interval": {"clock_id": "clock_1", "start": 2000, "end": 4500},
            "payload": {"voice_track": "voice_001"}}])
    events = temporal_events._model_observation_events(model, _catalog())
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "speaker_change"
    assert event.subjects == (f"speaker-{DIGEST}-voice_001",)
    assert len(event.state_changes) == 1
    assert event.state_changes[0].property == "speaking"
    assert event.state_changes[0].before is False
    assert event.state_changes[0].after is True
    assert event.state_changes[0].evidence_ids == ("obs-m3b-1",)


def test_an_utterance_inside_a_turn_is_attributed_to_its_speaker():
    """The turn says who spoke; the utterance says what was said.  The
    join attributes the utterance without a second diarization pass."""
    model = _model(
        entities=[{"id": f"speaker-{DIGEST}-voice_001", "kind": "speaker",
                   "label": None}],
        observations=[
            {"id": "obs-m3b-1", "asset_id": ASSET_ID,
             "modality": "voice_turn",
             "interval": {"clock_id": "clock_1", "start": 2000, "end": 4500},
             "payload": {"voice_track": "voice_001"}},
            {"id": "obs-m1-1", "asset_id": ASSET_ID, "modality": "speech",
             "interval": {"clock_id": "clock_1", "start": 2200, "end": 4000},
             "payload": {"text": "hello there", "utterance_index": 0,
                         "word_count": 2}}])
    events = temporal_events._model_observation_events(model, _catalog())
    utterance = [event for event in events
                 if event.event_type == "speech_start_end"]
    assert len(utterance) == 1
    assert utterance[0].subjects == (f"speaker-{DIGEST}-voice_001",)
    assert utterance[0].action_text == "hello there"


# ── One cut is one cut ─────────────────────────────────────────────


def test_one_cut_measured_twice_is_one_event():
    """The Project Model, the step files and the source memory all
    measure the same cut.  A cut is one cut."""
    first = TemporalEvent(
        event_id="a", event_type="scene_transition", action=None,
        action_text="", asset_id="asset-1", clip_id="clip_001",
        source_file=None, clock_id="clock:clip_001", start_seconds=5.0,
        end_seconds=5.04,
        onset=Boundary(5.0, "estimated", 0.04, "measured"),
        intermediate_states=(),
        completion=Boundary(5.04, "estimated", 0.04, "measured"))
    second = TemporalEvent(
        event_id="b", event_type="scene_transition", action=None,
        action_text="", asset_id="asset-1", clip_id="clip_001",
        source_file=None, clock_id="clock:clip_001", start_seconds=5.0,
        end_seconds=5.04,
        onset=Boundary(5.0, "estimated", 0.04, "measured"),
        intermediate_states=(),
        completion=Boundary(5.04, "estimated", 0.04, "measured"))
    merged = temporal_events._dedupe_events([first, second])
    assert len(merged) == 1


# ── Who was in the picture ─────────────────────────────────────────


def test_a_persons_tracked_presence_is_reported_as_a_state_change():
    """The model's tracks measure that a person WAS TRACKED over a
    span.  They do not measure that the person entered or left, and
    the event must not say they did."""
    model = _model(
        entities=[{"id": "person_1", "kind": "person", "label": "Ana"}],
        tracks=[{"id": "track-1", "entity_id": "person_1",
                 "clock_id": "clock_1", "local_track_id": "region_001",
                 "samples": [{"tick": 100}, {"tick": 900}]}],
        observations=[{"id": "obs-m3d-1", "asset_id": ASSET_ID,
                       "modality": "region_track",
                       "interval": {"clock_id": "clock_1", "start": 100,
                                    "end": 900},
                       "payload": {"track_id": "region_001"}}])
    events = temporal_events._presence_events(model, _catalog())
    assert len(events) == 2
    begins = [event for event in events if "begins" in event.action_text]
    assert len(begins) == 1
    assert begins[0].subjects == ("person_1",)
    assert begins[0].state_changes[0].property == "tracked"
    assert begins[0].state_changes[0].before is False
    assert begins[0].state_changes[0].after is True
    assert begins[0].state_changes[0].evidence_ids == ("obs-m3d-1",)
    assert "absence of measurement" in begins[0].basis


def test_a_person_tracked_across_an_event_is_named_in_it():
    """A participant is who the picture had in it."""
    model = _model(
        entities=[{"id": "person_1", "kind": "person", "label": "Ana"}],
        tracks=[{"id": "track-1", "entity_id": "person_1",
                 "clock_id": "clock_1", "local_track_id": "region_001",
                 "samples": [{"tick": 0}, {"tick": 5000}]}])
    event = TemporalEvent(
        event_id="e", event_type="action_state_change", action=None,
        action_text="", asset_id=ASSET_ID, clip_id="clip_001",
        source_file=None, clock_id="clock_1", start_seconds=1.0,
        end_seconds=2.0,
        onset=Boundary(1.0, "estimated", None, "measured"),
        intermediate_states=(),
        completion=Boundary(2.0, "estimated", None, "measured"))
    attached = temporal_events._attach_present_people([event], model)
    assert attached[0].subjects == ("person_1",)
    assert attached[0].subject_labels == ("Ana",)


# ── A project whose measurements live in the source memory ────────


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_project_whose_measurements_live_in_the_source_memory_is_read(
        tmp_path, monkeypatch):
    """A project that carries footage but whose Project Model was never
    migrated still has the measurements the lanes took.  The index reads
    them by the digest the project's own catalog declares."""
    from library.tools import source_memory

    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(tmp_path))
    root = tmp_path / "project"
    _write_json(root / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "source_file": "/tmp/clip_001.mp4",
             "frame_rate_nominal": 25.0, "duration_seconds": 20.0}]}},
        "source_fingerprints": {"clip_001": {
            "content_digest": DIGEST, "path": "/tmp/clip_001.mp4",
            "size_bytes": 10}},
    })
    _write_json(tmp_path / DIGEST / "sound.json", {
        "content_digest": DIGEST, "status": "measured",
        "method": "panns-cnn14-decisionlevelmax",
        "sound_events": [{"label": "Clicking", "start": 1.0, "end": 1.5,
                          "confidence": 0.4}]})
    index = temporal_events.build_temporal_events(str(root))
    assert [event.event_type for event in index.events] == ["audio_event"]
    assert index.events[0].clip_id == "clip_001"
    assert "source_memory" in index.sources
