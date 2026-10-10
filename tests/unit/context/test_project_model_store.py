import json
from pathlib import Path

import pytest

from library.tools.project_model import validate_project_model
from library.tools.project_model_store import (
    append_model_patch,
    commit_project_model,
    load_project_model,
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
