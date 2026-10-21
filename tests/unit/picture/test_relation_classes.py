from library.tools.relation_classes import (
    PREDICATES,
    RELATION_CLASSES,
    derive_physical_relations,
    derive_speaking_relations,
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
    assert score["true_positives"] == 1
    assert score["predicted"] == 1
    assert score["labeled"] == 1
    assert score["precision"] == 1.0
    assert score["recall"] == 1.0
    assert score["per_predicate"]["touching"]["recall"] == 1.0


def test_the_published_vocabulary_carries_the_programme_seven():
    """B-10 names the typed relationships; the engine publishes them."""
    programme = ("touching", "holding", "looking_at", "speaking",
                 "entering", "leaving", "interacting")
    for name in programme:
        assert name in PREDICATES, name
        assert PREDICATES[name]["relation_class"] in RELATION_CLASSES
    assert PREDICATES["touching"]["relation_class"] == "physical"
    assert PREDICATES["holding"]["relation_class"] == "physical"
    assert PREDICATES["looking_at"]["relation_class"] == "physical"
    assert PREDICATES["speaking"]["relation_class"] == "temporal"
    assert PREDICATES["entering"]["relation_class"] == "physical"
    assert PREDICATES["leaving"]["relation_class"] == "physical"
    assert PREDICATES["interacting"]["relation_class"] == "semantic"
    # Every published predicate says what it means and what derives it.
    for name, row in PREDICATES.items():
        assert row["semantics"]
        assert "derived_by" in row


def test_hand_landmarks_reach_the_deriver_from_either_row_shape():
    """D2: the hands were measured, but the row the deriver read carried
    none. A model that carries the landmarks on either row shape
    derives the contact."""
    model = _model()
    for row in model["observations"]:
        if row.get("modality") == "face_frame":
            row["modality"] = "face"
    relations = derive_physical_relations(model)
    assert [row["predicate"] for row in relations] == ["touching"]


def test_a_person_region_is_not_an_object_to_touch():
    """An M3d person mask is filed under an object entity; a hand inside
    it is the person's own body, never a contact with a thing."""
    model = _model()
    for sample in model["tracks"][1]["samples"]:
        sample["geometry"]["kind"] = "region"
    assert derive_physical_relations(model) == []


def test_a_sustained_contact_is_a_holding_not_a_longer_touching():
    model = _model()
    model["tracks"][1]["samples"] = [
        {"tick": tick, "geometry": {"bbox": [.4, .4, .6, .6]}}
        for tick in (100, 600, 1100)]
    model["observations"] = [
        row for row in model["observations"]
        if row["id"] not in ("cup-100", "cup-200")]
    model["observations"] += [
        {"id": f"cup-{tick}", "interval": {"clock_id": "clock",
                                          "start": tick, "end": tick + 1},
         "payload": {"track_id": "cup-track"}}
        for tick in (100, 600, 1100)]
    model["observations"] += [
        {"id": f"face-{tick}", "modality": "face_frame",
         "interval": {"clock_id": "clock", "start": tick, "end": tick + 1},
         "payload": {"hands": [{"joints": {"wrist": [.5, .5, .9]}}]}}
        for tick in (100, 600, 1100)]
    model["identity_links"] = [
        {"entity_id": "person", "observation_id": f"face-{tick}",
         "status": "verified"} for tick in (100, 600, 1100)]
    relations = derive_physical_relations(model)
    assert [row["predicate"] for row in relations] == ["holding"]


def _speaking_model():
    return {
        "entities": [{"id": "alice", "kind": "person"},
                     {"id": "bob", "kind": "person"}],
        "clocks": [{"id": "clock", "ticks_per_second": 1000}],
        "observations": [
            {"id": "turn-1", "modality": "voice_turn",
             "interval": {"clock_id": "clock", "start": 1000, "end": 2000},
             "payload": {"voice_track": "voice_001"},
             "provenance_id": "prov-1"},
            {"id": "face-bob", "modality": "face",
             "interval": {"clock_id": "clock", "start": 1500, "end": 2500},
             "payload": {"face_track": "face_002"}},
        ],
        "identity_links": [
            {"entity_id": "alice", "observation_id": "turn-1",
             "status": "verified"},
            {"entity_id": "bob", "observation_id": "face-bob",
             "status": "verified"},
        ],
    }


def test_speaking_is_a_co_occurrence_and_never_an_addressee():
    relations = derive_speaking_relations(_speaking_model())
    assert len(relations) == 1
    row = relations[0]
    assert row["predicate"] == "speaking"
    assert row["subject_id"] == "alice"
    assert row["object_id"] == "bob"
    assert row["relation_class"] == "temporal"
    assert row["status"] == "candidate"
    assert row["certainty"] == "inferred"
    assert row["evidence_ids"] == ["face-bob", "turn-1"]


def test_a_speaker_with_no_measured_presence_yields_no_speaking_edge():
    model = _speaking_model()
    model["observations"] = [row for row in model["observations"]
                              if row["id"] != "face-bob"]
    assert derive_speaking_relations(model) == []


def test_the_scorer_reports_each_predicate_on_its_own():
    relations = derive_physical_relations(_model())
    relations.append({**relations[0], "id": "relation-holding-1",
                      "predicate": "holding"})
    labels = {"model": _model(), "windows": [{"source_file": "IMG_1816.MOV",
        "contacts": [{"person_id": "P", "object_id": "C",
                      "start_seconds": .1, "end_seconds": .201}]}]}
    score = score_relations(relations, labels, {"person": "P", "cup": "C"},
                            predicates=("touching", "holding"))
    assert score["per_predicate"]["touching"]["recall"] == 1.0
    assert score["per_predicate"]["holding"]["recall"] == 1.0
    assert score["per_predicate"]["holding"]["predicted"] == 1


def test_a_person_to_person_contact_is_not_a_false_positive():
    """The M10 contact is between two people; the blind contact label
    is between a person and an object. They are different relations and
    the scorer must not call one a miss."""
    relations = derive_physical_relations(_model())
    relations[0] = {**relations[0], "object_id": "person"}
    labels = {"model": _model(), "windows": [{"source_file": "IMG_1816.MOV",
        "contacts": [{"person_id": "P", "object_id": "C",
                      "start_seconds": .1, "end_seconds": .201}]}]}
    kinds = {"person": "person", "cup": "object"}
    score = score_relations(relations, labels, {"person": "P", "cup": "C"},
                            entity_kinds=kinds)
    assert score["recall"] == 0.0
    assert score["predicted"] == 0
    assert score["precision"] is None
    without_kinds = score_relations(relations, labels,
                                    {"person": "P", "cup": "C"})
    assert without_kinds["precision"] == 0.0


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

