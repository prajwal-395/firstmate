"""LE-08: `ren build` ends with the per-reel digest and offers `--json`.

A build run on test doubles (stubbed capabilities, filed build
summaries - the reels runner fixture pattern from
`test_reels_runner_event_log.py`) ends with one digest line per reel
on stdout. With `--json`, the last stdout object parses and names
the `final` timeline. On main neither holds: the run prints its
per-capability lines to stderr and stdout stays empty.
"""
import contextlib
import json
from types import SimpleNamespace

from library.processes.reels import run_reels
from library.tools import reel_phase_log
from library.tools.event_log import EVENT_LOG_ENV


def _fixture_project(tmp_path):
    project = tmp_path / "show"
    project.mkdir()
    (project / "project.yaml").write_text(
        'name: "Test Show"\nslug: show\n', encoding="utf-8"
    )
    reel_phase_log.file_build_summary(
        str(project),
        1,
        "Reel 01 - alpha",
        reel_phase_log.assemble_summary(
            outcome=reel_phase_log.OUTCOME_PLACED,
            staging="Reel 01 - alpha (rebuild staging)",
            final="Reel 01 - alpha",
            decision="placed without verification",
        ),
    )
    reel_phase_log.file_build_summary(
        str(project),
        2,
        "Reel 02 - beta",
        reel_phase_log.assemble_summary(
            outcome=reel_phase_log.OUTCOME_PROMOTED,
            staging="Reel 02 - beta (rebuild staging)",
            final="Reel 02 - beta",
            decision="promoted",
            verify={"verdict": "verified", "passed": True},
        ),
    )
    return project


def _args(**overrides):
    base = {
        "skip_captions": False,
        "only_reel": None,
        "name_suffix": "",
        "allow_drop": None,
        "accept_editor_changes": None,
        "supersede": None,
        "retain": None,
        "rebuild_all": False,
        "placement_mode": "auto",
        "json": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _stub_run(monkeypatch):
    """The reels runner fixtures: no Resolve, no capabilities, no host env."""
    from library.tools import capabilities, project_format, resolve_lock

    monkeypatch.delenv(EVENT_LOG_ENV, raising=False)
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    monkeypatch.setattr(
        project_format, "ensure_project_format", lambda _folder: None)
    monkeypatch.setattr(capabilities, "run_order", lambda _process: [])
    monkeypatch.setattr(
        resolve_lock, "resolve_lease",
        lambda *args, **kwargs: contextlib.nullcontext())


def test_build_ends_with_one_digest_line_per_reel(tmp_path, monkeypatch,
                                                  capsys):
    project = _fixture_project(tmp_path)
    _stub_run(monkeypatch)

    assert run_reels.run(str(project), _args()) == 0

    lines = capsys.readouterr().out.splitlines()
    reel_lines = [line for line in lines if line.startswith("Reel ")]
    assert len(reel_lines) == 2
    assert "Reel 01 - alpha" in reel_lines[0]
    assert "placed_unverified" in reel_lines[0]
    assert "Reel 02 - beta" in reel_lines[1]
    assert "verified" in reel_lines[1]
    assert lines[-1].startswith("build: 2 reel(s), exit 0, log ")
    assert lines[-1].endswith("event_log.jsonl")


def test_build_json_tail_parses_and_names_the_final_timeline(
        tmp_path, monkeypatch, capsys):
    project = _fixture_project(tmp_path)
    _stub_run(monkeypatch)

    assert run_reels.run(str(project), _args(json=True)) == 0

    tail = capsys.readouterr().out.strip().splitlines()[-1]
    digest = json.loads(tail)
    assert digest["exit"] == 0
    assert digest["refusal_code"] is None
    assert digest["reel_count"] == 2
    assert [reel["final"] for reel in digest["reels"]] == [
        "Reel 01 - alpha", "Reel 02 - beta"]
    assert digest["reels"][0]["outcome"] == "placed_unverified"
    assert digest["reels"][1]["verify_verdict"] == "verified"
    assert digest["log"].endswith("event_log.jsonl")


def test_only_reel_scopes_the_digest(tmp_path, monkeypatch, capsys):
    project = _fixture_project(tmp_path)
    _stub_run(monkeypatch)

    assert run_reels.run(str(project), _args(only_reel=[2])) == 0

    lines = capsys.readouterr().out.splitlines()
    reel_lines = [line for line in lines if line.startswith("Reel ")]
    assert len(reel_lines) == 1
    assert "Reel 02 - beta" in reel_lines[0]


def test_a_refused_build_digest_carries_the_refusal_code(
        tmp_path, monkeypatch, capsys):
    from library.tools import (capabilities, operations, project_format,
                               provenance, resolve_lock)

    project = _fixture_project(tmp_path)
    monkeypatch.delenv(EVENT_LOG_ENV, raising=False)
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    monkeypatch.setattr(
        project_format, "ensure_project_format", lambda _folder: None)
    monkeypatch.setattr(capabilities, "run_order", lambda _process: [
        SimpleNamespace(id="reel.build")])

    def _busy(*args, **kwargs):
        raise resolve_lock.ResolveBusy("busy", "test", "retry")
    monkeypatch.setattr(resolve_lock, "resolve_lease", _busy)

    class BuildOperation:
        name = "reel.build"
        caller_supplied = False

        def execute(self, *_args, **_kwargs):
            with resolve_lock.resolve_lease("place Reel 01"):
                raise AssertionError("busy lease unexpectedly acquired")

    monkeypatch.setattr(operations, "get", lambda _id: BuildOperation())
    monkeypatch.setattr(provenance, "observing_operation",
                        lambda *_a, **_kw: contextlib.nullcontext())

    assert run_reels.run(str(project), _args(json=True)) == 4

    tail = capsys.readouterr().out.strip().splitlines()[-1]
    digest = json.loads(tail)
    assert digest["exit"] == 4
    assert digest["refusal_code"] == 4
    assert digest["reel_count"] == 2
