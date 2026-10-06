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


def test_canva_status_reports_the_declaration_without_a_driver(capsys):
    """`status --target canva` needs no driver: it prints the window
    match, the gate and the background capability - a status that
    spawned the Resolve driver for a target it cannot see would be a
    lie about what was read."""
    assert main_module.main(["status", "--target", "canva"]) == 0
    out = capsys.readouterr().out
    assert "com.google.Chrome" in out
    assert "background input: no" in out
    assert "scratch designs allowlisted" in out


def test_canva_run_refuses_before_any_driver_spawns(monkeypatch, capsys):
    """`run --target canva` refuses with the missing-executor fix and
    never constructs a driver - spawning Resolve's driver for a Canva
    task would act on the wrong app."""
    def _boom(*args, **kwargs):
        raise AssertionError("no driver may spawn for a refused target")

    monkeypatch.setattr(main_module, "DriverClient", _boom)

    assert main_module.main(["run", "edit this design", "--target", "canva"]) == 4
    err = capsys.readouterr().err
    assert "no executor" in err
    assert "--target resolve" in err


def test_run_takes_a_plain_lease_then_borrows(monkeypatch, capsys):
    """`run` takes the lease WITHOUT the broker's qualification
    interlock and then borrows: the interlock grants only when Ren
    Qualification is already open, so passing it refuses before
    `borrowed()` can open the project whenever the captain's project
    is open (measured live 2026-10-06) - the verb could never start."""
    _stub_resolve(monkeypatch)
    lease_kwargs: list = []
    borrowed_with: list = []

    import library.tools.resolve_lock as lock_module
    import library.tools.qualification_project as qual_module

    def recording_lease(purpose, **kw):
        lease_kwargs.append((purpose, kw))
        return nullcontext()

    def recording_borrowed(resolve):
        borrowed_with.append(resolve)
        return nullcontext()

    monkeypatch.setattr(lock_module, "resolve_lease", recording_lease)
    monkeypatch.setattr(qual_module, "borrowed", recording_borrowed)
    monkeypatch.setattr(
        main_module, "run_loop", lambda client, model, goal: LoopResult(outcome="done")
    )

    assert main_module.main(["run", "switch pages"]) == 0
    assert lease_kwargs == [("computer-use", {})]
    assert len(borrowed_with) == 1
