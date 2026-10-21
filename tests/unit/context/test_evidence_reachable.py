"""B-16: the knowledge the model holds must be REACHABLE from a query.

Defect caught: `ren evidence` answered `measurement` and `event_span`
only, and refused `entity`, `identity_link`, `relation`,
`interpretation`, `event` and `track` by name - so the identity scopes,
the typed relations, the competing interpretations and the provenance
traversal were unreachable from any production query, and the answer
dropped the derivation that says what produced the evidence.

Every project here is a hand-made Project Model dict under `tmp_path` -
no real project, no footage, no model (§8). The native evidence
payloads are built through `evidence_contract.new_measurement`, so the
fixtures carry the real envelope rather than a faked shape.
"""
from dataclasses import replace

import pytest

from library.tools import evidence_contract, evidence_query
from library.tools.evidence_query import EvidenceQueryError

DIGEST = "digest-b16-reachable"
CLOCK = "clock-b16"
ASSET = "asset-b16"
URI = "/tmp/ren-b16-fixture/clip-a.mov"


def _native(measurement_type, value, *, lane, start_tick=0, end_tick=20000,
            coverage="complete", samples=1, freshness="fresh",
            method="region-tracker-v1", derivation=True,
            sample_resolution=None):
    entry = evidence_contract.registry_entry(measurement_type)
    if entry["kind"] is evidence_contract.EvidenceType.INFERENCE:
        # A model-produced type is an INFERENCE and carries the
        # uncertainty its producer earned (BL-3).
        record = evidence_contract.new_inference(
            asset_id=ASSET, clock_id=CLOCK,
            start_tick=start_tick, end_tick=end_tick,
            measurement_type=measurement_type, value=value, method=method,
            provenance_id=f"prov-{DIGEST}-{lane}", uncertainty=0.2,
            coverage=evidence_contract.CoverageKind(coverage),
            coverage_samples=samples, coverage_sample_rate_hz=0.0,
            sample_resolution=sample_resolution)
    else:
        record = evidence_contract.new_measurement(
            asset_id=ASSET, clock_id=CLOCK,
            start_tick=start_tick, end_tick=end_tick,
            measurement_type=measurement_type, value=value, method=method,
            provenance_id=f"prov-{DIGEST}-{lane}",
            coverage=evidence_contract.CoverageKind(coverage),
            coverage_samples=samples, coverage_sample_rate_hz=0.0,
            sample_resolution=sample_resolution)
    native = evidence_contract.to_rust_evidence(
        replace(record, freshness=freshness))
    native.update({
        "source_stream_id": f"stream-{DIGEST}",
        "sampling_method": method,
        "temporal_coverage": [{"clock_id": CLOCK, "start": start_tick,
                               "end": end_tick}],
        "coverage_status": coverage,
        "verified_status": "verified",
        "producer": "fixture",
        "producer_version": "1",
    })
    if derivation:
        native["derivation"] = {
            "producer": "fixture", "producer_version": "1",
            "preprocessing": {"resize": 0.5},
            "sampling_policy": {"fps": 5},
            "source_revision": "rev-1",
            "derivation_digest": "sha256:b16",
        }
    return native


def _obs(obs_id, modality, payload, *, lane, native=None,
         start_tick=0, end_tick=20000):
    observation = {
        "id": obs_id, "asset_id": ASSET,
        "interval": {"clock_id": CLOCK, "start": start_tick, "end": end_tick},
        "modality": modality, "payload": dict(payload),
        "confidence": None, "provenance_id": f"prov-{DIGEST}-{lane}",
    }
    if native is not None:
        observation["payload"]["evidence_contract"] = native
    return observation


def _model(*observations):
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
        "observations": list(observations),
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
             "interval": {"clock_id": CLOCK, "start": 0, "end": 20000}},
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
             "samples": [{"tick": 0, "geometry": {},
                          "coordinate_space": "coord-1", "confidence": None,
                          "evidence_ids": ["obs-face-1"]}]},
        ],
        "provenance": [{"id": f"prov-{DIGEST}-m3b", "producer": "fixture"}],
        "clip_uses": [], "renders": [], "frame_maps": [],
        "time_transforms": [],
    }


def _face_model():
    return _model(_obs(
        "obs-face-1", "region_frame", {"faces": [{"box": [0.1, 0.1, 0.2, 0.2]}]},
        lane="m3b", start_tick=1000, end_tick=3000,
        native=_native("person.region_frame", {"faces": 1}, lane="m3b",
                       start_tick=1000, end_tick=3000)))


def _graph_plan(predicate):
    return evidence_query.compile_plan([predicate])


# ─── The predicates the model already holds ───────────────────────


def test_the_graph_predicates_are_reachable():
    """Every collection the model holds is answerable by name."""
    model = _face_model()
    expected = {
        "entity": "person_001",
        "identity_link": "link-1",
        "relation": "rel-1",
        "interpretation": "interp-1",
        "event": "event-1",
        "track": "track-1",
    }
    for predicate, wanted in expected.items():
        answer = evidence_query.evidence_for(
            model, _graph_plan({"predicate": predicate}))
        assert answer["status"] == "present", predicate
        assert answer["results"], predicate
        assert answer["results"][0]["id"] == wanted, predicate


def test_a_graph_answer_carries_the_source_of_every_leaf_observation():
    """A row is only usable if the reader can open the footage."""
    model = _face_model()
    answer = evidence_query.evidence_for(
        model, _graph_plan({"predicate": "entity", "kind": "person"}))
    result = answer["results"][0]
    assert result["evidence_ids"] == ["obs-face-1"]
    assert len(result["sources"]) == 1
    source = result["sources"][0]
    assert source["status"] == "resolved"
    assert source["source_file"] == URI
    assert source["start_seconds"] == 1.0
    assert source["end_seconds"] == 3.0


def test_a_graph_predicate_with_an_unknown_vocabulary_is_refused():
    with pytest.raises(EvidenceQueryError):
        evidence_query.compile_plan([{"predicate": "entity",
                                     "identity_scope": "galactic"}])
    with pytest.raises(EvidenceQueryError):
        evidence_query.compile_plan([{"predicate": "relation",
                                     "relation_class": "psychic"}])


def test_a_plan_that_mixes_interval_and_graph_predicates_is_refused():
    with pytest.raises(EvidenceQueryError) as refused:
        evidence_query.compile_plan([
            {"predicate": "measurement", "measurement_type": "speech.word"},
            {"predicate": "entity", "kind": "person"}])
    assert "one kind of question" in str(refused.value)


def test_a_hand_written_plan_with_an_unknown_predicate_is_refused():
    """A plan may arrive as a JSON file; a bad predicate is refused, not
    a KeyError from deep inside the matcher."""
    with pytest.raises(EvidenceQueryError):
        evidence_query.evidence_for(_face_model(),
                                    {"predicates": [{"predicate": "vibes"}]})


def test_a_graph_predicate_that_matches_nothing_says_not_observed():
    """A model row that is absent is not a measured absence."""
    answer = evidence_query.evidence_for(
        _face_model(), _graph_plan({"predicate": "entity", "kind": "dragon"}))
    assert answer["status"] == "not_observed"
    assert answer["results"] == []
    assert answer["absence"]["state"] == "not_observed"


# ─── D1: the answer named the wrong lane ──────────────────────────


def _sharpness_model():
    """The registry says lane m3 produces the sharpness measurement; the
    producer's own lane stamp says vi03."""
    return _model(_obs(
        "obs-sharp-1", "region_frame", {"sharpness": 12.5},
        lane="vi03",
        native=_native("image.sharpness.laplacian_variance", 12.5,
                       lane="vi03", start_tick=0, end_tick=20000,
                       sample_resolution=[1920, 1080])))


def test_d1_the_declared_lane_is_corrected_by_the_producers_own_stamp():
    answer = evidence_query.evidence_for(
        _sharpness_model(),
        _graph_plan({"predicate": "measurement",
                     "measurement_type": "image.sharpness.laplacian_variance"}))
    assert answer["status"] == "present"
    corrections = answer["lane_corrections"]
    assert corrections == [{
        "predicate": "measurement",
        "measurement_type": "image.sharpness.laplacian_variance",
        "event": None,
        "declared_lane": "m3",
        "producer_lane": "vi03",
    }]
    assert any("m3" in reason and "vi03" in reason
               for reason in answer["reasons"])


def test_d1_the_answer_does_not_report_a_lane_that_never_ran():
    """The lane that produced the evidence is the lane the answer is
    judged on: an absence is never claimed against a lane that never
    ran while the real producer did."""
    model = _model(_obs(
        "obs-other-1", "region_frame", {"text": "hello"}, lane="vi03",
        native=_native("image.sharpness.laplacian_variance", 12.5,
                       lane="vi03", start_tick=0, end_tick=20000,
                       sample_resolution=[1920, 1080])))
    answer = evidence_query.evidence_for(
        model,
        _graph_plan({"predicate": "measurement",
                     "measurement_type": "image.sharpness.laplacian_variance"}),
        )
    assert answer["status"] == "present"
    assert answer["required_missing_lanes"] == []
    assert answer["lane_corrections"][0]["producer_lane"] == "vi03"


def test_d1_a_lane_that_really_never_ran_is_still_named():
    """The correction never invents a lane: a measurement no lane
    produced is still 'not_yet_analysed'."""
    model = _model(_obs(
        "obs-other-1", "region_frame", {"text": "hello"}, lane="m1",
        native=_native("speech.word", "hello", lane="m1")))
    answer = evidence_query.evidence_for(
        model,
        _graph_plan({"predicate": "measurement",
                     "measurement_type": "image.sharpness.laplacian_variance"}))
    assert answer["status"] == "not_yet_analysed"
    assert answer["required_missing_lanes"] == ["m3"]
    assert answer["lane_corrections"] == []


# ─── D5: the answer dropped the derivation ────────────────────────


def test_d5_the_answer_carries_the_derivation_and_the_references():
    """An answer that names the evidence but not the derivation cannot
    answer 'what produced this'."""
    model = _face_model()
    native = model["observations"][0]["payload"]["evidence_contract"]
    native["references"] = [{"kind": "source_frame", "stream_id": "stream-x",
                              "frame_index": 12}]
    plan = _graph_plan({"predicate": "measurement",
                        "measurement_type": "person.region_frame"})
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] == "present"
    derivation = answer["results"][0]["derivation"]
    assert derivation[0]["observation_id"] == "obs-face-1"
    assert derivation[0]["producer"] == "fixture"
    assert derivation[0]["producer_version"] == "1"
    assert derivation[0]["method"] == "region-tracker-v1"
    assert derivation[0]["preprocessing"] == {"resize": 0.5}
    assert derivation[0]["sampling_policy"] == {"fps": 5}
    assert derivation[0]["derivation_digest"] == "sha256:b16"
    assert derivation[0]["references"] == [
        {"kind": "source_frame", "stream_id": "stream-x", "frame_index": 12}]


def test_d5_a_row_with_no_derivation_recorded_says_so():
    """A hole is visible: the row is there, with its hole named."""
    model = _face_model()
    native = model["observations"][0]["payload"]["evidence_contract"]
    del native["derivation"]
    plan = _graph_plan({"predicate": "measurement",
                        "measurement_type": "person.region_frame"})
    answer = evidence_query.evidence_for(model, plan)
    derivation = answer["results"][0]["derivation"]
    assert derivation[0]["derivation_recorded"] is False
    assert derivation[0]["derivation_digest"] is None
