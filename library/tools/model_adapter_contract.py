"""Stable capability adapter boundary for multimodal model implementations.

Adapters translate implementation-specific inputs and outputs at this edge.
They do not write model-native structures into Project Model evidence. Raw
responses are kept as separately checksummed artifacts; normalized evidence
keeps the producer's score semantics instead of treating a score as a
probability.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Literal, Protocol

from library.tools.project_model_contract import TimeMap


class ContractError(ValueError):
    """A capability request or adapter result violates the shared contract."""


FAILURE_STATES = (
    "model_unavailable", "out_of_memory", "unsupported_input",
    "execution_error", "low_confidence",
)
CONFIDENCE_SEMANTICS = (
    "uncalibrated_score", "calibrated_probability", "rank_only", "unknown",
)
CAPABILITY_INPUTS = {
    "speech.transcribe": ("audio", False),
    "speech.align": ("audio", False),
    "audio.diarize": ("audio", False),
    "audio.identify_speaker": ("audio", False),
    "audio.active_speaker": ("audiovisual", False),
    "audio.detect_event": ("audio", False),
    "audio.ground_event": ("audio", True),
    "audio.separate": ("audio", True),
    "visual.describe": ("video", False),
    "visual.embed": ("video", False),
    "visual.detect": ("video", True),
    "visual.ground": ("video", True),
    "visual.segment": ("video", False),
    "visual.track": ("video", False),
    "visual.depth": ("video", False),
    "visual.camera_geometry": ("video", False),
    "visual.ocr": ("video", False),
    "video.retrieve": ("video", True),
    "video.locate_event": ("video", True),
    "video.caption_events": ("video", False),
}


def _positive_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{name} must be a positive integer")


def _nonnegative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{name} must be a non-negative integer")


def _finite(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ContractError(f"{name} must be finite")


@dataclass(frozen=True)
class SourceClock:
    """An authoritative source clock, expressed as integer ticks per second."""

    id: str
    ticks_per_second: int
    asset_id: str
    origin: Literal["source_media"] = "source_media"
    frame_rate_num: int | None = None
    frame_rate_den: int | None = None
    stream_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.asset_id:
            raise ContractError("source clock id and asset_id are required")
        if self.origin != "source_media":
            raise ContractError("source clock origin must be authoritative source media")
        if isinstance(self.ticks_per_second, bool) or not isinstance(self.ticks_per_second, int):
            raise ContractError("ticks_per_second must be an integer")
        _positive_int(self.ticks_per_second, "ticks_per_second")
        if (self.frame_rate_num is None) != (self.frame_rate_den is None):
            raise ContractError("frame rate numerator and denominator must be supplied together")
        if self.frame_rate_num is not None:
            _positive_int(self.frame_rate_num, "frame_rate_num")
            _positive_int(self.frame_rate_den, "frame_rate_den")


@dataclass(frozen=True)
class FrameRange:
    """Half-open source frame range [start, end)."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if (isinstance(self.start, bool) or isinstance(self.end, bool)
                or not isinstance(self.start, int) or not isinstance(self.end, int)
                or self.start < 0 or self.end <= self.start):
            raise ContractError("frame range must be a non-empty half-open source range")


@dataclass(frozen=True)
class SampleRange:
    """Half-open audio sample range [start, end) at the declared sample rate."""

    start: int
    end: int
    sample_rate_hz: int

    def __post_init__(self) -> None:
        if (isinstance(self.start, bool) or isinstance(self.end, bool)
                or not isinstance(self.start, int) or not isinstance(self.end, int)
                or self.start < 0 or self.end <= self.start):
            raise ContractError("sample range must be a non-empty half-open source range")
        _positive_int(self.sample_rate_hz, "sample_rate_hz")


@dataclass(frozen=True)
class CoordinateFrame:
    """Transform from model-input pixels to oriented source-display pixels.

    Boxes and points in normalized results use top-left-origin normalized
    source-display coordinates. Crop is x/y/width/height in source pixels;
    scale and padding describe resize/letterbox applied after crop.
    """

    source_width: int
    source_height: int
    orientation_degrees: Literal[0, 90, 180, 270] = 0
    crop_xywh: tuple[int, int, int, int] | None = None
    model_width: int | None = None
    model_height: int | None = None
    scale_x: float = 1.0
    scale_y: float = 1.0
    pad_left: int = 0
    pad_top: int = 0

    def __post_init__(self) -> None:
        _positive_int(self.source_width, "source_width")
        _positive_int(self.source_height, "source_height")
        if self.orientation_degrees not in {0, 90, 180, 270}:
            raise ContractError("orientation must be 0, 90, 180, or 270 degrees")
        _finite(self.scale_x, "scale_x")
        _finite(self.scale_y, "scale_y")
        if self.scale_x <= 0 or self.scale_y <= 0:
            raise ContractError("coordinate resize scales must be positive")
        if (self.model_width is None) != (self.model_height is None):
            raise ContractError("model width and height must be supplied together")
        if self.model_width is not None:
            _positive_int(self.model_width, "model_width")
            _positive_int(self.model_height, "model_height")
        if self.crop_xywh is not None:
            x, y, width, height = self.crop_xywh
            _nonnegative_int(x, "crop x")
            _nonnegative_int(y, "crop y")
            _positive_int(width, "crop width")
            _positive_int(height, "crop height")
            if x + width > self.source_width or y + height > self.source_height:
                raise ContractError("crop exceeds source-display dimensions")
        _nonnegative_int(self.pad_left, "pad_left")
        _nonnegative_int(self.pad_top, "pad_top")

    def source_point(self, model_x: float, model_y: float) -> tuple[float, float]:
        """Map model-input pixels back to normalized oriented source pixels."""
        _finite(model_x, "model_x")
        _finite(model_y, "model_y")
        crop_x, crop_y, _, _ = self.crop_xywh or (
            0, 0, self.source_width, self.source_height)
        source_x = crop_x + (model_x - self.pad_left) / self.scale_x
        source_y = crop_y + (model_y - self.pad_top) / self.scale_y
        return source_x / self.source_width, source_y / self.source_height

    def source_box(self, box_xyxy: tuple[float, float, float, float]
                   ) -> tuple[float, float, float, float]:
        """Map model-input pixel xyxy to normalized source-display xyxy."""
        x0, y0 = self.source_point(box_xyxy[0], box_xyxy[1])
        x1, y1 = self.source_point(box_xyxy[2], box_xyxy[3])
        if not (0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1):
            raise ContractError("model box maps outside the source display frame")
        return x0, y0, x1, y1


@dataclass(frozen=True)
class ComputeBudget:
    """Caller-owned upper bounds; adapters may refuse instead of exceeding them."""

    max_wall_seconds: float | None = None
    max_peak_rss_mb: float | None = None
    max_frames: int | None = None
    max_audio_samples: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_wall_seconds", "max_peak_rss_mb"):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name)
                if value <= 0:
                    raise ContractError(f"{name} must be positive")
        for name in ("max_frames", "max_audio_samples"):
            value = getattr(self, name)
            if value is not None:
                _positive_int(value, name)


@dataclass(frozen=True)
class CapabilityInput:
    capability: str
    source_id: str
    source_sha256: str
    source_clock: SourceClock
    sample_clock: SourceClock | None = None
    clock_transforms: tuple[TimeMap, ...] = ()
    source_intervals: tuple[EvidenceInterval, ...] = ()
    frame_range: FrameRange | None = None
    sample_range: SampleRange | None = None
    prompt: str | None = None
    coordinates: CoordinateFrame | None = None
    budget: ComputeBudget = field(default_factory=ComputeBudget)
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability or not self.source_id:
            raise ContractError("capability and source_id are required")
        try:
            input_kind, prompt_required = CAPABILITY_INPUTS[self.capability]
        except KeyError as exc:
            raise ContractError(f"unknown model capability {self.capability!r}") from exc
        if (len(self.source_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.source_sha256.lower())):
            raise ContractError("source_sha256 must contain 64 hexadecimal characters")
        if self.source_clock.asset_id != self.source_id:
            raise ContractError("source clock must belong to the requested source asset")
        if (self.sample_clock is not None
                and self.sample_clock.asset_id != self.source_id):
            raise ContractError("sample clock must belong to the requested source asset")
        if self.frame_range is None and self.sample_range is None:
            raise ContractError("request must name a source frame range or sample range")
        if input_kind in {"video", "audiovisual"} and self.frame_range is None:
            raise ContractError(f"{self.capability} requires a source frame range")
        if input_kind in {"audio", "audiovisual"} and self.sample_range is None:
            raise ContractError(f"{self.capability} requires a source sample range")
        if input_kind == "video" and self.sample_range is not None:
            raise ContractError(f"{self.capability} takes video inputs only")
        if input_kind == "audio" and self.frame_range is not None:
            raise ContractError(f"{self.capability} takes audio inputs only")
        if (input_kind == "audiovisual" and self.frame_range is not None
                and self.sample_range is not None and self.sample_clock is None):
            raise ContractError("audiovisual requests need a separate sample_clock")
        if self.frame_range is not None and self.coordinates is None:
            raise ContractError("video requests must declare the source coordinate frame")
        if not self.source_intervals:
            derived = []
            if self.frame_range is not None and self.source_clock.frame_rate_num is not None:
                derived.append(interval_from_frames(self.source_clock, self.frame_range))
            sample_clock_for_interval = self.sample_clock or self.source_clock
            if self.sample_range is not None:
                derived.append(interval_from_samples(sample_clock_for_interval, self.sample_range))
            if len(derived) != int(self.frame_range is not None) + int(self.sample_range is not None):
                raise ContractError("requests need authoritative source_intervals when clock conversion is unavailable")
            object.__setattr__(self, "source_intervals", tuple(derived))
        interval_by_clock = {interval.clock_id: interval for interval in self.source_intervals}
        if len(interval_by_clock) != len(self.source_intervals):
            raise ContractError("source_intervals must use distinct source clocks")
        expected_clock_ids = set()
        if self.frame_range is not None:
            expected_clock_ids.add(self.source_clock.id)
        if self.sample_range is not None:
            expected_clock_ids.add((self.sample_clock or self.source_clock).id)
        if set(interval_by_clock) != expected_clock_ids:
            raise ContractError("source_intervals must cover exactly the requested source clocks")
        if self.frame_range is not None:
            video_interval = interval_by_clock.get(self.source_clock.id)
            if (video_interval is None or video_interval.start_frame != self.frame_range.start
                    or video_interval.end_frame != self.frame_range.end):
                raise ContractError("video source_interval must match the requested frame range")
        if self.sample_range is not None:
            sample_clock_for_interval = self.sample_clock or self.source_clock
            sample_interval = interval_by_clock.get(sample_clock_for_interval.id)
            if (sample_interval is None or sample_interval.start_sample != self.sample_range.start
                    or sample_interval.end_sample != self.sample_range.end):
                raise ContractError("audio source_interval must match the requested sample range")
        if (self.frame_range is not None and self.sample_range is not None
                and self.sample_clock is None):
            raise ContractError("audiovisual requests need a separate sample_clock")
        if (self.sample_clock is not None
                and self.sample_clock.id == self.source_clock.id
                and self.frame_range is not None):
            raise ContractError("video and sample clocks must have distinct ids")
        if any(not isinstance(transform, TimeMap)
               for transform in self.clock_transforms):
            raise ContractError("clock_transforms must reuse Project Model TimeMap records")
        if input_kind == "audiovisual":
            video_interval = interval_by_clock[self.source_clock.id]
            sample_clock = self.sample_clock
            sample_interval = interval_by_clock[sample_clock.id]
            if not any(_clock_map_overlaps(map_row, sample_interval,
                                           video_interval)
                       for map_row in self.clock_transforms):
                raise ContractError(
                    "audiovisual request needs a measured clock transform covering both source ranges")
        if self.sample_clock is not None and self.sample_range is None:
            raise ContractError("sample_clock requires a sample_range")
        if prompt_required and (self.prompt is None or not self.prompt.strip()):
            raise ContractError(f"{self.capability} requires a prompt")
        if self.prompt is not None and not self.prompt.strip():
            raise ContractError("prompt must be non-empty when supplied")
        if not isinstance(self.options, dict):
            raise ContractError("options must be a JSON-compatible object")
        try:
            json.dumps(self.options, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ContractError("options must contain deterministic JSON values") from exc
        if (self.frame_range is not None and self.budget.max_frames is not None
                and self.frame_range.end - self.frame_range.start > self.budget.max_frames):
            raise ContractError("requested frame range exceeds max_frames budget")
        if (self.sample_range is not None and self.budget.max_audio_samples is not None
                and self.sample_range.end - self.sample_range.start > self.budget.max_audio_samples):
            raise ContractError("requested sample range exceeds max_audio_samples budget")


@dataclass(frozen=True)
class SourceVideoFrame:
    """One decoded oriented frame with its authoritative source frame index."""

    frame_index: int
    image: Any
    width: int
    height: int

    def __post_init__(self) -> None:
        if isinstance(self.frame_index, bool) or not isinstance(self.frame_index, int) or self.frame_index < 0:
            raise ContractError("decoded frame must carry a non-negative source frame index")
        _positive_int(self.width, "decoded frame width")
        _positive_int(self.height, "decoded frame height")


@dataclass(frozen=True)
class SourceAudioSamples:
    """Decoded source samples retaining their original half-open range."""

    sample_range: SampleRange
    samples: Any


class CapabilityMedia(Protocol):
    """Source Memory-backed media reader, separate from model-specific code."""

    def read_video_frames(self, request: CapabilityInput) -> Iterable[SourceVideoFrame]:
        """Yield oriented frames and their original source frame positions."""

    def read_audio_samples(self, request: CapabilityInput) -> SourceAudioSamples:
        """Read exactly the requested source sample range."""


def read_video_frames(media: CapabilityMedia, request: CapabilityInput
                      ) -> tuple[SourceVideoFrame, ...]:
    """Read in source order, refusing frames outside the declared interval."""
    if request.frame_range is None or request.coordinates is None:
        raise ContractError("video frame reads require a video request")
    frames = tuple(media.read_video_frames(request))
    previous = -1
    for frame in frames:
        if not request.frame_range.start <= frame.frame_index < request.frame_range.end:
            raise ContractError("media reader returned a frame outside the request")
        if frame.frame_index <= previous:
            raise ContractError("media reader frames must be unique and source ordered")
        if (frame.width != request.coordinates.source_width
                or frame.height != request.coordinates.source_height):
            raise ContractError("media reader frame dimensions disagree with source coordinates")
        previous = frame.frame_index
    if request.budget.max_frames is not None and len(frames) > request.budget.max_frames:
        raise ContractError("decoded frame count exceeds max_frames budget")
    return frames


def read_audio_samples(media: CapabilityMedia, request: CapabilityInput
                       ) -> SourceAudioSamples:
    """Read and verify exactly the source sample interval named by request."""
    if request.sample_range is None:
        raise ContractError("audio sample reads require an audio request")
    audio = media.read_audio_samples(request)
    if audio.sample_range != request.sample_range:
        raise ContractError("media reader sample range disagrees with the request")
    return audio


@dataclass(frozen=True)
class Confidence:
    """Native score and its meaning; calibrated probability is explicit."""

    raw_score: float | None
    semantics: Literal["uncalibrated_score", "calibrated_probability", "rank_only", "unknown"]
    calibrated_probability: float | None = None
    calibration_id: str | None = None

    def __post_init__(self) -> None:
        if self.raw_score is not None:
            _finite(self.raw_score, "raw_score")
        if self.semantics not in CONFIDENCE_SEMANTICS:
            raise ContractError(f"unknown score semantics {self.semantics!r}")
        if self.calibrated_probability is not None:
            _finite(self.calibrated_probability, "calibrated_probability")
            if not 0 <= self.calibrated_probability <= 1 or not self.calibration_id:
                raise ContractError("calibrated probability requires [0,1] value and calibration_id")
        if self.semantics == "calibrated_probability" and self.calibrated_probability is None:
            raise ContractError("calibrated_probability semantics requires a calibrated value")


@dataclass(frozen=True)
class EvidenceInterval:
    clock_id: str
    start_tick: int
    end_tick: int
    start_frame: int | None = None
    end_frame: int | None = None
    start_sample: int | None = None
    end_sample: int | None = None

    def __post_init__(self) -> None:
        if not self.clock_id or self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ContractError("evidence interval must be non-empty on a named source clock")
        if (self.start_frame is None) != (self.end_frame is None):
            raise ContractError("frame interval endpoints must be supplied together")
        if (self.start_sample is None) != (self.end_sample is None):
            raise ContractError("sample interval endpoints must be supplied together")
        for label, start, end in (("frame", self.start_frame, self.end_frame),
                                  ("sample", self.start_sample, self.end_sample)):
            if (start is not None and
                    (isinstance(start, bool) or isinstance(end, bool)
                     or not isinstance(start, int) or not isinstance(end, int)
                     or start < 0 or end <= start)):
                raise ContractError(f"{label} evidence interval must be non-empty")


def _floor_fraction(value: Fraction) -> int:
    return value.numerator // value.denominator


def _ceil_fraction(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def interval_from_frames(clock: SourceClock, frames: FrameRange) -> EvidenceInterval:
    """Convert source frame edges to clock ticks without accumulating drift."""
    if clock.frame_rate_num is None or clock.frame_rate_den is None:
        raise ContractError("source clock has no authoritative frame rate")
    tick_ratio = Fraction(clock.ticks_per_second * clock.frame_rate_den,
                          clock.frame_rate_num)
    start = _floor_fraction(frames.start * tick_ratio)
    end = _ceil_fraction(frames.end * tick_ratio)
    return EvidenceInterval(clock.id, start, end, frames.start, frames.end)


def interval_from_samples(clock: SourceClock, samples: SampleRange) -> EvidenceInterval:
    """Convert source sample edges to clock ticks without float rounding."""
    tick_ratio = Fraction(clock.ticks_per_second, samples.sample_rate_hz)
    start = _floor_fraction(samples.start * tick_ratio)
    end = _ceil_fraction(samples.end * tick_ratio)
    return EvidenceInterval(clock.id, start, end,
                            start_sample=samples.start, end_sample=samples.end)


def _clock_map_overlaps(transform: TimeMap, audio: EvidenceInterval,
                        video: EvidenceInterval) -> bool:
    """Whether one existing PM clock map connects the requested intervals."""
    if (transform.source_clock == audio.clock_id
            and transform.target_clock == video.clock_id):
        source, target = audio, video
    elif (transform.source_clock == video.clock_id
            and transform.target_clock == audio.clock_id):
        source, target = video, audio
    else:
        return False
    if source.start_tick < transform.source_start or source.end_tick > transform.source_end:
        return False
    offset = transform.offset_ticks
    if offset is None:
        offset = Fraction(transform.target_start) - transform.source_start * transform.slope
    mapped_start = source.start_tick * transform.slope + offset
    mapped_end = source.end_tick * transform.slope + offset
    if (mapped_start < transform.target_start
            or mapped_end > transform.target_end):
        return False
    return mapped_start < target.end_tick and mapped_end > target.start_tick


@dataclass(frozen=True)
class NormalizedDetection:
    label: str
    box_xyxy: tuple[float, float, float, float]
    confidence: Confidence
    interval: EvidenceInterval | None = None
    points_xy: tuple[tuple[float, float], ...] = ()
    mask_artifact: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.label:
            raise ContractError("detection label is required")
        x0, y0, x1, y1 = self.box_xyxy
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
            raise ContractError("detection box must be ordered normalized source-display xyxy")
        for x, y in self.points_xy:
            if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1):
                raise ContractError("points must use normalized source-display coordinates")


@dataclass(frozen=True)
class NormalizedEmbedding:
    vector: tuple[float, ...]
    space_id: str
    dimension: int
    interval: EvidenceInterval | None = None

    def __post_init__(self) -> None:
        _positive_int(self.dimension, "embedding dimension")
        if not self.space_id or len(self.vector) != self.dimension:
            raise ContractError("embedding must name its vector space and match its dimension")
        if any(not math.isfinite(value) for value in self.vector):
            raise ContractError("embedding values must be finite")


@dataclass(frozen=True)
class NormalizedMask:
    """A mask artifact in source-display dimensions, stored outside the PM."""

    artifact_uri: str
    width: int
    height: int
    encoding: str
    interval: EvidenceInterval | None = None

    def __post_init__(self) -> None:
        if not self.artifact_uri or not self.encoding:
            raise ContractError("mask must name its external artifact and encoding")
        _positive_int(self.width, "mask width")
        _positive_int(self.height, "mask height")


@dataclass(frozen=True)
class RawArtifact:
    uri: str
    sha256: str
    media_type: str = "application/json"

    def __post_init__(self) -> None:
        if (not self.uri or len(self.sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.sha256.lower())):
            raise ContractError("raw output must be a separately stored artifact with SHA-256")


@dataclass(frozen=True)
class AdapterResult:
    status: Literal["completed", "unsupported", "failed", "no_evidence"]
    supported_features: frozenset[str] = frozenset()
    unsupported_features: frozenset[str] = frozenset()
    detections: tuple[NormalizedDetection, ...] = ()
    intervals: tuple[EvidenceInterval, ...] = ()
    embeddings: tuple[NormalizedEmbedding, ...] = ()
    masks: tuple[NormalizedMask, ...] = ()
    raw_output: RawArtifact | None = None
    failure: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status == "failed" and self.failure not in FAILURE_STATES:
            raise ContractError(f"failed result needs a known failure state: {FAILURE_STATES}")
        if self.status != "failed" and self.failure is not None:
            raise ContractError("failure state is valid only for failed results")
        if self.status == "no_evidence" and not self.detail:
            raise ContractError("no_evidence must state the searched scope or absence basis")
        if self.status in {"completed", "no_evidence"} and self.raw_output is None:
            raise ContractError("completed inference must retain its raw output artifact")
        if self.status == "completed" and not self.supported_features:
            raise ContractError("completed result must declare the features it actually supports")
        if self.supported_features & self.unsupported_features:
            raise ContractError("a feature cannot be both supported and unsupported")
        if self.status == "unsupported" and not self.unsupported_features:
            raise ContractError("unsupported result must name unsupported features")


class CapabilityAdapter(Protocol):
    """Thin implementation wrapper. Lifecycle/loading remains Ren-owned."""

    adapter_id: str
    capability: str
    model_revision: str
    supported_features: frozenset[str]

    def infer(self, request: CapabilityInput, model: Any,
              media: CapabilityMedia) -> AdapterResult:
        """Run against an already lifecycle-managed model instance."""


def input_fingerprint(request: CapabilityInput, stage_revision: str) -> str:
    """Stable cache key for deterministic fixture decode/preprocess stages."""
    if not stage_revision:
        raise ContractError("stage_revision is required for a cache key")
    payload = {
        "request": {
            "capability": request.capability,
            "source_id": request.source_id,
            "source_sha256": request.source_sha256.lower(),
            "source_clock": request.source_clock.__dict__,
            "sample_clock": (None if request.sample_clock is None
                             else request.sample_clock.__dict__),
            "clock_transforms": [
                {
                    "source_clock": item.source_clock,
                    "target_clock": item.target_clock,
                    "source_start": item.source_start,
                    "source_end": item.source_end,
                    "target_start": item.target_start,
                    "target_end": item.target_end,
                    "slope": [item.slope.numerator, item.slope.denominator],
                    "uncertainty_ticks": item.uncertainty_ticks,
                    "offset_ticks": (None if item.offset_ticks is None else [
                        item.offset_ticks.numerator, item.offset_ticks.denominator]),
                    "invertible": item.invertible,
                } for item in request.clock_transforms],
            "source_intervals": [item.__dict__ for item in request.source_intervals],
            "frame_range": (None if request.frame_range is None
                            else request.frame_range.__dict__),
            "sample_range": (None if request.sample_range is None
                             else request.sample_range.__dict__),
            "prompt": request.prompt,
            "coordinates": (None if request.coordinates is None
                            else request.coordinates.__dict__),
            "options": request.options,
        },
        "stage_revision": stage_revision,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def run_adapter(adapter: CapabilityAdapter, request: CapabilityInput,
                model: Any, media: CapabilityMedia) -> AdapterResult:
    """Validate adapter routing and call it without creating a new scheduler."""
    if request.capability != adapter.capability:
        raise ContractError(
            f"adapter {adapter.adapter_id!r} serves {adapter.capability!r}, "
            f"not {request.capability!r}")
    result = adapter.infer(request, model, media)
    if not isinstance(result, AdapterResult):
        raise ContractError("adapter must return AdapterResult")
    unknown = result.supported_features - adapter.supported_features
    if unknown:
        raise ContractError(f"adapter claimed undeclared features: {sorted(unknown)}")
    timed = list(result.intervals)
    timed.extend(item.interval for item in result.detections if item.interval is not None)
    timed.extend(item.interval for item in result.embeddings if item.interval is not None)
    timed.extend(item.interval for item in result.masks if item.interval is not None)
    for interval in timed:
        if interval.start_frame is not None:
            if (request.frame_range is None or interval.clock_id != request.source_clock.id
                    or interval.start_frame < request.frame_range.start
                    or interval.end_frame > request.frame_range.end):
                raise ContractError("adapter frame evidence exceeds the requested source range or clock")
            allowed = next(item for item in request.source_intervals
                           if item.clock_id == interval.clock_id)
            if (interval.start_tick < allowed.start_tick or interval.end_tick > allowed.end_tick):
                raise ContractError("adapter frame evidence ticks exceed the requested source interval")
        elif interval.start_sample is not None:
            sample_clock = request.sample_clock or request.source_clock
            if (request.sample_range is None or interval.clock_id != sample_clock.id
                    or interval.start_sample < request.sample_range.start
                    or interval.end_sample > request.sample_range.end):
                raise ContractError("adapter sample evidence exceeds the requested source range or clock")
            allowed = next(item for item in request.source_intervals
                           if item.clock_id == interval.clock_id)
            if (interval.start_tick < allowed.start_tick or interval.end_tick > allowed.end_tick):
                raise ContractError("adapter sample evidence ticks exceed the requested source interval")
        else:
            options = []
            options.extend(item for item in request.source_intervals
                           if item.clock_id == interval.clock_id)
            if not options:
                raise ContractError("adapter output uses a clock other than the requested source clocks")
            if not any(interval.start_tick >= allowed.start_tick
                       and interval.end_tick <= allowed.end_tick for allowed in options):
                raise ContractError("adapter time evidence exceeds the requested source range")
    if request.coordinates is not None:
        for mask in result.masks:
            if (mask.width != request.coordinates.source_width
                    or mask.height != request.coordinates.source_height):
                raise ContractError("mask dimensions must match the oriented source display")
    return result

