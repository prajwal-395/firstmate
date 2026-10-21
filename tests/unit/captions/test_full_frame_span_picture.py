"""A span's declared gap is a gap in the COPY, never a hole in the PICTURE.

The defect this names
---------------------
The reel-13 rebuild (PR 3048 fixed the Fusion refusal; the promotion was
still refused) failed the conformance gate on **F1: fourteen black holes
of one to twelve frames on V1**.  Every one of them was a beat the reel's
own declared span windows left uncovered - the declaration names one
segment per spoken phrase, so the silences BETWEEN the phrases were
beats no segment covered.

A `full_frame_span` IS the picture for the reel's whole body: `reel_build`
suppresses the footage video because the span replaces it.  So a beat no
segment covered was not "a quiet moment" - it was **black**, and the
conformance gate refused the reel for it.  The plan laid the hole on
purpose ("a declared gap lays as a hole in reel positions") and the gate
graded it as an error, which means **a declaration Ren accepted could
never be promoted**: the two halves of Ren disagreed about the same
seconds, and the reel lost.

The fix is in the PLAN, not in the gate: the declared windows are the
beats the copy speaks in, and the beats between them are still the
span's picture.  Each segment now lays its own window AND the beats that
follow it, up to the next segment's window, so the picture tiles every
keep range edge to edge.  The gate is untouched: a keep range that no
segment covers at all is still a hole, and the gate still refuses it.

What this file proves
---------------------
- a declaration whose windows leave gaps lays a CONTINUOUS picture, and
  the conformance gate's own F1 check finds no hole in it;
- the copy still lands on the words: the word clock is measured from the
  segment's picture start, so a segment carrying ground in front of its
  words still reveals on them;
- a keep range NO segment covers is still a hole, and F1 still refuses
  it - the gate that must not be weakened;
- a declaration that does not declare `allow_gaps` and leaves a beat
  uncovered still refuses at PLAN time, by name.
"""
from __future__ import annotations

import pytest

from library.tools import full_frame_element as ffe
from library.tools import reel_clock
from library.tools import reel_conformance_verifier as verifier

FPS = 24000 / 1001
WIDTH = 1080
HEIGHT = 1920

ONE_RANGE = [(10.0, 11.0)]
TWO_RANGES = [(10.0, 11.0), (12.0, 13.0)]
THREE_RANGES = [(10.0, 11.0), (12.0, 13.0), (14.0, 15.0)]

# Two windows a phrase-per-segment declaration makes: they leave a beat
# of silence between them, and the silence is inside the range.
GAPPED = [(10.0, 10.4), (10.6, 11.0)]
TILED = [(10.0, 10.5), (10.5, 11.0)]


def _transcript(windows):
    """Two timed words inside every declared window."""
    segments = []
    for ws, we in windows:
        words = []
        at = ws + 0.05
        for word in ("alpha", "beta"):
            words.append({"word": word, "start": at, "end": at + 0.1,
                          "timed": True})
            at += 0.15
        segments.append({"speaker": "SpeakerOne", "start": ws, "end": we,
                         "text": "alpha beta", "words": words})
    return {"segments": segments}


class _Moment:
    number = 13
    speakers = ("SpeakerOne",)


def _facts(transcript, ranges):
    return ffe.ReelFacts.from_moment(
        _Moment(), ranges, transcript, opening_seconds=3.0)


def _span(windows, **overrides):
    """A span declaration with one segment per declared window."""
    declaration = {
        "element": "full_frame_span",
        "placement": "span",
        "background": "#0B1B2B",
        "entrance": "draw",
        "exit": "fade",
        "font_family": "Montserrat",
        "word_sync": True,
        "segments": [
            {"window": list(window),
             "runs": [{"bind": "range_line", "type_role": "display",
                       "colour": "#FFFFFF"}]}
            for window in windows
        ],
    }
    declaration.update(overrides)
    return {"full_frame_elements": [declaration]}


def _plan(declaration, transcript, ranges=ONE_RANGE):
    return ffe.plan_reel_cards(
        ffe.declared_elements(declaration),
        _facts(transcript, ranges), 0, FPS,
        width=WIDTH, height=HEIGHT,
        project_folder=None,
        ranges=ranges, transcript=transcript)


def _timeline_items(cards):
    """The planned cards as the timeline holds them: V1, one item each."""
    return [
        verifier.TimelineItem(
            track_type="video", track_index=1,
            start_frame=card.reel_start_frame,
            end_frame=card.reel_start_frame + card.duration_frames,
            duration_frames=card.duration_frames,
            source_start_frame=0, source_end_frame=card.duration_frames,
            source_file=f"{card.render_name}.mov", speaker=None,
            name=card.render_name)
        for card in cards
    ]


def test_a_declared_gap_is_covered_by_the_ground_and_not_laid_as_a_hole():
    """THE DEFECT.  A span's windows leave beats uncovered, and those
    beats are the SPAN's picture: a span replaces the footage for the
    whole body, so a beat no segment covered was BLACK, and the
    conformance gate refused the reel for it.

    The plan now lays each segment's own window AND the beats that
    follow it, so the picture tiles the keep range and the gate finds no
    hole.
    """
    transcript = _transcript(GAPPED)
    cards = _plan(_span(GAPPED, allow_gaps=True), transcript)

    assert len(cards) == 2
    # The picture is continuous: the first segment starts at the range's
    # first frame and the second ends at the range's last.
    assert cards[0].reel_start_frame == 0
    assert cards[-1].reel_start_frame + cards[-1].duration_frames == 24
    # And the gate that refused the reel finds nothing to refuse.
    findings = verifier.check_picture_holes(
        "Reel 13", _timeline_items(cards))
    assert findings == [], (
        "the span's picture still has a hole in it: "
        f"{[f.message for f in findings]}")


def test_the_gap_is_carried_by_the_segment_that_speaks_before_it():
    """The beat between two phrases belongs to the phrase BEFORE it: that
    segment's picture runs on to the next segment's window, so the ground
    covers the silence instead of the reel cutting to black and back."""
    transcript = _transcript(GAPPED)
    cards = _plan(_span(GAPPED, allow_gaps=True), transcript)

    # The first segment's window is 10.0-10.4 (0.4s = 10 frames) and the
    # next segment's window opens at 10.6, so the first segment carries
    # the silence: 0.6s = 14 frames.
    assert cards[0].duration_frames == 14
    assert cards[1].duration_frames == 10
    # The second segment starts exactly where the first one ends.
    assert cards[1].reel_start_frame == 14


def test_the_copy_still_lands_on_the_words_when_the_ground_leads_them():
    """A segment that carries ground in front of its words still reveals
    on them: the word clock is measured from the segment's PICTURE
    start, not from its window's start."""
    transcript = _transcript(GAPPED)
    cards = _plan(_span(GAPPED, allow_gaps=True), transcript)

    # The first segment's picture starts at the range's first frame
    # (10.0) while its window opens at 10.05, so its first word lands
    # inside the file on the frame the reel plays it on.
    card = cards[0]
    word = transcript["segments"][0]["words"][0]["start"]
    expected = reel_clock.reel_frame_offset(ONE_RANGE[0], word, FPS)
    cue_frame = round(card.props["wordCues"][0]["start"] * FPS)
    assert card.reel_start_frame + cue_frame == expected


def test_a_keep_range_no_segment_covers_is_still_a_hole_and_still_refused():
    """THE GATE THAT MUST NOT BE WEAKENED.  A range that no segment
    covers at all has nothing to draw it: the ground cannot cover a beat
    no segment declared, so the reel's picture has a hole in it and F1
    refuses it by name."""
    windows = [(10.0, 11.0), (14.0, 15.0)]
    transcript = _transcript(windows)
    cards = _plan(_span(windows, allow_gaps=True), transcript,
                  ranges=THREE_RANGES)

    findings = verifier.check_picture_holes(
        "Reel 13", _timeline_items(cards))
    assert any(f.finding_class == verifier.FindingClass.F1
               for f in findings), (
        "a keep range no segment covers was graded as covered - the "
        "hole the gate exists for is gone")


def test_a_declaration_that_leaves_a_beat_uncovered_still_refuses_by_name():
    """Without `allow_gaps` the plan refuses the same declaration at
    PLAN time, by name: the refusal is fast, and it is the same
    refusal the gate would have made three hours later."""
    transcript = _transcript(GAPPED)

    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="no segment covers"):
        _plan(_span(GAPPED), transcript)


def test_a_span_that_tiles_its_range_is_unchanged():
    """A declaration whose windows tile the range lays exactly what it
    did before: the picture of a segment is its own window, because
    there is no gap to carry."""
    transcript = _transcript(TILED)
    cards = _plan(_span(TILED), transcript)

    assert [c.duration_frames for c in cards] == [12, 12]
    assert [c.reel_start_frame for c in cards] == [0, 12]
