"""VI-008: a geometric candidate is not a relation.

The defect these tests name: the engine measured hands and measured
objects, and nothing joined them into a RELATION.  A question like "the
person holding the cup" could only be answered by co-occurrence - the
person is in the frame and the cup is in the frame - which is not the
same claim at all.

Each test below names the defect it would catch.
"""
from pathlib import Path

import pytest

from library.tools import evidence_query
from library.tools.relation_verification import (
    CONFIRMATION_GEOMETRY,
    CONFIRMATION_VLM,
    ambiguous_candidates,
    confirmed_relations,
    derive_candidates,
    rejected_candidates,
    statement_for,
)


def _model(*, joint=(0.5, 0.5), box=(450, 450, 550, 550),
           phrase="pen", ticks=(1000, 2000)):
    """A model with a measured hand and a measured object.

    The hand rows carry the measured joints and are LINKED to the
    person; the object rows carry the detector's box in source pixels.
    """
    observations = []
    identity_links = []
    for tick in ticks:
        observations.append({
            "id": f"face-{tick}",
            "modality": "face_frame",
            "asset_id": "asset-abc123",
            "interval": {"clock_id": "clock", "start": tick, "end": tick + 1},
            "payload": {"hands": [{"chirality": "Right",
                                   "joints": {"wrist": [joint[0], joint[1], 0.9]}}]},
        })
        identity_links.append({"entity_id": "person",
                               "observation_id": f"face-{tick}",
                               "status": "verified"})
        observations.append({
            "id": f"obj-{tick}",
            "modality": "object_detection",
            "asset_id": "asset-abc123",
            "interval": {"clock_id": "clock", "start": tick, "end": tick + 1},
            "payload": {"evidence_contract": {"value": {
                "kind": "detection", "query": phrase, "phrase": phrase,
                "detector": "owlv2", "score": 0.4,
                "box": list(box), "frame_width": 1000, "frame_height": 1000,
                "frame_index": tick, "time_seconds": tick / 1000}},
                "value": {"kind": "detection"}},
        })
    return {
        "assets": [{"id": "asset-abc123", "uri": "/media/podcast.MXF"}],
        "clocks": [{"id": "clock", "domain": {"asset_id": "asset-abc123"}}],
        "entities": [{"id": "person", "kind": "person"}],
        "observations": observations,
        "identity_links": identity_links,
        "relations": [],
    }


def _plan(model, **extra):
    predicate = {"predicate": "relation_presence", "relation_type": "holding",
                 "object_phrase": "pen"}
    predicate.update(extra)
    return evidence_query.compile_plan([predicate])


def test_a_hand_joint_inside_the_box_is_a_confirmed_relation():
    """The geometry that clears the published bar IS the relation."""
    candidates = derive_candidates(_model())
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["predicate"] == "holding"
    assert candidate["status"] == "candidate"
    assert candidate["geometry"]["strength"] == "clear"

    confirmed = confirmed_relations(candidates)
    assert len(confirmed) == 1
    assert confirmed[0]["status"] == "verified"
    assert confirmed[0]["confirmation"]["basis"] == CONFIRMATION_GEOMETRY


def test_a_relation_is_time_bounded_and_never_a_permanent_attribute():
    """The edge carries the span it was measured over."""
    candidates = derive_candidates(_model())
    edge = confirmed_relations(candidates)[0]
    assert edge["interval"]["clock_id"] == "clock"
    assert edge["interval"]["start"] == 1000
    assert edge["interval"]["end"] == 2001


def test_a_hand_only_NEAR_the_box_is_a_candidate_and_never_answers():
    """Proximity without a joint inside the box is ambiguous.

    The defect this names: an ambiguous candidate answered the question
    it was supposed to be verified for.
    """
    model = _model(joint=(0.66, 0.5))
    candidates = derive_candidates(model)
    assert len(candidates) == 1
    assert candidates[0]["geometry"]["strength"] == "ambiguous"
    assert candidates[0]["status"] == "candidate"
    assert confirmed_relations(candidates) == []
    assert len(ambiguous_candidates(candidates)) == 1


def test_the_query_answers_from_confirmed_relations_only():
    """The acceptance: a person and a cup in one frame is not a hold.

    With a confirmed relation the answer is `present`; with only a
    candidate it is `inconclusive`, and the candidate is reported AS a
    candidate.
    """
    model = _model()
    model["relations"] = confirmed_relations(derive_candidates(model))
    model["relations"][0]["object_id"] = "object-pen"
    model["entities"].append({"id": "object-pen", "kind": "object",
                              "label": "pen"})
    answer = evidence_query.evidence_for(model, _plan(model))
    assert answer["status"] == "present"
    assert answer["results"][0]["start_s"] == 1.0
    assert answer["results"][0]["end_s"] == 2.001
    assert answer["results"][0]["object_phrase"] == "pen"

    ambiguous = _model(joint=(0.66, 0.5))
    ambiguous["relations"] = derive_candidates(ambiguous)
    ambiguous["relations"][0]["object_id"] = "object-pen"
    ambiguous["entities"].append({"id": "object-pen", "kind": "object",
                                  "label": "pen"})
    answer = evidence_query.evidence_for(ambiguous, _plan(ambiguous))
    assert answer["status"] != "present"
    assert answer["status"] == "inconclusive"
    assert answer["candidate_relations"]
    assert not answer["results"]


def test_a_filed_relation_carries_only_what_the_schema_allows():
    """The Project Model's relation row denies unknown fields.

    The defect this names: a relation row carrying the candidate's
    geometry or the verdict that confirmed it - the schema refuses the
    whole model, so the evidence is lost with it.
    """
    from library.tools import relation_verification

    candidates = derive_candidates(_model())
    confirmed = relation_verification.confirmed_relations(candidates)
    rows = relation_verification.rows_to_file(candidates, confirmed, [])
    for relation in rows["relations"]:
        assert set(relation) <= set(relation_verification.RELATION_FIELDS)
        assert isinstance(relation["provenance_id"], str)
    assert rows["interpretations"], "a confirmed relation says what confirmed it"
    scope = rows["interpretations"][0]["scope"]
    assert scope["basis"] == relation_verification.CONFIRMATION_GEOMETRY
    assert scope["relation_id"]


def test_a_co_occurrence_is_not_a_relation():
    """Both entities present, no contact: the answer is not `present`."""
    model = _model(joint=(0.05, 0.05))
    model["relations"] = [{
        "id": "relation-co_present-person-pen-1-2",
        "subject_id": "person", "predicate": "co_present",
        "object_id": "pen", "interval": {"clock_id": "clock",
                                         "start": 1000, "end": 2000},
        "evidence_ids": ["face-1000", "obj-1000"], "status": "candidate",
        "certainty": "measured", "relation_class": "temporal"}]
    model["entities"].append({"id": "pen", "kind": "object"})
    answer = evidence_query.evidence_for(model, _plan(model))
    assert answer["status"] != "present"


def test_the_model_verdict_confirms_an_ambiguous_candidate():
    """The local model is asked about the ambiguous ones, and only them."""
    model = _model(joint=(0.66, 0.5))
    candidates = derive_candidates(model)
    verdicts = [{"candidate_id": candidates[0]["id"], "answer": "yes",
                 "reason": "the fingers are closed round the pen",
                 "frames": [1.0, 2.0], "frame_t": 1.0}]
    confirmed = confirmed_relations(candidates, verdicts)
    assert len(confirmed) == 1
    assert confirmed[0]["status"] == "verified"
    assert confirmed[0]["confirmation"]["basis"] == CONFIRMATION_VLM
    assert confirmed[0]["confirmation"]["reason"]


def test_a_model_that_says_no_leaves_the_candidate_a_candidate():
    model = _model(joint=(0.66, 0.5))
    candidates = derive_candidates(model)
    verdicts = [{"candidate_id": candidates[0]["id"], "answer": "no",
                 "reason": "the hand is beside the pen, not on it",
                 "frames": [1.0, 2.0]}]
    assert confirmed_relations(candidates, verdicts) == []
    rejected = rejected_candidates(candidates, verdicts)
    assert len(rejected) == 1
    assert rejected[0]["verification"]["answer"] == "no"


def test_the_statement_is_the_predicates_claim():
    model = _model(joint=(0.66, 0.5))
    candidate = derive_candidates(model)[0]
    assert statement_for(candidate) == "a person's hand is holding the pen"


def test_the_verifier_asks_the_model_about_the_crop_and_records_it():
    """The verification is a crop-level call, and it is recorded."""
    from library.tools import relation_verification

    class _Model:
        def __init__(self):
            self.calls = []

        def analyze_images(self, images, prompt, max_tokens=120):
            self.calls.append((images, prompt))
            return ('{"answer": "yes", "frame": 1, '
                    '"reason": "the hand is on the pen"}')

    model = _model(joint=(0.66, 0.5))
    candidates = derive_candidates(model)
    stub = _Model()
    crops = Path("/tmp/vi008-test-crop.jpg")
    crops.parent.mkdir(parents=True, exist_ok=True)
    crops.write_bytes(b"not really a jpeg")

    verdicts = relation_verification.verify_ambiguous(
        ambiguous_candidates(candidates),
        source_files={"asset-abc123": "/media/podcast.MXF"},
        model=stub,
        extractor=lambda *args, **kwargs: [crops])
    assert len(verdicts) == 1
    assert verdicts[0]["answer"] == "yes"
    assert verdicts[0]["candidate_id"] == candidates[0]["id"]
    assert stub.calls, "the model was never asked"
    assert "holding the pen" in stub.calls[0][1]


def test_an_unpublished_relation_is_refused():
    """The vocabulary is published: a made-up predicate is a refusal."""
    from library.tools.evidence_query import EvidenceQueryError

    with pytest.raises(EvidenceQueryError):
        evidence_query.compile_plan([
            {"predicate": "relation_presence", "relation_type": "carrying",
             "object_phrase": "pen"}])
    with pytest.raises(EvidenceQueryError):
        evidence_query.compile_plan([
            {"predicate": "relation_presence", "relation_type": "speaking",
             "object_phrase": "pen"}])
