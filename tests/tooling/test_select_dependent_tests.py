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
