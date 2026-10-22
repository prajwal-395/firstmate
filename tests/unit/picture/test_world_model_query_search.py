"""The world's SEARCH surface: media intervals, grounded and honest.

Every test names a behavioural defect the search surface could have: an
interval that names no source bytes, a fact filed as a measurement its
producer never earned, a placeholder confidence read as a score, an
absent lane answering "nothing happened", an ordinal the answer cannot
name, a consumer that has to know about Resolve to read the result.
"""
import ast
import json
from pathlib import Path

import pytest

from library.tools import (
    project_model_evidence,
    source_memory,
    source_reference,
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


def _write_lanes(root: Path, digest: str, **kwargs) -> None:
    target = root / digest
    if "utterances" in kwargs:
        _write(target / source_memory.SLOT_TRANSCRIPT, {
            "content_digest": digest, "status": "transcribed",
            "utterances": kwargs["utterances"],
            "utterance_count": len(kwargs["utterances"]),
            "instrument": {"arm": "hybrid", "aligner": "mfa"},
        })
    if "faces" in kwargs:
        _write(target / source_memory.SLOT_IDENTITY, {
            "content_digest": digest, "status": "measured",
            "face_match_threshold": 0.25,
            "faces": kwargs["faces"], "voices": kwargs.get("voices", []),
            "speech_face_links": [],
            "instrument": {"face": "insightface", "voice": "speechbrain"},
        })
    if any(key in kwargs for key in ("on_screen", "speaking", "candidates")):
        _write(target / source_memory.SLOT_EVENTS, {
            "content_digest": digest, "status": "built",
            "predicates": {
                "on_screen": {"basis": "test",
                              "spans": kwargs.get("on_screen", [])},
                "speaking": {"basis": "test", "voice_face_links": [],
                             "spans": kwargs.get("speaking", [])},
            },
            "candidates": {
                "hand_near_mouth": {
                    "basis": "test",
                    "spans": kwargs.get("candidates", [])},
            },
        })
    if "holding" in kwargs:
        _write(target / source_memory.SLOT_RELATIONSHIPS, {
            "content_digest": digest, "status": "built",
            "relationships": {"holding": {"basis": "test",
                                          "spans": kwargs["holding"]}},
        })
    if "frames" in kwargs:
        _write(target / source_memory.SLOT_FRAMES_INDEX, {
            "content_digest": digest, "status": "sampled",
            "rate_hz_nominal": 2.0, "width": 384,
            "frames": kwargs["frames"],
        })


DIGEST = "b" * 64
DIGEST_2 = "c" * 64


def _candidate(start: float, end: float, track: str = "face_001") -> dict:
    return {"face_track": track, "start": start, "end": end,
            "frames": [{"index": 1, "t": start + 0.1, "d": 0.02}]}


# ── The grounding ───────────────────────────────────────────────────


def test_a_searched_interval_names_the_source_bytes_it_came_from(memory_root):
    """Every interval carries a reference a reader can place in a file.

    The defect: an interval that named no source would let a consumer
    cut a picture that no measurement ever described.
    """
    _write_lanes(memory_root, DIGEST, candidates=[_candidate(4.0, 6.0)])
    world = world_model_query.World(DIGEST, memory_root)
    answer = world.search({"mode": "action", "action": "hand_near_mouth"})
    assert answer["intervals"], "the action search answered nothing"
    interval = answer["intervals"][0]
    reference = interval["source_reference"]
    assert reference is not None, "the interval names no source"
    parsed = source_reference.parse(reference)
    assert parsed["kind"] == source_reference.INTERVAL
    assert source_reference.names_source(parsed)
    assert parsed["stream_id"] == answer["grounding"]["stream_id"]
    assert parsed["interval"]["clock_id"] == answer["grounding"]["clock_id"]
    assert parsed["interval"]["start"] == 4000
    assert parsed["interval"]["end"] == 6000


def test_every_interval_carries_the_class_its_producer_earned(memory_root):
    """A claim is filed under the class its OWN producer declared.

    The defect: a fact filed as a measurement because the reader found
    it convenient would let a model description read like an
    instrument's record.
    """
    _write_lanes(memory_root, DIGEST,
                 utterances=[{"start": 1.0, "end": 2.0, "text": "hello",
                              "confidence": 0.0, "words": []}],
                 candidates=[_candidate(4.0, 6.0)])
    world = world_model_query.World(DIGEST, memory_root)
    speech = world.search({"mode": "speech"})["intervals"][0]
    action = world.search({"mode": "action"})["intervals"][0]
    assert speech["provenance"]["class"] == "inferred"
    assert speech["provenance"]["claim"] == "words"
    assert action["provenance"]["class"] == "inferred"
    assert action["uncertainty"]["class"] == action["provenance"]["class"]


def test_a_transcript_utterance_carries_no_confidence_because_none_exists(
        memory_root):
    """M1's `confidence: 0.0` is a placeholder, not a score.

    The defect: reading that 0.0 as a confidence would report the
    transcript as maximally untrustworthy, which is a false negative
    the lane never measured.
    """
    _write_lanes(memory_root, DIGEST, utterances=[
        {"start": 1.0, "end": 2.0, "text": "hello", "confidence": 0.0,
         "words": []}])
    world = world_model_query.World(DIGEST, memory_root)
    interval = world.search({"mode": "speech"})["intervals"][0]
    assert interval["confidence"]["value"] is None
    assert interval["confidence"]["basis"] is None
    assert "no confidence score" in interval["uncertainty"]["reason"]


def test_a_lane_that_stored_a_score_reports_that_score(memory_root):
    """A measured score is reported with the field it came from."""
    _write_lanes(memory_root, DIGEST, faces=[
        {"track_id": "face_001",
         "spans": [{"start": 0.0, "end": 5.0, "box": [0, 0, 1, 1],
                    "det_score": 0.87}]}])
    world = world_model_query.World(DIGEST, memory_root)
    interval = world.search({"mode": "appearance"})["intervals"][0]
    assert interval["kind"] == "face_track"
    assert interval["confidence"]["value"] == pytest.approx(0.87)
    assert interval["confidence"]["basis"]


# ── The absence ─────────────────────────────────────────────────────


def test_a_search_over_a_lane_that_was_never_built_says_so(memory_root):
    """An absent lane is ABSENT, never an empty answer.

    The defect: a relation search answered with no rows would read as
    "nobody held anything", which is a false negative nobody measured.
    """
    _write_lanes(memory_root, DIGEST, candidates=[_candidate(4.0, 6.0)])
    world = world_model_query.World(DIGEST, memory_root)
    answer = world.search({"mode": "relation"})
    assert answer["intervals"] == []
    assert "m10" in answer["lanes_consulted_absent"]
    assert answer["unanswered"], "the missing lane is not named"


def test_an_entity_nobody_measured_is_named_not_answered(memory_root):
    """A name no lane carries is reported, not answered with nothing."""
    _write_lanes(memory_root, DIGEST, candidates=[_candidate(4.0, 6.0)])
    world = world_model_query.World(DIGEST, memory_root)
    answer = world.search({"mode": "identity", "entity": "nobody"})
    assert answer["intervals"] == []
    assert answer["unanswered"]
    assert "nobody" in answer["unanswered"][0]


# ── The order ───────────────────────────────────────────────────────


def test_an_order_names_the_ordinal_of_the_occurrence(memory_root):
    """The second time is a nameable answer.

    The defect: an order that returned the occurrences without their
    ordinal would leave "the second time" unanswerable, which is the
    question the whole world model cannot answer from its per-second
    view.
    """
    _write_lanes(memory_root, DIGEST, candidates=[
        _candidate(4.0, 6.0), _candidate(20.0, 22.0),
        _candidate(40.0, 42.0)])
    world = world_model_query.World(DIGEST, memory_root)
    answer = world.search({"mode": "order", "of": "action",
                            "action": "hand_near_mouth", "ordinal": "second"})
    assert len(answer["intervals"]) == 1
    assert answer["intervals"][0]["occurrence"] == 2
    assert answer["intervals"][0]["start"] == pytest.approx(20.0)


def test_an_order_after_a_moment_keeps_only_what_came_after_it(memory_root):
    """A relative order is a comparison, not a guess."""
    _write_lanes(memory_root, DIGEST, candidates=[
        _candidate(4.0, 6.0), _candidate(20.0, 22.0),
        _candidate(40.0, 42.0)])
    world = world_model_query.World(DIGEST, memory_root)
    answer = world.search({"mode": "order", "of": "action",
                            "action": "hand_near_mouth", "relative": "after",
                            "reference": {"start": 10.0, "end": 15.0}})
    starts = [row["start"] for row in answer["intervals"]]
    assert starts == [pytest.approx(20.0), pytest.approx(40.0)]


def test_an_unknown_ordinal_is_refused_by_name(memory_root):
    """A typo in the ordinal is refused, not answered with everything."""
    _write_lanes(memory_root, DIGEST, candidates=[_candidate(4.0, 6.0)])
    world = world_model_query.World(DIGEST, memory_root)
    with pytest.raises(ValueError) as excinfo:
        world.search({"mode": "order", "of": "action", "ordinal": "secondish"})
    assert "ordinal" in str(excinfo.value)


# ── The independence ────────────────────────────────────────────────


def test_the_query_surface_never_imports_the_resolve_client():
    """Evidence is inspected without Resolve.

    The defect: a query surface that reached Resolve to answer a
    question about the footage would make every question depend on a
    running NLE, and could not be asked on a machine that has none.
    """
    path = Path(world_model_query.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    for name in imported:
        assert "resolved" not in name, name
        assert name != "library.tools.resolved", name


def test_the_evidence_of_a_span_is_readable_without_resolve(memory_root):
    """The frames behind a span are named, with their own provenance."""
    _write_lanes(memory_root, DIGEST,
                 candidates=[_candidate(4.0, 6.0)],
                 frames=[{"file": "frames/frame_000001.jpg", "t": 4.5,
                          "pts": 4.5, "frame_index": 108}])
    world = world_model_query.World(DIGEST, memory_root)
    evidence = world.evidence(4.0, 6.0)
    frames = [row for row in evidence["evidence"]
              if row["kind"] == "frame"]
    assert frames, "the frames inside the span were not named"
    assert frames[0]["t"] == pytest.approx(4.5)
    assert frames[0]["provenance"]["lane"] == "m2"
    assert evidence["resolution"]["status"] == "not_attempted"


def test_a_search_answers_without_writing_to_the_memory(memory_root):
    """A question is a read, never a write.

    The defect: a search that built or rewrote the per-second view
    would make a question a write, and the view would stop being
    derived.
    """
    _write_lanes(memory_root, DIGEST, candidates=[_candidate(4.0, 6.0)])
    world = world_model_query.World(DIGEST, memory_root)
    world.search({"mode": "action"})
    assert not (memory_root / DIGEST
                / source_memory.SLOT_WORLD_MODEL).exists()


def test_a_search_reads_only_the_lanes_its_mode_needs(memory_root):
    """A relation search never pays for the transcript."""
    _write_lanes(memory_root, DIGEST,
                 utterances=[{"start": 1.0, "end": 2.0, "text": "hello",
                              "words": []}],
                 holding=[{"owners": ["face_001", "face_002"],
                           "start": 4.0, "end": 6.0,
                           "frames": [{"index": 8, "t": 4.5, "d": 0.12}]}])
    world = world_model_query.World(DIGEST, memory_root)
    world.search({"mode": "relation"})
    assert set(world._lanes) == {"m10"}


# ── The consumers ───────────────────────────────────────────────────


def test_the_interval_carries_no_resolve_or_timeline_vocabulary(memory_root):
    """A consumer that is not an editor can read the interval.

    The defect: an interval that carried a timeline, a reel or a
    Resolve handle would tie a future consumer - an animation tool, a
    sound designer, an asset library - to this product's shape.
    """
    _write_lanes(memory_root, DIGEST, candidates=[_candidate(4.0, 6.0)])
    world = world_model_query.World(DIGEST, memory_root)
    answer = world.search({"mode": "action"})
    text = json.dumps(answer["intervals"]).lower()
    for word in ("timeline", "reel", "resolve", "project_model"):
        assert word not in text, word


def test_the_digest_naming_convention_is_one_convention():
    """The reference a query builds names the asset the model names.

    The defect: two spellings of one asset would make a reference built
    by a query unresolvable against the model that carries it.
    """
    assert (source_reference.asset_id_for_digest(DIGEST)
            == project_model_evidence._asset_id(DIGEST))
    assert (source_reference.clock_id_for_digest(DIGEST)
            == project_model_evidence._clock_id(DIGEST))
    assert (source_reference.stream_id_for_digest(DIGEST)
            == project_model_evidence._stream_id(DIGEST))


def test_a_span_that_cannot_be_counted_carries_no_reference(memory_root):
    """A span with no length is refused, not widened into evidence."""
    _write_lanes(memory_root, DIGEST,
                 utterances=[{"start": 1.0, "end": 1.0, "text": "hello",
                              "words": []}])
    world = world_model_query.World(DIGEST, memory_root)
    interval = world.search({"mode": "speech"})["intervals"][0]
    assert interval["source_reference"] is None
    assert "could not be counted" in interval["uncertainty"]["reason"]
