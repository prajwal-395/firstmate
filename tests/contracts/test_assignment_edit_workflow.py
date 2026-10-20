"""The assignment-to-AXI bridge: named defects at the task boundary."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from library.tools import assignment_edit_workflow as workflow
from library.tools import assignment_lifecycle, assignment_provenance, assignment_view
from library.tools.edit_request import EditOperation, EditRequest, EditTarget
from library.tools.editorial_intent import build_intent


def _intent(_project, _intent_id):
    return build_intent(
        intent_id="intent-cleanup",
        requests=[{"id": "tighten", "kind": "tighten_dialogue",
                   "statement": "Tighten dialogue", "evidence_lanes": ["m1"]}],
    ), "model-revision-1"


def _approval_intent(_project, _intent_id):
    return build_intent(
        intent_id="intent-cleanup",
        requests=[{"id": "tighten", "kind": "tighten_dialogue",
                   "statement": "Tighten dialogue", "evidence_lanes": ["m1"],
                   "autonomy": "needs_approval",
                   "autonomy_basis": "the creator must approve this cut"}],
    ), "model-revision-1"


def _request(project):
    return EditRequest(
        id="cleanup-request", project="Resolve Project", timeline="Interview",
        operations=(EditOperation(
            op="clip.trim", target=EditTarget(unique_id="clip-1"),
            params={"in_frame": 12, "out_frame": 80},
            expected_effect="remove the marked pause while preserving sync"),),
    )


def _assignment(tmp_path):
    project = str(tmp_path)
    record = assignment_lifecycle.create(project, "assignment-1")
    record["intent_id"] = "intent-cleanup"
    assignment_lifecycle.save(project, record)
    assignment_provenance.record_evidence(
        project, "assignment-1", evidence_id="pause-span", kind="transcript",
        source="speech analysis", locator="clip-1@12-80", detail={"lane": "m1"})
    assignment_provenance.record_decision(
        project, "assignment-1", decision_id="remove-pause",
        question="Which pause should be shortened?", answer="clip-1@12-80",
        evidence_ids=["pause-span"])
    return project


def _plan(tmp_path, monkeypatch):
    project = _assignment(tmp_path)
    monkeypatch.setattr(workflow, "_intent", _intent)
    return project, workflow.plan(
        project, "assignment-1", workflow_id="E2E-009",
        intent_id="intent-cleanup", intent_request_id="tighten",
        request=_request(project), evidence_ids=["pause-span"],
        decision_ids=["remove-pause"], acceptance_criteria=[
            {"id": "sync", "statement": "dialogue remains in sync",
             "kind": "technical"},
            {"id": "clean", "statement": "the pause is shortened safely",
             "kind": "editorial"},
        ])


def test_a_pause_trim_without_assignment_evidence_is_refused(tmp_path, monkeypatch):
    project = str(tmp_path)
    record = assignment_lifecycle.create(project, "assignment-1")
    record["intent_id"] = "intent-cleanup"
    assignment_lifecycle.save(project, record)
    monkeypatch.setattr(workflow, "_intent", _intent)

    with pytest.raises(workflow.WorkflowRefused, match="provenance is not on the assignment"):
        workflow.plan(
            project, "assignment-1", workflow_id="E2E-009",
            intent_id="intent-cleanup", intent_request_id="tighten",
            request=_request(project), evidence_ids=["missing-span"],
            decision_ids=["missing-decision"], acceptance_criteria=[])


def test_a_plan_cannot_route_an_unrelated_operation_as_dialogue_cleanup(
    tmp_path, monkeypatch
):
    project = _assignment(tmp_path)
    monkeypatch.setattr(workflow, "_intent", _intent)
    unrelated = EditRequest(
        id="cleanup-request", project="Resolve Project", timeline="Interview",
        operations=(EditOperation(
            op="overlay.position", target=EditTarget(unique_id="graphic-1"),
            params={"dx": 12, "dy": 24}, expected_effect="move a logo"),),
    )

    with pytest.raises(workflow.WorkflowRefused, match="does not allow operations"):
        workflow.plan(
            project, "assignment-1", workflow_id="E2E-009",
            intent_id="intent-cleanup", intent_request_id="tighten",
            request=unrelated, evidence_ids=["pause-span"],
            decision_ids=["remove-pause"], acceptance_criteria=[])


def test_a_broll_replacement_cannot_be_linked_to_a_dialogue_intent(
    tmp_path, monkeypatch
):
    project = _assignment(tmp_path)
    monkeypatch.setattr(workflow, "_intent", _intent)
    broll_request = EditRequest(
        id="replacement-request", project="Resolve Project", timeline="Interview",
        operations=(EditOperation(
            op="broll.insert", target=EditTarget(unique_id="clip-1"),
            params={"track_type": "video", "track_index": 2,
                    "record_in": 12, "record_out": 80, "source_in": 0,
                    "source_out": 68, "clip": "cover.mov"},
            expected_effect="replace the marked B-roll with matching coverage"),),
    )

    with pytest.raises(workflow.WorkflowRefused,
                       match="requires EditorialIntent kind 'replace_broll'"):
        workflow.plan(
            project, "assignment-1", workflow_id="E2E-010",
            intent_id="intent-cleanup", intent_request_id="tighten",
            request=broll_request, evidence_ids=["pause-span"],
            decision_ids=["remove-pause"], acceptance_criteria=[])


def test_an_unapproved_workflow_never_reaches_axi(tmp_path, monkeypatch):
    project, entry = _plan(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        "library.tools.resolve_axi_core.submit_edit_request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(PermissionError, match="requires creator_brief or creator_approval"):
        workflow.apply(project, "assignment-1", entry["request_id"],
                       authorization_source="")
    assert calls == []


def test_a_creator_brief_cannot_authorize_an_intent_that_needs_approval(
    tmp_path, monkeypatch
):
    project = _assignment(tmp_path)
    monkeypatch.setattr(workflow, "_intent", _approval_intent)
    assignment_provenance.record_decision(
        project, "assignment-1", decision_id="creator-approval",
        question="May the pause be shortened?", answer="Yes.",
        evidence_ids=["pause-span"], decided_by="creator")
    entry = workflow.plan(
        project, "assignment-1", workflow_id="E2E-009",
        intent_id="intent-cleanup", intent_request_id="tighten",
        request=_request(project), evidence_ids=["pause-span"],
        decision_ids=["remove-pause", "creator-approval"],
        acceptance_criteria=[
            {"id": "sync", "statement": "dialogue remains in sync",
             "kind": "technical"},
            {"id": "clean", "statement": "the pause is shortened safely",
             "kind": "editorial"},
        ])
    calls = []
    monkeypatch.setattr(
        "library.tools.resolve_axi_core.submit_edit_request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(PermissionError, match="requires a recorded creator approval"):
        workflow.apply(project, "assignment-1", entry["request_id"],
                       authorization_source="creator_brief")
    assert calls == []


def test_verified_edit_receipt_stays_on_the_assignment(tmp_path, monkeypatch):
    project, entry = _plan(tmp_path, monkeypatch)
    submitted = []
    monkeypatch.setattr(
        "library.tools.assignment_spine.begin_revision",
        lambda *args, **kwargs: SimpleNamespace(checkpoints=[]),
    )
    monkeypatch.setattr(
        "library.tools.assignment_spine.end_act",
        lambda *args, **kwargs: None,
    )
    receipt = {"status": "committed", "generation": 8,
               "preservation": {"status": "preserved"}}
    monkeypatch.setattr(
        "library.tools.resolve_axi_core.submit_edit_request",
        lambda request, **kwargs: submitted.append((request, kwargs)) or receipt,
    )

    result = workflow.apply(
        project, "assignment-1", entry["request_id"],
        authorization_source="creator_brief")

    stored = assignment_lifecycle.load(project, "assignment-1")
    saved = stored["edit_workflows"][entry["request_id"]]
    assert result == receipt
    assert saved["status"] == "verified"
    assert saved["receipt"] == receipt
    assert {row["id"] for row in stored["acceptance"]["criteria"]} == {
        "sync", "clean",
    }
    assert submitted[0][1]["authorized"] is True
    assert submitted[0][0].originator["intent_id"] == "intent-cleanup"


def test_a_resumed_workflow_reuses_its_assignment_request_and_preservation_receipt(
    tmp_path, monkeypatch
):
    project, entry = _plan(tmp_path, monkeypatch)
    record = assignment_lifecycle.load(project, "assignment-1")
    record["edit_workflows"][entry["request_id"]]["status"] = "applying"
    assignment_lifecycle.save(project, record)
    monkeypatch.setattr(
        "library.tools.assignment_spine.begin_revision",
        lambda *args, **kwargs: SimpleNamespace(checkpoints=[]),
    )
    monkeypatch.setattr(
        "library.tools.assignment_spine.end_act",
        lambda *args, **kwargs: None,
    )
    preserved = {"human_transform": True, "unrelated_items": True}
    monkeypatch.setattr(
        "library.tools.resolve_axi_core.submit_edit_request",
        lambda request, **kwargs: {
            "status": "committed", "preservation": preserved,
        },
    )

    workflow.apply(project, "assignment-1", entry["request_id"],
                   authorization_source="creator_brief")

    stored = assignment_lifecycle.load(project, "assignment-1")
    assert stored["edit_workflows"][entry["request_id"]]["status"] == "verified"
    assert stored["edit_workflows"][entry["request_id"]]["receipt"][
        "preservation"] == preserved


def test_human_feedback_replans_on_the_same_assignment(tmp_path, monkeypatch):
    project, original = _plan(tmp_path, monkeypatch)
    assignment_provenance.record_decision(
        project, "assignment-1", decision_id="creator-feedback",
        question="Should the pause be tighter?", answer="Trim the marked span further.",
        evidence_ids=["pause-span"], decided_by="creator")
    next_request = EditRequest(
        id="cleanup-request-revision", project="Resolve Project",
        timeline="Interview", operations=(EditOperation(
            op="clip.trim", target=EditTarget(unique_id="clip-1"),
            params={"in_frame": 16, "out_frame": 80},
            expected_effect="tighten the same pause after creator feedback"),),
    )

    revised = workflow.plan(
        project, "assignment-1", workflow_id="E2E-009",
        intent_id="intent-cleanup", intent_request_id="tighten",
        request=next_request, evidence_ids=["pause-span"],
        decision_ids=["creator-feedback"], acceptance_criteria=[
            {"id": "sync", "statement": "dialogue remains in sync",
             "kind": "technical"},
            {"id": "clean", "statement": "the pause is shortened safely",
             "kind": "editorial"},
        ])

    stored = assignment_lifecycle.load(project, "assignment-1")
    assert revised["request_id"] != original["request_id"]
    assert set(stored["edit_workflows"]) == {
        original["request_id"], revised["request_id"],
    }


def test_assignment_readback_does_not_hide_a_planned_edit_workflow():
    record = {
        "operations": {},
        "reels": {},
        "edit_workflows": {
            "cleanup-request": {
                "request_id": "cleanup-request", "workflow_id": "E2E-009",
                "status": "planned", "request": {"operations": [
                    {"expected_effect": "remove the marked pause"},
                ]},
            },
        },
    }

    applied = assignment_view._applied(record, [])

    assert applied["counts"]["planned"] == 1
    assert applied["planned"][0]["workflow_id"] == "E2E-009"
