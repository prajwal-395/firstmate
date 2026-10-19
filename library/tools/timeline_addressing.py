"""Exact, live resolution of Resolve projects, timelines and timeline items.

Resolve's item handles are short-lived execution addresses. Editorial
selectors describe the material and survive timeline rebuilds; callers
must resolve them against a fresh timeline immediately before use.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class AddressingError(RuntimeError):
    """A live Resolve target could not be addressed safely."""


class TargetNotFound(AddressingError):
    """The requested project, timeline, track or item is absent."""


class AmbiguousTarget(AddressingError):
    """More than one live target matches the semantic selector."""


class StaleTarget(AddressingError):
    """A previously resolved handle no longer denotes the expected item."""


@dataclass(frozen=True)
class TimelineAddress:
    project_name: str
    timeline_id: str
    timeline_name: str


@dataclass(frozen=True)
class ItemAddress:
    timeline_id: str
    resolve_item_id: str
    editorial_identity: tuple
    track_type: str
    track_index: int
    item: Any


def _read(obj, method: str):
    try:
        value = getattr(obj, method)()
    except Exception as exc:
        raise AddressingError(f"could not read {method} from live Resolve object: {exc}") from exc
    if value is None or value == "":
        raise AddressingError(f"live Resolve object returned no {method}")
    return value


def project_name(project) -> str:
    return str(_read(project, "GetName"))


def verify_project(project, expected_name: str) -> str:
    actual = project_name(project)
    if actual != expected_name:
        raise StaleTarget(
            f"Resolve project is {actual!r}, expected exact project {expected_name!r}")
    return actual


def timeline_address(timeline, project_name: str = "") -> TimelineAddress:
    name = str(_read(timeline, "GetName"))
    try:
        uid = timeline.GetUniqueId()
    except Exception:  # noqa: BLE001 - optional Resolve API calls can be unavailable
        uid = None
    # Some Resolve versions do not expose a timeline UID. Exact name is
    # still an address, but callers must reject duplicate names.
    return TimelineAddress(project_name, str(uid or name), name)


def live_timelines(project) -> list[Any]:
    try:
        count = int(project.GetTimelineCount() or 0)
        timelines = [project.GetTimelineByIndex(index)
                     for index in range(1, count + 1)]
    except Exception as exc:
        raise AddressingError(f"could not enumerate live timelines: {exc}") from exc
    return [timeline for timeline in timelines if timeline is not None]


def resolve_timeline(project, *, project_name: str, timeline_id: str | None = None,
                     timeline_name: str | None = None):
    """Resolve one exact live timeline by id, or exact name if no id exists."""
    verify_project(project, project_name)
    if not timeline_id and not timeline_name:
        raise ValueError("timeline_id or timeline_name is required")
    timelines = live_timelines(project)
    if timeline_id:
        matches = [timeline for timeline in timelines
                   if timeline_address(timeline, project_name).timeline_id
                   == str(timeline_id)]
    else:
        matches = [timeline for timeline in timelines
                   if str(_read(timeline, "GetName")) == timeline_name]
    if not matches:
        raise TargetNotFound(
            f"no live timeline matches "
            f"{('id ' + repr(timeline_id)) if timeline_id else ('exact name ' + repr(timeline_name))}")
    if len(matches) != 1:
        raise AmbiguousTarget(
            f"{len(matches)} live timelines match the requested identity")
    result = matches[0]
    if timeline_name and str(_read(result, "GetName")) != timeline_name and not timeline_id:
        raise StaleTarget("resolved timeline name changed during lookup")
    return result


def verify_timeline(project, address: TimelineAddress) -> None:
    """Re-read project and timeline identity immediately before mutation."""
    verify_project(project, address.project_name)
    matches = []
    for timeline in live_timelines(project):
        uid = timeline_address(timeline, address.project_name).timeline_id
        if uid == address.timeline_id:
            matches.append(timeline)
    if len(matches) != 1:
        raise StaleTarget(
            f"timeline identity {address.timeline_id!r} resolves to {len(matches)} live timelines")
    if str(_read(matches[0], "GetName")) != address.timeline_name:
        raise StaleTarget(
            f"timeline {address.timeline_id!r} was renamed from "
            f"{address.timeline_name!r} to {_read(matches[0], 'GetName')!r}")


def editorial_identity(selector: Mapping[str, Any]) -> tuple:
    """Stable source-and-span identity, optionally narrowed by a role.

    A caller may also supply Resolve's `GetUniqueId()` as a live handle;
    that value is never part of the persistent editorial identity.
    """
    required = ("source_file", "source_in_frame", "source_out_frame")
    missing = [key for key in required if key not in selector]
    if missing:
        raise ValueError(f"editorial selector is missing {missing}")
    return (str(selector["source_file"]), int(selector["source_in_frame"]),
            int(selector["source_out_frame"]), selector.get("track_role"))


def resolve_item(timeline, selector: Mapping[str, Any], *, track_roles=None) -> ItemAddress:
    """Resolve a semantic item selector against the current timeline.

    Selectors may provide a Resolve id for a fast path, or a persistent
    source range plus an optional role constraint. An id absent from the
    current timeline falls through to the editorial identity; an id that
    now denotes different material is stale. Ambiguous matches refuse.
    """
    media_type = selector.get("track_type", "video")
    identity_of_timeline = timeline_address(timeline)
    wanted_id = str(selector.get("resolve_item_id") or "")
    identity = editorial_identity(selector) if "source_file" in selector else ()
    candidates = []
    try:
        count = int(timeline.GetTrackCount(media_type) or 0)
    except Exception as exc:
        raise AddressingError(f"could not enumerate {media_type} tracks: {exc}") from exc
    for index in range(1, count + 1):
        try:
            items = timeline.GetItemListInTrack(media_type, index) or []
        except Exception as exc:
            raise AddressingError(f"could not read {media_type} track {index}: {exc}") from exc
        role = track_roles.get((media_type, index)) if track_roles else None
        for item in items:
            try:
                item_id = str(item.GetUniqueId() or "")
            except Exception:  # noqa: BLE001 - Resolve bridge error
                item_id = ""
            if wanted_id and item_id == wanted_id:
                if identity:
                    try:
                        pool_item = item.GetMediaPoolItem()
                        current = (
                            str(pool_item.GetClipProperty("File Path") or ""),
                            int(item.GetSourceStartFrame()),
                            int(item.GetSourceEndFrame()),
                            role,
                        )
                    except Exception as exc:
                        raise StaleTarget(
                            f"Resolve item {wanted_id!r} no longer exposes "
                            f"its editorial identity: {exc}") from exc
                    if current != identity:
                        raise StaleTarget(
                            f"Resolve item {wanted_id!r} no longer matches "
                            f"editorial identity {identity!r}")
                return ItemAddress(identity_of_timeline.timeline_id, item_id,
                                   identity, media_type, index, item)
            if not identity:
                continue
            try:
                pool = item.GetMediaPoolItem()
                source = str(pool.GetClipProperty("File Path") or "")
                source_in = int(item.GetSourceStartFrame())
                source_out = int(item.GetSourceEndFrame())
            except Exception:  # noqa: BLE001, S112 - skip unreadable handles
                continue
            got = (source, source_in, source_out, role)
            if got == identity:
                candidates.append(ItemAddress(
                    identity_of_timeline.timeline_id, item_id, identity,
                    media_type, index, item))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise AmbiguousTarget(
            f"editorial item {identity!r} matches {len(candidates)} live items")
    if wanted_id:
        raise StaleTarget(f"Resolve item id {wanted_id!r} is no longer live")
    raise TargetNotFound(f"no live item matches editorial identity {identity!r}")


def verify_item(timeline, address: ItemAddress) -> None:
    """Check a previously resolved item handle still matches its live id."""
    current_timeline = timeline_address(timeline)
    if current_timeline.timeline_id != address.timeline_id:
        raise StaleTarget(
            f"item belongs to timeline {address.timeline_id!r}, not "
            f"{current_timeline.timeline_id!r}")
    try:
        current_id = str(address.item.GetUniqueId() or "")
    except Exception as exc:
        raise StaleTarget(f"resolved timeline item is no longer readable: {exc}") from exc
    if not current_id or current_id != address.resolve_item_id:
        raise StaleTarget(
            f"resolved item changed from {address.resolve_item_id!r} to {current_id!r}")
    try:
        count = int(timeline.GetTrackCount(address.track_type) or 0)
        live_ids = []
        for index in range(1, count + 1):
            for item in timeline.GetItemListInTrack(address.track_type,
                                                    index) or ():
                live_ids.append(str(item.GetUniqueId() or ""))
    except Exception as exc:  # noqa: BLE001 - Resolve API bridge errors
        raise StaleTarget(f"could not verify item on live timeline: {exc}") from exc
    if live_ids.count(address.resolve_item_id) != 1:
        raise StaleTarget(
            f"Resolve item id {address.resolve_item_id!r} appears "
            f"{live_ids.count(address.resolve_item_id)} times on the live timeline")
    if address.editorial_identity:
        try:
            pool = address.item.GetMediaPoolItem()
            current = (
                str(pool.GetClipProperty("File Path") or ""),
                int(address.item.GetSourceStartFrame()),
                int(address.item.GetSourceEndFrame()),
                address.editorial_identity[3],
            )
        except Exception as exc:
            raise StaleTarget(
                f"resolved item no longer exposes its editorial identity: {exc}") from exc
        if current != address.editorial_identity:
            raise StaleTarget(
                f"resolved item changed from editorial identity "
                f"{address.editorial_identity!r} to {current!r}")
