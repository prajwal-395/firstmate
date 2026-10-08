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
