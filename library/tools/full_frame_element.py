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
default here, for the reason ``series_look.py`` was emptied (AGENTS.md
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
2. :func:`plan_reel_cards` turns each declaration into planned picture -
   a CARD per declaration, or one SEGMENT per declared window inside a
   keep range for a ``full_frame_span`` - the props, the reel seconds
   it occupies, and the file it will be rendered to - using facts
   measured off the reel itself (:class:`ReelFacts`).
3. :func:`render_reel_cards` renders them all in ONE batch through the
   shared Remotion renderer (``library/tools/remotion_batch.py``), OPAQUE:
   a full-frame element is the picture, so it carries its own ground
   rather than relying on black showing through an alpha channel that
   nothing is beneath.
4. ``reel_build.build_reel_timeline`` places each card on the declared
   card row (`effect.card_row_role`, resolved to a track-plan role row
   - never the first a-roll row by position), and
   ``reel_build.lead_seconds`` shifts every other placement and every
   caption by the head cards' total, so one clock moves together.
5. ``reel_conformance_verifier`` grades the card as the picture item it
   is: inside F1, counted by F4, and exempt from F12 for a stated reason
   rather than by omission.

``tests/test_full_frame_element.py``.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

from library.tools import brand_library as _brandlib
from library.tools import motion_graphics_vocabulary as _mg
from library.tools.remotion_batch import (
    RemotionBatchError,
    RenderJob,
    render_batch,
)
from library.tools.render_fonts import font_is_deliverable, static_font_path
from library.tools.safe_area import resolve_safe_area
from library.tools.timeline_layout import CARD_ROW_ROLES

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
              "type_role", "colour_role", "image"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Drawn by the FullFrameCard Remotion composition and placed on "
            "the declared card row by reel_build.build_reel_timeline. "
            "Rendered and read back "
            "off a built timeline before this flag was set. The `image` "
            "axis draws a project-supplied still (a wordmark) above the "
            "runs, resolved through the channel_bug shape - a named file "
            "out of the project's own brand_assets/, staged lazily into "
            "Remotion's public/brand/ - and proved by a still that fails "
            "without the drawing node (tests/test_fullframe_card_image.py)."
        ),
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
    FullFrameElementKind(
        key="full_frame_span",
        what=("The reel's whole body as one animated element: a segment "
              "per declared window inside a keep range, each a full frame "
              "of type on its own ground, tiling the body's own seconds "
              "exactly, in place of picture rather than over it. "
              "The word the seven-back-to-back-cards build of "
              "docs/ANIMATED_REEL_CEILING.md was missing."),
        axes=("placement", "segments", "entrance", "exit", "copy",
              "type_role", "colour_role", "image", "word_sync",
              "emphasis_colour"),
        copy="required",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Each segment is drawn by the FullFrameCard Remotion "
            "composition - the same component, the same opaque ProRes "
            "4444 render, one file per segment - and "
            "tests/test_full_frame_span.py renders a two-segment span "
            "and reads per-segment ink back off the frames before this "
            "flag was set. A segment may name a project image through "
            "the same slot a card's mark takes, drawn by the same "
            "composition node tests/test_fullframe_card_image.py proved. "
            "A span declaring word_sync paces the typewriter/mask/draw "
            "entrance off the transcript's own word timings, measured "
            "per segment at plan time and proved by a render that fails "
            "without the cue-driven node "
            "(tests/test_fullframe_word_cues.py). A segment may name "
            "its own entrance - which character that keep range dances "
            "to is declared per segment, never derived - and a span may "
            "declare emphasis_colour, the colour the current word draws "
            "in while it owns the clock (tests/"
            "test_fullframe_cued_draw_emphasis.py). A span may subdivide "
            "a keep range into windowed segments paced to speech beats "
            "inside continuous speech, and a segment may declare its own "
            "background over the span's, so a mid-piece value inversion "
            "is a declaration rather than decoration "
            "(tests/test_full_frame_span.py)."
        ),
        never=(
            "Never a card beside it: the span already covers the reel's "
            "whole body, so a head or tail card on the same reel is two "
            "pictures on the same seconds. `plan_reel_cards` refuses the "
            "mix by name.",
            "Never over footage: it replaces picture for the whole body, "
            "so footage video is not placed where a span plays "
            "(reel_build.build_reel_timeline suppresses it) - two "
            "pictures on V1 is an overlap, not a composite.",
            "Never a declared duration: a span lasts as long as the "
            "body it covers, measured off the keep ranges at plan time. "
            "A second number is a second source of truth, so "
            "`duration_seconds` on a span is refused, not read.",
            "Never a silent desynchronisation: every segment's window "
            "must fall inside exactly one keep range, windows must not "
            "overlap, and they must tile every range - an overlapping "
            "pair, a window outside every range, or a beat no segment "
            "covers refuses by name (which is what stops invented "
            "round-number ranges reaching the timeline). A declared "
            "`allow_gaps` leaves beats uncovered on purpose, and the "
            "hole stays a hole in reel positions for the placer and the "
            "verifier to grade.",
            "Never artwork the engine supplies: the ground, the typeface "
            "and every word are the declaration's (AGENTS.md 14), the "
            "same as a card's.",
        ),
    ),
    FullFrameElementKind(
        key="full_frame_clip",
        what=("A finished animation the PROJECT supplies, played at the "
              "delivery frame for its own measured length, in place of "
              "picture rather than over it. The engine places the "
              "client's file verbatim and draws nothing into it."),
        axes=("placement", "asset"),
        copy="none",
        reachable=REACHABLE_NOW,
        reachability_note=(
            "Measured with ffprobe at plan time and placed on the "
            "declared card row by reel_build.build_reel_timeline, on "
            "the same path a rendered card takes - so the coverage "
            "assertion, the item count and the framing verdict all see "
            "it. Proved on an exported still off a built reel timeline "
            "(tests/test_full_frame_clip.py covers the plan and the "
            "refusals)."
        ),
        never=(
            "Never re-authored: the engine stages a client's asset "
            "verbatim and never edits one (AGENTS.md 13). A file that is "
            "not already the delivery frame is REFUSED rather than "
            "letterboxed or cropped - which of those to do is taste, and "
            "it is the project's to declare by re-authoring the file.",
            "Never a declared duration: the animation lasts as long as "
            "the file does, measured at plan time. A second number is a "
            "second source of truth, and one that disagrees either "
            "truncates the animation or freezes on its last frame - so "
            "`duration_seconds` on a clip is refused, not read.",
            "Never copy: a clip carries no runs, no ground and no "
            "typeface, because every pixel of it was drawn before the "
            "engine saw it. A card the engine types is `full_frame_card`.",
            "Never mid-reel: the same refusal a card gets, for the same "
            "reason - `placement` is head or tail.",
            "Never sound: it is placed as picture (`mediaType: 1`), so an "
            "audio stream riding along does not reach the timeline. A "
            "closing sting is a sound decision and 4.04 owns those.",
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

SPAN_PLACEMENT = "span"
"""Where a span sits: over the reel's whole body, and nowhere else.

The ONLY placement a ``full_frame_span`` declaration may carry, and one
no ``full_frame_card`` may carry.  A span is not a card with a long
duration - it is a different declaration, so the mid-reel refusal a card
carries is untouched by it: ``_normalise`` refuses ``span`` on a card
and anything but ``span`` on a span, each by name.
"""

WINDOW_EPS = 1e-9
"""Float dust for window checks, in seconds - never editorial time.

A planner shares the exact boundary float between neighbours, so
abutment is exact; this only absorbs arithmetic dust. The same
magnitude ``range_line`` and ``_word_cues`` already use for the same
reason."""

MOTION_CHARACTERS = tuple(_mg.AXES_BY_NAME["entrance"].positions)
"""How a card arrives and leaves.

The OVERLAY roster's own axis, imported rather than respelled: there is
one vocabulary of motion character in this engine, and the composition
that draws these cards reuses the overlay composition's own
``entranceTransform`` for the same reason.
"""

#: The entrances a word clock can pace.  ``typewriter`` spells the words
#: out, ``mask`` wipes across them and ``draw`` resolves onto them - the
#: three reveals docs/ANIMATED_REEL_CEILING.md names as time-based
#: across a card.  Every other character (a slide, a blur, a glitch) has
#: no word-shaped progress to follow, so ``word_sync`` beside one of
#: them is refused rather than silently running time-based.
WORD_CUED_ENTRANCES = ("typewriter", "mask", "draw")

NO_MOTION = "cut"
"""The motion character that draws no motion, and the ONLY default here.

Legal as a default under AGENTS.md 10.5's own carve-out: a value meaning
"nothing is drawn" is the absence of decoration, not a choice of it - the
same reading ``transition_vocabulary.CUT_TYPES`` and
``series_look.NEUTRAL_CDL`` are given.  Every other axis - colour, ground,
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
    "range_line": (
        "Inside a span segment only: the words spoken in that segment's "
        "own window - its declared `window`, or its whole keep range when "
        "it names none - verbatim, in reel-time order. A quotation of "
        "the reel's own speech, the binding the animated-reel build of "
        "docs/ANIMATED_REEL_CEILING.md laid per card by hand. Refused "
        "outside a span segment, where 'that window' names nothing."
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
        if name == "range_line":
            raise FullFrameDeclarationError(
                "a run binds to 'range_line', which resolves per SEGMENT "
                "against that segment's own keep range - never per reel. "
                "It is resolved where the span is planned "
                "(`_plan_span`), not here.")
        raise FullFrameDeclarationError(
            f"{name!r} is not a copy binding; known: "
            f"{', '.join(sorted(COPY_BINDINGS))}")

    @staticmethod
    def range_line(range_index: int,
                   ranges: Sequence[tuple[float, float]],
                   transcript: dict,
                   window: Optional[Sequence[float]] = None) -> str:
        """The words spoken in one span segment's window, verbatim.

        The quotation a span segment binds when it names ``range_line``:
        what the reel itself plays during that segment's own seconds.
        Read off the PLAYED ranges through ``reel_build.reel_time`` - the
        same arithmetic ``reel_opening.opening_words`` goes through, so
        the segment quotes the reel that exists rather than the span the
        plan asked for.

        ``window`` is the segment's own ``[start, end]`` in master
        seconds; omitted means the whole of keep range ``range_index``,
        which is what a segment with no declared window covers. Either
        way the membership test is in master seconds and the ordering in
        reel time, so the line reads in play order.

        Untimed words are skipped rather than guessed at, for the reason
        ``opening_words`` states. Unlike that function there is no
        first-speaker filter: a window is a kept beat, not an opening
        line, and dropping a speaker's words from inside it would be an
        edit.  ``""`` when the window holds no timed words - the caller
        refuses the segment by name rather than drawing it empty.
        """
        from library.tools.reel_build import reel_time

        if (not isinstance(range_index, int) or isinstance(range_index, bool)
                or not 0 <= range_index < len(ranges)):
            raise FullFrameDeclarationError(
                f"range_line asks for keep range {range_index!r} and the "
                f"reel plays {len(ranges)}; a segment quotes its own range "
                f"by index, never another one.")
        ws, we = (tuple(window) if window is not None
                  else ranges[range_index])
        found: list[tuple[float, str]] = []
        for segment in ((transcript or {}).get("segments") or []):
            for word in (segment.get("words") or []):
                if not word.get("timed"):
                    continue
                master = float(word["start"])
                if not (ws - WINDOW_EPS <= master < we - WINDOW_EPS):
                    continue
                at = reel_time(master, ranges)
                if at is None:
                    continue
                found.append((at, str(word.get("word", ""))))
        return " ".join(
            text for at, text in sorted(found) if text).strip()


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
    source_frames: int = 0
    """How many frames of the SOURCE file the placement plays.

    A rendered card is drawn at the reel's own fps, so this equals
    `duration_frames` and the distinction never surfaces.  A
    `full_frame_clip` is a file the project authored at whatever rate it
    chose - the logo animation this was written for is 30fps on a
    24000/1001 reel - and `AppendToTimeline`'s `endFrame` counts SOURCE
    frames while `recordFrame` counts timeline ones.  Placing the
    timeline count would play 4% of the animation short, which is a
    truncation nothing downstream could see."""

    rendered_path: str = ""
    """The file this card was rendered to.  Empty until it has been.

    A card with no file has not been drawn, and `reel_build` REFUSES to
    place one rather than leaving a reel that starts on speech - which is
    indistinguishable from a project that declared no card at all."""

    conform_note: str = ""
    """What the frame-rate conform cost, when the clip pays one.

    Empty for a clip at the reel's own rate, which plays 1:1.  A clip
    authored at another rate (the logo animation shipped at 30fps on a
    24000/1001 reel) decimates - 90 source frames over 71 timeline
    frames - and that arithmetic is SAID here and on stderr at plan
    time, because a glow ramp with every 5th frame dropped reads as
    judder nobody planned.  REPORTED, never a gate: the engine places
    what the project declared, and conforming the asset file is the
    project's act, not a refusal the plan may take on its behalf."""

    library_note: str = ""
    """What the series-shared store says about this file, when it says
    anything.

    Empty for a clip no manifest covers - most clips - and for a
    covered file whose bytes match.  A covered file that is missing,
    changed, or never recorded gets one SAYABLE line here and on
    stderr at plan time, because a reel built on a moved master
    bakes the move into Resolve.  REPORTED, never a gate, for the
    same reason as ``conform_note``: the reader is
    ``brand_library.library_note_for_asset``, which itself never
    raises."""

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
    if key == "full_frame_span":
        if placement != SPAN_PLACEMENT:
            raise FullFrameDeclarationError(
                f"{label} is a span and has placement={raw.get('placement')!r}; "
                f"a span covers the reel's whole body, so its placement is "
                f"{SPAN_PLACEMENT!r} and nothing else - head or tail on a "
                f"span would be a card beside the picture it already is.")
        return _normalise_span(raw, index, placement)
    if key == "full_frame_clip":
        return _normalise_clip(raw, index, label, placement)
    if placement == SPAN_PLACEMENT:
        raise FullFrameDeclarationError(
            f"{label} is a card and has placement='span'; a span is the "
            f"whole-span element full_frame_span, not a long card. A card "
            f"sits at the {' or the '.join(PLACEMENTS)} of the reel - "
            f"there is no default, because where a card sits is an "
            f"editorial decision, and no mid-reel position exists because "
            f"a card between two keep ranges lands inside a sentence.")
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

    shared = _shared_fields(raw, index, label)
    if shared["word_sync"]:
        raise FullFrameDeclarationError(
            f"{label} sets word_sync, and a card covers its own seconds, "
            f"not speech seconds: the words it quotes are spoken before "
            f"or after it plays, so no word clock runs while it is on "
            f"screen. A word-paced reveal lives on a full_frame_span, "
            f"whose segments play over their own speech - the same "
            f"reading that refuses `range_line` outside a span segment.")
    if shared["emphasis_colour"] is not None:
        raise FullFrameDeclarationError(
            f"{label} sets emphasis_colour, and a card covers its own "
            f"seconds, not speech seconds: with no word clock no word is "
            f"ever current, so the field would have no reader. A "
            f"current-word emphasis lives on a full_frame_span beside "
            f"word_sync.")
    runs = raw.get("runs")
    if not isinstance(runs, (list, tuple)) or not runs:
        raise FullFrameDeclarationError(
            f"{label} declares no `runs`; a card with no copy on it draws "
            f"a coloured rectangle, and an element that draws nothing is "
            f"not rendered (AGENTS.md 10.2)")

    return {
        "element": key,
        "placement": placement,
        "duration_seconds": float(duration),
        **shared,
        "runs": [_normalise_run(run, index, position)
                 for position, run in enumerate(runs, start=1)],
    }


#: A clip's own picture is the whole declaration, so everything a card
#: declares about how a frame is DRAWN has no reader on one. Each is
#: refused by name rather than ignored: a project that wrote one
#: believes it reached the picture.
CLIP_REFUSED_FIELDS: dict[str, str] = {
    "runs": ("a clip carries no copy - every pixel of it was drawn "
             "before the engine saw it. A card the engine types is "
             "`full_frame_card`."),
    "duration_seconds": ("a clip lasts as long as its file does, "
                         "measured at plan time. A declared length is a "
                         "second source of truth that either truncates "
                         "the animation or freezes on its last frame."),
    "background": "a clip draws its own ground.",
    "entrance": "a clip animates itself; its entrance is in the file.",
    "exit": "a clip animates itself; its exit is in the file.",
    "font_family": "a clip carries no type.",
    "font_file": "a clip carries no type.",
    "y": "a clip is the whole frame; there is nothing to position.",
    "image": "a clip IS the image.",
    "word_sync": ("a clip covers its own seconds, not speech seconds, "
                  "so no word clock runs while it plays."),
    "emphasis_colour": ("a clip covers its own seconds, so no word is "
                        "ever current while it plays."),
    "segments": "segments belong to a full_frame_span.",
}


@dataclass(frozen=True)
class ClipMeasurement:
    """A project clip, measured rather than assumed."""

    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float


def measure_clip(path: str, timeout: int = 30) -> ClipMeasurement:
    """Measure a project-supplied full-frame clip, or refuse it by name.

    A file is admitted on a MEASUREMENT, never on its extension - the
    same bar `brand_motion.measure_source` holds its sources to. What is
    read here is only what the placement needs: the frame the file
    delivers, and how long it runs in its own frames and in seconds.
    """
    import json as _json
    import subprocess as _subprocess

    if not os.path.isfile(path):
        raise FullFrameDeclarationError(
            f"full-frame clip {path!r} is not a file. A declared closing "
            f"element that is not on disk leaves the reel ending on the "
            f"switch-off, which is indistinguishable from a project that "
            f"declared no clip at all.")
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries",
           "stream=width,height,r_frame_rate,nb_frames,duration",
           "-show_entries", "format=duration", "-of", "json", path]
    try:
        res = _subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise FullFrameDeclarationError(
            f"ffprobe is not on PATH, so {path!r} cannot be measured, and "
            f"a clip is admitted on a measurement rather than on its "
            f"extension.") from exc
    except _subprocess.TimeoutExpired as exc:
        raise FullFrameDeclarationError(
            f"ffprobe timed out after {timeout}s on {path!r}") from exc
    if res.returncode != 0:
        raise FullFrameDeclarationError(
            f"ffprobe could not read {path!r}: "
            f"{res.stderr.strip() or 'no stderr'}")
    try:
        data = _json.loads(res.stdout or "{}")
    except ValueError as exc:
        raise FullFrameDeclarationError(
            f"ffprobe returned unparseable output for {path!r}") from exc
    streams = data.get("streams") or []
    if not streams:
        raise FullFrameDeclarationError(
            f"ffprobe found no video stream in {path!r}: a full-frame "
            f"element has to be a picture.")
    stream = streams[0]
    try:
        num, _, den = str(stream.get("r_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0
    duration = stream.get("duration") or (
        data.get("format") or {}).get("duration")
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        seconds = 0.0
    try:
        frames = int(stream.get("nb_frames"))
    except (TypeError, ValueError):
        frames = int(round(seconds * fps)) if fps > 0 else 0
    if seconds <= 0 or frames <= 0 or fps <= 0:
        raise FullFrameDeclarationError(
            f"{path!r} measures {frames} frame(s) over {seconds}s at "
            f"{fps}fps: a clip that lasts no time appears in no frame.")
    return ClipMeasurement(
        path=path, width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0), fps=fps,
        frame_count=frames, duration_seconds=seconds)


def _normalise_clip(raw: dict, index: int, label: str,
                    placement: str) -> dict:
    """One `full_frame_clip` declaration, checked field by field.

    The whole declaration is WHERE it sits, WHICH file it is, and WHY -
    the captain's own words, which is what any listing reads back. The
    shape `placed_assets.json` already uses for the master path, because
    this is the reels half of the same decision.
    """
    if placement not in PLACEMENTS:
        raise FullFrameDeclarationError(
            f"{label} has placement={raw.get('placement')!r}; a clip sits "
            f"at the {' or the '.join(PLACEMENTS)} of the reel. There is "
            f"no default, because where it sits is an editorial "
            f"decision, and no mid-reel position exists because a full "
            f"frame between two keep ranges lands inside a sentence.")

    asset = raw.get("asset")
    if not isinstance(asset, str) or not asset.strip():
        raise FullFrameDeclarationError(
            f"{label} names no `asset`. A clip IS a file the project "
            f"supplies; there is nothing for the engine to draw instead.")

    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise FullFrameDeclarationError(
            f"{label} carries no `reason` - the captain's own words, "
            f"which is what any listing reads back.")

    for field_name, why in CLIP_REFUSED_FIELDS.items():
        if raw.get(field_name) is not None:
            raise FullFrameDeclarationError(
                f"{label} is a full_frame_clip and sets "
                f"{field_name}={raw.get(field_name)!r}: {why}")

    return {
        "element": "full_frame_clip",
        "placement": placement,
        "asset": asset.strip(),
        "reason": reason.strip(),
    }


def resolve_clip_asset(named: str, project_folder: Optional[str]) -> str:
    """A declared clip path as an absolute file, or a refusal by name.

    An ABSOLUTE path is taken as written - a reference is an absolute
    path plus a map and this declaration is the map (AGENTS.md 10.1),
    which is what lets a series keep one brand animation beside the
    projects that share it rather than a copy inside each. A RELATIVE
    one resolves against the project folder, so `brand_assets/x.mov`
    means what it reads as.
    """
    path = os.path.expanduser(str(named or "").strip())
    if not os.path.isabs(path):
        if not project_folder:
            raise FullFrameDeclarationError(
                f"full-frame clip {named!r} is relative and no project "
                f"folder was given to resolve it against.")
        path = os.path.normpath(os.path.join(str(project_folder), path))
    if not os.path.isfile(path):
        raise FullFrameDeclarationError(
            f"full-frame clip {named!r} resolves to {path!r}, which is "
            f"not a file.")
    return path


def _shared_fields(raw: Any, index: int, label: str) -> dict:
    """The declaration fields a card and a span fill identically.

    Ground, motion characters, typeface, position, separator and the
    opening window: the engine states none of them, so both shapes refuse
    them absent in the same words.  What differs - a card's declared
    duration and runs, a span's measured body and segments - stays in
    `_normalise` and `_normalise_span`.
    """
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

    # An image slot, for a project-supplied still such as a wordmark.
    # The channel_bug shape, not a second mechanism: the declaration
    # names a file out of the project's own brand_assets/ and the plan
    # resolves it to a staged public path. The engine ships no artwork
    # (AGENTS.md 14), so a name that resolves to nothing REFUSES at
    # plan time rather than drawing a card with a hole in it.
    image_fields = _normalise_image_fields(raw, label)

    # A word clock for the reveal, off by default.  When true the plan
    # measures each span segment's words out of the transcript - the same
    # timings the caption path already reads - and the FullFrameCard
    # composition paces its typewriter/mask/draw entrance off them, so
    # the animation lands ON words instead of across the card's own
    # seconds (docs/ANIMATED_REEL_CEILING.md).  A card never takes this:
    # it covers its own seconds, not speech seconds, so `_normalise`
    # refuses it there the way `range_line` is refused outside a span.
    word_sync = raw.get("word_sync", False)
    if not isinstance(word_sync, bool):
        raise FullFrameDeclarationError(
            f"{label} has word_sync={raw.get('word_sync')!r}; pacing the "
            f"reveal off the spoken words is either on or off - omit it "
            f"and the reveal runs time-based, as before.")

    # The colour the CURRENT word draws in while it owns the clock.
    # Declared or absent, never defaulted: the engine ships no palette
    # (AGENTS.md 10.5), so an undeclared emphasis simply does not
    # emphasise. Read only beside a word clock - `_normalise` refuses it
    # on a card and `_normalise_span` refuses it without `word_sync`,
    # because a field no drawing reads is a declaration nothing owns.
    emphasis_colour = raw.get("emphasis_colour")
    if emphasis_colour is not None:
        if (not isinstance(emphasis_colour, str)
                or not emphasis_colour.strip()):
            raise FullFrameDeclarationError(
                f"{label} has emphasis_colour={raw.get('emphasis_colour')!r}; "
                f"the current word's colour is a declared colour or it is "
                f"absent - omit it and words keep their run's colour.")

    return {
        "background": background,
        "entrance": entrance,
        "exit": exit_char,
        "font_family": font_family,
        "font_file": str(font_file) if font_file else None,
        "y": float(y) if y is not None else None,
        **image_fields,
        "word_sync": word_sync,
        "emphasis_colour": (emphasis_colour.strip()
                            if emphasis_colour is not None else None),
        "separator": str(raw.get("separator") or ", "),
        "opening_seconds": (float(opening_seconds)
                            if opening_seconds is not None else None),
    }


def _normalise_image_fields(raw: Any, label: str) -> dict:
    """A project-supplied still, named but not yet resolved.

    One shape for a card's mark and a span segment's: the declaration
    names a file out of the project's own brand_assets/ (plus an
    optional width), and the plan resolves it through the channel_bug
    shape - staged lazily, refused by name when it resolves to nothing.
    """
    image = raw.get("image")
    if image is not None:
        if not isinstance(image, str) or not image.strip():
            raise FullFrameDeclarationError(
                f"{label} has image={image!r}; the image is a file in the "
                f"project's own brand_assets/, named so the plan can "
                f"stage it. Omit it and the card draws its runs alone.")

    image_width = raw.get("image_width")
    if image_width is not None:
        if (not isinstance(image_width, (int, float))
                or isinstance(image_width, bool) or image_width <= 0):
            raise FullFrameDeclarationError(
                f"{label} has image_width={image_width!r}; a width is a "
                f"positive number of pixels of the delivery frame")
        if image is None:
            raise FullFrameDeclarationError(
                f"{label} states an image_width for an image it does not "
                f"name; a magnitude with nothing to size is a declaration "
                f"nobody can read back.")
    return {
        "image": image.strip() if image is not None else None,
        "image_width": (float(image_width)
                        if image_width is not None else None),
    }


def _normalise_span(raw: dict, index: int, placement: str) -> dict:
    """One whole-span declaration, checked field by field.  Refuses by name.

    A span is what the seven-back-to-back-cards build was reaching for:
    one declaration whose segments cover the reel's whole body.  Its
    duration is MEASURED off the keep ranges at plan time, never stated -
    ``duration_seconds`` here is refused rather than read - and each
    segment paces its picture to a ``window`` of master seconds inside
    one keep range, so the boundaries sit on the reel's own speech beats
    the way that build's sat on its edit points.

    ``allow_gaps`` is the one explicit way to leave beats uncovered: a
    window the plan lays as a hole in reel positions, for the placer and
    the verifier to grade rather than a stretch the engine invents.
    """
    label = f"full_frame_elements[{index}]"
    if raw.get("duration_seconds") is not None:
        raise FullFrameDeclarationError(
            f"{label} is a span and states duration_seconds="
            f"{raw.get('duration_seconds')!r}; a span lasts as long as the "
            f"body it covers, measured off the keep ranges at plan time. "
            f"A second number is a second source of truth.")
    if raw.get("runs") is not None:
        raise FullFrameDeclarationError(
            f"{label} is a span and carries `runs`; one copy block cannot "
            f"say what each segment of the body shows. A span carries "
            f"`segments`, one per keep range, each with its own `runs`.")
    if raw.get("image") is not None or raw.get("image_width") is not None:
        raise FullFrameDeclarationError(
            f"{label} is a span and names an `image`; a span is the "
            f"container, and the unit that renders is the segment - each "
            f"segment names its own image, so one mark for the whole span "
            f"would be a second source for what the segments already say.")
    segments = raw.get("segments")
    if not isinstance(segments, (list, tuple)) or not segments:
        raise FullFrameDeclarationError(
            f"{label} declares no `segments`; a span with no segments "
            f"covers the body with nothing, and an element that draws "
            f"nothing is not rendered (AGENTS.md 10.2)")
    shared = _shared_fields(raw, index, label)
    if shared["word_sync"] and shared["entrance"] not in WORD_CUED_ENTRANCES:
        raise FullFrameDeclarationError(
            f"{label} sets word_sync with entrance={shared['entrance']!r}; "
            f"a word clock can pace {', '.join(WORD_CUED_ENTRANCES)} and "
            f"nothing else. Omit word_sync and the entrance runs "
            f"time-based, as before.")
    if shared["emphasis_colour"] is not None and not shared["word_sync"]:
        raise FullFrameDeclarationError(
            f"{label} sets emphasis_colour without word_sync; with no "
            f"word clock no word is ever current, so the field would "
            f"have no reader. Omit it and words keep their run's "
            f"colour.")
    segments = [_normalise_segment(segment, index, position)
                for position, segment in enumerate(segments, start=1)]
    # Each segment's EFFECTIVE character - its own, or the span's - is
    # what the clock must be able to pace and the emphasis must be able
    # to land on. Checked here, where both halves are known, rather than
    # in `_normalise_segment`, which sees neither the span's entrance
    # nor its emphasis.
    for position, segment in enumerate(segments, start=1):
        effective = segment["entrance"] or shared["entrance"]
        seg_label = f"{label}.segments[{position}]"
        if shared["word_sync"] and effective not in WORD_CUED_ENTRANCES:
            raise FullFrameDeclarationError(
                f"{seg_label} reads entrance={effective!r} beside "
                f"word_sync; a word clock can pace "
                f"{', '.join(WORD_CUED_ENTRANCES)} and nothing else.")
        if (shared["emphasis_colour"] is not None
                and effective == "typewriter"):
            raise FullFrameDeclarationError(
                f"{seg_label} reads entrance='typewriter' beside "
                f"emphasis_colour; a typewriter is a reveal, not a "
                f"per-word drawing, so there is no current-word glyph "
                f"to restyle - the field would have no reader on this "
                f"segment.")
    # Beats may go uncovered only when the span says so. Off by default,
    # and either on or off - the same shape `word_sync` takes - because
    # a half-covered range with no such declaration is the silent
    # desynchronisation the window check exists to refuse.
    allow_gaps = raw.get("allow_gaps", False)
    if not isinstance(allow_gaps, bool):
        raise FullFrameDeclarationError(
            f"{label} has allow_gaps={raw.get('allow_gaps')!r}; leaving "
            f"beats of a keep range uncovered is either on or off - omit "
            f"it and every keep range is tiled edge to edge.")
    return {
        "element": "full_frame_span",
        "placement": placement,
        "duration_seconds": None,
        **shared,
        "allow_gaps": allow_gaps,
        "segments": segments,
    }


def _normalise_window(raw: Any, label: str) -> Optional[tuple[float, float]]:
    """A segment's speech beat in master seconds, or None (positional).

    Shape-checked here; containment in a keep range is a plan-time check
    in `_span_windows`, where the ranges are known. A window is
    `[start, end]` - one field, so half a window cannot be declared -
    with both ends finite numbers and the start strictly before the end.
    No look, no cadence, no default: the beat is the declaration's.
    """
    import math

    if raw is None:
        return None
    if (not isinstance(raw, (list, tuple)) or len(raw) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in raw)):
        raise FullFrameDeclarationError(
            f"{label} has window={raw!r}; a window is "
            f"[start_seconds, end_seconds] in the reel's own master "
            f"seconds, naming the speech beat this segment paces its "
            f"picture to. Omit it and the segment covers its positional "
            f"keep range, as before.")
    ws, we = float(raw[0]), float(raw[1])
    if not ws < we:
        raise FullFrameDeclarationError(
            f"{label} has window=[{ws!r}, {we!r}]; a window starts "
            f"before it ends - the beat it paces its picture to, in the "
            f"reel's own master seconds.")
    return (ws, we)


def _normalise_segment(raw: Any, span_index: int, position: int) -> dict:
    """One span segment: the copy - and optionally the mark, the ground
    and the beat - one window of a keep range's seconds shows.

    A segment is the unit that renders (one file per segment through
    the FullFrameCard composition), so it names its own `image` through
    the same slot a card's mark takes, resolved the same way at plan
    time.  A segment naming no image carries no `image` key, which is
    what keeps its props rendering byte-identically to a card's.

    A segment may also name its own `entrance`: which word-paced
    character THIS beat dances to. Absent means the span's - the
    choice of character per segment is declared by whoever writes the
    plan, never derived by the engine (no parity, no cadence), because
    any such derivation would be the engine holding taste (AGENTS.md
    10.5). Whether the effective character paces beside `word_sync` is
    checked in `_normalise_span`, where the span's halves are known.

    A segment may name its own `background` the same way: which ground
    THIS beat inverts or holds to. Absent means the span's, which stays
    required and stays the default - a value inversion between beats is
    a declaration, and the engine states no colour of its own
    (AGENTS.md 10.5).

    A segment may name its own `window`: which master seconds THIS
    picture paces itself to. Absent means its positional keep range -
    segment i covers range i, the old reading - so a declaration that
    never subdivides plans exactly as before.
    """
    label = f"full_frame_elements[{span_index}].segments[{position}]"
    if not isinstance(raw, dict):
        raise FullFrameDeclarationError(
            f"{label} must be a mapping, got {type(raw).__name__}")
    runs = raw.get("runs")
    if not isinstance(runs, (list, tuple)) or not runs:
        raise FullFrameDeclarationError(
            f"{label} declares no `runs`; a segment with no copy on it "
            f"draws a coloured rectangle, and an element that draws "
            f"nothing is not rendered (AGENTS.md 10.2)")
    entrance = raw.get("entrance")
    if entrance is not None:
        entrance = str(entrance).strip().lower() if isinstance(
            entrance, str) else entrance
        if entrance not in MOTION_CHARACTERS:
            raise FullFrameDeclarationError(
                f"{label} has entrance={raw.get('entrance')!r}; the motion "
                f"characters are {', '.join(MOTION_CHARACTERS)}")
    background = raw.get("background")
    if background is not None:
        if not isinstance(background, str) or not background.strip():
            raise FullFrameDeclarationError(
                f"{label} has background={raw.get('background')!r}; a "
                f"segment's ground is a declared colour or it is absent "
                f"- omit it and the segment draws on the span's.")
        background = background.strip()
    return {
        "entrance": entrance,
        "window": _normalise_window(raw.get("window"), label),
        "background": background,
        "runs": [_normalise_segment_run(run, span_index, position, number)
                  for number, run in enumerate(runs, start=1)],
        **_normalise_image_fields(raw, label),
    }


def _normalise_segment_run(raw: Any, span_index: int, position: int,
                           run_index: int) -> dict:
    """One run inside one span segment: the card run shape, retargeted."""
    return _normalise_run(
        raw, span_index, run_index,
        label=(f"full_frame_elements[{span_index}].segments[{position}]"
               f".runs[{run_index}]"))


def _normalise_run(raw: Any, card_index: int, run_index: int,
                    label: Optional[str] = None) -> dict:
    """One run of copy: literal text OR a binding, never both, never neither."""
    label = (label if label is not None
             else f"full_frame_elements[{card_index}].runs[{run_index}]")
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


def _project_asset_resolver(project_folder: Optional[str]):
    """A function turning a NAMED project file into a staged public path.

    The same shape
    ``generate_motion_props.project_asset_resolver`` takes, and for the
    same reason: the engine ships no artwork (AGENTS.md 14), so this
    looks a file up and never supplies a substitute.  Returns None when
    there is no project to look in, which :func:`_plan_one` reads as
    "no way to resolve an asset" and refuses on.

    **Staged lazily, on the first asset actually asked for.**  Copying
    a project's brand files into the repository's ``public/brand/`` is
    a real side effect, and a run whose cards name no image should not
    have one - which is also what leaves the render working with no
    separate prep call: by the time ``render_reel_cards`` runs, the
    plan has already staged what the card draws.
    """
    if not project_folder:
        return None

    from library.tools.render_fonts import PROJECT_FONT_DIR

    staged: dict = {}

    def resolve(name: str) -> str:
        if not staged:
            from library.tools.remotion_brand_linker import link_brand_assets
            result = link_brand_assets(project_folder)
            staged["files"] = set(result.get("files") or [])
        base = os.path.basename(str(name).strip())
        # Basename only: a declaration is a file in the project's own
        # brand_assets/, so a path that climbs out of it resolves to
        # nothing rather than reaching whatever it points at.
        return (f"{PROJECT_FONT_DIR}/{base}"
                if base and base in staged["files"] else "")

    return resolve


def plan_reel_cards(declarations: Sequence[dict],
                    facts: ReelFacts,
                    body_frames: int,
                    fps: float,
                    *,
                    width: int,
                    height: int,
                    project_folder: Optional[str] = None,
                    ranges: Optional[Sequence[tuple[float, float]]] = None,
                    transcript: Optional[dict] = None,
                    resolve_asset=None,
                    ) -> list[PlannedCard]:
    """Resolve every declaration against ONE reel, in the order it plays.

    ``body_frames`` is how long the reel's SPEECH runs, in the same
    integer frames ``reel_build.placements`` lays it down in - which is
    what a tail card is placed after.  Head cards are laid out from reel
    frame zero in declaration order; the total of their durations is what
    ``reel_build.lead_frames`` shifts everything else by.

    ``ranges`` and ``transcript`` are what a SPAN is planned from: its
    segments bind to windows inside the reel's own keep ranges, so the
    boundaries sit on the reel's own speech beats.  Cards never read them -
    a card's duration is declared, not measured - so callers planning
    cards alone pass neither.

    **In FRAMES throughout.**  A card's position is arithmetic on counts
    the plan has already rounded, so the card and the clip beside it abut
    exactly.  Seconds enter only when something asks for them.

    A binding that resolves to nothing raises here, at plan time, rather
    than rendering a card with a hole in it.  A named ``image`` is
    resolved the same way - through ``resolve_asset``, or the project's
    own brand_assets/ when none is injected - and a name that resolves
    to nothing raises too, for the same reason.
    """
    if not declarations:
        return []

    spans = [d for d in declarations
             if d.get("element") == "full_frame_span"]
    # An injected resolver wins (tests); otherwise the project's own
    # brand_assets/ is the lookup, built lazily so a run whose elements
    # name no image stages nothing. Built before the span dispatch, so
    # a span segment names its image through the same slot a card's
    # mark takes rather than a second mechanism.
    resolver = (resolve_asset if resolve_asset is not None
                else _project_asset_resolver(project_folder))
    if spans:
        if len(spans) > 1 or len(spans) != len(declarations):
            raise FullFrameDeclarationError(
                f"a full_frame_span already covers the reel's whole body; "
                f"{len(declarations)} declaration(s) on one reel, "
                f"{len(spans)} of them span(s). A card beside a span is "
                f"two pictures on the same seconds, and two spans are two "
                f"covers for one body - declare one span alone.")
        return _plan_span(spans[0], 1, facts, ranges, transcript, fps,
                          width, height, project_folder, resolver)

    safe_area = resolve_safe_area(project_folder, width=width, height=height)
    heads = [d for d in declarations if d["placement"] == "head"]
    tails = [d for d in declarations if d["placement"] == "tail"]

    def _plan(declaration, index, cursor):
        if declaration["element"] == "full_frame_clip":
            return _plan_clip(declaration, index, cursor, fps,
                              width, height, project_folder)
        return _plan_one(declaration, index, cursor, facts, fps,
                         width, height, safe_area, resolver)

    planned: list[PlannedCard] = []
    cursor = 0
    index = 0
    for declaration in heads:
        index += 1
        card = _plan(declaration, index, cursor)
        planned.append(card)
        cursor += card.duration_frames

    cursor = sum(c.duration_frames for c in planned) + int(body_frames)
    for declaration in tails:
        index += 1
        card = _plan(declaration, index, cursor)
        planned.append(card)
        cursor += card.duration_frames
    return planned


def _plan_clip(declaration: dict, index: int, reel_start_frame: int,
               fps: float, width: int, height: int,
               project_folder: Optional[str]) -> PlannedCard:
    """One project-supplied clip, measured and ready to place.

    Nothing is rendered: `rendered_path` is the project's own file, so
    the clip reaches the timeline verbatim (AGENTS.md 13 - the engine
    renders a client's asset, it never edits one).

    The file must already BE the delivery frame.  Fitting one that is
    not changes how loud the client's mark reads, and which way to fit
    it is taste the engine may not take (the same reading
    `brand_motion.GeometryMismatch` records), so a mismatch refuses and
    names both sizes.
    """
    label = f"full_frame_elements[{index}]"
    path = resolve_clip_asset(declaration["asset"], project_folder)
    measured = measure_clip(path)
    if (measured.width, measured.height) != (int(width), int(height)):
        raise FullFrameDeclarationError(
            f"{label} names {path!r}, which measures "
            f"{measured.width}x{measured.height}; this reel delivers "
            f"{width}x{height}. Fitting it would letterbox the mark "
            f"small or crop it, and which of those to do is taste the "
            f"engine may not take - re-author the file at the delivery "
            f"frame.")
    # How many TIMELINE frames Resolve lays this clip down over, which
    # is the source's own frame count conformed to the reel's rate -
    # NOT `round(seconds * fps)`. Measured on the first build: 90 frames
    # of 30fps source on a 24000/1001 reel came back as 71 timeline
    # frames where `round(3.0 * 23.976)` predicted 72, and F13 refused
    # the reel by exactly that one frame. Resolve truncates, and the
    # captain's own hand placement of this animation on Reel 09 is 71
    # frames too. The epsilon keeps a same-rate clip (every rendered
    # card) at its own count rather than one short of it.
    duration_frames = int(math.floor(
        measured.frame_count * (fps / measured.fps) + 1e-6))
    if duration_frames <= 0:
        raise FullFrameDeclarationError(
            f"{label} conforms to {duration_frames} frames at "
            f"{fps:.3f}fps; a full-frame element must occupy at least one")
    # A cross-rate clip decimates, and the arithmetic is SAID - on the
    # card and on stderr - rather than left for the viewer to find.
    # 2026-09-17: the 30fps logo on a 24000/1001 reel drops 18 of 90
    # frames, double-stepping the glow decay every 5th frame, which is
    # what the captain heard as "fucked up".  The plan still places it:
    # this REPORTS, it never gates, and conforming the asset file stays
    # the project's act.
    conform_note = ""
    if abs(measured.fps - fps) > 1e-3:
        dropped = measured.frame_count - duration_frames
        conform_note = (
            f"{label} {os.path.basename(path)!r} runs at "
            f"{measured.fps:.3f}fps on a {fps:.3f}fps reel: "
            f"{measured.frame_count} source frames play over "
            f"{duration_frames} timeline frames ({dropped} dropped by "
            f"decimation). A clip at the reel's own rate plays 1:1 - "
            f"conform the asset file rather than retiming per reel.")
        print(f"  {conform_note}", file=sys.stderr)
    # What the shared store says about this file, when it says
    # anything: a covered master that moved on disk is SAID - on the
    # card and on stderr - rather than left for the build to bake in.
    # This REPORTS, it never gates, and the reader itself never
    # raises, so planning a clip with no store behind it costs one
    # silent manifest lookup and nothing else.
    library_note = _brandlib.library_note_for_asset(path)
    if library_note:
        print(f"  {library_note}", file=sys.stderr)
    return PlannedCard(
        index=index,
        element=declaration["element"],
        placement=declaration["placement"],
        reel_start_frame=int(reel_start_frame),
        duration_seconds=measured.duration_seconds,
        duration_frames=duration_frames,
        props={},
        render_name=os.path.splitext(os.path.basename(path))[0],
        source_frames=measured.frame_count,
        rendered_path=path,
        conform_note=conform_note,
        library_note=library_note,
    )


def _plan_one(declaration: dict, index: int, reel_start_frame: int,
              facts: ReelFacts, fps: float, width: int, height: int,
              safe_area, resolve_asset=None) -> PlannedCard:
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
    # A named image that resolves to nothing REFUSES the card by name.
    # The engine ships no artwork, so there is no substitute to draw -
    # and a card with a hole in it is the failure the refusal exists
    # to make impossible. A card naming no image carries no `image`
    # key at all, which is what keeps props written before this slot
    # existed rendering byte-identically.
    if declaration.get("image"):
        named = declaration["image"]
        url = resolve_asset(named) if resolve_asset else ""
        if not url:
            raise FullFrameDeclarationError(
                f"full_frame_elements[{index}] names image={named!r}, "
                f"which is not in the project's brand_assets/"
                if resolve_asset else
                f"full_frame_elements[{index}] names image={named!r} "
                f"and this caller supplied no way to look a project "
                f"asset up")
        props["image"] = url
        if declaration.get("image_width") is not None:
            props["imageWidth"] = declaration["image_width"]

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
                 index: int,
                 span_context: Optional[dict] = None) -> dict:
    """One run with its words settled, or a refusal naming what was empty.

    ``span_context`` carries a span segment's own window
    (``{"range_index": i, "ranges": [...], "transcript": {...},
    "window": (start, end)}``) and is how a run binding to
    ``range_line`` quotes it.  Without one that binding refuses: outside
    a span segment 'that window' names nothing.
    """
    if run["bind"]:
        if run["bind"] == "speakers":
            text = declaration["separator"].join(
                s for s in facts.speakers if s)
        elif run["bind"] == "range_line":
            if span_context is None:
                raise FullFrameDeclarationError(
                    f"full_frame_elements[{index}] binds a run to "
                    f"'range_line' outside a span segment. That binding "
                    f"quotes the segment's own window, so on a card - "
                    f"which covers no window - it names nothing.")
            text = ReelFacts.range_line(
                span_context["range_index"], span_context["ranges"],
                span_context["transcript"],
                window=span_context.get("window"))
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


def _word_cues(label: str, position: int, window: tuple[float, float],
               owner: int, ranges: Sequence[tuple[float, float]],
               transcript: dict, fps: float, duration_frames: int,
               resolved: tuple[dict, ...]) -> list[dict]:
    """One segment's word clock: what the reveal lands on, and when.

    Each cue carries the word's segment-local seconds (``start``/``end``,
    frame-exact on the same per-edge rounding the plan lays the segment
    down in) and ``chars`` - the cumulative characters shown through the
    end of that word, counted over the segment's resolved runs in the
    order ``typewriterSplit`` reveals them.  The composition owns the
    time-to-progress mapping and never aligns text itself; it just shows
    ``chars`` of the last cue whose ``start`` has passed.

    The clock runs on the segment's own ``window`` - its declared beat,
    or its whole keep range when it names none - so a subdivided beat's
    first word reads frame 0 rather than the range offset it plays at.
    Membership is in master seconds, ordering in reel time, the same
    split ``range_line`` makes.

    Two refusals, both by name.  A window with no timed words has no
    clock (the same rule that refuses an empty ``range_line``).  And the
    clock only exists where the segment quotes its window and nothing
    else: the resolved words must read exactly the window's spoken
    words, so a cue boundary always coincides with a word boundary on
    screen.  A segment carrying an eyebrow beside its quotation, or
    literal text that merely resembles the speech, would pace the
    reveal to words the viewer cannot match to glyphs - so it refuses
    rather than landing near words instead of on them.
    """
    from library.tools.reel_build import reel_time

    seg_label = f"{label}.segments[{position}]"
    ws, we = window
    rs, _ = ranges[owner]
    lo = sum(r[1] - r[0] for r in ranges[:owner]) + (ws - rs)
    spoken: list[tuple[str, float, float]] = []
    for segment in ((transcript or {}).get("segments") or []):
        for word in (segment.get("words") or []):
            if not word.get("timed"):
                continue
            text = str(word.get("word", ""))
            if not text:
                continue
            master = float(word["start"])
            if not (ws - WINDOW_EPS <= master < we - WINDOW_EPS):
                continue
            at = reel_time(master, ranges)
            if at is None:
                continue
            end_at = reel_time(float(word["end"]), ranges, at_end=True)
            if end_at is None:
                continue
            spoken.append((text, at - lo, end_at - lo))
    if not spoken:
        raise FullFrameDeclarationError(
            f"{seg_label} sets word_sync and its window [{ws:.3f}, "
            f"{we:.3f}] holds no timed words. There is no clock to pace "
            f"the reveal off - the same reading that refuses an empty "
            f"`range_line`.")
    drawn = "".join(run["text"] for run in resolved)
    if [w.lower() for w in drawn.split()] != [w.lower() for w, _, _ in spoken]:
        raise FullFrameDeclarationError(
            f"{seg_label} sets word_sync and its runs do not read exactly "
            f"the window's spoken words ({len(spoken)} word(s) there). A "
            f"word clock paces a quotation of the window - bind the "
            f"segment's run(s) to `range_line` - never an eyebrow beside "
            f"it or literal text about it.")
    cues: list[dict] = []
    cursor = 0
    for text, start, end in spoken:
        while cursor < len(drawn) and drawn[cursor].isspace():
            cursor += 1
        if drawn[cursor:cursor + len(text)].lower() != text.lower():
            raise FullFrameDeclarationError(
                f"{seg_label} sets word_sync and its runs do not read exactly "
                f"the window's spoken words ({len(spoken)} word(s) there). A "
                f"word clock paces a quotation of the window - bind the "
                f"segment's run(s) to `range_line` - never an eyebrow beside "
                f"it or literal text about it.")
        cursor += len(text)
        # Frame-exact on the plan's own arithmetic: seconds into the
        # segment, rounded to frames and clamped to the segment, so a
        # word starting on the edit point reads frame 0, not frame -1.
        start_f = min(max(int(round(start * fps)), 0), duration_frames)
        end_f = min(max(int(round(end * fps)), 0), duration_frames)
        cues.append({"word": text,
                     "start": start_f / fps,
                     "end": end_f / fps,
                     "chars": cursor})
    return cues


def _span_windows(label: str, segments: Sequence[dict],
                  ranges: Sequence[tuple[float, float]],
                  allow_gaps: bool
                  ) -> tuple[list[tuple[float, float]], list[int]]:
    """Every segment's effective window and its owning keep range.

    The check that REPLACED the segment-count refusal, and refuses
    everything it refused: a segment with no window covers its
    positional range, so more windowless segments than ranges, or a
    range no segment covers, still refuse - by window now, not by
    count. On top of that a declared window must fall inside exactly
    one keep range (never across the cut between two), windows must not
    overlap, segments run in play order, and the windows must tile
    every range unless ``allow_gaps`` explicitly leaves beats
    uncovered. Nothing here is a warning: each violation RAISES by
    name, because a span that silently desynchronises from the edit
    reaches the timeline as invented picture over real speech.

    Returns ``(windows, owners)`` in declaration order, which the
    caller lays in that same play order - enforced here, never sorted
    into it.
    """
    windows: list[tuple[float, float]] = []
    for position, segment in enumerate(segments, start=1):
        declared = segment.get("window")
        if declared is not None:
            windows.append((float(declared[0]), float(declared[1])))
        elif position - 1 < len(ranges):
            windows.append((float(ranges[position - 1][0]),
                            float(ranges[position - 1][1])))
        else:
            raise FullFrameDeclarationError(
                f"{label}.segments[{position}] names no `window`, so it "
                f"covers keep range {position - 1} by position - and the "
                f"reel plays only {len(ranges)} keep range(s). Give it a "
                f"`window` inside one keep range to subdivide it.")
    owners: list[int] = []
    for position, (ws, we) in enumerate(windows, start=1):
        hits = [k for k, (rs, re) in enumerate(ranges)
                if rs - WINDOW_EPS <= ws and we <= re + WINDOW_EPS]
        if not hits:
            played = ", ".join(f"({rs:.3f}, {re:.3f})" for rs, re in ranges)
            raise FullFrameDeclarationError(
                f"{label}.segments[{position}] declares window "
                f"[{ws:.3f}, {we:.3f}] and no keep range contains it. A "
                f"segment subdivides ONE keep range - its window must "
                f"fall inside exactly one keep range, never across the "
                f"cut between two. The reel plays {len(ranges)} keep "
                f"range(s): {played}.")
        if len(hits) > 1:
            raise FullFrameDeclarationError(
                f"{label}.segments[{position}] declares window "
                f"[{ws:.3f}, {we:.3f}] and it sits inside "
                f"{len(hits)} keep ranges at once; a window must fall "
                f"inside exactly one keep range. The reel's keep ranges "
                f"overlap, so redraw them before a span is planned over "
                f"them.")
        owners.append(hits[0])
    for position in range(2, len(windows) + 1):
        ws, _ = windows[position - 1]
        prev_we = windows[position - 2][1]
        owner, prev_owner = owners[position - 1], owners[position - 2]
        if owner < prev_owner:
            raise FullFrameDeclarationError(
                f"{label}.segments[{position}] covers keep range {owner} "
                f"after {label}.segments[{position - 1}] covered range "
                f"{prev_owner}. Declare segments in play order - range "
                f"by range, window by window inside each.")
        if owner == prev_owner and ws < prev_we - WINDOW_EPS:
            raise FullFrameDeclarationError(
                f"{label}.segments[{position}] opens at {ws:.3f} and "
                f"{label}.segments[{position - 1}] does not end until "
                f"{prev_we:.3f}. Two segments may not claim one second "
                f"of speech - declare segments in play order with each "
                f"window starting where the last ended.")
    for k, (rs, re) in enumerate(ranges):
        owned = [(ws, we) for (ws, we), o in zip(windows, owners) if o == k]
        holes: list[tuple[float, float]] = []
        if not owned:
            holes.append((rs, re))
        else:
            if owned[0][0] > rs + WINDOW_EPS:
                holes.append((rs, owned[0][0]))
            for (_, prev_we), (ws, _) in zip(owned, owned[1:]):
                if ws > prev_we + WINDOW_EPS:
                    holes.append((prev_we, ws))
            if owned[-1][1] < re - WINDOW_EPS:
                holes.append((owned[-1][1], re))
        if holes and not allow_gaps:
            start, end = holes[0]
            raise FullFrameDeclarationError(
                f"keep range ({rs:.3f}, {re:.3f}) plays {start:.3f}-"
                f"{end:.3f}s that no segment covers. Windows must tile "
                f"every keep range - share the exact boundary second "
                f"between neighbours, or declare `allow_gaps: true` on "
                f"the span to leave beats uncovered on purpose.")
    return windows, owners


def _plan_span(declaration: dict, index: int, facts: ReelFacts,
               ranges: Optional[Sequence[tuple[float, float]]],
               transcript: Optional[dict],
               fps: float, width: int, height: int,
               project_folder: Optional[str],
               resolve_asset=None) -> list[PlannedCard]:
    """One span declaration into one segment per declared window.

    Each segment covers its window and nothing else: its frames are the
    window's own per-edge rounding - ``int(round(end * fps)) -
    int(round(start * fps))``, the same arithmetic
    ``reel_build.placements`` lays footage down in - laid at the
    window's own offset inside the body, so tiling windows abut exactly
    and together cover the body's own seconds, the way the seven
    back-to-back cards of docs/ANIMATED_REEL_CEILING.md did by hand. A
    declared gap lays as a hole in reel positions: the plan lays what
    was declared, and the placer and the verifier grade the hole rather
    than a stretch the engine invented.

    Each segment renders through the FullFrameCard composition, opaque,
    like a card: the span is a word the planner can choose, not a second
    renderer.  ``render_name`` says ``span`` rather than ``card`` so the
    verifier identifies it as what it is
    (``reel_conformance_verifier.CARD_NAME_SHAPE``).
    """
    label = f"full_frame_elements[{index}]"
    if not ranges:
        raise FullFrameDeclarationError(
            f"{label} is a span and no keep ranges were passed to plan it "
            f"from; a span anchors one segment per range, so without them "
            f"there is nothing to anchor to. reel_build.plan_cards passes "
            f"the ranges the build is about to place.")
    segments = declaration["segments"]
    ranges = list(ranges)
    windows, owners = _span_windows(
        label, segments, ranges, bool(declaration.get("allow_gaps", False)))
    if transcript is None:
        transcript = {}

    safe_area = resolve_safe_area(project_folder, width=width, height=height)
    # The body frame each keep range starts at: ranges play end to end
    # in list order, so a window's reel position is its range's offset
    # plus its distance into that range. A windowless span resolves to
    # one window per range in order, which is exactly the cursor the
    # old count check accumulated - same numbers, no special case.
    body_offsets: list[int] = []
    cursor = 0
    for range_start, range_end in ranges:
        body_offsets.append(cursor)
        cursor += int(round(range_end * fps)) - int(round(range_start * fps))
    planned: list[PlannedCard] = []
    for position, (segment, (ws, we), owner) in enumerate(
            zip(segments, windows, owners), start=1):
        range_start, _ = ranges[owner]
        reel_start_frame = (body_offsets[owner]
                            + int(round(ws * fps))
                            - int(round(range_start * fps)))
        duration_frames = int(round(we * fps)) - int(round(ws * fps))
        if duration_frames <= 0:
            raise FullFrameDeclarationError(
                f"{label}.segments[{position}] declares window "
                f"[{ws:.3f}, {we:.3f}], which rounds to "
                f"{duration_frames} frames at {fps:.3f}fps; a segment "
                f"must occupy at least one")
        context = {"range_index": owner, "ranges": ranges,
                   "transcript": transcript, "window": (ws, we)}
        resolved = tuple(
            _resolve_run(run, declaration, facts, index,
                         span_context=context)
            for run in segment["runs"])
        # The character THIS segment dances to: its own declaration, or
        # the span's when it names none. `_normalise_span` already
        # proved the effective character paces beside `word_sync`, so
        # this is a lookup, never a second check.
        effective_entrance = segment.get("entrance") or declaration["entrance"]
        # The ground THIS segment draws on: its own declaration, or
        # the span's when it names none. The span's stays required, so
        # there is always a default - just never one the engine chose.
        background = segment.get("background") or declaration["background"]
        props: dict = {
            "runs": [dict(run) for run in resolved],
            "background": background,
            "entrance": effective_entrance,
            "exit": declaration["exit"],
            "fontFamily": declaration["font_family"],
            "fps": fps,
            "width": width,
            "height": height,
            "durationInFrames": duration_frames,
            "safeArea": safe_area.as_props(),
        }
        # The declared emphasis colour, inherited by every segment.
        # Absent unless declared, which is what keeps props written
        # before this slot existed rendering byte-identically - and what
        # keeps an undeclared emphasis drawing nothing rather than a
        # fallback nobody chose.
        if declaration.get("emphasis_colour") is not None:
            props["emphasisColour"] = declaration["emphasis_colour"]
        if declaration["y"] is not None:
            props["y"] = declaration["y"]
        if declaration["font_file"]:
            props["fontFile"] = static_font_path(declaration["font_file"])
        # A segment naming an image resolves it through the same slot a
        # card's mark takes, and refuses by name when it resolves to
        # nothing - the same refusal, because it is the same hole. A
        # segment naming no image carries no `image` key at all, which
        # is what keeps its props rendering byte-identically.
        if segment.get("image"):
            named = segment["image"]
            url = resolve_asset(named) if resolve_asset else ""
            seg_label = f"{label}.segments[{position}]"
            if not url:
                raise FullFrameDeclarationError(
                    f"{seg_label} names image={named!r}, "
                    f"which is not in the project's brand_assets/"
                    if resolve_asset else
                    f"{seg_label} names image={named!r} "
                    f"and this caller supplied no way to look a project "
                    f"asset up")
            props["image"] = url
            if segment.get("image_width") is not None:
                props["imageWidth"] = segment["image_width"]
        # A word-paced reveal, measured off the transcript's own timings -
        # the routing this layer was missing (docs/ANIMATED_REEL_CEILING.md).
        # Absent unless declared, which is what keeps props written before
        # this slot existed rendering byte-identically.
        if declaration.get("word_sync"):
            props["wordCues"] = _word_cues(
                label, position, (ws, we), owner, ranges, transcript,
                fps, duration_frames, resolved)
        planned.append(PlannedCard(
            index=position,
            element="full_frame_span",
            placement=SPAN_PLACEMENT,
            reel_start_frame=reel_start_frame,
            duration_seconds=duration_frames / fps,
            duration_frames=duration_frames,
            props=props,
            render_name=f"reel_{facts.reel_number:02d}_span_{position:02d}",
            resolved_runs=resolved,
        ))
    return planned


# ── Rendering ────────────────────────────────────────────────────────


def render_reel_cards(cards: Sequence[PlannedCard],
                      remotion_dir: str,
                      output_dir: str,
                      stream=sys.stderr,
                      project_folder: str = "") -> list[PlannedCard]:
    """Render every planned card to an OPAQUE file, and say where each is.

    Returns the SAME cards with :attr:`PlannedCard.rendered_path` filled
    in, so what is placed on the timeline is the object that was planned
    rather than a second record of it that could disagree.

    Opaque, and this is the one place the difference between this layer
    and the overlay layer is visible in what is rendered: ``4.05`` and
    ``timed_text_render`` composite over picture, while a full-frame
    element IS the picture, so it renders on its declared ground.
    ProRes 4444 for the same reason every other render here uses it - it
    is what the reels media pool already carries.

    One batch, not one process per card: every card goes through the
    shared renderer (``library/tools/remotion_batch.py``), which bundles
    once and draws every card through one browser, instead of paying a
    bundle-and-launch per card. Where the HyperFrames engine is
    selected (the project wins over the user), each card draws through
    its HyperFrames form instead - flattened over its own declared
    ground, because the engine composites and never grounds - stated
    per card in the output.

    A failed render RAISES.  Nothing downstream would notice a missing
    card: the reel would simply start on speech, which is what every reel
    did before this existed.
    """
    if not cards:
        return []
    # A `full_frame_clip` is a file the PROJECT authored; the engine
    # places it verbatim and never re-draws one (AGENTS.md 13), so it
    # arrives with `rendered_path` already filled and passes straight
    # through. It is kept in the returned order because the order is
    # play order and the placer reads it.
    to_render = [c for c in cards if not c.rendered_path]
    if not to_render:
        return list(cards)
    os.makedirs(output_dir, exist_ok=True)
    from library.tools import graphics_renderer as _engines
    if _engines.is_hyperframes(project_folder or None):
        print(f"    engine: HyperFrames for {len(to_render)} full-frame "
              f"card(s) (selected by {_engines.USER_SETTING_KEY} or the "
              f"project's pipeline.graphics_renderer; {FULL_FRAME_COMPOSITION} "
              f"has a HyperFrames form)", file=stream)
        return _render_reel_cards_hyperframes(
            to_render, cards, remotion_dir, output_dir, stream,
            project_folder or "")
    jobs: list[RenderJob] = []
    for card in to_render:
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
        jobs.append(RenderJob(props=card.props, out_path=out_path))
    try:
        results = render_batch(
            jobs, composition=FULL_FRAME_COMPOSITION,
            work_dir=output_dir,
            # The per-card budget, kept: a batch of N cards may take N
            # times what one card may, mechanically, with no new number.
            timeout=RENDER_TIMEOUT_SECONDS * max(1, len(jobs)),
            # The batch derives the Remotion directory from the repo
            # root; the caller handed the directory itself, so go up one
            # - the same step 4.05 takes (its `PersistentRenderer` seam).
            repo_root=os.path.dirname(os.path.abspath(remotion_dir)))
    except RemotionBatchError as exc:
        raise FullFrameRenderError(
            f"could not render {len(jobs)} full-frame card(s) through "
            f"{FULL_FRAME_COMPOSITION}: {exc}") from exc
    by_out = {res.get("out"): res for res in results}
    drawn: dict[int, PlannedCard] = {}
    for card, job in zip(to_render, jobs):
        res = by_out.get(job.out_path) or {}
        if not res.get("ok"):
            raise FullFrameRenderError(
                f"render of {card.render_name} "
                f"({FULL_FRAME_COMPOSITION}) failed: "
                f"{res.get('error', 'no result for this card')[:500]}")
        if not os.path.isfile(job.out_path) or os.path.getsize(
                job.out_path) == 0:
            raise FullFrameRenderError(
                f"render reported success but {job.out_path} is missing "
                f"or empty")
        drawn[id(card)] = replace(card, rendered_path=job.out_path)
        print(f"      OK: {job.out_path} "
              f"({os.path.getsize(job.out_path)} bytes)",
              file=stream)
    # In PLAY order, with the project's own clips where they were: the
    # placer walks this list and a card missing from it is a card the
    # timeline never gets.
    return [drawn.get(id(card), card) for card in cards]


def _render_reel_cards_hyperframes(to_render: Sequence[PlannedCard],
                                   cards: Sequence[PlannedCard],
                                   remotion_dir: str,
                                   output_dir: str,
                                   stream,
                                   project_folder: str) -> list[PlannedCard]:
    """Draw every planned card through its HyperFrames form, opaque.

    One card at a time - HyperFrames has no shared-bundle batch to
    amortise, and the engine's own run is seconds per card. A failed
    card raises :class:`FullFrameRenderError` naming it, the way the
    batch path always has: a missing card is a hole in the reel, never
    a warning.
    """
    from library.tools import hyperframes_render as _hf

    drawn: dict[int, PlannedCard] = {}
    for card in to_render:
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
        try:
            _hf.render_one_card(
                FULL_FRAME_COMPOSITION, card.props, out_path, output_dir,
                project_folder,
                os.path.dirname(os.path.abspath(remotion_dir)))
        except (_hf.HyperFramesUnavailable,
                _hf.HyperFramesRenderError) as exc:
            raise FullFrameRenderError(
                f"render of {card.render_name} "
                f"({FULL_FRAME_COMPOSITION}) through HyperFrames failed: "
                f"{exc}") from exc
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise FullFrameRenderError(
                f"render reported success but {out_path} is missing "
                f"or empty")
        drawn[id(card)] = replace(card, rendered_path=out_path)
        print(f"      OK: {out_path} "
              f"({os.path.getsize(out_path)} bytes)",
              file=stream)
    return [drawn.get(id(card), card) for card in cards]


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


# ── Where a card sits: the card row ──────────────────────────────────
#
# A head/tail card occupies seconds where no footage plays, so any row
# shows it - and the row it lands on is organisation, not compositing.
# The captain's rows are NAMED for what they carry (V1 Akshita, V5
# Semantic, V6/V7 Motion Graphics), and the closing logo animation
# landed on V1 - Akshita's camera row - on every reel because the reel
# builder placed it on the first a-roll row by POSITION, with nothing
# declared to say otherwise (2026-09-12, captain's blue marker on Reel
# 13: "we organized the rows in the timeline in a manner to be
# organized").
#
# So the card row is DECLARED, once per project, as a track-plan ROLE -
# never a track index, because indices differ per reel (5, 6 or 7 video
# tracks). The builder resolves it through `timeline_layout`'s
# `rows_for_role`, the same route the explainer, semantic visuals and
# lower thirds already take, and refuses a reel whose cards name no
# role: defaulting is what put the logo on Akshita's row.
#
# Which role is the captain's call (V5 "Semantic", where they hand-placed
# the logo on Reel 09, or the "Motion Graphics" row that did not exist
# then). The declaration lives beside the elements, under `effect:` -
# the PROJECT winning over the brand template, because a card is
# artwork-adjacent organisation of the project's own timeline
# (AGENTS.md 14)::
#
#     effect:
#       card_row_role: semantic   # or: motion_graphics
#       full_frame_elements:
#         - element: full_frame_clip
#           ...
#
# A `full_frame_span` is NOT governed by this: a span IS the body's
# picture (the footage video beneath it is suppressed), so it stays on
# the picture row and F23 keeps grading it there. This role names where
# head/tail cards and clips play - the elements that sit beside the
# footage, not in place of it.

#: The roles a card row may take. TWO, and only two: the captain's own
#: candidates. Anything else is refused by name.
#:
#: Re-exported from `timeline_layout.CARD_ROW_ROLES` (imported at top)
#: - one enumeration, not two: the layout owner decides what each role
#: means on a timeline, this module decides how a project declares one.

#: The `effect:`-level key carrying the declaration, beside
#: `full_frame_elements`.
CARD_ROW_ROLE_KEY = "card_row_role"


class CardRowRoleError(FullFrameDeclarationError):
    """The card row role is missing or names no role cards may take."""


def resolve_card_row_role(brand_effect: dict[str, Any] | None,
                          project_folder: str | None) -> str | None:
    """The declared card row role, or None where nothing declares one.

    The PROJECT's `project.yaml` wins over the brand template's `effect`
    slot - the same precedence `resolve_declaration` gives the elements
    themselves. A value that names no card role RAISES rather than
    passing through: a project that wrote one believes it reached the
    picture.
    """
    role: Any = None
    if isinstance(brand_effect, dict):
        role = brand_effect.get(CARD_ROW_ROLE_KEY)
    if project_folder:
        project_yaml = os.path.join(project_folder, "project.yaml")
        if os.path.exists(project_yaml):
            import yaml
            with open(project_yaml, "r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
            project_effect = (config.get("effect") or {})
            if not isinstance(project_effect, dict):
                raise CardRowRoleError(
                    f"{project_yaml}: `effect` must be a mapping, got "
                    f"{type(project_effect).__name__}")
            if CARD_ROW_ROLE_KEY in project_effect:
                role = project_effect[CARD_ROW_ROLE_KEY]
    if role is None:
        return None
    normalised = str(role).strip().lower()
    if normalised not in CARD_ROW_ROLES:
        raise CardRowRoleError(
            f"effect.{CARD_ROW_ROLE_KEY} is {role!r}: a card row is one "
            f"of {', '.join(CARD_ROW_ROLES)} - the captain's own "
            f"candidates. A track index is refused here on purpose: "
            f"indices differ per reel, so a number is wrong on a reel "
            f"with a different track count.")
    return normalised


def require_card_row_role(brand_effect: dict[str, Any] | None,
                          project_folder: str | None) -> str:
    """The declared card row role, or a refusal naming the choice.

    Called where cards are about to be PLACED, never where they are
    merely planned: a reel whose cards name no row refuses rather than
    defaulting to the first a-roll row, which is the defect this
    declaration exists to close.
    """
    role = resolve_card_row_role(brand_effect, project_folder)
    if role is None:
        raise CardRowRoleError(
            "this reel declares full-frame cards but no "
            f"effect.{CARD_ROW_ROLE_KEY}: declare one of "
            f"{', '.join(CARD_ROW_ROLES)} in the project's project.yaml "
            f"(or the brand template's `effect` slot). Which row the "
            f"closing card belongs on is the captain's call - V5 "
            f"\"Semantic\", where they hand-placed the logo on Reel 09, "
            f"or the \"Motion Graphics\" row. Until it is declared the "
            f"build stops rather than guessing V1.")
    return role


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
