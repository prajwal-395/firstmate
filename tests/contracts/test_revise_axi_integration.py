"""Contract tests for AXI completion as recorded by ``ren revise``."""


def _receipt(**overrides):
    receipt = {
        "status": "committed",
        "generation": 12,
        "lifecycle": {"stage": "COMMITTED"},
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": [{"passed": True, "operation": "clip.set_property"}],
            },
        },
        "committed": {"status": "committed", "generation": 12},
        "applied": {"complete": True},
    }
    receipt.update(overrides)
    return receipt


def test_revise_accepts_only_common_receipt_with_verified_committed_generation():
    """Catches: a lifecycle label alone clearing the revision issue."""
    from library.tools.revise import _verified_completion

    assert _verified_completion(_receipt()) is True
    assert _verified_completion(_receipt(
        lifecycle={"stage": "VERIFIED"},
        committed={"status": "readback_persisted_unverified",
                   "generation": 12})) is False


def test_revise_keeps_partial_or_missing_operation_evidence_unresolved():
    """Catches: one successful operation in a batch reporting full success."""
    from library.tools.revise import _verified_completion

    partial = _receipt(
        status="partial_failure",
        lifecycle={"stage": "APPLIED"},
        execution_contract={"verification": {
            "status": "failed",
            "checks": [{"passed": True}, {"passed": False}],
        }},
        applied={"complete": False})
    assert _verified_completion(partial) is False
    assert _verified_completion(_receipt(
        execution_contract={"verification": {
            "status": "verified", "checks": []}})) is False


def test_revise_accepts_verified_noop_without_inventing_a_commit():
    """Catches: an already-satisfied edit being treated as a failed write."""
    from library.tools.revise import _verified_completion

    assert _verified_completion(_receipt(
        status="already_satisfied", generation=None,
        lifecycle={"stage": "VERIFIED"},
        execution_contract={"verification": {
            "status": "verified", "checks": [{"passed": True}]}})) is True


def test_partial_receipt_stays_open_and_resume_retranslates():
    """Catches: an unresolved partial edit being retried on a stale plan."""
    from library.tools import revise

    result = revise._result("/project", {
        "revise_id": "revise_partial",
        "status": revise.STATUS_INCOMPLETE,
        "execution_receipt": {"status": "partial_failure"},
    })
    assert result["resume"].endswith("--retranslate")
    assert "remains unresolved" in result["reply"]


def test_revise_atomic_program_links_note_and_target_evidence_and_criteria():
    """Catches: revise bypasses EditorialIntent's evidence-grounded compiler."""
    from library.tools import edit_request, revise

    program = {"kind": "edit_program", "id": "program-1",
               "base_generation": 12,
               "ops": [{"op": "clip.trim", "text": "remove the repeated answer",
                        "params": {"unique_id": "clip-7", "in_frame": 10,
                                   "out_frame": 40},
                        "postconditions": [{"property": "source_range",
                                           "equals": [10, 40]}]},
                        {"op": "clip.set_enabled", "text": "hide the duplicate",
                         "params": {"unique_id": "clip-8", "enabled": False}}]}
    record = {"revise_id": "revise-1", "note_id": "note-1",
              "note": "remove the repeated answer"}

    request = revise._compile_revision_program(
        program, project="project", reel="reel", record=record)

    decisions = record["editorial_decisions"]["decisions"]
    assert len(decisions) == 2
    assert all(decision["state"] == "approved" for decision in decisions)
    assert all(decision["evidence_ids"] == ["revision-note:note-1"]
               for decision in decisions)
    assert [decision["intended_effect"] for decision in decisions] == [
        "remove the repeated answer", "hide the duplicate"]
    assert all(decision["evaluation_criteria"] for decision in decisions)
    assert record["approved_program"]["ops"][0]["evidence_ids"] == [
        "revision-note:note-1"]
    assert (record["approved_program"]["ops"][0]["evaluation_criteria"]
            == decisions[0]["evaluation_criteria"])
    assert request.operations[0].op == "clip.trim"
    assert request.operations[0].expected_effect == "remove the repeated answer"
    original = edit_request.from_edit_program(
        program, project="project", timeline="reel")
    assert [op.to_dict() for op in request.operations] == [
        op.to_dict() for op in original.operations]


def test_revise_atomic_program_names_missing_evidence_instead_of_compiling():
    """Catches: an approved revise program proceeds with no evidence link."""
    import pytest

    from library.tools import editorial_decisions, revise

    program = {"kind": "edit_program", "id": "program-1",
               "ops": [{"op": "clip.trim", "text": "remove the repeat",
                        "params": {"unique_id": "clip-7"}}]}
    record = {"revise_id": "revise-1", "note": "remove the repeat"}
    with pytest.raises(editorial_decisions.EditorialDecisionError,
                       match="rests on no evidence"):
        revise._compile_revision_program(
            program, project="project", reel="reel", record=record)


def test_revise_program_base_generation_reaches_the_request_as_a_number():
    """Catches: the spec's string base_generation reaching the request as a
    string, so the staleness check compares an int with a str and every
    revise program is reported stale against the very head it names.

    Measured live on 2026-10-20: `ren revise` against Reel 13 of the
    Ren-owned scratch copy refused with "request base generation 1 is
    stale; recorded head is 1" - the same number on both sides.
    """
    from library.tools import revise

    program = {"kind": "edit_program", "id": "program-1",
               "base_generation": "12",
               "ops": [{"op": "clip.trim", "text": "remove the repeated answer",
                        "params": {"unique_id": "clip-7", "in_frame": 10,
                                   "out_frame": 40}}]}
    record = {"revise_id": "revise-1", "note_id": "note-1",
              "note": "remove the repeated answer"}

    request = revise._compile_revision_program(
        program, project="project", reel="reel", record=record)

    assert request.expected_base_generation == 12
    assert isinstance(request.expected_base_generation, int)


def test_a_landed_revision_with_no_summary_does_not_reply_with_the_note():
    """Catches: the final reply repeating the intention instead of the result.

    The reply is the one line the person reads.  A landed revision with
    no change summary on record used to answer with nothing at all, and
    the host - holding the request - wrote the request back.  The reply
    now states the result, and it never states the note.
    """
    from library.tools import revise

    note = "move the name bar down so it stops fighting the captions"
    result = revise._result("/project", {
        "revise_id": "revise_1",
        "status": revise.STATUS_PATCHED,
        "note": note,
    })
    assert result["reply"], "a landed revision answered with nothing"
    assert note not in result["reply"], (
        "the reply repeated the note instead of stating the result")
    assert "applied" in result["reply"].lower()


def test_a_landed_revision_replies_with_the_change_that_landed():
    """Catches: a reply that names no outcome at all.

    The change summary IS the result - what moved, on which row, over
    which seconds - and it is what the person is told.
    """
    from library.tools import revise

    summary = ("Set position on V6 name bar from 0.02 to 0.14 of the frame "
               "height.")
    result = revise._result("/project", {
        "revise_id": "revise_2",
        "status": revise.STATUS_PATCHED,
        "note": "move the name bar down",
        "change": {"summary": summary},
    })
    assert result["reply"] == summary
