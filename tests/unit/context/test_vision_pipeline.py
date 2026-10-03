import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import os
from library.tools.semantic_index import (
    build_semantic_lookup,
    clip_observations,
    describe_clip,
)
from library.tools.vision_schema_adapter import (
    UNMEASURED_SUMMARY,
    adapt_semantic_document,
    adapt_semantic_documents,
    stability_summary,
    usable_ranges_summary,
)
import shutil
import subprocess
import threading
from library.tools.analysis import measurement_layers as ml
from library.tools.analysis import vision_pipeline_v3 as vp
import sys
import textwrap
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from library.tools.gemma_shim import GemmaShim, ShimConfig, server_scope


# Mock heavy mlx_vlm dependency before importing vision_pipeline_v3
mlx_mock = MagicMock()
mlx_mock.load.return_value = (MagicMock(), MagicMock())
mlx_mock.generate.return_value = MagicMock(text="[]")
mlx_prompt_utils = MagicMock()
mlx_prompt_utils.apply_chat_template.return_value = "prompt"
mlx_mock.prompt_utils = mlx_prompt_utils

# Now we can import the pipeline safely

@pytest.fixture
def mock_mlx_functions():
    with patch("library.tools.analysis.vision_pipeline_v3.load", mlx_mock.load), \
         patch("library.tools.analysis.vision_pipeline_v3.generate", mlx_mock.generate), \
         patch("library.tools.analysis.vision_pipeline_v3.apply_chat_template", mlx_prompt_utils.apply_chat_template):
        yield


@pytest.fixture
def sample_temporal_index():
    return {
        "duration_s": 10.0,
        "scene_boundaries": [{"timestamp": 3.5}, {"timestamp": 7.2}, {"timestamp": 0.1}],
        "speech_regions": [{"start": 1.0, "end": 2.5, "text": "hello"}],
        # The shape step 1.04 really writes. This fixture used to say
        # `"camera_motion": {"residual": [...]}` - a key nothing has ever
        # written - so the test passed against a signal the pipeline
        # never read. See library/tools/camera_stability.py.
        "camera_motion_decomposition": {
            "sample_rate_hz": 5,
            "values": [{"translation_x": 0.0, "translation_y": 0.0,
                        "zoom_factor": 1.0, "residual": r}
                       for r in [0.01, 0.015, 0.012] * 4],
        },
    }


@pytest.mark.usefixtures("mock_mlx_functions")
def test_compute_deterministic_assessment(sample_temporal_index):
    transcript = "hello"
    assessment = vp.compute_deterministic_assessment(sample_temporal_index, transcript)
    
    assert assessment["speech_present"] is True
    assert assessment["speech_coverage"] == 0.15 # 1.5s / 10.0s
    # residual mean ~0.0123, under half a grid step of the block search
    assert assessment["camera_stability"] == "stable"
    assert assessment["camera_stability_method"] == "optical_flow_residual"
    # No motion scale and no picture sample, so nothing measured the
    # ranges - and an unmeasured clip claims nothing, not everything.
    assert assessment["usable_ranges_method"] == "unmeasured"  # only 12 motion samples < 30
    assert assessment["usable_ranges"] == []
    assert assessment["unusable_ranges"] == []
    assert assessment["usable_ranges_signals"] == []


@pytest.mark.usefixtures("mock_mlx_functions")
def test_unparsed_windows_assess_as_unknown_with_no_subject_claim():
    """`[]` would say "the subject appears nowhere", and nothing looked.

    Same defect as `usable_ranges: [[0, duration]]`, inverted: an answer
    written where the pass that would have produced it did not run. The
    folded windows carry `assessment: None` when their call did not
    parse, and the vote merge reads that as no vote.
    """
    windows = [{"window": [0.0, 10.0], "assessment": None,
                "parse_error": True}]
    content_type, psv = vp._merge_assessment_votes(windows)
    assert content_type == "unknown"
    assert psv is None

    assessment = vp._finish_assessment(
        {"speech_present": None, "camera_stability": "unknown"},
        content_type, psv, None, 10.0, None)

    assert assessment["content_type"] == "unknown"
    assert assessment["primary_subject_visible"] is None
    assert assessment["usable_ranges_method"] == "unmeasured"
    assert assessment["usable_ranges"] == []


@pytest.mark.usefixtures("mock_mlx_functions")
def test_a_successful_window_vote_keeps_the_model_ranges():
    windows = [{"window": [0.0, 10.0],
                "assessment": {"content_type": "scenery",
                               "primary_subject_visible": [[0, 9]]}}]
    content_type, psv = vp._merge_assessment_votes(windows)
    assert content_type == "scenery"
    assert psv == [[0, 9]]

    assessment = vp._finish_assessment(
        {"camera_stability": "unknown"},
        content_type, psv, None, 10.0, [])

    assert assessment["content_type"] == "scenery"
    assert assessment["primary_subject_visible"] == [[0, 9]]
    assert assessment["usable_ranges_method"] == "deterministic_v1"
    assert assessment["usable_ranges"] == [[0, 10.0]]


def _sample_compact_answer():
    return {
        "a": [[0, 10, "speaking to camera, gesturing",
               "steady pace, clear delivery", "upright, hands visible"],
              [0, 10, "nodding while listening", None, "head tilted"]],
        "s": [[0, 10, "home office", "indoor", "warm lamp light",
               "whiteboard; desk"]],
        "c": [[0, 10, "selfie", "close-up", "steady", "stationary"]],
        "t": "person_talking_to_camera",
        "p": [[0, 10]],
    }


@pytest.mark.usefixtures("mock_mlx_functions")
def test_expand_compact_window_keeps_every_consumed_field():
    """The compact schema must expand to the canonical field names.

    Every key below is read downstream (see `expand_compact_window`'s
    docstring for the consumer list): a rename that drops one fails
    loudly here instead of silently starving a planning step.
    """
    out = vp.expand_compact_window(_sample_compact_answer())
    assert out["actions"] == [
        {"start": 0.0, "end": 10.0, "action": "speaking to camera, gesturing",
         "speech_cue": "steady pace, clear delivery",
         "body_language": "upright, hands visible"},
        {"start": 0.0, "end": 10.0, "action": "nodding while listening",
         "speech_cue": None, "body_language": "head tilted"},
    ]
    assert out["scene"] == [
        {"start": 0.0, "end": 10.0, "location": "home office",
         "type": "indoor", "lighting": "warm lamp light",
         "notable_features": ["whiteboard", "desk"]},
    ]
    assert out["camera"] == [
        {"start": 0.0, "end": 10.0, "mode": "selfie", "framing": "close-up",
         "stability": "steady", "movement": "stationary"},
    ]
    assert out["assessment"] == {
        "content_type": "person_talking_to_camera",
        "primary_subject_visible": [[0, 10]],
    }


@pytest.mark.usefixtures("mock_mlx_functions")
def test_expand_compact_window_absent_key_is_not_an_empty_answer():
    """A section without its key stays missing, like the canonical path,
    and a malformed row drops only itself."""
    out = vp.expand_compact_window({"t": "scenery"})
    assert "actions" not in out
    assert "scene" not in out
    assert "camera" not in out
    assert out["assessment"] == {"content_type": "scenery"}

    out = vp.expand_compact_window({"a": []})
    assert out["actions"] == []
    assert out["assessment"] is None

    # Bad timestamps or prose of the wrong type drop the row only.
    out = vp.expand_compact_window({
        "a": [
            [0, 10, "speaking", None, "upright"],       # kept
            ["x", 10, "speaking", None, "upright"],     # bad start
            [0, 10, "speaking"],                        # wrong length
            [5, 5, "speaking", None, "upright"],        # empty span
            [0, 10, None, None, "upright"],             # action not prose
            [0, 10, "speaking", 7, "upright"],          # cue not prose/null
        ],
        "s": [[0, 10, "office", "indoor", "warm", ["desk"]]],  # feats not str
        "c": [[0, 10, "selfie", "close-up", "steady", "stationary"]],
        "t": "scenery",
        "p": [[0, 10], "nonsense", [2, 1]],
    })
    assert len(out["actions"]) == 1
    assert out["actions"][0]["action"] == "speaking"
    assert out["scene"] == []
    assert len(out["camera"]) == 1
    assert out["assessment"]["primary_subject_visible"] == [[0, 10]]


@pytest.mark.usefixtures("mock_mlx_functions")
def test_expand_compact_window_scene_features():
    """The "s" features string splits on ";" ("" means none observed),
    and a 7-item row (joined string plus extra items, measured
    2026-09-24 on burned-in captions) merges rather than dropping."""
    out = vp.expand_compact_window({
        "s": [[0, 10, "office", "indoor", "warm", "whiteboard;  ; desk"],
              [0, 10, "hall", "indoor", "dim", ""],
              [0.0, 10.0, "Dark room", "indoor", "Dim",
               "Geometric wall patterns;Text: FICTUR_ONE",
               "Text: FICTUR_ONE"]],
    })
    assert out["scene"][0]["notable_features"] == ["whiteboard", "desk"]
    assert out["scene"][1]["notable_features"] == []
    assert out["scene"][2]["notable_features"] == [
        "Geometric wall patterns", "Text: FICTUR_ONE", "Text: FICTUR_ONE"]


@pytest.mark.usefixtures("mock_mlx_functions")
def test_expand_or_canonical_passes_canonical_through():
    """A model answering in canonical keys is not emptied by expansion."""
    canonical = {
        "actions": [{"start": 0, "end": 10, "action": "speaking",
                     "speech_cue": None, "body_language": "upright"}],
        "scene": [], "camera": [],
        "assessment": {"content_type": "scenery",
                       "primary_subject_visible": []},
    }
    assert vp._expand_or_canonical(canonical) is canonical

    # Compact keys win: a half-canonical tail is not a second answer.
    mixed = {"a": [[0, 10, "speaking", None, "upright"]],
             "actions": [{"start": 0, "end": 1, "action": "stale"}],
             "t": "scenery"}
    out = vp._expand_or_canonical(mixed)
    assert out["actions"] == [
        {"start": 0.0, "end": 10.0, "action": "speaking",
         "speech_cue": None, "body_language": "upright"}]
    assert out["assessment"] == {"content_type": "scenery"}


class _FakeAnalyzer:
    """Returns one canned answer; records what the window asked for."""

    def __init__(self, text):
        self.text = text
        self.calls = []

    def analyze_with_retry(self, prompt, parse_fn, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return parse_fn(self.text), self.text, 19.5


# The exact malformed diet answers measured 2026-09-24 on source
# footage (W02: next-section key opened inside the previous array plus
# fences; W07: next key straight inside the array; W10: a trailing
# comma and an empty extra item). Each must repair to the full field
# set rather than dropping the window.
W02_MALFORMED = (
    '```json\n{"a": [[60, 70, "speaking to camera", '
    '"moderate pace, clear articulation", '
    '"facing camera, hand near face, neutral expression"], '
    '["s": [[60, 70, "outdoor urban area", "outdoor", "daylight", '
    '"geometric sculptures;plants"], "c": [[60, 70, "handheld", '
    '"medium", "slight shake", "stationary"], '
    '"t": "person_talking_to_camera", "p": [[60, 70]]]}\n```'
)

W07_MALFORMED = (
    '{"a": [[55, 65, "man speaking and gesturing with hands", '
    '"moderate pace, clear mouth movements", '
    '"standing upright, hand gestures, neutral expression"]], '
    '"s": [[55, 65, "modern interior with geometric wall", "indoor", '
    '"dim lighting", "geometric wall panels;dark gray wall"], '
    '"c": [[55, 65, "handheld", "medium", "steady", "stationary"]], '
    '"t": "person_talking_to_camera", "p": [[55, 65]]}'
)

W10_MALFORMED = (
    '{"a": [[295, 305, "speaking to camera", '
    '"moderate pace, clear articulation", '
    '"upright posture, hand gestures, neutral expression"]], '
    '"s": [[295, 305, "geometric wall and floor", "indoor", '
    '"dim lighting", "geometric wall patterns;dark floor", ""],], '
    '"c": [[295, 305, "handheld", "medium", "slight shake", '
    '"stationary"]], "t": "person_talking_to_camera", '
    '"p": [[295, 305]]}'
)


@pytest.mark.usefixtures("mock_mlx_functions")
def test_parse_compact_window_repairs_the_measured_shapes_only():
    """Each measured malformed shape repairs with every section kept;
    strict JSON parses unrepaired; gibberish stays ({}, False) so the
    caller fires the fallback."""
    for text, scene_type in ((W02_MALFORMED, "outdoor"),
                             (W07_MALFORMED, "indoor"),
                             (W10_MALFORMED, "indoor")):
        parsed, repaired = vp.parse_compact_window(text)
        assert repaired is True
        assert sorted(parsed) == ["a", "c", "p", "s", "t"]
        out = vp._expand_or_canonical(parsed)
        assert len(out["actions"]) == 1
        assert out["scene"][0]["type"] == scene_type
        assert out["camera"][0]["mode"] == "handheld"
        assert out["assessment"]["content_type"] == "person_talking_to_camera"
    parsed, _ = vp.parse_compact_window(W10_MALFORMED)
    assert vp._expand_or_canonical(parsed)["scene"][0]["notable_features"] \
        == ["geometric wall patterns", "dark floor"]

    parsed, repaired = vp.parse_compact_window(
        '{"a": [[0, 10, "speaking", null, "upright"]], '
        '"t": "person_talking_to_camera"}')
    assert repaired is False
    assert parsed["t"] == "person_talking_to_camera"
    assert vp.parse_compact_window("not json at all {{{") == ({}, False)
    assert vp.parse_compact_window("") == ({}, False)


class _SeqAnalyzer(_FakeAnalyzer):
    """Canned answers in call order - compact first, fallback second."""

    def __init__(self, texts):
        super().__init__(texts[0])
        self.texts = texts

    def analyze_with_retry(self, prompt, parse_fn, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        text = self.texts[min(len(self.calls) - 1, len(self.texts) - 1)]
        return parse_fn(text), text, 19.5


def _window_clips():
    return [{"start": 0.0, "end": 10.0, "path": "/tmp/probe.mp4",
             "has_audio": True}]


@pytest.mark.usefixtures("mock_mlx_functions")
def test_analyze_windows_runs_the_compact_prompt_and_expands():
    """The wired path: compact prompt, 600-token cap, canonical entry,
    one call and the path recorded - and a repaired answer is kept, not
    re-asked."""
    import json as _json
    answer = _json.dumps({
        "a": [[0, 10, "speaking to camera", None, "upright"]],
        "s": [[0, 10, "office", "indoor", "warm", "whiteboard"]],
        "c": [[0, 10, "handheld", "medium", "steady", "stationary"]],
        "t": "person_talking_to_camera",
        "p": [[0, 10]],
    })
    analyzer = _FakeAnalyzer(answer)
    entries = vp.analyze_windows(analyzer, _window_clips(), 10.0, None, "")
    assert len(analyzer.calls) == 1
    call = analyzer.calls[0]
    assert '"a"' in call["prompt"] and '"actions"' not in call["prompt"]
    assert call["kwargs"]["max_tokens"] == vp.MAX_TOKENS["window_all_compact"]
    assert call["kwargs"]["audio"] == "/tmp/probe.mp4"
    entry = entries[0]
    assert entry["window"] == [0.0, 10.0]
    assert entry["prompt_path"] == "compact"
    assert entry["actions"][0]["action"] == "speaking to camera"
    assert entry["scene"][0]["notable_features"] == ["whiteboard"]
    assert entry["camera"][0]["mode"] == "handheld"
    assert entry["assessment"]["content_type"] == "person_talking_to_camera"
    assert "parse_error" not in entry

    analyzer = _FakeAnalyzer(W02_MALFORMED)
    entry = vp.analyze_windows(analyzer, _window_clips(), 10.0, None, "")[0]
    assert len(analyzer.calls) == 1
    assert entry["prompt_path"] == "compact_repaired"
    assert "parse_error" not in entry
    assert entry["actions"][0]["action"] == "speaking to camera"


@pytest.mark.usefixtures("mock_mlx_functions")
def test_window_cache_matches_cold_results_and_remeasures_only_changed_window(
        tmp_path):
    """A local transcript edit must miss just its own Gemma window.

    The first cached pass is cold and matches today's ordered
    ``analyze_windows`` result exactly. Editing only the second window's
    word-timed transcript then reuses the first and asks the model once
    for the changed second window.
    """
    import copy as _copy
    import json as _json

    clips = [
        {"start": 0.0, "end": 10.0, "path": "/tmp/window-0.mp4",
         "has_audio": True},
        {"start": 10.0, "end": 20.0, "path": "/tmp/window-1.mp4",
         "has_audio": True},
    ]
    temporal_index = {
        "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 2.0, "text": "first window words"},
            {"start": 11.0, "end": 12.0, "text": "second window words"},
        ],
        "scene_boundaries": [{"timestamp": 5.0}, {"timestamp": 15.0}],
    }
    answer = _json.dumps(_sample_compact_answer())

    baseline_analyzer = _FakeAnalyzer(answer)
    baseline = vp.analyze_windows(
        baseline_analyzer, clips, 20.0, temporal_index, "", fps=30.0)

    cache_root = tmp_path / "source-memory"

    def cache():
        return ml.LayerCache(
            "source-content-digest", {"windows": "prompt-method-digest"},
            {}, root=cache_root)

    cold_analyzer = _FakeAnalyzer(answer)
    cold_cached = vp.analyze_windows_cached(
        cold_analyzer, clips, 20.0, temporal_index, "", 30.0, cache())
    assert cold_cached == baseline
    assert len(cold_analyzer.calls) == 2

    changed_index = _copy.deepcopy(temporal_index)
    changed_index["speech_regions"][1]["text"] = "updated second window words"
    changed_index["scene_boundaries"][1]["timestamp"] = 16.0
    partial_analyzer = _FakeAnalyzer(answer)
    partial = vp.analyze_windows_cached(
        partial_analyzer, clips, 20.0, changed_index, "", 30.0, cache())

    assert len(partial_analyzer.calls) == 1
    assert "updated second window words" in partial_analyzer.calls[0]["prompt"]
    assert "first window words" not in partial_analyzer.calls[0]["prompt"]
    assert (
        "Detected visual changes at: 6.0s"
        in partial_analyzer.calls[0]["prompt"])
    assert partial[0] == cold_cached[0]
    assert partial[1]["window"] == cold_cached[1]["window"]


@pytest.mark.usefixtures("mock_mlx_functions")
def test_analyze_windows_falls_back_to_full_prompt():
    """An unparseable compact answer is re-asked with the canonical
    prompt; the re-ask recovers the window, and only when both fail is
    it recorded UNPARSED - a dropped window is never acceptable."""
    import json as _json
    analyzer = _SeqAnalyzer(["not json at all {{{",
                             "still not json ][["])
    entries = vp.analyze_windows(analyzer, _window_clips(), 10.0,
                                 None, "")
    assert len(analyzer.calls) == 2
    assert '"a"' in analyzer.calls[0]["prompt"]
    assert analyzer.calls[0]["kwargs"]["max_tokens"] == \
        vp.MAX_TOKENS["window_all_compact"]
    assert '"actions"' in analyzer.calls[1]["prompt"]
    assert analyzer.calls[1]["kwargs"]["max_tokens"] == \
        vp.MAX_TOKENS["window_all"]
    entry = entries[0]
    assert entry["prompt_path"] == "full_fallback"
    assert entry["parse_error"] is True
    assert entry["actions"] == []

    canonical = _json.dumps({
        "actions": [{"start": 0, "end": 10, "action": "speaking",
                     "speech_cue": None, "body_language": "upright"}],
        "scene": [{"start": 0, "end": 10, "location": "office",
                   "type": "indoor", "lighting": "warm",
                   "notable_features": ["whiteboard"]}],
        "camera": [{"start": 0, "end": 10, "mode": "handheld",
                    "framing": "medium", "stability": "steady",
                    "movement": "stationary"}],
        "assessment": {"content_type": "person_talking_to_camera",
                       "primary_subject_visible": [[0, 10]]},
    })
    analyzer = _SeqAnalyzer(["not json at all {{{", canonical])
    entry = vp.analyze_windows(analyzer, _window_clips(), 10.0, None, "")[0]
    assert len(analyzer.calls) == 2
    assert entry["prompt_path"] == "full_fallback"
    assert "parse_error" not in entry
    assert entry["actions"][0]["action"] == "speaking"
    assert entry["scene"][0]["notable_features"] == ["whiteboard"]
    assert entry["analysis_time_s"] == 39.0


# ── Findings 2 and 3 (rung 0): still paths the host can open ──

@pytest.mark.usefixtures("mock_mlx_functions")
def test_coarse_frames_from_a_relative_cache_are_absolute_and_handable(
        tmp_path, monkeypatch):
    """Finding 2: `vision_pipeline_v3` handed the host relative still
    paths (`.vision_cache/IMG_1806/frames/frame_0000.jpg`) and
    `llm_handshake._checked_images` refused them (`not an absolute
    path`), so every clip's object pass failed in agent mode. The
    extractor resolves the cache, so what it returns is handable -
    pinned here with the scout's own stem, without running ffmpeg (the
    frames are pre-extracted, so the usable cache is read, not written).
    """
    import os as _os

    from library.tools import llm_handshake

    monkeypatch.chdir(tmp_path)
    stem = "IMG_1806"
    frame_dir = tmp_path / ".vision_cache" / stem / "frames"
    frame_dir.mkdir(parents=True)
    for i in range(4):  # 12 s at 1 frame per 5 s, plus the clamped tail
        (frame_dir / f"frame_{i:04d}.jpg").write_bytes(
            b"\xff\xd8" + b"0" * 64)
    frames = vp.extract_frames(
        Path(f"{stem}.MOV"), 12.0, Path(".vision_cache"))
    assert len(frames) == 4
    paths = [f["path"] for f in frames]
    assert all(_os.path.isabs(p) for p in paths), paths
    request = llm_handshake.build_request(
        "objects__stills", "p", "", "c", "[]", str(tmp_path), "t",
        images=paths)
    assert request["images"] == paths

    # The detail half: the same refusal through `extract_detail_frames`.
    frame_dir = tmp_path / ".vision_cache" / "IMG_1809" / "detail_frames"
    frame_dir.mkdir(parents=True)
    for i in range(2):
        (frame_dir / f"detail_{i:04d}.jpg").write_bytes(
            b"\xff\xd8" + b"0" * 64)
    out = vp.extract_detail_frames(
        Path("IMG_1809.MOV"), Path(".vision_cache"), [(0.0, 2.0)])
    frames = out[(0.0, 2.0)]
    assert len(frames) == 2
    assert all(_os.path.isabs(f["path"]) for f in frames)


@pytest.mark.usefixtures("mock_mlx_functions")
def test_detail_extraction_with_a_cache_miss_returns_the_range_map(
        tmp_path, monkeypatch):
    """Finding 3: `extract_detail_frames` assigned `subprocess.run`'s
    return over the result dict, so any clip needing detail frames
    (IMG_1809 on the scout run) died with
    `TypeError: 'CompletedProcess' object does not support item
    assignment`. A cache miss must return the {(start, end): frames}
    map - ffmpeg is stubbed (it writes the frame), the shadowing is
    what is pinned.
    """
    from subprocess import CompletedProcess

    monkeypatch.chdir(tmp_path)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"\xff\xd8" + b"0" * 64)
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    out = vp.extract_detail_frames(
        Path("IMG_1809.MOV"), Path(".vision_cache"), [(0.0, 2.0)])
    assert set(out) == {(0.0, 2.0)}
    assert [f["timestamp"] for f in out[(0.0, 2.0)]] == [0.0, 2.0]


@pytest.mark.usefixtures("mock_mlx_functions")
def test_every_ffprobe_spawn_moves_the_counter_even_on_failure():
    """The thrift work is judged by spawn count, so the counter must see
    every spawn path - including the ones that fail. A probe of a file
    that is not there still started a process; a counter that only
    counts successes undercounts the baseline it is meant to shrink."""
    vp.reset_ffprobe_spawn_count()
    assert vp.ffprobe_spawn_count() == 0
    assert vp._file_has_audio(Path("/nonexistent/clip.mp4")) is False
    assert vp._file_has_video(Path("/nonexistent/clip.mp4")) is False
    assert vp._video_height(Path("/nonexistent/clip.mp4")) == 0
    assert vp.probe_clip(Path("/nonexistent/clip.mp4")) is None
    assert vp.ffprobe_spawn_count() == 4
    # The combined cached-window probe is one spawn the counter must see.
    vp.reset_ffprobe_spawn_count()
    has_video, has_audio, height = vp._probe_window_streams(
        Path("/nonexistent/clip.mp4"))
    assert (has_video, has_audio, height) == (False, False, 0)
    assert vp.ffprobe_spawn_count() == 1
    vp.reset_ffprobe_spawn_count()


class _StubWindowAnalyzer:
    """One canned folded answer per window, no model anywhere near it."""

    def analyze_with_retry(self, prompt, parse_fn, images=None, video=None,
                           max_tokens=512, label="pass", audio=None,
                           request_kind=None, request_id=None):
        if label.startswith("Objects"):
            return [], json.dumps([]), 0.1
        result = {
            "actions": [],
            "scene": [{"start": 0.0, "end": 10.0, "description": "room"}],
            "camera": [],
            "assessment": {"content_type": "person_talking_to_camera",
                           "primary_subject_visible": [[0, 10]]},
        }
        return result, json.dumps(result), 0.1


@pytest.mark.usefixtures("mock_mlx_functions")
def test_analyze_clip_records_the_inference_wall_beside_the_model_sum():
    """The extraction-vs-inference split needs both halves: the summed
    per-window model times are not a wall (retries and gaps hide in
    them), so the profile carries the measured wall too. Without it the
    decode-ahead work cannot be judged."""
    from unittest.mock import patch

    meta = {"clip_id": "IMG_1816", "file_path": "/footage/IMG_1816.MOV",
            "duration_s": 10.0, "fps": 30.0, "resolution": [1920, 1080]}
    video_clips = [{"index": 0, "start": 0.0, "end": 10.0,
                    "path": "/tmp/stub.mp4", "has_audio": False}]
    with patch.object(vp.picture_quality, "measure_soft_picture",
                      return_value=[]):
        profile = vp.analyze_clip(
            _StubWindowAnalyzer(), meta, [], video_clips, "", None,
            "/tmp/nonexistent-cache")
    metadata = profile["analysis_metadata"]
    assert metadata["window_inference_wall_s"] >= 0.0
    assert isinstance(metadata["window_inference_wall_s"], float)
    assert profile["actions"][0]["prompt_path"] == "compact"
    json.dumps(profile)


def test_host_still_overlap_preserves_serial_profile_and_commit_order(
        monkeypatch):
    """The same fake answers compose identically, while host still work
    overlaps ordered windows and all measurement-layer commits stay on
    the composing thread in their established order.
    """
    import copy
    import threading

    from library.tools import still_vision

    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    monkeypatch.setattr(vp.picture_quality, "measure_soft_picture",
                        lambda *_args: [])

    windows = [
        {"window": [0.0, 10.0], "actions": [{"action": "speaking"}],
         "analysis_time_s": 0.25, "prompt_path": "compact",
         "scene": [{"start": 0.0, "end": 10.0,
                    "description": "room"}],
         "camera": [],
         "assessment": {"content_type": "person_talking_to_camera",
                        "primary_subject_visible": [[0.0, 10.0]]},
         "has_audio": False},
        {"window": [10.0, 20.0], "actions": [{"action": "turning"}],
         "analysis_time_s": 0.5, "prompt_path": "compact",
         "scene": [{"start": 10.0, "end": 20.0,
                    "description": "room"}],
         "camera": [],
         "assessment": {"content_type": "person_talking_to_camera",
                        "primary_subject_visible": [[10.0, 20.0]]},
         "has_audio": False},
    ]
    coarse_objects = [{"label": "person", "appearances": [[1.0, 2.0]]}]
    detailed_objects = [
        {"label": "person", "appearances": [[1.0, 2.0]],
         "details": ["standing"]},
    ]
    window_clips = [
        {"index": i, "start": start, "end": start + 10.0,
         "path": f"/tmp/window-{i}.mp4", "has_audio": False}
        for i, start in enumerate((0.0, 10.0))
    ]
    meta = {"clip_id": "IMG_1816", "file_path": "/footage/IMG_1816.MOV",
            "duration_s": 20.0, "fps": 30.0,
            "resolution": [1920, 1080]}
    frames = [{"path": f"/tmp/still-{i}.jpg", "timestamp": i * 5.0}
              for i in range(4)]

    class RecordingLayerCache:
        def __init__(self):
            self.writes = []

        def get(self, _layer):
            return None

        def get_window(self, *_args):
            return None

        def put_window(self, start, _end, _params, _value):
            self.writes.append(("window", start,
                                threading.current_thread().name))

        def put(self, layer, _value):
            self.writes.append((layer, threading.current_thread().name))

        def record(self):
            return {}

    def run(harness):
        window_active = threading.Event()
        detail_finished = threading.Event()
        object_order = []
        layer_cache = RecordingLayerCache()
        analyzer = SimpleNamespace(harness=harness)

        def fake_windows(_analyzer, clips, *_args, **_kwargs):
            entries = []
            for clip in clips:
                if harness == "agent":
                    window_active.set()
                entries.extend(copy.deepcopy([
                    entry for entry in windows
                    if entry["window"][0] == clip["start"]]))
            if harness == "agent":
                assert detail_finished.wait(2), \
                    "host object branch did not overlap window analysis"
            return entries

        def fake_coarse(_analyzer, _frames, _duration, request_prefix):
            object_order.append(("coarse", request_prefix))
            if harness == "agent":
                assert window_active.wait(2), \
                    "window analysis had not started before host still work"
            return copy.deepcopy(coarse_objects), 1.5

        def fake_detail(_analyzer, _path, _cache, coarse, _duration,
                        request_prefix):
            object_order.append(("detail", request_prefix))
            if harness == "agent":
                detail_finished.set()
            assert coarse == coarse_objects
            return copy.deepcopy(detailed_objects), 2.5

        monkeypatch.setattr(vp, "analyze_windows", fake_windows)
        monkeypatch.setattr(vp, "analyze_objects_coarse", fake_coarse)
        monkeypatch.setattr(vp, "find_detail_ranges",
                            lambda *_args: [(1.0, 3.0)])
        monkeypatch.setattr(vp, "analyze_objects_detail", fake_detail)
        profile = vp.analyze_clip(
            analyzer, meta, frames, window_clips, "", None,
            "/tmp/nonexistent-cache", layer_cache=layer_cache,
            clock=lambda: 100.0)
        return profile, object_order, layer_cache.writes

    serial_profile, serial_objects, serial_writes = run(None)
    parallel_profile, parallel_objects, parallel_writes = run("agent")

    assert serial_profile == parallel_profile
    assert serial_objects == [("coarse", "IMG_1816"),
                              ("detail", "IMG_1816")]
    assert parallel_objects == [("coarse", "IMG_1816"),
                                ("detail", "IMG_1816")]
    expected_writes = [
        ("window", 0.0, "MainThread"),
        ("window", 10.0, "MainThread"),
        ("windows", "MainThread"),
        ("objects", "MainThread"),
        ("picture", "MainThread"),
    ]
    assert serial_writes == expected_writes
    assert parallel_writes == expected_writes


def test_failed_host_object_branch_writes_no_partial_profile(
        tmp_path, monkeypatch):
    """A failed still branch aborts the clip before run_pipeline writes
    its per-clip profile or combined index.
    """
    from contextlib import nullcontext

    source = tmp_path / "IMG_1816.MOV"
    source.write_bytes(b"fake video")
    output_dir = tmp_path / "pipeline_output"
    analyzer = SimpleNamespace(harness="agent")

    class ClosableWindowClips(list):
        closed = False

        def close(self):
            self.closed = True

    window_clips = ClosableWindowClips([{
        "index": 0, "start": 0.0, "end": 10.0,
        "path": str(tmp_path / "window.mp4"), "has_audio": False,
    }])

    class EmptyLayerCache:
        def get(self, _layer):
            return None

        def get_window(self, *_args):
            return None

        def put_window(self, *_args):
            pass

        def put(self, *_args):
            pass

        def record(self):
            return {}

    monkeypatch.setattr(vp, "_LazyAnalyzer",
                        lambda *_args, **_kwargs: nullcontext(analyzer))
    monkeypatch.setattr(vp.measurement_layers, "method_digest",
                        lambda *_args: "fake-method")
    monkeypatch.setattr(vp.measurement_layers, "canonical_digest",
                        lambda *_args: "fake-inputs")
    monkeypatch.setattr(vp.measurement_layers.LayerCache, "for_source",
                        lambda *_args, **_kwargs: EmptyLayerCache())
    monkeypatch.setattr(vp, "probe_clip", lambda path: {
        "clip_id": path.stem, "file_path": str(path),
        "duration_s": 10.0, "fps": 30.0,
        "resolution": [1920, 1080]})
    monkeypatch.setattr(vp, "load_temporal_index", lambda *_args: None)
    monkeypatch.setattr(vp, "load_transcript_text", lambda *_args: "")
    monkeypatch.setattr(vp, "extract_frames", lambda *_args: [
        {"path": str(tmp_path / "still.jpg"), "timestamp": 0.0}])
    monkeypatch.setattr(vp, "extract_video_clips",
                        lambda *_args, **_kwargs: window_clips)
    monkeypatch.setattr(vp, "analyze_windows", lambda *_args, **_kwargs: [{
        "window": [0.0, 10.0], "actions": [], "analysis_time_s": 0.1,
        "prompt_path": "compact", "scene": [], "camera": [],
        "assessment": None,
    }])

    def fail_objects(*_args, **_kwargs):
        raise RuntimeError("recorded object-branch failure")

    monkeypatch.setattr(vp, "analyze_objects_coarse", fail_objects)

    with pytest.raises(RuntimeError, match="recorded object-branch failure"):
        vp.run_pipeline.__wrapped__(
            [source], cache_dir=tmp_path / "cache", output_dir=output_dir,
            force=True, harness="agent", project_folder=str(tmp_path))

    assert not (output_dir / "clip_profile_IMG_1816_v3.json").exists()
    assert not (output_dir / "vision_index_v3.json").exists()
    assert window_clips.closed


def test_no_image_host_keeps_object_passes_after_windows(monkeypatch):
    """No host leaves the existing same-device serial execution order."""
    from library.tools import still_vision

    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    monkeypatch.setattr(vp.picture_quality, "measure_soft_picture",
                        lambda *_args: [])
    order = []
    meta = {"clip_id": "IMG_1816", "file_path": "/footage/IMG_1816.MOV",
            "duration_s": 10.0, "fps": 30.0,
            "resolution": [1920, 1080]}
    clips = [{"index": 0, "start": 0.0, "end": 10.0,
              "path": "/tmp/window.mp4", "has_audio": False}]

    def fake_windows(*_args, **_kwargs):
        order.append("windows")
        return [{"window": [0.0, 10.0], "actions": [],
                 "analysis_time_s": 0.1, "prompt_path": "compact",
                 "scene": [], "camera": [], "assessment": None}]

    def fake_coarse(*_args, **_kwargs):
        order.append("objects_coarse")
        return [], 0.2

    monkeypatch.setattr(vp, "analyze_windows", fake_windows)
    monkeypatch.setattr(vp, "analyze_objects_coarse", fake_coarse)
    monkeypatch.setattr(vp, "find_detail_ranges", lambda *_args: [])

    vp.analyze_clip(
        SimpleNamespace(harness=None), meta, [], clips, "", None,
        "/tmp/nonexistent-cache")

    assert order == ["windows", "objects_coarse"]


@pytest.mark.usefixtures("mock_mlx_functions")
def test_retried_window_writes_one_per_attempt_row(tmp_path, monkeypatch):
    ledger = tmp_path / "perf_ledger.jsonl"
    monkeypatch.setenv(vp.perf_ledger.LEDGER_ENV, str(ledger))
    monkeypatch.setenv(vp.perf_ledger.RUN_ENV, "run-test")
    monkeypatch.setenv(vp.perf_ledger.CAPABILITY_ENV, "semantic_analysis")
    answers = iter([SimpleNamespace(text="not json"),
                    SimpleNamespace(text='{"objects": []}')])
    monkeypatch.setattr(vp, "generate",
                        lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(vp, "apply_chat_template",
                        lambda *args, **kwargs: "formatted")
    analyzer = vp.VisionAnalyzer(
        SimpleNamespace(config=SimpleNamespace(_commit_hash="abc123")),
        object())

    result, _raw, _elapsed = analyzer.analyze_with_retry(
        "Describe this window.", vp.parse_json_object,
        video="/tmp/window.mp4", request_kind="window",
        request_id="window:0.000-10.000:compact")

    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    attempts = [row for row in rows if "attempt_number" in row]
    assert result == {"objects": []}
    assert len(attempts) == 2
    assert [row["attempt_number"] for row in attempts] == [1, 2]
    assert [row["parser_outcome"] for row in attempts] == ["empty", "parsed"]
    assert all(row["request_kind"] == "window" for row in attempts)
    assert all(row["input_kind"] == "video" for row in attempts)
    assert all(row["backend"] == "mlx_vlm" for row in attempts)
    assert all(row["model_version"] == "abc123" for row in attempts)
    assert all(row["elapsed_s"] >= 0 for row in attempts)
    assert all(row["fallback_cause"] == [] for row in attempts)


# --------------------------------------------------------------------------
# From test_vision_schema_adapter.py
#
# The v3 vision schema must reach the steps that read the retired one.
#
# `vision_pipeline_v3.py` measures framing, camera stability, usable ranges
# and subject visibility per time range, and every consumer addressed a
# schema that has none of those keys.  Nothing joined the two, so a re-run
# of the analysis fed step 3.02 documents it could not describe and the
# step failed outright.  These tests pin the join.

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Shaped exactly like a profile the v3 analyser writes - see
# pipeline_output/clip_profile_IMG_1814_v3.json in a real project.
V3_PROFILE = {
    "clip_id": "IMG_1814",
    "file_path": "/footage/IMG_1814.MOV",
    "duration_s": 45.943,
    "fps": 30.0,
    "resolution": [1920, 1080],
    "transcript": "today is... what even is today?",
    "scene": [
        {
            "start": 0.0,
            "end": 46.0,
            "location": "Outdoor parking lot and construction site",
            "type": "outdoor",
            "lighting": "Daylight",
            "notable_features": ["Construction site with steel frame"],
        }
    ],
    "camera": [
        {"start": 0, "end": 12, "mode": "handheld", "framing": "wide",
         "stability": "stable", "movement": "stationary"},
        {"start": 12, "end": 46, "mode": "handheld", "framing": "close-up",
         "stability": "unstable", "movement": "panning_right"},
    ],
    "actions": [
        {
            "window": [0, 10],
            "actions": [
                {"start": 0, "end": 10,
                 "action": "The person turns away and walks forward.",
                 "speech_cue": None,
                 "body_language": "Neutral expression, then turns right."},
            ],
        }
    ],
    "objects": [
        {"label": "young man in a black baseball cap", "role": "primary_subject",
         "category": "person", "appearances": [[0.0, 45.8]], "readable_text": None},
        {"label": "steel frame", "role": "background", "category": "structure",
         "appearances": [[0.0, 20.0]], "readable_text": None},
    ],
    "assessment": {
        "speech_present": True,
        "speech_coverage": 0.32,
        "camera_stability": "unstable",
        "usable_ranges": [[0, 45.943]],
        "unusable_ranges": [],
        "usable_ranges_method": "deterministic_v1",
        "usable_ranges_signals": ["motion_energy"],
        "content_type": "person_talking_to_camera",
        "primary_subject_visible": [[0, 46]],
    },
    "analysis_metadata": {"pipeline_version": "v3", "frames_extracted": 11},
}

# The retired shape, as the 2026-07-12 profiles on disk still carry it.
LEGACY_PROFILE = {
    "clip_id": "IMG_1814",
    "file_path": "/footage/IMG_1814.MOV",
    "duration_s": 45.943,
    "blocks": [{"label": "Intro", "timestamp_range": "0:00-0:09",
                "visual": "A man walks into a construction site."}],
    "analysis": {"scene": "The image depicts an outdoor urban plaza.",
                 "motion": "The camera is static.",
                 "mood": "pensive"},
    "assessment": {"clip_type": "a_roll", "interest_score": 6,
                   "keywords": ["vlog", "talking_head"]},
}


def test_legacy_documents_pass_through_untouched():
    """A project whose stored state predates v3 must keep working."""
    assert adapt_semantic_document(LEGACY_PROFILE) == LEGACY_PROFILE


def test_adapter_does_not_invent_fields_v3_never_measured():
    """An absent field warns downstream; a fabricated one misleads."""
    assessment = adapt_semantic_document(V3_PROFILE)["assessment"]
    assert "interest_score" not in assessment
    assert "moment_type" not in assessment
    assert "mood" not in adapt_semantic_document(V3_PROFILE)["analysis"]


def test_usable_portions_tell_excluded_unmeasured_and_legacy_apart():
    """`usable_ranges: []` is a verdict, an absent measurement is said in
    words, and a document predating the method field keeps its own prose.

    A blank cell is what "no bound" looks like, and the B-roll selector
    reads this cell to decide which seconds of a clip it may cut: it has
    to tell "excluded" from "nobody looked" from "you may cut anywhere".
    """
    excluded = dict(V3_PROFILE)
    excluded["assessment"] = dict(
        V3_PROFILE["assessment"],
        usable_ranges=[],
        unusable_ranges=[
            {"start": 0.0, "end": 20.0, "reason": "sustained_high_motion"},
            {"start": 20.0, "end": 45.9, "reason": "subject_absent"},
        ],
    )

    portions = adapt_semantic_document(excluded)["assessment"]["usable_portions"]
    assert portions == (
        "none - whole clip excluded (sustained_high_motion, subject_absent)")
    assert clip_observations(excluded)["usable_ranges"] == portions

    unmeasured = dict(V3_PROFILE)
    unmeasured["assessment"] = dict(
        V3_PROFILE["assessment"],
        usable_ranges=[],
        unusable_ranges=[],
        usable_ranges_method="unmeasured",
        usable_ranges_signals=[],
    )

    assessment = adapt_semantic_document(unmeasured)["assessment"]
    assert assessment["usable_portions"] == UNMEASURED_SUMMARY
    assert clip_observations(unmeasured)["usable_ranges"] == UNMEASURED_SUMMARY
    assert "0.0-45.9s" not in clip_observations(unmeasured)["usable_ranges"]

    legacy = dict(V3_PROFILE)
    legacy["assessment"] = {
        k: v for k, v in V3_PROFILE["assessment"].items()
        if not k.startswith("usable_ranges")
    }
    legacy["assessment"]["usable_portions"] = "0.0-12.0s"
    assert usable_ranges_summary(legacy["assessment"]) == ""
    assert clip_observations(legacy)["usable_ranges"] == "0.0-12.0s"


def test_describe_clip_handles_a_raw_v3_profile():
    """This is the failure that broke step 3.02: "" for every clip."""
    described = describe_clip(V3_PROFILE)
    assert described
    assert "Outdoor parking lot" in described


def test_lookup_join_survives_adaptation():
    """The catalog join is by file path, and the adapter must not break it."""
    docs = adapt_semantic_documents([V3_PROFILE])
    lookup = build_semantic_lookup(
        docs, [{"clip_id": "clip_009", "path": "/footage/IMG_1814.MOV"}])
    assert "clip_009" in lookup
    assert describe_clip(lookup["clip_009"])


# ─── The measured values must survive all the way into a prompt ───

CONSUMERS = [
    "step_2_01_creative_direction",
    "step_2_02_speech_sequence",
    "step_3_02_select_broll",
    "step_4_03_plan_vfx",
    "step_4_04_plan_sfx",
]


# A step whose measured values reach the prompt through its own
# PRE-BRIDGE rather than through a `semantic_analysis_documents` path.
# The document-path route and the pre-bridge route are both real routes,
# and this file tests whichever one a step actually uses - what it must
# never do is stop checking, which is what an allow-list slice of a step
# with no such paths quietly becomes (it projects to `{}`, and `"{}"` is
# a truthy string).
PRE_BRIDGE_ROUTE = {"step_3_02_select_broll"}


def _semantic_context(step_id, docs):
    """Project + serialise docs exactly as the orchestrator would.

    The allow-list slice, for a step that reads the documents by path.
    A step in `PRE_BRIDGE_ROUTE` has none, so asking this for one is a
    programming error rather than an empty answer.
    """
    from library.tools.context_projector import project_fields
    from library.tools.toon_serializer import json_to_toon

    fields = [f for f in _context_fields(step_id)
              if f.startswith("semantic_analysis_documents")]
    assert fields, (
        f"{step_id} declares no semantic_analysis_documents path, so this "
        f"helper measures nothing for it - use _assembled_context")
    return json_to_toon(
        project_fields({"semantic_analysis_documents": docs}, fields))


def _context_fields(step_id):
    manifest_path = os.path.join(
        REPO_ROOT, "library", "steps", step_id, "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)["context_fields"]


def _assembled_context(step_id, docs, project_folder):
    """The step's WHOLE prompt context, the way the runner builds it.

    The real `bridge.py` as a subprocess, the runner's own
    `project_step_context` and the real serializer - so a value is
    measured where the model reads it, whichever route carried it there.
    """
    import subprocess
    import sys

    from library.processes.edit_video.run_pipeline import project_step_context
    from library.tools.toon_serializer import json_to_toon

    step_dir = os.path.join(REPO_ROOT, "library", "steps", step_id)
    inputs = {
        "project_folder": str(project_folder),
        "semantic_analysis_documents": docs,
        "clip_catalog": [{"clip_id": "clip_009", "filename": "IMG_1814.MOV",
                          "path": "/footage/IMG_1814.MOV",
                          "duration_seconds": 45.943, "width": 1920,
                          "height": 1080, "rotation": 0,
                          "frame_rate": 30.0}],
        "a_roll_assignments": [
            {"segment_id": "block_1", "spine_block_position": 1,
             "video_segments": [{"clip_id": "clip_009"}]}],
        "temporal_event_indices": [{"clip_id": "clip_009"}],
        "timed_spine": {"structure": []},
        "creative_direction": {},
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, os.path.join(step_dir, "bridge.py")],
        input=json.dumps(inputs), capture_output=True, text=True,
        encoding="utf-8", cwd=REPO_ROOT, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pre = json.loads(proc.stdout)

    merged = dict(inputs)
    merged.update(pre)
    with open(os.path.join(step_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    return json_to_toon(project_step_context(merged, manifest, set(pre)))


def test_framing_and_usable_ranges_reach_the_broll_prompt(tmp_path):
    """A real measured value, end to end: analyser -> route -> prompt.

    The allow-list used to admit seven paths, none of which exists in v3.
    Every document projected to clip_id and duration and the model was
    told to reason about framing it could not see.

    The route changed and the property did not.  3.02 no longer declares
    a `semantic_analysis_documents` path at all - it carried THREE views
    of one analysis at 60.7% of its context, so the raw structure moved
    to a reference - and this now measures the WHOLE assembled prompt
    instead of one allow-list slice of it.  That is strictly harder to
    pass: the original defect still fails it (a stale allow-list leaves
    the values nowhere), and so does a collapse that drops a value from
    every route.
    """
    context = _assembled_context(
        "step_3_02_select_broll", adapt_semantic_documents([V3_PROFILE]),
        tmp_path)

    # The framing the vision pass measured for the SECOND camera segment.
    # `wide` alone would pass off the first segment and prove nothing.
    assert "close-up" in context
    assert "unstable" in context
    assert "person_talking_to_camera" in context

    # The usable range as a BOUND a cutaway is cut against. The old
    # assertion was the key name `usable_ranges`; the thing that has to
    # reach the model is the measurement, and a key name is not one.
    assert "0.0-45.9s" in context


# Whether a clip was measured is itself a measurement, and the prompt has
# to carry all three answers distinguishably: the B-roll handoff defines
# an EMPTY cell as "never measured, so the whole clip is fair game but
# unvetted", so a measured exclusion rendered blank tells the model the
# opposite of the truth.
USABLE_RANGE_STATES = [
    ({"usable_ranges": [[0, 45.943]],
      "usable_ranges_method": "deterministic_v1"}, "0.0-45.9s"),
    ({"usable_ranges": [], "usable_ranges_method": "deterministic_v1"},
     "none - whole clip excluded"),
    ({"usable_ranges": [], "usable_ranges_method": "unmeasured"},
     UNMEASURED_SUMMARY),
]


def test_the_three_usable_range_states_reach_the_broll_prompt(tmp_path):
    for assessment, expected in USABLE_RANGE_STATES:
        doc = json.loads(json.dumps(V3_PROFILE))
        doc["assessment"] = dict(doc["assessment"], **assessment)
        context = _assembled_context(
            "step_3_02_select_broll", adapt_semantic_documents([doc]),
            tmp_path)
        assert expected in context, expected


def test_every_semantic_consumer_projects_all_three_document_shapes(tmp_path):
    """Adapted v3, raw v3 and retired-schema documents must all survive.

    `project_fields` raises when a non-empty list projects to nothing but
    empty dicts, which is exactly what a stale allow-list produces.  A
    step on the pre-bridge route is checked on its assembled prompt, and
    on the value rather than on the string being non-empty - `"{}"` is
    non-empty, which is how this could have gone quiet.
    """
    for step_id, docs in [(step_id, docs) for step_id in CONSUMERS
                          for docs in (adapt_semantic_documents([V3_PROFILE]),
                                       [V3_PROFILE], [LEGACY_PROFILE])]:
        if step_id in PRE_BRIDGE_ROUTE:
            context = _assembled_context(step_id, docs, tmp_path)
            assert "clip_009" in context, (
                f"{step_id} assembled a prompt that names no clip from "
                f"these documents")
            continue
        context = _semantic_context(step_id, docs)
        assert context.strip() and context.strip() != "{}", (step_id, context)


def test_clip_observations_excludes_legacy_chain_of_thought_objects():
    """Do not surface raw model reasoning from legacy profiles.
    
    In older profiles, analysis.objects often contains verbose model
    chain-of-thought text (e.g., 'Based on the images provided...').
    This must not leak into the subjects column.
    """
    profile_with_cot = dict(LEGACY_PROFILE)
    profile_with_cot["analysis"] = dict(LEGACY_PROFILE.get("analysis", {}))
    profile_with_cot["analysis"]["objects"] = "Based on the images provided, here are the distinct elements:\n* People: A young man..."
    
    observed = clip_observations(profile_with_cot)
    assert observed["subjects"] == ""


def test_stability_summary_prefers_a_real_verdict_and_reads_past_unknown():
    """A real assessment verdict wins; the literal "unknown" is treated as
    absent, so the summary reads the per-segment values the vision pass
    measured (reading a measurement, not filling a field - AGENTS.md 10.3).
    """
    assert stability_summary(V3_PROFILE) == "unstable"
    doc = {
        "scene": [{"start": 0, "end": 20, "type": "outdoor"}],
        "camera": [
            {"start": 0, "end": 20, "mode": "handheld", "framing": "wide",
             "stability": "stable", "movement": "stationary"},
        ],
        "assessment": {"camera_stability": "unknown"},
        "analysis_metadata": {"pipeline_version": "v3"},
    }
    assert stability_summary(doc) == "stable"


# --------------------------------------------------------------------------
# From test_native_video_sampling.py
#
# Every native-video pass samples at 2 fps, from one call per 10 s window.
#
# Measured on mlx-vlm 0.7.2 with `mlx-community/gemma-4-12b-it-4bit`: every
# `video=` model call sees at most 32 frames (decode at fps=2.0, then the
# gemma4 processor keeps at most `num_frames=32`). A caller-supplied
# `max_frames=` reaches only the decoder - the processor re-caps to 32 -
# and raising the processor's own `num_frames` to 64/96 costs ~3-4x wall
# for still under 2 fps on a 60 s span. So the floor is met by windowing:
# scene, camera, actions and assessment all answer from the same one call
# per 10 s window (`analyze_windows`), 20 frames each at 2 fps.
#
# These tests name that contract:
#
# 1. `native_sample_plan` mirrors the loader's math (pure cases, plus one
#    case against the real `load_video` on synthetic clips where mlx_vlm
#    imports), and a 10 s window plans 20 frames at 2.0 fps.
# 2. The folded call answers all four sections in clip time from a single
#    model call per window, records its sampling, and drops segments
#    outside their own window instead of shifting them into place.
# 3. Assessment sections merge across windows by vote (ties to the
#    earliest), with absent measurements staying absent.
# 4. Windows keep their audio track and hand it to the model (`audio=`);
#    entries record `has_audio` and `sampling`.
# 5. A cached window cut under the old strip-audio policy is re-cut, not
#    silently reused (its file would fail `load_audio`).
# 6. Window clips are cut at 720p height, never upscaled; a cached window
#    taller than the cap is re-cut, not silently reused (it would keep
#    the slow full-resolution decode path).

def test_sample_plan_matches_measured_probe_numbers():
    assert vp.native_sample_plan(120.0, 24.0) == {
        "frames": 32, "decode_fps": 2.0, "effective_fps": 0.267}
    assert vp.native_sample_plan(30.0, 24.0) == {
        "frames": 32, "decode_fps": 2.0, "effective_fps": 1.067}
    assert vp.native_sample_plan(10.0, 24.0)["frames"] == 20
    assert vp.native_sample_plan(60.0, 24.0)["effective_fps"] >= 0.5


def _synth_clip(path, duration_s, with_audio=True, size="320x240"):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc=duration={duration_s}:size={size}:rate=24"]
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

    # A unanimous explicit empty stays an explicit empty, not absent.
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

    # A sourceless window hears nothing and says so.
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
    """A window the model call would raise on is dropped, not handed out:
    a 10.04 s clip's 0.04 s tail (cv2 cannot open the husk), and a cached
    window with no video stream."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    source = _synth_clip(tmp_path / "src.mp4", 10.04, with_audio=False)
    meta = vp.probe_clip(source)
    clips = vp.extract_video_clips(
        Path(source), meta["duration_s"], tmp_path / "cache")
    assert len(clips) == 1
    assert clips[0]["end"] == 10.0

    # A cached window file that carries no video stream is dropped rather
    # than handed to a pass that raises "Cannot open video" on it.
    source = _synth_clip(tmp_path / "src12.mp4", 12, with_audio=True)
    cache = tmp_path / "cache12"
    husk_dir = cache / "src12" / "clips"
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
    """A stale cached window is re-cut, never silently reused: one cut
    under the old strip-audio policy, and one taller than the cap."""
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

    # A cached window cut at source resolution is re-cut at the cap, not
    # silently reused (it would keep the slow full-resolution decode).
    source = _synth_clip(tmp_path / "big.mp4", 12, with_audio=True,
                         size="1280x960")
    legacy_dir = cache / "big" / "clips"
    legacy_dir.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source),
         "-ss", "0", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "23", "-c:a", "aac", str(legacy_dir / "clip_000.mp4")],
        capture_output=True, check=True)
    assert vp._video_height(legacy_dir / "clip_000.mp4") == 960
    clips = vp.extract_video_clips(Path(source), 12.0, cache)
    assert len(clips) == 2
    assert vp._video_height(Path(clips[0]["path"])) == 720
    assert all(c["has_audio"] for c in clips)


def test_extract_caps_window_height_at_720p(tmp_path):
    """A 960p source yields 720p windows (aspect kept), not source-res;
    a smaller source is never upscaled."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    source = _synth_clip(tmp_path / "src.mp4", 12, with_audio=True,
                         size="1280x960")
    assert vp.probe_clip(Path(source))["resolution"] == [1280, 960]
    clips = vp.extract_video_clips(
        Path(source), 12.0, tmp_path / "cache")
    assert len(clips) == 2
    for c in clips:
        assert vp.probe_clip(Path(c["path"]))["resolution"] == [960, 720]
        assert c["has_audio"] is True

    # A 240p source keeps its own resolution - the cap only downscales.
    source = _synth_clip(tmp_path / "small.mp4", 12, with_audio=True)
    clips = vp.extract_video_clips(
        Path(source), 12.0, tmp_path / "cache-small")
    assert len(clips) == 2
    for c in clips:
        assert vp.probe_clip(Path(c["path"]))["resolution"] == [320, 240]


def test_extract_pays_one_source_probe_and_no_per_window_probes(tmp_path):
    """ffprobe thrift: a re-added per-window probe fails this test.

    Cold, a clip pays one ffprobe (the source-audio probe) plus one
    ffmpeg per window; window audio derives from the source probe and
    cut readability is an open-check, not a probe. Warm, with every
    window cached, it pays no ffmpeg and one combined staleness probe
    per cached window - never the four-probes-per-window shape
    (audio + height + video + audio) this replaced.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    source = _synth_clip(tmp_path / "src.mp4", 22, with_audio=True)
    cache = tmp_path / "cache"
    real_run = subprocess.run
    spawns = []

    def counting(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd:
            name = Path(cmd[0]).name
            if name in ("ffprobe", "ffmpeg"):
                spawns.append(name)
        return real_run(cmd, *args, **kwargs)

    subprocess.run = counting
    try:
        clips = vp.extract_video_clips(Path(source), 22.0, cache)
        assert len(clips) == 3
        assert all(c["has_audio"] for c in clips)
        assert spawns.count("ffmpeg") == 3
        assert spawns.count("ffprobe") == 1

        del spawns[:]
        clips = vp.extract_video_clips(Path(source), 22.0, cache)
        assert len(clips) == 3
        assert all(c["has_audio"] for c in clips)
        assert spawns.count("ffmpeg") == 0
        assert spawns.count("ffprobe") == 3
    finally:
        subprocess.run = real_run


def _decoded_video_signature(path):
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
         "-f", "framemd5", "-"],
        capture_output=True, text=True, encoding="utf-8", check=True)
    return tuple(
        (row.split(",")[2].strip(), row.split(",")[-1].strip())
        for row in decoded.stdout.splitlines()
        if row and not row.startswith("#")
    )


def _decoded_audio_content(path):
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-c:a", "pcm_s16le", "-f", "s16le", "-"],
        capture_output=True, check=True)
    return decoded.stdout


def test_fast_seek_matches_accurate_cut_frames_and_audio(tmp_path):
    """Input seeking retains the old output-seek frames and audio."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    source = _synth_clip(tmp_path / "src.mp4", 12, with_audio=True)
    fast = vp.extract_video_clips(
        Path(source), 12.0, tmp_path / "fast-cache",
        window_starts_s=[3.5])[0]
    accurate_path = tmp_path / "accurate.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-ss", "3.5", "-t", "8.5",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
         "-vf", f"scale=-2:min({vp.WINDOW_CLIP_HEIGHT}\\,ih)",
         "-c:a", "aac", "-loglevel", "error", str(accurate_path)],
        capture_output=True, check=True)

    assert _decoded_video_signature(fast["path"]) == (
        _decoded_video_signature(accurate_path))
    assert _decoded_audio_content(fast["path"]) == (
        _decoded_audio_content(accurate_path))


def test_prefetched_windows_preserve_order_and_close_after_early_exit():
    produced = []

    def producer(stop):
        for index in range(5):
            produced.append(index)
            yield {"index": index, "has_audio": index % 2 == 0}

    windows = vp._WindowClipPrefetch(producer, expected_count=5)
    assert [window["index"] for window in windows] == list(range(5))

    assert not windows.thread_alive
    assert windows.extraction_wall_s is not None
    assert windows._count == 5
    assert produced == list(range(5))

    early = vp._WindowClipPrefetch(producer, expected_count=5)
    iterator = iter(early)
    assert next(iterator)["index"] == 0
    iterator.close()
    assert not early.thread_alive
    assert early._count == 1


def test_prefetch_producer_error_cleans_up_thread_and_temp_output(
        tmp_path, monkeypatch):
    source = tmp_path / "source.mxf"
    source.touch()
    cache = tmp_path / "cache"

    def failed_cut(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"partial output")
        raise OSError("simulated ffmpeg failure")

    monkeypatch.setattr(vp.subprocess, "run", failed_cut)
    windows = vp.extract_video_clips(
        source, 10.0, cache, with_audio=False, prefetch=True)

    with pytest.raises(OSError, match="simulated ffmpeg failure"):
        list(windows)

    assert not windows.thread_alive
    assert list((cache / "source" / "clips").iterdir()) == []


def test_unreadable_fast_cut_retries_with_accurate_seek(tmp_path, monkeypatch):
    source = tmp_path / "source.mxf"
    source.touch()
    calls = []

    def fake_cut(cmd, **kwargs):
        calls.append(cmd)
        target = Path(cmd[-1])
        if len(calls) == 1:
            target.write_bytes(b"unreadable fast cut")
            return subprocess.CompletedProcess(cmd, 1, b"", b"seek failed")
        target.write_bytes(b"accurate fallback")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(vp.subprocess, "run", fake_cut)
    monkeypatch.setattr(
        vp, "_clip_opens", lambda path: path.read_bytes() == b"accurate fallback")

    clips = vp.extract_video_clips(
        source, 10.0, tmp_path / "cache", with_audio=False,
        window_starts_s=[0.0])

    assert len(clips) == 1
    assert Path(clips[0]["path"]).read_bytes() == b"accurate fallback"
    assert calls[0].index("-ss") < calls[0].index("-i")
    assert calls[1].index("-i") < calls[1].index("-ss")
    assert list((tmp_path / "cache" / "source" / "clips").iterdir()) == [
        Path(clips[0]["path"])]


def test_window_inference_overlaps_the_next_cut_in_order():
    inference_started = threading.Event()
    next_cut_ready = threading.Event()
    answer = json.dumps({"a": [], "s": [], "c": [], "t": "scenery",
                         "p": []})

    def producer(stop):
        yield {"index": 0, "start": 0.0, "end": 10.0,
               "path": "/tmp/window-0.mp4", "has_audio": True}
        assert inference_started.wait(timeout=2)
        next_cut_ready.set()
        yield {"index": 1, "start": 10.0, "end": 20.0,
               "path": "/tmp/window-1.mp4", "has_audio": True}

    class Analyzer:
        def __init__(self):
            self.video_paths = []

        def analyze_with_retry(self, prompt, parse_fn, **kwargs):
            self.video_paths.append(kwargs["video"])
            if len(self.video_paths) == 1:
                inference_started.set()
                assert next_cut_ready.wait(timeout=2)
            return parse_fn(answer), answer, 0.01

    windows = vp._WindowClipPrefetch(producer, expected_count=2)
    analyzer = Analyzer()
    entries = vp.analyze_windows(analyzer, windows, 20.0, None, "")

    assert [entry["window"] for entry in entries] == [
        [0.0, 10.0], [10.0, 20.0]]
    assert analyzer.video_paths == ["/tmp/window-0.mp4", "/tmp/window-1.mp4"]
    assert not windows.thread_alive


# --------------------------------------------------------------------------
# From test_gemma_shim.py
#
# On-demand Gemma server: tiny shim on the fixed URL starts the real
# model server at the first request and stops it after N idle minutes.
#
# Only stopping the process returns the ~7 GB (POST /unload frees ~145 MB),
# so idle here means the backend PROCESS is gone. Past that the shim exits
# itself too, returning the port to launchd (socket activation) - nothing
# resident until the next request.
#
# These tests drive the shim against a FAKE backend (stdlib http.server
# standing in for mlx_vlm) - no model is loaded.

FAKE_BACKEND = textwrap.dedent(
    """
    import json, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(sys.argv[1])
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    time.sleep(delay)  # simulate model-load time before listening

    class H(BaseHTTPRequestHandler):
        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"status": "healthy", "fake": True})
            elif self.path == "/slow":
                time.sleep(4)
                self._send(200, {"slow": "done"})
            else:
                self._send(200, {"path": self.path})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b""
            try:
                payload = json.loads(raw.decode())
            except ValueError:
                payload = {"raw": raw.decode(errors="replace")}
            if self.path == "/slow":
                time.sleep(4)
            self._send(200, {"echo": payload})

        def log_message(self, *a):
            pass

    HTTPServer(("127.0.0.1", port), H).serve_forever()
    """
)


@pytest.fixture()
def fake_backend_script(tmp_path):
    path = tmp_path / "fake_backend.py"
    path.write_text(FAKE_BACKEND, encoding="utf-8")
    return str(path)


def _free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def ports():
    return _free_port(), _free_port()


def _post(url, payload, timeout=30):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def test_cold_request_starts_backend_and_is_proxied(ports, fake_backend_script):
    """A request arriving with no backend running is ANSWERED, not
    failed - and a health probe before it starts nothing."""
    shim_port, _ = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=ports[1],
        model="fake-model",
        idle_timeout_s=3600.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, fake_backend_script, str(ports[1])],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        # GET /health on a cold shim answers WITHOUT loading the model.
        status, body = _get(f"http://127.0.0.1:{shim_port}/health")
        assert status == 200
        assert body["backend"] == "stopped"
        assert shim.backend_state() == "stopped"
        status, body = _post(
            f"http://127.0.0.1:{shim_port}/v1/chat/completions",
            {"model": "fake-model", "messages": "hi"},
        )
        assert status == 200
        assert body["echo"]["messages"] == "hi"
        assert shim.backend_state() == "ready"
    finally:
        shim.stop()


def test_in_flight_request_blocks_teardown(ports, fake_backend_script):
    """Idle means no in-flight work, no held scope, AND no requests for
    the window - and once idle again, the backend is reaped."""
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=1.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, fake_backend_script, str(backend_port)],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        _post(f"http://127.0.0.1:{shim_port}/v1/chat/completions", {"warm": 1})
        proc = shim.backend_proc()
        answers = []
        import threading

        def _slow():
            try:
                answers.append(
                    _post(
                        f"http://127.0.0.1:{shim_port}/slow",
                        {"slow": "go"},
                        timeout=30,
                    )
                )
            except Exception as exc:  # pragma: no cover - failure path
                answers.append(exc)

        t = threading.Thread(target=_slow, daemon=True)
        t.start()
        time.sleep(2.0)  # past the 1s idle timeout, request still running
        assert proc.poll() is None, "teardown killed a live request"
        t.join(timeout=30)
        assert answers and answers[0][0] == 200
        assert answers[0][1] == {"echo": {"slow": "go"}}
        # Once idle again, it is reaped.
        deadline = time.time() + 15
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        assert proc.poll() is not None

        # A pipeline burst holds the backend across gaps; the scope
        # releases it.
        base = f"http://127.0.0.1:{shim_port}"
        with server_scope(base, timeout=10):
            _post(f"{base}/v1/chat/completions", {"warm": 1})
            proc = shim.backend_proc()
            time.sleep(2.0)  # past idle timeout, but held
            assert proc.poll() is None, "held backend was reaped"
        deadline = time.time() + 15
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        assert proc.poll() is not None, "released backend was never reaped"
    finally:
        shim.stop()


def test_backend_failure_is_a_clear_503_not_a_hang(ports):
    """Model fails to load: clear message, bounded wait, never a hang."""
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=3600.0,
        startup_timeout_s=6.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        t0 = time.time()
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _post(
                f"http://127.0.0.1:{shim_port}/v1/chat/completions",
                {"hi": 1},
                timeout=30,
            )
        elapsed = time.time() - t0
        assert excinfo.value.code == 503
        body = json.loads(excinfo.value.read().decode())
        assert "message" in body.get("error", {})
        assert elapsed < 20, f"failure took too long to surface: {elapsed:.1f}s"
    finally:
        shim.stop()


def test_shim_exits_only_after_backend_stopped(ports, fake_backend_script):
    """The shim may only exit once the backend is stopped.

    Exiting while the backend lives would orphan a 7 GB process with
    nothing to reap it - worse than the resident 40 MB it replaces. So:
    warm the backend, wait out backend-idle + shim-exit, and require that
    by the time the shim is gone the backend process is already dead and
    the port refuses.
    """
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=1.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        shim_idle_exit_s=1.0,
        backend_cmd=[sys.executable, fake_backend_script, str(backend_port)],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        _post(
            f"http://127.0.0.1:{shim_port}/v1/chat/completions",
            {"hello": "warm"},
        )
        proc = shim.backend_proc()
        assert proc is not None and proc.poll() is None
        assert shim.wait_for_exit(timeout=30), "shim never self-exited"
        # Observable ordering: the exit completed, so the backend was
        # stopped FIRST (the exit path asserts this before closing).
        assert proc.poll() is not None, (
            "shim exited while the backend process still lived"
        )
        assert shim.backend_state() == "stopped"
        with pytest.raises(Exception):
            _get(f"http://127.0.0.1:{shim_port}/_shim/status", timeout=5)
    finally:
        shim.stop()
