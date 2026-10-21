"""B-16: one reference, followed to the original evidence.

Defect caught: the knowledge graph existed and answered `traverse`,
`resolve_entity` and `resolve_event`, but no production path reached
it - so the question "why does Ren believe this, and what original
evidence supports it?" had no answer, and the identity links, the
relations and the interpretations behind a belief were unreachable.

Every project here is a hand-made Project Model dict - no real
project, no footage, no model (§8).
"""
from dataclasses import replace

from library.tools import evidence_contract, evidence_trace

DIGEST = "digest-b16-trace"
CLOCK = "clock-b16"
ASSET = "asset-b16"
URI = "/tmp/ren-b16-fixture/clip-a.mov"


def _native(measurement_type, value, *, lane, start_tick, end_tick):
    entry = evidence_contract.registry_entry(measurement_type)
    if entry["kind"] is evidence_contract.EvidenceType.INFERENCE:
        # A model-produced type is an INFERENCE and carries the
        # uncertainty its producer earned (BL-3).
        record = evidence_contract.new_inference(
            asset_id=ASSET, clock_id=CLOCK,
            start_tick=start_tick, end_tick=end_tick,
            measurement_type=measurement_type, value=value,
            method="region-tracker-v1",
            provenance_id=f"prov-{DIGEST}-{lane}", uncertainty=0.2,
            coverage=evidence_contract.CoverageKind.COMPLETE,
            coverage_samples=1, coverage_sample_rate_hz=0.0)
    else:
        record = evidence_contract.new_measurement(
            asset_id=ASSET, clock_id=CLOCK,
            start_tick=start_tick, end_tick=end_tick,
            measurement_type=measurement_type, value=value,
            method="region-tracker-v1",
            provenance_id=f"prov-{DIGEST}-{lane}",
            coverage=evidence_contract.CoverageKind.COMPLETE,
            coverage_samples=1, coverage_sample_rate_hz=0.0)
    native = evidence_contract.to_rust_evidence(
        replace(record, freshness="fresh"))
    native.update({
        "source_stream_id": f"stream-{DIGEST}",
        "sampling_method": "region-tracker-v1",
        "temporal_coverage": [{"clock_id": CLOCK, "start": start_tick,
                               "end": end_tick}],
        "coverage_status": "complete",
        "verified_status": "verified",
        "producer": "fixture",
        "producer_version": "1",
        "derivation": {
            "producer": "fixture", "producer_version": "1",
            "preprocessing": {"resize": 0.5},
            "sampling_policy": {"fps": 5},
            "source_revision": "rev-1",
            "derivation_digest": "sha256:b16",
        },
    })
    return native


def _model():
    observation = {
        "id": "obs-face-1", "asset_id": ASSET,
        "interval": {"clock_id": CLOCK, "start": 1000, "end": 3000},
        "modality": "region_frame",
        "payload": {
            "faces": [{"box": [0.1, 0.1, 0.2, 0.2]}],
            "evidence_contract": _native("person.region_frame", {"faces": 1},
                                         lane="m3b", start_tick=1000,
                                         end_tick=3000),
        },
        "confidence": None, "provenance_id": f"prov-{DIGEST}-m3b",
    }
    return {
        "id": "project-b16", "schema_version": 1,
        "clocks": [{"id": CLOCK, "ticks_per_second": 1000,
                    "domain": {"kind": "source", "asset_id": ASSET}}],
        "assets": [{"id": ASSET, "content_digest": DIGEST, "uri": URI}],
        "entities": [
            {"id": "person_001", "kind": "person", "label": "Ada",
             "identity_scope": "project"},
            {"id": "object_001", "kind": "object", "label": "cup",
             "identity_scope": "asset"},
        ],
        "observations": [observation],
        "identity_links": [
            {"id": "link-1", "entity_id": "person_001",
             "observation_id": "obs-face-1", "method": "diarization",
             "status": "candidate",
             "supporting_observation_ids": ["obs-face-1"],
             "contradicting_observation_ids": []},
        ],
        "relations": [
            {"id": "rel-1", "subject_id": "person_001",
             "object_id": "object_001", "predicate": "co_present",
             "relation_class": "temporal", "status": "candidate",
             "certainty": "measured", "evidence_ids": ["obs-face-1"],
             "interval": {"clock_id": CLOCK, "start": 1000, "end": 3000}},
        ],
        "interpretations": [
            {"id": "interp-1", "kind": "scene_summary",
             "certainty": "interpreted", "uncertainty": 0.2,
             "evidence_ids": ["obs-face-1"], "scope": {"event_ids": ["event-1"]}},
        ],
        "events": [
            {"id": "event-1", "kind": "speaking",
             "interval": {"clock_id": CLOCK, "start": 1000, "end": 3000},
             "status": "verified", "evidence_ids": ["obs-face-1"],
             "participant_ids": ["person_001"], "state_changes": []},
        ],
        "tracks": [
            {"id": "track-1", "clock_id": CLOCK, "entity_id": "object_001",
             "samples": [{"tick": 1000, "geometry": {},
                          "coordinate_space": "coord-1", "confidence": None,
                          "evidence_ids": ["obs-face-1"]}]},
        ],
        "provenance": [{"id": f"prov-{DIGEST}-m3b", "producer": "fixture"}],
        "time_transforms": [],
    }


def test_the_trace_follows_an_entity_to_the_source_footage():
    answer = evidence_trace.trace(_model(), "person_001")
    assert answer["found"] is True
    assert answer["kind"] == "entity"
    assert answer["entity"]["id"] == "person_001"
    assert [row["observation_id"] for row in answer["observations"]] == [
        "obs-face-1"]
    assert len(answer["sources"]) == 1
    source = answer["sources"][0]
    assert source["status"] == "resolved"
    assert source["source_file"] == URI
    assert source["start_seconds"] == 1.0
    assert source["end_seconds"] == 3.0


def test_the_trace_names_the_identity_links_the_relations_and_the_reading():
    answer = evidence_trace.trace(_model(), "person_001")
    assert [link["id"] for link in answer["identity_links"]] == ["link-1"]
    assert [relation["id"] for relation in answer["relations"]] == ["rel-1"]
    assert [event["id"] for event in answer["events"]] == ["event-1"]
    assert [row["id"] for row in answer["interpretations"]] == ["interp-1"]
    assert [track["id"] for track in answer["tracks"]] == ["track-1"]


def test_the_trace_walks_the_graph_and_carries_the_derivation():
    answer = evidence_trace.trace(_model(), "person_001", hops=2)
    walked = {node["id"] for node in answer["traversal"]["nodes"]}
    assert walked == {"person_001", "object_001"}
    assert any(edge["kind"] == "relation" for edge in answer["traversal"]["edges"])
    derivation = answer["derivation"]
    assert derivation[0]["observation_id"] == "obs-face-1"
    assert derivation[0]["preprocessing"] == {"resize": 0.5}
    assert derivation[0]["sampling_policy"] == {"fps": 5}
    assert derivation[0]["derivation_digest"] == "sha256:b16"
    assert derivation[0]["producer_version"] == "1"


def test_the_trace_of_an_event_names_its_participants_and_its_evidence():
    answer = evidence_trace.trace(_model(), "event-1")
    assert answer["kind"] == "event"
    assert [row["id"] for row in answer["participants"]] == ["person_001"]
    assert [row["observation_id"] for row in answer["observations"]] == [
        "obs-face-1"]
    assert answer["sources"][0]["source_file"] == URI


def test_the_trace_of_an_observation_names_its_entities():
    answer = evidence_trace.trace(_model(), "obs-face-1")
    assert answer["kind"] == "observation"
    assert answer["entities"] == ["person_001"]
    assert [link["id"] for link in answer["identity_links"]] == ["link-1"]
    assert answer["sources"][0]["start_seconds"] == 1.0


def test_a_reference_the_model_does_not_hold_is_refused():
    answer = evidence_trace.trace(_model(), "person_999")
    assert answer["found"] is False
    assert "no entity, event, observation or track" in answer["reason"]
    assert answer["sources"] == []


def test_a_reference_naming_two_things_is_refused():
    model = _model()
    model["entities"].append({"id": "obs-face-1", "kind": "object",
                               "label": "clash", "identity_scope": "asset"})
    answer = evidence_trace.trace(model, "obs-face-1")
    assert answer["found"] is False
    assert "more than one thing" in answer["reason"]
    assert {row["kind"] for row in answer["candidates"]} == {"entity",
                                                              "observation"}
