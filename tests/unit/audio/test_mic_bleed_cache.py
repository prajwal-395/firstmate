"""The mic-bleed measurement cache: same rows plus same audio, no second read.

`resolve_document_mic_bleed` runs on the build and again on the verify
gate minutes later, over the same transcript rows and the same cached
ISO WAVs. Without a cache the second resolution re-reads every
overlapping window out of the WAVs and re-runs the per-sample RMS
loop; with one it reuses the first run's measurements. A changed row,
rewritten audio or an unreadable cache measures live, so the reuse can
never go stale.

These tests count `_track_rms_dbfs` calls instead of timing anything:
a timing bound would be flaky on a shared machine and would pass
without proving the WAVs went unread.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

from library.tools import timeline_transcript as tt


def _words(text, start):
    step = 2.37 / len(text.split())
    return tuple({"word": word, "start": start + index * step,
                  "end": start + (index + 1) * step, "timed": True}
                 for index, word in enumerate(text.split()))


def _row(speaker, text, start, source):
    return {"speaker": speaker, "text": text,
            "timeline_start": start, "timeline_end": start + 2.37,
            "source_file": source, "source_start": 2716.0,
            "source_end": 2718.37, "resolve_item_id": f"{speaker}-clip",
            "words": list(_words(text, start))}


def _document():
    one = ("Yeah, so AI is actually better for small businesses.")
    two = ("Yeah so AI is actually better for small businesses")
    return {"segments": [_row("SpeakerOne", one, 1.05, "/SpeakerOne.MXF"),
                         _row("SpeakerTwo", two, 1.0, "/SpeakerTwo.MXF")],
            "mic_bleed_resolution": []}


def _write_wav(path: Path, level: int, duration: float = 4.0) -> Path:
    import math
    from array import array

    rate = tt.SAMPLE_RATE
    samples = array("h", [0]) * round(duration * rate)
    for index in range(round(1.0 * rate), round(3.42 * rate)):
        samples[index] = round(
            level * math.sin(2 * math.pi * 440 * index / rate))
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(samples.tobytes())
    return path


def _scratch(tmp_path, one_level=2000, two_level=200):
    scratch = Path(tt.transcript_path(str(tmp_path)).parent)
    scratch.mkdir(parents=True, exist_ok=True)
    _write_wav(scratch / "speakerone.wav", one_level)
    _write_wav(scratch / "speakertwo.wav", two_level)
    return scratch


def _counting_rms(monkeypatch):
    calls = {"count": 0}
    real = tt._track_rms_dbfs

    def counting(path, start, end):
        calls["count"] += 1
        return real(path, start, end)

    monkeypatch.setattr(tt, "_track_rms_dbfs", counting)
    return calls


def test_the_second_resolution_reuses_the_first_runs_measurements(
        tmp_path, monkeypatch):
    _scratch(tmp_path)
    first = tt.resolve_document_mic_bleed(_document(), str(tmp_path))
    assert [decision["status"]
            for decision in first["mic_bleed_resolution"]] == ["dropped"]
    cache = (Path(tt.transcript_path(str(tmp_path)).parent)
             / tt.MIC_BLEED_CACHE_FILENAME)
    assert cache.is_file()

    calls = _counting_rms(monkeypatch)
    second = tt.resolve_document_mic_bleed(_document(), str(tmp_path))
    assert calls["count"] == 0
    assert second["mic_bleed_resolution"] == first["mic_bleed_resolution"]
    assert second["segments"] == first["segments"]


def test_rewritten_audio_is_measured_live_not_reused(
        tmp_path, monkeypatch):
    _scratch(tmp_path)
    first = tt.resolve_document_mic_bleed(_document(), str(tmp_path))
    assert first["mic_bleed_resolution"][0]["winner"] == "SpeakerOne"

    # Both mics now read the same level: no clear winner, so nothing
    # drops - and the cache from the louder run must not leak through.
    _scratch(tmp_path, one_level=1000, two_level=1000)
    calls = _counting_rms(monkeypatch)
    second = tt.resolve_document_mic_bleed(_document(), str(tmp_path))
    assert calls["count"] > 0
    assert second["mic_bleed_resolution"][0]["status"] == "unresolved"
    assert (second["mic_bleed_resolution"][0]["reason"]
            == "level_difference_below_threshold")


def test_a_changed_row_is_measured_live_not_reused(
        tmp_path, monkeypatch):
    _scratch(tmp_path)
    tt.resolve_document_mic_bleed(_document(), str(tmp_path))

    changed = _document()
    changed["segments"][0] = dict(changed["segments"][0],
                                  text="Completely different words here now")
    calls = _counting_rms(monkeypatch)
    tt.resolve_document_mic_bleed(changed, str(tmp_path))
    assert calls["count"] > 0


def test_a_corrupt_cache_reads_as_a_miss(tmp_path, monkeypatch):
    scratch = _scratch(tmp_path)
    (scratch / tt.MIC_BLEED_CACHE_FILENAME).write_text(
        "{not json", encoding="utf-8")
    calls = _counting_rms(monkeypatch)
    resolved = tt.resolve_document_mic_bleed(_document(), str(tmp_path))
    assert calls["count"] > 0
    assert [decision["status"]
            for decision in resolved["mic_bleed_resolution"]] == ["dropped"]
    # The corrupt file is replaced by a working cache for the next run.
    stored = json.loads((scratch / tt.MIC_BLEED_CACHE_FILENAME).read_text(
        encoding="utf-8"))
    assert stored["decisions"]


def test_merge_speakers_reuses_only_the_entries_it_is_given(tmp_path):
    scratch = _scratch(tmp_path)
    duplicate = {"SpeakerTwo": [tt.SpokenSegment(
        speaker="SpeakerTwo",
        text="Yeah so AI is actually better for small businesses",
        timeline_start=1.0, timeline_end=3.37,
        source_file="/SpeakerTwo.MXF", source_start=2716.0,
        source_end=2718.37, resolve_item_id="SpeakerTwo-clip",
        words=_words("Yeah so AI is actually better for small businesses",
                     1.0))],
        "SpeakerOne": [tt.SpokenSegment(
            speaker="SpeakerOne",
            text="Yeah, so AI is actually better for small businesses.",
            timeline_start=1.05, timeline_end=3.42,
            source_file="/SpeakerOne.MXF", source_start=2716.0,
            source_end=2718.37, resolve_item_id="SpeakerOne-clip",
            words=_words(
                "Yeah, so AI is actually better for small businesses.",
                1.05))]}
    tracks = {"SpeakerTwo": scratch / "speakertwo.wav",
              "SpeakerOne": scratch / "speakerone.wav"}
    _, live = tt.merge_speakers(duplicate, tracks)
    assert live and live[0]["status"] == "dropped"
    key = tt._mic_bleed_candidate_key(
        live[0]["start"], live[0]["end"], live[0]["speakers"],
        live[0]["passage"])

    # One entry in, one candidate out: no WAV reads for the reuse, and
    # the adopted reading settles exactly as the live one did.
    import library.tools.timeline_transcript as _tt

    reads = {"count": 0}
    real = _tt._track_rms_dbfs

    def counting(path, start, end):
        reads["count"] += 1
        return real(path, start, end)

    _tt._track_rms_dbfs = counting
    try:
        merged2, reused2 = tt.merge_speakers(
            duplicate, tracks, measured={key: live[0]})
    finally:
        _tt._track_rms_dbfs = real
    assert reads["count"] == 0
    assert reused2 == live
    assert [segment.speaker for segment in merged2] == ["SpeakerOne"]
