"""The caption reading: acronyms and numerals as declared data.

`library/tools/caption_reading.py` is the one named place the captain's
2026-09-18 ruling lives - SEO, GEO and AI read uppercase, spelled-out
numbers read as digits - and these tests pin that a future scope answer
is a declaration edit: the acronyms convert because they are members of
`CAPTION_ACRONYMS`, and every numeral shape converts through the one
`parse_number` interpreter over the one word tables.
"""

from library.tools.caption_reading import (
    apply_caption_reading,
    apply_caption_reading_text,
)


def _words(*tokens):
    """Word entries with sequential fake timings."""
    return [
        {"word": token, "start": float(i), "end": float(i) + 0.4}
        for i, token in enumerate(tokens)
    ]


def test_acronyms_restore_case_insensitively_and_never_inside_a_word():
    for surface, read in [("seo", "SEO"), ("ai", "AI"), ("ceo", "CEO"),
                          ("cmos", "CMOs"),
                          ("said aim chair again", "said aim chair again")]:
        assert apply_caption_reading_text(surface) == read, surface


def test_quantities_ratings_versions_measures_read_as_digits():
    for surface, read in [
        ("seo two point oh", "SEO 2.0"),
        ("like three point five stars and", "like 3.5 stars and"),
        ("and that's only twenty percent.", "and that's only 20 percent."),
        ("five-star reviews, ai does see that.",
         "5-star reviews, AI does see that."),
        ("their press release from twenty twenty",
         "their press release from 2020"),
        ("maybe you're a hundred person shop,",
         "maybe you're a 100 person shop,"),
    ]:
        assert apply_caption_reading_text(surface) == read, surface


def test_reading_is_idempotent():
    for surface in [
        "seo two point oh",
        "five-star reviews, ai does see that.",
        "their press release from twenty twenty",
        "it really likes, especially youtube.",
    ]:
        once = apply_caption_reading_text(surface)
        assert apply_caption_reading_text(once) == once


def test_every_emitted_word_carries_a_span_and_output_never_grows():
    out = apply_caption_reading(_words("the", "link's", "in", "our", "bio."))
    assert len(out) == 5
    for entry in out:
        assert entry["start"] is not None
        assert entry["end"] is not None
    entries = _words("seo", "two", "point", "oh", "and", "geo")
    assert len(apply_caption_reading(entries)) <= len(entries)


def _corrections(*pairs):
    """Recorded spellings in the store's shape: heard -> correct."""
    return [
        {"id": f"lc-test-{i:02d}", "heard": heard, "correct": correct}
        for i, (heard, correct) in enumerate(pairs)
    ]


def test_recorded_spellings_are_read_as_data():
    """2026-09-19: the planner enforced the correction store after the
    reading ran, so every reel carrying "Google" refused its build on
    correct output. The reading takes the same store as data - never a
    second list. Longest phrase wins; a correction outranks the acronym
    rule ("aics" would read "AICs"); a mixed-case brand the captain did
    not enumerate (Reel 16) restores and composes with the numeral rule.
    """
    cases = [
        (_corrections(("google", "Google")),
         "we ran it on google search", "we ran it on Google search"),
        (_corrections(("atlanta", "Atlanta"),
                      ("atlanta journal constitution",
                       "Atlanta Journal Constitution")),
         "we're in atlanta and the atlanta journal constitution",
         "we're in Atlanta and the Atlanta Journal Constitution"),
        (_corrections(("AICs", "AI sees")),
         "the aics saw it", "the AI sees saw it"),
        (_corrections(("chatgpt", "ChatGPT"), ("chat gpt", "ChatGPT")),
         "chatgpt, it gave three", "ChatGPT, it gave 3"),
        (_corrections(("chatgpt", "ChatGPT"), ("chat gpt", "ChatGPT")),
         "they get on chatgpt", "they get on ChatGPT"),
    ]
    for corrections, surface, read in cases:
        assert apply_caption_reading_text(
            surface, corrections=corrections) == read, surface


def test_multiword_correct_merges_with_span():
    corrections = _corrections(("AICs", "AI sees"))
    out = apply_caption_reading(
        _words("the", "aics", "saw", "it"), corrections=corrections)
    assert [(entry["word"], entry["start"], entry["end"])
            for entry in out] == [
        ("the", 0.0, 0.4),
        ("AI sees", 1.0, 1.4),
        ("saw", 2.0, 2.4),
        ("it", 3.0, 3.4),
    ]
