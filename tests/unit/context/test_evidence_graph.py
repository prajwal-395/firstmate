"""Graph read layer over synthetic Project Model dicts.

Every fixture below is constructed in-test: no media, no Resolve, no
model passes. Contradiction thresholds are exercised at their
calibration values and reported once in the lane scorecard.
"""
import pytest

from library.tools import evidence_graph
from library.tools.evidence_graph import EvidenceIndex


def _obs(obs_id, modality, clock, start, end, payload=None,
         provenance="prov-d-m1", asset="asset-1", certainty="measured"):
    return {"id": obs_id, "asset_id": asset,
            "interval": {"clock_id": clock, "start": start, "end": end},
            "modality": modality, "payload": payload or {},
            "provenance_id": provenance, "certainty": certainty}


def _model():
    return {
        "id": "model-1", "schema_version": 1,
        "clocks": [{"id": "c1", "ticks_per_second": 1000}],
        "entities": [
            {"id": "person-1", "kind": "person", "label": "Host"},
            {"id": "speaker-a", "kind": "speaker", "label": "Speaker A"},
            {"id": "speaker-b", "kind": "speaker", "label": "Speaker B"},
            {"id": "mug", "kind": "object", "label": "Mug"},
        ],
        "observations": [
            _obs("o1", "voice_turn", "c1", 0, 2000,
                 {"voice_track": "v1"}),
            _obs("o2", "voice_turn", "c1", 1000, 3000,
                 {"voice_track": "v2"}),
            _obs("o3", "voice_turn", "c1", 5000, 6000,
                 {"voice_track": "v3"}),
            _obs("o4", "object.detection", "c1", 0, 2000,
                 {"object_id": "mug", "box": [0.1, 0.1, 0.3, 0.3]}),
            _obs("o5", "object.detection", "c1", 500, 2500,
                 {"object_id": "phone", "box": [0.12, 0.12, 0.3, 0.3]}),
            _obs("o6", "object.detection", "c1", 5000, 6000,
                 {"object_id": "mug", "box": [0.7, 0.7, 0.1, 0.1]}),
            _obs("o7", "scene", "c1", 0, 6000,
                 {"text": "host discusses revenue while chart visible"}),
        ],
        "identity_links": [
            {"id": "l1", "entity_id": "speaker-a", "observation_id": "o1",
             "method": "diarization", "status": "verified",
             "supporting_observation_ids": ["o1"],
             "contradicting_observation_ids": []},
            {"id": "l2", "entity_id": "speaker-b", "observation_id": "o2",
             "method": "diarization", "status": "candidate",
             "supporting_observation_ids": [],
             "contradicting_observation_ids": []},
            {"id": "l3", "entity_id": "speaker-a", "observation_id": "o3",
             "method": "diarization", "status": "candidate",
             "supporting_observation_ids": [],
             "contradicting_observation_ids": []},
            {"id": "l4", "entity_id": "person-1", "observation_id": "o7",
             "method": "face", "status": "verified",
             "supporting_observation_ids": ["o7"],
             "contradicting_observation_ids": ["o9"]},
        ],
        "events": [
            {"id": "e1", "kind": "speaking", "status": "verified",
             "certainty": "measured",
             "interval": {"clock_id": "c1", "start": 0, "end": 2000},
             "participant_ids": ["person-1"], "evidence_ids": ["o1"]},
            {"id": "e2", "kind": "speaking", "status": "verified",
             "certainty": "measured",
             "interval": {"clock_id": "c1", "start": 1000, "end": 3000},
             "participant_ids": ["person-1"], "evidence_ids": ["o2"]},
            {"id": "e3", "kind": "on_screen", "status": "verified",
             "certainty": "measured",
             "interval": {"clock_id": "c1", "start": 1000, "end": 3000},
             "participant_ids": ["person-1"], "evidence_ids": ["o4"]},
        ],
        "relations": [
            {"id": "r1", "subject_id": "person-1", "object_id": "speaker-a",
             "relation_type": "same_person", "status": "candidate",
             "certainty": "inferred",
             "interval": {"clock_id": "c1", "start": 0, "end": 6000},
             "evidence_ids": ["o1"]},
            {"id": "r2", "subject_id": "speaker-a", "object_id": "mug",
             "relation_type": "holding", "status": "candidate",
             "certainty": "inferred",
             "interval": {"clock_id": "c1", "start": 0, "end": 2000},
             "evidence_ids": ["o4"]},
        ],
        "tracks": [],
    }


def test_index_matches_linear_scan_on_adversarial_intervals():
    model = {
        "id": "adv", "schema_version": 1, "clocks": [],
        "entities": [], "observations": [
            _obs("giant", "scene", "c1", 0, 100000, {}),
            _obs("left", "scene", "c1", 0, 1000, {}),
            _obs("right", "scene", "c1", 1000, 2000, {}),
            _obs("same-a", "scene", "c1", 500, 1500, {}),
            _obs("same-b", "scene", "c1", 500, 800, {}),
            _obs("point", "scene", "c1", 3000, 3001, {}),
            _obs("other-clock", "scene", "c2", 0, 500, {}),
        ],
        "identity_links": [], "events": [], "relations": [],
        "tracks": [],
    }
    index = EvidenceIndex(model)
    windows = [(s, s + w) for s in range(0, 4000, 137)
               for w in (1, 999, 1000, 50000)]
    for start, end in windows:
        expected = [obs for obs in model["observations"]
                    if obs["interval"].get("clock_id") == "c1"
                    and obs["interval"]["start"] < end
                    and obs["interval"]["end"] > start]
        expected.sort(key=lambda obs: (obs["interval"]["start"],
                                       obs["interval"]["end"]))
        assert [obs["id"] for obs in
                index.observations_overlapping("c1", start, end)] == [
            obs["id"] for obs in expected]
    for tick in range(0, 4000, 211):
        expected = [obs for obs in model["observations"]
                    if obs["interval"].get("clock_id") == "c1"
                    and obs["interval"]["start"] <= tick
                    and tick < obs["interval"]["end"]]
        expected.sort(key=lambda obs: (obs["interval"]["start"],
                                       obs["interval"]["end"]))
        assert [obs["id"] for obs in index.observations_at("c1", tick)] == [
            obs["id"] for obs in expected]
    # Adjacent halves share no tick: a window ending exactly at the
    # boundary sees the left half only.
    assert [obs["id"] for obs in
            index.observations_overlapping("c1", 0, 1000)] == [
        "left", "giant", "same-b", "same-a"]
    assert index.observations_overlapping("c9", 0, 100) == []
    assert index.observations_at("c9", 50) == []


def test_overlapping_keeps_start_end_order_on_shuffled_insert():
    rows = [_obs(f"o{i}", "scene", "c1", s, e, {})
            for i, (s, e) in enumerate([
                (900, 1000), (0, 5000), (100, 200), (100, 150),
                (4000, 4001), (200, 900)])]
    model = {"id": "shuf", "schema_version": 1, "clocks": [],
             "entities": [], "observations": list(reversed(rows)),
             "identity_links": [], "events": [], "relations": [],
             "tracks": []}
    found = EvidenceIndex(model).observations_overlapping("c1", 0, 5000)
    keys = [(obs["interval"]["start"], obs["interval"]["end"])
            for obs in found]
    assert keys == sorted(keys)
    assert {obs["id"] for obs in found} == {obs["id"] for obs in rows}


def test_temporal_predicates_use_the_bisected_index():
    index = EvidenceIndex(_model())
    at = index.observations_at("c1", 1500)
    assert {obs["id"] for obs in at} == {"o1", "o2", "o4", "o5", "o7"}
    assert [obs["id"] for obs in index.observations_at("c1", 4000)] == ["o7"]
    window = index.observations_overlapping("c1", 2000, 5000)
    assert {obs["id"] for obs in window} == {"o2", "o5", "o7"}
    with pytest.raises(ValueError):
        index.observations_overlapping("c1", 5, 5)


def test_resolve_entity_browses_everything_attributed():
    resolved = EvidenceIndex(_model()).resolve_entity("speaker-a")
    assert resolved["found"] is True
    assert {obs["id"] for obs in resolved["observations"]} == {"o1", "o3"}
    assert [link["id"] for link in resolved["identity_links"]] == ["l1", "l3"]
    assert {rel["id"] for rel in resolved["relations"]} == {"r1", "r2"}
    by_label = EvidenceIndex(_model()).resolve_entity("Host")
    assert by_label["entity"]["id"] == "person-1"
    assert {event["id"] for event in by_label["events"]} == {"e1", "e2", "e3"}
    missing = EvidenceIndex(_model()).resolve_entity("nobody")
    assert missing["found"] is False


def test_resolve_event_carries_evidence_and_participants():
    resolved = EvidenceIndex(_model()).resolve_event("e1")
    assert resolved["found"] is True
    assert [obs["id"] for obs in resolved["evidence"]] == ["o1"]
    assert [row["id"] for row in resolved["participants"]] == ["person-1"]
    assert EvidenceIndex(_model()).resolve_event("missing")["found"] is False


def test_traverse_walks_relations_and_links():
    walked = EvidenceIndex(_model()).traverse("person-1", hops=2)
    assert walked["found"] is True
    assert {"speaker-a", "mug", "person-1"} <= {
        node["id"] for node in walked["nodes"]}
    assert walked["edges"], "expected relation and link edges"
    assert EvidenceIndex(_model()).traverse("nobody")["found"] is False
    with pytest.raises(ValueError):
        EvidenceIndex(_model()).traverse("person-1", hops=0)


def test_text_search_requires_every_term():
    index = EvidenceIndex(_model())
    hits = index.search_text("host revenue chart")
    assert [hit["row"]["id"] for hit in hits] == ["o7"]
    assert index.search_text("host absent-word") == []
    with pytest.raises(ValueError):
        index.search_text("   ")


def test_spatial_predicate_scans_boxes_in_a_window():
    index = EvidenceIndex(_model())
    hits = index.observations_overlapping_box(
        "c1", [0.1, 0.1, 0.3, 0.3], start=0, end=3000, min_iou=0.5)
    assert {obs["id"] for obs in hits} == {"o4", "o5"}
    far = index.observations_overlapping_box("c1", [0.1, 0.1, 0.3, 0.3],
                                             start=4000, end=7000, min_iou=0.5)
    assert far == []
    with pytest.raises(ValueError):
        index.observations_overlapping_box("c1", [0.1, 0.1, 0.0, 0.3])


def test_index_matches_linear_scan_on_every_window():
    model = _model()
    index = EvidenceIndex(model)
    for start in range(0, 6000, 250):
        expected = {obs["id"] for obs in model["observations"]
                    if not (obs["interval"]["end"] <= start
                            or obs["interval"]["start"] >= start + 1000)}
        assert {obs["id"] for obs in
                index.observations_overlapping("c1", start, start + 1000)} == expected
    for tick in range(0, 6000, 333):
        expected = {obs["id"] for obs in model["observations"]
                    if obs["interval"]["start"] <= tick < obs["interval"]["end"]}
        assert {obs["id"] for obs in index.observations_at("c1", tick)} == expected


def test_multi_observation_question_links_shared_spans():
    answer = EvidenceIndex(_model()).answer_requirements([
        {"modality": "voice_turn", "entity_id": "speaker-a"},
        {"modality": "object.detection", "text": "mug"},
    ])
    assert answer["unmet"] == []
    assert answer["groups"], "expected a linked speaker-plus-object group"
    group = answer["groups"][0]
    assert set(group["observation_ids"]) == {"o1", "o4"}
    assert group["provenance_ids"] == ["prov-d-m1"]
    assert group["interval"]["clock_id"] == "c1"


def test_unmet_requirements_are_named_not_papered():
    answer = EvidenceIndex(_model()).answer_requirements([
        {"modality": "voice_turn"},
        {"modality": "no_such_modality"},
    ])
    assert answer["unmet"] == [1]
    assert answer["groups"] == []
    with pytest.raises(ValueError):
        EvidenceIndex(_model()).answer_requirements([])


def test_select_returns_only_requested_evidence():
    fragment = EvidenceIndex(_model()).select(
        collections=["observations", "entities"],
        ids={"observations": ["o1"]})
    assert fragment["id"] == "model-1"
    assert [obs["id"] for obs in fragment["observations"]] == ["o1"]
    assert len(fragment["entities"]) == 4
    assert "events" not in fragment
    with pytest.raises(ValueError):
        EvidenceIndex(_model()).select(collections=["nope"])


def test_speaker_contradiction_needs_shared_ticks():
    kinds = {(c["kind"], tuple(c.get("observation_ids", [])))
             for c in evidence_graph.find_contradictions(_model())}
    assert ("speaker", ("o1", "o2")) in kinds
    # o3 shares a speaker with o1 but never overlaps it: no contradiction.
    assert not [c for c in evidence_graph.find_contradictions(_model())
                if c["kind"] == "speaker" and "o3" in c["observation_ids"]]


def test_object_contradiction_needs_box_agreement():
    found = [c for c in evidence_graph.find_contradictions(_model())
             if c["kind"] == "object"]
    assert len(found) == 1
    assert found[0]["observation_ids"] == ["o4", "o5"]
    assert found[0]["identities"] == {"o4": "mug", "o5": "phone"}


def test_event_duplicates_share_kind_participant_and_span():
    found = [c for c in evidence_graph.find_contradictions(_model())
             if c["kind"] == "event_duplicate"]
    assert len(found) == 1
    assert found[0]["event_ids"] == ["e1", "e2"]


def test_recorded_contradictions_surface_verbatim():
    found = [c for c in evidence_graph.find_contradictions(_model())
             if c["kind"] == "recorded"]
    assert found and found[0]["contradicting_observation_ids"] == ["o9"]


def test_contradiction_scope_pays_for_the_queried_span_only():
    scoped = evidence_graph.find_contradictions(
        _model(), scope={"clock_id": "c1", "start": 4000, "end": 4500})
    assert {c["kind"] for c in scoped} == {"recorded"}
    narrow = evidence_graph.find_contradictions(
        _model(), scope={"clock_id": "c1", "start": 0, "end": 1500})
    assert {"speaker", "object", "event_duplicate", "recorded"} == {
        c["kind"] for c in narrow}
    with pytest.raises(ValueError):
        evidence_graph.find_contradictions(_model(), scope={"clock_id": "c1"})
    contradiction = [c for c in evidence_graph.find_contradictions(_model())
                     if c["kind"] == "speaker"][0]
    request = evidence_graph.verification_request(contradiction)
    assert request["status"] == "needs_review"
    assert request["required_lanes"] == [request["required_lanes"][0]]
    ambiguity = request["resolvable_ambiguities"][0]
    assert ambiguity["start_s"] == pytest.approx(1.0)
    assert ambiguity["end_s"] == pytest.approx(2.0)
    assert set(request) >= {"resolvable_ambiguities", "required_lanes", "scope"}


def test_absence_vocabulary_splits_silence_from_assertion():
    assert evidence_graph.classify_absence(
        "present", has_explicit_negative=False,
        coverage_complete=True)["state"] == "present"
    assert evidence_graph.classify_absence(
        "confidently_absent", has_explicit_negative=True,
        coverage_complete=True)["state"] == "absent"
    assert evidence_graph.classify_absence(
        "confidently_absent", has_explicit_negative=False,
        coverage_complete=True)["state"] == "not_observed"
    assert evidence_graph.classify_absence(
        "not_yet_analysed", has_explicit_negative=False,
        coverage_complete=False)["state"] == "not_analyzed"
    assert evidence_graph.classify_absence(
        "inconclusive", has_explicit_negative=False,
        coverage_complete=False)["state"] == "inconclusive"
    assert evidence_graph.classify_absence(
        "confidently_absent", has_explicit_negative=True,
        coverage_complete=False)["state"] == "inconclusive"
    with pytest.raises(ValueError):
        evidence_graph.classify_absence(
            "maybe", has_explicit_negative=False, coverage_complete=True)


def test_query_and_graph_share_absence_vocabulary():
    from library.tools import evidence_query

    args = {"has_explicit_negative": False, "coverage_complete": True}
    graph = evidence_graph.classify_absence("confidently_absent", **args)
    query = evidence_query.classify_absence("confidently_absent", **args)
    assert graph == query


def test_answer_uncertainty_decomposes_instead_of_modelling():
    rows = [{"coverage": ["complete"], "freshness": ["fresh"],
             "clock_error_ticks": 0, "confidence_kind": "rank"},
            {"coverage": ["complete"], "freshness": ["fresh"],
             "clock_error_ticks": 500, "confidence_kind": "rank"}]
    report = evidence_graph.answer_uncertainty(rows, contradictions=1)
    assert report["results"] == 2
    assert report["coverage_fraction"] == 1.0
    assert report["freshness_fraction"] == 1.0
    assert report["worst_clock_error_ticks"] == 500
    assert report["summary"] == 0.3333
    empty = evidence_graph.answer_uncertainty([])
    assert empty["summary"] == 0.0


def test_confidence_audit_names_rows_that_lost_their_type():
    model = _model()
    model["observations"] = [
        _obs("c1", "scene", "c1", 0, 1000, {}),
        {**_obs("c2", "scene", "c1", 0, 1000, {}), "confidence": 0.7},
        {**_obs("c3", "scene", "c1", 0, 1000, {}), "confidence": 0.7,
         "confidence_detail": {"value": 0.7, "kind": "rank",
                               "producer": "rf-detr"}},
        {**_obs("c4", "scene", "c1", 0, 1000, {}), "confidence": 0.9,
         "confidence_detail": {"value": 0.9,
                               "kind": "calibrated_probability",
                               "producer": "mfa",
                               "calibration_version": "heldout"}},
    ]
    model["tracks"] = [{"id": "t1", "clock_id": "c1", "samples": [
        {"tick": 0, "confidence": 0.4},
        {"tick": 10, "confidence": 0.4,
         "confidence_detail": {"value": 0.4, "kind": "cosine",
                               "producer": "clip"}}]}]
    report = evidence_graph.audit_confidence_loss(model)
    assert report["untyped"] == ["c2", "tracks[t1].samples[0]"]
    assert report["calibrated"] == ["c4"]
    assert report["raw"] == {"cosine": ["tracks[t1].samples[1]"],
                             "rank": ["c3"]}
    with pytest.raises(ValueError):
        evidence_graph.audit_confidence_loss([])
