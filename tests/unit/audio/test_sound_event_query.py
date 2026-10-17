import numpy as np
import soundfile as sf

from library.tools.analysis import sound_event_query as query
from library.tools.analysis.source_separation import SeparatedSignal


def test_separation_is_triggered_by_low_confidence_query_match():
    events = [{"label": "Door", "start": 1.0, "end": 1.2,
               "confidence": 0.3}]

    assert query.should_separate_for_query("find the door slam", events)
    assert not query.should_separate_for_query("find the glass break", events)
    assert not query.should_separate_for_query("   ", events)


def test_separation_is_triggered_by_measured_masking_overlap():
    events = [
        {"label": "Dog bark", "start": 1.0, "end": 1.5,
         "confidence": 0.9},
        {"label": "Music", "start": 1.2, "end": 2.0,
         "confidence": 0.9},
    ]

    assert query.should_separate_for_query("dog bark", events)
    events[1]["start"] = 1.5
    assert not query.should_separate_for_query("dog bark", events)


def test_query_separation_returns_estimated_candidates_without_replacing_source(
        tmp_path, monkeypatch):
    source_path = tmp_path / "source.wav"
    sf.write(source_path, np.zeros((1600, 2), dtype=np.float32), 16000)
    original = {"method": "panns", "events": [
        {"label": "Door", "start": 0.2, "end": 0.4, "confidence": 0.3}
    ]}
    stem = SeparatedSignal(
        id="sound-query:source:music_candidate",
        kind="music_candidate", source_signal_id="sound-query:source",
        status="estimated", method="demucs-infer/htdemucs",
        model="htdemucs", sample_rate=16000,
        samples=np.zeros((2, 1600), dtype=np.float32),
    )
    monkeypatch.setattr(
        "library.tools.heavy_work_lock.owns", lambda _profile: True
    )
    monkeypatch.setattr(
        "library.tools.analysis.source_separation.separate_waveform",
        lambda *_args, **_kwargs: {stem.kind: stem},
    )
    monkeypatch.setattr(
        "library.tools.analysis.sound_event_pipeline.measure_sound_events",
        lambda *_args, **_kwargs: {"events": [
            {"label": "Door", "start": 0.21, "end": 0.39,
             "confidence": 0.55}
        ]},
    )

    result = query.measure_query_with_separation(
        source_path, "door slam", baseline=original, separator=object()
    )

    assert result["separation_triggered"] is True
    assert result["source"] is original
    assert result["candidate_events"] == [{
        "signal_id": stem.id,
        "signal_kind": "music_candidate",
        "signal_status": "estimated",
        "source_signal_id": stem.source_signal_id,
        "method": stem.method,
        "model": stem.model,
        "events": [{"label": "Door", "start": 0.21, "end": 0.39,
                    "confidence": 0.55}],
        "timing_basis": "measured_on_estimated_stem",
    }]


def test_explicitly_requested_nonoverlap_does_not_separate(tmp_path):
    source_path = tmp_path / "source.wav"
    sf.write(source_path, np.zeros((1600, 1), dtype=np.float32), 16000)
    baseline = {"events": [{"label": "Door", "start": 0.2, "end": 0.4,
                            "confidence": 0.8}]}

    result = query.measure_query_with_separation(
        source_path, "door", baseline=baseline
    )

    assert result["separation_triggered"] is False
    assert result["candidate_events"] == []
    assert result["source"] is baseline
