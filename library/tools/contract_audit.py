"""One declarative auditor over the capability graph, and nothing else.

The plan's item 1: the suite held the same handful of contract
properties in a dozen different test files - producers declare outputs,
consumers declare inputs, edges are valid, every value has a reader,
capabilities execute only what they own - each proven through its own
source scan or registry walk.  The production architecture has since
consolidated those concepts in the capability registry
(`library/tools/operations.py`, `library/tools/capabilities.py`), the
requirement layer (`library/tools/requirements.py`) and the DAG adapter
(`library/tools/dag_adapter.py`), so the proofs consolidate here: one
evaluation of the graph, one `problems() == []` assertion.

Two speeds, and the split is deliberate
---------------------------------------
`problems()` is FAST (registry + requirements + DAG + manifests, no
source parsing, no subprocesses): the shape of the contract.  A broken
shape refuses every run, so this is the continuously-run half.

`survey_problems()` is SLOW (the `output_contract` reader survey and
the `input_contract` declaration survey, each re-parsing `library/`
from scratch): the substance of the contract - every produced value
really has a reader, every declared input really refuses.  Same
assertion shape, run beside the fast one rather than inside it, so a
slow survey can never make the shape check late.

What lives here, by section
---------------------------
1. identity, executors, effects, locks, step dirs, artifacts -
   `capabilities.problems()`, wholesale rather than restated;
2. every consumer and producer names a real node;
3. no dead preconditions - a requirement nothing consumes is a goal
   (`verdict`/`optional`), never a precondition;
4. no unsatisfiable needs - a non-machine requirement nothing produces
   is satisfied from outside the pipeline (`EXTERNAL_STATE`), never
   silently unmakable;
5. every requirement carries its two witnesses (refuting and
   satisfying), so the refusability the suite executes is structurally
   present before it is behaviourally proven;
6. scopes are sound - non-empty, and only the vocabulary `scope.py`
   defines;
7. machine needs are sound - `assumes_machine` is environment-kind, and
   every environment requirement names its check and its remedy.

What does NOT live here
-----------------------
Single ownership across nodes is intentionally absent: `spine.word_timings`
is legitimately produced by three nodes, so "one producer" would fail
correct output.  Ownership is per (requirement, node) pair - some
capability of that node declares producing it - and that is section 1's.
Ruling 1 (an operation owns no logic) is a source-shape policy and
lives in `library/tools/static_check.py`, as do the prompt/code text
policies.  Behavioural proofs - a requirement really refusing, a bridge
really emitting only declared keys, a sparse plan really passing -
stay as scenario tests; the auditor proves the declarations they rest
on, not the behaviour itself.

    python3 -m library.tools.contract_audit --check
    python3 -m library.tools.contract_audit --survey
"""

from __future__ import annotations

import argparse

GOAL_KINDS = ("verdict", "optional")
"""Requirement kinds that are goals, not preconditions: produced for a
reader off the DAG's edges (a gate, a CLI, the next run), so empty
`consumers` is the declaration rather than a dead check."""


def problems(registry=None, reqs=None) -> list:
    """Every way the capability graph breaks its contract.  Empty is sound.

    `registry` and `reqs` are injectable so the auditor's own tests can
    plant deliberately-invalid graphs; production calls pass neither.
    """
    from library.tools import capabilities, dag_adapter, operations
    from library.tools import requirements as req_mod
    from library.tools.scope import PROJECT, REGION

    registry = operations.all() if registry is None else tuple(registry)
    reqs = req_mod.all_requirements() if reqs is None else list(reqs)
    out = list(capabilities.problems(registry))

    nodes = dag_adapter.node_ids()
    # Satisfied from outside the pipeline, by declaration: hand-written
    # in `EXTERNAL_STATE`, or runner-injected (`derive_runner_injected_keys`
    # names the inputs the runner supplies from outside the DAG).  A
    # producer-less need in the MIDDLE of the graph is neither, and
    # fails below.
    external = {r.name for r in req_mod.EXTERNAL_STATE} | {
        r.name for r in req_mod.derive_runner_injected_keys() if not r.produced_by
    }

    for r in reqs:
        for consumer in r.consumers:
            if consumer not in nodes:
                out.append(
                    f"requirement {r.name} is consumed by "
                    f"{consumer!r}, which is not a node"
                )
        for producer in r.produced_by:
            if producer not in nodes:
                out.append(
                    f"requirement {r.name} names producer "
                    f"{producer!r}, which is not a node"
                )
        if not r.consumers and r.kind not in GOAL_KINDS:
            out.append(
                f"requirement {r.name} (kind {r.kind!r}) is "
                f"consumed by nothing: a precondition no step "
                f"asks is dead, and a goal belongs in "
                f"{GOAL_KINDS}"
            )
        if (
            not r.produced_by
            and r.kind != req_mod.KIND_ENVIRONMENT
            and r.name not in external
        ):
            out.append(
                f"requirement {r.name} (kind {r.kind!r}) is "
                f"produced by nothing and is not environment "
                f"nor in EXTERNAL_STATE: nothing inside or "
                f"outside the pipeline can satisfy it"
            )
        if not getattr(r, "refuting_context", None):
            out.append(
                f"requirement {r.name} names no refuting "
                f"context: refusability cannot be executed"
            )
        if not getattr(r, "satisfying_context", None):
            out.append(
                f"requirement {r.name} names no satisfying "
                f"context: passing cannot be executed"
            )

    env_by_name = {r.name: r for r in reqs if r.kind == req_mod.KIND_ENVIRONMENT}
    for r in reqs:
        if r.kind == req_mod.KIND_ENVIRONMENT:
            if not callable(getattr(r, "check", None)):
                out.append(
                    f"machine requirement {r.name} names no "
                    f"check: the machine need cannot be tested"
                )
            if not getattr(r, "describe", ""):
                out.append(
                    f"machine requirement {r.name} names no "
                    f"remedy: a refusal cannot say what to fix"
                )

    allowed_scopes = {PROJECT, REGION}
    for op in registry:
        scopes = tuple(getattr(op, "scopes", ()))
        if not scopes:
            out.append(f"{op.name}: declares no scope, so nothing can address it")
        for scope in scopes:
            if scope not in allowed_scopes:
                out.append(
                    f"{op.name}: scope {scope!r} is not one the "
                    f"runner honours ({sorted(allowed_scopes)})"
                )
        for need in getattr(op, "assumes_machine", ()):
            if need not in env_by_name:
                out.append(
                    f"{op.name}: assumes machine need {need!r}, "
                    f"which is no environment requirement"
                )
    return out


def survey_problems() -> list:
    """The slow half: every value has a reader, every input refuses.

    Delegates to the two surveys rather than restating them - each
    survey's instrument keeps its own unit tests; this is the single
    ratchet that the tree agrees with its own tables.
    """
    from library.tools import input_contract, output_contract

    out = [f"output_contract: {d}" for d in output_contract.disagreements()]
    out += [
        f"input_contract: {d}"
        for d in input_contract.disagreements(input_contract.survey())
    ]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.contract_audit",
        description="One declarative audit over the capability graph.",
    )
    parser.add_argument(
        "--check", action="store_true", help="print every fast contract problem"
    )
    parser.add_argument(
        "--survey", action="store_true", help="print every slow survey disagreement"
    )
    args = parser.parse_args(argv)
    if args.survey:
        found = survey_problems()
        print("\n".join(found) or "surveys agree")
        return 1 if found else 0
    found = problems()
    print("\n".join(found) or "sound")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
