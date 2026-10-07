"""Distillation: planner routes become recipes, replay-gated.

Each test names the defect it catches: a route saved without anchors
(a recipe that can never replay), a route saved without passing
replay (trust without proof), a repair loop without a bound (an
agent polishing a guess forever).
"""

from __future__ import annotations

from library.tools.computer_use.distill import (
    repair,
    save_distilled,
    trace_to_script,
)
from library.tools.computer_use.scripts import RecipeStore


def _trace():
    return [
        {"action": "press", "outcome": "ok",
         "params": {"path": ["Color", "Nodes", "Add Serial Node"]},
         "path": ["Color", "Nodes", "Add Serial Node"]},
        {"action": "key", "outcome": "ok",
         "params": {"key": "return"}, "key": "return"},
        {"action": "click", "outcome": "ok",
         "params": {"x": 10.0, "y": 10.0},
         "ocr_text": "Master"},
        {"action": "drag", "outcome": "ok", "params": {}},
    ]


def test_a_trace_becomes_anchored_steps_with_expects():
    """Every anchorable action becomes a step with its anchor and an
    expect - the recipe the next run executes deterministically. The
    drag teaches nothing: an action with no deterministic anchor is
    dropped, never faked into a position."""
    script = trace_to_script("add a node", _trace())
    assert len(script.steps) == 3
    assert script.steps[0].anchors[0].via == "ax_path"
    assert script.steps[0].expect is not None
    assert script.steps[1].anchors[0].via == "key"
    assert script.steps[2].anchors[0].via == "ocr"


def test_an_empty_trace_distills_nothing():
    """A trace with no successful anchored action is not a route -
    distilling it raises instead of saving an empty recipe the
    replay would run as a vacuous pass."""
    try:
        trace_to_script("nothing", [{"action": "drag", "outcome": "ok",
                                     "params": {}}])
        assert False, "should have raised ValueError"
    except ValueError as exc:
        assert "distillable" in str(exc)


def test_a_bare_coordinate_distills_flagged_for_remapping():
    """A click the trace resolved to a bare point distills to a
    `why: llm` position - flagged by the anchor audit until a
    mapping run replaces it. The distillation does not invent a
    label the trace never saw."""
    script = trace_to_script("click", [
        {"action": "click", "outcome": "ok",
         "params": {"x": 10.0, "y": 10.0}, "point": [10.0, 10.0]}])
    assert script.steps[0].anchors[0].via == "position"
    assert script.steps[0].anchors[0].params["why"] == "llm"


def test_save_replays_before_trusting(tmp_path):
    """A distilled route that fails replay never lands in the store -
    the save is refused with the reason, and the name stays unknown.
    Trust without proof is the defect tonight's takes showed."""
    store = RecipeStore(tmp_path / "recipes")
    script = trace_to_script("add a node", _trace())
    # Break the proof: the menu route the fakes never press.
    script.steps[0].anchors[0].params["path"] = ["No", "Such", "Menu"]
    distilled = save_distilled(store, "unproven-route", script, None,
                               out_dir=tmp_path / "replays")
    assert not distilled.passed
    assert store.load("unproven-route") is None


def test_save_keeps_a_proven_route(tmp_path):
    """A distilled route that passes replay saves with zero successes
    - promotion still counts committed runs from there, it does not
    inherit the planner's one success."""
    store = RecipeStore(tmp_path / "recipes")
    script = trace_to_script("press return", [
        {"action": "key", "outcome": "ok",
         "params": {"key": "return"}, "key": "return"}])
    distilled = save_distilled(store, "return-key", script, "layout-a",
                               out_dir=tmp_path / "replays")
    assert distilled.passed, distilled.reason
    loaded, record = store.load("return-key")
    assert record["successes"] == 0
    assert record["layout"] == "layout-a"
    assert not store.is_promoted("return-key")


def test_repair_fixes_and_resaves(tmp_path):
    """A failed replay falls back to the agent, which repairs and
    re-saves: the agent sees the reason, returns a fixed script, and
    the green replay saves. The repair round is counted."""
    store = RecipeStore(tmp_path / "recipes")
    script = trace_to_script("press return", [
        {"action": "key", "outcome": "ok",
         "params": {"key": "return"}, "key": "return"}])
    script.steps[0].anchors = []
    first = save_distilled(store, "repaired", script, None,
                           out_dir=tmp_path / "replays")
    assert not first.passed

    def agent(failed_script, reason):
        assert "anchor" in reason or "expect" in reason or reason
        fixed = trace_to_script("press return", [
            {"action": "key", "outcome": "ok",
             "params": {"key": "return"}, "key": "return"}])
        return fixed

    fixed = repair(store, "repaired", first, None, agent,
                   out_dir=tmp_path / "replays")
    assert fixed.passed, fixed.reason
    assert fixed.repairs == 1
    assert store.load("repaired") is not None


def test_repair_gives_up_inside_the_bound(tmp_path):
    """An agent that cannot fix the route does not polish forever:
    past MAX_REPAIRS the route stays out of the store with the last
    reason - reported, never half-trusted."""
    from library.tools.computer_use import distill as distill_mod

    store = RecipeStore(tmp_path / "recipes")
    script = trace_to_script("press return", [
        {"action": "key", "outcome": "ok",
         "params": {"key": "return"}, "key": "return"}])
    script.steps[0].anchors = []
    first = save_distilled(store, "unfixable", script, None,
                           out_dir=tmp_path / "replays")
    calls: list = []

    def agent(failed_script, reason):
        calls.append(reason)
        return failed_script

    fixed = repair(store, "unfixable", first, None, agent,
                   out_dir=tmp_path / "replays")
    assert not fixed.passed
    assert len(calls) == distill_mod.MAX_REPAIRS
    assert store.load("unfixable") is None
