"""The hybrid seam: the window it builds, and every way it refuses.

Nothing here runs the transcriber, loads an alignment model, touches a
GPU or reaches a real project. The transcriber is replayed from the
recorded payload in `tests/fixtures/reel_hearing/`; the aligner is a
`hybrid_transcription.Aligner` built out of two plain functions, which
is the whole reason that seam is two callables rather than an import.

The dials these tests pin are not preferences. `SILENCE_SPLIT_SECONDS`
and `WINDOW_PAD_SECONDS` were settled by a 21-configuration sweep over a
full 82-minute file, and each one removes a COUNTED defect class:
`data/vep-push-the-alignment-floor/report.md`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import heard_speech, hybrid_transcription

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDED = (REPO_ROOT / "tests" / "fixtures" / "reel_hearing"
            / "reel26.heard.json")


def _heard(words, sentences=None) -> heard_speech.HeardSpeech:
    """A transcription with the given word and sentence spans."""
    return heard_speech.HeardSpeech(
        words=[heard_speech.HeardWord(word=w, start=s, end=e)
               for w, s, e in words],
        sentences=[heard_speech.HeardSentence(text=t, start=s, end=e)
                   for t, s, e in (sentences or [])],
        text=" ".join(w for w, _s, _e in words),
    )


def _aligner(align, covers=lambda language: language == "en"):
    return hybrid_transcription.Aligner(covers=covers, align=align)


def _one_word_per_window(windows, language, audio_path):
    """An aligner that places every word evenly inside its window."""
    segments = []
    for window in windows:
        tokens = window["text"].split()
        span = (window["end"] - window["start"]) / max(len(tokens), 1)
        segments.append({
            "start": window["start"], "end": window["end"],
            "text": window["text"],
            "words": [{"word": token,
                       "start": window["start"] + index * span,
                       "end": window["start"] + (index + 1) * span,
                       "score": 0.9}
                      for index, token in enumerate(tokens)],
        })
    return {"segments": segments}


# ── 1. The window ────────────────────────────────────────────────────

def test_a_window_is_rebuilt_from_the_words_not_the_sentence_span():
    """A sentence span covers the silence its last word was stretched
    across; the window must not."""
    spoken = _heard(
        [("Since", 10.0, 10.3), ("we", 10.3, 10.5), ("started", 10.5, 11.0)],
        [("Since we started", 10.0, 30.0)])
    windows = hybrid_transcription.alignment_windows(spoken)
    assert len(windows) == 1
    assert windows[0]["end"] == pytest.approx(
        11.0 + hybrid_transcription.WINDOW_PAD_SECONDS)


def test_a_sentence_is_split_at_an_internal_silence():
    """The over-long-word class comes from one cause: a sentence whose
    own words span a silence. Measured, a 1.0s split empties it."""
    spoken = _heard(
        [("Okay", 0.0, 0.2), ("so", 0.2, 0.4),
         ("right", 20.0, 20.3), ("then", 20.3, 20.6)],
        [("Okay so right then", 0.0, 20.6)])
    windows = hybrid_transcription.alignment_windows(spoken)
    assert [w["text"] for w in windows] == ["Okay so", "right then"]
    assert windows[0]["end"] < windows[1]["start"]


def test_the_split_is_at_one_second_and_two_seconds_would_not_split_this():
    """The sweep's own finding, pinned: at a 2.0s threshold four
    over-long words survive across the two mic files, at 1.0s none
    does. A gap of 1.4s is exactly the material that distinguishes
    them."""
    spoken = _heard(
        [("one", 0.0, 0.3), ("two", 1.7, 2.0)],
        [("one two", 0.0, 2.0)])
    assert hybrid_transcription.SILENCE_SPLIT_SECONDS == 1.0
    assert len(hybrid_transcription.alignment_windows(spoken)) == 2
    assert len(hybrid_transcription.alignment_windows(
        spoken, silence_split=2.0)) == 1


def test_every_window_is_padded_at_both_ends():
    """At pad zero, 8 one-word backchannels failed alignment outright
    and were LOST. Any pad at all recovered all 8."""
    spoken = _heard([("Yeah.", 5.0, 5.16)], [("Yeah.", 5.0, 5.16)])
    window = hybrid_transcription.alignment_windows(spoken)[0]
    pad = hybrid_transcription.WINDOW_PAD_SECONDS
    assert pad == 0.15
    assert window["start"] == pytest.approx(5.0 - pad)
    assert window["end"] == pytest.approx(5.16 + pad)


def test_a_window_never_starts_before_the_file_does():
    spoken = _heard([("Hi", 0.02, 0.4)], [("Hi", 0.02, 0.4)])
    assert hybrid_transcription.alignment_windows(spoken)[0]["start"] == 0.0


def test_a_word_stretched_across_a_silence_is_clamped_before_windowing():
    """The transcriber holds a sentence-final word for up to 13.44s,
    measured. Left alone it drags the window over the silence."""
    spoken = _heard(
        [("and", 1.0, 1.3), ("then", 1.3, 1.6), ("so", 1.6, 14.0)],
        [("and then so", 1.0, 14.0)])
    windows = hybrid_transcription.alignment_windows(spoken)
    assert len(windows) == 1
    assert windows[0]["end"] < 3.0


def test_words_no_sentence_claims_are_kept_rather_than_dropped():
    """Losing speech silently is the defect; the split gives the
    remainder usable windows on its own."""
    spoken = _heard(
        [("one", 0.0, 0.3), ("two", 0.3, 0.6), ("three", 0.6, 0.9)],
        [("one", 0.0, 0.3)])
    assert " ".join(w["text"] for w in
                    hybrid_transcription.alignment_windows(spoken)) == \
        "one two three"


def test_a_transcription_with_no_sentences_still_windows():
    spoken = _heard([("a", 0.0, 0.3), ("b", 0.3, 0.6)])
    assert len(hybrid_transcription.alignment_windows(spoken)) == 1


def test_the_recorded_payload_windows_without_the_transcriber_installed():
    """The one end-to-end window build in this file, on real recorded
    output rather than a hand-built one."""
    spoken = heard_speech.read_payload(
        json.loads(RECORDED.read_text(encoding="utf-8")), "reel26.mp4")
    windows = hybrid_transcription.alignment_windows(spoken)
    assert windows
    assert all(w["end"] > w["start"] for w in windows)
    assert all(w["text"].strip() for w in windows)
    # Every word survives into some window's text: the seam may re-cut
    # what the transcriber said, never drop any of it.
    assert (sum(len(w["text"].split()) for w in windows)
            == len(spoken.words))


# ── 2. Every trigger can fire ────────────────────────────────────────
#
# A gate that cannot fail reads as coverage and is worse than no gate
# (AGENTS.md 10.4). One test per trigger, and `TRIGGERS` is asserted
# complete against them.

def test_an_uncovered_language_falls_back(monkeypatch):
    monkeypatch.setattr(heard_speech, "identify_language",
                        lambda path, **kw: heard_speech.HeardLanguage("xh",
                                                                      0.99))
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        hybrid_transcription.transcribe_and_align(
            "audio.wav", _aligner(_one_word_per_window))
    assert refused.value.reason == hybrid_transcription.LANGUAGE_NOT_COVERED
    assert "xh" in refused.value.detail


def test_a_transcriber_that_is_not_installed_falls_back(monkeypatch):
    def _absent(path, **kw):
        raise heard_speech.TranscriberUnavailable("not on PATH")

    monkeypatch.setattr(heard_speech, "identify_language", _absent)
    monkeypatch.setattr(heard_speech, "executable", lambda: None)
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        hybrid_transcription.transcribe_and_align(
            "audio.wav", _aligner(_one_word_per_window))
    assert refused.value.reason == hybrid_transcription.TRANSCRIBER_UNAVAILABLE


def test_a_transcriber_that_refused_the_file_says_THAT(monkeypatch):
    """Measured by running the seam on six seconds of silence: the
    transcriber exits 1 with `no speech found`, and a record calling
    that "unavailable" sends a reader to check their PATH for a machine
    that is set up correctly."""
    def _refused(path, **kw):
        raise heard_speech.TranscriberUnavailable(
            "da voz exited 1: Error: no speech found in silence.wav")

    monkeypatch.setattr(heard_speech, "identify_language",
                        lambda path, **kw: heard_speech.HeardLanguage("en", 0.4))
    monkeypatch.setattr(heard_speech, "transcribe", _refused)
    monkeypatch.setattr(heard_speech, "executable", lambda: "/usr/local/bin/da")
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        hybrid_transcription.transcribe_and_align(
            "audio.wav", _aligner(_one_word_per_window))
    assert refused.value.reason == hybrid_transcription.TRANSCRIBER_REFUSED
    assert "no speech found" in refused.value.detail


def test_hearing_nothing_falls_back_rather_than_reporting_silence(monkeypatch):
    monkeypatch.setattr(heard_speech, "identify_language",
                        lambda path, **kw: heard_speech.HeardLanguage("en", 1.0))
    monkeypatch.setattr(heard_speech, "transcribe",
                        lambda path, **kw: _heard([]))
    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        hybrid_transcription.transcribe_and_align(
            "audio.wav", _aligner(_one_word_per_window))
    assert refused.value.reason == hybrid_transcription.HEARD_NOTHING


def test_a_window_that_aligned_to_no_words_falls_back(monkeypatch):
    """`whisperx.align` logs `backtrack failed, resorting to original`
    and emits an empty word list. Measured at 0 over 150.7 minutes under
    this window, so one is enough."""
    monkeypatch.setattr(heard_speech, "identify_language",
                        lambda path, **kw: heard_speech.HeardLanguage("en", 1.0))
    monkeypatch.setattr(
        heard_speech, "transcribe",
        lambda path, **kw: _heard([("Yeah.", 5.0, 5.16)],
                                  [("Yeah.", 5.0, 5.16)]))

    def _backtrack_failed(windows, language, audio_path):
        return {"segments": [{"start": w["start"], "end": w["end"],
                              "text": w["text"], "words": []}
                             for w in windows]}

    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        hybrid_transcription.transcribe_and_align(
            "audio.wav", _aligner(_backtrack_failed))
    assert (refused.value.reason
            == hybrid_transcription.SEGMENT_PRODUCED_NO_WORDS)
    assert "Yeah." in refused.value.detail


def test_a_word_still_over_the_clamp_after_alignment_falls_back(monkeypatch):
    monkeypatch.setattr(heard_speech, "identify_language",
                        lambda path, **kw: heard_speech.HeardLanguage("en", 1.0))
    monkeypatch.setattr(
        heard_speech, "transcribe",
        lambda path, **kw: _heard([("starting", 1.0, 1.4)],
                                  [("starting", 1.0, 1.4)]))

    def _stretched(windows, language, audio_path):
        return {"segments": [{"start": 1.0, "end": 64.0, "text": "starting",
                              "words": [{"word": "starting", "start": 1.0,
                                         "end": 63.6, "score": 0.2}]}]}

    with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
        hybrid_transcription.transcribe_and_align(
            "audio.wav", _aligner(_stretched))
    assert refused.value.reason == hybrid_transcription.WORD_OVER_THE_CLAMP


def test_every_declared_trigger_is_proved_by_a_test_in_this_file():
    """The enumeration and the coverage are checked against each other,
    so a trigger added without a test that fires it fails here."""
    body = Path(__file__).read_text(encoding="utf-8")
    for trigger in hybrid_transcription.TRIGGERS:
        constant = trigger.upper()
        assert f"hybrid_transcription.{constant}" in body, (
            f"{trigger!r} is declared in TRIGGERS and no test in this "
            f"file asserts it fires. A gate that cannot fail reads as "
            f"coverage (AGENTS.md 10.4).")


# ── 3. The clean pass, and what it records ───────────────────────────

def _clean(monkeypatch):
    monkeypatch.setattr(heard_speech, "identify_language",
                        lambda path, **kw: heard_speech.HeardLanguage("en", 0.98))
    monkeypatch.setattr(
        heard_speech, "transcribe",
        lambda path, **kw: heard_speech.HeardSpeech(
            words=[heard_speech.HeardWord("What", 0.0, 0.3),
                   heard_speech.HeardWord("changed", 0.3, 0.8)],
            sentences=[heard_speech.HeardSentence("What changed", 0.0, 0.8)],
            text="What changed",
            engine={"transcriber": "da", "version": "0.1.1"}))


def test_a_clean_pass_returns_the_aligners_own_document(monkeypatch):
    _clean(monkeypatch)
    result = hybrid_transcription.transcribe_and_align(
        "audio.wav", _aligner(_one_word_per_window))
    assert [s["text"] for s in result.aligned["segments"]] == ["What changed"]


def test_the_record_says_which_transcriber_and_which_window(monkeypatch):
    _clean(monkeypatch)
    record = hybrid_transcription.transcribe_and_align(
        "audio.wav", _aligner(_one_word_per_window)).record
    assert record["arm"] == hybrid_transcription.ARM_HYBRID
    assert record["transcriber"]["version"] == "0.1.1"
    assert record["language"] == {"language": "en", "confidence": 0.98}
    assert record["alignment_window"] == {
        "silence_split_seconds": hybrid_transcription.SILENCE_SPLIT_SECONDS,
        "pad_seconds": hybrid_transcription.WINDOW_PAD_SECONDS,
        "windows": 1,
        # 0.0..0.95: the head pad is clipped at the file's start.
        "covered_seconds": pytest.approx(0.95),
    }


def test_the_record_states_the_confidence_is_absent_rather_than_omitting_it(
        monkeypatch):
    """A null on a confidence field reads as 'nobody doubted this line'.
    That is the hole Reel 26's defect hid in and it is not rebuilt."""
    _clean(monkeypatch)
    record = hybrid_transcription.transcribe_and_align(
        "audio.wav", _aligner(_one_word_per_window)).record
    assert (record["asr_confidence"]
            == hybrid_transcription.ASR_CONFIDENCE_ABSENT)
    assert record["asr_confidence"] is not None


def test_the_aligner_is_asked_for_the_language_the_identifier_heard(
        monkeypatch):
    """Nothing in the transcriber's JSON names a language, so the align
    model would otherwise be chosen by assumption."""
    _clean(monkeypatch)
    asked = {}

    def _remember(windows, language, audio_path):
        asked["language"] = language
        return _one_word_per_window(windows, language, audio_path)

    hybrid_transcription.transcribe_and_align("audio.wav", _aligner(_remember))
    assert asked["language"] == "en"


def test_a_fallback_record_names_the_trigger_and_what_was_measured():
    failure = hybrid_transcription.FallbackRequired(
        hybrid_transcription.WORD_OVER_THE_CLAMP, "'starting' spans 62.63s")
    record = hybrid_transcription.fallback_record(failure, attempted="craig.wav")
    assert record["arm"] == hybrid_transcription.ARM_WHISPERX
    assert record["fell_back_because"]["trigger"] == \
        hybrid_transcription.WORD_OVER_THE_CLAMP
    assert "62.63s" in record["fell_back_because"]["detail"]
    assert record["attempted_on"] == "craig.wav"
