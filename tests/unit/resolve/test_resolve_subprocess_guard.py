"""An isolated test broker socket is not an isolated scripting connection."""
import sys
from types import ModuleType

import pytest

from library.tools import resolve_locale, resolve_lock


def test_test_broker_cannot_handshake_with_real_module(monkeypatch):
    calls = []
    module = ModuleType("fusionscript")
    module.scriptapp = lambda name: calls.append(name) or "connected"
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test broker child")
    monkeypatch.delenv("REN_LIVE_RESOLVE", raising=False)
    monkeypatch.setattr(resolve_lock, "held", lambda: True)
    monkeypatch.setattr(resolve_locale, "direct_connect_refused", lambda: None)
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript", module)
    with pytest.raises(RuntimeError, match="test subprocess"):
        resolve_locale.load_resolve_script()
    with pytest.raises(RuntimeError, match="test subprocess"):
        resolve_locale.scriptapp_preserving_locale(module)
    assert calls == []
    # The explicit live fixture opt-in keeps its existing path.
    monkeypatch.setenv("REN_LIVE_RESOLVE", "1")
    assert resolve_locale.scriptapp_preserving_locale(module) == "connected"
    assert calls == ["Resolve"]


def test_test_scripting_double_remains_usable(monkeypatch):
    module = ModuleType("test_scripting_double")
    module.scriptapp = lambda name: "fake resolve"
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test broker child")
    monkeypatch.delenv("REN_LIVE_RESOLVE", raising=False)
    monkeypatch.setattr(resolve_lock, "held", lambda: True)
    monkeypatch.setattr(resolve_locale, "direct_connect_refused", lambda: None)
    assert resolve_locale.scriptapp_preserving_locale(module) == "fake resolve"
