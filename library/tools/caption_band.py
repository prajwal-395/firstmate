"""Which band of the frame the captions own, and when they own it.

**The problem this closes.**  ``motion_graphics_vocabulary``'s
``lower_third`` entry has been flagged ``needs_renderer_work`` since the
roster was written, with the note::

    No component. Also needs a caption-collision rule, because the safe
    band it wants is the band plan_subtitles is already grouping cards
    into.

The first half stopped being true: ``MotionGraphics/index.tsx`` draws a
backdrop-blurred attribution block with a coloured left rule, and has
since PR #602.  The second half was still true, so the flag stayed and
``motion_graphics_plan.DRAWABLE`` - derived from the flag - kept dropping
every planned lower third as ``renderer_cannot_draw_it_yet``.  **A
component that draws, refused at plan time by a note about a rule nobody
had written.**

This module is that rule, and it is the executable form of a refusal the
roster already declares in prose.  ``lower_third``'s own ``never`` says:

    Overlapping a caption card. In a vertical frame the captions own the
    lower band; a lower third that collides with one has to move up or
    not be drawn.

The engine may not move it - choosing a new anchor is choosing a
position - so it is not drawn, and the drop is recorded by name.
AGENTS.md: a prerequisite is EXECUTABLE, not prose.

**Nothing here is a threshold and nothing here is taste.**  Three facts
are read from where they are already stated:

* **Which band the captions occupy** comes from the project's own
  declared caption ``position`` (``subtitle_style.resolve_subtitle_style``),
  including every per-speaker override, because a project may caption two
  speakers differently and one of them may sit somewhere else.  The union
  is what is occupied - a band one speaker's captions use is occupied
  whether or not the other speaker's do.
* **Which anchors lie in a band** comes from the anchor's own name.  The
  vocabulary's grid is ``{top,middle,bottom} x {left,centre,right}`` and
  ``anchorStyle`` decomposes it exactly that way; ``centre`` is the middle
  band's centre cell.
* **When the captions are on screen** comes from the spine.
  ``step_4_01_plan_subtitles`` emits cards for blocks whose ``block_type``
  is ``hook`` or ``speech`` and for no others, so those blocks' spans are
  when a caption card exists.  :data:`CAPTIONED_BLOCK_TYPES` is that
  enumeration and ``tests/unit/captions/test_caption_band.py`` reads 4.01's source to
  prove the two still agree.

**What is refused, and what is not.**  Only an element that draws COPY.
Two blocks of text in one band is unreadable; a bar, a bracket or a bug
in the same band is not a collision, and refusing one would be a gate
failing correct output (AGENTS.md 10.4).  Whether an element draws copy
is read from the roster entry (``copy == "required"``) or from the plan
entry actually carrying runs, so an ``optional``-copy element that
supplies text is refused and the same element without text is not.
Nothing is refused outside a captioned span: a graphic in the lower band
during a non-speech beat has nothing to collide with.

**The assumption this rule makes, said out loud.**  It reads the spine,
not the caption plan, because step 4.06 has no DAG edge from
``plan_subtitles`` and adding one to answer a geometry question would
couple the overlay layer's timebase to the caption planner's.  A run that
deliberately skips ``render_subtitles`` therefore gets a refusal it did
not need.  That run's drop record names this rule, so the operator can
see which rule cost them the element rather than finding a graphic
missing with no account of why.

``tests/unit/captions/test_caption_band.py``.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from library.tools import motion_graphics_vocabulary as vocabulary
from library.tools.subtitle_style import (
    VALID_POSITIONS,
    project_speaker_styles,
    resolve_subtitle_style,
)

#: The block types `step_4_01_plan_subtitles.plan_subtitles` emits caption
#: cards for.  Its own line is `if block_type not in ("hook", "speech"):
#: continue`, so these two spans are when a caption card is on screen and
#: every other block type is when one is not.  Stated here rather than
#: imported because importing a step body pulls its whole dependency
#: tree; `tests/unit/captions/test_caption_band.py` reads 4.01's source and fails if
#: the two ever say different things.
CAPTIONED_BLOCK_TYPES: Tuple[str, ...] = ("hook", "speech")

#: The three vertical bands of the frame.  `anchorStyle` decomposes every
#: anchor into one of these plus a horizontal cell, and a caption's
#: `position` names one of them.
BANDS: Tuple[str, ...] = ("top", "middle", "bottom")

#: What a caption `position` means as a band.  `subtitle_style` spells the
#: middle one `center`; the vocabulary's anchors spell it `middle`. One
#: mapping, so the difference is a spelling rather than two ideas.
BAND_BY_CAPTION_POSITION: Dict[str, str] = {
    "top": "top",
    "center": "middle",
    "bottom": "bottom",
}


class CaptionBandError(ValueError):
    """A band question that cannot be answered from what was declared."""


def anchor_band(anchor: str) -> str:
    """The vertical band an anchor sits in.

    Read from the anchor's own name, which is how ``anchorStyle`` reads
    it: ``top_*`` is the top band, ``bottom_*`` the bottom, and
    ``middle_*`` and the bare ``centre`` the middle.
    """
    name = (anchor or "").strip().lower()
    if name.startswith("top"):
        return "top"
    if name.startswith("bottom"):
        return "bottom"
    if name.startswith("middle") or name == "centre":
        return "middle"
    raise CaptionBandError(
        f"{anchor!r} is not one of the vocabulary's anchor positions "
        f"({list(vocabulary.AXES_BY_NAME['anchor'].positions)}), so which "
        f"band it sits in cannot be read from its name."
    )


def occupied_bands(
    brand_effect: Optional[Dict[str, Any]] = None,
    brand_style: Optional[Dict[str, Any]] = None,
    project_folder: Optional[str] = None,
) -> FrozenSet[str]:
    """Every band this project's captions can occupy.

    The base style's ``position``, plus every position a per-speaker
    override declares.  A project that captions one speaker at the top
    and another at the bottom occupies both, and an element in either
    collides with somebody.
    """
    positions = {resolve_subtitle_style(
        brand_effect=brand_effect, brand_style=brand_style,
        project_folder=project_folder).get("position", "bottom")}

    for speaker, overrides in (project_speaker_styles(project_folder) or {}).items():
        stated = (overrides or {}).get("position")
        if stated:
            positions.add(stated)

    bands = set()
    for position in positions:
        if position not in VALID_POSITIONS:
            raise CaptionBandError(
                f"caption position {position!r} is not one of "
                f"{list(VALID_POSITIONS)}; which band it occupies cannot be "
                f"read, and guessing one would put a graphic under a caption."
            )
        bands.add(BAND_BY_CAPTION_POSITION[position])
    return frozenset(bands)


def captioned_spans(audio_spine: Optional[Dict[str, Any]]) -> Tuple[Tuple[float, float], ...]:
    """When a caption card is on screen, in timeline seconds.

    The spans of the spine blocks 4.01 captions.  A block with no timing
    contributes nothing rather than a span starting at zero: a block the
    spine did not time is not evidence that a caption plays at second 0.
    """
    spans: List[Tuple[float, float]] = []
    for block in ((audio_spine or {}).get("structure") or []):
        if not isinstance(block, dict):
            continue
        if block.get("block_type") not in CAPTIONED_BLOCK_TYPES:
            continue
        start = block.get("timeline_start")
        end = block.get("timeline_end")
        if start is None or end is None:
            continue
        try:
            start_s, end_s = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if end_s > start_s:
            spans.append((start_s, end_s))
    return tuple(spans)


def overlaps_a_caption(start: float, end: float,
                       spans: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """The first captioned span this one touches, or None.

    Half-open on both sides: a graphic that ends exactly where a speech
    block begins does not overlap it.
    """
    for span_start, span_end in spans:
        if start < span_end and end > span_start:
            return (span_start, span_end)
    return None


def collision(
    *,
    element_key: str,
    anchor: str,
    start: float,
    end: float,
    has_copy: bool,
    bands: FrozenSet[str],
    spans: Sequence[Tuple[float, float]],
) -> str:
    """Why this element collides with a caption card, or ``""``.

    Returns the detail line for the drop record, so the caller never has
    to restate the rule.  Three conditions, all of them read rather than
    chosen: the element draws copy, its anchor is in a band the captions
    occupy, and its span touches one where a card exists.
    """
    if not has_copy:
        return ""
    if not bands or not spans:
        return ""
    band = anchor_band(anchor)
    if band not in bands:
        return ""
    hit = overlaps_a_caption(start, end, spans)
    if hit is None:
        return ""
    return (
        f"{element_key!r} draws copy at anchor {anchor!r}, in the {band} band "
        f"the captions occupy, over {start:.2f}-{end:.2f}s, which touches the "
        f"captioned span {hit[0]:.2f}-{hit[1]:.2f}s. Two blocks of text in one "
        f"band is unreadable, and moving the graphic would be the engine "
        f"choosing a position."
    )
