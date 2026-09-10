"""Ruling 1: an operation names a step's own work, and never re-implements it.

    "An operation is a named, scoped entry point into an existing step's
     own code."

The failure this exists to prevent is not a crash.  It is an operation
called `subtitles.plan` that quietly does its own caption layout, drifts
from step 4.01 over a few months, and produces a different answer from
the same project - with nothing in the suite noticing, because both
halves pass their own tests.  The pipeline would then have two
implementations of one decision and no way to say which is the pipeline.

So the rule is mechanical: every `Operation.run` must resolve into the
OWNING step's own directory, or into a `library/tools/` module that step
already imports.  Anything else is a second implementation.

`test_the_rule_refuses_*` are not decoration.  A gate that cannot fail
reads as coverage and is worse than no gate (AGENTS.md 10.4), so this
file plants the three violations it exists to catch and asserts each one
is refused.
"""
import ast
import functools
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from library.tools import operations

REPO = Path(__file__).resolve().parents[1]
STEPS = REPO / "library" / "steps"
TOOLS = REPO / "library" / "tools"
DAG = REPO / "library" / "processes" / "edit_video" / "dag.json"


def tools_imported_by(step_dir: Path) -> set:
    """Every library/tools module the step's OWN bodies import, either spelling.

    THE TRAP, measured: five step directories import library/tools two
    ways in one tree, because a `sys.path` insert lets them write the
    short form.  `step_5_04_compile_manifest/step.py` does both in a
    single file - `from tools.frame_utils import ...` at :87 beside
    `from library.tools.music_bed import ...` at :92.  A version of this
    helper that read only the long spelling would refuse every operation
    owned by 5 of 29 steps, and the failure would look like a Ruling 1
    violation rather than like a bug in the test.

    THE SECOND TRAP, measured 2026-09-10: `step_4_01_plan_subtitles`
    does `from library.tools import captain_edits` (PR #857).  A version
    of this     helper that maps a bare-package import to a match-everything
    wildcard would accept EVERY tools module for that step, and
    `test_the_rule_refuses_a_tool_the_owning_step_does_not_import`
    failed on a clean base proving exactly that.  There is no
    `library/tools/__init__.py` - it is a namespace package - so
    `from library.tools import NAME` can only bind the SUBMODULE, and
    the helper records `tools.NAME` precisely.  A name with no such
    submodule on disk keeps the permissive `tools` wildcard rather
    than falsely refusing what the helper cannot resolve.
    """
    mods = set()
    for path in step_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in ("tools", "library.tools"):
                    for alias in node.names:
                        if alias.name == "*":
                            mods.add("tools")
                        elif (TOOLS / f"{alias.name}.py").is_file() or (
                                TOOLS / alias.name).is_dir():
                            mods.add(f"tools.{alias.name}")
                        else:
                            mods.add("tools")
                    continue
                named = [node.module]
            elif isinstance(node, ast.Import):
                named = [a.name for a in node.names]
            else:
                continue
            for module in named:
                if module.startswith("library.tools."):
                    mods.add(module[len("library."):])      # tools.foo.bar
                elif module.startswith("tools."):
                    mods.add(module)
                elif module in ("tools", "library.tools"):
                    mods.add("tools")
    return mods


def _source_file(run: Callable) -> Path:
    while isinstance(run, functools.partial):
        run = run.func
    found = inspect.getsourcefile(inspect.unwrap(run))
    assert found, "cannot locate the source of this run callable"
    return Path(found).resolve()


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(REPO / "library").with_suffix("").parts)


def violation(op) -> str:
    """None if the operation obeys Ruling 1; otherwise why it does not."""
    source = _source_file(op.run)
    owner = STEPS / op.owning_dir

    if not owner.is_dir():
        return f"owning_dir {op.owning_dir!r} is not a step directory"

    # A callable defined in the registry itself is a second
    # implementation by definition - this is the case the rule exists for.
    if source == (TOOLS / "operations.py"):
        return "run is defined in operations.py - the registry owns logic"

    if STEPS in source.parents:
        if source.parent != owner:
            return (f"run lives in {source.parent.name} but the operation "
                    f"is owned by {op.owning_dir}; an operation may not "
                    f"reach into another step's body")
        return None

    if TOOLS in source.parents or source.parent == TOOLS:
        if _dotted(source) in tools_imported_by(owner) or \
                "tools" in tools_imported_by(owner):
            return None
        return (f"run resolves to {_dotted(source)}, which {op.owning_dir} "
                f"does not import. Either the step should use it, or this "
                f"operation is a second implementation of the step's work.")

    return (f"run resolves to {source}, which is neither library/steps/ "
            f"nor library/tools/")


# ── The rule, over the real registry ────────────────────────────────


@pytest.mark.parametrize("op", operations.all(), ids=lambda o: o.name)
def test_no_operation_introduces_a_second_implementation(op):
    assert violation(op) is None, f"{op.name}: {violation(op)}"


@pytest.mark.parametrize("op", operations.all(), ids=lambda o: o.name)
def test_every_operation_resolves_to_a_real_callable(op):
    """`run` is a property, so a typo in `attr` is only found by resolving."""
    assert callable(op.run), f"{op.name}: run did not resolve to a callable"


def test_every_owning_node_is_a_real_dag_node():
    """Sixteen of the runner's eighteen per-step services are keyed by the
    node id, so an operation with an unreal owning node loses its place in
    the ledgers, the run status, the gates and the collectors.

    Read across EVERY process, not just edit_video. `reel.build` and
    `reel.verify` are owned by nodes of `library/processes/reels`, and a
    check that knew only one graph would refuse two operations whose
    nodes are real - the same confidently-wrong answer in the opposite
    direction from the one this test exists to catch.
    """
    from library.tools import processes

    nodes = set(processes.node_owners())
    assert nodes >= {n["id"] for n in json.loads(DAG.read_text())["nodes"]}, (
        "the process registry no longer sees edit_video's own nodes")
    for op in operations.all():
        assert op.owning_node in nodes, (
            f"{op.name} claims owning_node {op.owning_node!r}, "
            f"which is not a DAG node of any process")


def test_no_two_processes_share_a_node_id():
    """An operation names a node, and a node must mean one thing.

    Node ids key the two ledgers, the run status, the review gate, the
    marker routing, the step export and `step_outputs` in
    `pipeline_data.json`, and a derived requirement is NAMED after one
    (`state.<consumer>.<key>`). Two processes sharing an id would give
    two different steps one slot in all of them.
    """
    from library.tools import processes

    processes.assert_node_ids_are_unique()          # must not raise

    seen = {}
    for pid in processes.process_ids():
        for node in processes.load_dag(pid)["nodes"]:
            assert node["id"] not in seen, (
                f"{node['id']!r} is declared by {seen[node['id']]!r} and "
                f"{pid!r}")
            seen[node["id"]] = pid


def test_the_uniqueness_gate_can_fail(monkeypatch):
    """AGENTS.md 10.4 applied to the gate above.

    Planted rather than trusted: a check that only ever runs against a
    tree which happens to be unique is a check nobody has seen refuse.
    """
    from library.tools import processes

    monkeypatch.setattr(processes, "load_dag", lambda pid: {
        "nodes": [{"id": "shared", "name": "x", "step_ref": "steps/x"}]})
    with pytest.raises(processes.ProcessError, match="unique"):
        processes.assert_node_ids_are_unique()
    monkeypatch.undo()
    processes.assert_node_ids_are_unique()          # restored, still clean


def test_operation_names_are_unique():
    seen = [op.name for op in operations.all()]
    assert len(seen) == len(set(seen)), "two operations share a name"


def test_every_operation_declares_at_least_one_scope():
    for op in operations.all():
        assert op.scopes, f"{op.name} declares no scope"
        for kind in op.scopes:
            assert kind in operations.scope_mod.KINDS, (
                f"{op.name} declares unknown scope {kind!r}")


def test_requirements_are_empty_until_increment_3_owns_them():
    """`requires` is declared and empty ON PURPOSE.

    Executable prerequisites are `library/tools/requirements.py`.  If this
    starts failing because someone populated `requires` with a prose
    string or a hand-rolled checker, that is the defect the refactor
    exists to remove reappearing in a new directory - not a stale test.
    """
    for op in operations.all():
        for requirement in op.requires:
            assert not isinstance(requirement, str), (
                f"{op.name} declares a PROSE requirement {requirement!r}. "
                f"Requirements are executable and owned by "
                f"library/tools/requirements.py.")


# ── The gate must be able to FAIL (AGENTS.md 10.4) ──────────────────


@dataclass(frozen=True)
class _Planted:
    name: str
    owning_dir: str
    run: Callable


def test_the_rule_refuses_a_tool_the_owning_step_does_not_import():
    from library.tools.music_bed import resolve_bed
    planted = _Planted("planted.bed", "step_4_01_plan_subtitles", resolve_bed)
    assert "does not import" in (violation(planted) or "")


def test_the_package_spelling_imports_the_named_submodule_only():
    """The bare-package spelling is precise, not a wildcard (2026-09-10).

    `step_4_01_plan_subtitles` does `from library.tools import
    captain_edits` (PR #857): the gate must see the submodule the step
    really uses without waving through every other tools module.  If
    this fails, `tools_imported_by` has regressed to the match-everything
    reading and the refusal test above passes only by accident.
    """
    from library.tools.captain_edits import apply_caption_fixes
    found = tools_imported_by(STEPS / "step_4_01_plan_subtitles")
    assert "tools.captain_edits" in found
    assert "tools" not in found, (
        "a bare `tools` wildcard is present, so any tools module would "
        "pass the gate for this step")
    assert violation(_Planted("legal.pkg", "step_4_01_plan_subtitles",
                              apply_caption_fixes)) is None


def test_the_rule_refuses_reaching_into_another_steps_body():
    from library.steps.step_5_03_creative_cohesion.step import (
        review_creative_cohesion,
    )
    planted = _Planted("planted.cohesion", "step_4_01_plan_subtitles",
                       review_creative_cohesion)
    assert "owned by" in (violation(planted) or "")


def test_the_rule_refuses_a_callable_the_registry_defines_itself():
    planted = _Planted("planted.wrapped", "step_4_01_plan_subtitles",
                       lambda **kw: None)
    assert "neither library/steps/" in (violation(planted) or "")


def test_the_rule_accepts_the_two_legal_shapes():
    """Both halves of the rule, so a refusal-only test cannot pass by
    refusing everything."""
    from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
    from library.tools.safe_area import resolve_safe_area

    # the owning step's own function
    assert violation(_Planted("legal.own", "step_4_01_plan_subtitles",
                              generate_subtitles)) is None
    # a library/tools module the owning step really imports
    assert violation(_Planted("legal.tool", "step_4_01_plan_subtitles",
                              resolve_safe_area)) is None


def test_the_both_spellings_trap_is_actually_handled():
    """The helper must see the short `tools.` spelling.

    If this fails, `tools_imported_by` has been narrowed to the long form
    and every operation owned by compile_manifest, mesh_spine,
    render_subtitles, render_motion_graphics or render would be refused
    for a reason that is not true.
    """
    found = tools_imported_by(STEPS / "step_5_04_compile_manifest")
    assert "tools.frame_utils" in found, (
        "the short `from tools.X import` spelling is not being read")
    assert "tools.music_bed" in found, (
        "the long `from library.tools.X import` spelling is not being read")


# ── The result type increment 6's hook conditions are built from ────


def _fake_requirement(name, produced_by):
    @dataclass(frozen=True)
    class _Req:
        name: str
        produced_by: str
    return _Req(name, produced_by)


def test_a_refusal_names_a_producer():
    """A refusal that only says "missing" is the prose prerequisite in a
    new costume. `requirement_unsatisfied` has to be able to route."""
    result = operations.OperationResult(
        operation="subtitles.render", owning_node="render_subtitles",
        scope=operations.scope_mod.project(), status=operations.REFUSED,
        unsatisfied=(_fake_requirement("transcript", "temporal_index"),))
    assert result.refused and not result.completed
    reason = result.refusal_reason()
    assert "transcript" in reason and "temporal_index" in reason, reason


def test_a_refusal_must_say_why():
    """The type refuses to be constructed as an unexplained refusal."""
    with pytest.raises(operations.OperationError):
        operations.OperationResult(
            operation="x", owning_node="render_subtitles",
            scope=operations.scope_mod.project(), status=operations.REFUSED)


def test_produced_nothing_separates_the_three_cases():
    """`output_empty` must distinguish produced-something, produced-nothing
    and refused - the empty-side-passes shape depends on it."""
    scope = operations.scope_mod.project()
    common = dict(operation="o", owning_node="render_subtitles", scope=scope)
    real = operations.OperationResult(
        status=operations.COMPLETED, payload={"segments": [1]}, **common)
    empty = operations.OperationResult(
        status=operations.COMPLETED, payload={}, **common)
    hollow = operations.OperationResult(
        status=operations.COMPLETED, payload={"segments": []},
        hollow=("segments",), **common)
    refused = operations.OperationResult(
        status=operations.REFUSED, error="no transcript", **common)

    assert real.produced_nothing is False
    assert empty.produced_nothing is True
    assert hollow.produced_nothing is True, (
        "a completion the hollow check flagged must read as produced-nothing")
    assert refused.produced_nothing is True


def test_an_unknown_status_is_refused():
    with pytest.raises(operations.OperationError):
        operations.OperationResult(
            operation="o", owning_node="render_subtitles",
            scope=operations.scope_mod.project(), status="maybe")


def test_every_call_carries_both_names():
    """Increment 2's provenance refuses an operation with no owning step,
    because sixteen of the eighteen per-step services are node-keyed."""
    for op in operations.all():
        identity = op.call_identity()
        assert identity["operation_id"] == op.name
        assert identity["step_id"] == op.owning_node
        assert identity["step_id"], f"{op.name} would arrive with no step id"


def test_the_per_segment_seam_is_region_scoped():
    """Increment 5 reaches render_one_segment through Scope/Region."""
    segment = operations.get("subtitles.render_segment")
    assert operations.REGION in segment.scopes
    assert segment.attr == "render_one_segment"
    where = operations.scope_mod.region("45.0-72.0")
    segment.check_scope(where)          # must not raise


def test_plan_subtitles_now_offers_region():
    """The scaffold that refused this is GONE, and increment 5 removed it.

    `test_plan_subtitles_does_not_yet_offer_region` stood here and
    refused REGION on `subtitles.plan`, because 4.01 numbered its cards
    from a run-global counter: a region-scoped plan renumbered every card
    after the region. Measured on project 001, changing one block moved
    13 ids in blocks that had not changed.

    Block-local caption ids (increment 1, PR #540) removed the cause -
    the same measurement now moves 0 - so the guard had served its
    purpose and deleting it is removing a scaffold, not weakening a
    safety check. This test replaces it so the capability cannot quietly
    regress to refusing.
    """
    plan = operations.get("subtitles.plan")
    assert operations.REGION in plan.scopes
    plan.check_scope(operations.scope_mod.region("45.0-72.0"))   # must not raise


def test_a_region_only_operation_refuses_project_scope():
    """`subtitles.splice` at project scope would be a whole-plan
    overwrite, which `subtitles.plan` already is. Offering both names for
    one behaviour is the second implementation Ruling 1 forbids."""
    for name in ("subtitles.splice", "transcript.reindex",
                 "transcript.splice"):
        op = operations.get(name)
        assert op.scopes == (operations.REGION,), name
        with pytest.raises(operations.ScopeNotSupported):
            op.check_scope(operations.scope_mod.project())


# ── The skill is a PROJECTION of the registry, never a source ───────


SKILL = REPO / ".agents" / "skills" / "pipeline_operations" / "SKILL.md"


def test_the_checked_in_skill_matches_what_the_registry_emits():
    """Byte-identical, or the skill is describing a pipeline that is not
    this one.

    A hand-written skill would state its prerequisites in prose - the
    exact defect the refactor removes - and 90 prose preconditions are
    already evaluated by nothing. Generated, the prose cannot drift and
    is never the contract.

    If this fails, do not edit SKILL.md: run
    `python3 -m library.tools.operations --emit-skill > .agents/skills/pipeline_operations/SKILL.md`
    """
    assert SKILL.is_file(), f"the generated skill is missing: {SKILL}"
    on_disk = SKILL.read_text(encoding="utf-8")
    emitted = operations.emit_skill()
    assert on_disk.rstrip("\n") == emitted.rstrip("\n"), (
        "the checked-in skill has drifted from the registry; regenerate it "
        "rather than editing it by hand")


def test_the_skill_names_every_operation():
    """A projection that silently dropped rows would read as a smaller
    pipeline than there is."""
    text = SKILL.read_text(encoding="utf-8")
    for op in operations.all():
        assert f"`{op.name}`" in text, f"{op.name} is missing from the skill"


def test_the_skill_states_no_prerequisite_in_prose():
    """The whole point. Requirements are executable and owned by
    library/tools/requirements.py; a skill that describes them in prose
    reproduces the defect in a new directory while looking like progress.
    """
    text = SKILL.read_text(encoding="utf-8").lower()
    for banned in ("prerequisite", "precondition", "you must first",
                   "make sure you have", "requires that"):
        assert banned not in text, (
            f"the generated skill states a prerequisite in prose "
            f"({banned!r}). Requirements are executable.")
