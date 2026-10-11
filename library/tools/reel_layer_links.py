"""Cascade Lane C: the reel layer's foreign keys into the Project Model.

The captain's diagnosis (data/vep-linked-model-cascade-plan/report.md in
firstmate's home): the footage layer is already a linked model in
practice, while the reel layer is keyed on master-timeline seconds and
reel-name strings with zero links into the footage layer's model ids.
This module is Lane C of that plan's section 5:

- C1: manifest rows carry ``clip_use_id`` + ``edit_ids[]`` + ``effect_ids[]``;
  bed segments + motion-graphics/timed-text entries carry ``clip_use_id`` /
  ``effect_id``; subtitle sidecars carry ``clip_use_id`` + ``caption_binding``.
- C2: exports write ``Render`` + ``Receipt`` + ``FrameMap`` (extending the
  Unit 7 ingest); hearing verdicts become receipt QA fields.
- C3: pulls resolve the Resolve anchor to ``clip_use_id`` through the
  current frame map at pull time; channel notes gain ``note_id`` at queue.

Lane discipline (the report's section 5 constraint): the in-flight
``vep-reel-build-writes-project-model`` lane owns store writes. This lane
adds FK columns and READS the model; it mints no model row, revises no
store, and changes no placement mechanics. Every function here is pure
(dicts in, dicts out) and fail-closed: a link that cannot be resolved is
reported with its reason, never guessed at, and no caller fails its own
duty when the model is absent.

Time bases: the model speaks integer millisecond ticks on named clocks;
manifests, pulls and beds speak float seconds. Matching converts model
ticks to seconds (``tick / 1000.0``) and compares in seconds with a 1ms
epsilon, so neither side is re-quantised into the other's grid.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping

LINK_VERSION = 1
"""Version of the link columns this module stamps. Bumped only when a
reader must parse the columns differently."""

EPSILON_SECONDS = 0.001
"""Two spans that agree within a millisecond agree. Below this is float
noise between two clocks that both measured the same cut."""


def _slug(text: str) -> str:
    """The reel slug ``reel_project_model`` mints clip uses under.

    Repeated here (same regex) so this module stays import-light for
    broker script scopes: ``use:<slug>:<i>`` and ``map:<slug>`` are the
    only model ids this lane resolves, and both namespace on this.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return cleaned or "reel"


def slug_for_timeline(timeline_name: str) -> str:
    """The model namespace slug for a Resolve timeline name."""
    return _slug(timeline_name)


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _ms_to_seconds(tick: Any) -> float | None:
    """A model millisecond tick as seconds, or None when not a number."""
    if isinstance(tick, bool) or not isinstance(tick, (int, float)):
        return None
    if not math.isfinite(float(tick)):
        return None
    return float(tick) / 1000.0


def _span_seconds(start: Any, end: Any) -> tuple[float, float] | None:
    """A float-second span, or None when refused.

    Refuses non-numeric bounds and non-positive spans: a zero-length
    span cannot be overlapped, and widening it would invent evidence.
    """
    if not _is_number(start) or not _is_number(end):
        return None
    start_f, end_f = float(start), float(end)
    if end_f <= start_f:
        return None
    return (start_f, end_f)


def _overlap(first: tuple[float, float],
             second: tuple[float, float]) -> float:
    return max(0.0, min(first[1], second[1]) - max(first[0], second[0]))


def _asset_id_by_uri(model: Mapping[str, Any]) -> dict[str, str]:
    """Model asset id by URI, first write wins."""
    out: dict[str, str] = {}
    for asset in (model.get("assets") or []):
        if not isinstance(asset, dict):
            continue
        uri = asset.get("uri")
        asset_id = asset.get("id")
        if isinstance(uri, str) and uri and isinstance(asset_id, str):
            out.setdefault(uri, asset_id)
    return out


def _asset_id_for_source(source_file: str,
                         by_uri: Mapping[str, str]) -> str | None:
    """The model asset id for a placed source file, never guessed.

    Exact URI equality first; a basename match second (the manifest
    may carry a relocated path for the same file). A basename that
    matches more than one asset is ambiguous and resolves to None:
    two files with one name are two files.
    """
    if not source_file:
        return None
    if source_file in by_uri:
        return by_uri[source_file]
    stem = Path(str(source_file)).name
    hits = {asset_id for uri, asset_id in by_uri.items()
            if Path(uri).name == stem}
    if len(hits) == 1:
        return next(iter(hits))
    return None


def edits_targeting(model: Mapping[str, Any],
                    clip_use_id: str) -> list[str]:
    """Every model edit whose ``target_ids`` name this clip use."""
    out: list[str] = []
    for edit in (model.get("edits") or []):
        if not isinstance(edit, dict):
            continue
        targets = edit.get("target_ids") or []
        if clip_use_id in targets and isinstance(edit.get("id"), str):
            out.append(edit["id"])
    return sorted(out)


def effects_on(model: Mapping[str, Any], clip_use_id: str) -> list[str]:
    """Every model effect whose ``target_id`` is this clip use."""
    out: list[str] = []
    for effect in (model.get("effects") or []):
        if (isinstance(effect, dict)
                and effect.get("target_id") == clip_use_id
                and isinstance(effect.get("id"), str)):
            out.append(effect["id"])
    return sorted(out)


def _use_source_span(use: Mapping[str, Any]) -> tuple[float, float] | None:
    interval = use.get("source_interval") if isinstance(use, Mapping) else None
    if not isinstance(interval, Mapping):
        return None
    start = _ms_to_seconds(interval.get("start"))
    end = _ms_to_seconds(interval.get("end"))
    if start is None or end is None or end <= start:
        return None
    return (start, end)


def _use_timeline_span(use: Mapping[str, Any]) -> tuple[float, float] | None:
    interval = use.get("timeline_interval") if isinstance(use, Mapping) else None
    if not isinstance(interval, Mapping):
        return None
    start = _ms_to_seconds(interval.get("start"))
    end = _ms_to_seconds(interval.get("end"))
    if start is None or end is None or end <= start:
        return None
    return (start, end)


def match_clip_use(*, source_file: str,
                   source_start: Any, source_end: Any,
                   timeline_start: Any = None, timeline_end: Any = None,
                   timeline_clock: str | None = None,
                   model: Mapping[str, Any],
                   reel_slug: str | None = None) -> tuple[str | None, str]:
    """The model clip use one placed row plays, or (None, reason).

    The join is on the SOURCE: the asset whose URI (or unambiguous
    basename) equals ``source_file``, and the use whose source interval
    overlaps the row's source span most. The timeline span only narrows
    the match when ``timeline_clock`` names the same reel clock the use
    ticks on; across timelines (an episode manifest against reel-built
    uses) the clocks differ and the basis is the source alone, said as
    ``"source"`` versus ``"source+timeline"``. Ties break toward the
    longer source overlap; no overlap resolves nothing.
    """
    if not isinstance(model, Mapping):
        return None, "no model to link against"
    uses = [row for row in (model.get("clip_uses") or [])
            if isinstance(row, dict)]
    if reel_slug:
        uses = [row for row in uses
                if str(row.get("id", "")).startswith(f"use:{reel_slug}:")]
    if not uses:
        scope = f" for reel {reel_slug!r}" if reel_slug else ""
        return None, f"the model carries no clip uses{scope}"
    asset_id = _asset_id_for_source(str(source_file or ""),
                                    _asset_id_by_uri(model))
    if asset_id is None:
        return None, (f"source {str(source_file or '')!r} names no model "
                      f"asset: refusing, not guessing")
    want_source = _span_seconds(source_start, source_end)
    if want_source is None:
        return None, "the row names no positive source span"
    want_timeline = _span_seconds(timeline_start, timeline_end)
    best_id: str | None = None
    best_overlap = 0.0
    best_basis = "source"
    for use in uses:
        if use.get("asset_id") != asset_id:
            continue
        have_source = _use_source_span(use)
        if have_source is None:
            continue
        source_hit = _overlap(want_source, have_source)
        if source_hit <= 0.0:
            continue
        basis = "source"
        if (want_timeline is not None and timeline_clock is not None):
            interval = use.get("timeline_interval")
            if (isinstance(interval, Mapping)
                    and interval.get("clock_id") == timeline_clock):
                have_timeline = _use_timeline_span(use)
                if have_timeline is not None:
                    if _overlap(want_timeline, have_timeline) <= 0.0:
                        continue
                    basis = "source+timeline"
        if source_hit > best_overlap:
            best_overlap = source_hit
            best_id = use.get("id") if isinstance(use.get("id"), str) else None
            best_basis = basis
    if best_id is None:
        return None, (f"no {asset_id!r} use overlaps source "
                      f"{want_source[0]:.3f}-{want_source[1]:.3f}s")
    return best_id, f"matched by {best_basis} ({best_overlap:.3f}s of source)"


def link_placed_row(row: Mapping[str, Any],
                    model: Mapping[str, Any],
                    reel_slug: str | None = None) -> tuple[dict[str, Any], str]:
    """One placed row carrying its model ids, plus the basis or reason.

    Returns ``(linked_row, basis_or_reason)``. A resolved row carries
    ``clip_use_id`` (str), ``edit_ids`` (list) and ``effect_ids``
    (list); an unresolved row carries the same keys with ``clip_use_id``
    None and empty id lists, so a reader can tell "linked to nothing"
    from "never linked". The input mapping is never mutated.
    """
    if not isinstance(row, dict):
        return ({"clip_use_id": None, "edit_ids": [],
                 "effect_ids": []}, "not a mapping")
    use_id, basis = match_clip_use(
        source_file=str(row.get("source_file") or ""),
        source_start=row.get("source_in", row.get("source_start")),
        source_end=row.get("source_out", row.get("source_end")),
        timeline_start=row.get("timeline_in",
                               row.get("timeline_start")),
        timeline_end=row.get("timeline_out", row.get("timeline_end")),
        timeline_clock=row.get("timeline_clock"),
        model=model, reel_slug=reel_slug)
    linked = dict(row)
    if use_id is None:
        linked["clip_use_id"] = None
        linked["edit_ids"] = []
        linked["effect_ids"] = []
        return linked, basis
    linked["clip_use_id"] = use_id
    linked["edit_ids"] = edits_targeting(model, use_id)
    linked["effect_ids"] = effects_on(model, use_id)
    return linked, basis


def link_manifest(manifest: Mapping[str, Any],
                  model: Mapping[str, Any] | None,
                  reel_slug: str | None = None) -> tuple[dict[str, Any], dict]:
    """An assembly manifest whose track clips carry C1 link columns.

    Every clip on every track gains ``clip_use_id`` + ``edit_ids`` +
    ``effect_ids`` (None/empty where unresolvable, with the reason in
    the ``_model_links`` summary). A-roll bed clips on A2 and SFX on A3
    link by source like picture: the bed's own ``clip_use_id`` is the
    use playing that source range, or an honest absence when the model
    mints no music use. ``_model_links`` records the link version, the
    reel scope, and per-row skips; it is the reader's index, not a
    second copy of the keys. Never raises on content: an absent model
    links every row to nothing and says so.
    """
    linked_manifest = {key: value for key, value in dict(manifest).items()}
    report: dict[str, Any] = {"version": LINK_VERSION,
                              "reel_slug": reel_slug,
                              "linked": 0, "skipped": [],
                              "model_absent": model is None}
    tracks = manifest.get("tracks") if isinstance(manifest, Mapping) else None
    if not isinstance(tracks, dict):
        report["skipped"].append({"track": None,
                                  "reason": "manifest carries no tracks"})
        linked_manifest["_model_links"] = report
        return linked_manifest, report
    new_tracks: dict[str, Any] = {}
    for track_name, track in tracks.items():
        if not isinstance(track, dict):
            new_tracks[track_name] = track
            continue
        clips = track.get("clips")
        if not isinstance(clips, list):
            new_tracks[track_name] = track
            continue
        new_clips = []
        for index, clip in enumerate(clips):
            if model is None:
                row = dict(clip) if isinstance(clip, dict) else {}
                row["clip_use_id"] = None
                row["edit_ids"] = []
                row["effect_ids"] = []
                new_clips.append(row)
                report["skipped"].append(
                    {"track": track_name, "index": index,
                     "label": row.get("label", "?"),
                     "reason": "no model to link against"})
                continue
            row, basis = link_placed_row(clip, model, reel_slug)
            new_clips.append(row)
            if row.get("clip_use_id") is None:
                report["skipped"].append(
                    {"track": track_name, "index": index,
                     "label": row.get("label", "?"), "reason": basis})
            else:
                report["linked"] += 1
        new_track = dict(track)
        new_track["clips"] = new_clips
        new_tracks[track_name] = new_track
    linked_manifest["tracks"] = new_tracks
    linked_manifest["_model_links"] = report
    return linked_manifest, report


def link_overlay_entries(entries: Any,
                         model: Mapping[str, Any] | None,
                         reel_slug: str | None = None) -> tuple[list, dict]:
    """Motion-graphics / timed-text entries carrying their ``effect_id``.

    An entry lands on the model effect whose target clip use covers the
    entry's span: timed-text segments name a master span
    (``timeline_start``/``timeline_end``) and match the ``title.text``
    effect whose parameters name the overlapping master span; other
    entries match any effect whose timeline interval overlaps theirs.
    Unmatched entries keep ``effect_id`` None with the reason in the
    report. Never raises on content.
    """
    report: dict[str, Any] = {"version": LINK_VERSION,
                              "reel_slug": reel_slug,
                              "linked": 0, "skipped": []}
    if not isinstance(entries, list):
        report["skipped"].append({"reason": "entries are not a list"})
        return [], report
    if model is None:
        out = []
        for entry in entries:
            row = dict(entry) if isinstance(entry, dict) else {}
            row["effect_id"] = None
            out.append(row)
            report["skipped"].append({"reason": "no model to link against"})
        return out, report
    effects = [row for row in (model.get("effects") or [])
               if isinstance(row, dict)]
    if reel_slug:
        effects = [row for row in effects
                   if str(row.get("id", "")).startswith(f"effect:{reel_slug}:")]
    out = []
    for index, entry in enumerate(entries):
        row = dict(entry) if isinstance(entry, dict) else {}
        if not isinstance(entry, dict):
            row["effect_id"] = None
            report["skipped"].append({"index": index,
                                      "reason": "not a mapping"})
            out.append(row)
            continue
        want = _span_seconds(entry.get("timeline_start",
                                       entry.get("start_seconds")),
                             entry.get("timeline_end",
                                       entry.get("end_seconds")))
        hit: str | None = None
        basis = ""
        if want is not None:
            best = 0.0
            for effect in effects:
                params = effect.get("parameters") or {}
                master = _span_seconds(params.get("master_start"),
                                       params.get("master_end")) \
                    if isinstance(params, Mapping) else None
                if master is not None:
                    span = _overlap(want, master)
                    if span > best:
                        best = span
                        hit = effect.get("id")
                        basis = f"master span ({span:.3f}s)"
                    continue
                interval = effect.get("interval") \
                    if isinstance(effect, Mapping) else None
                if not isinstance(interval, Mapping):
                    continue
                have = _span_seconds(_ms_to_seconds(interval.get("start")),
                                     _ms_to_seconds(interval.get("end")))
                if have is None:
                    continue
                span = _overlap(want, have)
                if span > best:
                    best = span
                    hit = effect.get("id")
                    basis = f"effect interval ({span:.3f}s)"
        if hit is None:
            row["effect_id"] = None
            report["skipped"].append(
                {"index": index,
                 "reason": ("the entry names no positive span" if want is None
                            else "no model effect overlaps the entry span")})
        else:
            row["effect_id"] = hit
            report["linked"] += 1
        out.append(row)
    return out, report


def subtitle_sidecar_link(sidecar: Mapping[str, Any],
                          clip_use_id: str | None = None,
                          caption_binding: str | None = None) -> dict[str, Any]:
    """A subtitle sidecar carrying ``clip_use_id`` + ``caption_binding``.

    ``caption_binding`` defaults to the sidecar's own content key
    (``content_key``, else ``reuse_key``): the binding is the cascade
    hash for captions and is READ here, never recomputed. An absent key
    on both sides stays absent and is said by the caller, not filled.
    """
    if not isinstance(sidecar, dict):
        return {"clip_use_id": clip_use_id, "caption_binding": caption_binding}
    linked = dict(sidecar)
    linked["clip_use_id"] = clip_use_id
    if caption_binding is not None:
        linked["caption_binding"] = caption_binding
    else:
        for key in ("content_key", "reuse_key"):
            value = sidecar.get(key)
            if isinstance(value, str) and value:
                linked["caption_binding"] = value
                break
        else:
            linked["caption_binding"] = None
    return linked


def hearing_qa_fields(hearing: Any) -> tuple[dict[str, Any], str]:
    """The receipt QA fields one hearing verdict becomes, plus a note.

    Accepts a ``reel_hearing.Hearing`` or the plain dict its record
    file carries (``write_record``). Returns ``(fields, note)`` where
    ``fields`` holds ``passed`` (bool|None), the per-metric findings a
    ``refusal`` names, and the hearing id when the record carries one.
    Never raises on content: an unreadable hearing is empty fields with
    the reason, and the caller records that instead of failing.
    """
    if hearing is None:
        return {}, "no hearing record to read"
    if hasattr(hearing, "operations") or hasattr(hearing, "planned"):
        to_dict = getattr(hearing, "to_dict", None)
        record = to_dict() if callable(to_dict) else dict(hearing.__dict__)
    elif isinstance(hearing, Mapping):
        record = dict(hearing)
    else:
        return {}, f"a hearing record must be a mapping, got {type(hearing).__name__}"
    fields: dict[str, Any] = {}
    for key in ("passed", "refusal", "hearing_id", "video_path",
                "script_divergence", "coverage", "drift", "pairing",
                "row_fit", "gates"):
        if key in record:
            fields[key] = record[key]
    findings = record.get("findings")
    if isinstance(findings, list) and findings and "passed" not in fields:
        fields["passed"] = all(bool(item.get("passed", True))
                               for item in findings
                               if isinstance(item, Mapping))
    return fields, "read"


def attach_hearing_qa(model: Mapping[str, Any], reel_slug: str,
                      hearing: Any) -> tuple[dict[str, Any], dict]:
    """A model copy whose receipt carries the hearing verdicts as QA.

    Writes ``details.qa.hearing`` on ``receipt:<slug>`` (creating
    ``details.qa`` beside whatever ``details`` already carries, never
    replacing it). Refuses with the reason when the receipt is absent -
    the render is recorded first, the hearing second - and when the
    hearing itself is unreadable. The input model is never mutated.
    """
    import copy
    report: dict[str, Any] = {"reel_slug": reel_slug, "attached": False,
                              "reason": ""}
    if not isinstance(model, Mapping):
        report["reason"] = "no model to attach to"
        return {}, report
    receipt_id = f"receipt:{reel_slug}"
    receipts = model.get("receipts") or []
    if not any(isinstance(row, dict) and row.get("id") == receipt_id
               for row in receipts):
        report["reason"] = (f"{receipt_id!r} is not in the model: "
                            f"record the deliver before its hearing")
        return dict(model), report
    fields, note = hearing_qa_fields(hearing)
    if not fields:
        report["reason"] = f"the hearing record is unreadable: {note}"
        return dict(model), report
    updated = copy.deepcopy(dict(model))
    for row in updated.get("receipts") or []:
        if isinstance(row, dict) and row.get("id") == receipt_id:
            details = dict(row.get("details") or {})
            qa = dict(details.get("qa") or {})
            qa["hearing"] = fields
            details["qa"] = qa
            row["details"] = details
            break
    report["attached"] = True
    report["reason"] = note
    report["fields"] = sorted(fields)
    return updated, report


def _frame_map_for(model: Mapping[str, Any],
                   reel_slug: str) -> tuple[dict | None, str]:
    """The source-to-reel frame map for one reel, or (None, reason)."""
    if not isinstance(model, Mapping):
        return None, "no model to resolve against"
    map_id = f"map:{reel_slug}"
    for row in (model.get("frame_maps") or []):
        if isinstance(row, dict) and row.get("id") == map_id:
            return row, "found"
    return None, (f"{map_id!r} is not in the model: "
                  f"build the reel before resolving anchors on it")


def resolve_clip_use_at_seconds(model: Mapping[str, Any] | None,
                                reel_slug: str,
                                seconds: Any) -> tuple[str | None, str]:
    """The ``clip_use_id`` playing one reel-timeline second, or (None, reason).

    Walks the reel's source-to-reel frame map (``map:<slug>``) ``to``
    intervals: the second is converted to the map's own millisecond
    ticks and the segment containing it answers with its
    ``source_clip_use``. A second on a segment boundary answers with
    the segment starting there. Out-of-range, unmapped and model-absent
    anchors are reasons, never guesses.
    """
    if model is None:
        return None, "no model to resolve against"
    if not _is_number(seconds):
        return None, f"an anchor second must be a number, got {seconds!r}"
    tick = int(math.floor(float(seconds) * 1000.0))
    frame_map, reason = _frame_map_for(model, reel_slug)
    if frame_map is None:
        return None, reason
    segments = frame_map.get("segments") or []
    if not isinstance(segments, list) or not segments:
        return None, f"{frame_map.get('id')!r} carries no segments"
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        to = segment.get("to") if isinstance(segment, Mapping) else None
        if not isinstance(to, Mapping):
            continue
        start, end = to.get("start"), to.get("end")
        if (isinstance(start, int) and isinstance(end, int)
                and start <= tick < end):
            use_id = segment.get("source_clip_use")
            if isinstance(use_id, str) and use_id:
                return use_id, f"segment {start}-{end}ms of {frame_map['id']!r}"
            return None, "the covering segment names no clip use"
    return None, (f"{float(seconds):.3f}s is on no {frame_map['id']!r} "
                  f"segment: refusing, not guessing")


def annotate_pulled_note(note: Mapping[str, Any],
                         model: Mapping[str, Any] | None,
                         reel_slug: str | None = None,
                         timeline_fps: float = 0.0,
                         timeline_start_frame: int = 0) -> dict[str, Any]:
    """One pulled note carrying its anchor's ``clip_use_id``.

    The anchor second is derived from the note's own ``frame`` through
    the pull file's ``timeline_fps``/``timeline_start_frame`` (the one
    conversion ``marker_routing.note_span_seconds`` owns): an
    unplaced note, or a pull that predates the rate record, resolves to
    nothing with the reason. The reel scope defaults to the note's own
    ``timeline`` slugged; an explicit ``reel_slug`` wins. The input is
    never mutated; the note's ``note_id`` travels untouched.
    """
    out = dict(note) if isinstance(note, dict) else {}
    slug = reel_slug or slug_for_timeline(str(note.get("timeline") or ""))
    frame = note.get("frame") if isinstance(note, dict) else None
    if (not isinstance(frame, int) or isinstance(frame, bool)
            or not _is_number(timeline_fps) or float(timeline_fps) <= 0):
        out["clip_use_id"] = None
        out["clip_use_reason"] = ("the note has no placed frame or the pull "
                                  "carries no timeline rate")
        return out
    seconds = (int(frame) - int(timeline_start_frame or 0)) / float(timeline_fps)
    use_id, reason = resolve_clip_use_at_seconds(model, slug, seconds)
    out["clip_use_id"] = use_id
    out["clip_use_reason"] = reason
    return out
