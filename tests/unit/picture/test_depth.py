"""Depth on demand (`library/tools/depth.py`, MU-16): relative depth as
one median per person region, and titles layered under a foreground
object that is not a person.

The M3g fixture is a 32x18 M2 grid: a far set and a near foreground
box (`region_001`, a laptop the speaker holds), with a second far
region (`region_002`) so front-to-back order is visible. The estimator
is injected and synthetic - brighter is nearer - so the lane builds
hermetically with no model, no network and no host memory. On `main`
this module does not exist: every test below fails there at import,
and the export test additionally fails on the missing behind-object
mode.
"""
import json
import os
import shutil
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from library.tools import (
    behind_subject,
    depth,
    footage_identity,
    source_memory,
    spatial_geometry,
)
from library.tools import person_regions as pr

GRID = (32, 18)
TIMES = (10.0, 10.5)


def _foreground_mask():
    mask = np.zeros((GRID[1], GRID[0]), dtype=bool)
    mask[6:18, 10:20] = True
    return mask


def test_project_depth_refuses_an_implicit_catalog_wide_run(tmp_path):
    with pytest.raises(ValueError, match="at least one named clip_id"):
        depth.build_project_depth(str(tmp_path))


def test_project_depth_refuses_an_empty_clip_selection(tmp_path):
    with pytest.raises(ValueError, match="at least one named clip_id"):
        depth.build_project_depth(str(tmp_path), [])


def _far_mask():
    mask = np.zeros((GRID[1], GRID[0]), dtype=bool)
    mask[0:6, 0:32] = True
    return mask


def _write_memory(root, digest):
    """M2 thumbnails plus M3d with a near and a far region at TIMES."""
    sdir = source_memory.source_dir(digest, root)
    (sdir / source_memory.SLOT_FRAMES_DIR).mkdir(parents=True)
    near, far = _foreground_mask(), _far_mask()
    frames, samples, masks = [], [], []
    for i, t in enumerate(TIMES):
        rgb = np.full((GRID[1], GRID[0], 3), 128, dtype=np.uint8)
        name = f"{source_memory.SLOT_FRAMES_DIR}/frame_{i:06d}.png"
        Image.fromarray(rgb).save(sdir / name)
        frames.append({"file": name, "t": t})
        instances = [
            {"track": "region_001", **pr.summarize(near),
             "mask": len(masks), "face": 0, "containment": 0.97,
             "face_track": "face_001"},
            {"track": "region_002", **pr.summarize(far),
             "mask": len(masks) + 1, "unassigned": "no_face_inside"},
        ]
        masks.append(pr.encode_runs(near))
        masks.append(pr.encode_runs(far))
        samples.append({"t": t, "instances": instances})
    source_memory.write_json(sdir / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": digest, "frames": frames,
        "frame_count": len(frames)})
    sidecar = pr.write_sidecar(sdir, masks, GRID)
    source_memory.write_json(sdir / source_memory.SLOT_REGIONS, {
        "content_digest": digest, "grid": list(GRID), "sidecar": sidecar,
        "tracks": [{"track_id": "region_001", "start": TIMES[0],
                    "end": TIMES[-1], "samples": len(TIMES),
                    "face_track": "face_001", "face_track_fraction": 1.0}],
        "samples": samples,
        "instrument": {"method": pr.M3D_METHOD}})


def _source(tmp_path):
    source = tmp_path / "LCATL0013.MXF"
    source.write_bytes(b"camera A bytes" * 64)
    digest = footage_identity.fingerprint(str(source))["content_digest"]
    root = tmp_path / "memory"
    _write_memory(root, digest)
    return str(source), digest, root


def _synthetic_estimator(*, near=200.0, far=20.0):
    """Raw maps (brighter is nearer): the foreground box near, the set
    far, with one outlier-hot pixel so percentile feet are visible."""
    def _estimate(paths):
        maps = []
        for _ in paths:
            raw = np.full((GRID[1], GRID[0]), far, dtype=np.float64)
            raw[_foreground_mask()] = near
            raw[0, 0] = 1000.0
            maps.append(raw)
        return maps
    return _estimate


# ── The arithmetic ────────────────────────────────────────────────


def test_normalisation_is_pooled_percentiles_not_min_max():
    inliers = np.linspace(190.0, 210.0, 100)
    stacked = np.concatenate([[10.0], inliers, [10000.0]]).reshape(1, -1, 1)
    normalised, low, high = depth.normalize_depth(stacked)
    assert (low, high) == pytest.approx((190.3, 209.7), abs=0.5)
    mid = float(normalised[0, 51, 0])
    # An inlier near 200 reads mid-scale on the pooled feet...
    assert mid == pytest.approx(0.5, abs=0.1)
    # ...where min-max would read it at (200-10)/(10000-10) = 0.019:
    # the outlier-hot pixel must not set the scale for the source.
    assert mid > 0.4


def test_a_constant_map_is_a_broken_estimate_not_a_flat_room():
    with pytest.raises(ValueError):
        depth.normalize_depth(np.full((2, 2), 7.0))


def test_depth_is_typed_relative_and_metric_requires_scale_calibration():
    relative = spatial_geometry.DepthReading(
        spatial_geometry.DepthKind.RELATIVE, 0.72, "normalized")
    assert relative.kind is spatial_geometry.DepthKind.RELATIVE
    with pytest.raises(spatial_geometry.GeometryRefusal,
                       match="requires scale calibration"):
        spatial_geometry.metric_depth(2.0, calibration=None)
    calibration = spatial_geometry.ScaleCalibration(
        "cal-1", 0.25, "synthetic known scale", "prov-c6-fixture")
    metric = spatial_geometry.metric_depth(2.0, calibration=calibration)
    assert (metric.value, metric.unit, metric.calibration_id) == (
        0.5, "m", "cal-1")


def test_camera_to_world_transform_matches_known_synthetic_geometry():
    transform = spatial_geometry.CameraWorldTransform(
        "camera", "world",
        (1, 0, 0, 10, 0, 1, 0, -2, 0, 0, 1, 3, 0, 0, 0, 1),
        "prov-c6-fixture")
    # C6 synthetic proxy: a known camera point and known rigid translation.
    assert transform.apply((1, 2, 4)) == (11.0, 0.0, 7.0)
    with pytest.raises(spatial_geometry.GeometryRefusal,
                       match="finite 4x4"):
        spatial_geometry.CameraWorldTransform(
            "camera", "world", (1, 2, 3), "prov-c6-fixture")


def test_median_depth_is_over_the_mask_and_none_when_empty():
    grid = np.arange(16, dtype=float).reshape(4, 4) / 15.0
    mask = np.zeros((4, 4), dtype=bool)
    mask[1:3, 1:3] = True
    assert depth.median_depth_in_region(grid, mask) == pytest.approx(0.5)
    assert depth.median_depth_in_region(
        grid, np.zeros((4, 4), dtype=bool)) is None
    with pytest.raises(ValueError):
        depth.median_depth_in_region(grid, np.zeros((2, 2), dtype=bool))


def test_foreground_is_smoothstep_and_feather_zero_refuses():
    near = depth.foreground_mask_from_depth(np.full((2, 2), 0.9))
    far = depth.foreground_mask_from_depth(np.full((2, 2), 0.1))
    assert (near == 1.0).all()
    assert (far == 0.0).all()
    mid = depth.foreground_mask_from_depth(np.full((1, 1), 0.40))[0, 0]
    assert mid == pytest.approx(0.5)
    with pytest.raises(ValueError):
        depth.foreground_mask_from_depth(np.full((1, 1), 0.5), feather=0.0)


def test_unmeasured_regions_sort_behind_measured_ones():
    assert depth.order_regions_by_depth(
        [("far", 0.1), ("near", 0.9), ("unknown", None)]) == [
        "near", "far", "unknown"]


def test_the_probe_names_only_known_reasons(tmp_path, monkeypatch):
    monkeypatch.delenv("PIPELINE_DEPTH_CHECKPOINT", raising=False)
    monkeypatch.delenv("PIPELINE_DEPTH_REPO", raising=False)
    name, reason = depth.probe_estimator()
    assert (name is None) == (reason is not None)
    if reason is not None:
        assert reason in depth.ESTIMATOR_REASONS


def test_pinned_machine_assets_resolve_where_installed(monkeypatch):
    """Record the depth asset resolution outcome on the running machine.

    Regression context for CAP-01: the 2026-10-14 FI-01 benchmark failed
    the depth lane because its checkpoint was unavailable; PR 2503 pinned
    the Depth Anything V2 Small assets to the machine cache. Where those
    assets are installed this resolves to the pinned estimator; elsewhere
    the test skips naming the missing environment rather than failing.
    """
    monkeypatch.delenv("PIPELINE_DEPTH_CHECKPOINT", raising=False)
    monkeypatch.delenv("PIPELINE_DEPTH_REPO", raising=False)
    name, reason = depth.probe_estimator()
    if name is None:
        pytest.skip(f"no depth assets on this machine: {reason}")
    assert name == "depth-anything-v2-small"


def test_the_probe_finds_the_pinned_machine_cache_without_environment(
        tmp_path, monkeypatch):
    checkpoint = tmp_path / "depth_anything_v2_vits.pth"
    checkpoint.write_bytes(b"pinned test weights")
    repo = tmp_path / "Depth-Anything-V2"
    source = repo / "depth_anything_v2" / "dpt.py"
    source.parent.mkdir(parents=True)
    source.write_text("# test fixture", encoding="utf-8")
    monkeypatch.setattr(depth, "DEPTH_CHECKPOINT", checkpoint)
    monkeypatch.setattr(depth, "DEPTH_REPO", repo)
    monkeypatch.setitem(sys.modules, "torch", ModuleType("torch"))
    monkeypatch.setitem(sys.modules, "transformers",
                        ModuleType("transformers"))
    monkeypatch.setattr(
        depth.subprocess, "run",
        lambda args, **kwargs: SimpleNamespace(
            stdout=depth.DEPTH_REPO_REVISION + "\n"),
    )
    monkeypatch.delenv("PIPELINE_DEPTH_CHECKPOINT", raising=False)
    monkeypatch.delenv("PIPELINE_DEPTH_REPO", raising=False)

    assert depth._depth_assets() == (checkpoint, repo)
    assert depth.probe_estimator() == ("depth-anything-v2-small", None)


# ── The lane ──────────────────────────────────────────────────────


def test_the_lane_measures_near_in_front_of_far(tmp_path):
    source, digest, root = _source(tmp_path)
    account = depth.build_source_depth(
        source, digest, root, estimator=_synthetic_estimator())
    assert account["samples_measured"] == len(TIMES)
    record = depth.read_m3g(digest, root)
    assert record["instrument"]["method"] == depth.M3G_METHOD
    assert record["direction"] == "brighter-is-nearer"
    assert record["depth_kind"] == "relative"
    assert record["unit"] == "normalized"
    assert record["metric_depth_available"] is False
    first = {r["track"]: r for r in record["samples"][0]["regions"]}
    assert first["region_001"]["median_depth"] > 0.9
    assert first["region_002"]["median_depth"] < 0.1


def test_a_lane_without_inputs_refuses_with_the_reason(tmp_path):
    source = tmp_path / "offline.MXF"
    source.write_bytes(b"bytes" * 16)
    digest = footage_identity.fingerprint(str(source))["content_digest"]
    root = tmp_path / "memory"
    with pytest.raises(RuntimeError, match="no M3d"):
        depth.build_source_depth(
            str(source), digest, root, estimator=_synthetic_estimator())


def test_a_lane_without_an_estimator_refuses_rather_than_zeros(
        tmp_path, monkeypatch):
    source, digest, root = _source(tmp_path)
    monkeypatch.delenv("PIPELINE_DEPTH_CHECKPOINT", raising=False)
    monkeypatch.delenv("PIPELINE_DEPTH_REPO", raising=False)
    # The default backend is DA3: point its probe at empty dirs too, so
    # the select falls through to the patched V2 probe and refuses.
    monkeypatch.setenv("PIPELINE_DA3_REPO", str(tmp_path / "no-repo"))
    monkeypatch.setenv("PIPELINE_DA3_WEIGHTS", str(tmp_path / "no-weights"))
    monkeypatch.delenv("PIPELINE_DEPTH_BACKEND", raising=False)
    monkeypatch.setattr(depth, "probe_estimator",
                        lambda: (None, "no_checkpoint"))
    with pytest.raises(depth.DepthUnavailable):
        depth.build_source_depth(source, digest, root)
    assert depth.read_m3g(digest, root) is None


def _fake_da3_assets(root):
    """A probe-visible DA3 installation with no runnable model: the api
    file plus one variant's weights, enough for `probe_variant`."""
    repo = root / "Depth-Anything-3"
    api = repo / "src" / "depth_anything_3" / "api.py"
    api.parent.mkdir(parents=True)
    api.write_text("# test fixture", encoding="utf-8")
    weights = root / "da3-small" / "model.safetensors"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"test weights")
    return repo, root


def test_select_backend_prefers_the_adopted_da3_when_present(
        tmp_path, monkeypatch):
    repo, weights_root = _fake_da3_assets(tmp_path / "da3")
    monkeypatch.setenv("PIPELINE_DA3_REPO", str(repo))
    monkeypatch.setenv("PIPELINE_DA3_WEIGHTS", str(weights_root))
    monkeypatch.delenv("PIPELINE_DEPTH_BACKEND", raising=False)
    assert depth.select_backend() == ("da3-small", None)


def test_select_backend_falls_back_to_v2_without_da3(
        tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DA3_REPO", str(tmp_path / "no-repo"))
    monkeypatch.setenv("PIPELINE_DA3_WEIGHTS", str(tmp_path / "no-weights"))
    monkeypatch.delenv("PIPELINE_DEPTH_BACKEND", raising=False)
    monkeypatch.setattr(depth, "probe_estimator",
                        lambda: ("depth-anything-v2-small", None))
    assert depth.select_backend() == ("depth-anything-v2-small", None)


def test_select_backend_refuses_when_nothing_can_run(
        tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DA3_REPO", str(tmp_path / "no-repo"))
    monkeypatch.setenv("PIPELINE_DA3_WEIGHTS", str(tmp_path / "no-weights"))
    monkeypatch.delenv("PIPELINE_DEPTH_BACKEND", raising=False)
    monkeypatch.setattr(depth, "probe_estimator",
                        lambda: (None, "no_checkpoint"))
    name, reason = depth.select_backend()
    assert name is None
    assert reason in depth.ESTIMATOR_REASONS


def test_a_pinned_da3_that_is_missing_refuses_loudly(
        tmp_path, monkeypatch):
    """An explicit pin is a promise: no silent switch to V2 when the
    pinned DA3 assets are absent."""
    monkeypatch.setenv("PIPELINE_DA3_REPO", str(tmp_path / "no-repo"))
    monkeypatch.setenv("PIPELINE_DA3_WEIGHTS", str(tmp_path / "no-weights"))
    monkeypatch.setenv("PIPELINE_DEPTH_BACKEND", "da3-base")
    monkeypatch.setattr(depth, "probe_estimator",
                        lambda: ("depth-anything-v2-small", None))
    assert depth.select_backend() == (None, "da3_missing")


def test_an_unknown_backend_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DEPTH_BACKEND", "da3metric-large")
    assert depth.select_backend() == (None, "unknown_backend")


def test_metric_depth_never_enters_m3g():
    """A metric map through percentile normalisation would silently
    become relative (GEO-008): refuse, and say where metric lives."""
    with pytest.raises(depth.DepthUnavailable, match="never enters M3g"):
        depth._backend_estimator("da3metric-large")


def test_depth_becomes_project_model_observations(tmp_path):
    from library.tools.project_model_evidence import migrate_project_evidence

    source, digest, root = _source(tmp_path)
    depth.build_source_depth(
        source, digest, root, estimator=_synthetic_estimator())
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pipeline_data.json").write_text(json.dumps({
        "capability_outputs": {"footage.catalog": {
            "clip_catalog": [{"clip_id": "clip_001",
                              "source_file": source}]}}}))
    model, _ = migrate_project_evidence(str(project), memory_root=root)
    found = [o for o in model["observations"]
             if o["modality"] == "depth"]
    assert len(found) == len(TIMES) * 2
    first = next(o for o in found
                 if o["payload"]["region_track"] == "region_001")
    assert first["payload"]["track_id"] == (
        f"track-region-{digest}-region_001")
    assert first["payload"]["median_depth"] > 0.9
    assert first["payload"]["direction"] == "brighter-is-nearer"


# ── The behind-object mode ────────────────────────────────────────


def _object_seg_result(frames=4, shape=(24, 32)):
    from library.tools.analysis.object_segmentation import FACE_SEED_LABEL, encode_rle

    person = np.zeros(shape, dtype=np.uint8)
    person[0:6, 0:10] = 1
    laptop = np.zeros(shape, dtype=np.uint8)
    laptop[6:18, 10:22] = 1
    return {
        "video_path": "/footage/clip_001.mp4",
        "frame_count": frames,
        "resolution": list(shape),
        "sample_fps": 2.0,
        "objects": [
            {"object_id": "obj_person", "label": FACE_SEED_LABEL,
             "category": "person", "frames": list(range(frames)),
             "masks_rle": {str(f): encode_rle(person) for f in range(frames)},
             "bboxes": {str(f): [0, 0, 10, 6] for f in range(frames)},
             "avg_area_ratio": 0.05},
            {"object_id": "obj_laptop", "label": "laptop",
             "category": "object", "frames": list(range(frames)),
             "masks_rle": {str(f): encode_rle(laptop) for f in range(frames)},
             "bboxes": {str(f): [10, 6, 12, 12] for f in range(frames)},
             "avg_area_ratio": 0.18},
        ],
    }


def test_grounding_picks_the_object_never_the_person():
    grounded, refusal = behind_subject.ground_object_segment(
        {"segment_id": "t1"}, _object_seg_result(), clip_id="c1")
    assert refusal is None
    assert grounded["object_id"] == "obj_laptop"
    assert grounded["object_label"] == "laptop"


def test_a_clip_with_only_the_person_tracked_refuses_by_name():
    from library.tools.analysis.object_segmentation import FACE_SEED_LABEL, encode_rle

    mask = np.zeros((24, 32), dtype=np.uint8)
    mask[6:18, 10:22] = 1
    seg = {"sample_fps": 2.0,
           "objects": [{"object_id": "obj_1", "label": FACE_SEED_LABEL,
                        "masks_rle": {"0": encode_rle(mask)}}]}
    grounded, refusal = behind_subject.ground_object_segment(
        {"segment_id": "t1"}, seg, clip_id="c1")
    assert grounded is None
    assert refusal["reason"] == "object_not_tracked"
    assert "behind_subject" in refusal["detail"]
    grounded, refusal = behind_subject.ground_object_segment(
        {"segment_id": "t1"}, None, clip_id="c1")
    assert refusal["reason"] == "no_segmentation"


def test_a_behind_object_title_with_no_clip_refuses(tmp_path):
    segment = {"segment_id": "t1", "timeline_start": 2.0,
               "timeline_end": 6.0}
    with pytest.raises(behind_subject.BehindSubjectRefused):
        behind_subject.apply_behind_object(
            [segment], {"c1": _object_seg_result()},
            clip_at=lambda s, e: [],
            matte_dir=str(tmp_path / "mattes"),
            precomp_dir=str(tmp_path / "precomp"), timeline_fps=10.0,
            clip_metadata={"c1": {"width": 32, "height": 24}})


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg and ffprobe - CI installs both")


def _title_sequence(directory, count=40, size=(32, 24)):
    from PIL import Image

    os.makedirs(directory, exist_ok=True)
    pattern = os.path.join(directory, "t_object_%05d.png")
    for i in range(count):
        Image.fromarray(
            np.full((size[1], size[0], 4), (255, 255, 255, 255),
                    dtype=np.uint8), mode="RGBA").save(pattern % i)
    return {
        "segment_id": "t1",
        "overlay_path": pattern % 0,
        "timeline_start": 2.0,
        "timeline_end": 6.0,
        "total_frames": count,
        "sequence": {"pattern": pattern, "first_frame": pattern % 0,
                     "frame_count": count},
    }


@needs_ffmpeg
def test_the_export_shows_a_foreground_object_occluding_the_title(
        tmp_path):
    """Acceptance (MU-16): a behind-OBJECT render shows the foreground
    object occluding the title on an EXPORTED frame - never on a
    viewer, and never on the precomp alone. The composite is what the
    build places: the precomp on its overlay row over the untouched
    picture, flattened by ffmpeg the way Resolve renders premultiplied
    Full. A title drawn on top instead of under would read white where
    the laptop is."""
    import subprocess

    from PIL import Image

    plate = np.full((24, 32, 3), 128, dtype=np.uint8)
    plate_path = str(tmp_path / "plate.png")
    Image.fromarray(plate, mode="RGB").save(plate_path)
    segment = _title_sequence(str(tmp_path / "titles"))
    placed, mattes, per_segment = behind_subject.apply_behind_object(
        [segment], {"c1": _object_seg_result()},
        clip_at=lambda s, e: [{"label": "clip1", "clip_id": "c1",
                               "source_file": "/footage/c1.mp4",
                               "timeline_in": 0.0, "timeline_out": 10.0,
                               "source_in": 0.0}],
        matte_dir=str(tmp_path / "mattes"),
        precomp_dir=str(tmp_path / "precomp"), timeline_fps=10.0,
        clip_metadata={"c1": {"width": 32, "height": 24}})
    assert placed[0]["layer"] == behind_subject.PRECOMPOSED_OBJECT_LAYER
    assert mattes[0]["object_label"] == "laptop"
    assert per_segment[0]["precomps"][0]["object_label"] == "laptop"
    export = str(tmp_path / "export_%05d.png")
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "10",
         "-i", plate_path, "-i", placed[0]["overlay_path"],
         "-filter_complex",
         "[0:v][1:v]overlay=0:0:format=rgb:shortest=1,format=rgb24",
         "-frames:v", "40", export],
        capture_output=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr.decode()[-300:]
    frame = np.asarray(Image.open(str(tmp_path / "export_00020.png")))
    assert frame.shape == (24, 32, 3)
    # Where the laptop is, the plate: the title passes behind it.
    assert (frame[6:18, 10:22] == 128).all()
    # Everywhere else, the title's own white.
    assert (frame[0:6, :] == 255).all()
    assert (frame[:, 0:10] == 255).all()
    assert (frame[:, 22:] == 255).all()
