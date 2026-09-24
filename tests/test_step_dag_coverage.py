"""Every step directory must have a DAG edge or an explicit unwired declaration.

The pipeline once had two steps (1.06, 1.07) that were implemented and never
wired into the DAG - the commits that added them never touched dag.json.
Nobody noticed until an output audit found their empty artifact slots.

This test is the part that stops it recurring.  It checks:

1.  Every directory under ``library/steps/step_*`` is registered in the
    ``STEPS`` table in ``project_layout.py``.  (Already covered by
    ``test_project_layout.py::test_every_step_directory_is_in_the_table``.)

2.  Every entry in the STEPS table either has a matching DAG node
    (``wired=True``) or carries a non-empty ``unwired_reason`` explaining
    why it is not wired.  Adding ``wired=False`` without a reason is a
    ``ValueError`` at import time (``StepDir.__post_init__``).

3.  The filesystem, the STEPS table, and the DAGs are consistent: no step
    directory can slip through without an explicit decision recorded in code.

EVERY PROCESS, not one.  ``library/steps/`` is one tree and belongs to the
repository rather than to a process, so "wired" means *some* process
declares a node for it.  Judging the tree against ``edit_video``'s graph
alone would report ``build_reels`` and ``verify_reels`` - both real nodes
of ``library/processes/reels`` - as orphans, which is the same class of
confidently-wrong answer this file exists to prevent, only inverted.
``library/tools/processes.py`` owns the enumeration and refuses two
processes that share a node id.

The test also reports what it finds, so the done-check can paste real output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
STEPS_DIR = REPO_ROOT / "library" / "steps"
PROCESSES_DIR = REPO_ROOT / "library" / "processes"


def _load_dag_node_step_refs() -> dict[str, str]:
    """Return ``{node_id: step_ref_dirname}`` across EVERY process."""
    out = {}
    for dag_path in sorted(PROCESSES_DIR.glob("*/dag.json")):
        dag = json.loads(dag_path.read_text(encoding="utf-8"))
        for node in dag["nodes"]:
            ref = node["step_ref"]
            # step_ref is "steps/step_1_01_scan_project"; strip the prefix
            dirname = ref.split("/", 1)[-1] if "/" in ref else ref
            out[node["id"]] = dirname
    return out


def _step_dirs_on_disk() -> set[str]:
    """Return the set of ``step_*`` directory names under ``library/steps/``."""
    return {
        p.name
        for p in STEPS_DIR.iterdir()
        if p.is_dir() and p.name.startswith("step_")
    }


# ── The check ───────────────────────────────────────────────────────


def test_every_step_directory_has_a_dag_edge_or_is_declared_unwired():
    """Fail if a step directory exists with no DAG node and no
    ``wired=False`` declaration carrying a reason.

    This is the structural guard against the 1.06/1.07 class of bug:
    a step directory is added but never wired, and nobody notices.
    """
    from library.tools.project_layout import STEPS

    dag_refs = _load_dag_node_step_refs()
    dag_dirnames = set(dag_refs.values())
    on_disk = _step_dirs_on_disk()
    table = {f"step_{s.dirname}": s for s in STEPS}

    orphans: list[str] = []
    unwired_without_reason: list[str] = []
    undeclared: list[str] = []

    for dirname in sorted(on_disk):
        step = table.get(dirname)

        if step is None:
            # Directory exists on disk but is not in the STEPS table at all
            undeclared.append(dirname)
            continue

        full_dirname = f"step_{step.dirname}"
        in_dag = full_dirname in dag_dirnames

        if step.wired and not in_dag:
            # Declared wired but DAG has no node pointing to it
            orphans.append(dirname)
        elif not step.wired:
            # Unwired step - must have a reason (enforced by __post_init__
            # but check here too for completeness)
            if not step.unwired_reason:
                unwired_without_reason.append(dirname)

    # Report - this output is pasted into the done-check
    from library.tools import processes

    print("\n--- Step directory vs DAG edge check ---")
    print(f"Processes:                 "
          f"{', '.join(processes.process_ids())}")
    print(f"Step directories on disk:  {len(on_disk)}")
    print(f"DAG nodes (all processes): {len(dag_refs)}")
    print(f"STEPS table entries:       {len(table)}")

    wired_steps = [s for s in STEPS if s.wired]
    unwired_steps = [s for s in STEPS if not s.wired]
    print(f"Wired steps:               {len(wired_steps)}")
    print(f"Unwired steps:             {len(unwired_steps)}")

    for s in unwired_steps:
        print(f"  - {s.node_id} ({s.dirname}): {s.unwired_reason}")

    if orphans:
        print(f"\nORPHANS (wired=True but no DAG node): {orphans}")
    if unwired_without_reason:
        print(f"\nUNWIRED WITHOUT REASON: {unwired_without_reason}")
    if undeclared:
        print(f"\nUNDECLARED (on disk but not in STEPS table): {undeclared}")

    if not orphans and not unwired_without_reason and not undeclared:
        print("\nAll step directories are accounted for. PASS")

    assert not undeclared, (
        f"Step directories on disk but not in the STEPS table: {undeclared}. "
        f"Add each to project_layout.STEPS - as wired=True with a DAG node, "
        f"or as wired=False with an unwired_reason."
    )
    assert not orphans, (
        f"Step directories declared wired=True but with no DAG node in ANY "
        f"process: {orphans}. Either add a node to some process's dag.json "
        f"or set wired=False with an unwired_reason in project_layout.STEPS."
    )
    assert not unwired_without_reason, (
        f"Unwired steps without a reason: {unwired_without_reason}. "
        f"Every wired=False entry needs an unwired_reason."
    )


def test_unwired_step_reason_is_not_placeholder():
    """Guard against someone filling ``unwired_reason`` with 'TODO' or
    empty-looking strings to satisfy ``__post_init__``."""
    from library.tools.project_layout import STEPS

    for s in STEPS:
        if not s.wired:
            assert len(s.unwired_reason.strip()) > 20, (
                f"{s.node_id} has a suspiciously short unwired_reason: "
                f"{s.unwired_reason!r}. Explain *why* the step is not wired."
            )


def test_wired_false_requires_reason_at_import_time():
    """StepDir.__post_init__ rejects wired=False without a reason."""
    from library.tools.project_layout import StepDir

    with pytest.raises(ValueError, match="unwired_reason"):
        StepDir("fake_step", "99_99_fake", wired=False)
