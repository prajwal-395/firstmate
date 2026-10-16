from __future__ import annotations

import copy

import pytest

from library.tools.analysis.audio_defect_benchmark import (
    SCHEMA,
    inject_defects,
    main,
    manifest_from_editorial_corpus,
    predictions_from_audio_quality,
    problem_report_from_audio_quality,
    repeated_speech_candidates,
    score_events,
    validate_manifest,
    write_injected_set,
)


def test_event_metrics_cover_errors_duration_and_false_alarms_per_hour():
    annotations = [
        {"defect": "clipping", "start_seconds": 10, "end_seconds": 12},
        {"defect": "dropout", "start_seconds": 30, "end_seconds": 32},
    ]
    predictions = [
        {"defect": "clipping", "start_seconds": 11, "end_seconds": 13},
        {"defect": "noise", "start_seconds": 40, "end_seconds": 41},
    ]
    result = score_events(annotations, predictions, 3600, runtime_seconds=120)
    assert result["overall"]["true_positive"] == 1
    assert result["overall"]["false_positive"] == 1
    assert result["overall"]["false_negative"] == 1
    assert result["overall"]["precision"] == pytest.approx(0.5)
    assert result["overall"]["recall"] == pytest.approx(0.5)
    assert result["overall"]["mean_boundary_error_seconds"] == pytest.approx(1)
    assert result["overall"]["mean_duration_error_seconds"] == pytest.approx(0)
    assert result["overall"]["false_alarms_per_program_hour"] == pytest.approx(1)
    assert result["cost"]["runtime_seconds_per_program_minute"] == pytest.approx(2)


def test_acceptance_cli_scores_a_serialized_heldout_case(tmp_path, capsys):
    import json

    payload = {
        "program_seconds": 60,
        "runtime_seconds": 30,
        "annotations": [{"defect": "dropout", "start_seconds": 5,
                         "end_seconds": 6}],
        "predictions": [{"defect": "dropout", "start_seconds": 5.1,
                         "end_seconds": 6.1}],
    }
    source = tmp_path / "score.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    assert main([str(source)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["overall"]["precision"] == 1
    assert report["overall"]["recall"] == 1
    assert report["cost"]["runtime_seconds_per_program_minute"] == 30


def test_correction_suggestion_measure_is_separate_from_detection():
    result = score_events([], [], 60, correction_suggestions=[{"proposal": "denoise"}])
    assert result["overall"]["f1"] == 1
    assert result["correction_decision"]["separate_from_detection_score"] is True


def test_defect_injection_preserves_control_and_records_ground_truth():
    import numpy as np

    clean = np.full(200, 0.1, dtype=np.float32)
    result = inject_defects(clean, 100, [
        {"defect": "dropout", "start_seconds": 0.5,
         "end_seconds": 1.0, "strength": 0},
        {"defect": "noise", "start_seconds": 1.0,
         "end_seconds": 1.5, "strength": 0.2, "seed": 23},
    ])
    assert np.array_equal(result["control"], clean)
    assert np.all(result["variants"][0]["samples"][50:100] == 0)
    assert not np.array_equal(result["variants"][1]["samples"][100:150], clean[100:150])
    assert result["variants"][1]["annotation"]["noise_kind"] == "broadband"
    assert result["variants"][0]["annotation"] == {
        "defect": "dropout", "start_seconds": 0.5, "end_seconds": 1.0,
    }


def test_room_tone_donor_checks_channel_layout_not_sample_count():
    import numpy as np

    clean = np.full(200, 0.1, dtype=np.float32)
    result = inject_defects(clean, 100, [{
        "defect": "room_tone_discontinuity", "start_seconds": 0.5,
        "end_seconds": 1.0, "strength": 0,
        "donor_samples": np.full(25, 0.2, dtype=np.float32),
    }])
    assert np.allclose(result["variants"][0]["samples"][50:100], 0.2)


def test_injected_set_writes_control_and_variant_without_mutating_base(tmp_path):
    import wave

    import numpy as np

    base = tmp_path / "held-out.wav"
    data = np.full(200, 8192, dtype="<i2")
    with wave.open(str(base), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(100)
        output.writeframes(data.tobytes())
    result = write_injected_set(
        base, [{"defect": "clipping", "start_seconds": 0.5,
                "end_seconds": 1.0, "strength": 18}], tmp_path / "variants")
    assert (tmp_path / "variants" / "control.wav").is_file()
    assert result["variants"][0]["annotations"][0]["defect"] == "clipping"
    with wave.open(str(base), "rb") as original:
        assert original.readframes(200) == data.tobytes()


def test_fi12_adapter_localizes_supported_events_and_abstains_on_masking():
    measurement = {"channels": [{
        "stream_index": 0, "channel_index": 0,
        "integrated_lufs": -20,
        "intervals": [
            {"start_seconds": 0, "end_seconds": 1, "sample_peak_dbfs": -0.001,
             "rms_dbfs": -10, "spectral_flatness": 0.8,
             "spectral_centroid_hz": 500},
            {"start_seconds": 1, "end_seconds": 2, "sample_peak_dbfs": -6,
             "rms_dbfs": -30, "spectral_flatness": 0.1,
             "spectral_centroid_hz": 3000},
        ],
        "silence_intervals": [{"start_seconds": 0.5, "end_seconds": 0.7}],
    }]}
    result = predictions_from_audio_quality(
        measurement, speech_spans=[(0.4, 0.8)])
    assert {row["defect"] for row in result} == {
        "clipping", "noise", "loudness_mismatch", "dialogue_interruption",
    }
    assert "music_dialogue_masking" not in {row["defect"] for row in result}


def test_fi12_adapter_finds_a_prominent_mains_tone():
    measurement = {"channels": [{
        "stream_index": 0, "channel_index": 0, "integrated_lufs": -24,
        "intervals": [{"start_seconds": 2, "end_seconds": 3,
                       "sample_peak_dbfs": -10, "rms_dbfs": -24,
                       "spectral_flatness": 0.03,
                       "spectral_centroid_hz": 120,
                       "tonal_peak_hz": 120,
                       "tonal_peak_power_fraction": 0.61}],
        "silence_intervals": [],
    }]}
    predictions = predictions_from_audio_quality(measurement)
    hum = [row for row in predictions if row.get("noise_kind") == "tonal_hum"]
    assert len(hum) == 1
    assert hum[0]["frequency_hz"] == 120


def test_fi12_v2_hum_calibration_end_to_end(tmp_path):
    """Locked FI-12-v2 tonal-hum calibration through the real measurement.

    Fixture recipe: 60 s mono 48 kHz, two-tone bed (440 + 880 Hz, total RMS
    -37 dBFS) plus a 120 Hz sine at 0.05 peak amplitude from 10-15 s. The v1
    adapter has no hum route, so the incumbent comparison is fixed: v1 recall
    is 0 by construction on any hum interval, v2 must find this one with no
    hum false alarms. The hum onset/offset also trips the pre-existing
    adjacent-window loudness rule, so overall precision against hum-only gold
    is 1/3; that side effect is recorded here, not hidden.
    """
    import wave

    import numpy as np

    from library.tools.audio_quality import measure_audio_quality

    rate, duration = 48_000, 60
    times = np.arange(rate * duration, dtype=np.float64) / rate
    bed_level = 10.0 ** (-37.0 / 20.0)
    samples = bed_level / np.sqrt(2.0) * (
        np.sin(2 * np.pi * 440.0 * times) + np.sin(2 * np.pi * 880.0 * times))
    samples = samples + 0.05 * np.sin(2 * np.pi * 120.0 * times) * (
        (times >= 10.0) & (times < 15.0))
    pcm = np.rint(np.clip(samples, -1.0, 1.0) * 32768.0).clip(
        -32768, 32767).astype("<i2")
    fixture = tmp_path / "hum-calibration.wav"
    with wave.open(str(fixture), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())

    measurement = measure_audio_quality(str(fixture), root=tmp_path / "memory",
                                        force=True)
    predictions = predictions_from_audio_quality(measurement)
    hum = [row for row in predictions if row.get("noise_kind") == "tonal_hum"]
    assert len(hum) == 1
    assert hum[0]["frequency_hz"] == 120
    assert hum[0]["start_seconds"] == pytest.approx(10, abs=1.05)
    assert hum[0]["end_seconds"] == pytest.approx(15, abs=1.05)

    hum_only = score_events(
        [{"defect": "noise", "start_seconds": 10, "end_seconds": 15}],
        [{"defect": "noise", "start_seconds": hum[0]["start_seconds"],
          "end_seconds": hum[0]["end_seconds"]}],
        duration)
    assert hum_only["by_defect"]["noise"]["recall"] == 1
    assert hum_only["by_defect"]["noise"]["precision"] == 1

    full = score_events(
        [{"defect": "noise", "start_seconds": 10, "end_seconds": 15}],
        [{key: row[key]
          for key in ("defect", "start_seconds", "end_seconds")}
         for row in predictions],
        duration)
    assert full["overall"]["recall"] == 1
    assert full["overall"]["precision"] == pytest.approx(1 / 3)


def test_fi12_adapter_groups_adjacent_windows_and_ignores_inactive_channel():
    measurement = {"method": "fixture", "channels": [{
        "stream_index": 0, "channel_index": 0,
        "integrated_lufs": -60,
        "intervals": [],
        "silence_intervals": [{"start_seconds": 0, "end_seconds": 60}],
    }]}
    assert predictions_from_audio_quality(measurement) == []

    measurement["channels"][0].update({
        "stream_index": 0, "channel_index": 0,
        "integrated_lufs": -22,
        "intervals": [
            {"start_seconds": 0, "end_seconds": 1, "sample_peak_dbfs": -10,
             "rms_dbfs": -10, "spectral_flatness": 0.1,
             "spectral_centroid_hz": 500},
            {"start_seconds": 1, "end_seconds": 2, "sample_peak_dbfs": -10,
             "rms_dbfs": -21, "spectral_flatness": 0.1,
             "spectral_centroid_hz": 600},
            {"start_seconds": 2, "end_seconds": 3, "sample_peak_dbfs": -10,
             "rms_dbfs": -21, "spectral_flatness": 0.1,
             "spectral_centroid_hz": 600},
            {"start_seconds": 3, "end_seconds": 4, "sample_peak_dbfs": -10,
             "rms_dbfs": -10, "spectral_flatness": 0.1,
             "spectral_centroid_hz": 500},
        ],
        "silence_intervals": [],
    })
    result = predictions_from_audio_quality(measurement)
    loudness = [row for row in result if row["defect"] == "loudness_mismatch"]
    assert len(loudness) == 1
    assert (loudness[0]["start_seconds"], loudness[0]["end_seconds"]) == (1, 4)


def test_problem_report_separates_measured_candidates_from_preferences():
    report = problem_report_from_audio_quality({"method": "fixture", "channels": [{
        "stream_index": 0, "channel_index": 0,
        "integrated_lufs": -20,
        "intervals": [{"start_seconds": 0, "end_seconds": 1,
                       "sample_peak_dbfs": -0.001, "rms_dbfs": -8,
                       "spectral_flatness": 0.1,
                       "spectral_centroid_hz": 500}],
        "silence_intervals": [],
    }]})
    assert report["findings"][0]["classification"] == "measured_candidate"
    assert report["findings"][0]["editorial_verdict"] == "review_required"
    assert report["findings"][0]["action"]
    assert report["editorial_preferences"] == []
    assert report["limitations"][0]["defect"] == "music_dialogue_masking"
    assert report["summary"]["technical_delivery_checks"] == "not_evaluated_by_this_report"


def test_repeated_speech_is_a_review_candidate_not_an_automatic_edit():
    candidates = repeated_speech_candidates([
        {"start_seconds": 1, "end_seconds": 4,
         "text": "We should start with the second example."},
        {"start_seconds": 12, "end_seconds": 15,
         "text": "We should start with the second example!"},
        {"start_seconds": 18, "end_seconds": 20,
         "text": "That is a different phrase."},
    ])
    assert len(candidates) == 1
    assert candidates[0]["classification"] == "measured_candidate"
    assert candidates[0]["editorial_verdict"] == "review_required"
    assert candidates[0]["earlier"]["start_seconds"] == 1
    assert candidates[0]["later"]["start_seconds"] == 12


def test_audio_quality_cli_can_emit_the_problem_report(monkeypatch, capsys):
    import json

    from library.tools import audio_quality

    measurement = {"method": "fixture", "channels": [{
        "stream_index": 0, "channel_index": 0,
        "integrated_lufs": -20,
        "intervals": [{"start_seconds": 0, "end_seconds": 1,
                       "sample_peak_dbfs": -0.001, "rms_dbfs": -8,
                       "spectral_flatness": 0.1,
                       "spectral_centroid_hz": 500}],
        "silence_intervals": [],
    }]}
    monkeypatch.setattr(audio_quality, "measure_audio_quality",
                        lambda *args, **kwargs: measurement)
    assert audio_quality.main(["source.wav", "--problems", "--speech-span",
                               "0:1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["measurement"] == measurement
    assert result["problem_report"]["findings"][0]["defect"] == "clipping"


def test_audio_quality_cli_passes_source_timed_transcript_segments(
        monkeypatch, capsys, tmp_path):
    import json

    from library.tools import audio_quality

    monkeypatch.setattr(audio_quality, "measure_audio_quality",
                        lambda *args, **kwargs: {"method": "fixture", "channels": []})
    segments = tmp_path / "speech.json"
    segments.write_text(json.dumps([{
        "start_seconds": 1, "end_seconds": 3, "text": "Here is a repeated test phrase."
    }, {
        "start_seconds": 8, "end_seconds": 10, "text": "Here is a repeated test phrase."
    }]), encoding="utf-8")
    assert audio_quality.main([
        "source.wav", "--problems", "--speech-segments-json", str(segments)
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["problem_report"]["repeated_speech_candidates"]) == 1


def test_audio_quality_cli_refuses_malformed_transcript_segments(
        monkeypatch, capsys, tmp_path):
    import json

    from library.tools import audio_quality

    monkeypatch.setattr(audio_quality, "measure_audio_quality",
                        lambda *args, **kwargs: {"method": "fixture", "channels": []})
    segments = tmp_path / "speech.json"
    segments.write_text(json.dumps([{
        "start_seconds": 8, "end_seconds": 10,
    }]), encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        audio_quality.main([
            "source.wav", "--problems", "--speech-segments-json", str(segments)
        ])
    assert refused.value.code == 2


def test_event_validation_rejects_out_of_program_and_unknown_defect():
    with pytest.raises(ValueError, match="after program duration"):
        score_events([], [{"defect": "dropout", "start_seconds": 9,
                           "end_seconds": 11}], 10)
    with pytest.raises(ValueError, match="unknown defect"):
        score_events([{"defect": "bad", "start_seconds": 0,
                       "end_seconds": 1}], [], 10)


def _manifest():
    return {
        "schema": SCHEMA,
        "source_separation": "excluded",
        "sources": [
            {"source_id": "source-train", "split": "training",
             "leakage_group_id": "session-train", "sha256": "a" * 64,
             "rights_status": "cleared"},
            {"source_id": "source-calibration", "split": "calibration",
             "leakage_group_id": "session-calibration", "sha256": "c" * 64,
             "rights_status": "cleared"},
            {"source_id": "source-held", "split": "held_out",
             "leakage_group_id": "session-held", "sha256": "b" * 64,
             "rights_status": "cleared", "labels_access": "sealed_custodian_only",
             "variants": [{"duration_seconds": 10,
                           "annotations": [{"defect": "clipping",
                                            "start_seconds": 2,
                                            "end_seconds": 3}]}]},
        ],
    }


def test_manifest_accepts_disjoint_cleared_and_sealed_heldout_labels():
    validate_manifest(_manifest())


def test_manifest_derives_rights_split_and_hash_from_editorial_corpus():
    corpus = {"corpus_id": "test-corpus", "sources": [
        {"source_id": "train", "path": "train.wav", "split": "training",
         "leakage_group_id": "tg", "sha256": "a" * 64,
         "rights": {"status": "cleared", "permitted_uses": ["benchmarking"]}},
        {"source_id": "cal", "path": "cal.wav", "split": "calibration",
         "leakage_group_id": "cg", "sha256": "c" * 64,
         "rights": {"status": "cleared", "permitted_uses": ["benchmarking"]}},
        {"source_id": "held", "path": "held.wav", "split": "held_out",
         "leakage_group_id": "hg", "sha256": "b" * 64,
         "rights": {"status": "cleared", "permitted_uses": ["benchmarking"]}},
    ]}
    variants = [{"duration_seconds": 10,
                 "annotations": [{"defect": "dropout", "start_seconds": 1,
                                 "end_seconds": 2}]}]
    manifest = manifest_from_editorial_corpus(
        corpus, ["train", "cal", "held"], {"held": variants})
    validate_manifest(manifest)
    assert manifest["sources"][2]["sha256"] == "b" * 64
    assert manifest["sources"][2]["synthetic_labels"] == "known_by_construction"


@pytest.mark.parametrize("mutation, message", [
    ("leakage", "may not cross"),
    ("unsealed", "remain sealed"),
    ("separation", "explicitly excluded"),
])
def test_manifest_refuses_leakage_unsealed_labels_or_separation(mutation, message):
    manifest = _manifest()
    if mutation == "leakage":
        manifest["sources"][2]["leakage_group_id"] = "session-train"
    elif mutation == "unsealed":
        manifest["sources"][2]["labels_access"] = "tuning"
    else:
        manifest["source_separation"] = "used"
    with pytest.raises(ValueError, match=message):
        validate_manifest(copy.deepcopy(manifest))


def test_manifest_refuses_injected_variants_outside_heldout():
    manifest = _manifest()
    manifest["sources"][0]["variants"] = [{
        "duration_seconds": 10, "annotations": [],
    }]
    with pytest.raises(ValueError, match="held-out only"):
        validate_manifest(manifest)
