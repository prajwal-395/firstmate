"""The motion-graphics elements this pipeline may plan, and the axes each one is declared on.

**The problem this closes.** The captain, 2026-08-29, on the capability:
the pipeline has *"access to remotion to make literally any kind of
motion design and animation desired."*  That is true of the toolchain and
false at the moment of decision.  A step asked to pick from an undefined
set picks nothing, or invents inconsistently: the creative audit of round
2 counted **9 of 28 output decisions the model was never asked**, and an
undefined roster is exactly that failure - a decision nobody offered.
"Anything is possible" is not a menu.

**What was here before.** An implicit roster of three, and nobody wrote
it down.  ``remotion-subtitles/src/compositions/MotionGraphics/index.tsx``
draws a two-line upper third, four corner brackets and a progress bar,
each behind its own boolean; ``generate_motion_props`` turns two brand
flags into those booleans and reads ``creative_direction`` for a title
that its schema has no field for.  Project 001 rendered eight segments,
53.8 MB of ProRes, in which ``max(alpha)`` is 0 on every frame - motion
graphics reported as delivered, drawing nothing.  Three elements chosen
by whoever wrote the component is not a vocabulary, and the fact that the
render path is broken is not a reason to keep it at three.

**So this table is written from what an editor needs, not from what the
composition can draw today.**  Every entry carries
:attr:`MotionElement.reachable` saying whether the current renderer can
put it on a frame, and one of the nineteen cannot.  The reachability
flag is a fact reported about each entry; it is not a filter on
membership.  A roster written around today's renderer would bake a
defect into the vocabulary permanently.

An axis, not a value
--------------------
Every entry defines the DIMENSIONS a declaration must fill and fixes none
of them.  There is no colour here, no duration, no size, no easing
strength and no intensity, and none of those has a default or a bound
either - the captain, 2026-08-28: *"i want no hardcoded values. there are
no house glow looks, there are no settled house grain or anything"*, and
PR #310 emptied ``series_look.py`` on that ruling.  A roster that said
"the stat callout holds for 1.2 s in the accent colour" would put the
same defect back one level up, in the one place it is hardest to see.

:data:`AXES` is the vocabulary of dimensions; an entry names the axes it
is declared on.  An axis with enumerated positions names the positions
(``fade``, ``slide``, ``scale``, ``mask``, ``cut`` is a set of motion
characters, not a chosen one).  An axis without them is continuous and
its magnitude belongs to whoever declares it.
:func:`assert_no_settled_values` is the runnable statement of that rule
and ``tests/test_motion_graphics_vocabulary.py`` parses this file's own
source to enforce it.

Series-neutral, because the purpose is two purposes
---------------------------------------------------
Set 2026-08-25: this engine serves a daily channel **and** client work,
so anything keyed to one identity is a defect rather than a shortcut
(AGENTS.md section 14).  Nothing here names a channel, a host, a show, a
palette or a typeface.  ``channel_bug`` is the closest an entry comes to
identity and it takes a project-supplied ASSET; the engine ships no
artwork and states none.  The captain's own standard - clean, vibrant and
deliberate, not distressed; saturated and bold, no washed-out neutrals -
is a standard for the DECLARATIONS a project or a template writes, and it
is deliberately not encoded here, because encoding it would make every
client's video look like the captain's.

Two neighbouring decisions this roster does NOT take
----------------------------------------------------
Both remain the captain's, and the roster is written so that either
answer works:

1. **What produces the copy a motion graphic shows.**  Entries declare
   :attr:`MotionElement.copy` - whether the element needs a text payload
   at all - and never where that text comes from.  A model writing it, a
   project declaring it, a transcript supplying it and a template
   carrying it all satisfy the same entry.  :data:`COPY_SOURCE_IS_UNSET`
   records that this file must never grow a producer.
2. **Whether the model authors each component or fills a props schema.**
   An entry names a KIND, its axes, its inputs and its refusals.  Under
   the props answer the axes are the schema; under the authoring answer
   they are the brief the authored component must honour, and the
   refusals are what review checks it against.  Nothing here says which.

If a future change to this table would force either answer, that is the
signal to stop and escalate rather than to decide it by implication.

Where the boundary runs
-----------------------
:data:`OUT_OF_VOCABULARY` records the things that look like motion
graphics and belong to another enumeration - captions, transitions,
picture treatment, bookend artwork - each with the module that owns it.
An element is in this roster when it is an ADDITIVE OVERLAY that carries
MEANING the picture and the captions do not already carry.  That is the
line: a treatment of the picture is VFX, a treatment of the spoken word
is a caption, a change between two shots is a transition, and a full
frame of artwork is a bookend.

Reading it
----------
    python3 -m library.tools.motion_graphics_vocabulary            # the roster
    python3 -m library.tools.motion_graphics_vocabulary --check    # the rules, as a gate

:func:`roster_rows` and :data:`ROSTER_LEGEND` are the prompt-side route -
the same shape ``sfx_library.load_sfx_catalog`` and
``music_measurement.MEASUREMENT_LEGEND`` take, so a planning step's
bridge can put the whole roster in front of the model as a table without
this module knowing anything about prompts.  Nothing is shortlisted:
nineteen entries fit, and whatever selects a shortlist becomes the chooser
(AGENTS.md section 10.5).
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Axes ─────────────────────────────────────────────────────────────
#
# A dimension a declaration fills.  An axis NEVER carries a default and
# NEVER carries a bound: how long is long, how large is large and how
# loud is loud are the declaring author's decisions, and an
# engine-supplied range is a strength nobody chose arriving one level up
# (the rule `series_look.py` was emptied to establish).


@dataclass(frozen=True)
class Axis:
    """One dimension an element is declared on.

    Attributes:
        name: What a declaration writes.
        ranges_over: What the dimension IS, in words. Never a magnitude.
        positions: The named positions, when the axis is enumerated
            rather than continuous. Naming a set is not choosing from
            it; an empty tuple means the axis is continuous and its
            magnitude is the declaring author's.
        resolved_against: The enumeration or measurement a position or
            magnitude is interpreted against, so the axis stays
            meaningful at any delivery format.
    """

    name: str
    ranges_over: str
    positions: tuple[str, ...]
    resolved_against: str

    @property
    def continuous(self) -> bool:
        return not self.positions


#: The whole vocabulary of dimensions. An entry names the axes it is
#: declared on; an axis outside this table is refused by name, for the
#: same reason `series_look.LOOK_ELEMENTS` refuses an unknown element - a
#: misspelt axis that silently draws nothing is the failure this
#: repository keeps hitting.
AXES: tuple[Axis, ...] = (
    Axis(
        name="timing",
        ranges_over=(
            "when the element starts and how long it holds, expressed as "
            "an anchor plus an offset plus a hold"
        ),
        positions=(),
        resolved_against=(
            "the spine block, the beat grid (library/tools/beat_grid.py) "
            "or a planned cut - the same three anchors "
            "timed_text_overlay.py already resolves against"
        ),
    ),
    Axis(
        name="anchor",
        ranges_over="where in the frame the element sits",
        positions=(
            "top_left", "top_centre", "top_right",
            "middle_left", "centre", "middle_right",
            "bottom_left", "bottom_centre", "bottom_right",
            "tracked",
        ),
        resolved_against=(
            "library/tools/safe_area.py, so a position is inside the "
            "platform's keep-clear band at any delivery format. "
            "'tracked' means the position comes from a measured track "
            "rather than from the frame."
        ),
    ),
    Axis(
        name="footprint",
        ranges_over=(
            "how much of the usable safe box the element occupies - its "
            "size, as a share of the frame rather than in pixels"
        ),
        positions=(),
        resolved_against=(
            "SafeAreaInsets.centered_usable_width and the delivery "
            "format, never a pixel literal"
        ),
    ),
    Axis(
        name="entrance",
        ranges_over="the character of how the element arrives",
        positions=("cut", "fade", "slide", "scale", "mask", "draw", "blur",
                   "typewriter", "glitch", "flip"),
        resolved_against=(
            "Remotion's own interpolate/spring; naming the set is not "
            "choosing one, and the strength of the chosen one is not "
            "stated here"
        ),
    ),
    Axis(
        name="exit",
        ranges_over="the character of how the element leaves",
        positions=("cut", "fade", "slide", "scale", "mask", "draw", "blur",
                   "typewriter", "glitch", "flip"),
        resolved_against="the same as entrance; the two are declared separately",
    ),
    Axis(
        name="emphasis",
        ranges_over=(
            "how strongly the element asserts itself against the picture "
            "beneath it - its contrast, weight and shadow taken together"
        ),
        positions=(),
        resolved_against=(
            "the declaring template's or project's own standard. The "
            "captain's is 'clean, vibrant and deliberate, not "
            "distressed', and it is a declaration, not a value in this "
            "file."
        ),
    ),
    Axis(
        name="colour_role",
        ranges_over="which role of the declaring palette draws the element",
        positions=("text", "outline", "accent"),
        resolved_against=(
            "library/tools/brand_palette.roles_from_palette. A role, "
            "never a colour: the withdrawn #00D4FF reached every frame "
            "of every video as a literal in generate_motion_props."
        ),
    ),
    Axis(
        name="type_role",
        ranges_over=(
            "the typographic weight of a run of copy WITHIN the element, "
            "so a stat and its unit are distinguishable without either "
            "naming a size"
        ),
        positions=("display", "supporting", "micro"),
        resolved_against=(
            "the declaring template's style.typography, or a project's "
            "pipeline.subtitle_typography-shaped declaration; and "
            "library/tools/render_fonts.py, which refuses a typeface "
            "that will not draw the glyphs"
        ),
    ),
    Axis(
        name="copy",
        ranges_over=(
            "the text payload, as one or more named runs. WHAT it says "
            "and WHERE it comes from are outside this module - see "
            "COPY_SOURCE_IS_UNSET."
        ),
        positions=(),
        resolved_against="nothing in this engine; it is supplied",
    ),
    Axis(
        name="data",
        ranges_over=(
            "the non-copy payload an element needs to draw: a number, a "
            "set of labelled magnitudes, a frame position, a track"
        ),
        positions=(),
        resolved_against=(
            "whichever step measured it; an entry naming this axis says "
            "in `needs` which measurement, and an unmeasured one is a "
            "refusal rather than an invented value"
        ),
    ),
    Axis(
        name="asset",
        ranges_over="a project-supplied file the element draws",
        positions=(),
        resolved_against=(
            "the project's own brand_assets/, staged verbatim. AGENTS.md "
            "section 14: artwork is a project asset and the engine ships "
            "none."
        ),
    ),
)

AXES_BY_NAME: dict[str, Axis] = {a.name: a for a in AXES}


# ── Functions in the edit ────────────────────────────────────────────
#
# The register an element belongs to.  This exists to make clumping
# VISIBLE.  The SFX library is the warning: 41 of its 78 entries are
# `emotional_temperature: cold tense`, so two thirds of a catalogue that
# looks large is one register and the useful sounds are hard to find.
# `register_spread()` reports the same statistic for this roster, and the
# test fails when any one function holds more than a third of it.

FUNCTIONS: dict[str, str] = {
    "identify": (
        "Names a person, a place, a source or the piece itself - "
        "something the viewer would otherwise have to ask."
    ),
    "quantify": (
        "Puts a number or a magnitude on screen because the speech "
        "states one and the picture does not show it."
    ),
    "enumerate": (
        "Makes a structure the speech implies visible and countable."
    ),
    "point": (
        "Directs the eye at something that is already in the frame."
    ),
    "quote": (
        "Shows copy that is NOT the speaker's live words, for the "
        "viewer to read."
    ),
    "punctuate": (
        "Marks an instant - a beat, an impact, a cut - with no semantic "
        "content of its own."
    ),
    "persist": (
        "Chrome that holds across the piece rather than saying anything "
        "at a moment."
    ),
}


# ── Reachability ─────────────────────────────────────────────────────

#: The current renderer draws it today.
REACHABLE_NOW = "reachable_now"

#: The element is defined and the renderer cannot draw it. This is
#: reported, never used to decide membership - the render path is broken
#: and being repaired separately, and a vocabulary written around a
#: defect keeps the defect after the repair lands.
NEEDS_RENDERER_WORK = "needs_renderer_work"

#: The renderer could draw it, and the pipeline does not measure the
#: input it needs. Distinct from the line above, because the work is in a
#: different half of the system.
NEEDS_MEASUREMENT = "needs_measurement"

REACHABILITY = (REACHABLE_NOW, NEEDS_RENDERER_WORK, NEEDS_MEASUREMENT)


@dataclass(frozen=True)
class MotionElement:
    """One element the pipeline may plan.

    Attributes:
        key: What a plan or a declaration names.
        function: Its register, a key of :data:`FUNCTIONS`.
        what_it_is: The element, in one sentence, with no magnitudes.
        earns_its_place: The question the viewer would otherwise be left
            with. An element that answers no question is decoration, and
            decoration goes in `persist` where it is declared as chrome
            rather than smuggled in as meaning.
        needs: What has to exist before it can be drawn - the axes it
            cannot be declared without, and any measurement.
        never: What it must not be used for. Stated as refusals, because
            a vocabulary entry that only says what a thing is teaches a
            model to reach for it everywhere.
        axes: The axis names it is declared on. Every one must be in
            :data:`AXES`.
        copy: "required", "optional" or "none" - whether the element
            needs a text payload. NOT where that text comes from.
        reachable: One of :data:`REACHABILITY`.
        reachability_note: What specifically is missing, when it is not
            reachable now.
    """

    key: str
    function: str
    what_it_is: str
    earns_its_place: str
    needs: str
    never: tuple[str, ...]
    axes: tuple[str, ...]
    copy: str
    reachable: str
    reachability_note: str = ""


#: The roster. Nineteen entries across seven functions.
#:
#: **What the size was aimed at.** Small enough that the whole table fits
#: in a prompt and nothing has to be shortlisted - whatever selects a
#: shortlist becomes the chooser (AGENTS.md section 10.5) - and spread
#: widely enough that no register carries the roster. The aim was one
#: entry per QUESTION an editor answers with a graphic, not one entry per
#: shape a component can draw: `stat_callout` and `counter_roll` are two
#: entries because a number that holds and a number that changes are two
#: decisions, while a rectangle and a rounded rectangle are one.
ROSTER: tuple[MotionElement, ...] = (
    # ── identify ─────────────────────────────────────────────────────
    MotionElement(
        key="title_lockup",
        function="identify",
        what_it_is=(
            "One or two runs of copy entering and leaving as a single "
            "unit, naming the piece or the section the viewer is in."
        ),
        earns_its_place=(
            "At an opening or a hard structural pivot, to establish a visual "
            "anchor for the topic. It earns its place alongside speech only "
            "when the title distills a complex spoken concept into a short, "
            "memorable phrase the viewer needs to retain."
        ),
        needs=(
            "copy for each run; an anchor and a timing; a colour role "
            "and a type role per run"
        ),
        never=(
            ("As chrome that holds for the whole piece - that is "
             "channel_bug or frame_accents, declared as chrome."),
            ("To blindly duplicate the caption rail. A title must distill "
             "the topic into a short phrase or name, not act as a second "
             "caption rail transcribing a full sentence."),
            ("As a substitute for a hook. A title tells the viewer "
             "where they are; it does not make them stay."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "This is the existing upper third. It draws, and it has "
            "never carried copy: creative_direction's schema has no "
            "title, subtitle, series_name or episode_label field, which "
            "is the copy-source decision, not a renderer defect."
        ),
    ),
    MotionElement(
        key="lower_third",
        function="identify",
        what_it_is=(
            "An attribution block naming a person, an organisation or a "
            "source, held while they are on screen."
        ),
        earns_its_place=(
            "The first time a speaker or a source appears and the audio "
            "does not name them - the single most common reason a "
            "viewer of client work asks 'who is this'."
        ),
        needs=(
            "copy for name and role; an anchor in the lower band; a "
            "timing tied to the block the subject appears in"
        ),
        never=(
            ("For the primary speaker of a self-shot monologue on "
             "their own channel: it names somebody the viewer already "
             "knows."),
            ("Below the platform's caption rail - safe_area.py's "
             "bottom inset is the largest of the four for exactly this "
             "reason."),
            ("Overlapping a caption card. In a vertical frame the "
             "captions own the lower band; a lower third that collides "
             "with one has to move up or not be drawn."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "MotionGraphics/index.tsx draws it, and the caption-collision "
            "rule this entry was waiting on is "
            "library/tools/caption_band.py: an element drawing copy into "
            "the band this project's captions occupy, over a span they "
            "occupy it, is dropped as collides_with_the_caption_band. "
            "Both halves are joined by "
            "library/tools/render_capability_index.py, which is what "
            "found this flag still saying `No component` after the "
            "component had been written."
        ),
    ),
    MotionElement(
        key="context_stamp",
        function="identify",
        what_it_is=(
            "A deliberately small, secondary mark carrying a place, a "
            "time or a condition."
        ),
        earns_its_place=(
            "A cutaway changes place or time and the speech does not "
            "say so, leaving the viewer to work out that the shot moved."
        ),
        needs=(
            "a short copy run; an anchor and a timing tied to the "
            "placement it labels"
        ),
        never=(
            ("On every cutaway. Repeated on each placement it stops "
             "being information and becomes chrome nobody declared."),
            ("To carry a sentence. It is a stamp; copy that needs "
             "reading is quote_card."),
            ("To assert a fact the pipeline did not measure or the "
             "project did not declare - a place name nobody supplied "
             "is an invention on screen."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note="Implemented as a translucent panel in the upper corner. It assumes the title safe band is clear of primary visual interest.",
    ),

    MotionElement(
        key="subject_emblem",
        function="identify",
        what_it_is=(
            "A large flat mark standing in for what the speech is about "
            "at that moment - a currency sign while money is discussed - "
            "drawn from type and shapes, never fetched."
        ),
        earns_its_place=(
            "The speech is about something the picture does not show, "
            "and hearing about it while seeing nothing of it leaves the "
            "viewer with a sentence and no image. A mark that arrives on "
            "the word gives the subject a shape for as long as the point "
            "holds."
        ),
        needs=(
            "the subject as free text, for the record; the mark itself "
            "as copy; a timing anchored to the words that say it - an "
            "anchor phrase the engine searches the measured timings for, "
            "or timeline seconds the plan states"
        ),
        never=(
            ("For a specific real thing - a face, a logo, a product, a "
             "place. A mark stands in for a subject; portraying one is "
             "illustration, and illustration is artwork a project "
             "supplies."),
            ("As a second caption rail. Copy here is a mark and a short "
             "label, never a sentence the captions are already showing."),
            ("Fetched from anywhere. The engine draws the mark from "
             "type and shapes; a picture from outside the run is a "
             "project asset, staged verbatim or not at all."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Drawn as a flat backplate in the plan's own colour with the "
            "display run set large over a supporting label. The colour "
            "the plan states is the whole of the look; the shape is the "
            "composition's own drawing, the way a bar is what "
            "comparison_bars draws."
        ),
    ),

    # ── quantify ─────────────────────────────────────────────────────
    MotionElement(
        key="stat_callout",
        function="quantify",
        what_it_is=(
            "A figure and its label, sized to be the dominant graphic "
            "for the moment it holds."
        ),
        earns_its_place=(
            "The speech states a number the point rests on, and the "
            "picture does not show it. A spoken figure is heard once; a "
            "drawn one is read for as long as it holds."
        ),
        needs=(
            "the figure and its label as copy; a timing anchored to "
            "when it is said"
        ),
        never=(
            ("For a number the viewer does not have to retain. Drawing "
             "every figure teaches the viewer to ignore all of them."),
            ("More than one on screen at once: two dominant graphics "
             "have no dominant graphic."),
            ("For a figure the speech does not actually state. The "
             "graphic asserts it, and nothing downstream checks it."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note="Implemented as a bold text graphic. It assumes the background provides sufficient contrast, as it currently lacks an opaque backplate.",
    ),
    MotionElement(
        key="counter_roll",
        function="quantify",
        what_it_is=(
            "A figure that animates from one value to another across "
            "its life."
        ),
        earns_its_place=(
            "The CHANGE is the point - growth, a countdown, an elapsed "
            "quantity - and a static figure would show only its end."
        ),
        needs=(
            "a start value, an end value and a format, as data; a "
            "timing whose hold is the roll"
        ),
        never=(
            ("On a figure that does not change. A roll on a static "
             "number is animation for its own sake, and the entry for "
             "that is stat_callout."),
            ("Running longer than the sentence that motivates it: the "
             "viewer reads the end value and waits."),
            ("Where the intermediate values are fabricated. Rolling 0 "
             "to a stated total is a shape; rolling through "
             "measurements nobody took is a chart of nothing."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy", "data"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Implemented in Remotion. data.start_value and data.end_value "
            "drive the roll with cubic ease-out; copy runs carry the label. "
            "tabular-nums keeps digit widths stable across the animation."
        ),
    ),
    MotionElement(
        key="digit_counter",
        function="quantify",
        what_it_is=(
            "A figure whose individual digits roll independently into "
            "place using spring physics, creating a mechanical odometer "
            "effect."
        ),
        earns_its_place=(
            "The same purpose as counter_roll - the CHANGE is the point - "
            "but with a different visual character: each digit springs "
            "to its final position, and the least significant digits "
            "settle before the most significant ones."
        ),
        needs=(
            "an end value and a format, as data; a timing whose hold is "
            "the roll"
        ),
        never=(
            ("On a figure that does not change. A roll on a static "
             "number is animation for its own sake, and the entry for "
             "that is stat_callout."),
            ("Where the intermediate values are fabricated. Rolling "
             "through measurements nobody took is a chart of nothing."),
            ("When the digits are too many to read at the frame size. "
             "A twelve-digit number that rolls at phone scale is an "
             "animation of nothing the viewer sees."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy", "data"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Implemented in Remotion with per-digit spring rolling: each "
            "digit of the target number rolls independently using "
            "Remotion spring() physics with staggered delays. Non-digit "
            "characters (commas, dots) render static. Inspired by "
            "Remotion Bits AnimatedCounter "
            "(MIT, github.com/av/remotion-bits)."
        ),
    ),
    MotionElement(
        key="comparison_bars",
        function="quantify",
        what_it_is=(
            "Two or more labelled magnitudes drawn at proportional "
            "length, appearing in the order they are spoken."
        ),
        earns_its_place=(
            "The speech compares quantities and the RELATION is the "
            "point. A ratio is hard to hear and immediate to see."
        ),
        needs="the labelled magnitudes as data; labels as copy; a timing",
        never=(
            ("As a chart. Axes, gridlines and tick labels do not read "
             "at arm's length on a vertical phone frame."),
            ("For more series than the safe box holds at a legible "
             "size - the constraint is the frame, and it is small."),
            ("Where the magnitudes are not comparable, or where one is "
             "estimated and the others measured. The drawing says they "
             "are the same kind of thing."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy", "data"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Implemented in Remotion. data.values drives the proportional "
            "bar widths; runs carry the labels. Each bar grows with "
            "staggered cubic ease-out in the order the runs are listed."
        ),
    ),

    # ── enumerate ────────────────────────────────────────────────────
    MotionElement(
        key="list_build",
        function="enumerate",
        what_it_is=(
            "Items appearing one at a time, each as it is said, earlier "
            "ones holding."
        ),
        earns_its_place=(
            "The speech enumerates and the viewer has to hold the whole "
            "set to follow the point. Audio gives them the last item; "
            "the build gives them all of it."
        ),
        needs=(
            "the items as ordered copy; a timing per item, anchored to "
            "when each is said"
        ),
        never=(
            ("For items the speech does not actually enumerate. A list "
             "imposes a structure, and imposing one the speaker did "
             "not use misreads them."),
            ("Longer than the safe box holds at a legible size. A list "
             "that scrolls or shrinks to fit has stopped being "
             "readable."),
            ("Where the items are not timed to the words. A set that "
             "appears all at once is a quote_card."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Implemented in Remotion. Each run is one list item with a "
            "bullet marker, revealed with staggered cubic ease-out and "
            "upward slide. Items hold once appeared."
        ),
    ),
    MotionElement(
        key="step_counter",
        function="enumerate",
        what_it_is=(
            "A positional marker saying where in a declared sequence "
            "the piece currently is."
        ),
        earns_its_place=(
            "The piece has a real, countable structure and the viewer's "
            "decision to stay depends on knowing how far through it "
            "they are."
        ),
        needs=(
            "the position and the total as data; a timing per section; "
            "an anchor"
        ),
        never=(
            ("Where the structure is not real. A counter over sections "
             "nobody declared invents an outline."),
            ("As a substitute for the list itself - it says where, not "
             "what."),
            ("Beside a progress_bar. Two elements answering 'how much "
             "is left' is one answer too many in a frame this small."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy", "data"),
        copy="optional",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Implemented in Remotion. data.position and data.total drive "
            "the display; optional copy runs label the current section. "
            "tabular-nums keeps digit widths stable."
        ),
    ),

    # ── point ────────────────────────────────────────────────────────
    MotionElement(
        key="pointer_annotation",
        function="point",
        what_it_is=(
            "A mark drawn at a position in the frame - an arrow, a "
            "ring, an underline - entering and leaving around the "
            "moment it refers to."
        ),
        earns_its_place=(
            "The speech refers to something visible and the viewer "
            "would otherwise spend the shot hunting for it."
        ),
        needs=(
            "a normalised frame position as data; a timing anchored to "
            "the reference"
        ),
        never=(
            ("Where the referent is not visible in the shot it is "
             "drawn over. An arrow pointing at nothing is worse than "
             "no arrow."),
            ("As decoration with no referent - a mark that means "
             "nothing still reads as meaning something."),
            ("On a moving referent. A static mark drifts off it within "
             "its own hold; that is tracked_label."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy", "data"),
        copy="optional",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Implemented in Remotion. The position it needs is a plan-side "
            "declaration, not a measurement, so it relies on the absolute "
            "coordinates passed in the 'data' field."
        ),
    ),
    MotionElement(
        key="tracked_label",
        function="point",
        what_it_is=(
            "A short label pinned to a subject or object and following "
            "it as it moves."
        ),
        earns_its_place=(
            "The referent moves enough that a static pointer would "
            "leave it, and naming it is what the moment needs."
        ),
        needs=(
            "a measured per-frame track as data; a short copy run; a "
            "timing bounded by the track"
        ),
        never=(
            ("On a track the pipeline did not MEASURE. An interpolated "
             "guess slides off the subject and reads as a bug, which "
             "is worse than the label's absence."),
            ("Outside the frames the track covers - a label clamped to "
             "the edge of its own track is pointing at whatever is "
             "there."),
            "For a referent a viewer can already name.",
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy", "data"),
        copy="required",
        reachable=NEEDS_MEASUREMENT,
        reachability_note=(
            "No component, and no track. object_segmentation (1.06) is "
            "in library/steps/ and not in the DAG, and "
            "docs/SUBJECT_MASKING_MEASURED.md is what SAM 2.1 cost; "
            "compute_face_presence measures a horizontal centre at 5 Hz "
            "for one largest face, which is a track for a face and not "
            "for an object."
        ),
    ),

    # ── quote ────────────────────────────────────────────────────────
    MotionElement(
        key="quote_card",
        function="quote",
        what_it_is=(
            "Copy the viewer reads that is not what the speaker is "
            "currently saying - a cited line, a message, a definition, "
            "a chapter title."
        ),
        earns_its_place=(
            "The piece refers to words that came from somewhere else. "
            "A quote card proves the citation exists and isolates it from "
            "the speaker's own voice, even when spoken aloud."
        ),
        needs="the copy and any attribution; a timing; an anchor",
        never=(
            ("To restate the speaker's own point as if it were a citation. "
             "It is for words from SOMEWHERE ELSE, though it may share the "
             "screen with captions of the speaker reading it aloud."),
            ("Carrying more text than can be read inside its own hold. "
             "The reading speed check plan_subtitles applies to "
             "captions is the same constraint here."),
            ("For words the pipeline attributes to a source nobody "
             "supplied - the card presents them as a quotation."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "type_role", "copy"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Deliverable through the timed_text_overlay route - "
            "library/tools/timed_text_overlay.py, rendered in 4.06 and "
            "placed on V6 - which already times a moment from the spine "
            "and refuses a card centred in the platform's UI band. That "
            "route takes its copy from a project declaration, which is "
            "one answer to the open copy question and not the only one."
        ),
    ),

    # ── punctuate ────────────────────────────────────────────────────
    MotionElement(
        key="beat_accent",
        function="punctuate",
        what_it_is=(
            "A shape or burst that arrives and leaves inside a very "
            "short span, carrying no semantic content."
        ),
        earns_its_place=(
            "An accent the edit is already making - a downbeat, a "
            "planned cut, an impact - lands harder with something in "
            "the picture acknowledging it."
        ),
        needs=(
            "an instant to land on, from the beat grid or a planned "
            "cut; an anchor and a footprint"
        ),
        never=(
            ("On an instant nothing in the edit is marking. A hit with "
             "no cut and no beat under it is noise."),
            ("To emphasise a spoken word - that is the caption "
             "system's job and it is already doing it."),
            ("At a cadence. Once it becomes regular it is chrome, and "
             "chrome is declared as chrome."),
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role"),
        copy="none",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Implemented as an animated colour flash. It relies on the "
            "plan's exact timing rather than listening to the beat grid "
            "at render time."
        ),
    ),

    # ── persist ──────────────────────────────────────────────────────
    MotionElement(
        key="progress_bar",
        function="persist",
        what_it_is=(
            "A bar showing how far through the piece the viewer is, "
            "advancing across its whole length."
        ),
        earns_its_place=(
            "A piece long enough that 'how much is left' is the "
            "question deciding whether the viewer stays."
        ),
        never=(
            ("Where the platform already draws one - the master serves "
             "three platforms and two of them do."),
            ("As an accent. It is an instrument, and styling it into "
             "decoration makes it unreadable as both."),
            ("Beside a step_counter, for the same reason the counter "
             "may not sit beside it."),
        ),
        needs=(
            "the timeline's own length, from the spine "
            "(library/tools/timeline_duration.py); an anchor and a "
            "footprint"
        ),
        axes=("anchor", "footprint", "emphasis", "colour_role"),
        copy="none",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Drawn today, behind effect.motion_progress_bar. Its bar "
            "height and its track are literals in the composition, "
            "which is the footprint axis missing rather than the "
            "element missing."
        ),
    ),
    MotionElement(
        key="frame_accents",
        function="persist",
        what_it_is=(
            "Geometry held at the frame's edges - brackets, rules, a "
            "border - carrying no message."
        ),
        earns_its_place=(
            "A series wants a visual signature a viewer recognises "
            "across episodes before anybody has spoken."
        ),
        never=(
            ("To signal anything. It says nothing, and a viewer who "
             "reads it as meaning is being misled."),
            ("Inside the platform's keep-clear band, where it sits "
             "under the interaction rail and is never seen - the "
             "withdrawn 60px literal did exactly this."),
            "Crowding the caption box. Chrome yields to the copy.",
        ),
        needs="an anchor set and a footprint; a colour role",
        axes=("anchor", "footprint", "emphasis", "colour_role",
              "entrance", "exit"),
        copy="none",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Drawn today, behind effect.motion_accents, as four corner "
            "brackets at one fixed size and stroke. The element is "
            "reachable; the anchor and footprint axes are not."
        ),
    ),
    MotionElement(
        key="channel_bug",
        function="persist",
        what_it_is=(
            "A small persistent mark identifying whose video this is, "
            "drawn from a project-supplied asset."
        ),
        earns_its_place=(
            "Client work that will be reposted, and any piece whose "
            "provenance has to survive being cropped or re-uploaded."
        ),
        never=(
            ("Drawn from anything this engine ships. The engine holds "
             "no artwork and states no identity (AGENTS.md section "
             "14)."),
            ("In the platform's keep-clear band, where the platform's "
             "own handle already sits."),
            ("At a footprint that competes with the content. A bug "
             "that is noticed is too large."),
        ),
        needs=(
            "an asset from the project's brand_assets/, staged "
            "verbatim; an anchor and a footprint"
        ),
        axes=("anchor", "footprint", "emphasis", "asset"),
        copy="none",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "MotionGraphics/index.tsx draws it, sized as a share of the "
            "safe-area width. The staging half was already here - "
            "remotion_brand_linker.link_brand_assets copies a project's "
            "brand_assets/ into Remotion's public/brand/ - and "
            "generate_motion_props.project_asset_resolver is what turns "
            "a NAMED file into a staged path, lazily, so a run planning "
            "no asset stages nothing. The engine still ships no artwork: "
            "an entry naming a file the project does not have is dropped "
            "as asset_not_found_on_disk."
        ),
    ),
    MotionElement(
        key="review_panel",
        function="quote",
        what_it_is=(
            "A mockup of a review listing, drawn in the visual language "
            "of the place the reviews were written on: an overall score "
            "beside its star rating and a count, then rows carrying who "
            "wrote each review and a line of what they wrote."
        ),
        earns_its_place=(
            "The speech points at reviews and the picture never shows "
            "them. A listing the viewer already recognises proves the "
            "thing being argued about exists as they know it, where "
            "naming it only asks them to remember it."
        ),
        never=(
            ("Fetched, scraped or screenshotted at render time. The "
             "engine never reads the web: a fetch needs the network, "
             "answers differently when it is repeated, and puts "
             "somebody's live words on screen with nobody accountable "
             "for them. What is drawn is what the declaration states."),
            ("Attributed to a real named business whose reviews these "
             "are not. A listing carrying a real name is read as a "
             "record of that place, so inventing rows under one is "
             "fabricating a record rather than illustrating a point."),
            ("Carrying a score the rows do not support, or a count the "
             "listing is not of. The drawing asserts that the score is "
             "of that many reviews of that place."),
            ("Carrying more rows than can be read inside its own hold. "
             "The reading-speed constraint plan_subtitles applies to "
             "captions is the same constraint here."),
            ("Standing in for a page capture. A capture of a real page "
             "is website_panel, which takes a project's own file; this "
             "is drawn, and drawn is not the same claim."),
        ),
        needs=(
            "the score, the count and the rows as data; the place and "
            "the listing's label as copy; the palette the listing is "
            "set in, as data; an anchor and a timing"
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "type_role", "copy", "data"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "MotionGraphics/index.tsx draws the card, the header and "
            "each row, with the stars as its own drawn shapes rather "
            "than a glyph a typeface may not carry. The payload and the "
            "refusal that guards it are library/tools/review_panel.py, "
            "and motion_graphics_plan.resolve_plan drops an entry whose "
            "data cannot be drawn instead of rendering a blank card."
        ),
    ),

    MotionElement(
        key="website_panel",
        function="quote",
        what_it_is=(
            "A capture of a page the speech is talking about, framed in "
            "a drawn browser chrome and composited with alpha, so the "
            "viewer can read what the speaker is describing."
        ),
        earns_its_place=(
            "Moments when the speech points at something on a page that "
            "the picture does not show. Without it the viewer hears "
            "about a page they never see."
        ),
        never=(
            ("Fetched live at render time. The engine never screenshots "
             "the web: a fetch needs the network, answers differently "
             "when it is repeated, and draws artwork nobody supplied. "
             "The capture is a project asset, taken by whoever publishes "
             "the video, and staged verbatim."),
            ("Full frame. A panel that covers the picture replaces it, "
             "and replacing the picture is a full-frame element or a "
             "bookend, owned elsewhere."),
            ("Addressed to a page the capture is not of. The chrome "
             "shows the address the plan states; stating one the "
             "capture was not taken of mislabels what is on screen."),
        ),
        needs=(
            "a still capture from the project's brand_assets/, staged "
            "verbatim; an anchor, a timing and a footprint; a colour "
            "role or a stated colour for the chrome"
        ),
        axes=("timing", "anchor", "footprint", "entrance", "exit",
              "emphasis", "colour_role", "copy", "asset"),
        copy="optional",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "MotionGraphics/index.tsx draws the chrome and the capture: "
            "the address bar shows the plan's copy when it states any, "
            "and the capture loads through staticFile from public/brand/, "
            "where remotion_brand_linker stages stills. A video capture "
            "is not staged - the linker copies stills only - so a panel "
            "moves across its entrance and exit alone, never within."
        ),
    ),
)

ELEMENTS_BY_KEY: dict[str, MotionElement] = {e.key: e for e in ROSTER}


# ── The boundary ─────────────────────────────────────────────────────

#: Things that look like motion graphics and are owned elsewhere. Kept
#: so the withdrawal is visible and a planner naming one is told which
#: enumeration owns it, rather than getting "unknown element".
OUT_OF_VOCABULARY: dict[str, str] = {
    "caption_word_emphasis": (
        "A treatment of the spoken word belongs to the caption system. "
        "library/tools/subtitle_style.py resolves it and "
        "plan_subtitles times it; drawing it twice would put two "
        "renderings of one sentence on one frame."
    ),
    "kinetic_typography": (
        "Same boundary as caption_word_emphasis: copy animated word by "
        "word IS the caption track, at a different intensity. Widening "
        "the caption style is the change, not a second element that "
        "draws over it."
    ),
    "shape_wipe_transition": (
        "A shape that reveals the next shot is a transition, and "
        "library/tools/transition_vocabulary.py is the one enumeration "
        "of those. Note also that a per-clip Fusion comp sees only its "
        "own clip (AGENTS.md section 5)."
    ),
    "zoom_emphasis": (
        "A treatment of the PICTURE, not an overlay on it. "
        "step_4_03_plan_vfx plans it and "
        "library/tools/fusion/comp_builder.build_effect_comp draws it."
    ),
    "screen_shake": (
        "Also a picture treatment - see zoom_emphasis. An additive "
        "overlay cannot move the frame beneath it."
    ),
    "light_leak": (
        "A picture treatment, and withdrawn there too for want of an "
        "asset library - transition_vocabulary.WITHDRAWN records it."
    ),
    "intro_card": (
        "A full frame of artwork with its own duration is a bookend. "
        "library/tools/bookends.py owns the declaration and "
        "mesh_spine turns it into a V1 block, which is what puts it "
        "inside the coverage assertion and the manifest duration."
    ),
    "end_card": (
        "Same as intro_card: a bookend, declared project-side."
    ),
    "emoji_sticker": (
        "Artwork. The engine ships none and must state none "
        "(AGENTS.md sections 11 and 14); a project supplies its own and "
        "channel_bug is the entry that draws a supplied asset."
    ),
    "watermark_tile": (
        "A repeated identity mark across the frame is channel_bug at a "
        "footprint nobody should declare, not a separate element."
    ),
}


#: This module names no producer of copy, and must not grow one.
#:
#: What a motion graphic SAYS, and where those words come from, is an
#: open captain decision as of 2026-08-29. Every entry above declares
#: only WHETHER it needs a text payload. A model writing the copy, a
#: project declaring it, a transcript supplying it and a brand template
#: carrying it all satisfy the same entry, and the roster is unchanged
#: under each. The same holds for the second open decision - whether the
#: model authors a component per video or fills a props schema - because
#: an entry names a kind, its axes, its inputs and its refusals, and
#: those are the schema under one answer and the brief under the other.
COPY_SOURCE_IS_UNSET = (
    "copy_source",
    "component_authoring",
)


# ── Rules, as callable statements ────────────────────────────────────

class MotionVocabularyError(ValueError):
    """A roster entry, or a plan naming one, that this module refuses."""


def canonical_key(raw) -> str | None:
    """The roster key for `raw`, or None when it is not in the roster."""
    key = str(raw or "").strip().lower()
    return key if key in ELEMENTS_BY_KEY else None


def refusal_reason(raw) -> str:
    """Why `raw` is not plannable. Empty when it is."""
    key = str(raw or "").strip().lower()
    if key in ELEMENTS_BY_KEY:
        return ""
    if key in OUT_OF_VOCABULARY:
        return OUT_OF_VOCABULARY[key]
    return (
        f"{raw!r} is not a motion-graphics element this pipeline knows. "
        f"Plannable elements: {', '.join(sorted(ELEMENTS_BY_KEY))}."
    )


def register_spread() -> dict[str, int]:
    """How many entries sit in each function. The clumping statistic."""
    counts = {name: 0 for name in FUNCTIONS}
    for element in ROSTER:
        counts[element.function] += 1
    return counts


def assert_roster_is_well_formed() -> None:
    """Every entry names a known function, known axes and a known reachability.

    Raised rather than warned: a misspelt axis is an element declared on
    a dimension nothing resolves, which draws at whatever the renderer
    happens to do - the silent-unread-key failure of AGENTS.md 10.2.
    """
    seen: set[str] = set()
    for element in ROSTER:
        if element.key in seen:
            raise MotionVocabularyError(f"{element.key} appears twice.")
        seen.add(element.key)
        if element.function not in FUNCTIONS:
            raise MotionVocabularyError(
                f"{element.key} names function {element.function!r}, which "
                f"is not in FUNCTIONS.")
        if element.reachable not in REACHABILITY:
            raise MotionVocabularyError(
                f"{element.key} names reachability {element.reachable!r}.")
        if element.reachable != REACHABLE_NOW and not element.reachability_note:
            raise MotionVocabularyError(
                f"{element.key} is not reachable and says nothing about "
                f"what is missing.")
        if element.copy not in ("required", "optional", "none"):
            raise MotionVocabularyError(
                f"{element.key} declares copy={element.copy!r}; it must be "
                f"required, optional or none.")
        if not element.never:
            raise MotionVocabularyError(
                f"{element.key} records no refusals. An entry that only "
                f"says what a thing is teaches a model to reach for it "
                f"everywhere.")
        for axis in element.axes:
            if axis not in AXES_BY_NAME:
                raise MotionVocabularyError(
                    f"{element.key} is declared on axis {axis!r}, which is "
                    f"not in AXES.")
        if element.copy != "none" and "copy" not in element.axes:
            raise MotionVocabularyError(
                f"{element.key} needs copy and is not declared on the copy "
                f"axis.")
        if element.copy == "none" and "copy" in element.axes:
            raise MotionVocabularyError(
                f"{element.key} declares copy=none and names the copy axis.")
    for key in OUT_OF_VOCABULARY:
        if key in ELEMENTS_BY_KEY:
            raise MotionVocabularyError(
                f"{key} is both in the roster and out of vocabulary.")


def assert_no_settled_values() -> None:
    """No axis carries a default or a bound.

    The structural half of the no-hardcoded-values rule: an `Axis` has
    nowhere to put a magnitude, and this asserts the dataclass has not
    grown one. The textual half - that no colour, duration, size or
    intensity literal appears in the table's prose either - is enforced
    against this file's own source in
    `tests/test_motion_graphics_vocabulary.py`, because that is the
    place a value would actually reappear.
    """
    forbidden = {"default", "minimum", "maximum", "bound", "range",
                 "value", "strength", "intensity"}
    fields = set(Axis.__dataclass_fields__)
    offending = sorted(fields & forbidden)
    if offending:
        raise MotionVocabularyError(
            f"Axis has grown {offending}, which is a place to put a "
            f"magnitude. An axis is a dimension; the magnitude belongs "
            f"to whoever declares it.")
    for axis in AXES:
        if not axis.ranges_over or not axis.resolved_against:
            raise MotionVocabularyError(
                f"axis {axis.name!r} does not say what it ranges over or "
                f"what it resolves against.")


# ── The prompt-side route ────────────────────────────────────────────

#: What each column of `roster_rows()` MEANS. Handed to a model beside
#: the table, the way `music_measurement.MEASUREMENT_LEGEND` is - it
#: defines the columns and never says what to conclude from them. A
#: planning step's handoff can then name the table without this module
#: knowing anything about prompts.
ROSTER_LEGEND: dict[str, str] = {
    "element": "The key a plan names.",
    "function": "What it does in the edit; see the function legend.",
    "what_it_is": "The element in one sentence.",
    "earns_its_place": (
        "The question the viewer is left with if it is absent. An "
        "element that answers no question here does not belong in the "
        "plan."
    ),
    "needs": "What must exist before it can be drawn.",
    "never": "What it must not be used for, as refusals.",
    "axes": (
        "The dimensions a declaration must fill. None of them has a "
        "value here - the magnitude is the plan's to choose."
    ),
    "copy": (
        "required / optional / none - whether the element needs a text "
        "payload. Where that text comes from is not stated."
    ),
    "reachable": (
        "reachable_now / needs_renderer_work / needs_measurement - a "
        "report on the renderer, not a filter on the vocabulary."
    ),
}


def roster_rows(include_unreachable: bool = True) -> list[dict]:
    """The roster as rows, for a bridge to serialise into a prompt table.

    `include_unreachable` exists for a caller that has to plan against
    today's renderer. It defaults to the WHOLE roster: nineteen entries
    fit, nothing needs shortlisting, and whatever selects a shortlist
    becomes the chooser (AGENTS.md section 10.5).
    """
    rows = []
    for element in ROSTER:
        if not include_unreachable and element.reachable != REACHABLE_NOW:
            continue
        rows.append({
            "element": element.key,
            "function": element.function,
            "what_it_is": element.what_it_is,
            "earns_its_place": element.earns_its_place,
            "needs": element.needs,
            "never": " | ".join(element.never),
            "axes": ", ".join(element.axes),
            "copy": element.copy,
            "reachable": element.reachable,
        })
    return rows


def axis_rows() -> list[dict]:
    """The axes as rows, so a plan can be told what it is filling in."""
    return [{
        "axis": axis.name,
        "ranges_over": axis.ranges_over,
        "positions": ", ".join(axis.positions) if axis.positions else "",
        "continuous": axis.continuous,
        "resolved_against": axis.resolved_against,
    } for axis in AXES]


def describe_roster() -> str:
    """The roster, the axes and the spread, as text for a report."""
    lines = ["MOTION-GRAPHICS ROSTER", ""]
    for name, meaning in FUNCTIONS.items():
        members = [e.key for e in ROSTER if e.function == name]
        lines.append(f"  {name} ({len(members)}): {meaning}")
        for key in members:
            element = ELEMENTS_BY_KEY[key]
            lines.append(f"      - {key}  [{element.reachable}] "
                         f"copy={element.copy}")
    lines.append("")
    lines.append(f"  {len(ROSTER)} elements, {len(FUNCTIONS)} functions, "
                 f"spread {register_spread()}")
    reach = {r: sum(1 for e in ROSTER if e.reachable == r)
             for r in REACHABILITY}
    lines.append(f"  reachability {reach}")
    lines.append("")
    lines.append("AXES (a dimension each; no defaults, no bounds)")
    for axis in AXES:
        kind = ("enumerated: " + ", ".join(axis.positions)
                if axis.positions
                else "open - no positions; the magnitude or payload is "
                     "the declaring author's")
        lines.append(f"  {axis.name}: {axis.ranges_over}")
        lines.append(f"      {kind}")
    lines.append("")
    lines.append("OUT OF VOCABULARY (owned by another enumeration)")
    for key in sorted(OUT_OF_VOCABULARY):
        lines.append(f"  {key}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import sys as _sys
    argv = list(_sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        assert_roster_is_well_formed()
        assert_no_settled_values()
        print("roster well formed; no axis carries a default or a bound")
        return 0
    print(describe_roster())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
