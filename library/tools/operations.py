"""The named things an operator can ask the pipeline to do, and nothing else.

Ruling 1, which this module exists to obey
------------------------------------------
**An operation is a named, scoped entry point into an existing step's own
code.  It owns no logic.**

`Operation.run` POINTS AT the step's own function.  It never wraps it,
never reimplements it, and never adds a rule the step does not already
have.  `tests/test_operations_add_no_second_implementation.py` enforces
that by resolving every `run` back to the file it is defined in and
refusing anything that is not the owning step's own body, or a
`library/tools/` module that step already imports.

The alternative - a verb layer over `library/tools/` - loses for exactly
this reason: it violates Ruling 1 the moment a verb does a step's work,
and the 38 tools that already have a `__main__` block prove that
reachable is not the same as addressable.

Why `owning_node` is not decoration
-----------------------------------
The runner's per-step loop wraps a step body in eighteen services, and
sixteen of them are keyed by the DAG node id: the two ledgers, the run
status, the review gate, the marker routing, the step export, the three
run-level collectors.  An operation that did not declare its owning node
would lose its place in all of them - it would run, and nothing would
record that it had.  So an operation names the node whose decision it
is, and `test_every_owning_node_is_a_real_dag_node` checks the name is
real.

The two services that are NOT node-keyed are the handbrake, which is
keyed by project, and the provenance snapshot, which takes no key at all
because it attributes by diffing the tree.  Provenance is therefore the
one service that already generalises to an operation, which is why the
design threads it first.

What an operation RETURNS, and why it is a type
----------------------------------------------
`OperationResult` is defined here, once, because four of the six hook
conditions in increment 6 are operation-derived - `operation_completed`,
`operation_refused`, `requirement_unsatisfied`, `output_empty` - and not
one of them can be built from a bare dict.  A hook handed a dict cannot
tell a refusal from a completion, cannot name WHICH requirement refused
or who produces it, and cannot tell produced-nothing from
produced-something.  That last one is the empty-side-passes shape the
design exists to stop, so a result type that cannot express it would
reintroduce it at the hook layer.

Defining it anywhere else means increment 6 inventing its own - a second
implementation, which Ruling 1 forbids - so it is here, and it is one
type for every operation.

**A refusal names a producer.**  `unsatisfied` carries the Requirement
objects themselves, not strings, so `refusal_reason()` can say what is
missing AND which step makes it.  A refusal that only says "missing" is
the prose prerequisite in a new costume.

What an operation REQUIRES, and why it is derived
-------------------------------------------------
`Operation.requires` is DERIVED, never hand-written: it is every
requirement in `library/tools/requirements.py` whose `consumers` include
this operation's `owning_node`.  Increment 3 already derives those from
`inputs[].required` and the DAG's `data_mapping`, so an operation
inherits exactly the prerequisites its step has - no more, no less, and
nothing to keep in sync.

Hand-writing them here would put a second requirement vocabulary beside
the one that exists, and the hand-written half would be prose in a
costume.  That is the defect this refactor removes, so `requires` is a
property with no setter and `_REGISTRY` carries no requirement literals.

What an operation EFFECTS, and why two are empty
-------------------------------------------------
`Operation.effect` is the exact mirror of `requires`: every requirement
in `library/tools/requirements.py` whose `produced_by` includes this
operation's `owning_node`.  Same vocabulary, same derivation discipline -
an effect in a different language could never be matched against a
precondition, so nothing composes.

Thirty-seven of the 39 operations have a non-empty effect.  Two are
empty, and the emptiness is TRUE, not a gap:
`run_scope.prerequisites` derives one condition per REQUIRED input, so
a node that no consumer requires anything from produces no requirement.
The two are one kind, verified against the tree (see
`EMPTY_EFFECT_REASONS` for the per-operation evidence):

* ARTIFACT - the product lands on disk for a caller, a gate or Resolve
  placement rather than travelling a DAG edge:
  `motion_graphics.render`, `motion_graphics.render_segment`.
  (`reel.gate_stills`, the third artifact, shares `verify_reels`'
  verdict effect by the node granularity `Operation.effect`
  declares - a stills grab planned as a verdict would be the
  confidently-wrong result this layer exists to stop, so the route
  selector names `reel.verify` and no plan names the grab.)

The two kinds the tree was checked against - VERDICT, the effect that
is pass/fail rather than a state key, and ANALYSIS, the production no
REQUIRED edge carries - are both GONE from the blind set, by the
captain's rulings 2026-09-23: a verdict is expressible as a goal as
its own requirement kind (`requirements.VERDICTS`), and an edge that
may or may not carry state is expressible as a goal as its own
requirement kind (`requirements.OPTIONALS`), so
`sfx_library.validate`, `reel.verify`, `validation.resolve`,
`prosody.analyse`, `color_grade.resolve`, `ocr.extract`,
`transitions.resolve`, `sfx.resolve` and `vfx.resolve` each derive a
non-empty effect from those pools with nothing listed here.
The composition consequence below still holds for the two that remain:
a composer working backwards from requirements alone can never select
them, because artifact productions are invisible as goals.
Hand-writing two effects in a second vocabulary to make the field look
complete would be the defect this module removes, in a new costume.

This follows the layer's own enforced doctrine, not just this
module's taste: `Requirement.__post_init__`
(`library/tools/requirements.py`) refuses a predicate or coverage
requirement with an empty `produced_by` and refuses an environment
requirement with a non-empty one, on the grounds that `produced_by`
models exactly one thing - a prior step wrote this into state - and
excludes both machine facts and a step's own subject matter.  A
verdict (a gate's exit code, a raise) and an artifact handed to a
caller or a gate are both the excluded half, so expressing them as
`produced_by` entries would extend what the field means.  An
OPTIONAL edge is neither excluded half: the producer wrote a state
key some consumer takes, so naming the producer says exactly what
the field means - which is why `requirements.OPTIONALS` derives
through the same filter with zero changed lines.  What stays
unmodelled is production nothing takes through any edge at all when
no goal names it either - and after this change there is none left
in the registry: even `ocr.extract`, with no edge and no reader,
derives its effect from the goal that names its production.

Executing
---------
`Operation.execute` gathers the step's inputs the way the RUNNER does -
`gather_step_inputs`, not a copy of it - checks the requirements, and
either runs the step's own function or REFUSES naming what is missing
and which step produces it.  The refusal is the point as much as the
success: an operation that cannot say why it will not run is a resolver,
not an executor.

Reachability
------------
    python3 -m library.tools.operations --list
    python3 -m library.tools.operations subtitles.render --project <p> --region 45.0-72.0
    python3 -m library.tools.operations subtitles.splice --project <p> \
        --region 45.0-72.0 --set stored_plan=@subtitle_plan.json
    python3 -m library.tools.operations --emit-skill

Same registry, same scope vocabulary, same refusal, whether it is called
from a shell, from Python, or by the runner.

Running one at a REGION is the captain's third entry point - "a user
asking for certain specific sections of the video to be re-edited" - and
`docs/ENTRY_POINTS_MEASURED.md` measures which operations reach it,
which do not, and what the ones that do not would need.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.tools import scope as scope_mod
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal
from library.tools.scope import PROJECT, REGION, Scope

REPO_ROOT = Path(__file__).resolve().parents[2]
STEPS_ROOT = REPO_ROOT / "library" / "steps"


class OperationError(RenRefusal):
    """The registry cannot serve this request."""


class UnknownOperation(OperationError):
    """No operation by that name."""


class ScopeNotSupported(OperationError):
    """This operation does not run at that scope, and says which it does."""


COMPLETED = "completed"
REFUSED = "refused"
STATUSES = (COMPLETED, REFUSED)

POST_BRIDGE = "post_bridge.py"
"""The body whose input the runner MERGES - step inputs, plus the
pre-bridge's output, plus the model's answer (`run_pipeline.merge_data`).
An operation gathers only the first of those three, which is why
`Operation.missing_model_answer` exists."""

SCOPE_PARAMETER = "scope"
"""The parameter name a step body uses for THE ADDRESS IT RUNS AT.

An operation declares `scopes`, `Operation.execute` takes a `Scope`, and
until this constant existed neither reached the step: `_arguments` binds
only from the GATHERED INPUTS, and a scope is not a gathered input. So a
REGION-scoped operation ran at PROJECT scope and returned a whole-project
answer that looked like a region's.

MEASURED before it was fixed, on a three-block spine with the region
covering block 1 alone:

    subtitles.plan at PROJECT scope      -> 4 caption cards
    subtitles.plan at REGION 2.0-4.0     -> 4 caption cards
    generate_subtitles(scope=that region) -> 1 caption card

The step had honoured the region all along - `generate_subtitles` and
`render_subtitle_overlays` both take `scope` and both narrow on it - and
the registry was dropping it on the floor. That is worse than an
operation that cannot run: `subtitles.render` at a region would have
re-rendered every segment of the video, and `subtitles.plan` at a region
was a whole-plan overwrite wearing a region's clothes, which is the ONE
thing `subtitles.splice` exists to prevent.

ONE spelling, and a step that wants the address spells it this way. Not
an enumeration like `MERGED_INPUT_PARAMETERS` because there is nothing
to enumerate: `library/tools/scope.Scope` is a type this repository owns,
and a body taking it under another name is a rename to make rather than a
spelling to accept.
"""

MERGED_INPUT_PARAMETERS: tuple = ("data", "inputs", "llm_output")
"""The parameter names a step body uses for THE WHOLE INPUT DICT.

Not a guess and not a heuristic: these are the three spellings measured
in this tree, and each is a body that never destructures. `data` is the
bridge/post-bridge convention, `inputs` is
`step_0_01_validate_sfx_library.validate_sfx_library(inputs)`, and
`llm_output` is `step_3_04_select_reels.post_bridge.resolve`, whose own
`main()` calls `resolve(data, data)` - the same merged dict twice.

An ENUMERATION rather than inference, for the reason the finding gave:
binding the merged dict to a parameter that wanted something else would
produce a confidently wrong result, which is worse than a `TypeError`.
So a fourth spelling must be added here deliberately, and
`tests/test_operations_execute.py::test_no_operation_takes_a_merged_dict_
under_a_name_this_module_does_not_know` fails the moment one appears
without being."""


@dataclass(frozen=True)
class OperationResult:
    """What one operation did.  The one shape hooks and the runner read.

    `unsatisfied` holds Requirement objects from
    `library/tools/requirements.py`, never strings: a refusal has to be
    able to name the step that PRODUCES what is missing, and only the
    requirement knows that.  Each is expected to carry `.name` and
    `.produced_by`; anything else is rendered by `repr` rather than
    guessed at.

    `hollow` is the paths a hollow-output check found.  The judgement
    belongs to `run_pipeline.check_output_is_real`, which already owns it
    for steps - this field CARRIES that verdict rather than recomputing
    it, so there is one hollow-output rule in the tree and not two.
    """

    operation: str
    owning_node: str
    scope: Scope
    status: str
    payload: Any = None
    unsatisfied: tuple[Any, ...] = field(default_factory=tuple)
    hollow: tuple[str, ...] = field(default_factory=tuple)
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""

    def __post_init__(self):
        if self.status not in STATUSES:
            raise OperationError(
                f"unknown status {self.status!r}",
                f"an operation completes or refuses. Known: "
                f"{', '.join(STATUSES)}",
                "this is a caller bug, not a usage bug - fix the caller "
                "to record 'completed' or 'refused'")
        if self.status == REFUSED and not (self.unsatisfied or self.error):
            raise OperationError(
                f"{self.operation} is REFUSED but names neither an "
                f"unsatisfied requirement nor an error",
                "a refusal that does not say why is the defect this type "
                "prevents",
                "this is a caller bug, not a usage bug - fix the caller "
                "to name the unsatisfied requirement or the error")

    @property
    def completed(self) -> bool:
        return self.status == COMPLETED

    @property
    def refused(self) -> bool:
        return self.status == REFUSED

    @property
    def produced_nothing(self) -> bool:
        """Did this produce nothing usable?

        The `output_empty` hook condition. A refusal produced nothing by
        definition; a completion did if its payload is empty or the
        hollow check found something.
        """
        return self.refused or not self.payload or bool(self.hollow)

    def refusal_reason(self) -> str:
        """Why it refused, naming a producer for each missing requirement."""
        if not self.refused:
            return ""
        parts = []
        for req in self.unsatisfied:
            name = getattr(req, "name", None) or repr(req)
            producers = getattr(req, "produced_by", None)
            if producers:
                who = ", ".join(producers) if not isinstance(producers, str) \
                    else producers
                parts.append(f"{name} (produced by {who})")
            else:
                parts.append(f"{name} (nothing declares a producer)")
        # `error` carries the FULL operator-facing refusal and repeats
        # these lines, so it is used only when there is nothing else -
        # an environment refusal has no unsatisfied requirement to name.
        if not parts and self.error:
            parts.append(self.error)
        return f"{self.operation} refused: " + "; ".join(parts)

    def as_record(self) -> dict:
        """The flat form for a log line or a hook payload."""
        return {
            "operation": self.operation,
            "owning_node": self.owning_node,
            "scope": str(self.scope),
            "status": self.status,
            "produced_nothing": self.produced_nothing,
            "unsatisfied": [getattr(r, "name", repr(r))
                            for r in self.unsatisfied],
            "hollow": list(self.hollow),
            "artifacts": list(self.artifacts),
            "error": self.error,
        }


# ── Loading a step body ─────────────────────────────────────────────
#
# Eight step files import a sibling by bare name (`from
# resolve_build_timeline import ...`, `from generate_remotion_props
# import ...`), so the step's OWN directory has to be on `sys.path` for
# it to import at all, and two directories import library/tools under
# the short `tools.` spelling, which needs `library/` there too.
#
# The module is registered in `sys.modules` before it executes because
# `@dataclass` inside a body loaded by path fails without it - it looks
# its own module up by name to resolve annotations, and gets None.

_LOADED: dict[str, Any] = {}


def load_step_module(step_dir: str, filename: str = "step.py"):
    """Import one step body, once, without running the DAG."""
    key = f"{step_dir}/{filename}"
    if key in _LOADED:
        return _LOADED[key]

    path = STEPS_ROOT / step_dir / filename
    if not path.is_file():
        raise OperationError(
            f"no such step body: {path}",
            "an operation is a step's own code, and this step has no "
            "such body file",
            "this is a registry bug, not a usage bug - fix the "
            "operation's body filename in the registry")

    for entry in (str(REPO_ROOT), str(REPO_ROOT / "library"),
                  str(path.parent)):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    name = f"_op_step_{step_dir}_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _LOADED[key] = module
    return module


@dataclass(frozen=True)
class Operation:
    """One named entry point into a step's own code."""

    name: str
    summary: str
    owning_node: str            # the DAG node whose decision this is
    owning_dir: str             # its directory under library/steps/
    body: str                   # step.py | bridge.py | post_bridge.py
    attr: str                   # the function's name in that body
    scopes: tuple[str, ...] = (PROJECT,)
    caller_supplied: bool = False
    """Its arguments come from a CALLER, not from gathering.

    The runner drives a step's bridge and post-bridge by writing the
    merged input dict to stdin, so every required parameter of THOSE is
    the whole dict by construction - which is what
    `MERGED_INPUT_PARAMETERS` enumerates and what
    `test_no_operation_takes_a_merged_dict_under_a_name_this_module_does_not_know`
    checks.

    A file may also export a UNIT the runner never drives:
    `motion_graphics.render_segment` renders ONE overlay segment and its
    arguments are the segment and a directory, handed to it by
    `reel_build`.  Declaring that is the honest way to say so - the
    alternative is inferring it from how many required parameters the
    function has, which would silently stop checking the two-argument
    `resolve(llm_output, data)` shape every real post-bridge uses.
    """

    @property
    def is_prompt(self) -> bool:
        """Whether this capability IS a prompt rather than a function.

        The captain's ruling, 2026-09-23: a capability may be a prompt.
        A node whose entire implementation is a prompt (`runtime: llm`
        with `entry_point: handoff.md` in its manifest - the declaration
        `library/processes/edit_video/run_pipeline.py` already
        dispatches on) has no function to name, so the registry names
        the prompt file as its `body` and leaves `attr` empty.

        Syntactic here (`body` names a `.md` file), truthful by gate:
        `tests/test_operations_add_no_second_implementation.py`
        refuses a prompt entry unless the owning step's own manifest
        declares exactly that runtime and entry point - so the registry
        cannot relabel a Python step as a prompt to dodge `run`.
        """
        return self.body.endswith(".md")

    @property
    def prompt_path(self) -> Path:
        """The prompt file this capability is, under its owning step."""
        return STEPS_ROOT / self.owning_dir / self.body

    @property
    def requires(self) -> tuple:
        """Every requirement this operation's OWNING NODE has.

        Derived from `library/tools/requirements.py`, never listed here.
        Increment 3 already builds these from `inputs[].required` and the
        DAG's `data_mapping`, so an operation asks exactly what its step
        asks - and a manifest change reaches the operation with nothing
        to update.

        A requirement is keyed by its `consumers`, which are DAG node
        ids; `owning_node` is what makes an operation addressable in that
        vocabulary, which is the second reason it is not decoration.
        """
        from library.tools import requirements
        return tuple(r for r in requirements.all_requirements()
                     if self.owning_node in r.consumers)

    @property
    def effect(self) -> tuple:
        """Every requirement this operation's OWNING NODE satisfies.

        The exact mirror of `requires`, in the SAME vocabulary: where
        `requires` filters `requirements.all_requirements()` on
        `owning_node in r.consumers`, this filters on `owning_node in
        r.produced_by`.  Derived, never hand-written, for the same
        reason - an effect in a second language cannot be matched
        against a precondition, so nothing composes.

        Node-granular, like `requires`: sibling operations owned by one
        node share one effect.  A bridge half therefore carries its
        node's whole production (`duration_zone.build` reads as
        producing the spine its step meshes) - the vocabulary is keyed
        by node id, so anything finer would be a second vocabulary.

        Empty for the two operations named in `EMPTY_EFFECT_REASONS`,
        and the emptiness is the truth: no requirement in the registry
        names those nodes as a producer, so running one satisfies no
        precondition.  See the module docstring for the kind and
        the composition consequence.
        """
        from library.tools import requirements
        return tuple(r for r in requirements.all_requirements()
                     if self.owning_node in r.produced_by)

    @property
    def run(self) -> Callable:
        """The step's own function.  Resolved on demand, never wrapped.

        `run` is DERIVED from `owning_dir`, not stored beside it, and
        that is the load-bearing choice in this dataclass: it makes the
        worst Ruling 1 violation - an operation owned by one step whose
        code lives in another - unrepresentable rather than merely
        tested for.  Measured while writing the gate: planting that
        violation in the registry was impossible without also changing
        this property.

        Do NOT turn `run` back into a plain field holding a callable.
        The moment it is one, a wrapper defined in this module type-checks
        and the registry starts owning logic.
        `tests/test_operations_add_no_second_implementation.py` still
        plants all three shapes, because a future edit here would make
        them reachable again.

        A prompt capability (`is_prompt`) has no `run` at all and raises
        here by design: there is no function to point at, and returning
        any fallback callable would be the registry owning logic through
        the hole the paragraph above closes. The refusal names the
        prompt the runner asks a model for instead.
        """
        if self.is_prompt:
            raise OperationError(
                f"{self.name} is a PROMPT capability, not a function",
                f"its implementation is {self.owning_dir}/{self.body}, "
                f"which the runner asks a model to answer. There is no "
                f"attribute to resolve because there is no body to point "
                f"at - and a fallback that returned one would be this "
                f"module owning logic, which this property exists to stop",
                "ask the host model to answer this operation's prompt "
                "instead of resolving it as code")
        return getattr(load_step_module(self.owning_dir, self.body),
                       self.attr)

    # ── Executing ───────────────────────────────────────────────────

    def context(self, project_folder: str):
        """What the requirement checks are allowed to read, for this project.

        Built from the project's own state, the same three routes
        `gather_step_inputs` uses: produced in this run, recorded by a
        previous one, or supplied from outside the pipeline.
        """
        from library.tools import requirements
        from library.tools.project_layout import ProjectLayout

        state = {}
        path = ProjectLayout(project_folder).pipeline_data_path
        if Path(path).is_file():
            state = json.loads(Path(path).read_text(encoding="utf-8"))
        recorded = state.get("step_outputs") or {}
        return requirements.Context(
            project_folder=project_folder, state=state,
            # `run_set` here is NOT what decides deferral - measured, not
            # assumed: `requirements.check` REBUILDS the context with its
            # own first argument whenever the two disagree, so whatever is
            # set here is discarded.  The lever is `unmet` below, which
            # passes ONLY this operation's node.  Left empty because that
            # is the truthful value for a context with no run behind it.
            run_set=frozenset(), recorded=recorded, external={})

    def unmet(self, project_folder: str) -> list:
        """Which of this operation's requirements are not satisfied.

        THE RUN SET IS THIS NODE ALONE, and that single argument is what
        makes an operation strict.  `requirements._producer_will_make_it`
        defers any requirement whose producer is in the run set - correct
        inside a DAG, where the producer really is scheduled ahead.  An
        operation runs ALONE: there is no ahead.  Passing the producers
        here would defer on a promise nobody made, which looks like
        tolerance and behaves like blindness.

        `tests/test_operations_execute.py` pins the distinction with one
        requirement and one state checked under both run sets, and that
        test fails if this argument grows.
        """
        from library.tools import requirements
        return requirements.check([self.owning_node],
                                  self.context(project_folder),
                                  self.requires)

    def _dag(self) -> dict:
        """The graph that DECLARES this operation's owning node.

        The one lookup a second process needed.  Before
        `library/processes/reels/` there was one graph, so this read
        `edit_video/dag.json` and was right by accident; a node of the
        reel process gathered against edit_video's edges would find none
        of its own and silently hand the step an empty dict.

        `library/tools/processes.py` owns the mapping, so a third process
        needs no change here - and node ids are globally unique, which is
        what makes "which graph declares this node" a question with one
        answer.
        """
        from library.tools import processes
        return processes.dag_declaring(self.owning_node)

    def gather(self, project_folder: str) -> dict:
        """The step's inputs, assembled the way the RUNNER assembles them.

        `gather_step_inputs` is called, never copied: it owns the
        `data_mapping` edges, the process-level whitelist, the external
        inputs and the raise on a missing required key.  A second
        implementation here would drift from the runner within a month,
        and the drift would be invisible because both would "work".

        It is process-AGNOSTIC - it takes a dag, a state and a manifest -
        so the reel process reuses the runner's assembler rather than
        growing one, which is the same reuse the caption path already
        makes of steps 4.01 and 4.05.
        """
        import sys as _sys

        from library.tools import run_scope
        from library.tools.project_layout import ProjectLayout

        process_dir = REPO_ROOT / "library" / "processes" / "edit_video"
        if str(process_dir) not in _sys.path:
            _sys.path.insert(0, str(process_dir))
        import run_pipeline

        dag = self._dag()
        manifests = run_scope.load_manifests(dag)
        state = {}
        path = ProjectLayout(project_folder).pipeline_data_path
        if Path(path).is_file():
            state = json.loads(Path(path).read_text(encoding="utf-8"))
        state.setdefault("project_folder", project_folder)
        return run_pipeline.gather_step_inputs(
            self.owning_node, dag, state,
            manifests.get(self.owning_node), step_type="operation")

    def execute(self, project_folder: str, scope: Scope = None,
                **overrides) -> OperationResult:
        """Run this operation, or REFUSE naming what is missing.

        The refusal matters as much as the success. An operation whose
        prerequisites are absent says which input is missing and which
        step produces it - the shape the runner already refuses in, and
        the reason `unsatisfied` carries Requirement objects rather than
        strings.
        """
        where = scope or scope_mod.project()
        self.check_scope(where)

        missing = self.unmet(project_folder)
        if missing:
            return OperationResult(
                operation=self.name, owning_node=self.owning_node,
                scope=where, status=REFUSED,
                unsatisfied=tuple(entry.requirement for entry in missing),
                error=self._teach(missing))

        inputs = self.gather(project_folder)
        inputs.update(overrides)

        # A PROMPT capability has no function to call: the runner asks a
        # model to answer the prompt, and an operation runs ALONE with no
        # model. Refused naming the prompt and the way out, placed here -
        # after the contract check, before the splat - because `run`
        # raises by design rather than returning something to bind.
        if self.is_prompt:
            return OperationResult(
                operation=self.name, owning_node=self.owning_node,
                scope=where, status=REFUSED,
                error=self._teach_prompt())

        # The step's own function, its own signature. Bound here so the
        # unbindable case below is asked of the SAME dict that would have
        # been splatted into the call.
        arguments = self._arguments(inputs, where)

        # A post-bridge resolves the MODEL's answer against the step's
        # own measurements. Run as an operation it is handed the
        # measurements alone, so it would resolve a plan nobody wrote and
        # return it looking like a result. Refused by name instead, and
        # the way out is stated: pass the model's keys as overrides, or
        # run the step so the runner asks the model for them.
        owed = self.missing_model_answer(inputs)
        if owed:
            return OperationResult(
                operation=self.name, owning_node=self.owning_node,
                scope=where, status=REFUSED,
                error=(
                    f"{self.name} is the POST-BRIDGE of {self.owning_node}: "
                    f"the dict it takes is the step's inputs PLUS its "
                    f"pre-bridge's output PLUS the model's answer, and an "
                    f"operation gathers only the first of those three.\n"
                    f"\nMissing what the model owes this step: "
                    f"{', '.join(owed)}.\n"
                    f"\nEither supply them - "
                    f"`operations.get({self.name!r}).execute(project, "
                    f"{owed[0]}=...)` - or run {self.owning_node} so the "
                    f"runner asks the model for them. Resolving without "
                    f"them would produce a confident answer to a question "
                    f"nobody was asked."))

        unbound = self.unbound_parameters(arguments)
        if unbound:
            return OperationResult(
                operation=self.name, owning_node=self.owning_node,
                scope=where, status=REFUSED,
                error=self._teach_unbound(unbound, where))

        payload = self.run(**arguments)
        return OperationResult(
            operation=self.name, owning_node=self.owning_node,
            scope=where, status=COMPLETED, payload=payload)

    def _teach_prompt(self) -> str:
        """The refusal for a capability that IS a prompt.

        Names the prompt file, says who answers it (the runner asking
        a model, never an operation running alone), and gives the way
        out. Composing still works: the contract this refuses under is
        derived from the owning node like every other operation, so a
        plan may name this capability even though executing it alone
        cannot.
        """
        return (
            f"{self.name} is the PROMPT of {self.owning_node}: its "
            f"implementation is {self.owning_dir}/{self.body}, which the "
            f"runner asks a model to answer - and an operation runs "
            f"ALONE, with no model to ask.\n"
            f"\nRun {self.owning_node} so the runner asks the model for "
            f"it, or run the DAG.")

    def _teach(self, missing: list) -> str:
        """The refusal, written so the reader can work out their next move.

        An operation runs ALONE, so a requirement whose producer would
        have run first inside a DAG is refused here rather than deferred
        (see `context`).  That is correct - deferring on a promise nobody
        made is a check that cannot fail - but it makes the refusal
        SURPRISING, because the same state runs fine as part of a run.

        So the message says which producer, and says that running it
        first or running the DAG is what satisfies this.  "REFUSED:
        missing audio_spine" is a wall; naming the producer and the two
        ways out is a control surface, which is the point of granular
        operations at all.
        """
        from library.tools import requirements

        lines = requirements.describe_refusal(missing)

        # Grouped by requirement, because one requirement with three
        # possible producers is one thing to fix, not three.
        deferrable = []
        for entry in missing:
            producers = tuple(entry.requirement.produced_by or ())
            if producers:
                deferrable.append((entry.requirement.name, producers))

        if deferrable:
            lines += ["",
                      ("This operation runs ALONE, so nothing will produce "
                       "these while it waits - inside a DAG run they would "
                       "be satisfied by a producer scheduled ahead of it."),
                      "To satisfy them:"]
            for name, producers in deferrable:
                # The STEP is named, never an operation.  An operation
                # owned by the producing node is not necessarily the
                # operation that produces this key - `duration_zone.build`
                # is owned by `mesh_spine` but is its BRIDGE half and
                # emits no spine.  Suggesting it would be confidently
                # wrong, which is worse than suggesting nothing, and
                # guessing the mapping is the defect this whole refactor
                # is about.
                who = " or ".join(f"`{p}`" for p in producers)
                lines.append(f"  - {name}: produced by {who} - run "
                             f"{'that step' if len(producers) == 1 else 'one of those steps'} "
                             f"first, or run the DAG.")
        return "\n".join(lines)

    def unbound_parameters(self, arguments: dict) -> tuple:
        """The step's own required parameters nothing could fill.

        Called before the splat, so the failure is a REFUSAL naming the
        arguments rather than a `TypeError` naming the first one Python
        happened to notice.

        MEASURED before this existed - four of the six operations that
        declare REGION scope could not be called at all through the
        registry, and each died the same way:

            TypeError: splice_region_plan() missing 2 required
            positional arguments: 'stored_plan' and 'scope'

        `subtitles.splice`, `subtitles.render_segment`,
        `transcript.reindex` and `transcript.splice` were catalogue
        entries: registered, listed, `--emit-skill`-ed, addressable as
        `subtitles.splice@45.0-72.0`, and unable to run. An entry point
        that cannot refuse when its inputs are absent is not an entry
        point, and one that raises `TypeError` instead has not refused -
        it has crashed.

        `*args`/`**kwargs` are not required parameters and are not
        reported; a body with `**kwargs` takes the whole dict anyway
        (`_arguments`).
        """
        import inspect
        return tuple(
            name for name, parameter in
            inspect.signature(self.run).parameters.items()
            if parameter.default is inspect.Parameter.empty
            and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                   inspect.Parameter.KEYWORD_ONLY)
            and name not in arguments)

    def _teach_unbound(self, unbound: tuple, where: Scope = None) -> str:
        """The refusal, written so the reader can supply what is missing.

        Names each argument, says the gathered inputs do not carry it,
        and gives the two ways to hand it over. It does NOT guess what
        the value should be: `stored_plan` is the step's own previous
        answer and `props` is one segment of it, and a registry that
        filled either from a plausible-looking place would produce the
        confidently-wrong result this whole layer exists to stop.
        """
        named = ", ".join(unbound)
        first = unbound[0]
        # The address goes back into the suggested command. A copyable
        # line that then refuses because it lost the region is a worse
        # control surface than no line at all, and `subtitles.splice`
        # runs at REGION scope ONLY.
        at = (f" --region {where.region_span.as_address()}"
              if where is not None and where.kind == REGION else "")
        return (
            f"{self.name} runs {self.owning_dir}/{self.body}:{self.attr}, "
            f"whose signature requires {named} - and nothing the DAG "
            f"routes to {self.owning_node} carries "
            f"{'them' if len(unbound) > 1 else 'it'}.\n"
            f"\nThese are arguments a CALLER decides, not state a "
            f"previous step recorded, so there is nothing to run first: "
            f"supply them.\n"
            f"\n  python3 -m library.tools.operations {self.name} "
            f"--project <p>{at} --set {first}=@{first}.json\n"
            f"  operations.get({self.name!r}).execute(project, "
            f"{first}=...)\n"
            f"\nRefused rather than called, because a step handed a "
            f"guessed argument answers a question nobody asked.")

    def _arguments(self, inputs: dict, scope: Scope = None) -> dict:
        """Only the arguments the step's own function actually names.

        The gathered dict is the STEP's whole input; a function that takes
        four of those keys must not be handed forty. Reading the real
        signature rather than a declaration keeps this true when the
        function changes.

        THE MERGED-DICT CASE, which used to make an operation
        unexecutable. Seven of the step bodies in this tree do not
        destructure their input at all - they take the WHOLE dict under
        one parameter, because that is what the runner writes to their
        stdin. Binding by name gave those `{}` and the call raised

            TypeError: build_duration_zone() missing 1 required
            positional argument: 'data'

        so `sfx_library.validate`, `duration_zone.build`,
        `motion_graphics.render`, `color_grade.resolve`,
        `validation.resolve`, `reel.candidates` and `reel.select` were
        catalogue entries: registered, listed, `--emit-skill`-ed, and
        unable to run. An operation that cannot execute is exactly what
        this refactor set out to stop being.
        `tests/test_operations_execute.py` now EXECUTES one of them.

        THE SCOPE, which used to reach no step at all. `scope` is not a
        gathered key - it is the address the operation was called at - so
        binding by name alone left every REGION-scoped operation running
        at PROJECT scope. See `SCOPE_PARAMETER` for the measurement.
        """
        import inspect
        parameters = inspect.signature(self.run).parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in parameters.values()):
            return dict(inputs)
        bound = {name: inputs[name] for name in parameters if name in inputs}
        for name in parameters:
            # A REAL gathered key of that name always wins - the loop
            # above ran first - so this only ever fills a parameter
            # nothing else could satisfy.
            if name in MERGED_INPUT_PARAMETERS and name not in bound:
                bound[name] = dict(inputs)
        # THE ADDRESS. Bound last and only when the step asks for it, so
        # a step with no `scope` parameter is unaffected and a gathered
        # key called `scope` still wins. PROJECT scope is passed too:
        # `scope_mod.project()` is what the step's own default means, and
        # passing it is what makes the region case the same code path
        # rather than a special one.
        if SCOPE_PARAMETER in parameters and SCOPE_PARAMETER not in bound:
            bound[SCOPE_PARAMETER] = scope or scope_mod.project()
        return bound

    def missing_model_answer(self, merged: dict) -> tuple:
        """Which keys the MODEL owes this body that the merged dict lacks.

        Empty for everything that is not a post-bridge, and that is the
        whole distinction the finding
        (`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md`) said had to be made
        before `data` could be bound at all:

            The runner builds a post-bridge's `data` as *step inputs +
            pre-bridge output + the model's answer* (`merge_data`);
            `Operation.gather` returns the step inputs alone. Binding
            `data=gathered` would hand a post-bridge a dict missing the
            model's answer and let it produce a confidently wrong result
            instead of raising. A `TypeError` is honest; a silently
            incomplete `data` is not.

        Both halves of that are true, and the resolution is neither
        binding blindly nor raising `TypeError`: it is a REFUSAL that
        names the missing keys and the way to supply them. A pre-bridge
        is unaffected - its `data` IS the step's inputs, so the gathered
        dict is complete and the call is correct.

        The declarations come from the runner's own
        `llm_output_declarations`, never from a second reading here, so
        this and the schema the model is really asked for cannot
        disagree.
        """
        if self.body != POST_BRIDGE:
            return ()
        import sys as _sys

        from library.tools import run_scope

        process_dir = REPO_ROOT / "library" / "processes" / "edit_video"
        if str(process_dir) not in _sys.path:
            _sys.path.insert(0, str(process_dir))
        import run_pipeline

        manifest = run_scope.load_manifests(
            self._dag()).get(self.owning_node) or {}
        declared = run_pipeline.llm_output_declarations(manifest,
                                                        set(merged))
        return tuple(o.get("name") for o in declared
                     if o.get("name") and o.get("name") not in merged)

    def supports(self, scope: Scope) -> bool:
        return scope.kind in self.scopes

    def call_identity(self) -> dict:
        """The TWO names every call site must pass: operation and step.

        Not style.  Increment 2's provenance refuses an operation that
        arrives without an owning node -

            ProvenanceError: operation subtitles.plan was given no owning
            step ... the step ledgers, the run status and the run-level
            collectors are all keyed by node id

        - because sixteen of the runner's eighteen per-step services are
        node-keyed.  An operation that passed only its own name would run
        and be recorded by nothing.  Both names, always, and the refusal
        is loud rather than silent if one is missed.
        """
        return {"operation_id": self.name, "step_id": self.owning_node}

    def check_scope(self, scope: Scope) -> None:
        if not self.supports(scope):
            raise ScopeNotSupported(
                f"{self.name} does not run at {scope.kind} scope",
                f"it runs at: {', '.join(self.scopes)}",
                f"run it at {', '.join(self.scopes)} scope instead")


# ── Operations whose derived effect is empty ────────────────────────
#
# Two operations satisfy no requirement in the registry, each for a
# reason that was verified against the tree rather than assumed.  The
# mapping is operation name to the reason its effect is empty anyway -
# the justification `tests/test_operations_declare_effect.py` demands
# before it accepts a new entry here.
#
# A new operation whose owning node produces no requirement FAILS that
# test until its author classifies it below.  Do NOT invent a
# requirement name to fill the field instead: that is a second
# vocabulary, and it would make the field look complete while teaching
# the composer a goal nothing refuses on.  If a future operation
# genuinely cannot be expressed here, that means the requirement layer
# is not the right home for effects - say so and revisit the design
# rather than hand-writing the entry.
#
# The mapping held NINE entries until the captain's rulings 2026-09-23.
# The three VERDICT rows (`sfx_library.validate`, `reel.verify`,
# `validation.resolve`) are GONE because those nodes now produce
# something: each is the `produced_by` of a `requirements.VERDICTS`
# entry, so their effects derive non-empty.  The three ANALYSIS rows
# (`prosody.analyse`, `color_grade.resolve`, `ocr.extract`) are GONE
# for the same reason in the other direction: each is the
# `produced_by` of a `requirements.OPTIONALS` entry, so their effects
# derive non-empty too.  The old rows are not reproduced here - their
# evidence (the exit code, the raise sites, the code-route readers on
# the verdict side; the optional edges and the absent one on the
# analysis side) is what motivated the two kinds, and
# `requirements.VERDICTS` and `requirements.OPTIONALS` carry the
# per-entry evidence now.
#
# `reel.gate_stills` left with the verdicts without being one: it is
# owned by `verify_reels`, so it shares that node's verdict effect by
# the node granularity `Operation.effect` declares.  It keeps no row
# because its effect is no longer empty - and no plan may name it for
# that effect, which the route selector (`composer._select_verify_reels`)
# and `tests/test_composer.py` pin.  Only ARTIFACT rows remain.

EMPTY_EFFECT_REASONS: dict[str, str] = {
    # ARTIFACT - the product lands on disk for a caller, a gate or
    # placement rather than travelling a DAG edge.
    "motion_graphics.render":
        "ARTIFACT. The post-bridge renders the overlay artefacts to "
        "disk "
        "(`library/steps/step_4_06_render_motion_graphics/post_bridge.py:"
        "render_motion_graphics`); the overlay record it returns reaches "
        "`compile_manifest` only through inputs that manifest declares "
        "OPTIONAL, which `run_scope.prerequisites` deliberately "
        "excludes - so no requirement names `render_motion_graphics` "
        "as a producer.",
    "motion_graphics.render_segment":
        "ARTIFACT. Caller-supplied (`caller_supplied=True`): the unit "
        "`reel_build` drives, `render_one_segment(planned, out_dir)`, "
        "rendering ONE segment onto no master spine. No edge carries "
        "it, and the owning node `render_motion_graphics` produces no "
        "requirement.",
}


# ── The registry ────────────────────────────────────────────────────
#
# Every entry points at a public function this repository already has -
# or, since the captain's 2026-09-23 ruling, at the prompt file that IS
# a step's whole implementation (`body="handoff.md"`, `attr=""`).
# Adding an operation means naming work that exists; if the name you
# want has no function behind it, split the body first - that is what
# increment 4a did for ten of them.

_REGISTRY: tuple[Operation, ...] = (
    Operation(
        name="sfx_library.validate",
        summary="Check the SFX library can actually serve a run",
        owning_node="validate_sfx_library",
        owning_dir="step_0_01_validate_sfx_library", body="step.py",
        attr="validate_sfx_library",
    ),
    Operation(
        name="footage.scan",
        summary="Scan the project folder for raw video files",
        owning_node="scan",
        owning_dir="step_1_01_scan_project", body="step.py",
        attr="scan_project_folder",
    ),
    Operation(
        name="footage.catalog",
        summary="Extract per-file metadata into the ordered clip catalog",
        owning_node="catalog",
        owning_dir="step_1_02_catalog_footage", body="step.py",
        attr="catalog_footage",
    ),
    Operation(
        name="semantics.analyse",
        summary="Run the v3 vision pass over clips without a profile",
        owning_node="semantic_analysis",
        owning_dir="step_1_03_semantic_analysis", body="step.py",
        attr="analyse_semantics",
    ),
    Operation(
        name="prosody.analyse",
        summary="Measure pitch, pace, voice quality and intensity per clip",
        owning_node="prosody_analysis",
        owning_dir="step_1_05_prosody_analysis", body="step.py",
        attr="analyse_prosody",
    ),
    Operation(
        name="ocr.extract",
        summary="Extract on-screen text from the footage",
        owning_node="ocr_extraction",
        owning_dir="step_1_07_ocr_extraction", body="step.py",
        attr="extract_ocr",
    ),
    Operation(
        name="creative.direct",
        summary="Decide the video's creative direction from the preflight reads",
        owning_node="creative_direction",
        owning_dir="step_2_01_creative_direction", body="handoff.md",
        attr="",
        # A PROMPT capability, per the captain's 2026-09-23 ruling that a
        # capability may be a prompt: this node is `runtime: llm` with
        # `entry_point: handoff.md` and no Python body to name, so `run`
        # raises and `execute` refuses naming the runner - while
        # `requires`/`effect` derive from `creative_direction` like every
        # other operation (eleven state keys fall out, no vocabulary
        # work). The Ruling 1 gate checks the manifest agrees.
    ),
    Operation(
        name="speech.enrich",
        summary="Enrich the model's speech sequence with word timings from the temporal index",
        owning_node="speech_sequence",
        owning_dir="step_2_02_speech_sequence", body="post_bridge.py",
        attr="enrich_speech_sequence",
        # The deterministic half of a hybrid step: the passage SELECTION
        # stays the model's answer (supplied as overrides, or run
        # `speech_sequence` so the runner asks the model for it) and this
        # resolves each passage to real source timings by text alignment,
        # replacing the LLM's hint outright. PROJECT only, and the
        # default: the alignment searches the whole clip by design, so a
        # region address would promise a scope the function does not keep.
    ),
    Operation(
        name="music.resolve",
        summary="Resolve the model's music choice against the measured candidates",
        owning_node="music_selection",
        owning_dir="step_2_04_music_selection", body="post_bridge.py",
        attr="resolve_selection",
        # The deterministic half of a hybrid step: the track CHOICE stays
        # the model's answer (supplied as overrides, or run
        # `music_selection` so the runner asks the model for it) and this
        # validates it against the measured candidates and re-measures
        # the file on disk. PROJECT only, and the default: the choice is
        # one per project, so a region address would promise a scope the
        # function does not keep.
    ),
    Operation(
        name="duration_zone.build",
        summary="Resolve the project's target duration into the band the model is shown",
        owning_node="mesh_spine",
        owning_dir="step_2_05_mesh_spine", body="bridge.py",
        attr="build_duration_zone",
    ),
    Operation(
        name="aroll.assign",
        summary="Map speech blocks and hook to their A-roll source files",
        owning_node="assign_aroll",
        owning_dir="step_3_01_assign_aroll", body="step.py",
        attr="assign_a_roll",
    ),
    Operation(
        name="broll.resolve",
        summary="Resolve B-roll selections to placed cutaways with source ranges",
        owning_node="select_broll",
        owning_dir="step_3_02_select_broll", body="post_bridge.py",
        attr="resolve_broll",
        # The deterministic half of a hybrid step: the cutaway SELECTION
        # stays the model's answer (supplied as overrides, or run
        # `select_broll` so the runner asks the model for it) and this
        # resolves each choice to a placed source range. PROJECT only,
        # and the default: placement is over the whole spine, so a region
        # address would promise a scope the function does not keep.
    ),
    # ── Reels ────────────────────────────────────────────────────────
    #
    # PROPOSING a reel is `select_reels`' own decision, and both halves
    # of that step are here.  BUILDING and VERIFYING one belong to the
    # OTHER PROCESS - `library/processes/reels` - and that is the whole
    # of what changed since docs/REEL_BUILD_HAS_NO_OWNING_NODE.md.
    #
    # That finding checked three homes for the build inside edit_video
    # and refused all three: `render`, whose one derived requirement is
    # an `assembly_manifest` the build never reads, so it would refuse
    # for a reason that is not true AND pass on a project with no
    # approved reel in it; `select_reels`, which cannot require the
    # captain's approval of its own output; and a new node in a graph no
    # run schedules.  Its conclusion was that the honest structure is a
    # SECOND PROCESS, and the captain authorised one.
    #
    # So `build_reels` and `verify_reels` are real nodes of a real graph
    # with real edges, and their contracts are DERIVED exactly like every
    # other: `timeline_transcript.on_file` comes off their manifests,
    # `state.verify_reels.reel_build` comes off the edge between them.
    # What no derivation could see - a plan the captain ruled on, a
    # project.yaml naming a master timeline - is hand written in
    # `requirements.EXTERNAL_STATE` and NAMED by both manifests.
    Operation(
        name="reel.candidates",
        summary="Measure every contiguous exchange in the cut, ranked and filtered by nothing",
        owning_node="select_reels",
        owning_dir="step_3_04_select_reels", body="bridge.py",
        attr="build_context",
    ),
    Operation(
        name="reel.select",
        summary="Check the model's chosen moments against the cut and publish them PROPOSED",
        owning_node="select_reels",
        owning_dir="step_3_04_select_reels", body="post_bridge.py",
        attr="resolve",
    ),
    Operation(
        name="reel.build",
        summary="Cut every APPROVED moment onto its own Resolve timeline, bad takes removed",
        owning_node="build_reels",
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="build_reels",
    ),
    Operation(
        name="reel.touchup",
        summary="Change one built reel's own timeline in place, instead of rebuilding it",
        owning_node="build_reels",
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="touch_reel",
        # Its arguments are caller-decided - the structured change:
        # which reel, which item, what changes - handed in by the
        # fix that computed it, the way `touch-reel` takes `--edits`.
        # The runner never drives it. See
        # `Operation.caller_supplied`.
        caller_supplied=True,
        # PROJECT only. The change addresses items by (row, position)
        # on one reel's timeline, so a region address would promise a
        # scope the touchup does not keep.
        scopes=(PROJECT,),
    ),
    Operation(
        name="reel.entry_motion",
        summary="Animate a placed overlay element in (and out) with a Fusion fade, without rebuilding its reel",
        owning_node="build_reels",
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="animate_entry",
        # Its arguments are caller-decided - which reel, which item,
        # how many frames of entrance and exit fade - handed in by
        # the fix that computed them. The runner never drives it. See
        # `Operation.caller_supplied`.
        caller_supplied=True,
        # PROJECT only, for the same reason as `reel.touchup`: the
        # change addresses items by (row, position) on one reel's
        # timeline.
        scopes=(PROJECT,),
    ),
    Operation(
        name="reel.set_properties",
        summary="Change properties on an already-placed clip in place, without deleting and re-placing it",
        owning_node="build_reels",
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="set_clip_properties",
        # Its arguments are caller-decided - which reel, which item,
        # which properties - handed in by the fix that computed them.
        # The runner never drives it. See
        # `Operation.caller_supplied`.
        caller_supplied=True,
        # PROJECT only, for the same reason as `reel.touchup`.
        scopes=(PROJECT,),
    ),
    Operation(
        name="reel.ask",
        summary="Write every APPROVED reel's three visual asks without building anything",
        owning_node="build_reels",
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="ask_reels",
    ),
    Operation(
        name="reel.verify",
        summary="Grade the built reel timelines against the plan they were built from",
        owning_node="verify_reels",
        owning_dir="step_7_02_verify_reels", body="step.py",
        attr="verify_reels",
    ),
    Operation(
        name="reel.gate_stills",
        summary="Grab gate stills at named reel-relative frames off one built reel timeline",
        owning_node="verify_reels",
        owning_dir="step_7_02_verify_reels", body="step.py",
        attr="grab_gate_stills",
        # Its arguments are caller-decided - a reel label, the timeline's
        # exact name and the reel-relative frames the gate wants to see -
        # handed in by the gate that asks the visual question. The runner
        # never drives it. See `Operation.caller_supplied`.
        caller_supplied=True,
        # PROJECT only. The grab switches timelines and moves the
        # playhead, so a region address would promise a scope the grab
        # does not keep.
        scopes=(PROJECT,),
    ),
    Operation(
        name="music.analyse",
        summary="Analyse the selected track for beat grid, BPM, key and structure",
        owning_node="music_analysis",
        owning_dir="step_2_06_music_analysis", body="step.py",
        attr="analyse_music",
    ),
    Operation(
        name="subtitles.plan",
        summary="Generate subtitle entries from the spine's own word timestamps",
        owning_node="plan_subtitles",
        owning_dir="step_4_01_plan_subtitles", body="step.py",
        attr="generate_subtitles",
        # REGION is offered as of increment 5.  It was withheld while
        # 4.01 numbered its cards from a run-global counter, because a
        # region-scoped plan renumbered every card after the region -
        # measured on project 001, changing one block moved 13 ids in
        # blocks that had not changed.  Block-local ids (increment 1)
        # removed that, and the same measurement now moves 0.
        #
        # A region-scoped plan is a PARTIAL plan for
        # `subtitle_splice.splice_plan`, never something to write over a
        # whole one: 4.01 plans each block from its own bounds and its
        # own words, so a block plans identically whether its neighbours
        # are present or not.
        scopes=(PROJECT, REGION),
    ),
    Operation(
        name="subtitles.splice",
        summary="Put a region's re-planned captions back into the stored plan",
        owning_node="plan_subtitles",
        owning_dir="step_4_01_plan_subtitles", body="step.py",
        attr="splice_region_plan",
        # REGION only.  A splice with no region is a whole-plan
        # overwrite, which is what `subtitles.plan` at PROJECT scope
        # already is - naming it twice would be the second
        # implementation Ruling 1 forbids.
        scopes=(REGION,),
    ),
    Operation(
        name="rough_cut.review",
        summary="Run the mechanical duration, continuity and source checks over the rough cut",
        owning_node="review_rough_cut",
        owning_dir="step_3_03_review_rough_cut", body="step.py",
        attr="run_mechanical_checks",
    ),
    Operation(
        name="transitions.resolve",
        summary="Resolve the model's transition plan to execution specs",
        owning_node="plan_transitions",
        owning_dir="step_4_02_plan_transitions", body="post_bridge.py",
        attr="resolve_transitions",
        # The deterministic half of a hybrid step: the transition
        # SELECTION stays the model's answer (supplied as overrides, or
        # run `plan_transitions` so the runner asks the model for it)
        # and this resolves each entry against the timed spine and the
        # real beat grid. PROJECT only, and the default: the resolution
        # reads the whole spine by design, so a region address would
        # promise a scope the function does not keep.
    ),
    Operation(
        name="vfx.resolve",
        summary="Resolve the model's VFX plan to execution specs",
        owning_node="plan_vfx",
        owning_dir="step_4_03_plan_vfx", body="post_bridge.py",
        attr="resolve_vfx",
        # The deterministic half of a hybrid step: the effect SELECTION
        # stays the model's answer (supplied as overrides, or run
        # `plan_vfx` so the runner asks the model for it) and this
        # resolves each entry against the timed spine, dropping what
        # names no real block. PROJECT only, and the default: the
        # resolution reads the whole spine by design, so a region
        # address would promise a scope the function does not keep.
    ),
    Operation(
        name="sfx.resolve",
        summary="Resolve the model's SFX plan to playable placements",
        owning_node="plan_sfx",
        owning_dir="step_4_04_plan_sfx", body="post_bridge.py",
        attr="resolve_sfx",
        # The deterministic half of a hybrid step: the sound SELECTION
        # stays the model's answer (supplied as overrides, or run
        # `plan_sfx` so the runner asks the model for it) and this
        # places each entry against the timed spine, the temporal
        # index and the real downbeat grid. PROJECT only, and the
        # default: the placement reads the whole spine by design, so
        # a region address would promise a scope the function does not
        # keep.
    ),
    Operation(
        name="transcript.reindex",
        summary="Re-measure the speech in one region, back at the raw footage",
        owning_node="temporal_index",
        owning_dir="step_1_04_temporal_index", body="step.py",
        attr="reindex_region",
        # REGION only, for the same reason: re-indexing everything is
        # what the step already does at PROJECT scope.
        scopes=(REGION,),
    ),
    Operation(
        name="transcript.splice",
        summary="Put a re-measured region back into the per-clip speech index",
        owning_node="temporal_index",
        owning_dir="step_1_04_temporal_index", body="step.py",
        attr="splice_region_index",
        scopes=(REGION,),
    ),
    Operation(
        name="subtitles.render",
        summary="Render one overlay artefact per captioned spine block",
        owning_node="render_subtitles",
        owning_dir="step_4_05_render_subtitles", body="step.py",
        attr="render_subtitle_overlays",
        # REGION is offered because re-rendering is idempotent per
        # segment and carries no run-global identity: a segment's name
        # comes from its own speaker, timeline and source span
        # (subtitle_segment_id), not from a counter. Contrast
        # subtitles.plan above.
        scopes=(PROJECT, REGION),
    ),
    Operation(
        name="subtitles.render_segment",
        summary="Render ONE subtitle segment - the per-segment unit a region-scoped redo reaches",
        owning_node="render_subtitles",
        owning_dir="step_4_05_render_subtitles", body="step.py",
        attr="render_one_segment",
        # The unit increment 5's region-scoped redo reaches: one segment,
        # so REGION is the scope that means anything here, and CLIP is
        # not offered because a segment is addressed by its timeline
        # span rather than by the clip it was cut from.
        scopes=(PROJECT, REGION),
    ),
    Operation(
        name="subtitles.rerender_swap",
        summary="Re-render named caption segments and swap them onto every timeline holding the old file",
        owning_node="render_subtitles",
        owning_dir="step_4_05_render_subtitles", body="step.py",
        attr="rerender_and_swap",
        # Its arguments are caller-decided pairs of
        # {old_mov, timeline_label, props}, handed in by the fix that
        # computed the new text - the runner never drives it. See
        # `Operation.caller_supplied`.
        caller_supplied=True,
        # PROJECT only. The swap scans every timeline for the old
        # files, so a region address would promise a scope the swap
        # does not keep.
        scopes=(PROJECT,),
    ),
    Operation(
        name="motion_graphics.render",
        summary="Render the planned motion graphics, bookends and timed text",
        owning_node="render_motion_graphics",
        owning_dir="step_4_06_render_motion_graphics", body="post_bridge.py",
        attr="render_motion_graphics",
    ),
    Operation(
        name="motion_graphics.render_segment",
        summary="Render ONE motion-graphics overlay segment",
        owning_node="render_motion_graphics",
        owning_dir="step_4_06_render_motion_graphics", body="post_bridge.py",
        attr="render_one_segment",
        # Its arguments are a planned segment and a directory, handed to
        # it by `reel_build` - the runner never drives it. See
        # `Operation.caller_supplied`.
        caller_supplied=True,
        # The unit, beside the pass, for the same reason
        # `subtitles.render_segment` sits beside `subtitles.render`: the
        # REELS path needs one segment on one reel's own timebase and has
        # no master spine, bookends or timed text to render beside it.
        # REGION is the scope that means anything - a segment is
        # addressed by its timeline span - and it is idempotent per
        # segment because the name comes from the reel and the index
        # rather than from a counter.
        scopes=(PROJECT, REGION),
    ),
    Operation(
        name="color_grade.resolve",
        summary="Join the colourist's answer to the measured clips as one CDL each",
        owning_node="color_grade",
        owning_dir="step_5_01_color_grade", body="post_bridge.py",
        attr="resolve_color_grade",
    ),
    Operation(
        name="audio_mix.resolve",
        summary="Join the mix answer to the bed and speech measurements as per-window clip gain",
        owning_node="audio_mix",
        owning_dir="step_5_02_audio_mix", body="post_bridge.py",
        attr="resolve_audio_mix",
        # The stdin-driven entry point, so `data` binds the whole
        # gathered dict under the known spelling - the
        # motion_graphics.render shape, not the speech.enrich
        # inner-unit one. The manifest declares `llm_outputs: []`,
        # so `missing_model_answer` never refuses: the decided-value
        # ladder records an undecided run as fallback or undetermined
        # rather than raising. Bridge outputs are not gathered, so an
        # operation run resolves over the step inputs alone.
    ),
    # compile_manifest belongs here in pipeline order and is NOT
    # registered: step_5_04_compile_manifest/step.py takes `out_dir`,
    # a path main() derives from project_folder - neither a
    # merged-dict spelling nor a declared input. See the PR.
    Operation(
        name="render.build",
        summary="Build the final timeline in DaVinci Resolve and export the finished video",
        owning_node="render",
        owning_dir="step_6_01_render", body="step.py",
        attr="run",
        # The plain step.py case: `run(inputs)` takes the whole
        # gathered dict under the known `inputs` spelling, the
        # footage.scan shape. Needs Resolve at call time.
    ),
    Operation(
        name="validation.resolve",
        summary="Combine the deterministic checks and the model's reading into one verdict",
        owning_node="validate",
        owning_dir="step_6_02_validate_output", body="post_bridge.py",
        attr="resolve_validation",
    ),
)


def all() -> tuple[Operation, ...]:
    """Every registered operation, in registry order."""
    return _REGISTRY


def names() -> tuple[str, ...]:
    return tuple(op.name for op in _REGISTRY)


def get(name: str) -> Operation:
    for op in _REGISTRY:
        if op.name == name:
            return op
    raise UnknownOperation(
        f"unknown operation {name!r}",
        f"the registry holds: {', '.join(names())}",
        f"run one of {', '.join(names())} - see --list for what each does")


# ── An operation, addressed at a region ──────────────────────────────

ADDRESS_SEPARATOR = "@"
"""What separates an operation from the region it runs at:
``subtitles.render@45.0-72.0``.

ONE spelling, parsed in ONE place. The design named three consumers -
`--break`, `--rerun`'s fourth form and this module's own CLI - and three
parsers for one address is three chances to disagree about what the
captain typed."""


@dataclass(frozen=True)
class Address:
    """An operation, and the region it is addressed at.

    `region` is None for the whole project, which is what a bare
    operation name means.
    """

    operation: str
    region: object = None

    def __str__(self) -> str:
        if self.region is None:
            return self.operation
        return f"{self.operation}{ADDRESS_SEPARATOR}{self.region}"

    @property
    def is_region_scoped(self) -> bool:
        return self.region is not None


def parse_address(text: str, known: Iterable[str] | None = None) -> Address:
    """`subtitles.render@45.0-72.0`, or a bare `subtitles.render`.

    The SPAN half is delegated to `region.parse` rather than re-parsed
    here: that module owns what an interval is and, once a region carries
    its own timeline, an address parsed here picks that up without this
    function learning about timelines at all.

    An unknown operation is refused BY NAME with the known set listed -
    the shape `run_scope._reject_unknown` gives an unknown step - because
    a breakpoint armed at an operation that does not exist is a pause the
    captain asked for and will never get.
    """
    raw = (text or "").strip()
    if not raw:
        raise UnknownOperation(
            "an operation address needs a name",
            "a blank address names nothing to run",
            "pass an operation name, optionally "
            f"{ADDRESS_SEPARATOR}<start>-<end>, e.g. "
            f"subtitles.render{ADDRESS_SEPARATOR}45.0-72.0")

    name, sep, span = raw.partition(ADDRESS_SEPARATOR)
    name = name.strip()
    declared = tuple(known) if known is not None else names()
    if name not in declared:
        raise UnknownOperation(
            f"unknown operation {name!r} in address {text!r}",
            f"known: {', '.join(sorted(declared)) or '(none)'}. A "
            f"breakpoint armed at an operation that does not exist is a "
            f"pause that will never come",
            f"address one of {', '.join(sorted(declared)) or '(none)'}")
    if not sep:
        return Address(name)
    if not span.strip():
        raise UnknownOperation(
            f"address {text!r} ends in {ADDRESS_SEPARATOR!r} with no region",
            "an address with a separator and no span names nowhere to run",
            f"give <start>-<end> in timeline seconds, or drop the "
            f"{ADDRESS_SEPARATOR!r} to mean the whole project")

    from library.tools import region as region_mod
    try:
        return Address(name, region_mod.parse(span))
    except ValueError as exc:
        raise UnknownOperation(
            f"address {text!r} names a region that does not parse",
            f"{exc}",
            "write the region as <start>-<end> in timeline seconds, e.g. "
            "45.0-72.0") from None


def looks_like_an_address(text: str) -> bool:
    """Whether a token is meant as an operation rather than a step id.

    A step id never contains the separator, and every registered
    operation name does contain a dot - so this needs no guessing and no
    lookahead into the DAG.
    """
    raw = (text or "").strip()
    return ADDRESS_SEPARATOR in raw or raw in names()


def by_node(node_id: str) -> tuple[Operation, ...]:
    """Everything owned by one DAG node."""
    return tuple(op for op in _REGISTRY if op.owning_node == node_id)


# ── Presentation: the CLI, and the skill that is a projection of it ──


def describe() -> str:
    width = max(len(op.name) for op in _REGISTRY)
    lines = [f"{len(_REGISTRY)} operations",
             "",
             f"{'name'.ljust(width)}  {'owning node'.ljust(22)}  scopes"]
    lines.append("-" * (width + 46))
    for op in _REGISTRY:
        lines.append(f"{op.name.ljust(width)}  "
                     f"{op.owning_node.ljust(22)}  {','.join(op.scopes)}")
        lines.append(f"{' ' * width}  {op.summary}")
    return "\n".join(lines)


def emit_skill() -> str:
    """The registry, rendered as an agent skill.

    A projection, never a source: a hand-written skill would state its
    prerequisites in prose, which is the defect being removed.  Generated,
    the prose cannot drift, and the registry stays the only contract.
    """
    out = [
        "---",
        "name: pipeline_operations",
        "description: >-",
        "  The named things the video pipeline can be asked to do, and the",
        "  scope each runs at. GENERATED from library/tools/operations.py -",
        "  do not edit; run `python3 -m library.tools.operations --emit-skill`.",
        "---",
        "",
        "# Pipeline operations",
        "",
        "An operation is a named entry point into an existing step's own code,",
        "run at a declared scope. It owns no logic of its own.",
        "",
        "## Scopes",
        "",
    ]
    for kind in scope_mod.KINDS:
        out.append(f"- `{kind}` - {scope_mod.KIND_LEGEND[kind]}")
    out += ["", "## Operations", "",
            "| operation | owning node | scopes | what it does |",
            "|---|---|---|---|"]
    for op in _REGISTRY:
        out.append(f"| `{op.name}` | `{op.owning_node}` | "
                   f"{', '.join(op.scopes)} | {op.summary} |")
    out += ["", "## Calling one", "",
            "```",
            "python3 -m library.tools.operations --list",
            ("python3 -m library.tools.operations <name> --project <path> "
             "[--region 45.0-72.0 | --clip clip_007] "
             "[--set name=<json>|@file.json]"),
            "```", ""]
    return "\n".join(out)


def parse_overrides(pairs: Iterable[str]) -> dict:
    """`--set name=<json>` and `--set name=@file.json`, into a dict.

    JSON rather than a bare string, because every argument this reaches
    is a structure - a subtitle plan, a segment's props, a list of
    re-measured speech regions. A parser that fell back to "it must be a
    string" would hand a step the text `[1, 2]` and let it fail somewhere
    further in, which is the confidently-wrong shape `Operation.execute`
    refuses everywhere else.
    """
    out: dict = {}
    for raw in pairs or ():
        name, sep, text = (raw or "").partition("=")
        name = name.strip()
        if not sep or not name:
            raise OperationError(
                f"--set {raw!r} names no value",
                "an override supplies one argument the DAG does not route "
                "to this step",
                f"write --set {raw or 'NAME'}=<json> or "
                f"--set {raw or 'NAME'}=@<file.json>, e.g. --set "
                f"stored_plan=@plan.json")
        if text.startswith("@"):
            path = Path(text[1:]).expanduser()
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise OperationError(
                    f"--set {name}=@{path} cannot be read",
                    f"{exc}",
                    f"point --set {name}=@ at a readable JSON file") from None
        try:
            out[name] = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OperationError(
                f"--set {name}=... is not valid JSON",
                f"{exc}. A value here is a structure",
                f"quote a string as '\"text\"', or pass --set {name}=@"
                f"<file.json>") from None
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.operations",
        description=("The named things the pipeline can be asked to do. "
                     "An operation is a step's own code, run at a scope."))
    parser.add_argument("operation", nargs="?", help="operation to run")
    parser.add_argument("--list", action="store_true",
                        help="list every operation and exit")
    parser.add_argument("--emit-skill", action="store_true",
                        help="print the registry as an agent skill")
    parser.add_argument("--project", default="",
                        help="project folder the operation runs against")
    parser.add_argument("--region", default="",
                        help="an interval of the TIMELINE, e.g. 45.0-72.0")
    parser.add_argument("--clip", default="", help="a clip id, e.g. clip_007")
    parser.add_argument("--json", action="store_true",
                        help="print the operation's result as JSON")
    parser.add_argument(
        "--set", action="append", metavar="NAME=VALUE", default=[],
        dest="overrides",
        help="Supply one argument the DAG does not route to this step: "
             "NAME=<json> or NAME=@<file.json>. Repeatable. This is what "
             "the refusal names when a step's signature asks for "
             "something a caller decides - a stored plan to splice into, "
             "one segment's props - rather than something a previous "
             "step recorded.")
    args = parser.parse_args(argv)

    if args.emit_skill:
        print(emit_skill())
        return 0
    if args.list or not args.operation:
        print(describe())
        return 0

    try:
        op = get(args.operation)
        where = scope_mod.from_cli(region_text=args.region, clip_id=args.clip)
        op.check_scope(where)
        if not args.project:
            raise RenRefusal(
                "no --project given",
                "an operation runs against a project: it is where the "
                "inputs come from and where the result lands",
                "re-run with --project <slug or project folder>")
        overrides = parse_overrides(args.overrides)
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        return REFUSAL_EXIT_CODE

    result = op.execute(args.project, scope=where, **overrides)

    if result.refused:
        print(f"REFUSED: {op.name}", file=sys.stderr)
        print(result.error, file=sys.stderr)
        if args.json:
            print(json.dumps(result.as_record(), indent=2))
        return REFUSAL_EXIT_CODE

    print(f"{op.name}: completed at {where}", file=sys.stderr)
    if args.json:
        print(json.dumps(result.as_record(), indent=2))
    else:
        json.dump(result.payload, sys.stdout, indent=2)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
