"""The reel build's opt-in OTIO placement: record every append, import once.

`build_reel_timeline(placement_mode="otio")` runs the SAME placers the
default path runs - cards, footage, the TV frame, captions, explainer,
semantic visuals, lower thirds, the post header - against a
`PlacementRecorder` standing in for the media pool. Nothing is imported
and nothing is appended while they run: each append becomes a recorded
placement, and each per-item transform the placers would set after an
append becomes a DEFERRED call. Then one `ImportTimelineFromFile` of the
compiled timeline (`otio_compile`) lands all of it, `import_recorded`
restores what the import does not carry, and the deferred transforms
run on the real items. Every pass after placement (retimes, grade,
punch-in, overrides, ledger, freeze, sweep, links) runs exactly as on
the default path. Why: the per-item scripting path held Resolve 13.35 s
for Reel 09 against 1.26 s for one import
(`docs/OTIO_COMPILATION_MEASURED.md`).

Default OFF. The default path is untouched; this is a second way to
reach the same timeline, chosen per build.

What the import does not carry, and the restore puts back:

- the timeline's custom resolution (measured: dropped);
- the bin each render belongs in - the import files every new pool item
  into the CURRENT folder, so the timeline is imported with its own bin
  current and each render is then moved to `overlay_import_bin`;
- the clip attributes an alpha render needs (`overlay_carriage`), which
  `import_pool_item` sets on every return and an import never sets.

What it refuses, because no OTIO spelling for it was measured: an
image-sequence caption, a transition element (it may carry audio, and
the default path deliberately places it A+V), a program channel other
than 1. Each refusal names itself; the default path builds such a reel.

The audio sweep becomes a READ: the default path deletes the
non-program spill an explicit audio append places beside the program
stream, but the import names the channel and places no spill
(measured: 0 strays against 2), so every speech item's channel is read
back once and a wrong one refuses the build.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from library.tools import otio_compile


class OtioPlacementRefused(RuntimeError):
    """This reel has something the OTIO placement cannot carry."""


def record_duration(profile: dict, name: str, started: float, *,
                    persist: bool = True) -> None:
    """Record and persist one wall-clock phase as soon as it completes."""
    record_elapsed(profile, name, time.perf_counter() - started,
                   persist=persist)


def record_elapsed(profile: dict, name: str, seconds: float, *,
                   persist: bool = True) -> None:
    """Keep a phase in the summary and KPI ledger, even if later work stops."""
    elapsed = round(max(0.0, seconds), 3)
    profile[name] = elapsed
    if not persist:
        return
    try:
        from library.tools import perf_ledger
        perf_ledger.record(
            f"otio_placement.{name}", elapsed,
            phase=name,
            reel=profile.get("timeline_name"),
            placement="otio")
    except Exception:  # noqa: BLE001 - timing must never fail a placement
        pass


class _PathItem:
    """A pool item that is only a path until the import makes it real.

    Answers `GetClipProperty("FPS")` from the REAL pool item where one
    is already pooled (footage, which the master put there), so the
    source-frame arithmetic is the default path's to the frame."""

    def __init__(self, path: str, real=None):
        self.path = path
        self._real = real

    def GetClipProperty(self, key=None):
        return self._real.GetClipProperty(key) if self._real else None


class _Placed:
    """What a recorded append returns: SetProperty lands in the plan."""

    def __init__(self, spec: _Spec):
        self._spec = spec

    def SetProperty(self, key, value):
        self._spec.properties[key] = value
        return True


@dataclass
class _Spec:
    path: str
    track: int
    media_type: int          # 1 video, 2 audio
    source_in: int
    source_out: int          # EXCLUSIVE, as `AppendToTimeline` reads it
    record: int
    properties: dict = field(default_factory=dict)
    channel: int | None = None


class RecordingTimeline:
    """The timeline a recorded build places onto: it holds nothing yet.

    `overlay_placement.apply_placement_transform` hands its write here
    instead of making it; `run_deferred` makes them on the real one."""

    def __init__(self):
        self.deferred: list = []

    def defer(self, label: str, call) -> None:
        self.deferred.append((label, call))


class PlacementRecorder:
    """Stands in for the media pool while a reel build records."""

    def __init__(self, pool):
        self.pool = pool                      # the REAL pool, for footage reads
        self.timeline = RecordingTimeline()
        self.specs: list = []
        self.bins: dict = {}                  # path -> overlay_import_bin
        self._footage: dict = {}
        self.profile: dict = {}

    # The media-pool surface the placers call.
    def AppendToTimeline(self, infos):
        placed = []
        for info in infos:
            if "mediaType" not in info:
                raise OtioPlacementRefused(
                    "an append without mediaType places video AND audio, "
                    "which has no measured OTIO spelling")
            spec = _Spec(path=info["mediaPoolItem"].path,
                         track=int(info["trackIndex"]),
                         media_type=int(info["mediaType"]),
                         source_in=int(info["startFrame"]),
                         source_out=int(info["endFrame"]),
                         record=int(info["recordFrame"]))
            self.specs.append(spec)
            placed.append(_Placed(spec))
        return placed

    def imported(self, path: str, dest) -> _PathItem:
        """`import_pool_item` while recording: the import is the OTIO's."""
        if dest:
            self.bins[path] = tuple(dest)
        return _PathItem(path, self._footage.get(path))

    def pooled(self, path: str):
        """`pool_item_for` while recording: footage is already pooled."""
        if path in self.bins:
            return _PathItem(path)
        if path not in self._footage:
            from library.tools.reel_build import pool_item_for
            self._footage[path] = pool_item_for(self.pool, path)
        real = self._footage[path]
        return _PathItem(path, real) if real is not None else None

    def set_channel(self, channel: int) -> None:
        """The program channel of the audio append just recorded."""
        self.specs[-1].channel = int(channel)


def _media_facts(paths, fps: float) -> dict:
    """{path: (start timecode frame, frame count)}, probed in parallel."""
    import json
    import subprocess

    def probe(path):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration,r_frame_rate", "-of", "json",
             path], capture_output=True, encoding="utf-8", check=True).stdout
        stream = (json.loads(out).get("streams") or [{}])[0]
        num, _, den = (stream.get("r_frame_rate") or "0/1").partition("/")
        rate = float(num) / float(den or 1) if float(num or 0) else fps
        frames = round(float(stream.get("duration") or 0) * rate)
        return path, (otio_compile.media_start_frame(path, fps), frames)

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(probe, sorted(set(paths))))


def compile_recorded(recorder: PlacementRecorder, name: str, track_plan,
                     fps: float, width: int, height: int) -> dict:
    """The recorded placements as one Resolve OTIO document."""
    facts = _media_facts([s.path for s in recorder.specs], fps)
    rows = []
    for spec_row in track_plan.video_tracks + track_plan.audio_tracks:
        media_type = 1 if spec_row.media_type == "video" else 2
        placements = []
        for spec in recorder.specs:
            if spec.media_type != media_type or spec.track != spec_row.index:
                continue
            start, frames = facts[spec.path]
            placements.append(otio_compile.Placement(
                spec.path, source_in=spec.source_in,
                frames=spec.source_out - spec.source_in,
                record_in=spec.record, media_start=start,
                media_frames=max(frames, spec.source_out),
                channel=spec.channel))
        # A new timeline's first audio row is Stereo and every row
        # `AddTrack("audio")` adds is Mono - what the default path's
        # rows read back as (Reel 09's export: Akshita CH1 Stereo,
        # Craig CH1 Mono).
        audio_type = None
        if media_type == 2:
            audio_type = "Stereo" if spec_row.index == 1 else "Mono"
        rows.append(otio_compile.Track(
            spec_row.media_type, spec_row.name, placements, audio_type))
    try:
        return otio_compile.compile_timeline(name, rows, fps, width, height)
    except otio_compile.OtioCompileError as exc:
        raise OtioPlacementRefused(f"{name}: {exc}") from exc


def _move_into(pool, items, dest: tuple) -> None:
    from library.tools.execution.organise_media_pool import ensure_folder
    folder = pool.GetRootFolder()
    for depth, part in enumerate(dest):
        folder = ensure_folder(pool, folder, part, None, dest[:depth])
    if items and not pool.MoveClips(list(items), folder):
        raise OtioPlacementRefused(
            f"Resolve would not move {len(items)} imported render(s) into "
            f"{'/'.join(dest)}")


def import_recorded(project, recorder: PlacementRecorder, name: str,
                    track_plan, fps: float, width: int, height: int,
                    timeline_bin: tuple):
    """ONE import of the recorded build, then the restore. Returns the
    real timeline, current, with every recorded item on it.

    Record frames are ABSOLUTE timeline frames, as the placers pass
    them; the compiled timeline starts at 0, as a reel timeline does,
    and the read-back below judges the absolute spans - so a timeline
    that imported at another start refuses rather than shifting."""
    from library.tools.overlay_carriage import apply_clip_attributes
    from library.tools.reel_build import _ensure_bin_path, _timeline_span
    from library.tools.resolve_lock import assert_current_timeline

    started = time.perf_counter()
    document = compile_recorded(recorder, name, track_plan, fps, width,
                                height)
    record_duration(recorder.profile, "offline_compile_s", started)
    pool = project.GetMediaPool()
    with tempfile.TemporaryDirectory(prefix="reel_otio_") as scratch:
        started = time.perf_counter()
        path = otio_compile.write(document, os.path.join(scratch,
                                                        "reel.otio"))
        record_duration(recorder.profile, "otio_file_write_s", started)
        started = time.perf_counter()
        dest = _ensure_bin_path(pool, timeline_bin)
        before = pool.GetCurrentFolder()
        try:
            pool.SetCurrentFolder(dest)
            record_duration(recorder.profile, "import_setup_s", started)
            started = time.perf_counter()
            timeline = pool.ImportTimelineFromFile(
                path, {"timelineName": name, "importSourceClips": True})
        finally:
            if before is not None:
                pool.SetCurrentFolder(before)
        record_duration(recorder.profile, "timeline_import_s", started)
    if not timeline:
        raise OtioPlacementRefused(
            f"{name}: ImportTimelineFromFile returned None for the "
            f"compiled timeline (every referenced file was on disk)")
    # The custom resolution the import drops - sized BEFORE the timeline
    # becomes current, under the same deadline as the default path. The
    # 2026-10-02 Fusion render-lock race hangs on these same three
    # writes when they land on a just-made-current timeline
    # (https://github.com/prajwal-395/video_editing_pilot/pull/1594);
    # a hang here refuses the build by name after the deadline
    # (`library/tools/resolve_deadline.py`).
    from library.tools import resolve_deadline as _deadline
    started = time.perf_counter()
    try:
        _deadline.apply_timeline_resolution(timeline, width, height)
    except (_deadline.ResolveCallTimeout,
            _deadline.ResolutionNotApplied) as exc:
        raise OtioPlacementRefused(f"{name}: {exc}") from exc
    record_duration(recorder.profile, "resolution_setup_s", started)

    started = time.perf_counter()
    assert_current_timeline(project, timeline)
    record_duration(recorder.profile, "timeline_cursor_check_s", started)

    # Every planned row, under its planned name, and every recorded item
    # where it was recorded - judged by the read-back, never the return.
    started = time.perf_counter()
    missing = []
    for row in track_plan.video_tracks + track_plan.audio_tracks:
        if timeline.GetTrackName(row.media_type, row.index) != row.name:
            missing.append(f"{row.media_type} row {row.index} is not "
                           f"named {row.name!r}")
        media_type = 1 if row.media_type == "video" else 2
        spans = {_timeline_span(item) for item in
                 timeline.GetItemListInTrack(row.media_type, row.index) or []}
        for spec in recorder.specs:
            if spec.media_type == media_type and spec.track == row.index:
                want = (spec.record,
                        spec.record + spec.source_out - spec.source_in)
                if want not in spans:
                    missing.append(f"{os.path.basename(spec.path)} at "
                                   f"{row.media_type[0].upper()}{row.index}"
                                   f"@{spec.record}")
    if missing:
        raise OtioPlacementRefused(
            f"{name}: the imported timeline does not hold what was "
            f"recorded: {missing[:6]}")
    record_duration(recorder.profile, "placement_restore_readback_s", started)

    # The renders: into the bin each belongs in, with their clip
    # attributes. New pool items land in the CURRENT folder, which was
    # the timeline's bin during the import.
    started = time.perf_counter()
    by_path: dict = {}
    for item in dest.GetClipList() or ():
        by_path.setdefault(item.GetClipProperty("File Path") or "",
                           []).append(item)
    by_bin: dict = {}
    for render, bin_path in recorder.bins.items():
        for item in by_path.get(render, ()):
            apply_clip_attributes(item, render)
            by_bin.setdefault(bin_path, []).append(item)
    for bin_path, items in by_bin.items():
        _move_into(pool, items, bin_path)
    record_duration(recorder.profile, "pool_organization_s", started)
    return timeline


def verify_channels(timeline, recorder: PlacementRecorder) -> dict:
    """The default path's `stream_enforcement` record, by one read-back.

    Nothing is deleted: the import placed exactly the named channel."""
    from library.tools.reel_build import _placed_channel, _timeline_span

    started = time.perf_counter()
    record = {"checked": 0, "deleted": [], "unverified": []}
    wrong = []
    for spec in recorder.specs:
        if spec.media_type != 2 or spec.channel is None:
            continue
        for item in timeline.GetItemListInTrack("audio", spec.track) or []:
            if _timeline_span(item) != (spec.record, spec.record
                                        + spec.source_out
                                        - spec.source_in):
                continue
            record["checked"] += 1
            channel = _placed_channel(item)
            label = (f"A{spec.track} {os.path.basename(spec.path)} "
                     f"@{spec.record}")
            if channel is None:
                record["unverified"].append(label)
            elif channel != spec.channel:
                wrong.append(f"{label} carries CH{channel}, program is "
                             f"CH{spec.channel}")
    if wrong:
        raise OtioPlacementRefused(
            f"the import placed the wrong program channel: {wrong[:4]}")
    record_duration(recorder.profile, "channel_checks_s", started)
    return record


def run_deferred(timeline, recorder: PlacementRecorder) -> int:
    """The per-item writes the placers made or deferred, on the real items,
    in the default path's order: an append's SetProperty, then the
    placement transform.

    A recorded SetProperty is NOT carried in the OTIO. Measured on Reel 09
    (2026-10-02, a 3840x2160 project, a 1080x1920 timeline): the TV
    frame's Tilt -55 went in as a fraction of the frame and read back
    -13.75, while the default path's SetProperty held -55 - Pan/Tilt
    units follow the PROJECT resolution (`resolve_transform`), which the
    import does not. So every one is set here, exactly as the placer set
    it, judged by what SetProperty returns."""
    from library.tools.reel_build import _timeline_span

    started = time.perf_counter()
    for spec in recorder.specs:
        if not spec.properties:
            continue
        kind = "video" if spec.media_type == 1 else "audio"
        for item in timeline.GetItemListInTrack(kind, spec.track) or []:
            if (_timeline_span(item) or (None,))[0] == spec.record:
                for key, value in spec.properties.items():
                    if not item.SetProperty(key, value):
                        raise OtioPlacementRefused(
                            f"Resolve refused {key}={value} on "
                            f"{os.path.basename(spec.path)}")
    for label, call in recorder.timeline.deferred:
        note = call(timeline)
        if note:
            print(f"  {label}: {note}", file=sys.stderr)
    record_duration(recorder.profile, "deferred_transform_s", started)
    return len(recorder.timeline.deferred)
