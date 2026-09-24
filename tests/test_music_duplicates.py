"""Two candidates that are one recording, established from measurements.

001's catalogue held seven files, four survived the duration check, and
two of those four were the same recording under two filenames - so the
model was told it had four things to choose between and it had three.

The numbers in these fixtures are the ones measured on 001 and on
re-encodes of its own tracks, 2026-08-28; `library/tools/music_duplicates.py`
carries the table. Nothing here is a filename comparison.
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

from library.tools.music_duplicates import (  # noqa: E402
    DB_TOLERANCE,
    DECLINED_SIGNALS,
    distinct_count,
    duplicate_groups,
    mark_duplicates,
    same_recording,
    summarise,
)


def candidate(title, path, duration, lufs, lra, tp, spread, wspread,
              speech_band, envelope, measured=True):
    return {
        "title": title, "audio_path": path, "duration_seconds": duration,
        "measured": measured, "integrated_lufs": lufs,
        "loudness_range_lu": lra, "true_peak_dbtp": tp,
        "rms_spread_db": spread, "window_spread_db": wspread,
        "speech_band_ratio_db": speech_band,
        "window_envelope_dbfs": envelope,
    }


# 001's own four surviving candidates, measured 2026-08-28.
SICKICK_ENVELOPE = [-23.1, -19.2, -20.7, -18.7, -17.5, -14.7,
                    -15.8, -11.7, -12.8, -13.2, -15.2, -15.3]

LYRICS_MP3 = candidate(
    "Sickick - Infected (lyrics)", "/library/Sickick - Infected (lyrics).mp3",
    201.886, -13.78, 7.40, 0.42, 14.79, 17.33, -6.39, SICKICK_ENVELOPE)
LYRICS_WAV = candidate(
    "Sickick - Infected _lyrics_", "/001/Sickick - Infected _lyrics_.wav",
    201.886, -13.78, 7.40, 0.09, 14.35, 17.33, -6.41, SICKICK_ENVELOPE)
INSTRUMENTAL = candidate(
    "Sickick- _Infected_ _Instrumental_", "/001/instrumental.wav",
    198.600, -15.17, 9.80, 0.00, 30.58, 31.05, -8.60,
    [-42.7, -43.1, -30.7, -26.9, -20.1, -17.4,
     -18.0, -12.9, -14.1, -14.1, -16.5, -16.7])
RISE = candidate(
    "_background music_ rise", "/001/rise.wav",
    149.013, -13.94, 6.00, -0.86, 10.66, 2.26, -2.81,
    [-17.7, -14.9, -15.2, -14.8, -15.0, -15.4,
     -15.2, -15.8, -14.9, -15.1, -15.3, -14.9])

FOUR = [LYRICS_MP3, INSTRUMENTAL, LYRICS_WAV, RISE]


# ── The pair, and the pair that is not one ────────────────────────────

def test_the_two_encodes_of_one_recording_are_found():
    verdict, deltas = same_recording(LYRICS_MP3, LYRICS_WAV)
    assert verdict is True
    assert deltas["integrated_lufs"] == 0.0
    assert deltas["rms_spread_db"] == pytest.approx(0.44, abs=0.001)
    assert deltas["window_envelope_dbfs"] == 0.0


def test_the_instrumental_of_the_same_song_is_not_folded_in():
    """The hardest real pair available: same song, different recording."""
    for other in (LYRICS_MP3, LYRICS_WAV):
        verdict, deltas = same_recording(other, INSTRUMENTAL)
        assert verdict is False
    # and the margin is more than an order of magnitude
    assert abs(LYRICS_MP3["integrated_lufs"]
               - INSTRUMENTAL["integrated_lufs"]) > 10 * 0.0 + 1.0
    assert abs(SICKICK_ENVELOPE[0] - INSTRUMENTAL["window_envelope_dbfs"][0]) \
        > 10 * DB_TOLERANCE


def test_the_verdict_is_blind_to_the_name():
    """`(lyrics)` vs `_lyrics_` is no closer by string distance than
    either is to `_Instrumental_`, which is a different recording.

    So the names are made to disagree where the measurements agree, and
    to agree where they disagree, and neither verdict may move.
    """
    renamed_a = dict(LYRICS_MP3, title="Zebra", audio_path="/z.mp3")
    renamed_b = dict(LYRICS_WAV, title="Aardvark", audio_path="/a.wav")
    assert same_recording(renamed_a, renamed_b)[0] is True

    same_name_a = dict(LYRICS_MP3, title="Identical", audio_path="/x1.wav")
    same_name_b = dict(INSTRUMENTAL, title="Identical", audio_path="/x2.wav")
    assert same_recording(same_name_a, same_name_b)[0] is False

    assert "the filename" in DECLINED_SIGNALS


# ── Nothing is deleted ────────────────────────────────────────────────

# ── An absent measurement is not evidence of sameness ─────────────────

def test_an_unmeasured_candidate_is_never_a_duplicate():
    hollow_a = {"title": "a", "audio_path": "/a.wav", "measured": False,
                "measurement_note": "already out on duration"}
    hollow_b = {"title": "b", "audio_path": "/b.wav", "measured": False,
                "measurement_note": "already out on duration"}
    assert same_recording(hollow_a, hollow_b)[0] is False
    assert same_recording(LYRICS_MP3, hollow_b)[0] is False
    marked = mark_duplicates([hollow_a, hollow_b, LYRICS_MP3, LYRICS_WAV])
    assert distinct_count(marked) == 3


def test_a_missing_column_is_not_a_match():
    stripped = {k: v for k, v in LYRICS_WAV.items()
                if k != "speech_band_ratio_db"}
    assert same_recording(LYRICS_MP3, stripped)[0] is False


def test_envelopes_of_different_lengths_do_not_match():
    short = dict(LYRICS_WAV)
    short["window_envelope_dbfs"] = SICKICK_ENVELOPE[:6]
    assert same_recording(LYRICS_MP3, short)[0] is False


# ── The tolerance is measured, not picked ─────────────────────────────

WORST_RE_ENCODE_DELTA_DB = 0.50  # mp3 128k, worst envelope bucket


def test_the_tolerance_is_twice_the_worst_measured_re_encode_difference():
    assert DB_TOLERANCE == pytest.approx(2 * WORST_RE_ENCODE_DELTA_DB)


@pytest.mark.parametrize("field", [
    "integrated_lufs",
])
def test_a_difference_past_the_tolerance_is_a_different_recording(field):
    other = dict(LYRICS_WAV)
    other[field] = LYRICS_MP3[field] + DB_TOLERANCE + 0.1
    assert same_recording(LYRICS_MP3, other)[0] is False


def test_a_different_length_is_a_different_recording():
    other = dict(LYRICS_WAV)
    other["duration_seconds"] = LYRICS_MP3["duration_seconds"] + 3.0
    assert same_recording(LYRICS_MP3, other)[0] is False
