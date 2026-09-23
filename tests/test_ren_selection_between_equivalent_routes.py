"""Selection between equivalent routes: the post-composition selector.

The captain's complaint that started Ren: "doing some minor edits
should not force a rebuild". Items 1-3 and both gap-closers landed
the machinery and the edit STILL routed to a rebuild, because
`composer.representative` picks among `build_reels` siblings by a
static tie-break (PROJECT scope, non-`post_bridge` body, registry
order) that always lands on `reel.build`. `_closure` plans over
NODES, so the search cannot even see that four routes exist.

This file pins the last piece of item 4
(`vep-ren-selection-between-equivalent-routes`), built to the design
in `data/vep-ren-two-routes-one-goal-no-basis-to-choose/report.md`
sections 4-6:

* `_closure` unchanged, `compose(goal)` unchanged and still purely
  the representative - the context-free default stays the rebuild.
* `compose_with_change(goal, change_spec, tracks)` selects among the
  siblings using runtime context: the structured change plus the
  `reel_touchup.qualify` verdict over a live track read.
* The plan record narrates the choice (report 7.2/7.3): which route
  won and why, what the other route would have done differently,
  and rebuild viability when the rebuild is the fallback.

What is FORBIDDEN here, and pinned as such: bending one operation's
declared effect to steer the choice. All four siblings genuinely
produce the same state key and must keep declaring the same thing -
`test_siblings_keep_declaring_the_same_effect` refuses the trap the
whole rebuild rests on. No registry, requirement, or step-body
change backs this file; if one becomes necessary, the design is
wrong, not the test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composer as C  # noqa: E402
from library.tools import operations as O  # noqa: E402
from library.tools import reel_read  # noqa: E402
from library.tools import reel_touchup as tu  # noqa: E402
from tests.composed_edit_harness import build_reel  # noqa: E402

#: The goal every `build_reels` sibling's derived effect names.
REEL_GOAL = "state.verify_reels.reel_build"

#: The four change-serving routes to it: the full rebuild plus the
#: three caller-supplied touchup-family operations. `reel.ask` shares
#: the derived effect but takes no change spec, so it is out of the
#: candidate set by its own contract.
CHANGE_ROUTES = ("reel.build", "reel.touchup", "reel.entry_motion",
                 "reel.set_properties")


def _tracks(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    return reel_read.read_tracks(timeline)


def _swap_spec():
    """The measured class: an ending swap, `swap_pixels` on V4."""
    return {"reel": 1, "edits": [
        {"op": "swap_pixels", "row": "V4", "item": 0,
         "media": "/lab/new_card.mov"}]}


# ── The default must not move ──────────────────────────────────────


def test_context_free_compose_still_resolves_to_the_rebuild():
    """`compose` is untouched: the reel goal plans the rebuild."""
    comp = C.compose(REEL_GOAL)
    assert comp.completed
    assert comp.operations == ("reel.build",)
    assert comp.selection == ()


def test_compose_with_change_and_no_change_plans_what_compose_plans():
    """The cheap route is chosen only when a change is supplied.

    This is the evidence selection was ADDED rather than the default
    swapped: with no change spec the new entry point resolves to the
    rebuild, operation for operation.
    """
    assert C.representative("build_reels") == "reel.build"
    comp = C.compose_with_change(REEL_GOAL)
    assert comp.completed
    assert comp.operations == C.compose(REEL_GOAL).operations == (
        "reel.build",)
    (selection,) = comp.selection
    assert selection.decided_by == C.REPRESENTATIVE_FALLBACK
    assert selection.operation == "reel.build"


def test_free_text_goal_still_refuses_by_name():
    """Free text never reaches the selector: the goal vocabulary is
    unchanged, so the ending swap phrased plainly refuses - with no
    selection attached, because there is no route to choose."""
    comp = C.compose_with_change("reel 26 ending swap", _swap_spec(),
                                 tracks=[])
    assert comp.refused
    assert comp.unknown_goal
    assert comp.selection == ()


# ── The gate decides ───────────────────────────────────────────────


def test_gate_composed_routes_to_touchup_with_the_basis_cited(tmp_path):
    """The ending-swap class takes the cheap route, narrated."""
    comp = C.compose_with_change(REEL_GOAL, _swap_spec(),
                                 _tracks(tmp_path))
    assert comp.completed
    assert comp.operations == ("reel.touchup",)
    (selection,) = comp.selection
    assert selection.node == "build_reels"
    assert selection.decided_by == C.SELECTOR
    assert selection.gate_class == tu.COMPOSED
    assert selection.measured_basis_cited is True
    # The narration: which route and why, the measured basis, and
    # what the other route would have done differently.
    assert "reel.touchup" in selection.reason
    assert "88x" in selection.reason
    assert "Reel 26" in selection.reason
    assert "re-derives" in selection.reason
    assert set(selection.alternatives) == set(CHANGE_ROUTES) - {
        "reel.touchup"}


def test_gate_refusal_routes_to_rebuild_with_the_caveat_surfaced(
        tmp_path):
    """A refused touchup falls back to the rebuild - and the plan
    says the fallback is not an always-available slow path."""
    spec = {"reel": 1, "edits": [{"op": "dissolve"}]}
    comp = C.compose_with_change(REEL_GOAL, spec, _tracks(tmp_path))
    assert comp.completed
    assert comp.operations == ("reel.build",)
    (selection,) = comp.selection
    assert selection.decided_by == C.SELECTOR
    assert selection.gate_class == "refused"
    assert "refused" in selection.reason
    assert "dissolve" in selection.reason
    # Report 7.3: on Reel 26 the rebuild refuses today, so routing
    # here risks a ~208s refusal, not a success - surfaced now,
    # not 208 seconds later.
    assert "208" in selection.reason
    assert "not an always-available" in selection.reason


def test_length_changing_spec_stays_composed_with_the_receipt(tmp_path):
    """`composed_with_rederivation` still takes the composed path -
    the gate never falls back to a rebuild - with its own cost
    statement in the record rather than the 88x basis, which was
    measured on a class with no length change."""
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V4", "item": 0, "duration": 42}]}
    comp = C.compose_with_change(REEL_GOAL, spec, _tracks(tmp_path))
    assert comp.completed
    assert comp.operations == ("reel.touchup",)
    (selection,) = comp.selection
    assert selection.gate_class == tu.COMPOSED_WITH_REDERIVATION
    assert selection.measured_basis_cited is False
    assert "88x" not in selection.reason


# ── Four routes, not two ───────────────────────────────────────────


@pytest.mark.parametrize("edits,expected", [
    ([{"op": "set_properties", "row": "V4", "item": 0,
       "properties": {"ZoomX": 1.5}}], "reel.set_properties"),
    ([{"op": "entry_motion", "row": "V4", "item": 0,
       "fade_in_frames": 6}], "reel.entry_motion"),
    ([{"op": "swap_pixels", "row": "V4", "item": 0,
       "media": "/lab/new_card.mov"}], "reel.touchup"),
    ([{"op": "set_properties", "row": "V4", "item": 0,
       "properties": {"ZoomX": 1.5}},
      {"op": "entry_motion", "row": "V4", "item": 2,
       "fade_in_frames": 6}], "reel.touchup"),
])
def test_single_kind_specs_route_to_the_narrowest_operation(
        tmp_path, edits, expected):
    """One-kind in-place specs take the operation that owns the kind;
    anything mixed (or composed at all) rides the touchup, which runs
    mixed kinds together. All four are chosen, never inherited."""
    comp = C.compose_with_change(REEL_GOAL, {"reel": 1, "edits": edits},
                                 _tracks(tmp_path))
    assert comp.completed
    assert comp.operations == (expected,)
    (selection,) = comp.selection
    assert selection.decided_by == C.SELECTOR
    assert selection.gate_class == tu.COMPOSED


def test_unmeasured_classes_borrow_no_measured_basis(tmp_path):
    """Risk 5: the 2.37s-vs-208s basis was taken on an ending swap.
    It testifies about that class and no other - property setting
    and entry motion route cheap without citing it."""
    for edits in (
        [{"op": "set_properties", "row": "V4", "item": 0,
          "properties": {"ZoomX": 1.5}}],
        [{"op": "entry_motion", "row": "V4", "item": 0,
          "fade_in_frames": 6}],
    ):
        comp = C.compose_with_change(
            REEL_GOAL, {"reel": 1, "edits": edits}, _tracks(tmp_path))
        (selection,) = comp.selection
        assert selection.measured_basis_cited is False
        # The figure may be NAMED only to decline it: the record
        # says what it was measured on and that it testifies about
        # nothing else - never as justification for this class.
        assert "testifies about nothing else" in selection.reason


def test_spec_without_tracks_stands_on_the_representative(tmp_path):
    """A change with no track read cannot be qualified: the gate
    reads tracks, not prose, so the static tie-break stands - and
    says so, rather than guessing the cheap route."""
    comp = C.compose_with_change(REEL_GOAL, _swap_spec())
    assert comp.completed
    assert comp.operations == ("reel.build",)
    (selection,) = comp.selection
    assert selection.decided_by == C.REPRESENTATIVE_FALLBACK


def test_free_text_change_spec_raises_rather_than_routing(tmp_path):
    """Garbage in the change slot is a caller error, not a slow
    rebuild: prose must never route to a 208s run in silence."""
    with pytest.raises(C.ComposerError):
        C.compose_with_change(REEL_GOAL, "swap the ending",
                              _tracks(tmp_path))


# ── The forbidden bend, pinned from the selector side ──────────────


def test_siblings_keep_declaring_the_same_effect():
    """All four change-serving siblings declare the identical effect
    and identical requires. Bending one declaration to steer the
    choice would corrupt the layer everything else depends on - the
    selector exists precisely so no declaration has to bend."""
    build = O.get("reel.build")
    for name in CHANGE_ROUTES[1:]:
        op = O.get(name)
        assert op.owning_node == "build_reels"
        assert ([r.name for r in op.effect]
                == [r.name for r in build.effect] == [REEL_GOAL])
        assert ([r.name for r in op.requires]
                == [r.name for r in build.requires])


# ── Risk 4: no route arrives unchosen-by-design ─────────────────────


def _routable_multi_operation_nodes():
    """Nodes owning >1 operation with a non-empty derived effect.

    Empty-effect operations are composer-blind by design (THE FIVE:
    disk artefacts, analyses the composer can never name), so no route
    choice exists there. Everything else the composer can name must
    have an explicit selector entry. (`verify_reels` outgrew the map
    the hour the verdict kind gave it a derived effect: its siblings
    share one effect by node granularity, and `composer._SELECTORS`
    records that only the verdict route serves a change.)"""
    from collections import defaultdict

    groups: dict = defaultdict(set)
    for op in O.all():
        effect = tuple(r.name for r in op.effect)
        if effect:
            groups[op.owning_node].add(effect)
    return sorted(
        node for node, effects in groups.items()
        if sum(1 for op in O.by_node(node)
               if tuple(r.name for r in op.effect) in effects) > 1)


def test_every_multi_operation_node_has_selector_coverage():
    """A future fifth route must arrive chosen-by-design: any node
    that outgrows `selector_coverage` fails here until it gains an
    explicit selector entry."""
    nodes = _routable_multi_operation_nodes()
    assert "build_reels" in nodes  # the case this file exists for
    assert set(nodes) <= set(C.selector_coverage()), (
        f"nodes without selector coverage: "
        f"{sorted(set(nodes) - set(C.selector_coverage()))}")
    for node in nodes:
        selection = C.select_operation(node)
        assert selection.node == node
        assert selection.operation == C.representative(node)


def test_an_uncovered_multi_operation_node_refuses(tmp_path):
    """The runtime half of the guard: if a node outgrows the map
    without updating it, selection refuses rather than letting the
    new route inherit the tie-break in silence."""
    floating = C.select_operation("build_reels", _swap_spec(),
                                  _tracks(tmp_path))
    assert floating.decided_by == C.SELECTOR
    original = dict(C._SELECTORS)
    try:
        del C._SELECTORS["build_reels"]
        with pytest.raises(C.ComposerError, match="no selector covers"):
            C.select_operation("build_reels", _swap_spec(),
                               _tracks(tmp_path))
    finally:
        C._SELECTORS.clear()
        C._SELECTORS.update(original)


# ── The plan record narrates the choice ────────────────────────────


def test_plan_record_names_the_route_and_why(tmp_path):
    """`describe_plan` carries the selection; `as_record` too. The
    operator learns which route won, why, and what the other route
    would have done - not just the operation name."""
    comp = C.compose_with_change(REEL_GOAL, _swap_spec(),
                                 _tracks(tmp_path))
    text = C.describe_plan(comp)
    assert "route for build_reels: reel.touchup (selector)" in text
    assert "gate composed" in text
    record = comp.selection[0].as_record()
    assert record["operation"] == "reel.touchup"
    assert record["measured_basis_cited"] is True
    assert C.compose(REEL_GOAL).as_record().get("selection") is None


def test_other_nodes_stand_on_the_representative_explicitly():
    """Nodes with siblings but no change gate do not pretend to
    select: the fallback entry names the tie-break and what would
    change it (a caller-supplied sibling with a real selector)."""
    comp = C.compose_with_change("state.judge_reels.reel_selection",
                                 _swap_spec(), tracks=[])
    assert comp.completed
    assert comp.operations == ("reel.candidates",)
    (selection,) = comp.selection
    assert selection.node == "select_reels"
    assert selection.decided_by == C.REPRESENTATIVE_FALLBACK
    assert "no change gate" in selection.reason
