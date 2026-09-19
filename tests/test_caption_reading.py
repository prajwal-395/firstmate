"""The caption reading: acronyms and numerals as declared data.

`library/tools/caption_reading.py` is the one named place the captain's
2026-09-18 ruling lives - SEO, GEO and AI read uppercase, spelled-out
numbers read as digits - and these tests pin that a future scope answer
is a declaration edit: the acronyms convert because they are members of
`CAPTION_ACRONYMS`, and every numeral shape converts through the one
`parse_number` interpreter over the one word tables.
"""

import sys

import pytest

sys.path.insert(0, ".")
from library.tools.caption_reading import (
    CAPTION_ACRONYMS,
    apply_caption_reading,
    apply_caption_reading_text,
)


def _words(*tokens):
    """Word entries with sequential fake timings."""
    return [
        {"word": token, "start": float(i), "end": float(i) + 0.4}
        for i, token in enumerate(tokens)
    ]


class TestAcronymDeclaration:
    def test_named_acronyms_are_declared(self):
        # 2026-09-18: SEO, GEO, AI. 2026-09-19: the captain's Reel 01
        # (CEO), Reel 07 (CMO) and Reel 03 (CRM) notes join them - the
        # same sanctioned path, a scope answer as a declaration edit.
        assert set(CAPTION_ACRONYMS) == {"SEO", "GEO", "AI", "CEO", "CMO",
                                         "CRM"}

    @pytest.mark.parametrize(
        "surface,read",
        [
            ("seo", "SEO"),
            ("SEO", "SEO"),
            ("Seo", "SEO"),
            ("geo", "GEO"),
            ("ai", "AI"),
            ("AI", "AI"),
            ("ceo", "CEO"),
            ("cmo", "CMO"),
            ("crm", "CRM"),
            ("cmos", "CMOs"),
        ],
    )
    def test_acronyms_restore_case_insensitively(self, surface, read):
        assert apply_caption_reading_text(surface) == read

    def test_possessive_keeps_its_clitic(self):
        assert apply_caption_reading_text("ai's") == "AI's"

    def test_chatgpt_is_not_a_member_so_it_stays(self):
        assert (
            apply_caption_reading_text("chatgpt, it gave three") == "chatgpt, it gave 3"
        )

    def test_substrings_do_not_match(self):
        assert (
            apply_caption_reading_text("said aim chair again") == "said aim chair again"
        )


class TestNumeralReading:
    @pytest.mark.parametrize(
        "surface,read",
        [
            ("seo two point oh", "SEO 2.0"),
            ("like three point five stars and", "like 3.5 stars and"),
            ("two point zero", "2.0"),
            ("and that's only twenty percent.", "and that's only 20 percent."),
            ("nine times more than you", "9 times more than you"),
            ("fifty times their size.", "50 times their size."),
            ("like five star reviews", "like 5 star reviews"),
            (
                "five-star reviews, ai does see that.",
                "5-star reviews, AI does see that.",
            ),
            ("their press release from twenty twenty", "their press release from 2020"),
            ("seven different modules and help", "7 different modules and help"),
            ("maybe you're a hundred person shop,", "maybe you're a 100 person shop,"),
            ("negative, or even zero, it", "negative, or even 0, it"),
            ("contradicting each other, two", "contradicting each other, 2"),
            ("literally a five year old was", "literally a 5 year old was"),
        ],
    )
    def test_quantities_ratings_versions_measures_read_as_digits(self, surface, read):
        assert apply_caption_reading_text(surface) == read

    @pytest.mark.parametrize(
        "surface",
        [
            "one of the best ways",
            "chose the first one i saw.",
            "picking that brand over another one.",
            "it can see one category",
            "each other, see which one is",
            "one is about position, um, where",
            # Rank labels are undecided and stay words.
            "number one, number two, number three.",
            "the top three searches, but on",
            "so being number one",
            # Ordinals are never cardinals.
            "so, one of the first steps",
            # The house style survives everywhere else.
            "it really likes, especially youtube.",
        ],
    )
    def test_pronouns_ranks_and_style_stay_words(self, surface):
        assert apply_caption_reading_text(surface) == surface

    def test_reading_is_idempotent(self):
        for surface in [
            "seo two point oh",
            "five-star reviews, ai does see that.",
            "their press release from twenty twenty",
            "it really likes, especially youtube.",
        ]:
            once = apply_caption_reading_text(surface)
            assert apply_caption_reading_text(once) == once


class TestTimingPreservation:
    def test_merged_numeral_spans_its_words(self):
        out = apply_caption_reading(_words("seo", "two", "point", "oh"))
        assert [(entry["word"], entry["start"], entry["end"]) for entry in out] == [
            ("SEO", 0.0, 0.4),
            ("2.0", 1.0, 3.4),
        ]

    def test_every_emitted_word_carries_a_span(self):
        out = apply_caption_reading(_words("the", "link's", "in", "our", "bio."))
        assert len(out) == 5
        for entry in out:
            assert entry["start"] is not None
            assert entry["end"] is not None

    def test_extra_keys_ride_along(self):
        entries = _words("geo")
        entries[0]["score"] = 0.9
        out = apply_caption_reading(entries)
        assert out[0] == {"word": "GEO", "start": 0.0, "end": 0.4, "score": 0.9}

    def test_output_never_grows(self):
        entries = _words("seo", "two", "point", "oh", "and", "geo")
        assert len(apply_caption_reading(entries)) <= len(entries)

    def test_empty_in_empty_out(self):
        assert apply_caption_reading([]) == []


def _corrections(*pairs):
    """Recorded spellings in the store's shape: heard -> correct."""
    return [
        {"id": f"lc-test-{i:02d}", "heard": heard, "correct": correct}
        for i, (heard, correct) in enumerate(pairs)
    ]


class TestRecordedSpellings:
    """2026-09-19: the planner enforced the correction store after the
    reading ran, so every reel carrying "Google" refused its build on
    correct output. The reading takes the same store as data - never a
    second list - and the plan asserts with it.
    """

    def test_reel_04_failing_card_reads(self):
        corrections = _corrections(("google", "Google"))
        assert (
            apply_caption_reading_text(
                "we ran it on Google search".lower(),
                corrections=corrections,
            )
            == "we ran it on Google search"
        )

    def test_recorded_spelling_is_idempotent(self):
        corrections = _corrections(("google", "Google"))
        once = apply_caption_reading_text("we ran it on google search",
                                          corrections=corrections)
        assert once == "we ran it on Google search"
        assert apply_caption_reading_text(once,
                                          corrections=corrections) == once

    def test_longest_phrase_wins(self):
        corrections = _corrections(
            ("atlanta", "Atlanta"),
            ("atlanta journal constitution", "Atlanta Journal Constitution"),
        )
        assert (
            apply_caption_reading_text(
                "we're in atlanta and the atlanta journal constitution",
                corrections=corrections,
            )
            == "we're in Atlanta and the Atlanta Journal Constitution"
        )

    def test_multiword_correct_merges_with_span(self):
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

    def test_merge_spans_first_to_last(self):
        corrections = _corrections(("Jim and i", "Gemini"))
        out = apply_caption_reading(_words("jim", "and", "i", "met"),
                                    corrections=corrections)
        assert [(entry["word"], entry["start"], entry["end"])
                for entry in out] == [
            ("Gemini", 0.0, 2.4),
            ("met", 3.0, 3.4),
        ]

    def test_possessive_survives_a_single_word_respell(self):
        corrections = _corrections(("google", "Google"))
        assert (
            apply_caption_reading_text("google's own engine",
                                       corrections=corrections)
            == "Google's own engine"
        )

    def test_correction_outranks_the_acronym_rule(self):
        # Without the store "aics" reads "AICs" (acronym plural); the
        # captain's spelling says "AI sees" and wins.
        corrections = _corrections(("AICs", "AI sees"))
        assert (
            apply_caption_reading_text("the aics saw it",
                                       corrections=corrections)
            == "the AI sees saw it"
        )

    def test_absent_store_reads_as_before(self):
        assert apply_caption_reading_text("we ran it on google search") == (
            "we ran it on google search"
        )
        assert (
            apply_caption_reading_text("chatgpt, it gave three")
            == "chatgpt, it gave 3"
        )

    def test_mixed_case_brand_restores_through_the_store(self):
        # 2026-09-19: the captain's Reel 16 proper-noun instruction
        # applied to the brand he did not enumerate. "chatgpt" is
        # mixed-case, never an acronym-table entry: the recorded
        # spelling restores it after the house lowercasing, composing
        # with the numeral rule on the same card.
        corrections = _corrections(("chatgpt", "ChatGPT"),
                                   ("chat gpt", "ChatGPT"))
        assert (
            apply_caption_reading_text("chatgpt, it gave three",
                                       corrections=corrections)
            == "ChatGPT, it gave 3"
        )
        assert (
            apply_caption_reading_text("they get on chatgpt",
                                       corrections=corrections)
            == "they get on ChatGPT"
        )

    def test_split_brand_hearing_merges_to_one_token(self):
        corrections = _corrections(("chatgpt", "ChatGPT"),
                                   ("chat gpt", "ChatGPT"))
        out = apply_caption_reading(
            _words("they're", "going", "to", "chat", "gpt"),
            corrections=corrections)
        assert [(entry["word"], entry["start"], entry["end"])
                for entry in out] == [
            ("they're", 0.0, 0.4),
            ("going", 1.0, 1.4),
            ("to", 2.0, 2.4),
            ("ChatGPT", 3.0, 4.4),
        ]

    def test_mixed_case_brand_reading_is_idempotent(self):
        corrections = _corrections(("chatgpt", "ChatGPT"),
                                   ("chat gpt", "ChatGPT"))
        once = apply_caption_reading_text("they get on chatgpt",
                                          corrections=corrections)
        assert once == "they get on ChatGPT"
        assert (apply_caption_reading_text(once, corrections=corrections)
                == once)
