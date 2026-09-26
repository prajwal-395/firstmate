"""MFA in the aligner slot: normalization, merge, retry, decline, record.

Nothing here runs the real `mfa` binary, loads an alignment model, or
reaches a real project. The subprocess is stubbed at
`mfa_align._run_mfa` by planting TextGrids; the one proof that the
real binary accepts this module's corpus layout and that its TextGrids
parse lives in the module's own smoke run, not in this file.

What these pin, and why each is here
------------------------------------
- Digit/symbol normalization spells tokens out for the aligner's
  benefit ONLY: the transcript text the pipeline consumes is byte for
  byte what was said (`data/vep-try-mfa-instead-of-wav2vec2/report.md`
  Q2: "20%" and "3.5" silently drop without it).
- Hyphenated compounds, acronym/camel-case spellings and digit phrases
  are normalized for MFA and mapped back to their original tokens. A
  token is timed only when every normalized piece aligns or an exact
  measured one-to-one transcriber span exists.
- A transiently empty chunk (~0.9% of sentences, recovered 12 of
  12 on rerun) is RETRIED; only a chunk still empty after the retry
  declines the run.
- An absent MFA environment declines rather than raising, and the
  record says which aligner timed the run either way. Since
  2026-09-24 there is no wav2vec2 second chance behind a decline:
  the decline travels up to the seam, which answers silence or
  refuses loudly.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest

from library.tools import (heard_speech, hybrid_transcription, mfa_align,
                           timeline_transcript, transcript_fit)
from library.tools import shared_environment as se


# ── helpers ────────────────────────────────────────────────────────


def _wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> Path:
    """A silent wav, enough for the chunk slicer to read."""
    frames = b"\x00\x00" * int(seconds * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return path


def _textgrid(words) -> str:
    """A minimal MFA TextGrid with a words tier and a phones tier."""
    lines = [
        'File type = "ooTextFile"',
        'Object class = "TextGrid"',
        "xmin = 0 ",
        "xmax = 2 ",
        "size = 2 ",
        "item []: ",
        "    item [1]:",
        '        class = "IntervalTier" ',
        '        name = "words" ',
        "        intervals: size = 4 ",
    ]
    for index, (word, start, end) in enumerate(words, start=1):
        lines += [
            f"        intervals [{index}]:",
            f"            xmin = {start} ",
            f"            xmax = {end} ",
            f'            text = "{word}" ',
        ]
    lines += [
        "    item [2]:",
        '        class = "IntervalTier" ',
        '        name = "phones" ',
        "        intervals [1]:",
        "            xmin = 0 ",
        "            xmax = 2 ",
        '            text = "sil" ',
    ]
    return "\n".join(lines) + "\n"


def _windows():
    return [
        {"start": 0.0, "end": 2.0, "text": "Absolutely."},
        {"start": 2.5, "end": 4.5, "text": "Take 20% off 3.5"},
    ]


# ── 1. normalization spells out, and changes nothing the pipeline reads


def test_digits_and_symbols_are_spelled_out_for_the_aligner():
    assert (
        mfa_align.normalize_for_aligner("Take 20% off 3.5")
        == "Take twenty percent off three point five"
    )


def test_mfa_splits_hyphenated_numeric_compounds_and_unions_their_windows():
    assert mfa_align.normalize_for_aligner("10-man 50-person eye-opening") == (
        "ten man fifty person eye opening")
    words = mfa_align.merge_window(
        "50-person", [("fifty", 0.2, 0.45), ("person", 0.46, 0.8)], 4.0)
    assert words == [{"word": "50-person", "start": 4.2, "end": 4.8}]


def test_mfa_expands_initialisms_and_camel_case_brands_for_alignment():
    assert mfa_align.normalize_for_aligner("CRM ChatGPT AI's") == (
        "see are em chat gee pee tee ay eye ess")
    words = mfa_align.merge_window(
        "CRM ChatGPT",
        [("see", 0.0, 0.1), ("are", 0.11, 0.2), ("em", 0.21, 0.3),
         ("chat", 0.4, 0.6), ("gee", 0.61, 0.7), ("pee", 0.71, 0.8),
         ("tee", 0.81, 0.9)],
        2.0,
    )
    assert [(w["word"], w["start"], w["end"]) for w in words] == [
        ("CRM", 2.0, 2.3), ("ChatGPT", 2.4, 2.9)]


def test_mfa_does_not_mark_a_partially_aligned_expansion_as_timed():
    words = mfa_align.merge_window(
        "50-person", [("fifty", 0.2, 0.45)], 4.0)
    assert words == [{
        "word": "50-person", "timed": False,
        "timing_reason": "mfa_word_not_fully_aligned",
    }]


def test_mfa_keeps_twenty_percent_as_two_measured_source_words():
    words = mfa_align.merge_window(
        "20 percent", [("twenty", 0.2, 0.5), ("percent", 0.51, 0.9)], 1.0)
    assert [(w["word"], w["start"], w["end"]) for w in words] == [
        ("20", 1.2, 1.5), ("percent", 1.51, 1.9)]


def test_mfa_does_not_copy_one_grouped_phrase_span_to_multiple_words():
    words = mfa_align.merge_window(
        "20 percent", [], 1.0,
        source_words=[{"word": "20 percent", "start": 1.2, "end": 1.9}],
    )
    assert [word["word"] for word in words] == ["20", "percent"]
    assert all(word["timed"] is False for word in words)
    assert all("start" not in word for word in words)


# ── 2. TextGrid parsing reads the words tier


def test_parse_reads_the_words_tier_and_skips_empties(tmp_path):
    grid = tmp_path / "chunk_0000.TextGrid"
    grid.write_text(
        _textgrid([("", 0.0, 0.5), ("hello", 0.5, 0.9), ("world", 0.9, 1.4)]),
        encoding="utf-8",
    )
    assert mfa_align.parse_textgrid(grid) == [("hello", 0.5, 0.9), ("world", 0.9, 1.4)]


# ── 3. the merge writes the ORIGINAL tokens back


def test_merge_times_original_tokens_through_spelled_out_phrases():
    words = mfa_align.merge_window(
        "Take 20% off",
        [
            ("take", 0.0, 0.3),
            ("twenty", 0.3, 0.6),
            ("percent", 0.6, 0.9),
            ("off", 0.9, 1.1),
        ],
        10.0,
    )
    assert [(w["word"], round(w["start"], 2), round(w["end"], 2)) for w in words] == [
        ("Take", 10.0, 10.3),
        ("20%", 10.3, 10.9),
        ("off", 10.9, 11.1),
    ]


def test_a_token_mfa_dropped_is_untimed_never_lost():
    """An incompletely aligned original token is explicit and untimed."""
    words = mfa_align.merge_window(
        "Well Mm-hmm eye-opening AI's model",
        [("well", 0.0, 0.3), ("model", 1.5, 1.9)],
        0.0,
    )
    assert [w["word"] for w in words] == [
        "Well",
        "Mm-hmm",
        "eye-opening",
        "AI's",
        "model",
    ]
    assert words[0]["start"] == pytest.approx(0.0)
    assert words[4]["start"] == pytest.approx(1.5)
    for word in words[1:4]:
        assert "start" not in word and "end" not in word


def test_mfa_dropped_word_keeps_transcriber_timing_through_transcript(
        monkeypatch, tmp_path):
    """A source-timed token MFA drops keeps its span through the full seam."""
    spoken = heard_speech.HeardSpeech(
        words=[heard_speech.HeardWord(word, start, end) for word, start, end in
               [("Hello", 10.10, 10.30), ("there", 10.40, 10.55),
                ("friend", 10.70, 10.90)]],
        sentences=[heard_speech.HeardSentence(
            "Hello there friend", 10.10, 10.90)])
    window = hybrid_transcription.alignment_windows(spoken)[0]
    monkeypatch.setattr(
        mfa_align, "_chunk_words",
        lambda _path: [("hello", 0.25, 0.45), ("friend", 0.85, 1.05)])

    aligned = mfa_align._merge_all([window], tmp_path)
    words = timeline_transcript.interpolate_untimed_words(
        aligned["segments"][0]["words"])
    recovered = next(word for word in words if word["word"] == "there")

    assert recovered["start"] == pytest.approx(10.40)
    assert recovered["end"] == pytest.approx(10.55)
    assert recovered["timed"] is True
    assert recovered["timing_source"] == "transcriber"
    assert transcript_fit.row_fit({
        "text": "Hello there friend", "words": words}) is None


# ── 4. the retry, then the decline


def _plant(out_dir: Path, name: str, words) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.TextGrid").write_text(_textgrid(words), encoding="utf-8")


def test_a_transiently_empty_chunk_is_retried(monkeypatch, tmp_path):
    """~0.9% of chunks produce no TextGrid; a rerun recovered 12 of
    12. The first pass misses chunk_0000, the retry recovers it, and
    no word loses its timing."""
    audio = _wav(tmp_path / "speaker.wav")
    calls = []

    def _flaky(corpus_dir, out_dir):
        calls.append(sorted(p.name for p in corpus_dir.glob("*.wav")))
        if len(calls) == 1:
            _plant(
                out_dir,
                "chunk_0001",
                [
                    ("take", 0.0, 0.3),
                    ("twenty", 0.3, 0.6),
                    ("percent", 0.6, 0.9),
                    ("off", 0.9, 1.1),
                    ("three", 1.1, 1.4),
                    ("point", 1.4, 1.6),
                    ("five", 1.6, 1.9),
                ],
            )
        else:
            _plant(out_dir, "chunk_0000", [("absolutely", 0.1, 0.9)])

    monkeypatch.setattr(mfa_align, "_run_mfa", _flaky)
    out = mfa_align.align(_windows(), "en", str(audio))

    assert len(calls) == 2
    assert calls[1] == ["chunk_0000.wav"], (
        "the retry carries only the missing chunk, not the whole corpus"
    )
    assert out["aligner"] == hybrid_transcription.ALIGNER_MFA
    first = out["segments"][0]["words"][0]
    assert first == {
        "word": "Absolutely.",
        "start": pytest.approx(0.1),
        "end": pytest.approx(0.9),
    }
    assert out["segments"][1]["text"] == "Take 20% off 3.5"


def test_a_chunk_empty_after_retry_declines_the_run(monkeypatch, tmp_path):
    """Without the retry real words silently lose their timings; with
    it but still nothing, the run is refused instead."""
    audio = _wav(tmp_path / "speaker.wav")
    monkeypatch.setattr(mfa_align, "_run_mfa", lambda corpus_dir, out_dir: None)
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        mfa_align.align(_windows(), "en", str(audio))
    assert refused.value.reason == mfa_align.MFA_CHUNK_UNALIGNED


# ── 5. the declines, each one a refusal rather than a crash


def test_an_absent_environment_declines_rather_than_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "mfa_available", lambda: (False, "no MFA here"))
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        mfa_align.align(_windows(), "en", str(tmp_path / "s.wav"))
    assert refused.value.reason == mfa_align.MFA_ENVIRONMENT_ABSENT


# ── 6. the seam: MFA times the run, the record says which aligner ──


def _heard(monkeypatch):
    from library.tools import heard_speech

    monkeypatch.setattr(
        heard_speech,
        "identify_language",
        lambda path, **kw: heard_speech.HeardLanguage("en", 0.98),
    )
    monkeypatch.setattr(
        heard_speech,
        "transcribe",
        lambda path, **kw: heard_speech.HeardSpeech(
            words=[heard_speech.HeardWord("Absolutely.", 0.5, 1.4)],
            sentences=[heard_speech.HeardSentence("Absolutely.", 0.5, 1.4)],
            text="Absolutely.",
            engine={"transcriber": "da", "version": "0.1.1"},
        ),
    )


def test_mfa_success_is_recorded_on_the_run(monkeypatch, tmp_path):
    """The MFA path stamps its own document; the record copies
    the stamp, the same way the arm is recorded."""
    import library.tools.timeline_transcript as tt

    _heard(monkeypatch)
    monkeypatch.setattr(
        mfa_align,
        "align",
        lambda segments, language, audio_path: {
            "segments": [
                {
                    "start": 0.35,
                    "end": 1.55,
                    "text": "Absolutely.",
                    "words": [{"word": "Absolutely.", "start": 0.5, "end": 1.4}],
                }
            ],
            "aligner": hybrid_transcription.ALIGNER_MFA,
        },
    )

    aligned, record = tt.transcribe_audio(tmp_path / "craig.wav")
    assert record["arm"] == hybrid_transcription.ARM_HYBRID
    assert record["aligner"] == hybrid_transcription.ALIGNER_MFA
    assert aligned["segments"][0]["words"][0]["word"] == "Absolutely."


def test_an_mfa_decline_is_refused_not_realigned(monkeypatch, tmp_path):
    """Absent environment (or any MFA decline) is an aligner-side
    refusal: speech exists that nothing can place, so the decline
    propagates out of the seam instead of an empty transcript. The
    wav2vec2 second chance behind it left with whisperx on
    2026-09-24."""
    import library.tools.timeline_transcript as tt

    _heard(monkeypatch)

    def _declined(segments, language, audio_path):
        raise hybrid_transcription.FallbackRequired(
            mfa_align.MFA_ENVIRONMENT_ABSENT, "no MFA here"
        )

    monkeypatch.setattr(mfa_align, "align", _declined)

    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        tt.transcribe_audio(tmp_path / "craig.wav")
    assert refused.value.reason == mfa_align.MFA_ENVIRONMENT_ABSENT


def test_the_document_says_which_aligner_timed_each_speaker():
    """The wav2vec2 value below is legacy vocabulary for transcripts
    written before 2026-09-24 - still readable, no longer producible."""
    import library.tools.timeline_transcript as tt

    record = tt.transcription_record(
        {
            "Akshita": {
                "arm": hybrid_transcription.ARM_HYBRID,
                "aligner": hybrid_transcription.ALIGNER_MFA,
            },
            "Craig": {
                "arm": hybrid_transcription.ARM_WHISPERX,
                "aligner": hybrid_transcription.ALIGNER_WAV2VEC2,
            },
        }
    )
    assert record["aligners"] == {"Akshita": "mfa", "Craig": "wav2vec2"}


# ── 7. the environment half: discovered, overridable, refusing by name


def test_the_mfa_binary_lives_under_vep_home(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path))
    for var in ("PIPELINE_MFA_BINARY", "PIPELINE_MICROMAMBA_ROOT", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    assert se.mfa_binary() == (
        tmp_path / se.MICROMAMBA_DIRNAME / "envs" / se.MFA_ENV_NAME / "bin" / "mfa"
    )


def test_the_refusal_names_the_install_script(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_MFA_BINARY", str(tmp_path / "no-mfa-here"))
    monkeypatch.setenv("PIPELINE_MFA_MODELS", str(tmp_path / "no-models"))
    usable, detail = se.mfa_available()
    assert usable is False
    assert se.MFA_INSTALL_SCRIPT in detail
