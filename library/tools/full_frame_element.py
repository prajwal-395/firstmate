"""Full-frame elements: the whole frame, for a bounded stretch, in place of picture.

What a full-frame element IS
----------------------------
Everything the overlay layer draws is ADDITIVE: a caption card, a lower
third, a counter, a timed text moment.  It sits over picture and the
picture keeps playing underneath.  ``motion_graphics_vocabulary`` owns
that layer and states the boundary in its own words - *"a full frame of
artwork is a bookend"* - and files ``intro_card`` and ``end_card`` under
:data:`motion_graphics_vocabulary.OUT_OF_VOCABULARY` for exactly this
reason.

This module is the other side of that boundary, for the REELS process.
A full-frame element:

- occupies the WHOLE delivery frame, so there is nothing to composite over;
- holds for a BOUNDED stretch of the reel's own time;
- **replaces picture rather than overlaying it** - it is a picture item on
  the reel's own picture track, not a layer above one.

Why REPLACE and not overlay at full opacity
-------------------------------------------
This was the open question and it is settled here with two measurements,
not a preference.

**1. The reels path cannot silence what plays under an overlay.**
An overlay at full opacity hides the picture and leaves the SOUND.  The
footage under it goes on talking, so the viewer watches a card while a
half-sentence plays.  The only repair is an audio level, and AGENTS.md 5
records the probe's verdict verbatim: *"The scripting API cannot set an
audio level, and that is a COMPLETE enumeration."*
(``library/steps/step_6_01_render/probe_resolve_capabilities.py``).  So
overlay is not a worse answer here, it is an unimplementable one - the
defect would be permanent and unfixable from the reels path.  A card that
occupies its own stretch of reel time cannot run over speech, because
there is no speech at those seconds to run over.

**2. An overlay is invisible to every check this product has.**
``reel_conformance_verifier`` counts picture items on V1 and V2 only
(``check_item_count``), grades delivered framing on V1 and V2 only
(``check_delivered_framing``), and takes the UNION of every picture track
for holes (``check_picture_holes``) - so an opaque card on a track above
V2 adds coverage where coverage already existed and changes no count and
no framing verdict.  It would ship a capability that nothing can see,
which is the defect class this repository has spent a week removing
(AGENTS.md 10.4).  On V1 the card is inside all three, and each of them
had to be taught what it is - see :mod:`library.tools.reel_conformance_verifier`
and ``docs/FULL_FRAME_ELEMENTS.md``.

The third reading - that this is already how the master does it - is the
confirmation rather than the argument.  ``bookends`` turns a declared card
into a V1 spine block *"which is what puts it inside the coverage
assertion and the manifest duration"*.  Reels had no equivalent because
they have no ``mesh_spine``; this is that missing half.

Why Remotion and not Fusion
---------------------------
Also settled with evidence rather than preference, and written up in
``docs/FULL_FRAME_ELEMENTS.md``.  In short: every Fusion route in this
repository is ``TimelineItem.ImportFusionComp(path)``, which attaches a
comp to a clip that already exists - a full-frame element has no carrier
clip to attach to; ``library/tools/fusion/`` emits no text node of any
kind, so it cannot draw type at all; and AGENTS.md 5 forbids creating a
timeline and calling ``ImportFusionComp`` in one process, which is exactly
what ``reel_build.build_reel_timeline`` does.  Remotion already renders
1080x1920 to a FILE, and a file is a media pool item, which is a timeline
clip - the carrier this needs.

A declaration, and no taste
---------------------------
The engine draws what a declaration states and states nothing itself.  No
colour, no typeface, no duration, no motion character and no copy has a
default here, for the reason ``house_look.py`` was emptied (AGENTS.md
10.5).  What a declaration looks like, in a project's own ``project.yaml``
(the PROJECT wins, the same precedence and the same reason as
``timed_text_overlay.resolve_declaration``: copy the viewer reads is
ARTWORK and artwork belongs to the project - AGENTS.md 14)::

    effect:
      full_frame_elements:
        - element: full_frame_card
          placement: head            # head | tail
          duration_seconds: 2.0
          background: "#101014"
          entrance: blur             # a motion character; absent means `cut`
          exit: fade
          font_family: Montserrat
          runs:
            - bind: opening_line     # or: text: "..."
              type_role: display
              colour: "#FFFFFF"

Copy is DECLARED or BOUND, never written here
---------------------------------------------
A run either carries literal ``text`` - the project's own words - or
``bind``s to a fact the pipeline already produces
(:data:`COPY_BINDINGS`).  A binding is a QUOTATION, not an invention: the
opening line is the words the reel itself plays, read back through
``reel_opening.opening_words``.  This module names no producer of copy
beyond that enumeration, and a binding that resolves to nothing REFUSES
rather than drawing an empty run - the same rule
``motion_graphics_plan`` applies to an entry with no readable parameter.

Step 3.04's handoff says titling a reel is *"not yours"* to the model that
chooses it, so nothing in the reels path may author a title.  That is why
there is a binding table and not a title generator.

How a declaration reaches the picture, in order
-----------------------------------------------
1. :func:`resolve_declaration` reads the project's ``project.yaml`` (or
   the brand template's ``effect`` slot) and :func:`declared_elements`
   normalises it, raising on anything malformed.
2. :func:`plan_reel_cards` turns each declaration into a CARD - the props,
   the reel seconds it occupies, and the file it will be rendered to -
   using facts measured off the reel itself (:class:`ReelFacts`).
3. :func:`render_reel_cards` renders each one through Remotion, OPAQUE
   (no ``--transparent``): a full-frame element is the picture, so it
   carries its own ground rather than relying on black showing through an
   alpha channel that nothing is beneath.
4. ``reel_build.build_reel_timeline`` places each card on **V1**, and
   ``reel_build.lead_seconds`` shifts every other placement and every
   caption by the head cards' total, so one clock moves together.
5. ``reel_conformance_verifier`` grades the card as the picture item it
   is: inside F1, counted by F4, and exempt from F12 for a stated reason
   rather than by omission.

``tests/test_full_frame_element.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

from library.tools import motion_graphics_vocabulary as _mg
from library.tools.render_fonts import font_is_deliverable, static_font_path
from library.tools.safe_area import resolve_safe_area

# The Remotion composition that draws a full-frame element.  Registered in
# `remotion-subtitles/src/Root.tsx`.
FULL_FRAME_COMPOSITION = "FullFrameCard"

#: Where rendered cards go, under the project's reel scratch area.  The
#: leaf name only; the caller owns the path it joins this onto, the same
#: shape `timed_text_render.TIMED_TEXT_RENDER_DIRNAME` takes.
FULL_FRAME_RENDER_DIRNAME = "reel_cards"

#: A full-frame card is a card, not an act.  The same reading, and the
#: same number, as `bookends.MAX_BOOKEND_SECONDS`: past this it is not a
#: card in front of a reel, it is a segment the plan should be choosing as
#: content.  Mechanical rather than editorial - it bounds what the shape
#: MEANS, and says nothing about what looks good at 1.5s versus 4s.
MAX_CARD_SECONDS = 30.0

# A card is rendered at the delivery format's own rate.  Not a choice:
# a card at a different rate from the timeline it is placed on is the
# frame-blending defect F10 already refuses for a whole reel.
RENDER_TIMEOUT_SECONDS = 300


class FullFrameDeclarationError(ValueError):
    """A ``full_frame_elements`` declaration this module refuses.

    Raised rather than dropped, for the reason
    ``bookends.BookendDeclarationError`` is: a declaration that renders
    nothing is indistinguishable from no declaration at all, and the 4th
    Wall end card survived four months in exactly that state
    (``docs/ASSET_LIBRARY_PLAN.md``).
    """


class FullFrameRenderError(RuntimeError):
    """A declared card could not be turned into a file."""


# ── The roster ───────────────────────────────────────────────────────
#
# The same shape `motion_graphics_vocabulary.ROSTER` takes, and for the
# same reasons: reachability is REPORTED per entry rather than filtering
# membership, and `never` is not optional, because an entry that only
# says what a thing IS teaches a model to reach for it everywhere.

REACHABLE_NOW = "reachable_now"
NEEDS_RENDERER_WORK = "needs_renderer_work"
REACHABILITY = (REACHABLE_NOW, NEEDS_RENDERER_WORK)


@dataclass(frozen=True)
class FullFrameElementKind:
    """One kind of full-frame element, and what a declaration must fill."""

    key: str
    what: str
    axes: tuple[str, ...]
    copy: str
    """``required``, ``optional`` or ``none`` - whether the element needs a
    text payload at all.  WHERE that text comes from is
    :data:`COPY_BINDINGS` and the declaration, never this entry."""
    reachable: str
    reachability_note: str
    never: tuple[str, ...]
    """What this element is NOT for.  Not optional -
    :func:`assert_roster_is_well_formed` raises without it."""


ROSTER: tuple[FullFrameElementKind, ...] = (
    FullFrameElementKind(
        key="full_frame_card",
        what=("A full frame of type on a declared ground, holding for a "
              "declared stretch of the reel's own time, in place of "
              "picture rather than over it."),
        axes=("placement", "timing", "entrance", "exit", "copy",
              "type_role", "colour_role"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Drawn by the FullFrameCard Remotion composition and placed on "
            "V1 by reel_build.build_reel_timeline. Rendered and read back "
            "off a built timeline before this flag was set."),
        never=(
            "Never over speech: it occupies its own reel seconds, so there "
            "is no speech underneath it to talk over.",
            "Never a caption: the spoken word belongs to the caption "
            "system (library/tools/subtitle_style.py), and a card "
            "repeating a line the captions are already drawing puts two "
            "renderings of one sentence in one reel.",
            "Never artwork the engine supplies: the ground, the typeface "
            "and every word are the declaration's (AGENTS.md 14).",
            "Never mid-reel: a card between two speech ranges cuts a "
            "sentence in half. `placement` is head or tail, and "
            "`_normalise` refuses anything else by name.",
        ),
    ),
)

ELEMENTS_BY_KEY: dict[str, FullFrameElementKind] = {
    element.key: element for element in ROSTER}


#: Things that look like a full-frame element and belong to another
#: enumeration.  The boundary is the point, the same way
#: `motion_graphics_vocabulary.OUT_OF_VOCABULARY` is.
OUT_OF_VOCABULARY: dict[str, str] = {
    "intro_card": (
        "On the MASTER timeline a full frame of artwork is a bookend. "
        "library/tools/bookends.py owns the declaration and mesh_spine "
        "turns it into a V1 spine block. This module is the REELS half, "
        "which has no mesh_spine to insert into."
    ),
    "timed_text_moment": (
        "A line of type OVER the finished picture, not in place of it. "
        "library/tools/timed_text_overlay.py owns it and 6.01 places it "
        "on V6."
    ),
    "title_lockup": (
        "An additive overlay that names the piece while picture plays. "
        "library/tools/motion_graphics_vocabulary.py owns the whole "
        "overlay roster; the line between the two enumerations is whether "
        "picture keeps playing underneath."
    ),
    "shape_wipe_transition": (
        "A full frame of graphic BETWEEN two shots is a transition, and "
        "AGENTS.md 5 closes both non-Fusion routes to those. "
        "library/tools/transition_vocabulary.py is the one enumeration."
    ),
}


class FullFrameVocabularyError(ValueError):
    """A roster entry, or a declaration naming one, that this module refuses."""


def assert_roster_is_well_formed() -> None:
    """Every entry declares its refusals, its reachability and its axes.

    The runnable statement of the rule, and it can fail: see
    ``tests/test_full_frame_element.py``.
    """
    seen = set()
    for element in ROSTER:
        if element.key in seen:
            raise FullFrameVocabularyError(
                f"{element.key!r} is in the roster twice")
        seen.add(element.key)
        if element.key in OUT_OF_VOCABULARY:
            raise FullFrameVocabularyError(
                f"{element.key!r} is both in the roster and out of it")
        if not element.never:
            raise FullFrameVocabularyError(
                f"{element.key!r} records no refusals. An entry that only "
                f"says what a thing IS teaches a model to reach for it "
                f"everywhere.")
        if element.reachable not in REACHABILITY:
            raise FullFrameVocabularyError(
                f"{element.key!r} has reachability {element.reachable!r}; "
                f"known: {', '.join(REACHABILITY)}")
        if not element.reachability_note:
            raise FullFrameVocabularyError(
                f"{element.key!r} states no reachability note, so nothing "
                f"records what was verified or what is missing")
        if not element.axes:
            raise FullFrameVocabularyError(
                f"{element.key!r} names no axes, so a declaration has "
                f"nothing to fill")


# ── The declaration ──────────────────────────────────────────────────

PLACEMENTS = ("head", "tail")
"""Where a card sits in the reel's own time.

Head and tail only, and the reason is structural rather than editorial: a
reel is its keep ranges laid end to end, and a card between two of them
lands inside a sentence the editor made contiguous.  ``reel_build``'s own
``closer_seam`` documents the same seam from the caption side.
"""

MOTION_CHARACTERS = tuple(_mg.AXES_BY_NAME["entrance"].positions)
"""How a card arrives and leaves.

The OVERLAY roster's own axis, imported rather than respelled: there is
one vocabulary of motion character in this engine, and the composition
that draws these cards reuses the overlay composition's own
``entranceTransform`` for the same reason.
"""

NO_MOTION = "cut"
"""The motion character that draws no motion, and the ONLY default here.

Legal as a default under AGENTS.md 10.5's own carve-out: a value meaning
"nothing is drawn" is the absence of decoration, not a choice of it - the
same reading ``transition_vocabulary.CUT_TYPES`` and
``house_look.NEUTRAL_CDL`` are given.  Every other axis - colour, ground,
typeface, duration, copy - has no default and refuses when absent.
"""

TYPE_ROLES = tuple(_mg.AXES_BY_NAME["type_role"].positions)
"""The typographic weight of a run.  Also the overlay roster's own axis."""


#: What a run may BIND to instead of carrying literal text, and what each
#: one resolves from.  A quotation of something the pipeline measured, in
#: every case - never a phrase this engine wrote.
#:
#: A binding that resolves to nothing REFUSES the card by name.  It never
#: draws an empty run and never substitutes another binding's value:
#: "the card silently lost its headline" is the failure mode this table's
#: refusals exist to make impossible.
COPY_BINDINGS: dict[str, str] = {
    "opening_line": (
        "The reel's first spoken words, verbatim, as "
        "reel_opening.opening_words reads them off the ranges the build "
        "will actually play. A quotation of the reel's own speech."
    ),
    "speakers": (
        "The people on the reel, as the plan names them "
        "(ReelMoment.speakers), joined by the declaration's own separator."
    ),
    "reel_number": (
        "The reel's number in the plan (ReelMoment.number)."
    ),
}


@dataclass(frozen=True)
class ReelFacts:
    """What a card may quote, measured off the reel it will open.

    Built by ``reel_build`` from the moment and the ranges the build is
    about to place, so a bound run quotes the reel that exists rather than
    the span the plan asked for before bad takes came out of it.
    """

    reel_number: int
    speakers: tuple[str, ...] = ()
    opening: tuple[dict, ...] = ()
    """The reel's opening words, each with the REEL second it lands at."""
    opening_window: float = 0.0
    """How many seconds of opening :attr:`opening` was measured over.

    Carried so a card asking for a LONGER window is refused rather than
    silently handed a shorter line.  A quotation that is quietly not the
    quotation asked for is the failure this field exists to make loud."""

    @classmethod
    def from_moment(cls, moment, ranges, transcript: dict,
                    opening_seconds: Optional[float] = None) -> "ReelFacts":
        """The facts of one reel, measured off the ranges it will PLAY.

        Not off the span the plan asked for: the two differ the moment a
        bad take is cut out of the opening, which is how reel 02 came to
        open on "Yeah. So" (``reel_opening``).  A card quoting the span
        would quote words the reel does not contain.

        ``opening_seconds`` is the WIDEST window any of this reel's cards
        will quote - :func:`required_opening_window` reads it off the
        declarations.  It defaults to ``reel_opening.OPENING_SECONDS``,
        the same window that module already measures a hook in.
        """
        from library.tools import reel_opening

        seconds = (reel_opening.OPENING_SECONDS if opening_seconds is None
                   else float(opening_seconds))
        words = reel_opening.opening_words(ranges, transcript, seconds)
        return cls(
            reel_number=int(getattr(moment, "number", 0) or 0),
            speakers=tuple(getattr(moment, "speakers", ()) or ()),
            opening=tuple(dict(w) for w in words),
            opening_window=seconds,
        )

    def opening_line(self, seconds: Optional[float] = None) -> str:
        """The opening words as one line, up to ``seconds`` of the reel."""
        limit = self.opening_window if seconds is None else float(seconds)
        if limit > self.opening_window + 1e-9:
            raise FullFrameDeclarationError(
                f"a card asks for {limit}s of opening and reel "
                f"{self.reel_number}'s facts were measured over "
                f"{self.opening_window}s. Build ReelFacts with "
                f"required_opening_window(declarations) rather than "
                f"handing the card a shorter line than it declared.")
        return " ".join(str(w.get("word", ""))
                        for w in self.opening
                        if float(w.get("at", 0.0)) < limit).strip()

    def binding(self, name: str, seconds: Optional[float] = None) -> str:
        """The text a binding resolves to, or ``""`` when nothing did."""
        if name == "opening_line":
            return self.opening_line(seconds)
        if name == "speakers":
            return ", ".join(s for s in self.speakers if s)
        if name == "reel_number":
            return str(self.reel_number) if self.reel_number else ""
        raise FullFrameDeclarationError(
            f"{name!r} is not a copy binding; known: "
            f"{', '.join(sorted(COPY_BINDINGS))}")


def required_opening_window(declarations: Sequence[dict]) -> float:
    """The widest opening window any declaration quotes.

    ``reel_opening.OPENING_SECONDS`` when none of them widens it, so a
    project that declares nothing about the window gets the window the
    hook measurement already uses.
    """
    from library.tools import reel_opening

    widest = float(reel_opening.OPENING_SECONDS)
    for declaration in declarations or ():
        declared = declaration.get("opening_seconds")
        if declared:
            widest = max(widest, float(declared))
    return widest


@dataclass(frozen=True)
class PlannedCard:
    """One card, resolved against one reel, ready to render and place."""

    index: int
    element: str
    placement: str
    reel_start_frame: int
    """The REEL FRAME this card starts at, and the authority on where it
    goes.

    An integer, because a card's position is arithmetic on frame counts
    the plan has already rounded - never `round(seconds * fps)` at the
    placement site. The head card measured on reel 07 was placed one
    frame short by exactly that class of mistake, and a one-frame gap
    between a card and the clip after it is an F1 black hole."""
    duration_seconds: float
    duration_frames: int
    props: dict
    render_name: str
    resolved_runs: tuple[dict, ...] = field(default_factory=tuple)
    rendered_path: str = ""
    """The file this card was rendered to.  Empty until it has been.

    A card with no file has not been drawn, and `reel_build` REFUSES to
    place one rather than leaving a reel that starts on speech - which is
    indistinguishable from a project that declared no card at all."""

    @property
    def reel_end_frame(self) -> int:
        return self.reel_start_frame + self.duration_frames

    def reel_start(self, fps: float) -> float:
        """Where it starts in REEL seconds. Derived, never authoritative."""
        return self.reel_start_frame / fps

    def reel_end(self, fps: float) -> float:
        return self.reel_end_frame / fps


def resolve_declaration(brand_effect: dict[str, Any] | None,
                        project_folder: str | None) -> dict[str, Any]:
    """The effect dict the reel build plans from: the PROJECT's cards win.

    Same precedence and same reason as
    ``timed_text_overlay.resolve_declaration``: a card is copy the viewer
    reads, which ``docs/ASSET_LIBRARY_PLAN.md`` section 3 rules is
    ARTWORK, and artwork belongs to the project rather than to a series
    template.  The whole slot is replaced rather than merged key by key -
    half a card from each of two sources is a card nobody designed.
    """
    effect = dict(brand_effect or {})
    declared = _project_declaration(project_folder)
    if declared is None:
        return effect
    effect["full_frame_elements"] = declared
    return effect


def _project_declaration(project_folder: str | None):
    """``effect.full_frame_elements`` out of a project.yaml, or None."""
    if not project_folder:
        return None
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return None
    import yaml
    with open(project_yaml, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    effect = (config.get("effect") or {})
    if not isinstance(effect, dict):
        raise FullFrameDeclarationError(
            f"{project_yaml}: `effect` must be a mapping, got "
            f"{type(effect).__name__}")
    if "full_frame_elements" not in effect:
        return None
    return effect["full_frame_elements"]


def declared_elements(effect: dict[str, Any] | None) -> list[dict]:
    """Normalise ``effect.full_frame_elements`` into ordered declarations.

    ``[]`` for anything that declares nothing, which is every project
    unless someone opted in - the same shape ``content.bookends`` and
    ``effect.timed_text_overlay`` take.  Anything malformed RAISES.
    """
    raw = (effect or {}).get("full_frame_elements")
    if raw in (None, [], ()):
        return []
    if not isinstance(raw, (list, tuple)):
        raise FullFrameDeclarationError(
            f"full_frame_elements must be a list of declarations, got "
            f"{type(raw).__name__}")
    return [_normalise(entry, index)
            for index, entry in enumerate(raw, start=1)]


def _normalise(raw: Any, index: int) -> dict:
    """One declaration, checked field by field.  Refuses by name."""
    label = f"full_frame_elements[{index}]"
    if not isinstance(raw, dict):
        raise FullFrameDeclarationError(
            f"{label} must be a mapping, got {type(raw).__name__}")

    key = str(raw.get("element") or "").strip().lower()
    if not key:
        raise FullFrameDeclarationError(
            f"{label} names no `element`; known: "
            f"{', '.join(sorted(ELEMENTS_BY_KEY))}")
    if key not in ELEMENTS_BY_KEY:
        why = OUT_OF_VOCABULARY.get(key)
        raise FullFrameDeclarationError(
            f"{label} names element {key!r}, which this enumeration does "
            f"not draw. " + (why or f"Known: {', '.join(sorted(ELEMENTS_BY_KEY))}."))
    element = ELEMENTS_BY_KEY[key]
    if element.reachable != REACHABLE_NOW:
        raise FullFrameDeclarationError(
            f"{label} names element {key!r}, which the roster records as "
            f"{element.reachable}: {element.reachability_note}")

    placement = str(raw.get("placement") or "").strip().lower()
    if placement not in PLACEMENTS:
        raise FullFrameDeclarationError(
            f"{label} has placement={raw.get('placement')!r}; a card sits "
            f"at the {' or the '.join(PLACEMENTS)} of the reel. There is "
            f"no default, because where a card sits is an editorial "
            f"decision, and no mid-reel position exists because a card "
            f"between two keep ranges lands inside a sentence.")

    duration = raw.get("duration_seconds")
    if (not isinstance(duration, (int, float)) or isinstance(duration, bool)
            or duration <= 0):
        raise FullFrameDeclarationError(
            f"{label} has duration_seconds={duration!r}; a card that lasts "
            f"no time appears in no frame")
    if duration > MAX_CARD_SECONDS:
        raise FullFrameDeclarationError(
            f"{label} holds for {duration}s. Past {MAX_CARD_SECONDS}s it is "
            f"not a card in front of a reel, it is a segment the plan "
            f"should be choosing as content - the same reading "
            f"bookends.MAX_BOOKEND_SECONDS records.")

    background = str(raw.get("background") or "").strip()
    if not background:
        raise FullFrameDeclarationError(
            f"{label} states no `background`. A full-frame element IS the "
            f"picture, so it carries its own ground; there is nothing "
            f"underneath for an alpha channel to reveal, and the engine "
            f"declares no colour of its own (AGENTS.md 10.5).")

    entrance = str(raw.get("entrance") or NO_MOTION).strip().lower()
    exit_char = str(raw.get("exit") or NO_MOTION).strip().lower()
    for name, value in (("entrance", entrance), ("exit", exit_char)):
        if value not in MOTION_CHARACTERS:
            raise FullFrameDeclarationError(
                f"{label} has {name}={value!r}; the motion characters are "
                f"{', '.join(MOTION_CHARACTERS)}")

    font_family = str(raw.get("font_family") or "").strip()
    if not font_family:
        raise FullFrameDeclarationError(
            f"{label} names no `font_family`. A typeface is a per-series "
            f"decision that lives with the project (AGENTS.md 14) and the "
            f"engine states none.")
    font_file = raw.get("font_file")
    if not font_is_deliverable(font_family, font_file):
        raise FullFrameDeclarationError(
            f"{label} declares font_family={font_family!r} with "
            f"font_file={font_file!r}, which library/tools/render_fonts.py "
            f"cannot deliver. A substituted face is a valid picture of the "
            f"right size that nothing downstream can tell apart.")

    runs = raw.get("runs")
    if not isinstance(runs, (list, tuple)) or not runs:
        raise FullFrameDeclarationError(
            f"{label} declares no `runs`; a card with no copy on it draws "
            f"a coloured rectangle, and an element that draws nothing is "
            f"not rendered (AGENTS.md 10.2)")

    opening_seconds = raw.get("opening_seconds")
    if opening_seconds is not None:
        if (not isinstance(opening_seconds, (int, float))
                or isinstance(opening_seconds, bool) or opening_seconds <= 0):
            raise FullFrameDeclarationError(
                f"{label} has opening_seconds={opening_seconds!r}; the "
                f"window a bound `opening_line` quotes is a positive number "
                f"of seconds. Omit it and reel_opening.OPENING_SECONDS is "
                f"the window - the same one that module already measures a "
                f"hook in.")

    y = raw.get("y")
    if y is not None:
        if (not isinstance(y, (int, float)) or isinstance(y, bool)
                or not 0.0 <= float(y) <= 1.0):
            raise FullFrameDeclarationError(
                f"{label} has y={y!r}; a normalised position on the "
                f"delivery frame is between 0 and 1. Omit it and the runs "
                f"are centred in the safe box, which is the absence of a "
                f"position rather than a chosen one - there is no picture "
                f"underneath to clear.")

    return {
        "element": key,
        "placement": placement,
        "duration_seconds": float(duration),
        "background": background,
        "entrance": entrance,
        "exit": exit_char,
        "font_family": font_family,
        "font_file": str(font_file) if font_file else None,
        "runs": [_normalise_run(run, index, position)
                 for position, run in enumerate(runs, start=1)],
        "y": float(y) if y is not None else None,
        "separator": str(raw.get("separator") or ", "),
        "opening_seconds": (float(opening_seconds)
                            if opening_seconds is not None else None),
    }


def _normalise_run(raw: Any, card_index: int, run_index: int) -> dict:
    """One run of copy: literal text OR a binding, never both, never neither."""
    label = f"full_frame_elements[{card_index}].runs[{run_index}]"
    if not isinstance(raw, dict):
        raise FullFrameDeclarationError(
            f"{label} must be a mapping, got {type(raw).__name__}")

    text = raw.get("text")
    bind = raw.get("bind")
    if text is not None and bind is not None:
        raise FullFrameDeclarationError(
            f"{label} carries both `text` and `bind`. A run says its words "
            f"or names where they come from, not both - two sources for "
            f"one line is a line nobody chose.")
    if text is None and bind is None:
        raise FullFrameDeclarationError(
            f"{label} carries neither `text` nor `bind`. The engine writes "
            f"no copy of its own; a run states the project's words or "
            f"binds to one of: {', '.join(sorted(COPY_BINDINGS))}.")
    if bind is not None:
        binding = str(bind).strip()
        if binding not in COPY_BINDINGS:
            raise FullFrameDeclarationError(
                f"{label} binds to {binding!r}, which the pipeline does not "
                f"produce for a reel. Known bindings: "
                f"{', '.join(sorted(COPY_BINDINGS))}.")
    elif not str(text).strip():
        raise FullFrameDeclarationError(
            f"{label} has empty `text`; a run that draws no glyph is not "
            f"a run")

    type_role = str(raw.get("type_role") or "").strip().lower()
    if type_role not in TYPE_ROLES:
        raise FullFrameDeclarationError(
            f"{label} has type_role={raw.get('type_role')!r}; the "
            f"typographic weights are {', '.join(TYPE_ROLES)}, and there "
            f"is no default because a size the engine picked is a size "
            f"nobody chose")

    colour = str(raw.get("colour") or raw.get("color") or "").strip()
    if not colour:
        raise FullFrameDeclarationError(
            f"{label} states no `colour`. The engine ships no palette "
            f"(AGENTS.md 10.5) and will not draw a run in a colour nobody "
            f"declared.")

    size = raw.get("font_size")
    if size is not None and (not isinstance(size, (int, float))
                             or isinstance(size, bool) or size <= 0):
        raise FullFrameDeclarationError(
            f"{label} has font_size={size!r}; a size is a positive number "
            f"of pixels of the delivery frame")

    return {
        "text": str(text) if text is not None else None,
        "bind": str(bind).strip() if bind is not None else None,
        "type_role": type_role,
        "colour": colour,
        "font_size": float(size) if size is not None else None,
        "uppercase": bool(raw.get("uppercase", False)),
    }


# ── Planning one reel's cards ────────────────────────────────────────


def plan_reel_cards(declarations: Sequence[dict],
                    facts: ReelFacts,
                    body_frames: int,
                    fps: float,
                    width: int = 1080,
                    height: int = 1920,
                    project_folder: Optional[str] = None,
                    ) -> list[PlannedCard]:
    """Resolve every declaration against ONE reel, in the order it plays.

    ``body_frames`` is how long the reel's SPEECH runs, in the same
    integer frames ``reel_build.placements`` lays it down in - which is
    what a tail card is placed after.  Head cards are laid out from reel
    frame zero in declaration order; the total of their durations is what
    ``reel_build.lead_frames`` shifts everything else by.

    **In FRAMES throughout.**  A card's position is arithmetic on counts
    the plan has already rounded, so the card and the clip beside it abut
    exactly.  Seconds enter only when something asks for them.

    A binding that resolves to nothing raises here, at plan time, rather
    than rendering a card with a hole in it.
    """
    if not declarations:
        return []

    safe_area = resolve_safe_area(project_folder, width=width, height=height)
    heads = [d for d in declarations if d["placement"] == "head"]
    tails = [d for d in declarations if d["placement"] == "tail"]

    planned: list[PlannedCard] = []
    cursor = 0
    index = 0
    for declaration in heads:
        index += 1
        card = _plan_one(declaration, index, cursor, facts, fps,
                         width, height, safe_area)
        planned.append(card)
        cursor += card.duration_frames

    cursor = sum(c.duration_frames for c in planned) + int(body_frames)
    for declaration in tails:
        index += 1
        card = _plan_one(declaration, index, cursor, facts, fps,
                         width, height, safe_area)
        planned.append(card)
        cursor += card.duration_frames
    return planned


def _plan_one(declaration: dict, index: int, reel_start_frame: int,
              facts: ReelFacts, fps: float, width: int, height: int,
              safe_area) -> PlannedCard:
    resolved = tuple(_resolve_run(run, declaration, facts, index)
                     for run in declaration["runs"])
    duration_frames = int(round(declaration["duration_seconds"] * fps))
    if duration_frames <= 0:
        raise FullFrameDeclarationError(
            f"full_frame_elements[{index}] rounds to {duration_frames} "
            f"frames at {fps:.3f}fps; a card must occupy at least one")

    props: dict = {
        "runs": [dict(run) for run in resolved],
        "background": declaration["background"],
        "entrance": declaration["entrance"],
        "exit": declaration["exit"],
        "fontFamily": declaration["font_family"],
        "fps": fps,
        "width": width,
        "height": height,
        "durationInFrames": duration_frames,
        "safeArea": safe_area.as_props(),
    }
    if declaration["y"] is not None:
        props["y"] = declaration["y"]
    if declaration["font_file"]:
        props["fontFile"] = static_font_path(declaration["font_file"])

    return PlannedCard(
        index=index,
        element=declaration["element"],
        placement=declaration["placement"],
        reel_start_frame=int(reel_start_frame),
        duration_seconds=declaration["duration_seconds"],
        duration_frames=duration_frames,
        props=props,
        render_name=f"reel_{facts.reel_number:02d}_card_{index:02d}",
        resolved_runs=resolved,
    )


def _resolve_run(run: dict, declaration: dict, facts: ReelFacts,
                 index: int) -> dict:
    """One run with its words settled, or a refusal naming what was empty."""
    if run["bind"]:
        if run["bind"] == "speakers":
            text = declaration["separator"].join(
                s for s in facts.speakers if s)
        else:
            text = facts.binding(run["bind"],
                                 declaration.get("opening_seconds"))
        if not text:
            raise FullFrameDeclarationError(
                f"full_frame_elements[{index}] binds a run to "
                f"{run['bind']!r} and reel {facts.reel_number} has nothing "
                f"there: {COPY_BINDINGS[run['bind']]} An empty run is not "
                f"drawn and not substituted - the card refuses instead.")
    else:
        text = run["text"]
    out = {"text": text.upper() if run["uppercase"] else text,
           "type_role": run["type_role"],
           "colour": run["colour"]}
    if run["font_size"] is not None:
        out["font_size"] = run["font_size"]
    return out


# ── Rendering ────────────────────────────────────────────────────────


def render_reel_cards(cards: Sequence[PlannedCard],
                      remotion_dir: str,
                      output_dir: str,
                      stream=sys.stderr) -> list[PlannedCard]:
    """Render every planned card to an OPAQUE file, and say where each is.

    Returns the SAME cards with :attr:`PlannedCard.rendered_path` filled
    in, so what is placed on the timeline is the object that was planned
    rather than a second record of it that could disagree.

    Opaque, and this is the one place the difference between this layer
    and the overlay layer is visible in a command line: ``4.05`` and
    ``timed_text_render`` both pass ``--transparent`` because they are
    composited over picture.  A full-frame element IS the picture, so it
    renders on its declared ground.  ProRes 4444 for the same reason
    every other render here uses it - it is what the reels media pool
    already carries.

    A failed render RAISES.  Nothing downstream would notice a missing
    card: the reel would simply start on speech, which is what every reel
    did before this existed.
    """
    if not cards:
        return []
    os.makedirs(output_dir, exist_ok=True)
    rendered: list[PlannedCard] = []
    for card in cards:
        out_path = os.path.join(output_dir, f"{card.render_name}.mov")
        props_path = os.path.join(output_dir, f"{card.render_name}_props.json")
        with open(props_path, "w", encoding="utf-8") as handle:
            json.dump(card.props, handle, indent=2, sort_keys=True)
        fps = float(card.props.get("fps") or 1.0)
        print(f"    [{card.index}] {card.render_name}: {card.placement}, "
              f"{card.duration_frames}f, reel:{card.reel_start(fps):.2f}-"
              f"{card.reel_end(fps):.2f}s "
              f"(frames {card.reel_start_frame}..{card.reel_end_frame})",
              file=stream)
        _render_one(out_path, props_path, remotion_dir)
        rendered.append(replace(card, rendered_path=out_path))
        print(f"      OK: {out_path} ({os.path.getsize(out_path)} bytes)",
              file=stream)
    return rendered


def _render_one(out_path: str, props_path: str, remotion_dir: str) -> None:
    """One ``npx remotion render`` of FullFrameCard, judged by its result."""
    command = [
        "npx", "remotion", "render",
        FULL_FRAME_COMPOSITION, out_path,
        "--props", props_path,
        "--codec", "prores",
        "--prores-profile", "4444",
        "--image-format", "png",
        # Deliberately NOT --transparent. See render_reel_cards.
    ]
    try:
        result = subprocess.run(
            command, cwd=remotion_dir, capture_output=True, text=True,
            # `encoding` explicitly: `text=True` alone decodes with the
            # locale codec, and this repository writes UTF-8 status
            # glyphs (AGENTS.md 9).
            encoding="utf-8", timeout=RENDER_TIMEOUT_SECONDS, check=False)
    except FileNotFoundError as exc:
        raise FullFrameRenderError(
            f"cannot run `npx` in {remotion_dir}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FullFrameRenderError(
            f"the render of {out_path} did not finish in "
            f"{RENDER_TIMEOUT_SECONDS}s") from exc
    if result.returncode != 0:
        raise FullFrameRenderError(
            f"remotion render of {FULL_FRAME_COMPOSITION} failed "
            f"({result.returncode}):\n{result.stderr[-2000:]}")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise FullFrameRenderError(
            f"remotion reported success but {out_path} is missing or empty")


# ── Reading the roster ───────────────────────────────────────────────

ROSTER_LEGEND = (
    "element: the roster key a declaration names. "
    "copy: whether the element needs a text payload. "
    "reachable: whether the renderer can put it on a frame today. "
    "never: what the element is not for."
)


def roster_rows() -> list[dict]:
    """The roster as rows, the prompt-side route.

    The same shape ``motion_graphics_vocabulary.roster_rows`` and
    ``music_measurement.MEASUREMENT_LEGEND`` take, so a planning step's
    bridge can put the whole roster in front of a model as a table without
    this module knowing anything about prompts.
    """
    return [{
        "element": e.key,
        "what": e.what,
        "copy": e.copy,
        "reachable": e.reachable,
        "axes": ", ".join(e.axes),
        "never": " ".join(e.never),
    } for e in ROSTER]


def main(argv=None) -> int:
    """``python3 -m library.tools.full_frame_element [--check]``."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        assert_roster_is_well_formed()
        print("full-frame roster: well formed "
              f"({len(ROSTER)} element(s), {len(COPY_BINDINGS)} binding(s))")
        return 0
    print("FULL-FRAME ROSTER\n")
    for element in ROSTER:
        print(f"  {element.key}  [{element.reachable}] copy={element.copy}")
        print(f"      {element.what}")
        print(f"      axes: {', '.join(element.axes)}")
        for refusal in element.never:
            print(f"      never: {refusal}")
        print()
    print("COPY BINDINGS (what a run may quote instead of stating)")
    for name, what in sorted(COPY_BINDINGS.items()):
        print(f"  {name}: {what}")
    print("\nOUT OF VOCABULARY (owned by another enumeration)")
    for name in sorted(OUT_OF_VOCABULARY):
        print(f"  {name}: {OUT_OF_VOCABULARY[name]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
