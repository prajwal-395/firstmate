"""Transcript duration anomalies warn before captions are planned.

A row's own words are scored against its own local rate, so Reel 12's
stretched "hallucinate"/"probably" warn while an ordinary row's naturally
long word stays silent: the threshold is derived per row, never constant.
`library/tools/transcript_duration_anomaly.py`, wired into
`generate_subtitles`. History: docs/evidence/transcription.md.
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


def test_the_r12_row_flags_both_complaints_against_its_local_rate():
    assert len(R12_TOKENS) == 46
    report = flag_duration_anomalies([_row(R12_TOKENS)])
    assert report["rows_checked"] == 1
    flagged = [(w["word"], w["duration_seconds"]) for w in report["warnings"]]
    assert {word for word, _ in flagged} >= {"hallucinate", "probably"}
    # The 0.58s content word is long but local: not an outlier.
    assert not [w for w in flagged if w[0] == "however,"]
    # The squeezed 0.16s duplicate is short: one-sided, never flags.
    assert ("probably", 0.16) not in flagged

    hallucinate = next(w for w in report["warnings"]
                       if w["word"] == "hallucinate")
    assert hallucinate["duration_seconds"] == 1.55
    assert hallucinate["speaker"] == "Akshita"
    assert hallucinate["position"] == 8
    # Derived from this row, not a constant: the row median, far below
    # either flagged duration.
    assert hallucinate["local_median_seconds"] == pytest.approx(
        0.27, abs=0.05)
    assert hallucinate["local_median_seconds"] < 0.72
    assert hallucinate["modified_z"] > 3.5


SLOW_ROW = [
    ("well", 0.38), ("yesterday", 0.68), ("we", 0.30),
    ("talked", 0.52), ("about", 0.40), ("the", 0.32),
    ("whole", 0.48), ("afternoon", 0.62), ("and", 0.34),
    ("it", 0.30), ("was", 0.36), ("really", 0.50),
    ("quite", 0.42), ("something", 0.58), ("else", 0.44),
    ("entirely", 0.60), ("though", 0.46), ("indeed", 0.55),
]
HEALTHY_FAST_ROW = [(f"w{i}", 0.18 + (i % 7) * 0.03) for i in range(20)]


def test_ordinary_rows_stay_silent():
    """A slow speaker's ~0.7s word is unremarkable at a ~0.45s median -
    the same seconds the R12 row flags. One number cannot pass this row
    and flag that one."""
    for tokens in (SLOW_ROW, HEALTHY_FAST_ROW):
        report = flag_duration_anomalies([_row(tokens, position=3,
                                               speaker="Craig")])
        assert report["rows_checked"] == 1
        assert report["warnings"] == []


def test_degenerate_rows_are_skipped_never_warned():
    """Too short, nothing timed but punctuation, or zero spread."""
    for row in (
            _row([("hi", 0.2), ("there", 5.0)]),
            {"position": 1, "words": [
                {"word": "hi", "start": 1.0, "end": 1.2},
                {"word": "...", "start": 1.2, "end": 2.2},
                {"word": "there"}]},
            _row([(f"w{i}", 0.25) for i in range(10)])):
        report = flag_duration_anomalies([row])
        assert report["warnings"] == []
        assert report["rows_skipped"] == 1
        assert report["rows_checked"] == 0


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


def test_generate_subtitles_says_so_on_the_run_and_only_then(capsys):
    generate_subtitles(_spine(R12_TOKENS), caption_case="lowercase")
    err = capsys.readouterr().err
    assert "hallucinate" in err
    assert "probably" in err

    generate_subtitles(_spine(HEALTHY_FAST_ROW), caption_case="lowercase")
    err = capsys.readouterr().err
    assert "duration-anomaly" not in err
    assert "aligner stretch" not in err
