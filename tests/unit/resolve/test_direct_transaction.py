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
