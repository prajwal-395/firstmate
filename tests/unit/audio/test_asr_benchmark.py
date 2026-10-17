"""Unit tests for `library/tools/asr_benchmark.py`.

Vendor subcommands are referenced only through `heard_speech`
constants - spelling them as quoted literals anywhere outside the
containment fails `tests/contracts/test_heard_speech.py`. Model
runtimes are faked; no audio, no subprocess, no download here.
"""
import json

import pytest

from library.tools import asr_benchmark, heard_speech, parakeet_asr
from library.tools.evaluation_scorecard import build_scorecard


def _heard(words=((0.0, 0.3),), text="hi"):
    return heard_speech.HeardSpeech(
        words=[heard_speech.HeardWord(word=text, start=s, end=e)
               for (s, e) in words],
        text=text,
        engine={"load_seconds": 1.0, "processing_seconds": 0.1,
                "duration_seconds": 1.0})


def _window(window_id="w1"):
    return asr_benchmark.Window(
        window_id=window_id, source_id="LC4931", split="calibration",
        source_path="/nonexistent.MXF", source_sha256="0" * 64,
        start_seconds=120.0, end_seconds=180.0,
        wav_path="/nonexistent.wav", wav_sha256="1" * 64)


def test_manifest_refuses_held_out_rows(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps([
        {"window_id": "h1", "source_id": "LC4930", "split": "held_out",
         "source_path": "x", "start_seconds": 0, "end_seconds": 10}]))
    with pytest.raises(asr_benchmark.BenchmarkError):
        asr_benchmark.load_manifest(str(manifest))


def test_manifest_accepts_held_out_only_with_explicit_opt_in(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps([
        {"window_id": "h1", "source_id": "LC4930", "split": "held_out",
         "source_path": "x", "start_seconds": 0, "end_seconds": 10}]))
    rows = asr_benchmark.load_manifest(str(manifest), allow_held_out=True)
    assert rows[0]["split"] == "held_out"


def test_held_out_single_report_refuses_mixed_manifest_before_extraction(
        monkeypatch, tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps([
        {"window_id": "h1", "source_id": "LC4930", "split": "held_out",
         "source_path": "x", "start_seconds": 0, "end_seconds": 10},
        {"window_id": "c1", "source_id": "LC4931", "split": "calibration",
         "source_path": "x", "start_seconds": 0, "end_seconds": 10}]))
    monkeypatch.setattr("sys.argv", [
        "asr_benchmark", "--manifest", str(manifest),
        "--windows-dir", str(tmp_path / "windows"),
        "--out-dir", str(tmp_path / "output"),
        "--candidates", heard_speech.SUBCOMMAND, "--revision", "one-shot",
        "--held-out-single-report"])
    with pytest.raises(asr_benchmark.BenchmarkError,
                       match="every manifest row to be held-out"):
        asr_benchmark.main()


def test_manifest_refuses_unknown_splits(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps([
        {"window_id": "q1", "source_id": "LC4931", "split": "mystery",
         "source_path": "x", "start_seconds": 0, "end_seconds": 10}]))
    with pytest.raises(asr_benchmark.BenchmarkError):
        asr_benchmark.load_manifest(str(manifest))


def test_source_clock_keeps_window_time_and_adds_source_offsets():
    source = {"words": [{"text": "hello", "start": 0.5, "end": 0.9}],
              "status": "answered"}
    result = asr_benchmark._with_source_clock(source, _window())
    assert result["words"] == [{
        "text": "hello", "start": 0.5, "end": 0.9,
        "source_start": 120.5, "source_end": 120.9}]
    assert result["source_interval"] == {
        "source_id": "LC4931", "start_seconds": 120.0,
        "end_seconds": 180.0,
        "timebase": "original_source_audio_seconds"}
    assert source["words"][0] == {
        "text": "hello", "start": 0.5, "end": 0.9}


def test_parakeet_unknown_language_refuses_before_model_load(monkeypatch):
    def _must_not_load(_path):
        raise AssertionError("language preflight must precede model load")

    monkeypatch.setattr(parakeet_asr, "is_model_loaded", lambda _path: False)
    monkeypatch.setattr(parakeet_asr, "load_model", _must_not_load)
    with pytest.raises(asr_benchmark.BenchmarkError,
                       match="before model load"):
        asr_benchmark.run_parakeet("window.wav", "model.nemo",
                                   detected_language="ja")


def test_voz_run_records_cold_warm_and_split(monkeypatch):
    calls = []

    def _fake(path):
        calls.append(path)
        return _heard()

    monkeypatch.setattr(heard_speech, "transcribe", _fake)
    result = asr_benchmark.run_voz("clip.wav")
    assert calls == ["clip.wav", "clip.wav"]
    assert result["hypothesis"]["words"] == [
        {"text": "hi", "start": 0.0, "end": 0.3}]
    assert result["cold_seconds"] >= 0
    assert result["warm_seconds"] >= 0
    assert result["split"]["load_seconds"] == 1.0
    assert result["warm_word_count"] == 1


def test_ear_and_uhm_runs_record_language_and_events(monkeypatch):
    monkeypatch.setattr(
        heard_speech, "identify_language",
        lambda path: heard_speech.HeardLanguage("en", 0.99))
    ear = asr_benchmark.run_ear("clip.wav")
    assert ear["language"] == {"language": "en", "confidence": 0.99}

    monkeypatch.setattr(
        heard_speech, "detect_fillers",
        lambda path: heard_speech.HeardFillers(
            events=[heard_speech.HeardFiller("um", 1.0, 1.5, 0.8)],
            duration_seconds=60.0))
    uhm = asr_benchmark.run_uhm("clip.wav")
    assert uhm["event_count"] == 1
    assert uhm["events"][0]["type"] == "um"


def test_report_is_a_valid_evaluation_report(tmp_path):
    out = tmp_path / "report.json"
    per_window = [{"cold_seconds": 9.0, "warm_seconds": 1.0,
                   "cold_peak_rss_bytes": 1000,
                   "warm_peak_rss_bytes": 900}]
    report = asr_benchmark.write_report(
        "candidate-under-test", "baseline", {"model": "test-rev"},
        "speech_transcription", "run-1", [_window()], per_window,
        [{"id": "window:w1", "sha256": "1" * 64}], str(out))
    assert report["format"] == "ren_evaluation_report/1"
    assert report["split"] == "calibration"
    assert report["selection_basis"] == ["calibration"]
    assert "wer" not in report["metrics"]
    scorecard = build_scorecard([out])
    assert scorecard["format"] == "ren_intelligence_scorecard/1"
    assert scorecard["versions"]["candidate-under-test"]["role"] == "baseline"


def test_report_identities_keep_incumbent_as_baseline():
    args = type("Args", (), {"parakeet_revision": "r",
                             "parakeet_model": "m"})()
    _voz_id, voz_role, _, _ = asr_benchmark._report_identity(
        heard_speech.SUBCOMMAND, args)
    assert voz_role == "baseline"
    align_id, align_role, _, modality = asr_benchmark._report_identity(
        "align", args)
    assert (align_id, align_role, modality) == (
        "desert-ant-align", "candidate", "speech_alignment")
