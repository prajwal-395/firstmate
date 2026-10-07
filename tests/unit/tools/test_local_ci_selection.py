"""The PR check selection: computed dependent tests plus the always-run set.

Defects caught: a PR that runs only its dependent selection and skips
the contract suite (the 34% global-invariant shape no filename
predicts), and a report line that cannot be pasted to reproduce the run.
"""

from library.tools.local_ci import config, hygiene, runner, selection


class _Proc:
    def __init__(self, cmd, code=0):
        self.returncode = code
        self.args = cmd


def _proc(cmd, code=0):
    return _Proc(cmd, code)


def _sequence(values):
    iterator = iter(values)
    return lambda: next(iterator)


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


def test_static_gate_compiles_only_files_the_checkout_has(monkeypatch,
                                                          tmp_path):
    # Throwaway #2083's static failure: a stale-base diff named
    # test_local_ci_github.py (added on main after the branch), absent
    # from the checkout - py_compile on the missing path failed the PR
    # for somebody else's file.
    import subprocess as real_subprocess
    (tmp_path / "here.py").write_text("OK = 1\n", encoding="utf-8")
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/bin/ruff")
    compiled = []

    def fake(cmd, **kwargs):
        if "ruff" in cmd[0]:
            return real_subprocess.CompletedProcess(cmd, 0, stdout="",
                                                    stderr="")
        if "-m" in cmd and "py_compile" in cmd:
            compiled.extend(cmd[cmd.index("py_compile") + 1:])
            return real_subprocess.CompletedProcess(cmd, 0, stdout="",
                                                    stderr="")
        return real_subprocess.CompletedProcess(cmd, 0, stdout="",
                                                stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake)
    result = runner.run_static(str(tmp_path), ["here.py", "gone.py"])
    assert result["ok"] is True
    assert compiled == ["here.py"]


def test_pytest_runs_only_paths_the_checkout_has(monkeypatch, tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "contracts").mkdir()
    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, False]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(runner.hygiene, "bind_renderer_dependencies",
                        lambda _checkout: None)
    seen = []

    def fake_run_logged(command, cwd, log_path, timeout):
        seen.extend(command)
        with open(log_path, "w", encoding="utf-8") as log:
            log.write("================ 1 passed in 1s ================\n")
        return {"exit": 0, "timed_out": False, "group_clean": True,
                "elapsed": 0.0}

    monkeypatch.setattr(runner, "_pytest_python", lambda: "python3")
    monkeypatch.setattr(runner, "_run_logged", fake_run_logged)
    result = runner.run(["tests/contracts", "tests/gone.py"],
                        str(tmp_path), str(tmp_path / "run.log"))
    assert result["ok"] is True
    assert "tests/gone.py" not in seen
    assert "tests/contracts" in seen
