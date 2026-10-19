"""Resolve lifecycle safety regressions named by punch list 8.2."""

import pytest

from library.tools import artifact_lifecycle as lifecycle


def _record(log_path, *, state=lifecycle.DISPOSABLE):
    identity = {"object_type": "timeline", "project_unique_id": "project-1",
                "object_unique_id": "timeline-1", "project_name": "Ren Qualification",
                "object_name": "Q Workflow duplicate"}
    lifecycle.register_resolve_artifact(
        log_path, artifact_id="artifact-1", kind="resolve_qualification_timeline",
        project_id="project-1", run_id="run-1", job_id="job-1",
        identity=identity, state=state)
    return identity


def test_duplicate_timeline_names_still_require_the_recorded_unique_id(
        monkeypatch, tmp_path):
    """8.2: same-name timeline cannot inherit another object's ownership."""
    log_path = tmp_path / "event_log.jsonl"
    identity = _record(log_path)
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held",
                        lambda: True)
    wrong_id = {**identity, "object_unique_id": "timeline-2"}
    called = []
    with pytest.raises(lifecycle.LifecycleRefused, match="differs"):
        lifecycle.retire_resolve_artifact(
            log_path, "artifact-1", wrong_id,
            lambda _identity: called.append(True))
    assert called == []


def test_delete_false_leaves_the_timeline_retryable(monkeypatch, tmp_path):
    """8.2: DeleteTimelines false cannot be recorded as a successful purge."""
    log_path = tmp_path / "event_log.jsonl"
    _record(log_path)
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held",
                        lambda: True)
    with pytest.raises(lifecycle.LifecycleRefused, match="did not verify"):
        lifecycle.retire_resolve_artifact(
            log_path, "artifact-1", _record_identity(log_path),
            lambda _identity: {"verified": False, "delete_returned": False})
    row, = lifecycle.resolve_artifacts(log_path)
    assert row["state"] == lifecycle.DISPOSABLE


def _record_identity(log_path):
    return lifecycle.resolve_artifacts(log_path)[0]["identity"]
