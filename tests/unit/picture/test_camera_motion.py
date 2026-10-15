import pytest

from library.tools.camera_motion import (
    matches_motion_constraints,
    summarize_camera_motion,
)
from library.tools.analysis.camera_motion_query import rank_static_intervals


def _index():
    rows = []
    for i in range(20):
        rows.append({
            "time": (i + 1) / 5,
            "translation_x": -0.01 if i < 10 else 0.3,
            "translation_y": 0.0,
            "rotation_rad": 0.0,
            "zoom_rate": 0.0,
            "subject_energy": 0.02 if i < 10 else 0.4,
        })
    return {"camera_motion_decomposition": {
        "sample_rate_hz": 5, "values": rows}}


def test_motion_summary_is_interval_scoped_and_multiscale():
    summary = summarize_camera_motion(_index(), 0, 2.1)
    assert summary["sample_count"] == 10
    assert summary["camera_motion"]["pan"] == pytest.approx(-0.01)
    assert summary["camera_motion"]["subject_energy"] == pytest.approx(0.02)
    assert len(summary["multi_resolution"]) == 5
    assert matches_motion_constraints(summary, max_static_score=0.02,
                                      max_subject_energy=0.03)
    moving = summarize_camera_motion(_index(), 2.1, 4.1)
    assert moving["static_score"] > summary["static_score"]
    assert not matches_motion_constraints(moving, max_static_score=0.02)


def test_missing_motion_fails_numeric_constraints_closed():
    summary = summarize_camera_motion({}, 0, 3)
    assert summary["static_score"] is None
    assert not matches_motion_constraints(summary, max_static_score=0.1)


def test_static_broll_retrieval_uses_numeric_limits_and_skips_unmeasured():
    rows = rank_static_intervals(
        [{"clip_id": "static", "duration_seconds": 2.0,
          "source_file": "static.mov"},
         {"clip_id": "unknown", "duration_seconds": 3.0},
         {"clip_id": "short", "duration_seconds": 1.0}],
        {"static": {"camera_motion_decomposition": {
            "sample_rate_hz": 5,
            "values": [{"time": (i + 1) / 5, "translation_x": 0.001,
                        "translation_y": 0.0, "rotation_rad": 0.0,
                        "zoom_rate": 0.0, "subject_energy": 0.001}
                       for i in range(10)]}},
         "unknown": {}},
        min_duration=2.0, max_static_score=0.01)
    assert [row["clip_id"] for row in rows] == ["static"]
    assert rows[0]["source_file"] == "static.mov"


@pytest.mark.parametrize(("start", "end"), [(-1, 1), (1, 1), (2, 1)])
def test_invalid_interval_refuses(start, end):
    with pytest.raises(ValueError, match="interval"):
        summarize_camera_motion({}, start, end)
