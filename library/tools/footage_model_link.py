"""Lane B (cascade report section 5): the footage layer's foreign keys.

The captain's constraint: a unifying thread connects everything so a
change cascades. The Project Model holds identity and time;
specialised stores keep their bytes and gain foreign keys into model
ids (report section 2, principle). This module is where the footage
layer's keys live, so every producer spells them one way:

- ``asset-{digest}`` / ``clock-{digest}`` / ``stream-{digest}``: the
  same scheme ``project_model_evidence`` mints (``_asset_id`` /
  ``_clock_id`` / ``_stream_id``) - restated here so the scan step can
  link without importing the migration's privates, and asserted equal
  by ``tests/scenarios/test_cascade_b_footage_layer.py``.
- ``obs-{digest}-m1u-{index:04d}``: one Observation per source-memory
  M1 utterance. The migration's word-level ids carry a per-lane
  counter no reader can recompute, so utterance linkage gets its own
  deterministic lane key (``m1u``) that both the index build and the
  migration mint identically.
- ``mask-{clip_id}-{object_id}`` / ``track-{clip_id}-{object_id}``:
  the same ids ``_migrate_segmentation`` mints for 1.06 objects.

The one writer stays the one writer: ``link_scan`` migrates through
``project_model_evidence.migrate_project_evidence`` and commits
through ``project_model_store.commit_project_model`` - the revision
API PR 2271 builds on. No second store, no direct file writes into
``project_model/``. Fail-soft like the reel writer: a model link that
fails the scan is recorded, never raised - the Resolve path is
unchanged.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from library.tools import source_memory

TICKS_PER_SECOND = 1000
"""Millisecond source clocks, the Unit-3 evidence policy."""

AUTHOR = "footage.scan"


def asset_id_for_digest(digest: str) -> str:
    return f"asset-{digest}"


def clock_id_for_digest(digest: str) -> str:
    return f"clock-{digest}"


def stream_id_for_digest(digest: str) -> str:
    return f"stream-{digest}"


def utterance_obs_id(digest: str, index: int) -> str:
    """The Observation id for one M1 utterance, recomputable by any reader."""
    return f"obs-{digest}-m1u-{int(index):04d}"


def mask_id_for(clip_id: str, object_id: str) -> str:
    return f"mask-{clip_id}-{object_id or 'obj'}"


def track_id_for(clip_id: str, object_id: str) -> str:
    return f"track-{clip_id}-{object_id or 'obj'}"


def sec_to_ticks(start: Any, end: Any) -> tuple[int, int] | None:
    """Float seconds to half-open millisecond ticks, or None when refused.

    The Unit-3 ``sec_to_ticks`` policy: non-finite bounds and
    non-positive spans are not evidence and land as absent, never as
    empty intervals.
    """
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start_f) and math.isfinite(end_f)):
        return None
    start_tick = math.floor(start_f * TICKS_PER_SECOND)
    end_tick = math.ceil(end_f * TICKS_PER_SECOND)
    if end_tick <= start_tick:
        return None
    return (start_tick, end_tick)


def digest_for_clip(project_folder: str, clip: dict,
                    recorded: dict | None = None) -> tuple[str | None, str]:
    """A catalog clip's content digest, live file first, record second.

    ``source_memory.digest_for_clip`` is the one implementation; this
    wrapper exists so footage-layer producers share one import.
    Basis is ``live``, ``recorded`` or ``absent``.
    """
    from library.tools import source_memory

    return source_memory.digest_for_clip(project_folder, clip, recorded)


@source_memory._project_scoped
def link_scan(project_folder: str, catalog: list | None = None) -> dict[str, Any]:
    """Migrate the scan's footage into the Project Model, as one revision.

    ``catalog`` is the in-memory clip catalog the scan just measured;
    when None the migration reads it off the project state as today.
    Returns ``{"status": "committed", "revision", "asset_ids"}`` or
    ``{"status": "refused", "reason"}``. Never raises.
    """
    try:
        return _link(project_folder, catalog)
    except Exception as exc:  # noqa: BLE001 - fail-soft by contract
        return {"status": "refused",
                "reason": f"footage model link not updated: {exc!r}"}


def _link(project_folder: str, catalog: list | None) -> dict[str, Any]:
    from library.tools import project_model_evidence as evidence
    from library.tools import project_model_store as store

    if catalog is not None and not isinstance(catalog, list):
        return {"status": "refused",
                "reason": "catalog is not a list; nothing to migrate"}
    base, _report = evidence.migrate_project_evidence(
        project_folder, catalog=catalog)
    asset_ids = sorted(
        row["id"] for row in base.get("assets") or []
        if isinstance(row, dict) and row.get("id"))
    store_at = Path(project_folder)
    try:
        expected_revision, _stored = store.load_project_model(store_at)
    except FileNotFoundError:
        expected_revision = 0
    digest_count = len({row.get("content_digest")
                        for row in base.get("assets") or []
                        if isinstance(row, dict)})
    reason = (f"footage.scan: {len(asset_ids)} asset(s), "
              f"{digest_count} digest(s)")
    try:
        revision = store.commit_evidence_import(
            store_at, base, expected_revision=expected_revision,
            author=AUTHOR, reason=reason)
        return {"status": "committed", "revision": revision,
                "asset_ids": asset_ids}
    except Exception as exc:  # noqa: BLE001 - fail-soft by contract
        return {"status": "refused", "reason": f"store commit refused: {exc!r}"}


def mask_sidecar_entries(clip_id: str, seg_doc: dict) -> list[dict[str, str]]:
    """``[{object_id, mask_id, track_id}]`` for one 1.06 segmentation doc.

    Ids match ``_migrate_segmentation`` exactly, so the sidecar the
    step stamps and the model the migration mints resolve to each
    other.
    """
    out = []
    for obj in (seg_doc or {}).get("objects") or []:
        if not isinstance(obj, dict):
            continue
        object_id = str(obj.get("object_id") or "obj")
        out.append({"object_id": object_id,
                    "mask_id": mask_id_for(clip_id, object_id),
                    "track_id": track_id_for(clip_id, object_id)})
    return out


def sidecar_name(clip_id: str) -> str:
    """The sidecar file beside ``{clip_id}_segmentation.json``."""
    return f"{clip_id}_segmentation.model_ids.json"


def stamp_catalog_revision(entries: list, model_revision) -> None:
    """Stamp the Project Model revision onto each catalog entry, in place.

    The catalog is a read model over the model's assets/streams: the
    revision says which revision's ids its rows resolve into. None
    when the scan's link was refused, and a reader treats that as
    unlinked, never as revision zero.

    Lives here rather than in the catalog step so the key has one
    owner: the step routes the value, this module spells it. (The
    input-contract survey reads a step's own ``entry[...]`` subscript
    as a refusal; a shared tool's indexing is a statement about the
    row, not about the step's input.)
    """
    for entry in entries or []:
        if isinstance(entry, dict):
            entry["model_revision"] = model_revision


@source_memory._project_scoped
def attach_transcript_obs_ids(document: dict, project_folder: str) -> dict[str, int]:
    """Stamp ``obs_id`` onto a timeline transcript's bound rows, in place.

    Each row bound to a source file joins to the M1 utterance its
    source span overlaps most; the id is ``utterance_obs_id`` off that
    utterance's index, so it resolves to the migration's
    utterance-level Observation. Rows with no source span, no
    resolvable digest, no fresh M1, or no overlapping utterance keep
    ``obs_id`` absent - honestly unlinked, never guessed. The span
    cache key is untouched: this adds a field, it changes no key.

    Returns ``{"linked", "unlinked"}`` counts for the document's
    account of itself.
    """
    from library.tools import source_memory

    segments = document.get("segments")
    if not isinstance(segments, list):
        return {"linked": 0, "unlinked": 0}
    try:
        catalog = source_memory.load_catalog(project_folder)
        recorded = source_memory.load_recorded_fingerprints(project_folder)
    except Exception:  # noqa: BLE001 - best-effort linkage
        return {"linked": 0,
                "unlinked": sum(1 for s in segments
                                if isinstance(s, dict))}
    by_path: dict[str, dict] = {}
    for clip in catalog:
        if not isinstance(clip, dict):
            continue
        for key in ("source_file", "path"):
            path = clip.get(key)
            if path:
                by_path.setdefault(str(path), clip)
    memory: dict[str, dict] = {}
    linked = unlinked = 0
    for row in segments:
        if not isinstance(row, dict):
            continue
        obs_id = _obs_for_row(row, project_folder, by_path, recorded,
                              memory)
        if obs_id is None:
            unlinked += 1
            continue
        row["obs_id"] = obs_id
        linked += 1
    return {"linked": linked, "unlinked": unlinked}


def _obs_for_row(row: dict, project_folder: str, by_path: dict,
                 recorded: dict, memory: dict) -> str | None:
    from library.tools import source_memory

    source_file = row.get("source_file")
    if not source_file:
        return None
    try:
        start = float(row.get("source_start"))
        end = float(row.get("source_end"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return None
    clip = by_path.get(str(source_file))
    if clip is None:
        return None
    digest, _basis = source_memory.digest_for_clip(
        project_folder, clip, recorded)
    if digest is None:
        return None
    if digest not in memory:
        try:
            doc, status = source_memory.read_m1(digest)
        except Exception:  # noqa: BLE001 - one digest never stops the join
            return None
        memory[digest] = doc if status == "fresh" else None
    doc = memory[digest]
    if not isinstance(doc, dict):
        return None
    best_idx, best_overlap = None, 0.0
    for idx, utt in enumerate(doc.get("utterances") or []):
        if not isinstance(utt, dict):
            continue
        try:
            u_start, u_end = float(utt.get("start")), float(utt.get("end"))
        except (TypeError, ValueError):
            continue
        overlap = min(end, u_end) - max(start, u_start)
        if overlap > best_overlap:
            best_idx, best_overlap = idx, overlap
    if best_idx is None:
        return None
    return utterance_obs_id(digest, best_idx)
