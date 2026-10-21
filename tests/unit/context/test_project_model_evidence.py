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


def _write_m3fgm4m5(memory: Path, digest: str) -> None:
    from library.tools import depth, person_regions, region_stats, source_memory
    from library.tools.analysis.sound_event_pipeline import METHOD as M5_METHOD

    source = memory / digest
    source.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, timestamp in enumerate((1.0, 2.0, 3.0)):
        relative = f"frames/{index:04d}.jpg"
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture frame")
        frames.append({"t": timestamp, "file": relative})
    _write(source / "frames.index.json", {
        "content_digest": digest, "frames": frames})
    sidecar = person_regions.write_sidecar(source, [[4, 0]], [2, 2])
    regions = {
        "content_digest": digest, "grid": [2, 2], "sidecar": sidecar,
        "tracks": [{"track_id": "region_001"}],
        "samples": [{"t": 1.0, "instances": []},
                    {"t": 2.0, "instances": [{"track": "region_001",
                                               "mask": 0}]},
                    {"t": 3.0, "instances": None}],
        "instrument": {"method": person_regions.M3D_METHOD},
    }
    _write(source / "regions.json", regions)
    _write(source / "region_stats.json", {
        "content_digest": digest, "status": "measured", "grid": [2, 2],
        "outside": "cells no person instance holds",
        "samples": [
            {"t": 1.0, "outside": {}, "regions": []},
            {"t": 2.0, "outside": {"luma_p50": 10},
             "regions": [{"track": "region_001", "mask": 0,
                          "inside": {"luma_p50": 100,
                                     "sharpness": 12.0}}]},
            {"t": 3.0, "regions": None,
             "unmeasured_because": "fixture unavailable"}],
        "instrument": {"method": region_stats.M3F_METHOD,
                       "m3d_sidecar_sha256": sidecar["sha256"]},
    })
    _write(source / "depth.json", {
        "content_digest": digest, "status": "measured", "grid": [2, 2],
        "direction": "brighter-is-nearer",
        "samples": [
            {"t": 1.0, "regions": []},
            {"t": 2.0, "regions": [{"track": "region_001", "mask": 0,
                                     "median_depth": 0.75}]},
            {"t": 3.0, "regions": None,
             "unmeasured_because": "fixture unavailable"}],
        "instrument": {"method": depth.M3G_METHOD,
                       "m3d_sidecar_sha256": sidecar["sha256"],
                       "depth_percentiles": {"p02": 0.0, "p98": 1.0}},
    })
    _write(source / "scenes.json", {
        "content_digest": digest, "status": "measured",
        "scenes": [{"start": 0.0, "end": 5.0, "location": "studio",
                    "type": "interview", "lighting": "soft",
                    "notable_features": []}],
        "scene_boundaries": [{"time": 4.0, "score": 0.7,
                              "type": "scene_change"}],
        "scene_cinematography": {
            "method": "scene-cinematography-measurements-v1",
            "status": "measured",
            "scenes": [{"id": "scene-0001", "start": 0.0, "end": 5.0,
                        "certainty": "interpreted",
                        "location": "studio", "type": "interview",
                        "boundary_basis_ids": ["m4_scene:semantic:0"],
                        "attributes": {"lighting_level": {
                            "value": "balanced", "evidence_ids": ["brightness:proxy:0"]}}}],
            "shots": [{"id": "shot-0001", "start": 0.0, "end": 5.0,
                       "certainty": "measured",
                       "boundary_basis_ids": [], "attributes": {
                           "camera_movement": {"value": "static",
                                                "evidence_ids": ["camera_motion:proxy:0"]}}}],
            "subshots": [{"id": "shot-0001-sub-01", "start": 0.0,
                           "certainty": "inferred",
                           "end": 5.0, "parent_shot_id": "shot-0001",
                           "attributes": {}}],
            "sequences": [{"id": "sequence-0001", "start": 0.0, "end": 5.0,
                            "certainty": "interpreted",
                            "scene_ids": ["scene-0001"],
                            "basis_measurement_ids": ["m4_scene:semantic:0"]}],
            "measurement_catalog": [
                {"id": "m4_scene:semantic:0", "lane": "m4_scene", "time": 0.0,
                 "value": {"location": "studio"}, "method": "m4-scene-profile"},
                {"id": "brightness:proxy:0", "lane": "brightness", "time": 0.0,
                 "value": 0.5, "method": "proxy"},
                {"id": "camera_motion:proxy:0", "lane": "camera_motion", "time": 0.0,
                 "value": "static", "method": "proxy"}],
        },
        "instrument": {
            "scene_pass": source_memory.M4_SCENE_PASS_METHOD,
            "boundary_method": source_memory.M4_BOUNDARY_METHOD},
    })
    _write(source / "sound.json", {
        "content_digest": digest, "status": "measured",
        "sound_events": [], "method": M5_METHOD,
    })


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


def test_model_provenance_is_attached_to_project_model_scene_evidence(
        fixture_project):
    from library.tools import source_memory

    scenes_path = fixture_project["memory"] / D1 / "scenes.json"
    scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
    model_record = {
        "format": "model_evidence_provenance/1",
        "evidence_ids": [f"m4-window:{D1}:0:5000"],
        "implementation_id": "fixture.scene_model",
        "checkpoint_revision": "fixture-rev",
        "license_context": {"approval_status": "approved"},
        "workload_purpose": "commercial_client",
        "output_reuse": {"class": "commercial"},
        "generated_at": "2026-10-17T00:00:00Z",
    }
    scenes["instrument"].update({
        "scene_pass": source_memory.M4_SCENE_PASS_METHOD,
        "boundary_method": source_memory.M4_BOUNDARY_METHOD,
    })
    scenes["window_coverage"] = [{
        "start_s": 0.0, "end_s": 5.0, "status": "complete",
        "producer_version": "fixture-scene-v1", "source_digest": D1,
        "model_provenance": model_record,
        "producer_project_id": "fixture-project",
        "producer_project_root": str(fixture_project["project"]),
    }]
    _write(scenes_path, scenes)

    model, _report = _migrate(fixture_project)

    scene = next(row for row in model["observations"]
                 if row["provenance_id"] == f"prov-{D1}-m4"
                 and row["modality"] == "scene")
    assert scene["payload"]["model_provenance"] == model_record


def _m4_scene_record(model, digest=D1):
    """The M4 scene description's evidence record, as the model stores it."""
    return next(
        obs["payload"]["evidence_contract"]
        for obs in model["observations"]
        if obs["provenance_id"] == f"prov-{digest}-m4"
        and obs["payload"].get("evidence_contract", {}).get(
            "measurement_type") == "video.scene_segment")


def test_a_scene_description_is_a_sampled_reading_that_names_its_model(
        fixture_project):
    """A description is read from sampled frames and attributed to the
    model that read them. Claiming COMPLETE coverage said the reading
    accounted for the whole window, and a query can then read a sampled
    reading as an exhaustive one."""
    from library.tools import source_memory

    scenes_path = fixture_project["memory"] / D1 / "scenes.json"
    scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
    scenes["instrument"].update({
        "scene_pass": source_memory.M4_SCENE_PASS_METHOD,
        "boundary_method": source_memory.M4_BOUNDARY_METHOD,
        "model_id": "mlx-community/gemma-4-12b-it-4bit",
        "sampling": {"frames_per_call": 32, "decode_fps": 2.0,
                     "window_s": 10.0,
                     "windows": {"scene": [
                         {"start": 0.0, "end": 5.0, "frames": 10,
                          "decode_fps": 2.0, "effective_fps": 2.0}]}},
    })
    scenes["scene_coverage"] = {
        "described_s": 5.0, "total_s": 10.0, "ratio": 0.5,
        "undescribed_ranges": [[5.0, 10.0]]}
    _write(scenes_path, scenes)

    model, _report = _migrate(fixture_project)

    record = _m4_scene_record(model)
    assert record["coverage"] == "sampled"
    assert record["coverage_samples"] == 10
    assert record["coverage_sample_rate_hz"] == 2.0
    assert record["producer_version"] == "mlx-community/gemma-4-12b-it-4bit"
    assert record["temporal_coverage"] == [
        {"clock_id": f"clock-{D1}", "start": 0, "end": 5000}]
    scene = next(row for row in model["observations"]
                 if row["provenance_id"] == f"prov-{D1}-m4"
                 and row["modality"] == "scene")
    scope = scene["payload"]["description_coverage"]
    assert scope["clip_described_ratio"] == 0.5
    assert scope["clip_undescribed_ranges"] == [[5.0, 10.0]]
    assert scope["frames_read"] == 10


def test_a_scene_description_never_claims_complete_coverage(
        fixture_project):
    """The defect this pins: a sampled reading filed as a complete
    observation of the window it was read from."""
    from library.tools import source_memory

    scenes_path = fixture_project["memory"] / D1 / "scenes.json"
    scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
    scenes["instrument"]["scene_pass"] = source_memory.M4_SCENE_PASS_METHOD
    scenes["window_coverage"] = [{
        "start_s": 0.0, "end_s": 5.0, "status": "complete",
        "producer_version": "fixture-scene-v1", "source_digest": D1,
    }]
    _write(scenes_path, scenes)
    model, _report = _migrate(fixture_project)

    record = _m4_scene_record(model)
    assert record["coverage"] != "complete"
    assert record["coverage_status"].startswith("sampled")


def test_m1_utterance_is_grounded_through_m3b_voice_and_m7_lips(
        fixture_project, monkeypatch):
    from library.tools import footage_analysis

    monkeypatch.setattr(footage_analysis, "slot_is_fresh",
                        lambda _lane, _digest, _root: True)
    transcript_path = (fixture_project["memory"] / D1
                       / "transcript.words.json")
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript["utterances"][0]["text"] = "look at this"
    transcript["utterances"][0]["start"] = 1.0
    transcript["utterances"][0]["end"] = 1.5
    _write(transcript_path, transcript)

    model, _report = _migrate(fixture_project)

    utterance = next(row for row in model["observations"]
                     if row["modality"] == "speech"
                     and row["payload"].get("text") == "look at this")
    assert any(row["payload"].get("predicate") == "speaking"
               for row in model["observations"]
               if row["modality"] == "event_span"), _report["notes"]
    assert any(row["modality"] == "face"
               for row in model["observations"]), _report["notes"]
    grounding = next(row for row in model["identity_links"]
                     if row["observation_id"] == utterance["id"]
                     and row["method"] == "cross-modal-grounding-v1")
    assert grounding["status"] == "candidate"
    assert len(grounding["supporting_observation_ids"]) == 3
    relation = next(row for row in model["relations"]
                    if row["predicate"] == "speaking_person_candidate")
    assert utterance["id"] in relation["evidence_ids"]
    validate_project_model(model)


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
    relations = model["relations"]
    assert report["counts"]["relations"] == len(relations) == 1
    assert relations[0]["relation_class"] in {"temporal", "physical"}
    assert relations[0]["status"] == "candidate"
    assert relations[0]["evidence_ids"]
    # Read-only proof: not one lane byte changed.
    after = {path: path.read_bytes()
             for path in fixture_project["memory"].rglob("*") if path.is_file()}
    assert before == after


def test_migration_preserves_typed_raw_confidence_and_derivation(fixture_project):
    from library.tools.evidence_graph import audit_confidence_loss

    model, _report = _migrate(fixture_project)
    scored = [row for row in model["observations"]
              if row.get("confidence") is not None]
    assert scored
    assert all(row["confidence_detail"]["value"] == row["confidence"]
               and row["confidence_detail"]["kind"] == "raw_score"
               and row["confidence_detail"]["producer"]
               for row in scored)
    assert audit_confidence_loss(model)["untyped"] == []
    word = next(row for row in model["observations"]
                if row["modality"] == "speech_word")
    derivation = word["payload"]["evidence_contract"]["derivation"]
    assert derivation["producer_version"]
    assert derivation["source_revision"] == D1
    assert derivation["preprocessing"]["method"]


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


def test_vi03_migrates_sampled_defects_as_measurements(fixture_project):
    from library.tools import source_memory

    source_memory.write_image_defects(D1, {
        "content_digest": D1, "method": "image_defects_v1",
        "sampling": {"rate_hz": 2, "sample_count": 4,
                     "sample_size": [180, 320], "coverage": "sampled",
                     "interval_s": [0.0, 2.0]},
        "signals": {
            "temporal_noise": {"values": [0.01, 0.02, 0.01]},
            "compression_blockiness": {"values": [0.1, 0.2, 0.1, 0.0]},
            "sharpness_proxy_laplacian_variance": [120.0, 121.0, 118.0, 5.0],
            "black_frame_sample_indices": [0],
            "frozen_frame_sample_indices": [2],
            "flicker_sample_indices": [3],
        }}, root=fixture_project["memory"])

    model, report = _migrate(fixture_project)
    observations = [row for row in model["observations"]
                    if row["provenance_id"] == f"prov-{D1}-vi03"]
    assert len(observations) == 23
    assert {row["payload"]["metric"] for row in observations} == {
        "image.temporal_noise.residual",
        "image.compression.blockiness_proxy", "image.frame.black",
        "image.frame.frozen", "image.flicker.alternation",
        "image.sharpness.laplacian_variance"}
    assert all(row["payload"]["sharpness_cause_claimed"] is False
               for row in observations)
    evidence_rows = [row["payload"]["evidence_contract"]
                     for row in observations]
    assert all(row["coverage"] == "sampled" for row in evidence_rows)
    assert all(row["references"][0]["kind"] == "source_sample"
               for row in evidence_rows)
    assert report["counts"]["observations"] >= len(observations)


def test_vi03_clipping_fractions_migrate_as_exposure_measurements(
        fixture_project):
    from library.tools import source_memory

    source_memory.write_image_defects(D1, {
        "content_digest": D1, "method": "image_defects_v1",
        "sampling": {"rate_hz": 2, "sample_count": 4,
                     "sample_size": [180, 320], "coverage": "sampled",
                     "interval_s": [0.0, 2.0]},
        "signals": {
            "temporal_noise": {"values": [0.01, 0.02, 0.01]},
            "compression_blockiness": {"values": [0.1, 0.2, 0.1, 0.0]},
            "sharpness_proxy_laplacian_variance": [120.0, 121.0, 118.0, 5.0],
            "clipping_underexposed_fraction": {
                "values": [0.0, 0.0, 0.5, 0.9]},
            "clipping_overexposed_fraction": {
                "values": [0.0, 0.01, 0.0, 0.0]},
            "black_frame_sample_indices": [0],
            "frozen_frame_sample_indices": [2],
            "flicker_sample_indices": [3],
        }}, root=fixture_project["memory"])

    model, _ = _migrate(fixture_project)
    observations = [row for row in model["observations"]
                    if row["provenance_id"] == f"prov-{D1}-vi03"]
    # The 23 pre-existing observations plus one per sample per
    # clipping signal. A record written before clipping existed
    # migrates unchanged (see the test above): absence is skipped,
    # never read as zero.
    assert len(observations) == 31
    metrics = {row["payload"]["metric"] for row in observations}
    assert "image.exposure.underexposed_fraction" in metrics
    assert "image.exposure.overexposed_fraction" in metrics
    clipped = [row for row in observations if row["payload"]["metric"]
               == "image.exposure.underexposed_fraction"]
    assert [row["payload"]["value"] for row in clipped] == [0.0, 0.0, 0.5, 0.9]


def test_a_measured_pause_carries_its_reading_and_its_reason(
        fixture_project):
    """The migrated gap says what it carries, not just how long it is.

    A gap row that carries only a duration asks the reader to judge it
    with nothing to judge by. The reading is on the row, with the
    measurement that produced it.
    """
    from library.tools.analysis import sound_event_pipeline

    source = fixture_project["memory"] / D1
    _write(source / "transcript.words.json", {
        "content_digest": D1, "status": "transcribed",
        "instrument": {"arm": "fixture", "aligner": "fixture"},
        "utterances": [{"text": "we shipped it and it worked", "words": [
            {"start": 1.0, "end": 1.5, "text": "we"},
            {"start": 1.6, "end": 2.1, "text": "shipped"},
            {"start": 2.2, "end": 2.7, "text": "it"}]}]})
    _write(source / "sound.json", {
        "content_digest": D1, "status": "measured",
        "method": sound_event_pipeline.METHOD,
        "sound_events": [{"label": "Applause", "start": 1.5, "end": 1.7,
                          "confidence": 0.9}]})

    model, _report = _migrate(fixture_project)

    pauses = [o for o in model["observations"]
              if o["modality"] == "speech_pause"]
    assert pauses, "the gap between two words is a measured pause"
    reading = pauses[0]["payload"]["reading"]
    assert reading["verdict"] == "linguistically_important"
    assert "event_occupied" in reading["markers"]
    assert reading["coverage"]["sound_events"] == "measured"


def test_a_sound_class_reads_present_and_absent_and_uncertain(
        fixture_project):
    """A class the clip measured, a class it measured as absent, and a
    class nobody measured are three different answers.

    D1's sound slot carries the current producer, so its classes are
    present or absent. D2's carries a superseded method, so its
    classes say nothing at all.
    """
    from library.tools.analysis import sound_event_pipeline

    _write(fixture_project["memory"] / D1 / "sound.json", {
        "content_digest": D1, "status": "measured",
        "method": sound_event_pipeline.METHOD,
        "sound_events": [{"label": "Applause", "start": 1.5, "end": 1.7,
                          "confidence": 0.9}]})

    model, _report = _migrate(fixture_project)

    rows = [o for o in model["observations"]
            if o["modality"] == "sound_class"]
    by_class: dict = {}
    for row in rows:
        by_class.setdefault(row["payload"]["sound_class"], []).append(row)
    assert set(by_class) == {"impact", "applause", "laughter", "music",
                             "environmental"}
    assert sorted({r["payload"]["state"]
                   for r in by_class["applause"]}) == ["present", "uncertain"]
    assert sorted({r["payload"]["state"]
                   for r in by_class["laughter"]}) == ["absent", "uncertain"]
    measured = [r for r in by_class["laughter"]
                if r["payload"]["state"] == "absent"][0]
    assert measured["payload"]["evidence_contract"]["coverage"] == "complete"
    uncertain = [r for r in by_class["laughter"]
                 if r["payload"]["state"] == "uncertain"][0]
    assert uncertain["payload"]["evidence_contract"]["coverage"] == \
        "unavailable"


def test_unmeasured_identity_stays_absent(fixture_project):
    # Cross-source voice identity only activates for tracks with the
    # measured minimum speech duration. These same-embedding tracks are
    # face-linked to different persons, so the resulting conflict must
    # remain a candidate link and preserve the face-backed contradiction.
    for digest in (D1, D2):
        identity_path = fixture_project["memory"] / digest / "identity.json"
        identity_doc = json.loads(identity_path.read_text(encoding="utf-8"))
        identity_doc["voices"][0]["spans"] = [[0.0, 22.0]]
        _write(identity_path, identity_doc)
        speakers_path = fixture_project["memory"] / digest / "speakers.json"
        speakers_doc = json.loads(speakers_path.read_text(encoding="utf-8"))
        speakers_doc["speakers"][0]["spans"] = [[0.0, 22.0]]
        _write(speakers_path, speakers_doc)

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
    conflicts = [link for link in model["identity_links"]
                 if link["method"] ==
                 "cross_source_voice_match_candidate"]
    assert len(conflicts) == 2
    observations = {row["id"]: row for row in model["observations"]}
    for link in conflicts:
        assert link["status"] == "candidate"
        assert len(link["supporting_observation_ids"]) == 2
        assert link["contradicting_observation_ids"]
        assert all(observations[obs_id]["modality"] == "face"
                   for obs_id in link["contradicting_observation_ids"])
        assert link["scope"]["start"] < link["scope"]["end"]
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


def test_same_looking_object_tracks_keep_separate_scoped_identity(fixture_project):
    """Class and appearance are not object identity; tracker evidence is
    scoped to its own track and never joins another same-looking instance."""
    seg_path = (ProjectLayout(str(fixture_project["project"]))
                .step_dir("object_segmentation")
                / "clip_001_segmentation.json")
    doc = json.loads(seg_path.read_text(encoding="utf-8"))
    twin = _seg_subject("obj_2")
    twin["label"] = "face_seeded_subject"
    twin["category"] = "person"
    doc["objects"].append(twin)
    _write(seg_path, doc)

    model, _report = _migrate(fixture_project)
    same_class = {entity["id"] for entity in model["entities"]
                  if entity["kind"] == "object"
                  and entity["object_class_id"] == "class-person"}
    assert {"object-clip_001-obj_1", "object-clip_001-obj_2",
            "object-clip_002-obj_1"} <= same_class
    migrated_tracks = {track["id"]: track for track in model["tracks"]}
    for object_id in ("obj_1", "obj_2"):
        entity = next(row for row in model["entities"]
                      if row["id"] == f"object-clip_001-{object_id}")
        track = next(row for row in migrated_tracks.values()
                     if row["entity_id"] == entity["id"])
        assert entity["identity_scope"] == "asset"
        assert track["local_track_id"] == f"clip_001/{object_id}"

    continuity = [link for link in model["identity_links"]
                  if link["method"] == "sam2_track_continuity"]
    assert len(continuity) == 4
    assert sum(row["modality"] == "object_track_sample"
               for row in model["observations"]) == 16
    by_entity = {link["entity_id"]: link for link in continuity}
    assert set(by_entity) >= {"object-clip_001-obj_1",
                              "object-clip_001-obj_2",
                              "object-clip_002-obj_1"}
    observations = {row["id"]: row for row in model["observations"]}
    for entity_id in ("object-clip_001-obj_1", "object-clip_001-obj_2",
                      "object-clip_002-obj_1"):
        link = by_entity[entity_id]
        assert link["observation_id"] in link["supporting_observation_ids"]
        assert link["scope"]["clock_id"] == observations[
            link["observation_id"]]["interval"]["clock_id"]
        assert all(observations[obs_id]["payload"]["track_id"]
                   == f"track-{entity_id.removeprefix('object-')}"
                   for obs_id in link["supporting_observation_ids"])


def test_verified_spans_carry_their_verdicts(fixture_project):
    model, _report = _migrate(fixture_project)
    candidates = [o for o in model["observations"]
                  if o["modality"] == "event_candidate"]
    assert candidates, "M7 hand_near_mouth candidates must migrate"
    assert any(o["payload"].get("verified") is True for o in candidates)
    assert any((o["payload"].get("verdicts") or [])
               for o in candidates)
    events = model["events"]
    event = next(item for item in events
                 if item["id"] == f"event-{candidates[0]['id']}")
    assert event["kind"] == "hand_near_mouth"
    assert event["status"] == "verified"
    assert event["evidence_ids"] == [candidates[0]["id"]]


def test_one_frame_m7_candidate_never_becomes_verified(fixture_project):
    fixture = fixture_project
    _write(fixture["memory"] / D1 / "events.json", {
        "content_digest": D1,
        "predicates": {"speaking": {"spans": []}, "on_screen": {"spans": []}},
        "candidates": {"hand_raise": {"spans": [
            {"start": 1.0, "end": 1.5, "frames": [{"t": 1.0}],
             "face_track": "face_001"}]}}})
    _write(fixture["memory"] / D1 / "verdicts.json", {
        "content_digest": D1,
        "verdicts": {"one": {"statement": "hand raised", "answer": True,
                                "frames": [1.0]}}})
    model, _report = _migrate(fixture)
    assert model["events"][0]["status"] == "candidate"


def test_project_model_action_sequence_keeps_contacts_and_state_changes(
        fixture_project):
    # Synthetic proxy sequence: source measurements below stand in for a
    # cup-contact, lift and set-down sequence; they make no footage claim.
    model, _report = _migrate(fixture_project)
    from library.tools.project_model import validate_project_model

    person_id = next(item["id"] for item in model["entities"]
                     if item["kind"] == "person")
    cup_id = "object-clip_001-obj_1"
    sample_ids = [item["id"] for item in model["observations"]
                  if item["modality"] == "object_track_sample"]
    contact_id = next(item["id"] for item in model["observations"]
                      if item["modality"] == "event_candidate")
    assert sample_ids and any(item["id"] == cup_id for item in model["entities"])

    model["events"] = [
        {"id": "event-hand-contacts-cup", "interval": {
            "clock_id": f"clock-{D1}", "start": 1000, "end": 1500},
         "kind": "contact", "participant_ids": [person_id, cup_id],
         "status": "candidate", "certainty": "inferred",
         "evidence_ids": [contact_id], "confidence": None},
        {"id": "event-cup-lifted", "interval": {
            "clock_id": f"clock-{D1}", "start": 1500, "end": 2500},
         "kind": "object_state_change", "participant_ids": [person_id, cup_id],
         "status": "candidate", "certainty": "inferred",
         "evidence_ids": [sample_ids[0]], "confidence": None,
         "state_changes": [{"entity_id": cup_id, "property": "support",
                             "from": "surface", "to": "held",
                             "evidence_ids": [sample_ids[0]]}]},
        {"id": "event-cup-set-down", "interval": {
            "clock_id": f"clock-{D1}", "start": 2500, "end": 3500},
         "kind": "object_state_change", "participant_ids": [person_id, cup_id],
         "status": "candidate", "certainty": "inferred",
         "evidence_ids": [sample_ids[1]], "confidence": None,
         "state_changes": [{"entity_id": cup_id, "property": "support",
                             "from": "held", "to": "surface",
                             "evidence_ids": [sample_ids[1]]}]},
    ]
    validate_project_model(model)


def test_vi05_track_motion_emits_candidate_state_change_with_two_samples(
        fixture_project):
    seg_path = (ProjectLayout(str(fixture_project["project"]))
                .step_dir("object_segmentation")
                / "clip_001_segmentation.json")
    document = json.loads(seg_path.read_text(encoding="utf-8"))
    obj = document["objects"][0]
    obj["bboxes"]["2"] = [30, 10, 100, 100]
    obj["bboxes"]["3"] = [30, 10, 100, 100]
    _write(seg_path, document)
    model, _report = _migrate(fixture_project)
    event = next(item for item in model["events"]
                 if item["kind"] == "object_position_change")
    assert event["status"] == "candidate"
    assert event["certainty"] == "measured"
    assert len(event["evidence_ids"]) == 2
    change = event["state_changes"][0]
    assert change["entity_id"] == "object-clip_001-obj_1"
    assert change["from"] != change["to"]
    assert change["evidence_ids"] == event["evidence_ids"]


def test_m10_relation_candidates_keep_two_participants_and_evidence(
        fixture_project):
    fixture = fixture_project
    _write_m6m7m8m10(fixture["memory"], D2)
    relation_path = fixture["memory"] / D2 / "relationships.json"
    relation_doc = json.loads(relation_path.read_text(encoding="utf-8"))
    relation_doc["relationships"]["pointing"]["spans"] = [{
        "owners": ["face_001", "face_002"], "start": 3.0, "end": 4.0}]
    _write(relation_path, relation_doc)
    model, _report = _migrate(fixture)
    relation = next(item for item in model["relations"]
                    if item["predicate"] == "pointing")
    assert relation["status"] == "candidate"
    assert relation["subject_id"] != relation["object_id"]
    assert len(relation["evidence_ids"]) == 1
    validate_project_model(model)


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
    # VI-05 attaches native evidence to every segmentation observation:
    # D1's two objects carry 8 track samples plus one appearance and
    # one visibility row each, so segmentation contributes 12. FI-13
    # speech records and the shared cross-source link add six more.
    assert coverage["lanes"][D1][
        "native_migrated_observations"]["segmentation"] == 12
    assert sum(coverage["lanes"][D1][
        "native_migrated_observations"].values()) == 28

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


def test_m3f_m3g_m4_m5_native_evidence_freshness_and_absence(
        fixture_project):
    fixture = fixture_project
    _write_m3fgm4m5(fixture["memory"], D1)
    model, report = _migrate(fixture)
    observations = [obs for obs in model["observations"]
                    if obs["provenance_id"].endswith(("-m3f", "-m3g", "-m4", "-m5"))]
    by_lane = {}
    for obs in observations:
        lane = obs["provenance_id"].rsplit("-", 1)[-1]
        by_lane.setdefault(lane, []).append(obs)
    assert by_lane["m3f"]
    assert by_lane["m3g"]
    assert by_lane["m4"]
    assert by_lane["m5"]
    native = lambda lane: [
        obs["payload"]["evidence_contract"] for obs in by_lane[lane]
        if "evidence_contract" in obs["payload"]]
    m3f_records = native("m3f")
    assert any(record["value"] == [] and record["coverage"] == "complete"
               for record in m3f_records)
    assert any(record["coverage"] == "unavailable"
               for record in m3f_records)
    m3g_records = native("m3g")
    assert any(record["value"] == 0.75 for record in m3g_records)
    assert any(record["coverage"] == "unavailable"
               for record in m3g_records)
    m4_records = native("m4")
    assert any(record["measurement_type"] == "video.scene_segment"
               for record in m4_records)
    assert any(record["measurement_type"] == "video.scene_boundary"
               for record in m4_records)
    vi09 = [obs for obs in model["observations"]
            if obs["provenance_id"].endswith("-vi09")]
    assert {obs["payload"]["level"] for obs in vi09} == {
        "scene", "shot", "subshot", "sequence"}
    assert {obs["payload"]["record"]["certainty"] for obs in vi09} == {
        "measured", "inferred", "interpreted"}
    cinematic = {event["kind"]: event for event in model["events"]
                 if event["kind"].startswith("cinematography_")}
    assert set(cinematic) == {
        "cinematography_scene", "cinematography_shot",
        "cinematography_subshot", "cinematography_sequence"}
    assert cinematic["cinematography_scene"]["parent_event_ids"] == [
        cinematic["cinematography_sequence"]["id"]]
    assert cinematic["cinematography_shot"]["parent_event_ids"] == [
        cinematic["cinematography_scene"]["id"]]
    assert cinematic["cinematography_subshot"]["parent_event_ids"] == [
        cinematic["cinematography_shot"]["id"]]
    assert cinematic["cinematography_scene"]["start_boundary"] == {
        "status": "exact", "uncertainty_ticks": None}
    assert cinematic["cinematography_shot"]["start_boundary"]["status"] == "exact"
    for event in cinematic.values():
        assert event["evidence_ids"]
        assert event["interval"]["clock_id"] == cinematic[
            "cinematography_scene"]["interval"]["clock_id"]
    assert {record["kind"] for record in model["interpretations"]} == {
        "cinematography_profile", "same_scene_context_sequence_hypothesis"}
    for record in model["interpretations"]:
        assert record["evidence_ids"][0] in {obs["id"] for obs in vi09}
    m5_records = [record for record in native("m5")
                  if record["measurement_type"] == "audio.sound_event"]
    assert len(m5_records) == 1
    assert m5_records[0]["value"] == []
    assert m5_records[0]["coverage"] == "complete"

    scene_doc = json.loads((fixture["memory"] / D1 / "scenes.json").read_text(
        encoding="utf-8"))
    scene_doc["scenes"] = []
    scene_doc["scene_boundaries"] = []
    _write(fixture["memory"] / D1 / "scenes.json", scene_doc)
    empty_scene_model, _empty_scene_report = _migrate(fixture)
    empty_scene_records = [
        obs["payload"]["evidence_contract"]
        for obs in empty_scene_model["observations"]
        if obs["provenance_id"].endswith("-m4")
        and "evidence_contract" in obs["payload"]
        and obs["payload"]["evidence_contract"].get("measurement_type")
        == "video.scene_segment"]
    assert len(empty_scene_records) == 1
    assert all(record["value"] == [] and record["coverage"] == "sampled"
               for record in empty_scene_records)

    coverage = evidence.evidence_coverage(
        str(fixture["project"]), model, report,
        memory_root=str(fixture["memory"]))
    lane_freshness = coverage["lanes"][D1]["freshness"]
    assert lane_freshness["m3f"] == "fresh"
    assert lane_freshness["m3g"] == "fresh"
    assert lane_freshness["m4"] == "unknown"
    assert lane_freshness["m5"] == "unknown"

    memory = fixture["memory"]
    _write(memory / D1 / "region_stats.json", {
        **json.loads((memory / D1 / "region_stats.json").read_text(
            encoding="utf-8")),
        "instrument": {"method": "retired-region-stats-method"}})
    _write(memory / D1 / "depth.json", {
        **json.loads((memory / D1 / "depth.json").read_text(
            encoding="utf-8")),
        "instrument": {"method": "retired-depth-method"}})
    _write(memory / D1 / "scenes.json", {
        **json.loads((memory / D1 / "scenes.json").read_text(
            encoding="utf-8")),
        "instrument": {"scene_pass": "retired-scene-method"}})
    _write(memory / D1 / "sound.json", {
        **json.loads((memory / D1 / "sound.json").read_text(
            encoding="utf-8")), "method": "retired-sound-method"})
    stale_model, stale_report = _migrate(fixture)
    stale_native = [obs for obs in stale_model["observations"]
                    if obs["provenance_id"].endswith(
                        ("-m3f", "-m3g", "-m4", "-m5"))
                    and "evidence_contract" in obs["payload"]]
    assert all(obs["modality"] == "sound_class"
               and obs["payload"]["evidence_contract"]["coverage"]
               == "unavailable"
               and obs["payload"]["evidence_contract"]["value"] is None
               for obs in stale_native)
    stale_coverage = evidence.evidence_coverage(
        str(fixture["project"]), stale_model, stale_report,
        memory_root=str(memory))
    for lane in ("m3f", "m3g", "m4", "m5"):
        assert stale_coverage["lanes"][D1]["freshness"][lane] == "stale"


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
    # FI-13: one M1 word plus four per-utterance speech records (rate,
    # completeness, disfluency, repetition) plus the cross-source
    # identical "hello" link anchored on D1.
    assert coverage["lanes"][D1]["migrated_observations"]["m1"] == 6
    assert coverage["lanes"][D1]["native_migrated_observations"]["m1"] == 6
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


def _write_m6m7m8m10(memory: Path, digest: str) -> None:
    """Current M6/M7/M8/M10 lane documents: a grouped M6 offset, M7
    with an empty speaking predicate (measured absence) beside a
    measured on-screen span, one matched and one unmatched M8 verdict,
    and a method-stamped M10 with one span and two empty relations."""
    from library.tools.relationships import M10_METHOD

    _write(memory / digest / "clock.json", {
        "content_digest": digest, "source_file": "/nonexistent/a.mov",
        "group_id": "group-1", "group_members": [digest, "digest-bbb-002"],
        "reference_digest": "digest-bbb-002",
        "offset_to_reference_seconds": 0.5,
        "path_to_reference": [digest, "digest-bbb-002"],
        "direct_measurement": {"agreement": 0.9, "offset_seconds": 0.5,
                               "p5_seconds": 0.45, "p95_seconds": 0.55,
                               "drift_seconds": 0.0,
                               "drift_exceeds_tolerance": False},
        "resolve_cross_check": None, "timecode_cross_check": None,
        "ngram_size": 4, "built_at": "2026-10-13T00:00:00+00:00"})
    _write(memory / "digest-bbb-002" / "clock.json", {
        "content_digest": "digest-bbb-002", "source_file": "/nonexistent/b.mov",
        "group_id": "group-1", "group_members": [digest, "digest-bbb-002"],
        "reference_digest": "digest-bbb-002",
        "offset_to_reference_seconds": 0.0,
        "path_to_reference": ["digest-bbb-002"],
        "direct_measurement": None, "resolve_cross_check": None,
        "timecode_cross_check": None, "ngram_size": 4,
        "built_at": "2026-10-13T00:00:00+00:00"})
    _write(memory / digest / "events.json", {
        "content_digest": digest, "size_bytes": 100,
        "source_file": "/nonexistent/a.mov", "status": "built",
        "frame_count": 2, "faces_assigned": 1, "faces_unassigned": 0,
        "predicates": {
            "speaking": {"basis": "fixture", "spans": []},
            "on_screen": {
                "basis": "fixture",
                "spans": [{"start": 0.0, "end": 5.0,
                           "face_track": "face_001"}]}},
        "candidates": {
            "hand_near_mouth": {"basis": "fixture", "spans": [
                {"start": 1.0, "end": 2.0, "face_track": "face_001",
                 "frames": [{"t": 1.0}, {"t": 1.5}]}]}},
        "built_at": "2026-10-13T00:00:00+00:00"})
    _write(memory / digest / "verdicts.json", {
        "content_digest": digest,
        "verdicts": {
            "k1": {"statement": "hand near mouth", "answer": True,
                   "reason": "seen", "model": "fixture",
                   "frames": [1.0, 1.5]},
            "k2": {"statement": "hand near mouth", "answer": False,
                   "reason": "other moment", "model": "fixture",
                   "frames": [9.0]}}})
    _write(memory / digest / "relationships.json", {
        "content_digest": digest, "source_file": "/nonexistent/a.mov",
        "relationships": {
            "holding": {"basis": "fixture", "spans": [
                {"owners": ["face_001"], "start": 2.0, "end": 3.0}]},
            "pointing": {"basis": "fixture", "spans": []},
            "looking_at": {"basis": "fixture", "spans": []}},
        "instrument": {"method": M10_METHOD,
                       "reads": ["persons.json", "identity.json",
                                 "regions.json"]},
        "built_at": "2026-10-13T00:00:00+00:00"})


def test_m6_m7_m8_m10_migrate_native_coverage_and_refusals(
        fixture_project):
    from library.tools.project_model import validate_project_model

    fixture = fixture_project
    memory = fixture["memory"]
    _write_m6m7m8m10(memory, D1)
    model, report = _migrate(fixture)
    validate_project_model(model)

    d1_obs = [o for o in model["observations"]
              if o["provenance_id"].startswith(f"prov-{D1}-")]
    contracts = {
        lane: [o["payload"]["evidence_contract"] for o in d1_obs
               if o["provenance_id"].endswith(f"-{lane}")
               and "evidence_contract" in (o.get("payload") or {})]
        for lane in ("m6", "m7", "m10")}

    m6_records = contracts["m6"]
    assert len(m6_records) == 1
    assert m6_records[0]["measurement_type"] == "sync.multicam_offset"
    assert m6_records[0]["coverage"] == "complete"
    assert m6_records[0]["method"] == "conversation-clock-ngram-v4"
    assert m6_records[0]["value"]["offset_to_reference_seconds"] == 0.5
    assert len(model["time_transforms"]) == 1
    transform = model["time_transforms"][0]
    assert transform["from_clock"] == f"clock-{D1}"
    assert transform["to_clock"] == "clock-digest-bbb-002"
    assert transform["segments"][0]["from"] == {
        "clock_id": f"clock-{D1}", "start": 0, "end": 9500}
    assert transform["segments"][0]["to"] == {
        "clock_id": "clock-digest-bbb-002", "start": 500, "end": 10000}
    assert transform["segments"][0]["uncertainty_ticks"] == 50

    m7_records = contracts["m7"]
    by_type = {record["measurement_type"] for record in m7_records}
    assert by_type == {"event.speaking_span", "event.visible_span",
                       "event.candidate_span"}
    speaking = [record for record in m7_records
                if record["measurement_type"] == "event.speaking_span"]
    assert len(speaking) == 1
    assert speaking[0]["value"] == []
    assert speaking[0]["coverage"] == "complete"
    candidate = [record for record in m7_records
                 if record["measurement_type"] == "event.candidate_span"]
    assert len(candidate) == 1
    assert candidate[0]["value"]["verified"] is True
    assert len(candidate[0]["value"]["verdicts"]) == 1

    m10_records = contracts["m10"]
    holding = [record for record in m10_records
               if record["value"] != []]
    assert len(holding) == 1
    assert holding[0]["value"] == {"relation": "holding",
                                   "owners": ["face_001"]}
    absent = [record for record in m10_records
              if record["value"] == []]
    assert len(absent) == 2
    assert all(record["coverage"] == "complete" for record in absent)

    assert any("M8 1 verdict(s) match no M7 candidate span" in note
               for note in report["notes"])
    coverage = evidence.evidence_coverage(
        str(fixture["project"]), model, report,
        memory_root=str(memory))
    lane = coverage["lanes"][D1]
    assert lane["present"]["m8_verdicts"] == 2
    assert lane["present"]["m8_unmatched_verdicts"] == 1
    assert lane["freshness"]["m6"] == "unknown"
    assert lane["freshness"]["m7"] == "unknown"
    assert lane["freshness"]["m8"] == "unknown"
    assert lane["freshness"]["m10"] == "unknown"
    assert lane["native_migrated_observations"]["m6"] == 1
    assert lane["native_migrated_observations"]["m7"] == 3
    assert lane["native_migrated_observations"]["m10"] == 3
    assert "m6" not in lane["legacy_only_migrated_observations"]
    assert "m7" not in lane["legacy_only_migrated_observations"]
    assert "m10" not in lane["legacy_only_migrated_observations"]

    _write(memory / D1 / "relationships.json", {
        **json.loads((memory / D1 / "relationships.json").read_text(
            encoding="utf-8")),
        "instrument": {"method": "retired-relationship-method"}})
    stale_m10, stale_report = _migrate(fixture)
    assert not [o for o in stale_m10["observations"]
                if o["provenance_id"].startswith(f"prov-{D1}-")
                and o["provenance_id"].endswith("-m10")]
    assert any("M10 stale method" in note
               for note in stale_report["notes"])
    stale_coverage = evidence.evidence_coverage(
        str(fixture["project"]), stale_m10, stale_report,
        memory_root=str(memory))
    assert stale_coverage["lanes"][D1]["freshness"]["m10"] == "stale"

    clock_doc = json.loads((memory / D1 / "clock.json").read_text(
        encoding="utf-8"))
    clock_doc["ngram_size"] = 2
    _write(memory / D1 / "clock.json", clock_doc)
    stale_m6, stale_m6_report = _migrate(fixture)
    assert not [o for o in stale_m6["observations"]
                if o["provenance_id"].startswith(f"prov-{D1}-")
                and o["provenance_id"].endswith("-m6")]
    assert any("M6 stale n-gram method" in note
               for note in stale_m6_report["notes"])
    stale_m6_coverage = evidence.evidence_coverage(
        str(fixture["project"]), stale_m6, stale_m6_report,
        memory_root=str(memory))
    assert stale_m6_coverage["lanes"][D1]["freshness"]["m6"] == "stale"

    _write(memory / D1 / "persons.json", json.loads(
        (memory / D1 / "persons.json").read_text(encoding="utf-8")))
    stale_m7, stale_m7_report = _migrate(fixture)
    assert not [o for o in stale_m7["observations"]
                if o["provenance_id"].startswith(f"prov-{D1}-")
                and o["provenance_id"].endswith("-m7")]
    assert any("M7 stale against its M3/M3b inputs" in note
               for note in stale_m7_report["notes"])
    stale_m7_coverage = evidence.evidence_coverage(
        str(fixture["project"]), stale_m7, stale_m7_report,
        memory_root=str(memory))
    assert stale_m7_coverage["lanes"][D1]["freshness"]["m7"] == "stale"
    assert stale_m7_coverage["lanes"][D1]["freshness"]["m8"] == "stale"


def test_identity_anchor_index_only_visits_new_observations():
    class Observation(dict):
        visits = 0
        def __getitem__(self, key):
            if key == "id":
                type(self).visits += 1
            return super().__getitem__(key)

    builder = evidence._Builder("fixture")
    for i in range(100):
        builder.model["observations"].append(Observation(
            id=f"obs-{i}", interval={"clock_id": "source", "start": i, "end": i + 1}))
        builder.link("person", f"obs-{i}", "fixture", D1, "m1", "fixture", 1.0)
    assert Observation.visits == 100
    assert len(builder.model["identity_links"]) == 100
    # A replaced list is a new index, and duplicate IDs keep first-match semantics.
    builder.model["observations"] = [
        {"id": "obs-0", "interval": {"clock_id": "replacement", "start": 3, "end": 4}},
        {"id": "obs-0", "interval": {"clock_id": "other", "start": 5, "end": 6}}]
    builder.link("person", "obs-0", "fixture", D1, "m1", "fixture", 1.0)
    assert builder.model["identity_links"][-1]["scope"]["clock_id"] == "replacement"


def test_source_signal_membership_enumerates_existing_ids_once(monkeypatch):
    class Signal(dict):
        visits = 0
        def __getitem__(self, key):
            if key == "id":
                type(self).visits += 1
            return super().__getitem__(key)
    builder = evidence._Builder("fixture")
    builder.model["signals"] = [Signal(id=f"existing-{i}") for i in range(100)]
    builder.model["observations"] = [{
        "id": f"word-{i}", "asset_id": "asset", "provenance_id": f"prov-{D1}-m1",
        "modality": "speech_word", "interval": {"start": i, "end": i + 1}}
        for i in range(100)]
    monkeypatch.setattr(evidence.source_memory, "read_m1", lambda *_: (None, "missing"))
    monkeypatch.setattr(evidence.source_memory, "read_sound", lambda *_: None)
    monkeypatch.setattr(evidence.source_memory, "read_scenes", lambda *_: None)
    evidence._materialize_source_signals(builder, D1, "asset", "clock", None)
    assert Signal.visits == 100
    assert len(builder.model["signals"]) == 200
    evidence._materialize_source_signals(builder, D1, "asset", "clock", None)
    assert len(builder.model["signals"]) == 200


def _one_source_project(tmp_path, digest: str, catalog_row: dict):
    """A project with one catalogued source and its M0 lane."""
    project = tmp_path / "proj"
    memory = tmp_path / "memory"
    _write(memory / digest / "source.json", {
        "content_digest": digest, "size_bytes": 100,
        "duration_seconds": 10.0, "video_streams": [{"index": 0}]})
    catalog = [catalog_row]
    _write(project / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": catalog}},
        "source_fingerprints": {
            "clip_001": {"path": "/nonexistent/c.mov",
                         "content_digest": digest}}})
    return project, memory


def test_the_migrated_source_clock_carries_the_measured_frame_rate(tmp_path):
    """A-10: the migrated source clock carried NO frame rate, so the
    exact frame mapping had nothing to build from: ``from_model_clock``
    refused every migrated source, and the machinery that converts a
    tick to the frame or the sample that holds it was a reader with no
    producer. The clock now carries the rate the probe measured, as
    the exact rational it is.
    """
    from library.tools import source_clocks

    digest = "digest-ccc-003"
    project, memory = _one_source_project(
        tmp_path, digest,
        {"clip_id": "clip_001", "source_file": "/nonexistent/c.mov",
         "path": "/nonexistent/c.mov",
         "frame_rate": 29.97, "frame_rate_nominal": 29.97,
         "avg_frame_rate": 29.97, "is_vfr": False})
    model, _report = evidence.migrate_project_evidence(
        str(project), memory_root=str(memory))
    clock = next(row for row in model["clocks"]
                 if row["domain"]["kind"] == "source")
    assert clock["frame_rate"] == {"numerator": 30000, "denominator": 1001}
    built = source_clocks.from_model_clock(digest, clock)
    assert (built.fps_num, built.fps_den) == (30000, 1001)


def test_a_variable_frame_rate_source_gets_no_frame_grid(tmp_path):
    """A-10: a variable-frame-rate file keeps no constant frame grid.

    Its nominal rate is a number the container claims and the frames
    do not keep, so the clock says nothing: publishing the nominal
    rate would be a lie the model then measures every frame against.
    """
    digest = "digest-ddd-004"
    project, memory = _one_source_project(
        tmp_path, digest,
        {"clip_id": "clip_001", "source_file": "/nonexistent/c.mov",
         "path": "/nonexistent/c.mov",
         "frame_rate": 60.0, "frame_rate_nominal": 60.0,
         "avg_frame_rate": 24.0, "is_vfr": True})
    model, report = evidence.migrate_project_evidence(
        str(project), memory_root=str(memory))
    clock = next(row for row in model["clocks"]
                 if row["domain"]["kind"] == "source")
    assert clock["frame_rate"] is None
    assert any("frame grid" in note for note in report["notes"])
