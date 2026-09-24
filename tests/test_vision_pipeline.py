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

