"""Resolve scriptapp handshakes are reached only under the instance lease."""

import pytest

from library.tools import resolve_lock
from library.tools.resolve_locale import scriptapp_preserving_locale


def test_scriptapp_refuses_before_connect_when_no_lease_is_held(
        tmp_path, monkeypatch):
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    monkeypatch.setattr(resolve_lock, "_depth", 0)
    monkeypatch.setattr(resolve_lock, "_mode", None)
    calls = []

    class ResolveModule:
        def scriptapp(self, name):
            calls.append(name)
            return object()

    with pytest.raises(RuntimeError, match="outside the instance lease"):
        scriptapp_preserving_locale(ResolveModule())
    assert calls == []

    connected = object()
    module = ResolveModule()
    module.scriptapp = lambda name: calls.append(name) or connected
    with resolve_lock.resolve_lease(
            "test scriptapp boundary", exclusive=False, timeout=1.0):
        assert scriptapp_preserving_locale(module) is connected
    assert calls == ["Resolve"]
