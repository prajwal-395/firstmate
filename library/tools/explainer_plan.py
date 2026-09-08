"""An animated explainer, and the one field that would make it automatic.

The captain named four capabilities; this is the fourth.  Their words:

    "there is a lot we can potentially do with it in terms of creating
     motion graphics and maybe even whole full screen animations and
     chroma key transitions and animated explainer videos which are all
     capabilities not flushed out for the pipeline."

The phrase covers three different things, and building the wrong one
wastes the work, so §1 settles which one this is before anything else.


1. What an animated explainer is HERE
=====================================
Three readings were on the table:

(a) **A reel that is ENTIRELY animation**, no talking heads, narrated
    over the hosts' audio.
(b) **A sustained animated SEQUENCE inside an otherwise normal reel**,
    explaining a concept the speech introduces.
(c) **A diagram that BUILDS across several beats** rather than one card
    appearing.

**(b) and (c) are one thing and this module is it.**  (c) describes the
internal structure - stages revealed one at a time - and (b) describes
where it sits - over picture that keeps playing, while the speech goes
on.  Neither is a capability without the other: a build whose stages are
not timed to the words is a card that animates for its own sake, and a
sequence over speech that arrives whole is a card.  So:

    **An animated explainer is a STAGED graphic whose stages are
    revealed at the reel seconds where the speech says them.**

The vocabulary already agrees, and it was written before this module.
`motion_graphics_vocabulary`'s `list_build` says its refusal in the same
words from the other side: *"Where the items are not timed to the words.
A set that appears all at once is a quote_card."*  What makes an
explainer an explainer is the anchoring, and the anchoring is what
nothing had.

**(a) is the same mechanism with a taste input the engine cannot
supply, and that is why it is not built.**  Mechanically an all-animation
reel is this, with the span equal to the whole reel and the graphic
opaque - one declaration, no new code.  What it needs and nobody has is
sixty seconds of authored artwork: a ground, a palette, a motion
language and a drawing for every beat.  AGENTS.md 14 puts artwork in the
project and AGENTS.md 10.5 forbids the engine stating taste, so the
engine can carry an all-animation reel and cannot author one.  That is a
statement about who draws it, not about whether it is reachable; a
project that owns the artwork declares a full-frame element for the span
(`docs/FULL_FRAME_ELEMENTS.md`) and gets it.


2. What an explainer needs, and what the pipeline produces
==========================================================
An explainer needs three things: a CLAIM, its PARTS, and an ORDER to
reveal them in.  Measured against what the pipeline really writes down,
2026-09-07:

=========================  ==========  ==============================
Needed                     Produced?   Where
=========================  ==========  ==============================
the claim                  **yes**     `reel_judgement.readings[].claim`,
                                       with `claim_quote` grounded in the
                                       reel's own words
its parts, enumerated      **no**      the claim is ONE SENTENCE of
                                       prose.  Nothing splits it.
when each part is said     **derived** the judge is already handed
                                       `lines` with `at` in REEL
                                       seconds; a part carrying its own
                                       quote is anchored by SEARCH
=========================  ==========  ==============================

So the gap is **one field**, and it is smaller than it looks.  Row three
is not a second gap: it is arithmetic over row two.  Give a part the
words where it is said and the reel second falls out of a search over
`lines`, which is the discipline AGENTS.md 6 already applies to a
passage (*"A passage is anchored by SEARCH, never by the occurrence
nearest the hint"*) and the discipline `reel_quality_bar` already
applies to `claim_quote` and every `assumes_known[].quote`.

That field is `claim_parts`, added to step 3.05's ask -
`reel_quality_bar.READING_FIELDS` - as an OPTIONAL, `contains`-grounded,
ordered list.  Optional for the same reason `takeaway_quote` is: *"this
claim has no parts"* is a real answer and the common one.  A claim that
is one indivisible statement gets no explainer, and that is correct.

**What was NOT enough, and why the cheap version was refused.**  Three
things already on disk look like parts and are not:

- `assumes_known` is a grounded, ordered list with quotes - and it is
  what the reel does NOT explain.  Drawing it would caption the reel's
  own gaps.
- the reel's SENTENCES are ordered and timed.  Staging them is refused
  by the vocabulary itself: *"For items the speech does not actually
  enumerate.  A list imposes a structure, and imposing one the speaker
  did not use misreads them."*
- `claim` is one sentence.  Splitting prose on "and" / a semicolon is an
  engine inventing a structure, which is the whole of AGENTS.md 10.5.


3. The route, which is not a third one
======================================
Two lanes settled the architecture this rides on and neither is
re-derived here.

`docs/FULL_FRAME_ELEMENTS.md` settled the renderer boundary: **picture
that has to be CREATED is Remotion's; a treatment of picture that EXISTS
is Fusion's.**  An explainer is created picture, so it is Remotion's.

`docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` settled the shape of an
element laid over a reel: **additive, on its own video track, timing
untouched either side**, with the plan RECORDED by the build and graded
against what was recorded rather than re-derived.

Between them there is nothing left for this module to invent about
rendering, and it invents nothing:

    reel_judgement.readings[].claim_parts   the model's grounded parts
      -> explainer_plan.anchor_stages       part -> reel second, BY SEARCH
      -> explainer_plan.plan_entries        stages -> plan entries
      -> motion_graphics_plan.resolve_plan  the EXISTING resolver
      -> motion_graphics_plan.plan_segments the EXISTING clusterer
      -> motion_graphics.render_segment     step 4.06's OWN renderer
      -> reel_build.build_reel_timeline     placed on the explainer track
      -> reel_conformance_verifier          F21, failing both ways

Every arrow after the second is code that already existed.  This module
owns the first two and the enumeration around them.

**No new element draws.**  The elements an explainer is made of are
`motion_graphics_vocabulary`'s own, and the three an explainer can be
built from - `list_build`, `comparison_bars`, `step_counter` - are
already `reachable_now` and already drawn by `MotionGraphics/index.tsx`.
A fourth entry that drew nothing would be the defect class this project
has spent a week removing.  What was missing was never the drawing.

**What WAS missing, twice over.**  The reels process is two nodes,
`build_reels` and `verify_reels`, and neither had any route to the
motion-graphics layer at all: `motion_graphics` appears nowhere in
`reel_build.py`, so no reel has ever carried a graphic of any kind.  And
`motion_graphics_plan` times an entry from `start_seconds` the MODEL
states freely, with nothing anchoring it to a word.  So even on the
master, a build timed to the speech was not expressible.


4. What this module refuses
===========================
A gate that cannot fail is worse than no gate (AGENTS.md 10.4), so the
refusals are the substance and they are all reachable from a real
declaration:

- a stage whose quote is not in the reel's own words - `UNGROUNDED`
- two stages the reel says in a different order than the plan lists -
  `OUT_OF_ORDER`.  An explainer that reveals part three before part one
  misreads the speech it is drawn over.
- a stage anchored past the end of the reel - `OFF_THE_END`
- a declared element the roster does not mark drawable - `NOT_DRAWABLE`,
  by name, never dropped quietly
- an explainer with no stages left - `NOTHING_TO_DRAW` (AGENTS.md 10.2:
  an overlay that draws nothing is not rendered)
- a malformed declaration - raised by name rather than dropped

And it states no taste.  No colour, no typeface, no element, no anchor,
no duration, no hold, no stage count and no default motion character:
every one of them is the declaration's, and a declaration missing one
REFUSES rather than being completed from a constant.


5. Where an explainer sits, and the measurement that was owed
=============================================================
`docs/FULL_FRAME_ELEMENTS.md` §7 recorded owed work and named its owner:

    "What is missing is a picture-area enumeration ... That is the owed
     work, and it belongs to whoever owns the overlay layer, not here."

`timed_text_overlay` records the same absence from the other side, and
works around it by making every moment state its own `y`.  An explainer
is an overlay and this is that enumeration: :func:`picture_bands`.

The measurement it makes is exact.  A reel delivers its source fitted
into the frame (`reel_framing.delivered_picture`), and on the field-test
project that is 1080x607 of a 1080x1920 delivery - **31.6% of the
frame**, the rest black.  The platform's keep-clear insets are
`safe_area`.  Intersect them and a frame has THREE bands an overlay can
sit in, not one:

    above    inside the safe area, above the picture   no picture under it
    over     inside the safe area, on the picture      picture under it
    below    inside the safe area, below the picture   no picture under it

**It measures and reports; it does not choose.**  Which band an
explainer sits in is the declaration's, and an explainer over the
picture is not refused - a graphic over the shot is a legitimate gesture
and refusing it would be a gate that fails correct output (AGENTS.md
10.4).  What the run says is what the declaration bought: how much
picture it covers and how many caption seconds it draws over, the same
report `transition_overlay.captions_covered` makes for the same reason.

This is also the answer to the question the lane opened with - whether
an explainer competes with the speech underneath it.  On this project it
need not: there are 536 rows of dead frame above the picture strip and
336 below it, inside the safe area, carrying nothing.

`tests/test_explainer_plan.py`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from library.tools import motion_graphics_plan as _mg_plan
from library.tools import motion_graphics_vocabulary as _vocab


# ── What an explainer may be built from ──────────────────────────────

EXPLAINER_ELEMENTS: Tuple[str, ...] = (
    "list_build",
    "comparison_bars",
    "step_counter",
)
"""The roster keys an explainer may name.

Not a new vocabulary - a SUBSET of `motion_graphics_vocabulary.ROSTER`,
and the subset is derived from the roster's own words rather than
chosen here.  Each of these three is an element whose `needs` names a
timing PER ITEM:

- `list_build`   "a timing per item, anchored to when each is said"
- `comparison_bars` "appearing in the order they are spoken"
- `step_counter` "a timing per section"

Every other roster entry is timed as a whole.  A `quote_card` has one
appearance; a `title_lockup` has one appearance; drawing either in
stages would be animating for its own sake.  `assert_elements_stage`
checks that reading against the roster on every import path, so a roster
edit that removed the staging from one of these three fails here rather
than silently making an explainer out of a card.
"""


class ExplainerError(ValueError):
    """A declaration or a reading this module refuses, by name."""


#: Why a stage was refused.  The whole of it - a reason outside this
#: tuple raises rather than going quiet, the shape `vfx_plan_basis` and
#: `motion_graphics_plan.DROP_REASONS` established.
REFUSALS: Tuple[str, ...] = (
    "ungrounded",
    "out_of_order",
    "off_the_end",
    "not_drawable",
    "nothing_to_draw",
)

UNGROUNDED = "ungrounded"
OUT_OF_ORDER = "out_of_order"
OFF_THE_END = "off_the_end"
NOT_DRAWABLE = "not_drawable"
NOTHING_TO_DRAW = "nothing_to_draw"


def assert_elements_stage() -> None:
    """Every element an explainer may use really stages, per the roster.

    Runnable, and it can fail: `tests/test_explainer_plan.py`.  The
    reading is the roster's own `needs`/`what_it_is` wording, so a roster
    change that stopped an element from staging is caught where the
    subset is declared instead of producing an explainer that arrives
    whole.
    """
    staging_words = ("one at a time", "in the order they are spoken",
                     "per item", "per section", "as it is said")
    for key in EXPLAINER_ELEMENTS:
        element = _vocab.ELEMENTS_BY_KEY.get(key)
        if element is None:
            raise ExplainerError(
                f"{key!r} is not in motion_graphics_vocabulary.ROSTER. An "
                f"explainer is made of that roster's elements and no "
                f"others; this module defines no element of its own.")
        said = " ".join((element.what_it_is, element.needs)).lower()
        if not any(word in said for word in staging_words):
            raise ExplainerError(
                f"{key!r} no longer says it stages: neither what_it_is nor "
                f"needs mentions any of {list(staging_words)}. An explainer "
                f"is a graphic whose stages are timed to the words, so an "
                f"element that arrives whole cannot be one.")


# ── A stage ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Stage:
    """One part of the claim, and the words where the reel says it.

    `text` is what is DRAWN.  `quote` is where the reel SAYS it, and it
    is the only thing that puts the stage in time - `at_seconds` is
    derived from it and is never declared.
    """

    text: str
    quote: str
    at_seconds: float = -1.0
    """Reel seconds, from :func:`anchor_stages`.  Negative means not yet
    anchored; nothing downstream accepts a negative."""
    precision: str = ""
    """`word` or `line` - which anchor answered.

    Said rather than assumed, because the difference is seconds.  A
    `word` anchor is the reel second of the word the quote begins on.  A
    `line` anchor is the start of the transcript segment containing it,
    which on this episode's own segments is up to four seconds early:
    six sources enumerated in one breath sit in two segments, so at line
    precision all six land on two instants and the build stops being a
    build.
    """

    def as_dict(self) -> dict:
        return {"text": self.text, "quote": self.quote,
                "at_seconds": round(self.at_seconds, 3),
                "precision": self.precision}


WORD = "word"
LINE = "line"
PRECISIONS = (WORD, LINE)


@dataclass
class AnchoredExplainer:
    """What anchoring produced, including everything it refused."""

    stages: List[Stage] = field(default_factory=list)
    refused: List[dict] = field(default_factory=list)
    reel_seconds: float = 0.0

    def refuse(self, stage_text: str, reason: str, detail: str = "") -> None:
        if reason not in REFUSALS:
            raise ExplainerError(
                f"{reason!r} is not a reason a stage may be refused for. "
                f"REFUSALS is the whole of it: {list(REFUSALS)}.")
        self.refused.append(
            {"stage": stage_text, "reason": reason, "detail": detail})

    @property
    def precisions(self) -> Dict[str, int]:
        counted: Dict[str, int] = {}
        for stage in self.stages:
            counted[stage.precision] = counted.get(stage.precision, 0) + 1
        return counted

    def as_dict(self) -> dict:
        return {
            "stages": [s.as_dict() for s in self.stages],
            "refused": list(self.refused),
            "reel_seconds": round(self.reel_seconds, 3),
            "anchored_by": self.precisions,
        }


def _normalise(text: Any) -> str:
    """A quote and the reel's words compared on the same footing.

    `reel_quality_bar.normalise`, imported rather than respelled: a
    quote that grounds for the quality bar must ground here, or one
    field of one reading would be true for one reader and false for the
    other.  There is one normalisation in this engine and this is a
    reference to it.
    """
    from library.tools.reel_quality_bar import normalise
    return normalise(text)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: Any) -> List[str]:
    return [t for t in _normalise(text).split(" ") if t]


def _word_stream(lines: Sequence[dict]) -> List[Tuple[str, float]]:
    """Every timed word the reel plays, in play order, with its second.

    One flat stream rather than a search per line, because a quote may
    begin in one transcript segment and finish in the next - which is
    exactly what a sentence spanning a segment boundary does - and a
    per-line search would refuse it as ungrounded.
    """
    stream: List[Tuple[str, float]] = []
    for line in lines or []:
        for word in (line.get("words") or []):
            for token in _tokens(word.get("word")):
                stream.append((token, float(word.get("at"))))
    return stream


def anchor_stages(parts: Sequence[Any], lines: Sequence[dict],
                  reel_seconds: float) -> AnchoredExplainer:
    """Put each part at the reel second where the reel SAYS it.

    `parts` is the model's ordered list of `{part, quote}`.  `lines` is
    the reel's own lines with `at` in REEL seconds - the same table step
    3.05 hands the judge, so the quote is grounded against exactly what
    the model read.  A line carrying `words` (from
    `reel_quality_bar.played_speech(..., with_words=True)`) anchors to
    the WORD its quote begins on; a line without them anchors to the
    line, and which one answered is recorded on the stage.

    Anchoring is a SEARCH, never a declared number.  A part carries no
    second and cannot: the second is a fact about the speech, and a
    model asked for one would state a plausible round number - the
    failure `speech_sequence` treats the LLM's ranges as a lookup HINT
    to avoid (AGENTS.md 6).  The search takes the FIRST occurrence, and
    that is stated rather than hidden: a quote the reel says twice is
    anchored where it is first said, which is where the listener first
    hears it.

    Three refusals, each reachable from a real reading:

    - the quote is not in the reel's words -> `ungrounded`
    - a part is said EARLIER than the part before it -> `out_of_order`
    - a part is anchored at or past the reel's end -> `off_the_end`

    Ordering is checked against the SPEECH, not against the list: the
    list is the order the model wrote them in and the speech is the
    order a listener hears them.  When they disagree the speech is
    right, and an explainer built on the other reading would reveal part
    three while part one is still being said.
    """
    out = AnchoredExplainer(reel_seconds=float(reel_seconds or 0.0))
    if reel_seconds is None or float(reel_seconds) <= 0:
        raise ExplainerError(
            "a reel with no length has no second to anchor a stage at; "
            "reel_seconds comes from the plan's own ranges "
            "(reel_build.reel_ranges), never from a default.")

    haystacks: List[Tuple[float, str]] = []
    for line in lines or []:
        says = _normalise(line.get("says") or line.get("text"))
        if not says:
            continue
        at = line.get("at", line.get("reel_start"))
        if at is None:
            raise ExplainerError(
                "a line with no `at` cannot anchor anything. `at` is reel "
                "seconds and is what step 3.05 already hands the judge; a "
                "line without it is a contract break, not a missing default.")
        haystacks.append((float(at), says))

    if not haystacks:
        raise ExplainerError(
            "no lines to anchor against. The reel's own words are the only "
            "thing a stage can be grounded in.")

    stream = _word_stream(lines)

    previous = -1.0
    for raw in parts or []:
        if isinstance(raw, str):
            entry: Dict[str, Any] = {"part": raw, "quote": ""}
        elif isinstance(raw, dict):
            entry = raw
        else:
            raise ExplainerError(
                f"a claim part must be a string or an object with `part` "
                f"and `quote`, got {type(raw).__name__}.")

        text = _collapse(str(entry.get("part") or entry.get("text") or ""))
        quote = _collapse(str(entry.get("quote") or ""))
        if not text:
            raise ExplainerError(
                "a claim part with no text draws nothing. An empty part is "
                "a malformed reading, not a part with a default.")

        needle = _normalise(quote)
        if not needle:
            out.refuse(text, UNGROUNDED,
                       "the part carries no quote, so nothing puts it in "
                       "time. A part is anchored by its own words or not "
                       "at all.")
            continue

        at, precision = _anchor_one(needle, stream, haystacks)
        if at is None:
            out.refuse(text, UNGROUNDED,
                       f"{quote!r} is not in the words this reel plays")
            continue
        if at >= out.reel_seconds:
            out.refuse(text, OFF_THE_END,
                       f"anchored at {at:.2f}s, past the reel's "
                       f"{out.reel_seconds:.2f}s")
            continue
        if at < previous:
            out.refuse(text, OUT_OF_ORDER,
                       f"said at {at:.2f}s, before the previous stage at "
                       f"{previous:.2f}s - the reel says these parts in a "
                       f"different order than the reading lists them")
            continue

        previous = at
        out.stages.append(Stage(text=text, quote=quote, at_seconds=at,
                                precision=precision))

    return out


def _anchor_one(needle: str, stream: Sequence[Tuple[str, float]],
                haystacks: Sequence[Tuple[float, str]]):
    """`(second, precision)` for one normalised quote, or `(None, "")`.

    Word first, line second.  Both are containment - present or absent,
    no fraction, no similarity and no threshold - which is what makes
    the anchor something a reader can re-run rather than trust, and it
    is the same check `reel_quality_bar.check_reading` grounds every
    other quote with.
    """
    tokens = [t for t in needle.split(" ") if t]
    if tokens and stream:
        width = len(tokens)
        for index in range(0, len(stream) - width + 1):
            if [w for w, _ in stream[index:index + width]] == tokens:
                return stream[index][1], WORD
    for at, says in haystacks:
        if needle in says:
            return at, LINE
    return None, ""


# ── The declaration ──────────────────────────────────────────────────

DECLARATION_KEY = "explainer"
"""The `effect` slot a project declares an explainer in.

`project.yaml`'s `effect.explainer`, and the PROJECT wins over a brand
template exactly as `timed_text_overlay.resolve_declaration` and
`full_frame_element.resolve_declaration` already have it: an explainer
carries a project's own copy on screen, and copy the viewer reads is
artwork (AGENTS.md 14).
"""


def resolve_declaration(brand_effect: Optional[dict],
                        project_folder: Optional[str]) -> Optional[dict]:
    """The project's declaration if it has one, else the template's.

    Whole-slot replacement, never a merge: half a project's explainer and
    half a template's is a third declaration nobody wrote.
    """
    own = _project_declaration(project_folder)
    if own is not None:
        return own
    if brand_effect and DECLARATION_KEY in brand_effect:
        return brand_effect.get(DECLARATION_KEY)
    return None


def _project_declaration(project_folder: Optional[str]):
    if not project_folder:
        return None
    import os
    path = os.path.join(str(project_folder), "project.yaml")
    if not os.path.isfile(path):
        return None
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml is in requirements.txt
        return None
    with open(path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    effect = (config.get("effect") or {})
    if DECLARATION_KEY not in effect:
        return None
    return effect.get(DECLARATION_KEY)


BANDS = ("above", "over", "below")
"""Where an explainer may sit, relative to the picture the reel delivers.

Three, because :func:`picture_bands` measures three.  There is no
default: a declaration names one or is refused, because choosing between
"on the shot" and "in the dead frame above it" is the gesture.
"""


def normalise_declaration(raw: Any) -> dict:
    """Check a declaration and fill in nothing.

    Raises `ExplainerError` naming the field.  A malformed declaration
    RAISES rather than being dropped, for the reason
    `bookends.assert_no_invented_bookends` gives: a declaration that
    vanishes into a log line is how the 4th Wall end card survived four
    months.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ExplainerError(
            f"effect.{DECLARATION_KEY} must be a mapping, got "
            f"{type(raw).__name__}.")

    element = str(raw.get("element") or "").strip()
    if not element:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY} names no `element`. The engine "
            f"chooses none: which graphic explains a claim is the "
            f"declaration's (AGENTS.md 10.5).")
    if element not in EXPLAINER_ELEMENTS:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.element is {element!r}. An explainer "
            f"is built from an element that stages: "
            f"{list(EXPLAINER_ELEMENTS)}. "
            + (_vocab.refusal_reason(element) or ""))

    band = str(raw.get("band") or "").strip().lower()
    if band not in BANDS:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.band is {band!r}; it must be one of "
            f"{list(BANDS)}. Where an explainer sits relative to the "
            f"picture is the gesture and the engine states no default.")

    anchor = str(raw.get("anchor") or "").strip().lower()
    if anchor not in _mg_plan.ANCHORS:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.anchor is {anchor!r}; it must be one "
            f"of {list(_mg_plan.ANCHORS)}.")

    hold = raw.get("hold_seconds")
    if hold is None:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY} states no `hold_seconds`. How long "
            f"the finished build stays on screen after its last stage is "
            f"the declaration's; the engine has no number for it.")
    try:
        hold = float(hold)
    except (TypeError, ValueError):
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.hold_seconds is {hold!r}, which is "
            f"not a number of seconds.")
    if hold < 0:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.hold_seconds is {hold}; a hold "
            f"cannot be negative.")

    colour = str(raw.get("colour") or raw.get("color") or "").strip()
    colour_role = str(raw.get("colour_role") or "").strip().lower()
    if not colour and not colour_role:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY} states neither `colour` nor "
            f"`colour_role`. `motion_graphics_plan.resolve_colour` drops an "
            f"entry that states neither, so a declaration without one draws "
            f"nothing and would do it silently.")
    if colour_role and colour_role not in _mg_plan.COLOUR_ROLES:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.colour_role is {colour_role!r}; it "
            f"must be one of {list(_mg_plan.COLOUR_ROLES)}.")

    type_role = str(raw.get("type_role") or "").strip().lower()
    if type_role and type_role not in _mg_plan.TYPE_ROLES:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.type_role is {type_role!r}; it must "
            f"be one of {list(_mg_plan.TYPE_ROLES)}.")

    entrance = str(raw.get("entrance") or "").strip().lower()
    if entrance and entrance not in _mg_plan.MOTION_CHARACTERS:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.entrance is {entrance!r}; it must be "
            f"one of {list(_mg_plan.MOTION_CHARACTERS)}.")
    exit_ = str(raw.get("exit") or "").strip().lower()
    if exit_ and exit_ not in _mg_plan.MOTION_CHARACTERS:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.exit is {exit_!r}; it must be one of "
            f"{list(_mg_plan.MOTION_CHARACTERS)}.")

    out = {
        "element": element,
        "band": band,
        "anchor": anchor,
        "hold_seconds": hold,
    }
    if colour:
        out["colour"] = colour
    if colour_role:
        out["colour_role"] = colour_role
    if type_role:
        out["type_role"] = type_role
    if entrance:
        out["entrance"] = entrance
    if exit_:
        out["exit"] = exit_
    if raw.get("footprint") is not None:
        out["footprint"] = raw["footprint"]
    if raw.get("emphasis") is not None:
        out["emphasis"] = raw["emphasis"]
    return out


# ── The plan ─────────────────────────────────────────────────────────

def plan_entries(anchored: AnchoredExplainer, declaration: dict) -> List[dict]:
    """Turn anchored stages into `motion_graphics_plan` entries.

    ONE entry, not one per stage.  The staging is INSIDE the element -
    `list_build` reveals its runs one at a time - so a plan entry per
    stage would draw N separate lists rather than one list of N items.
    The entry starts at the FIRST stage's second and runs until the last
    stage's second plus the declared hold, which is the only arithmetic
    here and the only number the declaration supplies.

    Returns `[]` where nothing is left to draw; the caller reports that
    rather than rendering an empty overlay (AGENTS.md 10.2).
    """
    if not anchored.stages:
        return []
    first = anchored.stages[0].at_seconds
    last = anchored.stages[-1].at_seconds
    end = min(last + float(declaration["hold_seconds"]), anchored.reel_seconds)
    duration = end - first
    if duration <= 0:
        return []

    entry: Dict[str, Any] = {
        "element": declaration["element"],
        "anchor": declaration["anchor"],
        "row": 0,
        "start_seconds": round(first, 3),
        "duration_seconds": round(duration, 3),
        # The COPY runs ARE the stages, in the order the reel says them,
        # under the key `motion_graphics_plan._copy_runs` already reads.
        # This module writes an entry the EXISTING resolver understands;
        # it does not define a second entry shape.
        "copy": [
            {"text": stage.text,
             "type_role": declaration.get("type_role", "supporting")}
            for stage in anchored.stages
        ],
        # Each stage's own second, rebased to the entry's start. This is
        # what makes it an explainer rather than a list that appears:
        # the renderer reveals run i at `stage_offsets[i]`.
        "data": {
            "stage_offsets": [round(s.at_seconds - first, 3)
                              for s in anchored.stages],
        },
    }
    for key in ("colour", "colour_role", "entrance", "exit",
                "footprint", "emphasis"):
        if key in declaration:
            entry[key] = declaration[key]
    return [entry]


# ── The picture-area enumeration ─────────────────────────────────────

@dataclass(frozen=True)
class PictureBands:
    """What an overlay lands ON, per band, for one delivery frame.

    All values are delivery-frame pixels.  A band with zero height does
    not exist on that frame: a reel whose picture fills the frame has no
    `above` and no `below`, and a declaration naming one is REPORTED as
    unavailable rather than silently moved.
    """

    frame_width: int
    frame_height: int
    picture: Tuple[int, int, int, int]
    """The rectangle the reel's picture really occupies."""
    safe: Tuple[int, int, int, int]
    """The rectangle inside the platform's keep-clear insets."""
    above: Tuple[int, int, int, int]
    over: Tuple[int, int, int, int]
    below: Tuple[int, int, int, int]

    def band(self, name: str) -> Tuple[int, int, int, int]:
        if name not in BANDS:
            raise ExplainerError(
                f"{name!r} is not a band; known: {list(BANDS)}")
        return getattr(self, name)

    def height_of(self, name: str) -> int:
        left, top, right, bottom = self.band(name)
        return max(0, bottom - top) if right > left else 0

    def covers_picture(self, name: str) -> bool:
        return name == "over" and self.height_of("over") > 0

    def as_dict(self) -> dict:
        return {
            "frame": [self.frame_width, self.frame_height],
            "picture": list(self.picture),
            "safe": list(self.safe),
            "bands": {name: {"rect": list(self.band(name)),
                             "height": self.height_of(name),
                             "covers_picture": self.covers_picture(name)}
                      for name in BANDS},
        }


def picture_bands(picture_rect: Sequence[int], frame_width: int,
                  frame_height: int, insets: Any) -> PictureBands:
    """The three bands, from the picture rectangle and the safe area.

    `picture_rect` is `(left, top, right, bottom)` in delivery-frame
    pixels - what `reel_framing.delivered_picture(...).rect` returns.
    `insets` is a `safe_area.SafeAreaInsets` or any object with
    `top`/`right`/`bottom`/`left`.

    Pure arithmetic over two existing measurements.  It measures NO new
    quantity and states no preference: the delivered rectangle is
    `reel_framing`'s and the insets are `safe_area`'s, and this is their
    intersection expressed as the three places something can go.
    """
    if frame_width <= 0 or frame_height <= 0:
        raise ExplainerError(
            "a frame with no width or height has no bands; the delivery "
            "format is library/tools/delivery_format.py.")
    p_left, p_top, p_right, p_bottom = (int(v) for v in picture_rect)
    s_left = int(getattr(insets, "left"))
    s_top = int(getattr(insets, "top"))
    s_right = frame_width - int(getattr(insets, "right"))
    s_bottom = frame_height - int(getattr(insets, "bottom"))

    # The picture as it really lands INSIDE the frame; a fill crop puts
    # the source rectangle outside it and a band cannot.
    v_top = max(p_top, 0)
    v_bottom = min(p_bottom, frame_height)

    def _band(top: int, bottom: int) -> Tuple[int, int, int, int]:
        top = max(top, s_top)
        bottom = min(bottom, s_bottom)
        if bottom <= top or s_right <= s_left:
            return (0, 0, 0, 0)
        return (s_left, top, s_right, bottom)

    return PictureBands(
        frame_width=int(frame_width),
        frame_height=int(frame_height),
        picture=(p_left, p_top, p_right, p_bottom),
        safe=(s_left, s_top, s_right, s_bottom),
        above=_band(s_top, v_top),
        over=_band(max(v_top, s_top), min(v_bottom, s_bottom)),
        below=_band(v_bottom, s_bottom),
    )


def band_insets(bands: PictureBands, band: str) -> Dict[str, int]:
    """The declared band, expressed as the insets a renderer positions in.

    This is what makes the enumeration REACH the picture rather than
    just describe it, and it adds no drawing code at all: the
    composition already resolves its nine-position `anchor` grid against
    `props.safeArea`, so handing it the band's own rectangle in place of
    the whole safe box puts `bottom_centre` at the bottom of THAT band.
    One prop, a narrower box, no second positioning mechanism - which is
    what `timed_text_overlay` was working around when it made every
    moment state its own `y`.
    """
    left, top, right, bottom = bands.band(band)
    if right <= left or bottom <= top:
        raise ExplainerError(
            f"band {band!r} has no area on a "
            f"{bands.frame_width}x{bands.frame_height} frame carrying "
            f"picture at {list(bands.picture)}; there is nowhere to put "
            f"an explainer in it.")
    return {
        "top": int(top),
        "right": int(bands.frame_width - right),
        "bottom": int(bands.frame_height - bottom),
        "left": int(left),
    }


def band_report(bands: PictureBands, declaration: dict) -> dict:
    """What the declared band actually bought, said on the run.

    REPORTS, never refuses.  A graphic over the shot is a legitimate
    gesture and a check that failed it would be a gate that fails
    correct output (AGENTS.md 10.4).  What is not legitimate is not
    KNOWING, which is why this is printed by the build that places it -
    the same reading `transition_overlay`'s gesture measurement makes.
    """
    name = declaration["band"]
    height = bands.height_of(name)
    return {
        "band": name,
        "rect": list(bands.band(name)),
        "height": height,
        "available": height > 0,
        "covers_picture": bands.covers_picture(name),
        "picture_covered_fraction": round(
            (bands.height_of("over") / max(1, bands.frame_height))
            if name == "over" else 0.0, 4),
    }


# ── Looking at what was drawn ────────────────────────────────────────

FRAME_EDGE_CLIPPED = "frame_edge"
"""The one thing about a rendered explainer that is a hard defect.

Remotion renders at the delivery size and CLIPS: a graphic too big for
its box is not drawn outside the frame, it is drawn cut off, and the
file is a valid picture of the right size that nothing downstream can
tell apart.  Ink touching the outermost row or column is what being cut
off looks like, and it needs no threshold - a graphic laid out inside a
safe area with a 90px left inset cannot legitimately reach column zero.
"""


def measure_render(overlay_path: str, frame: int = -1) -> dict:
    """Where the ink really is on one frame of a rendered explainer.

    `frame` is the frame index to read; `-1` is the LAST one, which is
    the worst case for a build - every stage is up by then.

    Measured on the ALPHA channel, because that is what a transparent
    overlay draws with, and reported as row and column extents.  It
    classifies nothing: `render_findings` is where the reading becomes
    a verdict, and it is deliberately a different function so the
    measurement can be read on its own.
    """
    import subprocess
    import tempfile

    import numpy as np
    from PIL import Image

    with tempfile.TemporaryDirectory() as work:
        import os
        still = os.path.join(work, "frame.png")
        select = ("select=eq(n\\," + str(int(frame)) + ")"
                  if frame >= 0 else "reverse,select=eq(n\\,0)")
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", overlay_path,
             "-vf", select, "-frames:v", "1", "-y", still],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, check=False)
        if result.returncode != 0 or not os.path.isfile(still):
            raise ExplainerError(
                f"could not read a frame of {overlay_path}: "
                f"{result.stderr[:200]}")
        alpha = np.array(Image.open(still).convert("RGBA"))[..., 3]

    height, width = alpha.shape
    ink = alpha > 0
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    if not len(rows):
        return {"frame": [int(width), int(height)], "ink_pixels": 0,
                "rows": None, "cols": None, "touches_frame_edge": []}
    touching = []
    if ink[0].any():
        touching.append("top")
    if ink[height - 1].any():
        touching.append("bottom")
    if ink[:, 0].any():
        touching.append("left")
    if ink[:, width - 1].any():
        touching.append("right")
    return {
        "frame": [int(width), int(height)],
        "ink_pixels": int(ink.sum()),
        "rows": [int(rows.min()), int(rows.max())],
        "cols": [int(cols.min()), int(cols.max())],
        "touches_frame_edge": touching,
    }


def render_findings(measured: dict, bands: Optional[PictureBands],
                    declaration: dict) -> List[dict]:
    """What a measured render says, as findings with a severity.

    **One ERROR and one WARNING, and the split is the whole design.**

    `frame_edge` is an ERROR because it is unambiguous: a graphic laid
    out inside a safe area cannot legitimately reach the outermost row
    or column, so ink there means the frame cut the graphic off.  A
    twelve-item list in a 536-row band is exactly that, and the file it
    produces is a valid picture of the right size.

    Ink outside the declared band is a WARNING and never an error,
    because the boundary is shared with the picture and a text shadow
    legitimately falls across it - a check that failed on one row of
    shadow would fail correct output (AGENTS.md 10.4).  The same reading
    `transition_overlay.captions_covered` gets, and for the same reason.
    """
    findings: List[dict] = []
    if measured.get("ink_pixels", 0) == 0:
        findings.append({
            "code": NOTHING_TO_DRAW, "severity": "error",
            "message": ("the rendered explainer puts no pixel on its last "
                        "frame - an overlay that draws nothing is not "
                        "rendered (AGENTS.md 10.2)")})
        return findings
    if measured.get("touches_frame_edge"):
        findings.append({
            "code": FRAME_EDGE_CLIPPED, "severity": "error",
            "message": (
                "the explainer's ink reaches the "
                + ", ".join(measured["touches_frame_edge"])
                + " edge of the frame, so the frame has cut it off; it "
                  "does not fit the band it was laid out in"),
            "detail": dict(measured)})
    if bands is None:
        return findings
    top, bottom = measured["rows"]
    band = declaration["band"]
    b_left, b_top, b_right, b_bottom = bands.band(band)
    over_top = max(0, b_top - top)
    over_bottom = max(0, bottom - b_bottom)
    if over_top or over_bottom:
        findings.append({
            "code": "outside_the_band", "severity": "warning",
            "message": (
                f"the explainer's ink runs {over_top} row(s) above and "
                f"{over_bottom} row(s) below the {band!r} band "
                f"[{b_top}..{b_bottom}]"),
            "detail": {"band": [b_top, b_bottom],
                       "ink_rows": [top, bottom]}})
    return findings


# ── Reading the judge's answer ───────────────────────────────────────

CLAIM_PARTS_KEY = "claim_parts"
"""The one field an explainer needs that nothing produced.

Added to `reel_quality_bar.READING_FIELDS` and step 3.05's schema.
OPTIONAL: a claim that is one indivisible statement has no parts, and
`[]` is the honest answer for most reels.
"""


def parts_for_reel(judgement: Optional[dict], reel_number: int) -> List[dict]:
    """The claim parts the judge wrote for one reel, or `[]`.

    `[]` covers three different things and deliberately does not
    distinguish them here - no judgement, no reading for this reel, or a
    reading whose claim has no parts.  All three mean the same thing to
    the build: this reel gets no explainer.  What each one MEANS is
    reported by the caller, which is where a project can see it.
    """
    if not judgement:
        return []
    for reading in (judgement.get("readings") or []):
        if int(reading.get("reel", -1)) != int(reel_number):
            continue
        parts = reading.get(CLAIM_PARTS_KEY) or []
        return [p for p in parts if p]
    return []


def lines_for_reel(judgement: Optional[dict], reel_number: int) -> List[dict]:
    """The `lines` table the judge read, if the judgement kept it."""
    if not judgement:
        return []
    for reading in (judgement.get("readings") or []):
        if int(reading.get("reel", -1)) == int(reel_number):
            return list(reading.get("lines") or [])
    return []


# ── The whole authoring pass ─────────────────────────────────────────

@dataclass
class ExplainerPlan:
    """One reel's explainer: what was planned, and what was refused."""

    reel_name: str
    declared: bool
    entries: List[dict] = field(default_factory=list)
    anchored: Optional[AnchoredExplainer] = None
    basis: str = ""
    band: Optional[dict] = None
    segments: List[dict] = field(default_factory=list)
    """What was really RENDERED and placed: `timeline_start`,
    `total_frames`, `overlay_path`.  This is what F21 grades the
    timeline against, so it is what the build wrote rather than what it
    intended - a segment the renderer refused never appears here and
    the check therefore never looks for it."""

    def as_dict(self) -> dict:
        return {
            "reel": self.reel_name,
            "declared": self.declared,
            "basis": self.basis,
            "entries": list(self.entries),
            "anchored": self.anchored.as_dict() if self.anchored else None,
            "band": self.band,
            "segments": [
                {"overlay_path": s.get("overlay_path"),
                 "timeline_start": s.get("timeline_start"),
                 "timeline_end": s.get("timeline_end"),
                 "total_frames": s.get("total_frames"),
                 "elements": list(s.get("elements") or [])}
                for s in self.segments
            ],
        }


NOT_DECLARED = "not_declared"
NO_PARTS = "no_parts_in_the_reading"
ALL_REFUSED = "every_stage_refused"
PLANNED = "planned"

BASES = (NOT_DECLARED, NO_PARTS, ALL_REFUSED, NOTHING_TO_DRAW, PLANNED)
"""Why a reel has the explainer it has, including none.

The whole of it, and a basis outside it raises.  The shape
`vfx_plan_basis` established and for its reason: an empty plan that does
not say WHY it is empty is indistinguishable from a plan nobody asked
for.
"""


def author_explainer(reel_name: str, reel_number: int, reel_seconds: float,
                     judgement: Optional[dict],
                     declaration: Any,
                     lines: Optional[Sequence[dict]] = None,
                     bands: Optional[PictureBands] = None) -> ExplainerPlan:
    """Author one reel's explainer from that reel's own data.

    Everything here is the reel's: its judgement, its lines, its length.
    Nothing is carried over from another reel and nothing is invented.
    """
    if declaration is None:
        return ExplainerPlan(reel_name=reel_name, declared=False,
                             basis=NOT_DECLARED)
    declared = normalise_declaration(declaration)
    if not declared:
        return ExplainerPlan(reel_name=reel_name, declared=False,
                             basis=NOT_DECLARED)

    band = band_report(bands, declared) if bands is not None else None
    parts = parts_for_reel(judgement, reel_number)
    if not parts:
        return ExplainerPlan(reel_name=reel_name, declared=True,
                             basis=NO_PARTS, band=band)

    rows = list(lines or lines_for_reel(judgement, reel_number))
    anchored = anchor_stages(parts, rows, reel_seconds)
    if not anchored.stages:
        return ExplainerPlan(reel_name=reel_name, declared=True,
                             anchored=anchored, basis=ALL_REFUSED, band=band)

    entries = plan_entries(anchored, declared)
    if not entries:
        return ExplainerPlan(reel_name=reel_name, declared=True,
                             anchored=anchored, basis=NOTHING_TO_DRAW,
                             band=band)
    return ExplainerPlan(reel_name=reel_name, declared=True, entries=entries,
                         anchored=anchored, basis=PLANNED, band=band)


# ── Where a rendered explainer goes ──────────────────────────────────

EXPLAINER_TRACK = 5
"""The reel video track an explainer segment is placed on.

**Stated as an assumption to be reconciled.**  On the reels path today
V1/V2 carry picture and V3 carries captions; nothing else exists.  Two
lanes were in flight when this was written and both add a track:
`docs/FULL_FRAME_ELEMENTS.md` places full-frame cards on **V1** (they
replace picture rather than overlay it) and
`docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` places transition elements on
**V4**.  V4 was claimed first, so an explainer takes V5 and the two
capabilities can be on one reel at once.

The master's own allocation is the thing that would otherwise apply -
`timeline_decisions` maps `motion_graphics_overlay` to V4 there - and it
is recorded rather than followed, because on a reel V4 is taken.

Reconciliation, if the ordering should change, is this constant and the
one `elif` in `reel_conformance_verifier._snapshot_to_reel_timeline`.
"""

EXPLAINER_TRACK_NAME = "Explainer"
"""What Resolve calls the track, the way V3 is already named "Captions".

A named track is self-describing to a reader with the timeline open and
to a check reading it back, which is the whole reason V3 has a name.
"""

RENDER_PREFIX = "explainer"
"""What a rendered segment is called: `explainer_<reel>_<index>.mov`.

AGENTS.md 5: prefix an overlay filename with its context.  A reel's
segments must not overwrite another reel's, which is the same defect
`subtitle_segment_id` exists to stop.
"""


def segment_name(reel_name: str, index: int) -> str:
    """The render name, and the only place it is spelled."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(reel_name).lower()).strip("_")
    return f"{RENDER_PREFIX}_{slug}_{index:02d}"


PLAN_FILENAME = "explainer_plans.json"
"""Where the build RECORDS what it planned, per project.

Read back by the conformance check rather than re-derived, for the
reason `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` §4 gives: a re-derived
plan is only the build's plan while nothing changed in between, and that
assumption already produced 42 confident meaningless errors on this path.
"""


def write_plans(project_folder: str, plans: Sequence[ExplainerPlan]) -> str:
    """Record every reel's explainer plan, including the empty ones."""
    import os
    from library.tools.project_layout import Area, ProjectLayout
    out_dir = ProjectLayout(project_folder).write_dir(Area.REVIEW)
    path = os.path.join(str(out_dir), PLAN_FILENAME)
    payload = {
        "format": "explainer_plans/1",
        "plans": [plan.as_dict() for plan in plans],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def plan_for_reel(plans: Optional[dict], reel_name: str) -> Optional[dict]:
    """The recorded plan for one reel by NAME, or None.

    None means "this build recorded nothing for this reel", which is
    what a project built before explainers existed looks like, and F21
    returns nothing rather than grading against an absence.
    """
    for plan in ((plans or {}).get("plans") or []):
        if str(plan.get("reel")) == str(reel_name):
            return plan
    return None


def read_plans(project_folder: str) -> dict:
    """What the build recorded, or `{}` when it recorded nothing."""
    import os
    from library.tools.project_layout import Area, ProjectLayout
    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)),
        PLAN_FILENAME)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def rename_plan_reels(project_folder: str, mapping: dict) -> None:
    """Rename `plans[].reel` fields in the recorded explainer plans.

    The staging half of promotion: a staged build records its explainer
    plans under the staging container so F21 grades the staging, and
    promotion renames the claim to the final timeline name. Plans for
    reels outside `mapping` are untouched. No file yet is a no-op -
    a build that drew nothing for any reel recorded nothing.
    """
    import os

    from library.tools.project_layout import Area, ProjectLayout
    if not mapping:
        return
    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)),
        PLAN_FILENAME)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    for plan in (payload.get("plans") or []):
        if plan.get("reel") in mapping:
            plan["reel"] = mapping[plan["reel"]]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def drop_plan_reels(project_folder: str, names) -> None:
    """Remove recorded explainer plans for the named reels.

    The gate-fail half of a refused staging: no baseline may survive
    for a container that is about to be deleted. Absent file or absent
    names are no-ops.
    """
    import os

    from library.tools.project_layout import Area, ProjectLayout
    drop = set(names or ())
    if not drop:
        return
    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)),
        PLAN_FILENAME)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["plans"] = [plan for plan in (payload.get("plans") or [])
                        if plan.get("reel") not in drop]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
