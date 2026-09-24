"""The few things about a component plan that are worth pinning.

Deliberately short. Nothing consumes this module yet, so a test per
refusal reason would be coverage of a shape that may still move; what IS
worth holding is the handful of properties the design would be pointless
without:

1. the engine states no magnitude - the captain's rule of 2026-09-08;
2. a missing look value REFUSES instead of defaulting, which is the
   mechanism that makes (1) hold;
3. a component that plans no acts is refused - the captain's 2026-09-09
   complaint, mechanised;
4. a component whose material nobody supplies is refused by name rather
   than drawn with a stand-in;
5. the acts land on the measured words, ahead of them, and the layers
   they build carry that timing.

The beat and its word windows are reel 40's own, copied verbatim from
its caption plan, so the timings asserted are measurements. Nothing here
reads a real project (AGENTS.md 8).
"""

from __future__ import annotations

import copy
import inspect
import re

import pytest

from library.tools import visual_component_plan as vcp
from library.tools.visual_component_plan import (
    COMPONENTS, ComponentPlanError, component_layers, resolve_component_plan)

BEAT = {
    "segment": 1,
    "shows": "five sources that cannot be reached",
    "anchor_start": 38.392,
    "anchor_end": 40.879,
    "event_start": 37.992,
}

WORDS = [[
    {"word": "and", "start": 38.051, "end": 38.191},
    {"word": "five", "start": 38.392, "end": 38.632},
    {"word": "of", "start": 38.672, "end": 38.753},
    {"word": "them", "start": 38.833, "end": 39.074},
    {"word": "just", "start": 39.214, "end": 39.435},
    {"word": "can't", "start": 39.756, "end": 39.956},
    {"word": "even", "start": 39.996, "end": 40.157},
    {"word": "be", "start": 40.197, "end": 40.277},
    {"word": "accessed,", "start": 40.438, "end": 40.879},
]]

LOOK = {
    "mark_colour": "#9e7d72", "failed_colour": "#302f2f",
    "mark_size": 0.13, "mark_spacing": 0.17, "row_y": 0.0,
    "land_ramp_seconds": 0.35, "land_rise": 0.10,
    "fail_ramp_seconds": 0.35, "fail_fall": 0.09, "failed_opacity": 0.35,
    "dim_ramp_seconds": 0.3, "dimmed_opacity": 0.3,
    "gather_ramp_seconds": 0.4, "gathered_spacing": 0.05,
    "withdraw_ramp_seconds": 0.3,
}

PLAN = [{
    "segment": 1,
    "shows": "five sources that cannot be reached",
    "component": "countable_set",
    "parts": 5,
    "acts": [
        {"do": "land", "parts": "all", "anchor_phrase": "five of them",
         "lead_seconds": 0.3, "through_phrase": "just"},
        {"do": "fail", "parts": "all", "anchor_phrase": "can't",
         "lead_seconds": 0.3, "through_phrase": "accessed"},
    ],
}]


def _resolve(plan, look=None):
    return resolve_component_plan(plan, [BEAT], WORDS, look or LOOK)


def _refusal(plan, look=None):
    kept, dropped = _resolve(plan, look)
    assert not kept and len(dropped) == 1, (kept, dropped)
    return dropped[0]




def test_a_look_that_states_no_value_refuses_rather_than_defaulting():
    """The refusing input: an otherwise perfect plan, and a look with
    `fail_fall` missing.

    The engine authors no distance for a mark to fall, so there is
    nothing to fall back to and the entry goes.
    """
    short = {k: v for k, v in LOOK.items() if k != "fail_fall"}
    drop = _refusal(PLAN, short)
    assert drop["reason"] == "look_states_no_value"
    assert "fail needs fail_fall" in drop["detail"]


def test_a_component_that_plans_no_action_is_refused():
    """The captain's 2026-09-09 complaint, mechanised: a thing that
    appears and then does nothing is a still, not a component."""
    plan = copy.deepcopy(PLAN)
    plan[0]["acts"] = []
    assert _refusal(plan)["reason"] == "no_action_planned"


def test_a_component_whose_material_nobody_supplies_is_refused_by_name():
    """The crux, made mechanical: nothing on this machine cuts a subject
    free of its own background, so `subject_on_surface` is reported
    rather than drawn with a stand-in."""
    plan = copy.deepcopy(PLAN)
    plan[0]["component"] = "subject_on_surface"
    drop = _refusal(plan)
    assert drop["reason"] == "material_not_supplied"
    assert "depictive_subject" in drop["detail"]


def test_the_real_beat_resolves_onto_its_words_and_builds_its_layers():
    """Every act leads the word it illustrates, and the marks land one at
    a time, reaching full opacity as the word finishes.

    ANIMATION_FIRST_REFERENCE §1: the picture arrives, the voice confirms
    it. That is the opposite of starting a graphic at a word's start
    frame.
    """
    kept, dropped = _resolve(PLAN)
    assert dropped == []
    (c,) = kept
    land, fail = c.acts

    assert land.start == pytest.approx(38.392 - 0.3, abs=1e-6)
    assert land.start < 38.392, "the picture did not lead its word"
    assert land.end == pytest.approx(39.435, abs=1e-6)   # end of "just"
    assert fail.start == pytest.approx(39.756 - 0.3, abs=1e-6)
    assert fail.end == pytest.approx(40.879, abs=1e-6)   # end of "accessed"
    assert c.start <= land.start and fail.end <= c.end

    layers = component_layers(c, LOOK)
    marks = [ly for ly in layers if not ly["id"].endswith("_failed")]
    assert len(marks) == 5 and len(layers) == 10
    assert {ly["kind"] for ly in layers} == {"disc"}

    # The stagger, and the ramp completing on the word rather than
    # starting there.
    first, last = marks[0]["opacity"], marks[-1]["opacity"]
    assert (min(k["at"] for k in first if k["value"] == 1.0)
            <= max(k["at"] for k in last if k["value"] == 0.0)), (
        "the set landed as a block, not one at a time")
    assert min(k["at"] for k in last if k["value"] == 1.0) == pytest.approx(
        land.end - c.start, abs=1e-3)

    # Rises in, then falls when it fails.
    y = marks[0]["y"]
    assert y[0]["value"] == pytest.approx(LOOK["row_y"] + LOOK["land_rise"])
    assert y[-1]["value"] == pytest.approx(LOOK["row_y"] + LOOK["fail_fall"])


def test_the_second_builder_draws_its_endpoints_and_its_travellers():
    """`flow_between` is claimed reachable, so a builder backs the claim."""
    look = dict(LOOK, source_x=-0.28, source_y=-0.18, sink_x=0.28,
                sink_y=0.18, endpoint_size=0.18, open_ramp_seconds=0.3,
                travel_ramp_seconds=0.4, arrive_ramp_seconds=0.3,
                arrived_colour=LOOK["failed_colour"])
    plan = [dict(PLAN[0], component="flow_between", parts=5, acts=[
        {"do": "open", "parts": [1, 2], "anchor_phrase": "five",
         "lead_seconds": 0.3, "through_phrase": "of"},
        {"do": "travel", "parts": [3, 4, 5], "anchor_phrase": "them",
         "lead_seconds": 0.2, "through_phrase": "can't"},
        {"do": "arrive", "parts": [2], "anchor_phrase": "even",
         "lead_seconds": 0.2, "through_phrase": "accessed"},
    ])]
    kept, dropped = _resolve(plan, look)
    assert dropped == []
    layers = component_layers(kept[0], look)
    assert [ly["id"] for ly in layers] == [
        "flow_end_1", "flow_end_2", "flow_end_2_arrived",
        "flow_mark_3", "flow_mark_4", "flow_mark_5"]
    mover = layers[3]
    assert mover["x"][0]["value"] == pytest.approx(look["source_x"])
    assert mover["x"][-1]["value"] == pytest.approx(look["sink_x"])


def test_a_component_with_no_builder_says_so_rather_than_drawing_nothing():
    (c,) = _resolve(PLAN)[0]
    held = vcp.ResolvedComponent(
        segment=c.segment, shows=c.shows, component="held_card", parts=1,
        start=c.start, end=c.end, acts=c.acts)
    with pytest.raises(ComponentPlanError, match="no builder for held_card"):
        component_layers(held, LOOK)
