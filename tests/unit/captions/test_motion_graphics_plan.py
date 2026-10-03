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
import json
import re
from types import SimpleNamespace
from pathlib import Path
import io


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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


# ── 3. rows, and several graphics at once ────────────────────────────

def test_simultaneous_elements_share_a_segment_in_rows_disjoint_ones_do_not():
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

    # Overlapping spans are composited; disjoint ones are not.
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

def test_every_entry_that_cannot_draw_is_dropped_by_name():
    """Never rendered as nothing, never swapped for a neighbour, never
    given a block or an emphasis nobody chose: each drop names its reason
    out of DROP_REASONS."""
    no_start, no_duration, no_copy = entry(), entry(), entry()
    del no_start["start_seconds"]
    del no_duration["duration_seconds"]
    del no_copy["copy"]
    cases = [
        (no_start, "no_timing_declared"),
        (no_duration, "no_timing_declared"),
        (no_copy, "no_copy_for_an_element_that_needs_one"),
        # A bare string states no tier (display vs supporting).
        (entry(copy="A NAME"), "no_type_role_declared"),
        (entry(anchor=mgp.ANCHOR_NEEDS_MEASUREMENT),
         "anchor_needs_a_measurement_nothing_takes"),
    ]
    # Whichever roster entry is currently recorded as undrawable, if any.
    unreachable = next((e.key for e in mgv.ROSTER
                        if e.reachable != mgv.REACHABLE_NOW), None)
    if unreachable is not None:
        cases.append((entry(element=unreachable),
                      "renderer_cannot_draw_it_yet"))
    for declared, reason in cases:
        dropped = resolve([declared]).dropped
        assert dropped and dropped[0].reason == reason, reason
        assert dropped[0].detail is not None
    assert "A NAME" in resolve([entry(copy="A NAME")]).dropped[0].detail


# ── Emphasis is declared, never assigned (AGENTS.md 10.5) ─────────────
#
# A run's text is the model's; its tier (`display` vs `supporting`) is
# presentation the plan must state - the handoff asks for copy as a
# mapping of role to text. A bare string, or a role the vocabulary does
# not know, states no tier, so the entry is dropped with the reason
# recorded rather than printed at an emphasis nobody chose.

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


# --------------------------------------------------------------------------
# From test_motion_graphics_data_slot.py
#
# The answer schema carries `data` and `asset`, and junk payloads are refused.
#
# Captain's intent 2026-09-21 (the cheap half of
# vep-motion-graphics-are-type-not-graphics): the answer schema handed to
# the motion-graphics planner enumerates an entry's fields as
# element/anchor/row/copy/color-or-colour_role/entrance/exit/why. `data`
# and `asset` are not in that list, and the seven roster elements that
# draw a THING rather than words carry their content in exactly those two
# fields. Measured consequence: comparison_bars, counter_roll,
# digit_counter, pointer_annotation, step_counter, website_panel and
# channel_bug were proposed ZERO times each across all 145 entries ever
# planned.
#
# The proof lane (vep-mg-schema-data-slot-proof) showed the slot alone
# invites junk: equal-pair payloads ([3,3], [5,5]) that mean nothing as
# comparisons, and `data` copied onto entries whose element never draws
# it. So the schema surface AND the two refusals are pinned here
# together: a planner CAN choose a depicting element, and the two junk
# shapes are dropped by name.

from library.tools import reel_semantic_visual as sem_vis

HANDOFF = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_4_06_render_motion_graphics",
    "handoff.md")
MANIFEST = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_4_06_render_motion_graphics",
    "manifest.json")


def _entry(**kw):
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


def _resolve(plan):
    return mgp.resolve_plan(plan, timeline_duration=DURATION, fps=FPS,
                            palette_roles={})


def _bars(values):
    return _entry(
        element="comparison_bars", anchor="centre",
        copy={"display": "THIS YEAR", "supporting": "LAST YEAR"},
        data={"values": list(values)})


def _roll(start, end):
    return _entry(
        element="counter_roll", anchor="centre",
        copy={"display": "SIGNUPS"},
        data={"start_value": start, "end_value": end})


# ── the schema surface: data and asset exist wherever a planner learns
# what an entry may contain ────────────────────────────────────────────

def _answer_blocks():
    with open(HANDOFF, encoding="utf-8") as handle:
        text = handle.read()
    start = text.index("## Your answer")
    return re.findall(r"```json(.*?)```", text[start:], re.S)


def _moment():
    return SimpleNamespace(
        number=9,
        timeline_name="Reel 09 - plays with his mind",
        timeline_start=10.0, timeline_end=18.0)


def _transcript():
    return {"segments": [
        {"timeline_start": 10.0, "timeline_end": 14.0,
         "resolve_item_id": "item1", "source_file": "clip_a.mov",
         "source_start": 100.0, "source_end": 104.0,
         "words": [
             {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
             {"word": "plays", "start": 11.0, "end": 11.4, "timed": True},
             {"word": "mind", "start": 12.5, "end": 13.0,
              "timed": True}]},
        {"timeline_start": 14.0, "timeline_end": 18.0,
         "resolve_item_id": "item2", "source_file": "clip_a.mov",
         "source_start": 104.0, "source_end": 108.0,
         "words": [
              {"word": "again", "start": 14.2, "end": 14.6,
               "timed": True}]}]}


# ── the refusals: the proof lane's two junk shapes ────────────────────

def test_the_proof_lanes_junk_payloads_are_dropped_by_name():
    """[3,3] means nothing as a comparison and violates the roster's own
    `never` rules. Dropped, not drawn as two identical bars."""
    resolved = _resolve([_bars([3, 3])])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason in mgp.DROP_REASONS
    # `data` on a copy element - the proof lane's copy-everywhere shape -
    # reaches no node in the composition: dropped by name too.
    resolved = _resolve([_entry(data={"values": [3, 9]})])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason in mgp.DROP_REASONS


# ── the exception: the engine's own lower-third directive ──────────

def _lower_third_2(**kw):
    base = _entry(
        element="lower_third", anchor="bottom_left",
        copy={"display": "ADA LOVELACE", "supporting": "Analyst"},
        color="#11FFAA",
        data={"construction": "staged_rule",
              "speaker": "Ada",
              "colour_basis": "effect.speaker_lower_thirds"})
    base.update(kw)
    return base


def _staged_list(**kw):
    base = _entry(
        element="list_build", anchor="bottom_left",
        copy=[{"text": "LINKEDIN", "type_role": "supporting"},
              {"text": "CRUNCHBASE", "type_role": "supporting"},
              {"text": "REDDIT", "type_role": "supporting"}],
        color="#FFB8D4",
        data={"stage_offsets": [0.0, 0.8, 2.0]})
    base.update(kw)
    return base


def test_the_engines_own_data_directives_resolve():
    """PR #1258's `data_no_element_draws` drop read the roster axes as
    the whole of what the composition draws and dropped every speaker
    card the night it landed - the only motion graphics the captain
    kept. `lower_third` declares no `data` axis and correctly so (the
    axis is a measured payload; the directive is engine-written), but
    the composition's own arm reads `data.construction`. The entry the
    deterministic speaker path writes - including the post-truncation
    shape - resolves and carries its payload."""
    entry = _lower_third_2(data={
        "construction": "staged_rule",
        "speaker": "Ada",
        "colour_basis": "effect.speaker_lower_thirds",
        "truncated_for_next": {"hold_seconds": 3.5,
                               "duration_seconds": 3.23,
                               "next_starts_at": 3.23}})
    resolved = _resolve([entry])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    (moment,) = resolved.moments
    assert moment["data"]["construction"] == "staged_rule"

    # The second deterministic producer: `explainer_plan.plan_entries`
    # writes `data.stage_offsets` on a `list_build`, which the renderer's
    # `stageStarts` reads - it resolves and carries its payload too.
    resolved = _resolve([_staged_list()])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    (moment,) = resolved.moments
    assert moment["data"] == {"stage_offsets": [0.0, 0.8, 2.0]}


def test_real_payloads_resolve_and_copy_without_data_is_untouched():
    resolved = _resolve([_bars([3, 9])])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    (moment,) = resolved.moments
    assert moment["data"] == {"values": [3, 9]}
    resolved = _resolve([_roll(0, 100)])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    assert resolved.moments[0]["data"] == {
        "start_value": 0, "end_value": 100}
    # A copy entry without data is untouched.
    resolved = _resolve([_entry()])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    assert resolved.moments[0]["data"] == {}


# ── ARM A second half: the slot ships WITH the instruction ──────────
#
# The proof lane showed a bare slot produces garbage: `data` copied
# onto all 8 entries, no switch to a depicting element, invented
# equal-pair payloads ([3,3], [5,5]). So the schema surface above and
# the instruction below are pinned together: the licence to choose a
# depicting element, and which payloads may be asserted versus which
# need a measurement.

def _handoff_text():
    with open(HANDOFF, encoding="utf-8") as handle:
        return handle.read()


def _manifest_plan_description():
    with open(MANIFEST, encoding="utf-8") as handle:
        manifest = json.load(handle)
    outputs = manifest["interface"]["llm_outputs"]
    return next(
        o for o in outputs if o["name"] == "motion_graphics_plan")[
        "description"]


def _reel_request(tmp_path):
    project = str(tmp_path / "proj-agree")
    os.makedirs(os.path.join(project, "pipeline_output", "review"))
    path = sem_vis.write_request(
        _moment(), _transcript(), [(10.0, 18.0)], project, FPS)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_three_schema_surfaces_agree(tmp_path):
    """All three places that describe the slot to the model state one
    contract: the manifest's llm_outputs, the handoff's answer section,
    and the reel request file's expected_schema.

    A planner reading any one of them must get the same slot: `data`
    and `asset` exist, a payload is asserted only from what the speech
    itself states, and the two junk shapes drop by name.
    """
    manifest_desc = _manifest_plan_description()
    handoff = _handoff_text()
    schema = _reel_request(tmp_path)["expected_schema"]
    for surface in (manifest_desc, handoff, schema):
        assert "data" in surface
        assert "asset" in surface
        assert "speech itself" in surface
        assert "all equal" in surface


# --------------------------------------------------------------------------
# From test_motion_graphics_template.py
#
# A brand template REFINES the motion-graphics layer. It may not GATE one.
#
# **The history in one paragraph.** `generate_motion_props.py` once set
# `show_accents = True` unconditionally, so four glowing L-brackets and a
# 12px progress bar sat on every frame of every video in `#00D4FF` - a
# cyan that is not any shipped template's colour. P3.1 (2026-08-16)
# replaced that with two template booleans and the captain's ruling that
# *a template declaring NOTHING gets NOTHING*. That was right about the
# colour and wrong about the layer: it made a template the thing that
# decided whether the video had motion graphics at all, and 001 - which
# declares `default_brand` - rendered eight fully transparent segments
# that nobody had been asked about.
#
# **The captain, 2026-09-02:** *"it does not matter, the LLM was still
# meant to plan these things and implement them properly, the brand
# template is only a secondary, we are still trying to get the LLM to
# produce well reasoned outputs on its own."*
#
# So the line moved, and this file holds the new one:
#
# * **A template REFINES.** Its palette resolves an entry's `colour_role`,
#   and that is the whole of what it does to the layer now.
# * **A template GATES nothing.** A project with no template plans the
#   same layer and states its own colours; there is no element, no
#   anchor and no timing a template can switch off.
# * **Nothing is drawn in a colour nobody chose.** That half of P3.1 is
#   untouched: no palette role and no stated colour means the entry is
#   DROPPED, never drawn in a constant, and the withdrawn cyan still
#   reaches no frame.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (  # noqa: E402
    WITHDRAWN_LEGACY_ACCENT_COLOR,
    generate_motion_props,
    timeline_duration,
)

SPINE = {
    "structure": [
        {"block_type": "hook", "position": 0,
         "timeline_start": 0.0, "timeline_end": 3.0},
        {"block_type": "speech", "position": 1,
         "timeline_start": 3.0, "timeline_end": 8.0},
        {"block_type": "broll", "position": 2,
         "timeline_start": 8.0, "timeline_end": 11.0},
    ]
}

#: A palette whose most saturated entry would read on screen.
READABLE_PALETTE = {"color_palette": ["#ff0055", "#ffffff", "#000000"]}

#: The synthetic cinematic palette's shape: the most saturated entry is
#: a dark muted navy, which 6px brackets would read as a smudge in.
UNREADABLE_PALETTE = {"color_palette": ["#223344", "#aabbcc", "#111111"]}


def plan(**kw):
    """One entry naming a colour_role and nothing else."""
    base = {"element": "frame_accents", "start_seconds": 0.5,
            "duration_seconds": 2.0, "anchor": "centre",
            "colour_role": "accent"}
    base.update(kw)
    return [base]


def segments(motion_plan, brand_style=None):
    return generate_motion_props(
        motion_plan, SPINE, fps=30, width=1080, height=1920,
        brand_style=brand_style or {}, project_folder="")


def _template(name):
    from tests.brand_fixtures import ALL_SYNTHETIC
    return dict(ALL_SYNTHETIC[name])


def _elements(segs):
    return [e for s in segs for e in s["props"]["elements"]]


# ── The template refines ─────────────────────────────────────────────

def test_a_palette_resolves_a_colour_role_only_with_a_readable_accent():
    segs, resolved = segments(plan(), READABLE_PALETTE)
    assert resolved.basis == mgp.ELEMENTS_PLANNED
    drawn = _elements(segs)
    assert all(e["color"] == "#ff0055" for e in drawn)
    assert all("brand palette" in e["colorBasis"] for e in drawn)
    # A colour that would read as a smudge is not an accent: the entry is
    # dropped rather than drawn in it.
    segs, resolved = segments(plan(), UNREADABLE_PALETTE)
    assert not segs
    assert resolved.dropped[0].reason == "no_colour_to_draw_it_in"


# ── The template gates nothing ───────────────────────────────────────

def test_the_same_plan_draws_with_or_without_a_template_and_its_flags():
    """The regression. The synthetic default declares neither motion flag,
    and that used to be the whole decision."""
    stated = plan(color="#FF8A3D")
    del stated[0]["colour_role"]

    without, _ = segments(stated, {})
    with_default, _ = segments(stated, _template("synthetic_default").get("style"))

    assert _elements(without), "no template must not empty the layer"
    assert len(_elements(with_default)) == len(_elements(without))
    assert {e["color"] for e in _elements(without)} == {"#FF8A3D"}

    # `motion_accents` / `motion_progress_bar` are no longer read: a
    # template setting both false cannot remove a planned element.
    gating = {"motion_accents": False, "motion_progress_bar": False}
    with_flags, _ = generate_motion_props(
        stated, SPINE, fps=30, width=1080, height=1920,
        brand_style={}, brand_effect=gating, project_folder="")
    without_flags, _ = segments(stated, {})
    assert _elements(with_flags), (
        "a template declaring both motion flags false emptied the layer - "
        "the gate this file exists to keep out is back")
    assert len(_elements(with_flags)) == len(_elements(without_flags))


# ── Nothing is drawn in a colour nobody chose ────────────────────────

def test_the_withdrawn_cyan_never_reaches_a_frame():
    """It was never any template's colour, only an unconditional default."""
    for style in ({}, READABLE_PALETTE, UNREADABLE_PALETTE):
        segs, _ = segments(plan(), style)
        for element in _elements(segs):
            assert element["color"] != WITHDRAWN_LEGACY_ACCENT_COLOR


def test_an_entry_with_no_colour_anywhere_is_dropped_by_name():
    bare = plan()
    del bare[0]["colour_role"]
    segs, resolved = segments(bare, {})
    assert not segs
    assert resolved.dropped[0].reason == "no_colour_to_draw_it_in"
    assert "no fallback" in mgp.DROP_REASONS["no_colour_to_draw_it_in"]


# ── The one thing still read off the edit ────────────────────────────

@pytest.mark.parametrize("start,duration", [(10.5, 4.0)])
def test_a_span_is_bounded_by_the_spine_length(start, duration):
    segs, _ = segments(
        plan(color="#FF8A3D", start_seconds=start, duration_seconds=duration),
        {})
    assert _elements(segs)[0]["timeline_end"] <= timeline_duration(SPINE)


# ── The brand rules live in the prompt, not beside the data ──────────
#
# `brand_refinement` used to carry two constant strings - how a colour is
# resolved, and what a template absence means - because step 4.06's
# `handoff.md` was under the captain's freeze. The freeze lifted
# 2026-09-09, so the prompt carries them and the bridge carries per-run
# facts only. A rule in both places is worse than either: the day they
# disagree, nothing says which one the model followed.

# ── The palette states whose it is, before a render ──────────────────
#
# 2026-09-21: fifteen graphics drew in `#aabbcc` - the lightest entry
# of a palette nobody chose for their series - and every props file
# read `brand palette role 'text'` with no name attached, while the
# run's own record carried no palette state at all. A palette-resolved
# colour now names the source that supplied it, and the basis record
# carries the palette fact. REPORTED, never a gate: the layer resolves
# exactly as it always has.


# --------------------------------------------------------------------------
# From test_motion_graphics_layer.py
#
# The `layer` axis: above the picture or behind the segmented subject.
#
# A title the walker passes in front of is `layer: behind_subject` on a
# `title_lockup` entry. Absent reads as above - the absence of
# compositing, not a choice of it. Unknown refuses by name.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def entry_2(**kw):
    base = {
        "element": "title_lockup",
        "start_seconds": 0.5,
        "duration_seconds": 2.0,
        "anchor": "centre",
        "copy": {"display": "A NAME"},
        "color": "#F5F5F0",
    }
    base.update(kw)
    return base


def resolve_2(plan):
    return mgp.resolve_plan(plan, timeline_duration=DURATION, fps=FPS)


def test_an_entry_naming_no_layer_draws_above():
    resolved = resolve_2([entry_2()])
    assert resolved.moments[0]["layer"] == "above"


def test_behind_subject_is_resolved_and_kept():
    resolved = resolve_2([entry_2(layer="behind_subject")])
    assert resolved.basis == mgp.ELEMENTS_PLANNED
    assert not resolved.dropped
    assert resolved.moments[0]["layer"] == "behind_subject"


def test_an_unknown_layer_is_dropped_by_name():
    resolved = resolve_2([entry_2(layer="underneath")])
    assert resolved.basis == mgp.EVERY_ENTRY_DROPPED
    assert resolved.dropped[0].reason == "unknown_layer"


def test_behind_moments_become_their_own_full_canvas_segments():
    """`generate_motion_props` never clusters a behind moment with an
    above one: one moment, one full-canvas segment carrying the layer,
    so compile routes it to the matte path instead of a row."""
    from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (
        generate_motion_props,
    )
    spine = {"structure": [{"timeline_end": DURATION}],
             "frame_rate": FPS}
    plan = [entry_2(layer="behind_subject"),
            entry_2(anchor="top_left", start_seconds=5.0)]
    segments, resolved = generate_motion_props(
        plan, spine, fps=FPS, width=1080, height=1920)
    assert len(resolved.moments) == 2
    assert len(segments) == 2
    behind = [s for s in segments if s.get("layer") == "behind_subject"]
    assert len(behind) == 1
    assert behind[0]["props"]["width"] == 1080
    assert behind[0]["props"]["height"] == 1920
    assert behind[0]["props"]["elements"][0]["startFrame"] == 0
    above = [s for s in segments if "layer" not in s]
    assert len(above) == 1


def test_a_behind_mov_becomes_a_numbered_png_sequence(tmp_path):
    """The Fusion Loader resolves a numbered PNG sequence where a
    qtrle .mov does not resolve at all, so a behind title is
    sequenced after its render - and a short sequence refuses."""
    import subprocess
    from library.steps.step_4_06_render_motion_graphics import (
        post_bridge as pb,
    )
    mov = tmp_path / "mg_test.mov"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=size=64x64:rate=30:duration=0.2",
         "-c:v", "qtrle", "-pix_fmt", "argb", str(mov)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert proc.returncode == 0, proc.stderr
    rendered = {"segment_id": "t1", "overlay_path": str(mov),
                "total_frames": 6, "layer": "behind_subject"}
    out = pb._sequence_behind_segment(rendered)
    assert out["overlay_path"].endswith("_behind_00000.png")
    assert out["sequence"]["frame_count"] == 6
    assert out["format"] == "PNG image sequence (RGBA)"
    assert out["overlay_path"] != str(mov)

    short = {"segment_id": "t2", "overlay_path": str(mov),
             "total_frames": 600, "layer": "behind_subject"}
    with pytest.raises(pb.MotionGraphicsRenderRefused):
        pb._sequence_behind_segment(short)


# --------------------------------------------------------------------------
# From test_motion_graphics_case_is_declared.py
#
# Copy renders in the case stated; uppercase is declared, never default.
#
# Finding 22, execution-frontier report 2026-09-24: motion-graphics text
# was force-uppercased whatever case the copy stated - the MG2.3 run's
# `title_lockup` copy 'link in bio' rendered 'LINK IN BIO'. A run now
# renders as typed unless it declares `uppercase: true`, and the plan
# (`motion_graphics_plan._copy_runs`), the renderer (the Remotion
# composition) and the tight-box measurement (`mg_tight_box._run_width`)
# all read that one flag - so the measured union is the drawn one.
#
# No Resolve, no render: plan resolution, a recording fitter, and the
# composition source.

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import mg_tight_box as box  # noqa: E402


COMPOSITION = (REPO_ROOT / "remotion-subtitles" / "src" / "compositions"
               / "MotionGraphics" / "index.tsx")


def _entry_2(**kw):
    base = {
        "element": "title_lockup",
        "start_seconds": 0.5,
        "duration_seconds": 2.0,
        "anchor": "top_left",
        "color": "#F5F5F0",
    }
    base.update(kw)
    return base


# ── The plan carries the declared style through ─────────────────────

def test_copy_renders_in_the_case_stated_by_default():
    """A lowercase display run stays lowercase unless the plan says shout."""
    resolved = _resolve([_entry_2(copy=[
        {"text": "link in bio", "type_role": "display"}])])
    run = resolved.moments[0]["runs"][0]
    assert run["text"] == "link in bio"
    assert not run["uppercase"]

    shouted = _resolve([_entry_2(copy=[
        {"text": "LINK IN BIO", "type_role": "display",
         "uppercase": True}])])
    assert shouted.moments[0]["runs"][0]["uppercase"] is True


# ── The measurement draws what the renderer draws ───────────────────

class _RecordingFitter:
    def __init__(self):
        self.seen = []

    def word_width(self, word):
        self.seen.append(word)
        return float(len(word))


def _cache_for(role="display", scale=1.0):
    size = box.TYPE_SIZE[role] * scale
    weight = box.TYPE_WEIGHT[role]
    fitter = _RecordingFitter()
    return {(round(size, 3), weight): fitter}, fitter


def test_measurement_honours_case_unless_the_flag_is_set():
    cache, fitter = _cache_for()
    box._run_width("link in bio", "display", 1.0, "", cache)
    assert fitter.seen[0] == "link in bio"

    cache, fitter = _cache_for()
    box._run_width("link in bio", "display", 1.0, "", cache,
                   uppercase=True)
    assert fitter.seen[0] == "LINK IN BIO"


# ── The renderer reads the flag, not the tier ───────────────────────

def test_no_display_tier_forces_uppercase_in_the_composition():
    """The two unconditional `type_role === "display" ? "uppercase"`
    transforms were the defect. Every uppercase transform now answers
    to the run's own declared flag."""
    source = COMPOSITION.read_text(encoding="utf-8")
    offenders = [line for line in source.splitlines()
                 if 'type_role === "display" ? "uppercase"' in line]
    assert not offenders, "composition still force-uppercases a display run"
    assert "run.uppercase" in source


# --------------------------------------------------------------------------
# From test_motion_graphic_lanes.py
#
# Two animations that cannot share one tight box get a row each.
#
# The captain, on Reel 26, on the one full-canvas file two animations had
# been rendered into, 2026-09-11::
#
#     "this was a full frame compostie render of two different animations,
#      see if you can make it so that its two different tighbox animations
#      that are layered on seperate rows on the timeline"
#
# He is describing `mg_tight_box`'s own refusal from the outside. Reel 26's
# segment carries a `title_lockup` at `top_centre` and a `subject_emblem`
# at `middle_right`; their spans touch, so `plan_segments` clustered them
# into one segment, and a middle zone beside another zone is exactly the
# case a tight box cannot bound - `top: 50%` centres on the CANVAS, so on
# a small canvas the middle stack centres on the wrong frame. One segment,
# one full 1080x1920 render, two million pixels a frame for two small
# graphics.
#
# Nothing was baked. They are separate plan entries with their own
# anchors, timings and copy, and the only thing joining them was the
# cluster - so the cluster splits, each half is its own tight box, and
# the two overlap in time, which makes them two rows.

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


from library.tools.mg_tight_box import (
    separable_groups,
    tighten_motion_graphics_props,
)
from library.tools.timeline_layout import (
    EXPLAINER,
    SEMANTIC,
    TrackPlan,
    plan_layout,
)

FPS_2 = 24000 / 1001
SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _moment_2(element, anchor, start, frames, text="WORDS", row=0):
    return {"element": element, "anchor": anchor, "row": row,
            "runs": [{"text": text, "type_role": "display"}],
            "startFrame": start, "durationFrames": frames,
            "footprint": None, "emphasis": None, "data": {}}


def _props(elements):
    return {"elements": elements, "fps": FPS_2, "width": 1080,
            "height": 1920, "safeArea": SAFE,
            "durationInFrames": 205}


# Reel 26's own two, at its own frames.
REEL_26 = [
    _moment_2("title_lockup", "top_centre", 0, 144,
            text="PICTURE ONE CUSTOMER"),
    _moment_2("subject_emblem", "middle_right", 109, 96, text="?"),
]


# ── The partition ──────────────────────────────────────────────────

def test_the_reel_26_pair_cannot_share_one_tight_box():
    """The premise, measured rather than recalled: together they force
    the full canvas."""
    assert tighten_motion_graphics_props(_props(REEL_26)) is None


def test_split_apart_each_half_is_a_tight_box():
    groups = separable_groups(REEL_26)
    assert [[e["element"] for e in g] for g in groups] == [
        ["title_lockup"], ["subject_emblem"]]
    boxes = [tighten_motion_graphics_props(_props(g)) for g in groups]
    assert all(box is not None for box in boxes)
    # Both are a fraction of the frame they used to cost.
    for box in boxes:
        assert box.width * box.height < 0.3 * 1080 * 1920
        assert box.placement["scaling"] == 1


def test_a_segment_already_in_one_zone_family_is_not_split():
    """A split that buys nothing is two rows for no reason. Top and
    bottom share canvas edges, so they bound exactly together.

    Side-anchored on both edges, so no layout-width floor applies
    and the pair stays tight. With a CENTRE anchor in the mix the
    floored canvas covers the frame and the pair renders full canvas
    instead - which is the fix, not a regression: the old narrow
    canvas rewrapped the centre copy (see
    test_centre_tall_mix_falls_back_to_full_canvas in
    test_mg_tight_layout_width.py).
    """
    together = [_moment_2("title_lockup", "top_left", 0, 60),
                _moment_2("lower_third", "bottom_left", 10, 60)]
    assert separable_groups(together) == [together]
    assert tighten_motion_graphics_props(_props(together)) is not None


# A top-centre title over a bottom panel: PR 1301 floors the centre
# copy at the full-frame layout width, so the edge-to-edge union trips
# the coverage backstop together - the pair that used to ship the
# rewrap defect (see test_centre_tall_mix_falls_back_to_full_canvas in
# test_mg_tight_layout_width.py).
TALL_MIX = [
    _moment_2("title_lockup", "top_centre", 0, 144,
            text="A SINGLE YEAR"),
    _moment_2("lower_third", "bottom_left", 10, 100),
]


# ── The lanes ──────────────────────────────────────────────────────

def test_the_split_segments_land_on_two_lanes():
    segments = mgp.plan_segments(REEL_26, fps=FPS_2, width=1080, height=1920,
                                 safe_area=SAFE)
    assert [s["elements"] for s in segments] == [
        ["title_lockup"], ["subject_emblem"]]
    assert [s["lane"] for s in segments] == [0, 1]
    # Each segment is its own span, rebased to its own start.
    assert segments[0]["total_frames"] == 144
    assert segments[1]["total_frames"] == 96
    assert segments[0]["props"]["elements"][0]["startFrame"] == 0
    assert segments[1]["props"]["elements"][0]["startFrame"] == 0
    # And they overlap, which is the whole point.
    assert segments[1]["timeline_start"] < segments[0]["timeline_end"]


def test_a_full_canvas_project_is_not_split(tmp_path):
    """Splitting only WINS where the render would be tight. Under a
    full-canvas declaration two groups are two full-frame renders on two
    rows for no gain, so the declaration is read rather than assumed."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: p\nslug: p\npipeline:\n"
        "  motion_graphics_overlay_geometry: full\n", encoding="utf-8")
    segments = mgp.plan_segments(REEL_26, fps=FPS_2, width=1080, height=1920,
                                 safe_area=SAFE,
                                 project_folder=str(project))
    assert len(segments) == 1
    assert segments[0]["elements"] == ["subject_emblem", "title_lockup"]
    assert segments[0]["lane"] == 0


def test_segments_that_do_not_overlap_share_one_lane():
    """A lane is a row, and a row holds everything that fits on it."""
    apart = [
        _moment_2("title_lockup", "top_centre", 0, 40),
        _moment_2("subject_emblem", "middle_right", 200, 40, text="?"),
    ]
    segments = mgp.plan_segments(apart, fps=FPS_2, width=1080, height=1920,
                                 safe_area=SAFE)
    assert len(segments) == 2
    assert [s["lane"] for s in segments] == [0, 0]


# ── The rows ───────────────────────────────────────────────────────

def _reel_material(**kwargs):
    return {"angles": [{"key": "a", "label": "SpeakerOne",
                        "speech_name": "SpeakerOne CH1",
                        "program_channel": 1}],
            "has_broll": False, "has_frame": True,
            "caption_spans": [(0, 100)], "has_transitions": False,
            "has_explainer": False, "has_semantic": True,
            "mg_spans": [], "has_generators": False,
            "timed_text_spans": [], "music_spans": [], "sfx_spans": [],
            **kwargs}


def test_two_overlapping_semantic_segments_are_two_rows():
    plan = plan_layout(_reel_material(
        semantic_spans=[(234, 378), (343, 439)]))
    rows = plan.rows_for_role(SEMANTIC)
    assert [r.name for r in rows] == ["Semantic", "Semantic 2"]
    assert [r.index for r in rows] == sorted(r.index for r in rows)
    # Contiguous with the caption row above them, no gap and no reuse.
    indices = [t.index for t in plan.video_tracks]
    assert indices == list(range(1, len(indices) + 1))


def test_the_explainer_layers_on_the_same_terms():
    plan = plan_layout(_reel_material(
        has_semantic=False, has_explainer=True,
        explainer_spans=[(0, 100), (50, 150), (60, 200)]))
    assert [r.name for r in plan.rows_for_role(EXPLAINER)] == [
        "Explainer", "Explainer 2", "Explainer 3"]


# ── The manifest gate ──────────────────────────────────────────────

def test_the_overlap_gate_reads_lanes_not_the_whole_track():
    from library.tools.manifest_validator import (
        _check_overlay_segments_do_not_overlap,
    )

    layered = {"motion_graphics_overlay": {"segments": [
        {"timeline_start": 9.7, "timeline_end": 15.8, "lane": 0},
        {"timeline_start": 14.3, "timeline_end": 18.3, "lane": 1},
    ]}}
    assert _check_overlay_segments_do_not_overlap(layered) == []

    stacked = {"motion_graphics_overlay": {"segments": [
        {"timeline_start": 9.7, "timeline_end": 15.8, "lane": 0},
        {"timeline_start": 14.3, "timeline_end": 18.3, "lane": 0},
    ]}}
    errors = _check_overlay_segments_do_not_overlap(stacked)
    assert len(errors) == 1
    assert "lane 0" in errors[0]


# --------------------------------------------------------------------------
# From test_generator_overlay_routing.py
#
# Tests for generator preset routing to the overlay track.
#
# The generator routing has three layers:
# 1. Planning: resolve_generator_overlays extracts generators from the
#    creative plan and produces overlay entries with timeline placement.
# 2. Rejection: resolve_vfx still rejects generators from the clip-effect
#    path - this is the existing half that PR 95 delivered.
# 3. Manifest: compile_manifest passes generator_overlays through to the
#    renderer.
#
# These tests exercise all three layers headlessly, without Resolve.

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_4_03_plan_vfx.post_bridge import (
    EFFECT_ALIASES,
    resolve_generator_overlays,
    resolve_vfx,
)
from library.tools.builtin_effect_loader import (
    list_generator_effects,
)


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


# ── Layer 1: Planning ──────────────────────────────────────────────

class TestResolveGeneratorOverlays:
    """resolve_generator_overlays routes generators to overlay entries."""

    REQUIRED_KEYS = {
        "overlay_id", "effect_name", "target_block_position",
        "timeline_start", "timeline_end", "composite_mode",
    }

    def test_generator_produces_a_complete_overlay_entry_on_its_block(self):
        """A generator preset in the plan becomes an overlay carrying every
        required key and its spine block's timing."""
        plan = [{"target_block_position": 2, "effect_type": "fireworks"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2, 3))
        assert len(overlays) == 1
        assert overlays[0]["effect_name"] == "fireworks"
        assert overlays[0]["timeline_start"] == 5.0
        assert overlays[0]["timeline_end"] == 10.0
        assert overlays[0]["target_block_position"] == 2
        missing = self.REQUIRED_KEYS - set(overlays[0].keys())
        assert not missing, f"Missing keys: {missing}"

    def test_mixed_plan_separates_generators(self):
        """A plan with both generators and clip effects only overlays the generators."""
        plan = [
            {"target_block_position": 1, "effect_type": "fireworks"},
            {"target_block_position": 2, "effect_type": "advanced_camera_shake"},
            {"target_block_position": 3, "effect_type": "snow"},
        ]
        spine = _spine(1, 2, 3)
        overlays = resolve_generator_overlays(plan, spine)
        clip_vfx = resolve_vfx(plan, spine)

        # Generators go to overlays
        overlay_names = {o["effect_name"] for o in overlays}
        assert overlay_names == {"fireworks", "snow"}

        # Clip effects go to VFX
        vfx_names = {v["effect_type"] for v in clip_vfx}
        assert "advanced_camera_shake" in vfx_names
        assert "fireworks" not in vfx_names
        assert "snow" not in vfx_names

    def test_an_invalid_or_duplicate_block_position_is_dropped(self):
        """A generator targeting a non-existent block is dropped, and only
        one generator per block position is allowed."""
        plan = [{"target_block_position": 99, "effect_type": "fireworks"}]
        assert resolve_generator_overlays(plan, _spine(1, 2)) == []
        plan = [
            {"target_block_position": 1, "effect_type": "fireworks"},
            {"target_block_position": 1, "effect_type": "snow"},
        ]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 1
        assert overlays[0]["effect_name"] == "fireworks"

    def test_aliased_generator_resolved(self, monkeypatch):
        """An aliased effect_type that resolves to a generator is routed.

        This used to search `EFFECT_ALIASES` for an entry pointing at a
        generator and skip when it found none.  It never finds one: the
        only alias is `push_in -> zoom_emphasis`, a clip effect, and an
        alias may only RENAME a capability, never choose one - so the
        test skipped in every environment and always would have.  The
        routing it is about is real either way, so the alias is supplied
        here instead of hunted for.
        """
        assert "fireworks" in list_generator_effects()
        monkeypatch.setitem(EFFECT_ALIASES, "firework_burst", "fireworks")
        plan = [{"target_block_position": 1, "effect_type": "firework_burst"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 1
        assert overlays[0]["effect_name"] == "fireworks"


# ── Layer 2: Rejection still works ────────────────────────────────

class TestGeneratorStillRejectedFromClipEffects:
    """resolve_vfx must still reject generators from V1 clip effects."""

    def test_generator_rejected_from_vfx(self):
        """A generator in the plan is rejected from resolve_vfx output."""
        plan = [{"target_block_position": 1, "effect_type": "fireworks"}]
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            result = resolve_vfx(plan, _spine(1, 2))
        finally:
            sys.stderr = old_stderr
        assert len(result) == 0
        assert "Rejected generator preset" in captured.getvalue()


# ── Layer 4: Manifest integration ─────────────────────────────────
