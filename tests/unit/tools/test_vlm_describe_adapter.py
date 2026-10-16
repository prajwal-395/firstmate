"""Unit tests for the SEM visual.describe adapters.

Everything here runs on fakes (canned model text, path-backed frames) and
tmp_path artifacts: no model runtime, no media decode, per the harness
CI-separation contract.
"""

import hashlib
import json

import pytest

from library.tools import vlm_describe_adapter as adapters
from library.tools.model_adapter_contract import (
    CapabilityInput,
    ComputeBudget,
    ContractError,
    CoordinateFrame,
    FrameRange,
    SourceClock,
    SourceVideoFrame,
    run_adapter,
)

VALID_JSON = json.dumps({
    "scene_class": "vehicle",
    "objects": [{"label": "car seat", "start_seconds": 0.0, "end_seconds": 0.09}],
    "actions": [{"label": "talking", "start_seconds": 0.0, "end_seconds": 0.09}],
    "camera_motion": [],
})

EMPTY_JSON = json.dumps({
    "scene_class": "unknown", "objects": [], "actions": [], "camera_motion": [],
})


class FakeModel:
    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.calls = []

    def analyze_images(self, paths, prompt, max_tokens=384):
        self.calls.append((list(paths), prompt, max_tokens))
        if self.error is not None:
            raise self.error
        return self.text


class FakeMedia:
    def __init__(self, frame_paths, width=1920, height=1080):
        self.frame_paths = frame_paths
        self.width = width
        self.height = height

    def read_video_frames(self, request):
        return [
            SourceVideoFrame(frame_index=request.frame_range.start + i,
                             image=str(path), width=self.width,
                             height=self.height)
            for i, path in enumerate(self.frame_paths)
        ]


def _request(tmp_path):
    n_frames = 3
    for i in range(n_frames):
        (tmp_path / f"frame-{i}.jpg").write_bytes(b"\xff\xd8fake")
    clock = SourceClock(id="src-clock", ticks_per_second=1000,
                        asset_id="IMG_1808", frame_rate_num=30,
                        frame_rate_den=1)
    return (CapabilityInput(
        capability="visual.describe", source_id="IMG_1808",
        source_sha256="ab" * 32, source_clock=clock,
        frame_range=FrameRange(0, n_frames),
        coordinates=CoordinateFrame(source_width=1920, source_height=1080),
        prompt="probe prompt",
        budget=ComputeBudget(max_frames=8)), [str(tmp_path / f"frame-{i}.jpg")
                                              for i in range(n_frames)])


def test_happy_path_returns_intervals_and_raw_artifact(tmp_path):
    request, _ = _request(tmp_path)
    adapter = adapters.adapter_for_model(
        "mlx-community/gemma-4-12b-it-4bit", tmp_path / "artifacts")
    result = run_adapter(adapter, request, FakeModel(VALID_JSON),
                         FakeMedia([tmp_path / "frame-0.jpg",
                                    tmp_path / "frame-1.jpg",
                                    tmp_path / "frame-2.jpg"]))
    assert result.status == "completed"
    assert result.supported_features == adapters.SUPPORTED_FEATURES
    assert len(result.intervals) == 2
    assert all(iv.clock_id == "src-clock" for iv in result.intervals)
    assert result.raw_output is not None
    assert result.raw_output.sha256 == hashlib.sha256(
        VALID_JSON.encode()).hexdigest()


def test_fenced_output_is_completed(tmp_path):
    request, _ = _request(tmp_path)
    adapter = adapters.adapter_for_model(
        "mlx-community/Qwen3.5-4B-4bit", tmp_path / "artifacts")
    result = run_adapter(adapter, request, FakeModel("```json\n" + VALID_JSON + "\n```"),
                         FakeMedia([tmp_path / "frame-0.jpg",
                                    tmp_path / "frame-1.jpg",
                                    tmp_path / "frame-2.jpg"]))
    assert result.status == "completed"
    assert "fenced=True" in (result.detail or "")


def test_garbage_output_fails_with_raw_retained(tmp_path):
    request, _ = _request(tmp_path)
    adapter = adapters.adapter_for_model(
        "mlx-community/gemma-4-12b-it-4bit", tmp_path / "artifacts")
    result = run_adapter(adapter, request, FakeModel("not json at all"),
                         FakeMedia([tmp_path / "frame-0.jpg",
                                    tmp_path / "frame-1.jpg",
                                    tmp_path / "frame-2.jpg"]))
    assert result.status == "failed"
    assert result.failure == "execution_error"
    assert result.raw_output is not None
    assert result.raw_output.media_type.startswith("text/plain")


def test_out_of_window_timestamp_fails_closed(tmp_path):
    request, _ = _request(tmp_path)
    invalid = json.dumps({
        "scene_class": "vehicle",
        "objects": [{"label": "car", "start_seconds": 0.0,
                     "end_seconds": 0.11}],
        "actions": [], "camera_motion": [],
    })
    adapter = adapters.adapter_for_model(
        "mlx-community/gemma-4-12b-it-4bit", tmp_path / "artifacts")
    result = run_adapter(adapter, request, FakeModel(invalid),
                         FakeMedia([tmp_path / "frame-0.jpg",
                                    tmp_path / "frame-1.jpg",
                                    tmp_path / "frame-2.jpg"]))
    assert result.status == "failed"
    assert result.failure == "execution_error"
    assert result.raw_output is not None


def test_empty_claims_are_no_evidence_with_scope(tmp_path):
    request, _ = _request(tmp_path)
    adapter = adapters.adapter_for_model(
        "mlx-community/gemma-4-12b-it-4bit", tmp_path / "artifacts")
    result = run_adapter(adapter, request, FakeModel(EMPTY_JSON),
                         FakeMedia([tmp_path / "frame-0.jpg",
                                    tmp_path / "frame-1.jpg",
                                    tmp_path / "frame-2.jpg"]))
    assert result.status == "no_evidence"
    assert "IMG_1808" in (result.detail or "")


def test_model_error_is_execution_error(tmp_path):
    request, _ = _request(tmp_path)
    adapter = adapters.adapter_for_model(
        "mlx-community/gemma-4-12b-it-4bit", tmp_path / "artifacts")
    result = run_adapter(adapter, request,
                         FakeModel(error=RuntimeError("metal blew up")),
                         FakeMedia([tmp_path / "frame-0.jpg",
                                    tmp_path / "frame-1.jpg",
                                    tmp_path / "frame-2.jpg"]))
    assert result.status == "failed"
    assert result.failure == "execution_error"


def test_capability_mismatch_is_refused(tmp_path):
    request, _ = _request(tmp_path)
    object.__setattr__(request, "capability", "visual.detect")
    adapter = adapters.adapter_for_model(
        "mlx-community/gemma-4-12b-it-4bit", tmp_path / "artifacts")
    with pytest.raises(ContractError):
        run_adapter(adapter, request, FakeModel(VALID_JSON), FakeMedia([]))


def test_task_routing_selects_adapters(tmp_path):
    incumbent = adapters.adapter_for_task("action_localization",
                                          tmp_path / "a")
    assert incumbent.model_revision == "73bcf09092aa277861d5a191b989b666f7f32e8f"
    default_simple = adapters.adapter_for_task("object_presence",
                                               tmp_path / "b")
    assert default_simple.adapter_id == incumbent.adapter_id
    fallback = adapters.adapter_for_task("object_presence", tmp_path / "c",
                                         escalation_enabled=True)
    assert fallback.model_revision == "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c"


def test_policy_retries_malformed_lightweight_output_with_incumbent(tmp_path):
    request, _ = _request(tmp_path)
    lightweight = FakeModel("not json")
    incumbent = FakeModel(VALID_JSON)
    result = adapters.infer_task_with_policy(
        "object_presence", request,
        FakeMedia([tmp_path / "frame-0.jpg", tmp_path / "frame-1.jpg",
                   tmp_path / "frame-2.jpg"]),
        {
            "mlx-community/Qwen3.5-4B-4bit": lightweight,
            "mlx-community/gemma-4-12b-it-4bit": incumbent,
        }, tmp_path / "policy-artifacts", lightweight_enabled=True)
    assert result.status == "completed"
    assert len(lightweight.calls) == 1
    assert len(incumbent.calls) == 1
    assert "policy escalation" in (result.detail or "")


def test_policy_keeps_well_formed_lightweight_result(tmp_path):
    request, _ = _request(tmp_path)
    lightweight = FakeModel(VALID_JSON)
    incumbent = FakeModel(VALID_JSON)
    result = adapters.infer_task_with_policy(
        "object_presence", request,
        FakeMedia([tmp_path / "frame-0.jpg", tmp_path / "frame-1.jpg",
                   tmp_path / "frame-2.jpg"]),
        {
            "mlx-community/Qwen3.5-4B-4bit": lightweight,
            "mlx-community/gemma-4-12b-it-4bit": incumbent,
        }, tmp_path / "policy-artifacts", lightweight_enabled=True)
    assert result.status == "completed"
    assert len(lightweight.calls) == 1
    assert incumbent.calls == []


def test_unknown_model_id_is_refused(tmp_path):
    with pytest.raises(ContractError):
        adapters.adapter_for_model("mlx-community/does-not-exist",
                                   tmp_path / "a")
