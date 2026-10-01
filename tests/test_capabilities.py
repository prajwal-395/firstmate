"""The capability registry's invariants hold, and each one can FAIL.

These replace assertions that protected good properties through obsolete
structure (punch list item 18): "every step directory belongs to a DAG"
becomes "every executable thing is registered", and the registry-wide
checks `#1351` cut with the DAG-shaped pins - real callables, unique ids,
real owning nodes, the empty-effect tripwire - come back as
`capabilities.problems()`.  Every invariant is planted broken below,
because a gate never seen refusing reads as coverage (AGENTS.md 10.4).
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from library.tools import (
    capabilities,
    dag_adapter,
    operations,
    project_layout,
    requirements,
)


def test_the_registry_is_sound():
    assert capabilities.problems() == []


def _scan():
    return operations.get("footage.scan")


@pytest.mark.parametrize("planted, expected", [
    (lambda: replace(_scan()), "registered twice"),
    (lambda: replace(_scan(), name="footage.ghost", attr="no_such_fn"),
     "defines no top-level 'no_such_fn'"),
    (lambda: replace(_scan(), name="footage.ghost", body="nope.py"),
     "does not exist"),
    (lambda: replace(_scan(), name="footage.ghost", owning_node="ghost"),
     "not a node of any process"),
    (lambda: replace(_scan(), name="footage ghost"), "not a stable token"),
])
def test_a_broken_capability_is_named(planted, expected):
    found = capabilities.problems(operations.all() + (planted(),))
    assert any(expected in p for p in found), found


def test_a_capability_that_produces_nothing_must_say_why(monkeypatch):
    monkeypatch.delitem(operations.EMPTY_EFFECT_REASONS,
                        "motion_graphics.render")
    assert any("motion_graphics.render: produces no requirement" in p
               for p in capabilities.problems())


def test_an_unregistered_step_directory_is_named(tmp_path, monkeypatch):
    (tmp_path / "step_9_99_orphan").mkdir()
    monkeypatch.setattr(capabilities, "STEPS_ROOT", tmp_path)
    assert capabilities.unregistered_step_dirs() == ("step_9_99_orphan",)


def test_a_producer_without_identity_is_named(monkeypatch):
    ghost = SimpleNamespace(name="state.x.y", produced_by=("ghost",))
    real = requirements.all_requirements()
    monkeypatch.setattr(requirements, "all_requirements",
                        lambda *a, **k: list(real) + [ghost])
    assert any("names producer 'ghost'" in p
               for p in capabilities.problems())


def test_an_artifact_without_an_owner_is_named(monkeypatch):
    some = next(iter(project_layout.AREAS))
    monkeypatch.setitem(project_layout.AREAS, some, replace(
        project_layout.AREAS[some], step="ghost"))
    assert any("owned by 'ghost'" in p for p in capabilities.problems())


def test_a_capability_without_a_legacy_node_refuses_node_keyed_questions():
    """Requirements are still node-keyed, so an empty answer would read as
    'requires nothing' and let a composer schedule it anywhere."""
    nodeless = replace(_scan(), owning_node="")
    with pytest.raises(dag_adapter.NoLegacyNode, match="footage.scan"):
        nodeless.requires                                     # noqa: B018
