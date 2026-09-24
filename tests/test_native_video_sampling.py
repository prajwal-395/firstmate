"""Native-video coverage is stated and scales with clip length.

Measured on mlx-vlm 0.7.2 with `mlx-community/gemma-4-12b-it-4bit`: every
`video=` model call sees exactly 32 frames (`Gemma4UnifiedVideoProcessor`
caps decoding at `num_frames=32` after `load_video` decodes at fps=2.0),
so a whole-clip call on a long clip thins silently (120 s -> 0.27 fps
effective). The pipeline therefore splits clips longer than
`NATIVE_WINDOW_S` into native windows instead, and records the sampling
each call actually used (`native_sample_plan` + per-pass window lists).

These tests name that contract:

1. `native_sample_plan` mirrors the loader's math (pure cases, plus one
   case against the real `load_video` on synthetic clips where mlx_vlm
   imports).
2. The windowed scene/camera/assessment passes offset segments to clip
   time, merge across window boundaries, and record their sampling.
3. Action windows keep their audio track and hand it to the model
   (`audio=`); entries record `has_audio` and `sampling`.
4. A cached window cut under the old strip-audio policy is re-cut, not
   silently reused (its file would fail `load_audio`).
"""
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from library.tools.analysis import vision_pipeline_v3 as vp


def test_sample_plan_matches_measured_probe_numbers():
    assert vp.native_sample_plan(120.0, 24.0) == {
        "frames": 32, "decode_fps": 2.0, "effective_fps": 0.267}
    assert vp.native_sample_plan(30.0, 24.0) == {
        "frames": 32, "decode_fps": 2.0, "effective_fps": 1.067}
    assert vp.native_sample_plan(10.0, 24.0)["frames"] == 20
    assert vp.native_sample_plan(60.0, 24.0)["effective_fps"] >= 0.5


def test_full_native_window_never_drops_below_half_fps():
    plan = vp.native_sample_plan(vp.NATIVE_WINDOW_S, 24.0)
    assert plan["frames"] == vp.NATIVE_VIDEO_FRAMES_PER_CALL
    assert plan["effective_fps"] >= 0.5


def _synth_clip(path, duration_s, with_audio=True):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc=duration={duration_s}:size=320x240:rate=24"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
                "-c:a", "aac", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def test_sample_plan_matches_real_loader_on_synthetic_clips(tmp_path):
    mlx_utils = pytest.importorskip("mlx_vlm.utils", reason="needs mlx_vlm")
    for duration_s in (10, 33):
        clip = _synth_clip(tmp_path / f"synth{duration_s}.mp4", duration_s)
        meta = vp.probe_clip(clip)
        # Explicit sampling - the values gemma4's processor resolves to on
        # 0.7.2 (fps=2.0, max 32 frames). Compared by decoded frame count,
        # which is stable across mlx_vlm return-shape versions.
        loaded = mlx_utils.load_video(
            str(clip), fps=2.0, min_frames=4, max_frames=32, frame_factor=2)
        arr = loaded[0]
        plan = vp.native_sample_plan(meta["duration_s"], meta["fps"])
        assert plan["frames"] == arr.shape[0]


def _windows():
    return [
        {"index": 0, "start": 0.0, "end": 60.0, "path": "/tmp/a.mp4",
         "has_audio": False},
        {"index": 1, "start": 60.0, "end": 120.0, "path": "/tmp/b.mp4",
         "has_audio": False},
        {"index": 2, "start": 120.0, "end": 130.0, "path": "/tmp/c.mp4",
         "has_audio": False},
    ]


def _seg(start, end):
    return {"start": start, "end": end, "location": "studio", "type": "indoor",
            "lighting": "dim", "notable_features": []}


def test_windowed_scene_offsets_segments_and_records_sampling():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.side_effect = [
        ([_seg(0.0, 60.0)], "raw", 30.0),
        ([_seg(0.0, 60.0)], "raw", 30.0),
        ([_seg(0.0, 10.0)], "raw", 10.0),
    ]
    segments, elapsed, sampling = vp.analyze_scene_windowed(
        analyzer, 130.0, None, _windows(), 24.0)
    assert [(s["start"], s["end"]) for s in segments] == [
        (0.0, 60.0), (60.0, 120.0), (120.0, 130.0)]
    assert elapsed == 70.0
    assert [w["frames"] for w in sampling] == [32, 32, 20]
    assert all(w["decode_fps"] == 2.0 for w in sampling)
    assert analyzer.analyze_with_retry.call_count == 3


def test_windowed_camera_merges_identical_mode_across_boundary():
    mode = {"start": 0, "end": 60, "mode": "mounted", "framing": "medium",
            "stability": "stable", "movement": "stationary"}
    analyzer = MagicMock()
    analyzer.analyze_with_retry.side_effect = [
        ([dict(mode)], "raw", 30.0),
        ([dict(mode)], "raw", 30.0),
    ]
    modes, _, sampling = vp.analyze_camera_windowed(
        analyzer, 120.0, _windows()[:2], 24.0)
    assert len(modes) == 1
    assert (modes[0]["start"], modes[0]["end"]) == (0.0, 120.0)
    assert len(sampling) == 2


def test_windowed_assessment_votes_and_offsets_subject_ranges():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.side_effect = [
        ({"content_type": "person_talking_to_camera",
          "primary_subject_visible": [[0, 60]]}, "raw", 20.0),
        ({"content_type": "scenery",
          "primary_subject_visible": [[0, 60]]}, "raw", 20.0),
        ({"content_type": "person_talking_to_camera",
          "primary_subject_visible": [[0, 10]]}, "raw", 10.0),
    ]
    assessment, _, sampling = vp.analyze_assessment_windowed(
        analyzer, 130.0, {"camera_stability": "unknown"}, _windows(), 24.0)
    assert assessment["content_type"] == "person_talking_to_camera"
    assert assessment["primary_subject_visible"] == [
        [0.0, 60.0], [60.0, 120.0], [120.0, 130.0]]
    assert len(sampling) == 3


def test_windowed_assessment_with_no_parsed_window_reports_unmeasured():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = ({}, "raw", 1.0)
    assessment, _, _ = vp.analyze_assessment_windowed(
        analyzer, 130.0, {"camera_stability": "unknown"}, _windows()[:1], 24.0)
    assert assessment["content_type"] == "unknown"
    assert assessment["primary_subject_visible"] is None


def test_windowed_assessment_keeps_unanimous_explicit_empty():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = (
        {"content_type": "scenery", "primary_subject_visible": []}, "raw", 1.0)
    assessment, _, _ = vp.analyze_assessment_windowed(
        analyzer, 120.0, {"camera_stability": "unknown"}, _windows()[:2], 24.0)
    assert assessment["primary_subject_visible"] == []


def test_action_window_passes_its_audio_and_records_sampling():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = ({"actions": []}, "raw", 0.5)
    clips = [{"index": 0, "start": 0.0, "end": 10.0, "path": "/tmp/w.mp4",
              "has_audio": True}]
    out = vp.analyze_actions(analyzer, clips, 10.0, None, "", fps=24.0)
    _, kwargs = analyzer.analyze_with_retry.call_args
    assert kwargs["audio"] == "/tmp/w.mp4"
    assert out[0]["has_audio"] is True
    assert out[0]["sampling"]["frames"] == 20


def test_sourceless_action_window_hears_nothing_and_says_so():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = ({"actions": []}, "raw", 0.5)
    clips = [{"index": 0, "start": 0.0, "end": 10.0, "path": "/tmp/w.mp4",
              "has_audio": False}]
    out = vp.analyze_actions(analyzer, clips, 10.0, None, "", fps=24.0)
    _, kwargs = analyzer.analyze_with_retry.call_args
    assert kwargs["audio"] is None
    assert out[0]["has_audio"] is False


def test_extract_keeps_audio_and_recuts_legacy_silent_cache(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    source = _synth_clip(tmp_path / "src.mp4", 12, with_audio=True)
    cache = tmp_path / "cache"
    # Legacy cache: a window cut when the extractor stripped audio.
    legacy_dir = cache / "src" / "clips"
    legacy_dir.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source),
         "-ss", "0", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "23", "-an", str(legacy_dir / "clip_000.mp4")],
        capture_output=True, check=True)
    assert vp._file_has_audio(legacy_dir / "clip_000.mp4") is False

    clips = vp.extract_video_clips(Path(source), 12.0, cache)
    assert len(clips) == 2
    assert all(c["has_audio"] for c in clips)
    assert vp._file_has_audio(Path(clips[0]["path"])) is True
