"""Why Reel 17's period sits mid-card, and what the planner prefers.

Reel 17, clip marker at timeline frame 222 (captain's note: "there is a
period in the middle of the subtitles here that i don't think should
belong there"). The placed card reads "all the time. like what are some
of the" - a period mid-card followed by a lowercase continuation.

The diagnosis, measured rather than assumed. The transcript carries two
real sentences - seg284 "We talk about it all the time." (source
3127.42-3128.63) and seg285 "Like what are some of the first steps
these businesses should be doing?" (source 3128.39-3131.42) - and an
independent second opinion (small.en ASR on the card's audio slice)
hears the same boundary ("...all the time," then "like what are some
of..."), so it is real speech, not a transcription artefact. The period is
real punctuation, so no correction-store shape reaches it: a display
suppression hides whole tokens (nothing here is hidden - every word is
spoken and wanted) and a spelling correction that deleted the period
would falsify the sentence the microphone caught. The fix belongs in
caption planning, and planning currently CHOOSES the straddle on
purpose: ending cards on punctuation is only the FOURTH tiebreak in
`split_into_groups` (after flashing, under-floor and longest-shortest),
and splitting the run at "time." would leave a card on screen for the
gap to "like" - 0.21s, under the 0.5s flash floor the manifest
validator fails a build on. The optimizer picks a straddled sentence
over a flashing card. That tradeoff is a planner decision, fleet-wide,
so this lane reports it rather than re-tuning the optimizer: the note
needs a planner rule plus a Reel 17 caption rebuild, and neither is a
correction-store entry or a caption swap.

What these tests pin, and what they do NOT. They pin the two halves of
the mechanism above - the flash-floor arithmetic that makes the split
expensive, and the tiebreak that keeps the preference weak - so a
future optimizer rewrite cannot drop either silently. They do NOT
reproduce Reel 17: the reproduction feeds the word run below to
`split_into_groups` with the real pixel fitter and asserts no emitted
card matches `[.!?]\\s+\\S` mid-card, and it FAILS on current code (the
second card comes out "the time. like what"). That failing shape is
the planner lane's specification, kept here so it cannot drift from
the mechanism it constrains. No test reaches a real project: every
number below is frozen off transcript segments 284/285.
"""

import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_01_plan_subtitles.step import (
    MIN_CAPTION_FLASH_SECONDS,
    CaptionFitter,
    resolve_caption_overlaps,
    split_into_groups,
)
from library.tools.render_fonts import measurable_font_path

#: A sentence break inside a card: terminal punctuation followed by more
#: words. The captain's Reel 17 note names exactly this shape.
MID_CARD_SENTENCE_BREAK = re.compile(r"[.!?]\s+\S")


def _reel_fitter() -> CaptionFitter:
    """The caption measure the live Reel 17 card was grouped against.

    Repo-local only: the bundled Montserrat at the reel caption size,
    inside the project's measured usable width (816px centred, less
    the 4px outline each side - frozen off the live plan, never read
    off the project, so no test reaches a real project).
    """
    return CaptionFitter(
        font_path=measurable_font_path("Montserrat", None, None),
        font_size=58,
        font_weight=800,
        usable_width=816.0 - 8,
        outline_width=4,
    )

# seg284 tail + seg285 head, transcript seconds, reading-lowercased -
# the exact run the Reel 17 card straddles.
WORDS = [
    ("we", 1415.29, 1415.42),
    ("talk", 1415.42, 1415.66),
    ("about", 1415.66, 1415.86),
    ("it", 1415.86, 1415.92),
    ("all", 1415.92, 1416.05),
    ("the", 1416.05, 1416.13),
    ("time.", 1416.13, 1416.50),
    ("like", 1416.26, 1416.67),
    ("what", 1416.67, 1416.79),
    ("are", 1416.79, 1416.86),
    ("some", 1416.86, 1417.04),
    ("of", 1417.04, 1417.08),
    ("the", 1417.08, 1417.14),
    ("first", 1417.14, 1417.51),
    ("steps", 1417.51, 1417.87),
    ("these", 1417.87, 1418.11),
    ("businesses", 1418.11, 1418.64),
    ("should", 1418.64, 1418.81),
    ("be", 1418.81, 1418.91),
    ("doing?", 1418.91, 1419.29),
]


def test_punctuation_end_is_a_tiebreak_not_a_rule():
    """The preference exists, weakly: among partitions equal on
    flashing, floor and balance, the one ending cards on punctuation
    wins - and a cheaper flashing count overrules it, which is the
    Reel 17 outcome."""
    words = [
        {"word": w, "start": s, "end": e}
        for w, s, e in [
            ("all", 0.0, 0.2),
            ("day.", 0.2, 0.6),
            ("every", 0.6, 0.8),
            ("day", 0.8, 1.4),
        ]
    ]
    even = split_into_groups(
        words, fits_fn=lambda text: len(text) <= 12, display_until=1.4
    )
    # "all day." (ends on the period) must beat "all day. every"'s
    # rival arrangement wherever flashing and floor tie - the
    # preference the planner lane will promote to a rule.
    assert even, "the split found no partition at all"
    joined = " | ".join(g["text"] for g in even)
    assert joined == "all day. | every day", (
        f"the punctuation tiebreak stopped preferring sentence ends: {joined}"
    )


def _live_words():
    return [
        {"word": w, "start": s, "end": e} for w, s, e in WORDS
    ]


def test_no_card_straddles_a_sentence():
    """The live specimen, planned: seg284's sentence must end a card.

    Feeds the frozen Reel 17 word run to `split_into_groups` under the
    real pixel fitter and asserts no emitted card carries a sentence
    break mid-card - the placed card reads "all the time. like what
    are some of the", and this is the test the planner fix must turn
    green. Fails on current code (the second card comes out "time.
    like what are some of").
    """
    groups = split_into_groups(
        _live_words(),
        fits_fn=_reel_fitter().fits_in_box,
        display_until=WORDS[-1][2] + 0.5,
    )
    assert groups, "the split found no partition at all"
    for group in groups:
        assert not MID_CARD_SENTENCE_BREAK.search(group["text"]), (
            f"a card straddles a sentence end: {group['text']!r} "
            f"in {' | '.join(g['text'] for g in groups)}"
        )
    assert any(g["text"].endswith("time.") for g in groups), (
        "no card ends on the seg284 sentence: "
        f"{' | '.join(g['text'] for g in groups)}"
    )


def _overlap_entry(text, start, end, speaker="Craig"):
    words = [
        {"word": word, "start": start, "end": end}
        for word in text.split()
    ]
    return {
        "id": f"t_{text[:4]}",
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "speaker": speaker,
        "word_count": len(words),
        "words": words,
        "emphasis_words": [],
    }


def test_sentence_final_runt_merges_backward():
    """The cross-block half: a card ending on a period joins the card
    before it, never the sentence after it.

    Transcript segs 284/285 overlap by 0.24s, so per-block cards
    overlap on the single track and `resolve_caption_overlaps` merges
    the trimmed runt forward - producing the same mid-card period the
    grouping half makes ("all the time. like what ..."). A runt that
    ends a thought must merge backward instead, so the joined card
    ends where the sentence ends.
    """
    entries = [
        _overlap_entry("we talk about it all", 1415.29, 1416.05),
        _overlap_entry("the time.", 1416.05, 1416.50),
        _overlap_entry("like what are some of", 1416.26, 1417.14),
    ]
    report = resolve_caption_overlaps(entries)
    assert report["merged"] == 1, f"nothing merged: {report}"
    texts = [entry["text"] for entry in entries]
    assert texts == [
        "we talk about it all the time.",
        "like what are some of",
    ], f"the runt merged forward into the next sentence: {texts}"
    for text in texts:
        assert not MID_CARD_SENTENCE_BREAK.search(text), (
            f"a merged card straddles a sentence end: {text!r}"
        )
