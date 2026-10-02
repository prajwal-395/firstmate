"""Writing the reel-coverage markers onto the master, and taking them off.

The Resolve half of `library/tools/master_markers.py`, which holds every
rule and no API calls.

The master is READ-ONLY except for these markers (the captain's ruling
of 2026-09-07).  This module therefore calls exactly three things on the
master timeline - `GetItemListInTrack`, `AddMarker` and
`DeleteMarkerAtFrame` - and `tests/unit/resolve/test_master_markers.py` asserts that
nothing which would move, rename, recolour, retime or re-render it
appears in this file at all.

`tests/unit/resolve/test_master_markers.py`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from library.tools import journal_naming

from library.tools.master_markers import (
    MarkerRefused,
    assert_no_collision,
    coverage_regions,
    is_ours,
    locate_on_master,
    marker_for,
    windows_of,
)
from library.tools.resolve_organization import reel_state
from library.tools.resolve_lock import under_lease

JOURNAL_PREFIX = "master_markers"


def _timelines(project) -> dict:
    return {project.GetTimelineByIndex(i).GetName():
            project.GetTimelineByIndex(i)
            for i in range(1, project.GetTimelineCount() + 1)}


def plan_markers(project, project_folder: str, master_name: str) -> dict:
    """One marker per reel, measured off the footage. Writes nothing."""
    from library.tools.plan_provenance import archived_timeline_names, read_provenance

    timelines = _timelines(project)
    master = timelines.get(master_name)
    if master is None:
        raise MarkerRefused(
            f"the project has no timeline called {master_name!r}. Its "
            f"49 timelines are addressed by exact name (AGENTS.md 5).")

    master_windows = []
    for track in range(1, (master.GetTrackCount("video") or 0) + 1):
        master_windows += windows_of(
            master.GetItemListInTrack("video", track) or [])
    if not master_windows:
        raise MarkerRefused(
            f"{master_name!r} places no footage this can read, so no reel "
            f"can be located on it. Nothing was marked.")

    review = os.path.join(project_folder, "pipeline_output", "review")
    provenance = read_provenance(review) or {}
    built = provenance.get("built_reels") or []
    archived = archived_timeline_names(review)

    per_reel, unlocatable = [], []
    for name, timeline in sorted(timelines.items()):
        if name == master_name:
            continue
        reel_windows = []
        for track in range(1, (timeline.GetTrackCount("video") or 0) + 1):
            reel_windows += windows_of(
                timeline.GetItemListInTrack("video", track) or [])
        spans = locate_on_master(reel_windows, master_windows)
        state, _why = reel_state(name, built, archived)
        if not spans:
            unlocatable.append({
                "reel": name,
                "why": "places no footage the master also places, so where "
                       "it was taken from cannot be measured"})
            continue
        per_reel.append((name, state, spans))

    master_end = int(master.GetEndFrame())
    regions = coverage_regions(per_reel)
    planned = [marker_for(region, master_end) for region in regions]
    covered = sum(r.end - r.start for r in regions)
    return {"master": master, "markers": planned, "regions": regions,
            "reels": per_reel, "unlocatable": unlocatable,
            "master_frames": master_end, "covered_frames": covered,
            "existing": master.GetMarkers() or {}}


@under_lease("write the reel-coverage markers onto the master")
def apply_markers(plan: dict, journal_path: str) -> dict:
    """Write the markers. Additive: refuses to land on the captain's own.

    Ours are cleared first so a second run REPLACES this module's own
    markers rather than colliding with them - `AddMarker` returns False
    on an occupied frame, and a run that silently wrote nothing would
    read as a run that was already up to date.
    """
    master = plan["master"]
    assert_no_collision(plan["markers"], plan["existing"])

    removed = clear_markers(master)
    journal = {
        "marked_at": datetime.now(timezone.utc).isoformat(),
        "timeline": master.GetName(),
        "removed_first": removed,
        "written": [],
    }
    try:
        for marker in plan["markers"]:
            if not master.AddMarker(marker.frame, marker.colour, marker.name,
                                    marker.note, marker.duration,
                                    marker.custom_data):
                raise MarkerRefused(
                    f"AddMarker at frame {marker.frame} for "
                    f"{marker.name!r} returned False. "
                    f"{len(journal['written'])} marker(s) had been written; "
                    f"the journal names them and `--clear` removes them.")
            journal["written"].append(
                {"frame": marker.frame, "duration": marker.duration,
                 "name": marker.name, "colour": marker.colour})
    finally:
        Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
        Path(journal_path).write_text(
            json.dumps(journal, indent=2), encoding="utf-8")
    journal["journal_path"] = journal_path
    return journal


@under_lease("clear this module's markers off the master")
def clear_markers(master) -> list[int]:
    """Remove the markers THIS module wrote, and only those.

    Selected by `customData`, never by colour and never by frame: the
    captain's own markers are green and are at frames of their choosing,
    and `DeleteMarkersByColor("Green")` would take them too.
    """
    ours = [frame for frame, marker in (master.GetMarkers() or {}).items()
            if is_ours(marker.get("customData", ""))]
    for frame in sorted(ours):
        if not master.DeleteMarkerAtFrame(frame):
            raise MarkerRefused(
                f"DeleteMarkerAtFrame({frame}) returned False on "
                f"{master.GetName()!r}. The markers this wrote cannot all "
                f"be removed, so they are not additive.")
    return sorted(ours)


def journal_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS marking's journal goes. One file per run, never reused.

    Named through `library/tools/journal_naming.py`, so a second marking in
    the same second is suffixed rather than landing on the first one's
    record.
    """
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", "review"),
        JOURNAL_PREFIX, when)
