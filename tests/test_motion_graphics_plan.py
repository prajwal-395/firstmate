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
    # series_look.py was emptied to stop.
    without = resolve([declared])
    assert without.basis == mgp.EVERY_ENTRY_DROPPED
    assert without.dropped[0].reason == "no_colour_to_draw_it_in"


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


def test_an_entry_with_no_timing_is_dropped_rather_than_given_a_block():
    for missing in ("start_seconds", "duration_seconds"):
        e = entry()
        del e[missing]
        resolved = resolve([e])
        assert resolved.dropped[0].reason == "no_timing_declared", missing


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


# ── The basis record reports what is drawn through what ──────────

def _lower_third(start, duration, row=0, anchor="bottom_left"):
    return {
        "element": "lower_third",
        "start_seconds": start,
        "duration_seconds": duration,
        "anchor": anchor,
        "row": row,
        "copy": {"display": "A NAME", "supporting": "A TITLE"},
        "color": "#F5F5F0",
    }


def test_two_cards_sharing_one_row_are_reported_on_the_basis_record():
    """The Reel 06 overlap of 2026-09-12, through the resolver's own
    record: one card at 0.0s, the next at 3.23s, a 3.5s hold, one
    anchor, one row. `overlapping_pairs` returned [] for this until
    the same-anchor skip learned that `row` only separates DIFFERENT
    rows - and this record is what the reel build now reads, so a
    detector nobody calls on that path is pinned here rather than
    assumed.
    """
    resolved = resolve([_lower_third(0.0, 3.5), _lower_third(3.23, 3.5)])
    assert len(resolved.moments) == 2
    pairs = resolved.basis_record()["drawn_through_each_other"]
    assert len(pairs) == 1
    assert pairs[0]["anchors"] == ["bottom_left", "bottom_left"]
    assert "row 0" in pairs[0]["why"]


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


def test_an_element_that_needs_copy_and_has_none_is_dropped():
    e = entry()
    del e["copy"]
    assert resolve([e]).dropped[0].reason == "no_copy_for_an_element_that_needs_one"


# ── Emphasis is declared, never assigned (AGENTS.md 10.5) ─────────────
#
# A run's text is the model's; its tier (`display` vs `supporting`) is
# presentation the plan must state - the handoff asks for copy as a
# mapping of role to text. A bare string, or a role the vocabulary does
# not know, states no tier, so the entry is dropped with the reason
# recorded rather than printed at an emphasis nobody chose.

def test_a_bare_string_declares_no_tier_and_is_dropped():
    resolved = resolve([entry(copy="A NAME")])
    assert resolved.dropped[0].reason == "no_type_role_declared"
    assert "A NAME" in resolved.dropped[0].detail


def test_a_declared_mapping_of_roles_still_resolves():
    """The handoff's own shape - every run names its tier - draws."""
    resolved = resolve([entry(copy={"display": "A NAME",
                                    "supporting": "A TITLE"})])
    assert not resolved.dropped
    assert [(r["text"], r["type_role"])
            for r in resolved.moments[0]["runs"]] == [
        ("A NAME", "display"), ("A TITLE", "supporting")]
    record = resolved.basis_record()
    assert record["resolved"] == 1


def test_a_tracked_anchor_is_dropped_rather_than_pinned_to_a_point():
    resolved = resolve([entry(anchor=mgp.ANCHOR_NEEDS_MEASUREMENT)])
    assert (resolved.dropped[0].reason
            == "anchor_needs_a_measurement_nothing_takes")


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
