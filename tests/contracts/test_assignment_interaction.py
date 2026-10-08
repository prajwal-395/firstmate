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
        def execute(self, project, **inputs):
            calls.append((project, inputs))
            if inputs.get("refuse"):
                refusal_reason = lambda: "footage.scan refused: fixture refusal"
                return SimpleNamespace(
                    refused=True, operation="footage.scan", error="fixture refusal",
                    refusal_reason=refusal_reason, unsatisfied=(), payload=None)
            return SimpleNamespace(refused=False, operation="footage.scan",
                                   error="", unsatisfied=(),
                                   payload={"files": ["clip-a.mov"], "count": 1})

    monkeypatch.setattr(operations, "get", lambda name: Operation())
    return calls


@pytest.mark.parametrize("overrides", [{}, {"refuse": True}])
def test_structured_and_file_transports_match_recorded_requests(
        tmp_path, monkeypatch, deterministic_operation, overrides):
    # The real file adapter's paths are rooted in request.project. Keep the
    # recorded identity and redirect only its project to this isolated fixture.
    request = {**RECORDED, "project": str(tmp_path),
               "inputs": {**RECORDED["inputs"], **overrides}}
    structured = interaction.structured_transport(request)
    file_result = interaction.file_transport(request)
    assert structured == file_result
    assert structured["assignment_id"] == RECORDED["assignment_id"]
    assert structured["operation_id"] == RECORDED["operation_id"]
    assert structured["inputs"] == request["inputs"]
    assert structured["status"] == ("refused" if overrides.get("refuse") else "completed")
    assert structured["recovery"]["resumable"] is True
    assert len(deterministic_operation) == 2


def test_file_transport_reuses_checkpoint_for_same_assignment(tmp_path,
                                                               deterministic_operation):
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
