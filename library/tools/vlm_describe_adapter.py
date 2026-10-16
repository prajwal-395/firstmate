"""`visual.describe` adapters for the SEM VLM lane behind the harness contract.

Implements `library.tools.model_adapter_contract.CapabilityAdapter` for the
two measured VLM implementations (Gemma 4 12B 4-bit incumbent, Qwen3.5-4B
4-bit conditional fallback) over the frozen probe prompt/response shape.
The adapter is a thin wrapper: it never loads, schedules, or caches a
model - the caller supplies an already lifecycle-managed model exposing
`analyze_images(paths, prompt, max_tokens)`, and decoded frames via the
contract's `CapabilityMedia`. Raw model text is persisted as a
`RawArtifact`; normalized claim spans become tick-based `EvidenceInterval`
records on the request's source clock.

Routing stays in `library.tools.vlm_routing_policy`; `adapter_for_task`
is the single join between the policy's model ids and these adapters.
Nothing here changes the production default: callers that never ask for
an adapter keep using `VisionModel` exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from library.tools import vlm_routing_policy
from library.tools.model_adapter_contract import (
    AdapterResult,
    CapabilityInput,
    CapabilityMedia,
    ContractError,
    EvidenceInterval,
    RawArtifact,
    read_video_frames,
    run_adapter,
)

CAPABILITY = "visual.describe"

SUPPORTED_FEATURES = frozenset({"scene_class", "objects", "actions", "camera_motion"})

_SCENE_CLASSES = {"vehicle", "indoor", "outdoor", "mixed", "unknown"}


def _strip_fence(text: str) -> tuple[str, bool]:
    body = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", body, re.DOTALL)
    if fence:
        return fence.group(1).strip(), True
    return body, False


def _parse_response(text: str, start: float, end: float) -> tuple[dict | None, dict]:
    body, fenced = _strip_fence(text)
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None, {"json": False, "schema": False, "timestamps": False,
                      "fenced": fenced}
    schema = (
        isinstance(obj, dict)
        and obj.get("scene_class") in _SCENE_CLASSES
        and all(isinstance(obj.get(k), list) for k in ("objects", "actions", "camera_motion"))
        and all(isinstance(row, dict) and isinstance(row.get("label"), str)
                and isinstance(row.get("start_seconds"), (int, float))
                and isinstance(row.get("end_seconds"), (int, float))
                for k in ("objects", "actions", "camera_motion") for row in obj[k])
    )
    timestamps = bool(schema) and all(
        start <= row["start_seconds"] < row["end_seconds"] <= end
        for k in ("objects", "actions", "camera_motion") for row in obj[k]
    )
    return obj, {"json": True, "schema": bool(schema),
                 "timestamps": bool(timestamps), "fenced": fenced}


def _window_seconds(request: CapabilityInput) -> tuple[float, float]:
    tps = request.source_clock.ticks_per_second
    ticks = sorted(
        (interval.start_tick, interval.end_tick)
        for interval in request.source_intervals
        if interval.clock_id == request.source_clock.id
    )
    if not ticks:
        raise ContractError("request carries no interval on its source clock")
    return ticks[0][0] / tps, ticks[-1][1] / tps


def _interval(clock_id: str, tps: int, start_s: float, end_s: float) -> EvidenceInterval:
    start_tick = int(Fraction(start_s).limit_denominator(1000) * tps)
    end_tick = int(Fraction(end_s).limit_denominator(1000) * tps)
    if end_tick <= start_tick:
        end_tick = start_tick + 1
    return EvidenceInterval(clock_id=clock_id, start_tick=start_tick, end_tick=end_tick)


@dataclass
class VlmDescribeAdapter:
    """Thin `visual.describe` wrapper around a lifecycle-managed VLM."""

    adapter_id: str
    model_revision: str
    artifact_dir: Path
    capability: str = CAPABILITY
    supported_features: frozenset[str] = SUPPORTED_FEATURES

    def infer(self, request: CapabilityInput, model: Any,
              media: CapabilityMedia) -> AdapterResult:
        frames = list(read_video_frames(media, request))
        paths = []
        for frame in frames:
            path = frame.image if isinstance(frame.image, (str, Path)) else None
            if path is None or not Path(path).is_file():
                return AdapterResult(status="failed", failure="unsupported_input",
                                     detail="adapter needs decoded frame file paths")
            paths.append(str(path))
        if not paths:
            return AdapterResult(status="failed", failure="unsupported_input",
                                 detail="no frames decoded for the requested range")
        max_tokens = int(request.options.get("max_tokens", 384))
        prompt = request.prompt or (
            "Describe sampled frames. Return exactly one JSON object with "
            '{"scene_class":"vehicle|indoor|outdoor|mixed|unknown",'
            '"objects":[{"label":"string","start_seconds":0.0,"end_seconds":1.0}],'
            '"actions":[{"label":"string","start_seconds":0.0,"end_seconds":1.0}],'
            '"camera_motion":[{"label":"string","start_seconds":0.0,"end_seconds":1.0}]} '
            "and no other text.")
        try:
            text = model.analyze_images(paths, prompt, max_tokens=max_tokens)
        # Model runtimes expose backend-specific exception classes; translate
        # those at the adapter boundary into the shared failure vocabulary.
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(status="failed", failure="execution_error",
                                 detail=f"model raised {type(exc).__name__}: {exc}")
        start_s, end_s = _window_seconds(request)
        parsed, checks = _parse_response(text, start_s, end_s)
        raw_bytes = text.encode("utf-8")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(raw_bytes).hexdigest()
        # Preserve the exact model bytes. A response that failed JSON parsing
        # is still a useful artifact, so don't give it a misleading .json suffix.
        artifact_path = self.artifact_dir / f"{digest}.txt"
        artifact_path.write_bytes(raw_bytes)
        raw = RawArtifact(uri=artifact_path.as_uri(), sha256=digest,
                          media_type="text/plain; charset=utf-8")
        if (not isinstance(parsed, dict) or not checks["schema"]
                or not checks["timestamps"]):
            return AdapterResult(status="failed", failure="execution_error",
                                 raw_output=raw,
                                 detail=f"invalid model output: {checks}")
        tps = request.source_clock.ticks_per_second
        clock_id = request.source_clock.id
        intervals = []
        for key in ("objects", "actions", "camera_motion"):
            for row in parsed[key]:
                intervals.append(_interval(clock_id, tps, row["start_seconds"],
                                           row["end_seconds"]))
        scene = parsed.get("scene_class")
        if not intervals:
            scope = f"{request.source_id} [{start_s:.3f},{end_s:.3f})"
            return AdapterResult(status="no_evidence", raw_output=raw,
                                 supported_features=SUPPORTED_FEATURES,
                                 detail=f"model returned no claims over {scope}")
        result = AdapterResult(status="completed", raw_output=raw,
                               supported_features=SUPPORTED_FEATURES,
                               intervals=tuple(intervals),
                               detail=f"scene_class={scene} fenced={checks['fenced']}")
        return result


_ADAPTER_SPECS = {
    "mlx-community/gemma-4-12b-it-4bit": (
        "vlm.describe.gemma-4-12b-it-4bit",
        "73bcf09092aa277861d5a191b989b666f7f32e8f",
    ),
    "mlx-community/Qwen3.5-4B-4bit": (
        "vlm.describe.qwen3.5-4b-4bit",
        "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c",
    ),
}


def adapter_for_model(model_id: str, artifact_dir: Path | str) -> VlmDescribeAdapter:
    """Return the adapter for a policy-resolved model id."""
    try:
        adapter_id, revision = _ADAPTER_SPECS[model_id]
    except KeyError as exc:
        raise ContractError(f"no visual.describe adapter for {model_id!r}") from exc
    return VlmDescribeAdapter(adapter_id=adapter_id, model_revision=revision,
                              artifact_dir=Path(artifact_dir))


def adapter_for_task(task_class: str, artifact_dir: Path | str,
                     escalation_enabled: bool = False) -> VlmDescribeAdapter:
    """Join the SEM-013 routing policy to the harness adapters."""
    tier = vlm_routing_policy.select_tier(task_class)
    model_id = vlm_routing_policy.resolve_model_id(tier, escalation_enabled)
    return adapter_for_model(model_id, artifact_dir)


def infer_task_with_policy(task_class: str, request: CapabilityInput,
                           media: CapabilityMedia,
                           managed_models: Mapping[str, Any],
                           artifact_dir: Path | str,
                           lightweight_enabled: bool = False) -> AdapterResult:
    """Run a task through supplied lifecycle-managed models and escalate.

    Models are owned and loaded by Ren's lifecycle layer. This adapter only
    selects among the supplied instances and retries with Gemma when an
    opted-in lightweight result is malformed, empty, abstaining, or mistimed.
    """
    first = adapter_for_task(task_class, artifact_dir, lightweight_enabled)
    model_id = (vlm_routing_policy.FALLBACK_MODEL_ID
                if first.adapter_id == "vlm.describe.qwen3.5-4b-4bit"
                else vlm_routing_policy.INCUMBENT_MODEL_ID)
    if model_id not in managed_models:
        raise ContractError(f"lifecycle-managed model not supplied: {model_id}")
    result = run_adapter(first, request, managed_models[model_id], media)
    if model_id != vlm_routing_policy.FALLBACK_MODEL_ID:
        return result

    parsed = None
    checks = {"json": False, "schema": False, "timestamps": False}
    if result.raw_output is not None:
        uri = urlparse(result.raw_output.uri)
        if uri.scheme == "file":
            raw_text = Path(unquote(uri.path)).read_text(encoding="utf-8")
            start_s, end_s = _window_seconds(request)
            parsed, checks = _parse_response(raw_text, start_s, end_s)
    observation = {
        "status": result.status,
        "parse": checks,
        "objects": parsed.get("objects", []) if isinstance(parsed, dict) else [],
        "actions": parsed.get("actions", []) if isinstance(parsed, dict) else [],
        "scene_class": parsed.get("scene_class") if isinstance(parsed, dict) else None,
    }
    escalate, reason = vlm_routing_policy.should_escalate(observation)
    if not escalate:
        return replace(result, detail=(result.detail or "") +
                       "; policy kept lightweight result")
    strong_id = vlm_routing_policy.INCUMBENT_MODEL_ID
    if strong_id not in managed_models:
        raise ContractError(f"lifecycle-managed escalation model not supplied: {strong_id}")
    strong = adapter_for_model(strong_id, artifact_dir)
    retried = run_adapter(strong, request, managed_models[strong_id], media)
    return replace(
        retried,
        detail=(f"policy escalation from {first.adapter_id}: {reason}; "
                f"lightweight_raw={None if result.raw_output is None else result.raw_output.sha256}; "
                f"strong_result={retried.detail or retried.status}"),
    )


__all__ = [
    "CAPABILITY",
    "SUPPORTED_FEATURES",
    "VlmDescribeAdapter",
    "adapter_for_model",
    "adapter_for_task",
    "infer_task_with_policy",
    "run_adapter",
]
