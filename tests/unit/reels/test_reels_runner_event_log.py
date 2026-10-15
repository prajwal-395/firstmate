"""The reels runner scopes its event log without owning Resolve exclusion."""
import contextlib
import os
from types import SimpleNamespace

from library.processes.reels import run_reels
from library.tools import event_log
from library.tools.event_log import EVENT_LOG_ENV, log_path


def _args():
    return SimpleNamespace(
        skip_captions=False, only_reel=None, name_suffix="",
        allow_drop=None, accept_editor_changes=None, supersede=None,
        retain=None, rebuild_all=False, placement_mode="auto")


def test_the_run_scopes_the_log_over_its_lease(tmp_path, monkeypatch):
    """The build's lease is taken with this project's log scoped.

    Defect caught: the lease fires its events into whatever
    `REN_EVENT_LOG` happens to hold - another project's log, or
    nowhere - so the build-reels run is missing from the operation
    history.
    """
    from library.tools import capabilities, project_format
    project = tmp_path / "project"
    (project / "pipeline_output" / "logs").mkdir(parents=True)
    monkeypatch.delenv(EVENT_LOG_ENV, raising=False)
    monkeypatch.setattr(project_format, "ensure_project_format",
                        lambda _folder: None)
    monkeypatch.setattr(capabilities, "run_order", lambda _process: [])
    seen = {}

    assert run_reels.run(str(project), _args()) == 0
    assert EVENT_LOG_ENV not in os.environ


def test_a_refused_run_restores_the_log(tmp_path, monkeypatch, capsys):
    """A refusal unwinds through the scope: the log leaks nowhere.

    Defect caught: a scope restored only on the success path leaves
    the dead run's log behind after a refusal - the next run's lease
    events land in the wrong project.
    """
    from library.tools import capabilities, operations, project_format, provenance
    project = tmp_path / "project"
    (project / "pipeline_output" / "logs").mkdir(parents=True)
    monkeypatch.delenv(EVENT_LOG_ENV, raising=False)
    monkeypatch.setattr(project_format, "ensure_project_format",
                        lambda _folder: None)

    class BusyOperation:
        name = "reel.build"
        caller_supplied = False

        def execute(self, *_args, **_kwargs):
            from library.tools.resolve_lock import ResolveBusy
            raise ResolveBusy("busy", "test", "retry")

    monkeypatch.setattr(capabilities, "run_order", lambda _p: [
        type("Spec", (), {"id": "reel.build"})()])
    monkeypatch.setattr(operations, "get", lambda _id: BusyOperation())
    monkeypatch.setattr(provenance, "observing_operation",
                        lambda *_a, **_kw: contextlib.nullcontext())
    monkeypatch.setattr(run_reels, "record_project_output", lambda *_a,
                        **_kw: None)
    monkeypatch.setattr(run_reels, "commit_run_tail", lambda *_a: None)

    assert run_reels.run(str(project), _args()) == 4
    assert EVENT_LOG_ENV not in os.environ
    assert event_log.log_path(str(project)).name == "event_log.jsonl"


def test_preparation_and_render_finish_before_placement_lease(
        tmp_path, monkeypatch):
    """The process runner does not extend Resolve exclusion over prep/render."""
    from types import SimpleNamespace
    from library.tools import capabilities, operations, project_format, provenance
    from library.tools import resolve_lock

    project = tmp_path / "project"
    (project / "pipeline_output" / "logs").mkdir(parents=True)
    monkeypatch.setattr(project_format, "ensure_project_format",
                        lambda _folder: None)
    monkeypatch.setattr(capabilities, "run_order", lambda _process: [
        SimpleNamespace(id="reel.build")])
    events = []

    @contextlib.contextmanager
    def lease(name, **_kwargs):
        events.append(("lease-enter", name))
        yield
        events.append(("lease-exit", name))

    class BuildOperation:
        name = "reel.build"
        caller_supplied = False

        def execute(self, *_args, **_kwargs):
            events.append(("prepare", "media and graphics"))
            events.append(("render", "graphics"))
            with resolve_lock.resolve_lease("place Reel 01"):
                events.append(("place", "Reel 01"))
                events.append(("readback", "Reel 01"))
            return SimpleNamespace(refused=False, error="",
                                   status="completed", payload={})

    monkeypatch.setattr(operations, "get", lambda _id: BuildOperation())
    monkeypatch.setattr(resolve_lock, "resolve_lease", lease)
    monkeypatch.setattr(provenance, "observing_operation",
                        lambda *_a, **_kw: contextlib.nullcontext())
    monkeypatch.setattr(run_reels, "record_project_output", lambda *_a,
                        **_kw: None)
    monkeypatch.setattr(run_reels, "commit_run_tail", lambda *_a: None)

    assert run_reels.run(str(project), _args()) == 0
    assert events == [
        ("prepare", "media and graphics"), ("render", "graphics"),
        ("lease-enter", "place Reel 01"), ("place", "Reel 01"),
        ("readback", "Reel 01"), ("lease-exit", "place Reel 01"),
    ]
