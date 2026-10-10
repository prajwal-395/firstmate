"""Model ids stamped onto Resolve markers (cascade Lane A3).

The captain's constraint: "there needs to be some kind of unifying thread
that connects everything together so that things that get changed are
cascaded down". The Project Model holds identity; the Resolve side holds
the placed picture. This module is the thread's Resolve end: it stamps
each placed clip's model ids (`asset_id`, `clip_use_id`) into the
`customData` envelope of the timeline marker at that clip, through
`marker_payload.merge_record` - the envelope's third writer, beside
`marker_capture` (stills) and `timeline_decisions` (decisions).

What it does and does not do
----------------------------
- Records only. It never places, moves, or retimes anything: placement
  mechanics are untouched, and a marker that is not already on the
  timeline is never created - `AddMarker` would draw on the timeline
  ruler, a visible change for a payload the captain cannot see (the
  same reason `timeline_decisions.stamp_timeline` only ever updates).
  A link naming a frame with no marker is REPORTED as skipped.
- Timeline markers only. `TimelineItem.UpdateMarkerCustomData` has not
  been measured against a running Resolve here and `hasattr` proves
  nothing about it (AGENTS.md 5), so per-clip markers are not stamped;
  per-clip linkage at clip granularity is the `NleBinding`'s
  `clip_bindings` map (`clip_use_id` -> Resolve `unique_id`), built by
  `build_nle_binding` below as a pure dict the owning store writer
  commits. This module writes no model revision itself: PR 2271 owns
  store writes, and this lane builds on its revision API without adding
  a second writer.
- Resolve access runs inside a held lease. `stamp_timeline_markers`
  takes a live timeline handle and makes no lease of its own: the
  broker qualification session holds the instance lease for the whole
  session, and a second acquisition inside it would contend with the
  session itself. Direct callers wrap it in `under_lease` themselves.

The record
----------
One `model_link` record per placed clip, with a deterministic id so
re-stamping REPLACES rather than accumulates:

    {
      "kind": "model_link",
      "writer": "model_stamps",
      "writer_version": 1,
      "id": "model_link_<clip_use_id>",
      "at": "2026-10-10T...",
      "asset_id": "asset-...",
      "clip_use_id": "clip_use:...",
      "revision": 7,                # the model revision these ids are from
    }

`revision` names the build's revision the ids were read from, so a
reader can tell a stamp from an older build apart from the current
one. Empty ids are REFUSED, never guessed at: a stamp that names the
wrong asset misroutes every cascade walk that follows it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from library.tools import marker_payload

KIND_MODEL_LINK = "model_link"
"""The `marker_payload` record kind this module writes."""

WRITER = "model_stamps"
WRITER_VERSION = 1


def _require_id(name: str, value) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"a model_link record needs a non-empty {name}, not {value!r}: "
            "refusing, not guessing")
    return value


def record_id_for(clip_use_id: str) -> str:
    """The deterministic record id for one clip use.

    Derived from the clip use alone, so stamping the same clip twice
    replaces the earlier record through `merge_record` instead of
    growing the `customData` string.
    """
    _require_id("clip_use_id", clip_use_id)
    return f"{KIND_MODEL_LINK}_{clip_use_id}"


def model_link_record(*, asset_id: str, clip_use_id: str,
                      revision: int | None = None,
                      timeline: str = "",
                      track: str = "",
                      label: str = "",
                      record_id: str = "") -> dict:
    """One `marker_payload` record: the model ids of one placed clip.

    `revision` is the model revision the ids were read from (the
    build's revision). It travels for traceability only: the stamp is
    still just a statement about what the ids were when the marker was
    stamped, and the shadow generation row is what joins a revision to
    a timeline state.
    """
    _require_id("asset_id", asset_id)
    _require_id("clip_use_id", clip_use_id)
    if revision is not None and (
            not isinstance(revision, int) or isinstance(revision, bool)
            or revision < 1):
        raise ValueError(
            f"a model_link revision must be a model revision (>= 1), "
            f"not {revision!r}")
    record = {
        "kind": KIND_MODEL_LINK,
        "writer": WRITER,
        "writer_version": WRITER_VERSION,
        "id": record_id or record_id_for(clip_use_id),
        "at": marker_payload.utc_now(),
        "asset_id": asset_id,
        "clip_use_id": clip_use_id,
    }
    if revision is not None:
        record["revision"] = revision
    if timeline:
        record["timeline"] = timeline
    if track:
        record["track"] = track
    if label:
        record["label"] = label
    return record


def model_links_of(envelope: dict) -> list:
    """Every model_link record in an envelope, in write order."""
    return marker_payload.records_of(envelope or {}, KIND_MODEL_LINK)


def model_link_for(envelope: dict, clip_use_id: str) -> dict | None:
    """The model_link record for one clip use, or None.

    Returns None rather than a best fit: two records for one use is a
    real divergence the caller reports, never one it picks between.
    """
    hits = [record for record in model_links_of(envelope)
            if record.get("clip_use_id") == clip_use_id]
    if len(hits) == 1:
        return hits[0]
    return None


def stamp_envelope(envelope: dict, records: list) -> dict:
    """Merge model_link records into an envelope. Destroys nothing.

    Every other writer's records, and any `foreign` payload, are
    untouched: `merge_record` replaces only the record carrying the
    same id and appends otherwise.
    """
    for record in records:
        if record.get("kind") != KIND_MODEL_LINK:
            raise ValueError(
                f"stamp_envelope only merges {KIND_MODEL_LINK!r} records, "
                f"not {record.get('kind')!r}")
        marker_payload.merge_record(envelope, record)
    return envelope


@dataclass
class StampReport:
    """What stamping a running timeline did, and to what."""

    timeline: str = ""
    markers_seen: int = 0
    markers_stamped: int = 0
    records_written: int = 0
    skipped: list = field(default_factory=list)
    refused: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{self.timeline or '(unnamed timeline)'}: "
            f"{self.markers_stamped}/{self.markers_seen} markers stamped, "
            f"{self.records_written} model_link records"
        ]
        for entry in self.skipped:
            lines.append(f"  · frame {entry['frame']}: {entry['reason']}")
        for entry in self.refused:
            lines.append(f"  ✗ frame {entry['frame']}: {entry['reason']}")
        return "\n".join(lines)


def stamp_timeline_markers(timeline, links: list) -> StampReport:
    """Stamp model ids onto the markers ALREADY on `timeline`. Creates none.

    `links` is one dict per placed clip: `frame` (the timeline marker
    at that clip), `asset_id`, `clip_use_id`, and optionally
    `revision`. A link naming a frame with no marker is skipped and
    said so - stamping must never draw a marker the captain did not
    ask for. A marker's visible fields are never passed and never
    touched: the only call made is `UpdateMarkerCustomData`.

    The caller holds the Resolve lease (the broker qualification
    session holds it for the whole session); this function takes none.
    """
    report = StampReport(timeline=timeline.GetName() or "")
    markers = timeline.GetMarkers() or {}
    report.markers_seen = len(markers)
    # Real Resolve keys this dict either way; every reader here goes
    # through int(), so the lookup does too - an int frame must find a
    # marker however the handle spelled its key.
    by_frame: dict = {}
    for key in markers:
        try:
            by_frame[int(key)] = key
        except (TypeError, ValueError):
            continue
    wanted: dict = {}
    for link in links:
        if not isinstance(link, Mapping):
            raise ValueError(f"a stamp link must be a mapping: {link!r}")
        frame = link.get("frame")
        if not isinstance(frame, int) or isinstance(frame, bool):
            raise ValueError(
                f"a stamp link needs an integer timeline frame: {link!r}")
        wanted.setdefault(frame, []).append(link)
    for frame, links_at_frame in sorted(wanted.items()):
        marker_key = by_frame.get(frame)
        marker = markers.get(marker_key) if marker_key is not None else None
        if marker is None:
            for link in links_at_frame:
                report.skipped.append({
                    "frame": frame,
                    "clip_use_id": link.get("clip_use_id", ""),
                    "reason": "no marker is on this frame; refusing to "
                              "create one for a machine payload",
                })
            continue
        envelope = marker_payload.parse(marker.get("customData") or "")
        records = [
            model_link_record(
                asset_id=link["asset_id"],
                clip_use_id=link["clip_use_id"],
                revision=link.get("revision"),
                timeline=report.timeline,
                track=link.get("track", ""),
                label=link.get("label", ""))
            for link in links_at_frame
        ]
        stamp_envelope(envelope, records)
        if not timeline.UpdateMarkerCustomData(
                int(frame), marker_payload.dumps(envelope)):
            report.refused.append({
                "frame": frame,
                "reason": "Resolve returned False for "
                          "UpdateMarkerCustomData; the marker is unchanged",
            })
            continue
        report.markers_stamped += 1
        report.records_written += len(records)
    return report


# ── The NLE binding: per-clip linkage without per-clip customData ──

def _stable_id(prefix: str, *parts) -> str:
    """A deterministic `<prefix>:<sha16>` over canonical JSON parts.

    The same algorithm as `project_model_editorial.stable_id`, repeated
    here so this module stays dependency-free (stdlib plus
    `marker_payload`): a binding id must be computable wherever a stamp
    is, including inside a broker script scope.
    """
    canonical = "\x00".join(
        json.dumps(part, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False)
        for part in parts
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def build_nle_binding(*, timeline_id: str, timeline_identity: str,
                      project_identity: str, generation: int,
                      clip_bindings: dict,
                      system: str = "resolve",
                      binding_id: str = "",
                      external_bindings: dict | None = None) -> dict:
    """The pure `NleBinding` dict joining clip uses to Resolve items.

    `clip_bindings` maps each placed `clip_use_id` to the Resolve
    timeline item `unique_id` playing it - the per-clip half of Lane
    A3 that timeline markers cannot carry (clip-marker `customData`
    writes are unmeasured). `timeline_id` is the shadow store's
    timeline identity (the `GetUniqueId`); `timeline_identity` is the
    exact timeline name read back; `project_identity` the exact
    Resolve project name; `generation` the shadow generation number
    this binding was verified against.

    Pure: it validates and returns the dict the owning store writer
    commits (one revision per build, PR 2271). It writes no revision
    itself. An empty binding is refused: a binding that binds nothing
    is not a binding.
    """
    _require_id("timeline_id", timeline_id)
    _require_id("timeline_identity", timeline_identity)
    _require_id("project_identity", project_identity)
    if not isinstance(system, str) or not system:
        raise ValueError("an NLE binding needs a non-empty system name")
    if (not isinstance(generation, int) or isinstance(generation, bool)
            or generation < 1):
        raise ValueError(
            f"an NLE binding needs the shadow generation (>= 1), "
            f"not {generation!r}")
    if not isinstance(clip_bindings, dict) or not clip_bindings:
        raise ValueError(
            "an NLE binding needs at least one clip_use_id -> unique_id "
            "entry: refusing an empty binding")
    for use_id, unique_id in clip_bindings.items():
        _require_id("clip_use_id", use_id)
        if not isinstance(unique_id, str) or not unique_id:
            raise ValueError(
                f"clip use {use_id!r} needs a non-empty Resolve unique_id, "
                f"not {unique_id!r}: refusing, not guessing")
    if external_bindings is None:
        external_bindings = {}
    if not isinstance(external_bindings, dict):
        raise ValueError("external_bindings must be a mapping")
    return {
        "id": binding_id or _stable_id(
            "nle", timeline_id, generation, clip_bindings),
        "timeline_id": timeline_id,
        "system": system,
        "project_identity": project_identity,
        "timeline_identity": timeline_identity,
        "generation": generation,
        "clip_bindings": dict(clip_bindings),
        "external_bindings": dict(external_bindings),
    }
