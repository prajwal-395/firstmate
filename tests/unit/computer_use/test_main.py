"""The `ren computer-use run` wiring, pinned against stubbed Resolve.

The model module is only shipped if the verb actually calls it: these
tests catch the flags being parsed and ignored - `--model api` running
the stub, or `--max-cost-usd` never reaching the session.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext

from library.tools.computer_use import __main__ as main_module
from library.tools.computer_use.loop import LoopResult


class _FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_resolve(monkeypatch):
    monkeypatch.setattr(main_module, "DriverClient", lambda: _FakeClient())
    monkeypatch.setattr(
        "library.tools.marker_feedback.connect_resolve", lambda: object()
    )
    monkeypatch.setattr(
        "library.tools.resolve_lock.resolve_lease",
        lambda purpose, **kw: nullcontext(),
    )
    monkeypatch.setattr(
        "library.tools.qualification_project.borrowed",
        lambda resolve: nullcontext(),
    )


def test_stub_model_returns_done_without_a_session(monkeypatch, capsys):
    """The default `--model stub` runs the loop with the done-stub, not
    a `ModelSession` - offline runs make no HTTP call."""
    _stub_resolve(monkeypatch)
    seen: list = []
    monkeypatch.setattr(
        main_module, "run_loop", lambda client, model, goal: seen.append(model) or LoopResult(outcome="done")
    )

    assert main_module.main(["run", "switch pages"]) == 0
    assert seen[0]({}) == {"action": "done"}
    assert "outcome: done" in capsys.readouterr().out


def test_api_model_builds_a_session_with_the_cost_flag(monkeypatch, capsys):
    """`--model api --max-cost-usd 0.5` builds a `ModelSession` whose
    config carries the flag - a parsed-but-ignored flag would run the
    stub's unlimited spend."""
    _stub_resolve(monkeypatch)
    built: list = []
    monkeypatch.setattr(
        main_module, "run_loop", lambda client, model, goal: LoopResult(outcome="done")
    )

    def recording_session(config):
        built.append(config)
        return lambda obs: {"action": "done"}

    monkeypatch.setattr(main_module, "ModelSession", recording_session)

    assert main_module.main(["run", "goal", "--model", "api", "--max-cost-usd", "0.5"]) == 0
    assert built[0].max_cost_usd == 0.5
    assert built[0].endpoint.startswith("http")
