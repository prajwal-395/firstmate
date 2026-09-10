"""What a VISUAL COMPONENT is, and the plan of action that drives one.

The span planner (`library/tools/reel_semantic_visual.py`) decides WHAT
EACH BEAT SHOWS: free text naming a noun, anchored to the words that
say it.  It produced fourteen beats for reel 40 - "raised hands asking
the room", "five sources that cannot be reached", "a bad information
ecosystem".  Nothing consumed them, so what reached the screen was
TYPE: the noun set as words and animated in.

The captain's reading of that, 2026-09-09:

    "im not trying to animate text on the screen, im trying to give
    something visual for the viewer to look at that is paired alongside
    the audio.  and i think you understood that in concept, but not to
    the fidelity i want.  i think in some aspect it requires creating a
    plan of action for each component you want to have animated
    throughout the video"

This module is the layer that answers the second sentence.  A beat says
what is shown; a COMPONENT is the thing on screen that shows it, and its
PLAN OF ACTION is the ordered list of acts it performs while the voice
continues.  `docs/VISUAL_COMPONENT_ANATOMY.md` is the written account -
the reference's three components and four of our own beats, taken apart
into what the thing is, what it is made of, how it enters, what it does,
how it leaves, and what the next one does with the space.

WHERE THIS SITS

- ABOVE it: `reel_semantic_visual` span beats (the noun and its words).
- BESIDE it: `motion_graphics_vocabulary` is the OVERLAY roster - edge
  decoration composited over footage that keeps playing (AGENTS.md 16).
  A component here OWNS the frame; the picture IS the animation.  The
  boundary is the same one `full_frame_element` draws.
- BELOW it: `remotion-subtitles/src/compositions/StagedScene` - a world,
  a camera and five primitives (image, text, light, disc, rule).  A
  resolved component becomes StagedScene layers and nothing else.

WHAT THE MODEL DECIDES, AND WHAT IT MAY NEVER DECIDE

The model decides the COMPONENT (which of the roster's forms carries
this noun), its PARTS (how many countable things there are - the number
the speaker said), and its ACTS (what happens, to which parts, cued to
which words).  That is the whole of the decision and it is all
structure.

It may never decide a look value.  Not a colour, not a size, not a
spacing, not a distance, not a ramp length, not a typeface, not an
opacity.  Every one of those is a magnitude, and the captain's rule of
2026-09-08 - "i want no hardcoded values.  there are no house glow
looks, there are no settled house grain or anything" - is why they live
in a per-project LOOK DECLARATION instead, the way `series_look.py`
holds nothing and `StagedScene` states nothing.  A plan entry carrying a
look key is refused as `look_value_in_component_plan`, read from the KEY
and never from the value, exactly as `reel_semantic_visual` refuses one
in a span beat.

And the engine states no magnitude either.  A look that does not state a
value an act needs REFUSES that entry as `look_states_no_value`; it
never falls back to a number this file authored, because there is none
to fall back to.  That refusal is the whole reason a component
vocabulary can exist without becoming a house look.

TIMING IS BY SEARCH, NEVER BY SECONDS

Every act names an `anchor_phrase` quoted from the segment's own
measured words and a `lead_seconds` at or above zero, and lands that far
BEFORE the phrase starts.  That is the reference's single most
transferable measurement (`docs/ANIMATION_FIRST_REFERENCE.md` §1: the
picture arrives, the voice confirms it) and it is AGENTS.md 6's rule
that a passage is anchored by search.  Explicit seconds are refused as
`timing_by_seconds`: seconds land NEAR words instead of ON them, so they
are not a second timing - they are no timing.

An act may also name a `through_phrase`, and then it COMPLETES on that
phrase's end rather than after the look's declared ramp.  A component
whose fall finishes on "accessed" is the difference between illustrating
the sentence and decorating it.

WHAT IS NOT DECIDED HERE, ON PURPOSE

Which component a given noun deserves.  The roster states what each form
CAN carry and what it must never be reached for; choosing between them
for "a bad information ecosystem" is the model's judgement, made with
the brief and the direction it already receives.  A rule here that
mapped nouns to forms would be this file taking a creative decision
(AGENTS.md 10.5).

`tests/test_visual_component_plan.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Sequence, Tuple

from library.tools.reel_semantic_visual import LOOK_KEYS


class ComponentPlanError(ValueError):
    """A component plan this module refuses outright: not a list of
    entries.  The same shape `SpanPlanError` takes for a span plan."""


# ---------------------------------------------------------------------------
# What a component can be made of, and whether that material exists today.
# ---------------------------------------------------------------------------

#: Material kinds a component can be built out of, and the plain answer
#: to "does this exist on the captain's machine today".
#:
#: This enumeration is the crux of the whole problem and it is stated
#: here rather than assumed anywhere: a Vox piece DRAWS its subjects and
#: this engine has no illustrator.  `docs/VISUAL_COMPONENT_ANATOMY.md`
#: §4 is the long form; these three lines are what the resolver acts on.
DRAWN_MARKS = "drawn_marks"
PROJECT_ASSET = "project_asset"
DEPICTIVE_SUBJECT = "depictive_subject"

MATERIALS: Dict[str, str] = {
    DRAWN_MARKS: (
        "Discs, rules, cones of light, type and the ground itself - the "
        "five primitives StagedScene draws. SUPPLIED: the renderer "
        "already has them and they need no asset. What they can carry "
        "is quantity, position, direction, containment and failure - "
        "diagram, not depiction."
    ),
    PROJECT_ASSET: (
        "A still the PROJECT declared and staged into Remotion's "
        "public/brand/ (AGENTS.md 14: artwork is a project asset and "
        "the engine ships none). SUPPLIED WHERE THE PROJECT DECLARED "
        "ONE, and refused by name where it did not - never substituted."
    ),
    DEPICTIVE_SUBJECT: (
        "A thing cut free of its own background and lit by the staging "
        "- the reference's brain and eyeball. NOT SUPPLIED. The engine "
        "has no illustrator, no icon set and no image generation; its "
        "only photographic material is frames of the person speaking, "
        "and the capability that would cut one out is "
        "`object_segmentation` (step 1.06), which is wired to nothing "
        "(docs/SUBJECT_MASKING_MEASURED.md). A component needing this "
        "is refused as `material_not_supplied` rather than drawn with "
        "a stand-in."
    ),
}

#: The material kinds a run can actually obtain. `PROJECT_ASSET` is
#: conditional and is checked per entry against what the project staged,
#: so it is not in this set: a component needing one is admitted here
#: and refused later if the asset is absent.
SUPPLIED_MATERIALS = frozenset({DRAWN_MARKS, PROJECT_ASSET})


# ---------------------------------------------------------------------------
# The roster.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Act:
    """One thing a component does, and what it cannot be drawn without.

    `needs` names LOOK VALUES - magnitudes the declaration must state.
    This file states none of them; it states only that they are needed,
    which is what turns a missing one into a refusal instead of a
    default.
    """

    name: str
    does: str
    #: The look value that is this act's OWN LENGTH in seconds, used
    #: when the act names no `through_phrase` to complete on.
    ramp: str
    #: Every other look value it cannot be drawn without.
    also_needs: Tuple[str, ...] = ()

    @property
    def needs(self) -> Tuple[str, ...]:
        return (self.ramp,) + tuple(self.also_needs)


@dataclass(frozen=True)
class Component:
    """One form a beat's picture can take.

    `never` is not optional, for the reason `motion_graphics_vocabulary`
    gives (AGENTS.md 16): an entry that only says what a thing IS
    teaches a model to reach for it everywhere.
    """

    name: str
    carries: str
    made_of: str
    part_is: str
    needs: Tuple[str, ...]
    acts: Tuple[Act, ...]
    never: Tuple[str, ...]

    def act(self, name: str) -> Act | None:
        for a in self.acts:
            if a.name == name:
                return a
        return None


COMPONENTS: Tuple[Component, ...] = (
    Component(
        name="countable_set",
        carries=(
            "A quantity the speaker said out loud, and what happens to "
            "the members of it. 'five sources that cannot be reached' "
            "is five marks, and the reaching failing is the act."
        ),
        made_of=DRAWN_MARKS,
        part_is="one mark per countable thing, laid out in one row",
        needs=("mark_colour", "mark_size", "mark_spacing", "row_y"),
        acts=(
            Act("land",
                "each named part arrives, rising into the row and "
                "reaching full opacity; a stagger across the act's own "
                "window when it names more than one part",
                "land_ramp_seconds", ("land_rise",)),
            Act("fail",
                "each named part drops and takes the failed colour and "
                "opacity - the picture of a member of the set not "
                "working",
                "fail_ramp_seconds", ("fail_fall", "failed_colour",
                                      "failed_opacity")),
            Act("dim",
                "each named part loses opacity in place, still present "
                "and no longer counted",
                "dim_ramp_seconds", ("dimmed_opacity",)),
            Act("gather",
                "the named parts close toward the row's centre, the "
                "set becoming one thing",
                "gather_ramp_seconds", ("gathered_spacing",)),
            Act("withdraw",
                "the named parts leave the frame by opacity, which is "
                "how a component gives the space back",
                "withdraw_ramp_seconds"),
        ),
        never=(
            "never for a quantity nobody said - the count is the "
            "spoken number, not an illustrator's choice of how many "
            "dots look good",
            "never as a bar chart or a proportion: comparison is "
            "`comparison_bars` in the overlay roster and belongs at an "
            "edge over footage",
            "never for a single subject - one mark is not a set, and a "
            "noun with no plurality wants `subject_on_surface`",
        ),
    ),
    Component(
        name="flow_between",
        carries=(
            "One thing going into another, and what comes out. 'bad "
            "information going in' and 'pulling from that ecosystem' "
            "are the same picture read in two directions."
        ),
        made_of=DRAWN_MARKS,
        part_is=(
            "part 1 is the source, part 2 is the sink, and every "
            "further part is one thing that travels between them"
        ),
        needs=("source_x", "source_y", "sink_x", "sink_y", "mark_colour",
               "mark_size", "endpoint_size"),
        acts=(
            Act("open",
                "the source and the sink arrive, the two ends of the "
                "sentence stated before anything crosses",
                "open_ramp_seconds"),
            Act("travel",
                "each named traveller crosses from source to sink, "
                "staggered across the act's own window",
                "travel_ramp_seconds"),
            Act("arrive",
                "the sink takes the arrived colour - the consequence, "
                "drawn on the thing that received it",
                "arrive_ramp_seconds", ("arrived_colour",)),
            Act("withdraw",
                "the named parts leave by opacity",
                "withdraw_ramp_seconds"),
        ),
        never=(
            "never as an arrow with a head - a head is a symbol with a "
            "grammar the roster has not written down",
            "never for a sequence of steps: an ordered process is "
            "`step_counter` in the overlay roster",
            "never with the source and the sink at the same place - a "
            "flow between one point and itself draws nothing",
        ),
    ),
    Component(
        name="subject_on_surface",
        carries=(
            "One depictive thing lying on the staged ground, casting "
            "the staging's own shadow: the reference's brain for "
            "'mind' and its eyeball for 'vision'."
        ),
        made_of=DEPICTIVE_SUBJECT,
        part_is="the subject, and each piece it splits into",
        needs=("subject_width", "subject_x", "subject_y", "shadow"),
        acts=(
            Act("rise", "the subject arrives out of the surface",
                "rise_ramp_seconds", ("rise_from",)),
            Act("split",
                "the subject becomes two pieces that part continuously, "
                "opening a hole the next subject rises into",
                "split_ramp_seconds", ("split_distance",)),
            Act("replace_through_defocus",
                "the subject blurs out and the next blurs in, which is "
                "the reference's only non-cut transition",
                "defocus_ramp_seconds", ("defocus_radius",)),
            Act("withdraw", "the subject leaves under a camera move",
                "withdraw_ramp_seconds"),
        ),
        never=(
            "never the speaker's own face standing in for an abstract "
            "noun - nine copies of one face is one idea repeated",
            "never a rectangular photograph with its own background "
            "intact: that reads as a clip in a frame however well the "
            "camera moves, and it is `held_card`",
        ),
    ),
    Component(
        name="held_card",
        carries=(
            "A still the project owns, framed and held - the "
            "reference's octagon-masked portrait, used exactly once."
        ),
        made_of=PROJECT_ASSET,
        part_is="the card, and each piece it splits into",
        needs=("card_width", "card_x", "card_y"),
        acts=(
            Act("settle",
                "the card fades up slightly oversized and eases to "
                "rest, which is the reference's only entrance",
                "settle_ramp_seconds", ("settle_from_scale",)),
            Act("split",
                "the card parts down its centre, the halves diverging",
                "split_ramp_seconds", ("split_distance",)),
            Act("withdraw", "the card leaves by opacity",
                "withdraw_ramp_seconds"),
        ),
        never=(
            "never engine artwork - the engine ships none and states "
            "none (AGENTS.md 14); the asset is the project's or the "
            "entry is refused",
            "never as a container for type: a card carrying words is a "
            "caption plate, and captions have their own path",
        ),
    ),
    Component(
        name="ground_turn",
        carries=(
            "The surface itself changing - the reference's light table "
            "becoming a black pitch, one frame, and a whole picture "
            "out of that alone."
        ),
        made_of=DRAWN_MARKS,
        part_is="the ground; it has no parts",
        needs=("turned_colour",),
        acts=(
            Act("invert", "the ground takes a new value",
                "invert_ramp_seconds", ("turned_colour",)),
            Act("mark",
                "markings appear on the turned ground, which is what "
                "makes the change mean something",
                "mark_ramp_seconds"),
        ),
        never=(
            "never as a flash or a strobe - a value change is a shot "
            "change here, not an accent",
        ),
    ),
)

COMPONENTS_BY_NAME: Dict[str, Component] = {c.name: c for c in COMPONENTS}


# ---------------------------------------------------------------------------
# Reachability, REPORTED per entry rather than filtering membership.
# ---------------------------------------------------------------------------

#: Why an entry cannot be drawn today, or `""` where it can.
#:
#: AGENTS.md 16's rule, applied to this roster: reachability is reported,
#: never a filter on membership. A roster written around today's builders
#: would keep its defect after the repair, and the two blockers below are
#: different problems with different owners - one needs an asset nobody
#: has, one needs renderer work.
BLOCKED: Dict[str, str] = {
    "subject_on_surface": (
        "material_not_supplied: nothing on this machine produces a "
        "subject cut free of its background. See MATERIALS["
        "'depictive_subject']."
    ),
    "held_card": (
        "no_builder_yet: the material exists where a project declares a "
        "still, and StagedScene draws an `image` layer, but no builder "
        "in this module turns a `held_card` plan into one."
    ),
    "ground_turn": (
        "renderer_cannot_draw: StagedScene's `ground.colour` is a "
        "constant, not an Animatable, so the surface cannot change "
        "value inside one scene."
    ),
}


def blocker(name: str) -> str:
    """Why component `name` cannot be drawn today, or `""`."""
    return BLOCKED.get(name, "")


def roster_rows() -> List[Dict[str, str]]:
    """The roster as prompt-side rows.

    The same shape `motion_graphics_vocabulary.roster_rows` takes, and
    the whole roster ships - nothing is shortlisted, because whatever
    selects a shortlist becomes the chooser (AGENTS.md 10.5).
    """
    return [
        {
            "component": c.name,
            "carries": c.carries,
            "part": c.part_is,
            "acts": " ".join(a.name for a in c.acts),
            "made_of": c.made_of,
            "reachable": "no" if blocker(c.name) else "yes",
            "blocker": blocker(c.name),
            "never": " | ".join(c.never),
        }
        for c in COMPONENTS
    ]


ROSTER_LEGEND = (
    "component: the form. carries: the kind of idea it can show. part: "
    "what one part of it is. acts: the verbs its plan of action may "
    "use. made_of: the material it needs. reachable/blocker: whether "
    "this run can draw it and why not. never: what it must not be "
    "reached for."
)


# ---------------------------------------------------------------------------
# What the resolver refuses.
# ---------------------------------------------------------------------------

COMPONENT_DROP_REASONS: Dict[str, str] = {
    "entry_is_not_a_mapping": (
        "The entry is not a mapping, so it names no component, no "
        "parts and no acts. Nothing is read off it."
    ),
    "unknown_component": (
        "The entry names a form that is not in the roster. The roster "
        "is the whole vocabulary and it ships in the prompt, so a name "
        "outside it was invented."
    ),
    "component_not_reachable": (
        "The component is in the roster and cannot be drawn on this "
        "run. `BLOCKED` names why - a material nobody has, a builder "
        "nobody wrote, or a renderer that cannot draw it. Reported "
        "rather than substituted for something that can."
    ),
    "material_not_supplied": (
        "The component is made of a material this machine does not "
        "produce. Drawing it with a stand-in would be the engine "
        "choosing what the viewer looks at."
    ),
    "no_parts_declared": (
        "The entry declares no parts, or fewer than one. A component "
        "with no parts draws nothing, and an overlay that draws "
        "nothing is not rendered (AGENTS.md 10.2)."
    ),
    "no_action_planned": (
        "The entry plans no acts. A thing that appears and then does "
        "nothing is a still, and a still is exactly what the captain "
        "said is not a visual component: the plan of action IS the "
        "component. Refused rather than shown once and held."
    ),
    "look_value_in_component_plan": (
        "The entry carries a key that names a look dimension - a "
        "colour, a size, a distance, a typeface, a ramp length. The "
        "plan decides WHAT HAPPENS, never how much of it, so the entry "
        "is refused rather than read past."
    ),
    "unknown_act": (
        "An act names a verb no component in the roster performs."
    ),
    "act_not_on_this_component": (
        "The act is a real verb and this component does not perform "
        "it. A component's acts are what that form can do; borrowing "
        "another form's verb would be planning a form nobody chose."
    ),
    "part_not_in_component": (
        "An act names a part index this component does not have. Parts "
        "are numbered from 1 up to the entry's declared count."
    ),
    "timing_by_seconds": (
        "An act states timeline seconds. An act is cued to its own "
        "words by SEARCH (AGENTS.md 6); seconds land near words "
        "instead of on them, so they are not a second timing - they "
        "are no timing."
    ),
    "no_anchor_declared": (
        "An act names no `anchor_phrase`. The act has nothing to land "
        "on."
    ),
    "anchor_phrase_not_found": (
        "An act's anchor phrase occurs nowhere in the beat's own "
        "measured words. The act lands on its words or not at all."
    ),
    "no_timing_declared": (
        "An act's `lead_seconds` is not a number at or above zero. A "
        "lead is how far BEFORE its words the act begins; a lag or a "
        "non-number declares no timing."
    ),
    "act_outside_beat": (
        "An act starts before the beat begins or ends after it ends. "
        "Not clamped: moving an act is choosing when the picture "
        "plays."
    ),
    "acts_out_of_order": (
        "The acts are not in the order they play. The plan of action "
        "is a sequence and reading it as one is the point; a list that "
        "has to be sorted to be understood was not planned as a "
        "sequence."
    ),
    "look_states_no_value": (
        "The look declaration states no value this component or one of "
        "its acts cannot be drawn without. The engine authors no "
        "magnitude, so there is nothing to fall back to and the entry "
        "is refused (the captain's rule of 2026-09-08)."
    ),
}


@dataclass(frozen=True)
class ResolvedAct:
    """One act with its words resolved to seconds on the reel clock."""

    do: str
    parts: Tuple[int, ...]
    start: float
    end: float
    anchor_phrase: str
    lead_seconds: float
    timing_basis: str


@dataclass(frozen=True)
class ResolvedComponent:
    """A component whose plan of action survived every refusal."""

    segment: int
    shows: str
    component: str
    parts: int
    start: float
    end: float
    acts: Tuple[ResolvedAct, ...]


def _phrase_window(phrase: str, words: Sequence[Mapping]) -> Tuple[float, float] | None:
    """The window of `phrase` in `words`, by search over occurrences.

    `reel_semantic_visual` searches a span segment the same way; this is
    the act-level case and it is deliberately the same rule - a phrase
    is found by matching its whole word run, not by the word nearest a
    hint (AGENTS.md 6).
    """
    wanted = [w for w in str(phrase or "").lower().split() if w]
    if not wanted:
        return None
    normal = [str(w.get("word") or "").lower().strip(",.!?;:\"'") for w in words]
    n = len(wanted)
    for i in range(0, len(normal) - n + 1):
        if [normal[i + k].strip(",.!?;:\"'") for k in range(n)] == [
                w.strip(",.!?;:\"'") for w in wanted]:
            try:
                return (float(words[i]["start"]), float(words[i + n - 1]["end"]))
            except (KeyError, TypeError, ValueError):
                return None
    return None


def resolve_component_plan(
    entries,
    beats: Sequence[Mapping],
    words_by_segment: Sequence[Sequence[Mapping]],
    look: Mapping[str, object],
) -> Tuple[List[ResolvedComponent], List[Dict[str, str]]]:
    """Resolve a component plan against its beats, words and look.

    `beats` are the span planner's own moments - each carrying
    `segment`, `shows`, `event_start` and `anchor_end` - and an entry
    binds to the beat with the same `segment` and `shows`.  The beat's
    window is what every act must land inside.

    Returns `(kept, dropped)`.  Every drop names a reason from
    `COMPONENT_DROP_REASONS` and the entry it refused, because a beat
    that was planned and not drawn must say so rather than going quiet
    (AGENTS.md 10.4).
    """
    if entries is None:
        raise ComponentPlanError("no component plan: None is not a list of entries")
    if isinstance(entries, Mapping) or not isinstance(entries, Sequence) or isinstance(entries, str):
        raise ComponentPlanError(
            f"component plan is {type(entries).__name__}, not a list of entries")

    by_beat = {
        (int(b.get("segment", 0)), str(b.get("shows") or "")): b
        for b in beats or [] if isinstance(b, Mapping)
    }

    kept: List[ResolvedComponent] = []
    dropped: List[Dict[str, str]] = []

    def drop(reason: str, entry, detail: str = "") -> None:
        dropped.append({
            "reason": reason,
            "why": COMPONENT_DROP_REASONS[reason],
            "entry": repr(entry)[:200],
            "detail": detail,
        })

    for entry in entries:
        if not isinstance(entry, Mapping):
            drop("entry_is_not_a_mapping", entry)
            continue

        offending = sorted(k for k in entry if str(k).lower() in LOOK_KEYS)
        if offending:
            drop("look_value_in_component_plan", entry, ", ".join(offending))
            continue

        name = str(entry.get("component") or "")
        component = COMPONENTS_BY_NAME.get(name)
        if component is None:
            drop("unknown_component", entry, name)
            continue
        if component.made_of not in SUPPLIED_MATERIALS:
            drop("material_not_supplied", entry,
                 f"{name} is made of {component.made_of}")
            continue
        if blocker(name):
            drop("component_not_reachable", entry, blocker(name))
            continue

        try:
            parts = int(entry.get("parts"))
        except (TypeError, ValueError):
            parts = 0
        if parts < 1:
            drop("no_parts_declared", entry, repr(entry.get("parts")))
            continue

        segment = int(entry.get("segment", 0) or 0)
        shows = str(entry.get("shows") or "")
        beat = by_beat.get((segment, shows))
        if beat is None:
            drop("anchor_phrase_not_found", entry,
                 f"no beat for segment {segment} shows {shows!r}")
            continue
        try:
            beat_start = float(beat["event_start"])
            beat_end = float(beat["anchor_end"])
        except (KeyError, TypeError, ValueError):
            drop("anchor_phrase_not_found", entry, "beat carries no window")
            continue

        acts = entry.get("acts")
        if not isinstance(acts, Sequence) or isinstance(acts, str) or not acts:
            drop("no_action_planned", entry, repr(acts)[:80])
            continue

        index = segment - 1
        words = (words_by_segment[index]
                 if 0 <= index < len(words_by_segment) else [])

        missing_look = [v for v in component.needs if v not in look]
        if missing_look:
            drop("look_states_no_value", entry, ", ".join(missing_look))
            continue

        resolved_acts: List[ResolvedAct] = []
        refused = False
        for act in acts:
            if not isinstance(act, Mapping):
                drop("entry_is_not_a_mapping", entry, repr(act)[:80])
                refused = True
                break

            verb = str(act.get("do") or "")
            spec = component.act(verb)
            if spec is None:
                known = any(c.act(verb) for c in COMPONENTS)
                drop("act_not_on_this_component" if known else "unknown_act",
                     entry, f"{name}.{verb}")
                refused = True
                break

            missing = [v for v in spec.needs if v not in look]
            if missing:
                drop("look_states_no_value", entry,
                     f"{verb} needs {', '.join(missing)}")
                refused = True
                break

            if any(k in act for k in ("start", "end", "at", "seconds",
                                      "start_seconds", "timeline_start")):
                drop("timing_by_seconds", entry, verb)
                refused = True
                break

            phrase = str(act.get("anchor_phrase") or "")
            if not phrase:
                drop("no_anchor_declared", entry, verb)
                refused = True
                break

            try:
                lead = float(act.get("lead_seconds"))
            except (TypeError, ValueError):
                lead = float("nan")
            if not (lead >= 0.0):
                drop("no_timing_declared", entry,
                     f"{verb} lead_seconds={act.get('lead_seconds')!r}")
                refused = True
                break

            window = _phrase_window(phrase, words)
            if window is None:
                drop("anchor_phrase_not_found", entry, f"{verb}: {phrase!r}")
                refused = True
                break

            start = window[0] - lead
            through = act.get("through_phrase")
            if through:
                closing = _phrase_window(str(through), words)
                if closing is None:
                    drop("anchor_phrase_not_found", entry,
                         f"{verb} through: {through!r}")
                    refused = True
                    break
                end = closing[1]
                basis = f"word_window:{phrase} -> {through}"
            else:
                end = start + float(look[spec.ramp])
                basis = f"word_window:{phrase}"

            if start < beat_start - 1e-6 or end > beat_end + 1e-6:
                drop("act_outside_beat", entry,
                     f"{verb} {start:.3f}-{end:.3f} outside "
                     f"{beat_start:.3f}-{beat_end:.3f}")
                refused = True
                break

            part_spec = act.get("parts", "all")
            if part_spec == "all":
                chosen = tuple(range(1, parts + 1))
            else:
                try:
                    chosen = tuple(int(p) for p in part_spec)
                except (TypeError, ValueError):
                    chosen = ()
                if not chosen or any(p < 1 or p > parts for p in chosen):
                    drop("part_not_in_component", entry,
                         f"{verb} parts={part_spec!r} of {parts}")
                    refused = True
                    break

            resolved_acts.append(ResolvedAct(
                do=verb, parts=chosen, start=start, end=end,
                anchor_phrase=phrase, lead_seconds=lead, timing_basis=basis))

        if refused:
            continue

        starts = [a.start for a in resolved_acts]
        if starts != sorted(starts):
            drop("acts_out_of_order", entry,
                 ", ".join(f"{a.do}@{a.start:.3f}" for a in resolved_acts))
            continue

        kept.append(ResolvedComponent(
            segment=segment, shows=shows, component=name, parts=parts,
            start=beat_start, end=beat_end, acts=tuple(resolved_acts)))

    return kept, dropped


# ---------------------------------------------------------------------------
# From a resolved component to StagedScene layers.
# ---------------------------------------------------------------------------
#
# Everything below reads magnitudes out of `look` and states none. The
# only numbers this file authors are 0 and 1 - "not there" and "there" -
# which are the absence of a choice rather than a choice, the same
# reading `series_look.NEUTRAL_CDL` gets (AGENTS.md 10.5).


def _keys(pairs: Sequence[Tuple[float, float]], ease: str | None = None) -> List[dict]:
    out: List[dict] = []
    for i, (at, value) in enumerate(pairs):
        key = {"at": round(float(at), 4), "value": float(value)}
        if i and ease:
            key["ease"] = ease
        out.append(key)
    return out


def _countable_set_layers(resolved: ResolvedComponent,
                          look: Mapping[str, object]) -> List[dict]:
    """A row of marks, each with its own tracks, built from the acts.

    A mark is a `disc`: the smallest drawn thing StagedScene has that
    reads as one countable item.  Its x is fixed by the row; its y,
    opacity and colour are what the acts move.  Colour is not
    animatable in StagedScene, so a `fail` draws the failed colour as a
    SECOND disc that fades in over the first - which is also what makes
    a failure read as a change of state rather than a change of object.
    """
    spacing = float(look["mark_spacing"])
    size = float(look["mark_size"])
    row_y = float(look["row_y"])
    colour = str(look["mark_colour"])
    n = resolved.parts
    t0 = resolved.start

    # Rebased to the component's own clock: the segment renders from its
    # own zero, the timebase MotionGraphics gives its planned elements.
    xs = [(i - (n - 1) / 2.0) * spacing for i in range(n)]

    opacity: Dict[int, List[Tuple[float, float]]] = {p: [(0.0, 0.0)] for p in range(1, n + 1)}
    y: Dict[int, List[Tuple[float, float]]] = {p: [] for p in range(1, n + 1)}
    failed: Dict[int, List[Tuple[float, float]]] = {p: [(0.0, 0.0)] for p in range(1, n + 1)}
    x_off: Dict[int, List[Tuple[float, float]]] = {p: [] for p in range(1, n + 1)}

    for act in resolved.acts:
        a, b = act.start - t0, act.end - t0
        count = len(act.parts)
        for k, p in enumerate(act.parts):
            # A stagger: part k of this act runs over its own slice of
            # the act's window, so a set lands one at a time rather than
            # as a block. One slice each is the absence of a rhythm,
            # not a chosen one.
            lo = a + (b - a) * (k / count)
            hi = a + (b - a) * ((k + 1) / count) if count > 1 else b
            if act.do == "land":
                rise = float(look["land_rise"])
                opacity[p] += [(lo, 0.0), (hi, 1.0)]
                y[p] += [(lo, row_y + rise), (hi, row_y)]
            elif act.do == "fail":
                fall = float(look["fail_fall"])
                base = y[p][-1][1] if y[p] else row_y
                failed[p] += [(lo, 0.0), (hi, 1.0)]
                y[p] += [(lo, base), (hi, base + fall)]
                opacity[p] += [(lo, opacity[p][-1][1]),
                               (hi, float(look["failed_opacity"]))]
            elif act.do == "dim":
                opacity[p] += [(lo, opacity[p][-1][1]),
                               (hi, float(look["dimmed_opacity"]))]
            elif act.do == "gather":
                target = (p - 1 - (n - 1) / 2.0) * float(look["gathered_spacing"])
                x_off[p] += [(lo, xs[p - 1]), (hi, target)]
            elif act.do == "withdraw":
                opacity[p] += [(lo, opacity[p][-1][1]), (hi, 0.0)]

    layers: List[dict] = []
    for p in range(1, n + 1):
        track_x = _keys(x_off[p], "inOut") if x_off[p] else xs[p - 1]
        track_y = _keys(y[p], "out") if y[p] else row_y
        layers.append({
            "kind": "disc", "id": f"{resolved.component}_{p}",
            "x": track_x, "y": track_y, "size": size,
            "opacity": _keys(opacity[p], "out"), "colour": colour,
        })
        if len(failed[p]) > 1:
            layers.append({
                "kind": "disc", "id": f"{resolved.component}_{p}_failed",
                "x": track_x, "y": track_y, "size": size,
                "opacity": _keys(failed[p], "out"),
                "colour": str(look["failed_colour"]),
            })
    return layers


def _flow_between_layers(resolved: ResolvedComponent,
                         look: Mapping[str, object]) -> List[dict]:
    """Two endpoints and the things that cross between them."""
    sx, sy = float(look["source_x"]), float(look["source_y"])
    kx, ky = float(look["sink_x"]), float(look["sink_y"])
    endpoint = float(look["endpoint_size"])
    size = float(look["mark_size"])
    colour = str(look["mark_colour"])
    t0 = resolved.start

    ends = {1: (sx, sy), 2: (kx, ky)}
    end_opacity: Dict[int, List[Tuple[float, float]]] = {1: [(0.0, 0.0)], 2: [(0.0, 0.0)]}
    arrived: List[Tuple[float, float]] = [(0.0, 0.0)]
    travellers: Dict[int, Dict[str, List[Tuple[float, float]]]] = {}

    for act in resolved.acts:
        a, b = act.start - t0, act.end - t0
        movers = [p for p in act.parts if p > 2]
        if act.do == "open":
            for p in act.parts:
                if p in end_opacity:
                    end_opacity[p] += [(a, 0.0), (b, 1.0)]
        elif act.do == "travel":
            for k, p in enumerate(movers):
                lo = a + (b - a) * (k / max(1, len(movers)))
                hi = a + (b - a) * ((k + 1) / max(1, len(movers)))
                travellers[p] = {
                    "x": [(lo, sx), (hi, kx)],
                    "y": [(lo, sy), (hi, ky)],
                    "opacity": [(0.0, 0.0), (lo, 1.0), (hi, 1.0),
                                (hi, 0.0)],
                }
        elif act.do == "arrive":
            arrived += [(a, 0.0), (b, 1.0)]
        elif act.do == "withdraw":
            for p in act.parts:
                if p in end_opacity:
                    end_opacity[p] += [(a, end_opacity[p][-1][1]), (b, 0.0)]
                elif p in travellers:
                    travellers[p]["opacity"] += [(b, 0.0)]

    layers: List[dict] = []
    for p, (x, y) in ends.items():
        layers.append({
            "kind": "disc", "id": f"flow_end_{p}", "x": x, "y": y,
            "size": endpoint, "opacity": _keys(end_opacity[p], "out"),
            "colour": colour,
        })
    if len(arrived) > 1:
        layers.append({
            "kind": "disc", "id": "flow_end_2_arrived", "x": kx, "y": ky,
            "size": endpoint, "opacity": _keys(arrived, "out"),
            "colour": str(look["arrived_colour"]),
        })
    for p, tracks in sorted(travellers.items()):
        layers.append({
            "kind": "disc", "id": f"flow_mark_{p}",
            "x": _keys(tracks["x"], "inOut"), "y": _keys(tracks["y"], "inOut"),
            "size": size, "opacity": _keys(tracks["opacity"]),
            "colour": colour,
        })
    return layers


#: Components this module can turn into StagedScene layers. A name here
#: and absent from `BLOCKED` is a claim that a render exists to back it.
LAYER_BUILDERS: Dict[str, Callable[[ResolvedComponent, Mapping[str, object]], List[dict]]] = {
    "countable_set": _countable_set_layers,
    "flow_between": _flow_between_layers,
}


def component_layers(resolved: ResolvedComponent,
                     look: Mapping[str, object]) -> List[dict]:
    """The StagedScene layers this resolved component draws.

    Times are on the COMPONENT's own clock, rebased to its start, which
    is the timebase a StagedScene segment renders on.
    """
    builder = LAYER_BUILDERS.get(resolved.component)
    if builder is None:
        raise ComponentPlanError(
            f"no builder for {resolved.component}: {blocker(resolved.component)}")
    return builder(resolved, look)


def assert_roster_is_well_formed() -> None:
    """Every entry states refusals, a material, parts and acts, and
    every buildable name has a builder.

    `motion_graphics_vocabulary` raises the same way and for the same
    reason: an entry that only says what a thing is teaches a model to
    reach for it everywhere (AGENTS.md 16).
    """
    for c in COMPONENTS:
        if not c.never:
            raise ComponentPlanError(f"{c.name} records no refusals")
        if c.made_of not in MATERIALS:
            raise ComponentPlanError(f"{c.name} names material {c.made_of!r}")
        if not c.acts:
            raise ComponentPlanError(f"{c.name} performs no acts")
        for a in c.acts:
            if not a.ramp:
                raise ComponentPlanError(f"{c.name}.{a.name} names no ramp")
    for name in BLOCKED:
        if name not in COMPONENTS_BY_NAME:
            raise ComponentPlanError(f"BLOCKED names {name!r}, not in the roster")
    for c in COMPONENTS:
        has_builder = c.name in LAYER_BUILDERS
        if has_builder == bool(blocker(c.name)):
            raise ComponentPlanError(
                f"{c.name}: builder={has_builder} contradicts blocker="
                f"{blocker(c.name)!r}")
