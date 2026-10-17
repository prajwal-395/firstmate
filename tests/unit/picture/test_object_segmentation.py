"""Step 1.06 is wired and matte-triggered: it segments ONLY the clips a
matte-needing plan names, and the trigger is testable without a GPU.

A clip is wanted when a `subject_grades` entry names its clip_id, or a
`behind_subject_overlays` segment plays over a span a placed picture
clip covers. Nothing planned, nothing segmented - the step returns an
empty trigger without loading the segmenter.
"""
import json
import os
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from prototypes.depth_blur.build_depth_blur import (
    even,
    foreground_alpha,
    normalize_depth,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_1_06_object_segmentation.step import (
    face_boxes_from_temporal_index,
    matte_trigger,
    run_step,
)


def _aroll(*rows):
    return [{"clip_id": cid, "timeline_start": start, "timeline_end": end}
            for cid, start, end in rows]


def _behind(*rows):
    return {"segments": [
        {"segment_id": sid, "timeline_start": start, "timeline_end": end}
        for sid, start, end in rows]}


def test_no_plan_names_no_clip():
    assert matte_trigger() == {}
    assert matte_trigger(
        color_grade_spec={"subject_grades": []},
        behind_subject_overlays={"segments": []},
        a_roll_assignments=_aroll(("c1", 0.0, 10.0))) == {}
    # A behind span outside every placed clip names nothing.
    assert matte_trigger(
        behind_subject_overlays=_behind(("t1", 20.0, 25.0)),
        a_roll_assignments=_aroll(("c1", 0.0, 10.0))) == {}


def test_a_subject_grade_names_its_clip():
    wanted = matte_trigger(
        color_grade_spec={"subject_grades": [
            {"clip_id": "c1", "scope": "subject-only"}]})
    assert list(wanted) == ["c1"]
    assert wanted["c1"]


def test_a_behind_segment_names_every_clip_its_span_plays_over():
    wanted = matte_trigger(
        behind_subject_overlays=_behind(("t1", 2.0, 5.0)),
        a_roll_assignments=_aroll(("c1", 0.0, 4.0), ("c2", 4.0, 10.0)))
    assert sorted(wanted) == ["c1", "c2"]
    # B-roll cover counts as a wanted clip.
    wanted = matte_trigger(
        behind_subject_overlays=_behind(("t1", 2.0, 5.0)),
        a_roll_assignments=_aroll(("c1", 0.0, 10.0)),
        b_roll_assignments=[{
            "clip_id": "b7", "timeline_start": 3.0, "timeline_end": 6.0}])
    assert sorted(wanted) == ["b7", "c1"]


def test_a_speech_block_names_its_video_segments_clip():
    """The real assign_aroll shape nests the clip_id.

    A speech block carries no top-level clip_id - the picture it
    plays comes from `video_segments`. Measured 2026-09-24: a behind
    title over a real speech block triggered nothing and the compile
    refused for no matte.
    """
    wanted = matte_trigger(
        behind_subject_overlays=_behind(("t1", 0.0, 0.72)),
        a_roll_assignments=[{
            "spine_block_position": 1, "block_type": "speech",
            "timeline_start": 0.0, "timeline_end": 0.72,
            "video_segments": [{
                "clip_id": "clip_001",
                "duration_seconds": 0.72,
                "video_in": 3.32, "video_out": 4.04,
            }],
        }])
    assert list(wanted) == ["clip_001"]

    # Video-segment shares resolve within the block span.
    wanted = matte_trigger(
        behind_subject_overlays=_behind(("t1", 5.0, 6.0)),
        a_roll_assignments=[{
            "spine_block_position": 1, "block_type": "speech",
            "timeline_start": 0.0, "timeline_end": 10.0,
            "video_segments": [
                {"clip_id": "c1", "duration_seconds": 4.0},
                {"clip_id": "c2", "duration_seconds": 6.0},
            ],
        }])
    assert list(wanted) == ["c2"]


def test_face_boxes_come_from_the_per_clip_index_files(tmp_path):
    index = tmp_path / "c1_index.json"
    index.write_text(json.dumps({
        "clip_id": "c1",
        "face_presence": {"face_boxes": [None, [0.1, 0.2, 0.3, 0.5]]},
    }), encoding="utf-8")
    boxes = face_boxes_from_temporal_index({
        "temporal_event_indices": [
            {"clip_id": "c1", "index_path": str(index)},
            {"clip_id": "c2", "index_path": str(tmp_path / "missing.json")},
        ]})
    assert boxes == {"c1": [0.1, 0.2, 0.3, 0.5]}


def test_an_untriggered_run_segments_nothing_and_loads_no_model(tmp_path):
    from library.steps.step_1_06_object_segmentation import step as mod
    monkeypatch = pytest.MonkeyPatch()
    def _boom():
        raise AssertionError("the segmenter must not load")
    monkeypatch.setattr(mod, "get_segmenter", _boom)
    try:
        result = run_step(
            [], [{"clip_id": "c1", "path": str(tmp_path / "c1.mp4")}],
            str(tmp_path / "out"))
    finally:
        monkeypatch.undo()
    assert result["object_segmentation"] == []
    assert result["matte_trigger"]["wanted_clip_ids"] == []


def test_model_lifecycle_keeps_a_batch_resident_and_releases_it():
    from library.tools import model_lifecycle

    model_lifecycle.unload_all()
    loads = []
    marker = object()
    def loader():
        loads.append(marker)
        return marker

    with model_lifecycle.resident_model("test-sam2", loader):
        assert getattr(model_lifecycle, "load_model")(
            "test-sam2", loader
        ) is marker
        with model_lifecycle.managed_model("test-sam2", loader) as first:
            assert first is marker
        with model_lifecycle.managed_model("test-sam2", loader) as second:
            assert second is marker
        assert len(loads) == 1
        assert "test-sam2" in model_lifecycle._loaded_models

    assert "test-sam2" not in model_lifecycle._loaded_models


def test_seeded_session_does_not_load_automatic_mask_generator(monkeypatch):
    from library.tools.analysis import object_segmentation as segmentation

    predictor = object()
    module = ModuleType("sam2.build_sam")
    module.build_sam2_video_predictor_hf = (
        lambda model_id, device: predictor)
    module.build_sam2_hf = lambda *args, **kwargs: pytest.fail(
        "the seeded session must not build the automatic-mask model")
    generator_module = ModuleType("sam2.automatic_mask_generator")
    generator_module.SAM2AutomaticMaskGenerator = lambda model: pytest.fail(
        "the seeded session must not construct the automatic-mask generator")
    monkeypatch.setitem(sys.modules, "sam2", ModuleType("sam2"))
    monkeypatch.setitem(sys.modules, "sam2.build_sam", module)
    monkeypatch.setitem(
        sys.modules, "sam2.automatic_mask_generator", generator_module)
    monkeypatch.setattr(segmentation, "_resolve_device", lambda device: device)

    assert segmentation.ObjectSegmenter._load_sam2(
        include_generator=False) == (predictor, None)


def test_model_lifecycle_requires_explicit_residency_for_direct_load():
    from library.tools import model_lifecycle

    model_lifecycle.unload_all()
    with pytest.raises(RuntimeError, match="active resident_model scope"):
        getattr(model_lifecycle, "load_model")("unscoped-test", object)


def test_model_lifecycle_serializes_loads_and_defers_eviction_for_users():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from library.tools import model_lifecycle

    model_lifecycle.unload_all()
    model = object()
    loads = []
    ready = threading.Event()
    release = threading.Event()
    entered = 0
    entered_lock = threading.Lock()

    def loader():
        loads.append(model)
        return model

    def use_model():
        nonlocal entered
        with model_lifecycle.managed_model("concurrent-test", loader) as loaded:
            with entered_lock:
                entered += 1
                if entered == 2:
                    ready.set()
            assert release.wait(timeout=5)
            return loaded

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(use_model)
        second = pool.submit(use_model)
        assert ready.wait(timeout=5)
        assert loads == [model]
        stats = model_lifecycle.resource_accounting()["concurrent-test"]
        assert stats["active_users"] == 2
        assert stats["loads"] == 1
        assert stats["cache_hits"] == 1

        assert not model_lifecycle.unload_model("concurrent-test")
        assert model_lifecycle.resource_accounting()[
            "concurrent-test"]["eviction_pending"]
        assert "concurrent-test" in model_lifecycle._loaded_models
        release.set()
        assert first.result(timeout=5) is model
        assert second.result(timeout=5) is model

    stats = model_lifecycle.resource_accounting()["concurrent-test"]
    assert stats["active_users"] == 0
    assert stats["evictions"] == 1
    assert "concurrent-test" not in model_lifecycle._loaded_models


def test_model_lifecycle_explicit_eviction_clears_batch_residency():
    from library.tools import model_lifecycle

    model_lifecycle.unload_all()
    loads = []
    def loader():
        model = object()
        loads.append(model)
        return model

    with model_lifecycle.resident_model("test-sam2-pressure", loader):
        model_lifecycle.unload_model("test-sam2-pressure")
        with model_lifecycle.managed_model("test-sam2-pressure", loader):
            pass
    assert len(loads) == 2
    assert "test-sam2-pressure" not in model_lifecycle._loaded_models


# ── The wiring ──────────────────────────────────────────────────────

def _dag():
    from library.tools import run_scope
    return run_scope.load_dag()


def test_the_step_runs_after_the_plans_and_before_the_compile():
    from library.tools import run_scope
    order = run_scope.topological_order(_dag())
    rank = {node_id: i for i, node_id in enumerate(order)}
    assert rank["render_motion_graphics"] < rank["object_segmentation"]
    assert rank["color_grade"] < rank["object_segmentation"]
    assert rank["object_segmentation"] < rank["compile_manifest"]


# --------------------------------------------------------------------------
# From test_depth_blur_prototype.py

def test_normalize_depth_uses_one_pooled_2nd_to_98th_percentile_range():
    depths = np.arange(100, dtype=np.float32).reshape(2, 5, 10)

    normalized, low, high = normalize_depth(depths)

    assert low == pytest.approx(np.percentile(depths, 2))
    assert high == pytest.approx(np.percentile(depths, 98))
    assert normalized.shape == depths.shape
    assert normalized.min() == 0.0
    assert normalized.max() == 1.0


def test_foreground_alpha_softens_only_the_relative_depth_transition():
    args = SimpleNamespace(
        foreground_center=0.5,
        foreground_feather=0.4,
        mask_softness_px=0.0,
    )
    normalized_depth = np.array([[0.3, 0.5, 0.7]], dtype=np.float32)

    alpha = foreground_alpha(normalized_depth, args, (3, 1))

    np.testing.assert_allclose(alpha.ravel(), [0.0, 0.5, 1.0], atol=1e-6)


def test_even_resolution_helper_reduces_odd_dimension():
    assert even(960) == 960
    assert even(539) == 538


# --------------------------------------------------------------------------
# Content-keyed reuse: a stored matte measuring these exact bytes is
# served from disk, never re-segmented.


def _seg_step_module():
    import library.steps.step_1_06_object_segmentation.step as seg_step
    return seg_step


def _write_clip(tmp_path, name="clip.mp4", payload=b"x" * 300000):
    clip = tmp_path / name
    clip.write_bytes(payload)
    return str(clip)


def _stored_doc(video_path, n_objects=2):
    return {
        "video_path": video_path,
        "frame_count": 10,
        "resolution": [1920, 1080],
        "sample_fps": 2.0,
        "seed_note": "face-seeded",
        "objects": [{"id": i} for i in range(n_objects)],
    }


def test_a_stamped_matte_reuses_identical_bytes(tmp_path):
    seg_step = _seg_step_module()
    clip_path = _write_clip(tmp_path)
    target = tmp_path / "c1_segmentation.json"
    target.write_text(json.dumps(_stored_doc(clip_path)))
    face_box = [0.1, 0.1, 0.5, 0.5]

    seg_step._stamp_segmentation_identity(
        target, clip_path, face_box, seg_step.SEGMENT_SAMPLE_FPS)

    cached = seg_step._stored_segmentation_reusable(
        target, clip_path, face_box, seg_step.SEGMENT_SAMPLE_FPS)
    assert cached == {"object_count": 2, "seed_note": "face-seeded"}


def test_a_refreshed_source_remeasures(tmp_path):
    seg_step = _seg_step_module()
    clip_path = _write_clip(tmp_path)
    target = tmp_path / "c1_segmentation.json"
    target.write_text(json.dumps(_stored_doc(clip_path)))
    face_box = [0.1, 0.1, 0.5, 0.5]
    seg_step._stamp_segmentation_identity(
        target, clip_path, face_box, seg_step.SEGMENT_SAMPLE_FPS)

    with open(clip_path, "wb") as f:
        f.write(b"y" * 300000)

    assert seg_step._stored_segmentation_reusable(
        target, clip_path, face_box, seg_step.SEGMENT_SAMPLE_FPS) is None


def test_a_legacy_matte_without_identity_remeasures(tmp_path):
    seg_step = _seg_step_module()
    clip_path = _write_clip(tmp_path)
    target = tmp_path / "c1_segmentation.json"
    target.write_text(json.dumps(_stored_doc(clip_path)))

    assert seg_step._stored_segmentation_reusable(
        target, clip_path, [0.1, 0.1, 0.5, 0.5],
        seg_step.SEGMENT_SAMPLE_FPS) is None


def test_a_new_face_box_remeasures(tmp_path):
    seg_step = _seg_step_module()
    clip_path = _write_clip(tmp_path)
    target = tmp_path / "c1_segmentation.json"
    target.write_text(json.dumps(_stored_doc(clip_path)))
    seg_step._stamp_segmentation_identity(
        target, clip_path, [0.1, 0.1, 0.5, 0.5],
        seg_step.SEGMENT_SAMPLE_FPS)

    assert seg_step._stored_segmentation_reusable(
        target, clip_path, [0.2, 0.2, 0.6, 0.6],
        seg_step.SEGMENT_SAMPLE_FPS) is None


def test_run_step_serves_a_cached_matte_without_loading_the_model(
        tmp_path, monkeypatch):
    seg_step = _seg_step_module()
    clip_path = _write_clip(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    cached_file = out_dir / "c1_segmentation.json"
    cached_file.write_text(json.dumps(_stored_doc(clip_path)))
    face_box = [0.1, 0.1, 0.5, 0.5]
    seg_step._stamp_segmentation_identity(
        cached_file, clip_path, face_box, seg_step.SEGMENT_SAMPLE_FPS)

    def _no_model():
        raise AssertionError("segmenter must not load on a cache hit")

    monkeypatch.setattr(seg_step, "get_segmenter", _no_model)

    result = seg_step.run_step(
        [],
        [{"clip_id": "c1", "path": clip_path}],
        str(out_dir),
        face_boxes_by_clip={"c1": face_box},
        color_grade_spec={"subject_grades": [
            {"clip_id": "c1", "scope": "subject-only"}]},
    )

    assert result["matte_trigger"]["reused"] == ["c1"]
    assert result["matte_trigger"]["segmented"] == ["c1"]
    assert result["object_segmentation"][0]["reused"] is True
    assert result["object_segmentation"][0]["object_count"] == 2


@pytest.mark.parametrize("pressure_after_first_clip, expected_sessions", [
    (False, 1), (True, 2),
])
def test_scheduled_sam_session_spans_clips_and_yields_for_ram_waiters(
        tmp_path, monkeypatch, pressure_after_first_clip, expected_sessions):
    from contextlib import nullcontext

    from library.steps.step_1_06_object_segmentation import step as mod
    from library.tools.analysis.object_segmentation import SegmentationResult

    clip_paths = [_write_clip(tmp_path, "first.mp4"),
                  _write_clip(tmp_path, "second.mp4")]
    out_dir = tmp_path / "out"
    face_box = [0.1, 0.1, 0.8, 0.8]

    class FakeSegmenter:
        def __init__(self):
            self.run_active = False
            self.begins = 0
            self.ends = 0
            self.calls = []

        def begin_run(self, **kwargs):
            self.begins += 1
            self.run_active = True

        def end_run(self):
            self.ends += 1
            self.run_active = False

        def segment_clip(self, path, **kwargs):
            self.calls.append(path)
            return SegmentationResult(path, 1, (2, 2), 2.0, [],
                                      seed_note="face_seeded")

    segmenter = FakeSegmenter()
    monkeypatch.setattr(mod, "get_segmenter", lambda: segmenter)
    monkeypatch.setattr(mod, "heavy_work_reservation",
                        lambda *a, **k: nullcontext())
    monkeypatch.setattr(mod, "heavy_work_lock",
                        lambda *a, **k: nullcontext())
    pressure_calls = iter([pressure_after_first_clip, False])
    monkeypatch.setattr(mod, "memory_reservation_contended",
                        lambda: next(pressure_calls))

    result = mod.run_step(
        [], [{"clip_id": f"c{i}", "path": path}
             for i, path in enumerate(clip_paths, 1)],
        str(out_dir), face_boxes_by_clip={"c1": face_box, "c2": face_box},
        color_grade_spec={"subject_grades": [
            {"clip_id": "c1", "scope": "subject-only"},
            {"clip_id": "c2", "scope": "subject-only"},
        ]})

    assert result["matte_trigger"]["segmented"] == ["c1", "c2"]
    assert segmenter.calls == clip_paths
    assert (segmenter.begins, segmenter.ends) == (
        expected_sessions, expected_sessions)
    assert not segmenter.run_active
