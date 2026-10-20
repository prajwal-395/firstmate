"""One build is one run.

Every test here names the defect it catches. The reels process is the
product's own front door for a build, and a build whose capabilities
carry different run ids is a build no profile can read back.
"""

import contextlib
import os
from types import SimpleNamespace

from library.processes.reels import run_reels
from library.tools import perf_ledger
from library.tools import run_id as run_identity


def _args():
    return SimpleNamespace(
        skip_captions=False, only_reel=None, name_suffix="",
        allow_drop=None, accept_editor_changes=None, supersede=None,
        retain=None, rebuild_all=False, placement_mode="auto")


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "pipeline_output" / "logs").mkdir(parents=True)
    return project


def _prepare(monkeypatch, operations):
    from library.tools import capabilities, project_format, provenance
    from library.tools import operations as operations_module

    monkeypatch.setattr(project_format, "ensure_project_format",
                        lambda _folder: None)
    monkeypatch.setattr(capabilities, "run_order", lambda _process: [
        SimpleNamespace(id=name) for name in operations])
    monkeypatch.setattr(operations_module, "get",
                        lambda _id: operations[_id])
    monkeypatch.setattr(provenance, "observing_operation",
                        lambda *_a, **_kw: contextlib.nullcontext())
    monkeypatch.setattr(run_reels, "record_project_output",
                        lambda *_a, **_kw: None)
    monkeypatch.setattr(run_reels, "commit_run_tail", lambda *_a: None)


def _operation(seen):
    class Op:
        caller_supplied = False

        def __init__(self, name):
            self.name = name

        def execute(self, *_args, **_kwargs):
            seen.append(os.environ.get(run_identity.ENV_VAR))
            return SimpleNamespace(payload={}, status="ok", refused=False,
                                   error="")
    return Op


def test_every_capability_of_one_build_carries_the_same_run_id(
        tmp_path, monkeypatch):
    """The capabilities of one build share one run id.

    Defect caught: every capability minting its own run id, so one
    build is scattered across a dozen runs in the ledger and no
    profile can say what the build cost.
    """
    project = _project(tmp_path)
    seen: list = []
    operations = {"reel.build": _operation(seen)("reel.build"),
                  "reel.ask": _operation(seen)("reel.ask")}
    _prepare(monkeypatch, operations)
    monkeypatch.delenv(run_identity.ENV_VAR, raising=False)

    assert run_reels.run(str(project), _args()) == 0

    assert len(seen) == 2
    assert all(run_id for run_id in seen)
    assert len(set(seen)) == 1


def test_the_build_records_the_task_the_person_waited_for(
        tmp_path, monkeypatch):
    """The build leaves a task row: what was asked, and how long it took.

    Defect caught: no row for the request itself, so the profile can
    only report tool durations and the wait between them reads as
    nothing.
    """
    project = _project(tmp_path)
    seen: list = []
    operations = {"reel.build": _operation(seen)("reel.build")}
    _prepare(monkeypatch, operations)
    monkeypatch.delenv(run_identity.ENV_VAR, raising=False)

    assert run_reels.run(str(project), _args()) == 0

    rows = perf_ledger.read_rows(str(project))
    tasks = [row for row in rows if row.get("kind") == perf_ledger.TASK]
    assert [row["state"] for row in tasks] == ["requested", "completed"]
    assert tasks[0]["task_class"] == "reels.build"
    assert tasks[1]["latency_s"] >= 0.0
    assert tasks[1]["run_id"] == seen[0]
