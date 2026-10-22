#!/usr/bin/env python3
"""Measure the placement pass's Resolve calls, for the reel build.

The critical-path profile (PR 3144, docs/benchmarks/2026-10-21-
critical-path.md) named the placement's per-item transform replay as
the reel build's next optimization: 154.8 s of the build's 155 s
deferred phase, the biggest single cost in the profile.

This script measures the thing the optimization is about: how many
Resolve calls the DEFERRED placement pass makes, and what they cost,
for a given number of overlays. It runs against the engine's own
Resolve double, so the number is the engine's call count and not a
model of it - and it is the same count the live build pays, because
the double answers the same methods the live session does.

    scripts/bench_deferred_transform.py --overlays 9 [--json out.json]

The wall time it prints is the DOUBLE's time: it says how long the
engine's own work took, not how long Resolve took. The live number is
the phase in the performance ledger (`otio_placement.deferred_transform_s`).
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools.overlay_placement import apply_placement_transform
from library.tools.reel_otio_placement import (
    PlacementRecorder,
    run_deferred,
)
from library.tools.transform_write_log import write_scope
from tests.resolve_double import (
    FakeTimeline,
    FakeTimelineItem,
    make_pool_clip,
    make_project,
)

SOURCE = "/m/source.mov"
PLACEMENT = {"scaling": 1.0, "pan": 120.0, "tilt": -80.0,
             "zoom_x": 1.0, "zoom_y": 1.0}


class _Calls:
    """Every Resolve call the pass makes, by method."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def count(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _instrument(project, timeline, calls: _Calls):
    """Count the Resolve calls the pass makes."""
    for name in ("GetSetting", "GetItemListInTrack", "GetName",
                 "GetUniqueId"):
        original = getattr(timeline, name, None)
        if original is None:
            continue

        def counted(*args, _name=name, _original=original, **kwargs):
            calls.count(_name)
            return _original(*args, **kwargs)

        setattr(timeline, name, counted)
    original_current = project.GetCurrentTimeline

    def current():
        calls.count("GetCurrentTimeline")
        return original_current()

    project.GetCurrentTimeline = current
    for item in timeline.GetItemListInTrack("video", 1) or []:
        for name in ("GetProperty", "SetProperty", "GetStart"):
            original = getattr(item, name, None)
            if original is None:
                continue

            def counted(*args, _name=name, _original=original, **kwargs):
                calls.count(_name)
                return _original(*args, **kwargs)

            setattr(item, name, counted)


def _measure(overlays: int, *, tmp: Path) -> dict:
    project = make_project("Deferred")
    pool = project.GetMediaPool()
    pool.GetRootFolder()._clips.append(
        make_pool_clip("source.mov", path=SOURCE))
    recorder = PlacementRecorder(pool)
    timeline = FakeTimeline(
        "deferred", project=project,
        settings={"timelineResolutionWidth": "1080",
                  "timelineResolutionHeight": "1920"})
    project.adopt(timeline)
    project.SetCurrentTimeline(timeline)
    items = []
    for index in range(overlays):
        recorder.AppendToTimeline([{
            "mediaPoolItem": recorder.pooled(SOURCE),
            "startFrame": 10, "endFrame": 30, "mediaType": 1,
            "trackIndex": 1, "recordFrame": index * 48}])
        item = FakeTimelineItem("source.mov", timeline, start=index * 48,
                                 duration=20)
        item.properties = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0,
                           "ZoomY": 1.0}
        timeline.add_item("video", 1, item)
        items.append(item)
        apply_placement_transform(
            recorder.timeline, 1, index * 48, dict(PLACEMENT),
            label=f"overlay {index}", resolve_project=project,
            project_folder=str(tmp), timeline_name="deferred")
    calls = _Calls()
    _instrument(project, timeline, calls)
    started = time.perf_counter()
    # The transform write log needs the write's identities; outside a
    # test run they come from the write scope, not from a Resolve cursor.
    kwargs = {}
    if "resolve_project" in inspect.signature(run_deferred).parameters:
        kwargs["resolve_project"] = project
    with write_scope(timeline_id="bench:deferred", project="Deferred",
                     project_folder=str(tmp)):
        run_deferred(timeline, recorder, project_folder=str(tmp),
                     project="Deferred", timeline_name="deferred",
                     **kwargs)
    elapsed = time.perf_counter() - started
    landed = sum(1 for item in items
                 if item.GetProperty("Pan") == PLACEMENT["pan"]
                 and item.GetProperty("Tilt") == PLACEMENT["tilt"])
    return {"overlays": overlays,
            "resolve_calls": calls.total,
            "resolve_calls_by_method": dict(sorted(calls.counts.items())),
            "resolve_calls_per_overlay": round(calls.total / overlays, 3),
            "engine_seconds": round(elapsed, 4),
            "overlays_landed": landed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlays", type=int, default=9,
                        help="how many overlays the reel carries")
    parser.add_argument("--json", type=Path, default=None,
                        help="write the measurement to this file")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        one = _measure(1, tmp=tmp)
        many = _measure(args.overlays, tmp=tmp)
    measurement = {"one_overlay": one, "n_overlays": many,
                   "overlays": args.overlays}
    print(json.dumps(measurement, indent=2, sort_keys=True))
    if args.json:
        args.json.write_text(json.dumps(measurement, indent=2,
                                        sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
