"""The reel build's opt-in OTIO placement: record every append, import once.

`build_reel_timeline(placement_mode="otio")` runs the SAME placers the
default path runs - cards, footage, the TV frame, captions, explainer,
semantic visuals, lower thirds, the post header - against a
`PlacementRecorder` standing in for the media pool. Nothing is imported
and nothing is appended while they run: each append becomes a recorded
placement, and each per-item transform the placers would set after an
append becomes a DEFERRED call. Then one `ImportTimelineFromFile` of the
compiled timeline (`otio_compile`) lands all of it, `import_recorded`
verifies the imported size, restores the pool bins and clip attributes,
and the deferred transforms run on the real items. Every pass after
placement (retimes, grade,
punch-in, overrides, ledger, freeze, sweep, links) runs exactly as on
the default path. Why: the per-item scripting path held Resolve 13.35 s
for Reel 09 against 1.26 s for one import
(`docs/OTIO_COMPILATION_MEASURED.md`).

Chosen per build. The default is `auto`: `select_placement` reads the
FREE plan and uses this path only when the reel is fully representable,
else append, before the reel's placement hold starts. Four reels built
both ways matched on every item field and every still
(`docs/OTIO_COMPILATION_MEASURED.md`, "Equivalence across reels").

What the import does not carry:

- the timeline's custom resolution (measured: dropped). The importer
  inherits the project's resolution. Where the project default already
  matches the reel, the post-import check reads the size back and
  makes no write. Where it does not, the importer takes the mismatch
  route below instead of falling back to append: the project default
  is switched to the reel size (read back), the import lands
  correctly sized, the cursor parks on the previous timeline while
  the imported (now non-current) timeline is sized to a CUSTOM
  resolution under the existing per-call deadline, the cursor returns
  to the imported timeline, and the project default is restored
  (read back) - so the net mutation is none and no write ever lands
  on a current timeline. A wrong-sized CURRENT timeline is still
  refused, never repaired in place: that write is the measured
  settings-sync deadlock;
- the bin each render belongs in - the import files every new pool item
  into the CURRENT folder, so the timeline is imported with its own bin
  current and each render is then moved to `overlay_import_bin`;
- the clip attributes an alpha render needs (`overlay_carriage`), which
  `import_pool_item` sets on every return and an import never sets.
- source markers on pooled media, which `AppendToTimeline` inherits;
  the import path reapplies and reads them back after import, including
  `customData`.

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


def project_timeline_resolution(project) -> tuple[int, int] | None:
    """Read the project default used by timeline imports, or None."""
    try:
        width = int(project.GetSetting("timelineResolutionWidth"))
        height = int(project.GetSetting("timelineResolutionHeight"))
    except Exception:  # noqa: BLE001 - unknown resolution selects append
        return None
    return (width, height) if width > 0 and height > 0 else None


def project_timeline_resolution_for_timeline(
        timeline) -> tuple[int, int] | None:
    """Read an imported timeline's size without changing it."""
    try:
        width = int(timeline.GetSetting("timelineResolutionWidth"))
        height = int(timeline.GetSetting("timelineResolutionHeight"))
    except Exception:  # noqa: BLE001 - unreadable import must refuse
        return None
    return (width, height) if width > 0 and height > 0 else None


def switch_project_default(project, width: int, height: int) -> tuple[int, int]:
    """Switch the project timeline default to the reel size, read back.

    Returns the previous default so the caller can restore it. The
    verdict is the read-back, never the return, and an unreadable or
    unwritten default refuses: without a proven restore there is no
    mismatch route, and the caller falls back to append before
    anything is imported.
    """
    try:
        orig = project_timeline_resolution(project)
    except Exception as exc:  # noqa: BLE001 - unreadable default is no route
        raise OtioPlacementRefused(
            "the Resolve project timeline resolution could not be read "
            "before import - refusing, so the default placement builds "
            "this reel") from exc
    if orig is None:
        raise OtioPlacementRefused(
            "the Resolve project timeline resolution could not be read "
            "before import - refusing, so the default placement builds "
            "this reel")
    target = (int(width), int(height))
    if tuple(orig) == target:
        return orig
    try:
        width_ok = project.SetSetting("timelineResolutionWidth", str(target[0]))
        height_ok = project.SetSetting("timelineResolutionHeight", str(target[1]))
    except Exception as exc:  # noqa: BLE001 - an unproven switch is no route
        raise OtioPlacementRefused(
            f"Resolve would not switch the project timeline default to "
            f"{target[0]}x{target[1]} ({exc!r}) - refusing, so the "
            f"default placement builds this reel") from exc
    if not width_ok or not height_ok:
        raise OtioPlacementRefused(
            f"Resolve would not switch the project timeline default to "
            f"{target[0]}x{target[1]} - refusing, so the default "
            f"placement builds this reel")
    if project_timeline_resolution(project) != target:
        raise OtioPlacementRefused(
            f"the project timeline default did not read back as "
            f"{target[0]}x{target[1]} - refusing, so the default "
            f"placement builds this reel")
    return orig


def restore_project_default(project, name: str,
                            orig: tuple[int, int]) -> None:
    """Put the project timeline default back, read back.

    A default that does not read back raises naming the values to fix
    by hand: the build must never leave the project's settings
    switched. Post-import this is a genuine refusal, not a fallback -
    the imported timeline is already custom-sized and unaffected, but
    the project is not as found.
    """
    expected = (int(orig[0]), int(orig[1]))
    try:
        width_ok = project.SetSetting("timelineResolutionWidth",
                                      str(expected[0]))
        height_ok = project.SetSetting("timelineResolutionHeight",
                                       str(expected[1]))
    except Exception as exc:  # noqa: BLE001 - a stuck default is said plainly
        raise OtioPlacementRefused(
            f"{name}: the project timeline default could not be restored "
            f"to {expected[0]}x{expected[1]} ({exc!r}) - restore it by "
            f"hand") from exc
    if not width_ok or not height_ok:
        raise OtioPlacementRefused(
            f"{name}: Resolve would not restore the project timeline "
            f"default to {expected[0]}x{expected[1]} - restore it by hand")
    if project_timeline_resolution(project) != expected:
        raise OtioPlacementRefused(
            f"{name}: the project timeline default did not read back as "
            f"{expected[0]}x{expected[1]} after the import - restore it "
            f"by hand")


def prove_mismatch_route(project, name: str, width: int, height: int) -> None:
    """Prove the mismatch route before the build records anything.

    Switches the project default to the reel size, reads the timeline
    to park the cursor on, and restores the default - every write read
    back. The real route in `import_recorded` repeats the same writes;
    this probe only decides `auto`'s fallback before any placement is
    recorded: a route that cannot be proven here builds with append,
    and an explicit `otio` refuses here rather than mid-import. Leaves
    the default as found (verified) and the cursor untouched (only
    read); a restore that does not read back raises naming the values
    to fix by hand.
    """
    target = (int(width), int(height))
    try:
        orig = project_timeline_resolution(project)
    except Exception as exc:  # noqa: BLE001 - unreadable default is no route
        raise OtioPlacementRefused(
            f"{name}: the Resolve project timeline resolution could not "
            f"be read before import - build this reel with the default "
            f"placement") from exc
    if orig is None:
        raise OtioPlacementRefused(
            f"{name}: the Resolve project timeline resolution could not "
            f"be read before import - build this reel with the default "
            f"placement")
    if tuple(orig) == target:
        return
    switched = switch_project_default(project, target[0], target[1])
    try:
        try:
            previous = project.GetCurrentTimeline()
        except Exception as exc:  # noqa: BLE001 - an unreadable cursor
            raise OtioPlacementRefused(
                f"{name}: the current timeline could not be read before "
                f"import - build this reel with the default "
                f"placement") from exc
        if previous is None:
            raise OtioPlacementRefused(
                f"{name}: no timeline is current to park the cursor on "
                f"while the import is sized - build this reel with the "
                f"default placement")
    finally:
        restore_project_default(project, name, switched)


def verify_imported_resolution(name: str, timeline, width: int,
                               height: int) -> None:
    """Require the imported frame size without setting timeline values."""
    expected = (int(width), int(height))
    actual = project_timeline_resolution_for_timeline(timeline)
    if actual != expected:
        raise OtioPlacementRefused(
            f"{name}: OTIO imported resolution {actual!r}, "
            f"expected {expected!r}. The resolution cannot be repaired "
            "after the timeline becomes current; rebuild with append "
            "placement or use a project whose default matches the reel.")


def size_imported_timeline_before_current(project, name: str, timeline,
                                          width: int, height: int) -> None:
    """Size an imported timeline to the reel, or prove it needs no sizing.

    Measured live 2026-10-08: Resolve makes the imported timeline current
    during the import, so the noncurrent sizing this was written for does
    not happen - and changing these settings on a current timeline is the
    measured settings-sync deadlock. When the import already reads back
    the reel size (the project default matches), no write is made and the
    read-back is the verdict. A wrong-sized CURRENT timeline refuses:
    falling back to append is `select_placement`'s call, made BEFORE the
    import, never here after it has landed and stolen the cursor.
    """
    expected = (int(width), int(height))
    if project_timeline_resolution_for_timeline(timeline) == expected:
        return
    current = project.GetCurrentTimeline()
    if current is not None:
        try:
            same_timeline = (
                current is timeline
                or current.GetUniqueId() == timeline.GetUniqueId())
        except Exception as exc:  # noqa: BLE001 - cannot prove safety
            raise OtioPlacementRefused(
                f"{name}: Resolve did not expose timeline identity before "
                "resolution setup; refusing an unproven settings write") from exc
        if same_timeline:
            raise OtioPlacementRefused(
                f"{name}: Resolve made the OTIO timeline current during "
                "import; refusing to change its resolution in place. "
                "Rebuild with append placement or use a project whose "
                "default matches the reel.")
    from library.tools.resolve_deadline import apply_timeline_resolution
    apply_timeline_resolution(timeline, width, height)
    verify_imported_resolution(name, timeline, width, height)


def otio_incompatibilities(
        prepared, overlay_placements=None, *, target_resolution=None,
        import_resolution=None) -> tuple[str, ...]:
    """Describe the known shapes this OTIO importer cannot represent.

    This reads the FREE placement plan plus the project's resolution
    captured before placement. Keep it beside the recorder's refusal
    seams so `auto` can choose append before the build touches its target
    timeline. Unsupported shapes and an unreadable default are reasons
    to append. A readable mismatch is handled by `select_placement` for
    `auto`; only explicit `otio` uses the mismatch route, which switches
    the project default transiently around the import
    (`prove_mismatch_route`, proven before anything is recorded).
    """
    reasons = []
    if overlay_placements:
        reasons.append(
            "transition elements may carry audio and have no measured OTIO "
            "spelling")

    from library.tools import do_not_draw as _dnd
    for segment in prepared.subtitle_segments or ():
        suppressed, _reason = _dnd.should_suppress(
            prepared.suppressions, prepared.name, segment or {})
        if suppressed:
            continue
        frames = segment.get("frames") or {}
        if segment.get("container") == "frames" and frames.get("dir"):
            label = (segment.get("segment_id")
                     or segment.get("overlay_path") or frames["dir"])
            reasons.append(
                f"image-sequence caption {label!r} has no measured OTIO "
                "spelling")

    for angle, channel in sorted(prepared.resolved_channels.items()):
        try:
            channel_number = int(channel)
        except (TypeError, ValueError):
            channel_number = None
        if channel_number != 1:
            reasons.append(
                f"program channel {channel!r} for angle {angle!r} has no "
                "measured OTIO spelling (only channel 1 is measured)")

    if target_resolution is not None and (
            len(tuple(target_resolution)) != 2
            or any(int(value) <= 0 for value in target_resolution)):
        reasons.append("the reel timeline resolution is invalid")
    elif target_resolution is not None:
        if import_resolution is None:
            reasons.append(
                "the Resolve project timeline resolution could not be "
                "read before import")
    return tuple(reasons)


def select_placement(requested: str, prepared,
                     overlay_placements=None, *, target_resolution=None,
                     import_resolution=None
                     ) -> tuple[str, tuple[str, ...]]:
    """Resolve `auto` from the free plan and captured project settings.

    A resolution mismatch selects append; the project-default switch is
    reserved for an explicit `otio` request.
    """
    if requested not in ("append", "otio", "auto"):
        raise ValueError(f"unknown reel placement mode {requested!r}")
    if requested == "append":
        return requested, ()
    reasons = otio_incompatibilities(
        prepared, overlay_placements,
        target_resolution=target_resolution,
        import_resolution=import_resolution)
    if requested == "otio":
        return requested, reasons
    if (target_resolution is not None
            and import_resolution is not None
            and tuple(map(int, target_resolution))
            != tuple(map(int, import_resolution))):
        reasons += (
            "the Resolve project timeline resolution differs from the "
            "reel; use --placement otio to opt in to the "
            "project-default switch",
        )
    return ("append" if reasons else "otio"), reasons


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
    markers: dict = field(default_factory=dict)


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
        from library.tools.reel_build import PoolIndex
        self.pool_index = PoolIndex(pool, authoritative=True)
        self.timeline = RecordingTimeline()
        self.specs: list = []
        self.bins: dict = {}                  # path -> overlay_import_bin
        self._footage: dict = {}
        self._source_markers: dict = {}
        self.profile: dict = {}

    # The media-pool surface the placers call.
    def AppendToTimeline(self, infos):
        placed = []
        for info in infos:
            if "mediaType" not in info:
                raise OtioPlacementRefused(
                    "an append without mediaType places video AND audio, "
                    "which has no measured OTIO spelling")
            media_item = info["mediaPoolItem"]
            path = media_item.path
            real_item = media_item._real
            if real_item is not None and path not in self._source_markers:
                self._source_markers[path] = real_item.GetMarkers() or {}
            spec = _Spec(path=path,
                         track=int(info["trackIndex"]),
                         media_type=int(info["mediaType"]),
                         source_in=int(info["startFrame"]),
                         source_out=int(info["endFrame"]),
                         record=int(info["recordFrame"]),
                         markers=dict(self._source_markers.get(path, {})))
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
            self._footage[path] = pool_item_for(self.pool, path, pool_index=self.pool_index)
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


def _root_file_clips(pool) -> list:
    """The file-backed clips filed at the pool ROOT, by path.

    Timeline pool entries carry no file path, so the imported timeline's
    own entry never counts here - only a source or render copy Resolve
    filed at the root does.
    """
    root = pool.GetRootFolder()
    return sorted((item.GetClipProperty("File Path") or "")
                  for item in root.GetClipList() or ())


def relink_imported_items(pool, timeline, recorder: PlacementRecorder,
                          track_plan, name: str) -> list:
    """Repair import-created items that report no media pool item.

    Measured live on Resolve 21.1, 2026-10-06: an `importSourceClips:
    False` import plays the pooled file but every placed item reads
    `GetMediaPoolItem()` None, even after save and reload - so the
    freeze-tail inheritance, the crop aiming and the comp mapping, which
    all read the item's pool item, refuse or go blind on an imported
    reel. Resolve has no `SetMediaPoolItem` (reel_touchup), so
    re-linking IS the append path: the unlinked item is deleted and the
    already-pooled clip is placed at the recorded span - matched by file
    path and imported span, and nothing is imported, so the pool gains
    no ROOT copy. A file nothing pools, an undeletable item or a
    re-placement that does not read back refuses rather than playing
    unlinked.
    """
    from library.tools.reel_build import (
        _speech_row_uids, _timeline_span, pool_item_for,
        sweep_placed_audio)
    repaired = []
    for spec in recorder.specs:
        kind = "video" if spec.media_type == 1 else "audio"
        want = (spec.record, spec.record + spec.source_out - spec.source_in)
        for item in timeline.GetItemListInTrack(kind, spec.track) or ():
            if _timeline_span(item) != want:
                continue
            linked = item.GetMediaPoolItem()
            if linked is not None and (
                    linked.GetClipProperty("File Path") or "") == spec.path:
                continue
            pooled = pool_item_for(pool, spec.path)
            if pooled is None:
                raise OtioPlacementRefused(
                    f"{name}: {os.path.basename(spec.path)} at "
                    f"{kind[0].upper()}{spec.track}@{spec.record} is bound "
                    f"to nothing and the file is not in the media pool - "
                    f"refusing rather than importing it, which would file "
                    f"a pool-ROOT copy")
            if not timeline.DeleteClips([item], False):
                raise OtioPlacementRefused(
                    f"{name}: Resolve would not delete the unlinked "
                    f"{os.path.basename(spec.path)} at "
                    f"{kind[0].upper()}{spec.track}@{spec.record} - "
                    f"refusing rather than placing a second item over it")
            before = _speech_row_uids(timeline, track_plan)
            placed = pool.AppendToTimeline([{
                "mediaPoolItem": pooled,
                "startFrame": spec.source_in,
                "endFrame": spec.source_out,
                "mediaType": spec.media_type,
                "trackIndex": spec.track,
                "recordFrame": spec.record,
            }])
            if not placed:
                raise OtioPlacementRefused(
                    f"{name}: Resolve would not place the pooled "
                    f"{os.path.basename(spec.path)} at "
                    f"{kind[0].upper()}{spec.track}@{spec.record} - "
                    f"AppendToTimeline returned nothing")
            if spec.media_type == 2 and spec.channel is not None:
                kept, _deleted, _unverified = sweep_placed_audio(
                    timeline, track_plan, before, spec.track,
                    spec.channel,
                    f"{name} relink "
                    f"{os.path.basename(spec.path)}@{spec.record}")
                if not kept:
                    raise OtioPlacementRefused(
                        f"{name}: the relinked "
                        f"{os.path.basename(spec.path)} at "
                        f"A{spec.track}@{spec.record} carries no CH"
                        f"{spec.channel} speech - refusing rather than "
                        f"leaving the row without its program stream")
            again = [candidate for candidate in
                     timeline.GetItemListInTrack(kind, spec.track) or ()
                     if _timeline_span(candidate) == want]
            bound = [candidate.GetMediaPoolItem() for candidate in again]
            if not again or any(
                    mpi is None or (mpi.GetClipProperty("File Path")
                                    or "") != spec.path for mpi in bound):
                raise OtioPlacementRefused(
                    f"{name}: the relinked "
                    f"{os.path.basename(spec.path)} at "
                    f"{kind[0].upper()}{spec.track}@{spec.record} does not "
                    f"read back bound to the pooled clip - refusing")
            repaired.append(f"{kind[0].upper()}{spec.track}@{spec.record} "
                            f"{os.path.basename(spec.path)}")
    return repaired


def restore_pool_markers(timeline, recorder: PlacementRecorder,
                         name: str) -> int:
    """Carry the source markers AppendToTimeline inherits onto OTIO items."""
    from library.tools.reel_build import _timeline_span

    restored = 0
    for spec in recorder.specs:
        if not spec.markers:
            continue
        kind = "video" if spec.media_type == 1 else "audio"
        expected_span = (spec.record,
                         spec.record + spec.source_out - spec.source_in)
        items = [item for item in
                 timeline.GetItemListInTrack(kind, spec.track) or ()
                 if _timeline_span(item) == expected_span]
        if len(items) != 1:
            raise OtioPlacementRefused(
                f"{name}: cannot identify the {kind} item at "
                f"{kind[0].upper()}{spec.track}@{spec.record} to restore "
                "source markers")
        item = items[0]
        for frame, marker in spec.markers.items():
            values = {
                "color": marker["color"],
                "name": marker["name"],
                "note": marker["note"],
                "duration": int(marker["duration"]),
                "customData": marker["customData"],
            }
            if not item.AddMarker(
                    int(frame), values["color"], values["name"],
                    values["note"], values["duration"],
                    values["customData"]):
                raise OtioPlacementRefused(
                    f"{name}: Resolve refused source marker at frame "
                    f"{frame} on {os.path.basename(spec.path)}")
            actual = (item.GetMarkers() or {}).get(int(frame))
            if actual is None or any(actual[key] != value
                                     for key, value in values.items()):
                raise OtioPlacementRefused(
                    f"{name}: source marker at frame {frame} on "
                    f"{os.path.basename(spec.path)} did not read back")
            restored += 1
    return restored


def _discard_mismatch_import(pool, project, timeline, previous,
                               orig_default, name: str) -> str:
    """Leave no debris behind a failed mismatch import; say what happened.

    Best-effort: the imported timeline is deleted, the project default
    is restored, and the cursor is parked back where it was - each step
    attempted even when the last failed. Returns the cleanup account
    for the caller's refusal. Post-import a failure is a genuine
    refusal, never a silent fallback: the route was proven before
    recording, so failing it means Resolve itself is not answering,
    and the default path's own sizings would wedge beside it.
    """
    status = []
    try:
        if pool.DeleteTimelines([timeline]):
            status.append("imported timeline deleted")
        else:
            status.append("Resolve would not delete the imported timeline - "
                          "delete it by hand")
    except Exception as exc:  # noqa: BLE001 - cleanup reports, never masks
        status.append(f"deleting the imported timeline raised {exc!r}")
    try:
        restore_project_default(project, name, orig_default)
        status.append(f"project default restored to "
                      f"{orig_default[0]}x{orig_default[1]}")
    except OtioPlacementRefused as exc:
        status.append(str(exc))
    try:
        from library.tools.resolve_lock import assert_current_timeline
        assert_current_timeline(project, previous)
        status.append("cursor parked back on the previous timeline")
    except Exception as exc:  # noqa: BLE001 - same reporting rule
        status.append(f"parking the cursor back raised {exc!r} - reopen "
                      f"the previous timeline by hand")
    return "; ".join(status)


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
    root_before = _root_file_clips(pool)
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
            # Source clips already in the pool are REUSED, not
            # re-imported: `importSourceClips: True` files a new item
            # at the pool ROOT for every referenced file whenever
            # Resolve's cache does not recognise the pooled item -
            # even one the project already holds in its Source footage
            # bin - so the reel's timeline binds a different media id
            # than the captain's master plays. Measured 2026-10-06,
            # after a relaunch: the 6-reel rebuild added LC4932.MXF and
            # LCATL0013.MXF to the pool ROOT beside their Source
            # footage items, and every later build bound the ROOT copy.
            # So the import asks for no source clips at all, and the
            # renders the import would have filed at the ROOT are
            # imported separately into their own bins instead.
            from library.tools.reel_build import pool_item_for
            unpooled = [spec.path for spec in recorder.specs
                        if pool_item_for(pool, spec.path) is None]
            if unpooled:
                from library.tools.reel_build import import_pool_item
                for render in unpooled:
                    if import_pool_item(
                            pool, render,
                            recorder.bins.get(render)) is None:
                        raise OtioPlacementRefused(
                            f"{name}: Resolve would not import the render "
                            f"{render!r}")
            timeline = None
            # The mismatch route: the import inherits the project's
            # resolution, so a project default that does not match the
            # reel is switched to the reel size (read back) for exactly
            # the import. `prove_mismatch_route` already proved this
            # switch before anything was recorded; a switch that fails
            # here refuses loudly rather than importing wrong-sized.
            import_started = started
            switch_started = time.perf_counter()
            orig_default = switch_project_default(project, width, height)
            mismatch = tuple(orig_default) != (int(width), int(height))
            if mismatch:
                record_duration(recorder.profile, "mismatch_default_switch_s",
                                switch_started)
                try:
                    previous = project.GetCurrentTimeline()
                except Exception as exc:  # noqa: BLE001 - unreadable cursor
                    restore_project_default(project, name, orig_default)
                    raise OtioPlacementRefused(
                        f"{name}: the current timeline could not be read "
                        f"before import - refusing rather than sizing a "
                        f"timeline with nowhere to park the cursor") from exc
                if previous is None:
                    restore_project_default(project, name, orig_default)
                    raise OtioPlacementRefused(
                        f"{name}: no timeline is current to park the cursor "
                        f"on while the import is sized - refusing rather "
                        f"than changing resolution on a current timeline")
            try:
                timeline = pool.ImportTimelineFromFile(
                    path, {"timelineName": name,
                           "importSourceClips": False})
            except Exception as exc:  # noqa: BLE001 - a failed import
                if mismatch:
                    restore_project_default(project, name, orig_default)
                raise OtioPlacementRefused(
                    f"{name}: ImportTimelineFromFile raised {exc!r} for "
                    f"the compiled timeline") from exc
        finally:
            if before is not None:
                pool.SetCurrentFolder(before)
        record_duration(recorder.profile, "timeline_import_s", import_started)
    if not timeline:
        if mismatch:
            restore_project_default(project, name, orig_default)
        raise OtioPlacementRefused(
            f"{name}: ImportTimelineFromFile returned None for the "
            f"compiled timeline (every referenced file was on disk)")
    if mismatch:
        # The import made the new timeline current (measured live
        # 2026-10-08), and changing resolution on a current timeline is
        # the measured settings-sync deadlock - so the cursor parks on
        # the previous timeline, the imported (now non-current)
        # timeline is sized to CUSTOM under the per-call deadline, the
        # cursor returns to the imported timeline (the reel's timeline,
        # where the default path leaves it too), and the project default
        # is restored read-back. Net mutation: none.
        recorder.profile["mismatch_route"] = True
        try:
            started = time.perf_counter()
            assert_current_timeline(project, previous)
            record_duration(recorder.profile, "mismatch_cursor_park_s",
                            started)
            started = time.perf_counter()
            from library.tools.resolve_deadline import (
                apply_timeline_resolution)
            apply_timeline_resolution(timeline, width, height)
            verify_imported_resolution(name, timeline, width, height)
            record_duration(recorder.profile, "mismatch_custom_size_s",
                            started)
            started = time.perf_counter()
            assert_current_timeline(project, timeline)
            record_duration(recorder.profile, "mismatch_cursor_return_s",
                            started)
            started = time.perf_counter()
            restore_project_default(project, name, orig_default)
            record_duration(recorder.profile, "mismatch_default_restore_s",
                            started)
        except Exception as exc:
            status = _discard_mismatch_import(
                pool, project, timeline, previous, orig_default, name)
            raise OtioPlacementRefused(
                f"{name}: the mismatch OTIO import failed ({exc!r}) - "
                f"{status}") from exc
        recorder.profile["project_default_restored"] = True
        if (project_timeline_resolution(project) != tuple(orig_default)
                or project_timeline_resolution_for_timeline(timeline)
                != (int(width), int(height))):
            raise OtioPlacementRefused(
                f"{name}: the mismatch import did not settle as found - "
                f"the project default or the imported size does not read "
                f"back; check both by hand")
    else:
        # The import inherits the project's resolution, which already
        # matches. Size the not-yet-current timeline before taking the
        # cursor; changing these settings after it becomes current has
        # deadlocked Resolve. Where the import already reads back the
        # reel size (measured live 2026-10-08: the import makes the new
        # timeline current during the import) the check below makes no
        # write at all.
        started = time.perf_counter()
        size_imported_timeline_before_current(
            project, name, timeline, width, height)
        record_duration(recorder.profile, "imported_resolution_verify_s",
                        started)
        started = time.perf_counter()
        assert_current_timeline(project, timeline)
        record_duration(recorder.profile, "timeline_cursor_check_s", started)

    started = time.perf_counter()
    marker_count = restore_pool_markers(timeline, recorder, name)
    record_duration(recorder.profile, "source_markers_restore_s", started)
    recorder.profile["source_markers_restored"] = marker_count

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
    # attributes. The import files every new pool item into the ROOT
    # bin (measured: "It filed the 35 overlay renders, plus the
    # imported timeline's own pool entry, into the ROOT bin"), never
    # the CURRENT folder - so the restore searches every bin, not just
    # the timeline's.
    started = time.perf_counter()
    by_path: dict = {}
    folders = [pool.GetRootFolder()]
    while folders:
        folder = folders.pop()
        for item in folder.GetClipList() or ():
            by_path.setdefault(item.GetClipProperty("File Path") or "",
                               []).append(item)
        folders.extend(folder.GetSubFolderList() or ())
    by_bin: dict = {}
    for render, bin_path in recorder.bins.items():
        for item in by_path.get(render, ()):
            apply_clip_attributes(item, render)
            by_bin.setdefault(bin_path, []).append(item)
    for bin_path, items in by_bin.items():
        _move_into(pool, items, bin_path)
    record_duration(recorder.profile, "pool_organization_s", started)
    started = time.perf_counter()
    repaired = relink_imported_items(
        pool, timeline, recorder, track_plan, name)
    record_duration(recorder.profile, "linkage_repair_s", started)
    recorder.profile["relinked_items"] = len(repaired)
    for label in repaired:
        print(f"  {name}: relinked {label}", file=sys.stderr)
    added = [path for path in _root_file_clips(pool)
             if path and path not in root_before]
    if added:
        raise OtioPlacementRefused(
            f"{name}: the import filed {len(added)} file-backed cop"
            f"{'y' if len(added) == 1 else 'ies'} at the pool ROOT - "
            f"refusing: {added[:4]}")
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


def run_deferred(timeline, recorder: PlacementRecorder, *,
                 project_folder: str = "", project: str = "",
                 timeline_name: str = "") -> int:
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
    from library.tools.transform_write_log import set_property

    if project_folder:
        # The replayed writes are logged against the project, and the
        # log resolves its home off this folder - which a recorded
        # build never created (recording writes nothing). Without it
        # the folder lookup refuses every logged write and the replay
        # holds zeros: a wrong-but-stored position that reads back
        # cleanly. The append path always has it (its earlier writes
        # created it); make the replay's home before it writes.
        os.makedirs(project_folder, exist_ok=True)
    started = time.perf_counter()
    for spec in recorder.specs:
        if not spec.properties:
            continue
        kind = "video" if spec.media_type == 1 else "audio"
        for item in timeline.GetItemListInTrack(kind, spec.track) or []:
            if (_timeline_span(item) or (None,))[0] == spec.record:
                for key, value in spec.properties.items():
                    if not set_property(
                            item, key, value,
                            item_identity={
                                "source_file": spec.path,
                                "track": f"{kind[0].upper()}{spec.track}",
                                "record_frame": spec.record,
                                "source_in": spec.source_in,
                                "source_out": spec.source_out,
                            },
                            project_folder=project_folder or None,
                            project=project or None,
                            timeline_name=timeline_name or None):
                        raise OtioPlacementRefused(
                            f"Resolve refused {key}={value} on "
                            f"{os.path.basename(spec.path)}")
    for label, call in recorder.timeline.deferred:
        note = call(timeline)
        if note:
            print(f"  {label}: {note}", file=sys.stderr)
    record_duration(recorder.profile, "deferred_transform_s", started)
    return len(recorder.timeline.deferred)
