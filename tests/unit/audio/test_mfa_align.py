"""MFA in the aligner slot: normalization, merge, retry, recovery, decline,
record.

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
  12 on rerun) is RETRIED; a chunk still empty after the retry is
  RECOVERED on a widened span, and only a window no pass can align
  keeps the transcriber's own timings marked - the run is refused
  only past MAX_UNALIGNED_FRACTION (one window never refuses).
- An absent MFA environment declines rather than raising, and the
  record says which aligner timed the run either way. Since
  2026-09-24 there is no wav2vec2 second chance behind a decline:
  the decline travels up to the seam, which answers silence or
  refuses loudly.
"""

from __future__ import annotations

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


# ── 1. normalization spells out; the merge writes the ORIGINAL tokens back


def test_digits_symbols_compounds_and_initialisms_are_spelled_for_the_aligner():
    for text, spelled in (
            ("Take 20% off 3.5", "Take twenty percent off three point five"),
            ("10-man 50-person eye-opening",
             "ten man fifty person eye opening"),
            ("CRM ChatGPT AI's", "see are em chat gee pee tee ay eye ess")):
        assert mfa_align.normalize_for_aligner(text) == spelled


# (transcript text, MFA words, window offset, source_words, expected)
# expected rows are (word, start, end), or (word, None, None) for a token
# that must stay explicitly untimed - never lost, never guessed.
MERGE_CASES = [
    # A spelled-out phrase times its original token.
    ("Take 20% off",
     [("take", 0.0, 0.3), ("twenty", 0.3, 0.6), ("percent", 0.6, 0.9),
      ("off", 0.9, 1.1)], 10.0, None,
     [("Take", 10.0, 10.3), ("20%", 10.3, 10.9), ("off", 10.9, 11.1)]),
    # A hyphenated numeric compound unions its pieces' windows.
    ("50-person", [("fifty", 0.2, 0.45), ("person", 0.46, 0.8)], 4.0, None,
     [("50-person", 4.2, 4.8)]),
    # Initialisms and camel-case brands map back letter by letter.
    ("CRM ChatGPT",
     [("see", 0.0, 0.1), ("are", 0.11, 0.2), ("em", 0.21, 0.3),
      ("chat", 0.4, 0.6), ("gee", 0.61, 0.7), ("pee", 0.71, 0.8),
      ("tee", 0.81, 0.9)], 2.0, None,
     [("CRM", 2.0, 2.3), ("ChatGPT", 2.4, 2.9)]),
    # "20 percent" stays two measured source words.
    ("20 percent", [("twenty", 0.2, 0.5), ("percent", 0.51, 0.9)], 1.0, None,
     [("20", 1.2, 1.5), ("percent", 1.51, 1.9)]),
    # A partially aligned expansion is NOT timed.
    ("50-person", [("fifty", 0.2, 0.45)], 4.0, None,
     [("50-person", None, None)]),
    # One grouped transcriber span is never copied to several words.
    ("20 percent", [], 1.0,
     [{"word": "20 percent", "start": 1.2, "end": 1.9}],
     [("20", None, None), ("percent", None, None)]),
    # Tokens MFA dropped are untimed, never lost.
    ("Well Mm-hmm eye-opening AI's model",
     [("well", 0.0, 0.3), ("model", 1.5, 1.9)], 0.0, None,
     [("Well", 0.0, 0.3), ("Mm-hmm", None, None), ("eye-opening", None, None),
      ("AI's", None, None), ("model", 1.5, 1.9)]),
]


def test_a_token_is_timed_only_when_every_piece_aligned():
    for text, aligned, offset, source_words, expected in MERGE_CASES:
        kwargs = {"source_words": source_words} if source_words else {}
        words = mfa_align.merge_window(text, aligned, offset, **kwargs)
        assert [w["word"] for w in words] == [e[0] for e in expected], text
        for word, (_token, start, end) in zip(words, expected):
            if start is None:
                assert "start" not in word and "end" not in word, (text, word)
            else:
                assert word["start"] == pytest.approx(start), (text, word)
                assert word["end"] == pytest.approx(end), (text, word)
    partial = mfa_align.merge_window("50-person", [("fifty", 0.2, 0.45)], 4.0)
    assert partial == [{"word": "50-person", "timed": False,
                        "timing_reason": "mfa_word_not_fully_aligned"}]
    grouped = mfa_align.merge_window(
        "20 percent", [], 1.0,
        source_words=[{"word": "20 percent", "start": 1.2, "end": 1.9}])
    assert all(word["timed"] is False for word in grouped)


# ── 2. TextGrid parsing reads the words tier


def test_parse_reads_the_words_tier_and_skips_empties(tmp_path):
    grid = tmp_path / "chunk_0000.TextGrid"
    grid.write_text(
        _textgrid([("", 0.0, 0.5), ("hello", 0.5, 0.9), ("world", 0.9, 1.4)]),
        encoding="utf-8",
    )
    assert mfa_align.parse_textgrid(grid) == [("hello", 0.5, 0.9), ("world", 0.9, 1.4)]


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


# ── 4. one unaligned window no longer refuses the file ──
#
# Pins the LCATL0013 defect, 2026-10-01: the disfluent "Then you're
# then you're Yeah, okay." failed in the full 1,025-window corpus and
# after the same-span retry, yet aligned in a one-window corpus - and
# the old zero-tolerance rule refused the whole file over it
# (`mfa_chunk_unaligned`, 10,621 words lost over 6). These tests stub
# `_run_mfa` by planting TextGrids, the same seam the retry tests use;
# the proof the widened span recovers the real window is the
# transcription itself, reported in firstmate
# `data/vep-mfa-one-window-drops-whole-file/eval.md`.


def _sourced_windows():
    """Real hybrid windows (with `source_words`) over synthetic spans."""
    texts = [
        "Craig nods along",
        "Lucie laughs then continues",
        "Then you're then you're Yeah, okay.",
        "So the system ships",
    ]
    words, sentences = [], []
    cursor = 10.0
    for text in texts:
        spans = []
        for token in text.split():
            spans.append((token, round(cursor, 3), round(cursor + 0.24, 3)))
            cursor = round(cursor + 0.34, 3)
        words.extend(spans)
        sentences.append((text, spans[0][1], spans[-1][2]))
        cursor = round(cursor + 0.8, 3)
    spoken = heard_speech.HeardSpeech(
        words=[heard_speech.HeardWord(word, start, end)
               for word, start, end in words],
        sentences=[heard_speech.HeardSentence(text, start, end)
                   for text, start, end in sentences],
        text=" ".join(word for word, _s, _e in words))
    return hybrid_transcription.alignment_windows(spoken)


def _plant_lab_text(corpus_dir: Path, out_dir: Path, name: str) -> None:
    """A TextGrid timing every token of the chunk's own `.lab` text."""
    lab = (corpus_dir / f"{name}.lab").read_text(encoding="utf-8")
    tokens = lab.split()
    _plant(out_dir, name,
           [(token.lower(), 0.05 + 0.1 * at, 0.1 + 0.1 * at)
            for at, token in enumerate(tokens)])


def test_a_single_unaligned_window_keeps_transcriber_timings_rather_than_refusing_the_file(
        monkeypatch, tmp_path):
    """The old whole-file refusal, pinned: one window MFA cannot align
    on any pass used to raise `mfa_chunk_unaligned` and drop the file.
    Now the file completes, the window keeps voz's own timings marked,
    and the document names it."""
    audio = _wav(tmp_path / "speaker.wav", seconds=30.0)
    windows = _sourced_windows()
    bad = 2

    def _never_bad(corpus_dir, out_dir):
        for wav_path in sorted(corpus_dir.glob("*.wav")):
            if wav_path.stem.endswith(f"{bad:04d}"):
                continue
            _plant_lab_text(corpus_dir, out_dir, wav_path.stem)

    monkeypatch.setattr(mfa_align, "_run_mfa", _never_bad)
    out = mfa_align.align(windows, "en", str(audio))

    assert len(out["segments"]) == len(windows)
    for index, segment in enumerate(out["segments"]):
        own = windows[index]["source_words"]
        assert [w["word"] for w in segment["words"]] == [
            s["word"] for s in own]
        assert all("start" in w and "end" in w
                   for w in segment["words"])
    fallen = out["segments"][bad]["words"]
    assert all(w.get("timing_source") == "transcriber" for w in fallen)
    assert [(w["start"], w["end"]) for w in fallen] == [
        (s["start"], s["end"]) for s in windows[bad]["source_words"]]
    for index, segment in enumerate(out["segments"]):
        if index != bad:
            assert all("timing_source" not in w
                       for w in segment["words"])
    assert out["mfa_recovery"] == {
        "recovered_window_indexes": [],
        "transcriber_timed_window_indexes": [bad],
    }


def test_a_widened_recovery_realigns_a_window_the_main_pass_lost(
        monkeypatch, tmp_path):
    """Recovery is tried before fallback: a window empty on the main
    and retry passes but alignable on the widened span comes back with
    MFA timings (no `timing_source` mark) and is named as recovered."""
    audio = _wav(tmp_path / "speaker.wav", seconds=30.0)
    windows = _sourced_windows()
    bad = 2
    seen_spans = {}

    def _wide_only(corpus_dir, out_dir):
        for wav_path in sorted(corpus_dir.glob("*.wav")):
            name = wav_path.stem
            with wave.open(str(wav_path), "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
            seen_spans[name] = frames / rate
            if name.endswith(f"{bad:04d}") and not name.startswith(
                    "recovered_"):
                continue
            _plant_lab_text(corpus_dir, out_dir, name)

    monkeypatch.setattr(mfa_align, "_run_mfa", _wide_only)
    out = mfa_align.align(windows, "en", str(audio))

    assert out["mfa_recovery"] == {
        "recovered_window_indexes": [bad],
        "transcriber_timed_window_indexes": [],
    }
    fallen = out["segments"][bad]["words"]
    assert all("timing_source" not in w for w in fallen)
    assert all("start" in w and "end" in w for w in fallen)
    main_span = seen_spans[f"chunk_{bad:04d}"]
    recovery_span = seen_spans[f"recovered_{bad:04d}"]
    assert recovery_span == pytest.approx(
        main_span + 2 * mfa_align.RECOVERY_PAD_SECONDS)


def test_broad_alignment_failure_still_refuses(monkeypatch, tmp_path):
    """6 of 10 windows unaligned (60%, far past the 5% fraction) still
    declines with `mfa_chunk_unaligned` - the refusal stays for a file
    where alignment genuinely fails broadly."""
    audio = _wav(tmp_path / "speaker.wav", seconds=60.0)
    words, sentences = [], []
    cursor = 1.0
    for line in range(10):
        text = f"line number {line} here"
        spans = []
        for token in text.split():
            spans.append((token, round(cursor, 3), round(cursor + 0.2, 3)))
            cursor = round(cursor + 0.3, 3)
        words.extend(spans)
        sentences.append((text, spans[0][1], spans[-1][2]))
        cursor = round(cursor + 0.5, 3)
    spoken = heard_speech.HeardSpeech(
        words=[heard_speech.HeardWord(word, start, end)
               for word, start, end in words],
        sentences=[heard_speech.HeardSentence(text, start, end)
                   for text, start, end in sentences],
        text=" ".join(word for word, _s, _e in words))
    windows = hybrid_transcription.alignment_windows(spoken)
    assert len(windows) == 10
    dead = {0, 1, 2, 3, 4, 5}

    def _mostly_dead(corpus_dir, out_dir):
        for wav_path in sorted(corpus_dir.glob("*.wav")):
            name = wav_path.stem
            tail = name.rsplit("_", 1)[-1]
            if tail.isdigit() and int(tail) in dead:
                continue
            _plant_lab_text(corpus_dir, out_dir, name)

    monkeypatch.setattr(mfa_align, "_run_mfa", _mostly_dead)
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        mfa_align.align(windows, "en", str(audio))
    assert refused.value.reason == mfa_align.MFA_CHUNK_UNALIGNED
    assert "6 of 10" in refused.value.detail


# ── 5. the retry, then the decline


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


# ── 6. the declines, each one a refusal rather than a crash


def test_an_absent_environment_declines_rather_than_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "mfa_available", lambda: (False, "no MFA here"))
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        mfa_align.align(_windows(), "en", str(tmp_path / "s.wav"))
    assert refused.value.reason == mfa_align.MFA_ENVIRONMENT_ABSENT


# ── 7. the seam: MFA times the run, the record says which aligner ──


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


# ── 8. the environment half: discovered, overridable, refusing by name


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
