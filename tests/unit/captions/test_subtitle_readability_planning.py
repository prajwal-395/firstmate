import pytest

from library.steps.step_4_01_plan_subtitles.step import generate_subtitles


def _speech_spine(words, *, source_end, timeline_end):
    return {
        "structure": [{
            "position": 7,
            "block_type": "speech",
            "timeline_start": 0.0,
            "timeline_end": timeline_end,
            "source_start": 0.0,
            "source_end": source_end,
            "content": {"text": " ".join(word["word"] for word in words)},
            "word_timestamps": words,
        }],
    }


def test_plan_extends_a_fast_card_without_changing_its_words():
    spine = _speech_spine([
        {"word": "this", "source_start": 0.0, "source_end": 0.1},
        {"word": "needs", "source_start": 0.12, "source_end": 0.25},
        {"word": "time.", "source_start": 0.3, "source_end": 0.5},
    ], source_end=0.5, timeline_end=2.0)

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={},
        fps=24000 / 1001,
    )["subtitle_plan"]
    entries = plan["subtitle_entries"]

    assert len(entries) == 1
    entry = entries[0]
    duration = entry["timeline_end"] - entry["timeline_start"]
    assert entry["text"] == "this needs time."
    assert [word["word"] for word in entry["words"]] == [
        "this", "needs", "time."
    ]
    assert duration >= 0.5
    assert len(entry["text"]) / duration <= 25
    assert plan["readability_issues"] == []


def test_reel08_short_block_tail_uses_the_safe_gap_before_the_next_block():
    # Frozen from Reel 08's current staging plan. The 0.38s block contains
    # only "mm-hmm."; block clamping used to undo its 0.7s minimum hold.
    spine = {"structure": [
        {
            "position": 5,
            "block_type": "speech",
            "timeline_start": 26.03,
            "timeline_end": 26.41,
            "source_start": 1285.0636666666664,
            "source_end": 1285.4436666666666,
            "speaker": "Akshita",
            "content": {"text": "mm-hmm."},
            "word_timestamps": [{
                "word": "mm-hmm.",
                "source_start": 1285.0636666666664,
                "source_end": 1285.4436666666666,
            }],
        },
        {
            "position": 6,
            "block_type": "speech",
            "timeline_start": 26.697,
            "timeline_end": 28.307,
            "source_start": 1297.023,
            "source_end": 1298.633,
            "speaker": "Akshita",
            "content": {"text": "ranking tells Google that you exist."},
            "word_timestamps": [
                {"word": "ranking", "source_start": 1297.023,
                 "source_end": 1297.273},
                {"word": "tells", "source_start": 1297.273,
                 "source_end": 1297.523},
                {"word": "Google", "source_start": 1297.523,
                 "source_end": 1297.923},
                {"word": "that", "source_start": 1297.923,
                 "source_end": 1298.103},
                {"word": "you", "source_start": 1298.103,
                 "source_end": 1298.303},
                {"word": "exist.", "source_start": 1298.303,
                 "source_end": 1298.633},
            ],
        },
    ]}

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={},
        fps=24000 / 1001,
    )["subtitle_plan"]
    entries = plan["subtitle_entries"]
    short = next(entry for entry in entries if entry["text"] == "mm-hmm.")

    assert short["timeline_start"] == 26.03
    short_frames = round((short["timeline_end"] - short["timeline_start"])
                         * (24000 / 1001))
    assert short_frames >= 12
    assert short["timeline_end"] < 26.697
    assert [word["word"] for word in short["words"]] == ["mm-hmm."]
    assert all(
        round((entry["timeline_end"] - entry["timeline_start"])
              * (24000 / 1001)) >= 12
        for entry in entries)
    assert plan["readability_issues"] == []


def test_unfixable_sub_floor_block_refuses_instead_of_emitting_f7_card():
    spine = _speech_spine([
        {"word": "2026.", "source_start": 0.0, "source_end": 0.212},
    ], source_end=0.212, timeline_end=0.212)

    with pytest.raises(ValueError, match="under the 0.500s readability floor"):
        generate_subtitles(
            spine, caption_case="as_written", brand_effect={}, brand_style={}
        )


def test_duration_floor_uses_frame_ceil_for_25fps_cards():
    spine = {"structure": [
        {
            "position": 1,
            "block_type": "speech",
            "timeline_start": 0.01,
            "timeline_end": 0.51,
            "source_start": 0.0,
            "source_end": 0.5,
            "content": {"text": "short"},
            "word_timestamps": [
                {"word": "short", "source_start": 0.01,
                 "source_end": 0.3},
            ],
        },
        {
            "position": 2,
            "block_type": "speech",
            "timeline_start": 0.8,
            "timeline_end": 1.4,
            "source_start": 0.8,
            "source_end": 1.4,
            "content": {"text": "next sentence."},
            "word_timestamps": [
                {"word": "next", "source_start": 0.8,
                 "source_end": 0.95},
                {"word": "sentence.", "source_start": 0.95,
                 "source_end": 1.2},
            ],
        },
    ]}

    entries = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={},
        fps=25,
    )["subtitle_plan"]["subtitle_entries"]
    short = next(entry for entry in entries if entry["text"] == "short")

    assert round((short["timeline_end"] - short["timeline_start"]) * 25) >= 13
    assert short["timeline_end"] < 0.8


def test_a_word_starting_at_the_exclusive_source_end_is_not_drawn():
    # Reel 03's excluded word begins 8 microseconds after its staged source
    # end. Rounding its reel time to milliseconds still lands at the edge.
    spine = _speech_spine([
        {"word": "typing", "source_start": 0.7, "source_end": 0.9},
        {"word": "best", "source_start": 1.0000083, "source_end": 1.2},
    ], source_end=1.0, timeline_end=1.3)

    entries = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={}
    )["subtitle_plan"]["subtitle_entries"]

    assert [word["word"] for entry in entries for word in entry["words"]] == [
        "typing"]
    assert all(entry["text"] == "typing" for entry in entries)


def test_short_tail_can_merge_across_long_pause_to_keep_date_readable():
    spine = _speech_spine([
        {"word": "today", "source_start": 0.0, "source_end": 0.28},
        {"word": "is", "source_start": 0.30, "source_end": 0.34},
        {"word": "march", "source_start": 0.68, "source_end": 1.24},
        {"word": "25th,", "source_start": 1.28, "source_end": 1.60},
        {"word": "2026.", "source_start": 2.769, "source_end": 2.981},
    ], source_end=2.981, timeline_end=2.981)

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={}
    )["subtitle_plan"]
    entries = plan["subtitle_entries"]

    assert any("25th, 2026." in entry["text"] for entry in entries)
    assert [word["word"] for entry in entries for word in entry["words"]] == [
        "today", "is", "march", "25th,", "2026."
    ]
    assert plan["readability_issues"] == []


def test_unfixably_fast_single_word_is_reported_by_card_id():
    spine = _speech_spine([
        {"word": "unreadablefastcaption", "source_start": 0.0,
         "source_end": 0.8},
    ], source_end=0.8, timeline_end=0.8)

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={}
    )["subtitle_plan"]
    entry = plan["subtitle_entries"][0]

    assert entry["text"] == "unreadablefastcaption"
    assert plan["readability_issues"] == [{
        "card_id": entry["id"],
        "spine_block_position": 7,
        "text": "unreadablefastcaption",
        "duration_seconds": 0.8,
        "characters_per_second": 26.25,
        "reasons": [
            "reading_speed_over_25_characters_per_second",
        ],
    }]
