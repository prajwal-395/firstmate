"""Runtime coverage index: executed code is named, the rest refuses.

Defects caught: a selector that narrows on a coverage index misleads
whenever the index (a) omits code a test executed, (b) names code it
did not, (c) loads against the wrong tree, or (d) is written from a
run that never traced. Each test below pins one half: the executed
module must appear with its lines, the unexecuted module must be
absent, and any staleness (sha, interpreter, native) or hollowness
(missing file, red tree, no tracer) refuses with the reason.

The fixture checkout under `tmp_path` is the scratch checkout of the
acceptance check; machine-dependent version components (tree sha,
interpreter, native) are pinned, so these tests are deterministic on
any machine. The live `--cov-context=test` leg runs only on demand via
`scripts/collect_coverage_index.py`, never in tests.
"""

import json

import pytest

from library.tools.local_ci import coverage_index
from library.tools.local_ci.hygiene import HygieneRefused

PINNED = {"sha": "sha-test-aaa", "interpreter": "python-test-3.12",
          "native": "native-test"}

WIDGET_TEST = "tests/unit/foo/test_widget.py::test_value"


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "library").mkdir(parents=True)
    (root / "tests" / "unit" / "foo").mkdir(parents=True)
    (root / "library" / "mod_b.py").write_text("MID = 1\n",
                                               encoding="utf-8")
    (root / "library" / "mod_a.py").write_text(
        "from library.mod_b import MID\nVALUE = MID\n", encoding="utf-8")
    (root / "library" / "unrelated.py").write_text("OTHER = 0\n",
                                                   encoding="utf-8")
    (root / "tests" / "unit" / "foo" / "test_widget.py").write_text(
        "from library.mod_a import VALUE\n\n"
        "def test_value():\n    assert VALUE == 1\n",
        encoding="utf-8")
    return root


def _contexts():
    """What `coverage json --show-contexts` reports after test_widget
    ran: it executed mod_a and mod_b, never unrelated."""
    return {
        "library/mod_a.py": {WIDGET_TEST: [1, 2]},
        "library/mod_b.py": {WIDGET_TEST: [1]},
        "library/unrelated.py": {},
    }


def _write(checkout, destination, tests, **overrides):
    merged = dict(PINNED)
    merged.update(overrides)
    return coverage_index.write_index(
        destination, str(checkout), tests,
        {"collected_at": 0, "wall_seconds": 10.0,
         "baseline_seconds": None, "overhead_seconds": None,
         "overhead_ratio": None, "selection": ["tests/unit"],
         "pytest": ["pytest"]},
        **merged)


def _load(checkout, destination, **overrides):
    merged = dict(PINNED)
    merged.update(overrides)
    return coverage_index.load(str(checkout), destination, **merged)


def test_executed_module_is_named_and_unexecuted_is_absent(checkout,
                                                           tmp_path):
    tests = coverage_index.build_index(_contexts(), str(checkout))
    assert set(tests[WIDGET_TEST]["files"]) == {
        "library/mod_a.py", "library/mod_b.py"}
    assert tests[WIDGET_TEST]["lines"]["library/mod_a.py"] == [1, 2]
    assert all("library/unrelated.py" not in entry["files"]
               for entry in tests.values())

    destination = tmp_path / "index.json"
    _write(checkout, destination, tests)
    loaded, reason = _load(checkout, destination)
    assert reason is None
    assert loaded[WIDGET_TEST]["files"] == [
        "library/mod_a.py", "library/mod_b.py"]


def test_stale_sha_is_refused_with_reason(checkout, tmp_path):
    destination = tmp_path / "index.json"
    _write(checkout, destination,
           coverage_index.build_index(_contexts(), str(checkout)))
    loaded, reason = _load(checkout, destination, sha="sha-test-bbb")
    assert loaded is None
    assert "stale" in reason and "tree_sha" in reason


def test_stale_interpreter_and_native_are_refused(checkout, tmp_path):
    destination = tmp_path / "index.json"
    _write(checkout, destination,
           coverage_index.build_index(_contexts(), str(checkout)))
    loaded, reason = _load(checkout, destination,
                           interpreter="python-other")
    assert loaded is None and "interpreter" in reason
    loaded, reason = _load(checkout, destination, native="native-other")
    assert loaded is None and "native" in reason


def test_missing_and_malformed_index_is_refused(checkout, tmp_path):
    loaded, reason = _load(checkout, tmp_path / "absent.json")
    assert loaded is None and "collect_coverage_index" in reason
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    loaded, reason = _load(checkout, broken)
    assert loaded is None and "unparseable" in reason


def test_outside_checkout_executions_are_not_indexed(checkout):
    tests = coverage_index.build_index(
        {"/usr/lib/python3.12/os.py": {WIDGET_TEST: [1]},
         "/venv/lib/site-packages/pytest/__init__.py": {WIDGET_TEST: [1]},
         "library/mod_a.py": {WIDGET_TEST: [1]}},
        str(checkout))
    assert tests[WIDGET_TEST]["files"] == ["library/mod_a.py"]


def test_overhead_records_wall_delta_and_none_without_baseline():
    delta, ratio = coverage_index.overhead_seconds(12.5, 10.0)
    assert (delta, ratio) == (pytest.approx(2.5), pytest.approx(0.25))
    assert coverage_index.overhead_seconds(12.5, None) == (None, None)


def test_collection_argv_matches_gate_markers(checkout):
    from library.tools.local_ci import config
    argv = coverage_index.collection_argv(
        "python3", ["tests/unit"], str(checkout))
    assert "--cov-context=test" in argv
    assert config.PYTEST_MARKERS in argv
    assert not any(owned in argv for owned in config.TIER1_TIER2_ONLY)


def test_index_path_follows_timing_convention(tmp_path, monkeypatch):
    monkeypatch.setenv("VEP_COVERAGE_INDEX_FILE", str(tmp_path / "x.json"))
    assert coverage_index.index_path() == tmp_path / "x.json"
    monkeypatch.delenv("VEP_COVERAGE_INDEX_FILE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert coverage_index.index_path() == (
        tmp_path / "ren" / "pytest-coverage-index.json")


def test_red_tree_writes_nothing(checkout, tmp_path, monkeypatch):
    from library.tools.local_ci import hygiene
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(hygiene, "disk_ok", lambda path: True)
    monkeypatch.setattr(coverage_index, "has_pytest_cov",
                        lambda python: True)

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "FAILED tests/unit/foo/test_widget.py::test_value"

    monkeypatch.setattr(coverage_index.subprocess, "run",
                        lambda *a, **k: Failed())
    destination = tmp_path / "index.json"
    result = coverage_index.collect(
        str(checkout), selection=["tests/unit"], python="python3",
        destination=destination)
    assert result["ok"] is False and "red" in result["reason"]
    assert not destination.exists()


def test_collection_refuses_without_tracer(checkout, monkeypatch):
    from library.tools.local_ci import hygiene
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(hygiene, "disk_ok", lambda path: True)
    monkeypatch.setattr(coverage_index, "has_pytest_cov",
                        lambda python: False)
    with pytest.raises(HygieneRefused, match="pytest-cov"):
        coverage_index.collect(
            str(checkout), selection=["tests/unit"], python="python3")


def test_collection_defers_while_gate_runs(checkout, monkeypatch):
    from library.tools.local_ci import hygiene
    monkeypatch.setattr(hygiene, "gate_running", lambda: True)
    with pytest.raises(HygieneRefused, match="gate holds the machine"):
        coverage_index.collect(
            str(checkout), selection=["tests/unit"], python="python3")


def test_contexts_adapter_skips_contextless_files():
    payload = {"files": {
        "library/mod_a.py": {"contexts": {WIDGET_TEST: [3]}},
        "library/mod_b.py": {"executed_lines": [1]},
        "library/mod_c.py": "not-a-record"}}
    assert coverage_index.contexts_from_coverage_json(payload) == {
        "library/mod_a.py": {WIDGET_TEST: [3]}}
    assert coverage_index.contexts_from_coverage_json({"no": "files"}) == {}
    assert coverage_index.contexts_from_coverage_json([]) == {}


def test_version_mismatch_is_refused(checkout, tmp_path):
    destination = tmp_path / "index.json"
    _write(checkout, destination, {})
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["version"] = 999
    destination.write_text(json.dumps(payload), encoding="utf-8")
    loaded, reason = _load(checkout, destination)
    assert loaded is None and "version" in reason
