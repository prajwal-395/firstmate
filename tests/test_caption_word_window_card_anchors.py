"""A card's reading-speed hold must not strand its first spoken word.

The reel conformance verifier found captions that began after a card's
first word had already ended.  The rendered props then carry a reversed
highlight window, and a final short group can fall below F7's 12-frame
floor.  These frozen spine fixtures exercise that planning path without
Resolve or a real project.
"""

from library.steps.step_4_01_plan_subtitles.step import (
    MIN_CAPTION_FLASH_SECONDS,
    generate_subtitles,
)


def _plan(words, text, duration):
    spine = {
        "structure": [{
            "block_type": "speech",
            "position": 1,
            "timeline_start": 0.0,
            "timeline_end": duration,
            "source_start": 0.0,
            "source_end": duration,
            "clip_id": "clip_001",
            "alignment_method": "whisperx",
            "word_timestamps": [
                {"word": word, "source_start": start, "source_end": end}
                for word, start, end in words
            ],
            "content": {"text": text},
            "speaker": "Craig",
        }],
    }
    return generate_subtitles(
        spine, caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def _assert_each_word_has_time_inside_its_card(entries):
    for entry in entries:
        for word in entry["words"]:
            visible_start = max(entry["timeline_start"], word["start"])
            visible_end = min(entry["timeline_end"], word["end"])
            assert visible_start < visible_end, (
                f"{word['word']!r} at {word['start']:.3f}-"
                f"{word['end']:.3f}s is outside card "
                f"{entry['timeline_start']:.3f}-"
                f"{entry['timeline_end']:.3f}s ({entry['text']!r})"
            )


def test_readability_hold_does_not_start_the_reel1_to_after_its_word():
    """The old cursor delay put `to` in a later card after it was spoken."""
    words = [
        ("it's", 0.00, 0.16),
        ("something", 0.16, 0.46),
        ("that's", 0.46, 0.63),
        ("going", 0.63, 0.765),
        ("to", 0.765, 0.865),
        ("continually", 0.865, 1.55),
        ("eat", 1.55, 1.71),
        ("into", 1.71, 1.89),
        ("their", 1.89, 2.11),
        ("budget.", 2.11, 2.74),
    ]

    entries = _plan(
        words,
        "it's something that's going to continually eat into their budget.",
        2.74,
    )

    _assert_each_word_has_time_inside_its_card(entries)
    assert sum(
        word["word"] == "to"
        for entry in entries for word in entry["words"]
    ) == 1


def test_late_reel17_tail_does_not_make_an_f7_short_card():
    """The same late-card schedule made the final 9-frame card."""
    words = [
        ("and", 0.00, 0.15),
        ("that", 0.15, 0.23),
        ("would", 0.23, 0.32),
        ("be", 0.32, 0.42),
        ("the", 0.42, 0.51),
        ("first", 0.51, 0.81),
        ("step.", 0.81, 1.10),
    ]

    entries = _plan(
        words, "and that would be the first step.", 1.10,
    )

    _assert_each_word_has_time_inside_its_card(entries)
    short_cards = [
        (entry["text"], entry["timeline_end"] - entry["timeline_start"])
        for entry in entries
        if entry["timeline_end"] - entry["timeline_start"]
        < MIN_CAPTION_FLASH_SECONDS
    ]
    assert not short_cards, f"cards below F7's duration floor: {short_cards}"
