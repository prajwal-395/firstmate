"""Behavioral capability resolution beyond the graph-wide contract audit.
"""
from __future__ import annotations
from dataclasses import replace
import pytest
from library.tools import dag_adapter, operations
from library.tools import capabilities
from library.tools import machine_needs as mn
from library.tools import processes


def test_a_capability_without_a_legacy_node_refuses_node_keyed_questions():
    """A missing node must not read as a capability with no requirements."""
    scan = operations.get("footage.scan")
    nodeless = replace(scan, owning_dir="step_9_99_nodeless")
    with pytest.raises(dag_adapter.NoLegacyNode, match="footage.scan"):
        nodeless.requires  # noqa: B018


def test_gathering_demands_only_what_a_capability_consumes(tmp_path):
    """Gathering and its refusal agree with the capability's declared reads."""
    for capability in (
        "reel.touchup",
        "transcript.splice",
        "subtitles.render_segment",
    ):
        assert operations.get(capability).consumes == ()
        operations.get(capability).gather(str(tmp_path))
    with pytest.raises(RuntimeError, match="audio_spine"):
        operations.get("subtitles.plan").gather(str(tmp_path))


# --------------------------------------------------------------------------
# From test_machine_needs.py
#
# The capability -> machine-needs table holds to the registry it mirrors.
#
# `machine_needs` is declared rather than derived (doctor cannot import
# the registry: it imports torch), so each of these names the drift that
# would make `ren doctor --for` lie.

def test_every_capability_has_a_needs_row_and_nothing_else_does():
    registry = set(capabilities.ids())
    table = set(mn.CAPABILITY_NEEDS)
    assert registry - table == set(), "capabilities doctor cannot answer for"
    assert table - registry == set(mn.FRONT_DOOR), "rows naming no capability"


def test_every_derived_environment_requirement_is_a_required_need():
    for spec in capabilities.all():
        required = mn.needs_of(spec.id).requires
        for env in spec.assumes_machine:
            assert env in mn.ENV_REQUIREMENT_NEED, (
                f"{spec.id}: {env} has no need in ENV_REQUIREMENT_NEED")
            assert mn.ENV_REQUIREMENT_NEED[env] in required, (
                f"{spec.id} refuses without {env}, but its needs row does "
                f"not require {mn.ENV_REQUIREMENT_NEED[env]}")


def test_a_capability_that_needs_a_model_answer_requires_a_harness():
    for spec in capabilities.all():
        if spec.needs_model_answer:
            assert "chat_harness" in mn.needs_of(spec.id).requires, spec.id


def test_the_table_uses_only_its_own_vocabulary():
    assert mn.problems() == []


def test_a_failing_optional_need_degrades_and_a_required_one_refuses():
    assert mn.availability("footage.search", {"model.search_embedding"})[0] \
        == mn.DEGRADED
    assert mn.availability("render.build", {"resolve.studio"}) \
        == (mn.UNAVAILABLE, ("resolve.studio",), {})
    assert mn.availability("project.inspect", {"resolve.studio"})[0] \
        == mn.AVAILABLE


# --------------------------------------------------------------------------
# From test_processes.py
#
# There is more than one process, and exactly one module knows.
#
# `library/processes/reels/` is the second process beside `edit_video`
# (`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md`). What makes two processes safe:
# node ids unique across both, and an unknown node refused by name rather
# than answered with an empty graph.

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
