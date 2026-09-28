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
        spine, caption_case="as_written", brand_effect={}, brand_style={}
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


def test_unreadable_block_tail_is_reported_by_card_id_without_losing_word():
    spine = _speech_spine([
        {"word": "2026.", "source_start": 0.0, "source_end": 0.212},
    ], source_end=0.212, timeline_end=0.212)

    plan = generate_subtitles(
        spine, caption_case="as_written", brand_effect={}, brand_style={}
    )["subtitle_plan"]
    entry = plan["subtitle_entries"][0]

    assert entry["text"] == "2026."
    assert [word["word"] for word in entry["words"]] == ["2026."]
    assert plan["readability_issues"] == [{
        "card_id": entry["id"],
        "spine_block_position": 7,
        "text": "2026.",
        "duration_seconds": 0.212,
        "characters_per_second": 23.58,
        "reasons": ["duration_below_0.5_seconds"],
    }]


def test_a_word_starting_at_the_exclusive_source_end_is_not_drawn():
    # Reel 03's excluded word begins 8 microseconds after its staged source
    # end. Rounding its reel time to milliseconds still lands at the edge.
    spine = _speech_spine([
        {"word": "typing", "source_start": 0.7, "source_end": 0.9},
        {"word": "best", "source_start": 1.0000083, "source_end": 1.2},
    ], source_end=1.0, timeline_end=1.0)

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
