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
the step-coverage test, `run_traceback.unwired_step_ids` - so a node of
the new process would silently derive nothing, gather nothing, and be
reported as unwired.  Every one of those is a confidently wrong answer
rather than a crash, which is the class this repository keeps paying for.

So this file asserts the properties that make two processes safe, and it
asserts them mechanically rather than by reading the four call sites.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from library.tools import processes

REPO = Path(__file__).resolve().parents[1]


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


# ── The lookup an operation depends on ──────────────────────────────


def test_an_unknown_node_is_refused_by_name():
    """A silent None here would give `Operation.gather` an empty graph and
    hand the step an empty dict, which is the confidently wrong answer."""
    with pytest.raises(processes.ProcessError, match="no process declares"):
        processes.dag_declaring("a_node_that_does_not_exist")
    assert processes.process_of("a_node_that_does_not_exist") is None


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

