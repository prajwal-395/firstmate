"""A compiled transition miss stays visible in the build report."""

from library.steps.step_6_01_render import step as render_step
from library.steps.step_5_04_compile_manifest import step as compile_step


def test_build_report_preserves_requested_frames_after_hard_cut_fallback():
    """TR3.1's 12-frame request must survive a fallback at frame 428."""
    transition = {
        "transition_id": "trans_001",
        "requested_type": "cross_dissolve",
        "transition_type": "cross_dissolve",
        "cut_point_frame": 428,
        "cut_point_timeline": 14.267,
        "duration_source": "stated_frames",
        "duration_frames": 12,
    }
    compile_step._downgrade_misplaced_transition(
        transition, "trans_001", "no V1 clip ends at frame 428")

    (item,) = render_step._transition_items_for_report(
        {"transitions": [transition]})

    assert item == {
        "transition_id": "trans_001",
        "requested_type": "cross_dissolve",
        "compiled_type": "hard_cut",
        "cut_point_frame": 428,
        "cut_point_timeline": 14.267,
        "duration_source": "stated_frames",
        "requested_duration_frames": 12,
        "requested_duration_seconds": None,
        "requested_duration_feel": None,
        "compiled_duration_frames": 0,
        "status": "downgraded",
        "reason": "no V1 clip ends at frame 428",
    }


def test_build_report_keeps_seconds_and_feel_in_their_own_units():
    """Seconds and feel words must not be rewritten as requested frames."""
    (seconds, feel) = render_step._transition_items_for_report({
        "transitions": [
            {
                "transition_id": "trans_seconds",
                "transition_type": "fade_to_black",
                "requested_type": "fade_to_black",
                "duration_source": "stated_seconds",
                "duration_seconds": 1.0,
                "duration_frames": 30,
            },
            {
                "transition_id": "trans_feel",
                "transition_type": "cross_dissolve",
                "requested_type": "cross_dissolve",
                "duration_source": "feel",
                "duration_feel": "quick",
                "duration_frames": 6,
            },
        ],
    })

    assert seconds["requested_duration_seconds"] == 1.0
    assert seconds["requested_duration_frames"] is None
    assert seconds["compiled_duration_frames"] == 30
    assert feel["requested_duration_feel"] == "quick"
    assert feel["requested_duration_frames"] is None
    assert feel["compiled_duration_frames"] == 6


def test_render_output_payload_does_not_drop_transition_items():
    """The step's declared state output carries its build report through."""
    transition_items = [{
        "transition_id": "trans_001",
        "requested_type": "cross_dissolve",
        "compiled_type": "hard_cut",
        "cut_point_frame": 428,
        "cut_point_timeline": 14.267,
        "duration_source": "frames",
        "requested_duration_frames": 12,
        "requested_duration_seconds": None,
        "requested_duration_feel": None,
        "compiled_duration_frames": 0,
        "status": "downgraded",
        "reason": "no V1 clip ends at frame 428",
    }]
    payload = render_step._render_output_payload(
        {"transition_items": transition_items}, {})

    assert payload["transition_items"] == transition_items
