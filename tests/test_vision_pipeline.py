import sys
import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Mock heavy mlx_vlm dependency before importing vision_pipeline_v3
mlx_mock = MagicMock()
mlx_mock.load.return_value = (MagicMock(), MagicMock())
mlx_mock.generate.return_value = MagicMock(text="[]")
mlx_prompt_utils = MagicMock()
mlx_prompt_utils.apply_chat_template.return_value = "prompt"
mlx_mock.prompt_utils = mlx_prompt_utils

# Now we can import the pipeline safely
from library.tools.analysis import vision_pipeline_v3 as vp

@pytest.fixture(autouse=True)
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


def test_expand_compact_window_absent_key_is_not_an_empty_answer():
    """A section without its key stays missing, like the canonical path."""
    out = vp.expand_compact_window({"t": "scenery"})
    assert "actions" not in out
    assert "scene" not in out
    assert "camera" not in out
    assert out["assessment"] == {"content_type": "scenery"}

    out = vp.expand_compact_window({"a": []})
    assert out["actions"] == []
    assert out["assessment"] is None


def test_expand_compact_window_drops_malformed_rows():
    """Bad timestamps or prose of the wrong type drop the row only."""
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


def test_expand_compact_window_feeds_the_vote_merge():
    """The expanded assessment is what `_merge_assessment_votes` reads."""
    out = vp.expand_compact_window(_sample_compact_answer())
    windows = [{"window": [0.0, 10.0], "assessment": out["assessment"]}]
    content_type, psv = vp._merge_assessment_votes(windows)
    assert content_type == "person_talking_to_camera"
    assert psv == [[0, 10]]


def test_compact_prompt_names_the_same_enums_and_bounds():
    """The diet changes the envelope, not the task: vocabularies stay."""
    prompt = vp.PROMPT_WINDOW_ALL_COMPACT.format(
        window_start=0, window_end=10, window_dur=10, duration=46,
        transcript_line="\n(No speech in this segment.)",
        boundaries="No hard scene boundaries detected (likely continuous)",
    )
    for token in ("person_talking_to_camera", "scenery", "action_sequence",
                  "multiple_people", "object_showcase", "transition",
                  "selfie", "handheld", "mounted", "panning", "tracking",
                  "close-up", "medium", "wide",
                  "indoor", "outdoor", "vehicle", "mixed",
                  "stationary", "walking"):
        assert token in prompt, token
    assert "quotation" in prompt
    assert "timestamps" in prompt
    assert '"a"' in prompt and '"s"' in prompt and '"c"' in prompt


def test_expand_compact_window_splits_semicolon_features():
    """The "s" features string splits on ";" - "" means none observed."""
    out = vp.expand_compact_window({
        "s": [[0, 10, "office", "indoor", "warm", "whiteboard;  ; desk"],
              [0, 10, "hall", "indoor", "dim", ""]],
    })
    assert out["scene"][0]["notable_features"] == ["whiteboard", "desk"]
    assert out["scene"][1]["notable_features"] == []


def test_expand_compact_window_merges_unrolled_feature_items():
    """A 7-item "s" row (joined string plus extra items) merges rather
    than dropping the section - measured 2026-09-24 on a window with
    burned-in captions, where the model hedged both shapes."""
    out = vp.expand_compact_window({
        "s": [[0.0, 10.0, "Dark room", "indoor", "Dim",
               "Geometric wall patterns;Text: FICTUR_ONE",
               "Text: FICTUR_ONE"]],
    })
    assert out["scene"][0]["notable_features"] == [
        "Geometric wall patterns", "Text: FICTUR_ONE", "Text: FICTUR_ONE"]


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


def test_expand_or_canonical_prefers_compact_in_a_mixed_answer():
    """Compact keys win: a half-canonical tail is not a second answer."""
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


def test_analyze_windows_runs_the_compact_prompt_and_expands():
    """The wired path: compact prompt, 600-token cap, canonical entry."""
    import json as _json
    answer = _json.dumps({
        "a": [[0, 10, "speaking to camera", None, "upright"]],
        "s": [[0, 10, "office", "indoor", "warm", "whiteboard"]],
        "c": [[0, 10, "handheld", "medium", "steady", "stationary"]],
        "t": "person_talking_to_camera",
        "p": [[0, 10]],
    })
    analyzer = _FakeAnalyzer(answer)
    clips = [{"start": 0.0, "end": 10.0, "path": "/tmp/probe.mp4",
              "has_audio": True}]
    entries = vp.analyze_windows(analyzer, clips, 10.0, None, "")
    assert len(analyzer.calls) == 1
    call = analyzer.calls[0]
    assert '"a"' in call["prompt"] and '"actions"' not in call["prompt"]
    assert call["kwargs"]["max_tokens"] == vp.MAX_TOKENS["window_all_compact"]
    assert call["kwargs"]["audio"] == "/tmp/probe.mp4"
    entry = entries[0]
    assert entry["window"] == [0.0, 10.0]
    assert entry["actions"][0]["action"] == "speaking to camera"
    assert entry["scene"][0]["notable_features"] == ["whiteboard"]
    assert entry["camera"][0]["mode"] == "handheld"
    assert entry["assessment"]["content_type"] == "person_talking_to_camera"
    assert "parse_error" not in entry


# The exact malformed diet answers measured 2026-09-24 on source
# footage (W02: next-section key opened inside the previous array plus
# fences; W07: next key straight inside the array). Both must repair
# to the full field set rather than dropping the window.
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


def test_parse_compact_window_repairs_unclosed_section_array():
    """The measured `["s":` shape repairs with every field kept."""
    parsed, repaired = vp.parse_compact_window(W02_MALFORMED)
    assert repaired is True
    assert sorted(parsed) == ["a", "c", "p", "s", "t"]
    out = vp._expand_or_canonical(parsed)
    assert len(out["actions"]) == 1
    assert out["scene"][0]["type"] == "outdoor"
    assert out["camera"][0]["mode"] == "handheld"
    assert out["assessment"]["content_type"] == "person_talking_to_camera"


def test_parse_compact_window_repairs_key_inside_array():
    """The measured `, "c":` shape (no bogus bracket) repairs too."""
    parsed, repaired = vp.parse_compact_window(W07_MALFORMED)
    assert repaired is True
    out = vp._expand_or_canonical(parsed)
    assert out["scene"][0]["type"] == "indoor"
    assert out["assessment"]["content_type"] == "person_talking_to_camera"


def test_parse_compact_window_repairs_trailing_comma_and_empty_extra():
    """The measured `"...;dark floor", ""],]` shape (W10, round 2).

    The model trailed both a comma and an empty extra item after the
    features string - the expander already merges a 7th item, so once
    the comma is forgiven the row lands whole.
    """
    parsed, repaired = vp.parse_compact_window(
        '{"a": [[295, 305, "speaking to camera", '
        '"moderate pace, clear articulation", '
        '"upright posture, hand gestures, neutral expression"]], '
        '"s": [[295, 305, "geometric wall and floor", "indoor", '
        '"dim lighting", "geometric wall patterns;dark floor", ""],], '
        '"c": [[295, 305, "handheld", "medium", "slight shake", '
        '"stationary"]], "t": "person_talking_to_camera", '
        '"p": [[295, 305]]}')
    assert repaired is True
    out = vp._expand_or_canonical(parsed)
    assert out["scene"][0]["notable_features"] == [
        "geometric wall patterns", "dark floor"]
    assert out["scene"][0]["type"] == "indoor"


def test_parse_compact_window_leaves_good_answers_untouched():
    """Strict JSON parses with repaired False - the common path."""
    parsed, repaired = vp.parse_compact_window(
        '{"a": [[0, 10, "speaking", null, "upright"]], '
        '"t": "person_talking_to_camera"}')
    assert repaired is False
    assert parsed["t"] == "person_talking_to_camera"


def test_parse_compact_window_rejects_garbage():
    """Gibberish stays ({}, False) so the caller fires the fallback."""
    assert vp.parse_compact_window("not json at all {{{") == ({}, False)
    assert vp.parse_compact_window("") == ({}, False)


def test_compact_prompt_states_the_close_each_array_rule():
    """The prompt names the measured error: close each array."""
    prompt = vp.PROMPT_WINDOW_ALL_COMPACT.format(
        window_start=0, window_end=10, window_dur=10, duration=46,
        transcript_line="\n(No speech in this segment.)",
        boundaries="No hard scene boundaries detected (likely continuous)",
    )
    assert "Close each section" in prompt
    assert "]]" in prompt


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


def test_analyze_windows_records_compact_path():
    """A clean compact answer takes one call and records its path."""
    import json as _json
    answer = _json.dumps({
        "a": [[0, 10, "speaking to camera", None, "upright"]],
        "s": [[0, 10, "office", "indoor", "warm", "whiteboard"]],
        "c": [[0, 10, "handheld", "medium", "steady", "stationary"]],
        "t": "person_talking_to_camera",
        "p": [[0, 10]],
    })
    entries = vp.analyze_windows(_FakeAnalyzer(answer),
                                 _window_clips(), 10.0, None, "")
    assert entries[0]["prompt_path"] == "compact"
    assert "parse_error" not in entries[0]


def test_analyze_windows_records_compact_repaired_path():
    """A repaired compact answer is kept (not re-asked) and recorded."""
    analyzer = _FakeAnalyzer(W02_MALFORMED)
    entries = vp.analyze_windows(analyzer, _window_clips(), 10.0,
                                 None, "")
    assert len(analyzer.calls) == 1
    entry = entries[0]
    assert entry["prompt_path"] == "compact_repaired"
    assert "parse_error" not in entry
    assert entry["actions"][0]["action"] == "speaking to camera"
    assert entry["assessment"]["content_type"] == "person_talking_to_camera"


def test_analyze_windows_falls_back_to_full_prompt():
    """Gibberish compact AND gibberish fallback: two calls, UNPARSED.

    A dropped window is never acceptable, so an unparseable compact
    answer is re-asked with the canonical prompt - and only when both
    fail is the window recorded as UNPARSED, with the path on it.
    """
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


def test_analyze_windows_full_fallback_recovers_the_window():
    """A failed compact answer recovered by the canonical re-ask."""
    import json as _json
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
    entries = vp.analyze_windows(analyzer, _window_clips(), 10.0,
                                 None, "")
    assert len(analyzer.calls) == 2
    entry = entries[0]
    assert entry["prompt_path"] == "full_fallback"
    assert "parse_error" not in entry
    assert entry["actions"][0]["action"] == "speaking"
    assert entry["scene"][0]["notable_features"] == ["whiteboard"]
    assert entry["analysis_time_s"] == 39.0


# ── Findings 2 and 3 (rung 0): still paths the host can open ──

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


def test_detail_frames_from_a_relative_cache_are_absolute(
        tmp_path, monkeypatch):
    """Finding 2, detail half: the same relative-cache refusal through
    `extract_detail_frames` - the frames a detail pass hands the host
    must already be absolute when they leave the extractor.
    """
    import os as _os

    monkeypatch.chdir(tmp_path)
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

