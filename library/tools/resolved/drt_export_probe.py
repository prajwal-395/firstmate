"""Typed, Qualification-only export of a non-current timeline to `.drt`."""

from __future__ import annotations

import time

from library.tools.resolved.jobs import (
    EXCLUSIVE, QUALIFICATION_PROJECT, JobRefused,
)

KIND = "timeline.drt_export_probe"
_PARAMS = frozenset({"project", "timeline_uid"})


def _required_text(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobRefused(f"{KIND} needs a non-empty {key!r}")
    return value.strip()


def prepare(params: dict) -> dict:
    """Validate the closed request and return its exclusive job shape."""
    if not isinstance(params, dict):
        raise JobRefused(f"{KIND} params must be an object")
    unknown = set(params) - _PARAMS
    if unknown:
        raise JobRefused(
            f"{KIND} refuses unknown field(s) or caller-supplied paths: "
            f"{sorted(unknown)}")

    project = _required_text(params, "project")
    if project != QUALIFICATION_PROJECT:
        raise JobRefused(
            f"{KIND} may only target {QUALIFICATION_PROJECT!r}, "
            f"not {project!r}")
    _required_text(params, "timeline_uid")
    return {
        "mode": EXCLUSIVE,
        "priority": "read",
        "executed": True,
        "project": QUALIFICATION_PROJECT,
        "timeline": "",
        "locality": "project",
        # Resolve's shared archive helper is deterministic by timeline name;
        # each request still runs so it measures the export again.
        "coalesce_key": None,
        "qualification": True,
    }


def _timeline_by_uid(project, timeline_uid: str):
    matches = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and str(timeline.GetUniqueId() or "") == timeline_uid:
            matches.append(timeline)
    if len(matches) != 1:
        raise JobRefused(
            f"{KIND} expected exactly one timeline with UID "
            f"{timeline_uid!r}; found {len(matches)}")
    return matches[0]


def _cursor_state(resolve, project) -> dict:
    current = project.GetCurrentTimeline()
    return {
        "timeline_uid": (str(current.GetUniqueId() or "") or None)
        if current else None,
        "page": resolve.GetCurrentPage(),
    }


def run(job: dict, resolve, *, project=None) -> dict:
    """Export the named UID while the broker holds its exclusive lease.

    `Timeline.Export` accepts the existing handle and the helper stages
    the archive under the qualification media root. This path neither
    selects a timeline nor changes the Resolve page.
    """
    from library.tools import qualification_project
    from library.tools.versions import store

    params = job["params"]
    # Keep the execution boundary fail-closed if a stored job was altered
    # after `prepare` or reached this runner without passing it.
    prepare(params)
    if project is None:
        from library.tools.resolved.jobs import _open_project
        project = _open_project(resolve, QUALIFICATION_PROJECT)
    if project.GetName() != QUALIFICATION_PROJECT:
        raise JobRefused(
            f"{KIND} may only run in {QUALIFICATION_PROJECT!r}; "
            f"Resolve has {project.GetName() or 'no project'!r}")

    target_uid = _required_text(params, "timeline_uid")
    before = _cursor_state(resolve, project)
    if before["timeline_uid"] == target_uid:
        raise JobRefused(
            f"{KIND} requires a non-current timeline; UID "
            f"{target_uid!r} is current",
            result={"current_timeline_uid_before": before["timeline_uid"],
                    "current_page_before": before["page"],
                    "current_timeline_uid_after": before["timeline_uid"],
                    "current_page_after": before["page"],
                    "cursor_stayed_put": True})
    timeline = _timeline_by_uid(project, target_uid)
    name = timeline.GetName()
    started = time.perf_counter()
    archive = store.export_timeline_recovery_archive(
        resolve, timeline, name,
        str(qualification_project.drt_export_probe_project_folder()),
        label=f"{KIND} {job.get('id', '')}")
    elapsed = time.perf_counter() - started
    after = _cursor_state(resolve, project)
    return {
        "project": QUALIFICATION_PROJECT,
        "timeline_uid": target_uid,
        "timeline_name": name,
        "archived": archive["archived"],
        "archive_path": archive.get("path"),
        "reason": archive.get("reason"),
        "current_timeline_uid_before": before["timeline_uid"],
        "current_page_before": before["page"],
        "current_timeline_uid_after": after["timeline_uid"],
        "current_page_after": after["page"],
        "cursor_stayed_put": (before["timeline_uid"] ==
                               after["timeline_uid"] and
                               before["page"] == after["page"]),
        "elapsed_seconds": elapsed,
    }
