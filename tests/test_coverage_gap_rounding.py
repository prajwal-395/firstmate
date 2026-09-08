"""A sub-frame abutment is not a hole in the timeline.

`_video_coverage_gaps` exists to catch the 6.4s of black that once
shipped, and it counts in frames rather than seconds because float noise
between abutting clips would otherwise read as findings. But the frame
number of each clip boundary is rounded INDEPENDENTLY, so two clips that
genuinely meet can land on different frames: on project 001, clips
meeting at 43.646s and 43.650s became frames 1309 and 1310 and failed
compile_manifest with "1 uncovered range totalling 0.004s".

Four milliseconds is an eighth of a frame at 30fps. It cannot render as
black. It stopped a render one step from the end.

So a hole now has to be a frame wide in BOTH clocks. These tests hold
that line from both sides: the rounding artefact must pass, and the real
hole the assertion was written for must still fail.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "compile_manifest_step",
    REPO_ROOT / "library" / "steps" / "step_5_04_compile_manifest" / "step.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_video_coverage_gaps = _mod._video_coverage_gaps


def manifest(clips, duration, fps=30.0):
    return {
        "project": {"frame_rate": fps, "duration_seconds": duration},
        "tracks": {"V1": {"clips": [
            {"timeline_in": a, "timeline_out": b} for a, b in clips
        ]}},
    }


def test_the_001_rounding_artefact_is_not_a_gap():
    """43.646 -> 43.650 rounds to frames 1309 and 1310."""
    gaps = _video_coverage_gaps(
        manifest([(0.0, 43.646), (43.650, 54.768)], duration=54.768))
    assert gaps == [], f"sub-frame abutment reported as a hole: {gaps}"


def test_clips_that_abut_exactly_are_not_a_gap():
    gaps = _video_coverage_gaps(
        manifest([(0.0, 10.0), (10.0, 20.0)], duration=20.0))
    assert gaps == []


def test_a_real_hole_still_fails():
    """The 6.4s of black this assertion was written for."""
    gaps = _video_coverage_gaps(
        manifest([(0.0, 4.3), (10.7, 20.0)], duration=20.0))
    assert len(gaps) == 1
    start, end = gaps[0]
    assert abs((end - start) - 6.4) < 1e-6


def test_a_one_frame_hole_still_fails():
    """The tolerance is one frame, and one frame of real black counts."""
    one_frame = 1.0 / 30.0
    gaps = _video_coverage_gaps(
        manifest([(0.0, 10.0), (10.0 + one_frame, 20.0)], duration=20.0))
    assert len(gaps) == 1, "a genuine one-frame hole must still be caught"


def test_a_hole_at_the_end_still_fails():
    """The outro case: 1.5s of nothing after the last clip."""
    gaps = _video_coverage_gaps(
        manifest([(0.0, 54.768)], duration=56.270))
    assert len(gaps) == 1
    start, end = gaps[0]
    assert abs(start - 54.768) < 1e-6
    assert abs(end - 56.270) < 1e-6


def test_a_sub_frame_tail_is_not_a_hole():
    gaps = _video_coverage_gaps(
        manifest([(0.0, 54.768)], duration=54.772))
    assert gaps == []


# ── The same rounding class, in the manifest validator ──────────────
#
# `in=8.38 < prev_out=8.382000000000001` failed the build on project 001.
# Two milliseconds is a sixteenth of a frame; the renderer cannot show an
# overlap that small. A real collision is frames or seconds wide, and
# must still fail.

from library.tools.manifest_validator import _validate_structure  # noqa: E402


def _manifest_with(clips, fps=30.0):
    return {
        "project": {"frame_rate": fps, "duration_seconds": 60.0},
        "tracks": {"V1": {"clips": [
            {"timeline_in": a, "timeline_out": b, "label": f"c{i}"}
            for i, (a, b) in enumerate(clips)
        ]}},
    }


def _overlap_errors(clips, fps=30.0):
    return [e for e in _validate_structure(_manifest_with(clips, fps))
            if "overlaps the previous clip" in e]


def test_a_sub_frame_overlap_is_not_an_overlap():
    assert _overlap_errors([(0.0, 8.382000000000001), (8.38, 18.37)]) == []


def test_a_real_overlap_still_fails():
    assert len(_overlap_errors([(0.0, 10.0), (9.0, 18.0)])) == 1


def test_a_multi_frame_overlap_still_fails():
    """Three frames at 30fps - small, but visible and wrong."""
    assert len(_overlap_errors([(0.0, 10.1), (10.0, 18.0)])) == 1
