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
        main_module, "run_loop", lambda client, model, goal, **kw: seen.append(model) or LoopResult(outcome="done")
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
        main_module, "run_loop", lambda client, model, goal, **kw: LoopResult(outcome="done")
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


def test_canva_run_skips_resolve_machinery(monkeypatch, capsys):
    """`run --target canva` drives the Chrome window without touching
    Resolve: no lease, no borrow, no connect - a Canva run that borrowed
    the captain's project would displace his work for a job that never
    touches Resolve. The loop receives the chrome driver target, and the
    process is pinned by the Canva window title - never guessed among
    the captain's windows."""
    def _boom(*args, **kwargs):
        raise AssertionError("Resolve machinery must not run for a canva task")

    monkeypatch.setattr(main_module, "DriverClient", lambda: _FakeClient())
    monkeypatch.setattr("library.tools.marker_feedback.connect_resolve", _boom)
    monkeypatch.setattr("library.tools.resolve_lock.resolve_lease", _boom)
    monkeypatch.setattr("library.tools.qualification_project.borrowed", _boom)
    seen: list = []
    monkeypatch.setattr(
        main_module,
        "run_loop",
        lambda client, model, goal, **kw: seen.append(kw) or LoopResult(outcome="done"),
    )

    import library.tools.computer_use.__main__ as main_mod
    monkeypatch.setattr(main_mod, "_canva_window_pid", lambda client, name: 4242)

    assert main_module.main(["run", "edit this design", "--target", "canva"]) == 0
    assert seen == [{"target": "chrome", "pid": 4242}]
    assert "outcome: done" in capsys.readouterr().out


def test_canva_run_refuses_with_no_canva_window(monkeypatch, capsys):
    """Without a Canva window open the run refuses naming what to open -
    the loop never picks among the captain's windows, and an empty match
    never defaults to one."""
    monkeypatch.setattr(main_module, "DriverClient", lambda: _FakeClient())
    import library.tools.computer_use.__main__ as main_mod
    from library.tools.computer_use.targets import TargetError

    def _nowhere(client, name):
        raise TargetError("none", "none", "none", code="TARGET_NOT_OPEN")

    monkeypatch.setattr(main_mod, "_canva_window_pid", _nowhere)
    monkeypatch.setattr(
        main_module,
        "run_loop",
        lambda client, model, goal, **kw: LoopResult(outcome="done"),
    )

    assert main_module.main(["run", "edit this design", "--target", "canva"]) == 4
    assert "no executor" not in capsys.readouterr().err


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
        main_module, "run_loop", lambda client, model, goal, **kw: LoopResult(outcome="done")
    )

    assert main_module.main(["run", "switch pages"]) == 0
    assert len(lease_kwargs) == 1
    purpose, kwargs = lease_kwargs[0]
    assert purpose.startswith("computer-use run (take, ")
    assert kwargs.get("exclusive") is True
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

    def recording_run(client, script, llm=None, **kw):
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


def test_canva_window_pid_picks_the_matching_window():
    """Window selection matches the target's title substring and pins
    the owning process - with the captain's windows in the list, only
    the Canva one may be picked, largest wins among several."""
    import pytest

    from library.tools.computer_use.targets import TargetError

    class _C:
        def __init__(self, windows):
            self._windows = windows

        def call(self, method, params=None):
            assert method == "get_windows"
            assert params.get("target") == "chrome"
            return {"windows": self._windows}

    frame = {"x": 0, "y": 0, "width": 800, "height": 600}
    big = dict(frame)
    big.update({"width": 1200, "height": 900})
    client = _C([
        {"title": "Inbox", "owner_pid": 111, "frame": frame},
        {"title": "small canva draft", "owner_pid": 222, "frame": frame},
        {"title": "big Canva poster", "owner_pid": 333, "frame": big},
    ])
    assert main_module._canva_window_pid(client, "canva") == 333

    with pytest.raises(TargetError) as excinfo:
        main_module._canva_window_pid(_C([]), "canva")
    assert excinfo.value.code == "TARGET_NOT_OPEN"


def test_script_on_canva_refuses_before_any_driver_spawns(monkeypatch, capsys):
    """`script --target canva` refuses: deterministic anchors resolve
    against Resolve's window, and chrome anchors are unbuilt - running
    them against Chrome would act on unanchored coordinates. Spawning
    Resolve's driver for a refused target would be doubly wrong."""
    def _boom(*args, **kwargs):
        raise AssertionError("no driver may spawn for a refused target")

    monkeypatch.setattr(main_module, "DriverClient", _boom)

    assert main_module.main(
        ["script", "/no/such/script.json", "--target", "canva"]) == 4
    assert "drives Resolve anchors, not canva" in capsys.readouterr().err


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


class _FakeScriptClient:
    def __init__(self, scale=2.0):
        self.calls: list = []
        self.scale = scale

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def call(self, method, params=None):
        self.calls.append((method, dict(params or {})))
        if method == "get_windows":
            return {"windows": [
                {"title": "other", "backing_scale": 1.0},
                {"title": "DaVinci Resolve", "is_main": True,
                 "backing_scale": self.scale}]}
        return {}


class _FakeScriptTimeline:
    def __init__(self):
        self.timecode = "00:00:00:00"

    def GetName(self):
        return "Q Spine"

    def GetUniqueId(self):
        return "timeline-q-spine"

    def SetCurrentTimecode(self, timecode):
        self.timecode = timecode
        return True

    def GetCurrentTimecode(self):
        return self.timecode


class _FakeScriptProject:
    def __init__(self):
        self.current = _FakeScriptTimeline()

    def GetName(self):
        return "Ren Qualification"

    def GetTimelineCount(self):
        return 1

    def GetTimelineByIndex(self, index):
        return _FakeScriptTimeline()

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True


class _FakeScriptManager:
    def __init__(self):
        self._project = _FakeScriptProject()

    def LoadProject(self, name):
        self._project = _FakeScriptProject()
        return self._project

    def GetCurrentProject(self):
        return self._project


class _FakeScriptResolve:
    def GetProjectManager(self):
        return _FakeScriptManager()


_SEED_JSON = (
    '{"clip_id": "talk", "frame": 48, "subject": "speaker", '
    '"source": {"x": 1132.0, "y": 449.0}, '
    '"viewer": {"x": 1719.0, "y": 622.0}, '
    '"object_id": "obj_1", "sample_index": 2, "seed_frame": 48}')


def _stub_script(monkeypatch, outcome="done", scale=2.0):
    from library.tools.computer_use.scripts import ScriptResult

    runs: list = []
    leases: list = []
    clients: list = []
    clients.append(_FakeScriptClient(scale=scale))
    monkeypatch.setattr(main_module, "DriverClient", lambda: clients[0])
    monkeypatch.setattr(
        "library.tools.marker_feedback.connect_resolve",
        lambda: _FakeScriptResolve())

    def fake_lease(purpose, **kwargs):
        leases.append((purpose, kwargs))
        return nullcontext()

    monkeypatch.setattr(
        "library.tools.resolve_lock.resolve_lease", fake_lease)
    # The verb routes its cursor move through the guarded setter, so
    # the tests hold the (fake) lease the guard requires - the guard
    # itself is pinned by the lease tests, not here.
    monkeypatch.setattr(
        "library.tools.resolve_lock.held", lambda: True)
    monkeypatch.setattr(
        "library.tools.resolve_lock.exclusive_held", lambda: True)
    monkeypatch.setattr(
        "library.tools.qualification_project.borrowed",
        lambda resolve: nullcontext())

    def fake_run_script(client, script, **kwargs):
        runs.append((script, kwargs))
        return ScriptResult(outcome=outcome, steps=[
            {"step": 0, "action": "press", "outcome": "ok",
             "via": "ax_path"}])

    monkeypatch.setattr(main_module, "run_script", fake_run_script)
    return runs, clients, leases


def test_script_seed_runs_the_seed_recipe_offline(monkeypatch, capsys):
    """`script magic-mask-seed` builds the seed Script from the receipt
    JSON and runs it with no model and no foreground flag - the
    offline default never takes the captain's screen."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "magic-mask-seed", "--timeline", "Q Spine",
            "--seed", _SEED_JSON,
            "--playhead-timecode", "00:00:02:00"]
    assert main_module.main(argv) == 0
    script, kwargs = runs[0]
    assert kwargs["foreground"] is False
    assert kwargs["deadline"] > 0
    assert "obj_1" in script.goal
    assert [s.action for s in script.steps] == [
        "press", "menu", "click", "type", "click", "click", "click"]
    # The receipt's device pixels ride the click as window points:
    # (1719, 622) at the measured 2x scale clicks (859.5, 311).
    params = script.steps[6].anchors[0].params
    assert (params["x"], params["y"]) == (859.5, 311.0)
    out = capsys.readouterr().out
    assert "outcome: done" in out
    assert "backing scale: 2.0" in out


def test_script_seed_refuses_when_no_scale_is_measured(monkeypatch, capsys):
    """A run whose window reports no backing scale exits 1 before the
    script is built: a seed click with nowhere to convert to would
    land double on Retina."""
    runs, _, _ = _stub_script(monkeypatch, scale=None)
    argv = ["script", "--recipe", "magic-mask-seed", "--timeline", "Q Spine",
            "--seed", _SEED_JSON]
    assert main_module.main(argv) == 1
    assert runs == []
    assert "backing scale" in capsys.readouterr().err


def test_script_seed_open_builds_the_warm_route(monkeypatch, capsys):
    """`script magic-mask-seed-open` builds the short route (page,
    mode, click) for a palette the take already read open - no
    Library, search, type or apply steps that would re-apply the
    effect onto shared footage."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "magic-mask-seed-open",
            "--timeline", "Q Spine",
            "--seed", _SEED_JSON,
            "--playhead-timecode", "00:00:05:00"]
    assert main_module.main(argv) == 0
    script, kwargs = runs[0]
    assert kwargs["foreground"] is False
    assert [s.action for s in script.steps] == [
        "press", "click", "click"]
    assert "open Magic Mask" in script.goal
    params = script.steps[2].anchors[0].params
    assert (params["x"], params["y"]) == (859.5, 311.0)
    out = capsys.readouterr().out
    assert "outcome: done" in out


def test_script_seed_refuses_a_seed_it_cannot_parse(monkeypatch, capsys):
    """A `--seed` that is not a MaskSeed receipt exits 2 before Resolve
    is touched: no borrow, no timeline, no driver call can follow a
    seed nobody parsed."""
    runs, clients, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "magic-mask-seed", "--timeline", "Q Spine",
            "--seed", '{"viewer": {"x": 1}}']
    assert main_module.main(argv) == 2
    assert runs == [] and clients[0].calls == []
    assert "--seed" in capsys.readouterr().err


def test_script_track_selects_the_named_direction(monkeypatch, capsys):
    """`script magic-mask-track --direction backward` runs the tracker
    recipe whose click names Track Backward - a direction flag that
    never reached the builder would track the wrong way."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "magic-mask-track", "--timeline", "Q Spine",
            "--direction", "backward"]
    assert main_module.main(argv) == 0
    script, _ = runs[0]
    assert script.steps[0].anchors[0].params["text"] == "Track Backward"


def test_script_foreground_activates_and_flags_input(monkeypatch, capsys):
    """`--allow-foreground` activates Resolve first and runs the Script
    foreground - without both, a Qt-drawn click silently changes
    nothing while the run reports ok."""
    runs, clients, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "magic-mask-track", "--timeline", "Q Spine",
            "--direction", "forward", "--allow-foreground"]
    assert main_module.main(argv) == 0
    assert ("activate_resolve", {"allow_foreground": True}) in clients[0].calls
    _, kwargs = runs[0]
    assert kwargs["foreground"] is True
    assert kwargs["deadline"] > 0


def test_script_failed_run_exits_nonzero(monkeypatch, capsys):
    """A recipe whose step does not verify exits 1: the report names
    the outcome instead of passing silently."""
    _stub_script(monkeypatch, outcome="failed")
    argv = ["script", "--recipe", "magic-mask-track", "--timeline", "Q Spine"]
    assert main_module.main(argv) == 1
    assert "outcome: failed" in capsys.readouterr().out


def test_script_takes_a_plain_lease_it_borrows_under(monkeypatch, capsys):
    """The verb borrows Resolve and opens the qualification project
    itself, so a qualification-marked lease would be refused by the
    broker interlock while the captain's project is open - nearly
    always (found live 2026-10-06, SkyLight lane PR 1933)."""
    runs, _, leases = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "magic-mask-track", "--timeline", "Q Spine"]
    assert main_module.main(argv) == 0
    assert runs != []
    assert leases != []
    assert all("qualification_project" not in kwargs
               for _, kwargs in leases)


def test_script_effect_panels_opens_both_panels_offline(monkeypatch, capsys):
    """`script --recipe effect-panels` builds the three menu steps
    (Edit page, Effects panel, Inspector panel) and runs them with
    no model and no foreground flag - the mapping measured every
    step backgrounded, so the offline default must not take the
    captain's screen."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "effect-panels", "--timeline", "Q Spine"]
    assert main_module.main(argv) == 0
    script, kwargs = runs[0]
    assert kwargs["foreground"] is False
    assert kwargs["deadline"] > 0
    assert [s.action for s in script.steps] == ["menu"] * 3
    assert "Inspector" in script.steps[2].anchors[0].params["path"]
    out = capsys.readouterr().out
    assert "outcome: done" in out


def test_script_effect_panels_needs_a_timeline(monkeypatch, capsys):
    """Without --timeline the verb establishes no cursor: the panels
    would open over whatever timeline the captain left current."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "effect-panels"]
    assert main_module.main(argv) == 2
    assert runs == []
    assert "--timeline" in capsys.readouterr().err


def test_script_effect_panels_takes_no_seed(monkeypatch, capsys):
    """A --seed on the panels recipe is refused: opening panels
    reads no seed, and a seed nobody reads would run the wrong
    route while looking deliberate."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "effect-panels", "--timeline", "Q Spine",
            "--seed", "{}"]
    assert main_module.main(argv) == 2
    assert runs == []
    assert "--seed" in capsys.readouterr().err


def test_script_with_recipe_and_path_is_refused(monkeypatch, capsys):
    """A run takes a recipe or a file, never both - running both
    would act twice on the same Resolve."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "some.json", "--recipe", "magic-mask-track",
            "--timeline", "Q Spine"]
    assert main_module.main(argv) == 2
    assert runs == []
    assert "never both" in capsys.readouterr().err


def test_script_with_neither_recipe_nor_path_is_refused(monkeypatch, capsys):
    """A run with nothing to run exits 2 before Resolve is touched."""
    runs, _, _ = _stub_script(monkeypatch)
    assert main_module.main(["script"]) == 2
    assert runs == []


def test_script_file_with_recipe_flags_is_refused(monkeypatch, capsys):
    """Recipe flags on a file run are refused, not silently ignored -
    a --seed that never reached a builder would run the wrong route."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "some.json", "--seed", _SEED_JSON]
    assert main_module.main(argv) == 2
    assert runs == []
    assert "--recipe" in capsys.readouterr().err


def test_script_recipe_with_file_flags_is_refused(monkeypatch, capsys):
    """File flags on a recipe run are refused: --teach would promise
    to persist anchors no file receives, --model api a fallback the
    deterministic run never calls."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "magic-mask-track", "--timeline",
            "Q Spine", "--teach"]
    assert main_module.main(argv) == 2
    assert runs == []


def test_script_track_with_seed_is_refused(monkeypatch, capsys):
    """A --seed on the track recipe is refused: the tracker reads no
    seed, so one passed would be dropped in silence."""
    runs, _, _ = _stub_script(monkeypatch)
    argv = ["script", "--recipe", "magic-mask-track", "--timeline",
            "Q Spine", "--seed", _SEED_JSON]
    assert main_module.main(argv) == 2
    assert runs == []


def _record_lease_order(monkeypatch, order, resolve_obj=None):
    """Entry-recording lease and connect.

    The verb under test must enter the lease before connecting:
    resolve_locale refuses the Resolve handshake outside the lease, so
    the reverse order crashes every live run before any UI touches
    Resolve (found live 2026-10-07 - the shipped recipe verb died on
    its first live invocation). A test asserting
    `order.index("lease") < order.index("connect")` fails on that
    defect and passes on the fix."""
    from contextlib import contextmanager

    @contextmanager
    def recording_lease(purpose, **kw):
        order.append("lease")
        yield

    def recording_connect():
        order.append("connect")
        return resolve_obj if resolve_obj is not None else object()

    monkeypatch.setattr(
        "library.tools.resolve_lock.resolve_lease", recording_lease)
    monkeypatch.setattr(
        "library.tools.marker_feedback.connect_resolve", recording_connect)


def test_run_connects_inside_the_lease(monkeypatch, capsys):
    """`run` connected before leasing: the live handshake refusal
    crashed the verb before the loop ever observed."""
    _stub_resolve(monkeypatch)
    order: list = []
    _record_lease_order(monkeypatch, order)
    monkeypatch.setattr(
        main_module, "run_loop",
        lambda client, model, goal, **kw: LoopResult(outcome="done"),
    )
    assert main_module.main(["run", "switch pages"]) == 0
    assert order.index("lease") < order.index("connect")


def test_script_file_connects_inside_the_lease(monkeypatch, capsys, tmp_path):
    """`script` on a file connected before leasing: same crash, same
    gate - the deterministic run died before its first step."""
    _stub_resolve(monkeypatch)
    order: list = []
    _record_lease_order(monkeypatch, order)
    path = tmp_path / "media.json"
    path.write_text(
        '{"goal": "switch pages", "steps": [{"action": "press", '
        '"anchors": [{"via": "ax_path", "path": ["Workspace"]}]}]}',
        encoding="utf-8",
    )
    from library.tools.computer_use.scripts import ScriptResult

    monkeypatch.setattr(
        main_module, "run_script",
        lambda client, script, llm=None, **kw: ScriptResult(outcome="done", steps=[]),
    )
    assert main_module.main(["script", str(path)]) == 0
    assert order.index("lease") < order.index("connect")


def _record_take(monkeypatch, takes: list, deadline_value: float = 1234.0,
                 resolve_obj=None):
    """A ui_take double that records entry and yields a fixed deadline.
    A verb with a hand-rolled lease never calls it - the take it runs
    releases between steps."""
    from types import SimpleNamespace

    @contextmanager
    def recording_take(purpose, **kw):
        takes.append((purpose, kw))
        yield SimpleNamespace(
            resolve=resolve_obj if resolve_obj is not None else object(),
            deadline=deadline_value)

    monkeypatch.setattr(
        "library.tools.computer_use.take.ui_take", recording_take)


def test_run_goes_through_one_take_with_its_deadline(monkeypatch, capsys):
    """`run` enters one ui_take for the whole task and threads its
    deadline to the loop: a verb with a hand-rolled lease releases
    between steps, and a loop without the deadline runs past the
    take's wall-clock bound."""
    _stub_resolve(monkeypatch)
    takes: list = []
    _record_take(monkeypatch, takes)
    seen: list = []
    monkeypatch.setattr(
        main_module, "run_loop",
        lambda client, model, goal, **kw: seen.append(kw) or LoopResult(outcome="done"),
    )
    assert main_module.main(["run", "switch pages"]) == 0
    assert [purpose for purpose, _ in takes] == ["computer-use run"]
    assert seen[0]["deadline"] == 1234.0


def test_script_file_goes_through_one_take_with_its_deadline(
    monkeypatch, capsys, tmp_path
):
    """`script` on a file enters one ui_take and threads its deadline
    to the runner: same gate as `run`, for the deterministic path."""
    _stub_resolve(monkeypatch)
    takes: list = []
    _record_take(monkeypatch, takes)
    path = tmp_path / "media.json"
    path.write_text(
        '{"goal": "switch pages", "steps": [{"action": "press", '
        '"anchors": [{"via": "ax_path", "path": ["Workspace"]}]}]}',
        encoding="utf-8",
    )
    from library.tools.computer_use.scripts import ScriptResult

    seen: list = []
    monkeypatch.setattr(
        main_module, "run_script",
        lambda client, script, **kw: seen.append(kw) or ScriptResult(outcome="done", steps=[]),
    )
    assert main_module.main(["script", str(path)]) == 0
    assert [purpose for purpose, _ in takes] == ["computer-use script file"]
    assert seen[0]["deadline"] == 1234.0


def test_script_recipe_connects_inside_the_lease(monkeypatch, capsys):
    """`script --recipe` connected before leasing: the seed take's
    verb crashed on its first live run before establishing anything."""
    _stub_script(monkeypatch)
    order: list = []
    _record_lease_order(monkeypatch, order, resolve_obj=_FakeScriptResolve())
    argv = ["script", "--recipe", "magic-mask-track", "--timeline", "Q Spine"]
    assert main_module.main(argv) == 0
    assert order.index("lease") < order.index("connect")


def test_script_recipe_goes_through_one_take_with_its_deadline(monkeypatch, capsys):
    """`script --recipe` enters one ui_take and threads its deadline:
    the mask, tracker and panel arms share the block, so one test pins
    them all."""
    runs, _, _ = _stub_script(monkeypatch)
    takes: list = []
    _record_take(monkeypatch, takes, resolve_obj=_FakeScriptResolve())
    argv = ["script", "--recipe", "magic-mask-track", "--timeline", "Q Spine"]
    assert main_module.main(argv) == 0
    assert [purpose for purpose, _ in takes] == [
        "computer-use script recipe magic-mask-track"]
    assert runs[0][1]["deadline"] == 1234.0
