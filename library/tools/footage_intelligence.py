"""Footage intelligence's own orchestration: analysis capabilities, by id.

Punch list item 7 (captain, 2026-10-01): "analysis should not
conceptually be a partial execution of an editing DAG". `ren analyze`
used to run its step half as `manage_project.py run --target
footage_analysis` - the editing runner, walking the edit DAG and
stopping early. This module replaces that: it SELECTS analysis
capabilities by id (`library/tools/capabilities.py`), COMPOSES them by
what each requires and effects, and EXECUTES each through
`Operation.execute` - the step's own function, the same input gathering
the runner uses, the same refusal when a prerequisite is missing.

    agent goal -> selection (ids) -> composition (requires/effects)
               -> executor (Operation.execute, one child process each)
               -> verification (`check_output_is_real`) -> receipt

What stays shared with editing, and why
---------------------------------------
Editing CONSUMES what analysis produces, so the result lands where the
edit reads it: the capability's record (`capability_outputs`) and the
preflight ledger. `analysis_state` owns the analysis-facing state
boundary, including identity invalidation and step exports. No DAG is
walked and no edit step can run. `ren edit <collection>` therefore
continues with preflight already done, exactly as before.

Provenance records each execution under its CAPABILITY id
(`provenance.observing_operation`), not as a step of a run.

Why a child process per capability
----------------------------------
The runner isolates every step in its own interpreter, and the analysis
steps are the reason: a vision model, then WhisperX, then Praat, each
holding gigabytes it never hands back. Running them in one process would
stack those footprints. The child writes its result to a file, never to
stdout, because the libraries these steps load print to stdout.

    python3 -m library.tools.footage_intelligence <project> [--memory-only]
        [--with ocr.extract] [--skip semantics.analyse] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Selection ──────────────────────────────────────────────────────

ANALYSIS = ("footage.scan", "footage.catalog", "semantics.analyse",
            "temporal.index", "prosody.analyse")
"""What an analysis run executes by default: the separable-product
report's boundary - scan, catalog, vision, temporal index and prosody -
and nothing that plans an edit. Object segmentation stays on the edit
side: its trigger is a later plan, not the footage."""

MEMORY_ONLY = ("footage.scan", "footage.catalog")
"""`--memory-only`: what the memory lanes cannot run without."""

OPT_IN = ("ocr.extract",)
"""Analysis capabilities a run executes only when asked (`--with`): OCR
has no reader (run_scope.DESELECTED_BY_DEFAULT says why)."""

ROSTER = ANALYSIS + OPT_IN

# Per-capability outcomes.
BUILT = "built"
REUSED = "reused"
FAILED = "failed"
NOT_RUN = "not_run"


def resolve_name(name: str) -> str:
    """A roster capability id, from its id or its legacy node name.

    The node name is accepted because `ren analyze -- --with
    ocr_extraction` was the documented spelling before this module.
    """
    if name in ROSTER:
        return name
    from library.tools import dag_adapter, operations
    matches = [op.name for op in operations.all()
               if op.name in ROSTER and dag_adapter.node_of(op) == name]
    if len(matches) == 1:
        return matches[0]
    raise RenRefusal(
        f"{name!r} is not an analysis capability",
        "an analysis run selects from the footage-intelligence roster only",
        f"name one of: {', '.join(ROSTER)}")


def select(memory_only: bool = False, with_: Sequence[str] = (),
           skip: Sequence[str] = ()) -> tuple:
    """The capability ids a run executes, before composition."""
    chosen = list(MEMORY_ONLY if memory_only else ANALYSIS)
    for name in with_:
        cid = resolve_name(name)
        if cid not in chosen:
            chosen.append(cid)
    skipped = {resolve_name(name) for name in skip}
    return tuple(c for c in chosen if c not in skipped)


def compose(capability_ids: Sequence[str]) -> tuple:
    """Order the selection so every capability follows what it requires.

    Migrated analysis capabilities resolve from their capability-owned
    declarations (`capability_contracts.compose_ordered`) with no DAG
    import.  Anything unmigrated falls back to the derived requirement
    vocabulary (`CapabilitySpec.requires` / `.effects`). Ties keep the
    roster's order. A requirement no selected capability effects is left
    to the project's recorded state, and `Operation.execute` refuses
    naming it when that state does not hold it either.
    """
    from library.tools import capability_contracts
    try:
        rank = {cid: i for i, cid in enumerate(ROSTER)}
        return capability_contracts.compose_ordered(capability_ids, rank=rank)
    except KeyError:
        pass
    from library.tools import capabilities
    specs = {cid: capabilities.get(cid) for cid in capability_ids}
    rank = {cid: i for i, cid in enumerate(ROSTER)}
    after: dict[str, set] = {
        cid: {other for other, o in specs.items() if other != cid
              and set(o.effects) & set(spec.requires)}
        for cid, spec in specs.items()}
    ordered: list[str] = []
    while len(ordered) < len(specs):
        ready = sorted((cid for cid in specs if cid not in ordered
                        and after[cid] <= set(ordered)),
                       key=lambda c: rank.get(c, len(rank)))
        if not ready:
            raise RenRefusal(
                "the analysis selection has a requirement cycle",
                f"left unordered: {sorted(set(specs) - set(ordered))}",
                "report it: the derived requirements disagree with the "
                "roster")
        ordered.append(ready[0])
    return tuple(ordered)


# State services live behind one analysis boundary. The editing runner and
# this executor share the same serialized project state, but the executor
# does not import or drive the editing process.


# ── Execution ──────────────────────────────────────────────────────


def _execute_in_child(project: str, cid: str) -> dict:
    """`Operation.execute` in its own interpreter; the result as a record."""
    with tempfile.TemporaryDirectory(prefix="ren-analyze-") as scratch:
        out = Path(scratch) / "result.json"
        command = [sys.executable, "-m", "library.tools.footage_intelligence",
                   "--execute", cid, "--result", str(out), project]
        completed = subprocess.run(command, cwd=str(REPO_ROOT), check=False,
                                   stdout=sys.stderr)
        if out.is_file():
            return json.loads(out.read_text(encoding="utf-8"))
        return {"status": FAILED,
                "error": f"exited {completed.returncode} without a result"}


def execute_one(project: str, cid: str, result_path: str) -> int:
    """The child's half: execute one capability, write its result."""
    from library.tools import operations
    try:
        result = operations.get(cid).execute(project)
        record = {"status": result.status, "error": result.error,
                  "payload": result.payload}
    except Exception as exc:  # noqa: BLE001 - the parent records it
        import traceback
        record = {"status": FAILED,
                  "error": f"{type(exc).__name__}: {exc}\n"
                           f"{traceback.format_exc()}"}
    Path(result_path).write_text(json.dumps(record), encoding="utf-8")
    return 0


def run(project: str, capability_ids: Sequence[str]) -> dict:
    """Execute the composed selection; a capability already done is reused.

    Stops at the first failure: what follows requires it. Never raises
    for a step's failure - the record says what happened.
    """
    from library.tools import analysis_state, dag_adapter, operations, provenance
    started = time.perf_counter()
    order = compose(capability_ids)
    state = analysis_state.load_state(project)
    run_id = provenance.new_run_id()
    outcomes: list[dict] = []
    failed = False
    for cid in order:
        node = dag_adapter.node_of(operations.get(cid))
        row = {"capability": cid, "legacy_node": node}
        outcomes.append(row)
        if failed:
            row["status"] = NOT_RUN
            continue
        if analysis_state.completed(cid, state):
            row["status"] = REUSED
            print(f"[analyze] {cid}: reused", file=sys.stderr, flush=True)
            continue
        print(f"[analyze] {cid}: executing", file=sys.stderr, flush=True)
        t0 = time.perf_counter()
        from library.tools import project_model_store
        project_model_source = project_model_store.pipeline_projection(Path(project))
        with provenance.observing_operation(project, cid, run_id):
            result = _execute_in_child(project, cid)
        seconds = time.perf_counter() - t0
        row["seconds"] = round(seconds, 1)
        problem = result.get("error") if result["status"] != \
            operations.COMPLETED else None
        if problem is None:
            hollow = analysis_state.check_output_is_real(node, result["payload"])
            if hollow:
                problem = ("reported success but produced no usable "
                           "output:\n  - " + "\n  - ".join(hollow))
        if problem is not None:
            row["status"] = FAILED
            row["reason"] = problem
            analysis_state.record_failure(project, state, node, problem)
            print(f"[analyze] {cid}: FAILED - {problem}", file=sys.stderr,
                  flush=True)
            failed = True
            continue
        row["status"] = BUILT
        project_model_result = project_model_store.pipeline_projection(Path(project))
        analysis_state.record_completion(project, state, cid, node,
                                         result["payload"], seconds,
                                         project_model_source,
                                         project_model_result)
    return {
        "orchestrator": "footage_intelligence",
        "capabilities": outcomes,
        "status": "failed" if failed else "complete",
        "seconds": round(time.perf_counter() - started, 1),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.footage_intelligence",
        description="Execute the footage-analysis capabilities over a "
                    "project, composed by what each requires.")
    parser.add_argument("project")
    parser.add_argument("--memory-only", action="store_true")
    parser.add_argument("--with", dest="with_", action="append", default=[])
    parser.add_argument("--skip", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--execute", help=argparse.SUPPRESS)
    parser.add_argument("--result", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.execute:
        return execute_one(os.path.abspath(args.project), args.execute,
                           args.result)
    try:
        ids = select(args.memory_only, args.with_, args.skip)
        record = run(os.path.abspath(args.project), ids)
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        return REFUSAL_EXIT_CODE
    print(json.dumps(record, indent=2) if args.json else
          "\n".join(f"{r['capability']:20s} {r['status']}"
                    for r in record["capabilities"]))
    return 0 if record["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
