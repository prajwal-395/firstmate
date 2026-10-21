import json

from library.tools.project_work_state import project_work_state


def test_work_state_composes_existing_project_records_read_only(tmp_path):
    project = tmp_path / "show"
    project.mkdir()
    (project / "project.yaml").write_text(
        'name: "Test Show"\nslug: show\nproject_format_version: 1\n',
        encoding="utf-8")
    (project / "pipeline_output" / "logs").mkdir(parents=True)
    (project / "pipeline_output" / "logs" / "event_log.jsonl").write_text(
        json.dumps({"seq": 1, "type": "branch_committed", "at": "today", "task": "cut"}) + "\n",
        encoding="utf-8")
    before = (project / "project.yaml").read_bytes()
    state = project_work_state(str(project))

    assert state["project"]["name"] == "Test Show"
    assert state["last_changes"][0]["event"] == "branch_committed"
    assert state["pending_approvals"] == []
    assert "Test Show" in state["summary"]
    assert (project / "project.yaml").read_bytes() == before
    assert not (project / "pipeline_output" / "logs" / "perf_ledger.jsonl").exists()


def test_work_state_reports_pending_review_and_latest_request(tmp_path):
    project = tmp_path / "show"
    project.mkdir()
    (project / "project.yaml").write_text('name: Show\nslug: show\n', encoding="utf-8")
    gate = project / "pipeline_output" / "gates" / "2_01"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(json.dumps({"status": "pending"}), encoding="utf-8")
    requests = project / "pipeline_output" / "llm_requests"
    requests.mkdir(parents=True)
    (requests / "edit_spec_1.json").write_text(json.dumps({
        "kind": "edit_spec", "timestamp": "2026-10-08", "prompt": "Move the intro earlier"
    }), encoding="utf-8")

    state = project_work_state(str(project))

    assert state["pending_approvals"] == [{"step": "2_01", "status": "pending"}]
    assert state["last_requested_change"]["prompt"] == "Move the intro earlier"
    assert state["open_work"][0]["kind"] == "edit_spec"


def test_work_state_includes_resumable_assignment_checkpoints(tmp_path):
    project = tmp_path / "show"
    project.mkdir()
    (project / "project.yaml").write_text('name: Show\nslug: show\n', encoding="utf-8")
    assignments = project / "pipeline_output" / "llm_requests" / "assignments"
    assignments.mkdir(parents=True)
    (assignments / "assignment.json").write_text(json.dumps({
        "version": 1,
        "assignment_id": "assignment-1",
        "state": "review",
        "checkpoint": "picture.plan:abc123",
        "operations": {"picture.plan:abc123": {
            "operation_id": "picture.plan", "status": "interrupted"}},
    }), encoding="utf-8")

    state = project_work_state(str(project))

    assert state["assignments"] == [{
        "assignment_id": "assignment-1",
        "state": "review",
        # The FINAL ACCEPTANCE (QA-008): a task whose gate has not run
        # carries no verdict, and the work-state says so rather than
        # reading the absence as a pass.
        "acceptance": {"outcome": "not_evaluated", "state": None,
                       "reason": "", "counts": {}},
        "checkpoint": "picture.plan",
        "recovery": "resume",
        "resume_point": {
            "action": "resume_operation", "command": None,
            "why": "operation picture.plan did not finish; resubmit its "
                   "recorded request"},
    }]
    assert {"kind": "assignment", **state["assignments"][0]} in state["open_work"]


def test_work_state_reports_what_the_task_proposed_applied_and_verified(tmp_path):
    """The defect: `ren work-state` named the assignment and its state
    and said NOTHING about what the task had done, so a fresh session
    could not tell a plan from an applied edit, and could not recover
    the decisions the task had already made."""
    project = tmp_path / "show"
    project.mkdir()
    (project / "project.yaml").write_text(
        'name: Show\nslug: show\n', encoding="utf-8")
    assignments = project / "pipeline_output" / "llm_requests" / "assignments"
    assignments.mkdir(parents=True)
    from library.tools import assignment_lifecycle as lifecycle

    record = lifecycle.create(
        str(project), "assignment-1", kind="reels",
        reels={"moment-1": {"number": 1, "name": "Reel 01 - pricing",
                            "status": "built"}})
    record["state"] = "review"
    record["evidence"] = [{"id": "pause-span", "kind": "transcript",
                           "source": "speech analysis",
                           "locator": "clip-1@12-80"}]
    record["decisions"] = [{"id": "remove-pause",
                            "question": "Which pause should be shortened?",
                            "answer": "clip-1@12-80", "basis": "evidence",
                            "evidence_ids": ["pause-span"],
                            "decided_by": "creator"}]
    lifecycle.save(str(project), record)
    _revision(project, "revise_a", assignment_id="assignment-1")

    state = project_work_state(str(project))

    [task] = state["tasks"]
    assert task["assignment_id"] == "assignment-1"
    assert task["proposed"] == 1
    # Two things landed: the correction AXI verified, and the reel the
    # assignment owns. Both are on the timeline, so both count.
    assert task["applied"] == 2
    assert task["verified"] == 1
    assert task["delivered"] == 0
    # The decisions the task already made are recoverable, and the next
    # act is named.
    assert [row["id"] for row in task["decisions"]] == ["remove-pause"]
    assert task["decisions"][0]["answer"] == "clip-1@12-80"
    assert task["resume_point"]["command"]
    assert "1 proposed" in state["summary"]
    assert "2 applied" in state["summary"]
    assert "1 verified" in state["summary"]


def _revision(project, revise_id, **fields):
    """One correction record, written the way `ren revise` writes it."""
    from library.tools import revise

    record = {"format": revise.FORMAT, "revise_id": revise_id,
              "project": str(project), "note": "the pause is too long",
              "note_id": "note-1", "source": "chat", "reel": "",
              "assignment_id": None, "spec_id": f"edit_spec_{revise_id}",
              "status": "patched",
              "change": {"summary": "the pause was shortened"},
              "verification": {"state": "axi_verified",
                               "receipt": {"status": "committed",
                                           "generation": 7,
                                           "lifecycle": {"stage": "COMMITTED"},
                                           "execution_contract": {
                                               "verification": {
                                                   "status": "verified"}}}}}
    record.update(fields)
    path = revise.record_path(str(project), revise_id, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


def test_work_state_verb_is_registered_in_both_front_doors():
    from pathlib import Path

    from ren.commands import VERBS

    repo = Path(__file__).resolve().parents[3]
    assert any(verb.name == "work-state" and
               verb.module_argv == ("library.tools.project_work_state",)
               for verb in VERBS)
    native = (repo / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "work-state"' in native
