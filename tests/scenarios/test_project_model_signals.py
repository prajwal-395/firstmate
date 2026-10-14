"""Measured source-memory signals reach editorial cut planning end to end.

The small derived source below follows the normal Project Model migration
path. It exercises the producer records on disk, the migration, validation,
and the editorial planner; no Project Model signals are hand-prepared.
"""
from __future__ import annotations

import json
from pathlib import Path

from library.tools import project_model_evidence
from library.tools.project_model import validate_project_model
from library.tools.project_model_editorial import plan_edit_point_candidates


DIGEST = "a" * 64


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_analyzed_source_memory_reaches_measured_edit_point_candidates(
        tmp_path, monkeypatch):
    from library.tools import footage_analysis, source_memory

    project = tmp_path / "project"
    memory = tmp_path / "source-memory"
    media_path = "/unavailable/derived-fixture.mov"
    _write(project / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": [{
            "clip_id": "clip_001", "source_file": media_path,
            "path": media_path,
        }]}},
        "source_fingerprints": {"clip_001": {
            "path": media_path, "content_digest": DIGEST,
        }},
    })
    _write(memory / DIGEST / "source.json", {
        "content_digest": DIGEST, "size_bytes": 100,
        "duration_seconds": 5.0,
        "video_streams": [{"index": 0, "codec_type": "video"}],
    })
    _write(memory / DIGEST / "transcript.words.json", {
        "content_digest": DIGEST, "status": "transcribed",
        "instrument": {"arm": "fixture", "aligner": "MFA"},
        "utterances": [{"start": 1.0, "end": 2.0,
                        "words": [{"word": "hello", "start": 1.0,
                                   "end": 1.5, "confidence": 0.9}],
                        "phonemes": [{"phone": "HH", "start": 1.0,
                                      "end": 1.2}]}],
    })
    _write(memory / DIGEST / "persons.json", {
        "content_digest": DIGEST, "frames": [],
        "instrument": {"method": "fixture"},
    })
    _write(memory / DIGEST / "identity.json", {
        "content_digest": DIGEST, "status": "measured",
        "faces": [], "voices": [], "speech_face_links": [],
        "instrument": {"face": "fixture", "voice": "fixture"},
    })
    _write(memory / DIGEST / "scenes.json", {
        "content_digest": DIGEST, "status": "measured",
        "scenes": [], "scene_boundaries": [],
        "scene_cinematography": {
            "method": "scene-cinematography-measurements-v1",
            "status": "measured", "scenes": [], "shots": [],
            "subshots": [], "sequences": [],
            "measurement_catalog": [
                {"id": "camera_motion:fixture:0", "lane": "camera_motion",
                 "time": 2.0,
                 "value": {"translation_x": 0.0, "translation_y": 0.0,
                           "residual": 0.0}, "method": "fixture"},
                {"id": "camera_motion:fixture:1", "lane": "camera_motion",
                 "time": 3.0,
                 "value": {"translation_x": 0.2, "translation_y": 0.0,
                           "residual": 0.0}, "method": "fixture"},
            ],
        },
        "instrument": {"scene_pass": source_memory.M4_SCENE_PASS_METHOD,
                       "boundary_method": source_memory.M4_BOUNDARY_METHOD},
    })
    _write(memory / DIGEST / "sound.json", {
        "content_digest": DIGEST, "status": "measured",
        "source_file": media_path, "sound_events": [],
        "method": "fixture",
        "music_status": "measured", "music_method": "beat-grid-fixture",
        "music_interval_evidence": [
            {"kind": "beat", "start_seconds": 2.5,
             "end_seconds": 2.5, "certainty_state": "measured"},
            {"kind": "music_section", "label": "chorus",
             "start_seconds": 3.0, "end_seconds": 4.0,
             "certainty_state": "measured"},
        ],
    })
    _write(memory / DIGEST / "events.json", {
        "content_digest": DIGEST,
        "predicates": {"speaking": {"spans": []},
                       "on_screen": {"spans": []}},
        "candidates": {"hand_raise": {"spans": [{
            "start": 1.7, "end": 2.2, "face_track": "face_001",
            "frames": [{"t": 1.7}, {"t": 2.0}],
        }]}},
    })
    music_file = tmp_path / "selected-bed.wav"
    music_file.write_bytes(b"derived fixture music")
    from library.tools import footage_identity
    from library.steps.step_2_06_music_analysis.step import _method_hash
    music_digest = footage_identity.fingerprint(str(music_file))["content_digest"]
    source_memory.write_music_evidence(
        music_digest, str(music_file), [
            {"kind": "beat", "start_seconds": 0.5,
             "end_seconds": 0.5, "certainty_state": "measured"},
            {"kind": "music_section", "label": "verse",
             "start_seconds": 1.0, "end_seconds": 2.0,
             "certainty_state": "measured"},
        ], {"kind": "complete"}, "fixture-beat-grid", root=memory)
    _write(project / "pipeline_output/steps/2_06_music_analysis"
           / "music_analysis.json", {
        "available": True, "file": str(music_file), "duration_s": 3.0,
        "method_hash": _method_hash(),
    })
    monkeypatch.setattr(footage_analysis, "slot_is_fresh",
                        lambda _lane, _digest, _root: True)

    model, report = project_model_evidence.migrate_project_evidence(
        str(project), memory_root=memory)
    validate_project_model(model)
    candidates = plan_edit_point_candidates(model)
    signals_by_id = {signal["id"]: signal for signal in model["signals"]}

    assert report["digests"] == [DIGEST]
    assert f"asset-{music_digest}" in {asset["id"] for asset in model["assets"]}
    assert {signal["measurement_type"] for signal in model["signals"]} >= {
        "speech", "speech.phoneme", "music.beat", "music.section",
        "gesture.event", "camera.settle",
    }
    assert all(row["measurement_ids"] for row in candidates)
    expected = {
        (1000, "speech"), (1500, "speech"), (1000, "phoneme"),
        (1200, "phoneme"), (1700, "gesture"), (2200, "gesture"),
        (2500, "beat"), (3000, "music_section"), (4000, "music_section"),
        (2000, "camera_settle"),
        (500, "beat"), (1000, "music_section"), (2000, "music_section"),
    }
    assert all(any(row["tick"] == tick and kind in row["signals"]
                   for row in candidates)
               for tick, kind in expected)
    assert all(signal_id in signals_by_id
               for row in candidates for signal_id in row["measurement_ids"])
