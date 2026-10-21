"""The caption rewrite fits new words onto a card's measured windows.

A caption fix changes what a rendered card SAYS. The card's timing is
measured, not invented, so the fit is the whole contract: a word that
survives keeps the window it was measured with, a replaced run shares
the span it replaces, and a text that cannot be timed inside the card's
own span refuses instead of being drawn.

These tests pin that fit, and the refusals that keep a fix from
quietly becoming a re-timing.
"""
from __future__ import annotations

import pytest

from library.tools.caption_rewrite import (
    CaptionRewriteError,
    caption_fix_operation,
    fit_words,
    rewrite_props,
)


def _words(*pairs):
    """The card's measured windows: `(word, start, end)` each."""
    return [{"word": word, "startFrame": start, "endFrame": end}
            for word, start, end in pairs]


def _props(text="the wrong number", words=None, start=0, end=48):
    """One caption card's render props."""
    return {
        "subtitles": [{
            "text": text,
            "startFrame": start,
            "endFrame": end,
            "emphasisWords": ["number"],
            "words": words if words is not None else _words(
                ("the", 0, 8), ("wrong", 8, 24), ("number", 24, 48)),
            "fitScale": 1.0,
        }],
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 60,
        "style": {"fontSize": 64},
    }


def test_a_surviving_word_keeps_the_window_it_was_measured_with():
    """Defect: a fix that re-timed every word moved the captions off
    the speech they cover."""
    fitted = fit_words(
        _words(("the", 0, 8), ("wrong", 8, 24), ("number", 24, 48)),
        ["the", "right", "number"], span=(0, 48))

    assert [(row["word"], row["startFrame"], row["endFrame"])
            for row in fitted] == [
        ("the", 0, 8), ("right", 8, 24), ("number", 24, 48)]


def test_a_replaced_run_shares_the_span_it_replaces():
    """The new words sweep where the old ones did - the card's own
    measured span, split between them."""
    fitted = fit_words(
        _words(("the", 0, 8), ("wrong", 8, 24), ("number", 24, 48)),
        ["the", "right", "figure", "number"], span=(0, 48))

    assert [(row["word"], row["startFrame"], row["endFrame"])
            for row in fitted] == [
        ("the", 0, 8), ("right", 8, 16), ("figure", 16, 24),
        ("number", 24, 48)]


def test_an_inserted_run_takes_the_gap_between_its_neighbours():
    fitted = fit_words(
        _words(("the", 0, 8), ("wrong", 12, 24), ("number", 24, 48)),
        ["the", "very", "wrong", "number"], span=(0, 48))

    assert [(row["word"], row["startFrame"], row["endFrame"])
            for row in fitted] == [
        ("the", 0, 8), ("very", 8, 12), ("wrong", 12, 24),
        ("number", 24, 48)]


def test_an_inserted_word_with_no_gap_to_take_refuses():
    """Defect: a word given a window nobody measured would highlight
    over words the speech does not cover."""
    with pytest.raises(CaptionRewriteError, match="do not fit"):
        fit_words(
            _words(("the", 0, 8), ("wrong", 8, 24), ("number", 24, 48)),
            ["the", "very", "wrong", "number"], span=(0, 48))


def test_a_text_that_cannot_be_timed_inside_the_card_refuses():
    """Defect: a caption drawn with words no sweep can cross says
    something its timing does not carry."""
    with pytest.raises(CaptionRewriteError, match="do not fit"):
        fit_words(_words(("the", 0, 2)), ["one", "two", "three", "four",
                                          "five"], span=(0, 2))


def test_a_card_with_no_measured_words_refuses_the_fit():
    with pytest.raises(CaptionRewriteError, match="no measured word windows"):
        fit_words([], ["one"], span=(0, 8))


def test_the_rewrite_keeps_the_cards_own_timing():
    """The fix changes what the card says, never when it says it."""
    props = _props()
    rewritten = rewrite_props(props, "the right number")

    cue = rewritten["subtitles"][0]
    assert cue["text"] == "the right number"
    assert (cue["startFrame"], cue["endFrame"]) == (0, 48)
    assert [(row["word"], row["startFrame"], row["endFrame"])
            for row in cue["words"]] == [
        ("the", 0, 8), ("right", 8, 24), ("number", 24, 48)]
    # The card's own bounds, style and frame survive the rewrite.
    assert rewritten["style"] == props["style"]
    assert rewritten["width"] == props["width"]
    assert rewritten["height"] == props["height"]
    assert rewritten["durationInFrames"] == props["durationInFrames"]
    # The props on disk are never edited.
    assert props["subtitles"][0]["text"] == "the wrong number"


def test_the_rewrite_re_derives_the_emphasis_words():
    """An emphasis word the new text does not contain scales nothing."""
    rewritten = rewrite_props(_props(), "the right figure")
    assert "number" not in rewritten["subtitles"][0]["emphasisWords"]


def test_a_rewrite_of_the_same_text_refuses():
    with pytest.raises(CaptionRewriteError, match="already reads"):
        rewrite_props(_props(), "the wrong number")


def test_a_card_drawing_several_cues_refuses_the_fix():
    """Defect: a fix that guessed which cue was wrong would rewrite a
    caption nobody named."""
    props = _props()
    props["subtitles"].append(dict(props["subtitles"][0]))

    with pytest.raises(CaptionRewriteError, match="holds 2 caption cues"):
        rewrite_props(props, "the right number")


def test_the_operation_carries_the_text_it_applies():
    operation = caption_fix_operation("clip-17", "the right number")

    assert operation == {"op": "caption_fix", "unique_id": "clip-17",
                         "text": "the right number"}


def test_an_operation_without_a_card_or_a_text_refuses():
    with pytest.raises(ValueError, match="names no unique_id"):
        caption_fix_operation("", "the right number")
    with pytest.raises(ValueError, match="needs the card's new text"):
        caption_fix_operation("clip-17", "   ")
