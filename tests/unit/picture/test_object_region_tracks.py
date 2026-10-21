"""MU-15: object region tracks (open-vocabulary seed -> SAM 2).

Every project here is built under `tmp_path`: a hand-made 1.06
segmentation file with a detector-seeded "laptop" object (synthetic
masks, no model) plus minimal lane documents in a scratch memory root.
Nothing reads a real project, a real memory root, or a model. The
memory root is passed explicitly and `MEMORY_ROOT_ENV` is cleared, so
a host or agent environment cannot leak in.

What each test names (no registry/schema/count gates):

- detector seeds without a class are refused: a classless track mints
  no ObjectClass, which is the whole point of seeding it;
- a detector-seeded object migrates to an object entity with an
  `open-vocabulary` ObjectClass (on main every class reads
  `sam2_seed_category`);
- a fixture "laptop" query returns the object region track whose masks
  cover the laptop on >= 90% of sampled frames (on main
  `object_region_at` does not exist);
- a named object ("laptop") resolves to a seed by label/category (on
  main it is `unknown_subject`, so `mask.create` has no producer);
- two objects sharing a name refuse as ambiguous rather than picking
  one; blob ids never match a name;
- the `mask.create` the planner emits for a named object passes the
  algebra's own gates (provider, seed receipt, matte mark) before it
  ever reaches a timeline;
- the 1.06 content key covers detector seeds: a new seed re-measures
  rather than reusing a matte measured without it.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from library.tools import project_model_evidence as evidence
from library.tools import region_query
from library.tools import source_memory
from library.tools.analysis import object_detector_bakeoff as bakeoff
from library.tools.analysis import open_vocabulary_detect as ovd
from library.tools.analysis import object_segmentation as seg
from library.tools import mask_seed
from library.tools.mask_seed import MaskSeed, Point, ImageRect
from library.tools.project_layout import ProjectLayout
from library.tools.ren_refusal import RenRefusal

D = "digest-laptop-001"
SHAPE = (20, 32)
N_SAMPLES = 10
SAMPLE_FPS = 2.0


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _laptop_mask(frame: int) -> np.ndarray:
    """A 6x4 laptop rect sliding one pixel right per sample."""
    mask = np.zeros(SHAPE, dtype=np.uint8)
    mask[6:10, 4 + frame:10 + frame] = 1
    return mask


def _seg_doc() -> dict:
    objects = [{
        "object_id": "obj_2",
        "label": "black laptop",
        "category": "laptop",
        "seed": "detector:owlv2",
        "frames": list(range(N_SAMPLES)),
        "masks_rle": {str(f): seg.encode_rle(_laptop_mask(f))
                      for f in range(N_SAMPLES)},
        "bboxes": {str(f): [4 + f, 6, 6, 4] for f in range(N_SAMPLES)},
        "avg_area_ratio": 0.0375,
    }]
    return {"video_path": "/nonexistent/laptop.mov",
            "frame_count": N_SAMPLES, "resolution": list(SHAPE),
            "sample_fps": SAMPLE_FPS,
            "seed_note": "detector_seeded: 1 object target(s) "
                         "'black laptop' (owlv2)",
            "objects": objects}


def _ground_truth() -> dict[int, list[float]]:
    height, width = SHAPE
    return {round(f / SAMPLE_FPS * 1000): [
        (4 + f) / width, 6 / height, (10 + f) / width, 10 / height]
        for f in range(N_SAMPLES)}


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
                {"clip_id": "clip_lap",
                 "source_file": "/nonexistent/laptop.mov",
                 "path": "/nonexistent/laptop.mov"}]}},
        "source_fingerprints": {
            "clip_lap": {"path": "/nonexistent/laptop.mov",
                         "content_digest": D}},
    })
    seg_dir = ProjectLayout(str(project)).step_dir(
        "object_segmentation", create=True)
    _write(seg_dir / "clip_lap_segmentation.json", _seg_doc())
    return {"project": project, "memory": memory,
            "seg": _seg_doc(), "ground_truth": _ground_truth()}


def _migrate(fixture_project):
    return evidence.migrate_project_evidence(
        str(fixture_project["project"]),
        memory_root=str(fixture_project["memory"]))


# ── Detector seeds name a class, or they are refused ──


def test_seed_boxes_refused_without_class():
    with pytest.raises(ValueError):
        seg.check_seed_boxes([{
            "box": [0.1, 0.1, 0.4, 0.4], "label": "black laptop",
            "category": "", "detector": "owlv2",
            "frame": {"time_seconds": 0.0, "width": 1920, "height": 1080}}])


def test_seed_boxes_refused_without_area():
    with pytest.raises(ValueError):
        seg.check_seed_boxes([{
            "box": [0.4, 0.4, 0.4, 0.4], "label": "black laptop",
            "category": "laptop", "detector": "owlv2",
            "frame": {"time_seconds": 0.0, "width": 1920, "height": 1080}}])


def test_normalize_seed_box_clamps_but_never_invents():
    assert seg.normalize_seed_box([-0.5, -0.5, 0.3, 0.3]) == [0.0, 0.0, 0.3, 0.3]
    assert seg.normalize_seed_box([0.5, 0.5, 0.4, 0.4]) is None


# ── Bakeoff scoring: the baseline scores without phrases ──


def test_bakeoff_results_are_json_serializable(tmp_path, monkeypatch):
    """`run_bakeoff` rows carry the model id, never the loaded model:
    the first full run died writing bakeoff_27.json because a row held
    the `Owlv2ForObjectDetection` object (`TypeError: ... is not JSON
    serializable`), losing every measured timing with it."""
    import json as _json

    def _fake_load(name):
        return {"device": "test", "model_id": f"fake-{name}",
                "model": object(), "revision": "published-commit"}

    def _fake_detect(detector, image_path, phrases, *, score_threshold):
        return [ovd.Detection("laptop", [0.0, 0.0, 0.5, 0.5], 0.9,
                              detector)]

    monkeypatch.setattr(ovd, "load_detector", _fake_load)
    monkeypatch.setattr(ovd, "detect_image", _fake_detect)
    frames = [{"image": "x.jpg", "timestamp_s": 12.5,
               "phrases": ["laptop"],
               "boxes": [{"phrase": "laptop",
                          "box": [0.0, 0.0, 0.5, 0.5]}]}]
    results = bakeoff.run_bakeoff(tmp_path, frames,
                                  detectors=["owlv2", "grounding_dino"])
    _json.dumps(results)
    assert results["detectors"]["owlv2"]["model"] == "fake-owlv2"
    assert results["detectors"]["owlv2"]["recall"] == 1.0
    row = results["detectors"]["owlv2"]
    assert row["model_revision"] == "published-commit"
    detection = row["detections_by_frame"][0]["detections"][0]
    assert detection == {
        "class": "laptop",
        "bbox_xyxy_normalized": [0.0, 0.0, 0.5, 0.5],
        "score": 0.9,
        "producer": "owlv2",
        "producer_model": "fake-owlv2",
        "producer_revision": "published-commit",
    }
    assert row["detections_by_frame"][0]["timestamp_s"] == 12.5
    assert row["false_positives"] == 0
    assert row["false_negatives"] == 0
    doc = bakeoff.render_markdown(results, frames_source="t",
                                  ground_truth_source="t", gemma_source="t")
    assert "fake-owlv2" in doc


def test_bakeoff_recall_counts_covering_boxes():
    hit = bakeoff.score_frame(
        [{"phrase": "laptop", "box": [0.0, 0.0, 0.5, 0.5]}],
        [ovd.Detection("laptop", [0.0, 0.0, 0.5, 0.5], 0.9, "owlv2")],
        ["laptop"])
    assert hit["recall"] == 1.0
    miss = bakeoff.score_frame(
        [{"phrase": "laptop", "box": [0.0, 0.0, 0.5, 0.5]}],
        [ovd.Detection("laptop", [0.5, 0.5, 1.0, 1.0], 0.9, "owlv2")],
        ["laptop"])
    assert miss["recall"] == 0.0


def test_bakeoff_counts_duplicate_predictions_and_misses_separately():
    scored = bakeoff.score_frame(
        [{"phrase": "laptop", "box": [0.0, 0.0, 0.4, 0.4]},
         {"phrase": "laptop", "box": [0.6, 0.6, 1.0, 1.0]}],
        [ovd.Detection("laptop", [0.0, 0.0, 0.4, 0.4], 0.9, "owlv2"),
         ovd.Detection("laptop", [0.0, 0.0, 0.4, 0.4], 0.8, "owlv2")],
        ["laptop"])
    assert scored["hits"] == 1
    assert scored["false_positives"] == 1
    assert scored["misses"] == 1
    assert scored["precision"] == 0.5
    assert scored["recall"] == 0.5
    assert scored["per_class"]["laptop"] == {
        "true_positives": 1, "false_positives": 1,
        "false_negatives": 1}


def test_ground_truth_keeps_source_timestamp_and_rejects_negative_time():
    frame = {"image": "frame.jpg", "timestamp_s": "2.25",
             "phrases": ["cup"],
             "boxes": [{"phrase": "cup", "box": [0, 0, 1, 1]}]}
    assert bakeoff.check_ground_truth({"frames": [frame]})[0][
        "timestamp_s"] == 2.25
    frame["timestamp_s"] = -0.1
    with pytest.raises(ValueError, match="negative timestamp_s"):
        bakeoff.check_ground_truth({"frames": [frame]})
    frame.pop("timestamp_s")
    with pytest.raises(ValueError, match="missing its source timestamp_s"):
        bakeoff.check_ground_truth({"frames": [frame]})


def test_failed_detection_frame_counts_all_labels_as_missed(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        ovd, "load_detector",
        lambda _name: {"device": "test", "model_id": "fake"})

    def fail(*_args, **_kwargs):
        raise RuntimeError("fixture backend failure")

    monkeypatch.setattr(ovd, "detect_image", fail)
    results = bakeoff.run_bakeoff(
        tmp_path,
        [{"image": "unreadable.jpg", "timestamp_s": 3.0,
          "phrases": ["cup"],
          "boxes": [{"phrase": "cup", "box": [0, 0, 1, 1]}]}],
        detectors=[ovd.OWLV2])
    row = results["detectors"][ovd.OWLV2]
    assert row["frames_failed"] == 1
    assert row["frames_scored"] == 0
    assert row["false_negatives"] == 1
    assert row["recall"] == 0.0
    assert row["detections_by_frame"][0]["error"] == (
        "RuntimeError: fixture backend failure")


def test_class_threshold_overrides_the_global_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ovd, "load_detector",
        lambda _name: {"device": "test", "model_id": "fake"})
    seen = []

    def detect(_detector, _image, _phrases, *, score_threshold):
        seen.append(score_threshold)
        return [ovd.Detection("cup", [0, 0, 1, 1], 0.3, "owlv2")]

    monkeypatch.setattr(ovd, "detect_image", detect)
    results = bakeoff.run_bakeoff(
        tmp_path,
        [{"image": "x.jpg", "timestamp_s": 1,
          "phrases": ["cup"],
          "boxes": [{"phrase": "cup", "box": [0, 0, 1, 1]}]}],
        detectors=[ovd.OWLV2], score_threshold=0.5,
        class_thresholds={"cup": 0.2})
    row = results["detectors"][ovd.OWLV2]
    assert seen == [0.2]
    assert row["recall"] == 1.0


def test_bakeoff_baseline_covers_without_a_phrase():
    scored = bakeoff.score_frame(
        [{"phrase": "laptop", "box": [0.0, 0.0, 0.5, 0.5]}],
        [ovd.Detection("", [0.0, 0.0, 0.5, 0.5], 1.0, "vision_foreground")],
        ["laptop"])
    assert scored["recall"] == 1.0


def test_bakeoff_refuses_empty_ground_truth(tmp_path):
    with pytest.raises(ValueError):
        bakeoff.check_ground_truth({"frames": []})


def test_detector_boxes_clamp_and_score():
    assert ovd.normalize_box([0.0, 0.0, 0.5, 0.5]) == [0.0, 0.0, 0.5, 0.5]
    assert ovd.normalize_box([0.5, 0.5, 0.5, 0.5]) is None
    assert ovd.box_iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0
    assert ovd.box_iou([0, 0, 0.5, 0.5], [0.5, 0.5, 1, 1]) == 0.0


# ── Migration: the detector seed mints an open-vocabulary class ──


def test_detector_seed_mints_open_vocabulary_class(fixture_project):
    model, _report = _migrate(fixture_project)
    entity = next(e for e in model["entities"]
                  if e["kind"] == "object")
    assert entity["label"] == "black laptop"
    assert entity["object_class_id"] == "class-laptop"
    cls = next(c for c in model["object_classes"]
               if c["id"] == "class-laptop")
    assert cls["taxonomy"] == "open-vocabulary"


# ── Acceptance: the fixture laptop query ──


def test_laptop_query_returns_covering_track(fixture_project):
    model, _report = _migrate(fixture_project)
    hit = region_query.object_region_at(model, "laptop", 2.0)
    assert hit["present"] is True
    assert hit["bbox"] is not None
    coverage = region_query.object_track_coverage(
        model, "laptop", fixture_project["ground_truth"],
        project_folder=str(fixture_project["project"]))
    assert coverage["frames"] == N_SAMPLES
    assert coverage["fraction"] >= 0.9


def test_laptop_query_absent_outside_the_span(fixture_project):
    model, _report = _migrate(fixture_project)
    hit = region_query.object_region_at(model, "laptop", 99.0)
    assert hit["present"] is False
    assert hit["reason"] == "absent_at_time"


def test_unknown_object_is_an_absence_not_a_guess(fixture_project):
    model, _report = _migrate(fixture_project)
    assert region_query.object_region_at(
        model, "espresso machine", 2.0) == {
            "present": False, "reason": "unknown_person"}


# ── The seed: a name resolves to a mask ──


def test_named_object_resolves_by_category(fixture_project):
    seg_result = fixture_project["seg"]
    object_id, mask, _used = mask_seed.resolve_subject_mask(
        seg_result, "laptop", clip_id="clip_lap", sample_index=4,
        frame_time=2.0)
    assert object_id == "obj_2"
    assert mask.any()
    seed = mask_seed.seed_point_in_mask(mask)
    height, width = SHAPE
    assert mask[int(seed.y), int(seed.x)] > 0


def test_named_object_resolves_by_label(fixture_project):
    seg_result = fixture_project["seg"]
    object_id, _mask, _used = mask_seed.resolve_subject_mask(
        seg_result, "Black Laptop", clip_id="clip_lap", sample_index=0,
        frame_time=0.0)
    assert object_id == "obj_2"


def test_two_objects_sharing_a_name_refuse(fixture_project):
    seg_result = fixture_project["seg"]
    twin = dict(seg_result["objects"][0])
    twin["object_id"] = "obj_3"
    seg_result["objects"].append(twin)
    with pytest.raises(RenRefusal) as exc_info:
        mask_seed.resolve_subject_mask(
            seg_result, "laptop", clip_id="clip_lap", sample_index=0,
            frame_time=0.0)
    assert exc_info.value.what == "ambiguous_subject"


def test_blob_ids_never_match_a_name():
    seg_result = {"resolution": list(SHAPE), "sample_fps": SAMPLE_FPS,
                  "objects": [{
                      "object_id": "obj_9", "label": "auto_object_1",
                      "category": "unknown", "frames": [0],
                      "masks_rle": {"0": seg.encode_rle(
                          np.ones(SHAPE, dtype=np.uint8))},
                      "bboxes": {"0": [0, 0, 32, 20]},
                      "avg_area_ratio": 1.0}]}
    with pytest.raises(RenRefusal) as exc_info:
        mask_seed.resolve_subject_mask(
            seg_result, "mug", clip_id="clip_x", sample_index=0,
            frame_time=0.0)
    assert exc_info.value.what == "unknown_subject"


# ── The producer: the planner's mask.create passes the algebra's gates ──


def _seed() -> MaskSeed:
    return MaskSeed(clip_id="clip_lap", frame=4, subject="laptop",
                    source=Point(7.0, 8.0), viewer=Point(70.0, 80.0),
                    object_id="obj_2", sample_index=4, seed_frame=4)


def test_mask_create_for_object_passes_the_algebra_gates():
    op = mask_seed.mask_create_for_object(_seed(), "logo-matte")
    assert op["op"] == "mask.create"
    assert op["provider"] == "resolve_magic_mask_api"
    assert op["seed"]["object_id"] == "obj_2"
    assert op["seed"]["viewer"] == {"x": 70.0, "y": 80.0}


def test_mask_create_refuses_an_unmarked_matte():
    with pytest.raises(RenRefusal):
        mask_seed.mask_create_for_object(_seed(), "logo")


def test_mask_create_refuses_an_unknown_provider():
    with pytest.raises(RuntimeError):
        mask_seed.mask_create_for_object(_seed(), "logo-matte",
                                         provider="magic_matte")


def test_produced_seed_point_is_inside_the_mask(fixture_project):
    seg_result = fixture_project["seg"]
    _object_id, mask, _used = mask_seed.resolve_subject_mask(
        seg_result, "laptop", clip_id="clip_lap", sample_index=4,
        frame_time=2.0)
    seed = mask_seed.compute_seed(
        project_folder=str(fixture_project["project"]),
        clip_id="clip_lap", frame=48, subject="laptop",
        image_rect=ImageRect(0.0, 0.0, 320.0, 200.0), source_fps=24.0,
        seg_result=seg_result)
    assert seed.object_id == "obj_2"
    assert seed.sample_index == 4
    assert mask[int(seed.source.y), int(seed.source.x)] > 0
    op = mask_seed.mask_create_for_object(seed, "logo-matte")
    assert op["seed"]["viewer"]["x"] == seed.viewer.x


# ── The step: seeds ride the content key ──


def test_step_seed_map_mistyped_never_falls_back(tmp_path):
    from library.steps.step_1_06_object_segmentation import step as s1

    assert s1._seed_map_or_none(None) is None
    with pytest.raises(TypeError):
        s1._seed_map_or_none(["not", "a", "map"])


def test_step_cache_key_covers_detector_seeds(tmp_path):
    from library.steps.step_1_06_object_segmentation import step as s1

    seeds = [{"box": [0.1, 0.1, 0.4, 0.4], "label": "black laptop",
              "category": "laptop", "detector": "owlv2",
              "frame": {"time_seconds": 0.0, "width": 1920,
                        "height": 1080}}]
    assert s1._seed_key(None) == []
    assert s1._seed_key(seeds) != []
    other = [dict(seeds[0], detector="florence2")]
    assert s1._seed_key(seeds) != s1._seed_key(other)


def test_step_reuses_face_only_but_remeasures_new_seeds(tmp_path):
    from library.steps.step_1_06_object_segmentation import step as s1

    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"0123456789" * 100)
    stored = tmp_path / "clip_lap_segmentation.json"
    _write(stored, {"objects": [], "seed_note": "face_seeded"})
    s1._stamp_segmentation_identity(stored, str(clip), None, 2.0)
    assert s1._stored_segmentation_reusable(
        stored, str(clip), None, 2.0) is not None
    seeds = [{"box": [0.1, 0.1, 0.4, 0.4], "label": "black laptop",
              "category": "laptop", "detector": "owlv2",
              "frame": {"time_seconds": 0.0, "width": 1920,
                        "height": 1080}}]
    assert s1._stored_segmentation_reusable(
        stored, str(clip), None, 2.0, seeds) is None
    s1._stamp_segmentation_identity(stored, str(clip), None, 2.0, seeds)
    assert s1._stored_segmentation_reusable(
        stored, str(clip), None, 2.0, seeds) is not None


def test_rf_detr_detectors_are_on_the_slate():
    assert ovd.check_detector(ovd.RFDETR_NANO) == ovd.RFDETR_NANO
    assert ovd.check_detector(ovd.RFDETR_SMALL) == ovd.RFDETR_SMALL
    with pytest.raises(ValueError):
        ovd.check_detector("rf_detr_medium")


def test_rf_detr_load_pins_publisher_revision(monkeypatch):
    import sys
    import types

    seen = {}

    class FakeProcessor:
        @classmethod
        def from_pretrained(cls, model_id, revision=None, **kwargs):
            seen["processor"] = (model_id, revision)
            return object()

    class FakeModel:
        @classmethod
        def from_pretrained(cls, model_id, revision=None, **kwargs):
            seen["model"] = (model_id, revision)
            return cls()

        def to(self, device):
            return self

    fake_transformers = types.ModuleType("transformers")
    fake_transformers.RfDetrImageProcessor = FakeProcessor
    fake_transformers.RfDetrForObjectDetection = FakeModel
    # The from-import inside the loader resolves through sys.modules,
    # so the fake module (not an attribute patch on whatever object
    # one import statement happens to return) is the hermetic seam.
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    loaded = ovd._load_rf_detr(
        ovd.RFDETR_NANO_MODEL_ID, ovd.RFDETR_NANO_REVISION)
    assert seen["processor"] == (
        ovd.RFDETR_NANO_MODEL_ID, ovd.RFDETR_NANO_REVISION)
    assert seen["model"] == (
        ovd.RFDETR_NANO_MODEL_ID, ovd.RFDETR_NANO_REVISION)
    assert loaded["revision"] == ovd.RFDETR_NANO_REVISION


def _rf_detr_fake_loaded(monkeypatch, tmp_path):
    import torch
    from types import SimpleNamespace

    from PIL import Image
    frame = tmp_path / "still.jpg"
    Image.new("RGB", (64, 48), "white").save(frame)

    class FakeModel:
        config = SimpleNamespace(
            id2label={0: "person", 41: "cup", 73: "laptop"})

        def __call__(self, **kwargs):
            return object()

    class FakeProcessor:
        def __call__(self, images, return_tensors=None):
            return {"pixel_values": torch.zeros(1, 3, 8, 8)}

        def post_process_object_detection(
                self, outputs, target_sizes, threshold=0.0):
            assert target_sizes == [(48, 64)]
            return [{
                "boxes": torch.tensor([
                    [6.4, 4.8, 32.0, 24.0],   # laptop, 0.9
                    [0.0, 0.0, 64.0, 48.0],   # person, 0.8
                    [32.0, 24.0, 64.0, 48.0],  # cup, 0.05 (under floor)
                ]),
                "scores": torch.tensor([0.9, 0.8, 0.05]),
                "labels": torch.tensor([73, 0, 41]),
            }]

    loaded = {"processor": FakeProcessor(), "model": FakeModel(),
              "device": "cpu", "model_id": ovd.RFDETR_NANO_MODEL_ID,
              "revision": ovd.RFDETR_NANO_REVISION}
    monkeypatch.setattr(ovd, "load_detector", lambda _name: loaded)
    return frame


def test_rf_detr_maps_coco_labels_and_drops_unasked(
        tmp_path, monkeypatch):
    frame = _rf_detr_fake_loaded(monkeypatch, tmp_path)
    found = ovd.detect_image(
        ovd.RFDETR_NANO, str(frame), ["laptop", "cup"],
        score_threshold=0.1)
    assert [(d.phrase, d.score) for d in found] == [("laptop", 0.9)]
    assert found[0].extra == {"coco_label": "laptop"}
    # The true person detection answers no queried phrase, so it is
    # dropped rather than scored as a false positive; the faint cup
    # clears no floor.
    assert all(d.phrase in ("laptop", "cup") for d in found)


def test_rf_detr_refuses_without_post_processor(tmp_path, monkeypatch):
    from PIL import Image
    frame = tmp_path / "still.jpg"
    Image.new("RGB", (16, 16), "white").save(frame)
    monkeypatch.setattr(ovd, "load_detector", lambda _name: {
        "processor": object(), "model": object(), "device": "cpu"})
    with pytest.raises(ovd.DetectorNotAvailable):
        ovd.detect_image(
            ovd.RFDETR_SMALL, str(frame), ["cup"],
            score_threshold=0.1)
