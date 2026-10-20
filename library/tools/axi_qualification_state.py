"""The LIVE runtime state a recorded AXI qualification is checked against.

`edit_algebra` records, for every operation promoted to production, the
CONDITIONS the live evidence was earned under: the
`qualification_predicates` in `axi_qualification_records.json`. The router
(`library/tools/execution_router.py`) refuses a promoted operation unless
the runtime it is about to write satisfies those predicates, so that state
has to be MEASURED.

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
  (`axi_qualification.PREDICATE_FIELDS`), so the router's predicate check
  is unchanged - only the INPUT to it became real.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from library.tools import axi_qualification

#: Every field a recorded predicate may name. The measurement below
#: produces all of them, so a predicate never compares against a field the
#: runtime did not report.
PREDICATE_FIELDS = frozenset(axi_qualification.PREDICATE_FIELDS)

#: Resolve's media kinds, as the pool item reports them, mapped onto the
#: vocabulary the recorded predicates use.
_MEDIA_KINDS = {
    "video": "video",
    "audio": "audio",
    "still": "still",
    "image sequence": "still",
}

#: The media kinds a request may DECLARE when it touches no existing item
#: (`track.add` names the kind of the row it adds).
_DECLARED_KINDS = ("video", "audio")

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
    """The runtime, from handles already in hand."""
    from library.tools import reel_read

    return {"resolve": resolve,
            "project": resolve_project,
            "timeline": timeline,
            "rows": _rows(reel_read.read_tracks(timeline))}


def _measure(runtime: Mapping, operations: Sequence[Mapping]) -> dict:
    """Read the live runtime and describe it in the predicate vocabulary."""
    rows = list(runtime.get("rows") or ())
    by_id = {str(row.get("unique_id") or ""): row for row in rows}
    targets = _target_ids(operations)
    chosen = [by_id[uid] for uid in targets if uid in by_id]
    state = {
        "resolve_version": str(
            runtime["resolve"].GetVersionString() or ""),
        "project_name": str(runtime["project"].GetName() or ""),
        "timeline_name": str(runtime["timeline"].GetName() or ""),
        "media_type": _media_type(chosen, operations),
        "clip_composition": _clip_composition(chosen),
        "linked_audio_configuration": _linked_audio(chosen, by_id),
        "timeline_structure": _timeline_structure(rows),
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


def _media_kind(row: Mapping) -> str:
    """The item's media kind, in the vocabulary the predicates use."""
    raw = str(row.get("media_type") or "").strip().casefold()
    return _MEDIA_KINDS.get(raw, raw or "unknown")


def _media_type(chosen: Sequence[Mapping],
                operations: Sequence[Mapping]) -> str:
    """The media kind the request touches.

    An item the request addresses answers for itself. A request that
    addresses no item (a row it adds) declares the kind it touches.
    Requests that touch more than one kind answer "mixed", which matches
    no recorded predicate - a request that spans kinds was not the one
    the evidence was earned on.
    """
    kinds = {_media_kind(row) for row in chosen}
    if len(kinds) == 1:
        return kinds.pop()
    if len(kinds) > 1:
        return "mixed"
    declared = set()
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        for key in ("media_type", "track_type"):
            value = str(operation.get(key) or "").strip().casefold()
            if value in _DECLARED_KINDS:
                declared.add(value)
    if len(declared) == 1:
        return declared.pop()
    if len(declared) > 1:
        return "mixed"
    return "none"


def _clip_composition(chosen: Sequence[Mapping]) -> str:
    """What the request's item IS, or "none" when it addresses no item."""
    if not chosen:
        return "none"
    compositions = {_composition(row) for row in chosen}
    return compositions.pop() if len(compositions) == 1 else "mixed"


def _composition(row: Mapping) -> str:
    """One item's composition, as the recorded predicates name it."""
    media = _media_kind(row)
    linked = bool(row.get("linked_unique_ids"))
    track_type = str(row.get("track_type") or "")
    if media == "unknown":
        # A generator, a title, a compound clip: real, but it plays no
        # footage the pool can name. The inspection says so rather than
        # calling it a video clip.
        return "unknown_item"
    if track_type == "audio" or media == "audio":
        return "linked_audio_clip" if linked else "audio_clip"
    if media == "still":
        return "still_clip"
    return "linked_video_clip" if linked else "single_video_clip"


def _linked_audio(chosen: Sequence[Mapping],
                  by_id: Mapping[str, Mapping]) -> str:
    """Whether the request's item carries LINKED AUDIO.

    A linked item that the row read did not resolve is counted as linked
    audio: the inspection cannot claim the qualified condition, so it
    refuses rather than asserts it.
    """
    for row in chosen:
        for uid in row.get("linked_unique_ids") or ():
            linked = by_id.get(str(uid))
            if linked is None:
                return "linked_audio_present"
            if str(linked.get("track_type") or "") == "audio":
                return "linked_audio_present"
    return "no_linked_audio"


def _timeline_structure(rows: Sequence[Mapping]) -> str:
    """How much the timeline holds, as the recorded predicates name it.

    "single seeded clip, empty baseline otherwise" is a statement about
    the ITEMS on the timeline: one clip and nothing else. Two items are
    not an empty baseline, so they answer with their own name.
    """
    count = len(rows)
    if count == 0:
        return "empty_fixture"
    if count == 1:
        return "single_seeded_clip_empty_baseline"
    return "multi_item_timeline"
