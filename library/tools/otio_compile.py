"""A placement plan, compiled offline into ONE OpenTimelineIO file Resolve imports.

The reel builder places a timeline one scripting call at a time: per item a
pool lookup, an `AppendToTimeline`, a `SetProperty` and read-back per
transform key, then a link pass - 457 calls and 13.35 s of exclusive
Resolve hold for Reel 09. The same timeline, written here with NO Resolve
call and imported once, held Resolve 1.26 s with the restore pass included,
and read back identical on every field of every item.
[docs/OTIO_COMPILATION_MEASURED.md](../../docs/OTIO_COMPILATION_MEASURED.md)
has the run, the diff and what the format drops.

This module is the FREE half: plain JSON in Resolve's own OTIO dialect
(`Resolve_OTIO` metadata), no `opentimelineio` dependency - the same
choice `otio_mix` made. It carries exactly what a base timeline needs and
nothing Resolve was not told by the scripting path either: placement,
transform, link group, program channel, track name and audio subtype.

The laws, each MEASURED (Resolve Studio 21.1, 2026-10-02, a 1080x1920
timeline in a 1080x1920 project) rather than assumed:

1. **Source frames are measured from the media's START TIMECODE.** The
   scripting API's `startFrame` counts from the first frame of the file;
   an OTIO `source_range` counts from the container timecode at the
   NOMINAL rate (`00:08:32:08` at 23.976 is frame 12296). A clip written
   from 0 imports at the wrong source. `media_start_frame` reads it.
2. **Pan is in units of the frame WIDTH and Tilt of the frame HEIGHT**,
   where the scripting API speaks Resolve's pixel-like unit: API Pan -35
   is OTIO -0.032407 on a 1080-wide frame, API Tilt -1836 is -0.95625 on
   a 1920-tall one. Zoom is the same number in both. Measured with project
   and timeline at one resolution only; under a project resolution that
   differs from the timeline's, Pan/Tilt units move
   (`resolve_transform`), so an importer VERIFIES every transform against
   the plan after the import and repairs a mismatch - never trusts this law.
3. **The program channel is `Source Channel ID` = channel - 1.** Channel 1
   is the only one measured (every reel export on disk names ID 0, and the
   scripting path's read-back reads `channel_idx [1]`); another channel
   REFUSES rather than guessing the mapping.
4. **`ImportTimelineFromFile` returns None, saying nothing, when ANY
   referenced file is missing** (`otio_mix` fact 3). `compile_timeline`
   refuses first and names the files.

What the format DROPS on a round-trip (measured, same run): Fusion comps,
clip colour, and the `customData` of every marker; and the timeline's
custom resolution. The reel builder applies comps and the grade AFTER
placement already and writes no clip colour or marker during placement,
so only the resolution is the importer's to restore.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field

#: The scripting API's transform keys and the axis each one is a fraction of.
#: Zoom carries no axis: the same number in both vocabularies (law 2).
TRANSFORM_PARAMETERS = {
    "ZoomX": ("transformationZoomX", None),
    "ZoomY": ("transformationZoomY", None),
    "Pan": ("transformationPan", "width"),
    "Tilt": ("transformationTilt", "height"),
}

#: The only program channel whose OTIO spelling was measured (law 3).
MEASURED_CHANNELS = (1,)


class OtioCompileError(ValueError):
    """The plan cannot be written as an OTIO Resolve would import whole."""


@dataclass(frozen=True)
class Placement:
    """One item: a file, the source frames it plays, where it plays."""

    path: str
    source_in: int            # frames from the file's FIRST frame (API startFrame)
    frames: int
    record_in: int            # frames from the timeline's start
    media_start: int          # the file's start timecode, in frames (law 1)
    media_frames: int
    transform: dict[str, float] = field(default_factory=dict)  # API units
    link_group: int | None = None
    channel: int | None = None  # audio only: the program channel, 1-based
    enabled: bool = True


@dataclass(frozen=True)
class Track:
    kind: str                 # "video" | "audio"
    name: str
    placements: Sequence[Placement]
    audio_type: str | None = None  # "Mono" | "Stereo", as Resolve spells it


def media_start_frame(path: str, fps: float) -> int:
    """The container's start timecode in frames, at the NOMINAL rate.

    0 for a file that declares none (a render). `00:08:32:08` at 23.976
    is 12296: Resolve counts non-drop timecode at the rounded rate."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format_tags=timecode:stream_tags=timecode", "-of", "json", path],
        capture_output=True, encoding="utf-8", check=True).stdout
    data = json.loads(out)
    tags = [data.get("format", {}).get("tags", {})] + [
        s.get("tags", {}) for s in data.get("streams", [])]
    timecode = next((t["timecode"] for t in tags if t.get("timecode")), None)
    if timecode is None:
        return 0
    from library.tools.marker_capture import timecode_to_frames
    return timecode_to_frames(timecode, fps)


def _time(value: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": float(value)}


def _range(start: float, duration: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _time(start, rate),
            "duration": _time(duration, rate)}


def _transform_effect(transform: dict[str, float], width: int,
                      height: int) -> dict:
    axes = {"width": float(width), "height": float(height)}
    unknown = sorted(set(transform) - set(TRANSFORM_PARAMETERS))
    if unknown:
        raise OtioCompileError(
            f"transform key(s) {unknown} have no measured OTIO spelling; "
            f"one of {sorted(TRANSFORM_PARAMETERS)}")
    parameters = []
    for key, (parameter_id, axis) in TRANSFORM_PARAMETERS.items():
        if key not in transform:
            continue
        value = transform[key]
        parameters.append({
            "Parameter ID": parameter_id, "Variant Type": "Double",
            "Parameter Value": value / axes[axis] if axis else value,
            "Key Frames": {}})
    return {"OTIO_SCHEMA": "Effect.1", "name": "",
            "effect_name": "Resolve Effect",
            "metadata": {"Resolve_OTIO": {
                "Effect Name": "Transform", "Name": "Transform",
                "Enabled": True, "Type": 2, "Display Type": 1,
                "Parameters": parameters}}}


def _clip(p: Placement, kind: str, rate: float, width: int,
          height: int) -> dict:
    meta: dict = {}
    if p.link_group is not None:
        meta["Link Group ID"] = p.link_group
    if kind == "audio" and p.channel is not None:
        if p.channel not in MEASURED_CHANNELS:
            raise OtioCompileError(
                f"{os.path.basename(p.path)}: program channel {p.channel} "
                f"has no measured OTIO spelling (only {MEASURED_CHANNELS})")
        meta["Channels"] = [{"Source Channel ID": p.channel - 1,
                             "Source Track ID": 0}]
    name = os.path.basename(p.path)
    return {
        "OTIO_SCHEMA": "Clip.2", "name": name,
        "metadata": {"Resolve_OTIO": meta} if meta else {},
        "source_range": _range(p.media_start + p.source_in, p.frames, rate),
        "effects": ([_transform_effect(p.transform, width, height)]
                    if p.transform and kind == "video" else []),
        "markers": [], "enabled": p.enabled,
        "media_references": {"DEFAULT_MEDIA": {
            "OTIO_SCHEMA": "ExternalReference.1", "name": name,
            "metadata": {}, "target_url": p.path,
            "available_range": _range(p.media_start, p.media_frames, rate),
            "available_image_bounds": None}},
        "active_media_reference_key": "DEFAULT_MEDIA"}


def _gap(frames: int, rate: float) -> dict:
    return {"OTIO_SCHEMA": "Gap.1", "name": "", "metadata": {},
            "source_range": _range(0, frames, rate), "effects": [],
            "markers": [], "enabled": True}


def compile_timeline(name: str, tracks: Sequence[Track], rate: float,
                     width: int, height: int) -> dict:
    """The Resolve OTIO document for `tracks`, video rows then audio rows
    in the order given (Resolve numbers each kind from 1 in that order).

    Refuses a missing file, an overlap on one row and anything without a
    measured spelling, because Resolve's own refusal is a silent None."""
    missing = sorted({p.path for t in tracks for p in t.placements
                      if not os.path.exists(p.path)})
    if missing:
        raise OtioCompileError(
            f"{len(missing)} referenced file(s) are not on disk, and Resolve "
            f"would import nothing: {missing[:5]}")
    children = []
    for track in tracks:
        if track.kind not in ("video", "audio"):
            raise OtioCompileError(f"track kind {track.kind!r}")
        items, position = [], 0
        for p in sorted(track.placements, key=lambda q: q.record_in):
            if p.record_in < position:
                raise OtioCompileError(
                    f"{track.name}: {os.path.basename(p.path)} at "
                    f"{p.record_in} overlaps the item ending at {position}")
            if p.record_in > position:
                items.append(_gap(p.record_in - position, rate))
            items.append(_clip(p, track.kind, rate, width, height))
            position = p.record_in + p.frames
        meta: dict = {"Locked": False}
        if track.kind == "audio" and track.audio_type:
            meta["Audio Type"] = track.audio_type
        children.append({
            "OTIO_SCHEMA": "Track.1", "name": track.name,
            "kind": "Video" if track.kind == "video" else "Audio",
            "metadata": {"Resolve_OTIO": meta}, "source_range": None,
            "effects": [], "markers": [], "enabled": True,
            "children": items})
    return {"OTIO_SCHEMA": "Timeline.1", "name": name,
            "metadata": {"Resolve_OTIO": {"Resolve OTIO Meta Version": "1.0"}},
            "global_start_time": _time(0, rate),
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "", "metadata": {},
                       "source_range": None, "effects": [], "markers": [],
                       "enabled": True, "children": children}}


def write(document: dict, path: str) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1)
    return path

