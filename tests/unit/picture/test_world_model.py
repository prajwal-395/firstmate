"""The world model (M9): the join of M0-M8 into a per-second timeline.

Every test names a behavioral defect the join could have: a word
leaking into the wrong second, a face losing its track, a speaking
span that fails to link voice to face, a missing lane defaulted
instead of absent. No test asserts a schema or count.
"""
import json
from pathlib import Path
import pytest
from library.tools import source_memory, world_model
from library.tools import span_verification


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """An isolated memory root: nothing touches the machine store."""
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _m0_doc(digest: str, duration: float = 10.0) -> dict:
    return {
        "content_digest": digest, "size_bytes": 12345,
        "observed_paths": ["/nowhere/FILE.MXF"],
        "duration_seconds": duration,
        "video_streams": [{"index": 0, "codec_type": "video",
                            "codec": "h264", "width": 1920, "height": 1080,
                            "avg_frame_rate": "30000/1001"}],
        "audio_streams": [{"index": 1, "codec_type": "audio",
                            "codec": "pcm_s16le", "channel": 1,
                            "channels": 2, "sample_rate": 48000}],
        "measured_levels_db": {"CH1": -20.0},
        "program_track": {"channel": 1, "basis": "single",
                          "measured_levels_db": {"CH1": -20.0}},
        "gop_frames": None,
    }


def _m1_doc(digest: str, words: list) -> dict:
    """An M1 transcript with words at explicit times."""
    utterances = []
    for start, end, text in words:
        utterances.append({
            "start": start, "end": end, "text": text,
            "words": [{"word": w, "start": start + i * 0.1,
                       "end": start + i * 0.1 + 0.08}
                      for i, w in enumerate(text.split())],
            "confidence": 0.0, "method": "hybrid-mfa",
        })
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": "transcribed", "utterance_cut": "hybrid-windows",
        "program_track": {"channel": 1, "basis": "single",
                          "measured_levels_db": {"CH1": -20.0}},
        "utterances": utterances,
        "utterance_count": len(utterances),
        "word_count": sum(len(u["words"]) for u in utterances),
        "speech_seconds": sum(u["end"] - u["start"] for u in utterances),
        "instrument": {"arm": "hybrid", "aligner": "mfa"},
    }


def _m3_doc(digest: str, frames: list) -> dict:
    """An M3 record with per-frame faces and hands."""
    return {
        "content_digest": digest, "size_bytes": 12345,
        "source_file": "/nowhere/FILE.MXF", "status": "measured",
        "coordinates": "image-normalised, top-left origin",
        "frame_count": len(frames), "frames": frames,
        "instrument": {"method": "vision-helper-v1 over M2",
                       "m2_width": 384, "persistence_iou": 0.3,
                       "vision_seconds": 1.0, "ms_per_frame": 25.0,
                       "faces_removed_by_persistence": 0,
                       "frame_pixels": [1920, 1080]},
    }


def _m3b_doc(digest: str, faces: list, voices: list) -> dict:
    """An M3b record with face tracks and voice tracks."""
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": "measured", "face_match_threshold": 0.25,
        "faces": faces, "voices": voices,
        "speech_face_links": [],
        "instrument": {"face": "insightface", "voice": "speechbrain",
                       "frame_source": "M2-times-source-decode",
                       "frame_pixels": [1920, 1080], "sample_count": 10,
                       "sample_interval_s": 10.0, "max_samples": 120,
                       "face_input_max_width": 1408,
                       "face_model_pack": "buffalo_l",
                       "face_cache_key": "abc", "voice_device": "cpu",
                       "voice_cache_key": "def"},
    }


def _m4_doc(digest: str, scenes: list) -> dict:
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": "measured", "scenes": scenes,
        "scene_boundaries": [], "scene_coverage": {"ratio": 1.0},
        "instrument": {"scene_pass": "folded window call"},
    }


def _m5_doc(digest: str, events: list) -> dict:
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": "measured", "sound_events": events,
        "method": "panns-cnn14",
    }


def _m7_doc(digest: str, on_screen: list, speaking: list,
            candidates: list) -> dict:
    return {
        "content_digest": digest, "size_bytes": 12345,
        "source_file": "/nowhere/FILE.MXF", "status": "built",
        "frame_count": 20, "faces_assigned": 1, "faces_unassigned": 0,
        "predicates": {
            "on_screen": {"basis": "test", "spans": on_screen},
            "speaking": {"basis": "test", "voice_face_links": [],
                         "voice_unavailable_reason": None,
                         "spans": speaking},
        },
        "candidates": {
            "hand_near_mouth": {"basis": "test", "spans": candidates},
        },
    }


def _m8_verdict(statement: str, frame_times: list, answer: str) -> dict:
    model_id = "test-model"
    key = span_verification.verdict_key(statement, frame_times, model_id)
    return key, {
        "answer": answer, "frame": 1, "reason": "test",
        "frame_t": frame_times[0], "frames": frame_times,
        "statement": statement, "model": model_id,
        "prompt_version": span_verification.PROMPT_VERSION,
        "seconds": 0.1, "reply": "{}",
    }


def _build_all_lanes(root: Path, digest: str, **kwargs) -> None:
    """Write every lane a test needs under one digest."""
    target = root / digest
    _write(target / source_memory.SLOT_SOURCE, _m0_doc(digest, kwargs.get("duration", 10.0)))
    if "m1_words" in kwargs:
        _write(target / source_memory.SLOT_TRANSCRIPT, _m1_doc(digest, kwargs["m1_words"]))
    if "m3_frames" in kwargs:
        _write(target / source_memory.SLOT_PERSONS, _m3_doc(digest, kwargs["m3_frames"]))
    if "m3b_faces" in kwargs or "m3b_voices" in kwargs:
        _write(target / source_memory.SLOT_IDENTITY, _m3b_doc(
            digest, kwargs.get("m3b_faces", []), kwargs.get("m3b_voices", [])))
    if "m4_scenes" in kwargs:
        _write(target / source_memory.SLOT_SCENES, _m4_doc(digest, kwargs["m4_scenes"]))
    if "m5_events" in kwargs:
        _write(target / source_memory.SLOT_SOUND, _m5_doc(digest, kwargs["m5_events"]))
    if "m7_on_screen" in kwargs or "m7_speaking" in kwargs or "m7_candidates" in kwargs:
        _write(target / source_memory.SLOT_EVENTS, _m7_doc(
            digest, kwargs.get("m7_on_screen", []),
            kwargs.get("m7_speaking", []), kwargs.get("m7_candidates", [])))
    if "m8_verdicts" in kwargs:
        verdicts = {}
        for statement, frame_times, answer in kwargs["m8_verdicts"]:
            key, verdict = _m8_verdict(statement, frame_times, answer)
            verdicts[key] = verdict
        _write(target / source_memory.SLOT_VERDICTS,
               {"content_digest": digest, "verdicts": verdicts})


# ── The join ──────────────────────────────────────────────────────


def test_a_word_past_its_second_is_not_joined_into_the_previous_one(
        tmp_path, memory_root):
    """A word at 1.5s belongs to second 1, not second 0.

    The defect: an off-by-one in the time join would leak a word into
    the previous second's entry, so "what is happening at second 0"
    would report speech that has not started yet.
    """
    digest = "a" * 64
    _build_all_lanes(memory_root, digest, duration=5.0,
                     m1_words=[(1.5, 1.8, "hello world")])
    record = world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                                  memory_root)
    assert record["timeline_seconds"] == 6
    entry0 = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 0.0)
    entry1 = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 1.0)
    assert entry0["speech"]["words"] == []
    assert len(entry1["speech"]["words"]) == 2


def test_a_face_with_no_m3b_track_carries_no_track_id(tmp_path, memory_root):
    """A face no M3b observation vouches for is unassigned, not guessed.

    The defect: inventing a track_id for an unassigned face would
    attribute it to the wrong person in "who is on screen" queries.
    """
    digest = "b" * 64
    _build_all_lanes(memory_root, digest, duration=5.0,
                     m3_frames=[{"t": 0.0, "faces": [
                         {"box": [0.1, 0.1, 0.3, 0.3], "confidence": 0.9}],
                         "hands": []}],
                     m3b_faces=[], m3b_voices=[])
    world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                         memory_root)
    entry = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 0.0)
    assert len(entry["faces"]) == 1
    assert entry["faces"][0]["track_id"] is None


def test_the_speaking_span_links_voice_to_face_at_that_second(
        tmp_path, memory_root):
    """The speaker at a second comes from M7's speaking spans.

    The defect: joining M1 words without the M7 speaking span would
    report words with no speaker, so "who is speaking" could not be
    answered from the world model.
    """
    digest = "c" * 64
    _build_all_lanes(memory_root, digest, duration=5.0,
                     m1_words=[(0.5, 1.5, "hello there")],
                     m7_speaking=[{"voice_track": "voice_001",
                                   "face_track": "face_001",
                                   "start": 0.0, "end": 2.0}])
    world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                         memory_root)
    entry = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 0.0)
    assert entry["speech"]["speaker"] == {"voice_track": "voice_001",
                                          "face_track": "face_001"}


def test_a_sound_event_outside_its_second_is_not_joined(tmp_path, memory_root):
    """A sound event at 5.5s belongs to second 5, not second 3.

    The defect: a loose time filter would leak a sound event into a
    second it does not overlap, so "what is happening at second 3"
    would report applause that has not started.
    """
    digest = "d" * 64
    _build_all_lanes(memory_root, digest, duration=10.0,
                     m5_events=[{"label": "applause", "start": 5.5,
                                 "end": 6.0, "confidence": 0.9}])
    world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                         memory_root)
    entry3 = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 3.0)
    entry5 = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 5.0)
    assert entry3["sound"]["events"] == []
    assert len(entry5["sound"]["events"]) == 1


def test_the_scene_segment_containing_the_second_is_joined(
        tmp_path, memory_root):
    """The scene at second t is the M4 segment containing t.

    The defect: joining the wrong scene segment (e.g. the first one)
    would report the wrong location for "where are we" queries.
    """
    digest = "e" * 64
    _build_all_lanes(memory_root, digest, duration=10.0,
                     m4_scenes=[
                         {"start": 0.0, "end": 3.0, "location": "studio",
                          "type": "indoor", "lighting": "bright",
                          "notable_features": []},
                         {"start": 3.0, "end": 10.0, "location": "office",
                          "type": "indoor", "lighting": "dim",
                          "notable_features": []},
                     ])
    world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                         memory_root)
    entry2 = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 2.0)
    entry5 = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 5.0)
    assert entry2["scene"]["location"] == "studio"
    assert entry5["scene"]["location"] == "office"


def test_hand_near_mouth_candidates_are_joined_with_verdicts(
        tmp_path, memory_root):
    """M7 candidates appear in the timeline with their M8 verdicts.

    The defect: joining M7 spans without M8 verdicts would lose the
    VLM's yes/no answer, so "did he cover his mouth" could not be
    answered from the world model.
    """
    digest = "f" * 64
    candidate = {"face_track": "face_001", "start": 0.0, "end": 1.0,
                 "frames": [{"index": 0, "t": 0.0, "d": 0.5,
                             "box": [0.1, 0.1, 0.3, 0.3]}]}
    _build_all_lanes(memory_root, digest, duration=5.0,
                     m7_candidates=[candidate],
                     m8_verdicts=[("covers his mouth", [0.0], "yes")])
    world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                         memory_root)
    entry = world_model.query_at_time(
        source_memory.read_world_model(digest, memory_root), 0.0)
    assert len(entry["events"]["hand_near_mouth"]) == 1
    span = entry["events"]["hand_near_mouth"][0]
    assert span["face_track"] == "face_001"
    assert len(span["verdicts"]) == 1
    assert span["verdicts"][0]["answer"] == "yes"


def test_a_missing_lane_is_absent_from_lanes_not_defaulted(
        tmp_path, memory_root):
    """A lane never built is absent from `lanes`, never defaulted.

    The defect: defaulting a missing lane to empty would read as
    "measured and nothing there" when the truth is "never measured" -
    the source memory's own convention (an absent M1 is not silence).
    """
    digest = "a" * 64
    _build_all_lanes(memory_root, digest, duration=5.0,
                     m1_words=[(0.5, 1.0, "hello")])
    record = world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                                  memory_root)
    assert "m1" in record["lanes"]
    assert "m4" not in record["lanes"]
    assert "m7" not in record["lanes"]


def test_query_at_time_returns_the_entry_for_that_second(tmp_path, memory_root):
    """query_at_time returns the timeline entry for second floor(t).

    The defect: returning the wrong entry (e.g. off by one) would
    answer "what is happening at T" with the observations of a
    different second.
    """
    digest = "b" * 64
    _build_all_lanes(memory_root, digest, duration=5.0,
                     m1_words=[(0.5, 0.8, "first"),
                               (2.5, 2.8, "second")])
    world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                         memory_root)
    wm = source_memory.read_world_model(digest, memory_root)
    entry0 = world_model.query_at_time(wm, 0.0)
    entry2 = world_model.query_at_time(wm, 2.0)
    assert entry0["t"] == 0
    assert entry2["t"] == 2
    assert entry0["speech"]["words"][0]["word"] == "first"
    assert entry2["speech"]["words"][0]["word"] == "second"


def test_entities_carry_face_and_voice_tracks(tmp_path, memory_root):
    """The world model's entities section carries M3b's tracks.

    The defect: dropping the tracks would lose the persistent
    identities the timeline's track_ids point at, so "who is this"
    could not be resolved from the world model.
    """
    digest = "c" * 64
    _build_all_lanes(memory_root, digest, duration=5.0,
                     m3b_faces=[{"track_id": "face_001",
                                  "embedding": [0.1] * 512,
                                  "spans": [{"start": 0.0, "end": 1.0,
                                             "box": [0.1, 0.1, 0.3, 0.3],
                                             "det_score": 0.9}]}],
                     m3b_voices=[{"track_id": "voice_001",
                                   "embedding": [0.2] * 192,
                                   "spans": [[0.0, 1.0]]}])
    world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                         memory_root)
    wm = source_memory.read_world_model(digest, memory_root)
    assert len(wm["entities"]["face_tracks"]) == 1
    assert wm["entities"]["face_tracks"][0]["track_id"] == "face_001"
    assert len(wm["entities"]["voice_tracks"]) == 1
    assert wm["entities"]["voice_tracks"][0]["track_id"] == "voice_001"
