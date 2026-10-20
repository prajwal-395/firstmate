"""What Ren understood about a request, and what it did with it.

The defects this names:

- a PROPOSED correction read as an applied edit (a plan is not a change);
- a correction that ran without request-specific verification read as a
  proven outcome;
- a correction that has not landed yet INVISIBLE on the task, so the host
  could not see the note it was waiting on;
- the first correction of a request belonging to NOBODY, because the note
  arrived before the assignment did;
- a step that stayed silent read as "nothing was missing";
- the host deriving all six answers from the checkpoint dictionaries,
  which is the machinery the captain's principle says the model must not
  be responsible for.
"""

from __future__ import annotations

import json

import pytest

from library.tools import assignment_lifecycle as lifecycle
from library.tools import assignment_view as view
from library.tools import revise


def _assignment(project, *, request="two reels about pricing", reels=None):
    """One assignment, with the reels it owns."""
    lifecycle.create(str(project), "reels-1", kind="reels", request=request,
                     reels=reels or {})
    lifecycle.transition(str(project), "reels-1", "draft", event="propose")
    return "reels-1"


def _revision(project, revise_id, **fields):
    """One revision record, written the way `ren revise` writes it."""
    record = {"format": revise.FORMAT, "revise_id": revise_id,
              "project": str(project), "note": "the captions are too small",
              "note_id": "note-1", "source": "chat", "reel": "",
              "assignment_id": None, "spec_id": f"edit_spec_{revise_id}",
              "status": "awaiting_translation",
              "created_at": "2026-10-20T10:00:00+00:00",
              "updated_at": "2026-10-20T10:00:00+00:00"}
    record.update(fields)
    path = revise.record_path(str(project), revise_id, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


def test_a_proposed_correction_is_never_reported_as_applied(tmp_path):
    """A plan is not a change: the proposal sits in `planned`, not in
    `landed`, and the counts say so."""
    _assignment(tmp_path)
    _revision(tmp_path, "revise_a", status="proposed",
              assignment_id="reels-1")

    state = view.task_state(str(tmp_path))
    applied = state["applied"]
    assert applied["counts"]["planned"] == 1
    assert applied["counts"]["landed"] == 0
    assert [row["id"] for row in applied["planned"]] == ["revise_a"]
    assert applied["landed"] == []


def test_a_correction_that_ran_without_verification_is_unverified(tmp_path):
    """The scoped rebuild ran; the requested outcome was not proven. The
    view says so instead of counting the change as done."""
    _assignment(tmp_path)
    _revision(tmp_path, "revise_a", status="unverified",
              assignment_id="reels-1",
              change={"summary": "the reel was rebuilt"},
              verification={"state": "not_proven"})

    state = view.task_state(str(tmp_path))
    assert state["verified"]["counts"]["verified"] == 0
    [row] = state["verified"]["unverified"]
    assert row["id"] == "revise_a"
    assert "verification" in row["why"]
    assert state["applied"]["counts"]["not_applied"] == 1
    assert state["applied"]["counts"]["landed"] == 0


def test_a_verified_correction_carries_the_receipt_that_proved_it(tmp_path):
    _assignment(tmp_path)
    _revision(tmp_path, "revise_a", status="patched",
              assignment_id="reels-1",
              change={"summary": "caption size raised"},
              verification={"state": "axi_verified",
                            "receipt": {"status": "committed",
                                        "generation": 7,
                                        "lifecycle": {"stage": "COMMITTED"},
                                        "execution_contract": {
                                            "verification": {
                                                "status": "verified"}}}})

    state = view.task_state(str(tmp_path))
    [row] = state["verified"]["verified"]
    assert row["evidence"]["receipt_status"] == "committed"
    assert row["evidence"]["verification_status"] == "verified"
    assert state["applied"]["counts"]["landed"] == 1


def test_an_open_correction_is_visible_on_the_task_before_it_lands(tmp_path):
    """The defect: a note the host is waiting on was invisible on the
    assignment until it landed, so the host could not show the person
    what it was already working on."""
    _assignment(tmp_path)
    _revision(tmp_path, "revise_a", status="awaiting_translation",
              assignment_id="reels-1")

    state = view.task_state(str(tmp_path))
    assert state["corrections"]["open"] == ["revise_a"]
    [item] = state["corrections"]["items"]
    assert item["note"] == "the captions are too small"
    assert item["resume"].endswith("--id revise_a")
    # The assignment's own history has not moved: the correction is
    # waiting, not applied, and the view must not dress it up as either.
    record = lifecycle.load(str(tmp_path), "reels-1")
    assert [entry["event"] for entry in record["history"]] == ["propose"]


def test_a_correction_about_an_owned_reel_belongs_to_that_task(tmp_path):
    """The note arrived before the assignment did, so it names no
    assignment. It is still about this task's reel."""
    _assignment(tmp_path, reels={"moment-1": {"number": 1,
                                               "name": "Reel 01 - pricing",
                                               "status": "built"}})
    _revision(tmp_path, "revise_a", status="patched", reel="Reel 01 - pricing",
              assignment_id=None)

    state = view.task_state(str(tmp_path))
    assert [item["revise_id"] for item in state["corrections"]["items"]] == [
        "revise_a"]


def test_a_step_that_stayed_silent_is_not_read_as_nothing_missing(tmp_path):
    """Three readings, kept apart: a step that declared a gap, and a step
    that never answered, are different facts."""
    _assignment(tmp_path)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "undetermined_declarations": [
            {"step_id": "music_selection", "reading": "declared",
             "attempt": 1,
             "entries": [{"what": "the bed's loudness was never measured",
                          "why_it_mattered": "the mix had no target",
                          "what_would_have_helped": "a loudness reading"}]},
            {"step_id": "plan_vfx", "reading": "not_declared", "attempt": 1,
             "entries": []}]}), encoding="utf-8")

    gaps = view.task_state(str(tmp_path))["uncertainties"]["declared_gaps"]
    readings = {row["step_id"]: row["reading"] for row in gaps}
    assert readings == {"music_selection": "declared",
                        "plan_vfx": "not_declared"}
    assert gaps[0]["entries"][0]["what"]


def test_the_assignment_envelope_and_the_standalone_read_agree(tmp_path):
    """One read, not two: the host that reads the assignment envelope
    must get the same six answers the standalone view gives."""
    from library.tools import assignment_interaction

    _assignment(tmp_path)
    _revision(tmp_path, "revise_a", status="question", assignment_id="reels-1",
              question={"question": "which reel?"})

    envelope = assignment_interaction.state(str(tmp_path))
    carried = envelope["result"]["task_state"]
    assert carried == view.task_state(str(tmp_path))
    assert carried["uncertainties"]["open_questions"] == [
        {"revision_id": "revise_a", "question": "which reel?"}]
    assert carried["corrections"]["items"][0]["answer_with"].endswith(
        '--answer "..."')


def test_a_project_with_no_assignment_keeps_the_readers_shape(tmp_path):
    """A reader that branches on the shape of the answer has to branch on
    it for an empty project too."""
    state = view.task_state(str(tmp_path))
    assert state["assignment_id"] is None
    for section in view.SECTIONS:
        assert section in state
    assert state["applied"]["counts"] == {"landed": 0, "in_flight": 0,
                                          "planned": 0, "not_applied": 0}
    assert state["verified"]["counts"] == {"verified": 0, "unverified": 0}


def test_the_named_assignment_must_exist(tmp_path):
    _assignment(tmp_path)
    with pytest.raises(FileNotFoundError):
        view.task_state(str(tmp_path), "reels-nope")
