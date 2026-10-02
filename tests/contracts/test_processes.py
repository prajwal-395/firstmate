"""There is more than one process, and exactly one module knows.

`library/processes/reels/` is the second process beside `edit_video`
(`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md`). What makes two processes safe:
node ids unique across both, and an unknown node refused by name rather
than answered with an empty graph.
"""
from __future__ import annotations

import pytest

from library.tools import processes


# ── The property that makes merging safe ────────────────────────────


def test_node_ids_are_unique_across_every_process(monkeypatch):
    """Node ids key the two ledgers, the run status, the review gate, the
    marker routing, the step export and `step_outputs`; a derived
    requirement is NAMED after one. Two processes sharing an id would
    give two different steps one slot in all of them - and the check
    refuses that."""
    processes.assert_node_ids_are_unique()
    assert len(processes.node_owners()) == sum(
        len(processes.load_dag(pid)["nodes"])
        for pid in processes.process_ids())

    monkeypatch.setattr(processes, "load_dag", lambda pid: {
        "nodes": [{"id": "shared", "name": "x", "step_ref": "steps/x"}]
    })
    with pytest.raises(processes.ProcessError, match="unique"):
        processes.assert_node_ids_are_unique()


# ── The lookup an operation depends on ──────────────────────────────


def test_an_unknown_node_is_refused_by_name():
    """A silent None here would give `Operation.gather` an empty graph and
    hand the step an empty dict, which is the confidently wrong answer."""
    with pytest.raises(processes.ProcessError, match="no process declares"):
        processes.dag_declaring("a_node_that_does_not_exist")
    assert processes.process_of("a_node_that_does_not_exist") is None


