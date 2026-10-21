"""A rendered card's FILE is its own pixels, not its slot.

The defect this names, measured 2026-10-20 on the Ren-owned scratch
copy `Ren Scratch - breaktest` (Reel 13):

    CompWindowUncovered: REFUSING to build [V1:14] reel_picture_14:
    GlobalOut 9 stops before comp frame 47, so the last 38 of the 48
    frames this item plays have no media under them.

The item that played 48 frames was a full-frame SPAN segment, and the
pool item that described it held 10 - the length of ANOTHER reel's
segment.  The cause is the file NAME: a rendered card was named by its
slot alone - `reel_13_span_15.mov` - and the slot is keyed by the reel
NUMBER.  A number is not an identity: that project held SEVERAL reels
numbered 13 (a re-proposal and a variant of the same moment), every one
of them rendered its segments to the same path, and one build renders
them all.  The file that reached the timeline was therefore whichever
reel rendered last, while the reel that placed a different length met a
media pool item describing somebody else's file - and the Fusion pass
sized that item's comp from the pool's frame count and refused the
whole reel at the end of a placement that had already cost minutes.

The name is now the card's IDENTITY - the readable slot plus a digest of
the drawing inputs - which is what the caption layer already does
(`library/tools/subtitle_segment_id`): two artefacts that differ in any
drawing input digest differently and cannot collide, and two that agree
are the same pixels, which is the sharing rather than the collision.

Every gate here is proved in BOTH directions, because a naming rule that
only ever refuses would be a rule nobody could satisfy, and one that
only ever agrees would be the defect again (AGENTS.md 10.4).
"""
from __future__ import annotations

import re

from library.tools import full_frame_element as ffe
from library.tools.reel_conformance_verifier import CARD_NAME_SHAPE

FPS = 24000 / 1001
RANGES = [(10.0, 16.0)]


class _Moment:
    """A reel, addressed the way the build addresses one."""

    number = 13
    speakers = ("SpeakerOne",)


def _transcript(text: str = "So ranking number one on Google") -> dict:
    words = text.split()
    return {"segments": [{
        "speaker": "SpeakerOne", "timeline_start": 10.0,
        "timeline_end": 16.0, "text": text,
        "words": [{"word": w, "start": 10.0 + i * 0.4,
                   "end": 10.35 + i * 0.4, "timed": True}
                  for i, w in enumerate(words)],
    }]}


def _facts(moment=None, transcript=None):
    return ffe.ReelFacts.from_moment(
        moment or _Moment(), RANGES, transcript or _transcript(),
        opening_seconds=3.0)


def _span(text: str, window=(10.0, 16.0)) -> dict:
    """A span declaration whose one segment draws `text` over `window`.

    The window is master seconds inside the keep range, and the segment's
    LENGTH is measured off it - a span states no duration of its own.
    """
    return {"full_frame_elements": [{
        "element": "full_frame_span",
        "placement": "span",
        "background": "#101014",
        "entrance": "blur",
        "exit": "fade",
        "font_family": "Montserrat",
        "allow_gaps": True,
        "segments": [{
            "window": list(window),
            "runs": [{"text": text, "type_role": "display",
                      "colour": "#FFFFFF"}],
        }],
    }]}


def _plan(declaration: dict, moment=None, transcript=None) -> list:
    return ffe.plan_reel_cards(
        ffe.declared_elements(declaration), _facts(moment, transcript),
        960, FPS, width=1080, height=1920, ranges=RANGES,
        transcript=transcript or _transcript())


def test_two_reels_that_share_a_number_never_share_a_card_file():
    """The measured defect: reel 13 and its variant, one path.

    Both reels are numbered 13 - the number is the reel's SLOT, not its
    identity - and both render a span segment into position 1.  Before
    the fix both wrote `reel_13_span_01.mov`, so one build left the
    other's card on disk, and the reel whose segment was a different
    length met a media pool item describing the other reel's file.
    """
    first = _plan(_span("listing for something healthcare related."))
    second = _plan(_span("a case study on their clients page"))
    assert first and second
    assert first[0].render_name.startswith("reel_13_span_01")
    assert second[0].render_name.startswith("reel_13_span_01")
    assert first[0].render_name != second[0].render_name, (
        "two reels numbered 13 wrote one file, so the file that reached "
        "the timeline was whichever reel rendered last - the measured "
        "cause of the CompWindowUncovered refusal")


def test_a_re_planned_card_is_a_new_file():
    """A card whose drawing changed may not land on the old file.

    The pool caches a media item's frame count at import, and the
    scripting API cannot refresh it: a file rewritten under a pool item
    leaves that item describing the previous render.  A new drawing
    therefore needs a new path, or the item under the old path still
    describes pixels that are gone.
    """
    first = _plan(_span("one wording"))
    again = _plan(_span("one wording"))
    reworded = _plan(_span("another wording"))
    assert first[0].render_name == again[0].render_name, (
        "the same plan must name the same file, or every rebuild "
        "re-imports every card and the pool grows without bound")
    assert first[0].render_name != reworded[0].render_name, (
        "a card that draws different pixels may not share the file of "
        "the card that drew the old ones")


def _three_segment_span(text: str, second_window=(10.6, 11.0)) -> dict:
    """A span declaration with three segments in one keep range.

    The SECOND segment's window decides how much of the range the FIRST
    segment's picture covers - the ground between two phrases belongs to
    the phrase before it - so moving that window changes the first
    segment's length.
    """
    return {"full_frame_elements": [{
        "element": "full_frame_span",
        "placement": "span",
        "background": "#101014",
        "entrance": "blur",
        "exit": "fade",
        "font_family": "Montserrat",
        "allow_gaps": True,
        "segments": [
            {"window": [10.0, 10.4],
             "runs": [{"text": text, "type_role": "display",
                       "colour": "#FFFFFF"}]},
            {"window": list(second_window),
             "runs": [{"text": "and the middle", "type_role": "display",
                       "colour": "#FFFFFF"}]},
            {"window": [11.0, 16.0],
             "runs": [{"text": "and the rest", "type_role": "display",
                       "colour": "#FFFFFF"}]},
        ],
    }]}


def test_a_card_that_changes_length_is_a_new_file():
    """The length is a drawing input, and it is the one that broke it.

    The refusal named two lengths: the pool item said 10 frames and the
    item played 48.  A card whose duration changed must not keep the
    name of the card that was a different length.
    """
    short = _plan(_three_segment_span("the same words",
                                      second_window=(10.6, 11.0)))
    long = _plan(_three_segment_span("the same words",
                                     second_window=(10.8, 11.0)))
    assert short[0].duration_frames != long[0].duration_frames
    assert short[0].render_name != long[0].render_name


def test_two_cards_that_draw_the_same_pixels_share_the_file():
    """The other half: sharing is not a collision.

    Two reels that draw the SAME card hold the same pixels, so they may
    hold one file.  That is the sharing the digest exists for - a
    refusal that separated them would multiply every render for nothing.
    """
    one = _plan(_span("the same words everywhere"))
    other = _plan(_span("the same words everywhere"))
    assert one[0].render_name == other[0].render_name
    assert one[0].props == other[0].props


def test_the_name_carries_the_slot_and_the_digest():
    """Readable first, then proof - the shape a reader can check."""
    card = _plan(_span("readable"))[0]
    assert re.fullmatch(r"reel_13_span_01_[0-9a-f]{8}", card.render_name), (
        f"the name is {card.render_name!r}: the slot, then the digest of "
        "the drawing inputs")
    # The digest is OF the props, so a reader can recompute it.
    from library.tools.render_cache import drawing_digest, short_digest
    assert card.render_name.endswith(
        short_digest(drawing_digest(card.props)))


def test_the_verifier_still_reads_a_card_by_its_name():
    """The out-of-band gate keeps its teeth.

    `CARD_NAME_SHAPE` is how the conformance verifier spots a timeline
    item shaped like a full-frame element that no declaration accounts
    for.  A shape that did not know the digest would read every current
    card as an out-of-band element, and a shape that required one would
    read every card rendered before 2026-10-20 the same way.
    """
    assert CARD_NAME_SHAPE.match("reel_13_span_01")
    assert CARD_NAME_SHAPE.match("reel_13_span_01_1a2b3c4d")
    assert CARD_NAME_SHAPE.match("reel_07_card_01_deadbeef")
    assert not CARD_NAME_SHAPE.match("reel_13_span_01_1a2b3c4d_extra")
    assert not CARD_NAME_SHAPE.match("reel_13_other_01")
