"""Build the bounded synthetic Resolve fixture for promotion qualification.

The fixture is deliberately fixed to Ren Qualification and synthetic media.
It creates one unique clip-anchor case and one ambiguous duplicate-placement
case. The broker owns Resolve, the borrow, and the exclusive lease for the
whole setup; no paths or caller code cross the job boundary.
"""

from __future__ import annotations

from pathlib import Path

from library.tools.resolved.jobs import QUALIFICATION_PROJECT, JobRefused

KIND = "reel.prepare_qualification_fixture"
MASTER = "Q Spine"
FINAL = "Q Promotion Proof"
STAGING = FINAL + " (rebuild staging)"
AMBIGUOUS_FINAL = "Q Promotion Proof Ambiguous"
AMBIGUOUS_STAGING = AMBIGUOUS_FINAL + " (rebuild staging)"
MARKER_NAME = "Ren live promotion anchor"
MARKER_NOTE = "Keep this clip anchored note on qual_b."
MARKER_SOURCE_FRAME = 24


def prepare(params: dict) -> dict:
    if params != {"project": QUALIFICATION_PROJECT}:
        raise JobRefused(
            f"{KIND} accepts only {{'project': {QUALIFICATION_PROJECT!r}}}")
    return {
        "mode": "exclusive", "priority": "mutation", "executed": True,
        "project": QUALIFICATION_PROJECT, "timeline": FINAL,
        "locality": "project", "coalesce_key": None,
        "qualification": True,
    }


def _exact_timeline(project, name: str):
    found = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == name:
            found.append(timeline)
    if len(found) != 1:
        raise JobRefused(
            f"{KIND} expected exactly one timeline {name!r}; found "
            f"{len(found)}")
    return found[0]


def _create_timeline(project, pool, media, name: str, copies: int):
    timeline = pool.CreateEmptyTimeline(name)
    if not timeline:
        raise JobRefused(f"{KIND} could not create {name!r}")
    placed = pool.AppendToTimeline([
        {"mediaPoolItem": media, "mediaType": 1} for _ in range(copies)])
    items = timeline.GetItemListInTrack("video", 1) or []
    if not placed or len(items) != copies:
        raise JobRefused(
            f"{KIND} placed {len(items)} item(s) on {name!r}; expected "
            f"{copies}")
    if not timeline.SetTrackName("video", 1, "A-Roll"):
        raise JobRefused(f"{KIND} could not name {name!r} V1")
    if timeline.GetTrackName("video", 1) != "A-Roll":
        raise JobRefused(f"{KIND} did not read back the A-Roll row name")
    return timeline, items


def _track_plan(items) -> dict:
    from library.tools.timeline_layout import A_ROLL

    first, last = items[0], items[-1]
    return {
        "video_tracks": [{"index": 1, "media_type": "video",
                          "role": A_ROLL, "name": "A-Roll",
                          "occupant": "qual_b"}],
        "audio_tracks": [],
        "material": {"picture_led_spans": [[
            int(first.GetStart()), int(last.GetEnd())]]},
    }


def _pool_clip(project):
    root = project.GetMediaPool().GetRootFolder()
    for item in root.GetClipList() or []:
        if Path(item.GetName()).stem == "qual_b":
            return item
    raise JobRefused(f"{KIND} could not find synthetic pool clip 'qual_b'")


def _add_clip_marker(item) -> dict:
    left = int(item.GetLeftOffset() or 0)
    source_frame = left + MARKER_SOURCE_FRAME
    if source_frame >= left + int(item.GetDuration()):
        raise JobRefused(f"{KIND} marker frame is outside synthetic clip")
    wrote = item.AddMarker(source_frame, "Blue", MARKER_NAME, MARKER_NOTE,
                           1, "ren-qualification-promotion-proof")
    markers = item.GetMarkers() or {}
    marker = markers.get(source_frame) or markers.get(str(source_frame))
    if not wrote or not isinstance(marker, dict) \
            or marker.get("name") != MARKER_NAME \
            or marker.get("note") != MARKER_NOTE:
        raise JobRefused(
            f"{KIND} clip marker did not read back at source frame "
            f"{source_frame}")
    return {"source_frame": source_frame, "name": MARKER_NAME,
            "note": MARKER_NOTE}


def _build(project, manager) -> dict:
    names = {FINAL, STAGING, AMBIGUOUS_FINAL, AMBIGUOUS_STAGING}
    existing = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() in names:
            existing.append(timeline.GetName())
    if existing:
        raise JobRefused(
            f"{KIND} refuses existing fixture timelines {sorted(existing)}; "
            "it never overwrites or cleans up a qualification timeline")
    _exact_timeline(project, MASTER)
    media = _pool_clip(project)
    pool = project.GetMediaPool()
    final, original_items = _create_timeline(project, pool, media, FINAL, 1)
    marker = _add_clip_marker(original_items[0])
    staging, staged_items = _create_timeline(
        project, pool, media, STAGING, 1)
    ambiguous_final, ambiguous_original_items = _create_timeline(
        project, pool, media, AMBIGUOUS_FINAL, 1)
    ambiguous_marker = _add_clip_marker(ambiguous_original_items[0])
    ambiguous_staging, ambiguous_items = _create_timeline(
        project, pool, media, AMBIGUOUS_STAGING, 2)
    if not ambiguous_staging or len(ambiguous_items) != 2:
        raise JobRefused(f"{KIND} could not create duplicate-source case")
    from library.tools.resolve_lock import assert_current_timeline
    assert_current_timeline(project, _exact_timeline(project, MASTER))
    current = project.GetCurrentTimeline()
    if not current or current.GetName() != MASTER:
        raise JobRefused(f"{KIND} did not read back cursor on {MASTER!r}")
    if not manager.SaveProject():
        raise JobRefused(f"{KIND} could not save {QUALIFICATION_PROJECT!r}")
    return {
        "project": QUALIFICATION_PROJECT,
        "master_timeline_name": MASTER,
        "final_timeline_name": FINAL,
        "staging_timeline_name": STAGING,
        "track_plan": _track_plan(staged_items),
        "marker": marker,
        "ambiguous_final_timeline_name": AMBIGUOUS_FINAL,
        "ambiguous_staging_timeline_name": AMBIGUOUS_STAGING,
        "ambiguous_track_plan": _track_plan(ambiguous_items),
        "ambiguous_marker": ambiguous_marker,
        "timelines_created": [final.GetName(), staging.GetName(),
                               ambiguous_final.GetName(),
                               ambiguous_staging.GetName()],
    }


def run(job: dict, resolve) -> dict:
    from library.tools import qualification_project
    from library.tools.resolve_deadline import DeadlineProxy, deadline_proxy

    if not isinstance(resolve, DeadlineProxy):
        resolve = deadline_proxy(
            resolve, label=f"{KIND} {job.get('id', '')}")
    with qualification_project.borrowed(resolve) as borrowed_from:
        manager = resolve.GetProjectManager()
        qualification_project.switch_project(manager, QUALIFICATION_PROJECT)
        project = manager.GetCurrentProject()
        opened = (project.GetName() or "") if project else ""
        if opened != QUALIFICATION_PROJECT:
            raise JobRefused(
                f"{KIND} opened {opened or 'no project'!r}, not "
                f"{QUALIFICATION_PROJECT!r}")
        result = _build(project, manager)
    result["borrowed_from"] = {
        "project": borrowed_from["project"],
        "timeline": borrowed_from["timeline"],
        "timeline_id": borrowed_from["timeline_id"],
        "page": borrowed_from["page"],
    }
    result["restored"] = dict(result["borrowed_from"])
    return result
