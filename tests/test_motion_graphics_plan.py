"""The motion-graphics layer is PLANNED, on its own timebase, in rows.

Three claims, and each of them was false before 2026-09-02.

1. **A model plans the layer, whether or not a brand template exists.**
   Step 4.06 used to resolve the whole thing from `effect.motion_accents`
   and `effect.motion_progress_bar`, so a project naming no template got
   an empty layer and nobody was ever asked. The captain: *"it does not
   matter, the LLM was still meant to plan these things ... the brand
   template is only a secondary"*.
2. **The layer carries its own timebase.** An element declares a start
   and a hold in TIMELINE seconds and is not tied to a spine block:
   *"the motion graphics can be seperate and on their own timescale if
   they need to be"*.
3. **Several graphics may be on screen at once, in rows:** *"they are
   allowed to use multiple rows in order to have various motion
   graphics"*.

And one that must stay true: **nothing here invents taste.** An entry
with no colour anywhere is dropped rather than drawn in a constant, and
every drop is named with a reason out of `DROP_REASONS`.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import motion_graphics_plan as mgp
from library.tools import motion_graphics_vocabulary as mgv

FPS = 30
DURATION = 12.0

SAFE_AREA = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def entry(**kw):
    base = {
        "element": "title_lockup",
        "start_seconds": 0.5,
        "duration_seconds": 2.0,
        "anchor": "top_left",
        "copy": {"display": "A NAME"},
        "color": "#F5F5F0",
    }
    base.update(kw)
    return base


def resolve(plan, palette=None):
    return mgp.resolve_plan(plan, timeline_duration=DURATION, fps=FPS,
                            palette_roles=palette or {})


# ── 1. no brand template, and the layer still draws ──────────────────

def test_a_plan_draws_with_no_brand_template_at_all():
    """The regression this whole change exists for.

    `palette_roles={}` is exactly what a project naming no brand
    template resolves to. The old code path made that an empty layer;
    here it is a layer that draws in the colours the plan itself stated.
    """
    resolved = resolve([entry()])
    assert resolved.basis == mgp.ELEMENTS_PLANNED
    assert not resolved.dropped
    assert resolved.moments[0]["color"] == "#F5F5F0"
    assert resolved.moments[0]["colorBasis"] == "stated by the plan"


def test_the_template_refines_a_colour_role_and_does_not_gate_the_layer():
    """A palette answers a `colour_role`; its absence drops nothing."""
    declared = entry(colour_role="accent", color=None)
    del declared["color"]
    with_template = resolve([declared], palette={"accent": "#FF0055"})
    assert with_template.moments[0]["color"] == "#FF0055"
    assert "brand palette" in with_template.moments[0]["colorBasis"]

    # The SAME entry with no template: dropped for want of a colour, and
    # dropped BY NAME - not drawn in a house colour, which is what
    # house_look.py was emptied to stop.
    without = resolve([declared])
    assert without.basis == mgp.EVERY_ENTRY_DROPPED
    assert without.dropped[0].reason == "no_colour_to_draw_it_in"


def test_no_element_is_ever_drawn_in_a_colour_nobody_chose():
    for plan in ([entry(color=None, colour_role=None)],
                 [{"element": "frame_accents", "start_seconds": 0.0,
                   "duration_seconds": 1.0, "anchor": "centre"}]):
        cleaned = [{k: v for k, v in e.items() if v is not None}
                   for e in plan]
        resolved = resolve(cleaned)
        assert not resolved.moments
        assert resolved.dropped[0].reason == "no_colour_to_draw_it_in"


# ── 2. its own timebase ──────────────────────────────────────────────

def test_a_span_is_read_in_timeline_seconds_and_no_block_is_consulted():
    """`resolve_plan` is not even given the spine - only its length.

    That is the structural statement of the timebase: there is no block
    list in scope, so no code path can quantise a start onto one.
    """
    resolved = resolve([entry(start_seconds=1.75, duration_seconds=6.4)])
    moment = resolved.moments[0]
    assert moment["timeline_start"] == 1.75
    assert moment["timeline_end"] == pytest.approx(8.15)
    assert moment["startFrame"] == round(1.75 * FPS)
    assert moment["durationFrames"] == round(6.4 * FPS)


def test_a_span_that_runs_past_the_end_is_bounded_and_its_start_is_not_moved():
    resolved = resolve([entry(start_seconds=10.0, duration_seconds=99.0)])
    moment = resolved.moments[0]
    assert moment["timeline_start"] == 10.0, "a start is never moved"
    assert moment["timeline_end"] == DURATION


def test_an_entry_with_no_timing_is_dropped_rather_than_given_a_block():
    for missing in ("start_seconds", "duration_seconds"):
        e = entry()
        del e[missing]
        resolved = resolve([e])
        assert resolved.dropped[0].reason == "no_timing_declared", missing


def test_a_span_outside_the_timeline_is_dropped_and_not_clamped():
    resolved = resolve([entry(start_seconds=DURATION + 1)])
    assert resolved.dropped[0].reason == "outside_the_timeline"


# ── 3. rows, and several graphics at once ────────────────────────────

def test_two_elements_at_one_moment_get_their_own_rows_in_one_segment():
    """Rows are an ON-SCREEN layout, composited into one overlay clip.

    A second Resolve video track would have had to sit above the
    generator lane (V5) and the timed-text lane (V6), silently changing
    what composites over what.
    """
    resolved = resolve([
        entry(row=0),
        entry(element="quote_card", row=1, copy={"display": "a line"},
              color="#FF8A3D"),
    ])
    assert len(resolved.moments) == 2

    segments = mgp.plan_segments(resolved.moments, fps=FPS, width=1080,
                                 height=1920, safe_area=SAFE_AREA)
    assert len(segments) == 1, "simultaneous elements share one segment"
    rows = [e["row"] for e in segments[0]["props"]["elements"]]
    assert sorted(rows) == [0, 1]
    assert segments[0]["element_count"] == 2


def test_overlapping_spans_are_composited_and_disjoint_ones_are_not():
    resolved = resolve([
        entry(start_seconds=0.0, duration_seconds=2.0),
        entry(start_seconds=1.0, duration_seconds=2.0, row=1),
        entry(start_seconds=9.0, duration_seconds=1.0),
    ])
    segments = mgp.plan_segments(resolved.moments, fps=FPS, width=1080,
                                 height=1920, safe_area=SAFE_AREA)
    assert len(segments) == 2
    assert segments[0]["element_count"] == 2
    assert segments[1]["element_count"] == 1
    # Which is what keeps manifest_validator's non-overlap rule true.
    assert segments[0]["timeline_end"] <= segments[1]["timeline_start"]


def test_a_local_start_frame_is_rebased_onto_its_own_segment():
    resolved = resolve([entry(start_seconds=4.0, duration_seconds=1.0)])
    segments = mgp.plan_segments(resolved.moments, fps=FPS, width=1080,
                                 height=1920, safe_area=SAFE_AREA)
    assert segments[0]["props"]["elements"][0]["startFrame"] == 0
    assert segments[0]["timeline_start"] == 4.0


# ── Refusals, all of them named ──────────────────────────────────────

def test_an_element_the_renderer_cannot_draw_is_dropped_by_name():
    """Never rendered as nothing, and never swapped for a neighbour."""
    # Whichever entry the roster currently records as undrawable. It was
    # `channel_bug` until its component was written; the search is over
    # the roster so this test follows the flag rather than pinning a
    # name that a repair makes stale.
    unreachable = next((e.key for e in mgv.ROSTER
                        if e.reachable != mgv.REACHABLE_NOW), None)
    if unreachable is None:
        pytest.skip("every roster entry is reachable; nothing to refuse. "
                    "Runs again the moment an entry is added ahead of its "
                    "component, which is what this test is for.")
    resolved = resolve([entry(element=unreachable)])
    assert resolved.dropped[0].reason == "renderer_cannot_draw_it_yet"
    assert resolved.dropped[0].detail


def test_an_element_owned_by_another_enumeration_says_which_one():
    resolved = resolve([entry(element="zoom_emphasis")])
    dropped = resolved.dropped[0]
    assert dropped.reason == "not_in_the_vocabulary"
    assert "plan_vfx" in dropped.detail


def test_an_element_that_needs_copy_and_has_none_is_dropped():
    e = entry()
    del e["copy"]
    assert resolve([e]).dropped[0].reason == "no_copy_for_an_element_that_needs_one"


def test_a_tracked_anchor_is_dropped_rather_than_pinned_to_a_point():
    resolved = resolve([entry(anchor=mgp.ANCHOR_NEEDS_MEASUREMENT)])
    assert (resolved.dropped[0].reason
            == "anchor_needs_a_measurement_nothing_takes")


def test_an_unknown_anchor_is_not_snapped_to_the_nearest_one():
    resolved = resolve([entry(anchor="top_middle")])
    assert resolved.dropped[0].reason == "unknown_anchor"


def test_a_drop_reason_outside_the_enumeration_is_refused():
    """A new drop branch has to say what it is before it can go quiet."""
    resolved = mgp.ResolvedPlan()
    with pytest.raises(mgp.MotionPlanError):
        mgp.Dropped(element="x", reason="because").as_record()
    assert resolved.basis == mgp.NOT_PLANNED


def test_every_recorded_drop_carries_the_words_of_its_reason():
    resolved = resolve([entry(element="not_a_thing"), entry(anchor="nope")])
    for row in resolved.basis_record()["dropped"]:
        assert row["reason"] in mgp.DROP_REASONS
        assert row["what_the_reason_means"] == mgp.DROP_REASONS[row["reason"]]


# ── The three empty readings are spelled differently ─────────────────

def test_a_plan_of_none_and_a_plan_all_dropped_are_not_the_same_absence():
    chose_none = resolve([])
    all_dropped = resolve([entry(element="not_a_thing")])
    never_asked = mgp.resolve_plan(None, timeline_duration=DURATION,
                                   fps=FPS, asked=False)
    assert chose_none.basis == mgp.NO_ELEMENTS_PLANNED
    assert all_dropped.basis == mgp.EVERY_ENTRY_DROPPED
    assert never_asked.basis == mgp.NOT_PLANNED
    readings = {chose_none.basis, all_dropped.basis, never_asked.basis}
    assert len(readings) == 3
    assert set(mgp.BASES) >= readings


def test_the_basis_record_names_every_casualty():
    resolved = resolve([entry(), entry(element="not_a_thing")])
    record = resolved.basis_record()
    assert record["proposed"] == 2
    assert record["resolved"] == 1
    assert len(record["dropped"]) == 1
    assert record["what_the_basis_means"]


# ── No magnitude comes out of this module ────────────────────────────

def test_the_module_supplies_no_duration_no_colour_and_no_footprint():
    """Every magnitude on a moment traces to the plan or is absent."""
    resolved = resolve([entry()])
    moment = resolved.moments[0]
    assert moment["footprint"] is None, "no default footprint"
    assert moment["emphasis"] is None, "no default emphasis"
    assert moment["color"] == "#F5F5F0", "the plan's own colour"


def test_a_plan_that_is_not_a_list_is_refused_rather_than_coerced():
    with pytest.raises(mgp.MotionPlanError):
        mgp.resolve_plan("title_lockup", timeline_duration=DURATION, fps=FPS)


def test_props_that_carry_an_element_draw_and_props_that_do_not_do_not():
    resolved = resolve([entry()])
    segments = mgp.plan_segments(resolved.moments, fps=FPS, width=1080,
                                 height=1920, safe_area=SAFE_AREA)
    assert mgp.props_draw_ink(segments[0]["props"])
    assert not mgp.props_draw_ink({"elements": []})
