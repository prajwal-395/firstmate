import json
from pathlib import Path

import pytest

from library.tools.project_model import validate_project_model
from library.tools.project_model_store import (
    append_model_patch,
    commit_project_model,
    commit_evidence_import,
    load_project_model,
    merge_evidence_records,
    parity_report,
    write_parity_report,
)


FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(encoding="utf-8")
)


def test_revision_log_replays_and_repairs_snapshot(tmp_path):
    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0, reason="fixture") == 1
    updated = dict(FIXTURE)
    updated["intents"] = [{**FIXTURE["intents"][0], "constraints": ["keep captions"]}]
    assert append_model_patch(project, {"intents": updated["intents"]}, expected_revision=1) == 2
    (project / "project_model" / "snapshot.json").unlink()

    revision, model = load_project_model(project)

    assert revision == 2
    assert model == updated
    snapshot = json.loads((project / "project_model" / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["revision"] == 2
    assert len((project / "project_model" / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_revision_conflict_and_corrupt_event_are_rejected(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    with pytest.raises(RuntimeError, match="stale"):
        commit_project_model(project, FIXTURE, expected_revision=0)
    events = project / "project_model" / "events.jsonl"
    events.write_text(events.read_text(encoding="utf-8").replace('"reason":""', '"reason":"tampered"'), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_project_model(project)


def test_capability_evidence_merge_appends_one_revision_and_stale_writer_refuses(tmp_path):
    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0) == 1
    added = {**FIXTURE["observations"][0], "id": "obs-from-capability"}
    assert merge_evidence_records(
        project, observations=[added], expected_revision=1,
        author="capability:semantics.analyse") == 2
    with pytest.raises(RuntimeError, match="stale project model revision 1"):
        merge_evidence_records(
            project, observations=[{**added, "id": "stale-observation"}],
            expected_revision=1, author="capability:semantics.analyse")
    revision, replayed = load_project_model(project)
    assert revision == 2
    assert replayed["observations"][-1] == added
    assert len((project / "project_model" / "events.jsonl").read_text(
        encoding="utf-8").splitlines()) == 2


def test_evidence_import_cas_preserves_timeline_state_and_edit_history(tmp_path):
    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0) == 1
    timeline = {"id": "timeline-state-1", "timeline_id": "timeline-1",
                "generation": 1, "clip_use_ids": ["use-1"],
                "edit_operation_ids": ["edit-1"]}
    assert append_model_patch(
        project, {"timeline_states": [timeline]}, expected_revision=1) == 2

    evidence = {**FIXTURE, "observations": [
        FIXTURE["observations"][0],
        {**FIXTURE["observations"][0], "id": "obs-imported"}]}
    assert commit_evidence_import(
        project, evidence, expected_revision=2,
        author="capability:project_model_evidence") == 3
    revision, current = load_project_model(project)
    assert revision == 3
    assert current["timeline_states"] == [timeline]
    assert current["edits"] == FIXTURE["edits"]
    assert {row["id"] for row in current["observations"]} == {"obs-1", "obs-imported"}
    with pytest.raises(RuntimeError, match="stale project model revision 2"):
        commit_evidence_import(
            project, evidence, expected_revision=2,
            author="capability:project_model_evidence")


def test_parity_report_is_read_only_and_names_unreadable_stores(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    (project / "pipeline_data.json").write_text("{", encoding="utf-8")
    before = (project / "pipeline_data.json").read_bytes()

    report = write_parity_report(project)

    assert report["adapters"]["pipeline_data"]["readable"] is False
    assert report["mismatches"] == [{"store": "pipeline_data", "kind": "unreadable", "path": "pipeline_data.json"}]
    assert (project / "pipeline_data.json").read_bytes() == before
    assert parity_report(project)["project_id"] == FIXTURE["id"]
    validate_project_model(FIXTURE)


def test_pipeline_data_projects_the_event_revision_and_parity_is_actionable(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    state = {"existing": True}
    from library.tools.project_model_store import project_pipeline_state

    assert project_pipeline_state(project, state) is True
    (project / "pipeline_data.json").write_text(json.dumps(state), encoding="utf-8")
    report = parity_report(project)
    assert report["revision"]["number"] == 1
    assert not any(row["kind"] in {
        "stale_revision", "project_model_projection_drift"
    } for row in report["mismatches"])

    state["project_model_projection"]["revision"] = 0
    (project / "pipeline_data.json").write_text(json.dumps(state), encoding="utf-8")
    drift = parity_report(project)["mismatches"]
    projection_drift = next(row for row in drift
                            if row["kind"] == "project_model_projection_drift")
    assert "save pipeline state" in projection_drift["action"]

    snapshot_path = project / "project_model" / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["revision"] = 0
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    before = snapshot_path.read_bytes()
    report = parity_report(project)
    assert any(row["kind"] == "stale_revision" for row in report["mismatches"])
    assert snapshot_path.read_bytes() == before
