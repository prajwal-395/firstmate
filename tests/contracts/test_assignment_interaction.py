"""Transport conformance for the host-neutral assignment contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from library.tools import assignment_interaction as interaction
from library.tools import operations

RECORDED = {
    "version": 1,
    "assignment_id": "assignment-recording-0042",
    "operation_id": "footage.scan",
    "project": "/fixture/project",
    "inputs": {"include_hidden": False},
    "stage": "execute",
}


@pytest.fixture
def deterministic_operation(monkeypatch):
    calls = []

    class Operation:
        def __init__(self, name):
            self.name = name

        def execute(self, project, **inputs):
            calls.append((project, inputs))
            if inputs.get("refuse"):
                refusal_reason = lambda: "footage.scan refused: fixture refusal"
                return SimpleNamespace(
                    refused=True,
                    operation=self.name,
                    error="fixture refusal",
                    refusal_reason=refusal_reason,
                    unsatisfied=(),
                    payload=None,
                )
            return SimpleNamespace(
                refused=False,
                operation=self.name,
                error="",
                unsatisfied=(),
                payload={"files": ["clip-a.mov"], "count": 1},
            )

    monkeypatch.setattr(operations, "get", lambda name: Operation(name))
    return calls


@pytest.mark.parametrize("overrides", [{}, {"refuse": True}])
def test_structured_and_file_transports_match_recorded_requests(
    tmp_path, monkeypatch, deterministic_operation, overrides
):
    # The real file adapter's paths are rooted in request.project. Keep the
    # recorded identity and redirect only its project to this isolated fixture.
    request = {
        **RECORDED,
        "project": str(tmp_path),
        "inputs": {**RECORDED["inputs"], **overrides},
    }
    structured = interaction.structured_transport(request)
    file_result = interaction.file_transport(request)
    assert structured == file_result
    assert structured["assignment_id"] == RECORDED["assignment_id"]
    assert structured["operation_id"] == RECORDED["operation_id"]
    assert structured["inputs"] == request["inputs"]
    assert structured["status"] == (
        "refused" if overrides.get("refuse") else "completed"
    )
    assert structured["recovery"]["resumable"] is True
    assert len(deterministic_operation) == 1


def test_file_transport_reuses_checkpoint_for_same_assignment(
    tmp_path, deterministic_operation
):
    request = {**RECORDED, "project": str(tmp_path)}
    first = interaction.file_transport(request)
    second = interaction.file_transport(request)
    assert second == first
    assert len(deterministic_operation) == 1


def test_envelope_rejects_unknown_stage_or_version():
    with pytest.raises(ValueError, match="unknown interaction stage"):
        interaction.AssignmentRequest.from_dict({**RECORDED, "stage": "invent"})
    with pytest.raises(ValueError, match="unsupported assignment envelope version"):
        interaction.AssignmentRequest.from_dict({**RECORDED, "version": 9})


def test_operation_effects_allow_safe_work_and_refuse_unsafe_actions(
    tmp_path, deterministic_operation
):
    request = {**RECORDED, "project": str(tmp_path)}
    safe = interaction.structured_transport(request)
    assert safe["effect"]["disposition"] == "proceed"
    for extra, disposition in [
        ({"delete_source_media": True}, "ask"),
        ({"direction_change": True}, "clarify"),
    ]:
        answer = interaction.structured_transport(
            {
                **request,
                "assignment_id": f"assignment-{disposition}",
                "inputs": {**request["inputs"], **extra},
            }
        )
        assert answer["status"] == "refused"
        assert answer["effect"]["disposition"] == disposition
    assert len(deterministic_operation) == 1


def test_approved_work_replacement_requires_per_assignment_approval(
    tmp_path, deterministic_operation
):
    from library.tools import assignment_lifecycle

    assignment_lifecycle.transition(str(tmp_path), "approved-one", "draft")
    assignment_lifecycle.transition(str(tmp_path), "approved-one", "review")
    assignment_lifecycle.transition(str(tmp_path), "approved-one", "approved")
    request = {
        **RECORDED,
        "operation_id": "picture.plan",
        "assignment_id": "approved-one",
        "project": str(tmp_path),
        "inputs": {},
    }
    denied = interaction.structured_transport(request)
    assert denied["status"] == "refused"
    assert denied["effect"]["disposition"] == "ask"
    assignment_lifecycle.authorize_replacement(str(tmp_path), "approved-one")
    allowed = interaction.structured_transport(request)
    assert allowed["status"] == "completed"
    assert allowed["effect"]["disposition"] == "proceed"


def test_duplicate_call_and_interruption_recovery_are_idempotent(
    tmp_path, deterministic_operation
):
    request = {**RECORDED, "project": str(tmp_path)}
    first = interaction.structured_transport(request)
    second = interaction.structured_transport(request)
    assert second == first
    assert len(deterministic_operation) == 1
    from library.tools import assignment_lifecycle

    recovered = assignment_lifecycle.recover(str(tmp_path), RECORDED["assignment_id"])
    assert recovered["recovery"] == "completed"
    assert recovered["saved_envelope"] == first

    pending_id = "pending-operation"
    pending_request = {
        **RECORDED,
        "assignment_id": pending_id,
        "project": str(tmp_path),
    }
    assignment_lifecycle.begin_operation(
        str(tmp_path), pending_id, RECORDED["operation_id"], pending_request
    )
    assignment_lifecycle.interrupt(str(tmp_path), pending_id)
    interrupted = assignment_lifecycle.recover(str(tmp_path), pending_id)
    assert interrupted["recovery"] == "resume"
    assert interrupted["resume_request"] == pending_request
    resumed = interaction.structured_transport(pending_request)
    assert resumed["status"] == "completed"
    assert (
        assignment_lifecycle.recover(str(tmp_path), pending_id)["recovery"]
        == "completed"
    )
    assert len(deterministic_operation) == 2


def test_assignment_can_repeat_an_operation_with_new_inputs(
    tmp_path, deterministic_operation
):
    request = {**RECORDED, "project": str(tmp_path)}
    interaction.structured_transport(request)
    revised = {
        **request,
        "inputs": {"include_hidden": True},
    }
    first = interaction.structured_transport(revised)
    assert interaction.structured_transport(revised) == first
    assert len(deterministic_operation) == 2


def test_lifecycle_transitions_and_resume_from_each_checkpoint(tmp_path):
    from library.tools import assignment_lifecycle as lifecycle

    for index, name in enumerate(lifecycle.STATES):
        assignment_id = f"checkpoint-{name}"
        lifecycle.create(str(tmp_path), assignment_id)
        for target in lifecycle.STATES[1 : index + 1]:
            lifecycle.transition(str(tmp_path), assignment_id, target)
        record = lifecycle.recover(str(tmp_path), assignment_id)
        assert record["state"] == name
        assert record["recovery"] == "continue"
    record = lifecycle.create(str(tmp_path), "transition")
    assert record["state"] == "brief"
    for state in ("draft", "review", "approved", "delivered"):
        record = lifecycle.transition(str(tmp_path), "transition", state)
        assert record["state"] == state
        assert lifecycle.transition(str(tmp_path), "transition", state) == record
    with pytest.raises(ValueError, match="invalid assignment transition"):
        lifecycle.transition(str(tmp_path), "transition", "draft")
