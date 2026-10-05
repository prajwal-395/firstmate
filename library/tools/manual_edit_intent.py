"""Verify that a manual edit preserved creative intent.

The captain, 2026-10-05: a manual edit that crops the face, swaps in
non-illustrating B-roll, or makes captions obscure the subject is attributed
to the captain and the mismatch is downgraded to a warning - but the
creative degradation is not flagged. Ren preserves the edit but does not
verify it against intent.

This module is the intent-verification seam for manual edits. After a
manual edit is detected and attributed, the edited spans are checked
against creative intent:

- Face cropping: a transform change that zooms in beyond a threshold
  crops the subject's face.
- B-roll relevance: a clip swap that replaces an illustrating cutaway
  with one that does not illustrate the speech (requires VLM - specified
  here, not implemented until E1's checks exist).
- Caption obscuring: a transform or caption change that makes captions
  overlap the subject's face.

Findings are warnings attributed to the manual edit, not build failures.
The edit is preserved; the regression is flagged.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

#: Zoom level beyond which a face is considered cropped.
#: A zoom of 1.0 is the identity transform. A zoom of 1.5 magnifies
#: the image 50%, which crops the face on most framings.
ZOOM_CROP_THRESHOLD = 1.5

#: Transform fields that affect framing.
_TRANSFORM_ZOOM_KEYS = ("Zoom", "zoom", "ZoomX", "ZoomY")


def _zoom_of(transform: Mapping) -> Optional[float]:
    """Read the zoom level from a transform dict, or None if absent."""
    for key in _TRANSFORM_ZOOM_KEYS:
        if key in transform:
            try:
                return float(transform[key])
            except (TypeError, ValueError):
                return None
    return None


def _item_zoom(item: Mapping) -> Optional[float]:
    """Read the zoom level from a timeline item's transform."""
    transform = item.get("transform") or {}
    if not isinstance(transform, Mapping):
        return None
    return _zoom_of(transform)


def _item_track(item: Mapping) -> str:
    """A human-readable track name for a timeline item."""
    track_type = str(item.get("track_type") or "video")
    track_name = str(item.get("track_name") or "")
    return f"{track_type}:{track_name}" if track_name else track_type


def _item_name(item: Mapping) -> str:
    """A human-readable name for a timeline item."""
    return str(item.get("name") or item.get("unique_id") or "unnamed")


def _item_span(item: Mapping) -> str:
    """A human-readable source span for a timeline item."""
    source_in = item.get("source_in_frame")
    source_out = item.get("source_out_frame")
    if source_in is not None and source_out is not None:
        return f"source {source_in}..{source_out}"
    record_in = item.get("record_in")
    record_out = item.get("record_out")
    if record_in is not None and record_out is not None:
        return f"record {record_in}..{record_out}"
    return "unknown span"


def _check_face_crop(before_item: Mapping, after_item: Mapping) -> Optional[str]:
    """Check if a transform change crops the subject's face.

    Returns a description of the regression if the edit cropped the face,
    or None if it did not.
    """
    before_zoom = _item_zoom(before_item)
    after_zoom = _item_zoom(after_item)
    if before_zoom is None or after_zoom is None:
        return None
    if after_zoom <= before_zoom:
        return None
    if after_zoom < ZOOM_CROP_THRESHOLD:
        return None
    track = _item_track(after_item)
    name = _item_name(after_item)
    span = _item_span(after_item)
    return (
        f"zoom increased from {before_zoom:.2f} to {after_zoom:.2f} "
        f"on {track} {name!r} ({span}), which crops the subject's face"
    )


def _check_broll_swap(before_item: Mapping, after_item: Mapping) -> Optional[str]:
    """Check if a clip swap replaced an illustrating cutaway.

    This check requires VLM analysis of the swapped clip's content against
    the speech at that timeline span. It is specified here and will be
    implemented when E1's intent-verification seam provides the VLM check.

    Returns None until the VLM check is available.
    """
    return None


def _check_caption_obscuring(before_item: Mapping,
                             after_item: Mapping) -> Optional[str]:
    """Check if a change made captions obscure the subject.

    This check requires measuring the subject's face position in the
    rendered frame and comparing it against caption bounding boxes. It is
    specified here and will be implemented when E1's intent-verification
    seam provides the face measurement.

    Returns None until the face measurement is available.
    """
    return None


def verify_manual_edit_intent(
        edit_history: Sequence[Mapping],
        review_dir: str = "",
        reel_name: str = "") -> list[dict]:
    """Verify that manual edits preserved creative intent.

    For each manual edit in the history, checks whether the edit degraded
    creative intent (cropped face, non-illustrating B-roll, obscuring
    captions). Returns a list of findings, each a dict with:

    - ``edit_id``: the history entry id of the manual edit
    - ``edit_at``: when the edit was recorded
    - ``edit_summary``: what the edit changed
    - ``regression``: a description of the creative-intent regression
    - ``check``: which intent check flagged it (e.g. ``"face_crop"``)

    Findings are warnings attributed to the manual edit, not build
    failures. The edit is preserved; the regression is flagged.
    """
    findings = []
    if not edit_history:
        return findings

    from library.tools import reel_edit_history as _history
    from library.tools import plan_provenance

    for entry in edit_history:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("actor") != _history.ACTOR_CAPTAIN:
            continue
        if entry.get("act") != _history.ACT_MANUAL_EDIT:
            continue

        editor_change_id = (entry.get("refs") or {}).get("editor_change_id")
        if not editor_change_id:
            continue

        records = _editor_change_records(review_dir, reel_name)
        record = None
        for candidate in records:
            if str(candidate.get("id")) == str(editor_change_id):
                record = candidate
                break
        if record is None:
            continue

        before_snapshot = record.get("before_snapshot") or {}
        after_snapshot = record.get("after_snapshot") or {}

        changes = _snapshot_diff(before_snapshot, after_snapshot)
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            kind = str(change.get("kind") or "")
            if kind != "item_changed":
                continue
            before_item = change.get("before") or {}
            after_item = change.get("after") or {}
            if not isinstance(before_item, Mapping):
                continue
            if not isinstance(after_item, Mapping):
                continue

            regression = _check_face_crop(before_item, after_item)
            if regression:
                findings.append({
                    "edit_id": str(entry.get("id") or ""),
                    "edit_at": str(entry.get("at") or ""),
                    "edit_summary": str(entry.get("summary") or ""),
                    "regression": regression,
                    "check": "face_crop",
                })

            regression = _check_broll_swap(before_item, after_item)
            if regression:
                findings.append({
                    "edit_id": str(entry.get("id") or ""),
                    "edit_at": str(entry.get("at") or ""),
                    "edit_summary": str(entry.get("summary") or ""),
                    "regression": regression,
                    "check": "broll_swap",
                })

            regression = _check_caption_obscuring(before_item, after_item)
            if regression:
                findings.append({
                    "edit_id": str(entry.get("id") or ""),
                    "edit_at": str(entry.get("at") or ""),
                    "edit_summary": str(entry.get("summary") or ""),
                    "regression": regression,
                    "check": "caption_obscuring",
                })

    return findings


def _editor_change_records(review_dir: str, reel_name: str) -> list[dict]:
    """Read the editor change records for a reel from plan_provenance."""
    if not review_dir:
        return []
    from library.tools import plan_provenance
    try:
        doc = plan_provenance.read_provenance(review_dir) or {}
    except Exception:  # noqa: BLE001 - unreadable provenance reads as absence
        return []
    records = ((doc.get("unattributed_editor_changes") or {}).get(
        str(reel_name)) or [])
    return [r for r in records if isinstance(r, Mapping)]


def _snapshot_diff(before: Mapping, after: Mapping) -> list[dict]:
    """Diff two snapshots, returning the list of changes.

    Reuses `reel_replace_guard.snapshot_diff` when available; falls back
    to a minimal diff when it is not.
    """
    try:
        from library.tools import reel_replace_guard
        return reel_replace_guard.snapshot_diff(before, after)
    except Exception:  # noqa: BLE001 - fallback to minimal diff
        return _minimal_snapshot_diff(before, after)


def _minimal_snapshot_diff(before: Mapping, after: Mapping) -> list[dict]:
    """A minimal snapshot diff: item-level add/remove/change."""
    changes = []
    before_items = {id(item): item for item in before.get("items", ())}
    after_items = {id(item): item for item in after.get("items", ())}
    for key in set(before_items) | set(after_items):
        if key not in before_items:
            changes.append({"kind": "item_added", "before": None,
                            "after": after_items[key]})
        elif key not in after_items:
            changes.append({"kind": "item_removed", "before": before_items[key],
                            "after": None})
        else:
            changes.append({"kind": "item_changed",
                            "before": before_items[key],
                            "after": after_items[key]})
    return changes
