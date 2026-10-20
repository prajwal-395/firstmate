"""A refused revision settles its note: it holds nothing (D5).

The break test (2026-10-20, `data/vep-ren-breaktest/findings.md`, D5) ran
twelve ordinary edit requests through `ren revise` on a scratch copy of one
reel. Eight revisions were refused, and every one of them left a translated
spec that was never recorded in the edit ledger - so `ren edit <project>
--rerun render_motion_graphics` refused with

    the edit pipeline is held because a natural-language note has no fully
    resolved, recorded edit spec:
    note ...: resolved edit spec has not been recorded exactly in the ledger

and the whole project stayed blocked until each refusal was resolved by hand
with `ren spec resolve <project> --id <id>`. The refusal never said that it
would block later runs.

A refusal is a DECISION, not a gap. The translation was understood and the
front door declined to carry it out, so the note is settled: it holds
nothing, it reaches no planner, and the edit ledger stays clean - a refused
edit is not a declared edit the build would replay.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import edit_ledger, edit_spec, llm_handshake, revise
from library.tools.project_layout import ProjectLayout

REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
NOTE = "cut the first two seconds off the opening"


def _project(tmp_path) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    ProjectLayout(str(project)).ensure()
    return project


def _program_spec(request: str, source_note_id: str) -> dict:
    """A translated program the shared execution planner refuses.

    `clip.trim` is not a production-qualified executor and is not in the
    protected experimental set `ren revise` is granted, so the router
    refuses it - the break test's first request, refused with the
    planner's own reason.
    """
    return {
        "format": edit_spec.FORMAT,
        "request": request,
        "source_note_id": source_note_id,
        "program": {
            "kind": "edit_program",
            "reel": REEL,
            "base_generation": "1",
            "idempotency_key": "trim-opening",
            "ops": [{
                "op": "clip.trim",
                "text": "cut the first two seconds off the opening",
                "params": {"unique_id": "clip-1", "in_frame": 48,
                           "out_frame": 240},
            }],
        },
    }


def _refused_revision(project: Path) -> dict:
    """Take a note through `ren revise` until the front door refuses it."""
    record = revise.start(str(project), NOTE)
    response = Path(llm_handshake.response_path(str(project),
                                                record["spec_id"]))
    response.write_text(json.dumps(
        _program_spec(NOTE, record["note_id"])), encoding="utf-8")
    return revise.advance(str(project), record["revise_id"])


def test_a_refused_revision_does_not_hold_the_edit_pipeline(tmp_path):
    """The defect: one refusal blocked every later run.

    Catches: a translated spec the front door refused staying unrecorded
    and therefore pending, so `ren edit` refused to run at all with
    "the edit pipeline is held because a natural-language note has no
    fully resolved, recorded edit spec".
    """
    from library.processes.edit_video.run_pipeline import run_pipeline

    project = _project(tmp_path)
    result = _refused_revision(project)
    assert result["status"] == revise.STATUS_REFUSED

    states = edit_spec.pending_note_states(str(project))
    assert states, "the refused note is still tracked"
    for state in states.values():
        assert state.get("state") == "refused", state

    summary = run_pipeline(str(project))
    assert "the edit pipeline is held" not in str(summary.get("reason"))


def test_the_refusal_says_the_note_is_settled(tmp_path):
    """The refusal says what it settled, because a refusal that silently
    blocked the next run is what the break test reported."""
    project = _project(tmp_path)
    result = _refused_revision(project)
    reply = result["reply"]
    assert "refused" in reply
    assert "settled" in reply
    assert "does not hold the edit pipeline" in reply


def test_a_refused_edit_never_reaches_the_edit_ledger(tmp_path, capsys):
    """The ledger replays what it declares, so a refused edit is not in it.

    Catches: the old remedy - `ren spec resolve` on the refused spec -
    recording the refused edit as a declared edit the next build would
    replay.
    """
    project = _project(tmp_path)
    record = _refused_revision(project)
    assert edit_ledger.load_rows(str(project)) == []

    exit_code = edit_spec.main(
        ["resolve", str(project), "--id", record["spec_id"]])
    assert exit_code == 2
    said = capsys.readouterr().err
    assert "was refused and is settled" in said
    assert "--retranslate" in said
    assert edit_ledger.load_rows(str(project)) == []


def test_a_translation_nobody_refused_still_holds_the_pipeline(tmp_path):
    """The guard is not weakened: an unanswered translation still holds.

    Catches: the hold being switched off for every note, so a note whose
    translation never reached the ledger would reach a planner as a
    keyword guess.
    """
    from library.processes.edit_video.run_pipeline import run_pipeline

    project = _project(tmp_path)
    request_id, _path, note_id = edit_spec.prepare_request(
        str(project), NOTE)
    response = Path(llm_handshake.response_path(str(project), request_id))
    response.write_text(json.dumps(
        _program_spec(NOTE, note_id)), encoding="utf-8")

    states = edit_spec.pending_note_states(str(project))
    assert note_id in states
    assert states[note_id].get("state") is None
    assert "not been recorded exactly in the ledger" in states[note_id]["reason"]

    summary = run_pipeline(str(project))
    assert summary["status"] == "REFUSED"
    assert "the edit pipeline is held" in str(summary.get("reason"))


def test_a_refused_note_reaches_no_planning_step(tmp_path):
    """A settled note is routed away from planning, not merely unheld."""
    from library.tools import marker_routing

    project = _project(tmp_path)
    _refused_revision(project)
    routed = marker_routing.route_project(str(project))
    assert routed
    for note in routed:
        assert note.basis == marker_routing.BASIS_EDIT_SPEC_REFUSED
        assert note.steps == []


def test_a_refusal_settles_the_spec_not_the_note(tmp_path):
    """A later translation of the same note is not settled by the refusal.

    Catches: the refusal settling the NOTE, so the editor could never ask
    for the same change again - a refusal is a decision about one
    translation, not a verdict on the request.
    """
    project = _project(tmp_path)
    record = _refused_revision(project)
    request_id, _path, note_id = edit_spec.prepare_request(
        str(project), NOTE, note_id=record["note_id"])
    assert request_id != record["spec_id"]

    states = edit_spec.pending_note_states(str(project))
    assert states[note_id].get("state") is None
    assert "waiting for the host" in states[note_id]["reason"]
    assert edit_ledger.load_rows(str(project)) == []


def test_a_refused_revision_can_be_retranslated(tmp_path):
    """A refusal is a decision the editor can change their mind about.

    Catches: the refusal closing the revision for good, so the only way
    to try the change again was to type the note a second time and leave
    a second note in the project's notes channel.
    """
    project = _project(tmp_path)
    record = _refused_revision(project)
    again = revise.advance(str(project), record["revise_id"],
                           retranslate=True)
    assert again["status"] == revise.STATUS_AWAITING
    assert again["spec_id"] != record["spec_id"]

    states = edit_spec.pending_note_states(str(project))
    assert states[record["note_id"]].get("state") is None
    assert "waiting for the host" in states[record["note_id"]]["reason"]


def test_the_refusal_is_recorded_with_its_reason(tmp_path):
    """The record carries the reason the front door gave."""
    project = _project(tmp_path)
    record = _refused_revision(project)
    refusal = edit_spec.read_refusal(str(project), record["spec_id"])
    assert refusal is not None
    assert "clip.trim" in refusal["reason"]
    assert refusal["detail"]["note_id"] == record["note_id"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
