"""A model plans the motion-graphics layer, and a brand template refines it.

**The defect this closes.**  On project 001's run of record step 4.06
wrote::

    all 8 resolved motion graphics props draw nothing: the brand template
    declares neither effect.motion_accents nor effect.motion_progress_bar,
    and the creative direction supplies no title or subtitle

Read as a report that is honest; read as a design it is the bug.  No
model was ever ASKED whether this video wanted a title, an accent or a
progress bar.  The capability was switched off by two absent booleans
before any creative reasoning happened, and a missing OPTIONAL input had
silently disabled a whole layer.

The captain's ruling, 2026-09-02: *"it does not matter, the LLM was still
meant to plan these things and implement them properly, the brand
template is only a secondary, we are still trying to get the LLM to
produce well reasoned outputs on its own."*

So **the template REFINES and it may not GATE.**  A project with no brand
template at all gets a planned motion-graphics layer; what the template
adds, when there is one, is a palette to resolve a colour role against
and a typeface to resolve a type role against.  A project without one
does not get a reduced layer - the plan states its own colour, and only
a plan that states NEITHER a role the template can resolve NOR a colour
of its own is dropped, by name, with the reason recorded.

**The overlay layer carries its own timebase.**  The captain, same
ruling: *"why is it that it tries to line up the motion graphics with the
clip segments in the video, the motion graphics can be seperate and on
their own timescale if they need to be, and also they are allowed to use
multiple rows in order to have various motion graphics."*

`generate_motion_props` used to emit one props dict per spine block, with
`durationInFrames` equal to the block's length and the element's timing
therefore equal to the block's timing by construction.  Here an entry
declares `start_seconds` and `duration_seconds` in TIMELINE seconds and
nothing consults a block boundary: the spine reaches the prompt as
CONTEXT - where the speech is, where the cutaways are - and never as a
grid the plan has to land on.

**And several elements may be on screen at once, in rows.**
`anchor` is the vocabulary's nine-position grid; `row` is which line
within that anchor an element occupies, so two elements anchored
`bottom_centre` at the same moment stack rather than collide.  Rows are
an ON-SCREEN layout, not a second Resolve video track: overlapping
entries are composited into ONE overlay segment by :func:`plan_segments`,
which is the shape `timed_text_overlay.plan_timed_text_segments` already
uses and the reason `manifest_validator`'s non-overlap rule for
`motion_graphics_overlay` stays true.  A second V-track would have had to
sit above the generator lane (V5) and the timed-text lane (V6), which
would silently change what composites over what - a stacking order
nobody chose.

**What is refused, and why refusing is the honest answer.**
`motion_graphics_vocabulary.ROSTER` is eighteen elements and the
composition draws all but one of them.  An entry naming that one
is DROPPED with `renderer_cannot_draw_it_yet` and the drop is RECORDED -
never quietly rendered as nothing, and never silently substituted with a
neighbouring element.  The whole roster still reaches the prompt, because
whatever selects a shortlist becomes the chooser (AGENTS.md 10.5) and a
roster written around today's renderer would bake the defect in
permanently (`motion_graphics_vocabulary`'s own argument).

**This module states no magnitude.**  There is no default colour, no
default duration, no default footprint, no minimum count and no maximum.
`DROP_REASONS` is the whole of what a drop can be for and a reason
outside it is refused by name, so a new drop branch has to say what it is
before it can go quiet - the shape `vfx_plan_basis.py` established.

`tests/test_motion_graphics_plan.py`.


Rules relocated from AGENTS.md 10.2
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.2 keeps the headline
and points here.

**An overlay that draws nothing is not rendered.**
`generate_motion_props.props_draw_ink` is the predicate; keep it in step with the MotionGraphics composition.
The output carries NO `available` key when nothing draws, because `available: false` anywhere fails the run. [why](docs/RULE_EVIDENCE.md#overlays-that-draw-nothing)

**A MODEL plans the motion-graphics layer, on its own timebase, in rows - and a brand template REFINES it rather than gating it.**
One enumeration, `library/tools/motion_graphics_plan.py`. Step 4.06 is hybrid: its bridge puts the whole roster (§16) in front of the model, its handoff asks for `motion_graphics_plan`, its post-bridge renders what resolves. Captain's ruling of 2026-09-02 - the gate was the bug, not the render.
- **A template GATES nothing.** `effect.motion_accents` and `effect.motion_progress_bar` are no longer read at all; a project that names no template plans the same layer and states its own colours. What a template still does is resolve an entry's `colour_role` against its palette. **No palette role and no stated colour DROPS the entry** - there is still no house colour (§12).
- **Every element declares `start_seconds` and `duration_seconds` in TIMELINE seconds.** `resolve_plan` is handed the timeline's LENGTH and no block list, so nothing can quantise a start onto a cut. The spine bounds a span; it never times one.
- **`anchor` is where in the frame, `row` is which line within that anchor.** Rows are an ON-SCREEN layout laid out by a flex stack over the live elements, not a second Resolve track: overlapping entries are composited into ONE segment by `plan_segments` (the shape `timed_text_overlay.plan_timed_text_segments` already uses), which is what keeps `manifest_validator`'s non-overlap rule for `motion_graphics_overlay` true. A row PITCH is not the mechanism - a fixed offset per row drew a two-run title straight through the row below it.
- **`layer` is above the picture or behind the segmented subject.** An entry naming neither draws above, which is the absence of compositing rather than a choice of it. A `behind_subject` moment is resolved here, rendered by step 4.06 into its own full-canvas segment, and composited under the subject's tracked matte at compile time (`library/tools/behind_subject.py`); step 1.06 segments only the clips such moments play over. A `behind_subject` request with no usable matte refuses by name (`BehindSubjectRefused`, a `RenRefusal`) - never drawn on top.
- **An element the renderer cannot draw is DROPPED by name and the drop is RECORDED**, never rendered as nothing and never swapped for a neighbour. `DROP_REASONS` is the whole of what a drop can be for and a reason outside it is refused (the `vfx_plan_basis` shape).
- **`planning_basis` says which absence an empty layer is.** `no_elements_planned` is a decision; `every_entry_dropped` is the absence of one. Spelled differently on purpose.
- The upper third's COPY still has no producer in the ENGINE (`motion_graphics_vocabulary.COPY_SOURCE_IS_UNSET`) - the model writes it in the plan, which is one of the answers that entry was written to accept. [why](docs/RULE_EVIDENCE.md#the-motion-graphics-that-were-planned-and-absent)
- `tests/test_motion_graphics_plan.py`, `tests/test_motion_graphics_delivery.py` (which is what proves one reaches a pixel), `tests/test_motion_graphics_template.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (Any, Callable, Dict, FrozenSet, List, Optional,
                    Sequence, Tuple)

from library.tools import caption_band
from library.tools import motion_graphics_vocabulary as vocabulary
from library.tools import review_panel
from library.tools import semantic_visual

#: What the model's answer is called.  One spelling, here.
PLAN_KEY = "motion_graphics_plan"

#: The elements the composition can put on a frame today.  DERIVED from
#: the roster's own reachability flag rather than listed, so an element
#: that becomes drawable is drawable here the moment the roster says so
#: and a stale second list cannot exist.
DRAWABLE = frozenset(
    element.key for element in vocabulary.ROSTER
    if element.reachable == vocabulary.REACHABLE_NOW
)

#: Where in the frame an element may sit.  The vocabulary's own axis.
ANCHORS = tuple(vocabulary.AXES_BY_NAME["anchor"].positions)

#: `tracked` follows a subject and needs a per-frame position nothing
#: measures for the overlay layer - `motion_graphics_vocabulary` files
#: `tracked_label` under NEEDS_MEASUREMENT for the same reason.  An entry
#: anchored here is dropped rather than pinned to a fixed point, because
#: pinning it would be this module choosing a position.
ANCHOR_NEEDS_MEASUREMENT = "tracked"

#: How an element arrives and leaves.  The vocabulary's own axis.
MOTION_CHARACTERS = tuple(vocabulary.AXES_BY_NAME["entrance"].positions)

#: Which role of a declaring palette draws the element.
COLOUR_ROLES = tuple(vocabulary.AXES_BY_NAME["colour_role"].positions)

#: Whether the element draws above the picture or behind the segmented
#: subject. The vocabulary's own axis.
LAYERS = tuple(vocabulary.AXES_BY_NAME["layer"].positions)

#: The layer an entry draws on when it names none. Above the picture -
#: what every overlay has always done. Spelled as a constant rather
#: than read off the axis so the status-quo ante is explicit: the
#: absence of compositing is not a value the engine chose.
LAYER_ABOVE = "above"

#: The layer that composites under the subject's tracked matte.
LAYER_BEHIND_SUBJECT = "behind_subject"

#: The typographic weight of a run of copy.
TYPE_ROLES = tuple(vocabulary.AXES_BY_NAME["type_role"].positions)


# ── What a drop can be for ───────────────────────────────────────────
#
# The whole of it.  `resolve_plan` refuses a reason outside this table by
# name, so a new branch that discards an entry has to say what it is
# before it can go quiet.  Same shape as `vfx_plan_basis.DROP_REASONS`.

DROP_REASONS: Dict[str, str] = {
    "not_in_the_vocabulary": (
        "The entry names something that is not a motion-graphics element "
        "this pipeline knows. motion_graphics_vocabulary.refusal_reason "
        "carries the words, including the enumeration that owns a near "
        "miss."
    ),
    "renderer_cannot_draw_it_yet": (
        "The element is in the roster and the composition has no node "
        "for it. Recorded rather than rendered as nothing, and never "
        "swapped for a neighbouring element - a substitution here would "
        "be the engine choosing which graphic the video gets."
    ),
    "anchor_needs_a_measurement_nothing_takes": (
        "The entry anchors `tracked`, which follows a subject frame by "
        "frame, and no measurement of that reaches the overlay layer. "
        "Pinning it to a fixed point would be choosing a position."
    ),
    "unknown_anchor": (
        "The anchor is not one of the vocabulary's positions. Nothing is "
        "snapped to the nearest one: a near match is a chooser."
    ),
    "no_timing_declared": (
        "The entry declares no start or no duration. The overlay layer "
        "carries its own timebase, so there is no block boundary to fall "
        "back to and inventing one would put the coupling back."
    ),
    "outside_the_timeline": (
        "The declared span starts at or after the end of the timeline, "
        "or runs for no frames. Not clamped: moving a start is choosing "
        "when the graphic plays."
    ),
    "no_copy_for_an_element_that_needs_one": (
        "The roster entry declares copy `required` and the plan carries "
        "none. What a graphic SAYS has no producer in this engine "
        "(motion_graphics_vocabulary.COPY_SOURCE_IS_UNSET), so an empty "
        "run cannot be filled in from anywhere."
    ),
    "no_type_role_declared": (
        "The entry's copy states no typographic tier the vocabulary "
        "knows. Emphasis (`display` vs `supporting`) is presentation "
        "the plan declares - the handoff asks for copy as a mapping of "
        "role to text - and printing an undeclared run at an engine-set "
        "tier would be choosing how loudly the graphic speaks."
    ),
    "collides_with_the_caption_band": (
        "The element draws copy, its anchor is in the vertical band this "
        "project's captions occupy, and its span touches a block "
        "plan_subtitles puts a card on. The roster already declared this "
        "refusal in prose - lower_third's `never` says a graphic that "
        "collides with a caption 'has to move up or not be drawn' - and "
        "the engine may not move it, because choosing a new anchor is "
        "choosing a position. library/tools/caption_band.py."
    ),
    "no_asset_for_an_element_that_needs_one": (
        "The roster entry declares the `asset` axis and the plan names no "
        "file. The engine ships no artwork and states none (AGENTS.md "
        "14), so there is nothing to substitute: an asset element with no "
        "asset draws nothing, and an overlay that draws nothing is not "
        "rendered."
    ),
    "asset_not_found_on_disk": (
        "The plan names a file the project's brand_assets/ does not have, "
        "or the caller supplied no way to look one up. Refused rather "
        "than rendered as an empty frame - the same refusal "
        "compile_manifest makes for an overlay segment the manifest names "
        "and disk does not have."
    ),
    "no_colour_to_draw_it_in": (
        "Neither a brand palette role nor a colour stated by the plan "
        "itself resolves to a colour. There is no fallback: drawing in a "
        "constant is what PR #310 emptied series_look.py to stop."
    ),
    "anchor_phrase_not_found": (
        "The entry's anchor phrase occurs nowhere in the measured word "
        "timings. The visual lands on its words or not at all - landing "
        "near them would be decorating across the speech."
    ),
    "anchor_word_untimed": (
        "The anchor phrase is said but its measured window is missing. "
        "A graphic cued to an unmeasured word is cued to a guess."
    ),
    "no_word_timings_to_anchor_against": (
        "The entry anchors to words and no measured word timings reached "
        "the resolver on this run. Without a measurement there is no "
        "window to land on."
    ),
    "conflicting_timing": (
        "The entry names both an anchor phrase and explicit timeline "
        "seconds - two timings. The engine does not pick one, because "
        "choosing would be choosing when the graphic plays."
    ),
    "data_the_element_draws_from_is_absent": (
        "The element's whole content is its `data` payload and the "
        "payload cannot be drawn. The module that owns the payload says "
        "which part is missing; nothing here fills one in, because "
        "every part of it is something somebody supplied - a rating, a "
        "review, a palette - and the engine supplies none of them "
        "(AGENTS.md 10.5)."
    ),
    "data_no_element_draws": (
        "The entry carries a `data` payload and its element's roster "
        "axes do not include `data`. No node in the composition reads "
        "it, so it would travel silently on the moment and change "
        "nothing on screen. A payload belongs on an element that draws "
        "it, or nowhere."
    ),
    "data_states_no_difference": (
        "The payload's values are all equal, so the drawing would show "
        "no relation and no change: identical bars are not a "
        "comparison, and a roll from a value to itself is a static "
        "figure with animation for its own sake. The roster refuses "
        "both shapes in prose - comparison_bars must be comparable "
        "magnitudes, counter_roll only where the change is the point - "
        "and this is that refusal with teeth."
    ),
    "unknown_layer": (
        "The entry names a layer that is neither `above` nor "
        "`behind_subject`. Nothing is snapped to the nearer one: a "
        "graphic composited under the subject when the plan meant "
        "above it would hide behind a person, and the reverse would "
        "paste over them - both are placements, and the engine may "
        "not choose one."
    ),
}

#: Elements whose `data` payload IS their content, and the callable that
#: says why a payload cannot be drawn, or `""`.
#:
#: Not every element naming the `data` axis belongs here: `step_counter`
#: draws a position inside a copy run and `comparison_bars` draws bars
#: whose labels are the copy, so each still says something without its
#: data. These are the ones where the data is the whole of what is on
#: screen, and an entry arriving without it is an entry with nothing to
#: draw rather than a smaller drawing.
DATA_IS_THE_CONTENT: Dict[str, Callable[[Any], str]] = {
    "review_panel": review_panel.unusable_reason,
}


class MotionPlanError(ValueError):
    """A plan this module refuses outright, rather than dropping an entry."""


# ── The three bases ──────────────────────────────────────────────────
#
# `no_elements_planned` and `every_entry_dropped` are spelled
# differently on purpose: the first is a decision the model took and the
# second is the absence of one.  `vfx_plan_basis` draws the same line and
# for the same reason - `{"visual_effects": []}` read identically whether
# the planner chose stillness or named four effects the post-bridge threw
# away.

NO_ELEMENTS_PLANNED = "no_elements_planned"
EVERY_ENTRY_DROPPED = "every_entry_dropped"
ELEMENTS_PLANNED = "elements_planned"

#: The plan was never asked for.  Distinct from an empty plan, the same
#: way `undetermined.NOT_DECLARED` is distinct from `NOTHING_MISSING`.
NOT_PLANNED = "not_planned"

BASES = (NOT_PLANNED, NO_ELEMENTS_PLANNED, EVERY_ENTRY_DROPPED,
         ELEMENTS_PLANNED)


@dataclass
class Dropped:
    """One entry the resolver discarded, and why."""

    element: str
    reason: str
    detail: str = ""
    entry: Dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict:
        if self.reason not in DROP_REASONS:
            raise MotionPlanError(
                f"{self.reason!r} is not a reason an entry may be dropped "
                f"for. DROP_REASONS is the whole of it: "
                f"{sorted(DROP_REASONS)}.")
        row = {"element": self.element, "reason": self.reason,
               "what_the_reason_means": DROP_REASONS[self.reason]}
        if self.detail:
            row["detail"] = self.detail
        return row


#: Horizontal cells that span the WHOLE usable width.
#:
#: `anchorStyle` sets BOTH `left: safeArea.left` and `right:
#: safeArea.right` for a `centre` horizontal, which is what lets a
#: centred title centre itself and what `progress_bar` needs to draw a
#: full-width bar. It also means a `*_centre` element occupies every
#: column of its band, so anything at `*_left` or `*_right` in the same
#: band at the same moment is drawn through it.
FULL_WIDTH_HORIZONTALS = ("centre",)


def anchor_band(anchor: str) -> Tuple[str, str]:
    """An anchor as (vertical band, horizontal cell).

    The same decomposition `anchorStyle` performs, read from the name.
    """
    name = (anchor or "").strip().lower()
    if name == "centre":
        return ("middle", "centre")
    vertical, _, horizontal = name.partition("_")
    return (vertical, horizontal or "centre")


def overlapping_pairs(moments: List[dict]) -> List[dict]:
    """Pairs of moments that are CERTAIN to be drawn through each other.

    **This is a measurement, and it is reported rather than enforced.**

    TWO shapes collide. Across anchors, `anchorStyle` gives a `*_centre`
    element the whole usable width, so a centred title and a corner stamp
    in the same vertical band, live at the same moment, collide by
    construction - there is no layout pass that could have separated
    them. Within ONE anchor, `row` is the stacking mechanism - but only
    across DIFFERENT rows. Two moments at the same anchor in the same
    row at the same moment share one layout slot, so the second is drawn
    through the first (2026-09-12: two speaker lower thirds at
    `bottom_left`, `row: 0`, overlapping 0.27s on one reel, reported as
    nothing because this function skipped every same-anchor pair).

    Found by compositing every reachable element over a real reel frame
    (`data/vep-animation-completeness/` in the firstmate home): the title ran through the
    context stamp and the channel bug, the quote card through the lower
    third and the stat callout, and the step counter through the progress
    bar. Nine elements, four collisions, and every one of them passed
    every check this repository had - because the demo renders that
    proved the elements DRAW put one element on screen at a time.

    Takes MOMENTS as `resolve_plan` returned them - `startFrame` and
    `durationFrames` in frames. Plan ENTRIES carrying `start_seconds`
    and `duration_seconds` are not moments: read without frames they
    would all start at 0 for 0 frames and compare as non-overlapping,
    which is a second way to get `[]` out of a real collision. Resolve
    first, then ask.

    Not a drop, for the reason AGENTS.md 10.4 gives: a full-width
    `progress_bar` under a `bottom_left` counter may be exactly what the
    plan meant, and a gate that fails correct output is no more coverage
    than one that cannot fail. The pair is named so a reviewer sees it and
    so a future layout pass has something to test against.
    """
    pairs: List[dict] = []
    for i, first in enumerate(moments):
        for second in moments[i + 1:]:
            band_a, cell_a = anchor_band(first.get("anchor", ""))
            band_b, cell_b = anchor_band(second.get("anchor", ""))
            if band_a != band_b:
                continue
            same_anchor = first.get("anchor") == second.get("anchor")
            if same_anchor:
                # One anchor: `row` is the mechanism, and it works across
                # DIFFERENT rows. The same row twice is one layout slot
                # occupied twice, which is the collision - not the
                # stacking. A moment carrying no row is row 0, which is
                # the row `resolve_plan` gives an entry that states none.
                if int(first.get("row", 0) or 0) != int(
                        second.get("row", 0) or 0):
                    continue
            elif not (cell_a in FULL_WIDTH_HORIZONTALS
                      or cell_b in FULL_WIDTH_HORIZONTALS):
                # Two different side cells never share a column.
                continue
            start_a = first.get("startFrame", 0)
            end_a = start_a + first.get("durationFrames", 0)
            start_b = second.get("startFrame", 0)
            end_b = start_b + second.get("durationFrames", 0)
            if start_a >= end_b or start_b >= end_a:
                continue
            # Whether either side is declared CHROME. `persist` is the
            # roster's own function for what holds under the piece -
            # `frame_accents` is a border round the whole frame and
            # `progress_bar` a rule at its foot - so chrome-under-content
            # is what those elements are FOR, while content through
            # content is two things the viewer must read in one place.
            # Reported rather than filtered: which of the two a pair is
            # is a reading, and this module does not take it.
            functions = {
                vocabulary.ELEMENTS_BY_KEY[key].function
                for key in (first.get("element"), second.get("element"))
                if key in vocabulary.ELEMENTS_BY_KEY
            }
            if same_anchor:
                why = (
                    f"both sit at {first.get('anchor')!r} in row "
                    f"{int(first.get('row', 0) or 0)} while they are "
                    f"both on screen. `row` separates elements sharing "
                    f"ONE anchor across different rows; the same row "
                    f"twice is one layout slot occupied twice."
                )
            else:
                why = (
                    f"a {FULL_WIDTH_HORIZONTALS[0]!r} horizontal spans the "
                    f"whole usable width, so both occupy every column of "
                    f"the {band_a} band while they are both on screen. "
                    f"`row` separates elements sharing ONE anchor and these "
                    f"do not share one."
                )
            pairs.append({
                "elements": [first.get("element"), second.get("element")],
                "anchors": [first.get("anchor"), second.get("anchor")],
                "band": band_a,
                "involves_chrome": "persist" in functions,
                "frames": [max(start_a, start_b), min(end_a, end_b)],
                "why": why,
            })
    return pairs


@dataclass
class ResolvedPlan:
    """What `resolve_plan` returns: the drawable moments and the basis."""

    moments: List[dict] = field(default_factory=list)
    basis: str = NOT_PLANNED
    proposed: int = 0
    dropped: List[Dropped] = field(default_factory=list)
    # Where the palette roles came from - the brand template's
    # `series_id` on a pipeline run, "" where no palette was supplied -
    # and what that palette is. Recorded rather than read twice: the
    # run that drew in a palette's colours must say whose they were
    # before a render, not fifteen silently unusable movies after one.
    palette_source: str = ""
    palette_roles: Dict[str, str] = field(default_factory=dict)
    # Whether the palette has a usable accent (`brand_palette`'s own
    # rule), or None where the caller did not say. A palette answering
    # `text`/`outline` with no accent is the shape the repo's own
    # tests call unreadable, and it is stated here rather than found
    # on a timeline.
    palette_has_usable_accent: Optional[bool] = None

    def palette_record(self) -> dict:
        """The palette state as an enumerable run fact, before any render.

        REPORTED, never a gate: the template refines and does not gate,
        so a palette with no usable accent still resolves the roles it
        has and this record is what says so.
        """
        drawn = sum(1 for moment in self.moments
                    if "brand palette" in str(moment.get("colorBasis", "")))
        return {
            "source": self.palette_source,
            "roles": dict(self.palette_roles),
            "has_usable_accent": self.palette_has_usable_accent,
            "moments_drawn_in_palette_colours": drawn,
            "moments_drawn_in_plan_stated_colours": len(self.moments) - drawn,
        }

    def basis_record(self) -> dict:
        """The account that travels onto the step's own output.

        Every casualty is named. An empty layer that SAYS which absence
        it is cannot be misread as a clean one.
        """
        return {
            "basis": self.basis,
            "what_the_basis_means": {
                NOT_PLANNED: "no plan was asked for on this run",
                NO_ELEMENTS_PLANNED: (
                    "the model was asked and planned no element - a "
                    "decision, not an absence"),
                EVERY_ENTRY_DROPPED: (
                    "the model planned elements and every one of them "
                    "was dropped - the absence of a decision surviving, "
                    "not a decision to draw nothing"),
                ELEMENTS_PLANNED: "the model planned elements that draw",
            }[self.basis],
            "proposed": self.proposed,
            "resolved": len(self.moments),
            "dropped": [d.as_record() for d in self.dropped],
            # A measurement, never a gate. See `overlapping_pairs`.
            "drawn_through_each_other": overlapping_pairs(self.moments),
            # Whose palette answered, and how many moments drew in it.
            # See `palette_record`.
            "palette": self.palette_record(),
        }


# ── Resolution ───────────────────────────────────────────────────────

def _number(raw) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return None
    return None


def _text(raw) -> str:
    return str(raw).strip() if isinstance(raw, (str, int, float)) else ""


def _payload_number(raw) -> Optional[float]:
    """A numeric payload value, or None. Booleans are not magnitudes."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _data_is_drawn_without_an_axis(key: str, data: Any) -> bool:
    """Whether a `data` payload on an element with no `data` axis is
    still drawn, because the composition reads it.

    Two cases, both engine directives no model prompt names - a payload
    carrying either key did not come from the bare `data` slot the
    `data_no_element_draws` drop was written for (2026-09-21, PR
    #1258), which dropped every speaker card the night it landed:

    - `lower_third`'s staged construction: the composition's
      `lower_third` arm reads `data.construction` (and `mg_tight_box`
      sizes the same key), so the payload selects which of the arm's
      two drawings reaches the frame. Written by the deterministic
      speaker path (`speaker_identity.entry_for`).
    - `list_build`'s stage offsets: the renderer's `stageStarts`
      (`remotion-subtitles/src/compositions/MotionGraphics/index.tsx`)
      reads `data.stage_offsets` - the per-item seconds that make a
      staged element an explainer rather than a list that appears.
      Written by the deterministic explainer path
      (`explainer_plan.plan_entries`). PR #1258's drop refused it too,
      and the #1270 exemption covered only the first case, so the
      explainer contract test stayed red until this second arm landed.

    Presence, not shape: the engine may add keys to its own directives
    (`truncated_for_next` arrived after `construction`) and a subset
    list would drop the card again the day it does. A payload carrying
    a directive alongside depicting magnitudes keeps both - no
    producer writes that shape, and refusing half a payload is
    rewriting, not refusing.
    """
    if not isinstance(data, dict):
        return False
    if (key == "lower_third"
            and data.get("construction") == "staged_rule"):
        return True
    return key == "list_build" and "stage_offsets" in data


def _data_states_no_difference(key: str, data: Any) -> str:
    """Why a depicting payload draws no relation and no change, or `""`.

    The proof lane for the `data` slot (2026-09-21) found the model
    inventing equal-pair payloads - [3,3], [5,5] - which mean nothing as
    comparisons and violate the roster's own `never` rules. Asked only
    of the two elements whose drawing IS a relation or a change:
    comparison_bars (labelled magnitudes at proportional length) and
    counter_roll (a figure animating from one value to another).
    Anything else carrying `data` - step_counter, pointer_annotation,
    review_panel, digit_counter - is read on its own terms elsewhere,
    and a position equalling its total is a real state (the last step),
    not an empty one.
    """
    if not isinstance(data, dict):
        return ""
    if key == "comparison_bars":
        values = data.get("values")
        if not isinstance(values, list):
            return ""
        magnitudes = [_payload_number(v) for v in values]
        magnitudes = [m for m in magnitudes if m is not None]
        if len(magnitudes) >= 2 and len(set(magnitudes)) < 2:
            return (f"`data.values` states {len(magnitudes)} magnitudes "
                    f"and all are {magnitudes[0]}. Identical bars are "
                    f"not a comparison the speech made.")
        return ""
    if key == "counter_roll":
        start = _payload_number(data.get("start_value"))
        end = _payload_number(data.get("end_value"))
        if start is not None and end is not None and start == end:
            return (f"`data` rolls from {start} to {end}. A figure that "
                    f"does not change is stat_callout's entry, not a "
                    f"roll.")
        return ""
    return ""


def _copy_runs(entry: Dict[str, Any]) -> List[dict]:
    """The runs of copy an entry carries, in the order it wrote them.

    A run is `{text, type_role}`. `type_role` is the vocabulary's axis
    and carries no size: what `display` and `micro` MEASURE is the
    composition's business, and what they MEAN is the vocabulary's.
    Nothing here supplies a run the plan did not write, and nothing
    here supplies a TIER the plan did not state: a run whose emphasis
    the entry never declared carries `type_role: ""`, and `resolve_plan`
    drops an entry carrying such a run as `no_type_role_declared`
    rather than printing it at an emphasis nobody chose (AGENTS.md
    10.5). The handoff asks for copy as a mapping of role to text, so
    a bare string is an answer that declined the axis, not one that
    named it.
    """
    raw = entry.get("copy")
    runs: List[dict] = []
    if isinstance(raw, str):
        text = raw.strip()
        if text:
            runs.append({"text": text, "type_role": ""})
        return runs
    if isinstance(raw, dict):
        # A mapping of type_role -> text, in the roles' own order so a
        # display run is never printed under its supporting run because
        # a dict happened to be built the other way round. A key that
        # is not a role of the vocabulary states no tier: it is carried
        # undeclared rather than guessed as `supporting`.
        for role in TYPE_ROLES:
            text = _text(raw.get(role))
            if text:
                runs.append({"text": text, "type_role": role})
        for key, value in raw.items():
            if key in TYPE_ROLES:
                continue
            text = _text(value)
            if text:
                runs.append({"text": text, "type_role": ""})
        return runs
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    runs.append({"text": text, "type_role": ""})
            elif isinstance(item, dict):
                text = _text(item.get("text"))
                if not text:
                    continue
                role = _text(item.get("type_role"))
                runs.append({
                    "text": text,
                    "type_role": role if role in TYPE_ROLES else "",
                })
    return runs


def resolve_colour(entry: Dict[str, Any], palette_roles: Dict[str, str],
                    palette_source: str = "") -> Tuple[str, str]:
    """The colour an element draws in, and where it came from.

    **This is the whole of "the template refines and does not gate".**
    A brand palette, when the project named a template that has one,
    resolves the entry's `colour_role`. A project with no template
    resolves nothing here - and the plan's own `color` then answers,
    because the model is allowed taste and the engine is not.

    `palette_source` names where the roles came from - the brand
    template's `series_id` on a pipeline run - and a palette-resolved
    colour carries it, so a props file never says "brand palette"
    without saying whose. Fifteen graphics once drew in the lightest
    entry of a palette nobody chose for their series and every one of
    them read `brand palette role 'text'` with no name attached; the
    name is what makes the colour traceable to the project's own
    brand. Empty means no palette was supplied and the basis reads
    exactly as it always has.

    Returns `(colour, basis)` with an empty colour when neither
    answers. There is no third source: a constant here is a house look
    (AGENTS.md section 12).
    """
    role = _text(entry.get("colour_role")).lower()
    if role in COLOUR_ROLES:
        from_palette = _text(palette_roles.get(role))
        if from_palette:
            if palette_source:
                return (from_palette,
                        f"brand palette role {role!r} of {palette_source!r}")
            return from_palette, f"brand palette role {role!r}"
    stated = _text(entry.get("color") or entry.get("colour"))
    if stated:
        return stated, "stated by the plan"
    return "", ""


def resolve_plan(plan: Any, *, timeline_duration: float, fps: float,
                  palette_roles: Optional[Dict[str, str]] = None,
                  palette_source: str = "",
                  palette_has_usable_accent: Optional[bool] = None,
                  asked: bool = True,
                 caption_bands: Optional[FrozenSet[str]] = None,
                 captioned_spans: Sequence[Tuple[float, float]] = (),
                 resolve_asset: Optional[Callable[[str], str]] = None,
                 word_windows: Sequence[Dict[str, Any]] = (),
                 ) -> ResolvedPlan:
    """Turn the model's plan into drawable moments, naming every casualty.

    `timeline_duration` bounds a span and nothing else times one: the
    layer carries its own timebase and a spine block is never consulted.

    `palette_roles` is what a brand template's palette resolved to, or
    `{}` when the project named no template. It REFINES - see
    :func:`resolve_colour` - and its absence drops nothing on its own.

    `palette_source` names where those roles came from - the brand
    template's `series_id` on a pipeline run - so a palette-resolved
    colour carries its provenance onto the props file. `""` where no
    palette was supplied. `palette_has_usable_accent` is
    `brand_palette`'s own answer for that palette, or None where the
    caller did not say. Both travel onto :meth:`ResolvedPlan.basis_record`
    as the `palette` fact, stated before any render.

    `caption_bands` and `captioned_spans` are where and when this
    project's captions are on screen, from `library/tools/caption_band.py`.
    An element that draws copy into a band the captions occupy, over a
    span they occupy it, is dropped as `collides_with_the_caption_band`.
    Both default to empty, which refuses nothing: a caller that cannot
    say where the captions are does not get a guess, and a resolver
    called without them behaves exactly as it did before the rule
    existed.

    `resolve_asset` turns a file the plan NAMES into a URL the
    composition can load, and returns `""` when the project does not have
    it. Only elements declaring the `asset` axis consult it. There is no
    fallback and no placeholder: the engine ships no artwork (AGENTS.md
    14), so an unresolvable asset is a drop with a reason rather than an
    element rendered as an empty frame.

    `word_windows` is the measured speech - `{word, start, end}` in
    timeline seconds, the shape `semantic_visual.collect_word_windows`
    produces. An entry naming `anchor_phrase` is timed by SEARCH over
    these (AGENTS.md 6) instead of by declared seconds, so the visual
    lands on its own words. An entry naming both is dropped as
    `conflicting_timing`: two timings is ambiguous and the engine does
    not pick one.
    """
    palette_roles = palette_roles or {}
    resolved = ResolvedPlan(
        proposed=0,
        palette_source=palette_source,
        palette_roles=dict(palette_roles),
        palette_has_usable_accent=palette_has_usable_accent,
    )
    if not asked:
        resolved.basis = NOT_PLANNED
        return resolved
    if plan is None:
        resolved.basis = NO_ELEMENTS_PLANNED
        return resolved
    if isinstance(plan, dict):
        plan = plan.get("elements", plan.get(PLAN_KEY, []))
    if not isinstance(plan, list):
        raise MotionPlanError(
            f"{PLAN_KEY} must be a list of entries, got "
            f"{type(plan).__name__}.")

    resolved.proposed = len(plan)

    def drop(entry, key, reason, detail=""):
        if reason not in DROP_REASONS:
            raise MotionPlanError(
                f"{reason!r} is not a reason an entry may be dropped for. "
                f"DROP_REASONS is the whole of it: {sorted(DROP_REASONS)}.")
        resolved.dropped.append(
            Dropped(element=key or "(unnamed)", reason=reason,
                    detail=detail, entry=entry if isinstance(entry, dict) else {}))

    for raw in plan:
        entry = raw if isinstance(raw, dict) else {}
        named = _text(entry.get("element") or entry.get("element_key")
                      or (raw if isinstance(raw, str) else ""))
        key = vocabulary.canonical_key(named)
        if not key:
            drop(entry, named, "not_in_the_vocabulary",
                 vocabulary.refusal_reason(named))
            continue

        element = vocabulary.ELEMENTS_BY_KEY[key]
        if key not in DRAWABLE:
            drop(entry, key, "renderer_cannot_draw_it_yet",
                 element.reachability_note or
                 f"the roster records it as {element.reachable}")
            continue

        anchor = _text(entry.get("anchor")).lower()
        if anchor == ANCHOR_NEEDS_MEASUREMENT:
            drop(entry, key, "anchor_needs_a_measurement_nothing_takes")
            continue
        if anchor not in ANCHORS:
            drop(entry, key, "unknown_anchor",
                 f"{anchor!r} is not one of {list(ANCHORS)}")
            continue

        # Above the picture or behind the segmented subject. An entry
        # naming neither draws above - the absence of compositing, not
        # a chosen value. A behind_subject moment is resolved here and
        # grounded later: the masks do not exist yet when the plan is
        # resolved (step 1.06 runs after this one), so compile grounds
        # it against the matte and refuses by name where none is usable
        # (library/tools/behind_subject.py) - never drawn on top.
        raw_layer = _text(entry.get("layer")).lower()
        if not raw_layer:
            layer = LAYER_ABOVE
        elif raw_layer in LAYERS:
            layer = raw_layer
        else:
            drop(entry, key, "unknown_layer",
                 f"{entry.get('layer')!r} is neither 'above' nor "
                 f"'behind_subject'")
            continue

        start = _number(entry.get("start_seconds"))
        duration = _number(entry.get("duration_seconds"))
        timing_basis = "declared"
        phrase = _text(entry.get(semantic_visual.ANCHOR_PHRASE_KEY))
        if phrase:
            # A visual cued to its own words. Explicit seconds beside a
            # phrase is two timings, and the engine picks neither.
            if start is not None or duration is not None:
                drop(entry, key, "conflicting_timing",
                     f"anchor_phrase={phrase!r} beside "
                     f"start_seconds={entry.get('start_seconds')!r} "
                     f"duration_seconds={entry.get('duration_seconds')!r}")
                continue
            try:
                start, duration, timing_basis = (
                    semantic_visual.resolve_anchor_timing(
                        entry, word_windows or []))
            except semantic_visual.SemanticVisualError as anchor_err:
                drop(entry, key, anchor_err.reason, anchor_err.detail)
                continue
        if start is None or duration is None:
            drop(entry, key, "no_timing_declared",
                 "start_seconds and duration_seconds are both required; "
                 f"got start={entry.get('start_seconds')!r} "
                 f"duration={entry.get('duration_seconds')!r}")
            continue
        start = max(0.0, start)
        if duration <= 0 or start >= timeline_duration:
            drop(entry, key, "outside_the_timeline",
                 f"start {start}s, duration {duration}s, timeline "
                 f"{timeline_duration}s")
            continue
        # The END is bounded by the timeline because a frame past the
        # last one cannot be rendered at all.  The START is never moved:
        # moving it would choose when the graphic plays.
        end = min(start + duration, timeline_duration)
        frames = max(1, int(round((end - start) * fps)))

        runs = _copy_runs(entry)
        if element.copy == "required" and not runs:
            drop(entry, key, "no_copy_for_an_element_that_needs_one")
            continue
        undeclared = sorted({r["text"] for r in runs
                             if r.get("type_role") not in TYPE_ROLES})
        if undeclared:
            drop(entry, key, "no_type_role_declared",
                 "runs without a declared tier: "
                 + ", ".join(repr(t) for t in undeclared))
            continue

        # Where the captions are, and when. Checked after the copy is
        # known, because whether the element draws copy at all is half
        # the question: a bar or a bracket in the caption band is not a
        # collision, and refusing it would be a gate failing correct
        # output.
        collides = caption_band.collision(
            element_key=key, anchor=anchor, start=start, end=end,
            has_copy=bool(runs), bands=caption_bands or frozenset(),
            spans=captioned_spans)
        if collides:
            drop(entry, key, "collides_with_the_caption_band", collides)
            continue

        # A project-supplied file, for the elements that draw one. Read
        # from the roster's own axes rather than a second list of which
        # elements take an asset.
        asset_url = ""
        if "asset" in element.axes:
            named = _text(entry.get("asset") or entry.get("asset_file"))
            if not named:
                drop(entry, key, "no_asset_for_an_element_that_needs_one")
                continue
            asset_url = _text(resolve_asset(named)) if resolve_asset else ""
            if not asset_url:
                drop(entry, key, "asset_not_found_on_disk",
                     f"{named!r} is not in the project's brand_assets/"
                     if resolve_asset else
                     f"{named!r} was named and this caller supplied no way "
                     f"to look a project asset up")
                continue

        # An element whose content IS its data payload. Asked before the
        # colour, because a card with no reviews on it is not a colour
        # question.
        unusable = DATA_IS_THE_CONTENT.get(key)
        if unusable is not None:
            why = unusable(entry.get("data"))
            if why:
                drop(entry, key, "data_the_element_draws_from_is_absent", why)
                continue

        # A payload no node draws, and a payload that draws nothing.
        # Asked before the colour for the same reason: neither is a
        # colour question. An empty `data` is no payload at all - a
        # planner echoing the slot with `{}` refuses nothing. And an
        # engine directive is drawn despite declaring no `data` axis -
        # `lower_third`'s staged construction, `list_build`'s stage
        # offsets - because the composition's own arms read it - so it
        # never reaches this drop.
        data_payload = entry.get("data")
        if (data_payload and "data" not in element.axes
                and not _data_is_drawn_without_an_axis(key,
                                                       data_payload)):
            drop(entry, key, "data_no_element_draws",
                 f"{key!r} declares no `data` axis "
                 f"({', '.join(element.axes)}), so the payload reaches "
                 f"no node in the composition")
            continue
        no_difference = _data_states_no_difference(key, data_payload)
        if no_difference:
            drop(entry, key, "data_states_no_difference", no_difference)
            continue

        colour, colour_basis = resolve_colour(
            entry, palette_roles, palette_source)
        # Asked of the elements the ROSTER says draw in a colour. An
        # element whose axes do not include `colour_role` draws something
        # else - `channel_bug` draws a project's own file - and demanding
        # a colour of it would refuse it for failing to declare a
        # dimension the vocabulary never gave it.
        if not colour and "colour_role" in element.axes:
            drop(entry, key, "no_colour_to_draw_it_in",
                 "the entry names no colour_role the brand palette "
                 "resolves and states no colour of its own")
            continue

        entrance = _text(entry.get("entrance")).lower()
        exit_ = _text(entry.get("exit")).lower()
        resolved.moments.append({
            "element": key,
            "anchor": anchor,
            # Above the picture or behind the segmented subject. The
            # build precomposites a behind_subject moment under the
            # subject's matte into a `qtrle` overlay placed on a
            # motion-graphics row.
            "layer": layer,
            # Which line within that anchor. Two elements anchored the
            # same way at the same moment stack instead of colliding.
            "row": max(0, int(_number(entry.get("row")) or 0)),
            "runs": runs,
            "color": colour,
            "colorBasis": colour_basis,
            "entrance": entrance if entrance in MOTION_CHARACTERS else "cut",
            "exit": exit_ if exit_ in MOTION_CHARACTERS else "cut",
            # Timeline seconds, kept beside the frame counts so a reader
            # of the props file never has to divide by the fps to see
            # what the plan actually said.
            "timeline_start": round(start, 3),
            "timeline_end": round(end, 3),
            # How this span was timed: declared seconds, or the measured
            # word window an anchor phrase searched for. `subject` is the
            # model's free-text reasoning for the visual, carried as
            # provenance - nothing resolves from it
            # (library/tools/semantic_visual.py).
            "timing_basis": timing_basis,
            "subject": semantic_visual.entry_subject(entry),
            # Where this span sits in the WHOLE piece, as fractions.
            # `progress_bar` is the element that draws them; every other
            # element carries them because a reader of the props file
            # should not have to recompute what the plan already knew.
            "timelineProgressStart": round(
                start / timeline_duration, 4) if timeline_duration else 0.0,
            "timelineProgressEnd": round(
                end / timeline_duration, 4) if timeline_duration else 0.0,
            "startFrame": int(round(start * fps)),
            "durationFrames": frames,
            "asset": asset_url,
            "footprint": _number(entry.get("footprint")),
            "emphasis": _number(entry.get("emphasis")),
            "why": _text(entry.get("why") or entry.get("rationale")),
            "data": entry.get("data", {}),
        })

    if resolved.moments:
        resolved.basis = ELEMENTS_PLANNED
    elif resolved.proposed:
        resolved.basis = EVERY_ENTRY_DROPPED
    else:
        resolved.basis = NO_ELEMENTS_PLANNED
    return resolved


# ── Segments ─────────────────────────────────────────────────────────

def plan_segments(moments: List[dict], *, fps: float, width: int,
                  height: int, safe_area: dict,
                  project_folder: str = "") -> List[dict]:
    """Cluster the moments into overlay segments, each on a stated LANE.

    Moments whose spans touch or overlap go into ONE segment and have
    their `startFrame` rebased to that segment's start, so several
    graphics are on screen together on a single video lane.

    ...unless the cluster cannot carry ONE TIGHT BOX, and the project
    asked for tight boxes.  The captain, on Reel 26, 2026-09-11:
    *"this was a full frame compostie render of two different
    animations, see if you can make it so that its two different
    tighbox animations that are layered on seperate rows on the
    timeline"*.  A middle-anchored stack beside a top or bottom one is
    the one case `mg_tight_box` must refuse to bound - ``top: 50%``
    centres on the CANVAS - so clustering the two forced a full
    1080x1920 render for two small graphics.  Nothing was baked
    together: they are separate plan entries with their own anchors and
    timings, and the only thing joining them was this cluster.
    `mg_tight_box.separable_groups` owns the partition; each group
    becomes its own segment and its own tight box.  The middle mix is
    the correctness case - ``top: 50%`` centres on the wrong frame -
    and the top+bottom tall mix is the size case: a centre-anchored
    title over a bottom panel unions edge to edge, so floored at the
    full-frame layout width (PR 1301) it trips the coverage backstop
    and renders full canvas, while apart each half is a short tight
    row at the same wrap width.

    A segment therefore carries a **lane**: segments on one lane never
    overlap in time, and lanes become Resolve rows
    (`timeline_layout.allocate_non_overlapping_rows`, the same packing
    that decides SFX and music rows).  `manifest_validator` checks
    overlap per lane for the same reason.  With no split - which is
    every project that declares full-canvas graphics, and every
    cluster already inside one zone family - every segment is lane 0
    and the output is what it always was.

    The clustering is the same algorithm
    `timed_text_overlay.plan_timed_text_segments` uses.  Deliberately
    the same: a second clustering with its own rounding would be two
    answers to one question.
    """
    from library.tools.mg_tight_box import separable_groups
    from library.tools.overlay_mode import resolve_motion_graphics_geometry
    from library.tools.timeline_layout import allocate_non_overlapping_rows

    ordered = sorted(moments, key=lambda m: (m["startFrame"], m.get("row", 0)))
    clusters: List[List[dict]] = []
    for moment in ordered:
        if clusters and moment["startFrame"] <= _cluster_end(clusters[-1]):
            clusters[-1].append(moment)
        else:
            clusters.append([moment])

    # Splitting a cluster only WINS where the render would be tight:
    # under a full-canvas declaration two groups are two full-frame
    # renders on two rows for no gain, so the declaration is read here
    # rather than assumed.
    split = (resolve_motion_graphics_geometry(project_folder or None)
             == "tight")
    groups: List[List[dict]] = []
    for cluster in clusters:
        groups.extend(separable_groups(cluster) if split else [cluster])
    groups.sort(key=lambda g: (min(m["startFrame"] for m in g),
                               min(m.get("row", 0) for m in g)))

    spans = [(min(m["startFrame"] for m in g), _cluster_end(g))
             for g in groups]
    lanes = [row for _, row in allocate_non_overlapping_rows(spans, 0)]

    segments = []
    for index, (group, lane) in enumerate(zip(groups, lanes)):
        start_frame = min(m["startFrame"] for m in group)
        end_frame = _cluster_end(group)
        total_frames = max(1, end_frame - start_frame)
        segments.append({
            "index": index,
            "lane": lane,
            "timeline_start": round(start_frame / fps, 3),
            "timeline_end": round(end_frame / fps, 3),
            "total_frames": total_frames,
            "element_count": len(group),
            "elements": sorted({m["element"] for m in group}),
            "props": {
                "elements": [
                    {**m, "startFrame": m["startFrame"] - start_frame}
                    for m in group
                ],
                "fps": fps,
                "width": width,
                "height": height,
                "safeArea": safe_area,
                "durationInFrames": total_frames,
            },
        })
    return segments


def _cluster_end(cluster: List[dict]) -> int:
    return max(m["startFrame"] + m["durationFrames"] for m in cluster)


def props_draw_ink(props: dict) -> bool:
    """Would Remotion put a single pixel on this frame?

    Every pixel the composition draws belongs to an entry in
    `props["elements"]`, and `resolve_plan` has already dropped every
    entry that resolves to nothing - so a segment with an element draws,
    and one without does not.  That is a much shorter statement than the
    flag-per-element predicate it replaces, and it stays true when the
    composition grows a node, which the old one did not.
    """
    return bool(props.get("elements"))
