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
    # The sweep's finding: at the default a 1.4s gap splits (four
    # over-long words survive at 2.0s, none at 1.0s); at 2.0s it would not.
    spoken = _heard(
        [("one", 0.0, 0.3), ("two", 1.7, 2.0)],
        [("one two", 0.0, 2.0)])
    assert len(hybrid_transcription.alignment_windows(spoken)) == 2
    assert len(hybrid_transcription.alignment_windows(
        spoken, silence_split=2.0)) == 1


def test_every_window_is_padded_at_both_ends():
    """At pad zero, 8 one-word backchannels failed alignment outright
    and were LOST. Any pad at all recovered all 8."""
    spoken = _heard([("Yeah.", 5.0, 5.16)], [("Yeah.", 5.0, 5.16)])
    window = hybrid_transcription.alignment_windows(spoken)[0]
    pad = hybrid_transcription.WINDOW_PAD_SECONDS
    assert pad > 0
    assert window["start"] == pytest.approx(5.0 - pad)
    assert window["end"] == pytest.approx(5.16 + pad)
    # ...and never before the file does.
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
    # A transcription with no sentences at all still windows.
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

def _backtrack_failed(windows, language, audio_path):
    """`whisperx.align`'s `backtrack failed`: an empty word list."""
    return {"segments": [{"start": w["start"], "end": w["end"],
                          "text": w["text"], "words": []}
                         for w in windows]}


def _absent(path, **kw):
    raise heard_speech.TranscriberUnavailable("not on PATH")


def _refused(path, **kw):
    # Measured on six seconds of silence: exit 1, `no speech found`.
    # Calling that "unavailable" sends a reader to check their PATH.
    raise heard_speech.TranscriberUnavailable(
        "da voz exited 1: Error: no speech found in silence.wav")


def _says(language, confidence=1.0):
    return lambda path, **kw: heard_speech.HeardLanguage(language, confidence)


def _hears(*words):
    return lambda path, **kw: _heard(list(words), list(words))


# (identify_language, transcribe, executable, aligner, trigger, in detail)
TRIGGER_CASES = [
    (_says("xh", 0.99), _hears(("bonjour", 5.0, 5.5)), None,
     _one_word_per_window, hybrid_transcription.LANGUAGE_NOT_COVERED, "xh"),
    (_absent, None, lambda: None, _one_word_per_window,
     hybrid_transcription.TRANSCRIBER_UNAVAILABLE, None),
    (_says("en", 0.4), _refused, lambda: "/usr/local/bin/da",
     _one_word_per_window, hybrid_transcription.TRANSCRIBER_REFUSED,
     "no speech found"),
    (_says("en"), lambda path, **kw: _heard([]), None,
     _one_word_per_window, hybrid_transcription.HEARD_NOTHING, None),
    (_says("en"), _hears(("Yeah.", 5.0, 5.16)), None, _backtrack_failed,
     hybrid_transcription.SEGMENT_PRODUCED_NO_WORDS, "Yeah."),
]


def test_every_trigger_refuses_by_name(monkeypatch):
    """One row per trigger; each one really fires, and says why."""
    for identify, transcribe, executable, align, trigger, said in \
            TRIGGER_CASES:
        monkeypatch.setattr(heard_speech, "identify_language", identify)
        if transcribe is not None:
            monkeypatch.setattr(heard_speech, "transcribe", transcribe)
        if executable is not None:
            monkeypatch.setattr(heard_speech, "executable", executable)
        with pytest.raises(hybrid_transcription.FallbackRequired) as refused:
            hybrid_transcription.transcribe_and_align(
                "audio.wav", _aligner(align))
        assert refused.value.reason == trigger
        if said:
            assert said in refused.value.detail, trigger


def test_2026_word_over_the_clamp_is_sanitized_without_losing_speech(
        monkeypatch):
    monkeypatch.setattr(heard_speech, "identify_language",
                        lambda path, **kw: heard_speech.HeardLanguage("en", 1.0))
    monkeypatch.setattr(
        heard_speech, "transcribe",
        lambda path, **kw: _heard(
            [("today", 1.0, 1.3), ("is", 1.31, 1.45),
             ("march", 1.46, 1.9), ("25th,", 1.91, 2.15),
             ("2026.", 2.2, 2.5)],
            [("today is march 25th, 2026.", 1.0, 2.5)]))

    def _stretched(windows, language, audio_path):
        words = [
            {"word": "today", "start": 1.0, "end": 1.3},
            {"word": "is", "start": 1.31, "end": 1.45},
            {"word": "march", "start": 1.46, "end": 1.9},
            {"word": "25th,", "start": 1.91, "end": 2.15},
            {"word": "2026.", "start": 2.2, "end": 4.87},
        ]
        return {"segments": [{"start": 1.0, "end": 4.87,
                              "text": "today is march 25th, 2026.",
                              "words": words}]}

    result = hybrid_transcription.transcribe_and_align(
        "audio.wav", _aligner(_stretched))
    words = result.aligned["segments"][0]["words"]
    assert words[-1]["word"] == "2026."
    assert words[-1]["end"] == pytest.approx(2.5)
    assert words[-1]["end"] - words[-1]["start"] <= \
        hybrid_transcription.MAX_WORD_SECONDS


def test_music_leadin_cannot_make_english_speech_language_not_covered(
        monkeypatch):
    """A full-file language guess can sample a silent or music lead-in.
    Probe a sustained word window so a short lead-in cannot label speech
    English as Nynorsk (nn).
    """
    spoken = _heard(
        [("Today", 18.0, 18.3), ("is", 18.31, 18.45),
         ("march", 18.46, 18.9), ("2026.", 18.91, 19.25)],
        [("Today is march 2026.", 18.0, 19.25)])
    monkeypatch.setattr(heard_speech, "transcribe",
                        lambda path, **kw: spoken)
    probes = []

    def _speech_window(path, **kwargs):
        probes.append((path, kwargs))
        return heard_speech.HeardLanguage("en", 0.99)

    monkeypatch.setattr(heard_speech, "identify_language", _speech_window)
    asked = {}

    def _remember(windows, language, audio_path):
        asked["language"] = language
        return _one_word_per_window(windows, language, audio_path)

    result = hybrid_transcription.transcribe_and_align(
        "audio.wav", _aligner(_remember))

    assert asked["language"] == "en"
    assert probes == [("audio.wav", {
        "sample_start_seconds": pytest.approx(17.85),
        "sample_duration_seconds": pytest.approx(1.55),
    })]
    assert result.record["language"]["language"] == "en"


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


def test_a_clean_pass_returns_the_aligners_document_and_a_full_record(
        monkeypatch):
    _clean(monkeypatch)
    result = hybrid_transcription.transcribe_and_align(
        "audio.wav", _aligner(_one_word_per_window))
    assert [s["text"] for s in result.aligned["segments"]] == ["What changed"]
    record = result.record
    # A null confidence reads as "nobody doubted this line" - the hole
    # Reel 26's defect hid in - so the absence is STATED.
    assert (record["asr_confidence"]
            == hybrid_transcription.ASR_CONFIDENCE_ABSENT)
    assert record["arm"] == hybrid_transcription.ARM_HYBRID
    assert record["transcriber"]["version"] == "0.1.1"
    assert record["language"] == {"language": "en", "confidence": 0.98}
    assert record["alignment_window"] == {
        "silence_split_seconds": hybrid_transcription.SILENCE_SPLIT_SECONDS,
        "pad_seconds": hybrid_transcription.WINDOW_PAD_SECONDS,
        "windows": 1,
        # 0.0..0.95: the head pad is clipped at the file's start.
        "covered_seconds": pytest.approx(0.95),
        # A clean run places every boundary with MFA: nothing keeps
        # the transcriber's own timings.
        "transcriber_timed_windows": 0,
        "transcriber_timed_words": 0,
    }


def test_transcriber_timed_words_are_counted_on_the_record(monkeypatch):
    """A window MFA could not align keeps voz's timings marked
    `timing_source: "transcriber"` (the LCATL0013 shape, 2026-10-01):
    the pass succeeds and the record counts how many windows and words
    MFA never placed, because the two timings are different
    measurements."""
    monkeypatch.setattr(heard_speech, "identify_language",
                        lambda path, **kw: heard_speech.HeardLanguage("en", 1.0))
    monkeypatch.setattr(
        heard_speech, "transcribe",
        lambda path, **kw: _heard(
            [("Craig", 1.0, 1.2), ("nods", 1.2, 1.5),
             ("Yeah.", 5.0, 5.16)],
            [("Craig nods", 1.0, 1.5), ("Yeah.", 5.0, 5.16)]))

    def _one_fallback_window(windows, language, audio_path):
        segments = []
        for window in windows:
            if window["text"] == "Yeah.":
                words = [{"word": w["word"], "start": w["start"],
                          "end": w["end"], "timed": True,
                          "timing_source":
                          hybrid_transcription.TRANSCRIBER_TIMING_SOURCE}
                         for w in window["source_words"]]
            else:
                words = [{"word": token, "start": window["start"],
                          "end": window["end"]}
                         for token in window["text"].split()]
            segments.append({"start": window["start"],
                             "end": window["end"],
                             "text": window["text"], "words": words})
        return {"segments": segments}

    result = hybrid_transcription.transcribe_and_align(
        "audio.wav", _aligner(_one_fallback_window))
    assert len(result.aligned["segments"]) == 2
    assert result.record["alignment_window"]["transcriber_timed_windows"] == 1
    assert result.record["alignment_window"]["transcriber_timed_words"] == 1
