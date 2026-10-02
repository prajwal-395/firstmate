"""There is more than one process, and this is the only module that knows.

Why this exists
---------------
`library/processes/edit_video` describes the production of ONE VIDEO FROM
FOOTAGE.  Reels are a SECOND product: they are derived from a master
timeline that already exists, their transcript is written by a CLI tool
outside the DAG, and their approval is the captain's act rather than any
step's output.  `docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` measured what
happens when that second product is forced into the first process's
graph - a contract that refuses for a reason that is not true and passes
on a project with no approved reel in it - and named the honest
structure: a second process beside the first.

The captain authorised it.  Their words, which this module is the
mechanical half of: *"the functionality of all the steps should be
reconfigurable and customizable such that we can properly mix and match
the process to what we need"*.

What a second process must NOT cost
-----------------------------------
A second copy of anything.  `library/steps/` is one tree and stays one
tree: a step directory belongs to the repository, not to a process, and
two processes may both reach it.  The reel process reaches 4.01 and 4.05
through `library/tools/operations.py` exactly as
`reel_build.reel_subtitle_segments` already does - the registry is the
reuse mechanism, and there is no second caption path.

So the change a second process really needs is small and is here: the
things that used to say "the DAG" have to say "the DAG that declares this
node".  Four callers were reading `library/processes/edit_video/dag.json`
as though it were the only one -

    requirements.all_requirements   derives every state_key requirement
    operations.Operation.gather     assembles a step's inputs
    operations.Operation.requires   via all_requirements
    capabilities.problems           every step dir is reached

- and each now asks this module instead.  A run still derives from the
dag it was HANDED (`run_pipeline` passes its own), which is what keeps a
reduced graph judged against itself.

Node ids are GLOBALLY unique
----------------------------
Across every process, not merely within one.  Sixteen of the runner's
eighteen per-step services are keyed by node id, a capability's node is
derived from its step directory, and a derived requirement is NAMED
`state.<consumer>.<key>` - so two processes sharing a node id would give
two different steps one ledger entry, one node view of their recorded
output and one requirement.  `assert_node_ids_are_unique` refuses that, and
`tests/test_processes.py` pins it can fail.

A process is a DIRECTORY, never a list
--------------------------------------
`process_ids()` scans `library/processes/*/dag.json`.  A hand-written
enumeration would be a second place to update, and the failure mode -
adding a process nobody registered - is exactly the 1.06/1.07 defect
`capabilities.unregistered_step_dirs` exists to stop.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

_LIBRARY_ROOT = Path(__file__).resolve().parents[1]
PROCESSES_ROOT = _LIBRARY_ROOT / "processes"

EDIT_VIDEO = "edit_video"
"""One video from footage.  The original, and still the only one with a
runner: `library/processes/edit_video/run_pipeline.py`."""

REELS = "reels"
"""Short reels cut from a master timeline that already exists.  Its nodes
are driven through the operation registry - see `manage_project.py
build-reels` - because two of its five stages are outside any DAG by
construction (the transcript needs Resolve open and WhisperX loaded, and
the approval is the captain's)."""


class ProcessError(Exception):
    """The processes on disk do not describe a coherent tree."""


def process_ids() -> Tuple[str, ...]:
    """Every process directory that declares a `dag.json`, sorted.

    Read off disk rather than listed, so a process cannot exist and be
    invisible to the derivation that has to see it.
    """
    if not PROCESSES_ROOT.is_dir():
        return ()
    return tuple(sorted(
        path.name for path in PROCESSES_ROOT.iterdir()
        if path.is_dir() and (path / "dag.json").is_file()))


def dag_path(process_id: str) -> Path:
    return PROCESSES_ROOT / process_id / "dag.json"


def load_dag(process_id: str) -> dict:
    """One process's own graph."""
    path = dag_path(process_id)
    if not path.is_file():
        raise ProcessError(
            f"no such process {process_id!r}: {path} does not exist. "
            f"Known: {', '.join(process_ids()) or '(none)'}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifests(dag: Mapping) -> Dict[str, dict]:
    """`{node_id: manifest}` for one graph.

    Delegates to `run_scope.load_manifests`, which is what the runner
    reads: a second reader here would be a second answer to "what does
    this step declare".
    """
    from library.tools import run_scope
    return run_scope.load_manifests(dag)


def execution_order(process_id: str) -> List[str]:
    """The process's nodes in run order.

    `run_scope.topological_order` is the same Kahn walk the runner does,
    called rather than copied.
    """
    from library.tools import run_scope
    return run_scope.topological_order(load_dag(process_id))


# ── The whole tree, for anything that is not about one run ──────────

def every_dag() -> Dict[str, dict]:
    """`{process_id: dag}` for every process on disk."""
    return {pid: load_dag(pid) for pid in process_ids()}


def merged_dag() -> dict:
    """Every process's nodes and edges as ONE graph, for derivation only.

    NOT a runnable graph and never handed to a runner: it has no entry or
    exit nodes and its two halves are not connected, because they are not
    one pipeline.  What it is for is the questions that are about the
    REPOSITORY rather than about a run - which requirements exist, which
    step directories have a node - where asking each process separately
    and concatenating is the same answer with more places to forget one.

    Safe to merge only because node ids are globally unique, which
    `assert_node_ids_are_unique` enforces.
    """
    assert_node_ids_are_unique()
    nodes: List[dict] = []
    edges: List[dict] = []
    for pid in process_ids():
        dag = load_dag(pid)
        nodes.extend(dag.get("nodes", []))
        edges.extend(dag.get("edges", []))
    return {"id": "+".join(process_ids()), "nodes": nodes, "edges": edges}


def node_owners() -> Dict[str, str]:
    """`{node_id: process_id}` across the whole tree."""
    out: Dict[str, str] = {}
    for pid in process_ids():
        for node in load_dag(pid).get("nodes", []):
            out[node["id"]] = pid
    return out


def process_of(node_id: str) -> Optional[str]:
    """Which process declares this node, or None.

    None rather than a raise: several callers hold an identifier that
    names no node at all (`project_layout.node_id_for` passes those
    through unchanged), and inventing an answer for one would be worse
    than saying nothing.
    """
    return node_owners().get(node_id)


def dag_declaring(node_id: str) -> dict:
    """The graph a node belongs to.

    This is the lookup that makes a step reachable from two processes: an
    operation names its owning node, and the node names its graph, so
    nothing has to carry a process around.
    """
    owner = process_of(node_id)
    if owner is None:
        raise ProcessError(
            f"no process declares a node {node_id!r}. Known nodes: "
            f"{', '.join(sorted(node_owners())) or '(none)'}")
    return load_dag(owner)


def assert_node_ids_are_unique() -> None:
    """Two processes may not name one node, and this is why.

    A node id is the key of the two ledgers, the run status, the review
    gate, the marker routing, the step export and the node view of
    `capability_outputs` in `pipeline_data.json`, and it is what a
    derived requirement is NAMED after (`state.<consumer>.<key>`).  Sharing one would give two
    different steps a single slot in all of them, and the symptom would
    be a step reading another process's output as its own.
    """
    seen: Dict[str, str] = {}
    clashes: List[str] = []
    for pid in process_ids():
        for node in load_dag(pid).get("nodes", []):
            node_id = node["id"]
            if node_id in seen:
                clashes.append(
                    f"{node_id!r} is declared by both {seen[node_id]!r} "
                    f"and {pid!r}")
            else:
                seen[node_id] = pid
    if clashes:
        raise ProcessError(
            "node ids must be unique across every process - they key the "
            "ledgers, the run status and the node views of recorded "
            "output:\n  "
            + "\n  ".join(clashes))


def step_dirnames() -> Dict[str, str]:
    """`{node_id: step directory name}` across every process.

    `step_ref` is `steps/step_4_01_plan_subtitles`; the directory name is
    what `project_layout.STEPS` and `library/steps/` are keyed by.
    """
    out: Dict[str, str] = {}
    for pid in process_ids():
        for node in load_dag(pid).get("nodes", []):
            ref = node["step_ref"]
            out[node["id"]] = ref.split("/", 1)[-1] if "/" in ref else ref
    return out


def describe() -> str:
    lines = []
    for pid in process_ids():
        dag = load_dag(pid)
        order = execution_order(pid)
        lines.append(f"{pid}  ({len(dag.get('nodes', []))} nodes)")
        for node_id in order:
            lines.append(f"    {node_id}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
