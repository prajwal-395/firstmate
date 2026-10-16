"""DA3 depth backends and geometric readings (GEO-001..012).

Hermetic: geometry over synthetic arrays, loader probes over tmp dirs,
the estimator protocol over an injected model. Nothing here touches the
network, a model, or Resolve. Live weights run only under
`PIPELINE_DA3_SMOKE=1` with the pinned assets on disk, and skip
otherwise - CI never sees them.
"""

import os

import numpy as np
import pytest

from library.tools import depth_da3, depth_geometry


def _grid(value=0.0, bump=1.0):
    depth = np.full((24, 32), value)
    depth[8:16, 10:20] += bump
    return depth


def test_rank_correlation_is_scale_free():
    first = _grid()
    assert depth_geometry.rank_correlation(first, first * 3.0 + 7.0) == \
        pytest.approx(1.0)
    assert depth_geometry.rank_correlation(first, -first) == pytest.approx(-1.0)


def test_rank_correlation_refuses_constants():
    with pytest.raises(ValueError, match="constant"):
        depth_geometry.rank_correlation(np.ones((4, 4)), _grid()[:4, :4])


def test_temporal_consistency_needs_two_maps():
    with pytest.raises(ValueError, match="at least two"):
        depth_geometry.temporal_consistency([_grid()])


def test_temporal_consistency_reads_still_footage_near_one():
    rng = np.random.RandomState(7)
    base = _grid() + rng.rand(24, 32) * 1e-6
    again = _grid() + rng.rand(24, 32) * 1e-6
    assert depth_geometry.temporal_consistency([base, again])[0] > 0.9


def test_edge_agreement_rewards_aligned_discontinuities():
    gray = np.zeros((24, 32), dtype=np.uint8)
    gray[:, 16:] = 200
    aligned = np.zeros((24, 32))
    aligned[:, 16:] = 1.0
    assert depth_geometry.edge_agreement(aligned, gray) > 0.9
    flat = np.zeros((24, 32))
    flat[0:4, 0:4] = 1.0
    assert depth_geometry.edge_agreement(flat, gray) < 0.5


def test_edge_agreement_refuses_shape_mismatch():
    with pytest.raises(ValueError, match="one window"):
        depth_geometry.edge_agreement(np.zeros((8, 8)),
                                      np.zeros((4, 4), dtype=np.uint8))


def test_relative_motion_reads_a_pure_translation():
    ext = np.tile(np.eye(4), (3, 1, 1))
    ext[1, 0, 3] = 0.5
    ext[2, 0, 3] = 1.0
    steps = depth_geometry.relative_motion(ext[:, :3, :])
    assert [round(s["translation"], 6) for s in steps] == [0.5, 0.5]
    assert all(s["rotation_deg"] == pytest.approx(0.0) for s in steps)


def test_relative_motion_reads_a_pure_rotation():
    ext = np.tile(np.eye(4), (2, 1, 1))
    angle = np.radians(10.0)
    ext[1, :3, :3] = [[np.cos(angle), -np.sin(angle), 0],
                       [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    steps = depth_geometry.relative_motion(ext)
    assert steps[0]["translation"] == pytest.approx(0.0)
    assert steps[0]["rotation_deg"] == pytest.approx(10.0)


def test_stillness_names_a_locked_off_camera():
    ext = np.tile(np.eye(4), (4, 1, 1))
    verdict = depth_geometry.stillness(
        depth_geometry.relative_motion(ext), seconds_per_step=0.5)
    assert verdict["reads_static"] is True


def test_motion_attribution_splits_camera_from_subject():
    assert depth_geometry.motion_attribution(0.0, 0.0, 0.0)[
        "attribution"] == "still"
    assert depth_geometry.motion_attribution(0.5, 0.6, 0.0)[
        "attribution"] == "camera-dominant"
    assert depth_geometry.motion_attribution(0.0, 0.9, 2.5)[
        "attribution"] == "subject-dominant"


def test_confidence_reliability_rewards_ordered_confidence():
    rng = np.random.RandomState(3)
    conf = np.tile(np.linspace(0, 1, 25).reshape(5, 5), (5, 5))
    dis = (1.0 - conf) + rng.rand(25, 25) * 0.01
    report = depth_geometry.confidence_reliability(conf, dis, bins=5)
    assert report["calibration_rho"] > 0.9


def test_summarize_interval_carries_scale_and_camera():
    record = depth_geometry.summarize_interval(
        _grid(), np.full((24, 32), 2.0), "static", "relative", "normalized")
    assert record["camera_state"] == "static"
    assert record["depth_kind"] == "relative"
    assert record["unit"] == "normalized"
    assert abs(record["near_fraction"] + record["mid_fraction"]
               + record["far_fraction"] - 1.0) < 1e-9


def test_summarize_interval_refuses_mixed_scales():
    with pytest.raises(ValueError, match="relative/metric"):
        depth_geometry.summarize_interval(_grid(), None, "static",
                                          "disparity", "normalized")


def test_summarize_interval_refuses_nonfinite_confidence():
    with pytest.raises(ValueError, match="non-finite"):
        depth_geometry.summarize_interval(
            _grid(), np.full((24, 32), np.nan), "static",
            "relative", "normalized")


def _record(camera="static", kind="relative", conf=2.0):
    return depth_geometry.summarize_interval(
        _grid(), None if conf is None else np.full((24, 32), conf),
        camera, kind, "meters" if kind == "metric" else "normalized")


def test_select_intervals_filters_by_camera_kind_and_confidence():
    rows = [_record("static", "relative", 8.0),
            _record("moving", "relative", 9.0),
            _record("static", "metric", 7.0)]
    assert depth_geometry.select_intervals(rows, camera_state="static") == \
        [rows[0], rows[2]]
    assert depth_geometry.select_intervals(rows, depth_kind="metric") == \
        [rows[2]]
    assert depth_geometry.select_intervals(rows, min_confidence=8.0) == \
        [rows[0], rows[1]]
    assert depth_geometry.select_intervals(
        rows, camera_state="static", depth_kind="relative",
        min_confidence=8.0) == [rows[0]]


def test_select_intervals_drops_records_missing_filtered_keys():
    rows = [_record("static", "relative", None), {"camera_state": "static"}]
    assert depth_geometry.select_intervals(rows, min_confidence=1.0) == []
    assert depth_geometry.select_intervals(rows, depth_kind="relative") == \
        [rows[0]]


def test_match_inliers_reports_both_counts():
    rng = np.random.RandomState(11)
    gray = (rng.rand(96, 128) * 255).astype(np.uint8)
    depth = rng.rand(96, 128)
    report = depth_geometry.match_inliers(gray, gray, depth, depth)
    assert report["inliers_gated"] <= report["matches"]
    assert report["inliers_plain"] >= report["inliers_gated"] - \
        report["matches"]


def test_probe_names_absent_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DA3_REPO", str(tmp_path / "nope"))
    monkeypatch.setenv("PIPELINE_DA3_WEIGHTS", str(tmp_path / "noweights"))
    assert depth_da3.probe_variant("da3-small") == \
        (False, "no-code-checkout")
    assert depth_da3.probe_variant("da3-giant-xl") == \
        (False, "unknown-variant")
    assert depth_da3.probe_variant("da3-giant") == (False, "research-only")


def test_research_only_refuses_client_work():
    with pytest.raises(depth_da3.Da3ResearchOnly):
        depth_da3.load_variant("da3-giant", purpose="commercial_client")
    with pytest.raises(depth_da3.Da3ResearchOnly):
        depth_da3.load_variant("da3-giant",
                               purpose="personal_noncommercial")


def test_native_direction_is_measured_for_benchmarked_variants():
    assert set(depth_da3.DIRECTION) == set(depth_da3.VARIANTS)
    oriented = depth_da3.native_to_brighter_nearer(
        "da3-small", np.array([[0.5, 1.5]]))
    assert oriented[0, 0] > oriented[0, 1]


def test_native_orientation_refuses_unmeasured_variants(monkeypatch):
    monkeypatch.delitem(depth_da3.DIRECTION, "da3-small")
    with pytest.raises(depth_da3.Da3Unavailable):
        depth_da3.native_to_brighter_nearer("da3-small", np.zeros((2, 2)))


def test_license_evidence_names_publisher_and_revision():
    for name in depth_da3.VARIANTS:
        evidence = depth_da3.license_evidence(name)
        assert evidence["license"] == "Apache-2.0"
        assert evidence["exact_version"]
        assert evidence["upstream_url"].startswith("https://")


@pytest.mark.skipif(os.environ.get("PIPELINE_DA3_SMOKE") != "1",
                    reason="live DA3 weights only under PIPELINE_DA3_SMOKE=1")
def test_live_da3_small_reports_pose_and_confidence(tmp_path):
    from PIL import Image

    frame = (np.random.RandomState(0).rand(240, 320, 3) * 255).astype("uint8")
    path = tmp_path / "frame.png"
    Image.fromarray(frame).save(path)
    estimate = depth_da3.estimator_for("da3-small", process_res=320)
    maps = estimate([str(path)])
    assert len(maps) == 1 and np.isfinite(maps[0]).all()
