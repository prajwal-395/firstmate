"""A brand template REFINES the motion-graphics layer. It may not GATE one.

**The history in one paragraph.** `generate_motion_props.py` once set
`show_accents = True` unconditionally, so four glowing L-brackets and a
12px progress bar sat on every frame of every video in `#00D4FF` - a
cyan that is not any shipped template's colour. P3.1 (2026-08-16)
replaced that with two template booleans and the captain's ruling that
*a template declaring NOTHING gets NOTHING*. That was right about the
colour and wrong about the layer: it made a template the thing that
decided whether the video had motion graphics at all, and 001 - which
declares `default_brand` - rendered eight fully transparent segments
that nobody had been asked about.

**The captain, 2026-09-02:** *"it does not matter, the LLM was still
meant to plan these things and implement them properly, the brand
template is only a secondary, we are still trying to get the LLM to
produce well reasoned outputs on its own."*

So the line moved, and this file holds the new one:

* **A template REFINES.** Its palette resolves an entry's `colour_role`,
  and that is the whole of what it does to the layer now.
* **A template GATES nothing.** A project with no template plans the
  same layer and states its own colours; there is no element, no
  anchor and no timing a template can switch off.
* **Nothing is drawn in a colour nobody chose.** That half of P3.1 is
  untouched: no palette role and no stated colour means the entry is
  DROPPED, never drawn in a constant, and the withdrawn cyan still
  reaches no frame.
"""
import os
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEP_DIR = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_4_06_render_motion_graphics")
for _p in (PROJECT_ROOT, STEP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_motion_props import (  # noqa: E402
    WITHDRAWN_LEGACY_ACCENT_COLOR,
    brand_palette_roles,
    generate_motion_props,
    timeline_duration,
)
from library.tools import motion_graphics_plan as mgp  # noqa: E402

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

#: `cinematic_narrative`'s shape: the most saturated entry is a dark
#: muted navy, which 6px brackets would read as a smudge in.
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
    with open(os.path.join(PROJECT_ROOT, "library", "templates",
                           f"{name}.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _elements(segs):
    return [e for s in segs for e in s["props"]["elements"]]


# ── The template refines ─────────────────────────────────────────────

def test_a_palette_resolves_a_colour_role():
    segs, resolved = segments(plan(), READABLE_PALETTE)
    assert resolved.basis == mgp.ELEMENTS_PLANNED
    drawn = _elements(segs)
    assert all(e["color"] == "#ff0055" for e in drawn)
    assert all("brand palette" in e["colorBasis"] for e in drawn)


def test_a_palette_beats_a_colour_the_plan_also_stated():
    """A declared role is a request for the brand's own colour, so the
    brand answers it. The plan's own value is what a role-less entry
    uses, not a second guess at the same question."""
    segs, _ = segments(plan(color="#123456"), READABLE_PALETTE)
    assert all(e["color"] == "#ff0055" for e in _elements(segs))


def test_a_palette_with_no_readable_accent_does_not_answer_the_role():
    """P3.1's other half, unchanged: a colour that would read as a smudge
    is not an accent, and the entry is dropped rather than drawn in it."""
    segs, resolved = segments(plan(), UNREADABLE_PALETTE)
    assert not segs
    assert resolved.dropped[0].reason == "no_colour_to_draw_it_in"


# ── The template gates nothing ───────────────────────────────────────

def test_the_same_plan_draws_with_a_template_and_without_one():
    """The regression. `default_brand` declares neither motion flag, and
    that used to be the whole decision."""
    stated = plan(color="#FF8A3D")
    del stated[0]["colour_role"]

    without, _ = segments(stated, {})
    with_default, _ = segments(stated, _template("default_brand").get("style"))

    assert _elements(without), "no template must not empty the layer"
    assert len(_elements(with_default)) == len(_elements(without))
    assert {e["color"] for e in _elements(without)} == {"#FF8A3D"}


def test_no_template_flag_can_switch_an_element_off():
    """`motion_accents` and `motion_progress_bar` are no longer read at
    all, so a template setting them false cannot remove a planned
    element.

    This used to assert that `brand_effect` was not a PARAMETER of the
    resolver, which was a proxy for the real invariant and stopped being
    a true one: the resolver now takes `brand_effect` to read the caption
    style's `position`, which is the band this project's captions occupy
    (library/tools/caption_band.py). The parameter's presence never was
    the defect - reading the two flags out of it was - so the check is
    now on the behaviour and on the code, both of which can still fail if
    the gate returns.
    """
    stated = plan(color="#FF8A3D")
    gating = {"motion_accents": False, "motion_progress_bar": False}
    with_flags, _ = generate_motion_props(
        stated, SPINE, fps=30, width=1080, height=1920,
        brand_style={}, brand_effect=gating, project_folder="")
    without_flags, _ = segments(stated, {})
    assert _elements(with_flags), (
        "a template declaring both motion flags false emptied the layer - "
        "the gate this file exists to keep out is back")
    assert len(_elements(with_flags)) == len(_elements(without_flags))

    import ast as _ast
    import inspect
    resolver_source = inspect.getsource(generate_motion_props)
    for flag in ("motion_accents", "motion_progress_bar"):
        assert flag not in resolver_source, (
            f"{flag} is read by the resolver. A template boolean that "
            f"removes a planned element is the gate again.")
    del _ast

    # Scoped past the module docstring, which CITES both flags as the
    # evidence for why they are no longer read. Evidence is what a
    # [why] link carries in this repository; the scan is pointed at the
    # code, the same way test_motion_graphics_vocabulary's is.
    import ast
    source = open(mgp.__file__, encoding="utf-8").read()
    docstring = ast.parse(source).body[0]
    assert isinstance(docstring, ast.Expr)
    code = "\n".join(source.splitlines()[docstring.end_lineno:])
    for flag in ("motion_accents", "motion_progress_bar"):
        assert flag not in code, (
            f"{flag} is read by the planner. A template boolean that "
            f"removes a planned element is the gate again.")


def test_a_project_with_no_template_resolves_no_roles_and_that_drops_nothing():
    assert brand_palette_roles({}) == {}
    assert brand_palette_roles(None) == {}
    stated = plan(color="#FF8A3D")
    del stated[0]["colour_role"]
    _segs, resolved = segments(stated, {})
    assert not resolved.dropped


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

def test_the_spine_supplies_the_length_and_nothing_else():
    """It bounds a span. It does not time one - which is the timebase.

    A block boundary at 3.0s and 8.0s is nowhere in the resolved
    element: it starts at 0.5s and holds for 2.0s because the plan said
    so.
    """
    assert timeline_duration(SPINE) == 11.0
    segs, _ = segments(plan(color="#FF8A3D"), {})
    element = _elements(segs)[0]
    assert element["timeline_start"] == 0.5
    assert element["timeline_end"] == 2.5
    starts = {b["timeline_start"] for b in SPINE["structure"]}
    assert element["timeline_start"] not in starts


@pytest.mark.parametrize("start,duration", [(0.0, 11.0), (10.5, 4.0)])
def test_a_span_is_bounded_by_the_spine_length(start, duration):
    segs, _ = segments(
        plan(color="#FF8A3D", start_seconds=start, duration_seconds=duration),
        {})
    assert _elements(segs)[0]["timeline_end"] <= timeline_duration(SPINE)
