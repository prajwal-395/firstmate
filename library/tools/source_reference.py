"""The universal source reference: one way to point at the original data.

A claim about a picture is worth as much as the bytes it was read from,
and a claim that cannot say which bytes those were is a claim about
nothing. This module is the ONE vocabulary for that pointer, and every
producer and consumer of the canonical Project Model speaks it.

The reference composes what the model already carries:

``asset``       the original file, whole.
``stream``      one stream inside an asset.
``element``     one native element of a stream - a frame or a sample -
                on the clock that counts it.
``interval``    a run of a stream's elements, on the clock that counts
                them.
``region``      a place in a named coordinate space.
``sub_element`` an optional finer name inside another reference (a mask,
                a track, a layer).

Two properties are deliberate. First, the vocabulary names no
application and no model provider: a reference stays meaningful when
the analysis that made it came from one tool and the edit that uses it
is executed by another. Second, a reference names the SOURCE and never
a derived record: the observation that described a frame is not the
frame, and grounding a claim in the description would be grounding it
in a caption.

The reference is data, not a class: the canonical model is JSON, and the
same spelling travels through Rust, Python and the schema. These
functions build and parse that spelling strictly, so a malformed
reference is refused by name instead of being half-understood.
"""

from __future__ import annotations

import math
from typing import Any

ASSET = "asset"
STREAM = "stream"
ELEMENT = "element"
INTERVAL = "interval"
REGION = "region"
SUB_ELEMENT = "sub_element"

REFERENCE_KINDS = (ASSET, STREAM, ELEMENT, INTERVAL, REGION, SUB_ELEMENT)
"""Every kind a source reference may carry. The list IS the contract:
a kind that is not here has no resolver and is refused."""

FRAME = "frame"
SAMPLE = "sample"

ELEMENT_KINDS = (FRAME, SAMPLE)
"""The native elements a source can be addressed by."""

#: The kinds that reach the source itself. A measurement is complete
#: only when at least one of its references is one of these: an asset
#: names the file, and a file is not a place in it.
SOURCE_KINDS = (STREAM, ELEMENT, INTERVAL, REGION)

#: The reference spellings that predate this contract. They are read
#: (a stored document keeps its own words) and never written.
LEGACY_REFERENCE_KINDS = ("source_sample", "source_frame", "observation")
"""The pre-contract spellings, kept for reading old documents.

``source_frame`` and ``source_sample`` name a stream and an index and
are the modern ``element`` without its clock; ``observation`` names a
derived record, which is not a source reference at all. Nothing writes
them any more, and :func:`upgrade_legacy` turns the first two into the
modern spelling when the caller holds the clock they were counted on.
"""


class SourceReferenceError(ValueError):
    """A reference that is not one of the kinds, or is malformed.

    Carries the offending kind so the refusal names what was wrong
    rather than only that something was.
    """

    def __init__(self, message: str, *, kind: str | None = None) -> None:
        self.kind = kind
        super().__init__(message)


#: The ticks per second of a source's own clock in the canonical Project
#: Model: one millisecond per tick. The clock policy belongs to the
#: model that carries the clock, and this is the one it writes.
SOURCE_TICKS_PER_SECOND = 1000


def asset_id_for_digest(content_digest: str) -> str:
    """The id a source's asset is known by.

    One spelling, so a reference built by a query surface and a
    reference built by the model that carries it name the same asset.
    """
    if not content_digest:
        raise SourceReferenceError("an asset id names a digest")
    return f"asset-{content_digest}"


def clock_id_for_digest(content_digest: str) -> str:
    """The id a source's clock is known by."""
    if not content_digest:
        raise SourceReferenceError("a clock id names a digest")
    return f"clock-{content_digest}"


def stream_id_for_digest(content_digest: str, stream_index: int = 0) -> str:
    """The id a source's stream is known by."""
    if not content_digest:
        raise SourceReferenceError("a stream id names a digest")
    if stream_index == 0:
        return f"stream-{content_digest}"
    return f"stream-{content_digest}-{stream_index}"


def ticks_for_seconds(start_seconds: float, end_seconds: float
                      ) -> tuple[int, int]:
    """Float seconds to half-open integer ticks, or a refusal.

    A span that is not finite, or not positive, cannot be counted: a
    zero-length measurement is not a half-open interval, and widening
    it would invent evidence.
    """
    try:
        start = float(start_seconds)
        end = float(end_seconds)
    except (TypeError, ValueError):
        raise SourceReferenceError("a span is counted in seconds") from None
    if not (math.isfinite(start) and math.isfinite(end)):
        raise SourceReferenceError("a span is finite")
    start_tick = math.floor(start * SOURCE_TICKS_PER_SECOND)
    end_tick = math.ceil(end * SOURCE_TICKS_PER_SECOND)
    if end_tick <= start_tick:
        raise SourceReferenceError("a span is non-empty")
    return start_tick, end_tick


def interval_for_digest(content_digest: str, start_seconds: float,
                         end_seconds: float) -> dict[str, Any]:
    """The reference to a span of a source, on that source's own clock.

    The span is counted in whole ticks on the source's millisecond
    clock, exactly as the canonical Project Model counts it, so a
    reference means the same thing to a reader that holds the model and
    to a reader that holds only the source memory.
    """
    start_tick, end_tick = ticks_for_seconds(start_seconds, end_seconds)
    return interval(stream_id_for_digest(content_digest),
                    clock_id_for_digest(content_digest), start_tick, end_tick)


def asset(asset_id: str) -> dict[str, Any]:
    """A reference to the original file, whole."""
    if not asset_id:
        raise SourceReferenceError("an asset reference names an asset")
    return {"kind": ASSET, "asset_id": asset_id}


def stream(stream_id: str) -> dict[str, Any]:
    """A reference to one stream inside an asset."""
    if not stream_id:
        raise SourceReferenceError("a stream reference names a stream")
    return {"kind": STREAM, "stream_id": stream_id}


def element(stream_id: str, clock_id: str, *, frame_index: int | None = None,
            sample_index: int | None = None) -> dict[str, Any]:
    """A reference to one native element of a stream, with its clock.

    A frame and a sample are different things and are never both given:
    a reference that names two indices names nothing.
    """
    if not stream_id or not clock_id:
        raise SourceReferenceError(
            "an element reference names a stream and the clock that "
            "counts it")
    if (frame_index is None) == (sample_index is None):
        raise SourceReferenceError(
            "an element reference names exactly one of a frame index "
            "and a sample index")
    if frame_index is not None:
        if frame_index < 0:
            raise SourceReferenceError("a frame index is not negative")
        element_row: dict[str, Any] = {"kind": FRAME,
                                       "frame_index": int(frame_index)}
    else:
        assert sample_index is not None
        if sample_index < 0:
            raise SourceReferenceError("a sample index is not negative")
        element_row = {"kind": SAMPLE, "sample_index": int(sample_index)}
    return {"kind": ELEMENT, "stream_id": stream_id, "clock_id": clock_id,
            "element": element_row}


def interval(stream_id: str, clock_id: str, start: int, end: int
             ) -> dict[str, Any]:
    """A reference to a run of a stream's elements, on its clock."""
    if not stream_id or not clock_id:
        raise SourceReferenceError(
            "an interval reference names a stream and its clock")
    if not isinstance(start, int) or not isinstance(end, int):
        raise SourceReferenceError("an interval is counted in whole ticks")
    if start >= end:
        raise SourceReferenceError(
            "an interval reference is non-empty and half-open (start < end)")
    return {"kind": INTERVAL, "stream_id": stream_id,
            "interval": {"clock_id": clock_id, "start": int(start),
                         "end": int(end)}}


def region(space_id: str, geometry: dict[str, Any]) -> dict[str, Any]:
    """A reference to a place in a named coordinate space."""
    if not space_id:
        raise SourceReferenceError("a region reference names a space")
    if not isinstance(geometry, dict) or not geometry:
        raise SourceReferenceError("a region reference carries its geometry")
    return {"kind": REGION, "space_id": space_id, "geometry": dict(geometry)}


def sub_element(parent: dict[str, Any], sub_kind: str, sub_id: str
                ) -> dict[str, Any]:
    """A reference to a named part of another reference."""
    parse(parent)
    if not sub_kind or not sub_id:
        raise SourceReferenceError(
            "a sub-element reference names what it is and which one")
    return {"kind": SUB_ELEMENT, "parent": dict(parent),
            "sub_kind": sub_kind, "sub_id": sub_id}


def parse(reference: Any) -> dict[str, Any]:
    """Check one reference and return it, or refuse it by name.

    The check is the whole contract: a reference with an unknown kind,
    a missing id, an element that names two indices or an interval that
    is empty is refused here rather than half-read by whoever follows
    it.
    """
    if not isinstance(reference, dict):
        raise SourceReferenceError("a source reference is a mapping")
    kind = reference.get("kind")
    if kind in LEGACY_REFERENCE_KINDS:
        raise SourceReferenceError(
            f"reference kind {kind!r} is the pre-contract spelling; it "
            "is read, never written", kind=str(kind))
    if kind not in REFERENCE_KINDS:
        raise SourceReferenceError(
            f"reference kind {kind!r} is not one of {REFERENCE_KINDS}",
            kind=str(kind))
    if kind == ASSET:
        if not reference.get("asset_id"):
            raise SourceReferenceError("an asset reference names an asset",
                                       kind=kind)
    elif kind == STREAM:
        if not reference.get("stream_id"):
            raise SourceReferenceError("a stream reference names a stream",
                                       kind=kind)
    elif kind == ELEMENT:
        if not reference.get("stream_id") or not reference.get("clock_id"):
            raise SourceReferenceError(
                "an element reference names a stream and the clock that "
                "counts it", kind=kind)
        element_row = reference.get("element")
        if not isinstance(element_row, dict):
            raise SourceReferenceError(
                "an element reference carries its element", kind=kind)
        element_kind = element_row.get("kind")
        if element_kind not in ELEMENT_KINDS:
            raise SourceReferenceError(
                f"element kind {element_kind!r} is not one of "
                f"{ELEMENT_KINDS}", kind=kind)
        index_key = "frame_index" if element_kind == FRAME else "sample_index"
        index = element_row.get(index_key)
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise SourceReferenceError(
                f"an element reference carries a whole {index_key}",
                kind=kind)
    elif kind == INTERVAL:
        if not reference.get("stream_id"):
            raise SourceReferenceError("an interval reference names a stream",
                                       kind=kind)
        interval_row = reference.get("interval")
        if not isinstance(interval_row, dict):
            raise SourceReferenceError("an interval reference carries its "
                                       "interval", kind=kind)
        if not interval_row.get("clock_id"):
            raise SourceReferenceError("an interval is counted on a clock",
                                       kind=kind)
        start, end = interval_row.get("start"), interval_row.get("end")
        if (not isinstance(start, int) or not isinstance(end, int)
                or isinstance(start, bool) or isinstance(end, bool)):
            raise SourceReferenceError("an interval is counted in whole ticks",
                                       kind=kind)
        if start >= end:
            raise SourceReferenceError(
                "an interval reference is non-empty and half-open "
                "(start < end)", kind=kind)
    elif kind == REGION:
        if not reference.get("space_id"):
            raise SourceReferenceError("a region reference names a space",
                                       kind=kind)
        if not isinstance(reference.get("geometry"), dict):
            raise SourceReferenceError("a region reference carries its "
                                       "geometry", kind=kind)
    elif kind == SUB_ELEMENT:
        parse(reference.get("parent"))
        if not reference.get("sub_kind") or not reference.get("sub_id"):
            raise SourceReferenceError(
                "a sub-element reference names what it is and which one",
                kind=kind)
    return dict(reference)


def names_source(reference: Any) -> bool:
    """Whether the reference reaches the source itself.

    A stream, an element, an interval or a region does. An asset names
    the file, and a file is not a place in it. A sub-element is exactly
    as good as the parent it hangs from.
    """
    if not isinstance(reference, dict):
        return False
    kind = reference.get("kind")
    if kind in SOURCE_KINDS:
        return True
    if kind == SUB_ELEMENT:
        return names_source(reference.get("parent"))
    return False


def upgrade_legacy(reference: Any, clock_id: str | None = None
                    ) -> dict[str, Any] | None:
    """Turn a pre-contract reference into the modern spelling.

    ``source_frame`` and ``source_sample`` become an ``element``; the
    clock they were counted on has to be supplied by the caller, because
    the old spelling did not carry one. ``observation`` names a derived
    record rather than the source, so it has no modern spelling and
    ``None`` is returned for it.
    """
    if not isinstance(reference, dict):
        return None
    kind = reference.get("kind")
    if kind not in LEGACY_REFERENCE_KINDS:
        return dict(reference) if isinstance(reference, dict) else None
    if kind == "observation" or not clock_id:
        return None
    stream_id = reference.get("stream_id")
    if not stream_id:
        return None
    if kind == "source_frame":
        index = reference.get("frame_index")
        if not isinstance(index, int):
            return None
        return element(stream_id, clock_id, frame_index=index)
    index = reference.get("sample_index")
    if not isinstance(index, int):
        return None
    return element(stream_id, clock_id, sample_index=index)
