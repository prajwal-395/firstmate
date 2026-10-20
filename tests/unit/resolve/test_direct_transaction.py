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


def test_direct_transaction_failure_after_uncertain_write_compensates_and_retries_idempotently(
        monkeypatch, tmp_path):
    """Defect: a write that mutates before raising must remain recoverable."""
    from library.tools import event_log

    events = []
    state = {"markers": []}
    calls = []
    operation = {"op": "marker.add", "frame": 12}

    def apply(op):
        calls.append(dict(op))
        if len(calls) == 2:
            state["markers"].append(op["frame"])
            raise TimeoutError("broker reply timed out after mutation")
        state["markers"].append(op["frame"])

    def emit(_project, _kind, **fields):
        events.append({"fields": dict(fields)})

    monkeypatch.setattr(event_log, "read_events", lambda _project: events)
    monkeypatch.setattr(event_log, "emit", emit)
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held", lambda: True)
    monkeypatch.setattr(
        direct_transaction, "_qualification_for",
        lambda _op: direct_transaction.RecoveryQualification(
            operation="marker.add", inverse="marker.delete",
            evidence=("test recovery",),
            compensator=lambda _operation, original: state.update(original)))

    with pytest.raises(direct_transaction.DirectTransactionFailed,
                       match="failed and was compensated"):
        direct_transaction.execute(
            decision=SimpleNamespace(mode="direct"),
            operations=[operation, {**operation, "frame": 13}],
            project_folder=str(tmp_path), transaction_id="uncertain-write",
            observe=lambda: {"markers": list(state["markers"])},
            apply=apply, verify=lambda *_args: None,
            same_state=lambda before, after: before == after)

    assert state == {"markers": []}
    assert [event["fields"]["stage"] for event in events][-1] == "committed"
    retried = direct_transaction.execute(
        decision=SimpleNamespace(mode="direct"),
        operations=[operation, {**operation, "frame": 13}],
        project_folder=str(tmp_path), transaction_id="uncertain-write",
        observe=lambda: {"markers": list(state["markers"])},
        apply=apply, verify=lambda *_args: None,
        same_state=lambda before, after: before == after)
    assert retried["status"] == "compensated"
    assert retried["idempotent"] is True
    assert len(calls) == 2


def test_direct_transaction_prepared_journal_failure_prevents_first_write(
        monkeypatch, tmp_path):
    """Defect: a missing durable recovery record must block mutation."""
    from library.tools import event_log

    writes = []
    monkeypatch.setattr(event_log, "read_events", lambda _project: [])
    monkeypatch.setattr(event_log, "emit", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(OSError("journal unavailable"))))
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held", lambda: True)
    monkeypatch.setattr(
        direct_transaction, "_qualification_for",
        lambda _op: direct_transaction.RecoveryQualification(
            operation="marker.add", inverse="marker.delete",
            evidence=("test recovery",), compensator=lambda *_args: None))

    with pytest.raises(OSError, match="journal unavailable"):
        direct_transaction.execute(
            decision=SimpleNamespace(mode="direct"),
            operations=[{"op": "marker.add", "frame": 12}],
            project_folder=str(tmp_path), transaction_id="no-prepared-record",
            observe=lambda: {"markers": []},
            apply=lambda op: writes.append(op), verify=lambda *_args: None,
            same_state=lambda before, after: before == after)

    assert writes == []


def test_interrupted_direct_transaction_refuses_as_unresolved_recovery(
        monkeypatch):
    from library.tools import event_log, transaction_receipt

    events = [{"fields": {
        "direct_transaction": True, "transaction_id": "worker-interrupted",
        "stage": "operation_applying", "index": 0,
        "operations": [{"op": "marker.add", "frame": 12}],
    }}]
    monkeypatch.setattr(event_log, "read_events", lambda _project: events)

    with pytest.raises(direct_transaction.DirectTransactionRefused,
                       match="operation_applying"):
        direct_transaction.read_transaction("/project", "worker-interrupted")

    interrupted = transaction_receipt.interrupted(events)
    assert interrupted[0]["operations"] == [{
        "index": 0, "op": "marker.add", "mutation_state": "uncertain"}]
