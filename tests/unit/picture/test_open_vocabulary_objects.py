"""VI-05: open-vocabulary object measurements and tracks.

MU-15 proved one detector-seeded laptop migrates, resolves and covers;
VI-04 proved same-looking tracks keep separate scoped identities. What
neither proved is the VI-05 row: evidence-linked detections, appearance
and visibility beside the spatial tracks and masks, with a multi-object
fixture whose identities stay separate and whose coverage gaps are
explicit.

Every project here is built under `tmp_path`: a hand-made 1.06
segmentation file with two detector-seeded objects (a laptop and a
mug, distinct categories and detectors, the mug with a two-sample gap
where SAM 2 held no mask) plus a blob-seeded region with no class,
and minimal lane documents in a scratch memory root. Nothing reads a
real project, a real memory root, or a model. The memory root is
passed explicitly and `MEMORY_ROOT_ENV` is cleared, so a host or agent
environment cannot leak in.

What each test names (no registry/schema/count gates):

- a detector score travels the seed into the track and the detection
  record verbatim, is refused outside 0..1, and rides no content key;
- files written before scores existed still load, with score None;
- every migrated track sample carries native `object.track_sample`
  evidence naming the method, resolution and entity (on main the
  observations carry none);
- every detector-seeded object carries one `object.detection`
  observation naming its detector and score, while the blob-seeded
  region carries none (on main no detection observation exists);
- every object carries one `object.appearance` observation that is
  shape only - colour and texture are named as unmeasured, never
  defaulted (on main no appearance observation exists);
- the mug's `object.visibility` observation names its gap ticks and
  missing frames, the laptop's says continuous, and the migration
  notes say the same (on main no visibility observation exists);
- laptop and mug queries resolve to separate entities and tracks, the
  gap reads as held coverage with explicit gaps beside it, and an
  unresolvable name stays an absence;
- `object_track_gaps` answers from the visibility evidence and
  refuses a model that predates it rather than guessing a cadence.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from library.tools import project_model_evidence as evidence
from library.tools import region_query
from library.tools import source_memory
from library.tools.analysis import object_segmentation as seg
from library.tools.project_layout import ProjectLayout

D = "digest-objects-002"
SHAPE = (20, 32)
SAMPLE_FPS = 2.0

LAPTOP_FRAMES = list(range(10))
MUG_FRAMES = [0, 1, 2, 5, 6, 7]
BLOB_FRAMES = [0, 1]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _laptop_mask(frame: int) -> np.ndarray:
    mask = np.zeros(SHAPE, dtype=np.uint8)
    mask[6:10, 4 + frame:10 + frame] = 1
    return mask


def _mug_mask() -> np.ndarray:
    mask = np.zeros(SHAPE, dtype=np.uint8)
    mask[12:18, 18:23] = 1
    return mask


def _blob_mask() -> np.ndarray:
    mask = np.zeros(SHAPE, dtype=np.uint8)
    mask[0:4, 0:4] = 1
    return mask


def _seg_doc() -> dict:
    return {
        "video_path": "/nonexistent/objects.mov",
        "frame_count": 10,
        "resolution": list(SHAPE),
        "sample_fps": SAMPLE_FPS,
        "seed_note": "detector_seeded: 2 object target(s) 'black laptop' "
                     "(owlv2), 'white mug' (grounding_dino)",
        "objects": [
            {
                "object_id": "obj_2",
                "label": "black laptop",
                "category": "laptop",
                "seed": "detector:owlv2",
                "score": 0.87,
                "frames": LAPTOP_FRAMES,
                "masks_rle": {str(f): seg.encode_rle(_laptop_mask(f))
                              for f in LAPTOP_FRAMES},
                "bboxes": {str(f): [4 + f, 6, 6, 4]
                           for f in LAPTOP_FRAMES},
                "avg_area_ratio": 0.0375,
            },
            {
                "object_id": "obj_3",
                "label": "white mug",
                "category": "mug",
                "seed": "detector:grounding_dino",
                "score": 0.62,
                "frames": MUG_FRAMES,
                "masks_rle": {str(f): seg.encode_rle(_mug_mask())
                              for f in MUG_FRAMES},
                "bboxes": {str(f): [18, 12, 5, 6]
                           for f in MUG_FRAMES},
                "avg_area_ratio": 0.0469,
            },
            {
                "object_id": "obj_4",
                "label": "auto_object_1",
                "category": "unknown",
                "seed": "blob",
                "frames": BLOB_FRAMES,
                "masks_rle": {str(f): seg.encode_rle(_blob_mask())
                              for f in BLOB_FRAMES},
                "bboxes": {str(f): [0, 0, 4, 4]
                           for f in BLOB_FRAMES},
                "avg_area_ratio": 0.025,
            },
        ],
    }


@pytest.fixture()
def fixture_project(tmp_path, monkeypatch):
    monkeypatch.delenv(source_memory.MEMORY_ROOT_ENV, raising=False)
    project = tmp_path / "proj"
    memory = tmp_path / "memory"
    digest_dir = memory / D
    _write(digest_dir / "source.json", {
        "content_digest": D, "size_bytes": 100,
        "duration_seconds": 10.0, "video_streams": [{"index": 0}]})
    _write(digest_dir / "persons.json", {
        "content_digest": D, "frames": []})
    _write(digest_dir / "identity.json", {
        "content_digest": D, "status": "measured",
        "face_match_threshold": 0.25, "faces": [], "voices": [],
        "speech_face_links": [], "instrument": {}})
    _write(project / "pipeline_data.json", {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_obj",
                 "source_file": "/nonexistent/objects.mov",
                 "path": "/nonexistent/objects.mov"}]}},
        "source_fingerprints": {
            "clip_obj": {"path": "/nonexistent/objects.mov",
                         "content_digest": D}},
    })
    seg_dir = ProjectLayout(str(project)).step_dir(
        "object_segmentation", create=True)
    _write(seg_dir / "clip_obj_segmentation.json", _seg_doc())
    return {"project": project, "memory": memory}


def _migrate(fixture_project):
    return evidence.migrate_project_evidence(
        str(fixture_project["project"]),
        memory_root=str(fixture_project["memory"]))


def _observations_by_modality(model, modality):
    return [o for o in model["observations"]
            if o["modality"] == modality]


# ── The score: metadata that travels but never identifies ──


def test_seed_score_travels_verbatim():
    seeds = seg.check_seed_boxes([{
        "box": [0.1, 0.1, 0.4, 0.4], "label": "black laptop",
        "category": "laptop", "detector": "owlv2", "score": 0.87}])
    assert seeds[0].score == 0.87


def test_seed_without_score_reads_as_unrecorded():
    seeds = seg.check_seed_boxes([{
        "box": [0.1, 0.1, 0.4, 0.4], "label": "white mug",
        "category": "mug", "detector": "grounding_dino"}])
    assert seeds[0].score is None


def test_seed_score_outside_unit_range_is_refused():
    with pytest.raises(ValueError):
        seg.check_seed_boxes([{
            "box": [0.1, 0.1, 0.4, 0.4], "label": "black laptop",
            "category": "laptop", "detector": "owlv2", "score": 1.4}])


def test_seed_key_ignores_score_only_changes():
    from library.steps.step_1_06_object_segmentation import step as s1

    base = {"box": [0.1, 0.1, 0.4, 0.4], "label": "black laptop",
            "category": "laptop", "detector": "owlv2"}
    assert s1._seed_key([base]) == s1._seed_key(
        [dict(base, score=0.87)])


def test_prescore_seg_files_load_with_score_none(tmp_path):
    """Files written before scores travelled carry no `score` key;
    they read as unrecorded, not as zero."""
    doc = _seg_doc()
    for obj in doc["objects"]:
        obj.pop("score", None)
    path = tmp_path / "clip_obj_segmentation.json"
    _write(path, doc)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    tracked = seg.TrackedObject(
        object_id=loaded["objects"][0]["object_id"],
        label=loaded["objects"][0]["label"],
        category=loaded["objects"][0]["category"],
        frames=loaded["objects"][0]["frames"],
        masks_rle={int(k): v for k, v in
                   loaded["objects"][0]["masks_rle"].items()},
        bboxes={int(k): tuple(v) for k, v in
                loaded["objects"][0]["bboxes"].items()},
        avg_area_ratio=loaded["objects"][0]["avg_area_ratio"],
        seed=loaded["objects"][0]["seed"])
    assert tracked.score is None


# ── Track samples: evidence-linked, not bare geometry ──


def test_track_samples_carry_native_evidence(fixture_project):
    model, _report = _migrate(fixture_project)
    samples = _observations_by_modality(model, "object_track_sample")
    assert len(samples) == (len(LAPTOP_FRAMES) + len(MUG_FRAMES)
                            + len(BLOB_FRAMES))
    entities = {e["id"] for e in model["entities"]}
    for obs in samples:
        contract = obs["payload"]["evidence_contract"]
        assert contract["measurement_type"] == "object.track_sample"
        assert contract["method"] == "sam2-video-track-v1@32x20"
        assert contract["sample_resolution"] == [32, 20]
        assert contract["target_entity_id"] in entities
        assert contract["coverage"] == "sampled"


# ── Detections: who named each track ──


def test_detections_name_detector_and_score(fixture_project):
    model, _report = _migrate(fixture_project)
    detections = _observations_by_modality(model, "object_detection")
    assert len(detections) == 2
    by_detector = {o["payload"]["detector"]: o for o in detections}
    laptop = by_detector["owlv2"]
    assert laptop["payload"]["category"] == "laptop"
    assert laptop["payload"]["score"] == 0.87
    assert laptop["confidence"] == 0.87
    assert laptop["payload"]["evidence_contract"]["measurement_type"] \
        == "object.detection"
    mug = by_detector["grounding_dino"]
    assert mug["payload"]["category"] == "mug"
    assert mug["payload"]["score"] == 0.62


def test_blob_region_carries_no_detection(fixture_project):
    model, _report = _migrate(fixture_project)
    detections = _observations_by_modality(model, "object_detection")
    assert all(o["payload"]["object_id"] != "obj_4"
               for o in detections)


# ── Appearance: shape measured, colour refused ──


def test_appearance_is_shape_only(fixture_project):
    model, _report = _migrate(fixture_project)
    appearances = _observations_by_modality(model, "object_appearance")
    assert len(appearances) == 3
    mug = next(o for o in appearances
               if o["payload"]["object_id"] == "obj_3")
    value = mug["payload"]
    assert value["samples"] == len(MUG_FRAMES)
    assert value["aspect_min"] == value["aspect_max"] \
        == value["aspect_mean"] == pytest.approx(5 / 6, abs=1e-4)
    assert value["mask_area_ratio_mean"] == pytest.approx(0.0469)
    assert value["unmeasured"] == ["colour", "texture"]
    assert "colour" not in value or value.get("colour") is None
    assert mug["payload"]["evidence_contract"]["measurement_type"] \
        == "object.appearance"


# ── Visibility: gaps explicit in the model and the notes ──


def test_visibility_names_the_mug_gap(fixture_project):
    model, report = _migrate(fixture_project)
    rows = _observations_by_modality(model, "object_visibility")
    assert len(rows) == 3
    mug = next(o for o in rows
               if o["payload"]["object_id"] == "obj_3")
    assert mug["payload"]["visible_ticks"] == [
        round(f / SAMPLE_FPS * 1000) for f in MUG_FRAMES]
    assert mug["payload"]["gaps"] == [{
        "start_tick": 1500, "end_tick": 2500,
        "missing_frames": [3, 4]}]
    assert mug["payload"]["evidence_contract"]["measurement_type"] \
        == "object.visibility"
    laptop = next(o for o in rows
                  if o["payload"]["object_id"] == "obj_2")
    assert laptop["payload"]["gaps"] == []
    assert any("clip_obj/obj_3" in note and "gap" in note
               for note in report["notes"])
    assert any("clip_obj/obj_2" in note and "continuous" in note
               for note in report["notes"])


def test_visibility_does_not_claim_a_frame_without_track_geometry(
        fixture_project):
    seg_path = ProjectLayout(str(fixture_project["project"])).step_dir(
        "object_segmentation") / "clip_obj_segmentation.json"
    doc = json.loads(seg_path.read_text(encoding="utf-8"))
    mug = next(obj for obj in doc["objects"]
               if obj["object_id"] == "obj_3")
    # Simulate an artifact whose frame list includes a propagated mask but
    # whose stored track has no bbox for it. Migration skips such a sample.
    mug["frames"].append(3)
    mug["frames"].sort()
    _write(seg_path, doc)

    model, _report = _migrate(fixture_project)
    visibility = next(o["payload"] for o in
                      _observations_by_modality(model, "object_visibility")
                      if o["payload"]["object_id"] == "obj_3")
    assert visibility["visible_ticks"] == [0, 500, 1000, 2500, 3000, 3500]
    assert visibility["gaps"] == [{
        "start_tick": 1500, "end_tick": 2500,
        "missing_frames": [3, 4]}]


# ── The acceptance: two objects, separate identities, explicit gaps ──


def test_multi_object_queries_stay_on_their_entity(fixture_project):
    model, _report = _migrate(fixture_project)
    laptop_hit = region_query.object_region_at(model, "laptop", 1.0)
    mug_hit = region_query.object_region_at(model, "mug", 1.0)
    assert laptop_hit["present"] is True
    assert mug_hit["present"] is True
    assert laptop_hit["entity_id"] != mug_hit["entity_id"]
    assert laptop_hit["bbox"] != mug_hit["bbox"]
    laptop_entity = next(e for e in model["entities"]
                         if e["id"] == laptop_hit["entity_id"])
    mug_entity = next(e for e in model["entities"]
                      if e["id"] == mug_hit["entity_id"])
    assert laptop_entity["object_class_id"] == "class-laptop"
    assert mug_entity["object_class_id"] == "class-mug"


def test_gap_reads_as_held_track_with_explicit_gaps(fixture_project):
    """At t=2.0s the mug's track holds its frame-2 sample (Ren's
    held-matte semantic) while the gaps beside it name frames 3-4 as
    unmeasured: presence inside the span never claims the gap frames."""
    model, _report = _migrate(fixture_project)
    hit = region_query.object_region_at(model, "mug", 2.0)
    assert hit["present"] is True
    assert hit["sample_tick"] == 1000
    gaps = region_query.object_track_gaps(model, "mug")
    assert gaps["gaps"] == [{
        "start_tick": 1500, "end_tick": 2500,
        "missing_frames": [3, 4]}]
    assert region_query.object_track_gaps(model, "laptop")["gaps"] == []


def test_unknown_object_is_an_absence_not_a_guess(fixture_project):
    model, _report = _migrate(fixture_project)
    assert region_query.object_region_at(
        model, "espresso machine", 1.0) == {
            "present": False, "reason": "unknown_person"}


def test_gaps_refused_without_visibility_evidence():
    with pytest.raises(ValueError):
        region_query.object_track_gaps({"entities": [], "tracks": [],
                                        "observations": []}, "laptop")


def test_continuity_links_scope_each_track(fixture_project):
    model, _report = _migrate(fixture_project)
    continuity = [link for link in model["identity_links"]
                  if link["method"] == "sam2_track_continuity"]
    assert len(continuity) == 3
    observations = {row["id"]: row for row in model["observations"]}
    for link in continuity:
        assert link["observation_id"] in link["supporting_observation_ids"]
        assert all(observations[obs_id]["payload"]["track_id"]
                   == observations[link["observation_id"]][
                       "payload"]["track_id"]
                   for obs_id in link["supporting_observation_ids"])
