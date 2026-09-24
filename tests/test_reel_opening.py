"""The opening of a reel, measured - and the four it found.

The handoff for step 3.4 has always stated the hook rule: "The first line
has to earn the next five seconds: a question, a claim, or a provocation.
Not throat-clearing, not a speaker settling into a sentence." Nothing
measured whether a proposal obeyed it, and four of the nineteen reels the
captain approved on 2026-09-05 opened on exactly what it forbids.

These tests pin what is reported, what is deliberately NOT reported, and
that a good opening produces nothing - the last of which is what stops
this becoming another gate that fires on correct output.
"""

from __future__ import annotations

import pytest

from library.tools.reel_opening import (
    OPENING_SECONDS,
    concern_lines,
    observations,
    opening_words,
)


def _words(text: str, speaker: str = "Akshita") -> list:
    return [{"word": w, "speaker": speaker} for w in text.split()]


# ── What it reports ──────────────────────────────────────────────────

def test_a_back_reference_is_reported():
    """Reel 04 opened on the word 'earlier'."""
    observed = observations(
        _words("earlier it doesn't even matter if you're a 15 man shop"))

    assert len(observed) == 1
    assert observed[0]["observation"] == "back_reference"
    assert observed[0]["matched"] == "earlier"
    assert "outside this reel" in observed[0]["why"]
    assert observed[0]["the_fix"]


def test_an_answer_with_no_question_before_it_is_reported():
    """Reel 07 opened on 'Absolutely', answering nothing the viewer heard."""
    observed = observations(
        _words("Absolutely. And just like a hiring manager would check both"),
        span_text="Absolutely. And just like a hiring manager would check "
                  "both. So your website is your resume.")

    assert [o["observation"] for o in observed] == ["answer_with_no_question"]
    assert observed[0]["matched"] == "Absolutely"


# ── What it does NOT report, which is the harder half ────────────────

def test_an_answer_that_really_follows_a_question_is_left_alone():
    """The check must be able to pass, or it is not a check.

    An acknowledgement is only a defect when the viewer never heard what
    it acknowledges. Inside a reel that opens on Craig's question it is
    ordinary conversation.
    """
    assert observations(
        _words("Absolutely. So they're completely different systems"),
        span_text="So what do marketing directors get wrong about geo? "
                  "Absolutely. So they're completely different systems.",
    ) == []


def test_a_question_opening_produces_nothing():
    assert observations(_words("Why do AI platforms love video content?")) == []


def test_throat_clearing_is_deliberately_not_matched():
    """Reel 01 opens "okay so i'm hearing just from a lot of different
    marketing directors", and this module does NOT report it.

    That is a judgement about writing, not a fact about the span, and the
    captain's ruling is that taste belongs to the model and no threshold
    may be invented for it. Reporting it would make this module the
    chooser. It is named here so the omission reads as a decision rather
    than as a miss.
    """
    assert observations(
        _words("okay so i'm hearing just from a lot of different "
               "marketing directors")) == []


# ── The words it measures ────────────────────────────────────────────

def _transcript(segments):
    return {"segments": segments}


def _seg(speaker, text, start, step=0.3):
    words = [{"word": w, "start": start + i * step,
              "end": start + (i + 1) * step, "timed": True}
             for i, w in enumerate(text.split())]
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": start + len(text.split()) * step,
            "source_file": "/m/a.MXF", "words": words}


def test_opening_words_are_read_through_the_keep_ranges():
    """A take cut out of the opening takes its words with it."""
    tx = _transcript([_seg("Akshita", "this whole take was flubbed", 0.0),
                      _seg("Akshita", "Yeah so we ran an audit", 10.0)])

    # Only the second range plays.
    got = opening_words([(10.0, 20.0)], tx)

    assert [w["word"] for w in got][:3] == ["Yeah", "so", "we"]
    assert "flubbed" not in [w["word"] for w in got]


def test_untimed_words_never_reach_the_opening():
    """A segment carrying text with no word timings is an artefact.

    Reel 03 carries three of them - 40 words of text inside 1.06s, up to
    80 words a second. Placing those in an opening would report a hook
    nobody spoke.
    """
    tx = _transcript([
        {"speaker": "Akshita", "text": "a whole sentence with no timings",
         "timeline_start": 0.0, "timeline_end": 0.2,
         "source_file": "/m/a.MXF",
         "words": [{"word": "a", "start": 0.0, "end": 0.1, "timed": False}]},
        _seg("Akshita", "Why do AI platforms love video", 1.0),
    ])

    got = opening_words([(0.0, 30.0)], tx)

    assert [w["word"] for w in got][0] == "Why"


# ── How it reaches a reader ──────────────────────────────────────────


def test_nothing_here_scores_or_rejects():
    """The captain's ruling: taste is the model's and no threshold may be
    invented for it. This module returns observations or nothing."""
    observed = observations(_words("earlier it doesn't even matter"))

    assert set(observed[0]) == {"observation", "opening", "matched",
                                "why", "the_fix"}
    for key in ("score", "severity", "rejected", "verdict", "pass"):
        assert key not in observed[0]


@pytest.mark.parametrize("opener", ["Yes"])
def test_every_answer_shape_is_matched_at_the_start_only(opener):
    assert observations(_words(f"{opener} and here is why"))
    assert observations(_words(f"the answer is {opener.lower()} and here is why")) == []
