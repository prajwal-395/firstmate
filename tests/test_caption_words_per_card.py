"""Rung 7 (finding 31, C3.2/MG3.1): caption words-per-card is a plan value.

"Captions 2 words max" had no route: `split_into_groups` grouped at a
literal `max_words=6` nothing could set. The ceiling now rides the
brand template's `effect.caption_words_per_card` (None/absent is the
default 6 - the feel-word case, E3), is refused when it is not a
whole number >= 1, and is receipted on the plan as
`subtitle_plan.max_words`.
"""
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_01_plan_subtitles.step import (
    generate_subtitles,
    resolve_max_words_per_card,
    split_into_groups,
)


def _words(n):
    return [{"word": f"w{i}", "start": float(i), "end": float(i) + 0.5}
            for i in range(n)]


def _fits_all(text):
    return True


def test_a_stated_number_is_read():
    assert resolve_max_words_per_card(
        brand_effect={"caption_words_per_card": 2}) == 2


def test_request_word_limit_overrides_the_house_default_and_reaches_grouping():
    """Finding 31: the request's count must control actual card grouping."""
    max_words = resolve_max_words_per_card(
        {"caption_words_per_card": 6}, audio_spine={"max_words": 2})
    groups = split_into_groups(_words(6), fits_fn=_fits_all,
                               max_words=max_words)
    assert [group["word_count"] for group in groups] == [2, 2, 2]


def test_non_integer_request_word_limit_is_refused():
    """A malformed plan number must not silently fall back to the brand."""
    import pytest

    with pytest.raises(ValueError, match="audio_spine.max_words"):
        resolve_max_words_per_card(audio_spine={"max_words": 2.5})


def test_subtitle_plan_records_the_request_limit_used_by_card_grouping(
        monkeypatch):
    """Finding 31: plan_subtitles.max_words must match every grouped card."""
    from library.steps.step_4_01_plan_subtitles import step as subtitles

    class _FitsAll:
        measured = True
        usable_width = 1000

        @staticmethod
        def fits_in_box(_text):
            return True

        @staticmethod
        def fit_scale(_text, _emphasis_words=None):
            return 1.0

    monkeypatch.setattr(subtitles, "resolve_subtitle_style",
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(subtitles, "resolve_safe_area",
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(subtitles, "build_caption_fitter",
                        lambda *_args, **_kwargs: _FitsAll())
    words = [
        {"word": f"word{i}", "source_start": i * 0.3,
         "source_end": i * 0.3 + 0.2}
        for i in range(4)
    ]
    result = generate_subtitles({
        "max_words": 2,
        "structure": [{
            "position": 1,
            "block_type": "speech",
            "content": {"text": "word0 word1 word2 word3"},
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "source_start": 0.0,
            "source_end": 2.0,
            "word_timestamps": words,
        }],
    }, brand_effect={"caption_words_per_card": 6}, brand_style={})

    plan = result["subtitle_plan"]
    assert plan["max_words"] == 2
    assert [entry["word_count"] for entry in plan["subtitle_entries"]] == [
        2, 2]


def test_two_words_max_groups_pairs():
    """C3.2's shape: no card carries more than the stated 2."""
    groups = split_into_groups(_words(6), fits_fn=_fits_all, max_words=2)
    assert [g["word_count"] for g in groups] == [2, 2, 2]
