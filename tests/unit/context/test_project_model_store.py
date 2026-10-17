import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from library.tools import project_model_store
from library.tools.project_model import validate_project_model
from library.tools.project_model_store import (
    append_model_patch,
    commit_evidence_import,
    commit_project_model,
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


@pytest.mark.parametrize("damage", ["missing", "invalid_json", "stale_revision", "stale_hash", "stale_model"])
def test_snapshot_damage_is_rebuilt_from_the_saved_event_history(tmp_path, damage):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0, reason="saved baseline")
    updated = {**FIXTURE, "intents": [
        {**FIXTURE["intents"][0], "constraints": ["preserve evidence"]}
    ]}
    append_model_patch(project, {"intents": updated["intents"]}, expected_revision=1)
    snapshot_path = project / "project_model" / "snapshot.json"
    if damage == "missing":
        snapshot_path.unlink()
    elif damage == "invalid_json":
        snapshot_path.write_text("{truncated", encoding="utf-8")
    else:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if damage == "stale_revision":
            snapshot["revision"] = 1
        elif damage == "stale_hash":
            snapshot["head_hash"] = "0" * 64
        else:
            snapshot["model"] = FIXTURE
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    revision, model = load_project_model(project)

    assert (revision, model) == (2, updated)
    repaired = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert repaired == {
        "revision": 2,
        "head_hash": json.loads((project / "project_model" / "events.jsonl").read_text(
            encoding="utf-8").splitlines()[-1])["event_hash"],
        "model": updated,
    }


def test_snapshot_write_failure_after_durable_append_recovers_on_restart(tmp_path, monkeypatch):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    updated = {**FIXTURE, "intents": [
        {**FIXTURE["intents"][0], "constraints": ["recover committed revision"]}
    ]}
    real_write = project_model_store._write_snapshot

    def interrupted_write(*_args, **_kwargs):
        raise OSError("simulated interruption after event fsync")

    monkeypatch.setattr(project_model_store, "_write_snapshot", interrupted_write)
    with pytest.raises(OSError, match="simulated interruption"):
        append_model_patch(project, {"intents": updated["intents"]}, expected_revision=1)
    monkeypatch.setattr(project_model_store, "_write_snapshot", real_write)

    revision, replayed = load_project_model(project)
    assert revision == 2
    assert replayed == updated
    assert json.loads((project / "project_model" / "snapshot.json").read_text(
        encoding="utf-8"))["model"] == updated


def test_interrupted_event_append_refuses_without_discarding_valid_history(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    events_path = project / "project_model" / "events.jsonl"
    with events_path.open("ab") as stream:
        stream.write(b'{"revision":2,"kind":"merge_patch"')
    before = events_path.read_bytes()

    with pytest.raises(ValueError, match="invalid project model event at revision 2"):
        load_project_model(project)

    assert events_path.read_bytes() == before
    assert json.loads((project / "project_model" / "snapshot.json").read_text(
        encoding="utf-8"))["revision"] == 1


def test_concurrent_capability_evidence_updates_retry_without_losing_either(tmp_path):
    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0)
    gate = Barrier(2)
    rows = [
        {**FIXTURE["observations"][0], "id": "obs-cap-a"},
        {**FIXTURE["observations"][0], "id": "obs-cap-b"},
    ]

    def write(row):
        gate.wait()
        try:
            return merge_evidence_records(
                project, observations=[row], expected_revision=1,
                author=f"capability:{row['id']}")
        except RuntimeError as exc:
            assert "stale project model revision 1" in str(exc)
            return merge_evidence_records(
                project, observations=[row], expected_revision=2,
                author=f"capability:{row['id']}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(write, rows))

    revision, model = load_project_model(project)
    assert revision == 3
    assert sorted(revisions) == [2, 3]
    assert {row["id"] for row in model["observations"]} >= {
        "obs-cap-a", "obs-cap-b", FIXTURE["observations"][0]["id"]
    }


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


def test_replay_validates_once_not_once_per_event(tmp_path, monkeypatch):
    """Replaying N events validates one model, not N models.

    Regression for the 2026-10-17 build stall: `_replay` validated the
    whole model after every event, so loading a 37-event log with a
    350 MB model ran 37 full jsonschema validations (7.6 s each -
    a 12-minute hang on the `sorted(validator.iter_errors(...))`
    frame). The hash chain `_read_events` verifies still guards every
    event; the schema check runs once on the replayed model.
    """
    from library.tools import project_model_store as store

    calls = []
    real_validate = store.validate_project_model

    def counting_validate(model):
        calls.append(id(model))
        return real_validate(model)

    monkeypatch.setattr(store, "validate_project_model", counting_validate)

    project = tmp_path / "project"
    assert commit_project_model(project, FIXTURE, expected_revision=0,
                                reason="baseline") == 1
    for revision in range(1, 6):
        updated = dict(FIXTURE)
        updated["intents"] = [{**FIXTURE["intents"][0],
                               "constraints": [f"keep {revision}"]}]
        append_model_patch(project, {"intents": updated["intents"]},
                           expected_revision=revision)
    calls.clear()

    revision, model = load_project_model(project)

    assert revision == 6
    assert calls, "the replayed model must still be validated"
    assert len(calls) <= 2, (
        f"replaying 6 events validated {len(calls)} models - "
        "the per-event validation loop is back")


def test_replay_still_refuses_a_broken_event_chain(tmp_path):
    from library.tools.project_model_store import _read_events, _replay

    project = tmp_path / "project"
    commit_project_model(project, FIXTURE, expected_revision=0, reason="x")
    events_path = project / "project_model" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    lines.append(json.dumps({"revision": 2, "previous_hash": "0" * 64,
                             "kind": "bogus", "payload": {}}))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="broken project model event chain"):
        _read_events(events_path)
    with pytest.raises(ValueError, match="invalid event kind or order"):
        _replay([{"revision": 1, "kind": "bogus", "payload": {}}])
