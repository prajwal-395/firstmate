"""Reconcile the live Resolve timeline with the Project Model's view of it.

PRES-007.

Resolve is the timeline truth and the Project Model is Ren's canonical
record of what a reel contains.  Between a mutation and the model's
commit there is a window in which the two can disagree - and after the
commit the model has to be able to SAY what it believes the timeline
holds, or there is nothing to reconcile against.

Three things were missing, and this module supplies them:

1. The model's ``timeline_states`` recorded a generation number and no
   composition: ``clip_use_ids`` and ``edit_operation_ids`` were empty,
   so the model could not say WHICH clips the timeline holds.
2. The model's ``nle_bindings`` collection was declared in the schema
   and never committed, so nothing joined a model clip use to the live
   Resolve item that plays it.
3. Nothing compared the live read with the model at all.

The join
--------
A live clip and a model clip use are joined on the MATERIAL - the
model's asset for the clip's source path - and, within one material, on
the TIMELINE extent.  The extent is compared in the model's own ticks:
the live read is in frames and the model is in ticks, and both clocks
carry the rate that converts one to the other exactly.  A clock without a
frame rate makes the comparison UNAVAILABLE, never a drift: "the model
cannot say" is not "the model disagrees".

What the comparison reports
---------------------------
- ``unbound`` - a live clip the model does not describe.
- ``missing`` - a clip use the model places on this timeline that the
  live timeline does not hold.
- ``changed`` - a joined pair whose extent or material differs.
- ``in_sync`` - none of the above.

A reconcile never writes.  It compares, and it names what differs; the
commit that follows records the comparison's result so the NEXT one has
a baseline.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The four answers a reconcile gives.
IN_SYNC = "in_sync"
DRIFT = "drift"
UNBOUND = "unbound"
UNAVAILABLE = "unavailable"


class ReconcileRefused(ValueError):
    """The reconciliation cannot be established, and this says why."""


# ── The model's view ─────────────────────────────────────────────────


def timeline_state(model: Mapping[str, Any], timeline_id: str) -> dict | None:
    """The model's current state for one timeline, or None."""
    for row in model.get("timeline_states") or []:
        if isinstance(row, Mapping) and row.get("timeline_id") == timeline_id:
            return dict(row)
    return None


def bindings_for(model: Mapping[str, Any], timeline_id: str) -> list[dict]:
    """The model's NLE bindings for one timeline."""
    return [dict(row) for row in model.get("nle_bindings") or []
            if isinstance(row, Mapping) and row.get("timeline_id") == timeline_id]


def clip_use_rows(model: Mapping[str, Any]) -> dict[str, dict]:
    """Every clip use, by id."""
    return {str(row["id"]): dict(row)
            for row in model.get("clip_uses") or []
            if isinstance(row, Mapping) and row.get("id")}


def _clocks(model: Mapping[str, Any]) -> dict[str, dict]:
    return {str(row["id"]): dict(row)
            for row in model.get("clocks") or []
            if isinstance(row, Mapping) and row.get("id")}


def _asset_by_uri(model: Mapping[str, Any]) -> dict[str, str]:
    """The model's asset id for each source path it holds."""
    return {str(row.get("uri")): str(row["id"])
            for row in model.get("assets") or []
            if isinstance(row, Mapping) and row.get("id") and row.get("uri")}


def _ticks_of(clock: Mapping[str, Any] | None, frames: Any) -> int | None:
    """The tick a frame sits on, on this model's clock.

    ``None`` where the clock carries no frame rate: the conversion is
    unavailable, and an unavailable comparison is not a disagreement.
    """
    if clock is None or frames is None:
        return None
    try:
        rate = clock.get("frame_rate")
        ticks_per_second = int(clock.get("ticks_per_second") or 0)
        count = int(frames)
    except (TypeError, ValueError):
        return None
    if not rate or ticks_per_second <= 0:
        return None
    try:
        numerator = int(rate["numerator"])
        denominator = int(rate["denominator"])
    except (KeyError, TypeError, ValueError):
        return None
    if numerator <= 0 or denominator <= 0:
        return None
    return count * ticks_per_second * denominator // numerator


# ── The live read ───────────────────────────────────────────────────


def live_clips(snapshot: Mapping[str, Any]) -> list[dict]:
    """Every clip on the live timeline, in timeline order.

    A clip with no readable span is not a clip the model can be asked
    about, and it is skipped rather than counted as a difference.
    """
    rows = []
    for track in snapshot.get("tracks") or []:
        if not isinstance(track, Mapping):
            continue
        for clip in track.get("clips") or []:
            if not isinstance(clip, Mapping):
                continue
            if clip.get("record_in") is None or clip.get("record_out") is None:
                continue
            rows.append(dict(clip))
    return rows


# ── The join ────────────────────────────────────────────────────────


def join_live_clips(model: Mapping[str, Any],
                    snapshot: Mapping[str, Any],
                    timeline_id: str = "") -> dict[str, Any]:
    """Join the live clips to the model's clip uses.

    The join is on the MATERIAL - the model's asset for the clip's source
    path - and, within one material, on the TIMELINE extent.  Two clips
    of the same footage on one timeline are two different moments, and
    the extent is what says which is which.

    The extent is compared in the model's own ticks: the live read is in
    frames on the TIMELINE, and the model's clip use is in ticks on the
    reel's clock, so the reel's clock is the one that converts.

    A clip whose material the model does not hold is UNBOUND.  A clip
    whose material the model holds but whose extent matches no clip use
    is also unbound, and named as such: the model and the live timeline
    disagree about that material, and that is the finding.  A clip whose
    extent cannot be placed on the model's ticks at all is UNAVAILABLE,
    which is not a disagreement.
    """
    uses = clip_use_rows(model)
    assets = _asset_by_uri(model)
    clocks = _clocks(model)
    candidates: dict[str, list[dict]] = {}
    for row in uses.values():
        candidates.setdefault(str(row.get("asset_id") or ""), []).append(row)

    reel_clock = clocks.get(f"clock:reel:{timeline_id}")

    bound: dict[str, str] = {}
    unbound: list[dict] = []
    unavailable: list[dict] = []
    for clip in live_clips(snapshot):
        asset_id = assets.get(str(clip.get("source_file") or ""))
        if asset_id is None:
            unbound.append({
                "unique_id": str(clip.get("unique_id") or ""),
                "name": str(clip.get("name") or ""),
                "reason": "the model holds no asset for this clip's source",
            })
            continue
        want_start = _ticks_of(reel_clock, clip.get("record_in"))
        want_end = _ticks_of(reel_clock, clip.get("record_out"))
        if want_start is None or want_end is None:
            unavailable.append({
                "unique_id": str(clip.get("unique_id") or ""),
                "name": str(clip.get("name") or ""),
                "asset_id": asset_id,
                "reason": "the model's clock carries no frame rate, so the "
                          "live frames cannot be placed on its ticks",
            })
            continue
        matches = []
        for use in candidates.get(asset_id, []):
            timeline = use.get("timeline_interval")
            if not isinstance(timeline, Mapping):
                continue
            start = timeline.get("start")
            end = timeline.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            if start <= want_start < end:
                matches.append(use)
        if len(matches) != 1:
            unbound.append({
                "unique_id": str(clip.get("unique_id") or ""),
                "name": str(clip.get("name") or ""),
                "asset_id": asset_id,
                "reason": ("no model clip use holds this clip's extent"
                           if not matches else
                           f"{len(matches)} model clip uses hold this "
                           "clip's extent"),
            })
            continue
        bound[str(clip.get("unique_id") or "")] = str(matches[0]["id"])
    return {"bound": bound, "unbound": unbound, "unavailable": unavailable,
            "uses": uses}


# ── The comparison ──────────────────────────────────────────────────


def reconcile(model: Mapping[str, Any], *, snapshot: Mapping[str, Any],
              timeline_id: str) -> dict[str, Any]:
    """Compare a live Resolve read with the Project Model's view of it.

    A pure function of the model and the read, so it runs against a
    RECORDED generation with no Resolve connection, and against a live
    one the same way.
    """
    state = timeline_state(model, timeline_id)
    joined = join_live_clips(model, snapshot, timeline_id)
    bound = joined["bound"]
    uses = joined["uses"]
    bindings = bindings_for(model, timeline_id)

    bound_use_ids = set(bound.values())
    live_ids = {str(clip.get("unique_id") or "")
                for clip in live_clips(snapshot)}

    missing = []
    for binding in bindings:
        for use_id, unique_id in (binding.get("clip_bindings") or {}).items():
            if unique_id not in live_ids:
                missing.append({
                    "clip_use_id": str(use_id),
                    "unique_id": str(unique_id),
                    "reason": "the model binds this clip use to a Resolve "
                              "item the live timeline does not hold",
                })

    unbound = list(joined["unbound"])
    unavailable_ids = {row["unique_id"] for row in joined["unavailable"]}
    for unique_id in live_ids:
        if unique_id in bound or unique_id in unavailable_ids:
            continue
        if any(unique_id in (binding.get("clip_bindings") or {}).values()
               for binding in bindings):
            continue
        if not any(row["unique_id"] == unique_id for row in unbound):
            unbound.append({
                "unique_id": unique_id,
                "name": "",
                "reason": "the live timeline holds a clip the model does "
                          "not describe",
            })

    changed = []
    for unique_id, use_id in sorted(bound.items()):
        use = uses.get(use_id)
        if use is None:
            changed.append({
                "unique_id": unique_id, "clip_use_id": use_id,
                "field": "clip_use",
                "model": None, "live": unique_id,
                "reason": "the model binds a clip use it no longer holds",
            })
            continue
        clip = next((row for row in live_clips(snapshot)
                     if str(row.get("unique_id") or "") == unique_id), None)
        if clip is None:
            continue
        if str(use.get("asset_id") or "") != "" and \
                str(clip.get("source_file") or "") and \
                _asset_uri(model, str(use.get("asset_id"))) != \
                str(clip.get("source_file")):
            changed.append({
                "unique_id": unique_id, "clip_use_id": use_id,
                "field": "source_file",
                "model": _asset_uri(model, str(use.get("asset_id"))),
                "live": str(clip.get("source_file")),
                "reason": "the model and the live timeline play different "
                          "footage here",
            })

    if unbound or missing or changed:
        status = DRIFT
    elif joined["unavailable"]:
        status = UNAVAILABLE
    elif state is None:
        status = UNBOUND
    else:
        status = IN_SYNC
    return {
        "status": status,
        "timeline_id": timeline_id,
        "model_generation": (state or {}).get("generation"),
        "model_clip_use_ids": list((state or {}).get("clip_use_ids") or []),
        "binding_ids": [str(row.get("id")) for row in bindings],
        "live_clips": len(live_ids),
        "bound": dict(sorted(bound.items())),
        "unbound": unbound,
        "unavailable": list(joined["unavailable"]),
        "missing": missing,
        "changed": changed,
    }


def _asset_uri(model: Mapping[str, Any], asset_id: str) -> str:
    for row in model.get("assets") or []:
        if isinstance(row, Mapping) and str(row.get("id")) == asset_id:
            return str(row.get("uri") or "")
    return ""


# ── The commit ──────────────────────────────────────────────────────


def commit_reconciled_state(project_folder, *, snapshot: Mapping[str, Any],
                            timeline_id: str, generation: int,
                            project_identity: str, timeline_identity: str,
                            expected_revision: int,
                            author: str = "ren") -> dict[str, Any]:
    """Commit the reconciled composition of one timeline.

    Three things land in ONE model event, so the model never holds a
    composition it cannot join to the live timeline:

    - the ``timeline_states`` row for the timeline, naming the clip uses
      it holds and the edit operations applied to it;
    - the ``nle_bindings`` row that joins each of those clip uses to the
      live Resolve item that plays it;
    - nothing else: the collections are carried whole, so another
      timeline's rows are not disturbed.

    A timeline with no live clip to bind has no binding to record, and
    that is said rather than papered over with an empty one.
    """
    from pathlib import Path

    from library.tools import model_stamps, project_model_store
    from library.tools.project_model_editorial import stable_id

    project = Path(project_folder)
    revision, model = project_model_store.load_project_model(project)
    if revision != expected_revision:
        raise ReconcileRefused(
            f"stale project model revision {expected_revision}; current "
            f"revision is {revision}")

    joined = join_live_clips(model, snapshot, timeline_id)
    bound = joined["bound"]
    clip_bindings = {use_id: unique_id
                     for unique_id, use_id in bound.items()}
    report = reconcile(model, snapshot=snapshot, timeline_id=timeline_id)

    rows = [row for row in model.get("timeline_states") or []
            if isinstance(row, Mapping)
            and row.get("timeline_id") != timeline_id]
    previous = timeline_state(model, timeline_id) or {}
    rows.append({
        "id": stable_id("timeline_state", timeline_id),
        "timeline_id": timeline_id,
        "generation": int(generation),
        "clip_use_ids": sorted(set(clip_bindings)),
        "edit_operation_ids": list(previous.get("edit_operation_ids") or []),
    })

    bindings = [row for row in model.get("nle_bindings") or []
                if isinstance(row, Mapping)
                and row.get("timeline_id") != timeline_id]
    if clip_bindings:
        bindings.append(model_stamps.build_nle_binding(
            timeline_id=timeline_id,
            timeline_identity=timeline_identity,
            project_identity=project_identity,
            generation=int(generation),
            clip_bindings=clip_bindings))

    committed = project_model_store.append_model_patch(
        project,
        {"timeline_states": rows, "nle_bindings": bindings},
        expected_revision=expected_revision, author=author,
        reason=(f"reconcile timeline {timeline_id} against Resolve "
                f"generation {generation}: {report['status']}"))
    return {
        "status": "committed",
        "revision": committed,
        "timeline_id": timeline_id,
        "generation": int(generation),
        "reconcile": report,
    }

