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

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_selector():
    """`scripts/select_dependent_tests.py` without touching `sys.path`.

    An import-time `sys.path.insert` of `scripts/` is process-global:
    collection order would decide what a later bare name binds to
    (`tests/test_static_check.py`). The agents-md gate loads
    its scripts the same way (`tests/test_agents_md_gates.py`).
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


def test_the_resolver_change_selects_the_speaker_contract_test():
    """#1258's edge: the changed module is `motion_graphics_plan`, the
    missed test was `test_speaker_identity.py` - which never imports it
    at top level, only inside a function."""
    assert "tests/test_speaker_identity.py" in select(
        ["library/tools/motion_graphics_plan.py"])


def test_the_resolver_change_selects_the_explainer_contract_test():
    """#1270's edge: the same refusal caught a second producer, and the
    repair lane's selection missed `test_explainer_plan.py` too."""
    assert "tests/test_explainer_plan.py" in select(
        ["library/tools/motion_graphics_plan.py"])


def test_a_producer_change_selects_the_resolver_contract_set():
    """The backward hook: changing a producer runs every contract test
    of the resolver it feeds, not just the producer's own file."""
    selected = select(["library/tools/speaker_identity.py"])
    for contract in MG_CONTRACT:
        assert contract in selected, contract
