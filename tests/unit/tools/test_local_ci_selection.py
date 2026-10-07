"""The PR check selection: computed dependent tests plus the always-run set.

Defects caught: a PR that runs only its dependent selection and skips
the contract suite (the 34% global-invariant shape no filename
predicts), and a report line that cannot be pasted to reproduce the run.
"""

from library.tools.local_ci import config, runner, selection


def test_selection_always_contains_the_cross_cutting_set():
    paths = selection.selection_for(["library/tools/beat_grid.py"])
    for entry in config.ALWAYS_RUN:
        assert entry in paths, f"always-run {entry} missing from selection"


def test_selection_is_empty_of_duplicates():
    paths = selection.selection_for(["library/tools/beat_grid.py"])
    assert len(paths) == len(set(paths))


def test_an_empty_diff_still_runs_the_always_run_set():
    # "No selection" is never "no check": the contracts always run.
    assert selection.selection_for([]) == list(config.ALWAYS_RUN)


def test_pytest_argv_excludes_markers_and_drive_live_resolve():
    argv = runner.pytest_argv(["tests/contracts"])
    assert "not real_model and not resolve_live" in argv
    assert "-n" in argv and "2" in argv
    for driven in config.RESOLVE_DRIVING:
        assert driven in argv


def test_report_line_is_the_brief_contract():
    line = runner.format_report("https://example/pr/1", "abc123", 18, 2,
                                "python3 scripts/select_dependent_tests.py x")
    assert line == ("PR https://example/pr/1 head abc123 - 18 passed, "
                    "2 failed, selection "
                    "python3 scripts/select_dependent_tests.py x")


def test_static_gate_without_ruff_fails_closed_not_skipped(monkeypatch,
                                                           tmp_path):
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    result = runner.run_static(str(tmp_path), [])
    assert result["ok"] is False
    assert "ruff" in result["reason"]


def test_static_gate_runs_ruff_first_and_stops_there(monkeypatch, tmp_path):
    import subprocess as real_subprocess
    calls = []
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/bin/ruff")

    def fake(cmd, **kwargs):
        calls.append(cmd)
        return real_subprocess.CompletedProcess(cmd, 1, stdout="E501 bad",
                                                stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake)
    result = runner.run_static(str(tmp_path), ["a.py"])
    assert result["ok"] is False
    assert "ruff gate failed" in result["reason"]
    assert len(calls) == 1  # compile and size gates never ran
