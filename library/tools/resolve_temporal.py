"""The single boundary between Ren's half-open frame spans and Resolve.

Ren represents every played interval as ``[start, end)``. Resolve's
AppendToTimeline fields are projected here, and timeline item reads are
normalized back to that convention. Source frames use the source clock;
record frames use the timeline clock.
"""
from __future__ import annotations

from dataclasses import dataclass


def _frame(value, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer frame")
    result = int(value)
    if result != value:
        raise TypeError(f"{name} must be an integer frame")
    return result


@dataclass(frozen=True)
class FrameSpan:
    start: int
    end: int

    def __post_init__(self):
        start, end = _frame(self.start, "start"), _frame(self.end, "end")
        if start < 0 or end <= start:
            raise ValueError(f"invalid half-open frame span [{start}, {end})")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> int:
        return self.end - self.start


def span_from_start_duration(start: int, duration: int) -> FrameSpan:
    """Build a half-open span from its start and positive frame count."""
    left, count = _frame(start, "start"), _frame(duration, "duration")
    if count < 1:
        raise ValueError("duration must be positive")
    return FrameSpan(left, left + count)


def append_fields(source_start: int, source_end: int, record_start: int,
                  track_index: int, media_type: int = 1) -> dict:
    """Project a Ren source span and record start to Resolve append fields.

    Resolve's ``endFrame`` is exclusive. ``trackIndex`` is Resolve's
    one-based track number. ``recordFrame`` is a timeline frame.
    """
    source = FrameSpan(source_start, source_end)
    record = _frame(record_start, "record_start")
    track = _frame(track_index, "track_index")
    if record < 0 or track < 1:
        raise ValueError("record_start must be non-negative and track_index positive")
    return {"startFrame": source.start, "endFrame": source.end,
            "trackIndex": track, "recordFrame": record,
            "mediaType": int(media_type)}


def append_duration_fields(source_start: int, duration: int,
                           record_start: int, track_index: int,
                           media_type: int = 1) -> dict:
    """Project a source start and duration to Resolve's half-open fields."""
    start = _frame(source_start, "source_start")
    count = _frame(duration, "duration")
    if start < 0 or count < 1:
        raise ValueError("source_start must be non-negative and duration positive")
    span = span_from_start_duration(start, count)
    return append_fields(span.start, span.end, record_start,
                         track_index, media_type)


def append_hold_fields(held_source_frame: int, duration: int,
                       record_start: int, track_index: int,
                       media_type: int = 1) -> tuple[dict, FrameSpan]:
    """Build Resolve's source window for a zero-speed hold.

    PR 2692 documented that ``AppendToTimeline.startFrame`` for source
    media is one-based while ``GetSourceStartFrame`` is zero-based. To
    hold frame H for N record frames, the appended source window ends at
    H and starts N-1 frames earlier; the API start is then shifted +1.
    """
    held = _frame(held_source_frame, "held_source_frame")
    count = _frame(duration, "duration")
    if held < 0 or count < 1 or held - count + 1 < 0:
        raise ValueError("hold window must fit at or after source frame zero")
    normalized = FrameSpan(held - count + 1, held + 1)
    fields = append_fields(normalized.start + 1, normalized.end + 1,
                           record_start, track_index, media_type)
    return fields, normalized


def hold_append_window(held_source_frame: int, duration: int) -> tuple[int, int, FrameSpan]:
    """Return Resolve's PR 2692 source window and normalized source span."""
    held = _frame(held_source_frame, "held_source_frame")
    count = _frame(duration, "duration")
    if held < 0 or count < 1 or held - count + 1 < 0:
        raise ValueError("hold window must fit at or after source frame zero")
    span = FrameSpan(held - count + 1, held + 1)
    return span.start + 1, span.start + 1 + span.duration, span


def record_span(start, end) -> FrameSpan:
    """Normalize Resolve GetStart/GetEnd to Ren's half-open record span."""
    return FrameSpan(_frame(start, "record start"), _frame(end, "record end"))


def source_span(start, end) -> FrameSpan:
    """Normalize Resolve source frame read-backs as a half-open span.

    GetSourceEndFrame is retained verbatim by timeline_ingest for audit,
    since it can disagree with played duration by one frame. Callers that
    need the played interval should use ``played_source_span`` below.
    """
    return FrameSpan(_frame(start, "source start"), _frame(end, "source end"))


def played_source_span(source_start: int, record: FrameSpan, *,
                       source_fps: float, timeline_fps: float,
                       still: bool = False, zero_speed_hold: bool = False,
                       playback_rate: float = 1.0) -> FrameSpan:
    """Normalize source read-back using the played record duration.

    A still's duration is set in timeline frames. A zero-speed hold plays
    one source frame. Other clips convert record duration across clocks.
    """
    start = _frame(source_start, "source_start")
    if start < 0 or source_fps <= 0 or timeline_fps <= 0:
        raise ValueError("source start and frame rates must be positive")
    if zero_speed_hold:
        count = 1
    elif still:
        count = record.duration
    else:
        if playback_rate <= 0:
            raise ValueError("playback_rate must be positive for a moving clip")
        count = round(record.duration * float(source_fps)
                      / float(timeline_fps) * float(playback_rate))
    return FrameSpan(start, start + max(1, count))


def final_source_frame(source_start: int, record: FrameSpan, *,
                       source_fps: float, timeline_fps: float,
                       playback_rate: float = 1.0) -> int:
    """Source frame shown at the final frame of an ordinary clip."""
    return played_source_span(source_start, record, source_fps=source_fps,
                              timeline_fps=timeline_fps,
                              playback_rate=playback_rate).end - 1


def read_playback_rate(item, *, zero_speed_hold: bool = False) -> float:
    """Read Resolve's speed percentage as a positive source-rate ratio.

    Older Resolve proxies can lack GetSpeed; those retain the documented
    normal-speed default. A zero speed is represented by the explicit hold
    flag, because its played source window is one frame.
    """
    if zero_speed_hold:
        return 0.0
    try:
        speed = item.GetSpeed()
    except AttributeError:
        return 1.0
    if speed is None:
        return 1.0
    try:
        rate = float(speed["Percentage"]) / 100.0
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Resolve speed read-back is malformed: {speed!r}") from exc
    if rate <= 0:
        raise ValueError(f"Resolve speed must be positive for a moving clip: {rate}")
    return rate


def record_duration_for_source(source: FrameSpan, *, source_fps: float,
                               timeline_fps: float, still: bool = False) -> int:
    """Return the timeline duration represented by a source span."""
    if still:
        return source.duration
    if source_fps <= 0 or timeline_fps <= 0:
        raise ValueError("frame rates must be positive")
    return round(source.duration * float(timeline_fps) / float(source_fps))


def record_duration_for_seconds(start: float, end: float,
                                 timeline_fps: float) -> int:
    """Convert a second-based source interval to timeline frames."""
    if end <= start or timeline_fps <= 0:
        raise ValueError("seconds interval and timeline frame rate must be positive")
    return round((float(end) - float(start)) * float(timeline_fps))
