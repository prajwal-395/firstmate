"""Choosing a camera angle, proved against named defects.

EDIT-020 said "choose camera angles based on speaker identity, action
visibility, eyeline, and continuity", and the repository could place a
DECLARED angle and refuse an unplanned one - but nothing read the
speaker, the action, the eyeline or the continuity of the candidates side
by side, so there was nowhere for the judgement to be made from.

Each test names one defect this module closes:

- a candidate naming no speaker COUNTS as one that shows the speaker, so
  an unmeasured candidate beats a measured one;
- an eyeline reading is a VERDICT rather than a possible line cross, so
  a shot/reverse-shot cut is refused as an error;
- the candidates are ordered by a SCORE, so "better by 0.4" is the
  answer and the criteria are decoration;
- a candidate whose range does not cover the passage is offered as a
  choice, so the words would have nowhere to land;
- a criterion whose evidence was never measured reads as one the
  candidate FAILED.

None of them asserts that the module contains a particular entry or a
particular count: candidates are built and measured, so a test passes
only when the module actually does what the defect requires.
"""
from __future__ import annotations

import pytest

from library.tools import angle_evidence
from library.tools.angle_evidence import (
    OPPOSES,
    SUPPORTS,
    UNDETERMINED,
    AngleEvidenceError,
)
from library.tools.editorial_policy import parse_policy


def _face_track(centre, *, samples=200, rate=10.0):
    """A measured face track, in the shape subject_center_reading reads."""
    return {"face_presence": {"face_center_x": [centre] * samples,
                              "sample_rate_hz": rate}}


def _candidate(angle_id="cam_b", **overrides):
    row = {
        "angle_id": angle_id,
        "clip_id": "clip_002",
        "camera_id": "cam_b",
        "scene": "kitchen",
        "source_start": 10.0,
        "source_end": 14.0,
        "speakers": ["Ana"],
        "actions": ["opening the box"],
    }
    row.update(overrides)
    return row


def _context(**overrides):
    fields = {
        "passage": {"source_start": 10.0, "source_end": 14.0},
        "speaking_speaker": "Ana",
        "action_windows": [
            {"start_seconds": 11.0, "end_seconds": 13.0,
             "description": "opening the box"},
        ],
        "previous_angle": {
            "clip_id": "clip_001",
            "source_start": 4.0,
            "source_end": 8.0,
            "camera_id": "cam_a",
            "scene": "kitchen",
        },
        "temporal_indices": {
            "clip_001": _face_track(0.30),
            "clip_002": _face_track(0.32),
        },
    }
    fields.update(overrides)
    return fields


def _measure(candidate=None, context=None):
    return angle_evidence.measure_candidate(candidate or _candidate(),
                                            context or _context())


def _criterion(row, name):
    for entry in row["criteria"]:
        if entry["criterion"] == name:
            return entry
    raise AssertionError(f"no criterion {name!r} in the row")


# ── The four criteria ─────────────────────────────────────────────

def test_the_angle_showing_the_speaker_supports_speaker_identity():
    """The person talking is the person the camera shows."""
    row = _measure()
    assert _criterion(row, "speaker_identity")["verdict"] == SUPPORTS


def test_an_angle_showing_another_speaker_opposes_speaker_identity():
    """A reaction shot of somebody else is not the speaker's shot."""
    row = _measure(_candidate(speakers=["Bo"]))
    assert _criterion(row, "speaker_identity")["verdict"] == OPPOSES


def test_an_angle_naming_no_speaker_is_undetermined_not_opposed():
    """An unmeasured candidate is not a failing one."""
    row = _measure(_candidate(speakers=[]))
    assert _criterion(row, "speaker_identity")["verdict"] == UNDETERMINED


def test_the_angle_covering_the_action_supports_action_visibility():
    """The camera shows what is being talked about."""
    row = _measure()
    assert _criterion(row, "action_visibility")["verdict"] == SUPPORTS


def test_an_angle_that_covers_no_measured_action_opposes_action_visibility():
    """An angle pointed somewhere else does not show the action."""
    row = _measure(_candidate(source_start=30.0, source_end=34.0))
    assert _criterion(row, "action_visibility")["verdict"] == OPPOSES


def test_a_passage_with_no_measured_action_window_is_undetermined():
    """Nothing measured about the action means nothing concluded."""
    row = _measure(context=_context(action_windows=[]))
    assert _criterion(row, "action_visibility")["verdict"] == UNDETERMINED


def test_the_same_side_of_the_frame_supports_the_eyeline():
    """Both shots on the same side is the eyeline agreeing."""
    row = _measure()
    assert _criterion(row, "eyeline")["verdict"] == SUPPORTS


def test_the_other_side_of_the_frame_opposes_the_eyeline_as_a_possible_cross():
    """A flip is also what shot/reverse-shot looks like."""
    context = _context(temporal_indices={
        "clip_001": _face_track(0.30),
        "clip_002": _face_track(0.70),
    })
    row = _measure(context=context)
    eyeline = _criterion(row, "eyeline")
    assert eyeline["verdict"] == OPPOSES
    assert eyeline["possible"] is True


def test_an_unmeasured_subject_centre_leaves_the_eyeline_undetermined():
    """No measurement is not a disagreement."""
    context = _context(temporal_indices={
        "clip_001": _face_track(0.30),
        "clip_002": {"face_presence": {}},
    })
    row = _measure(context=context)
    assert _criterion(row, "eyeline")["verdict"] == UNDETERMINED


def test_a_different_camera_in_the_same_scene_supports_continuity():
    """A new camera of the same scene continues it."""
    row = _measure()
    assert _criterion(row, "continuity")["verdict"] == SUPPORTS


def test_the_same_camera_as_the_shot_before_opposes_continuity():
    """Cutting to the same camera is not a cut."""
    row = _measure(_candidate(camera_id="cam_a"))
    assert _criterion(row, "continuity")["verdict"] == OPPOSES


def test_another_scene_opposes_continuity():
    """A scene change is not continuity."""
    row = _measure(_candidate(scene="street"))
    assert _criterion(row, "continuity")["verdict"] == OPPOSES


# ── The ordering ──────────────────────────────────────────────────

def test_the_candidates_are_ordered_by_the_criteria_and_not_by_a_score():
    """The answer names the criteria, never a number."""
    strong = _candidate("cam_b", speakers=["Ana"], actions=["opening the box"])
    weak = _candidate("cam_c", speakers=["Bo"], actions=["washing a cup"],
                      camera_id="cam_a", scene="street")
    ranked = angle_evidence.rank_candidates([weak, strong], _context())
    assert [row["angle_id"] for row in ranked["candidates"]] == ["cam_b", "cam_c"]
    assert "score" not in str(ranked["candidates"][0]).lower()


def test_a_candidate_whose_range_does_not_cover_the_passage_is_excluded():
    """The words would have nowhere to land."""
    ranked = angle_evidence.rank_candidates(
        [_candidate(source_start=100.0, source_end=104.0)], _context())
    assert ranked["candidates"] == []
    assert ranked["excluded"]
    assert "range_does_not_cover_the_passage" in ranked["excluded"][0]["excluded"]


def test_the_chosen_angle_is_the_first_one_and_says_why():
    """A recommendation with its reasoning, not a bare verdict."""
    chosen = angle_evidence.select_angle(
        [_candidate("cam_b", speakers=["Ana"])], _context())
    assert chosen["chosen"]["angle_id"] == "cam_b"
    assert "criteria" in chosen["why"]


def test_no_candidate_means_no_choice_rather_than_a_default():
    """Nothing placeable is not an angle."""
    chosen = angle_evidence.select_angle([], _context())
    assert chosen["chosen"] is None
    assert "no candidate" in chosen["why"]


# ── The project's own order ───────────────────────────────────────

def test_the_project_may_weigh_the_criteria_in_its_own_order():
    """The order is the project's, and the criteria are the item's."""
    policy = parse_policy({
        "objective": "interview",
        "slots": {"angle": {"order": ["continuity", "speaker_identity",
                                      "action_visibility", "eyeline"]}},
    }, project_id="demo")
    assert angle_evidence.criterion_order(policy)[0] == "continuity"
    assert angle_evidence.criterion_order() == angle_evidence.CRITERIA


def test_an_order_naming_a_criterion_nothing_measures_is_refused():
    """A criterion the engine cannot measure must not be weighable."""
    policy = parse_policy({
        "objective": "interview",
        "slots": {"angle": {"order": ["vibes"]}},
    }, project_id="demo")
    with pytest.raises(AngleEvidenceError):
        angle_evidence.criterion_order(policy)


def test_an_unknown_criterion_is_refused_rather_than_ignored():
    """A misspelled criterion is a declaration nothing reads."""
    with pytest.raises(AngleEvidenceError):
        angle_evidence.measure_candidate(_candidate(), _context(),
                                         criteria=("vibes",))
