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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (  # noqa: E402
    WITHDRAWN_LEGACY_ACCENT_COLOR,
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

