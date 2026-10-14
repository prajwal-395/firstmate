"""VI-10 keeps timed audio claims typed, method-bound and covered."""

from library.tools import source_memory
from library.tools.audio_interval_evidence import music_interval_evidence


def test_music_intervals_keep_method_uncertainty_and_full_track_coverage():
    analysis = {
        "available": True,
        "duration_s": 8.0,
        "tempo": {
            "method": "beat-this-final0",
            "beats": [0.4, 0.9],
            "downbeats": [0.4],
            "downbeat_source": "detected",
        },
        "structure": {
            "method": "energy-based",
            "sections": [{"type": "intro", "start": 0.0, "end": 4.0}],
        },
    }

    rows, coverage = music_interval_evidence(analysis)

    assert {row["kind"] for row in rows} == {
        "beat", "downbeat", "music_section"}
    assert sum(row["kind"] == "beat" for row in rows) == 2
    assert all(row["record_type"] == "interval" for row in rows)
    assert all(row["certainty_state"] == "measured" for row in rows)
    assert all(row["method"] for row in rows)
    assert rows[0]["uncertainty"]["kind"] == "unquantified"
    assert rows[-1]["uncertainty"]["boundary_seconds"] is None
    assert coverage == {
        "kind": "complete", "start_seconds": 0.0,
        "end_seconds": 8.0, "duration_seconds": 8.0,
    }


def test_environmental_intervals_record_method_resolution_and_coverage():
    events = [{"label": "Rain", "start": 1.2, "end": 2.7,
               "confidence": 0.81}]
    rows, coverage = source_memory._environmental_sound_intervals(
        events, "panns-cnn14-decisionlevelmax", 5.0)

    assert rows == [{
        "record_type": "interval",
        "domain": "environmental_audio",
        "kind": "environmental_sound_event",
        "label": "Rain",
        "interval_semantics": "span",
        "start_seconds": 1.2,
        "end_seconds": 2.7,
        "evidence_type": "measurement",
        "certainty_state": "measured",
        "method": "panns-cnn14-decisionlevelmax",
        "uncertainty": {"kind": "resolution_bound", "boundary_seconds": 0.01},
        "coverage": {"kind": "complete", "start_seconds": 0.0,
                     "end_seconds": 5.0, "duration_seconds": 5.0},
        "confidence": 0.81,
    }]
    assert coverage["kind"] == "complete"
    assert source_memory._environmental_sound_intervals(
        [], "unmeasured: no checkpoint", 5.0) == (
            [], {"kind": "unavailable", "start_seconds": None,
                 "end_seconds": 5.0, "duration_seconds": 5.0})


def test_m5_music_and_environmental_writers_preserve_each_other(tmp_path):
    digest = "d" * 64
    root = tmp_path / "memory"
    rain_events = [{"label": "Rain", "start": 1.0, "end": 2.0,
                    "confidence": 0.8}]
    source_memory.write_sound(
        digest, "/source/audio.wav", "measured", rain_events,
        "panns-test", root=root, duration_seconds=4.0)
    sound_before = source_memory.read_sound(digest, root)
    music_rows, music_coverage = music_interval_evidence({
        "available": True, "duration_s": 4.0,
        "tempo": {"method": "beat-test", "beats": [1.0],
                  "downbeats": [], "downbeat_source": None},
        "structure": {"method": "structure-test", "sections": []},
    })
    source_memory.write_music_evidence(
        digest, "/source/audio.wav", music_rows, music_coverage,
        "beat-test; structure-test", root=root)
    wind_events = [{"label": "Wind", "start": 2.0, "end": 3.0,
                    "confidence": 0.7}]
    source_memory.write_sound(
        digest, "/source/audio.wav", "measured", wind_events,
        "panns-new", root=root, duration_seconds=4.0)

    stored = source_memory.read_sound(digest, root)
    assert stored["sound_events"][0]["label"] == "Wind"
    assert stored["sound_interval_evidence"][0]["method"] == "panns-new"
    assert stored["music_interval_evidence"] == music_rows
    assert stored["music_coverage"] == music_coverage
    assert sound_before["sound_coverage"]["duration_seconds"] == 4.0
