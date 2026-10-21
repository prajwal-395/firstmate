"""An animated explainer, and the one field that would make it automatic.

    **An animated explainer is a STAGED graphic whose stages are
    revealed at the reel seconds where the speech says them.**

It sits over picture that keeps playing, while the speech goes on, and
its stages are timed to the words that say them - the anchoring is what
makes it an explainer rather than a card (`motion_graphics_vocabulary`'s
`list_build` refuses items not timed to the words).  An all-animation
reel is the same mechanism with the span equal to the whole reel; the
engine can carry one and cannot author one, because the artwork belongs
to the project (AGENTS.md 14, 10.5) - a project that owns it declares a
full-frame element (`docs/FULL_FRAME_ELEMENTS.md`).  The reading is
argued in `docs/ANIMATED_EXPLAINER.md`.


1. The parts come from one field
================================
An explainer needs a CLAIM, its PARTS, and an ORDER.  The claim is
`reel_judgement.readings[].claim`.  The parts are `claim_parts`
(`CLAIM_PARTS_KEY`), in step 3.05's ask (`reel_quality_bar.READING_FIELDS`)
as an OPTIONAL, `contains`-grounded, ordered list of `{part, quote}`:
"this claim has no parts" is a real answer and the common one, and a
claim with no parts gets no explainer.  The order and timing are derived:
`anchor_stages` finds each part's quote in the reel's own `lines` BY
SEARCH (AGENTS.md 6), at `WORD` or `LINE` precision (`PRECISIONS`).

Nothing else stands in for the parts.  `assumes_known` is what the reel
does NOT explain; staging the reel's sentences imposes a structure the
speaker did not use; splitting `claim` on "and" or a semicolon is the
engine inventing structure (AGENTS.md 10.5).


2. The route, which is not a third one
======================================
Created picture is Remotion's (`docs/FULL_FRAME_ELEMENTS.md`); an element
over a reel is additive, on its own track, timing untouched, with the plan
RECORDED by the build and graded against what was recorded
(`docs/CHROMA_KEY_TRANSITIONS_MEASURED.md`)::

    reel_judgement.readings[].claim_parts   the model's grounded parts
      -> explainer_plan.anchor_stages       part -> reel second, BY SEARCH
      -> explainer_plan.plan_entries        stages -> plan entries
      -> motion_graphics_plan.resolve_plan  the EXISTING resolver
      -> motion_graphics_plan.plan_segments the EXISTING clusterer
      -> motion_graphics.render_segment     step 4.06's OWN renderer
      -> reel_build.build_reel_timeline     placed on the explainer track
      -> reel_conformance_verifier          F21, failing both ways

This module owns the first two arrows and the enumeration around them.
`author_explainer` authors one reel's explainer from that reel's own
judgement, lines and length, and its `ExplainerPlan` carries the basis
from `BASES` (`NOT_DECLARED`, `NO_PARTS`, `ALL_REFUSED`,
`NOTHING_TO_DRAW`, `PLANNED`).  The build places each segment
(`segment_name`, `RENDER_PREFIX`) on `EXPLAINER_TRACK`
(`EXPLAINER_TRACK_NAME`) and records every reel it touched - empty ones
included - in `PLAN_FILENAME`, merged rather than overwritten
(`write_plans`), so F21 grades each timeline against its own record.

**No new element draws.**  The elements an explainer is made of are
`EXPLAINER_ELEMENTS` - `list_build`, `comparison_bars`, `step_counter` -
from `motion_graphics_vocabulary`, already reachable and drawn by
`MotionGraphics/index.tsx`.  `assert_elements_stage` checks that each one
really stages, per the roster.

The declaration lives in the `effect.explainer` slot (`DECLARATION_KEY`);
`resolve_declaration` takes the project's if it has one, else the
template's - whole-slot replacement, never a merge.


3. What this module refuses
===========================
The refusals (`REFUSALS`) are all reachable from a real declaration:

- a stage whose quote is not in the reel's own words - `UNGROUNDED`
- two stages the reel says in a different order than the plan lists -
  `OUT_OF_ORDER`
- a stage anchored past the end of the reel - `OFF_THE_END`
- a declared element the roster does not mark drawable - `NOT_DRAWABLE`,
  by name, never dropped quietly
- an explainer with no stages left - `NOTHING_TO_DRAW` (AGENTS.md 10.2:
  an overlay that draws nothing is not rendered)
- a malformed declaration - raised by name (`ExplainerError`)

And it states no taste.  No colour, no typeface, no element, no anchor,
no duration, no hold, no stage count and no default motion character:
every one of them is the declaration's, and a declaration missing one
REFUSES rather than being completed from a constant
(`normalise_declaration`).


4. Where an explainer sits
==========================
:func:`picture_bands` is the picture-area enumeration
`docs/FULL_FRAME_ELEMENTS.md` §7 recorded as owed.  It intersects the
picture a reel really delivers (`reel_framing.delivered_picture`) with
the platform's keep-clear insets (`safe_area`), giving THREE bands
(`BANDS`):

    above    inside the safe area, above the picture   no picture under it
    over     inside the safe area, on the picture      picture under it
    below    inside the safe area, below the picture   no picture under it

**It measures and reports; it does not choose.**  The band is the
declaration's, and an explainer over the picture is not refused.
`band_report` says what the declaration bought - how much picture it
covers and how many caption seconds it draws over - the same report
`transition_overlay.captions_covered` makes.

A rendered explainer is measured on its alpha (`measure_render`), and
`render_findings` makes one ERROR and one WARNING of it: ink on the
outermost row or column (`FRAME_EDGE_CLIPPED`) means the frame cut the
graphic off; ink outside the declared band is only a WARNING, because a
shadow legitimately falls across the shared boundary.

`tests/unit/captions/test_full_frame_element.py`.

The measurements and rulings behind these rules (the three readings of
the captain's phrase, the needed-versus-produced table, the field-test
band measurement): docs/evidence/explainer_plan.md.
"""

from __future__ import annotations

import json
import os
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
    "no_picture",
    "asset_not_found",
    "duplicate_element",
)

UNGROUNDED = "ungrounded"
OUT_OF_ORDER = "out_of_order"
OFF_THE_END = "off_the_end"
NOT_DRAWABLE = "not_drawable"
NOTHING_TO_DRAW = "nothing_to_draw"
NO_PICTURE = "no_picture"
ASSET_NOT_FOUND = "asset_not_found"
DUPLICATE_ELEMENT = "duplicate_element"


def assert_elements_stage() -> None:
    """Every element an explainer may use really stages, per the roster.

    Runnable, and it can fail: `tests/unit/captions/test_full_frame_element.py`.  The
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
                haystacks: Sequence[Tuple[float, str]],
                start: int = 0):
    """`(second, precision)` for one normalised quote, or `(None, "")`.

    Word first, line second.  Both are containment - present or absent,
    no fraction, no similarity and no threshold - which is what makes
    the anchor something a reader can re-run rather than trust, and it
    is the same check `reel_quality_bar.check_reading` grounds every
    other quote with.

    `start` is where in the word stream the search begins, and it is
    what makes a STEP find ITS word: a step belongs to a beat, so its
    quote is searched from the beat's own word onward.  Without it a
    step that quotes a word the reel says twice is anchored to the
    FIRST saying, which may be a different sentence - "that" at 7.01 s
    instead of the beat's own "that" at 32.85 s.
    """
    tokens = [t for t in needle.split(" ") if t]
    if tokens and stream:
        width = len(tokens)
        for index in range(max(0, start), len(stream) - width + 1):
            if [w for w, _ in stream[index:index + width]] == tokens:
                return stream[index][1], WORD
    for at, says in haystacks:
        if needle in says:
            return at, LINE
    return None, ""


def _anchor_index(needle: str, stream: Sequence[Tuple[str, float]],
                  start: int = 0) -> int:
    """Where in the word stream a quote matched, or -1.

    The step's search starts AFTER its beat's own match, so a step is
    anchored inside the beat it belongs to.
    """
    tokens = [t for t in needle.split(" ") if t]
    if not tokens or not stream:
        return -1
    width = len(tokens)
    for index in range(max(0, start), len(stream) - width + 1):
        if [w for w, _ in stream[index:index + width]] == tokens:
            return index
    return -1


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


def is_beat_declaration(raw: Any) -> bool:
    """Does this declaration ask for the BEAT route?

    True when it carries `beats`.  The two routes never mix: a
    declaration with `beats` is a scene of PICTURES, and one without is
    the stage route - one element whose stages are copy runs.
    """
    return isinstance(raw, dict) and BEATS_KEY in raw


def normalise_beats_declaration(raw: Any) -> dict:
    """Check a BEAT declaration and fill in nothing.

    Raises `ExplainerError` naming the field, for the same reason
    `normalise_declaration` does: a declaration that vanishes into a log
    line is how the 4th Wall end card survived four months.

    What it insists on, and why each is the declaration's:

    - `ground` - the colour the scene is drawn on.  The engine ships no
      palette (AGENTS.md 10.5).
    - `font_family` - the type the labels are set in.
    - `beats` - at least one, each with a `quote`, an `asset` and an
      absolute `box`.
    - `hold_seconds` - how long the scene holds after its last beat.

    What it does NOT insist on, because the beat itself carries them:
    the camera, the label's size, weight and colour.  A declaration
    that states them uses them; one that does not gets the scene
    renderer's own drawing of the character (a label at the renderer's
    default weight is a mechanical default, not a taste).
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ExplainerError(
            f"effect.{DECLARATION_KEY} must be a mapping, got "
            f"{type(raw).__name__}.")
    beats = raw.get(BEATS_KEY)
    if not isinstance(beats, list) or not beats:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.{BEATS_KEY} must be a non-empty "
            f"list of beats; got {beats!r}. A scene with no beats draws "
            f"nothing (AGENTS.md 10.2).")
    ground = str(raw.get("ground") or "").strip()
    if not ground:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.ground is {ground!r}. The colour "
            f"the scene is drawn on is the declaration's; the engine "
            f"ships no palette (AGENTS.md 10.5).")
    font = str(raw.get("font_family") or "").strip()
    if not font:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.font_family is {font!r}. The "
            f"typeface the labels are set in is the declaration's.")
    hold = raw.get("hold_seconds")
    if hold is None:
        raise ExplainerError(
            f"effect.{DECLARATION_KEY} states no `hold_seconds`. How long "
            f"the scene holds after its last beat is the declaration's.")
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
    out = {
        BEATS_KEY: [dict(b) for b in beats if isinstance(b, dict)],
        "ground": ground,
        "font_family": font,
        "hold_seconds": hold,
    }
    for position, beat in enumerate(out[BEATS_KEY], start=1):
        # A beat with `layers:` places each layer in its OWN box; the
        # beat-level `box` is the shorthand for a beat of one layer.
        if not beat.get("layers") and not beat.get("elements"):
            _absolute_box(beat.get("box"), f"beats[{position}].box")
        if beat.get("label"):
            _absolute_box(beat.get("label_box"), f"beats[{position}].label_box")
        # The beat's LAYERS: the shorthand `asset` + `box` is one layer,
        # and `layers:` is a list of them - a beat may be a COMPOSED
        # scene (an illustration plus a mark that builds beside it),
        # not one icon.  A beat of one icon every five seconds with the
        # canvas empty around it is not a composition (the captain,
        # 2026-10-21, on the first contact sheet).
        layers = beat.get("layers")
        if layers is not None:
            if not isinstance(layers, list) or not layers:
                raise ExplainerError(
                    f"beats[{position}].layers must be a non-empty list of "
                    f"layers; got {layers!r}. A beat with no layer draws "
                    f"nothing.")
            for number, layer in enumerate(layers, start=1):
                label = f"beats[{position}].layers[{number}]"
                if not isinstance(layer, dict):
                    raise ExplainerError(
                        f"{label} must be a mapping, got "
                        f"{type(layer).__name__}.")
                if not str(layer.get("asset") or "").strip():
                    raise ExplainerError(
                        f"{label} names no asset. A layer IS a picture; "
                        f"text that arrives in pieces is a caption rail.")
                _absolute_box(layer.get("box"), f"{label}.box")
                _check_motion(layer, label)
        for number, element in enumerate(beat.get("elements") or [],
                                         start=1):
            label = f"beats[{position}].elements[{number}]"
            if not isinstance(element, dict):
                raise ExplainerError(
                    f"{label} must be a mapping, got "
                    f"{type(element).__name__}.")
            if not str(element.get("asset") or "").strip() \
                    and not element.get("text"):
                raise ExplainerError(
                    f"{label} names neither an asset nor text. An element "
                    f"IS a picture or a line of type; an element with "
                    f"neither draws nothing.")
            if element.get("text"):
                _absolute_box(element.get("box"), f"{label}.box")
            else:
                _absolute_box(element.get("box"), f"{label}.box")
            _check_motion(element, label)
            if element.get("spin") is not None:
                try:
                    float(element["spin"])
                except (TypeError, ValueError):
                    raise ExplainerError(
                        f"{label}.spin is {element['spin']!r}, which is "
                        f"not a number of degrees per second.") from None
                if element.get("upright"):
                    # A picture whose MEANING depends on which way it
                    # points may not be turned.  The captain's frames
                    # showed a RISING arrow ending up pointing DOWN,
                    # which says the opposite of the words it was
                    # there to say ("content built from it", "and AI
                    # really likes that").  The declaration is the only
                    # place that knows what a picture means, so it says
                    # so - and the engine refuses the motion that would
                    # flip it.
                    raise ExplainerError(
                        f"{label} is upright and names a spin. A picture "
                        f"whose meaning depends on which way it points "
                        f"may not be turned: an arrow that ends pointing "
                        f"down says the opposite of the words it is "
                        f"there to say. Take the spin off it, or drop "
                        f"the upright.")
            if element.get("pulse") is not None:
                pulse = element.get("pulse")
                if not isinstance(pulse, dict):
                    raise ExplainerError(
                        f"{label}.pulse must be a mapping, got "
                        f"{type(pulse).__name__}.")
                for key in ("scale", "float", "period"):
                    if pulse.get(key) is None:
                        continue
                    try:
                        float(pulse[key])
                    except (TypeError, ValueError):
                        raise ExplainerError(
                            f"{label}.pulse.{key} is {pulse[key]!r}, which "
                            f"is not a number.") from None
                if pulse.get("period") is not None \
                        and float(pulse["period"]) <= 0:
                    raise ExplainerError(
                        f"{label}.pulse.period is {pulse['period']}; a "
                        f"breath with no period is not a breath.")
            if element.get("drift") is not None:
                drift = element.get("drift")
                if (not isinstance(drift, (list, tuple)) or len(drift) != 2):
                    raise ExplainerError(
                        f"{label}.drift is {drift!r}; it must be two "
                        f"numbers, [x, y] in world units - how far the "
                        f"element keeps MOVING while it is on the frame.")
                try:
                    (float(drift[0]), float(drift[1]))
                except (TypeError, ValueError):
                    raise ExplainerError(
                        f"{label}.drift is {drift!r}, which does not hold "
                        f"two numbers.") from None
        for number, step in enumerate(beat.get("steps") or [], start=1):
            label = f"beats[{position}].steps[{number}]"
            if not isinstance(step, dict):
                raise ExplainerError(
                    f"{label} must be a mapping, got "
                    f"{type(step).__name__}.")
            if not str(step.get("quote") or "").strip():
                raise ExplainerError(
                    f"{label} carries no quote, so nothing puts it in "
                    f"time. A step is anchored by the words it draws.")
            for element_number, element in enumerate(step.get("elements") or [],
                                                     start=1):
                element_label = f"{label}.elements[{element_number}]"
                if not isinstance(element, dict):
                    raise ExplainerError(
                        f"{element_label} must be a mapping, got "
                        f"{type(element).__name__}.")
                if not str(element.get("asset") or "").strip() \
                        and not element.get("text"):
                    raise ExplainerError(
                        f"{element_label} names neither an asset nor "
                        f"text. An element IS a picture or a line of "
                        f"type.")
                _absolute_box(element.get("box"), f"{element_label}.box")
                _check_motion(element, element_label)
        for number, connector in enumerate(beat.get("connectors") or [],
                                           start=1):
            label = f"beats[{position}].connectors[{number}]"
            for end in ("from", "to"):
                point = connector.get(end)
                if (not isinstance(point, (list, tuple)) or len(point) != 2):
                    raise ExplainerError(
                        f"{label}.{end} is {point!r}; it must be two "
                        f"numbers, [x, y] in delivery-frame pixels.")
                try:
                    (float(point[0]), float(point[1]))
                except (TypeError, ValueError):
                    raise ExplainerError(
                        f"{label}.{end} is {point!r}, which does not hold "
                        f"two numbers.") from None
            if not str(connector.get("colour") or "").strip():
                raise ExplainerError(
                    f"{label} states no colour. A line's colour is the "
                    f"declaration's; the engine ships no palette "
                    f"(AGENTS.md 10.5).")
        if beat.get("stay_seconds") is not None:
            try:
                stay = float(beat["stay_seconds"])
            except (TypeError, ValueError):
                raise ExplainerError(
                    f"beats[{position}].stay_seconds is "
                    f"{beat['stay_seconds']!r}, which is not a number of "
                    f"seconds.") from None
            if stay < 0:
                raise ExplainerError(
                    f"beats[{position}].stay_seconds is {stay}; a stay "
                    f"cannot be negative.")
        _check_motion(beat, f"beats[{position}]")
    if any(str(b.get("label") or "") for b in out[BEATS_KEY]):
        size = raw.get("label_size")
        if size is None:
            raise ExplainerError(
                f"effect.{DECLARATION_KEY}.label_size is {size!r}. A beat "
                f"carrying a label needs the label's size; the engine "
                f"states no size of its own (AGENTS.md 10.5), and a label "
                f"at no size draws nothing.")
        try:
            out["label_size"] = float(size)
        except (TypeError, ValueError):
            raise ExplainerError(
                f"effect.{DECLARATION_KEY}.label_size is {size!r}, which is "
                f"not a number of pixels.") from None
        if out["label_size"] <= 0:
            raise ExplainerError(
                f"effect.{DECLARATION_KEY}.label_size is {size!r}; a label "
                f"with no size draws nothing.")
    window = raw.get("window")
    if window is not None:
        if (not isinstance(window, (list, tuple)) or len(window) != 2):
            raise ExplainerError(
                f"effect.{DECLARATION_KEY}.window is {window!r}; it must be "
                f"two numbers, [start, end] in reel seconds.")
        try:
            start, end = (float(v) for v in window)
        except (TypeError, ValueError):
            raise ExplainerError(
                f"effect.{DECLARATION_KEY}.window is {window!r}, which does "
                f"not hold two numbers.") from None
        if end <= start:
            raise ExplainerError(
                f"effect.{DECLARATION_KEY}.window is {window!r}, which has "
                f"no length; a scene with no length draws nothing.")
        out["window"] = [start, end]
    for key in ("camera", "label_weight", "label_colour", "asset_prefix"):
        if raw.get(key) is not None:
            out[key] = raw[key]
    # The PERSISTENT furniture: the header, the rule, the progress mark
    # - the things that are on the frame for the whole piece, so the
    # frame is never bare between beats.
    persistent = raw.get("persistent") or []
    if not isinstance(persistent, list):
        raise ExplainerError(
            f"effect.{DECLARATION_KEY}.persistent must be a list of "
            f"layers; got {persistent!r}.")
    for number, entry in enumerate(persistent, start=1):
        label = f"persistent[{number}]"
        if not isinstance(entry, dict):
            raise ExplainerError(
                f"{label} must be a mapping, got {type(entry).__name__}.")
        if not str(entry.get("asset") or "").strip() \
                and not entry.get("text"):
            raise ExplainerError(
                f"{label} names neither an asset nor text. A persistent "
                f"layer is a picture or a line of type.")
        _absolute_box(entry.get("box"), f"{label}.box")
    out["persistent"] = [dict(entry) for entry in persistent
                          if isinstance(entry, dict)]
    return out


def _check_motion(beat: dict, label: str) -> None:
    """Check one layer's ARRIVAL: the character, and the magnitude it needs.

    The character says WHAT moves - the picture fades in, grows into
    place, or slides in - and the magnitude is the PROJECT's (AGENTS.md
    10.5): a picture that grows from 0.86 or slides from 0.2 frame-widths
    is a look somebody chose, and the engine has no number for it.

    So `scale` and `slide` REFUSE without their magnitude, and `fade`
    and `cut` need none: a fade is a fade whatever its size.
    """
    entrance = str(beat.get("entrance") or "fade").strip().lower()
    if entrance not in BEAT_ENTRANCES:
        raise ExplainerError(
            f"{label}.entrance is {entrance!r}; it must be one of "
            f"{list(BEAT_ENTRANCES)}. How a picture arrives is the "
            f"declaration's (AGENTS.md 10.5).")
    motion = beat.get("motion") or {}
    if not isinstance(motion, dict):
        raise ExplainerError(
            f"{label}.motion must be a mapping, got "
            f"{type(motion).__name__}.")
    if entrance == "scale" and motion.get("scale_from") is None:
        raise ExplainerError(
            f"{label}.entrance is 'scale' and states no "
            f"motion.scale_from. How small the picture is before it grows "
            f"is the declaration's; the engine states no size of its own "
            f"(AGENTS.md 10.5).")
    if entrance == "slide" and motion.get("from") is None:
        raise ExplainerError(
            f"{label}.entrance is 'slide' and states no motion.from. "
            f"Where the picture comes from is the declaration's; the "
            f"engine states no direction of its own (AGENTS.md 10.5).")
    if motion.get("scale_from") is not None:
        try:
            value = float(motion["scale_from"])
        except (TypeError, ValueError):
            raise ExplainerError(
                f"{label}.motion.scale_from is {motion['scale_from']!r}, "
                f"which is not a number.") from None
        if not 0 < value <= 1:
            raise ExplainerError(
                f"{label}.motion.scale_from is {value}; a picture that "
                f"grows from nothing is a scale of 0, and one that "
                f"shrinks is not an arrival.")
    if motion.get("from") is not None:
        pair = motion["from"]
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2):
            raise ExplainerError(
                f"{label}.motion.from is {pair!r}; it must be two numbers, "
                f"[x, y] in world units (one world unit is one delivery "
                f"width).")
        try:
            (float(pair[0]), float(pair[1]))
        except (TypeError, ValueError):
            raise ExplainerError(
                f"{label}.motion.from is {pair!r}, which does not hold two "
                f"numbers.") from None


def normalise_declaration(raw: Any) -> dict:
    """Check a declaration and fill in nothing.

    Raises `ExplainerError` naming the field.  A malformed declaration
    RAISES rather than being dropped, for the reason
    `bookends.assert_no_invented_bookends` gives: a declaration that
    vanishes into a log line is how the 4th Wall end card survived four
    months.
    """
    if is_beat_declaration(raw):
        return normalise_beats_declaration(raw)
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


# ── The beat: one PICTURE, landing on the words it illustrates ─────────
#
# A stage is COPY.  A beat is a PICTURE.
#
# The stage route above draws the reel's own words as the thing that
# appears: `plan_entries` builds ONE entry whose `copy` runs are the
# stages.  That is a caption rail that arrives in pieces, and the
# captain's verdict on the first full-screen explainer was exactly that
# - "it was just the subtitles animated into the screen ... there were
# no pictures or visual elements that actually composed an animation".
#
# A beat is the other thing: a PICTURE (a project-owned still, drawn by
# the engine's own scene renderer) that arrives on the words it
# illustrates, with the words themselves reduced to a supporting label.
# The picture is the idea; the label names it.
#
# The rules the beat route keeps, because they are the ones that made
# the stage route trustworthy:
#
# - the beat is put in time by a SEARCH over the reel's own WORD
#   stream, never by a number the declaration states (AGENTS.md 6);
# - a beat whose quote the reel does not say is REFUSED, by name;
# - the picture COMPLETES on the word, it does not begin there - the
#   measured rule of `docs/ANIMATION_FIRST_REFERENCE.md` §1, where the
#   reference's illustrations arrive 67-839 ms BEFORE the noun they
#   illustrate;
# - the OFFSET between the picture and the word is MEASURED off the
#   rendered file, per beat, and reported (`measure_beat_offsets`).

BEATS_KEY = "beats"
"""The declaration key the beat route reads.

`effect.explainer.beats` - an ordered list of beats.  It is the WHOLE
of the beat route: a declaration that carries it is a scene of pictures,
and a declaration that does not is the stage route above.  Never both:
two shapes in one slot is a declaration nobody wrote.
"""


@dataclass(frozen=True)
class Beat:
    """One picture, and the words it illustrates.

    `at_seconds` is the reel second of the WORD the beat is anchored to
    - the picture's LANDING, not its start.  `start_seconds` is when
    the picture begins to arrive, which is `lead_seconds` earlier: the
    picture builds INTO the word rather than following it.
    """

    index: int
    quote: str
    at_seconds: float
    label: str = ""
    """Supporting text at most.  A label names the picture; it is never
    the sentence the captions are already drawing."""
    asset: str = ""
    """The project-owned still - the picture.  A static graphic IS a
    still (the captain's rule), and the engine draws no artwork of its
    own (AGENTS.md 14)."""
    box: Tuple[int, int, int, int] = ()
    """The still's ABSOLUTE CANVAS BOX, in delivery-frame pixels:
    `(left, top, right, bottom)`.  Absolute, because a picture placed
    by a share of the frame moves when the frame's aspect changes, and
    a composition that has to be looked at is placed in pixels."""
    label_box: Tuple[int, int, int, int] = ()
    """The label's absolute canvas box.  Required when the beat carries
    a label: the engine does not choose where a label sits."""
    lead_seconds: float = 0.0
    """How long BEFORE the word the picture starts arriving."""
    entrance: str = ""
    """The character of the arrival.  Its ramp is what the character
    MEANS in this renderer, the way `RAMP_FRAMES` is what `fade` means
    in `MotionGraphics`."""
    exit: str = ""
    precision: str = ""
    layers: Tuple[dict, ...] = ()
    """The beat's LAYERS - each a picture at its own absolute box.

    One layer is the shorthand; a list is a COMPOSED scene.  A beat of
    one icon with the canvas empty around it is not a composition."""
    stay_seconds: float = 0.0
    """How long the picture holds on after its landing, so the NEXT
    beat's picture builds beside it instead of replacing it."""
    motion: dict = field(default_factory=dict)
    """The MAGNITUDE of the arrival - the project's, never the engine's
    (AGENTS.md 10.5)."""
    elements: Tuple[dict, ...] = ()
    """The beat's ELEMENTS - each a picture or a run of type at its own
    absolute canvas box.

    A beat of one icon with the canvas empty around it is not a
    composition; a beat of several elements that build, connect and
    move into the next beat is."""
    connectors: Tuple[dict, ...] = ()
    """The LINES between the beat's elements, which draw as they
    arrive."""
    steps: Tuple[dict, ...] = ()
    """The beat's STEPS - each a moment inside the beat, anchored to
    its OWN word.

    A beat that arrives whole and then holds is one composition held
    for six seconds, and the viewer sees nothing happen while the
    sentence is still being said.  A step is a piece of the beat that
    arrives on its own word: the picture builds, one element at a
    time, every second or so the words say something new."""

    @property
    def start_seconds(self) -> float:
        return max(0.0, self.at_seconds - float(self.lead_seconds))

    def as_dict(self) -> dict:
        return {"index": self.index, "quote": self.quote,
                "at_seconds": round(self.at_seconds, 3),
                "start_seconds": round(self.start_seconds, 3),
                "lead_seconds": round(self.lead_seconds, 3),
                "label": self.label, "asset": self.asset,
                "box": list(self.box), "label_box": list(self.label_box),
                "entrance": self.entrance, "exit": self.exit,
                "layers": [dict(layer) for layer in self.layers],
                "elements": [dict(element) for element in self.elements],
                "connectors": [dict(c) for c in self.connectors],
                "steps": [dict(step) for step in self.steps],
                "stay_seconds": round(self.stay_seconds, 3),
                "motion": dict(self.motion),
                "precision": self.precision}


@dataclass
class AnchoredBeats:
    """What anchoring produced, including everything it refused."""

    beats: List[Beat] = field(default_factory=list)
    refused: List[dict] = field(default_factory=list)
    reel_seconds: float = 0.0

    def refuse(self, beat_text: str, reason: str, detail: str = "") -> None:
        if reason not in REFUSALS:
            raise ExplainerError(
                f"{reason!r} is not a reason a beat may be refused for. "
                f"REFUSALS is the whole of it: {list(REFUSALS)}.")
        self.refused.append(
            {"beat": beat_text, "reason": reason, "detail": detail})

    def as_dict(self) -> dict:
        return {"beats": [b.as_dict() for b in self.beats],
                "refused": list(self.refused),
                "reel_seconds": round(self.reel_seconds, 3)}


#: How long each entrance CHARACTER takes, in frames.
#:
#: A character, not a magnitude: these are the drawing of the words
#: `fade`, `draw` and `blur` in this renderer, the same reading
#: `MotionGraphics.RAMP_FRAMES` gets and for the same reason - a
#: declaration chooses the character and never these numbers.
BEAT_RAMP_FRAMES: Dict[str, int] = {
    "cut": 0,
    "fade": 8,
    "slide": 8,
    "scale": 8,
    "mask": 8,
    "draw": 12,
    "blur": 10,
    "flip": 12,
    "glitch": 10,
}

#: The characters a picture may ARRIVE in.  What moves, not by how much:
#: the magnitude is the declaration's (AGENTS.md 10.5).
BEAT_ENTRANCES: Tuple[str, ...] = ("cut", "fade", "scale", "slide")


def beat_ramp_seconds(entrance: str, fps: float) -> float:
    """The arrival ramp of one entrance character, in seconds.

    Absent character means no ramp at all: the picture is there when
    the word is, which is what `cut` means.
    """
    frames = BEAT_RAMP_FRAMES.get(str(entrance or "cut").lower())
    if frames is None:
        raise ExplainerError(
            f"{entrance!r} is not an entrance character this renderer "
            f"draws; known: {sorted(BEAT_RAMP_FRAMES)}.")
    return frames / float(fps or 0.0)


def _absolute_box(raw: Any, label: str) -> Tuple[int, int, int, int]:
    """One absolute canvas box, or a refusal by name.

    ABSOLUTE, in delivery-frame pixels: `(left, top, right, bottom)`.
    The captain's rule for this work - a static graphic is a still and
    its position is an absolute canvas box - and the reason it is not a
    share of the frame: a picture placed by a share moves when the
    frame changes, and a composition is looked at.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ExplainerError(
            f"{label} must be an absolute canvas box - four numbers, "
            f"[left, top, right, bottom] in delivery-frame pixels - got "
            f"{raw!r}.")
    try:
        left, top, right, bottom = (int(v) for v in raw)
    except (TypeError, ValueError):
        raise ExplainerError(
            f"{label} must be four whole pixels, got {raw!r}.") from None
    if right <= left or bottom <= top:
        raise ExplainerError(
            f"{label} is {raw!r}, which has no area. A box with no area "
            f"draws nothing, and a picture with no area is a hole in the "
            f"frame rather than a picture.")
    return (left, top, right, bottom)


def plan_beats(declarations: Sequence[Any], lines: Sequence[dict],
               reel_seconds: float,
               resolve_asset=None, fps: Optional[float] = None) -> AnchoredBeats:
    """Put each declared beat on the word it illustrates.

    `declarations` is the declaration's own `beats` list.  `lines` is
    the reel's own lines with `at` in REEL seconds and the WORD
    timings inside them - the same table `anchor_stages` reads, so a
    quote that grounds for a stage grounds for a beat.

    `resolve_asset` maps a declared asset name to a path on disk (or
    to nothing).  A beat whose still is not there is REFUSED, because a
    picture that is not on disk is a hole in the frame, and a hole in
    the frame is not a picture.

    The refusals are the stage route's three, plus two of its own:

    - the quote is not in the reel's words -> `ungrounded`
    - the reel says this beat BEFORE the one before it -> `out_of_order`
    - the beat is anchored at or past the reel's end -> `off_the_end`
    - the beat names no picture -> `no_picture`
    - the beat's picture is not on disk -> `asset_not_found`

    `fps` is the reel's own rate, and it is needed only when a beat
    names an entrance character and no lead: the lead is then the
    CHARACTER's ramp (`beat_ramp_seconds`), which is what the character
    means in this renderer and not a number anybody chose.
    """
    out = AnchoredBeats(reel_seconds=float(reel_seconds or 0.0))
    # The element ids seen so far, and the CONTENT each one carried: an
    # id is ONE element, drawn once and moved from beat to beat.
    _seen_elements: Dict[str, tuple] = {}
    if reel_seconds is None or float(reel_seconds) <= 0:
        raise ExplainerError(
            "a reel with no length has no second to anchor a beat at; "
            "reel_seconds comes from the plan's own ranges, never from a "
            "default.")

    haystacks: List[Tuple[float, str]] = []
    for line in lines or []:
        says = _normalise(line.get("says") or line.get("text"))
        if not says:
            continue
        at = line.get("at", line.get("reel_start"))
        if at is None:
            raise ExplainerError(
                "a line with no `at` cannot anchor anything. `at` is reel "
                "seconds and is what the build already hands the judge; a "
                "line without it is a contract break, not a missing "
                "default.")
        haystacks.append((float(at), says))
    if not haystacks:
        raise ExplainerError(
            "no lines to anchor against. The reel's own words are the "
            "only thing a beat can be grounded in.")

    stream = _word_stream(lines)
    previous = -1.0
    for index, raw in enumerate(declarations or [], start=1):
        if not isinstance(raw, dict):
            raise ExplainerError(
                f"a beat must be a mapping, got {type(raw).__name__}.")
        quote = _collapse(str(raw.get("quote") or ""))
        text = _collapse(str(raw.get("label") or raw.get("text") or quote))
        if not quote:
            out.refuse(text, UNGROUNDED,
                       "the beat carries no quote, so nothing puts it in "
                       "time. A beat is anchored by the words it "
                       "illustrates or not at all.")
            continue
        asset = str(raw.get("asset") or "").strip()
        declared_layers = raw.get("layers")
        if not asset and not declared_layers and not raw.get("elements"):
            out.refuse(text, NO_PICTURE,
                       "the beat names no asset, so it draws no picture. "
                       "A beat IS a picture; text that arrives in pieces "
                       "is a caption rail (the captain's verdict on the "
                       "first full-screen explainer).")
            continue
        if asset and resolve_asset is not None:
            found = resolve_asset(asset)
            if not found:
                out.refuse(text, ASSET_NOT_FOUND,
                           f"{asset!r} does not resolve to a file on disk. "
                           f"A picture that is not there is a hole in the "
                           f"frame.")
                continue
        needle = _normalise(quote)
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
                       f"said at {at:.2f}s, before the previous beat at "
                       f"{previous:.2f}s - the reel says these beats in a "
                       f"different order than the declaration lists them")
            continue
        box = (_absolute_box(raw.get("box"), f"beats[{index}].box")
               if raw.get("box") else ())
        label_box: Tuple[int, int, int, int] = ()
        if raw.get("label"):
            label_box = _absolute_box(raw.get("label_box"),
                                      f"beats[{index}].label_box")
        # The beat's LAYERS.  A beat with a `layers:` list is a composed
        # scene; a beat with an `asset` is the shorthand for a list of
        # one.  Every layer's asset must resolve, or the beat is
        # REFUSED: a layer that is not there is a hole in the frame.
        layers: List[dict] = []
        declared_layers = raw.get("layers")
        if declared_layers:
            for number, layer in enumerate(declared_layers, start=1):
                name = str(layer.get("asset") or "").strip()
                if resolve_asset is not None and not resolve_asset(name):
                    out.refuse(text, ASSET_NOT_FOUND,
                               f"{name!r} does not resolve to a file on "
                               f"disk. A picture that is not there is a "
                               f"hole in the frame.")
                    layers = []
                    break
                layers.append({
                    "asset": name,
                    "box": tuple(int(v) for v in layer.get("box")),
                    "entrance": str(layer.get("entrance") or ""),
                    "motion": dict(layer.get("motion") or {}),
                    "stay_seconds": float(layer.get("stay_seconds")
                                          or raw.get("stay_seconds") or 0.0),
                })
            if not layers:
                continue
        else:
            layers = [{"asset": asset, "box": box, "entrance": "",
                       "motion": {}}]
        # The beat's ELEMENTS: the composed scene.  Each is a picture or
        # a run of type at its own absolute box, and one may CARRY an
        # element from an earlier beat - it MOVES into this beat
        # instead of disappearing and reappearing.
        elements: List[dict] = []
        for number, element in enumerate(raw.get("elements") or [], start=1):
            element_id = str(element.get("id") or f"beat-{index}-{number}")
            # The CONTENT is what must not change under one id: a
            # different BOX is the element MOVING, which is the point
            # of `carry:`; a different picture or a different title is
            # two elements wearing one name.
            content = (str(element.get("asset") or ""),
                       json.dumps(element.get("text"), sort_keys=True))
            seen = _seen_elements.setdefault(element_id, content)
            if seen != content:
                out.refuse(text, DUPLICATE_ELEMENT,
                           f"the element id {element_id!r} is used again "
                           f"with different content. One id is ONE "
                           f"element: an element that is drawn once and "
                           f"moved from beat to beat carries its FIRST "
                           f"content, and a title that keeps the first "
                           f"beat's words for the whole reel is a lie.")
                elements = []
                break
            if not isinstance(element, dict):
                raise ExplainerError(
                    f"beats[{index}].elements[{number}] must be a "
                    f"mapping, got {type(element).__name__}.")
            name = str(element.get("asset") or "").strip()
            if name and resolve_asset is not None and not resolve_asset(name):
                out.refuse(text, ASSET_NOT_FOUND,
                           f"{name!r} does not resolve to a file on disk. "
                           f"A picture that is not there is a hole in the "
                           f"frame.")
                elements = []
                break
            elements.append({
                "id": str(element.get("id") or f"beat-{index}-{number}"),
                "asset": name,
                "text": element.get("text"),
                "box": tuple(int(v) for v in (element.get("box") or ())),
                "entrance": str(element.get("entrance") or ""),
                "motion": dict(element.get("motion") or {}),
                "carry": str(element.get("carry") or ""),
                "size": element.get("size"),
                "colour": element.get("colour"),
                "weight": element.get("weight"),
                "stay_seconds": float(element.get("stay_seconds")
                                      or raw.get("stay_seconds") or 0.0),
                "drift": tuple(float(v) for v in (element.get("drift")
                                                  or (0.0, 0.0))),
                "pulse": dict(element.get("pulse") or {}),
                "spin": element.get("spin"),
                "upright": bool(element.get("upright")),
            })
        if raw.get("elements") and not elements:
            continue
        connectors: List[dict] = []
        for number, connector in enumerate(raw.get("connectors") or [],
                                           start=1):
            if not isinstance(connector, dict):
                raise ExplainerError(
                    f"beats[{index}].connectors[{number}] must be a "
                    f"mapping, got {type(connector).__name__}.")
            connectors.append({
                "from": tuple(connector.get("from") or ()),
                "to": tuple(connector.get("to") or ()),
                "colour": str(connector.get("colour") or ""),
                "thickness": connector.get("thickness"),
                "angle": connector.get("angle"),
                "draw": connector.get("draw", True),
                "entrance": str(connector.get("entrance") or ""),
            })
            if not layers:
                continue
        else:
            layers = [{"asset": asset, "box": box, "entrance": "",
                       "motion": {}}]
        entrance = str(raw.get("entrance") or "")
        # The LEAD is the longest ramp among the beat's arrivals: the
        # beat's own entrance and every layer's.  The picture BUILDS
        # INTO the word, so the build starts a ramp earlier and ends on
        # it - which is what the reference does ("a word completes on
        # its syllable. It does not begin there").
        characters = [entrance] if entrance else []
        characters.extend(str(layer.get("entrance") or "")
                          for layer in layers)
        lead = raw.get("lead_seconds")
        if lead is None:
            characters = [c for c in characters if c]
            if characters:
                if fps is None:
                    raise ExplainerError(
                        f"beats[{index}] names the entrance "
                        f"{characters[0]!r} and no `lead_seconds`. How "
                        f"early the picture starts arriving is the "
                        f"character the declaration chose, and the "
                        f"character needs the reel's rate to be drawn - "
                        f"pass `fps`.")
                lead = max(beat_ramp_seconds(c, fps) for c in characters)
            else:
                lead = 0.0
        # The beat's STEPS: each one is anchored to its OWN word, so
        # the beat builds over the words it covers instead of arriving
        # whole and holding.
        steps: List[dict] = []
        step_previous = -1.0
        # A step is searched from the BEAT's own word onward: the step
        # belongs to this beat, so a word the reel says twice is
        # anchored to the saying INSIDE this beat.
        beat_index = _anchor_index(_normalise(quote), stream)
        for number, step in enumerate(raw.get("steps") or [], start=1):
            label = f"beats[{index}].steps[{number}]"
            if not isinstance(step, dict):
                raise ExplainerError(
                    f"{label} must be a mapping, got "
                    f"{type(step).__name__}.")
            step_quote = _collapse(str(step.get("quote") or ""))
            if not step_quote:
                raise ExplainerError(
                    f"{label} carries no quote, so nothing puts it in "
                    f"time. A step is anchored by the words it draws.")
            step_at, step_precision = _anchor_one(
                _normalise(step_quote), stream, haystacks,
                start=beat_index)
            if step_at is None:
                out.refuse(text, UNGROUNDED,
                           f"{step_quote!r} (step {number}) is not in the "
                           f"words this reel plays")
                steps = []
                break
            if step_at < step_previous:
                out.refuse(text, OUT_OF_ORDER,
                           f"step {number} is said at {step_at:.2f}s, "
                           f"before the step before it at "
                           f"{step_previous:.2f}s")
                steps = []
                break
            step_elements = []
            for element in (step.get("elements") or []):
                name = str(element.get("asset") or "").strip()
                if name and resolve_asset is not None \
                        and not resolve_asset(name):
                    out.refuse(text, ASSET_NOT_FOUND,
                               f"{name!r} does not resolve to a file on "
                               f"disk. A picture that is not there is a "
                               f"hole in the frame.")
                    steps = []
                    break
                step_elements.append({
                    "id": str(element.get("id")
                              or f"beat-{index}-step-{number}"),
                    "asset": name,
                    "text": element.get("text"),
                    "box": tuple(int(v) for v in (element.get("box") or ())),
                    "entrance": str(element.get("entrance") or ""),
                    "motion": dict(element.get("motion") or {}),
                    "drift": tuple(float(v) for v in (element.get("drift")
                                                      or (0.0, 0.0))),
                    "pulse": dict(element.get("pulse") or {}),
                    "spin": element.get("spin"),
                    "upright": bool(element.get("upright")),
                })
            if steps == [] and step_elements == [] and (step.get("elements")
                                                       or []):
                break
            steps.append({
                "index": number,
                "quote": step_quote,
                "at_seconds": step_at,
                "precision": step_precision,
                "elements": step_elements,
                "connectors": [dict(c) for c in (step.get("connectors") or [])],
            })
            step_previous = step_at
        if steps == [] and (raw.get("steps") or []):
            continue
        previous = at
        out.beats.append(Beat(
            index=index, quote=quote, at_seconds=at,
            label=str(raw.get("label") or ""), asset=asset, box=box,
            label_box=label_box,
            lead_seconds=float(lead),
            entrance=entrance,
            exit=str(raw.get("exit") or ""), precision=precision,
            layers=tuple(layers),
            elements=tuple(elements),
            connectors=tuple(connectors),
            steps=tuple(steps),
            stay_seconds=float(raw.get("stay_seconds") or 0.0),
            motion=dict(raw.get("motion") or {})))
    return out


def _world_box(box: Sequence[int], width: int, height: int) -> dict:
    """An absolute canvas box, as the scene renderer's world units.

    ONE world unit is one delivery WIDTH at zoom 1
    (`remotion-subtitles/src/compositions/StagedScene`), so both axes
    divide by the width.  The conversion happens here and nowhere else,
    which is what keeps the DECLARATION in absolute pixels - the unit a
    person places a picture in - and the renderer in its own units.
    """
    left, top, right, bottom = (int(v) for v in box)
    unit = float(width or 1)
    return {
        "x": ((left + right) / 2.0 - unit / 2.0) / unit,
        "y": ((top + bottom) / 2.0 - float(height or 1) / 2.0) / unit,
        "width": (right - left) / unit,
    }


def beat_scene_props(beats: Sequence[Beat], declaration: dict, *,
                     width: int, height: int, fps: float,
                     duration_frames: int, start_seconds: float = 0.0,
                     font_family: str = "") -> dict:
    """The scene props for a beat route, ready to render.

    The scene is a WORLD, and a world is a COMPOSED FRAME - not one
    picture with a caption under it.  What the scene holds:

    - the PERSISTENT furniture (`persistent:`) - the header, the rule,
      the progress mark: the things that are on the frame for the whole
      piece, so the frame is never bare between beats;
    - the ELEMENTS (`elements:`) - per beat, each a picture or a run of
      type at its own absolute canvas box, each with the character it
      arrives in.  An element that names `carry:` CONTINUES from an
      earlier element: it does not disappear and reappear, it MOVES,
      which is how one beat becomes the next;
    - the CONNECTORS (`connectors:`) - the lines between the elements,
      which DRAW (their length grows) over the beat's ramp;
    - the LABEL - the supporting line of type, at most.

    Every element's track is ONE track with the beat's landing as its
    keyframe: an element that is carried from one beat to the next is
    drawn once and MOVES, and one that is not is drawn, held and
    leaves.

    The declaration owns every colour, every size, every box and the
    camera.  The engine owns the timing arithmetic and the layer
    shapes, and states no look of its own (AGENTS.md 10.5).

    The props are the scene renderer's own (`StagedScene`), so a beat
    route is drawn by the SAME renderer the animation IR is lowered
    into - not by a second one.
    """
    layers: List[dict] = []
    label_size = float(declaration.get("label_size") or 0)
    label_weight = int(declaration.get("label_weight") or 700)
    label_colour = str(declaration.get("label_colour") or "#FFFFFF")
    ordered = list(beats)
    prefix = str(declaration.get("asset_prefix") or "brand/")

    # ── the persistent furniture ─────────────────────────────────────
    for number, entry in enumerate(declaration.get("persistent") or [],
                                   start=1):
        layer = _scene_layer(entry, prefix, width, height, fps,
                             label_size or 26.0, label_weight, label_colour)
        if layer is None:
            continue
        placement = layer["placement"]
        placement["opacity"] = [{"at": 0.0, "value": 1.0}]
        # The persistent furniture may MOVE for the whole piece - a
        # progress mark that travels is the piece's own clock, and the
        # magnitude and the direction are the declaration's.
        drift = tuple(float(v) for v in (entry.get("drift") or (0.0, 0.0)))
        if drift != (0.0, 0.0):
            end = max(0.0, duration_frames / float(fps or 1.0))
            for axis, index in (("x", 0), ("y", 1)):
                if isinstance(placement.get(axis), (int, float)):
                    placement[axis] = [
                        {"at": 0.0, "value": float(placement[axis])},
                        {"at": end,
                         "value": float(placement[axis]) + drift[index]},
                    ]
        layer["id"] = f"persistent-{number}"
        layers.append(layer)

    # ── the elements, merged across the beats they carry through ────
    #
    # An element belongs to a BEAT or to a STEP inside it, and a step
    # has its own word: the beat's own elements arrive with the beat,
    # and each step's elements arrive with that step's word, so the
    # picture builds over the words it covers.
    appearances: Dict[str, List[Tuple[Any, dict, float, float]]] = {}
    order: List[str] = []
    for beat in ordered:
        entries = [(entry, _beat_arrival(beat, start_seconds),
                    _beat_landing(beat, start_seconds))
                   for entry in _beat_elements(beat)]
        for step in (getattr(beat, "steps", None) or ()):
            step_arrival = (float(step.get("at_seconds") or 0.0)
                            - float(start_seconds))
            step_landing = step_arrival
            for entry in (step.get("elements") or ()):
                entries.append((entry, step_arrival, step_landing))
        for entry, arrival_at, landing_at in entries:
            key = str(entry.get("id") or f"beat-{beat.index}")
            if key not in appearances:
                appearances[key] = []
                order.append(key)
            appearances[key].append((beat, entry, arrival_at, landing_at))

    for key in order:
        apps = appearances[key]
        if not apps:
            continue
        first_beat, first_entry, arrival, landing = apps[0]
        last_beat, last_entry = apps[-1][0], apps[-1][1]
        next_arrival = None
        next_beat = None
        if ordered.index(last_beat) + 1 < len(ordered):
            next_beat = ordered[ordered.index(last_beat) + 1]
            next_arrival = _beat_arrival(next_beat, start_seconds)
        stay = float(first_entry.get("stay_seconds",
                                     getattr(last_beat, "stay_seconds", 0.0))
                     or 0.0)
        exit_at = next_arrival
        if stay:
            exit_at = max(exit_at or 0.0, landing + stay)
        ramp = beat_ramp_seconds(first_entry.get("entrance") or "fade", fps)
        # The EXIT, and it is where the transition is decided:
        #
        # - a PICTURE CARRIES OUT.  It holds at full until the next
        #   beat's picture starts arriving and then fades as it pushes
        #   away, so the two overlap and the frame is never a dim
        #   empty rectangle between two beats (the captain's 7.0 s,
        #   26.5 s and 32.0 s, and the dim fades at 11.0 s, 17.0 s,
        #   23.5 s and 31.5 s).
        # - TYPE in a SHARED box CUTS: two titles in one box are the
        #   unreadable smear the captain saw in the footer.
        shared = _shares_ground(first_entry.get("box"), next_beat)
        cut_exit = bool(first_entry.get("text")) and shared
        if exit_at is not None:
            exit_at = max(landing, exit_at)
        layer = _scene_layer(first_entry, prefix, width, height, fps,
                             label_size or 26.0, label_weight, label_colour)
        if layer is None:
            continue
        layer["id"] = key
        placement = layer["placement"]
        # The box track: one keyframe per beat the element appears in,
        # each at that beat's LANDING - so a carried element is in its
        # new place when the word says so.
        for beat, entry, _arrival_at, landing_at in apps:
            box = _world_box(entry["box"], width, height)
            at = landing_at
            for axis in ("x", "y", "width"):
                placement[axis] = _put_key(placement.get(axis), at,
                                           box[axis])
        # The entrance: the character the FIRST appearance declares.
        entrance = str(first_entry.get("entrance") or "fade").lower()
        if entrance == "scale":
            width_track = placement["width"]
            if isinstance(width_track, list):
                full = float(width_track[0]["value"])
                scale_from = float((first_entry.get("motion") or {})
                                   .get("scale_from") or 1.0)
                placement["width"] = [
                    {"at": max(0.0, arrival), "value": full * scale_from},
                ] + list(width_track)
        elif entrance == "slide":
            pair = (first_entry.get("motion") or {}).get("from") or [0.0, 0.0]
            for axis, index in (("x", 0), ("y", 1)):
                track = placement.get(axis)
                if isinstance(track, list) and track:
                    rest = float(track[0]["value"])
                    placement[axis] = [
                        {"at": max(0.0, arrival),
                         "value": rest + float(pair[index])},
                    ] + list(track)
        # The PULSE: the element keeps CHANGING for as long as it is on
        # the frame - it breathes.  A beat whose picture arrives and then
        # stands still leaves the frame unchanged for the seconds the
        # word is still being said, and the captain's frames showed
        # exactly that ("0.5-6.5 s is the same frame").  The engine
        # SAMPLES the pulse into keyframes, because the scene renderer's
        # tracks are keyframed and nothing else is needed to make a
        # picture move every frame of its life.
        # The last beat has no next beat, so it has no exit - but it
        # still LIVES until the end of the scene, and a picture that
        # stops breathing when its beat is the last one is a still.
        life_end = (exit_at if exit_at is not None
                    else max(landing, duration_frames / float(fps or 1.0)))
        pulse = first_entry.get("pulse") or {}
        if pulse:
            _apply_pulse(placement, pulse, landing, life_end, fps)
        spin = first_entry.get("spin")
        if spin:
            _apply_spin(placement, float(spin), landing, life_end, fps)
        # The DRIFT: the element keeps MOVING for as long as it is on
        # the frame.  An element that arrives and then stands still is a
        # card, and a card is not an animation - the tutorial's lesson
        # (`docs/evidence/explainer_beats.md` §the lesson) is that the
        # motion is CONTINUOUS, not a sequence of arrivals.  The
        # magnitude and the direction are the declaration's.
        drift = tuple(float(v) for v in (first_entry.get("drift")
                                          or (0.0, 0.0)))
        if (drift != (0.0, 0.0)) and exit_at is not None:
            for axis, index in (("x", 0), ("y", 1)):
                track = placement.get(axis)
                if isinstance(track, list) and track:
                    rest = float(track[-1]["value"])
                    placement[axis] = list(track) + [
                        {"at": max(0.0, exit_at),
                         "value": rest + drift[index]},
                    ]
        # The EXIT: the element leaves THE WAY IT CAME - it shrinks back
        # out of a scale, slides back out of a slide - over the exit's
        # own ramp and by the SAME declared magnitude, because the
        # engine holds no number of its own (AGENTS.md 10.5).  A frame
        # that cuts from one idea to the next is not a transformation.
        if exit_at is not None and ramp > 0:
            # The push-out happens as the NEXT picture arrives, not
            # before it: the old picture is still in place when the
            # new one starts to come in, so the two overlap and the
            # frame is never empty between two beats.
            fade_start = max(0.0, exit_at)
            if entrance == "scale":
                track = placement.get("width")
                if isinstance(track, list) and track:
                    full = float(track[-1]["value"])
                    scale_from = float((first_entry.get("motion") or {})
                                       .get("scale_from") or 1.0)
                    placement["width"] = list(track) + [
                        {"at": fade_start, "value": full},
                        {"at": max(0.0, exit_at + ramp),
                         "value": full * scale_from},
                    ]
            elif entrance == "slide":
                pair = (first_entry.get("motion") or {}).get("from") \
                    or [0.0, 0.0]
                for axis, index in (("x", 0), ("y", 1)):
                    track = placement.get(axis)
                    if isinstance(track, list) and track:
                        rest = float(track[-1]["value"])
                        placement[axis] = list(track) + [
                            {"at": fade_start, "value": rest},
                            {"at": max(0.0, exit_at),
                             "value": rest + float(pair[index])},
                        ]
        # The opacity: in on the first word, out with the last.
        opacity = [{"at": max(0.0, arrival), "value": 0.0},
                   {"at": max(0.0, landing), "value": 1.0}]
        if exit_at is not None:
            if cut_exit:
                # TYPE in a shared box CUTS: two titles in one box are
                # the unreadable smear the captain saw in the footer.
                cut = min(ramp, 0.12)
                opacity += [{"at": max(0.0, exit_at - cut), "value": 1.0},
                            {"at": max(0.0, exit_at), "value": 0.0}]
            else:
                # A PICTURE carries out: it holds at full until the
                # next picture starts arriving, then fades as it pushes
                # away - so the two overlap and the frame is never a
                # dim empty rectangle between two beats (the captain's
                # 7.0 s, 26.5 s and 32.0 s, and the dim fades at
                # 11.0 s, 17.0 s, 23.5 s and 31.5 s).
                opacity += [{"at": max(0.0, exit_at), "value": 1.0},
                            {"at": max(0.0, exit_at + ramp), "value": 0.0}]
        placement["opacity"] = opacity
        layers.append(layer)

    # ── the connectors: the lines that draw between the elements ────
    for beat in ordered:
        landing = _beat_landing(beat, start_seconds)
        arrival = _beat_arrival(beat, start_seconds)
        for number, connector in enumerate(beat.connectors or [], start=1):
            rule = _connector_rule(connector, beat, arrival, landing,
                                   width, height, fps)
            if rule is not None:
                rule["id"] = f"connector-{beat.index}-{number}"
                layers.append(rule)
        # A step's connectors draw with the STEP, on the step's word.
        for step in (getattr(beat, "steps", None) or ()):
            step_landing = (float(step.get("at_seconds") or 0.0)
                            - float(start_seconds))
            for number, connector in enumerate(step.get("connectors") or [],
                                               start=1):
                rule = _connector_rule(connector, beat, step_landing,
                                       step_landing, width, height, fps)
                if rule is not None:
                    rule["id"] = (f"connector-{beat.index}-"
                                  f"step-{step.get('index', number)}"
                                  f"-{number}")
                    layers.append(rule)

    # ── the supporting label ─────────────────────────────────────────
    for beat in ordered:
        if not (beat.label and beat.label_box):
            continue
        landing = _beat_landing(beat, start_seconds)
        arrival = _beat_arrival(beat, start_seconds)
        opacity = [{"at": max(0.0, arrival), "value": 0.0},
                   {"at": max(0.0, landing), "value": 1.0}]
        # The label LEAVES with its beat.  A label that stays on the
        # frame after its beat is over prints itself over the NEXT
        # beat's label in the same box, and the smear that follows is
        # unreadable (the captain's frames, 2026-10-21).
        position = ordered.index(beat)
        exit_at = None
        if position + 1 < len(ordered):
            exit_at = _beat_arrival(ordered[position + 1], start_seconds)
        ramp = beat_ramp_seconds(beat.exit or "cut", fps)
        if exit_at is not None:
            fade_start = max(landing, exit_at - ramp)
            opacity += [{"at": fade_start, "value": 1.0},
                        {"at": max(fade_start, exit_at), "value": 0.0}]
        label_placement = _world_box(beat.label_box, width, height)
        label_placement["opacity"] = opacity
        layers.append({
            "kind": "text",
            "id": f"label-{beat.index}",
            "runs": [{
                "text": beat.label,
                # World units: the renderer scales type with the
                # camera, and the declaration states the size in
                # delivery-frame pixels.
                "size": label_size / float(width or 1),
                "weight": label_weight,
                "colour": label_colour,
                "revealAt": max(0.0, landing),
            }],
            "placement": label_placement,
        })
    props: dict = {
        "ground": {"colour": str(declaration.get("ground") or "#000000")},
        "layers": layers,
        "fontFamily": font_family or str(declaration.get("font_family") or ""),
        "fps": float(fps),
        "width": int(width),
        "height": int(height),
        "durationInFrames": int(duration_frames),
    }
    if declaration.get("camera"):
        props["camera"] = declaration["camera"]
    return props


def _beat_elements(beat: "Beat") -> List[dict]:
    """The beat's ELEMENTS: its `elements:` list, or its own asset.

    The shorthand - a beat with an `asset` and a `box` - is a beat of
    one element, and it is the same shape the list has.
    """
    elements = getattr(beat, "elements", None)
    if elements:
        return [dict(entry) for entry in elements]
    if getattr(beat, "asset", ""):
        return [{"id": f"beat-{beat.index}", "asset": beat.asset,
                 "box": beat.box, "entrance": beat.entrance,
                 "motion": dict(getattr(beat, "motion", {}) or {})}]
    return []


def _beat_landing(beat: "Beat", start_seconds: float) -> float:
    return beat.at_seconds - float(start_seconds)


def _beat_arrival(beat: "Beat", start_seconds: float) -> float:
    return beat.start_seconds - float(start_seconds)


def _put_key(track: Any, at: float, value: float) -> List[dict]:
    """One more keyframe on a track, in time order.

    A track is a list of `{at, value}` pairs; the renderer eases
    between them and holds flat outside them, so a track that names
    two places is a MOVE and a track that names one is a PLACE.
    """
    keys = list(track) if isinstance(track, list) else []
    keys = [k for k in keys if float(k["at"]) < at - 1e-9]
    keys.append({"at": at, "value": value})
    return keys


def _shares_ground(box: Optional[Sequence[int]],
                   other: Optional[Any]) -> bool:
    """Do two elements want the same part of the frame?

    Two boxes that OVERLAP cannot be on the frame together: the second
    prints itself over the first, and two titles in the same box are
    the unreadable smear the captain saw in the footer.
    """
    if not box or other is None:
        return False
    left, top, right, bottom = (int(v) for v in box)
    for entry in _beat_elements(other):
        other_box = entry.get("box")
        if not other_box or len(other_box) != 4:
            continue
        o_left, o_top, o_right, o_bottom = (int(v) for v in other_box)
        if (left < o_right and o_left < right
                and top < o_bottom and o_top < bottom):
            return True
    for step in (getattr(other, "steps", None) or ()):
        for entry in (step.get("elements") or ()):
            other_box = entry.get("box")
            if not other_box or len(other_box) != 4:
                continue
            o_left, o_top, o_right, o_bottom = (int(v) for v in other_box)
            if (left < o_right and o_left < right
                    and top < o_bottom and o_top < bottom):
                return True
    return False


def _track_value_at(track: Any, at: float) -> float:
    """The value a keyframed track holds at `at`.

    The tracks here are flat (a value changes at a keyframe and holds
    until the next), which is what the scene renderer draws.
    """
    if isinstance(track, (int, float)):
        return float(track)
    value = 0.0
    for key in track or ():
        if float(key["at"]) <= at:
            value = float(key["value"])
        else:
            break
    return value


def _apply_spin(placement: dict, degrees_per_second: float, landing: float,
                exit_at: float, fps: float) -> None:
    """The element TURNS for as long as it is on the frame.

    A picture that only breathes can still read as a still; a picture
    that turns cannot.  The rate is the declaration's - the engine
    states no rate of its own (AGENTS.md 10.5) - and it is sampled into
    the rotation track the scene renderer already reads.
    """
    start = max(0.0, landing)
    end = max(start + 1e-6, exit_at)
    step = max(1, int(round(float(fps) / 8.0)))
    at = start
    while at < end:
        placement["rotate"] = _put_key(
            placement.get("rotate"), at,
            degrees_per_second * (at - start))
        at += step / float(fps or 1.0)


def _apply_pulse(placement: dict, pulse: dict, landing: float,
                 exit_at: float, fps: float) -> None:
    """Sample a PULSE into the placement's tracks.

    `scale` is how much the element breathes (a share of its own size)
    and `float` how far it rises and falls (world units); `period` is
    how long one breath takes.  All three are the declaration's - the
    engine states no rate of its own (AGENTS.md 10.5).  The samples are
    keyframes on the tracks the scene renderer already reads, so a
    breathing element needs no new drawing code.
    """
    import math

    scale = float(pulse.get("scale") or 0.0)
    rise = float(pulse.get("float") or 0.0)
    period = float(pulse.get("period") or 0.0)
    if period <= 0 or (scale == 0.0 and rise == 0.0):
        return
    start = max(0.0, landing)
    end = max(start + 1e-6, exit_at)
    step = max(1, int(round(float(fps) * period / 6.0)))
    samples: List[float] = []
    at = start
    while at < end:
        samples.append(at)
        at += step / float(fps or 1.0)
    if not samples:
        return
    width_track = placement.get("width")
    y_track = placement.get("y")
    for at in samples:
        phase = 2.0 * math.pi * ((at - start) / period)
        breath = math.sin(phase)
        if scale:
            base = _track_value_at(width_track, at)
            placement["width"] = _put_key(
                placement.get("width"), at, base * (1.0 + scale * breath))
        if rise:
            base = _track_value_at(y_track, at)
            placement["y"] = _put_key(
                placement.get("y"), at, base - rise * breath)


def _scene_layer(entry: dict, prefix: str, width: int, height: int,
                 fps: float, label_size: float, label_weight: int,
                 label_colour: str) -> Optional[dict]:
    """One element, as a scene layer: an IMAGE or a run of TYPE.

    A picture is a project-owned still at an absolute canvas box; type
    is a list of runs, each with its own colour and size, so a title
    can be half in the accent colour - the composition's own way of
    saying which half matters.
    """
    box = entry.get("box")
    if not box or len(box) != 4:
        return None
    placement = _world_box(box, width, height)
    text = entry.get("text")
    if text:
        runs = []
        if isinstance(text, str):
            text = [{"text": text}]
        for run in text:
            runs.append({
                "text": str(run.get("text") or ""),
                # World units: the renderer scales type with the camera.
                "size": float(run.get("size") or label_size) / float(width or 1),
                "weight": int(run.get("weight") or label_weight),
                "colour": str(run.get("colour") or label_colour),
                "newLine": bool(run.get("new_line") or run.get("newLine")),
            })
        if not runs:
            return None
        return {"kind": "text", "runs": runs, "placement": placement}
    asset = str(entry.get("asset") or "").strip()
    if not asset:
        return None
    return {
        "kind": "image",
        "src": prefix + os.path.basename(asset),
        "placement": placement,
    }


def _connector_rule(connector: dict, beat: "Beat", arrival: float,
                    landing: float, width: int, height: int,
                    fps: float) -> Optional[dict]:
    """A CONNECTOR: the line between two points, drawn as it draws.

    The engine draws the line from the two points the declaration
    gives - it draws no diagram of its own - and the line DRAWS: its
    length grows from nothing to the whole distance over the beat's
    ramp, so the connection between two things arrives WITH the word.
    """
    start = connector.get("from")
    end = connector.get("to")
    if not start or not end or len(start) != 2 or len(end) != 2:
        return None
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    unit = float(width or 1)
    dx, dy = (x1 - x0) / unit, (y1 - y0) / unit
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0:
        return None
    thickness = float(connector.get("thickness") or 6) / unit
    angle = 0.0
    if connector.get("angle") is not None:
        angle = float(connector["angle"])
    else:
        import math
        angle = math.degrees(math.atan2(dy, dx))
    ramp = beat_ramp_seconds(connector.get("entrance") or "fade", fps)
    draw = connector.get("draw", True)
    length_track = [{"at": max(0.0, arrival), "value": 0.0},
                    {"at": max(0.0, landing), "value": length}]
    if not draw:
        length_track = [{"at": max(0.0, landing), "value": length}]
    return {
        "kind": "rule",
        "x": ((x0 + x1) / 2.0 - unit / 2.0) / unit,
        "y": ((y0 + y1) / 2.0 - float(height or 1) / 2.0) / unit
        - thickness / 2.0,
        "length": length_track,
        "angle": angle,
        "thickness": thickness,
        "colour": str(connector.get("colour") or "#F2C94C"),
        "opacity": [{"at": max(0.0, arrival), "value": 0.0},
                    {"at": max(0.0, landing), "value": 1.0}],
    }


def beat_window(beats: Sequence[Beat], reel_seconds: float,
                hold_seconds: float = 0.0,
                window: Optional[Sequence[float]] = None) -> Tuple[float, float]:
    """The reel seconds the scene covers: `(start, end)`.

    The WHOLE reel by default: an explainer over a reel's own audio is
    a full-screen composition, and a scene that started at its first
    picture would leave the reel's opening on the picture it was
    supposed to replace.  A declaration may narrow it with `window`,
    and then the scene is that stretch of the reel.

    The window is the SPAN of the composition: a scene that ended
    before its last picture would cut it off, so the declared hold
    after the last beat is inside it.
    """
    if window:
        start = max(0.0, float(window[0]))
        end = min(float(reel_seconds or 0.0), float(window[1]))
    else:
        start, end = 0.0, float(reel_seconds or 0.0)
    if beats:
        # The hold is a FLOOR on the end, never a ceiling on the reel:
        # a window that closed before the last picture would cut it off.
        end = max(end, min(float(reel_seconds or 0.0),
                           max(b.at_seconds for b in beats)
                           + float(hold_seconds or 0.0)))
    if end <= start:
        return (0.0, 0.0)
    return (start, end)


# ── Measuring what the render actually did ───────────────────────────

def measure_beat_offsets(rendered_path: str, beats: Sequence[Beat], *,
                         width: int, height: int, fps: float,
                         start_seconds: float = 0.0,
                         ground: str = "") -> List[dict]:
    """What each beat's picture ACTUALLY did, against the word it illustrates.

    Read off the RENDERED file, not off the plan: a plan says when a
    picture was SUPPOSED to arrive, and the only thing that settles
    when it did is the picture itself.  For every beat this returns

    - `arrival_seconds` - the first frame the beat's box carries
      anything other than the ground, measured by the difference
      between that frame and the ground's own tone;
    - `landing_seconds` - the first frame the box is as full as it ever
      gets, which is the frame the picture is COMPLETE;
    - `arrival_offset_ms` / `landing_offset_ms` - those two against the
      word the beat illustrates.  NEGATIVE means the picture led the
      word, which is what the reference does (67-839 ms ahead of the
      noun, `docs/ANIMATION_FIRST_REFERENCE.md` §1) and what "the picture
      completes on the word" means.

    The measurement is deliberately coarse - a quarter-scale luma
    stream, and a threshold on the deviation from the ground - because
    it answers "did the picture arrive with the word", not "is the
    picture beautiful".

    `start_seconds` is the reel second the rendered file's frame 0
    sits at: the render covers the scene's window, not the reel's
    first second, and the offsets are measured against the WORD, so
    the window's own offset has to come back out.
    """
    import subprocess

    import numpy as np

    beats = list(beats or ())
    if not beats:
        return []
    if not os.path.isfile(rendered_path):
        raise ExplainerError(
            f"cannot measure {rendered_path!r}: it is not on disk.")
    scale = 4
    small_w = max(1, int(width) // scale)
    small_h = max(1, int(height) // scale)
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", rendered_path,
         "-vf", f"scale={small_w}:{small_h}", "-pix_fmt", "gray",
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=600, check=False)
    if result.returncode != 0 or not result.stdout:
        raise ExplainerError(
            f"could not read frames of {rendered_path}: "
            f"{result.stderr[-200:] if result.stderr else 'no output'}")
    frame_bytes = small_w * small_h
    count = len(result.stdout) // frame_bytes
    if count <= 0:
        raise ExplainerError(
            f"{rendered_path} holds no readable frames.")
    frames = np.frombuffer(result.stdout[:count * frame_bytes],
                           dtype=np.uint8).reshape(count, small_h, small_w)

    measured: List[dict] = []
    # ONE deviation curve per DISTINCT box.  Beats that share a box
    # share a curve - they take turns in the same place - so the curve
    # is SEGMENTED into the stretches in which something was drawn, and
    # each beat is matched to the segment that carries it.  A curve per
    # beat would read the first picture's arrival for every beat, which
    # is a measurement of nothing.
    curves: Dict[Tuple[int, int, int, int], Any] = {}
    for beat in beats:
        key = _measure_box(beat)
        if not key:
            continue
        if key in curves:
            continue
        left, top, right, bottom = key
        x0 = max(0, min(small_w - 1, int(left) // scale))
        x1 = max(x0 + 1, min(small_w, int(right) // scale))
        y0 = max(0, min(small_h - 1, int(top) // scale))
        y1 = max(y0 + 1, min(small_h, int(bottom) // scale))
        region = frames[:, y0:y1, x0:x1].astype(np.int16)
        # The ground's own tone is what the region holds when no
        # picture is there: the MODE over the clip - the tone the
        # region spends most of its pixels on.  The MEDIAN is wrong
        # here and measurably so: a picture that is on screen for half
        # the clip puts the median BETWEEN the two tones, so every
        # frame of bare ground reads as a deviation.
        counts = np.bincount(region.reshape(-1), minlength=256)
        ground_level = float(int(np.argmax(counts)))
        deviation = np.abs(region - ground_level).max(axis=(1, 2))
        peak = float(deviation.max())
        # The CHANGE in the box from one frame to the next: the
        # reading that survives a crossfade between two beats, where
        # the box never empties.
        difference = np.zeros(len(region), dtype=np.float64)
        if len(region) > 1:
            difference[1:] = np.abs(
                region[1:].astype(np.float64)
                - region[:-1].astype(np.float64)).mean(axis=(1, 2))
        curves[key] = {"deviation": deviation, "peak": peak,
                       "difference": difference}

    for beat in beats:
        key = _measure_box(beat)
        curve = curves.get(key)
        if curve is None:
            measured.append({
                "index": beat.index, "quote": beat.quote,
                "word_seconds": round(beat.at_seconds, 3),
                "arrival_seconds": None, "landing_seconds": None,
                "arrival_offset_ms": None, "landing_offset_ms": None,
                "note": "the beat has no box to measure",
            })
            continue
        deviation = curve["deviation"]
        difference = curve["difference"]
        peak = float(curve["peak"])
        word_in_scene = beat.at_seconds - float(start_seconds)
        if peak < 1.0:
            measured.append({
                "index": beat.index, "quote": beat.quote,
                "word_seconds": round(beat.at_seconds, 3),
                "arrival_seconds": None, "landing_seconds": None,
                "arrival_offset_ms": None, "landing_offset_ms": None,
                "peak_deviation": round(peak, 2),
                "note": "nothing was drawn in this beat's box",
            })
            continue
        # The ARRIVAL: the frame in which the box changed MOST around
        # the beat's own arrival.  The change is the reading, because
        # the transition between two beats is now a CROSSFADE: the old
        # picture is still on the frame when the new one arrives, so
        # "the first frame with ink" would read the old picture and
        # "the first empty frame" never comes.
        expected_arrival = (beat.start_seconds - float(start_seconds)) * float(fps)
        window = max(2, int(0.45 * float(fps)))
        low = max(1, int(expected_arrival) - window)
        high = min(len(difference) - 1, int(expected_arrival) + window)
        if high <= low:
            low, high = 1, max(2, len(difference) - 1)
        arrival_frame = int(low + int(np.argmax(difference[low:high])))
        # The LANDING: the first frame the picture is COMPLETE - its own
        # local peak, not the clip's (a dark picture on a dark ground
        # never reaches the brightness of a white one).
        stretch_end = min(len(deviation) - 1, arrival_frame + int(1.6 * fps))
        local_peak = float(deviation[arrival_frame:stretch_end + 1].max()) \
            if stretch_end > arrival_frame else peak
        landed = np.where(deviation[arrival_frame:stretch_end + 1]
                          >= max(3.0, local_peak * 0.90))[0]
        landing_frame = (arrival_frame + int(landed[0])) if len(landed) \
            else None
        arrival_seconds = arrival_frame / float(fps)
        landing_seconds = (landing_frame / float(fps)
                           if landing_frame is not None else None)
        measured.append({
            "index": beat.index,
            "quote": beat.quote,
            "word_seconds": round(beat.at_seconds, 3),
            "arrival_seconds": round(arrival_seconds, 3),
            "landing_seconds": (round(landing_seconds, 3)
                                if landing_seconds is not None else None),
            "arrival_offset_ms": round(
                (arrival_seconds - word_in_scene) * 1000.0, 1),
            "landing_offset_ms": (
                round((landing_seconds - word_in_scene) * 1000.0, 1)
                if landing_seconds is not None else None),
            "peak_deviation": round(peak, 2),
        })
    return measured


def _measure_box(beat: "Beat") -> Tuple[int, int, int, int]:
    """The box a beat is MEASURED in: its own, or its first layer's.

    A beat that carries `layers:` has no box of its own - each layer
    has one - and the beat is measured in the box of the layer that
    carries the picture the beat is about.
    """
    if beat.box:
        return tuple(int(v) for v in beat.box)
    for layer in (getattr(beat, "layers", None) or ()):
        box = tuple(int(v) for v in (layer.get("box") or ()))
        if len(box) == 4:
            return box
    elements = list(getattr(beat, "elements", None) or ())
    # The box the beat is measured in is the box of an element that
    # ARRIVES with it - one that does not CARRY from an earlier beat.
    # A carried element is on the frame from its first beat, so its box
    # is never empty and cannot say when THIS beat's picture arrived.
    for element in elements:
        if element.get("carry"):
            continue
        box = tuple(int(v) for v in (element.get("box") or ()))
        if len(box) == 4:
            return box
    for element in elements:
        box = tuple(int(v) for v in (element.get("box") or ()))
        if len(box) == 4:
            return box
    return ()


def _segments(deviation, peak: float) -> List[Tuple[int, int]]:
    """The stretches of the clip in which something was drawn.

    A segment is a run of frames whose deviation from the ground is at
    least half the peak - half, because a picture arriving is not the
    same as a picture complete, and the two are measured apart.
    """
    if peak < 1.0:
        return []
    threshold = max(3.0, peak * 0.5)
    runs: List[Tuple[int, int]] = []
    start = None
    for index, value in enumerate(deviation):
        if value >= threshold:
            if start is None:
                start = index
        elif start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(deviation) - 1))
    return runs


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
        if os.path.getsize(still) <= 0:
            raise ExplainerError(
                f"could not read a frame of {overlay_path}: ffmpeg "
                "exited 0 but left an empty file - a capture that did "
                "not happen (see marker_capture's WHEN THE ROUTE FAILS).")
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
    beats: Optional[AnchoredBeats] = None
    """The BEAT route's anchored beats, when the declaration asked for
    one.  Empty for the stage route, which is a different shape of
    explainer and never both."""
    scene: Optional[dict] = None
    """The scene props the beat route renders, when they were built."""
    offsets: List[dict] = field(default_factory=list)
    """What each beat's picture ACTUALLY did against the word it
    illustrates, measured off the rendered file."""
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
            "beats": self.beats.as_dict() if self.beats else None,
            "scene": self.scene,
            "offsets": list(self.offsets),
            "band": self.band,
            "segments": [
                {"overlay_path": s.get("overlay_path"),
                 "segment_id": s.get("segment_id"),
                 "placement_label": s.get("placement_label"),
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
                     bands: Optional[PictureBands] = None,
                     resolve_asset=None,
                     fps: Optional[float] = None) -> ExplainerPlan:
    """Author one reel's explainer from that reel's own data.

    Everything here is the reel's: its judgement, its lines, its length.
    Nothing is carried over from another reel and nothing is invented.

    Two routes, never both: a declaration carrying `beats` is the BEAT
    route - a scene of PICTURES, each landing on the words it
    illustrates - and a declaration without is the STAGE route, whose
    stages are copy runs.
    """
    if declaration is None:
        return ExplainerPlan(reel_name=reel_name, declared=False,
                             basis=NOT_DECLARED)
    declared = normalise_declaration(declaration)
    if not declared:
        return ExplainerPlan(reel_name=reel_name, declared=False,
                             basis=NOT_DECLARED)

    if BEATS_KEY in declared:
        rows = list(lines or lines_for_reel(judgement, reel_number))
        anchored = plan_beats(declared[BEATS_KEY], rows, reel_seconds,
                              resolve_asset=resolve_asset, fps=fps)
        if not anchored.beats:
            return ExplainerPlan(reel_name=reel_name, declared=True,
                                 beats=anchored, basis=ALL_REFUSED)
        return ExplainerPlan(reel_name=reel_name, declared=True,
                             beats=anchored, basis=PLANNED)

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
"""The reel video track an explainer segment is placed on - in the FULL
layout (V1/V2 picture, V3 captions, V4 transitions).

Rows pack, so the plan's explainer row is what the placer reads; this
stays as the legacy fallback and the message default where no placed
item names a row. Under the captain's Reel 09 ruling the picture rows
are per angle with the frame row above them, so a reel wearing the
look and carrying transitions places its explainer on V6.

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
    """Record every reel this build touched, including the empty ones.

    Merged, not overwritten: every reel THIS build touched is replaced
    by what it placed - or by its empty basis, when it placed none -
    and every other reel's record stands. Overwriting would delete the
    baselines a partial (`only`) build did not touch, and F21 would
    then grade those timelines against an absence - which returns
    nothing, a silent coverage loss. The same merge
    `reel_semantic_visual.write_records` keeps for the semantic layer.
    """
    import os
    from library.tools.project_layout import Area, ProjectLayout
    out_dir = ProjectLayout(project_folder).write_dir(Area.REVIEW)
    path = os.path.join(str(out_dir), PLAN_FILENAME)
    stored: dict = {"format": "explainer_plans/1", "plans": []}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                stored = json.load(handle) or stored
        except (OSError, ValueError):
            stored = {"format": "explainer_plans/1", "plans": []}
    touched = {plan.reel_name for plan in (plans or ())}
    kept = [p for p in (stored.get("plans") or [])
            if str(p.get("reel")) not in touched]
    kept.extend(plan.as_dict() for plan in (plans or ()))
    stored["plans"] = kept
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)
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
    promotion renames the claim to the final timeline name. A plan the
    previous build left under the final name is REPLACED, not kept
    beside the renamed one - two plans for one reel leave
    `plan_for_reel` reading the stale first. Plans for reels outside
    `mapping` are untouched. No file yet is a no-op - a build that drew
    nothing for any reel recorded nothing.
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
    finals = set(mapping.values())
    payload["plans"] = [plan for plan in (payload.get("plans") or [])
                        if plan.get("reel") not in finals]
    for plan in payload["plans"]:
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
