"""Unit tests for `library/tools/parakeet_asr.py`.

NeMo is never imported here: the module's seams take a fake model and a
fake hypothesis shaped like NeMo's timestamped output, and refusal paths
are exercised by pointing at absent files.
"""
import os

import pytest

from library.tools import heard_speech, parakeet_asr


class _Hypothesis:
    def __init__(self, text, words):
        self.text = text
        self.timestamp = {"word": words}


class _Model:
    def __init__(self, hypothesis):
        self._hypothesis = hypothesis

    def transcribe(self, paths, timestamps=False):
        assert timestamps is True
        return [self._hypothesis]


def _words():
    return [
        {"word": "Let", "start": 0.72, "end": 0.80},
        {"word": "me", "start": 0.80, "end": 0.88},
        {"word": "", "start": 0.88, "end": 0.90},
        {"word": "dropped-no-end", "start": 0.90},
        {"word": "backwards", "start": 1.2, "end": 1.2},
        {"word": "do", "start": 0.96, "end": 1.04},
    ]


def test_words_from_hypothesis_keeps_timed_words_sorted():
    rows = parakeet_asr._words_from_hypothesis(
        _Hypothesis("Let me do", _words()))
    assert [(r["word"], r["start"]) for r in rows] == [
        ("Let", 0.72), ("me", 0.80), ("do", 0.96)]


def test_words_from_hypothesis_without_word_segments_is_empty():
    assert parakeet_asr._words_from_hypothesis(_Hypothesis("hi", None)) == []
    assert parakeet_asr._words_from_hypothesis(object()) == []


def test_transcribe_maps_onto_heard_speech(monkeypatch, tmp_path):
    wav = tmp_path / "window.wav"
    wav.write_bytes(b"\0" * 44)
    model_file = tmp_path / "model.nemo"
    model_file.write_bytes(b"fake")
    monkeypatch.setattr(parakeet_asr, "_require_nemo", lambda: object())
    monkeypatch.setattr(parakeet_asr, "_load_model",
                        lambda nemo_asr, path: _Model(
                            _Hypothesis("Let me do", _words())))
    heard = parakeet_asr.transcribe(str(wav), model_path=str(model_file),
                                    checkpoint_revision="rev-sha",
                                    detected_language="en")
    assert isinstance(heard, heard_speech.HeardSpeech)
    assert [w.word for w in heard.words] == ["Let", "me", "do"]
    assert heard.engine["transcriber"] == parakeet_asr.TRANSCRIBER_NAME
    assert heard.engine["checkpoint"] == parakeet_asr.CHECKPOINT_FILENAME
    assert heard.engine["checkpoint_revision"] == "rev-sha"
    assert heard.engine["precision"] == "float32"
    assert heard.engine["detected_language"] == "en"
    assert heard.anomalies == []


@pytest.mark.serial("fake checkpoint-load seam; no model weights loaded")
def test_load_model_keeps_one_checkpoint_resident(monkeypatch, tmp_path):
    model_file = tmp_path / "model.nemo"
    model_file.write_bytes(b"fake")
    sentinel = object()
    calls = []
    monkeypatch.setattr(parakeet_asr, "_require_nemo", lambda: object())
    monkeypatch.setattr(parakeet_asr, "_load_model",
                        lambda nemo, path: calls.append(path) or sentinel)
    resident_model = getattr(parakeet_asr, "load_model")
    first = resident_model(str(model_file))
    second = resident_model(str(model_file))
    assert first is second is sentinel
    assert calls == [str(model_file.resolve())]


def test_transcribe_refuses_an_absent_window(tmp_path):
    with pytest.raises(parakeet_asr.ParakeetUnavailable):
        parakeet_asr.transcribe(str(tmp_path / "missing.wav"),
                                model_path=str(tmp_path / "m.nemo"))


def test_transcribe_refuses_an_absent_checkpoint(tmp_path):
    wav = tmp_path / "window.wav"
    wav.write_bytes(b"\0" * 44)
    with pytest.raises(parakeet_asr.ParakeetUnavailable):
        parakeet_asr.transcribe(str(wav),
                                model_path=str(tmp_path / "missing.nemo"))


def test_transcribe_refuses_without_nemo(monkeypatch, tmp_path):
    wav = tmp_path / "window.wav"
    wav.write_bytes(b"\0" * 44)
    model_file = tmp_path / "model.nemo"
    model_file.write_bytes(b"fake")

    def _no_nemo():
        raise parakeet_asr.ParakeetUnavailable("no nemo here")

    monkeypatch.setattr(parakeet_asr, "_require_nemo", _no_nemo)
    with pytest.raises(parakeet_asr.ParakeetUnavailable):
        parakeet_asr.transcribe(str(wav), model_path=str(model_file),
                                detected_language="en")
    assert os.path.basename(parakeet_asr.CHECKPOINT_FILENAME).endswith(".nemo")


def test_transcribe_refuses_unknown_or_unsupported_language(
        monkeypatch, tmp_path):
    wav = tmp_path / "window.wav"
    wav.write_bytes(b"\0" * 44)
    model_file = tmp_path / "model.nemo"
    model_file.write_bytes(b"fake")

    def _must_not_load(*_args):
        raise AssertionError("unsupported language must refuse before load")

    monkeypatch.setattr(parakeet_asr, "_load_model", _must_not_load)
    for language in ("", "ja"):
        with pytest.raises(parakeet_asr.ParakeetUnavailable,
                           match="unsupported or unknown"):
            parakeet_asr.transcribe(str(wav), model_path=str(model_file),
                                    detected_language=language)
