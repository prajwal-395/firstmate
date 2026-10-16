"""Cross-modal alignment fixtures are synthetic and require no model/media."""

import pytest

from library.tools.cross_modal_grounding import (
    add_active_speaker_candidates, apply_project_model, map_interval,
)


def _model(*, second_person: bool = False) -> dict:
    interval = {"clock_id": "clock-a", "start": 1000, "end": 2000}
    observations = [
        {"id": "utterance-0", "asset_id": "asset-a", "interval": interval,
         "modality": "speech", "payload": {"utterance_index": 0,
         "text": "look at this"}, "confidence": None,
         "provenance_id": "prov-a-m1"},
        {"id": "turn-0", "asset_id": "asset-a", "interval": interval,
         "modality": "voice_turn", "payload": {"voice_track": "voice-0"},
         "confidence": None, "provenance_id": "prov-a-m3b"},
        {"id": "lip-0", "asset_id": "asset-a", "interval": interval,
         "modality": "event_span", "payload": {"predicate": "speaking",
         "voice_track": "voice-0", "face_track": "face-0"},
         "confidence": None, "provenance_id": "prov-a-m7"},
        {"id": "face-evidence", "asset_id": "asset-a",
         "interval": interval, "modality": "face",
         "payload": {"face_track": "face-0"}, "confidence": None,
         "provenance_id": "prov-a-m3b"},
    ]
    links = [
        {"entity_id": "speaker-0", "observation_id": "turn-0",
         "method": "diarization"},
        {"entity_id": "person-0", "observation_id": "turn-0",
         "method": "speech_face_cooccurrence"},
        {"entity_id": "person-0", "observation_id": "face-evidence",
         "method": "face_embedding_cluster"},
    ]
    entities = [{"id": "speaker-0"}, {"id": "person-0"}]
    if second_person:
        links.append({"entity_id": "person-1", "observation_id": "turn-0",
                      "method": "speech_face_cooccurrence"})
        entities.append({"id": "person-1"})
    return {"observations": observations, "identity_links": links,
            "relations": [], "events": [], "entities": entities}


def test_utterance_is_grounded_only_when_voice_and_lip_evidence_agree():
    model = _model()

    counts = apply_project_model(model)

    assert counts == {"grounded_utterances": 1, "cross_modal_reports": 0}
    link = next(row for row in model["identity_links"]
                if row["method"] == "cross-modal-grounding-v1")
    assert link["entity_id"] == "person-0"
    assert link["status"] == "candidate"
    assert set(link["supporting_observation_ids"]) == {
        "turn-0", "lip-0", "face-evidence"}
    relation = model["relations"][0]
    assert (relation["subject_id"], relation["predicate"],
            relation["object_id"]) == (
                "speaker-0", "speaking_person_candidate", "person-0")
    assert relation["status"] == "candidate"
    assert relation["relation_class"] == "temporal"
    assert set(relation["evidence_ids"]) == {
        "utterance-0", "turn-0", "lip-0", "face-evidence"}


def test_voice_turn_without_lip_evidence_does_not_ground_person():
    model = _model()
    model["observations"] = [row for row in model["observations"]
                             if row["id"] != "lip-0"]

    counts = apply_project_model(model)

    assert counts["grounded_utterances"] == 0
    assert not [row for row in model["identity_links"]
                if row["method"] == "cross-modal-grounding-v1"]
    assert not model["relations"]


def test_disagreeing_lip_face_and_voice_attributions_are_reported():
    model = _model(second_person=True)
    next(row for row in model["observations"] if row["id"] == "lip-0")[
        "payload"]["face_track"] = "face-1"
    model["observations"].append({
        "id": "face-evidence-1", "asset_id": "asset-a",
        "interval": {"clock_id": "clock-a", "start": 1000, "end": 2000},
        "modality": "face", "payload": {"face_track": "face-1"},
        "confidence": None, "provenance_id": "prov-a-m3b"})
    model["identity_links"].append({
        "entity_id": "person-1", "observation_id": "face-evidence-1",
        "method": "face_embedding_cluster"})

    counts = apply_project_model(model)

    assert counts["grounded_utterances"] == 0
    report = next(row for row in model["observations"]
                  if row["modality"] == "cross_modal_report")
    assert report["payload"]["result"] == "disagreement"
    assert {"turn-0", "lip-0", "face-evidence-1"} <= set(
        report["payload"]["evidence_ids"])


def test_ambiguous_face_grounding_is_a_cited_report_not_a_verdict():
    model = _model(second_person=True)

    counts = apply_project_model(model)

    assert counts == {"grounded_utterances": 0, "cross_modal_reports": 1}
    report = next(row for row in model["observations"]
                  if row["modality"] == "cross_modal_report")
    assert report["payload"]["result"] == "disagreement"
    assert {"utterance-0", "turn-0", "lip-0"} <= set(
        report["payload"]["evidence_ids"])
    assert "verdict" not in report["payload"]
    assert all(row["status"] == "candidate" for row in model["relations"])


def test_dialogue_deictic_reference_points_to_cotimed_event_candidate():
    model = _model()
    model["entities"].append({"id": "object-cup"})
    model["observations"].append({
        "id": "object-observation", "asset_id": "asset-a",
        "interval": {"clock_id": "clock-a", "start": 1500, "end": 1800},
        "modality": "object_track_sample", "payload": {},
        "confidence": None, "provenance_id": "prov-a-vi05"})
    model["events"] = [{
        "id": "event-cup", "interval": {
            "clock_id": "clock-a", "start": 1500, "end": 1800},
        "kind": "object_interaction_candidate",
        "participant_ids": ["person-0", "object-cup"],
        "status": "candidate", "certainty": "inferred",
        "evidence_ids": ["object-observation"]}]

    apply_project_model(model)

    reference = next(row for row in model["relations"]
                     if row["predicate"] == "refers_to")
    assert (reference["subject_id"], reference["object_id"]) == (
        "person-0", "object-cup")
    assert reference["status"] == "candidate"
    assert set(reference["evidence_ids"]) == {
        "utterance-0", "object-observation"}


def test_m5_audio_event_overlap_is_cited_without_claiming_causation():
    model = _model()
    model["observations"].append({
        "id": "sound-0", "asset_id": "asset-a",
        "interval": {"clock_id": "clock-a", "start": 1700, "end": 2200},
        "modality": "sound_event", "payload": {"label": "door"},
        "confidence": 0.8, "provenance_id": "prov-a-m5"})
    model["events"] = [{
        "id": "event-0", "interval": {
            "clock_id": "clock-a", "start": 1500, "end": 1800},
        "kind": "object_state_change", "participant_ids": ["person-0"],
        "status": "candidate", "certainty": "inferred",
        "evidence_ids": ["lip-0"]}]

    counts = apply_project_model(model)

    report = next(row for row in model["observations"]
                  if row["modality"] == "cross_modal_report")
    assert report["payload"]["result"] == "temporal_overlap"
    assert report["payload"]["claim"] == "audio_visual_event_alignment"
    assert set(report["payload"]["evidence_ids"]) == {"sound-0", "lip-0"}
    assert counts["cross_modal_reports"] == 1


def test_m6_offsets_join_audio_and_event_from_two_camera_clocks():
    model = _model()
    model["observations"] = [
        {"id": "sound-b", "asset_id": "asset-b",
         "interval": {"clock_id": "clock-b", "start": 8800, "end": 9800},
         "modality": "sound_event", "payload": {"label": "door"},
         "confidence": 0.8, "provenance_id": "prov-b-m5"},
        {"id": "m6-a", "asset_id": "asset-a",
         "interval": {"clock_id": "clock-a", "start": 0, "end": 20000},
         "modality": "multicam_offset", "payload": {
             "group_id": "group-1", "reference_digest": "a",
             "offset_to_reference_seconds": 0.0}, "confidence": None,
         "provenance_id": "prov-a-m6"},
        {"id": "m6-b", "asset_id": "asset-b",
         "interval": {"clock_id": "clock-b", "start": 0, "end": 20000},
         "modality": "multicam_offset", "payload": {
             "group_id": "group-1", "reference_digest": "a",
             "offset_to_reference_seconds": 1.25}, "confidence": None,
         "provenance_id": "prov-b-m6"},
        {"id": "event-evidence", "asset_id": "asset-a",
         "interval": {"clock_id": "clock-a", "start": 10000, "end": 11000},
         "modality": "event_span", "payload": {}, "confidence": None,
         "provenance_id": "prov-a-m7"},
    ]
    model["events"] = [{
        "id": "event-a", "interval": {
            "clock_id": "clock-a", "start": 10000, "end": 11000},
        "kind": "object_state_change", "participant_ids": ["person-0"],
        "status": "candidate", "certainty": "inferred",
        "evidence_ids": ["event-evidence"]}]

    apply_project_model(model)

    report = next(row for row in model["observations"]
                  if row["modality"] == "cross_modal_report")
    assert report["interval"] == {
        "clock_id": "clock-a", "start": 10050, "end": 11000}
    assert set(report["payload"]["evidence_ids"]) == {
        "sound-b", "event-evidence", "m6-a", "m6-b"}


def test_synthetic_camera_offsets_align_speech_lips_and_audio():
    # Held-out C6 proxy: two podcast angles have a known 1.25 s source
    # offset. Audio and lip windows are intentionally stored on separate
    # source clocks and must meet on the reference clock.
    camera_a_speech = map_interval(10.0, 11.0, 0.0)
    camera_b_lips = map_interval(8.75, 9.75, 1.25)
    camera_b_sound = map_interval(8.8, 9.8, 1.25)

    assert camera_a_speech == camera_b_lips
    assert max(camera_a_speech[0], camera_b_sound[0]) < min(
        camera_a_speech[1], camera_b_sound[1])


@pytest.mark.parametrize("span,offset", [((1.0, 1.0), 0), ((2.0, 1.0), 0),
                                          ((0.0, 1.0), float("nan"))])
def test_invalid_interval_or_offset_refuses(span, offset):
    with pytest.raises(ValueError):
        map_interval(*span, offset)


def _asd_model(*, clocks: tuple[str, str] = ("clock-a", "clock-a"),
               transform_error: int = 0,
               separate_assets: bool = False) -> dict:
    voice_clock, face_clock = clocks
    model = _model()
    model["observations"] = [
        {"id": "turn-asd", "asset_id": "asset-a",
         "interval": {"clock_id": voice_clock, "start": 1000, "end": 2000},
         "modality": "voice_turn", "payload": {"voice_track": "voice-0"},
         "confidence": None, "provenance_id": "prov-turn"},
        {"id": "face-asd", "asset_id": "asset-b" if separate_assets else "asset-a",
         "interval": {"clock_id": face_clock, "start": 1000, "end": 2000},
         "modality": "face", "payload": {"face_track": "face-0"},
         "confidence": None, "provenance_id": "prov-face"},
        {"id": "score-asd", "asset_id": "asset-b" if separate_assets else "asset-a",
         "interval": {"clock_id": face_clock, "start": 1100, "end": 1900},
         "modality": "active_speaker_score",
         "payload": {"voice_track": "voice-0", "face_track": "face-0",
                     "raw_score": 0.91, "score_kind": "uncalibrated",
                     **({"voice_asset_id": "asset-a"} if separate_assets else {})},
         "confidence": None, "provenance_id": "prov-asd"},
    ]
    model["identity_links"] = [
        {"entity_id": "speaker-0", "observation_id": "turn-asd",
         "method": "diarization"},
        {"entity_id": "person-0", "observation_id": "face-asd",
         "method": "face_embedding_cluster"},
    ]
    if voice_clock != face_clock:
        model["time_transforms"] = [{
            "id": "transform-a-to-b", "from_clock": voice_clock,
            "to_clock": face_clock, "segments": [{
                "from": {"clock_id": voice_clock, "start": 0, "end": 5000},
                "to": {"clock_id": face_clock, "start": 0, "end": 5000},
                "slope": {"numerator": 1, "denominator": 1},
                "offset_ticks": 0, "uncertainty_ticks": transform_error,
                "invertible": True}], "provenance_id": "prov-transform"}]
    return model


def test_active_speaker_score_becomes_candidate_on_visible_clock():
    model = _asd_model()

    result = add_active_speaker_candidates(model, max_error_ticks=0)

    assert result == {"relations_added": 1,
                      "alignment_observations_added": 0, "joins_refused": 0}
    relation = model["relations"][0]
    assert relation["predicate"] == "speaking_person_candidate"
    assert relation["interval"] == {
        "clock_id": "clock-a", "start": 1100, "end": 1900}
    assert relation["status"] == "candidate"
    assert relation["certainty"] == "inferred"
    assert relation["relation_class"] == "temporal"
    assert relation["confidence"] is None
    assert {"score-asd", "turn-asd", "face-asd"} <= set(
        relation["evidence_ids"])


def test_active_speaker_cross_clock_join_cites_transform_and_observation():
    model = _asd_model(clocks=("clock-a", "clock-b"), transform_error=25)

    result = add_active_speaker_candidates(model, max_error_ticks=25)

    assert result == {"relations_added": 1,
                      "alignment_observations_added": 1, "joins_refused": 0}
    alignment = next(row for row in model["observations"]
                     if row["modality"] == "time_alignment")
    assert alignment["payload"]["time_transform_id"] == "transform-a-to-b"
    relation = model["relations"][0]
    assert relation["interval"]["clock_id"] == "clock-b"
    assert alignment["id"] in relation["evidence_ids"]


def test_active_speaker_can_join_audio_from_another_camera_asset():
    model = _asd_model(clocks=("clock-a", "clock-b"), separate_assets=True)

    result = add_active_speaker_candidates(model, max_error_ticks=0)

    assert result["relations_added"] == 1
    assert model["relations"][0]["interval"]["clock_id"] == "clock-b"
    assert {"turn-asd", "face-asd"} <= set(model["relations"][0]["evidence_ids"])


def test_active_speaker_refuses_missing_or_over_error_transform():
    model = _asd_model(clocks=("clock-a", "clock-b"), transform_error=26)

    result = add_active_speaker_candidates(model, max_error_ticks=25)

    assert result["joins_refused"] == 1
    assert not model["relations"]
