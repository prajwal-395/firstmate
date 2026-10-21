"""The world's query surface: narrow questions, native timing, provenance.

Every test names a behavioural defect the query surface could have: a
span answered at the second's edge instead of the record's own, a fact
with no lane behind it, an absent lane reading as an empty answer, a
question paying for a lane it never asked about, a candidate reported as
an answer, a query that rewrote the per-second view it only reads.
"""
import json
from pathlib import Path

import pytest

from library.tools import (
    source_memory,
    span_verification,
    token_measure,
    world_model_query,
)


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """An isolated memory root: nothing touches the machine store."""
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _m0_doc(digest: str, duration: float = 60.0) -> dict:
    return {
        "content_digest": digest, "size_bytes": 12345,
        "observed_paths": ["/nowhere/FILE.MXF"],
        "duration_seconds": duration,
        "video_streams": [{"index": 0, "codec_type": "video",
                            "codec": "h264", "width": 1920, "height": 1080,
                            "avg_frame_rate": "30000/1001"}],
        "audio_streams": [{"index": 1, "codec_type": "audio",
                           "codec": "pcm_s24le", "channel": 1,
                           "channels": 2, "sample_rate": 48000}],
        "measured_levels_db": {"CH1": -20.0},
        "program_track": {"channel": 1, "basis": "single",
                           "measured_levels_db": {"CH1": -20.0}},
        "gop_frames": None,
    }


def _m1_doc(digest: str, utterances: list) -> dict:
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": "transcribed", "utterance_cut": "hybrid-windows",
        "program_track": {"channel": 1, "basis": "single",
                           "measured_levels_db": {"CH1": -20.0}},
        "utterances": utterances,
        "utterance_count": len(utterances),
        "word_count": sum(len(u.get("words") or []) for u in utterances),
        "speech_seconds": sum(u["end"] - u["start"] for u in utterances),
        "instrument": {"arm": "hybrid", "aligner": "mfa"},
    }


def _m3b_doc(digest: str, faces: list, voices: list) -> dict:
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": "measured", "face_match_threshold": 0.25,
        "faces": faces, "voices": voices, "speech_face_links": [],
        "instrument": {"face": "insightface", "voice": "speechbrain"},
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


def _m10_doc(digest: str, holding: list) -> dict:
    return {
        "content_digest": digest, "size_bytes": 12345,
        "source_file": "/nowhere/FILE.MXF", "status": "built",
        "frame_count": 20,
        "relationships": {"holding": {"basis": "test", "spans": holding}},
    }


def _write_lanes(root: Path, digest: str, **kwargs) -> None:
    target = root / digest
    _write(target / source_memory.SLOT_SOURCE, _m0_doc(digest))
    if "utterances" in kwargs:
        _write(target / source_memory.SLOT_TRANSCRIPT,
               _m1_doc(digest, kwargs["utterances"]))
    if "faces" in kwargs or "voices" in kwargs:
        _write(target / source_memory.SLOT_IDENTITY, _m3b_doc(
            digest, kwargs.get("faces", []), kwargs.get("voices", [])))
    if "scenes" in kwargs:
        _write(target / source_memory.SLOT_SCENES,
               _m4_doc(digest, kwargs["scenes"]))
    if "sound_events" in kwargs:
        _write(target / source_memory.SLOT_SOUND,
               _m5_doc(digest, kwargs["sound_events"]))
    if any(key in kwargs for key in ("on_screen", "speaking", "candidates")):
        _write(target / source_memory.SLOT_EVENTS, _m7_doc(
            digest, kwargs.get("on_screen", []), kwargs.get("speaking", []),
            kwargs.get("candidates", [])))
    if "holding" in kwargs:
        _write(target / source_memory.SLOT_RELATIONSHIPS,
               _m10_doc(digest, kwargs["holding"]))


DIGEST = "a" * 64


# ── The span ───────────────────────────────────────────────────────


def test_a_span_is_answered_at_the_record_s_own_timing_not_the_second(
        memory_root):
    """An utterance from 10.4s to 12.7s is answered as 10.4-12.7.

    The defect: a query surface that reported the SECOND the fact fell
    into would answer 10.0-13.0, and a planner cutting on the boundary
    would cut half a second early.
    """
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 10.4, "end": 12.7, "text": "a measured line",
         "words": [{"word": "a", "start": 10.4, "end": 10.5}]},
    ])
    world = world_model_query.World(DIGEST, memory_root)
    facts = world.facts(10.0, 13.0, ["speech"])
    utterance = [row for row in facts if row["kind"] == "utterance"]
    assert utterance, "the utterance inside the span was not answered"
    assert utterance[0]["start"] == pytest.approx(10.4)
    assert utterance[0]["end"] == pytest.approx(12.7)


def test_a_fact_outside_the_span_is_not_answered_in_it(memory_root):
    """A span question does not reach past its own edges."""
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 10.4, "end": 12.7, "text": "inside", "words": []},
        {"start": 30.0, "end": 31.0, "text": "outside", "words": []},
    ])
    world = world_model_query.World(DIGEST, memory_root)
    texts = [row["value"].get("text")
             for row in world.facts(10.0, 13.0, ["speech"])]
    assert "inside" in texts
    assert "outside" not in texts


# ── The provenance ─────────────────────────────────────────────────


def test_every_fact_names_the_lane_and_the_field_it_came_from(memory_root):
    """A fact without a lane behind it cannot be checked by anybody.

    The defect: a fact that carried a value and no provenance would let
    a planner act on a measurement with no way back to the record.
    """
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 1.0, "end": 2.0, "text": "hello", "words": []},
    ], sound_events=[{"label": "laughter", "start": 1.0, "end": 2.0,
                      "confidence": 0.8}])
    world = world_model_query.World(DIGEST, memory_root)
    facts = world.facts(0.0, 5.0)
    assert facts
    for row in facts:
        provenance = row["provenance"]
        assert provenance["lane"]
        assert provenance["slot"]
        assert provenance["reader"]
        assert provenance["field"]
        assert provenance["status"] in ("measured", "candidate",
                                        "verified", "rejected")


def test_a_lane_that_was_never_built_is_named_absent_and_answers_nothing(
        memory_root):
    """An absent lane is ABSENT, never an empty answer.

    The defect: a sound question answered with no rows would read as
    "nothing was heard", which is a false negative the measurement never
    made.
    """
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 1.0, "end": 2.0, "text": "hello", "words": []},
    ])
    world = world_model_query.World(DIGEST, memory_root)
    answer = world.answer({"aspect": "what", "start": 0.0, "end": 5.0})
    assert "m5" in answer["lanes_absent"]
    assert "m5" not in answer["lanes_used"]
    assert [row for row in answer["facts"] if row["aspect"] == "sound"] == []


def test_a_question_reads_only_the_lanes_it_needs(memory_root):
    """A question about speech never pays for the faces.

    The defect: a query surface that materialised every lane for every
    question would cost the whole episode for a question about one span.
    """
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 1.0, "end": 2.0, "text": "hello", "words": []},
    ], faces=[{"track_id": "face_001",
               "spans": [{"start": 0.0, "end": 5.0, "box": [0, 0, 1, 1],
                          "det_score": 0.9}]}])
    world = world_model_query.World(DIGEST, memory_root)
    world.facts(0.0, 5.0, ["speech"])
    assert set(world._lanes) == {"m1", "m7"}


# ── The candidates ─────────────────────────────────────────────────


def test_a_candidate_is_reported_as_a_candidate_and_never_as_an_answer(
        memory_root):
    """M10's holding spans are geometric CANDIDATES, not answers.

    The defect: a query surface that reported them as facts would let a
    planner act on an unverified geometric guess.
    """
    _write_lanes(memory_root, DIGEST, holding=[
        {"owners": ["face_001", "face_002"], "start": 4.0, "end": 6.0,
         "frames": [{"index": 8, "t": 4.5, "d": 0.12}]},
    ])
    world = world_model_query.World(DIGEST, memory_root)
    rows = world.facts(3.0, 7.0, ["relationships"])
    assert rows
    assert all(row["provenance"]["status"] == "candidate" for row in rows)
    assert all(row["provenance"]["lane"] == "m10" for row in rows)


def test_a_verdict_on_a_candidate_is_carried_with_it(memory_root):
    """A candidate with a verdict says so; one without stays a candidate.

    The defect: dropping the verdict would leave a verified span looking
    exactly like an unverified one.
    """
    span = {"face_track": "face_001", "start": 4.0, "end": 6.0,
            "frames": [{"index": 8, "t": 4.5, "d": 0.12}]}
    _write_lanes(memory_root, DIGEST, candidates=[span])
    statement = "covers his mouth with his hand"
    frames = [4.5]
    key, verdict = None, None
    model_id = "test-model"
    key = span_verification.verdict_key(statement, frames, model_id)
    verdict = {"answer": "yes", "frame": 1, "reason": "test",
               "frame_t": frames[0], "frames": frames,
               "statement": statement, "model": model_id,
               "prompt_version": span_verification.PROMPT_VERSION,
               "seconds": 0.1, "reply": "{}"}
    target = memory_root / DIGEST
    _write(target / source_memory.SLOT_VERDICTS,
           {"content_digest": DIGEST, "verdicts": {key: verdict}})
    world = world_model_query.World(DIGEST, memory_root)
    rows = world.facts(3.0, 7.0, ["events"])
    assert rows
    assert rows[0]["provenance"]["status"] == "verified"
    assert rows[0]["value"]["verdicts"][0]["answer"] == "yes"


# ── The entity ─────────────────────────────────────────────────────


def test_an_entity_query_returns_that_entity_s_occurrences_across_lanes(
        memory_root):
    """One entity, every lane that names it, at its own timing.

    The defect: an entity query that answered from one lane would hide
    the entity's other occurrences.
    """
    _write_lanes(memory_root, DIGEST,
                 faces=[{"track_id": "face_001",
                         "spans": [{"start": 0.0, "end": 5.0,
                                    "box": [0, 0, 1, 1], "det_score": 0.9}]}],
                 on_screen=[{"face_track": "face_001", "start": 0.0,
                             "end": 5.0}],
                 speaking=[{"voice_track": "voice_001",
                            "face_track": "face_001", "start": 1.0,
                            "end": 2.5}])
    world = world_model_query.World(DIGEST, memory_root)
    entity = world.entity("face_001")
    kinds = {row["kind"] for row in entity["occurrences"]}
    assert "face_track" in kinds
    assert "on_screen" in kinds
    assert "speaking" in kinds
    lanes = entity["lanes_used"]
    assert "m3b" in lanes and "m7" in lanes


def test_an_entity_nobody_measured_answers_with_no_occurrences(memory_root):
    """An entity the lanes never saw is empty, not an error."""
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 1.0, "end": 2.0, "text": "hello", "words": []}])
    world = world_model_query.World(DIGEST, memory_root)
    entity = world.entity("face_999")
    assert entity["occurrences"] == []
    assert entity["lanes_used"] == []


# ── The per-second view ────────────────────────────────────────────


def test_a_query_does_not_write_the_per_second_view(memory_root):
    """The derived view stays a READ of the records, not a new store.

    The defect: a query surface that rebuilt or rewrote `world_model.json`
    would make a question a write, and the view would stop being derived.
    """
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 1.0, "end": 2.0, "text": "hello", "words": []}])
    world = world_model_query.World(DIGEST, memory_root)
    world.facts(0.0, 5.0)
    world.entity("face_001")
    assert not (memory_root / DIGEST
                / source_memory.SLOT_WORLD_MODEL).exists()


# ── The planning context ───────────────────────────────────────────


def test_the_planning_context_carries_only_what_the_questions_asked_for(
        memory_root):
    """A planning call's world contribution is the questions' facts.

    The defect: a context that carried the whole world would make every
    planning call pay for the episode.
    """
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 1.0, "end": 2.0, "text": "inside", "words": []},
        {"start": 40.0, "end": 41.0, "text": "elsewhere", "words": []},
    ])
    world = world_model_query.World(DIGEST, memory_root)
    context = world_model_query.planning_context(world, [
        {"aspect": "speech", "start": 0.0, "end": 5.0}])
    texts = [row["value"].get("text")
             for row in context["questions"][0]["facts"]]
    assert "inside" in texts
    assert "elsewhere" not in texts
    assert context["lanes_absent"], "the absent lanes are not named"


def test_the_context_size_is_measured_with_a_named_tokenizer(memory_root):
    """A count is never published without the tokenizer that made it."""
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 1.0, "end": 2.0, "text": "hello", "words": []}])
    world = world_model_query.World(DIGEST, memory_root)
    context = world_model_query.planning_context(world, [
        {"aspect": "speech", "start": 0.0, "end": 5.0}])
    size = world_model_query.measure_context(context)
    assert set(size) == set(token_measure.available())
    assert size[token_measure.BYTES] > 0
