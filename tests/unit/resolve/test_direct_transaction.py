from types import SimpleNamespace

import pytest

from library.tools import direct_transaction


def test_unqualified_direct_transaction_refuses_before_observation(monkeypatch):
    monkeypatch.setattr(direct_transaction, "RECOVERY_QUALIFICATIONS", {})
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held", lambda: True)
    observed = []

    with pytest.raises(direct_transaction.DirectTransactionRefused,
                       match="no direct recovery qualification"):
        direct_transaction.execute(
            decision=SimpleNamespace(mode="direct"),
            operations=[{"op": "marker.add", "frame": 4}],
            project_folder="/project", transaction_id="tx-1",
            observe=lambda: observed.append(True), apply=lambda _op: None,
            verify=lambda *_args: None, same_state=lambda a, b: a == b)

    assert observed == []


def test_staged_route_never_enters_direct_executor(monkeypatch):
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held", lambda: True)
    with pytest.raises(direct_transaction.DirectTransactionRefused,
                       match="router did not select direct mode"):
        direct_transaction.execute(
            decision=SimpleNamespace(mode="staged"),
            operations=[{"op": "marker.add", "frame": 4}],
            project_folder="/project", transaction_id="tx-1",
            observe=dict, apply=lambda _op: None,
            verify=lambda *_args: None, same_state=lambda a, b: a == b)


def test_direct_transaction_undo_redo_share_verified_compensation_journal(
        monkeypatch, tmp_path):
    from library.tools import event_log

    state = {"value": 0}
    events = [{"fields": {
        "direct_transaction": True, "transaction_id": "tx-undo-redo",
        "stage": "committed", "operations": [{"op": "marker.add"}],
        "original_state": {"value": 0}, "observed_state": {"value": 1},
        "recovery_evidence": [["qualification"]]}}]

    def emit(_project, _kind, **fields):
        events.append({"fields": dict(fields)})
        return {}

    monkeypatch.setattr(event_log, "read_events", lambda _project: events)
    monkeypatch.setattr(event_log, "emit", emit)
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held", lambda: True)
    monkeypatch.setattr(
        direct_transaction, "_qualification_for",
        lambda _op: direct_transaction.RecoveryQualification(
            operation="marker.add", inverse="marker.delete",
            evidence=("qualification",),
            compensator=lambda _operation, original: state.update(original)))
    state["value"] = 1

    undone = direct_transaction.undo_committed(
        project_folder=str(tmp_path), transaction_id="tx-undo-redo",
        observe=lambda: dict(state), apply=lambda _op: state.update(value=1),
        same_state=lambda before, after: before == after)
    assert undone["status"] == "verified"
    assert state == {"value": 0}

    redone = direct_transaction.redo_committed(
        project_folder=str(tmp_path), transaction_id="tx-undo-redo",
        observe=lambda: dict(state), apply=lambda _op: state.update(value=1),
        verify=lambda _ops, _before, after: (
            None if after == {"value": 1} else "wrong read-back"),
        same_state=lambda before, after: before == after)
    assert redone["status"] == "verified"
    assert state == {"value": 1}
    assert events[-1]["fields"]["action"] == "redo"
