"""There is more than one process, and exactly one module knows.

Why this file exists
--------------------
`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` measured that a reel build has no
honest home inside `edit_video`'s DAG, and named the structure that does:
a second process beside it.  The captain authorised one, and
`library/processes/reels/` is it.

The risk in adding a second process is not that it fails to work.  It is
that the FIRST one was hardcoded in four places that read like general
code - `requirements.all_requirements`, `operations.Operation.gather`,
`test_step_dag_coverage`, `run_traceback.unwired_step_ids` - so a node of
the new process would silently derive nothing, gather nothing, and be
reported as unwired.  Every one of those is a confidently wrong answer
rather than a crash, which is the class this repository keeps paying for.

So this file asserts the properties that make two processes safe, and it
asserts them mechanically rather than by reading the four call sites.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import processes

REPO = Path(__file__).resolve().parents[1]


# ── The registry sees what is on disk ───────────────────────────────


def test_a_process_is_a_directory_never_a_list():
    """`process_ids` reads the filesystem.

    A hand-written enumeration would be a second place to update, and
    the failure mode - a process nobody registered - is the 1.06/1.07
    defect `tests/test_step_dag_coverage.py` exists to stop, one level
    up.
    """
    on_disk = {path.parent.name
               for path in (REPO / "library" / "processes").glob("*/dag.json")}
    assert set(processes.process_ids()) == on_disk
    assert processes.EDIT_VIDEO in on_disk
    assert processes.REELS in on_disk


def test_every_process_declares_a_manifest_beside_its_dag():
    """A graph with no manifest is a process nothing can describe."""
    for pid in processes.process_ids():
        manifest = REPO / "library" / "processes" / pid / "manifest.json"
        assert manifest.is_file(), f"{pid} has a dag.json and no manifest.json"
        declared = json.loads(manifest.read_text(encoding="utf-8"))
        assert declared["id"] == pid, (
            f"{pid}/manifest.json calls itself {declared['id']!r}")
        assert declared["level"] == "process"


def test_every_node_names_a_step_directory_that_exists():
    """A `step_ref` pointing nowhere is a node that cannot run."""
    for node_id, dirname in processes.step_dirnames().items():
        assert (REPO / "library" / "steps" / dirname).is_dir(), (
            f"node {node_id!r} names {dirname!r}, which is not a step "
            f"directory")


# ── The property that makes merging safe ────────────────────────────


def test_node_ids_are_unique_across_every_process():
    """Node ids key the two ledgers, the run status, the review gate, the
    marker routing, the step export and `step_outputs`; a derived
    requirement is NAMED after one. Two processes sharing an id would
    give two different steps one slot in all of them."""
    processes.assert_node_ids_are_unique()
    assert len(processes.node_owners()) == sum(
        len(processes.load_dag(pid)["nodes"])
        for pid in processes.process_ids())


def test_the_uniqueness_check_can_refuse(monkeypatch):
    """AGENTS.md 10.4: a gate that cannot fail reads as coverage."""
    monkeypatch.setattr(processes, "load_dag", lambda pid: {
        "nodes": [{"id": "collided", "name": "x", "step_ref": "steps/x"}]})
    with pytest.raises(processes.ProcessError, match="unique"):
        processes.assert_node_ids_are_unique()


def test_merged_dag_is_every_node_and_every_edge():
    merged = processes.merged_dag()
    assert len(merged["nodes"]) == len(processes.node_owners())
    edges = sum(len(processes.load_dag(pid).get("edges", []))
                for pid in processes.process_ids())
    assert len(merged["edges"]) == edges
    # NOT runnable, and it says so by carrying no entry or exit nodes.
    assert "entry_nodes" not in merged and "exit_nodes" not in merged


# ── The lookup an operation depends on ──────────────────────────────


def test_a_node_resolves_to_the_graph_that_declares_it():
    edit = processes.dag_declaring("plan_subtitles")
    reels = processes.dag_declaring("build_reels")
    assert edit["id"] == processes.EDIT_VIDEO
    assert reels["id"] == processes.REELS
    assert {n["id"] for n in reels["nodes"]} == {"build_reels",
                                                 "verify_reels"}


def test_an_unknown_node_is_refused_by_name():
    """A silent None here would give `Operation.gather` an empty graph and
    hand the step an empty dict, which is the confidently wrong answer."""
    with pytest.raises(processes.ProcessError, match="no process declares"):
        processes.dag_declaring("a_node_that_does_not_exist")
    assert processes.process_of("a_node_that_does_not_exist") is None


def test_execution_order_comes_off_the_graph():
    """`run_scope.topological_order` is the runner's own Kahn walk, called
    rather than copied - so a process's order and a run's order cannot
    disagree."""
    order = processes.execution_order(processes.REELS)
    assert order == ["build_reels", "verify_reels"]

    edit = processes.execution_order(processes.EDIT_VIDEO)
    assert edit[0] == "validate_sfx_library" and edit[-1] == "validate"


# ── The reel process, described ─────────────────────────────────────


def test_the_reel_process_reuses_steps_rather_than_copying_them():
    """The whole point of a second process, and the thing that would be
    dishonest to get wrong.

    The captain's words were *"the functionality of all the steps should
    be reconfigurable and customizable such that we can properly mix and
    match the process to what we need"*. Reels reach 4.01 and 4.05
    through `reel_build.reel_subtitle_segments`, which drives the
    operation registry - so there is one caption implementation, reached
    from two processes. A copy under `library/steps/step_7_*` would be
    the failure this asserts against.
    """
    import ast

    source = (REPO / "library" / "tools" / "reel_build.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    driven = {ast.literal_eval(node.args[0])
              for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "get"
              and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "operations"
              and node.args
              and isinstance(node.args[0], ast.Constant)}
    assert {"subtitles.plan", "subtitles.render_segment"} <= driven, (
        "the reel caption path no longer drives steps 4.01 and 4.05 "
        "through the registry, so there is a second caption path")

    for step_dir in ("step_7_01_build_reels", "step_7_02_verify_reels"):
        step = ast.parse((REPO / "library" / "steps" / step_dir / "step.py")
                         .read_text(encoding="utf-8"))
        # NAMES, not text: both bodies say in prose where captions come
        # from, and a substring check would read the explanation as the
        # thing it explains.
        named = set()
        for node in ast.walk(step):
            if isinstance(node, ast.Name):
                named.add(node.id)
            elif isinstance(node, ast.Attribute):
                named.add(node.attr)
            elif isinstance(node, ast.ImportFrom) and node.module:
                named.add(node.module)
                named.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                named.update(a.name for a in node.names)
        captioning = sorted(n for n in named if "subtitle" in n.lower()
                            or "caption" in n.lower())
        assert captioning == [], (
            f"{step_dir} names {captioning}; captions belong to 4.01 and "
            f"4.05, reached through the registry by "
            f"reel_build.reel_subtitle_segments, and a reel-shaped second "
            f"caption path is the thing this process exists NOT to be")


def test_the_reel_process_has_no_runner_of_its_own():
    """One runner, and it belongs to edit_video.

    A second `run_pipeline.py` would be the largest second implementation
    this repository could grow. The reel process's two nodes are driven
    through the operation registry instead, which is what
    `manage_project.py build-reels` does.
    """
    reels = REPO / "library" / "processes" / processes.REELS
    assert sorted(p.name for p in reels.glob("*.py")) == [], (
        "the reel process grew Python of its own; its nodes are driven "
        "through library/tools/operations.py")
