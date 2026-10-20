"""Weak shot sequences and the arrangements that would break them.

EDIT-021 said "detect weak shot sequences and propose alternative
arrangements", and the repository could tell that a SHOT was
technically unsuitable - `library/tools/shot_reject.py`, a verdict
about soft or unstable picture - but nothing said that a SEQUENCE of
shots was weak.  Three cuts in a row to the same camera for no measured
reason, or four shots in a row showing the same thing, were invisible.

Each test names one defect this module closes:

- a run of shots with no measured variety is NOT weak, so an ordinary
  cut is reported as a defect;
- a run whose cuts are all MEASURED MOTIVATED is weak anyway, so the
  finding ignores the evidence;
- the weakness is a LENGTH threshold nothing declared, so a cut is
  weak for being short;
- an alternative REPLACES the spoken audio, so the words move;
- an alternative that reorders two SPOKEN passages is proposed, so the
  chronology breaks;
- a weak sequence produces no alternative at all, so the finding is a
  complaint with nothing to do about it.

None of them asserts that the module contains a particular entry or a
particular count: shots are built and measured, so a test passes only
when the module actually does what the defect requires.
"""
from __future__ import annotations

from library.tools import shot_sequence_quality
from library.tools.editorial_policy import parse_policy


def _shot(position, camera="cam_a", scene="kitchen", **overrides):
    row = {
        "position": position,
        "clip_id": f"clip_{position}",
        "camera_id": camera,
        "scene": scene,
        "source_start": float(position) * 10.0,
        "source_end": float(position) * 10.0 + 4.0,
        "timeline_start": float(position) * 4.0,
        "timeline_end": float(position) * 4.0 + 4.0,
        "framing": "medium",
        "motivated_by": [],
        "word_timestamps": [],
    }
    row.update(overrides)
    return row


def _policy(**slots):
    return parse_policy({"objective": "interview", "slots": slots},
                        project_id="demo")


def _weak(shots, policy=None):
    found = shot_sequence_quality.weak_sequences(shots, policy=policy)
    return found["weak"]


# ── What makes a run weak ─────────────────────────────────────────

def test_a_run_with_variety_and_motivated_cuts_is_not_weak():
    """An ordinary cut is not a defect."""
    shots = [
        _shot(1, motivated_by=["scene_change"]),
        _shot(2, camera="cam_b", motivated_by=["audio_event"]),
    ]
    assert _weak(shots) == []


def test_a_run_of_one_camera_with_nothing_motivating_it_is_weak():
    """Cuts that are there because cuts are there are the defect."""
    shots = [_shot(1), _shot(2), _shot(3)]
    weak = _weak(shots)
    assert weak
    reasons = weak[0]["reasons"]
    assert shot_sequence_quality.NO_MOTIVATED_CUT in reasons
    assert shot_sequence_quality.NO_VARIETY in reasons


def test_a_run_whose_every_cut_is_motivated_is_not_weak():
    """The finding reads the evidence it was given."""
    shots = [
        _shot(1, motivated_by=["scene_change"]),
        _shot(2, motivated_by=["motion_peak"]),
        _shot(3, motivated_by=["audio_event"]),
    ]
    assert _weak(shots) == []


def test_a_project_may_declare_the_longest_run_it_will_tolerate():
    """A declared limit makes a long run weak on its own."""
    shots = [_shot(1, motivated_by=["scene_change"], framing="wide"),
             _shot(2, motivated_by=["motion_peak"], framing="close"),
             _shot(3, motivated_by=["audio_event"], framing="medium"),
             _shot(4, motivated_by=["scene_change"], framing="wide")]
    assert _weak(shots) == []
    weak = _weak(shots, policy=_policy(shot={"max_same_angle_run": 2}))
    assert weak
    assert shot_sequence_quality.OVER_MAX_RUN in weak[0]["reasons"]


def test_an_undeclared_limit_reads_as_undeclared():
    """No limit is not a limit of zero."""
    found = shot_sequence_quality.weak_sequences([_shot(1), _shot(2)])
    assert found["max_same_angle_run"] is None
    assert found["max_same_angle_run_source"] == "undeclared"


def test_a_single_shot_is_not_a_weak_sequence():
    """One shot is not a sequence."""
    assert _weak([_shot(1)]) == []


# ── The alternatives ──────────────────────────────────────────────

def _weak_run():
    shots = [_shot(1), _shot(2), _shot(3)]
    return shots, _weak(shots)[0]


def test_another_camera_over_the_same_range_is_offered_as_a_replacement():
    """The words do not move when the picture does."""
    shots, weak = _weak_run()
    available = [_shot(9, camera="cam_b", source_start=10.0, source_end=14.0)]
    alternatives = shot_sequence_quality.propose_alternatives(
        weak, available, shots=shots)
    replacements = [row for row in alternatives
                    if row["kind"] == "replace_the_picture"]
    assert replacements
    assert replacements[0]["with"]["camera_id"] == "cam_b"


def test_a_replacement_that_moves_the_audio_is_not_offered():
    """A replacement covers the SAME stretch, or it is not a replacement."""
    shots, weak = _weak_run()
    available = [_shot(9, camera="cam_b", source_start=90.0, source_end=94.0)]
    alternatives = shot_sequence_quality.propose_alternatives(
        weak, available, shots=shots)
    assert [row for row in alternatives
            if row["kind"] == "replace_the_picture"] == []


def test_two_spoken_passages_are_not_proposed_for_a_swap():
    """Swapping two spoken passages would break the chronology."""
    shots = [_shot(1, word_timestamps=[{"word": "a", "source_start": 10.0,
                                        "source_end": 10.4}]),
             _shot(2, word_timestamps=[{"word": "b", "source_start": 20.0,
                                        "source_end": 20.4}])]
    weak = _weak(shots)
    assert weak
    alternatives = shot_sequence_quality.propose_alternatives(
        weak[0], [], shots=shots)
    assert [row for row in alternatives
            if row["kind"] == "reorder_two_shots"] == []


def test_two_silent_shots_are_proposed_for_a_swap():
    """Two shots that carry no words can move without moving what was said."""
    shots = [_shot(1), _shot(2), _shot(3)]
    weak = _weak(shots)[0]
    alternatives = shot_sequence_quality.propose_alternatives(
        weak, [], shots=shots)
    swaps = [row for row in alternatives if row["kind"] == "reorder_two_shots"]
    assert swaps


def test_a_weak_run_with_no_other_camera_still_names_the_break_it_needs():
    """The finding is a finding even when nothing can be offered."""
    shots = [_shot(1), _shot(2)]
    weak = _weak(shots)[0]
    alternatives = shot_sequence_quality.propose_alternatives(
        weak, [], shots=shots)
    assert all(row["requires"] for row in alternatives)
