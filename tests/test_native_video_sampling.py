"""Every native-video pass samples at 2 fps, from one call per 10 s window.

Measured on mlx-vlm 0.7.2 with `mlx-community/gemma-4-12b-it-4bit`: every
`video=` model call sees at most 32 frames (decode at fps=2.0, then the
gemma4 processor keeps at most `num_frames=32`). A caller-supplied
`max_frames=` reaches only the decoder - the processor re-caps to 32 -
and raising the processor's own `num_frames` to 64/96 costs ~3-4x wall
for still under 2 fps on a 60 s span. So the floor is met by windowing:
scene, camera, actions and assessment all answer from the same one call
per 10 s window (`analyze_windows`), 20 frames each at 2 fps.

These tests name that contract:

1. `native_sample_plan` mirrors the loader's math (pure cases, plus one
   case against the real `load_video` on synthetic clips where mlx_vlm
   imports), and a 10 s window plans 20 frames at 2.0 fps.
2. The folded call answers all four sections in clip time from a single
   model call per window, records its sampling, and drops segments
   outside their own window instead of shifting them into place.
3. Assessment sections merge across windows by vote (ties to the
   earliest), with absent measurements staying absent.
4. Windows keep their audio track and hand it to the model (`audio=`);
   entries record `has_audio` and `sampling`.
5. A cached window cut under the old strip-audio policy is re-cut, not
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


def test_every_folded_window_samples_at_two_fps():
    plan = vp.native_sample_plan(vp.ACTION_WINDOW_S, 24.0)
    assert plan["frames"] == 20
    assert plan["decode_fps"] == 2.0
    assert plan["effective_fps"] == 2.0


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


def _folded_answer(w_start, w_end):
    return {
        "actions": [{"start": w_start, "end": w_end, "action": "talking",
                     "speech_cue": None, "body_language": "seated"}],
        "scene": [{"start": w_start, "end": w_end, "location": "studio",
                   "type": "indoor", "lighting": "dim",
                   "notable_features": []}],
        "camera": [{"start": w_start, "end": w_end, "mode": "mounted",
                    "framing": "medium", "stability": "stable",
                    "movement": "stationary"}],
        "assessment": {"content_type": "person_talking_to_camera",
                       "primary_subject_visible": [[w_start, w_end]]},
    }


def _windows():
    return [
        {"index": 0, "start": 0.0, "end": 10.0, "path": "/tmp/a.mp4",
         "has_audio": True},
        {"index": 1, "start": 10.0, "end": 20.0, "path": "/tmp/b.mp4",
         "has_audio": True},
    ]


def test_folded_window_answers_all_sections_in_clip_time_from_one_call():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.side_effect = [
        (_folded_answer(0.0, 10.0), "raw", 30.0),
        (_folded_answer(10.0, 20.0), "raw", 30.0),
    ]
    out = vp.analyze_windows(analyzer, _windows(), 20.0, None, "", fps=24.0)
    assert analyzer.analyze_with_retry.call_count == 2
    assert len(out) == 2
    first = out[0]
    assert len(first["actions"]) == 1
    assert [(s["start"], s["end"]) for s in first["scene"]] == [(0.0, 10.0)]
    assert [(s["start"], s["end"]) for s in first["camera"]] == [(0.0, 10.0)]
    assert first["assessment"]["content_type"] == "person_talking_to_camera"
    assert first["assessment"]["primary_subject_visible"] == [[0.0, 10.0]]
    assert first["sampling"]["frames"] == 20
    assert first["sampling"]["effective_fps"] == 2.0
    assert first["has_audio"] is True


def test_folded_window_drops_out_of_window_segments_instead_of_shifting():
    analyzer = MagicMock()
    answer = _folded_answer(0.0, 10.0)
    answer["scene"] = [{"start": 0.0, "end": 60.0, "location": "studio",
                        "type": "indoor", "lighting": "dim",
                        "notable_features": []}]
    analyzer.analyze_with_retry.return_value = (answer, "raw", 30.0)
    out = vp.analyze_windows(analyzer, _windows()[:1], 10.0, None, "",
                             fps=24.0)
    assert out[0]["scene"] == []
    # The other sections survive the one bad segment.
    assert len(out[0]["camera"]) == 1
    assert out[0]["assessment"]["content_type"] == "person_talking_to_camera"


def test_folded_camera_modes_merge_across_window_boundary():
    mode = {"start": 0, "end": 10, "mode": "mounted", "framing": "medium",
            "stability": "stable", "movement": "stationary"}
    analyzer = MagicMock()
    first = _folded_answer(0.0, 10.0)
    second = _folded_answer(10.0, 20.0)
    first["camera"] = [dict(mode)]
    second["camera"] = [dict(mode, start=10, end=20)]
    analyzer.analyze_with_retry.side_effect = [
        (first, "raw", 30.0), (second, "raw", 30.0)]
    out = vp.analyze_windows(analyzer, _windows(), 20.0, None, "", fps=24.0)
    modes = [m for w in out for m in w["camera"]]
    merged = vp.merge_camera_modes(modes)
    assert len(merged) == 1
    assert (merged[0]["start"], merged[0]["end"]) == (0.0, 20.0)


def test_folded_assessment_votes_merge_with_clip_time_ranges():
    entries = [
        {"window": [0.0, 10.0],
         "assessment": {"content_type": "person_talking_to_camera",
                        "primary_subject_visible": [[0, 10]]}},
        {"window": [10.0, 20.0],
         "assessment": {"content_type": "scenery",
                        "primary_subject_visible": [[10, 20]]}},
        {"window": [20.0, 30.0],
         "assessment": {"content_type": "person_talking_to_camera",
                        "primary_subject_visible": [[20, 30]]}},
    ]
    content_type, psv = vp._merge_assessment_votes(entries)
    assert content_type == "person_talking_to_camera"
    assert psv == [[0.0, 10.0], [10.0, 20.0], [20.0, 30.0]]


def test_folded_assessment_with_no_parsed_window_reports_unmeasured():
    entries = [{"window": [0.0, 10.0], "assessment": None,
                "parse_error": True}]
    content_type, psv = vp._merge_assessment_votes(entries)
    assert content_type == "unknown"
    assert psv is None


def test_folded_assessment_keeps_unanimous_explicit_empty():
    entries = [
        {"window": [0.0, 10.0],
         "assessment": {"content_type": "scenery",
                        "primary_subject_visible": []}},
        {"window": [10.0, 20.0],
         "assessment": {"content_type": "scenery",
                        "primary_subject_visible": []}},
    ]
    content_type, psv = vp._merge_assessment_votes(entries)
    assert content_type == "scenery"
    assert psv == []


def test_unparsed_folded_window_records_parse_error_and_votes_nothing():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = ({}, "raw", 0.5)
    out = vp.analyze_windows(analyzer, _windows()[:1], 10.0, None, "",
                             fps=24.0)
    assert out[0]["parse_error"] is True
    assert out[0]["actions"] == []
    assert out[0]["scene"] == []
    assert out[0]["camera"] == []
    assert out[0]["assessment"] is None
    content_type, psv = vp._merge_assessment_votes(out)
    assert (content_type, psv) == ("unknown", None)


def test_folded_window_passes_its_audio_and_records_sampling():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = (
        _folded_answer(0.0, 10.0), "raw", 0.5)
    clips = [{"index": 0, "start": 0.0, "end": 10.0, "path": "/tmp/w.mp4",
              "has_audio": True}]
    out = vp.analyze_windows(analyzer, clips, 10.0, None, "", fps=24.0)
    _, kwargs = analyzer.analyze_with_retry.call_args
    assert kwargs["audio"] == "/tmp/w.mp4"
    assert out[0]["has_audio"] is True
    assert out[0]["sampling"]["frames"] == 20


def test_sourceless_folded_window_hears_nothing_and_says_so():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = (
        _folded_answer(0.0, 10.0), "raw", 0.5)
    clips = [{"index": 0, "start": 0.0, "end": 10.0, "path": "/tmp/w.mp4",
              "has_audio": False}]
    out = vp.analyze_windows(analyzer, clips, 10.0, None, "", fps=24.0)
    _, kwargs = analyzer.analyze_with_retry.call_args
    assert kwargs["audio"] is None
    assert out[0]["has_audio"] is False


def test_tail_sliver_too_short_to_analyze_is_dropped_not_handed_out(tmp_path):
    """A 10.04 s clip's second window is 0.04 s: cv2 cannot open the husk
    (and a single frame fails `load_video`), so the extractor drops it
    instead of handing a clip the model call raises on."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    source = _synth_clip(tmp_path / "src.mp4", 10.04, with_audio=False)
    meta = vp.probe_clip(source)
    clips = vp.extract_video_clips(
        Path(source), meta["duration_s"], tmp_path / "cache")
    assert len(clips) == 1
    assert clips[0]["end"] == 10.0


def test_cached_window_without_a_video_stream_is_dropped(tmp_path):
    """A cached window file that carries no video stream is dropped rather
    than handed to a pass that raises "Cannot open video" on it."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    source = _synth_clip(tmp_path / "src.mp4", 12, with_audio=True)
    cache = tmp_path / "cache"
    husk_dir = cache / "src" / "clips"
    husk_dir.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=10",
         "-c:a", "aac", str(husk_dir / "clip_000.mp4")],
        capture_output=True, check=True)
    assert vp._file_has_audio(husk_dir / "clip_000.mp4") is True
    assert vp._file_has_video(husk_dir / "clip_000.mp4") is False

    clips = vp.extract_video_clips(Path(source), 12.0, cache)
    assert [c["path"] for c in clips] == [str(husk_dir / "clip_001.mp4")]


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
