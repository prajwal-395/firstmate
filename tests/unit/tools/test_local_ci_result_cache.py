"""Tier-1 result cache: every invalidation input forces a re-run.

Defects caught: the benchmark's prototype bug twice over - a helper
module under `tests/` (`golden_edit.py`, changed in all three PR-2145
heads) and a data file beside the test (`golden_monologue_answers.json`,
also changed) both missed by a library-only closure, so a stale PASS
would have suppressed the rerun. Each test below pins one key input:
change it and the stored verdict must MISS.

All keys resolve against a miniature checkout under `tmp_path` with
machine-dependent components pinned (interpreter/native/env/config
overrides), so these tests are deterministic on any machine.
"""

import os

import pytest

from library.tools.local_ci import daemon, queue, result_cache, runner, state

PINNED = {"interpreter": "python-test-3.12", "native": "native-test",
          "env": "env-test", "config_text": "config-test"}

PLAIN_TEST = "from library.mod_a import VALUE\n\ndef test_value():\n    assert VALUE == 1\n"
# The PR-2145 shape: a scenario test over a tests-helper plus a data
# file, env-gated (it renders media, like the npm-missing failure).
GATED_TEST = ("import shutil\nfrom tests.scenarios import golden_helper\n\n"
              "HAS_FFMPEG = shutil.which('ffmpeg')\n\n"
              "def test_answers():\n"
              "    assert golden_helper.ANSWERS == [1]\n")


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "library").mkdir(parents=True)
    (root / "tests" / "unit" / "foo").mkdir(parents=True)
    (root / "tests" / "scenarios").mkdir(parents=True)
    (root / "library" / "mod_c.py").write_text("LEAF = 3\n",
                                               encoding="utf-8")
    (root / "library" / "mod_b.py").write_text(
        "from library.mod_c import LEAF\nMID = LEAF + 1\n",
        encoding="utf-8")
    (root / "library" / "mod_a.py").write_text(
        "from library.mod_b import MID\nVALUE = MID - 1\n",
        encoding="utf-8")
    (root / "library" / "unrelated.py").write_text("OTHER = 0\n",
                                                   encoding="utf-8")
    (root / "tests" / "conftest.py").write_text("", encoding="utf-8")
    (root / "tests" / "unit" / "conftest.py").write_text(
        "", encoding="utf-8")
    (root / "tests" / "unit" / "foo" / "test_widget.py").write_text(
        PLAIN_TEST, encoding="utf-8")
    (root / "tests" / "scenarios" / "golden_helper.py").write_text(
        "ANSWERS = [1]\n", encoding="utf-8")
    (root / "tests" / "scenarios" / "test_golden_thing.py").write_text(
        GATED_TEST, encoding="utf-8")
    (root / "tests" / "scenarios" / "answers.json").write_text(
        "[1]\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[tool.pytest]\n",
                                         encoding="utf-8")
    return root


@pytest.fixture
def state_dir(tmp_path):
    path = tmp_path / "ren-ci"
    path.mkdir()
    return str(path)


def _key(checkout, rel, **overrides):
    merged = dict(PINNED)
    merged.update(overrides)
    return result_cache.compute_key(str(checkout), rel, **merged)


def _store_hit(state_dir, checkout, rel, passed=3, **overrides):
    merged = dict(PINNED)
    merged.update(overrides)
    builder = result_cache.KeyBuilder(str(checkout))
    key = result_cache.compute_key(str(checkout), rel, builder, **merged)
    result_cache.store(state_dir, rel, key, passed, passed)
    return builder


def _lookup(state_dir, checkout, rel, builder=None, **overrides):
    merged = dict(PINNED)
    merged.update(overrides)
    return result_cache.lookup(
        state_dir, str(checkout), rel, builder, **merged)


def test_identical_tree_reuses(checkout, state_dir):
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    hit = _lookup(state_dir, checkout, rel)
    assert hit is not None and hit["passed"] == 3


def test_test_file_change_invalidates(checkout, state_dir):
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    (checkout / rel).write_text(
        PLAIN_TEST + "\ndef test_more():\n    assert True\n",
        encoding="utf-8")
    assert _lookup(state_dir, checkout, rel) is None


def test_transitive_library_closure_change_invalidates(checkout, state_dir):
    # The test imports mod_a imports mod_b imports mod_c: a change two
    # hops down must still force the re-run.
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    (checkout / "library" / "mod_c.py").write_text("LEAF = 4\n",
                                                   encoding="utf-8")
    assert _lookup(state_dir, checkout, rel) is None


def test_key_builder_parses_shared_imports_once_per_checkout(
        checkout, monkeypatch):
    rel = "tests/unit/foo/test_widget.py"
    second_rel = "tests/unit/foo/test_widget_2.py"
    (checkout / second_rel).write_text(PLAIN_TEST, encoding="utf-8")
    parsed = []
    original = result_cache._parse_import_stems

    def counted(path):
        parsed.append(path)
        return original(path)

    monkeypatch.setattr(result_cache, "_parse_import_stems", counted)
    builder = result_cache.KeyBuilder(str(checkout))

    builder.closure(rel)
    first_parse_count = len(parsed)
    builder.closure(second_rel)

    assert first_parse_count > 0
    assert len(parsed) == len(set(parsed))


def test_unrelated_library_change_preserves_reuse(checkout, state_dir):
    # Precision: a file nothing in the closure imports must NOT evict.
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    (checkout / "library" / "unrelated.py").write_text("OTHER = 1\n",
                                                       encoding="utf-8")
    assert _lookup(state_dir, checkout, rel) is not None


def test_tests_helper_change_invalidates(checkout, state_dir):
    # PR 2145, miss one: `tests/scenarios/golden_edit.py` changed in all
    # three heads but the prototype's library-only closure never saw it.
    rel = "tests/scenarios/test_golden_thing.py"
    _store_hit(state_dir, checkout, rel)
    (checkout / "tests" / "scenarios" / "golden_helper.py").write_text(
        "ANSWERS = [2]\n", encoding="utf-8")
    assert _lookup(state_dir, checkout, rel) is None


def test_data_file_change_invalidates(checkout, state_dir):
    # PR 2145, miss two: `golden_monologue_answers.json` changed beside
    # the test. Content change and ADDED files both invalidate.
    rel = "tests/scenarios/test_golden_thing.py"
    _store_hit(state_dir, checkout, rel)
    (checkout / "tests" / "scenarios" / "answers.json").write_text(
        "[2]\n", encoding="utf-8")
    assert _lookup(state_dir, checkout, rel) is None


def test_added_data_file_invalidates(checkout, state_dir):
    rel = "tests/scenarios/test_golden_thing.py"
    _store_hit(state_dir, checkout, rel)
    (checkout / "tests" / "scenarios" / "extra.json").write_text(
        "{}\n", encoding="utf-8")
    assert _lookup(state_dir, checkout, rel) is None


def test_conftest_chain_change_invalidates(checkout, state_dir):
    rel = "tests/unit/foo/test_widget.py"
    for conftest in ("tests/conftest.py", "tests/unit/conftest.py"):
        _store_hit(state_dir, checkout, rel)
        (checkout / conftest).write_text("# touched\n", encoding="utf-8")
        assert _lookup(state_dir, checkout, rel) is None, conftest


def test_pytest_config_change_invalidates(checkout, state_dir):
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    assert _lookup(state_dir, checkout, rel,
                   config_text="config-changed") is None


def test_interpreter_change_invalidates(checkout, state_dir):
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    assert _lookup(state_dir, checkout, rel,
                   interpreter="python-test-3.13") is None


def test_native_change_invalidates(checkout, state_dir):
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    assert _lookup(state_dir, checkout, rel,
                   native="native-rebuilt") is None


def test_env_change_invalidates_gated_test_only(checkout, state_dir):
    # The 2145 failure was npm-missing: no content hash catches that, so
    # env-gated tests carry the fingerprint and never reuse across it.
    # Ungated tests must NOT pay for machine movement.
    gated = "tests/scenarios/test_golden_thing.py"
    plain = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, gated)
    _store_hit(state_dir, checkout, plain)
    assert _lookup(state_dir, checkout, gated, env="env-moved") is None
    assert _lookup(state_dir, checkout, plain, env="env-moved") is not None


def _junit(path, cases):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<testsuites>",
             '<testsuite name="pytest">']
    for rel, status in cases:
        name = rel.replace("/", ".")[:-len(".py")]
        open_tag = (f'<testcase classname="{name}" name="t" file="{rel}">')
        if status == "failed":
            lines += [open_tag, "<failure message=\"boom\"/>",
                      "</testcase>"]
        elif status == "skipped":
            lines += [open_tag, "<skipped/>", "</testcase>"]
        else:
            lines.append(open_tag + "</testcase>")
    lines += ["</testsuite>", "</testsuites>"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_failure_is_never_stored_and_evicts(checkout, state_dir,
                                            tmp_path):
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    junit = tmp_path / "junit.xml"
    _junit(junit, [(rel, "failed")])
    summary = result_cache.record_from_junit(
        state_dir, str(checkout), str(junit), [rel], **PINNED)
    assert summary["stored"] == [] and summary["dropped"] == [rel]
    assert _lookup(state_dir, checkout, rel) is None


def test_all_skipped_never_reuses(checkout, state_dir, tmp_path):
    # An all-skipped file executed zero tests: no evidence, no row.
    rel = "tests/unit/foo/test_widget.py"
    junit = tmp_path / "junit.xml"
    _junit(junit, [(rel, "skipped")])
    summary = result_cache.record_from_junit(
        state_dir, str(checkout), str(junit), [rel], **PINNED)
    assert summary["stored"] == []
    assert _lookup(state_dir, checkout, rel) is None


def test_partial_skip_drops_prior_pass_and_is_not_cached(
        checkout, state_dir, tmp_path):
    # A partial pass does not capture why the remaining test skipped.
    # Drop any older verdict too, so the same key cannot mask a changed
    # skip condition on the next integration plan.
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    junit = tmp_path / "partial.xml"
    _junit(junit, [(rel, "passed"), (rel, "skipped")])
    summary = result_cache.record_from_junit(
        state_dir, str(checkout), str(junit), [rel], **PINNED)
    assert summary["stored"] == []
    assert summary["dropped"] == [rel]
    assert _lookup(state_dir, checkout, rel) is None


def test_missing_junit_stores_nothing(checkout, state_dir):
    rel = "tests/unit/foo/test_widget.py"
    summary = result_cache.record_from_junit(
        state_dir, str(checkout), str(state_dir) + "/nope.xml",
        [rel], **PINNED)
    assert summary["stored"] == []
    assert _lookup(state_dir, checkout, rel) is None


def test_passing_junit_stores_per_file_verdicts(checkout, state_dir,
                                                tmp_path):
    first, second = ("tests/unit/foo/test_widget.py",
                     "tests/scenarios/test_golden_thing.py")
    junit = tmp_path / "junit.xml"
    _junit(junit, [(first, "passed"), (second, "passed")])
    summary = result_cache.record_from_junit(
        state_dir, str(checkout), str(junit), [first, second],
        **PINNED)
    assert sorted(summary["stored"]) == sorted([first, second])
    assert _lookup(state_dir, checkout, first)["passed"] == 1
    assert _lookup(state_dir, checkout, second)["passed"] == 1


def test_partition_reports_reused_counts(checkout, state_dir):
    first, second = ("tests/unit/foo/test_widget.py",
                     "tests/scenarios/test_golden_thing.py")
    _store_hit(state_dir, checkout, first)
    part = result_cache.partition(
        state_dir, str(checkout), [first, second],
        pytest_python="", **PINNED)
    assert part["total"] == 2
    assert [entry["path"] for entry in part["reused"]] == [first]
    assert part["run"] == [second]
    assert part["reused_passed"] == 3


def test_disabled_cache_runs_everything(checkout, state_dir, monkeypatch):
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    monkeypatch.setenv("REN_CI_RESULT_CACHE", "0")
    part = result_cache.partition(
        state_dir, str(checkout), [rel], pytest_python="", **PINNED)
    assert part["run"] == [rel] and part["reused"] == []


def test_expand_selection_expands_dirs_and_skips_conftest(checkout):
    expanded = result_cache.expand_selection(
        str(checkout),
        ["tests/unit", "tests/conftest.py",
         "tests/unit/foo/test_widget.py", "tests/gone.py"])
    assert expanded == ["tests/unit/foo/test_widget.py"]


def _store_key(state_dir, checkout, rel, passed, **overrides):
    """Store under an explicitly computed key (one parallel branch)."""
    merged = dict(PINNED)
    merged.update(overrides)
    builder = result_cache.KeyBuilder(str(checkout))
    key = result_cache.compute_key(str(checkout), rel, builder, **merged)
    result_cache.store(state_dir, rel, key, passed, passed)
    return key


def test_parallel_branch_keys_coexist(checkout, state_dir):
    # CI-01 pin, half one: two branches verify the same path under
    # different keys (AAA vs BBB). On main the second store OVERWROTE
    # the first (`INSERT OR REPLACE` on `test_path PRIMARY KEY`), so
    # the loser's lookup missed. After: both rows coexist and each
    # lookup hits only under its own exact key.
    rel = "tests/unit/foo/test_widget.py"
    key_a = _store_key(state_dir, checkout, rel, 3,
                       interpreter="python-branch-AAA")
    key_b = _store_key(state_dir, checkout, rel, 5,
                       interpreter="python-branch-BBB")
    assert key_a != key_b
    hit_a = _lookup(state_dir, checkout, rel,
                    interpreter="python-branch-AAA")
    hit_b = _lookup(state_dir, checkout, rel,
                    interpreter="python-branch-BBB")
    assert hit_a is not None and hit_a["key"] == key_a
    assert hit_a["passed"] == 3
    assert hit_b is not None and hit_b["key"] == key_b
    assert hit_b["passed"] == 5
    assert _lookup(state_dir, checkout, rel,
                   interpreter="python-branch-CCC") is None


def test_lookup_aaa_hits_after_bbb_stored(checkout, state_dir):
    # CI-01 pin, half two: the exact AAA/BBB ordering from the item -
    # AAA stored first, BBB stored later by another branch, and a
    # lookup under AAA still hits (on main it missed: row overwritten).
    rel = "tests/unit/foo/test_widget.py"
    _store_key(state_dir, checkout, rel, 3,
               interpreter="python-branch-AAA")
    _store_key(state_dir, checkout, rel, 5,
               interpreter="python-branch-BBB")
    hit = _lookup(state_dir, checkout, rel,
                  interpreter="python-branch-AAA")
    assert hit is not None and hit["passed"] == 3


def test_eviction_drops_least_recently_used_first(checkout, state_dir,
                                                  monkeypatch):
    # CI-01 pin, half three: history per path is bounded, and past the
    # bound the LEAST-recently-used key goes first - not the oldest
    # stored. BBB is stored after AAA but AAA is looked up (used) more
    # recently, so storing CCC must evict BBB and keep AAA + CCC.
    monkeypatch.setenv("REN_CI_CACHE_VERSIONS", "2")
    rel = "tests/unit/foo/test_widget.py"
    _store_key(state_dir, checkout, rel, 3,
               interpreter="python-branch-AAA")
    _store_key(state_dir, checkout, rel, 5,
               interpreter="python-branch-BBB")
    assert _lookup(state_dir, checkout, rel,
                   interpreter="python-branch-AAA") is not None
    _store_key(state_dir, checkout, rel, 7,
               interpreter="python-branch-CCC")
    assert _lookup(state_dir, checkout, rel,
                   interpreter="python-branch-AAA") is not None
    assert _lookup(state_dir, checkout, rel,
                   interpreter="python-branch-CCC") is not None
    assert _lookup(state_dir, checkout, rel,
                   interpreter="python-branch-BBB") is None


def test_legacy_single_row_db_migrates_without_losing_verdicts(
        checkout, state_dir):
    # Pre-CI-01 databases hold one row per path (`test_path PRIMARY
    # KEY`). Opening one must keep its verdict and serve it under the
    # matching key - never wipe or strand it.
    import sqlite3
    rel = "tests/unit/foo/test_widget.py"
    builder = result_cache.KeyBuilder(str(checkout))
    key = result_cache.compute_key(str(checkout), rel, builder, **PINNED)
    db = __import__("os").path.join(
        state_dir, result_cache.CACHE_FILENAME)
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "CREATE TABLE tier1_results ("
            " test_path TEXT PRIMARY KEY,"
            " key TEXT NOT NULL,"
            " passed INTEGER NOT NULL,"
            " total INTEGER NOT NULL,"
            " head TEXT NOT NULL DEFAULT '',"
            " recorded_at REAL NOT NULL)")
        connection.execute(
            "INSERT INTO tier1_results"
            " (test_path, key, passed, total, head, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (rel, key, 3, 3, "head-old", 1.0))
        connection.commit()
    finally:
        connection.close()
    hit = _lookup(state_dir, checkout, rel)
    assert hit is not None and hit["passed"] == 3
    assert hit["head"] == "head-old"


def test_tier1_status_line_reports_reuse(tmp_path, monkeypatch):
    # The brief's contract: `reused N/M` on the Tier-1 status line.
    from library.tools.local_ci import github
    posts = []
    monkeypatch.setattr(github, "post_status",
                        lambda *args, **kwargs: posts.append(args))
    monkeypatch.setattr(github, "pull_url",
                        lambda number: f"https://example/pr/{number}")
    pr = {"number": 9, "head": "head-9", "url": "https://example/pr/9"}
    outcome = {"pr": 9, "head": "head-9", "url": "https://example/pr/9",
               "ok": True, "passed": 10, "failed": 0, "elapsed": 1.0,
               "log": "/tmp/x.log", "selection": ["tests/unit"],
               "command": "pytest tests", "cache_reused": 2,
               "cache_total": 3}
    record = daemon.finish_tier1(
        queue.World(state_dir=str(tmp_path)), pr, outcome,
        seen_at=1.0, slot=0)
    assert "reused 2/3" in record["report"]
    assert posts and posts[-1][1] == "success"
    assert "reused 2/3" in posts[-1][2]


def test_runner_passes_junitxml_through(monkeypatch, tmp_path):
    commands = []

    def run_logged(command, _cwd, _log_path, _timeout):
        commands.append(command)
        with open(_log_path, "w", encoding="utf-8") as log:
            log.write("================ 1 passed in 1s ================\n")
        return {"exit": 0, "timed_out": False, "group_clean": True,
                "elapsed": 0.0}

    checkout = tmp_path / "checkout"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "tests" / "test_a.py").write_text("OK = 1\n",
                                                  encoding="utf-8")
    monkeypatch.setattr(runner.hygiene, "disk_ok", lambda _path: True)
    monkeypatch.setattr(runner.hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(runner.hygiene, "broker_serving", lambda: False)
    monkeypatch.setattr(runner.hygiene, "bind_renderer_dependencies",
                        lambda _checkout: None)
    monkeypatch.setattr(runner, "_pytest_python", lambda: "python3")
    monkeypatch.setattr(runner, "_run_logged", run_logged)
    junit = str(tmp_path / "out.junit.xml")
    result = runner.run(["tests/test_a.py"], str(checkout),
                        str(tmp_path / "run.log"), junit_path=junit)
    assert result["ok"] is True
    assert "--junitxml" in commands[0]
    assert commands[0][commands[0].index("--junitxml") + 1] == junit


def test_tier2_always_executes_despite_the_cache(monkeypatch, tmp_path,
                                                  state_dir, checkout):
    # CI-02: the shared cache never feeds Tier-2's verdict - a stored
    # PASS under the same key still runs the full gate on the exact
    # tree (skipping execution on a hit waits for CI-08), and the run
    # files its verdict under the locally computed key instead. The
    # gate log cites reuse + execution.
    rel = "tests/unit/foo/test_widget.py"
    _store_hit(state_dir, checkout, rel)
    calls = []

    def run_logged(command, cwd, log_path, timeout, env=None):
        calls.append(command)
        with open(log_path, "w", encoding="utf-8") as log:
            log.write("FULL-SUITE GATE: PASS  |  unit: 1 passed")
        out = (env or {}).get(runner.TIER2_JUNIT_OUT_ENV, "")
        if out:
            junit = __import__("pathlib").Path(out)
            junit.parent.mkdir(parents=True, exist_ok=True)
            _junit(junit, [(rel, "passed")])
        return {"timed_out": False, "group_clean": True}

    monkeypatch.setattr(runner, "_run_logged", run_logged)
    monkeypatch.setattr(
        runner, "_tier2_interpreter",
        lambda checkout_path: PINNED["interpreter"])
    monkeypatch.setattr(result_cache, "native_fingerprint",
                        lambda *args, **kwargs: PINNED["native"])
    monkeypatch.setattr(result_cache, "config_fingerprint",
                        lambda *args, **kwargs: PINNED["config_text"])
    monkeypatch.setattr(result_cache, "env_fingerprint",
                        lambda *args, **kwargs: PINNED["env"])
    log_path = str(tmp_path / "gate.log")
    result = runner.run_gate(
        str(checkout), log_path, timeout=1200,
        cache={"state_dir": state_dir, "source": "batch"})
    assert result["ok"] is True
    assert calls and calls[0][1] == "scripts/full_suite_gate.sh"
    assert "--junitxml" not in calls[0]
    with open(log_path, encoding="utf-8") as handle:
        text = handle.read()
    assert "reused 1/2" in text and "execut" in text
    hit = _lookup(state_dir, checkout, rel)
    assert hit is not None and hit["source"] == "batch"


def test_tier1_second_run_on_same_tree_skips_pytest(
        checkout, state_dir, tmp_path, monkeypatch):
    # End to end through run_tier1 with every seam stubbed: the first
    # run executes and records, the second reuses without pytest.
    from library.tools.local_ci import github, hygiene, selection
    monkeypatch.setattr(github, "post_status", lambda *a, **k: None)
    monkeypatch.setattr(github, "main_sha", lambda: "base")
    monkeypatch.setattr(hygiene, "ensure_mirror", lambda *a, **k: "mirror")
    monkeypatch.setattr(hygiene, "fetch_base", lambda *a: None)
    monkeypatch.setattr(hygiene, "fetch_pr", lambda *a: "head-1")

    import contextlib

    @contextlib.contextmanager
    def fake_checkout(mirror, rev, state):
        yield str(checkout)

    monkeypatch.setattr(hygiene, "disposable_checkout", fake_checkout)
    monkeypatch.setattr(selection, "changed_files", lambda *a: ["library/x"])
    monkeypatch.setattr(
        selection, "capped_selection_for",
        lambda *a, **k: {"paths": ["tests/unit/foo/test_widget.py"],
                         "capped": False, "dependent_total": 1,
                         "dependent_kept": 1, "budget": 30.0,
                         "budget_seconds": 30.0, "estimated_seconds": 1.0,
                         "deferred": 0, "deferred_paths": []})
    monkeypatch.setattr(runner, "run_static",
                        lambda *a, **k: {"ok": True, "reason": ""})
    monkeypatch.setattr(runner, "_pytest_python", lambda: "python3")
    monkeypatch.setattr(result_cache, "pytest_interpreter_version",
                        lambda *a, **k: PINNED["interpreter"])
    monkeypatch.setattr(result_cache, "native_fingerprint",
                        lambda *a, **k: PINNED["native"])
    monkeypatch.setattr(result_cache, "config_fingerprint",
                        lambda *a, **k: PINNED["config_text"])
    monkeypatch.setattr(result_cache, "env_fingerprint",
                        lambda *a, **k: PINNED["env"])
    runs = []

    def fake_run(paths, checkout_path, log_path, workers=2, junit_path=""):
        runs.append(list(paths))
        rel = "tests/unit/foo/test_widget.py"
        target = junit_path or os.path.join(
            os.path.dirname(log_path), "stub.junit.xml")
        __import__("pathlib").Path(target).parent.mkdir(
            parents=True, exist_ok=True)
        _junit(__import__("pathlib").Path(target), [(rel, "passed")])
        with open(log_path, "w", encoding="utf-8") as log:
            log.write("================ 1 passed in 1s ================\n")
        return {"passed": 1, "failed": 0, "ok": True, "exit": 0,
                "elapsed": 1.0, "timed_out": False,
                "timeout_diagnostic": "",
                "process_group_leaked": False, "broker_leaked": False,
                "reason": "", "log": log_path}

    monkeypatch.setattr(runner, "run", fake_run)
    world = queue.World(state_dir=state_dir)
    pr = {"number": 1, "head": "head-1", "url": "https://example/pr/1"}
    first = daemon.run_tier1(world, pr, workers=1)
    assert runs and first["cache_reused"] == 0 and first["cache_total"] == 1
    runs.clear()
    second = daemon.run_tier1(world, pr, workers=1)
    assert runs == []  # no pytest: the stored PASS was reused
    assert second["ok"] is True
    assert second["cache_reused"] == 1 and second["cache_total"] == 1
    assert state.read_checks(state_dir) == {}  # run_tier1 writes no ledger
