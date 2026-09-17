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
- A token MFA drops (the measured class: backchannel "Mm-hmm",
  hyphenated compounds, possessive "AI's") is UNTIMED, never lost and
  never mis-timed - the same shape whisperx emits for a word it could
  not place, which `interpolate_untimed_words` already handles.
- A transiently empty chunk (~0.9% of sentences, recovered 12 of 12
  on rerun) is RETRIED; only a chunk still empty after the retry
  declines the run to wav2vec2.
- An absent MFA environment declines to wav2vec2 rather than raising,
  and the record says which aligner timed the run either way.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest

from library.tools import hybrid_transcription, mfa_align
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


def test_ordinary_words_pass_through_verbatim():
    """Brand words reach G2P untouched; the named failure class is left
    alone rather than mangled into something confidently wrong."""
    assert (
        mfa_align.normalize_for_aligner("ChatGPT CRM GEO Mm-hmm eye-opening AI's")
        == "ChatGPT CRM GEO Mm-hmm eye-opening AI's"
    )


def test_ordinals_currency_and_commas():
    assert mfa_align._spell_token("21st") == "twenty first"
    assert mfa_align._spell_token("1st") == "first"
    assert mfa_align._spell_token("2nd") == "second"
    assert mfa_align._spell_token("3rd") == "third"
    assert mfa_align._spell_token("4th") == "fourth"
    assert mfa_align._spell_token("$5") == "five dollars"
    assert mfa_align._spell_token("1,000") == "one thousand"


def test_the_map_keeps_every_original_token_beside_its_phrase():
    pairs = mfa_align.normalization_map("Take 20% off")
    assert pairs == [("Take", "Take"), ("20%", "twenty percent"), ("off", "off")]


# ── 2. TextGrid parsing reads the words tier


def test_parse_reads_the_words_tier_and_skips_empties(tmp_path):
    grid = tmp_path / "chunk_0000.TextGrid"
    grid.write_text(
        _textgrid([("", 0.0, 0.5), ("hello", 0.5, 0.9), ("world", 0.9, 1.4)]),
        encoding="utf-8",
    )
    assert mfa_align.parse_textgrid(grid) == [("hello", 0.5, 0.9), ("world", 0.9, 1.4)]


def test_a_missing_textgrid_parses_to_nothing(tmp_path):
    assert mfa_align.parse_textgrid(tmp_path / "nope.TextGrid") == []


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
    """The measured failure class - "Mm-hmm", hyphenated compounds,
    possessive "AI's" - degrades to an untimed word, the shape
    whisperx already emits and `interpolate_untimed_words` handles."""
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
    it but still nothing, wav2vec2 takes the run instead."""
    audio = _wav(tmp_path / "speaker.wav")
    monkeypatch.setattr(mfa_align, "_run_mfa", lambda corpus_dir, out_dir: None)
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        mfa_align.align(_windows(), "en", str(audio))
    assert refused.value.reason == mfa_align.MFA_CHUNK_UNALIGNED


# ── 5. the declines, each one to wav2vec2 rather than a crash


def test_an_absent_environment_declines_rather_than_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "mfa_available", lambda: (False, "no MFA here"))
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        mfa_align.align(_windows(), "en", str(tmp_path / "s.wav"))
    assert refused.value.reason == mfa_align.MFA_ENVIRONMENT_ABSENT


def test_an_uncovered_language_declines(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "mfa_available", lambda: (True, "here"))
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        mfa_align.align(_windows(), "xh", str(tmp_path / "s.wav"))
    assert refused.value.reason == mfa_align.MFA_LANGUAGE_NOT_COVERED


def test_covers_names_only_the_installed_model():
    assert mfa_align.covers("en")
    assert not mfa_align.covers("xh")


# ── 6. the seam: MFA preferred, wav2vec2 live behind it, record says which


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
    """The preferred path stamps its own document; the record copies
    the stamp, the same way the arm is recorded today."""
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
    monkeypatch.setattr(
        tt,
        "_whisperx_transcribe_and_align",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("the fallback must not run")
        ),
    )

    aligned, record = tt.transcribe_audio(tmp_path / "craig.wav")
    assert record["arm"] == hybrid_transcription.ARM_HYBRID
    assert record["aligner"] == hybrid_transcription.ALIGNER_MFA
    assert aligned["segments"][0]["words"][0]["word"] == "Absolutely."


def test_an_mfa_decline_runs_wav2vec2_and_records_it(monkeypatch, tmp_path):
    """Absent environment (or any MFA decline) falls back to the live
    wav2vec2 aligner for the run - a lane without MFA transcribes."""
    import library.tools.timeline_transcript as tt

    _heard(monkeypatch)
    asked = {"wav2vec2": 0}

    def _declined(segments, language, audio_path):
        raise hybrid_transcription.FallbackRequired(
            mfa_align.MFA_ENVIRONMENT_ABSENT, "no MFA here"
        )

    def _wav2vec2(segments, language, audio_path):
        asked["wav2vec2"] += 1
        return {
            "segments": [
                {
                    "start": 0.35,
                    "end": 1.55,
                    "text": "Absolutely.",
                    "words": [
                        {"word": "Absolutely.", "start": 0.5, "end": 1.4, "score": 0.8}
                    ],
                }
            ],
            "aligner": hybrid_transcription.ALIGNER_WAV2VEC2,
        }

    monkeypatch.setattr(mfa_align, "align", _declined)
    monkeypatch.setattr(tt, "align_segments", _wav2vec2)
    monkeypatch.setattr(
        tt,
        "_whisperx_transcribe_and_align",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("the full fallback must not run")
        ),
    )

    aligned, record = tt.transcribe_audio(tmp_path / "craig.wav")
    assert asked == {"wav2vec2": 1}
    assert record["arm"] == hybrid_transcription.ARM_HYBRID
    assert record["aligner"] == hybrid_transcription.ALIGNER_WAV2VEC2
    assert aligned["segments"][0]["text"] == "Absolutely."


def test_the_composite_covers_what_either_aligner_covers(monkeypatch):
    """MFA declines the language check through the same contract, so
    an uncovered language still reaches the wav2vec2 question."""
    import library.tools.timeline_transcript as tt

    monkeypatch.setattr(tt, "aligner_covers", lambda language: False)
    assert tt._aligner().covers("en") is True
    assert tt._aligner().covers("xh") is False


def test_the_document_says_which_aligner_timed_each_speaker():
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


def test_a_record_predating_the_second_aligner_is_not_relabelled():
    """`aligners` says what was recorded. A speaker whose record has
    no `aligner` reads None, not a guess about the past."""
    import library.tools.timeline_transcript as tt

    record = tt.transcription_record(
        {"Craig": {"arm": hybrid_transcription.ARM_HYBRID}}
    )
    assert record["aligners"] == {"Craig": None}


# ── 7. the environment half: discovered, overridable, refusing by name


def test_the_mfa_binary_lives_under_vep_home(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path))
    for var in ("PIPELINE_MFA_BINARY", "PIPELINE_MICROMAMBA_ROOT", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    assert se.mfa_binary() == (
        tmp_path / se.MICROMAMBA_DIRNAME / "envs" / se.MFA_ENV_NAME / "bin" / "mfa"
    )


def test_an_explicit_mfa_binary_wins_outright(monkeypatch, tmp_path):
    elsewhere = tmp_path / "mfa"
    monkeypatch.setenv("PIPELINE_MFA_BINARY", str(elsewhere))
    assert se.mfa_binary() == elsewhere


def test_no_home_directory_is_baked_into_mfa_discovery():
    import ast

    tree = ast.parse(Path(se.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "prajwal" not in node.value


def test_the_refusal_names_the_install_script(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_MFA_BINARY", str(tmp_path / "no-mfa-here"))
    monkeypatch.setenv("PIPELINE_MFA_MODELS", str(tmp_path / "no-models"))
    usable, detail = se.mfa_available()
    assert usable is False
    assert se.MFA_INSTALL_SCRIPT in detail


def test_the_install_script_reports_without_installing(tmp_path):
    script = Path(__file__).resolve().parents[1] / se.MFA_INSTALL_SCRIPT
    assert script.is_file() and os.access(script, os.X_OK)

    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(script), "--check"],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        check=False,
        env=dict(
            os.environ,
            PIPELINE_MFA_BINARY=str(tmp_path / "no-mfa-here"),
            PIPELINE_MFA_MODELS=str(tmp_path / "no-models"),
        ),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "MFA ENV: ABSENT" in result.stdout
