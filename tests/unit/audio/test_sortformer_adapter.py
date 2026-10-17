"""Sortformer owns turns; ECAPA remains the identity embedding producer."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from library.tools import model_registry, shared_environment
from library.tools import single_track_diarization as diar


def test_sortformer_turns_keep_overlap_and_get_ecapa_embeddings(
        monkeypatch, tmp_path):
    model = tmp_path / "SortformerOffline_v2.1.mlmodelc"
    cli = tmp_path / "fluidaudiocli"
    monkeypatch.setattr(shared_environment, "require_sortformer",
                        lambda **_kw: (cli, model))
    monkeypatch.setattr(model_registry, "route", lambda *a, **kw: {
        "selected_implementation": diar.DEFAULT_DIARIZATION_IMPLEMENTATION})
    audio = np.ones(8 * diar.SAMPLE_RATE, dtype=np.float32)
    monkeypatch.setattr(diar, "_read_wav16_mono", lambda _p: (16000, audio))
    monkeypatch.setattr(diar, "embedding_device", lambda: "cpu")
    starts_seen = []

    def encode(_audio, starts, **_kwargs):
        starts_seen.extend(starts.tolist())
        rows = np.zeros((len(starts), diar.EMBEDDING_DIM), dtype=np.float32)
        for index in range(len(rows)):
            rows[index, index] = 1.0
        return rows

    monkeypatch.setattr(diar, "_embed_windows_span", encode)
    monkeypatch.setattr(diar, "_license_provenance",
                        lambda ids: [{"implementation_id": value}
                                     for value in ids])

    def run(command, **kwargs):
        assert command[:2] == [str(cli), "sortformer"]
        assert "--offline" in command and "--palettized" in command
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps({"segments": [
            {"speaker": "speaker_0", "startTimeSeconds": .1,
             "endTimeSeconds": 1.0},
            {"speaker": "speaker_1", "startTimeSeconds": .8,
             "endTimeSeconds": 1.8},
            {"speaker": "speaker_0", "startTimeSeconds": 1.02,
             "endTimeSeconds": 2.0},
        ]}), encoding="utf-8")

    monkeypatch.setattr(diar.subprocess, "run", run)
    result = diar._diarize_sortformer("audio.wav", None, "cpu")

    assert result.implementation_id == diar.DEFAULT_DIARIZATION_IMPLEMENTATION
    assert result.speakers_estimated == 2
    assert result.clusters[0].turns == [(0.1, 2.0)]
    assert result.clusters[1].turns == [(0.8, 1.8)]
    assert len(starts_seen) == 2  # one ECAPA embedding per merged Sortformer turn
    assert result.window_labels == [0, 1]
    assert [p["implementation_id"] for p in result.provenance] == [
        diar.DEFAULT_DIARIZATION_IMPLEMENTATION,
        diar.ECAPA_FALLBACK_IMPLEMENTATION]
    picked = diar.segment_voice_embeddings([(0.85, 1.0)], result,
                                           speaker_label="speaker_02")
    assert np.allclose(picked[0], result.window_embeddings[1])


def test_unavailable_sortformer_uses_ecapa_fallback_and_records_reason(
        monkeypatch):
    fallback = diar.Diarization(
        clusters=[], window_embeddings=np.zeros((0, diar.EMBEDDING_DIM)),
        window_starts=np.zeros(0), window_labels=[], speakers_estimated=0,
        inference_seconds=0.1, method="ECAPA", weights="ecapa", device="cpu")
    monkeypatch.setattr(
        diar, "_diarize_sortformer",
        lambda *_a: (_ for _ in ()).throw(
            diar.DiarizationUnavailable("Sortformer model absent")))
    monkeypatch.setattr(diar, "_diarize_ecapa", lambda *_a, **_kw: fallback)
    monkeypatch.setattr(diar, "_license_provenance",
                        lambda ids: [{"implementation_id": ids[0]}])

    result = diar.diarize_track("audio.wav", device="cpu")

    assert result is fallback
    assert result.fallback_reason == "Sortformer model absent"
    assert result.provenance == [{"implementation_id": diar.ECAPA_FALLBACK_IMPLEMENTATION}]
