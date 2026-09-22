"""Transcript duration anomalies warn before captions are planned.

Reel 12 (field test, 2026-09-19) is the specimen: the large-v3 arm
dropped "pull from there" and stuttered "probably" into two in a single
46-word row, MFA stretched "hallucinate" across the gap to 1.55s, and
the coverage gate reported CLEAN - every leg reads the transcript. The
two duration outliers in that row WERE the two captain complaints
("hallucinate" 1.55s the row maximum, "probably" 0.72s the runner-up,
against a next-longest content word of 0.58s), so the row's own words
scored against its own local rate must warn, while an ordinary row
carrying a naturally long word must stay silent. The second test is
what proves the threshold discriminates rather than fires on
everything: the same absolute seconds warn in fast speech and pass in
slow speech, because the threshold is derived per row, never constant.

`library/tools/transcript_duration_anomaly.py`, wired into
`generate_subtitles` in `library/steps/step_4_01_plan_subtitles/step.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
from library.tools.transcript_duration_anomaly import flag_duration_anomalies


def _row(tokens, position=8, speaker="Akshita"):
    """Stamp (word, duration) pairs onto cumulative timeline seconds."""
    words = []
    cursor = 1843.0
    for token, span in tokens:
        words.append({"word": token, "start": round(cursor, 3),
                      "end": round(cursor + span, 3), "timed": True})
        cursor += span + 0.02
    return {"position": position, "speaker": speaker,
            "source_start": 1843.91, "source_end": 1857.27,
            "text": " ".join(token for token, _ in tokens),
            "words": words}


# The Reel-12 row's shape: 46 words, bulk clustered at the speaker's
# rate, "hallucinate" at 1.55s the maximum, "probably" at 0.72s the
# runner-up, next-longest content word ("however,") 0.58s - and the
# squeezed stutter duplicate "probably" at 0.16s. Durations are the
# forensics report's numbers
# (`data/vep-ft-r12-missing-words-unexplained/report.md`); the ordinary
# tokens are filler at the same rate, which is all the detector reads.
R12_TOKENS = [
    ("and", 0.09), ("to", 0.11), ("a", 0.12), ("so", 0.13),
    ("of", 0.14), ("it's", 0.15), ("probably", 0.16), ("the", 0.17),
    ("gonna", 0.18), ("models", 0.1868), ("that", 0.1936),
    ("make", 0.2004), ("up", 0.2072), ("things", 0.214),
    ("say", 0.2208), ("which", 0.2276), ("over", 0.22),
    ("most", 0.2344), ("likely", 0.2412), ("people", 0.248),
    ("think", 0.2548), ("about", 0.2616), ("this", 0.2684),
    ("then", 0.27), ("when", 0.2752), ("they", 0.282),
    ("talk", 0.2888), ("with", 0.2956), ("what", 0.3024),
    ("still", 0.31), ("you", 0.3092), ("know", 0.316),
    ("really", 0.3228), ("just", 0.3296), ("like", 0.3364),
    ("because", 0.35), ("another", 0.37), ("something", 0.39),
    ("actually", 0.42), ("around", 0.45), ("before", 0.48),
    ("without", 0.52), ("together", 0.56), ("however,", 0.58),
    ("hallucinate", 1.55), ("probably", 0.72),
]


class TestAnomalousRowWarns:
    def test_r12_row_flags_both_complaints(self):
        assert len(R12_TOKENS) == 46
        report = flag_duration_anomalies([_row(R12_TOKENS)])
        assert report["rows_checked"] == 1
        flagged = {w["word"] for w in report["warnings"]}
        assert "hallucinate" in flagged
        assert "probably" in flagged

    def test_genuine_long_word_and_stutter_duplicate_stay_silent(self):
        report = flag_duration_anomalies([_row(R12_TOKENS)])
        flagged = [(w["word"], w["duration_seconds"])
                   for w in report["warnings"]]
        # The 0.58s content word is long but local: not an outlier.
        assert not [w for w in flagged if w[0] == "however,"]
        # The squeezed 0.16s duplicate is short: one-sided, never flags.
        assert not [w for w in flagged
                    if w == ("probably", 0.16)]

    def test_warning_names_the_local_rate(self):
        report = flag_duration_anomalies([_row(R12_TOKENS)])
        hallucinate = next(w for w in report["warnings"]
                           if w["word"] == "hallucinate")
        assert hallucinate["duration_seconds"] == 1.55
        assert hallucinate["speaker"] == "Akshita"
        assert hallucinate["position"] == 8
        # Derived from this row, not a constant: the row median, far
        # below either flagged duration.
        assert hallucinate["local_median_seconds"] == pytest.approx(
            0.27, abs=0.05)
        assert hallucinate["local_median_seconds"] < 0.72
        assert hallucinate["modified_z"] > 3.5


class TestOrdinaryRowStaysSilent:
    def test_naturally_long_word_in_slow_speech_does_not_warn(self):
        # A slow speaker: the same ~0.7s absolute duration the R12 row
        # flags is unremarkable here, because the row's own median is
        # ~0.45s. This is what "derived from the local rate" buys that
        # a seconds constant cannot: one number cannot pass this row
        # and flag that one.
        tokens = [
            ("well", 0.38), ("yesterday", 0.68), ("we", 0.30),
            ("talked", 0.52), ("about", 0.40), ("the", 0.32),
            ("whole", 0.48), ("afternoon", 0.62), ("and", 0.34),
            ("it", 0.30), ("was", 0.36), ("really", 0.50),
            ("quite", 0.42), ("something", 0.58), ("else", 0.44),
            ("entirely", 0.60), ("though", 0.46), ("indeed", 0.55),
        ]
        report = flag_duration_anomalies([_row(tokens, position=3,
                                              speaker="Craig")])
        assert report["rows_checked"] == 1
        assert report["warnings"] == []

    def test_healthy_fast_row_stays_silent(self):
        tokens = [(f"w{i}", 0.18 + (i % 7) * 0.03) for i in range(20)]
        report = flag_duration_anomalies([_row(tokens)])
        assert report["warnings"] == []


class TestDegenerateRows:
    def test_short_row_is_skipped_never_warned(self):
        report = flag_duration_anomalies([_row([("hi", 0.2),
                                               ("there", 5.0)])])
        assert report["warnings"] == []
        assert report["rows_skipped"] == 1
        assert report["rows_checked"] == 0

    def test_untimed_and_punctuation_words_do_not_count(self):
        words = [{"word": "hi", "start": 1.0, "end": 1.2},
                 {"word": "...", "start": 1.2, "end": 2.2},
                 {"word": "there"}]
        report = flag_duration_anomalies([{"position": 1,
                                           "words": words}])
        assert report["warnings"] == []
        assert report["rows_skipped"] == 1

    def test_zero_spread_row_is_skipped(self):
        tokens = [(f"w{i}", 0.25) for i in range(10)]
        report = flag_duration_anomalies([_row(tokens)])
        assert report["warnings"] == []
        assert report["rows_skipped"] == 1


# ── planned through generate_subtitles, the load-bearing wiring ──

def _spine(tokens, position=8):
    """One speech block carrying `tokens` as its word timestamps."""
    words = []
    cursor = 100.0
    for token, span in tokens:
        words.append({"word": token, "source_start": round(cursor, 3),
                      "source_end": round(cursor + span, 3)})
        cursor += span + 0.02
    source_end = cursor
    return {"structure": [{
        "block_type": "speech",
        "position": position,
        "timeline_start": 47.0,
        "timeline_end": 47.0 + (source_end - 100.0),
        "source_start": 100.0,
        "source_end": source_end,
        "clip_id": "LC4932.MXF",
        "alignment_method": "mfa",
        "word_timestamps": words,
        "content": {"text": " ".join(token for token, _ in tokens)},
    }]}


class TestPlanStepWiring:
    def test_anomalous_block_says_so_on_the_run(self, capsys):
        generate_subtitles(_spine(R12_TOKENS),
                           caption_case="lowercase")
        err = capsys.readouterr().err
        assert "hallucinate" in err
        assert "probably" in err

    def test_ordinary_block_plans_quietly(self, capsys):
        tokens = [(f"w{i}", 0.18 + (i % 7) * 0.03) for i in range(20)]
        generate_subtitles(_spine(tokens), caption_case="lowercase")
        err = capsys.readouterr().err
        assert "duration-anomaly" not in err
        assert "aligner stretch" not in err
