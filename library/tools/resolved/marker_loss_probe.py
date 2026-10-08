"""Qualification-only live probe of the post-promotion marker gate."""

from __future__ import annotations

from library.tools.resolved.jobs import QUALIFICATION_PROJECT, JobRefused
from library.tools.resolved.reel_promotion_fixture import FINAL, MARKER_NAME

KIND = "reel.probe_qualification_marker_loss"


def prepare(params: dict) -> dict:
    if params != {"project": QUALIFICATION_PROJECT}:
        raise JobRefused(
            f"{KIND} accepts only {{'project': "
            f"{QUALIFICATION_PROJECT!r}}}")
    return {
        "mode": "exclusive", "priority": "mutation", "executed": True,
        "project": QUALIFICATION_PROJECT, "timeline": FINAL,
        "locality": "timeline", "coalesce_key": None,
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


def _same_marker(entry: dict, expected: dict) -> bool:
    anchor = entry.get("anchor") or {}
    expected_anchor = expected.get("anchor") or {}
    return all((entry.get(key) or "") == (expected.get(key) or "")
               for key in ("name", "note", "color")) and all(
                   anchor.get(key) == expected_anchor.get(key)
                   for key in ("source_file", "source_frame"))


def _probe(timeline, manager) -> dict:
    from library.tools import marker_carry, marker_gate
    from library.tools.resource_leases import (
        LeaseDeclaration, ResourceRef, resource_lease)

    name = timeline.GetName()
    timeline_markers = marker_carry.read_markers(timeline, name)
    clip_markers = marker_carry.read_clip_markers(timeline, name)
    candidates = [marker for marker in clip_markers
                  if marker.get("name") == MARKER_NAME
                  and marker.get("anchor")]
    if len(candidates) != 1:
        raise JobRefused(
            f"{KIND} expected one live {MARKER_NAME!r} clip anchor on "
            f"{name!r}; found {len(candidates)}")
    marker = candidates[0]
    matches = marker_carry._clip_candidates(marker["anchor"], timeline)
    exact = [(item, anchor) for item, anchor in matches
             if anchor.get("source_file")
             == marker["anchor"].get("source_file")
             and anchor.get("source_frame") == marker["source_frame"]]
    if len(exact) != 1:
        raise JobRefused(
            f"{KIND} could not resolve the marker to one clip item on "
            f"{name!r}; found {len(exact)}")
    item = exact[0][0]
    source_frame = int(marker["source_frame"])
    capture = marker_gate.assemble_capture(
        name, timeline_markers, clip_markers)
    deletion_succeeded = False
    gate_message = ""
    with resource_lease(
            LeaseDeclaration(write=frozenset({ResourceRef("timeline", name)})),
            purpose=f"qualification marker-loss probe on {name}"):
        try:
            deleted = item.DeleteMarkerAtFrame(source_frame)
            live_markers = item.GetMarkers() or {}
            marker_absent = not (
                source_frame in live_markers
                or str(source_frame) in live_markers)
            deletion_succeeded = marker_absent
            if not deleted or not marker_absent:
                raise JobRefused(
                    f"{KIND} could not remove and verify the synthetic "
                    f"marker at source frame {source_frame}; returned "
                    f"{deleted!r}")
            try:
                marker_gate.verify_promotion(
                    name, capture, timeline,
                    {"clip_carried": [marker]})
            except marker_gate.MarkerGateLost as lost:
                gate_message = str(lost)
            if not gate_message or marker["name"] not in gate_message \
                    or marker["note"] not in gate_message:
                raise JobRefused(
                    f"{KIND} marker gate did not name the dropped marker "
                    f"and its words on {name!r}")
        finally:
            if deletion_succeeded:
                restored = item.AddMarker(
                    source_frame, marker.get("color") or "Blue",
                    marker["name"], marker["note"],
                    int(marker.get("duration") or 1),
                    marker.get("custom_data") or "")
                actual = item.GetMarkers() or {}
                entry = actual.get(source_frame) or actual.get(
                    str(source_frame))
                if (not restored or not isinstance(entry, dict)
                        or entry.get("name") != marker["name"]
                        or entry.get("note") != marker["note"]):
                    raise JobRefused(
                        f"{KIND} failed to restore {marker['name']!r} "
                        f"at source frame {source_frame}; recover the "
                        "synthetic marker in Ren Qualification")
                if not manager.SaveProject():
                    raise JobRefused(
                        f"{KIND} restored the marker but could not save "
                        f"{QUALIFICATION_PROJECT!r}")
    restored_markers = marker_carry.read_clip_markers(timeline, name)
    if not any(_same_marker(entry, marker) for entry in restored_markers):
        raise JobRefused(
            f"{KIND} restored the marker API result but it did not read "
            f"back on {name!r}")
    return {"timeline": name, "marker": marker,
            "gate_caught": True, "gate_message": gate_message,
            "marker_restored": True}


def run(job: dict, resolve) -> dict:
    from library.tools import qualification_project
    from library.tools.resolve_deadline import DeadlineProxy, deadline_proxy

    if not isinstance(resolve, DeadlineProxy):
        resolve = deadline_proxy(resolve, label=f"{KIND} {job.get('id', '')}")
    with qualification_project.borrowed(resolve) as borrowed_from:
        manager = resolve.GetProjectManager()
        qualification_project.switch_project(manager, QUALIFICATION_PROJECT)
        project = manager.GetCurrentProject()
        opened = (project.GetName() or "") if project else ""
        if opened != QUALIFICATION_PROJECT:
            raise JobRefused(
                f"{KIND} opened {opened or 'no project'!r}, not "
                f"{QUALIFICATION_PROJECT!r}")
        result = _probe(_exact_timeline(project, FINAL), manager)
    result["borrowed_from"] = {
        "project": borrowed_from["project"],
        "timeline": borrowed_from["timeline"],
        "timeline_id": borrowed_from["timeline_id"],
        "page": borrowed_from["page"],
    }
    result["restored"] = dict(result["borrowed_from"])
    return result
