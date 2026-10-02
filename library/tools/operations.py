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

The DAG node is DERIVED, and LEGACY
-----------------------------------
Most of the runner's per-step services (the two ledgers, the run status,
the review gate, the marker routing, the step export, the run-level
collectors) are keyed by the DAG node id.  An operation does not name
one: it names its executor's step directory (`owning_dir`), and its node
is the node whose `step_ref` is that directory (`legacy_node`, answered
by `library/tools/dag_adapter.node_of`).  It is metadata, not identity:
an operation's `name` is its capability id
(`library/tools/capabilities.py`), and every node-keyed read goes
through `library/tools/dag_adapter.py`.  The handbrake (keyed by
project) and the provenance snapshot (no key) are the two services that
are not node-keyed.

What an operation RETURNS
-------------------------
`OperationResult` is defined here, once, for every operation - the
operation-derived hook conditions (`library/tools/hooks.py`) read it, and
a second result type elsewhere would be a second implementation.  Its
`status` is one of `STATUSES` (`COMPLETED`, `REFUSED`); `hollow` carries
the hollow-output check's verdict rather than a second rule.

**A refusal names a producer.**  `unsatisfied` carries the Requirement
objects themselves, not strings, so `refusal_reason()` can say what is
missing AND which step makes it.

What an operation REQUIRES and EFFECTS, both derived
----------------------------------------------------
`Operation.requires` is DERIVED, never hand-written: every requirement in
`library/tools/requirements.py` whose `consumers` include this operation's
`legacy_node`.  `Operation.effect` is DECLARED PER CAPABILITY (captain,
2026-10-02): each entry lists the state keys it `produces`, and its effect
is every requirement whose key at its node (`Requirement.key_at`) is one
of them.  A node's effect is derived from its capabilities
(`dag_adapter.node_effects`), never declared.  Same vocabulary, so an
effect can be matched against a precondition.  Both are properties with
no setter, and `_REGISTRY` carries no requirement literals.

An operation whose result is no requirement's key has an empty effect,
and `EMPTY_EFFECT_REASONS` gives each its evidence under one of four
kinds: ARTIFACT (the product lands on disk for a caller, a gate or
Resolve placement), BRIDGE (a pre-bridge's table is the model's
context), RECEIPT (a caller-supplied change returns a receipt, not the
build's key) and REGION UNIT (one span of one clip handed back to the
caller).  Verdicts derive theirs from `requirements.VERDICTS` and
optional edges from `requirements.OPTIONALS` (captain, 2026-09-23).  A
composer working backwards from requirements alone can never select an
empty-effect operation, and declaring an effect a capability does not
have would be the defect this module removes.  The graph-wide proof is
`tests/test_contract_audit.py::test_the_graph_is_sound`.

Executing
---------
`Operation.execute` gathers the step's inputs the way the RUNNER does -
`gather_step_inputs`, not a copy of it - checks the requirements, and
either runs the step's own function or REFUSES naming what is missing
and which step produces it.  A scope the operation does not support
raises `ScopeNotSupported`; an unknown name raises `UnknownOperation`.
`tests/test_operations.py`, `tests/test_operations_execute.py`.

Reachability
------------
    python3 -m library.tools.operations --list
    python3 -m library.tools.operations subtitles.render --project <p> --region 45.0-72.0
    python3 -m library.tools.operations subtitles.splice --project <p> \\
        --region 45.0-72.0 --set stored_plan=@subtitle_plan.json
    python3 -m library.tools.operations --emit-skill

Same registry, same scope vocabulary, same refusal, whether it is called
from a shell, from Python, or by the runner.  `--emit-skill` generates
the `pipeline_operations` skill.  Running one at a REGION is the
captain's third entry point; `docs/ENTRY_POINTS_MEASURED.md` measures
which operations reach it.

The measurements and rulings behind these rules (the service count, the
verb-layer alternative, the VERDICT and ANALYSIS kinds retired from the
blind set): docs/evidence/operations.md.
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
    legacy_node: str
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
            "legacy_node": self.legacy_node,
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
    owning_dir: str             # its directory under library/steps/
    body: str                   # step.py | bridge.py | post_bridge.py
    attr: str                   # the function's name in that body
    produces: tuple[str, ...]
    """The state keys this capability's result IS when its step runs it.

    DECLARED per capability, and the ONE vocabulary for what a
    capability produces (captain's ruling on punch list item 4,
    2026-10-02): `effect` is every requirement whose key, at this
    capability's node, is listed here.  A node's effect is no longer
    declared anywhere - it is DERIVED as the union of its capabilities'
    effects (`dag_adapter.node_effects`), a computed view for the
    adapter and legacy readers, not a second vocabulary.

    Siblings say what each really writes: `duration_zone.build` is
    `mesh_spine`'s bridge and writes `duration_zone`, not the spine
    `spine.mesh` writes; a touch-up returns a receipt, not `reel_build`.
    Every key must be one the owning step's manifest declares as an
    output (`capabilities.problems`).  Empty means the result is no
    state key - `EMPTY_EFFECT_REASONS` says why for each."""
    consumes: tuple[str, ...]
    """The state keys this capability READS - the mirror of `produces`.

    DECLARED per capability: `requires` is every requirement of its node
    whose `consumed_key` is listed here, plus the node's requirements no
    state key carries (the machine, an approval on file, a binding).
    `gather` narrows to the same keys, so an input the capability does
    not read is never demanded of it.  Empty for a capability whose
    arguments come from its caller or name one region (`reel.touchup`,
    `transcript.splice`): it gathers nothing from state."""
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
    def legacy_node(self) -> str:
        """The DAG node, reached through the compatibility adapter.

        Derived from `owning_dir` - the node whose `step_ref` is the
        executor's directory - so the capability id stays the identity,
        the node stays metadata, and no field can name a node the
        executor does not run under (`library/tools/dag_adapter.py`).
        """
        from library.tools import dag_adapter
        return dag_adapter.node_of(self)

    @property
    def requires(self) -> tuple:
        """Every requirement this operation's OWNING NODE has.

        Derived from `library/tools/requirements.py`, never listed here.
        Increment 3 already builds these from `inputs[].required` and the
        DAG's `data_mapping`, so an operation asks exactly what its step
        asks - and a manifest change reaches the operation with nothing
        to update.

        A requirement is keyed by its `consumers`, which are DAG node
        ids; `legacy_node` is what makes an operation addressable in that
        vocabulary.
        """
        from library.tools import dag_adapter
        return dag_adapter.requirements_consumed(self)

    @property
    def effect(self) -> tuple:
        """Every requirement this capability's declared `produces` satisfies.

        In the SAME vocabulary as `requires` - requirement names - so an
        effect can be matched against a precondition: a requirement is
        an effect when this capability's node is among its producers
        and the key it is satisfied by there (`Requirement.key_at`) is
        one this capability `produces`.

        Per capability, not per node: siblings owned by one node carry
        their own effects (`spine.mesh` the spine, its bridge
        `duration_zone.build` none).  Empty for the operations named in
        `EMPTY_EFFECT_REASONS`, and the emptiness is the truth: running
        one satisfies no precondition.  See the module docstring for the
        composition consequence.
        """
        from library.tools import dag_adapter
        return dag_adapter.requirements_produced(self)

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
        from library.tools import capability_outputs
        recorded = capability_outputs.node_outputs(state)
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
        return requirements.check([self.legacy_node],
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
        from library.tools import dag_adapter
        return dag_adapter.declaring_dag(self)

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
            self.legacy_node, dag, state,
            self._manifest_view(dag, manifests.get(self.legacy_node)),
            step_type="operation")

    def _manifest_view(self, dag: dict, manifest: dict | None):
        """The step's manifest with every input this capability does not
        `consume` marked optional, so gathering never DEMANDS what it does
        not read - and still hands it over when present.  Inputs are
        named on the consuming side of the edge, `consumes` on the
        producing side; the edges into the node translate."""
        if not manifest:
            return manifest
        consumed = set(self.consumes)
        read_as = {dst for edge in dag["edges"]
                   if edge["to"] == self.legacy_node
                   for src, dst in (edge.get("data_mapping") or {}).items()
                   if src in consumed}
        read_as |= consumed
        view = json.loads(json.dumps(manifest))
        for inp in view.get("interface", {}).get("inputs", []):
            if inp.get("name") not in read_as:
                inp["required"] = False
        return view

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
                operation=self.name, legacy_node=self.legacy_node,
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
                operation=self.name, legacy_node=self.legacy_node,
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
                operation=self.name, legacy_node=self.legacy_node,
                scope=where, status=REFUSED,
                error=(
                    f"{self.name} is the POST-BRIDGE of {self.legacy_node}: "
                    f"the dict it takes is the step's inputs PLUS its "
                    f"pre-bridge's output PLUS the model's answer, and an "
                    f"operation gathers only the first of those three.\n"
                    f"\nMissing what the model owes this step: "
                    f"{', '.join(owed)}.\n"
                    f"\nEither supply them - "
                    f"`operations.get({self.name!r}).execute(project, "
                    f"{owed[0]}=...)` - or run {self.legacy_node} so the "
                    f"runner asks the model for them. Resolving without "
                    f"them would produce a confident answer to a question "
                    f"nobody was asked."))

        unbound = self.unbound_parameters(arguments)
        if unbound:
            return OperationResult(
                operation=self.name, legacy_node=self.legacy_node,
                scope=where, status=REFUSED,
                error=self._teach_unbound(unbound, where))

        payload = self.run(**arguments)
        return OperationResult(
            operation=self.name, legacy_node=self.legacy_node,
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
            f"{self.name} is the PROMPT of {self.legacy_node}: its "
            f"implementation is {self.owning_dir}/{self.body}, which the "
            f"runner asks a model to answer - and an operation runs "
            f"ALONE, with no model to ask.\n"
            f"\nRun {self.legacy_node} so the runner asks the model for "
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
            from library.tools import dag_adapter
            _, producing = dag_adapter.requirement_index()
            for name, producers in deferrable:
                # The STEP is always named; a capability only when it
                # DECLARES producing this key.  Sibling ownership is not
                # evidence - `duration_zone.build` is owned by
                # `mesh_spine` but is its bridge and emits no spine -
                # so a node with no declaring capability names no
                # operation rather than guessing one.
                who = " or ".join(f"`{p}`" for p in producers)
                ops = producing.get(name, ())
                via = (" (capability " + " or ".join(
                    f"`{o}`" for o in ops) + ")") if ops else ""
                lines.append(f"  - {name}: produced by {who}{via} - run "
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
            f"routes to {self.legacy_node} carries "
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
            self._dag()).get(self.legacy_node) or {}
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
        return {"operation_id": self.name, "step_id": self.legacy_node}

    def check_scope(self, scope: Scope) -> None:
        if not self.supports(scope):
            raise ScopeNotSupported(
                f"{self.name} does not run at {scope.kind} scope",
                f"it runs at: {', '.join(self.scopes)}",
                f"run it at {', '.join(self.scopes)} scope instead")


# ── Operations whose derived effect is empty ────────────────────────
#
# Capabilities that satisfy no requirement in the registry each have a
# reason that was verified against the tree rather than assumed.  The
# mapping is operation name to the reason its effect is empty anyway -
# the contract auditor checks that every empty effect is named.
#
# A new capability whose effect is empty FAILS the auditor until its
# author classifies it below. Do NOT invent a
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
# Since effects are declared per capability (`Operation.produces`),
# the siblings that write no requirement's key are here too: the
# BRIDGE, RECEIPT and REGION UNIT rows below, and `reel.gate_stills`,
# which no longer borrows `verify_reels`' verdict.

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
    "reel.reading_context":
        "ARTIFACT. The pre-bridge of `judge_reels`: its table goes to "
        "the reader model and nowhere else. The owning node produces "
        "no requirement - see `reel.judge`.",
    "reel.judge":
        "ARTIFACT. The post-bridge returns `reel_judgement`, which no "
        "DAG edge carries: its readers, `reel_conformance_verifier` "
        "and the quality-bar CLI, open it off disk "
        "(`library/tools/reel_quality_bar.py:read_judgement`) - so no "
        "requirement names `judge_reels` as a producer.",
    # BRIDGE - a pre-bridge's table is the model's context.  It reaches
    # state, but no requirement names a key it writes.
    "duration_zone.build":
        "BRIDGE. `mesh_spine`'s pre-bridge writes `duration_zone`, the "
        "band the model is shown; the spine every consumer requires is "
        "`spine.mesh`'s. No requirement names `duration_zone`.",
    "reel.candidates":
        "BRIDGE. `select_reels`' pre-bridge writes the measured "
        "exchanges the selector reads; `reel_selection`, the key "
        "`judge_reels` requires, is `reel.select`'s.",
    # RECEIPT - a caller-supplied change to something already built
    # returns a receipt, not the state key the build wrote.
    "reel.touchup":
        "RECEIPT. Returns `reel_touchup`, a receipt for one change to "
        "one built timeline - no output `build_reels` declares, and not "
        "`reel_build`, which only `reel.build` writes.",
    "reel.entry_motion":
        "RECEIPT. Returns `reel_touchup` for one animated element on an "
        "already-built timeline - no output `build_reels` declares; "
        "`reel_build` is `reel.build`'s.",
    "reel.set_properties":
        "RECEIPT. Returns `reel_touchup` for properties written onto "
        "one placed clip - no output `build_reels` declares; "
        "`reel_build` is `reel.build`'s.",
    "reel.ask":
        "RECEIPT. Writes each approved reel's visual asks and returns "
        "`reel_ask`, which no requirement of any `build_reels` "
        "consumer reads - nothing is built, so no `reel_build`.",
    "reel.gate_stills":
        "ARTIFACT. Caller-supplied: grabs stills off one timeline to "
        "disk and returns where they went; the verdict "
        "`verify_reels` produces is `reel.verify`'s, not this grab's.",
    "subtitles.render_segment":
        "ARTIFACT. One caption segment rendered to disk and described; "
        "`subtitle_overlay`, the `render_subtitles` key "
        "`compile_manifest` requires, is `subtitles.render`'s.",
    "subtitles.rerender_swap":
        "ARTIFACT. Caller-supplied: re-renders named segments and swaps "
        "them onto every timeline, returning a report - no state key "
        "`render_subtitles` declares.",
    "objects.segment":
        "ARTIFACT. The masks land on disk under the segmentation area "
        "for `compile_manifest` to place; `object_segmentation` reaches "
        "it only through an input that manifest declares OPTIONAL, "
        "which `run_scope.prerequisites` deliberately excludes - so no "
        "requirement names `object_segmentation` as a producer.",
    # REGION UNIT - one span of one clip, handed back to the caller.
    "transcript.reindex":
        "REGION UNIT. Re-measures the speech of ONE span of one clip and "
        "returns the regions; `temporal_event_indices` is the project "
        "index `temporal.index` writes for `temporal_index`.",
    "transcript.splice":
        "REGION UNIT. Splices fresh regions into ONE clip's index "
        "document and returns it; the caller files it, and the "
        "`temporal_index` project index is `temporal.index`'s.",
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
        owning_dir="step_0_01_validate_sfx_library", body="step.py",
        attr="validate_sfx_library",
        produces=("sfx_library_status",),
        consumes=(),
    ),
    Operation(
        name="footage.scan",
        summary="Scan the project folder for raw video files",
        owning_dir="step_1_01_scan_project", body="step.py",
        attr="scan_project_folder",
        produces=(
            "project_config",
            "raw_footage_files",
            "raw_audio_files",
            "skipped_files",
            "total_files",
        ),
        consumes=(),
    ),
    Operation(
        name="footage.catalog",
        summary="Extract per-file metadata into the ordered clip catalog",
        owning_dir="step_1_02_catalog_footage", body="step.py",
        attr="catalog_footage",
        produces=(
            "clip_catalog",
            "project_fps",
            "source_resolution",
            "audio_catalog",
            "skipped_files",
            "total_clips",
        ),
        consumes=("raw_footage_files",),
    ),
    Operation(
        name="semantics.analyse",
        summary="Run the v3 vision pass over clips without a profile",
        owning_dir="step_1_03_semantic_analysis", body="step.py",
        attr="analyse_semantics",
        produces=("semantic_analysis_documents", "total_clips_analyzed"),
        consumes=("raw_footage_files",),
    ),
    Operation(
        name="temporal.index",
        summary="Index each clip's speech, sound and motion over time",
        owning_dir="step_1_04_temporal_index", body="step.py",
        attr="index_project",
        produces=(
            "temporal_event_indices",
            "total_indexed",
            "total_failed",
            "total_reused",
            "index_dir",
            "audio_indices",
            "source",
        ),
        consumes=(
            "raw_footage_files",
            "semantic_analysis_documents",
            "clip_catalog",
        ),
    ),
    Operation(
        name="prosody.analyse",
        summary="Measure pitch, pace, voice quality and intensity per clip",
        owning_dir="step_1_05_prosody_analysis", body="step.py",
        attr="analyse_prosody",
        produces=("prosody_analysis",),
        consumes=("raw_footage_files", "temporal_event_indices"),
    ),
    Operation(
        name="ocr.extract",
        summary="Extract on-screen text from the footage",
        owning_dir="step_1_07_ocr_extraction", body="step.py",
        attr="extract_ocr",
        produces=("ocr_extraction",),
        consumes=("raw_footage_files",),
    ),
    Operation(
        name="creative.direct",
        summary="Decide the video's creative direction from the preflight reads",
        owning_dir="step_2_01_creative_direction", body="handoff.md",
        attr="",
        produces=("creative_direction",),
        consumes=(
            "semantic_analysis_documents",
            "temporal_event_indices",
            "clip_catalog",
        ),
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
        owning_dir="step_2_02_speech_sequence", body="post_bridge.py",
        attr="enrich_speech_sequence",
        produces=("speech_sequence",),
        consumes=(
            "semantic_analysis_documents",
            "temporal_event_indices",
            "creative_direction",
        ),
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
        owning_dir="step_2_04_music_selection", body="post_bridge.py",
        attr="resolve_selection",
        produces=("music_selection",),
        consumes=("creative_direction",),
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
        owning_dir="step_2_05_mesh_spine", body="bridge.py",
        attr="build_duration_zone",
        produces=("duration_zone",),
        consumes=(
            "speech_sequence",
            "music_selection",
            "creative_direction",
            "music_analysis",
            "clip_catalog",
            "semantic_analysis_documents",
        ),
    ),
    Operation(
        name="spine.mesh",
        summary="Mesh the model's spine against the speech and the music into the timed spine",
        owning_dir="step_2_05_mesh_spine", body="post_bridge.py",
        attr="resolve_spine",
        produces=("audio_spine", "timed_spine"),
        consumes=(
            "speech_sequence",
            "music_selection",
            "creative_direction",
            "music_analysis",
            "clip_catalog",
            "semantic_analysis_documents",
        ),
    ),
    Operation(
        name="aroll.assign",
        summary="Map speech blocks and hook to their A-roll source files",
        owning_dir="step_3_01_assign_aroll", body="step.py",
        attr="assign_a_roll",
        produces=(
            "a_roll_assignments",
            "voiceover_assignments",
            "hook_assignment",
        ),
        consumes=("audio_spine", "clip_catalog", "project_fps"),
    ),
    Operation(
        name="aroll.splice",
        summary="Re-assign a region's A-roll from the spine and put it back into the stored assignments",
        owning_dir="step_3_01_assign_aroll", body="step.py",
        attr="splice_region_aroll",
        produces=(
            "a_roll_assignments",
            "voiceover_assignments",
            "hook_assignment",
        ),
        consumes=("audio_spine", "clip_catalog", "project_fps"),
        # REGION only, beside `aroll.assign`: the region's blocks are
        # re-assigned from the (re-anchored) spine and spliced into the
        # recorded output, supplied as `stored_assignments`. Every other
        # block's assignment comes back byte-identical (`plan_splice`),
        # and a region whose blocks MOVED on the timeline is refused as
        # the re-plan it is.
        scopes=(REGION,),
    ),
    Operation(
        name="broll.resolve",
        summary="Resolve B-roll selections to placed cutaways with source ranges",
        owning_dir="step_3_02_select_broll", body="post_bridge.py",
        attr="resolve_broll",
        produces=("b_roll_assignments", "b_roll_interjections"),
        consumes=(
            "a_roll_assignments",
            "semantic_analysis_documents",
            "clip_catalog",
            "temporal_event_indices",
            "creative_direction",
            "timed_spine",
        ),
        # The deterministic half of a hybrid step: the cutaway SELECTION
        # stays the model's answer (supplied as overrides, or run
        # `select_broll` so the runner asks the model for it) and this
        # resolves each choice to a placed source range. PROJECT only,
        # and the default: placement is over the whole spine, so a region
        # address would promise a scope the function does not keep.
    ),
    Operation(
        name="broll.splice",
        summary="Resolve a region's re-planned cutaways and put them back into the stored selections",
        owning_dir="step_3_02_select_broll", body="post_bridge.py",
        attr="splice_region_broll",
        produces=("b_roll_assignments", "b_roll_interjections"),
        consumes=(
            "a_roll_assignments",
            "semantic_analysis_documents",
            "clip_catalog",
            "temporal_event_indices",
            "creative_direction",
            "timed_spine",
        ),
        # REGION only, beside `broll.resolve`: the model's answer FOR THE
        # REGION is supplied as `broll_creative` (and
        # `b_roll_interjections`), the selections it goes INTO as
        # `stored_selections`. Cutaways keyed outside the region come back
        # byte-identical (`plan_splice`); V2 collisions with kept ones
        # are placed around or refused.
        scopes=(REGION,),
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
        owning_dir="step_3_04_select_reels", body="bridge.py",
        attr="build_context",
        produces=(
            "reel_candidates",
            "turns",
            "length_guidance_seconds",
            "picture_holes",
            "who_leads_was_inferred",
            "declared_speakers",
            "undetermined",
            "reel_diagnostics_reference",
        ),
        consumes=("timeline_transcript",),
    ),
    Operation(
        name="reel.select",
        summary="Check the model's chosen moments against the cut and publish them PROPOSED",
        owning_dir="step_3_04_select_reels", body="post_bridge.py",
        attr="resolve",
        produces=("reel_selection",),
        consumes=("timeline_transcript",),
    ),
    Operation(
        name="reel.reading_context",
        summary="Work out the words each proposed reel plays, for a reader who has not heard the episode",
        owning_dir="step_3_05_judge_reels", body="bridge.py",
        attr="build_context",
        produces=("reels_to_read", "reels_not_readable"),
        consumes=("reel_selection", "timeline_transcript"),
    ),
    Operation(
        name="reel.judge",
        summary="Check each reading against its reel's own words and derive the verdicts and ordering",
        owning_dir="step_3_05_judge_reels", body="post_bridge.py",
        attr="resolve",
        produces=("reel_judgement",),
        consumes=("reel_selection", "timeline_transcript"),
    ),
    Operation(
        name="reel.build",
        summary="Cut every APPROVED moment onto its own Resolve timeline, bad takes removed",
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="build_reels",
        produces=("reel_build",),
        consumes=("timeline_transcript",),
    ),
    Operation(
        name="reel.touchup",
        summary="Change one built reel's own timeline in place, instead of rebuilding it",
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="touch_reel",
        produces=(),
        consumes=(),
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
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="animate_entry",
        produces=(),
        consumes=(),
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
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="set_clip_properties",
        produces=(),
        consumes=(),
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
        owning_dir="step_7_01_build_reels", body="step.py",
        attr="ask_reels",
        produces=("reel_ask",),
        consumes=("timeline_transcript",),
    ),
    Operation(
        name="reel.verify",
        summary="Grade the built reel timelines against the plan they were built from",
        owning_dir="step_7_02_verify_reels", body="step.py",
        attr="verify_reels",
        produces=("reel_verification",),
        consumes=("reel_build", "timeline_transcript"),
    ),
    Operation(
        name="reel.gate_stills",
        summary="Grab gate stills at named reel-relative frames off one built reel timeline",
        owning_dir="step_7_02_verify_reels", body="step.py",
        attr="grab_gate_stills",
        produces=(),
        consumes=(),
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
        owning_dir="step_2_06_music_analysis", body="step.py",
        attr="analyse_music",
        produces=("music_analysis",),
        consumes=("music_selection",),
    ),
    Operation(
        name="subtitles.plan",
        summary="Generate subtitle entries from the spine's own word timestamps",
        owning_dir="step_4_01_plan_subtitles", body="step.py",
        attr="generate_subtitles",
        produces=("subtitle_plan",),
        consumes=("audio_spine", "rough_cut_review"),
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
        owning_dir="step_4_01_plan_subtitles", body="step.py",
        attr="splice_region_plan",
        produces=("subtitle_plan",),
        consumes=("audio_spine", "rough_cut_review"),
        # REGION only.  A splice with no region is a whole-plan
        # overwrite, which is what `subtitles.plan` at PROJECT scope
        # already is - naming it twice would be the second
        # implementation Ruling 1 forbids.
        scopes=(REGION,),
    ),
    Operation(
        name="rough_cut.review",
        summary="Run the mechanical duration, continuity and source checks over the rough cut",
        owning_dir="step_3_03_review_rough_cut", body="step.py",
        attr="run_mechanical_checks",
        produces=("rough_cut_review",),
        consumes=(
            "b_roll_assignments",
            "a_roll_assignments",
            "audio_spine",
            "creative_direction",
            "speech_sequence",
        ),
    ),
    Operation(
        name="transitions.resolve",
        summary="Resolve the model's transition plan to execution specs",
        owning_dir="step_4_02_plan_transitions", body="post_bridge.py",
        attr="resolve_transitions",
        produces=("transition_spec",),
        consumes=(
            "a_roll_assignments",
            "clip_catalog",
            "project_fps",
            "creative_direction",
            "music_selection",
            "rough_cut_review",
            "temporal_event_indices",
            "b_roll_assignments",
            "timed_spine",
            "music_analysis",
        ),
        # The deterministic half of a hybrid step: the transition
        # SELECTION stays the model's answer (supplied as overrides, or
        # run `plan_transitions` so the runner asks the model for it)
        # and this resolves each entry against the timed spine and the
        # real beat grid. PROJECT only, and the default: the resolution
        # reads the whole spine by design, so a region address would
        # promise a scope the function does not keep.
    ),
    Operation(
        name="transitions.splice",
        summary="Resolve a region's re-planned transitions and put them back into the stored plan",
        owning_dir="step_4_02_plan_transitions", body="post_bridge.py",
        attr="splice_region_transitions",
        produces=("transition_spec",),
        consumes=(
            "a_roll_assignments",
            "clip_catalog",
            "project_fps",
            "creative_direction",
            "music_selection",
            "rough_cut_review",
            "temporal_event_indices",
            "b_roll_assignments",
            "timed_spine",
            "music_analysis",
        ),
        # REGION only, beside `transitions.resolve`: a region owns the
        # cuts INTO the blocks it touches (plus the end slot on the last
        # block). The model's answer for those cuts is supplied as
        # `transition_creative`, the plan it goes INTO as
        # `stored_transitions`; every other cut comes back byte-identical
        # (`plan_splice`).
        scopes=(REGION,),
    ),
    Operation(
        name="vfx.resolve",
        summary="Resolve the model's VFX plan to execution specs",
        owning_dir="step_4_03_plan_vfx", body="post_bridge.py",
        attr="resolve_vfx",
        produces=("enhancement_spec",),
        consumes=(
            "a_roll_assignments",
            "creative_direction",
            "rough_cut_review",
            "semantic_analysis_documents",
            "b_roll_assignments",
            "timed_spine",
            "music_analysis",
            "music_selection",
        ),
        # The deterministic half of a hybrid step: the effect SELECTION
        # stays the model's answer (supplied as overrides, or run
        # `plan_vfx` so the runner asks the model for it) and this
        # resolves each entry against the timed spine, dropping what
        # names no real block. PROJECT only, and the default: the
        # resolution reads the whole spine by design, so a region
        # address would promise a scope the function does not keep.
    ),
    Operation(
        name="vfx.splice",
        summary="Resolve a region's re-planned effects and put them back into the stored plan",
        owning_dir="step_4_03_plan_vfx", body="post_bridge.py",
        attr="splice_region_vfx",
        produces=("enhancement_spec",),
        consumes=(
            "a_roll_assignments",
            "creative_direction",
            "rough_cut_review",
            "semantic_analysis_documents",
            "b_roll_assignments",
            "timed_spine",
            "music_analysis",
            "music_selection",
        ),
        # REGION only, beside `vfx.resolve` the way `subtitles.splice`
        # sits beside `subtitles.plan`. The model's answer FOR THE REGION
        # is supplied as `vfx_creative` and the plan it goes INTO as
        # `stored_spec`; every effect on a block outside the region comes
        # back byte-identical and the report measures it
        # (`plan_splice`). At PROJECT scope this would be `vfx.resolve`
        # again.
        scopes=(REGION,),
    ),
    Operation(
        name="sfx.resolve",
        summary="Resolve the model's SFX plan to playable placements",
        owning_dir="step_4_04_plan_sfx", body="post_bridge.py",
        attr="resolve_sfx",
        produces=("sfx_spec",),
        consumes=(
            "music_selection",
            "music_analysis",
            "semantic_analysis_documents",
            "creative_direction",
            "temporal_event_indices",
            "b_roll_assignments",
            "rough_cut_review",
            "timed_spine",
            "transition_spec",
            "project_fps",
        ),
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
        name="sfx.splice",
        summary="Place a region's re-planned sounds and put them back into the stored plan",
        owning_dir="step_4_04_plan_sfx", body="post_bridge.py",
        attr="splice_region_sfx",
        produces=("sfx_spec",),
        consumes=(
            "music_selection",
            "music_analysis",
            "semantic_analysis_documents",
            "creative_direction",
            "temporal_event_indices",
            "b_roll_assignments",
            "rough_cut_review",
            "timed_spine",
            "transition_spec",
            "project_fps",
        ),
        # REGION only, beside `sfx.resolve` as `vfx.splice` sits beside
        # `vfx.resolve`: the model's answer FOR THE REGION is supplied as
        # `sfx_creative` and the plan it goes INTO as `stored_spec`, and
        # every sound planned from a block outside the region comes back
        # byte-identical (`plan_splice`).
        scopes=(REGION,),
    ),
    Operation(
        name="transcript.reindex",
        summary="Re-measure the speech in one region, back at the raw footage",
        owning_dir="step_1_04_temporal_index", body="step.py",
        attr="reindex_region",
        produces=(),
        consumes=(),
        # REGION only, for the same reason: re-indexing everything is
        # what the step already does at PROJECT scope.
        scopes=(REGION,),
    ),
    Operation(
        name="transcript.splice",
        summary="Put a re-measured region back into the per-clip speech index",
        owning_dir="step_1_04_temporal_index", body="step.py",
        attr="splice_region_index",
        produces=(),
        consumes=(),
        scopes=(REGION,),
    ),
    Operation(
        name="subtitles.render",
        summary="Render one overlay artefact per captioned spine block",
        owning_dir="step_4_05_render_subtitles", body="step.py",
        attr="render_subtitle_overlays",
        produces=("subtitle_overlay",),
        consumes=("subtitle_plan", "audio_spine", "project_fps"),
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
        owning_dir="step_4_05_render_subtitles", body="step.py",
        attr="render_one_segment",
        produces=(),
        consumes=(),
        # The unit increment 5's region-scoped redo reaches: one segment,
        # so REGION is the scope that means anything here, and CLIP is
        # not offered because a segment is addressed by its timeline
        # span rather than by the clip it was cut from.
        scopes=(PROJECT, REGION),
    ),
    Operation(
        name="subtitles.rerender_swap",
        summary="Re-render named caption segments and swap them onto every timeline holding the old file",
        owning_dir="step_4_05_render_subtitles", body="step.py",
        attr="rerender_and_swap",
        produces=(),
        consumes=(),
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
        owning_dir="step_4_06_render_motion_graphics", body="post_bridge.py",
        attr="render_motion_graphics",
        produces=(
            "motion_graphics_overlay",
            "behind_subject_overlays",
            "timed_text_overlay",
        ),
        consumes=(
            "enhancement_spec",
            "audio_spine",
            "creative_direction",
            "project_fps",
        ),
    ),
    Operation(
        name="motion_graphics.render_segment",
        summary="Render ONE motion-graphics overlay segment",
        owning_dir="step_4_06_render_motion_graphics", body="post_bridge.py",
        attr="render_one_segment",
        produces=(),
        consumes=(),
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
        owning_dir="step_5_01_color_grade", body="post_bridge.py",
        attr="resolve_color_grade",
        produces=("color_grade_spec",),
        consumes=(
            "a_roll_assignments",
            "b_roll_assignments",
            "creative_direction",
            "semantic_analysis_documents",
            "clip_catalog",
        ),
    ),
    Operation(
        name="audio_mix.resolve",
        summary="Join the mix answer to the bed and speech measurements as per-window clip gain",
        owning_dir="step_5_02_audio_mix", body="post_bridge.py",
        attr="resolve_audio_mix",
        produces=("audio_mix_spec",),
        consumes=("audio_spine", "music_selection", "a_roll_assignments"),
        # The stdin-driven entry point, so `data` binds the whole
        # gathered dict under the known spelling - the
        # motion_graphics.render shape, not the speech.enrich
        # inner-unit one. The manifest declares `llm_outputs: []`,
        # so `missing_model_answer` never refuses: the decided-value
        # ladder records an undecided run as fallback or undetermined
        # rather than raising. Bridge outputs are not gathered, so an
        # operation run resolves over the step inputs alone.
    ),
    Operation(
        name="cohesion.review",
        summary="Review the planned transitions and sound against the declared direction",
        owning_dir="step_5_03_creative_cohesion", body="step.py",
        attr="review_creative_cohesion",
        produces=("cohesion_review",),
        consumes=("creative_direction", "transition_spec", "sfx_spec"),
        # The plain step.py case: `review_creative_cohesion(inputs)`
        # takes the whole gathered dict, the render.build shape.
    ),
    Operation(
        name="objects.segment",
        summary="Segment the subjects of the clips a grade or behind-subject plan names",
        owning_dir="step_1_06_object_segmentation", body="step.py",
        attr="segment_triggered_clips",
        produces=("object_segmentation", "matte_trigger"),
        consumes=("clip_catalog",),
        # The plain step.py case: the whole gathered dict under `data`.
        # Its body was `main()` alone until its output had to be
        # recorded under a capability id (`capability_outputs`).
    ),
    Operation(
        name="manifest.compile",
        summary="Compile every plan the run recorded into the assembly manifest",
        owning_dir="step_5_04_compile_manifest", body="step.py",
        attr="compile_step",
        produces=("assembly_manifest",),
        consumes=(
            "a_roll_assignments",
            "audio_mix_spec",
            "audio_spine",
            "b_roll_assignments",
            "clip_catalog",
            "music_selection",
            "semantic_analysis_documents",
            "subtitle_overlay",
            "subtitle_plan",
        ),
        # `compile_step(inputs)` derives the output directory from
        # `project_folder` (the `out_dir` `compile_manifest` takes) and
        # compiles from the project's recorded state, AGENTS.md 10.1.
    ),
    Operation(
        name="render.build",
        summary="Build the final timeline in DaVinci Resolve and export the finished video",
        owning_dir="step_6_01_render", body="step.py",
        attr="run",
        produces=("render_output",),
        consumes=("assembly_manifest",),
        # The plain step.py case: `run(inputs)` takes the whole
        # gathered dict under the known `inputs` spelling, the
        # footage.scan shape. Needs Resolve at call time.
    ),
    Operation(
        name="validation.resolve",
        summary="Combine the deterministic checks and the model's reading into one verdict",
        owning_dir="step_6_02_validate_output", body="post_bridge.py",
        attr="resolve_validation",
        produces=("validation_result",),
        consumes=("render_output", "assembly_manifest"),
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
    from library.tools import dag_adapter
    return dag_adapter.capabilities_at(node_id)


# ── Presentation: the CLI, and the skill that is a projection of it ──


def describe() -> str:
    width = max(len(op.name) for op in _REGISTRY)
    lines = [f"{len(_REGISTRY)} operations",
             "",
             f"{'name'.ljust(width)}  {'owning node'.ljust(22)}  scopes"]
    lines.append("-" * (width + 46))
    for op in _REGISTRY:
        lines.append(f"{op.name.ljust(width)}  "
                     f"{op.legacy_node.ljust(22)}  {','.join(op.scopes)}")
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
        out.append(f"| `{op.name}` | `{op.legacy_node}` | "
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

    # The execution receipt: what this run wrote, under the capability
    # id, with the legacy node derived (`provenance.observing_operation`).
    from library.tools import provenance
    with provenance.observing_operation(args.project, op.name):
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
