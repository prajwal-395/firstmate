"""The reels runner scopes the event log over its whole run.

The defect: `run_reels.run` takes the exclusive instance lease, but
`REN_EVENT_LOG` is never set there, so the lease's acquired/released
hook never fires during a build-reels run - the same gap the edit
runner closed per step. Every test names a defect it catches: on
origin/main the lease is taken with no log scoped, so the build's
fence leaves no event.
"""
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
    from library.tools import capabilities, project_format, resolve_lock
    project = tmp_path / "project"
    (project / "pipeline_output" / "logs").mkdir(parents=True)
    monkeypatch.delenv(EVENT_LOG_ENV, raising=False)
    monkeypatch.setattr(project_format, "ensure_project_format",
                        lambda _folder: None)
    monkeypatch.setattr(capabilities, "run_order", lambda _process: [])
    seen = {}

    def _lease(name, **kwargs):
        seen["log"] = os.environ.get(EVENT_LOG_ENV)
        return contextlib.nullcontext()
    monkeypatch.setattr(resolve_lock, "resolve_lease", _lease)

    assert run_reels.run(str(project), _args()) == 0
    assert seen["log"] == str(log_path(str(project)))
    assert EVENT_LOG_ENV not in os.environ


def test_a_refused_run_restores_the_log(tmp_path, monkeypatch, capsys):
    """A refusal unwinds through the scope: the log leaks nowhere.

    Defect caught: a scope restored only on the success path leaves
    the dead run's log behind after a refusal - the next run's lease
    events land in the wrong project.
    """
    from library.tools import project_format, resolve_lock
    project = tmp_path / "project"
    (project / "pipeline_output" / "logs").mkdir(parents=True)
    monkeypatch.delenv(EVENT_LOG_ENV, raising=False)
    monkeypatch.setattr(project_format, "ensure_project_format",
                        lambda _folder: None)

    def _busy(name, **kwargs):
        raise resolve_lock.ResolveBusy("busy", "test", "retry")
    monkeypatch.setattr(resolve_lock, "resolve_lease", _busy)

    assert run_reels.run(str(project), _args()) == 4
    assert EVENT_LOG_ENV not in os.environ
    assert event_log.log_path(str(project)).name == "event_log.jsonl"
