from library.tools.relation_classes import (
    derive_physical_relations,
    derive_temporal_relations,
    score_pickups,
    score_relations,
)


def _model():
    return {
        "assets": [{"id": "asset", "uri": "/media/IMG_1816.MOV"}],
        "clocks": [{"id": "clock", "domain": {"asset_id": "asset"}}],
        "entities": [
            {"id": "person", "kind": "person"},
            {"id": "cup", "kind": "object"},
            {"id": "phone", "kind": "object"},
        ],
        "tracks": [
            {"id": "person-track", "entity_id": "person",
             "clock_id": "clock", "provenance_id": "p",
             "samples": [{"tick": tick, "geometry": {"bbox": [0, 0, .5, 1]}}
                         for tick in (100, 200)]},
            {"id": "cup-track", "local_track_id": "sam/cup",
             "entity_id": "cup", "clock_id": "clock", "provenance_id": "o",
             "samples": [{"tick": tick, "geometry": {
                 "bbox": [.4, .4, .6, .6]}} for tick in (100, 200)]},
            {"id": "phone-track", "entity_id": "phone", "clock_id": "clock",
             "samples": [{"tick": tick} for tick in (100, 200)]},
        ],
        "observations": [
            {"id": "person-100", "interval": {"clock_id": "clock",
                                                    "start": 100, "end": 101},
             "payload": {"track_id": "person-track"}},
            {"id": "person-200", "interval": {"clock_id": "clock",
                                                    "start": 200, "end": 201},
             "payload": {"track_id": "person-track"}},
            {"id": "cup-100", "interval": {"clock_id": "clock",
                                                 "start": 100, "end": 101},
             "payload": {"track_id": "cup-track"}},
            {"id": "cup-200", "interval": {"clock_id": "clock",
                                                 "start": 200, "end": 201},
             "payload": {"track_id": "cup-track"}},
            {"id": "phone-100", "interval": {"clock_id": "clock",
                                                   "start": 100, "end": 101},
             "payload": {"track_id": "phone-track"}},
            {"id": "phone-200", "interval": {"clock_id": "clock",
                                                   "start": 200, "end": 201},
             "payload": {"track_id": "phone-track"}},
            {"id": "face-100", "modality": "face_frame",
             "interval": {"clock_id": "clock", "start": 100, "end": 101},
             "payload": {"hands": [{"joints": {"wrist": [.5, .5, .9]}}]}},
            {"id": "face-200", "modality": "face_frame",
             "interval": {"clock_id": "clock", "start": 200, "end": 201},
             "payload": {"hands": [{"joints": {"wrist": [.5, .5, .9]}}]}},
        ],
        "identity_links": [
            {"entity_id": "person", "observation_id": "face-100",
             "status": "verified"},
            {"entity_id": "person", "observation_id": "face-200",
             "status": "verified"},
        ],
    }


def test_temporal_relations_cite_both_track_observations():
    relations = derive_temporal_relations(_model())
    assert len(relations) == 3
    row = next(r for r in relations if r["predicate"] == "co_present"
               and {r["subject_id"], r["object_id"]} == {"cup", "phone"})
    assert row["relation_class"] == "temporal"
    assert row["status"] == "candidate"
    assert row["evidence_ids"] == ["cup-100", "cup-200", "phone-100", "phone-200"]


def test_physical_edges_are_candidates_backed_by_pose_and_object_evidence():
    relations = derive_physical_relations(_model())
    assert len(relations) == 1
    row = relations[0]
    assert row["subject_id"] == "person"
    assert row["object_id"] == "cup"
    assert row["relation_class"] == "physical"
    assert row["status"] == "candidate"
    assert set(row["evidence_ids"]) == {"face-100", "face-200", "cup-100", "cup-200"}


def test_relation_scorer_matches_classified_physical_edges_by_time_and_identity():
    model = _model()
    labels = {"model": model, "windows": [{"source_file": "IMG_1816.MOV",
        "contacts": [{"person_id": "P", "object_id": "C",
                      "predicate": "holding", "start_seconds": .1,
                      "end_seconds": .201}]}]}
    score = score_relations(derive_physical_relations(model), labels,
                            {"person": "P", "cup": "C"})
    assert score == {"true_positives": 1, "predicted": 1, "labeled": 1,
                     "precision": 1.0, "recall": 1.0}


def test_pickup_scorer_reports_recall_and_empty_denominator_as_unknown():
    labels = {"pickups": [{"object": "C", "clock_id": "clock",
                           "start_tick": 10, "end_tick": 20}]}
    event = {"kind": "pick_up", "participant_ids": ["cup"],
             "interval": {"clock_id": "clock", "start": 10, "end": 20}}
    assert score_pickups([event], labels, {"cup": "C"})["recall"] == 1.0
    assert score_pickups([], {"pickups": []}, {})["recall"] is None


def test_pickup_scorer_accepts_blind_window_labels_in_seconds():
    model = _model()
    labels = {"model": model, "windows": [{"source_file": "IMG_1816.MOV",
        "pickups": [{"object_id": "C", "person_id": "P",
                     "start_seconds": .01, "end_seconds": .02}]}]}
    event = {"kind": "pick_up", "participant_ids": ["cup"],
             "interval": {"clock_id": "clock", "start": 10, "end": 20}}
    score = score_pickups([event], labels, {"cup": "C"})
    assert score["labeled"] == 1
    assert score["recall"] == 1.0

