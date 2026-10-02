"""Timing telemetry affects worker order, never test membership."""

from types import SimpleNamespace

import pytest

from scripts.pytest_timing import ordered_items, write_timings


def test_longer_files_are_ordered_first_without_splitting_a_file(tmp_path):
    root = tmp_path.resolve()
    items = [
        SimpleNamespace(path=root / "tests" / "a.py", nodeid="tests/a.py::a1"),
        SimpleNamespace(path=root / "tests" / "b.py", nodeid="tests/b.py::b1"),
        SimpleNamespace(path=root / "tests" / "a.py", nodeid="tests/a.py::a2"),
    ]

    ordered = ordered_items(
        items,
        {"tests/a.py": 5.0, "tests/b.py": 2.0},
        root,
    )

    assert [item.nodeid for item in ordered] == [
        "tests/a.py::a1", "tests/a.py::a2", "tests/b.py::b1",
    ]


def test_only_executed_testcase_time_is_recorded(tmp_path, monkeypatch):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.tooling.test_pytest_timing.TestTiming" '
        'time="1.25"/>'
        '<testcase classname="tests.tooling.test_pytest_timing" time="0.5"/>'
        '<testcase classname="tests.tooling.test_no_live_resolve" time="9">'
        '<skipped/></testcase>'
        '<testcase classname="tests.unit.resolve.test_fusion_parser.Test" '
        'time="0.75"/>'
        '</testsuite>',
        encoding="utf-8",
    )
    destination = tmp_path / "timings.json"
    monkeypatch.setenv("VEP_TEST_TIMING_FILE", str(destination))

    count = write_timings([report])

    assert count == 2
    assert destination.read_text(encoding="utf-8") == (
        '{\n  "files": {\n'
        '    "tests/tooling/test_pytest_timing.py": 1.75,\n'
        '    "tests/unit/resolve/test_fusion_parser.py": 0.75\n'
        '  },\n  "version": 1\n}\n'
    )


def test_empty_report_does_not_replace_the_last_full_run(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase classname="temporary.test_outside" '
        'time="1.25"/></testsuite>',
        encoding="utf-8",
    )
    destination = tmp_path / "timings.json"
    old_data = '{"version": 1, "files": {"tests/test_old.py": 3.0}}\n'
    destination.write_text(old_data, encoding="utf-8")

    with pytest.raises(ValueError, match="did not identify"):
        write_timings([report], destination)

    assert destination.read_text(encoding="utf-8") == old_data
