"""The narrow selection is computed, not judged - and pinned here.

PR #1258 changed `resolve_plan` and validated it without running the
tests of the things that FEED `resolve_plan`: the speaker contract
test sat red through the merge. PR #1270 repaired the speaker cards
but missed the second producer, and the explainer contract test stayed
red. Both regressions are selection failures, and this file pins the
procedure that stops the third: `scripts/select_dependent_tests.py`.

Each test names the incident edge it guards, so a simplification that
drops one reads as a red test, not a smaller file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_selector():
    """`scripts/select_dependent_tests.py` without touching `sys.path`.

    An import-time `sys.path.insert` of `scripts/` is process-global:
    collection order would decide what a later bare name binds to
    (`tests/tooling/test_static_check.py`). The agents-md gate loads
    its scripts the same way (`tests/tooling/test_agents_md_gates.py`).
    """
    spec = importlib.util.spec_from_file_location(
        "_select_dependent_tests",
        REPO_ROOT / "scripts" / "select_dependent_tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_selector = _load_selector()
CONTRACT_SETS = _selector.CONTRACT_SETS
select = _selector.select

MG_CONTRACT = CONTRACT_SETS["motion_graphics_plan"]
WORKFLOW_CONTRACT = CONTRACT_SETS["qualification_workflow"]


def test_workflow_qualification_selects_the_october_failure_classes():
    selected = select(["library/tools/qualification_workflow.py"])
    for contract in WORKFLOW_CONTRACT:
        assert contract in selected, contract


def test_dependent_selection_never_passes_conftest_as_a_test():
    selected = select(["library/tools/qualification_workflow.py"])
    assert not any(path.endswith("/conftest.py") for path in selected)


def test_the_resolver_change_selects_the_speaker_contract_test():
    """#1258's edge: the changed module is `motion_graphics_plan`, the
    missed test was `test_speaker_identity.py` - which never imports it
    at top level, only inside a function."""
    assert "tests/unit/audio/test_speaker_identity.py" in select(
        ["library/tools/motion_graphics_plan.py"])


def test_the_resolver_change_selects_the_explainer_contract_test():
    """#1270's edge: the same refusal caught a second producer, and the
    repair lane's selection missed `test_explainer_plan.py` too."""
    assert "tests/unit/captions/test_full_frame_element.py" in select(
        ["library/tools/motion_graphics_plan.py"])


def test_a_producer_change_selects_the_resolver_contract_set():
    """The backward hook: changing a producer runs every contract test
    of the resolver it feeds, not just the producer's own file."""
    selected = select(["library/tools/speaker_identity.py"])
    for contract in MG_CONTRACT:
        assert contract in selected, contract


def test_golden_recipe_sources_select_their_media_scenarios():
    expected = {
        "tests/scenarios/golden.py": {
            "tests/scenarios/test_golden_conversation.py",
            "tests/scenarios/test_reel_preview.py",
            "tests/scenarios/test_golden_structure.py",
        },
        "tests/scenarios/golden_edit.py": {
            "tests/scenarios/test_golden_monologue.py",
            "tests/scenarios/test_golden_structure.py",
        },
        "tests/scenarios/golden_monologue_answers.json": {
            "tests/scenarios/test_golden_monologue.py",
            "tests/scenarios/test_golden_structure.py",
        },
    }
    for source, scenarios in expected.items():
        assert scenarios <= set(select([source]))


def test_golden_helper_runtime_imports_select_media_scenarios():
    expected = {
        "library/tools/timeline_transcript.py":
            "tests/scenarios/test_golden_conversation.py",
        "library/tools/reel_preview.py":
            "tests/scenarios/test_reel_preview.py",
        "library/processes/edit_video/run_pipeline.py":
            "tests/scenarios/test_golden_monologue.py",
        "library/processes/reels/run_reels.py":
            "tests/scenarios/test_golden_conversation.py",
    }
    for source, scenario in expected.items():
        assert scenario in select([source])


def test_a_binary_changed_file_contributes_no_stems_and_crashes_nothing(tmp_path):
    """ren-ci shells the selector per PR, and PRs carry images: a PNG
    in the diff must select by stem only (nothing), never UnicodeDecode
    the run. Found on PR #1897's 394-file diff."""
    png = tmp_path / "still.png"
    png.write_bytes(bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))
    assert select([str(png)]) == []


def test_changed_test_file_is_selected_for_itself():
    path = "tests/tooling/test_select_dependent_tests.py"
    assert path in select([path])


def test_test_configuration_and_fixture_changes_select_nested_safeguards():
    nested = {
        "tests/tooling/test_nested_project_collection.py",
        "tests/tooling/test_nested_broker_isolation.py",
    }
    for changed in ("pyproject.toml", "tests/conftest.py",
                    "tests/unit/resolve/conftest.py", "tests/helpers.py"):
        assert nested <= set(select([changed])), changed


def test_guard_changes_select_only_the_relevant_nested_safeguard():
    assert "tests/tooling/test_nested_project_collection.py" in select(
        ["library/tools/paths.py"])
    assert "tests/tooling/test_nested_broker_isolation.py" in select(
        ["library/tools/resolved/client.py"])
    assert "tests/tooling/test_nested_broker_isolation.py" in select(
        ["library/tools/resolve_lock.py"])


def test_distances_rank_the_changed_test_before_its_dependents():
    """Tier-1 fills its time budget in distance order, so the order
    itself is a contract: the changed test is 0, a direct importer is
    1, and nothing outranks the change itself."""
    module = _load_selector()
    distances = module.select_with_distances(
        ["tests/tooling/test_select_dependent_tests.py"])
    path = "tests/tooling/test_select_dependent_tests.py"
    assert distances[path] == 0
    assert set(distances) == set(module.select(
        ["tests/tooling/test_select_dependent_tests.py"]))
    assert all(hops >= 0 for hops in distances.values())


def test_selection_roots_at_ren_select_root(monkeypatch, tmp_path):
    """ren-ci selects against the PR checkout, not the tree the script
    lives in: with REN_SELECT_ROOT at a foreign tree, the index reads
    that tree. Otherwise a PR predating a test file selects paths its
    own checkout does not have, and pytest errors on the missing."""
    import os
    root = tmp_path / "foreign"
    (root / "library").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "library" / "only_here.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_only_here.py").write_text(
        "import only_here\n\n\ndef test_it():\n    assert only_here.VALUE\n",
        encoding="utf-8")
    monkeypatch.setenv("REN_SELECT_ROOT", str(root))
    module = _load_selector()
    assert module.REPO_ROOT == str(root)
    assert module.select(["library/only_here.py"]) == [
        "tests/test_only_here.py"]
    assert os.environ.get("REN_SELECT_ROOT") == str(root)


def _cache_tree(root):
    (root / "library").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "library" / "mod_y.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    (root / "library" / "mod_x.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_mod_x.py").write_text(
        "import mod_x\n\n\ndef test_it():\n    assert mod_x.VALUE\n",
        encoding="utf-8")


def _cache_module(monkeypatch, tmp_path, name):
    monkeypatch.setenv("REN_SELECT_ROOT", str(tmp_path / name))
    monkeypatch.setenv("REN_SELECTOR_CACHE_DIR",
                       str(tmp_path / "cache"))
    return _load_selector()


def test_cached_selection_matches_a_fresh_parse(monkeypatch, tmp_path):
    """The index cache is a speedup, not a second selector: a warm
    cache selects exactly what a full re-parse selects."""
    _cache_tree(tmp_path / "tree")
    module = _cache_module(monkeypatch, tmp_path, "tree")
    first = module.select(["library/mod_x.py"])
    assert first == ["tests/test_mod_x.py"]
    import os
    assert os.path.isfile(module._selector_cache_path())
    second = module.select(["library/mod_x.py"])
    assert second == first
    monkeypatch.setenv("REN_SELECTOR_NO_CACHE", "1")
    uncached = _load_selector().select(["library/mod_x.py"])
    assert uncached == first


def test_changed_content_invalidates_that_file(monkeypatch, tmp_path):
    """A rewritten file re-parses on the next selection: the cache is
    keyed by content fingerprint, so a new import takes effect
    without clearing anything by hand."""
    _cache_tree(tmp_path / "tree")
    module = _cache_module(monkeypatch, tmp_path, "tree")
    assert module.select(["library/mod_x.py"]) == ["tests/test_mod_x.py"]
    (tmp_path / "tree" / "library" / "mod_x.py").write_text(
        "import mod_y\n\nVALUE = mod_y.VALUE\n", encoding="utf-8")
    (tmp_path / "tree" / "tests" / "test_mod_y.py").write_text(
        "import mod_y\n\n\ndef test_it():\n    assert mod_y.VALUE\n",
        encoding="utf-8")
    assert module.select(["library/mod_x.py"]) == [
        "tests/test_mod_x.py", "tests/test_mod_y.py"]
    monkeypatch.setenv("REN_SELECTOR_NO_CACHE", "1")
    assert _load_selector().select(["library/mod_x.py"]) == [
        "tests/test_mod_x.py", "tests/test_mod_y.py"]


def test_corrupt_cache_is_a_cold_start(monkeypatch, tmp_path):
    """An unreadable cache never fails a selection: it parses
    everything, exactly as before the cache existed."""
    _cache_tree(tmp_path / "tree")
    module = _cache_module(monkeypatch, tmp_path, "tree")
    assert module.select(["library/mod_x.py"]) == ["tests/test_mod_x.py"]
    with open(module._selector_cache_path(), "w", encoding="utf-8") as handle:
        handle.write("not json {{{")
    module._MEMO.clear()
    assert module.select(["library/mod_x.py"]) == ["tests/test_mod_x.py"]


_COV_PIN = {"sha": "sha-ci06-fixture", "interpreter": "python-ci06",
            "native": "native-ci06"}


def _cov_module(monkeypatch, tmp_path, name):
    """The hybrid selector against a fixture tree with a pinned index.

    Every marker the coverage path reads is set here, so the tests
    below are hermetic: no host index, cache, or interpreter stamp
    leaks in.
    """
    monkeypatch.setenv("REN_SELECT_ROOT", str(tmp_path / name))
    monkeypatch.setenv("REN_SELECTOR_CACHE_DIR",
                       str(tmp_path / "cache"))
    monkeypatch.delenv("REN_SELECTOR_NO_CACHE", raising=False)
    monkeypatch.setenv("VEP_COVERAGE_INDEX_FILE",
                       str(tmp_path / "coverage.json"))
    monkeypatch.setenv("VEP_COVERAGE_TREE_SHA", _COV_PIN["sha"])
    monkeypatch.setenv("VEP_COVERAGE_INTERPRETER",
                       _COV_PIN["interpreter"])
    monkeypatch.setenv("VEP_COVERAGE_NATIVE", _COV_PIN["native"])
    return _load_selector()


def _write_cov_index(checkout, destination, tests_map):
    from library.tools.local_ci import coverage_index
    return coverage_index.write_index(
        destination, str(checkout), tests_map,
        {"collected_at": 0, "wall_seconds": 10.0,
         "baseline_seconds": None, "overhead_seconds": None,
         "overhead_ratio": None, "selection": ["tests/unit"],
         "pytest": ["pytest"]},
        sha=_COV_PIN["sha"], interpreter=_COV_PIN["interpreter"],
        native=_COV_PIN["native"])


def _leaf_tree(root):
    (root / "library").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "library" / "leaf.py").write_text("VALUE = 1\n",
                                              encoding="utf-8")
    (root / "tests" / "test_a_leaf.py").write_text(
        "import leaf\n\n\ndef test_runs():\n    assert leaf.VALUE == 1\n",
        encoding="utf-8")
    (root / "tests" / "test_b_leaf.py").write_text(
        "import leaf\n\n\ndef test_other():\n"
        "    assert leaf.VALUE is not None\n",
        encoding="utf-8")


def _leaf_index():
    return {
        "tests/test_a_leaf.py::test_runs": {
            "files": ["library/leaf.py", "tests/test_a_leaf.py"],
            "lines": {"library/leaf.py": [1],
                      "tests/test_a_leaf.py": [1, 4]}},
        "tests/test_b_leaf.py::test_other": {
            "files": ["tests/test_b_leaf.py"],
            "lines": {"tests/test_b_leaf.py": [1, 4]}},
    }


def test_coverage_narrows_a_leaf_change_but_keeps_the_executing_test(
        monkeypatch, tmp_path):
    """CI-06 acceptance, narrowing half: on a fixture tree with a
    fresh index, a leaf-module change selects strictly fewer files
    than the static selector while still selecting the test that
    executes it. `test_b_leaf` imports the leaf but the index shows
    it never executed it, so it is dropped; the report says so."""
    _leaf_tree(tmp_path / "tree")
    module = _cov_module(monkeypatch, tmp_path, "tree")
    _write_cov_index(tmp_path / "tree", tmp_path / "coverage.json",
                     _leaf_index())
    changed = ["library/leaf.py"]
    static = module.select(changed, use_coverage=False)
    assert static == ["tests/test_a_leaf.py", "tests/test_b_leaf.py"]
    narrowed = module.select(changed)
    assert narrowed == ["tests/test_a_leaf.py"]
    selected, info = module.select_with_coverage_info(changed)
    assert selected == narrowed
    assert info["static_count"] == 2
    assert info["narrowed_count"] == 1
    assert info["strictly_narrower"] is True
    assert info["subset"] is True
    assert info["added"] == []
    assert info["dropped"] == ["tests/test_b_leaf.py"]
    distances = module.select_with_distances(changed)
    assert distances == {"tests/test_a_leaf.py": 1}


def test_uncovered_but_importable_edge_still_selects(
        monkeypatch, tmp_path):
    """CI-06 conservatism as code: a change to a file no indexed test
    executed falls back to the wide selection, so the static importer
    still selects even though coverage never saw the edge."""
    _leaf_tree(tmp_path / "tree")
    (tmp_path / "tree" / "library" / "hidden.py").write_text(
        "SECRET = 1\n", encoding="utf-8")
    (tmp_path / "tree" / "tests" / "test_hidden.py").write_text(
        "import hidden\n\n\ndef test_sees():\n"
        "    assert hidden.SECRET == 1\n",
        encoding="utf-8")
    module = _cov_module(monkeypatch, tmp_path, "tree")
    _write_cov_index(tmp_path / "tree", tmp_path / "coverage.json",
                     _leaf_index())
    changed = ["library/hidden.py"]
    static = module.select(changed, use_coverage=False)
    assert static == ["tests/test_hidden.py"]
    assert module.select(changed) == static


def test_deleting_the_index_restores_the_static_selection(
        monkeypatch, tmp_path, capsys):
    """CI-06 acceptance, fallback half: deleting the index restores
    the exact current selection byte-for-byte - the list and the CLI
    stdout both match `--no-coverage`."""
    import os
    _leaf_tree(tmp_path / "tree")
    module = _cov_module(monkeypatch, tmp_path, "tree")
    _write_cov_index(tmp_path / "tree", tmp_path / "coverage.json",
                     _leaf_index())
    changed = ["library/leaf.py"]
    assert module.select(changed) == ["tests/test_a_leaf.py"]
    assert module.main(changed) == 0
    narrowed_out = capsys.readouterr().out
    assert module.main(["--no-coverage", *changed]) == 0
    static_out = capsys.readouterr().out
    assert narrowed_out != static_out
    os.unlink(tmp_path / "coverage.json")
    assert module.select(changed) == ["tests/test_a_leaf.py",
                                      "tests/test_b_leaf.py"]
    assert module.main(changed) == 0
    assert capsys.readouterr().out == static_out
    assert module.select(changed) == module.select(
        changed, use_coverage=False)


def test_dynamic_import_signal_falls_back_wide(monkeypatch, tmp_path):
    """A changed file that reaches modules its stems do not name
    (`importlib`) cannot be narrowed: the runtime edge is invisible
    to both the static stems and the last coverage run."""
    _leaf_tree(tmp_path / "tree")
    (tmp_path / "tree" / "library" / "dynmod.py").write_text(
        "import importlib\n\nmod = importlib.import_module(\"os\")\n"
        "VALUE = 1\n",
        encoding="utf-8")
    for name in ("test_dyn_a.py", "test_dyn_b.py"):
        (tmp_path / "tree" / "tests" / name).write_text(
            "import dynmod\n\n\ndef test_it():\n"
            "    assert dynmod.VALUE == 1\n",
            encoding="utf-8")
    module = _cov_module(monkeypatch, tmp_path, "tree")
    tests_map = _leaf_index()
    tests_map["tests/test_dyn_a.py::test_it"] = {
        "files": ["library/dynmod.py", "tests/test_dyn_a.py"],
        "lines": {"library/dynmod.py": [1, 3, 4]}}
    tests_map["tests/test_dyn_b.py::test_it"] = {
        "files": ["tests/test_dyn_b.py"],
        "lines": {"tests/test_dyn_b.py": [1, 4]}}
    _write_cov_index(tmp_path / "tree", tmp_path / "coverage.json",
                     tests_map)
    changed = ["library/dynmod.py"]
    static = module.select(changed, use_coverage=False)
    assert static == ["tests/test_dyn_a.py", "tests/test_dyn_b.py"]
    assert module.select(changed) == static


def test_one_wide_change_poisons_the_whole_selection(
        monkeypatch, tmp_path):
    """Any fixture/config/native/env change falls back to the wide
    selection - even beside a narrowable leaf change that would narrow
    on its own."""
    _leaf_tree(tmp_path / "tree")
    (tmp_path / "tree" / "tests" / "conftest.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tree" / "pyproject.toml").write_text(
        "[tool.pytest]\n", encoding="utf-8")
    (tmp_path / "tree" / "native").mkdir()
    (tmp_path / "tree" / "native" / "x.rs").write_text(
        "fn main() {}\n", encoding="utf-8")
    (tmp_path / "tree" / "Dockerfile").write_text(
        "FROM python:3.12\n", encoding="utf-8")
    module = _cov_module(monkeypatch, tmp_path, "tree")
    _write_cov_index(tmp_path / "tree", tmp_path / "coverage.json",
                     _leaf_index())
    assert module.select(["library/leaf.py"]) == ["tests/test_a_leaf.py"]
    for wide in ("tests/conftest.py", "pyproject.toml",
                 "native/x.rs", "Dockerfile"):
        changed = ["library/leaf.py", wide]
        assert module.select(changed) == module.select(
            changed, use_coverage=False), wide


def test_stale_index_falls_back_wide(monkeypatch, tmp_path):
    """An index versioned for another tree never narrows: staleness
    refuses exactly like a missing index."""
    _leaf_tree(tmp_path / "tree")
    module = _cov_module(monkeypatch, tmp_path, "tree")
    _write_cov_index(tmp_path / "tree", tmp_path / "coverage.json",
                     _leaf_index())
    monkeypatch.setenv("VEP_COVERAGE_TREE_SHA", "sha-some-other-tree")
    changed = ["library/leaf.py"]
    assert module.select(changed) == module.select(
        changed, use_coverage=False)
    module.select(changed)
    assert module._LAST_COVERAGE_REPORT["reason"] == "fingerprint mismatch"


def test_missing_coverage_index_reports_no_compatible_baseline(
        monkeypatch, tmp_path):
    monkeypatch.setenv("VEP_COVERAGE_INDEX_FILE",
                       str(tmp_path / "missing-index.json"))
    module = _load_selector()
    module.select(["library/tools/local_ci/selection.py"])
    assert module._LAST_COVERAGE_REPORT["reason"] == "no compatible baseline"


def test_narrowing_report_counts_are_exact():
    """The precision/recall report is arithmetic over the two sets:
    coverage only ever removes, so `added` is empty by construction."""
    module = _load_selector()
    info = module.coverage_narrowing_report(
        ["tests/test_a_leaf.py", "tests/test_b_leaf.py"],
        ["tests/test_a_leaf.py"])
    assert (info["static_count"], info["narrowed_count"]) == (2, 1)
    assert info["subset"] is True
    assert info["strictly_narrower"] is True
    assert info["added"] == []
    assert info["dropped"] == ["tests/test_b_leaf.py"]
