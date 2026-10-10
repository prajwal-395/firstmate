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


def test_a_hub_selection_fills_the_time_budget_by_priority(
        monkeypatch, tmp_path):
    # PRs 2271/2273 ran 3,500-4,977 tests in Tier-1 because the
    # dependent selection widens for widely imported modules. The
    # time budget keeps the highest-priority tests - direct first,
    # then by import distance - and leaves breadth to the Tier-2
    # batched full suite.
    distances = {"tests/unit/x/test_hub.py": 0,
                 **{f"tests/unit/x/test_near_{n}.py": 1
                    for n in range(4)},
                 **{f"tests/unit/x/test_far_{n}.py": 2
                    for n in range(4)}}
    durations = {"tests/unit/x/test_hub.py": 5.0,
                 **{f"tests/unit/x/test_near_{n}.py": 5.0
                    for n in range(4)},
                 **{f"tests/unit/x/test_far_{n}.py": 5.0
                    for n in range(4)}}
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: ["tests/unit/x/test_hub.py"])
    result = selection.capped_selection_for(
        ["library/hub.py"], str(tmp_path), budget_seconds=15.0,
        durations=durations, failure_rates={})
    assert result["capped"] is True
    assert result["dependent_total"] == 9
    assert result["dependent_kept"] == 3
    assert result["deferred"] == 6
    assert "tests/unit/x/test_hub.py" in result["paths"]
    assert "tests/unit/x/test_near_0.py" in result["paths"]
    assert "tests/unit/x/test_far_0.py" not in result["paths"]
    assert result["estimated_seconds"] == 15.0
    for entry in config.ALWAYS_RUN:
        assert entry in result["paths"]
    assert len(result["deferred_paths"]) == 6


def test_an_ordinary_selection_under_budget_is_not_capped(
        monkeypatch, tmp_path):
    distances = {"tests/unit/x/test_a.py": 1,
                 "tests/unit/y/test_b.py": 1}
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    result = selection.capped_selection_for(
        ["library/a.py"], str(tmp_path), budget_seconds=30.0,
        durations={"tests/unit/x/test_a.py": 1.0,
                   "tests/unit/y/test_b.py": 1.0},
        failure_rates={})
    assert result["capped"] is False
    assert result["deferred"] == 0
    assert "tests/unit/x/test_a.py" in result["paths"]
    assert "tests/unit/y/test_b.py" in result["paths"]


def test_unmeasured_files_cost_the_default_estimate(monkeypatch, tmp_path):
    # Unknown is never zero (zero would let unmeasured files bypass
    # the budget) and never infinite (which would starve new tests).
    distances = {f"tests/unit/x/test_{n}.py": 1 for n in range(4)}
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: [])
    result = selection.capped_selection_for(
        ["library/a.py"], str(tmp_path), budget_seconds=5.0,
        durations={}, failure_rates={})
    assert result["estimated_seconds"] == (
        2 * config.TIER1_UNKNOWN_FILE_ESTIMATE_SECONDS)
    assert result["dependent_kept"] == 2
    assert result["deferred"] == 2


def test_higher_failure_rate_wins_within_one_distance(
        monkeypatch, tmp_path):
    # At equal distance the historically failing test runs first:
    # a quiet test deferred to Tier-2 costs nothing, a flaky one
    # skipped from Tier-1 costs the fast signal.
    distances = {"tests/unit/x/test_flaky.py": 1,
                 "tests/unit/x/test_quiet.py": 1}
    durations = {"tests/unit/x/test_flaky.py": 5.0,
                 "tests/unit/x/test_quiet.py": 5.0}
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: [])
    result = selection.capped_selection_for(
        ["library/a.py"], str(tmp_path), budget_seconds=5.0,
        durations=durations,
        failure_rates={"tests/unit/x/test_flaky.py": 0.4})
    assert result["dependent_kept"] == 1
    assert "tests/unit/x/test_flaky.py" in result["paths"]
    assert result["deferred_paths"] == ["tests/unit/x/test_quiet.py"]


def test_direct_tests_run_even_when_they_alone_exceed_the_budget(
        monkeypatch, tmp_path):
    # The floor: a change always answers for its own tests, even when
    # they alone cost more than the budget. Everything else defers.
    distances = {"tests/unit/x/test_hub.py": 0,
                 "tests/unit/x/test_other.py": 1}
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: ["tests/unit/x/test_hub.py"])
    result = selection.capped_selection_for(
        ["library/hub.py"], str(tmp_path), budget_seconds=5.0,
        durations={"tests/unit/x/test_hub.py": 60.0,
                   "tests/unit/x/test_other.py": 1.0},
        failure_rates={})
    assert "tests/unit/x/test_hub.py" in result["paths"]
    assert "tests/unit/x/test_other.py" not in result["paths"]
    assert result["capped"] is True


def test_always_run_stays_mandatory_outside_the_budget(
        monkeypatch, tmp_path):
    # No filename predicts the cross-cutting contract failures, so the
    # budget must not eat them: they ride along whatever the dependent
    # half costs.
    distances = {"tests/unit/x/test_a.py": 1}
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: [])
    result = selection.capped_selection_for(
        ["library/a.py"], str(tmp_path), budget_seconds=1.0,
        durations={"tests/unit/x/test_a.py": 1.0}, failure_rates={})
    assert result["capped"] is False
    assert result["estimated_seconds"] == 1.0
    for entry in config.ALWAYS_RUN:
        assert entry in result["paths"]


def test_tier2_only_files_never_run_in_tier1(monkeypatch, tmp_path):
    # The 301s contract audit alone vetoes any small budget, so it and
    # the other slow files are Tier-2-only: excluded even when the
    # import graph selects them, and their cost never spends the
    # budget. Tier-2 runs them on the exact stacked tree.
    slow = list(config.TIER1_TIER2_ONLY)
    assert len(slow) >= 5
    distances = {path: 1 for path in slow}
    distances["tests/unit/x/test_fast.py"] = 1
    durations = {path: 200.0 for path in slow}
    durations["tests/unit/x/test_fast.py"] = 1.0
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: [])
    result = selection.capped_selection_for(
        ["library/hub.py"], str(tmp_path), budget_seconds=300.0,
        durations=durations, failure_rates={})
    for path in slow:
        assert path not in result["paths"], path
    assert sorted(result["tier2_only_deferred"]) == sorted(slow)
    assert result["tier2_only"] == sorted(slow)
    assert "tests/unit/x/test_fast.py" in result["paths"]
    assert result["capped"] is False
    assert result["estimated_seconds"] == 1.0


def test_tier2_only_beats_the_direct_floor(monkeypatch, tmp_path):
    # Even a change to the audit file itself does not run it in
    # Tier-1: the direct floor stops at the Tier-2 boundary.
    audit = "tests/contracts/test_contract_audit.py"
    assert audit in config.TIER1_TIER2_ONLY
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: {audit: 0})
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: [audit])
    result = selection.capped_selection_for(
        [audit], str(tmp_path), budget_seconds=300.0,
        durations={audit: 301.0}, failure_rates={})
    assert audit not in result["paths"]
    assert result["tier2_only_deferred"] == [audit]
    assert result["dependent_kept"] == 0


def test_unknown_files_cost_their_subsystem_median(monkeypatch, tmp_path):
    # A new resolve test costs what resolve tests cost - not zero
    # (which would bypass the budget) and not a flat guess when its
    # subsystem is measured.
    distances = {"tests/unit/resolve/test_new.py": 1,
                 "tests/unit/resolve/test_old.py": 1}
    durations = {"tests/unit/resolve/test_old.py": 10.0,
                 "tests/unit/resolve/test_older.py": 20.0,
                 "tests/scenarios/test_slow.py": 500.0}
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: [])
    result = selection.capped_selection_for(
        ["library/a.py"], str(tmp_path), budget_seconds=100.0,
        durations=durations, failure_rates={})
    # Subsystem median (10, 20) is 15, not the suite-wide median and
    # not the flat default: 15 + 10 spent, nothing deferred.
    assert result["capped"] is False
    assert result["estimated_seconds"] == 25.0


def test_unknown_files_fall_back_to_the_flat_estimate(
        monkeypatch, tmp_path):
    # Cold telemetry (nothing measured anywhere) still prices unknown
    # files at the configured default, never zero.
    distances = {"tests/unit/x/test_new.py": 1}
    monkeypatch.setattr(selection, "dependent_distances",
                        lambda paths, repo: dict(distances))
    monkeypatch.setattr(selection, "direct_tests_for",
                        lambda paths, repo: [])
    result = selection.capped_selection_for(
        ["library/a.py"], str(tmp_path), budget_seconds=100.0,
        durations={}, failure_rates={})
    assert result["estimated_seconds"] == (
        config.TIER1_UNKNOWN_FILE_ESTIMATE_SECONDS)


def test_pytest_argv_ignores_the_tier2_only_files(tmp_path):
    # The always-run `tests/contracts` directory would re-admit the
    # audit through directory expansion without the ignore.
    argv = runner.pytest_argv(["tests/contracts"])
    for owned in config.TIER1_TIER2_ONLY:
        assert owned in argv
        assert argv[argv.index(owned) - 1] == "--ignore"


def test_pytest_argv_skips_ignores_a_checkout_deleted(
        tmp_path):
    # `--ignore` on a path the PR deleted fails the run for somebody
    # else's deletion, so checkout-aware ignores skip missing files.
    gone = config.TIER1_TIER2_ONLY[0]
    assert not (tmp_path / gone).exists()
    argv = runner.pytest_argv(["tests/contracts"], checkout=str(tmp_path))
    assert gone not in argv
    for driven in config.RESOLVE_DRIVING:
        assert driven in argv


def test_default_budget_holds_the_curated_mandatory_set():
    # The retune (300s, see config's why): the curated mandatory core
    # (~59s serial) fits comfortably inside the default budget, where
    # the old 360s set could never fit any small budget.
    assert config.tier1_time_budget_seconds() == 300
