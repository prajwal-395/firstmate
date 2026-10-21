"""The epistemic class of a claim: measured, inferred or interpreted.

Every test here names a behavioural defect the world model could have:

* a VLM's description of a room filed as a measurement (the punchlist
  VI-012 defect: a model description promoted into an objective
  measurement without verification),
* a claim nobody declared silently becoming a measurement,
* a candidate the verifier REFUSED reading exactly like a candidate
  nobody looked at,
* two lanes disagreeing about one second and the join keeping only the
  first,
* a producer's claim type drifting away from the lookup that reads it.
"""
import importlib
import json
from pathlib import Path

import pytest

from library.tools import (
    claim_provenance,
    source_memory,
    span_verification,
    world_model,
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


def _m0_doc(digest: str, duration: float = 4.0) -> dict:
    return {
        "content_digest": digest, "size_bytes": 12345,
        "observed_paths": ["/nowhere/FILE.MXF"],
        "duration_seconds": duration,
        "video_streams": [], "audio_streams": [],
        "measured_levels_db": {}, "gop_frames": None,
    }


def _m4_doc(digest: str) -> dict:
    """An M4 lane: a scene DESCRIPTION a model wrote, over a measured
    boundary."""
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": "measured",
        "scenes": [{"start": 0.0, "end": 4.0, "location": "studio",
                    "type": "indoor", "lighting": "dim",
                    "notable_features": []}],
        "scene_boundaries": [], "scene_coverage": None,
        "instrument": {"scene_pass": "folded window call"},
    }


def _m7_doc(digest: str, speaking=(), candidates=()) -> dict:
    return {
        "content_digest": digest, "size_bytes": 12345,
        "source_file": "/nowhere/FILE.MXF", "status": "built",
        "frame_count": 20, "faces_assigned": 1, "faces_unassigned": 0,
        "predicates": {
            "on_screen": {"basis": "test", "spans": []},
            "speaking": {"basis": "test", "voice_face_links": [],
                         "voice_unavailable_reason": None,
                         "spans": list(speaking)},
        },
        "candidates": {"hand_near_mouth": {"basis": "test",
                                           "spans": list(candidates)}},
    }


def _build(root: Path, digest: str, **lanes) -> dict:
    target = root / digest
    _write(target / source_memory.SLOT_SOURCE,
           _m0_doc(digest, lanes.get("duration", 4.0)))
    if "m4" in lanes:
        _write(target / source_memory.SLOT_SCENES, _m4_doc(digest))
    if "m7" in lanes:
        _write(target / source_memory.SLOT_EVENTS, _m7_doc(
            digest, lanes["m7"].get("speaking", ()),
            lanes["m7"].get("candidates", ())))
    if "m8" in lanes:
        verdicts = {}
        for statement, frames, answer in lanes["m8"]:
            key = span_verification.verdict_key(statement, frames, "test-model")
            verdicts[key] = {
                "answer": answer, "frame": 1, "reason": "test",
                "frame_t": frames[0], "frames": frames,
                "statement": statement, "model": "test-model",
                "prompt_version": span_verification.PROMPT_VERSION,
                "seconds": 0.1, "reply": "{}",
            }
        _write(target / source_memory.SLOT_VERDICTS,
               {"content_digest": digest, "verdicts": verdicts})
    if "m6" in lanes:
        _write(target / source_memory.SLOT_CLOCK, lanes["m6"])
    return world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                                root)


# ── The class of a claim ───────────────────────────────────────────


def test_a_models_description_of_a_room_is_not_filed_as_a_measurement(
        tmp_path, memory_root):
    """The defect: the world model filed a VLM's description of a scene
    exactly like the device's own record of the frame rate, so a reader
    could not tell a description from a reading.

    The scene description is a model's assertion, so it is `inferred`.
    """
    digest = "1" * 64
    _build(memory_root, digest, m4=True)
    record = source_memory.read_world_model(digest, memory_root)
    entry = record["timeline"][0]
    assert entry["scene"]["location"] == "studio"
    assert entry["provenance"]["scene"]["class"] == "inferred"
    assert entry["provenance"]["scene"]["measurement_type"] == \
        "video.scene_segment"


def test_a_claim_nobody_declared_stays_unknown(tmp_path, memory_root):
    """The defect: a claim with no producer behind it would be defaulted
    to a measurement, and a default that reads as a measurement says an
    instrument read something nobody read.

    Unknown stays unknown.
    """
    assert claim_provenance.claim_class("a_claim_nobody_declared") == \
        claim_provenance.UNKNOWN_CLASS
    assert claim_provenance.tag("a_claim_nobody_declared")["class"] == \
        claim_provenance.UNKNOWN_CLASS


def test_a_model_verification_does_not_promote_a_description(
        tmp_path, memory_root):
    """The defect: a model confirming another model's claim would read
    as an objective measurement.

    A model's confirmation is recorded as a verification and the class
    does not move; only a DETERMINISTIC verifier may promote a
    description to a measurement.
    """
    verification = claim_provenance.verification_from_verdict(
        {"answer": "yes", "statement": "the laptop is open",
         "model": "test-model"})
    assert claim_provenance.may_file_as_measured("inferred") is False
    assert claim_provenance.may_file_as_measured(
        "inferred", verification) is False
    assert claim_provenance.may_file_as_measured(
        "inferred", {"verifier_kind": "instrument"}) is True
    assert claim_provenance.tag("scene")["class"] == "inferred"
    assert claim_provenance.tag(
        "scene", {"verifier_kind": "instrument"})["class"] == "measured"
    assert claim_provenance.tag(
        "scene", verification)["class"] == "inferred"


def test_every_producer_declares_the_type_the_lookup_reads():
    """The defect: a producer that changed the kind of claim it writes
    would leave the lookup filing its claims under a type it no longer
    produces - the class would go on being read from a stale
    declaration.

    The producer is the authority; the lookup must agree with it.
    """
    for claim_key, declared in claim_provenance.CLAIM_DECLARATIONS.items():
        producer = importlib.import_module(declared["producer"])
        assert claim_key in producer.CLAIM_TYPES, claim_key
        assert producer.CLAIM_TYPES[claim_key] == \
            declared["measurement_type"], claim_key


# ── Verification and disagreement ──────────────────────────────────


def test_a_refuted_candidate_does_not_read_like_an_unverified_one(
        tmp_path, memory_root):
    """The defect: a candidate the verifier REFUSED and a candidate
    nobody ever looked at were the same record, so a reader could not
    tell a checked claim from an unchecked one.
    """
    digest = "2" * 64
    candidate = {"face_track": "face_001", "start": 0.5, "end": 1.5,
                 "frames": [{"t": 0.5, "d": 0.0}, {"t": 1.0, "d": 0.5}]}
    _build(memory_root, digest,
           m7={"candidates": [candidate]},
           m8=[("covers his mouth with his hand", [0.5, 1.0], "no")])
    entry = source_memory.read_world_model(digest, memory_root)["timeline"][0]
    refuted = entry["events"]["hand_near_mouth"][0]
    assert refuted["verification"]["status"] == "refuted"
    assert entry["provenance"]["candidate_span"]["class"] == "inferred"

    other = "3" * 64
    _build(memory_root, other, m7={"candidates": [candidate]})
    unchecked = source_memory.read_world_model(
        other, memory_root)["timeline"][0]["events"]["hand_near_mouth"][0]
    assert unchecked["verification"]["status"] == "unverified"


def test_two_lanes_that_disagree_about_one_second_are_both_kept(
        tmp_path, memory_root):
    """The defect: the join kept the FIRST speaking span and dropped the
    second, so a disagreement between two lanes about who is speaking
    was invisible - the second claim was simply gone.
    """
    digest = "4" * 64
    _build(memory_root, digest, m7={"speaking": [
        {"voice_track": "voice_001", "face_track": "face_001",
         "start": 0.0, "end": 2.0},
        {"voice_track": "voice_002", "face_track": "face_002",
         "start": 1.0, "end": 3.0},
    ]})
    entry = source_memory.read_world_model(digest, memory_root)["timeline"][1]
    face_tracks = {span["face_track"] for span in entry["speech"]["speakers"]}
    assert face_tracks == {"face_001", "face_002"}
    quantities = {conflict["quantity"] for conflict in entry["conflicts"]}
    assert "who is speaking" in quantities


def test_two_measurements_of_the_same_offset_that_disagree_are_both_kept(
        tmp_path, memory_root):
    """The defect: the world model carried the audio n-gram offset and
        dropped the Resolve cross-check that contradicted it, so a
        disagreement between two instruments was invisible.
    """
    digest = "5" * 64
    _build(memory_root, digest, m6={
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "group_id": "g", "group_members": ["a", "b"],
        "reference_digest": "b", "offset_to_reference_seconds": 11.31,
        "resolve_cross_check": {"agrees_with_ngram_offset": False,
                                "offset_seconds": 37.775},
    })
    record = source_memory.read_world_model(digest, memory_root)
    conflicts = record["multicam"]["conflicts"]
    assert len(conflicts) == 1
    values = {claim["value"] for claim in conflicts[0]["claims"]}
    assert values == {11.31, 37.775}


# ── The census ─────────────────────────────────────────────────────


def test_the_census_names_the_model_descriptions_stored_as_measurements(
        tmp_path, memory_root):
    """The defect: nothing counted how much of a world model was a
    description rather than a reading, so a model description stored as
    a measurement could not be seen - let alone fixed.
    """
    digest = "6" * 64
    _build(memory_root, digest, m4=True)
    record = source_memory.read_world_model(digest, memory_root)

    tagged = claim_provenance.census(record)
    assert tagged["claims"] > 0
    assert tagged["model_descriptions_filed_as_measurements"] == 0
    assert tagged["share_with_a_class"] == 1.0

    legacy = {**record, "timeline": [
        {key: value for key, value in entry.items()
         if key not in ("provenance", "conflicts")}
        for entry in record["timeline"]]}
    untagged = claim_provenance.census(legacy)
    assert untagged["share_with_a_class"] == 0.0
    assert untagged["model_descriptions_filed_as_measurements"] == \
        untagged["claims"]
