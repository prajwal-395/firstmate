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
from pathlib import Path

import pytest

from library.tools import assignment_lifecycle as lifecycle
from library.tools import assignment_view as view
from library.tools import revise


def _assignment(project, *, request="two reels about pricing", reels=None,
                intent_id=""):
    """One assignment, with the reels it owns."""
    lifecycle.create(str(project), "reels-1", kind="reels", request=request,
                     reels=reels or {}, intent_id=intent_id)
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
    assert state[view.LEDGER] == {"assignment_id": "", "evidence": [],
                                  "decisions": [], "unlinked_decisions": [],
                                  "unused_evidence": []}


def test_the_tasks_own_decisions_are_reported_and_not_lost(tmp_path):
    """The defect: a task's evidence and decisions were durable ON the
    assignment and INVISIBLE to every reader, so a fresh session could
    not recover the choices an earlier session made - the answer to
    "what did you decide, and why" was only in the conversation."""
    from library.tools import assignment_provenance

    _assignment(tmp_path)
    assignment_provenance.record_evidence(
        str(tmp_path), "reels-1", evidence_id="pause-span", kind="transcript",
        source="speech analysis", locator="clip-1@12-80", detail={"lane": "m1"})
    assignment_provenance.record_decision(
        str(tmp_path), "reels-1", decision_id="remove-pause",
        question="Which pause should be shortened?", answer="clip-1@12-80",
        evidence_ids=["pause-span"])

    state = view.task_state(str(tmp_path))
    ledger = state[view.LEDGER]
    assert [row["id"] for row in ledger["decisions"]] == ["remove-pause"]
    assert ledger["decisions"][0]["question"] == (
        "Which pause should be shortened?")
    assert ledger["decisions"][0]["answer"] == "clip-1@12-80"
    assert [row["id"] for row in ledger["evidence"]] == ["pause-span"]
    assert ledger["evidence"][0]["locator"] == "clip-1@12-80"
    # The plain-text reader names it too: a fresh session reads prose.
    assert "remove-pause" in view.render(state)


def test_an_approved_decision_is_recoverable_after_the_session_ends(tmp_path):
    """The defect: a correction records the decisions it compiled and
    who approved them, and NO reader reported them - so a fresh session
    could not answer "what did you decide, and who said yes"."""
    _assignment(tmp_path)
    _revision(tmp_path, "revise_a", status="patched",
              assignment_id="reels-1",
              change={"summary": "the pause was shortened"},
              editorial_decisions={
                  "intent_id": "revise:revise_a",
                  "decisions": [{
                      "decision_id": "revise:revise_a:decision:0",
                      "statement": "shorten the marked pause",
                      "state": "approved",
                      "stated_by": "captain",
                      "evidence_ids": ["revision-note:note-1"],
                  }]})

    state = view.task_state(str(tmp_path))
    decisions = state[view.LEDGER]["decisions"]
    [decision] = decisions
    assert decision["id"] == "revise:revise_a:decision:0"
    assert decision["question"] == "shorten the marked pause"
    assert decision["decided_by"] == "captain"
    assert decision["source"] == "revision:revise_a"
    assert "shorten the marked pause" in view.render(state)


def test_the_continuity_row_counts_what_was_proposed_applied_and_verified(
        tmp_path):
    """The four words the punchlist uses: proposed, applied, verified,
    delivered. The row counts them off the SAME records the six answers
    read, so a reader cannot read a plan as an applied edit."""
    from library.tools import assignment_artifacts

    _assignment(tmp_path, reels={"moment-1": {"number": 1,
                                               "name": "Reel 01 - pricing",
                                               "status": "proposed"}})
    _revision(tmp_path, "revise_a", status="patched", assignment_id="reels-1",
              change={"summary": "the pause was shortened"},
              verification={"state": "axi_verified",
                            "receipt": {"status": "committed",
                                        "generation": 7,
                                        "lifecycle": {"stage": "COMMITTED"},
                                        "execution_contract": {
                                            "verification": {
                                                "status": "verified"}}}})
    assignment_artifacts.publish_delivery_receipt(
        str(tmp_path), assignment_id="reels-1",
        approved_version={"status": "approved", "assignment_id": "reels-1"},
        files=[{"name": "reel-1.mp4", "path": "/tmp/reel-1.mp4",
                "platform": "vertical", "exists": True, "status": "ok",
                "qc": {"passed": True}}],
        qc={"passed": True}, resume_point={"action": "deliver"})

    row = view.continuity_row(str(tmp_path),
                              lifecycle.load(str(tmp_path), "reels-1"))

    assert row["proposed"] == 1
    assert row["applied"] == 1
    # Two proofs on record: the correction AXI verified, and the
    # delivered file's own checks. Both are evidence, so both count.
    assert row["verified"] == 2
    assert row["delivered"] == 1
    assert row["resume_point"]["command"]
    assert [entry["event"] for entry in row["history"]] == ["propose"]


def test_a_task_with_nothing_done_says_zero_rather_than_nothing(tmp_path):
    """A count of zero is a fact; a missing count is silence, and a
    reader cannot tell them apart."""
    _assignment(tmp_path)

    row = view.continuity_row(str(tmp_path),
                              lifecycle.load(str(tmp_path), "reels-1"))

    assert row["proposed"] == 0
    assert row["applied"] == 0
    assert row["verified"] == 0
    assert row["delivered"] == 0
    assert row["decisions"] == []


def test_the_named_assignment_must_exist(tmp_path):
    _assignment(tmp_path)
    with pytest.raises(FileNotFoundError):
        view.task_state(str(tmp_path), "reels-nope")


def _project_model(project):
    """A project with a canonical Project Model, so an intent can land."""
    from library.tools import project_model_store

    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "native" / "ren-protocol" /
         "fixtures" / "project-model.json").read_text(encoding="utf-8"))
    project_model_store.commit_project_model(project, fixture,
                                             expected_revision=0)


def test_the_canonical_intent_is_what_ren_understood(tmp_path):
    """The intent is the canonical record of what was ASKED FOR (EDIT-001),
    and it lives in the Project Model. The task state reads it, so the
    agent is told what Ren took the request to mean instead of reading a
    request string."""
    from library.tools import editorial_intent

    _assignment(tmp_path, intent_id="intent:1")
    _project_model(tmp_path)
    intent = editorial_intent.build_intent(
        intent_id="intent:1",
        desired_outcome="the viewer learns the price",
        requests=[{"id": "request:1", "kind": "remove_repetition",
                   "statement": "remove the repeated answer"}])
    editorial_intent.commit_intent(str(tmp_path), intent)

    state = view.task_state(str(tmp_path))
    understood = state["understood"]["intent"]
    assert understood["intent_id"] == "intent:1"
    assert understood["desired_outcome"] == "the viewer learns the price"
    assert [row["statement"] for row in understood["requests"]] == [
        "remove the repeated answer"]


def test_a_request_whose_evidence_is_missing_is_shown_as_missing(tmp_path):
    """EDIT-008: silence is not evidence. A request whose evidence bar is
    unmet is a request Ren cannot act on yet, and the task state has to
    say so rather than read as "understood and ready"."""
    from library.tools import editorial_intent

    _assignment(tmp_path, intent_id="intent:1")
    _project_model(tmp_path)
    intent = editorial_intent.build_intent(
        intent_id="intent:1",
        desired_outcome="the viewer learns the price",
        requests=[{"id": "request:1", "kind": "remove_repetition",
                   "statement": "remove the repeated answer"}])
    editorial_intent.commit_intent(str(tmp_path), intent)

    state = view.task_state(str(tmp_path))
    [row] = state["uncertainties"]["intent_uncertainty"]
    assert row["request_id"] == "request:1"
    assert [entry["lane"] for entry in row["missing_evidence"]] == ["m1"]
    assert row["missing_evidence"][0]["status"] == "not_yet_analysed"


def test_an_intent_that_is_not_on_record_is_said_not_guessed(tmp_path):
    """The assignment names an intent the Project Model does not carry.
    The section says so and names what IS on record, rather than
    substituting an intent the person never asked for."""
    from library.tools import editorial_intent

    _assignment(tmp_path, intent_id="intent:missing")
    _project_model(tmp_path)
    editorial_intent.commit_intent(
        str(tmp_path),
        editorial_intent.build_intent(intent_id="intent:1",
                                      desired_outcome="outcome 1"))

    intent = view.task_state(str(tmp_path))["understood"]["intent"]
    assert intent["intent_id"] == ""
    assert "intent:missing" in intent["note"]
    assert "intent:1" in intent["on_record"]


def test_several_intents_and_no_name_are_not_resolved_by_guesswork(tmp_path):
    """A project with two intents and an assignment that names neither
    does not get one picked for it."""
    from library.tools import editorial_intent

    _assignment(tmp_path)
    _project_model(tmp_path)
    for index in (1, 2):
        editorial_intent.commit_intent(
            str(tmp_path),
            editorial_intent.build_intent(
                intent_id=f"intent:extra-{index}",
                desired_outcome=f"outcome {index}"))

    intent = view.task_state(str(tmp_path))["understood"]["intent"]
    assert intent["intent_id"] == ""
    assert "no intent is on record" in intent["note"]
    assert "intent:extra-1" in intent["on_record"]
