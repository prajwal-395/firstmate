"""Unit 3: evidence/identity migration proven on fixtures, never live data.

Every project here is built under ``tmp_path``: hand-written M0-M10 lane
documents plus 1.06 segmentation files in the real layout. Nothing reads
a real project, a real memory root, or a model.
"""
import json
from pathlib import Path

import pytest

from library.tools import project_model_evidence as evidence
from library.tools.project_layout import ProjectLayout
from library.tools.project_model import validate_project_model
from library.tools.project_model_store import (
    commit_project_model,
    load_project_model,
    write_parity_report,
)

D1 = "digest-aaa-001"
D2 = "digest-bbb-002"

EMB_A = [1.0, 0.0, 0.0, 0.0]
EMB_B = [0.0, 1.0, 0.0, 0.0]
EMB_C = [0.0, 0.0, 1.0, 0.0]
# The same voice on both sources, each already linked to a different
# person's face: a cross-source voice match never merges two persons.
EMB_VOICE = [0.5, 0.5, 0.5, 0.5]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _lane_docs(digest: str, faces: list[dict], voices: list[dict],
               links: list[dict]) -> dict[str, dict]:
    return {
        "source.json": {
            "content_digest": digest, "size_bytes": 100,
            "duration_seconds": 10.0, "video_streams": [{"index": 0}],
        },
        "transcript.words.json": {
            "content_digest": digest, "status": "transcribed",
            "instrument": {"arm": "fixture", "aligner": "fixture"},
            "utterances": [{"words": [
                {"start": 1.0, "end": 1.5, "text": "hello",
                 "confidence": 0.9}]}]},
        "speakers.json": {
            "content_digest": digest, "status": "measured",
            "speakers": voices, "instrument": {},
        },
        "persons.json": {
            "content_digest": digest,
            "frames": [{"t": 1.0,
                        "faces": [{"box": [0, 0, 10, 10]}],
                        "hands": []}],
        },
        "identity.json": {
            "content_digest": digest, "status": "measured",
            "face_match_threshold": 0.25,
            "faces": faces, "voices": voices,
            "speech_face_links": links,
            "instrument": {"face": "fixture-face", "voice": "fixture-voice",
                           "frame_pixels": [384, 216]},
        },
        "scenes.json": {
            "content_digest": digest, "status": "measured",
            "scenes": [{"start": 0.0, "end": 5.0, "location": "studio",
                        "type": "interview", "lighting": "soft",
                        "notable_features": []}],
            "scene_boundaries": [], "scene_coverage": None, "instrument": {},
        },
        "sound.json": {
            "content_digest": digest, "status": "measured",
            "sound_events": [{"label": "speech", "start": 1.0, "end": 2.0,
                              "confidence": 0.8}],
            "method": "panns",
        },
        "clock.json": {
            "content_digest": digest, "group_id": None,
            "reference_digest": None, "offset_to_reference_seconds": 0.0,
        },
        "events.json": {
            "content_digest": digest,
            "predicates": {
                "speaking": {"spans": [{"start": 1.0, "end": 2.0,
                                        "voice_track": "voice_001",
                                        "face_track": "face_001"}]},
                "on_screen": {"spans": [{"start": 0.0, "end": 5.0,
                                         "face_track": "face_001"}]}},
            "candidates": {
                "hand_near_mouth": {"spans": [
                    {"start": 1.0, "end": 2.0, "face_track": "face_001",
                     "frames": [{"t": 1.0}, {"t": 1.5}]}]}},
        },
        "verdicts.json": {
            "content_digest": digest,
            "verdicts": {"k1": {"statement": "hand near mouth",
                                "answer": True, "reason": "seen",
                                "model": "fixture",
                                "frames": [1.0, 1.5]}},
        },
        "world_model.json": {
            "content_digest": digest, "status": "built",
            "duration_seconds": 10.0, "lanes": ["m0", "m1"],
            "timeline": [],
        },
        "relationships.json": {
            "content_digest": digest,
            "relationships": {
                "holding": {"spans": [{"owners": ["face_001"],
                                       "start": 2.0, "end": 3.0}]},
                "pointing": {"spans": []}},
        },
    }


def _seg_doc(objects: list[dict]) -> dict:
    return {"video_path": "/nonexistent/clip.mov", "frame_count": 4,
            "resolution": [1080, 1920], "sample_fps": 2.0,
            "seed_note": "face_seeded", "objects": objects}


def _seg_subject(object_id: str = "obj_1") -> dict:
    return {"object_id": object_id, "label": "face_seeded_subject",
            "category": "person", "frames": [0, 1, 2, 3],
            "masks_rle": {},
            "bboxes": {str(i): [10, 10, 100, 100] for i in range(4)},
            "avg_area_ratio": 0.1}


def _seg_blob(object_id: str = "obj_9") -> dict:
    doc = _seg_subject(object_id)
    doc["label"] = "auto_object_1"
    doc["category"] = "unknown"
    return doc


def _write_m3e(memory: Path, digest: str, method: str) -> None:
    source = memory / digest
    (source / "frames").mkdir(parents=True, exist_ok=True)
    _write(source / "frames.index.json", {
        "content_digest": digest, "frames": [
            {"t": 1.0, "file": "frames/0001.jpg"},
            {"t": 2.0, "file": "frames/0002.jpg"}]})
    _write(source / "saliency.json", {
        "content_digest": digest, "status": "measured",
        "samples": [{"t": 1.0, "boxes": []},
                    {"t": 2.0, "boxes": None}],
        "instrument": {"method": method}})


def _write_m3c(memory: Path, digest: str, method: str) -> None:
    _write(memory / digest / "expressions.json", {
        "content_digest": digest, "status": "measured",
        "frames": [{"t": 1.0, "faces": []},
                    {"t": 2.0, "faces": [{"expression": "happy"}]}],
        "instrument": {"method": method}})


@pytest.fixture()
def fixture_project(tmp_path):
    project = tmp_path / "proj"
    memory = tmp_path / "memory"
    faces_d1 = [{"track_id": "face_001", "embedding": EMB_A,
                 "spans": [{"start": 0.0, "end": 5.0,
                            "box": [0, 0, 10, 10], "det_score": 0.9}]}]
    voices_d1 = [{"track_id": "voice_001", "embedding": EMB_VOICE,
                  "spans": [[1.0, 2.0]]}]
    faces_d2 = [
        {"track_id": "face_001", "embedding": EMB_B,
         "spans": [{"start": 0.0, "end": 5.0,
                    "box": [1, 1, 11, 11], "det_score": 0.8}]},
        {"track_id": "face_002", "embedding": EMB_C,
         "spans": [{"start": 0.0, "end": 5.0,
                    "box": [2, 2, 12, 12], "det_score": 0.7}]},
    ]
    voices_d2 = [{"track_id": "voice_001", "embedding": EMB_VOICE,
                  "spans": [[3.0, 4.0]]}]
    for digest, faces, voices in ((D1, faces_d1, voices_d1),
                                  (D2, faces_d2, voices_d2)):
        docs = _lane_docs(
            digest, faces, voices,
            [{"face_track": "face_001", "voice_track": "voice_001"}])
        for name, doc in docs.items():
            _write(memory / digest / name, doc)
    catalog = [
        {"clip_id": "clip_001", "source_file": "/nonexistent/a.mov",
         "path": "/nonexistent/a.mov"},
        {"clip_id": "clip_002", "source_file": "/nonexistent/b.mov",
         "path": "/nonexistent/b.mov"},
    ]
    _write(project / "pipeline_data.json", {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": catalog}},
        "source_fingerprints": {
            "clip_001": {"path": "/nonexistent/a.mov",
                         "content_digest": D1},
            "clip_002": {"path": "/nonexistent/b.mov",
                         "content_digest": D2}},
    })
    seg_dir = ProjectLayout(str(project)).step_dir(
        "object_segmentation", create=True)
    _write(seg_dir / "clip_001_segmentation.json",
           _seg_doc([_seg_subject(), _seg_blob()]))
    # clip_002's subject is ambiguous on purpose: two roster persons hold
    # D2 face spans covering t=0, so its subject mask must stay on the
    # object instance rather than guess a person.
    _write(seg_dir / "clip_002_segmentation.json",
           _seg_doc([_seg_subject()]))
    return {"project": project, "memory": memory}


def _migrate(fixture_project):
    return evidence.migrate_project_evidence(
        str(fixture_project["project"]),
        memory_root=str(fixture_project["memory"]))


def test_migration_validates_and_covers_the_catalog(fixture_project):
    before = {path: path.read_bytes()
              for path in fixture_project["memory"].rglob("*") if path.is_file()}
    model, report = _migrate(fixture_project)

    validate_project_model(model)
    uris = {asset["uri"] for asset in model["assets"]}
    assert uris == {"/nonexistent/a.mov", "/nonexistent/b.mov"}
    assert {clock["id"] for clock in model["clocks"]} == {
        f"clock-{D1}", f"clock-{D2}"}
    persons = [e for e in model["entities"] if e["kind"] == "person"]
    assert len(persons) == 3
    assert report["counts"]["observations"] > 0
    word = next(o for o in model["observations"]
                if o["modality"] == "speech_word")
    assert word["payload"]["evidence_contract"]["measurement_type"] == (
        "speech.word")
    source_metadata = [o for o in model["observations"]
                       if o["modality"] == "source_metadata"]
    assert len(source_metadata) == 2
    assert source_metadata[0]["payload"]["evidence_contract"]["freshness"] == (
        "unknown")
    assert all(o["payload"]["evidence_contract"]["measurement_type"] ==
               "media.source_metadata" for o in source_metadata)
    assert all(o["payload"]["evidence_contract"]["coverage"] == "complete"
               for o in source_metadata)
    assert all(o["payload"]["evidence_contract"]["contract_version"] == 1
               for o in model["observations"]
               if o["modality"] in ("face", "voice_turn"))
    assert report["counts"]["identity_links"] > 0
    assert report["counts"]["masks"] >= 3
    assert report["counts"]["tracks"] == 3
    assert report["counts"]["relations"] == 0
    # Read-only proof: not one lane byte changed.
    after = {path: path.read_bytes()
             for path in fixture_project["memory"].rglob("*") if path.is_file()}
    assert before == after


def test_migration_registers_each_m0_stream_and_technical_tables(
        fixture_project):
    """M0 stream identity and bounded source tables reach the Project Model."""
    from library.tools import source_memory

    memory = fixture_project["memory"] / D1
    _write(memory / source_memory.SLOT_SOURCE, {
        "content_digest": D1, "size_bytes": 100, "duration_seconds": 4.0,
        "video_streams": [{"index": 0, "codec_type": "video"}],
        "audio_streams": [{"index": 1, "codec_type": "audio"}],
        "streams": [
            {"index": 0, "codec_type": "video", "pix_fmt": "yuv420p10le",
             "color_transfer": "smpte2084",
             "capture_metadata_status": "unavailable"},
            {"index": 1, "codec_type": "audio", "channels": 6,
             "capture_metadata_status": "available",
             "capture_metadata": {"creation_time": "recorded"},
             "capture_metadata_provenance": {
                 "producer": "ffprobe", "basis": "recorded_container_metadata"}},
            {"index": 2, "codec_type": "subtitle", "codec": "mov_text"},
        ],
        "packet_table": [{"stream_index": 0, "pts": 1}],
        "frame_table": [{"stream_index": 0, "pts_time": "0.033"}],
        "timestamp_discontinuities": [{"stream_index": 0,
                                        "kind": "missing_pts"}],
        "file_integrity": {"probe_status": "warning",
                            "corrupt_packet_count": 1},
    })

    model, _ = _migrate(fixture_project)
    streams = {row["stream_index"]: row for row in model["streams"]
               if row["asset_id"] == f"asset-{D1}"}
    assert set(streams) == {0, 1, 2}
    assert streams[0]["metadata"]["color_transfer"] == "smpte2084"
    assert streams[1]["metadata"]["capture_metadata_status"] == "available"
    assert streams[1]["source_digest"] == D1
    assert streams[1]["provenance_id"]
    asset = next(row for row in model["assets"] if row["id"] == f"asset-{D1}")
    assert asset["metadata"]["packet_table"] == [{"stream_index": 0, "pts": 1}]
    assert asset["metadata"]["file_integrity"]["corrupt_packet_count"] == 1


def test_unmeasured_identity_stays_absent(fixture_project):
    model, _report = _migrate(fixture_project)

    persons = {e["id"] for e in model["entities"] if e["kind"] == "person"}
    voice_obs_links = [
        link for link in model["identity_links"]
        if link["method"] == "speech_face_cooccurrence"]
    assert voice_obs_links, "within-source voice links must migrate"
    for link in voice_obs_links:
        assert link["entity_id"] in persons
    # The same voice embedding on both sources mints no cross-digest
    # voice-to-person link (each voice is already linked to a different
    # person): every cooccurrence link stays inside its own digest's
    # provenance, and no voice match bridges the two persons.
    assert not [link for link in model["identity_links"]
                if link["method"] == "cross_source_voice_match"]
    for link in voice_obs_links:
        prov = next(o for o in model["observations"]
                    if o["id"] == link["observation_id"])["provenance_id"]
        assert prov in (f"prov-{D1}-m3b", f"prov-{D2}-m3b")
    # Blob-seeded objects carry no semantic class.
    blob_entities = [e for e in model["entities"]
                     if e["label"] == "auto_object_1"]
    assert len(blob_entities) == 1
    assert blob_entities[0]["object_class_id"] is None
    # clip_002's face-seeded subject stays on its object instance.
    subject_masks = [m for m in model["masks"]
                     if m["id"] == "mask-clip_002-obj_1"]
    assert len(subject_masks) == 1
    assert subject_masks[0]["entity_id"] == "object-clip_002-obj_1"
    # clip_001's subject resolves to the single D1 person.
    mask = next(m for m in model["masks"]
                if m["id"] == "mask-clip_001-obj_1")
    owner = next(e for e in model["entities"] if e["id"] == mask["entity_id"])
    assert owner["kind"] == "person"


def test_verified_spans_carry_their_verdicts(fixture_project):
    model, _report = _migrate(fixture_project)
    candidates = [o for o in model["observations"]
                  if o["modality"] == "event_candidate"]
    assert candidates, "M7 hand_near_mouth candidates must migrate"
    assert any(o["payload"].get("verified") is True for o in candidates)
    assert any((o["payload"].get("verdicts") or [])
               for o in candidates)


def test_sec_to_ticks_refuses_empty_intervals():
    assert evidence.sec_to_ticks(1.0, 2.0) == (1000, 2000)
    assert evidence.sec_to_ticks(2.0, 1.0) is None
    assert evidence.sec_to_ticks(1.0, 1.0) is None
    assert evidence.sec_to_ticks(float("nan"), 2.0) is None


def test_m3e_migrates_native_coverage_and_refuses_stale_method(
        fixture_project):
    from library.tools.attention_saliency import M3E_METHOD
    from library.tools.person_measurements import M3_METHOD

    fixture = fixture_project
    memory = fixture["memory"]
    _write_m3e(memory, D1, M3E_METHOD)
    _write(memory / D1 / "persons.json", {
        "content_digest": D1,
        "instrument": {"method": M3_METHOD},
        "frames": [{"t": 1.0, "faces": [], "hands": []},
                    {"t": 2.0, "faces": [{"box": [0.1, 0.1, 0.3, 0.4]}],
                     "hands": []}]})
    model, report = _migrate(fixture)
    face_frames = [o for o in model["observations"]
                   if o["modality"] == "face_frame"]
    assert len(face_frames) == 2
    assert face_frames[0]["payload"]["evidence_contract"]["value"][
        "face"] is None
    assert face_frames[0]["payload"]["evidence_contract"]["coverage"] == (
        "complete")
    observations = [o for o in model["observations"]
                    if o["modality"] == "saliency"]
    assert len(observations) == 2
    by_tick = {o["interval"]["start"]: o for o in observations}
    evidence_contract = by_tick[1000]["payload"]["evidence_contract"]
    assert evidence_contract["measurement_type"] == "image.salient_boxes"
    assert evidence_contract["coverage"] == "complete"
    assert evidence_contract["value"] == []
    assert evidence_contract["freshness"] == "fresh"
    assert by_tick[1000]["interval"] == {
        "clock_id": f"clock-{D1}", "start": 1000, "end": 1001}
    assert by_tick[2000]["payload"]["evidence_contract"]["coverage"] == (
        "unavailable")
    coverage = evidence.evidence_coverage(
        str(fixture["project"]), model, report,
        memory_root=str(memory))
    assert coverage["lanes"][D1]["freshness"]["m3e"] == "fresh"
    assert coverage["lanes"][D1]["freshness"]["m3"] == "fresh"
    assert coverage["lanes"][D1]["native_migrated_observations"]["m3"] == 2
    assert sum(coverage["lanes"][D1][
        "native_migrated_observations"].values()) == 6

    _write_m3e(memory, D1, "retired-attention-method")
    stale_model, stale_report = _migrate(fixture)
    assert not [o for o in stale_model["observations"]
                if o["modality"] == "saliency"]
    assert any("M3e stale" in note for note in stale_report["notes"])
    stale_coverage = evidence.evidence_coverage(
        str(fixture["project"]), stale_model, stale_report,
        memory_root=str(memory))
    assert stale_coverage["lanes"][D1]["freshness"]["m3e"] == "stale"


def test_m0_m3b_m3c_m3d_migrate_freshness_and_absence(fixture_project):
    from library.tools.attention_saliency import M3E_METHOD
    from library.tools.expression_classifier import M3C_METHOD
    from library.tools.person_measurements import M3_METHOD
    from library.tools.person_regions import M3D_METHOD, write_sidecar

    fixture = fixture_project
    memory = fixture["memory"]
    _write_m3e(memory, D1, M3E_METHOD)
    # Identity is rewritten after M2 so the fixture models a current M3b.
    identity_path = memory / D1 / "identity.json"
    _write(identity_path, json.loads(identity_path.read_text(encoding="utf-8")))
    _write(memory / D1 / "persons.json", {
        "content_digest": D1,
        "instrument": {"method": M3_METHOD},
        "frames": [{"t": 1.0, "faces": [], "hands": []},
                    {"t": 2.0, "faces": [{"box": [0.1, 0.1, 0.3, 0.4]}],
                     "hands": []}]})
    _write_m3c(memory, D1, M3C_METHOD)
    sidecar = write_sidecar(memory / D1, [], [2, 2])
    _write(memory / D1 / "regions.json", {
        "content_digest": D1, "grid": [2, 2], "sidecar": sidecar,
        "samples": [{"t": 1.0, "instances": []},
                    {"t": 2.0, "instances": None}],
        "tracks": [], "instrument": {"method": M3D_METHOD}})

    model, report = _migrate(fixture)
    by_modality = {}
    for obs in model["observations"]:
        by_modality.setdefault(obs["modality"], []).append(obs)
    assert by_modality["source_metadata"]
    assert by_modality["face"][0]["payload"]["evidence_contract"][
        "measurement_type"] == "person.face_box"
    assert by_modality["voice_turn"][0]["payload"]["evidence_contract"][
        "measurement_type"] == "person.voice_turn"
    expression = by_modality["expression_frame"]
    assert expression[0]["payload"]["evidence_contract"]["value"] == []
    regions = by_modality["region_frame"]
    assert regions[0]["payload"]["evidence_contract"]["value"] == []
    assert regions[1]["payload"]["evidence_contract"]["coverage"] == (
        "unavailable")
    coverage = evidence.evidence_coverage(
        str(fixture["project"]), model, report, memory_root=str(memory))
    assert coverage["lanes"][D1]["freshness"]["m3b"] == "fresh"
    assert coverage["lanes"][D1]["freshness"]["m3c"] == "fresh"
    assert coverage["lanes"][D1]["freshness"]["m3d"] == "fresh"

    _write_m3c(memory, D1, "retired-expression-method")
    stale_m3c, stale_report = _migrate(fixture)
    assert not [o for o in stale_m3c["observations"]
                if o["modality"] == "expression_frame"]
    stale_coverage = evidence.evidence_coverage(
        str(fixture["project"]), stale_m3c, stale_report,
        memory_root=str(memory))
    assert stale_coverage["lanes"][D1]["freshness"]["m3c"] == "stale"

    _write(memory / D1 / "regions.json", {
        "content_digest": D1, "grid": [2, 2], "sidecar": sidecar,
        "samples": [{"t": 1.0, "instances": []}], "tracks": [],
        "instrument": {"method": "retired-region-method"}})
    stale_m3d, stale_report = _migrate(fixture)
    assert not [o for o in stale_m3d["observations"]
                if o["modality"] == "region_frame"]
    stale_coverage = evidence.evidence_coverage(
        str(fixture["project"]), stale_m3d, stale_report,
        memory_root=str(memory))
    assert stale_coverage["lanes"][D1]["freshness"]["m3d"] == "stale"


def _model_with_editorial(model: dict, person_id: str) -> dict:
    model = json.loads(json.dumps(model))
    model["clocks"].extend([
        {"id": "clock-timeline",
         "domain": {"kind": "timeline", "timeline_id": "timeline-1"},
         "ticks_per_second": 1000, "frame_rate": None},
        {"id": "clock-render",
         "domain": {"kind": "render", "render_id": "render-1"},
         "ticks_per_second": 1000, "frame_rate": None},
    ])
    model["assets"].append({"id": "asset-render", "kind": "render",
                            "uri": "renders/out.mov", "content_digest": None,
                            "metadata": {}, "provenance": []})
    model["clip_uses"].append({
        "id": "use-1", "asset_id": f"asset-{D1}",
        "source_interval": {"clock_id": f"clock-{D1}",
                            "start": 1000, "end": 2000},
        "timeline_interval": {"clock_id": "clock-timeline",
                              "start": 0, "end": 1000},
        "entity_links": [person_id], "role": "a_roll",
        "intent_id": None})
    model["frame_maps"].append({
        "id": "map-1", "from_clock": "clock-timeline",
        "to_clock": "clock-render",
        "segments": [{"from": {"clock_id": "clock-timeline",
                               "start": 0, "end": 1000},
                      "to": {"clock_id": "clock-render",
                             "start": 0, "end": 1000},
                      "mapping_kind": "one_to_one",
                      "rate": {"numerator": 1, "denominator": 1},
                      "source_clip_use": "use-1"}]})
    model["receipts"].append({"id": "receipt-1", "status": "success",
                              "renderer": "fixture", "input_revision": "rev-1",
                              "output_asset_id": "asset-render",
                              "details": {}})
    model["renders"].append({"id": "render-1", "input_revision": "rev-1",
                             "output_asset_id": "asset-render",
                             "settings": {}, "receipt_id": "receipt-1",
                             "frame_map_id": "map-1"})
    validate_project_model(model)
    return model


def test_resolve_person_traverses_masks_aroll_and_render(fixture_project):
    model, _report = _migrate(fixture_project)
    d1_person = next(
        e["id"] for e in model["entities"]
        if e["kind"] == "person" and any(
            m["entity_id"] == e["id"] and m["id"] == "mask-clip_001-obj_1"
            for m in model["masks"]))
    full = _model_with_editorial(model, d1_person)

    resolved = evidence.resolve_person(full, d1_person)

    assert resolved is not None
    assert resolved["entity"]["id"] == d1_person
    assert resolved["observations"], "person must own observations"
    assert any(m["id"] == "mask-clip_001-obj_1"
               for m in resolved["masks"])
    assert [u["id"] for u in resolved["clip_uses"]] == ["use-1"]
    assert len(resolved["rendered_ranges"]) == 1
    rendered = resolved["rendered_ranges"][0]
    assert rendered["render_id"] == "render-1"
    assert rendered["render_interval"] == {"clock_id": "clock-render",
                                           "start": 0, "end": 1000}
    assert resolved["provenance"], "every hop must carry provenance"


def test_resolve_person_refuses_the_unmeasured(fixture_project):
    model, _report = _migrate(fixture_project)
    assert evidence.resolve_person(model, "person_999") is None
    assert evidence.resolve_person(model, "") is None


def test_coverage_and_bridge_parity_dual_read(fixture_project):
    project = fixture_project["project"]
    model, report = _migrate(fixture_project)
    commit_project_model(project, model, expected_revision=0,
                         reason="unit 3 fixture")

    parity = write_parity_report(project)
    kinds = [m["kind"] for m in parity["mismatches"]]
    assert "catalog_assets_missing_from_model" not in kinds

    _revision, stored = load_project_model(project)
    coverage = evidence.evidence_coverage(str(project), stored, report,
                                          memory_root=str(
                                              fixture_project["memory"]))
    assert coverage["lanes"][D1]["present"]["m9"] is True
    assert coverage["lanes"][D1]["migrated_observations"]["m1"] == 1
    assert coverage["lanes"][D1]["native_migrated_observations"]["m1"] == 1
    assert coverage["lanes"][D1]["legacy_only_migrated_observations"]["m4"] > 0
    assert coverage["counts"]["observations"] == len(
        stored["observations"])
    assert coverage["absent_identity"], "withheld links must be named"
    assert coverage["dropped"] == report["dropped"]


def test_a_voice_matched_across_sources_links_its_turns_to_the_person(
        tmp_path):
    """D2 shows no face, so its voice turns reached no person until a
    cross-source voice match joined them to the person D1 links that
    voice to. The match must reach the model as an identity link, or the
    person's speech on the second camera stays unattributed there."""
    project, memory = tmp_path / "proj", tmp_path / "memory"
    faces_d1 = [{"track_id": "face_001", "embedding": EMB_A,
                 "spans": [{"start": 0.0, "end": 5.0,
                            "box": [0, 0, 10, 10], "det_score": 0.9}]}]
    voice = {"track_id": "voice_001", "embedding": EMB_VOICE,
             "spans": [[1.0, 30.0]]}
    for digest, faces, links in (
            (D1, faces_d1, [{"face_track": "face_001",
                             "voice_track": "voice_001"}]),
            (D2, [], [])):
        for name, doc in _lane_docs(digest, faces, [voice], links).items():
            _write(memory / digest / name, doc)
    catalog = [{"clip_id": f"clip_00{i}", "source_file": f"/nonexistent/{n}",
                "path": f"/nonexistent/{n}"}
               for i, n in ((1, "a.mov"), (2, "b.mov"))]
    _write(project / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": catalog}},
        "source_fingerprints": {
            "clip_001": {"path": "/nonexistent/a.mov", "content_digest": D1},
            "clip_002": {"path": "/nonexistent/b.mov", "content_digest": D2}}})

    model, _report = evidence.migrate_project_evidence(
        str(project), memory_root=str(memory))

    validate_project_model(model)
    persons = [e["id"] for e in model["entities"] if e["kind"] == "person"]
    matched = [link for link in model["identity_links"]
               if link["method"] == "cross_source_voice_match"]
    assert matched and {link["entity_id"] for link in matched} == set(persons)
    for link in matched:
        obs = next(o for o in model["observations"]
                   if o["id"] == link["observation_id"])
        assert obs["provenance_id"] in (f"prov-{D2}-m3b", f"prov-{D2}-m1b")


def test_resolve_person_cli_reads_a_model_file(fixture_project, capsys):
    model, _report = _migrate(fixture_project)
    d1_person = next(
        e["id"] for e in model["entities"]
        if e["kind"] == "person" and any(
            m["entity_id"] == e["id"] and m["id"] == "mask-clip_001-obj_1"
            for m in model["masks"]))
    full = _model_with_editorial(model, d1_person)
    model_file = fixture_project["project"] / "model.json"
    model_file.write_text(json.dumps(full), encoding="utf-8")

    assert evidence.main(["resolve-person", str(model_file),
                          "--ref", d1_person]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entity"]["id"] == d1_person
    assert evidence.main(["resolve-person", str(model_file),
                          "--ref", "nobody-here"]) == 1


def test_resolve_person_traverses_the_e2e_qualification_fixture():
    """The Unit 3 query resolves the shared E2E contract fixture: a
    measured person from source observations through masks and A-roll
    to a rendered range, with provenance. Read-only on the fixture."""
    from library.tools.project_model import FIXTURE_ROOT, read_project_model

    model = read_project_model(FIXTURE_ROOT / "expected-project-model.json")
    resolved = evidence.resolve_person(model, "person-ben")

    assert resolved is not None
    assert resolved["entity"]["label"] == "Ben (fictional)"
    assert {o["modality"] for o in resolved["observations"]} == {
        "face", "speech"}
    assert [m["id"] for m in resolved["masks"]] == ["mask-ben"]
    assert [u["id"] for u in resolved["clip_uses"]] == ["use-ben"]
    assert len(resolved["rendered_ranges"]) == 1
    assert resolved["rendered_ranges"][0]["render_id"] == "render-e2e"
    assert resolved["provenance"], "every hop must carry provenance"


def test_migrate_cli_writes_and_commits(fixture_project, capsys):
    project = fixture_project["project"]
    out = project / "model.json"
    assert evidence.main(["migrate", str(project), "--memory-root",
                          str(fixture_project["memory"]),
                          "--out", str(out), "--commit"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 1
    revision, _stored = load_project_model(project)
    assert revision == 1
