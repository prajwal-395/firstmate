"""The `ren computer-use run` / `script` wiring, pinned against stubbed Resolve.

The model module is only shipped if the verb actually calls it: these
tests catch the flags being parsed and ignored - `--model api` running
the stub, or `--max-cost-usd` never reaching the session. The script
tests catch the advertised verb staying unwired: the registry has
promised `script` since PR 4, so a module that rejects it is the
defect.
"""

from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext

from library.tools.computer_use import __main__ as main_module
from library.tools.computer_use.loop import LoopResult


class _FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_resolve(monkeypatch, client=None):
    monkeypatch.setattr(
        main_module, "DriverClient", lambda: _FakeClient() if client is None else client
    )
    monkeypatch.setattr(
        "library.tools.marker_feedback.connect_resolve", lambda: object()
    )
    monkeypatch.setattr(
        "library.tools.resolve_lock.resolve_lease",
        lambda purpose, **kw: nullcontext(),
    )
    monkeypatch.setattr(
        "library.tools.computer_use.scripts.resolve_lease",
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


def test_script_runs_the_file_with_no_model(monkeypatch, capsys, tmp_path):
    """`script` executes the file's steps deterministically - the model
    module is never touched. A script verb that planned would spend
    inference on a route already verified deterministic."""
    _stub_resolve(monkeypatch)
    path = tmp_path / "media.json"
    path.write_text(
        '{"goal": "switch pages", "steps": [{"action": "press", '
        '"anchors": [{"via": "ax_path", "path": ["Workspace"]}]}]}',
        encoding="utf-8",
    )
    seen: list = []

    from library.tools.computer_use.scripts import ScriptResult

    def recording_run(client, script, llm=None):
        assert llm is None, "default stub model must not plan"
        seen.append(script.to_dict())
        return ScriptResult(outcome="done", steps=[
            {"step": 0, "action": "press", "outcome": "ok", "via": "ax_path"}])

    monkeypatch.setattr(main_module, "run_script", recording_run)

    assert main_module.main(["script", str(path)]) == 0
    assert seen[0]["goal"] == "switch pages"
    out = capsys.readouterr().out
    assert "outcome: done" in out
    assert "via ax_path" in out


def test_script_with_an_unreadable_file_is_refused(monkeypatch, capsys):
    """A missing script file is a typed refusal naming the file, not a
    traceback - the verb must say which path to fix."""
    _stub_resolve(monkeypatch)

    assert main_module.main(["script", "/no/such/script.json"]) == 4
    err = capsys.readouterr().err
    assert "/no/such/script.json" in err


def test_script_with_a_malformed_file_is_refused(monkeypatch, capsys, tmp_path):
    """A file that is not a script is refused before anything runs - a
    verb that ran it halfway would act on half a route."""
    _stub_resolve(monkeypatch)
    path = tmp_path / "broken.json"
    path.write_text(
        '{"goal": "switch pages", "steps": [{"action": "click", '
        '"anchors": [], "expect": {"via": "teleport"}}]}',
        encoding="utf-8",
    )

    assert main_module.main(["script", str(path)]) == 4
    assert "is not a script" in capsys.readouterr().err


def test_script_on_canva_refuses_before_any_driver_spawns(monkeypatch, capsys):
    """`script --target canva` refuses like run does - there is no
    Chrome executor, and spawning Resolve's driver for a Canva task
    would act on the wrong app."""
    def _boom(*args, **kwargs):
        raise AssertionError("no driver may spawn for a refused target")

    monkeypatch.setattr(main_module, "DriverClient", _boom)

    assert main_module.main(
        ["script", "/no/such/script.json", "--target", "canva"]) == 4
    assert "no executor" in capsys.readouterr().err


def _write_script(tmp_path, steps):
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps({"goal": "do the thing", "steps": steps}), encoding="utf-8"
    )
    return path


def test_script_verb_runs_a_deterministic_file(client, monkeypatch, tmp_path, capsys):
    """The advertised `script` verb runs a file through the real runner
    against the mock driver - a module that rejects its own advertised
    subcommand is the defect this catches."""
    _stub_resolve(monkeypatch, client)
    path = _write_script(
        tmp_path,
        [
            {
                "action": "open",
                "anchors": [{"via": "ax_path", "params": {"path": ["Workspace"]}}],
                "expect": {"via": "pixels_changed"},
            }
        ],
    )

    assert main_module.main(["script", str(path)]) == 0
    assert "outcome: done" in capsys.readouterr().out


def test_script_verb_refuses_a_malformed_file(monkeypatch, tmp_path, capsys):
    """A file that is not JSON refuses naming the file - user input
    wrong is a refusal with a fix, never a traceback."""
    _stub_resolve(monkeypatch)
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    assert main_module.main(["script", str(path)]) == 4
    err = capsys.readouterr().err
    assert "broken.json" in err
    assert "not JSON" in err


def test_teach_saves_the_llms_anchor(client, monkeypatch, tmp_path, capsys):
    """With `--teach`, the anchor the fallback LLM taught is written
    back to the file - without persistence the teaching evaporates and
    every run pays the model again. The session sees the failed step in
    the observation history."""
    _stub_resolve(monkeypatch, client)
    seen: list = []

    class _StubSession:
        def __init__(self, config):
            self.config = config

        def __call__(self, obs):
            seen.append(obs)
            return {"action": "click", "x": 100.0, "y": 100.0}

    monkeypatch.setattr(main_module, "ModelSession", _StubSession)
    path = _write_script(
        tmp_path,
        [
            {
                "action": "click",
                "anchors": [{"via": "ocr", "params": {"text": "Not On Screen"}}],
                "expect": {"via": "pixels_changed"},
            }
        ],
    )

    assert main_module.main(["script", str(path), "--model", "api", "--teach"]) == 0
    assert "saved to" in capsys.readouterr().out
    taught = json.loads(path.read_text(encoding="utf-8"))
    anchors = taught["steps"][0]["anchors"]
    assert {"via": "position", "params": {"x": 100.0, "y": 100.0, "why": "llm"}} in anchors
    assert seen[0].history[-1] == {
        "action": "script:click",
        "outcome": "no anchor resolved",
    }


def test_learned_anchors_evaporate_without_teach(client, monkeypatch, tmp_path, capsys):
    """Without `--teach` the file is untouched and the verb says which
    flag would keep the learned anchors - persistence is opt-in, never
    a surprise rewrite of the caller's file."""
    _stub_resolve(monkeypatch, client)

    class _StubSession:
        def __init__(self, config):
            pass

        def __call__(self, obs):
            return {"action": "click", "x": 100.0, "y": 100.0}

    monkeypatch.setattr(main_module, "ModelSession", _StubSession)
    path = _write_script(
        tmp_path,
        [
            {
                "action": "click",
                "anchors": [{"via": "ocr", "params": {"text": "Not On Screen"}}],
                "expect": {"via": "pixels_changed"},
            }
        ],
    )
    before = path.read_text(encoding="utf-8")

    assert main_module.main(["script", str(path), "--model", "api"]) == 0
    assert "--teach" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == before
