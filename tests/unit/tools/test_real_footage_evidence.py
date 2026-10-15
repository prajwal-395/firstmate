"""Real-footage evidence: measured from bytes, never modelled.

Every test names a defect the module must not have: an unreadable file
passing as measured, a window edge reported as a scene cut, silence
reported as speech absence, or an observation whose type the evidence
query layer refuses.
"""
import subprocess

import pytest

from library.tools import evidence_query, real_footage_evidence as evidence


FFPROBE_JSON = """{"streams": [{"width": 1920, "height": 1080,
"avg_frame_rate": "30000/1001", "nb_frames": "123456",
"codec_name": "mxf"}], "format": {"duration": "288.955",
"size": "3514985520"}}"""

SHOWINFO = """[Parsed_showinfo_1 @ 0x123] n:   0 pts:      0 pts_time:0
[Parsed_showinfo_1 @ 0x123] n:  901 pts:  901901 pts_time:30.0333
[Parsed_showinfo_1 @ 0x123] n: 2700 pts:2702700 pts_time:90.09 scene_score:0.72
"""

AUDIO_OUT = """[silencedetect @ 0x1] silence_start: 5.0
[silencedetect @ 0x1] silence_end: 8.5 | silence_duration: 3.5
[Parsed_volumedetect_1 @ 0x2] mean_volume: -25.3 dB
[Parsed_volumedetect_1 @ 0x2] max_volume: -8.1 dB
"""


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_probe_measures_duration_fps_and_size():
    metadata = evidence.probe_source("LCATL0012.MXF",
                                     run=lambda argv: _completed(FFPROBE_JSON))

    assert metadata["duration_seconds"] == pytest.approx(288.955)
    assert metadata["fps"] == pytest.approx(29.97, abs=0.01)
    assert (metadata["width"], metadata["height"]) == (1920, 1080)
    assert metadata["size_bytes"] == 3514985520


def test_probe_refuses_unreadable_files():
    with pytest.raises(RuntimeError, match="ffprobe refused"):
        evidence.probe_source("missing.MXF",
                              run=lambda argv: _completed("", "no file", 1))


def test_probe_refuses_zero_duration():
    with pytest.raises(RuntimeError, match="no usable duration"):
        evidence.probe_source("empty.MXF",
                              run=lambda argv: _completed('{"streams": [],'
                                                          '"format": {}}'))


def test_scene_detection_reports_interior_boundaries_only():
    boundaries = evidence.detect_scenes("clip.MOV", 10.0, 100.0,
                                        run=lambda argv: _completed("", SHOWINFO))

    assert [row["tick_s"] for row in boundaries] == [40.033]
    assert all(10.0 < row["tick_s"] < 100.0 for row in boundaries)


def test_scene_detection_rejects_empty_windows():
    with pytest.raises(ValueError, match="non-empty window"):
        evidence.detect_scenes("clip.MOV", 5.0, 5.0, run=lambda argv: None)


def test_scene_detection_refuses_ffmpeg_errors():
    with pytest.raises(RuntimeError, match="scene detection failed"):
        evidence.detect_scenes("clip.MOV", 0.0, 10.0,
                               run=lambda argv: _completed(
                                   "", "Unrecognized option 'vsync'"))


def test_audio_measurement_refuses_ffmpeg_errors():
    with pytest.raises(RuntimeError, match="audio measurement failed"):
        evidence.measure_audio("clip.MOV", 0.0, 10.0,
                               run=lambda argv: _completed(
                                   "", "Error splitting the argument list"))


def test_audio_measurement_splits_sounded_and_silence():
    audio = evidence.measure_audio("clip.MOV", 0.0, 20.0,
                                   run=lambda argv: _completed("", AUDIO_OUT))

    assert audio["silence_spans"] == [[5.0, 8.5]]
    assert audio["sounded_spans"] == [[0.0, 5.0], [8.5, 20.0]]
    assert audio["mean_volume_db"] == pytest.approx(-25.3)
    assert "silencedetect" in audio["method"]


def test_observations_use_registered_measurement_types():
    metadata = {"duration_seconds": 20.0, "fps": 30.0, "width": 1920,
                "height": 1080, "size_bytes": 100, "codec": "h264"}
    observations = evidence.observations_for(
        "IMG_1818", "digest", "clock-1", metadata,
        [{"tick_s": 12.5, "score": 0.6}],
        {"sounded_spans": [[0.0, 5.0]], "silence_spans": [[5.0, 20.0]],
         "mean_volume_db": -25.0, "max_volume_db": -8.0,
         "method": "silencedetect+volumedetect"})

    kinds = {row["payload"]["evidence_contract"]["measurement_type"]
             for row in observations}
    assert kinds == {"media.source_metadata", "video.scene_boundary",
                     "audio.sound_event"}
    for kind in kinds:
        plan = evidence_query.compile_plan(
            [{"predicate": "measurement", "measurement_type": kind}])
        assert plan["predicates"][0]["measurement_type"] == kind


def test_scene_signals_feed_edit_point_candidates():
    from library.tools.edit_point_planner import (
        generate_edit_point_candidates)

    signals = evidence.signals_for(
        "IMG_1818", "clock-1", [{"tick_s": 12.5, "score": 0.6}])
    candidates = generate_edit_point_candidates(
        {"id": "m", "signals": signals})

    assert {candidate["tick"] for candidate in candidates} == {12500, 12501}


def test_model_assembly_names_assets_and_clocks():
    model = evidence.model_for("model-real", [{
        "source_id": "IMG_1818", "digest": "digest", "clock_id": "clock-1",
        "metadata": {"duration_seconds": 20.0, "fps": 30.0, "width": 1920,
                     "height": 1080, "size_bytes": 100, "codec": "h264"},
        "boundaries": [{"tick_s": 12.5, "score": 0.6}],
        "audio": {"sounded_spans": [[0.0, 5.0]], "silence_spans": [[5.0, 20.0]],
                  "mean_volume_db": -25.0, "max_volume_db": -8.0,
                  "method": "silencedetect+volumedetect"}}])

    assert model["assets"] == [{"id": "IMG_1818", "content_digest": "digest"}]
    assert model["clocks"][0]["domain"] == {"asset_id": "IMG_1818"}
    assert len(model["observations"]) == 3
    assert model["signals"] != []
