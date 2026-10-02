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
    if getattr(op, "is_prompt", False):
        return _prompt_violation(op)
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


def _prompt_violation(op) -> str:
    """None if a prompt capability names its step's own declared prompt.

    The third legal shape, per the captain's 2026-09-23 ruling that a
    capability may be a prompt: the operation names no function, so
    there is no callable to locate - and that is exactly what would
    make an unchecked prompt entry a hole (any Python step relabelled
    as a prompt to dodge `run`). So the gate checks the tree's own
    declaration instead: the prompt file must exist under the owning
    step, that step's manifest must declare `runtime: llm` with this
    very file as its entry point, and no function may be named beside
    it. A prompt entry that fails any of those is refused like any
    other second implementation.
    """
    owner = STEPS / op.owning_dir

    if not owner.is_dir():
        return f"owning_dir {op.owning_dir!r} is not a step directory"

    prompt = owner / op.body
    if not prompt.is_file():
        return (f"{op.name} names prompt {op.body!r} but {prompt} is "
                f"not a file - a prompt capability names the owning "
                f"step's own prompt file")

    try:
        manifest = json.loads(
            (owner / "manifest.json").read_text(encoding="utf-8"))
    except OSError:
        return (f"{op.name} names a prompt but {op.owning_dir} has no "
                f"manifest declaring it one")
    declared = ((manifest.get("implementation") or {}).get("default")
                or {})
    if declared.get("runtime") != "llm" or \
            declared.get("entry_point") != op.body:
        return (f"{op.name} names prompt {op.body!r} but "
                f"{op.owning_dir}/manifest.json does not declare it: "
                f"implementation.default is {declared!r}, not "
                f"{{'runtime': 'llm', 'entry_point': {op.body!r}}}")
    if op.attr:
        return (f"{op.name} is a prompt capability yet names attr "
                f"{op.attr!r} - a prompt names no function")
    return None










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


def test_the_rule_refuses_a_prompt_no_manifest_declares():
    """AGENTS.md 10.4 on the prompt shape: the manifest check must fail.

    Planted rather than trusted - a prompt branch that only ever sees
    `creative.direct` is a check nobody has seen refuse. `scan` is a
    deterministic step with no prompt file, so a prompt entry owned by
    it is refused naming the file.
    """
    planted = operations.Operation(
        name="planted.prompt", summary="a prompt nobody declared",
        owning_dir="step_1_01_scan_project",
        body="handoff.md", attr="", produces=(), consumes=())
    assert "handoff.md" in (violation(planted) or "")


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
        operation="subtitles.render", legacy_node="render_subtitles",
        scope=operations.scope_mod.project(), status=operations.REFUSED,
        unsatisfied=(_fake_requirement("transcript", "temporal_index"),))
    assert result.refused and not result.completed
    reason = result.refusal_reason()
    assert "transcript" in reason and "temporal_index" in reason, reason


def test_a_refusal_must_say_why():
    """The type refuses to be constructed as an unexplained refusal."""
    with pytest.raises(operations.OperationError):
        operations.OperationResult(
            operation="x", legacy_node="render_subtitles",
            scope=operations.scope_mod.project(), status=operations.REFUSED)


def test_produced_nothing_separates_the_three_cases():
    """`output_empty` must distinguish produced-something, produced-nothing
    and refused - the empty-side-passes shape depends on it."""
    scope = operations.scope_mod.project()
    common = dict(operation="o", legacy_node="render_subtitles", scope=scope)
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
            operation="o", legacy_node="render_subtitles",
            scope=operations.scope_mod.project(), status="maybe")










# ── The skill is a PROJECTION of the registry, never a source ───────


SKILL = REPO / ".agents" / "skills" / "pipeline_operations" / "SKILL.md"






