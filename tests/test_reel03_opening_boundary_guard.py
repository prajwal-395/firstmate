"""A contiguous opening must not pull the previous sentence into a reel."""

from __future__ import annotations

import pytest

from library.tools import subtitle_coverage
from library.tools.reel_proposal import (
    OPENING_WORD_EDGE_GUARD_SECONDS,
    Approval,
    ReelMoment,
    _opening_word_edge_guard,
    partial_overlaps,
    snap_to_speech,
    validate_proposal,
)

SOURCE = "/media/LCATL0013.MXF"
ITEM = "same-resolve-item"
EDGE = 126.02154166666666
EARLY_PLAYBACK_START = EDGE - 0.062


def _transcript(previous_item=ITEM, current_item=ITEM,
                previous_end=212.9, previous_source_end=EDGE,
                current_source_start=EDGE):
    previous_start = 211.55
    previous_source_start = 124.67154166666667
    current_start = 212.9
    current_end = 218.26
    return {"derived_from": {"duration_seconds": 500.0}, "segments": [
        {
            "speaker": "Craig",
            "text": "So we are hearing about that.",
            "timeline_start": previous_start,
            "timeline_end": previous_end,
            "source_file": SOURCE,
            "resolve_item_id": previous_item,
            "source_start": previous_source_start,
            "source_end": previous_source_end,
            "words": [
                {"word": "about", "start": 212.55, "end": 212.87},
                {"word": "that.", "start": 212.87, "end": 212.9},
            ],
        },
        {
            "speaker": "Craig",
            "text": "If you're attorney,",
            "timeline_start": current_start,
            "timeline_end": current_end,
            "source_file": SOURCE,
            "resolve_item_id": current_item,
            "source_start": current_source_start,
            "source_end": current_source_start + (current_end - current_start),
            "words": [
                {"word": "If", "start": 212.9, "end": 213.28},
                {"word": "you're", "start": 213.28, "end": 213.39},
                {"word": "attorney,", "start": 213.39, "end": 213.82},
            ],
        },
    ]}


def _played(transcript, source_start):
    return subtitle_coverage.played_words_from_transcript(
        transcript["segments"],
        [{"source_file": SOURCE,
          "source_start": source_start,
          "source_end": EDGE + 0.92,
          "reel_start": 0.0}],
    )


def _captioned():
    words = [
        ("if", 0.0, 9 / (24000 / 1001)),
        ("you're", 9 / (24000 / 1001), 12 / (24000 / 1001)),
        ("attorney,", 12 / (24000 / 1001), 22 / (24000 / 1001)),
    ]
    return [
        {"word": word, "norm": subtitle_coverage.normalize_word(word),
         "reel_start": start, "reel_end": end, "card": "opening.mov"}
        for word, start, end in words
    ]


def _cards():
    return [{"card": "opening.mov", "reel_start": 0.0, "reel_end": 0.92,
             "text_norms": {"if", "you're", "attorney"}}]


def test_reel03_opening_snap_keeps_first_word_and_drops_prior_sentence():
    transcript = _transcript()

    start, end = snap_to_speech(213.26, 218.26, transcript)
    assert start == pytest.approx(212.9 + OPENING_WORD_EDGE_GUARD_SECONDS)
    assert end == pytest.approx(218.26)
    assert partial_overlaps(start, end, transcript) == []
    validate_proposal(
        [ReelMoment(number=3, slug="people-stopped-searching",
                    reason="opening boundary regression",
                    timeline_start=start, timeline_end=end,
                    approval=Approval.APPROVED)],
        transcript, 500.0,
    )

    # The staged item's measured source start is 62ms before the shared
    # word edge. Before the guard that plays both previous-row words and
    # fails F25 against the opening card.
    before = _played(transcript, EARLY_PLAYBACK_START)["words"]
    prior_words = [word for word in before if word["word"] in {"about", "that."}]
    assert [word["word"] for word in prior_words] == ["about", "that."]
    assert prior_words[0]["reel_start"] == pytest.approx(0.0)
    assert prior_words[0]["reel_end"] == pytest.approx(0.032)
    assert prior_words[1]["reel_start"] == pytest.approx(0.032)
    assert prior_words[1]["reel_end"] == pytest.approx(0.062)
    before_gate = subtitle_coverage.check_word_coverage(
        before, _captioned(), _cards())
    assert any(finding["kind"] == "word_mismatch"
               and finding["severity"] == "error"
               for finding in before_gate["findings"])

    guarded_source_start = EARLY_PLAYBACK_START + (
        start - 212.9)
    after = _played(transcript, guarded_source_start)["words"]
    assert [word["word"] for word in after] == ["If", "you're", "attorney,"]
    after_gate = subtitle_coverage.check_word_coverage(
        after, _captioned(), _cards())
    assert [finding for finding in after_gate["findings"]
            if finding["severity"] == "error"] == []


@pytest.mark.parametrize("changes", [
    {"previous_end": 212.89},
    {"previous_item": "different-resolve-item"},
    {"current_item": None},
    {"current_source_start": EDGE + 0.001},
])
def test_guard_needs_one_contiguous_same_item_word_edge(changes):
    transcript = _transcript(**changes)
    assert _opening_word_edge_guard(212.9, transcript) is None
    assert snap_to_speech(213.26, 218.26, transcript) == pytest.approx(
        (212.9, 218.26))
