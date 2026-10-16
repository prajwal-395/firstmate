"""Revisioned CI performance records refuse incomparable shortcuts."""

import json

import pytest

from scripts.ci_perf_report import (
    check,
    latest_baseline,
    main,
    measure,
    resource_totals,
)


def _junit(path, rows):
    cases = "".join(
        f'<testcase classname="tests.unit.audio.test_sample" name="{name}" '
        f'time="{seconds}" file="tests/unit/audio/test_sample.py">{body}</testcase>'
        for name, seconds, body in rows
    )
    path.write_text(f"<testsuite>{cases}</testsuite>", encoding="utf-8")
    return path


def test_cli_help_formats_under_argparse():
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0


def test_report_records_revision_counts_slowest_tests_and_execution_coverage(tmp_path):
    report = _junit(tmp_path / "junit.xml", [
        ("fast", 0.2, ""),
        ("slow", 4.5, ""),
        ("optional", 0.0, '<skipped message="capability unavailable"/>'),
    ])
    result = measure([report], "abc123", "PASS", 9.0, 8.1, 1024)
    assert result["revision"] == "abc123"
    assert result["counts"] == {"passed": 2, "failed": 0, "skipped": 1}
    assert result["selected_count"] == 3
    assert result["executed_count"] == 2
    assert result["test_files"] == 1
    assert result["slow_tests"][0] == ("tests.unit.audio.test_sample::slow", 4.5)
    assert result["cpu_seconds"] == 8.1
    assert result["peak_rss_bytes"] == 1024


def test_performance_gate_rejects_faster_but_incomplete_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "tests/unit/audio/test_sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("def a(): ...\ndef b(): ...\n", encoding="utf-8")
    complete = measure([_junit(tmp_path / "complete.xml", [("a", 1, ""), ("b", 2, "")])], "r1", "PASS", 10)
    incomplete = measure([_junit(tmp_path / "incomplete.xml", [("a", 0.1, "")])], "r2", "PASS", 1)
    assert "baseline tests were deselected or stopped executing" in check(incomplete, complete)[0]


def test_cli_regression_exercise_refuses_faster_incomplete_fixture(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "tests/unit/audio/test_sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("def a(): ...\ndef b(): ...\n", encoding="utf-8")
    baseline = measure([_junit(tmp_path / "baseline.xml", [("a", 1, ""), ("b", 2, "")])], "good", "PASS", 10)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    incomplete_xml = _junit(tmp_path / "incomplete.xml", [("a", 0.1, "")])
    history = tmp_path / "history.jsonl"
    assert main([
        "--revision", "bad", "--verdict", "PASS", "--wall-seconds", "1",
        "--baseline", str(baseline_path), "--history", str(history), str(incomplete_xml),
    ]) == 1
    assert "baseline tests were deselected or stopped executing" in capsys.readouterr().err
    assert json.loads(history.read_text(encoding="utf-8"))["verdict"] == "FAIL"


def test_performance_gate_checks_provisional_wall_threshold():
    base = {"selected_count": 1, "test_set_sha256": "same", "executed_count": 1,
            "counts": {"failed": 0}, "wall_seconds": 100}
    run = {**base, "wall_seconds": 121}
    assert "wall time exceeds the provisional regression threshold" in check(run, base)


def test_performance_gate_names_slow_test_regressions():
    base = {"selected_count": 1, "test_set_sha256": "same", "executed_count": 1,
            "counts": {"failed": 0}, "wall_seconds": 100,
            "test_times": {"tests/unit/audio/test_x.py::test_x": 0.1}}
    run = {**base, "test_times": {"tests/unit/audio/test_x.py::test_x": 30.0}}
    assert any("slow-test regression" in error for error in check(run, base))


def test_performance_gate_allows_a_run_that_adds_a_test():
    base = {"selected_count": 1, "test_set_sha256": "old", "executed_count": 1,
            "counts": {"failed": 0}, "wall_seconds": 10,
            "test_ids": ["tests.unit.audio.test_sample::test_existing"],
            "test_times": {"tests.unit.audio.test_sample::test_existing": 0.2}}
    run = {**base, "selected_count": 2, "executed_count": 2,
           "test_set_sha256": "new", "wall_seconds": 11,
           "test_ids": [*base["test_ids"], "tests.unit.audio.test_sample::test_added"],
           "test_times": {**base["test_times"], "tests.unit.audio.test_sample::test_added": 0.1}}
    assert check(run, base) == []


def test_performance_gate_ignores_a_skip_shared_with_the_baseline():
    base = {"selected_count": 2, "executed_count": 1,
            "counts": {"failed": 0, "passed": 1, "skipped": 1}, "wall_seconds": 10,
            "test_ids": ["m::test_runs", "m::test_skipped"],
            "test_times": {"m::test_runs": 0.2}}
    run = {**base, "selected_count": 3, "executed_count": 2,
           "test_ids": [*base["test_ids"], "m::test_added"],
           "test_times": {**base["test_times"], "m::test_added": 0.1}}
    assert check(run, base) == []


def test_performance_gate_flags_a_baseline_test_that_stops_executing():
    base = {"selected_count": 2, "executed_count": 2,
            "counts": {"failed": 0, "passed": 2, "skipped": 0}, "wall_seconds": 10,
            "test_ids": ["m::test_runs", "m::test_now_skipped"],
            "test_times": {"m::test_runs": 0.2, "m::test_now_skipped": 0.1}}
    run = {**base, "selected_count": 2, "executed_count": 1,
           "counts": {"failed": 0, "passed": 1, "skipped": 1},
           "test_times": {"m::test_runs": 0.2}}
    assert "now skipped" in check(run, base)[0]


def test_performance_gate_forgives_a_deleted_test_function_but_not_a_deselected_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    module = tmp_path / "test_sample.py"
    module.write_text("def test_kept(): ...\ndef test_present(): ...\n", encoding="utf-8")
    base = {"selected_count": 3, "executed_count": 3,
            "counts": {"failed": 0}, "wall_seconds": 10,
            "test_ids": ["test_sample.py::test_kept", "test_sample.py::test_deleted",
                         "test_sample.py::test_present"],
            "test_times": {"test_sample.py::test_kept": 0.1, "test_sample.py::test_deleted": 0.1,
                           "test_sample.py::test_present": 0.1}}
    run = {**base, "selected_count": 1, "executed_count": 1,
           "test_ids": ["test_sample.py::test_kept"],
           "test_times": {"test_sample.py::test_kept": 0.1}}
    errors = check(run, base)
    assert len(errors) == 1
    assert "test_sample.py::test_present" in errors[0]
    assert "test_deleted" not in errors[0]


def test_performance_gate_uses_the_history_envelope_for_noisy_tests():
    noisy = "m::test_noisy"
    base = {"selected_count": 1, "executed_count": 1,
            "counts": {"failed": 0}, "wall_seconds": 100,
            "test_ids": [noisy], "test_times": {noisy: 2.187}}
    history = [{**base, "test_times": {noisy: seconds}}
               for seconds in (32.031, 6.446, 2.187, 32.62, 16.856, 1.303)]
    run = {**base, "test_times": {noisy: 9.769}}
    assert check(run, base, history=history) == []
    stable = "m::test_stable"
    slow_base = {**base, "test_ids": [stable], "test_times": {stable: 0.12}}
    slow_history = [{**slow_base, "test_times": {stable: 0.12}}]
    slow_run = {**slow_base, "test_times": {stable: 30.0}}
    assert any("slow-test regression" in error for error in check(slow_run, slow_base, history=slow_history))


def test_latest_baseline_ignores_narrowed_or_failed_runs(tmp_path):
    history = tmp_path / "history.jsonl"
    history.write_text("\n".join(json.dumps(row) for row in [
        {"schema": 1, "verdict": "PASS", "counts": {"failed": 0}, "revision": "good"},
        {"schema": 1, "verdict": "NARROWED PASS", "counts": {"failed": 0}, "revision": "narrow"},
        {"schema": 1, "verdict": "FAIL", "counts": {"failed": 1}, "revision": "bad"},
    ]), encoding="utf-8")
    assert latest_baseline(history)["revision"] == "good"


def test_resource_totals_aggregate_cpu_and_keep_largest_lane_rss(tmp_path):
    first = tmp_path / "parallel.time"
    first.write_text("user 1.25\nsys 0.25\nmaximum resident set size 8192\n", encoding="utf-8")
    second = tmp_path / "serial.time"
    second.write_text("user 2.0\nsys 0.5\nmaximum resident set size 4096\n", encoding="utf-8")
    assert resource_totals([first, second]) == (4.0, 8192)


def test_cli_records_measured_resources_and_revision(tmp_path, capsys):
    report = _junit(tmp_path / "junit.xml", [("a", 0.2, "")])
    timing = tmp_path / "lane.time"
    timing.write_text("user 1.0\nsys 0.5\nmaximum resident set size 4096\n", encoding="utf-8")
    history = tmp_path / "history.jsonl"
    assert main([
        "--revision", "deadbeef", "--verdict", "PASS", "--wall-seconds", "2",
        str(report), "--time-files", str(timing), "--history", str(history),
    ]) == 0
    row = json.loads(history.read_text(encoding="utf-8"))
    assert row["revision"] == "deadbeef"
    assert row["cpu_seconds"] == 1.5
    assert row["peak_rss_bytes"] == 4096
    assert '"revision": "deadbeef"' in capsys.readouterr().out


def test_malformed_or_empty_junit_refuses(tmp_path):
    report = tmp_path / "empty.xml"
    report.write_text("<testsuite />", encoding="utf-8")
    with pytest.raises(ValueError, match="no test cases"):
        measure([report], "r", "PASS", 1)
