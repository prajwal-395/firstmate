"""The LIVE runtime state a recorded AXI qualification is checked against.

`edit_algebra` records, for every operation promoted to production, the
DIMENSIONS the live evidence was earned under: the
`qualification_predicates` in `axi_qualification_records.json`. The
router (`library/tools/execution_router.py`) refuses a promoted operation
unless the runtime it is about to write satisfies those predicates, so
that state has to be MEASURED.

Until it was measured, nothing measured it: no production path ever put a
`qualification_state` into its routing context, so `_qualification_gaps`
answered "runtime qualification state was not inspected" and refused every
promoted operation - `clip.delete`, `clip.set_property` and `track.add` -
with a statement about the CODE rather than about the timeline. A break
test on 2026-10-20 measured the consequence: through `ren revise`, no
destructive edit could complete, and the reason named a missing inspection
rather than the conditions the evidence was earned under.

This module is that measurement, and the only place that reads Resolve to
produce it:

* it reads through AXI - the same `resolve_axi_core_commands` accessors
  every AXI verb uses, and `reel_read` for the rows - under the instance
  lease, so an inspection cannot overlap another writer. A caller that
  already holds the lease (the broker's AXI execute path holds it
  EXCLUSIVE, with the target timeline current) is read under THAT lease;
* it NEVER raises into its caller. A state it could not read comes back
  as `{"inspected": False, "reason": ...}`, and the router refuses with
  that reason. An unread state is never reported as an inspected one;
* it measures in the vocabulary the recorded predicates are written in
  (`axi_qualification_dimensions.DIMENSIONS`), so the router's predicate
  check is unchanged - only the INPUT to it became real. The version has
  ONE reading (`axi_qualification.canonical_resolve_version`): Resolve
  reports a bare dotted version, the recorded predicates are compared in
  it, and a product label can never make a satisfiable predicate
  unsatisfiable.

The dimensions are a property of the STATE, never of a project: a
predicate that named the fixture project restricted an operation to the
place the evidence was earned rather than to the state the evidence was
earned in. `library/tools/axi_qualification_dimensions.py` owns that
vocabulary and what each measurement MEANS; this module owns the READ.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from library.tools import axi_qualification_dimensions as dimensions

#: Every dimension a recorded predicate may name. The measurement below
#: produces all of them, so a predicate never compares against a field
#: the runtime did not report.
PREDICATE_FIELDS = frozenset(dimensions.DIMENSIONS)

#: How long the inspection waits for the instance. It is a READ that
#: gates a routing decision, so it rides out a write in progress and then
#: says the instance is held - it does not stall a front door for the
#: lease's own quarter-hour default.
INSPECTION_LEASE_TIMEOUT_SECONDS = 120.0


def with_runtime_state(context: Mapping | None, *, project: str,
                       timeline: str,
                       operations: Sequence[Mapping] = (),
                       resolve=None) -> dict:
    """The routing context with the measured runtime state attached.

    A caller that cannot say WHAT to inspect gets its context back
    unchanged: the router then refuses with the reason it gives when
    nobody looked, which is the truth. A caller that can say gets the
    measurement, and the router's predicate check runs against a real
    reading.

    `resolve` is the caller's own Resolve connection, when it has one:
    the broker's worker holds a persistent connection and must not open
    a second scripting client (the 2026-10-06 crash).
    """
    merged = dict(context or {})
    if not str(project or "").strip() or not str(timeline or "").strip():
        return merged
    if not _promotion_at_stake(operations):
        # No operation in the set was promoted to production, so the
        # recorded predicates cannot change the route: the measurement
        # would cost a Resolve read and say nothing.
        return merged
    merged["qualification_state"] = inspect(
        project=project, timeline=timeline, operations=operations,
        resolve=resolve)
    return merged


def _promotion_at_stake(operations: Sequence[Mapping]) -> bool:
    """Whether any operation in the set carries a production-enabled row.

    The recorded predicates are what a PROMOTION is conditional on, so
    they are read only where a promotion is in question.
    """
    from library.tools import edit_algebra

    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        entry = edit_algebra.status(str(operation.get("op", "")))
        if entry is None:
            continue
        if any(backend.production_enabled for backend in entry.backends):
            return True
    return False


def inspect(*, project: str, timeline: str,
            operations: Sequence[Mapping] = (), resolve=None) -> dict:
    """The measured runtime state, or why it could not be measured.

    The returned mapping is the `qualification_state` the router reads:
    `{"inspected": True, "state": {...}}` when the runtime was read, and
    `{"inspected": False, "reason": ...}` when it was not.
    """
    if not str(project or "").strip():
        return {"inspected": False,
                "reason": "the request names no Resolve project to inspect"}
    if not str(timeline or "").strip():
        return {"inspected": False,
                "reason": "the request names no timeline to inspect"}
    try:
        from library.tools.resolve_lock import held, resolve_lease

        if held():
            return _measure(_read_runtime(project, timeline, resolve),
                            operations)
        with resolve_lease("axi qualification inspection", exclusive=False,
                           timeout=INSPECTION_LEASE_TIMEOUT_SECONDS):
            return _measure(_read_runtime(project, timeline, resolve),
                            operations)
    except Exception as exc:  # noqa: BLE001 - an unread state refuses
        return {"inspected": False, "reason": f"{type(exc).__name__}: {exc}"}


def inspect_handles(*, resolve, resolve_project, timeline,
                    operations: Sequence[Mapping] = ()) -> dict:
    """The measured runtime state, from Resolve handles already in hand.

    The AXI command path holds its lease and its handles already, so it
    reads through these rather than opening a second scripting client.
    """
    if not _promotion_at_stake(operations):
        return {"inspected": False,
                "reason": "no operation in this set was promoted to "
                          "production, so the recorded predicates cannot "
                          "change the route"}
    try:
        return _measure(
            _read_runtime_handles(resolve, resolve_project, timeline),
            operations)
    except Exception as exc:  # noqa: BLE001 - an unread state refuses
        return {"inspected": False, "reason": f"{type(exc).__name__}: {exc}"}


# ── The measurement ─────────────────────────────────────────────────


def _read_runtime(project: str, timeline: str, resolve=None) -> dict:
    """The live runtime, read through AXI.

    THE CONNECTION POINT: a test stands in for Resolve here rather than
    opening the live instance (tests/conftest.py). A caller that already
    holds a Resolve connection passes it, so the broker's worker does
    not open a second scripting client.
    """
    from library.tools.resolve_axi_core_commands import (
        _connect,
        _project,
        _target_timeline,
    )

    if resolve is None:
        resolve = _connect()
    open_project = _project(resolve, project)
    target, _is_current, _note = _target_timeline(open_project, timeline)
    return _read_runtime_handles(resolve, open_project, target)


def _read_runtime_handles(resolve, resolve_project, timeline) -> dict:
    """The runtime, from handles already in hand.

    The rows measured are the rows the editorial pipeline reads: video
    and audio. A Resolve subtitle track holds caption TEXT, not clips -
    Ren's own caption layer is rendered cards on a video row named
    "Subtitles" - so a subtitle track is a caption layer, not timeline
    items, and counting it would report a timeline that is not there.
    """
    from library.tools import reel_read

    tracks = reel_read.read_tracks(timeline)
    return {"resolve": resolve,
            "project": resolve_project,
            "timeline": timeline,
            "tracks": tracks,
            "rows": _rows(tracks),
            "settings": _settings(timeline)}


def _settings(timeline) -> dict:
    """The timeline's own settings, or the refusal of what it would say.

    An unreadable frame rate or resolution is NOT a default: a
    dimension that reports "unknown" where the runtime did not say is a
    dimension that lets an unproven state through, so the measurement
    refuses instead.
    """
    settings = {}
    for key, cast in (("timelineFrameRate", float),
                      ("timelineResolutionWidth", int),
                      ("timelineResolutionHeight", int)):
        try:
            raw = timeline.GetSetting(key)
            settings[key] = cast(raw)
        except Exception as unreadable:
            raise ValueError(
                f"the timeline's {key} could not be read "
                f"({unreadable}); refusing to measure a dimension the "
                "runtime did not report") from unreadable
    return settings


def _measure(runtime: Mapping, operations: Sequence[Mapping]) -> dict:
    """Read the live runtime and describe it in the predicate vocabulary."""
    from library.tools.axi_qualification import canonical_resolve_version

    rows = list(runtime.get("rows") or ())
    tracks = list(runtime.get("tracks") or ())
    by_id = {str(row.get("unique_id") or ""): row for row in rows}
    targets = _target_ids(operations)
    chosen = [by_id[uid] for uid in targets if uid in by_id]
    settings = runtime.get("settings") or {}
    state = {
        # The ONE reading of the version: Resolve reports a bare dotted
        # version and the recorded predicates are written in it, so the
        # comparison is version against version.
        "resolve_version": canonical_resolve_version(
            runtime["resolve"].GetVersionString()),
        "media_type": dimensions.media_type(chosen, operations),
        "clip_composition": dimensions.clip_composition(chosen),
        "linked_audio_configuration": dimensions.linked_audio_configuration(
            chosen, by_id),
        "track_layout": dimensions.track_layout(tracks),
        "track_occupancy": dimensions.track_occupancy(tracks),
        "timeline_density": dimensions.timeline_density(tracks),
        "timeline_structure": dimensions.timeline_structure(tracks),
        "project_settings": dimensions.project_settings(
            int(settings.get("timelineResolutionWidth") or 0),
            int(settings.get("timelineResolutionHeight") or 0)),
        "frame_rate": dimensions.frame_rate(
            settings.get("timelineFrameRate")),
        "existing_effects": dimensions.existing_effects(chosen),
    }
    missing = [uid for uid in targets if uid not in by_id]
    uncovered = sorted(PREDICATE_FIELDS - set(state))
    if uncovered:
        # A new predicate field arrived and this measurement does not
        # cover it. Refuse rather than let a predicate compare against a
        # field the runtime never reported.
        return {"inspected": False,
                "reason": "the measurement does not cover: "
                          + ", ".join(uncovered)}
    return {"inspected": True, "state": state,
            "targets": list(targets),
            "missing_targets": missing}


def _rows(tracks: Sequence[Mapping]) -> list[dict]:
    """Every clip on every row, as one flat list with its row's identity."""
    rows = []
    for track in tracks or ():
        for clip in track.get("clips") or ():
            rows.append({**clip,
                         "track_type": str(track.get("type") or ""),
                         "track_index": track.get("index"),
                         "track_name": str(track.get("name") or "")})
    return rows


def _target_ids(operations: Sequence[Mapping]) -> list[str]:
    """The stable item identities the request addresses, in order."""
    ids: list[str] = []
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        uid = str(operation.get("unique_id")
                  or operation.get("resolve_item_id") or "")
        if uid and uid not in ids:
            ids.append(uid)
    return ids


# ── The vocabulary, reached through the one enumeration ──────────────
#
# These are thin names over `axi_qualification_dimensions`, kept because
# a caller (and a test) asks for a measurement by name. The MEANING
# lives in the dimensions module, so a second reading of the same
# question cannot exist.


def _media_type(chosen: Sequence[Mapping],
                operations: Sequence[Mapping]) -> str:
    return dimensions.media_type(chosen, operations)


def _clip_composition(chosen: Sequence[Mapping]) -> str:
    return dimensions.clip_composition(chosen)


def _linked_audio(chosen: Sequence[Mapping],
                  by_id: Mapping[str, Mapping]) -> str:
    return dimensions.linked_audio_configuration(chosen, by_id)


def _timeline_structure(tracks: Sequence[Mapping]) -> str:
    return dimensions.timeline_structure(tracks)


def _track_layout(tracks: Sequence[Mapping]) -> str:
    return dimensions.track_layout(tracks)


def _timeline_density(tracks: Sequence[Mapping]) -> str:
    return dimensions.timeline_density(tracks)


def _existing_effects(chosen: Sequence[Mapping]) -> str:
    return dimensions.existing_effects(chosen)
