"""Ingest transcribes through the reel path's seam and says which instrument answered.

Finding 6 of the fidelity audit: step 1.04 transcribed with its own
WhisperX plus wav2vec2 call while the reel path had moved to Voz plus
MFA, and ingest records carried no `aligner` field at all
(`docs/MFA_ADOPTION_VERIFIED.md:80-93`) - so nothing could say which
words were timed by what. This step now calls the same
`timeline_transcript.transcribe_audio` seam the reels use, stamps
every index with the arm and aligner that answered, and serves a
pre-stamp index as the legacy WhisperX instrument it was - an
explicit `ren reindex` moves a project, never an unasked
re-transcription. Only an index nothing transcribed is re-indexed.

Nothing here runs a transcriber, an aligner, or reaches a real
project: the seam is stubbed, the cache is a tmp file.
"""

import json
import os

import pytest

from library.steps.step_1_04_temporal_index import step as temporal_index
from library.tools import hybrid_transcription


def _seam(monkeypatch, aligned, record):
    import library.tools.timeline_transcript as tt

    monkeypatch.setattr(
        tt, "transcribe_audio", lambda *a, **k: (aligned, record))


def _hybrid_mfa():
    return (
        {"segments": [{
            "start": 0.35, "end": 1.55, "text": "Absolutely.",
            "words": [{"word": "Absolutely.", "start": 0.5, "end": 1.4}],
        }]},
        {"arm": hybrid_transcription.ARM_HYBRID,
         "aligner": hybrid_transcription.ALIGNER_MFA,
         "language": {"language": "en", "confidence": 0.98}},
    )


def test_ingest_regions_carry_the_hybrid_arm_and_aligner(monkeypatch):
    """The seam's MFA record reaches the regions: before this change
    ingest records carried no aligner field at all, so an MFA word
    and a wav2vec2 word read identically."""
    _seam(monkeypatch, *_hybrid_mfa())

    regions, transcription = temporal_index.detect_speech_regions(
        "/tmp/clip.wav", "/tmp", onsets=[], language="en")

    assert transcription["arm"] == hybrid_transcription.ARM_HYBRID
    assert transcription["aligner"] == hybrid_transcription.ALIGNER_MFA
    assert transcription["detected_language"] == "en"
    assert regions[0]["method"] == "hybrid-mfa"
    assert regions[0]["words"][0] == {
        "word": "absolutely.", "start": 0.5, "end": 1.4}


def test_ingest_legacy_whisperx_words_keep_their_method(monkeypatch):
    """Full-WhisperX words must not wear the hybrid's method: a run
    that fell back and a run that did not are different instruments,
    and the audit's numbers only mean anything per instrument. The
    record below is legacy vocabulary - no transcript written since
    2026-09-24 carries the whisperx arm, but old indexes still map.
    """
    _seam(monkeypatch,
          {"segments": [{
              "start": 0.35, "end": 1.55, "text": "Absolutely.",
              "words": [{"word": "Absolutely.", "start": 0.55,
                         "end": 1.45}],
              "avg_logprob": -0.08}]},
          {"arm": hybrid_transcription.ARM_WHISPERX,
           "aligner": hybrid_transcription.ALIGNER_WAV2VEC2,
           "language": {"language": "en", "confidence": 0.98},
           "fell_back_because": {"trigger": "heard_nothing",
                                 "detail": "no words"}})

    regions, transcription = temporal_index.detect_speech_regions(
        "/tmp/clip.wav", "/tmp", onsets=[], language="en")

    assert transcription["arm"] == hybrid_transcription.ARM_WHISPERX
    assert transcription["aligner"] == hybrid_transcription.ALIGNER_WAV2VEC2
    assert regions[0]["method"] == "whisperx-wav2vec2-large-v3"
    assert regions[0]["confidence"] == -0.08


def test_ingest_silence_keeps_its_none_stamp(monkeypatch):
    """The seam heard nothing and said so: the index must keep the
    "none" stamp, not let the legacy whisperx defaults rewrite it
    into an instrument that never ran."""
    import library.tools.timeline_transcript as tt

    monkeypatch.setattr(
        tt, "transcribe_audio",
        lambda *a, **k: (
            {"segments": []},
            hybrid_transcription.fallback_record(
                hybrid_transcription.FallbackRequired(
                    hybrid_transcription.HEARD_NOTHING,
                    "the transcriber returned no words"),
                attempted="clip.wav")))

    regions, transcription = temporal_index.detect_speech_regions(
        "/tmp/clip.wav", "/tmp", onsets=[], language="en")

    assert regions == []
    assert transcription["arm"] == temporal_index.UNTRANSCRIBED
    assert transcription["aligner"] == temporal_index.UNTRANSCRIBED


def test_ingest_failure_is_empty_with_an_honest_stamp(monkeypatch):
    """Nothing transcribed is `[]` with the arm as `"none"` - an
    empty region list is not a measurement of silence, and a null
    stamp would read as "timed by the default"."""
    import library.tools.timeline_transcript as tt

    monkeypatch.setattr(
        tt, "transcribe_audio",
        lambda *a, **k: (_ for _ in ()).throw(ImportError("no transcriber")))

    regions, transcription = temporal_index.detect_speech_regions(
        "/tmp/clip.wav", "/tmp", onsets=[], language="en")

    assert regions == []
    assert transcription["arm"] == temporal_index.UNTRANSCRIBED
    assert transcription["aligner"] == temporal_index.UNTRANSCRIBED


def _cached_index(tmp_path, **overrides):
    document = {
        "clip_id": "clip_001",
        "transcription_language": "en",
        "scene_boundaries": [], "speech_regions": [],
        "energy_curve": {}, "audio_events": [], "motion_energy": {},
    }
    document.update(overrides)
    path = tmp_path / "clip_001.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def test_a_pre_stamp_cache_is_served_as_legacy_not_retranscribed(tmp_path):
    """Every index written before this change was timed by the old
    WhisperX path - an instrument, not nothing. Serving it stamped
    as legacy keeps the run from silently re-transcribing whole
    projects; `ren reindex` moves them when asked."""
    path = _cached_index(tmp_path)

    index = temporal_index._load_cached_index(
        path, expected_language="en")

    assert index is not None
    assert index["transcription_arm"] == temporal_index.LEGACY_ARM
    assert index["transcription_aligner"] == (
        temporal_index.LEGACY_ALIGNER)
    assert index["detected_language"] == "en"
    # Stamped on read: the next read is stable, not a re-discovery.
    again = temporal_index._load_cached_index(
        path, expected_language="en")
    assert again["transcription_aligner"] == "wav2vec2"


def test_an_untranscribed_cache_is_retried_not_pinned(tmp_path):
    """A clip nothing could transcribe stamps `"none"`; serving that
    as current would pin empty words forever, because the stamp
    never changes on its own."""
    path = _cached_index(
        tmp_path, transcription_aligner=temporal_index.UNTRANSCRIBED)

    assert temporal_index._load_cached_index(
        path, expected_language="en") is None


def test_a_stamped_cache_still_hits(tmp_path):
    """The refusal above must not become a gate that fails correct
    output: an MFA-timed index in the right language is reused."""
    regions = [{"start": 0.5, "end": 1.4, "text": "absolutely.",
                "words": [{"word": "absolutely.", "start": 0.5,
                           "end": 1.4}],
                "confidence": 0.0, "method": "hybrid-mfa"}]
    path = _cached_index(
        tmp_path, speech_regions=regions,
        transcription_aligner=hybrid_transcription.ALIGNER_MFA,
        transcription_arm=hybrid_transcription.ARM_HYBRID)

    index = temporal_index._load_cached_index(
        path, expected_language="en")

    assert index is not None
    assert index["speech_regions"] == regions


def _project_with_indexes(tmp_path, documents):
    """A scratch project folder whose temporal-index area holds
    `documents` ({basename: document}). Real layout, tmp root -
    never a real project."""
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(str(tmp_path))
    index_dir = layout.write_dir(Area.TEMPORAL_INDEX,
                                 step="temporal_index")
    for name, document in documents.items():
        with open(os.path.join(str(index_dir), name), "w",
                  encoding="utf-8") as handle:
            json.dump(document, handle)
    return str(tmp_path)


def _legacy_document():
    return {"clip_id": "clip_001", "transcription_language": "en",
            "speech_regions": []}


def _current_document():
    return {"clip_id": "clip_002", "transcription_language": "en",
            "transcription_arm": hybrid_transcription.ARM_HYBRID,
            "transcription_aligner": hybrid_transcription.ALIGNER_MFA,
            "speech_regions": []}


def test_reindex_invalidates_only_what_no_current_instrument_timed(tmp_path):
    """`ren reindex` moves exactly the legacy, untranscribed and
    corrupt files - a current MFA index survives, and a foreign
    file is left alone rather than deleted by a cache verb."""
    root = _project_with_indexes(tmp_path, {
        "clip_001.json": _legacy_document(),
        "clip_002.json": _current_document(),
        "clip_003.json": {"clip_id": "clip_003",
                          "transcription_aligner": "none",
                          "speech_regions": []},
        "notes.json": {"not": "an index"},
    })
    with open(os.path.join(
            root, "pipeline_output", "steps", "1_04_temporal_index",
            "index", "clip_004.json"), "w", encoding="utf-8") as handle:
        handle.write("{corrupt")

    report = temporal_index.invalidate_legacy_indexes(root)

    assert sorted(report["invalidated"]) == [
        "clip_001.json", "clip_003.json", "clip_004.json"]
    assert report["current"] == ["clip_002.json"]
    assert report["unrecognized"] == ["notes.json"]


def test_speech_window_language_avoids_language_not_covered_for_batch(
        monkeypatch, tmp_path):
    """Batching is the cost fix: two clips' windows align in ONE
    call, and every word comes back on its own clip's clock. Language
    detection samples those word windows rather than the file lead-in,
    so music cannot choose an uncovered MFA language."""
    from library.tools import heard_speech

    language_probes = []

    def _identify_speech(path, **kwargs):
        language_probes.append((path, kwargs))
        return heard_speech.HeardLanguage("en", 0.98)

    monkeypatch.setattr(heard_speech, "identify_language", _identify_speech)
    monkeypatch.setattr(
        heard_speech, "transcribe",
        lambda path, **kw: heard_speech.HeardSpeech(
            words=[heard_speech.HeardWord("Hi.", 0.5, 0.9)],
            sentences=[heard_speech.HeardSentence("Hi.", 0.5, 0.9)],
            text="Hi.",
            engine={"transcriber": "da", "version": "0.1.1"}))

    import library.tools.timeline_transcript as tt

    calls = []

    def _one_align(segments, language, audio_path):
        calls.append((len(segments), language))
        out = []
        for segment in segments:
            out.append({
                "start": segment["start"] + 0.05,
                "end": segment["end"] - 0.05,
                "text": segment["text"],
                "words": [{"word": "Hi.",
                           "start": segment["start"] + 0.05,
                           "end": segment["end"] - 0.05}],
            })
        return {"segments": out,
                "aligner": hybrid_transcription.ALIGNER_MFA}

    monkeypatch.setattr(
        tt, "_aligner",
        lambda: hybrid_transcription.Aligner(
            covers=lambda language: True, align=_one_align))

    import wave
    audios = []
    for name in ("a.wav", "b.wav"):
        path = str(tmp_path / name)
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)
        audios.append(path)

    results = temporal_index.transcribe_clips_batched(
        [{"key": "clip_001", "audio_path": audios[0]},
         {"key": "clip_002", "audio_path": audios[1]}],
        language="en")

    assert len(calls) == 1
    assert calls[0] == (2, "en")
    assert [path for path, _ in language_probes] == audios
    for _path, probe in language_probes:
        assert probe["sample_start_seconds"] == pytest.approx(0.35)
        assert probe["sample_duration_seconds"] == pytest.approx(0.7)
    first = results["clip_001"][0][0]
    second = results["clip_002"][0][0]
    assert results["clip_001"][1]["aligner"] == "mfa"
    # The concat offset is subtracted: both clips' words sit in the
    # first second, on their own clocks.
    assert first["words"][0]["start"] == second["words"][0]["start"]
    assert first["words"][0]["start"] < 1.0
