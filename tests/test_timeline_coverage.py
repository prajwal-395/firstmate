"""Every frame of the timeline must show a clip, or the render is black.

The shipped export carried 7.2s of black, 6.4s of it starting at 4.3s.
The rough-cut review records only negative gaps by design, the B-roll
duration invariant was satisfied by the very shortening that made the
hole, and only the final ffmpeg probe noticed - one step from the end,
with nothing left to do but fail the run.  This check is the same
observation, moved to `compile_manifest` where it can still be acted on.
"""

import pytest

from library.steps.step_5_04_compile_manifest.step import (
    _assert_timeline_fully_covered,
    _video_coverage_gaps,
)


def manifest_with(v1=(), v2=(), duration=10.0, fps=30.0, spine=None):
    def clips(spans, prefix):
        return [
            {"timeline_in": start, "timeline_out": end,
             "timeline_in_frame": int(round(start * fps)),
             "timeline_out_frame": int(round(end * fps)),
             "label": f"{prefix}_{i}"}
            for i, (start, end) in enumerate(spans)
        ]

    manifest = {
        "project": {"frame_rate": fps, "duration_seconds": duration},
        "tracks": {
            "V1": {"label": "A-Roll", "clips": clips(v1, "aroll")},
            "V2": {"label": "B-Roll", "clips": clips(v2, "broll")},
        },
    }
    if spine is not None:
        manifest["_spine_blocks"] = spine
    return manifest


def block(start, end, block_type="transition_slot", **extra):
    entry = {
        "position": 1,
        "timeline_start": start,
        "timeline_end": end,
        "block_type": block_type,
    }
    entry.update(extra)
    return entry


def test_fully_covered_timeline_passes():
    manifest = manifest_with(v1=[(0.0, 4.5), (4.5, 10.0)])
    assert _video_coverage_gaps(manifest) == []
    _assert_timeline_fully_covered(manifest)


def test_gap_between_two_clips_fails_and_names_the_range():
    manifest = manifest_with(v1=[(0.0, 4.308), (10.741, 20.0)], duration=20.0)
    with pytest.raises(ValueError) as excinfo:
        _assert_timeline_fully_covered(manifest)
    message = str(excinfo.value)
    assert "4.308s to 10.741s" in message
    assert "6.433s" in message


def test_gap_at_the_head_of_the_timeline_fails():
    manifest = manifest_with(v1=[(2.0, 10.0)])
    with pytest.raises(ValueError, match="0.000s to 2.000s"):
        _assert_timeline_fully_covered(manifest)


def test_timeline_running_past_its_last_clip_fails():
    """A 10s project whose picture stops at 8s ends on 2s of black."""
    manifest = manifest_with(v1=[(0.0, 8.0)], duration=10.0)
    with pytest.raises(ValueError, match="8.000s to 10.000s"):
        _assert_timeline_fully_covered(manifest)


def test_broll_on_v2_covers_a_hole_in_v1():
    """A non-speech block covered only by a cutaway is legitimate."""
    manifest = manifest_with(v1=[(0.0, 4.0), (7.0, 10.0)], v2=[(4.0, 7.0)])
    assert _video_coverage_gaps(manifest) == []
    _assert_timeline_fully_covered(manifest)


def test_broll_too_short_for_its_block_is_caught():
    """The exact shape of the shipped defect: a 3.567s clip on a 10s block.

    `select_broll`'s post-bridge shortens the cutaway's timeline_end when
    its source cannot fill the block - which is correct over speech and
    leaves black on a block with no A-roll underneath.
    """
    manifest = manifest_with(
        v1=[(0.0, 0.741), (10.741, 20.0)],
        v2=[(0.741, 4.308)],
        duration=20.0,
    )
    with pytest.raises(ValueError, match=r"4\.308s to 10\.741s"):
        _assert_timeline_fully_covered(manifest)


def test_multiple_gaps_are_all_reported():
    manifest = manifest_with(
        v1=[(0.0, 4.308), (10.741, 21.937), (22.678, 30.0)], duration=30.0)
    with pytest.raises(ValueError) as excinfo:
        _assert_timeline_fully_covered(manifest)
    message = str(excinfo.value)
    assert "2 uncovered range(s)" in message
    assert "4.308s to 10.741s" in message
    assert "21.937s to 22.678s" in message


def test_sub_frame_seam_between_abutting_clips_is_not_a_gap():
    """Float noise where one clip ends and the next begins is not black."""
    manifest = manifest_with(v1=[(0.0, 4.9999), (5.0, 10.0)])
    assert _video_coverage_gaps(manifest) == []


def test_overlapping_clips_do_not_manufacture_a_gap():
    manifest = manifest_with(v1=[(0.0, 6.0)], v2=[(2.0, 4.0), (5.0, 10.0)])
    assert _video_coverage_gaps(manifest) == []


def test_zero_length_project_is_not_checked():
    assert _video_coverage_gaps(manifest_with(duration=0.0)) == []


def _beat_manifest(gap_end, spine, gap_start=4.0):
    """A 10s timeline whose only hole runs `gap_start` to `gap_end`."""
    return manifest_with(
        v1=[(0.0, gap_start), (gap_end, 10.0)], spine=spine)


DECLARED = {"intentional_black_beat": True,
            "black_beat_reason": "hold on black before the turn"}


def test_declared_black_beat_within_the_bound_passes():
    """A hole the plan chose, short and outside speech, is an edit."""
    manifest = _beat_manifest(4.4, [block(3.0, 5.0, **DECLARED)])
    assert _video_coverage_gaps(manifest) == [(4.0, 4.4)]
    _assert_timeline_fully_covered(manifest)


def test_declared_black_beat_longer_than_the_bound_fails():
    """A beat is a beat; 0.8s of black is a hole with a note attached."""
    manifest = _beat_manifest(4.8, [block(3.0, 5.0, **DECLARED)])
    with pytest.raises(ValueError, match="longer than the 0.5s"):
        _assert_timeline_fully_covered(manifest)


def test_declared_black_beat_without_a_reason_fails():
    """A bare flag is a rubber stamp - the reason is what makes it a choice."""
    manifest = _beat_manifest(4.4, [
        block(3.0, 5.0, intentional_black_beat=True)])
    with pytest.raises(ValueError, match="no black_beat_reason"):
        _assert_timeline_fully_covered(manifest)


def test_declared_black_beat_with_an_empty_reason_fails():
    manifest = _beat_manifest(4.4, [
        block(3.0, 5.0, intentional_black_beat=True, black_beat_reason="  ")])
    with pytest.raises(ValueError, match="no black_beat_reason"):
        _assert_timeline_fully_covered(manifest)


def test_undeclared_gap_the_size_of_a_legal_beat_still_fails():
    """Intent is recorded in the plan, never inferred from a short hole."""
    manifest = _beat_manifest(4.4, [block(3.0, 5.0)])
    with pytest.raises(ValueError, match="no spine block declares"):
        _assert_timeline_fully_covered(manifest)


def test_black_beat_declared_on_a_speech_block_fails():
    manifest = _beat_manifest(4.4, [
        block(3.0, 5.0, block_type="speech", **DECLARED)])
    with pytest.raises(ValueError, match="speech is never held on black"):
        _assert_timeline_fully_covered(manifest)


def test_black_beat_declared_on_a_hook_block_fails():
    manifest = _beat_manifest(4.4, [
        block(3.0, 5.0, block_type="hook", **DECLARED)])
    with pytest.raises(ValueError, match="speech is never held on black"):
        _assert_timeline_fully_covered(manifest)


def test_gap_straddling_a_declared_blocks_edge_fails():
    """The declaration covers the block, not whatever runs past its end."""
    manifest = _beat_manifest(4.4, [block(3.0, 4.2, **DECLARED)])
    with pytest.raises(ValueError, match="no spine block declares"):
        _assert_timeline_fully_covered(manifest)


def test_the_shipped_gap_is_not_rescued_by_a_declaration():
    """6.433s stays a failure however it is labelled."""
    manifest = manifest_with(
        v1=[(0.0, 4.308), (10.741, 20.0)], duration=20.0,
        spine=[block(0.0, 20.0, **DECLARED)])
    with pytest.raises(ValueError, match="longer than the 0.5s"):
        _assert_timeline_fully_covered(manifest)


def test_clips_without_frame_fields_fall_back_to_seconds():
    manifest = {
        "project": {"frame_rate": 30.0, "duration_seconds": 10.0},
        "tracks": {"V1": {"clips": [
            {"timeline_in": 0.0, "timeline_out": 6.0, "label": "a"},
        ]}},
    }
    gaps = _video_coverage_gaps(manifest)
    assert len(gaps) == 1
    assert gaps[0] == pytest.approx((6.0, 10.0))
