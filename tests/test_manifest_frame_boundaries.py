"""A frame-aligned join must not fail from sub-frame seconds drift."""
import pytest

from library.steps.step_5_04_compile_manifest.step import (
    _apply_manifest_qa_checks,
    _transition_cut_frame,
    _v1_index_ending_at,
)


def _manifest(first_out_frame, second_in_frame):
    return {
        "tracks": {
            "V1": {
                "clips": [
                    {
                        "label": "outgoing",
                        "timeline_out": 5.382,
                        "timeline_out_frame": first_out_frame,
                    },
                    {
                        "label": "incoming",
                        "timeline_in": 5.367,
                        "timeline_in_frame": second_in_frame,
                        "timeline_out": 15.4,
                        "timeline_out_frame": 462,
                    },
                ],
            },
        },
    }


def test_manifest_overlap_check_uses_frame_boundaries_when_seconds_drift():
    """A 0.015s display drift is not an overlap when both clips join at frame 161."""
    _apply_manifest_qa_checks(_manifest(161, 161))


def test_manifest_overlap_check_still_rejects_a_real_one_frame_overlap():
    """Frame comparison must still catch clips whose Resolve spans overlap."""
    with pytest.raises(ValueError, match="overlap: 1 frame\\(s\\)"):
        _apply_manifest_qa_checks(_manifest(162, 161))


def test_exact_anchor_does_not_attach_to_a_nearby_v1_cut():
    """A stated frame is exact even when another cut is within 0.25 seconds."""
    clips = [{
        "timeline_out": 14.267,
        "timeline_out_frame": 462,
    }]
    assert _v1_index_ending_at(
        clips, 14.267, cut_frame=428) is None
    assert _v1_index_ending_at(
        clips, 14.267, cut_frame=462) == 0
    # Legacy, unanchored entries retain their seconds-based tolerance.
    assert _v1_index_ending_at(clips, 14.267) == 0


def test_explicit_malformed_anchor_frame_does_not_fall_back_to_seconds():
    with pytest.raises(ValueError, match="unreadable exact cut frame"):
        _transition_cut_frame({"transition_id": "t1",
                                "cut_point_frame": None})
