"""The contract auditor holds, and each of its sections can FAIL.

`library/tools/contract_audit.py` evaluates the capability graph once -
producers, consumers, requirements, scopes, outputs, effects and machine
needs - while the slow survey checks readers and declarations.  These
cases plant broken inputs into that public audit surface so a green tree
check cannot stand in for proof that the guard refuses.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from library.tools import (
    capabilities,
    contract_audit,
    operations,
    project_layout,
    requirements,
)


def test_the_graph_is_sound():
    assert contract_audit.problems() == []


def test_the_surveys_agree():
    assert contract_audit.survey_problems() == []


def _ghost_req(**over):
    """A requirement shaped like the registry's, naming a ghost."""
    base = {
        "name": "state.ghost.key",
        "kind": "state_key",
        "describe": "a planted ghost",
        "produced_by": ("scan",),
        "consumers": ("scan",),
        "check": lambda ctx: None,
        "refuting_context": object(),
        "satisfying_context": object(),
        "consumed_key": "ghost_key",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _problems_with_reqs(extra):
    real = requirements.all_requirements()
    return contract_audit.problems(reqs=list(real) + list(extra))


def test_a_consumer_that_is_no_node_is_named():
    found = _problems_with_reqs([_ghost_req(consumers=("ghost",))])
    assert any("consumed by 'ghost'" in p for p in found), found


def test_a_producer_that_is_no_node_is_named():
    found = _problems_with_reqs([_ghost_req(produced_by=("ghost",))])
    assert any("names producer 'ghost'" in p for p in found), found


def test_a_precondition_nothing_consumes_is_named():
    found = _problems_with_reqs([_ghost_req(consumers=(), produced_by=("scan",))])
    assert any("consumed by nothing" in p for p in found), found


def test_a_goal_nothing_consumes_is_not_a_dead_precondition():
    found = _problems_with_reqs(
        [_ghost_req(kind="verdict", consumers=(), produced_by=("validate",))]
    )
    assert not any("consumed by nothing" in p for p in found), found


def test_a_need_nothing_can_satisfy_is_named(monkeypatch):
    ghost = _ghost_req(produced_by=())
    monkeypatch.setattr(requirements, "EXTERNAL_STATE", ())
    monkeypatch.setattr(requirements, "derive_runner_injected_keys", lambda *a, **k: [])
    found = contract_audit.problems(reqs=[ghost])
    assert any("produced by nothing" in p for p in found), found


def test_an_external_need_is_not_unsatisfiable():
    found = _problems_with_reqs([_ghost_req(produced_by=())])
    # The ghost is neither hand-declared external nor runner-injected,
    # so it still fails - while the tree's own three pass (the sound
    # test above proves it).  This pins the direction: externality is
    # declared, never assumed.
    assert any("produced by nothing" in p for p in found), found


def test_a_requirement_without_witnesses_is_named():
    found = _problems_with_reqs(
        [_ghost_req(refuting_context=None, satisfying_context=None)]
    )
    assert any("no refuting context" in p for p in found), found
    assert any("no satisfying context" in p for p in found), found


def test_a_scope_outside_the_vocabulary_is_named():
    op = replace(operations.get("footage.scan"), scopes=("galaxy",))
    found = contract_audit.problems(
        registry=[o for o in operations.all() if o.name != "footage.scan"] + [op]
    )
    assert any("scope 'galaxy'" in p for p in found), found


@pytest.mark.parametrize("changes, expected", [
    ({}, "registered twice"),
    (
        {"name": "footage.ghost", "attr": "no_such_fn"},
        "defines no top-level",
    ),
    ({"name": "footage.ghost", "body": "nope.py"}, "does not exist"),
    (
        {"name": "footage.ghost", "owning_dir": "step_9_99_ghost"},
        "no process runs",
    ),
    ({"name": "footage ghost"}, "not a stable token"),
    (
        {"name": "footage.ghost", "produces": ("no_such_output",)},
        "declares no output for",
    ),
    (
        {"name": "footage.ghost", "consumes": ("no_such_input",)},
        "which no requirement",
    ),
])
def test_a_broken_capability_is_named(changes, expected):
    source = operations.get("footage.scan")
    planted = replace(source, **changes) if changes else source
    found = contract_audit.problems(registry=operations.all() + (planted,))
    assert any(expected in p for p in found), found


def test_a_capability_that_produces_nothing_needs_a_reason(monkeypatch):
    monkeypatch.delitem(operations.EMPTY_EFFECT_REASONS, "motion_graphics.render")
    found = contract_audit.problems()
    assert any(
        "motion_graphics.render: produces no requirement" in p for p in found
    ), found


def test_an_artifact_without_an_owner_is_named(monkeypatch):
    area = next(iter(project_layout.AREAS))
    monkeypatch.setitem(
        project_layout.AREAS,
        area,
        replace(project_layout.AREAS[area], step="ghost"),
    )
    found = contract_audit.problems()
    assert any("owned by 'ghost'" in p for p in found), found


def test_an_uncited_heavy_lock_site_is_named(monkeypatch):
    monkeypatch.delitem(capabilities.HEAVY_LOCK_SITES, "reel.build")
    found = contract_audit.problems()
    assert any(
        "rebuild_reels_in_project is cited by no capability" in p for p in found
    ), found


def test_a_cited_heavy_lock_site_that_takes_no_lock_is_named(monkeypatch):
    monkeypatch.setitem(
        capabilities.HEAVY_LOCK_SITES,
        "reel.build",
        ("library.tools.reel_build:build_reels_typo",),
    )
    found = contract_audit.problems()
    assert any("build_reels_typo does not take" in p for p in found), found


def test_an_unregistered_step_directory_is_named(monkeypatch):
    monkeypatch.setattr(
        capabilities,
        "unregistered_step_dirs",
        lambda: ("step_9_99_orphan",),
    )
    found = contract_audit.problems()
    assert any(
        "step directory step_9_99_orphan is reached by no capability" in p
        for p in found
    ), found


def test_a_capability_with_no_scope_is_named():
    op = replace(operations.get("footage.scan"), scopes=())
    found = contract_audit.problems(
        registry=[o for o in operations.all() if o.name != "footage.scan"] + [op]
    )
    assert any("declares no scope" in p for p in found), found


def test_a_machine_need_that_is_no_environment_requirement_is_named(monkeypatch):
    real = requirements.all_requirements()
    some_state_need = next(
        r.name for r in real if r.kind == requirements.KIND_STATE_KEY and r.produced_by
    )
    op = operations.get("footage.scan")
    ghost = SimpleNamespace(
        name=op.name, scopes=op.scopes, assumes_machine=(some_state_need,)
    )
    monkeypatch.setattr(operations, "all", lambda: (ghost,))
    monkeypatch.setattr("library.tools.capabilities.problems", lambda registry=None: [])
    found = contract_audit.problems()
    assert any("assumes machine need" in p for p in found), found
    assert some_state_need in str(found)


def test_a_machine_requirement_without_a_check_is_named():
    ghost = _ghost_req(
        kind=requirements.KIND_ENVIRONMENT, check=None, describe="fix it thus"
    )
    found = _problems_with_reqs([ghost])
    assert any("names no check" in p for p in found), found


def test_a_machine_requirement_without_a_remedy_is_named():
    ghost = _ghost_req(kind=requirements.KIND_ENVIRONMENT, describe="")
    found = _problems_with_reqs([ghost])
    assert any("names no remedy" in p for p in found), found
