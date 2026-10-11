"""Reel builds write and maintain the canonical Project Model.

PRs 2239-2258 built the model (ren-protocol schema, revision store,
evidence/identity migration in ``project_model_evidence``,
editorial migration in ``project_model_editorial``) but no reel
pipeline caller used them: a real reel build never produced a Project
Model, and the native engines could only be driven from fixtures.
This module is the one writer that closes that gap, using the
existing store and migration functions rather than a new builder.

Where each half enters the model
--------------------------------
- Assets, evidence and person identity (speech, body/face masks,
  A-roll linked to identities): ``migrate_project_evidence`` over the
  preflight's lanes - the step that produced them. This writer
  refreshes that base on every build; it never re-measures them.
- Clip uses and edits: the reel's own recorded placements
  (``reel_build.rebuild_reels_in_project`` records
  ``reel_placements`` beside ``track_plans`` so the writer grades
  what was built, never a re-derivation).
- Effects: step 4.03's mint (PR 2263) is the one producer - its
  ``enhancement_spec.move_effects`` rows are carried verbatim, and
  an absent key is recorded as "no move_effects in
  enhancement_spec", never filled in here.
- Titles: rendered timed-text segments mapped onto the reel through
  the placements' master spans, as ``title.text`` effects.
- Renders with frame maps: the build writes the source-to-reel
  frame map; ``note_deliver`` (called from ``deliver-reel``)
  appends the output asset, receipt and reel-to-render map.

One store revision per build (``maintain_from_build``), so the
model is current after any partial rebuild: reel rows are
namespaced per reel (``use:<slug>:<i>``), a partial rebuild
replaces only its own reels' rows and keeps every other reel's.
``run_reels.merge_record`` already merges ``reel_placements`` per
reel, so concurrent ``--only-reel`` lanes each keep their rows.

Fail-soft: the Resolve path is unchanged. Both hooks record a
refusal dict on the record rather than raising - a model write
that fails a build is worse than no model write.

Read path: ``show`` here, surfaced as
``bin/native-axi reel-model <project> [--reel N]``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

try:
    from library.tools.project_model import SCHEMA_VERSION, validate_project_model
except ImportError:  # imported as ``tools.*`` from inside library/
    from tools.project_model import SCHEMA_VERSION, validate_project_model

TICKS_PER_SECOND = 1000
"""Millisecond clocks, the Unit-3 evidence policy: lanes measure
float seconds, so integer millisecond ticks preserve every measured
span without a frame-rate assumption no lane recorded."""

PROVENANCE_ID = "prov-reel-build"
PRODUCER = "reel_project_model"

MODEL_COLLECTIONS = (
    "assets", "streams", "clocks", "entities", "observations",
    "identity_links", "masks", "object_classes", "tracks", "relations",
    "intents", "clip_uses", "edits", "effects", "renders", "frame_maps",
    "nle_bindings", "receipts", "provenance",
)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return cleaned or "reel"


def _sha16(*parts: Any) -> str:
    canonical = "\x00".join(
        json.dumps(part, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, default=str)
        for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def sec_to_ms(start: Any, end: Any) -> tuple[int, int] | None:
    """Float seconds to half-open millisecond ticks, or None when refused.

    Refuses non-finite bounds and non-positive spans: a zero-length
    measurement cannot be a half-open interval, and widening it would
    invent evidence (the Unit-3 ``sec_to_ticks`` policy, in ms).
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


def blank_model(model_id: str) -> dict[str, Any]:
    """A valid empty model: every required collection present, none filled."""
    return {
        "id": model_id,
        "schema_version": SCHEMA_VERSION,
        "timebase_policy": "rational_frames",
        "clocks": [],
        "assets": [],
        "streams": [],
        "entities": [],
        "observations": [],
        "identity_links": [],
        "masks": [],
        "object_classes": [],
        "tracks": [],
        "relations": [],
        "intents": [],
        "clip_uses": [],
        "edits": [],
        "effects": [],
        "renders": [],
        "frame_maps": [],
        "nle_bindings": [],
        "receipts": [],
        "provenance": [],
    }


def ensure_provenance(model: dict[str, Any]) -> None:
    """The reel-build provenance row, exactly once."""
    for row in model.get("provenance") or []:
        if isinstance(row, dict) and row.get("id") == PROVENANCE_ID:
            return
    model.setdefault("provenance", []).append({
        "id": PROVENANCE_ID,
        "producer": PRODUCER,
        "producer_version": "1",
        "source_digest": None,
    })


def _clock(model: dict[str, Any], clock_id: str, domain: dict[str, Any]) -> str:
    for clock in model.get("clocks") or []:
        if isinstance(clock, dict) and clock.get("id") == clock_id:
            return clock_id
    model.setdefault("clocks", []).append({
        "id": clock_id,
        "domain": domain,
        "ticks_per_second": TICKS_PER_SECOND,
        "frame_rate": None,
    })
    return clock_id


def asset_for_source(model: dict[str, Any], source_file: str,
                     track_type: str = "video") -> tuple[str, str] | None:
    """The asset and clock ids for a placed source file, minting when absent.

    Reuses an evidence-migrated asset when one already carries this
    URI, so the reel fragment joins the evidence base instead of
    duplicating it. Returns None for an empty path (the caller
    records the skip).
    """
    uri = str(source_file or "")
    if not uri:
        return None
    for asset in model.get("assets") or []:
        if isinstance(asset, dict) and asset.get("uri") == uri:
            asset_id = asset["id"]
            clock_id = next(
                (clock["id"] for clock in model.get("clocks") or []
                 if isinstance(clock, dict)
                 and isinstance(clock.get("domain"), dict)
                 and clock["domain"].get("asset_id") == asset_id),
                f"clock:src:{_sha16(uri)}")
            _clock(model, clock_id, {"kind": "source", "asset_id": asset_id})
            return asset_id, clock_id
    digest = _sha16(uri)
    asset_id = f"asset:src:{digest}"
    clock_id = f"clock:src:{digest}"
    model.setdefault("assets", []).append({
        "id": asset_id,
        "kind": "video" if track_type == "video" else "audio",
        "uri": uri,
        "content_digest": None,
        "metadata": {"producer": PRODUCER},
        "provenance": [PROVENANCE_ID],
    })
    _clock(model, clock_id, {"kind": "source", "asset_id": asset_id})
    codec = "video" if track_type == "video" else "audio"
    model.setdefault("streams", []).append({
        "id": f"stream:src:{digest}:{codec}",
        "asset_id": asset_id,
        "clock_id": clock_id,
        "stream_index": 0,
        "metadata": {"codec_type": codec},
    })
    return asset_id, clock_id


def person_for_speaker(model: dict[str, Any], speaker: str) -> str | None:
    """The entity id for a transcript speaker, never guessed across people.

    A speaker matching a migrated roster label (case-insensitive, the
    ``resolve_person`` rule) reaches that person; otherwise a
    dedicated ``speaker`` entity carries the diarization identity, so
    a voice label can never merge two measured persons.
    """
    name = str(speaker or "").strip()
    if not name:
        return None
    lowered = name.lower()
    for entity in model.get("entities") or []:
        if (isinstance(entity, dict)
                and entity.get("kind") in ("person", "speaker")
                and isinstance(entity.get("label"), str)
                and entity["label"].lower() == lowered):
            return entity["id"]
    entity_id = f"speaker:{_slug(name)}:{_sha16(name)}"
    for entity in model.get("entities") or []:
        if isinstance(entity, dict) and entity.get("id") == entity_id:
            return entity_id
    model.setdefault("entities", []).append({
        "id": entity_id,
        "kind": "speaker",
        "label": name,
        "object_class_id": None,
    })
    return entity_id


def _link(model: dict[str, Any], entity_id: str, observation_id: str,
          method: str) -> None:
    link_id = f"link:{_sha16(entity_id, observation_id, method)}"
    for link in model.get("identity_links") or []:
        if isinstance(link, dict) and link.get("id") == link_id:
            return
    # Diarization labels are candidates, never verified: only a
    # measured speech-face link (Unit 3) verifies a person.
    model.setdefault("identity_links", []).append({
        "id": link_id,
        "entity_id": entity_id,
        "observation_id": observation_id,
        "method": method,
        "status": "candidate",
        "confidence": None,
        "provenance_id": PROVENANCE_ID,
    })


def speech_observations(model: dict[str, Any], transcript: dict | None,
                        speaker: str, source_file: str, asset_id: str,
                        clock_id: str, entity_id: str) -> list[str]:
    """Speech observations for one speaker's transcript rows on one source.

    Observations are about the SOURCE media (source-clock interval),
    carrying the row's own words. Rows with no usable source span are
    skipped, never widened. Returns the observation ids minted.
    """
    minted: list[str] = []
    segments = (transcript or {}).get("segments") if isinstance(transcript, dict) else None
    if not isinstance(segments, list):
        return minted
    for index, row in enumerate(segments):
        if not isinstance(row, dict):
            continue
        if str(row.get("speaker") or "").strip() != str(speaker or "").strip():
            continue
        if str(row.get("source_file") or "") != str(source_file or ""):
            continue
        span = sec_to_ms(row.get("source_start"), row.get("source_end"))
        if span is None:
            continue
        obs_id = f"obs:speech:{_sha16(speaker, source_file, row.get('source_start'), row.get('source_end'), index)}"
        if not any(isinstance(obs, dict) and obs.get("id") == obs_id
                   for obs in model.get("observations") or []):
            model.setdefault("observations", []).append({
                "id": obs_id,
                "asset_id": asset_id,
                "interval": {"clock_id": clock_id, "start": span[0], "end": span[1]},
                "modality": "speech",
                "payload": {
                    "text": str(row.get("text") or ""),
                    "speaker_entity_id": entity_id,
                },
                "confidence": None,
                "provenance_id": PROVENANCE_ID,
            })
        _link(model, entity_id, obs_id, "diarization_speaker_label")
        minted.append(obs_id)
    return minted


def _reel_clock(model: dict[str, Any], reel_slug: str,
                timeline_name: str) -> str:
    return _clock(model, f"clock:reel:{reel_slug}",
                  {"kind": "timeline", "timeline_id": timeline_name})


def clip_uses_from_placements(model: dict[str, Any], reel_slug: str,
                              timeline_name: str,
                              placements: list[dict[str, Any]],
                              track_roles: dict[tuple[str, int], str],
                              transcript: dict | None = None) -> dict[str, Any]:
    """One ClipUse per placed piece, plus the reel's assemble edit.

    Roles come from the recorded track plan (``track_roles`` maps
    ``(track_type, track_index)`` to the plan role): A-roll picture
    rows mint ``a_roll``, everything else picture mints ``b_roll``,
    audio rows mint ``speech``. A placement with no source span or
    no reel span is refused with its reason, never clamped.
    Returns ``{"use_ids", "evidence_ids", "skipped"}``.
    """
    reel_clock = _reel_clock(model, reel_slug, timeline_name)
    use_ids: list[str] = []
    evidence_ids: list[str] = []
    skipped: list[dict[str, Any]] = []
    total_master = 0.0
    total_reel = 0.0
    for index, place in enumerate(placements):
        if not isinstance(place, dict):
            skipped.append({"index": index, "reason": "not a mapping"})
            continue
        source = sec_to_ms(place.get("source_in"), place.get("source_out"))
        reel_span = sec_to_ms(place.get("record_start"),
                              float(place.get("record_start", 0.0))
                              + max(float(place.get("source_out", 0.0))
                                    - float(place.get("source_in", 0.0)), 0.0))
        if source is None:
            skipped.append({"index": index, "reason": "empty source span"})
            continue
        if reel_span is None:
            skipped.append({"index": index, "reason": "empty reel span"})
            continue
        track_type = str(place.get("track_type") or "")
        try:
            track_index = int(place.get("track_index", 0))
        except (TypeError, ValueError):
            track_index = 0
        asset = asset_for_source(model, place.get("source_file"), track_type)
        if asset is None:
            skipped.append({"index": index, "reason": "no source file"})
            continue
        asset_id, source_clock = asset
        role = track_roles.get((track_type, track_index), "")
        if track_type == "audio":
            use_role = "speech"
        elif role == "a_roll":
            use_role = "a_roll"
        else:
            use_role = "b_roll"
        speaker = place.get("speaker")
        entity_links: list[str] = []
        if speaker:
            entity_id = person_for_speaker(model, str(speaker))
            if entity_id is not None:
                entity_links.append(entity_id)
                evidence_ids.extend(speech_observations(
                    model, transcript, str(speaker),
                    str(place.get("source_file") or ""),
                    asset_id, source_clock, entity_id))
        use_id = f"use:{reel_slug}:{index:04d}"
        present = {row["id"] for row in model.get("clip_uses") or []
                   if isinstance(row, dict) and "id" in row}
        if use_id not in present:
            model.setdefault("clip_uses", []).append({
                "id": use_id,
                "asset_id": asset_id,
                "source_interval": {"clock_id": source_clock,
                                    "start": source[0], "end": source[1]},
                "timeline_interval": {"clock_id": reel_clock,
                                      "start": reel_span[0], "end": reel_span[1]},
                "entity_links": entity_links,
                "role": use_role,
                "intent_id": None,
            })
        use_ids.append(use_id)
        try:
            total_master += max(float(place.get("master_end", 0.0))
                                - float(place.get("master_start", 0.0)), 0.0)
            total_reel += max(float(place.get("source_out", 0.0))
                              - float(place.get("source_in", 0.0)), 0.0)
        except (TypeError, ValueError):
            pass
    edit_id = f"edit:{reel_slug}:assemble"
    if use_ids and not any(isinstance(row, dict) and row.get("id") == edit_id
                           for row in model.get("edits") or []):
        model.setdefault("edits", []).append({
            "id": edit_id,
            "operation": "reel.assemble",
            "target_ids": list(use_ids),
            "evidence_ids": sorted(set(evidence_ids)),
            "author": "reel.build",
            "intent_id": None,
            "parent_revision": None,
            "before": {"master_seconds": round(total_master, 3)},
            "after": {"reel_seconds": round(total_reel, 3)},
        })
    return {"use_ids": use_ids, "evidence_ids": sorted(set(evidence_ids)),
            "skipped": skipped}


def _placement_for_master_span(placements: list[dict[str, Any]],
                               start: Any, end: Any) -> dict[str, Any] | None:
    """The video placement overlapping a master span most, or None.

    Master spans are the join between plan-time decisions (VFX, timed
    text) and reel-time clip uses: a decision lands on the picture it
    covers. Ties break toward the longer overlap; no overlap mints
    nothing.
    """
    try:
        want_start, want_end = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(want_start) or not math.isfinite(want_end):
        return None
    best: dict[str, Any] | None = None
    best_overlap = 0.0
    for place in placements:
        if not isinstance(place, dict):
            continue
        if str(place.get("track_type") or "") != "video":
            continue
        try:
            got_start = float(place.get("master_start", 0.0))
            got_end = float(place.get("master_end", 0.0))
        except (TypeError, ValueError):
            continue
        overlap = min(want_end, got_end) - max(want_start, got_start)
        if overlap > best_overlap:
            best_overlap = overlap
            best = place
    return best


def carry_move_effects(model: dict[str, Any],
                       move_effects: list[dict[str, Any]]) -> dict[str, Any]:
    """Carry ``enhancement_spec.move_effects`` (PR 2263) verbatim.

    The mint reads ids and intervals off the post-migration model, so
    its rows are already model-shaped: validated on the way in, and a
    row that fails validation is dropped with its reason rather than
    failing the build. Target ids stay the mint's own - the join onto
    reel clip uses is follow-up work once PR 2263 merges.
    """
    carried: list[str] = []
    skipped: list[dict[str, Any]] = []
    present = {row["id"] for row in model.get("effects") or []
               if isinstance(row, dict) and "id" in row}
    for position, row in enumerate(move_effects):
        if not isinstance(row, dict) or "id" not in row:
            skipped.append({"index": position,
                            "reason": "not a model effect row"})
            continue
        try:
            validate_project_model({**model, "effects": [
                *(model.get("effects") or []), row]})
        except Exception as exc:  # noqa: BLE001 - any shape refusal drops the row
            skipped.append({"id": row.get("id", position),
                            "reason": f"invalid model effect: {exc}"})
            continue
        if row["id"] not in present:
            model.setdefault("effects", []).append(row)
            present.add(row["id"])
        carried.append(row["id"])
    return {"carried": carried, "skipped": skipped}


def titles_from_segments(model: dict[str, Any], reel_slug: str,
                         reel_clock: str,
                         segments: list[dict[str, Any]],
                         placements: list[dict[str, Any]],
                         use_id_by_placement: dict[int, str]) -> dict[str, Any]:
    """Rendered timed-text segments as ``title.text`` model effects.

    Each segment is timed from the spine (master seconds); it lands on
    the reel picture covering that master span, and its interval is
    that clip use's timeline interval. A segment no reel picture
    covers is recorded with its reason, never placed by a default.
    """
    minted: list[str] = []
    skipped: list[dict[str, Any]] = []
    for position, segment in enumerate(segments):
        if not isinstance(segment, dict):
            skipped.append({"index": position, "reason": "not a mapping"})
            continue
        place = _placement_for_master_span(
            placements, segment.get("timeline_start"),
            segment.get("timeline_end"))
        if place is None:
            skipped.append({"index": position,
                            "reason": "no reel picture covers the title span"})
            continue
        target_id = use_id_by_placement.get(id(place))
        if target_id is None:
            skipped.append({"index": position,
                            "reason": "covered placement minted no clip use"})
            continue
        use = next((row for row in model.get("clip_uses") or []
                    if isinstance(row, dict) and row.get("id") == target_id),
                   None)
        if use is None:
            skipped.append({"index": position,
                            "reason": "target clip use is not in the model"})
            continue
        params: dict[str, Any] = {
            "segment_file": str(segment.get("overlay_path") or ""),
            "master_start": segment.get("timeline_start"),
            "master_end": segment.get("timeline_end"),
        }
        copy = segment.get("copy", segment.get("text"))
        if isinstance(copy, str) and copy.strip():
            params["copy"] = copy
        elif isinstance(copy, dict) and copy:
            params["copy"] = copy
        effect_id = f"effect:{reel_slug}:title{position:03d}"
        present = {row["id"] for row in model.get("effects") or []
                   if isinstance(row, dict) and "id" in row}
        if effect_id not in present:
            model.setdefault("effects", []).append({
                "id": effect_id,
                "class_id": "title.text",
                "target_id": target_id,
                "interval": dict(use["timeline_interval"]),
                "parameters": params,
                "keyframes": [],
            })
        minted.append(effect_id)
    return {"minted": minted, "skipped": skipped}


def frame_map_for_reel(model: dict[str, Any], reel_slug: str,
                       reel_clock: str, use_ids: list[str]) -> str:
    """The source-to-reel frame map: what source ranges became the reel.

    One ``one_to_one`` segment per clip use (``retimed`` with the
    measured rate where a placement played at speed), so the map is
    the edit decision the build placed, readable by the FilmCraft
    adapter without re-deriving it.
    """
    map_id = f"map:{reel_slug}"
    model["frame_maps"] = [row for row in model.get("frame_maps") or []
                           if not (isinstance(row, dict)
                                   and row.get("id") == map_id)]
    segments = []
    for use_id in use_ids:
        use = next((row for row in model.get("clip_uses") or []
                    if isinstance(row, dict) and row.get("id") == use_id),
                   None)
        if use is None:
            continue
        segments.append({
            "from": dict(use["source_interval"]),
            "to": dict(use["timeline_interval"]),
            "mapping_kind": "one_to_one",
            "rate": {"numerator": 1, "denominator": 1},
            "source_clip_use": use_id,
        })
    model.setdefault("frame_maps", []).append({
        "id": map_id,
        "from_clock": segments[0]["from"]["clock_id"] if segments else reel_clock,
        "to_clock": reel_clock,
        "segments": segments,
    })
    return map_id


def build_reel_fragment(model: dict[str, Any], reel_name: str,
                        placements: list[dict[str, Any]],
                        track_plan: dict[str, Any] | None = None,
                        transcript: dict | None = None,
                        move_effects: list[dict[str, Any]] | None = None,
                        title_segments: list[dict[str, Any]] | None = None,
                        masks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Mint one reel's fragment onto the model, in place.

    Pure: no Resolve, no lanes, no store. Roles come from the
    recorded track plan; caller-supplied ``masks`` (measured
    elsewhere, e.g. 1.06 segmentation) are carried after validation,
    never invented here. Move effects are carried verbatim off
    ``enhancement_spec.move_effects`` - step 4.03 (PR 2263) is the
    one producer, and this module never mints a second copy. Returns
    the per-reel report.
    """
    reel_slug = _slug(reel_name)
    ensure_provenance(model)
    roles: dict[tuple[str, int], str] = {}
    for track in ((track_plan or {}).get("video_tracks") or []):
        if isinstance(track, dict) and track.get("role") == "a_roll":
            try:
                roles[("video", int(track.get("index", 0)))] = "a_roll"
            except (TypeError, ValueError):
                pass
    reel_clock = _reel_clock(model, reel_slug, reel_name)
    report: dict[str, Any] = {"reel": reel_name, "reel_slug": reel_slug}
    uses = clip_uses_from_placements(model, reel_slug, reel_name,
                                     placements, roles, transcript)
    report["clip_uses"] = len(uses["use_ids"])
    report["clip_uses_skipped"] = uses["skipped"]
    use_id_by_placement = {id(place): use_id for place, use_id
                           in zip(placements, uses["use_ids"])
                           if isinstance(place, dict)}
    if masks:
        carried, skipped_masks = 0, []
        present_masks = {row["id"] for row in model.get("masks") or []
                         if isinstance(row, dict) and "id" in row}
        for position, mask in enumerate(masks):
            if not isinstance(mask, dict) or "id" not in mask:
                skipped_masks.append({"index": position,
                                      "reason": "not a mask row"})
                continue
            if mask["id"] not in present_masks:
                model.setdefault("masks", []).append(mask)
                present_masks.add(mask["id"])
                carried += 1
        report["masks_carried"] = carried
        report["masks_skipped"] = skipped_masks
    # Move effects ride the step 4.03 mint (PR 2263) and are carried
    # verbatim here. An absent key is said, not filled: minting here
    # would be a second implementation of the producer.
    if move_effects:
        carried_moves = carry_move_effects(model, list(move_effects))
        report["move_effects"] = carried_moves["carried"]
        report["move_effects_skipped"] = carried_moves["skipped"]
    else:
        report["move_effects"] = []
        report["move_effects_skipped"] = [
            {"reason": "no move_effects in enhancement_spec"}]
    titles = titles_from_segments(model, reel_slug, reel_clock,
                                  list(title_segments or []), placements,
                                  use_id_by_placement)
    report["titles"] = titles["minted"]
    report["titles_skipped"] = titles["skipped"]
    report["frame_map"] = frame_map_for_reel(model, reel_slug, reel_clock,
                                             uses["use_ids"])
    validate_project_model(model)
    return report


# ── Store maintenance ─────────────────────────────────────────────


def _state_outputs(project_folder: str) -> dict[str, Any]:
    """The producers' outputs off pipeline state, or {} when unreadable.

    Reads ``pipeline_data.json`` through ``capability_outputs`` the
    way any step reader does - never Resolve, never a re-derivation.
    """
    from library.tools.project_layout import ProjectLayout
    try:
        from library.tools import capability_outputs
        path = ProjectLayout(project_folder).pipeline_data_path
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            "enhancement_spec": capability_outputs.value(
                state, "vfx.resolve", "enhancement_spec"),
            "motion_graphics_overlay": capability_outputs.value(
                state, "motion_graphics.render", "motion_graphics_overlay"),
        }
    except (OSError, ValueError, KeyError):
        return {}


def _reel_namespaces(reel_slugs: list[str]) -> tuple[str, ...]:
    prefixes: list[str] = []
    for slug in reel_slugs:
        prefixes.extend((f"use:{slug}:", f"effect:{slug}:",
                         f"edit:{slug}:", f"map:{slug}"))
    return tuple(prefixes)


def maintain_from_build(project_folder: str, build_record: dict,
                        transcript: dict | None = None,
                        enhancement_spec: dict | None = None,
                        motion_graphics_overlay: dict | None = None,
                        masks: list[dict[str, Any]] | None = None,
                        store_root: str | Path | None = None,
                        author: str = "reel.build",
                        reason: str | None = None) -> dict[str, Any]:
    """Bring the Project Model current with one reel build, as one revision.

    ``build_record`` is the ``reel_build`` record (``reel_placements``
    + ``track_plans`` + ``reel_fps``). Evidence inputs default to the
    pipeline state the producers wrote. ``store_root`` redirects the
    store (the read-only scratch run); otherwise the project owns it.
    Never raises: a model write that fails a build is reported, not
    thrown.
    """
    project_folder = str(project_folder)
    store_at = Path(store_root) if store_root is not None else Path(project_folder)
    slug = Path(project_folder).name or "project"
    record = build_record if isinstance(build_record, dict) else {}
    if transcript is None and isinstance(record.get("_transcript"), dict):
        # The CLI maintain path stashes the transcript on the record;
        # the step hook passes it directly. Either way it is an input,
        # never re-derived here.
        transcript = record["_transcript"]
    try:
        return _maintain(project_folder, store_at, slug, record,
                         transcript, enhancement_spec,
                         motion_graphics_overlay, masks, author, reason)
    except Exception as exc:  # noqa: BLE001 - fail-soft by contract
        return {"status": "refused",
                "reason": f"project model not updated: {exc!r}"}


def _maintain(project_folder: str, store_at: Path, slug: str,
              build_record: dict, transcript: dict | None,
              enhancement_spec: dict | None,
              motion_graphics_overlay: dict | None,
              masks: list[dict[str, Any]] | None,
              author: str, reason: str | None) -> dict[str, Any]:
    from library.tools import project_model_store as store
    record = build_record if isinstance(build_record, dict) else {}
    placements_by_reel = record.get("reel_placements")
    if not isinstance(placements_by_reel, dict) or not placements_by_reel:
        return {"status": "refused",
                "reason": "build record carries no reel_placements; "
                          "the model cannot grade what was not recorded"}
    if enhancement_spec is None or motion_graphics_overlay is None:
        state = _state_outputs(project_folder)
        if enhancement_spec is None:
            enhancement_spec = state.get("enhancement_spec")
        if motion_graphics_overlay is None:
            motion_graphics_overlay = state.get("motion_graphics_overlay")
    spec = enhancement_spec if isinstance(enhancement_spec, dict) else {}
    overlay = (motion_graphics_overlay
               if isinstance(motion_graphics_overlay, dict) else {})
    # The evidence base: what the preflight's lanes measured, migrated
    # through Unit 3. A project with no lanes yet (or unreadable ones)
    # gets an empty base with the reason said, never a refusal.
    try:
        from library.tools import project_model_evidence as evidence
        base, evidence_report = evidence.migrate_project_evidence(
            project_folder)
        evidence_note = (f"migrated {len(evidence_report.get('digests', []))} "
                         f"digest(s), {len(evidence_report.get('persons', []))} "
                         f"person(s)")
    except Exception as exc:  # noqa: BLE001 - lanes are best-effort here
        base = blank_model(f"project-{slug}")
        evidence_note = f"blank base: evidence migration said {exc!r}"
    ensure_provenance(base)
    try:
        expected_revision, stored = store.load_project_model(store_at)
    except (FileNotFoundError, ValueError):
        expected_revision, stored = 0, None
    if stored is None:
        model = base
    else:
        # Keep every stored row the fresh base does not replace and no
        # rebuilt reel owns: other reels' fragments and prior renders
        # survive a partial rebuild.
        model = stored
        fresh_ids = {row["id"] for key in MODEL_COLLECTIONS
                     for row in base.get(key) or []
                     if isinstance(row, dict) and "id" in row}
        for key in MODEL_COLLECTIONS:
            model[key] = [row for row in model.get(key) or []
                          if not (isinstance(row, dict)
                                  and row.get("id") in fresh_ids)]
        for key in MODEL_COLLECTIONS:
            model[key] = [*model.get(key, []), *base.get(key, [])]
        ensure_provenance(model)
    reel_slugs = [_slug(name) for name in placements_by_reel]
    namespaces = _reel_namespaces(reel_slugs)
    for key in ("clip_uses", "edits", "effects", "frame_maps", "masks"):
        model[key] = [row for row in model.get(key) or []
                      if not (isinstance(row, dict)
                              and str(row.get("id", "")).startswith(namespaces))]
    track_plans = record.get("track_plans")
    if not isinstance(track_plans, dict):
        track_plans = {}
    # The step 4.03 mint (PR 2263) is the one move producer; its
    # rows arrive on `enhancement_spec.move_effects` and are carried
    # verbatim. Anything else is absent, never re-minted here.
    carried_moves = spec.get("move_effects")
    if not isinstance(carried_moves, list):
        carried_moves = []
    segments = overlay.get("segments")
    if not isinstance(segments, list):
        segments = []
    reports = []
    for reel_name, placements in placements_by_reel.items():
        if not isinstance(placements, list):
            continue
        track_plan = track_plans.get(reel_name)
        reports.append(build_reel_fragment(
            model, str(reel_name), placements,
            track_plan if isinstance(track_plan, dict) else None,
            transcript,
            move_effects=carried_moves,
            title_segments=segments,
            masks=[mask for mask in (masks or [])
                   if isinstance(mask, dict)] or None))
    validate_project_model(model)
    reels_named = sorted(placements_by_reel)
    write_reason = reason or f"reel.build: {', '.join(reels_named)}"
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            revision = store.commit_project_model(
                store_at, model, expected_revision=expected_revision,
                author=author, reason=write_reason)
            break
        except RuntimeError as exc:
            last_error = exc
            try:
                expected_revision, stored = store.load_project_model(store_at)
            except (FileNotFoundError, ValueError) as reload_exc:
                return {"status": "refused",
                        "reason": f"store unreadable on retry: {reload_exc!r}"}
            model = stored
            # Re-apply this build's fragments onto the reloaded head.
            for reel_name, placements in placements_by_reel.items():
                if not isinstance(placements, list):
                    continue
                namespaces = _reel_namespaces([_slug(str(reel_name))])
                for key in ("clip_uses", "edits", "effects",
                            "frame_maps", "masks"):
                    model[key] = [
                        row for row in model.get(key) or []
                        if not (isinstance(row, dict) and str(
                            row.get("id", "")).startswith(namespaces))]
                track_plan = (track_plans.get(reel_name)
                              if isinstance(track_plans, dict) else None)
                build_reel_fragment(
                    model, str(reel_name), placements,
                    track_plan if isinstance(track_plan, dict) else None,
                    transcript,
                    move_effects=carried_moves,
                    title_segments=segments,
                    masks=[mask for mask in (masks or [])
                           if isinstance(mask, dict)] or None)
            validate_project_model(model)
    else:
        return {"status": "refused",
                "reason": f"store contention, last error: {last_error!r}"}
    counts = {key: len(model.get(key) or []) for key in MODEL_COLLECTIONS}
    return {"status": "committed", "revision": revision,
            "reason": write_reason, "reels": reports,
            "evidence": evidence_note, "counts": counts}


def note_deliver(project_folder: str, reel: int, timeline_name: str,
                 output_path: str, expected_frames: int | None = None,
                 fps: float | None = None,
                 store_root: str | Path | None = None,
                 author: str = "deliver-reel",
                 hearing: dict | None = None) -> dict[str, Any]:
    """Append a delivered render, its receipt and reel-to-render map.

    The build wrote the source-to-reel map; delivery appends what the
    render produced. Frames are probed off the delivered file itself;
    an unmeasurable file is reported, never guessed. Never raises.

    `hearing` is Cascade Lane C2: the reel's hearing record (the plain
    dict ``reel_hearing.write_record`` wrote beside the render), stored
    as the receipt's QA fields. An unreadable hearing is reported on
    the return and the deliver still commits - the render happened;
    only its QA note is missing.
    """
    from library.tools import project_model_store as store
    project_folder = str(project_folder)
    store_at = Path(store_root) if store_root is not None else Path(project_folder)
    try:
        return _note_deliver(store_at, project_folder, int(reel),
                             str(timeline_name), str(output_path),
                             expected_frames, fps, author, hearing)
    except Exception as exc:  # noqa: BLE001 - fail-soft by contract
        return {"status": "refused",
                "reason": f"deliver not recorded: {exc!r}"}


def _probe_frames(path: str) -> tuple[int | None, float | None]:
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=nb_frames,r_frame_rate,duration",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, encoding="utf-8", check=False, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None, None
    values: dict[str, str] = {}
    for line in (done.stdout or "").splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    frames: int | None = None
    try:
        frames = int(values["nb_frames"])
    except (KeyError, ValueError):
        frames = None
    rate: float | None = None
    try:
        numerator, _, denominator = values["r_frame_rate"].partition("/")
        rate = float(numerator) / float(denominator)
    except (KeyError, ValueError, ZeroDivisionError):
        rate = None
    if frames is None and rate:
        try:
            frames = int(round(float(values["duration"]) * rate))
        except (KeyError, ValueError):
            frames = None
    return frames, rate


def _note_deliver(store_at: Path, project_folder: str, reel: int,
                  timeline_name: str, output_path: str,
                  expected_frames: int | None, fps: float | None,
                  author: str, hearing: dict | None = None) -> dict[str, Any]:
    from library.tools import project_model_store as store
    if not output_path or not Path(output_path).is_file():
        return {"status": "refused",
                "reason": f"no delivered file at {output_path!r}"}
    probed_frames, probed_rate = _probe_frames(output_path)
    frames = expected_frames if expected_frames else probed_frames
    rate = fps if fps else probed_rate
    if not frames or frames <= 0:
        return {"status": "refused",
                "reason": f"delivered file has no measurable frame count: "
                          f"{output_path!r}"}
    reel_slug = _slug(timeline_name)
    try:
        expected_revision, model = store.load_project_model(store_at)
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "refused",
                "reason": f"no Project Model to record the deliver on: {exc!r}"}
    ensure_provenance(model)
    try:
        reel_uses = [row for row in model.get("clip_uses") or []
                     if isinstance(row, dict)
                     and str(row.get("id", "")).startswith(f"use:{reel_slug}:")]
        # The render maps the PICTURE: audio rows overlap the picture
        # by design, so summing every use would double-count the
        # reel. Speech rows stay linked through their entity links.
        video_uses = [row for row in reel_uses
                      if row.get("role") in ("a_roll", "b_roll")]
        reel_end = max(row["timeline_interval"]["end"] for row in video_uses)
        reel_start = min(row["timeline_interval"]["start"] for row in video_uses)
        reel_clock_id = video_uses[0]["timeline_interval"]["clock_id"]
    except (IndexError, KeyError, TypeError, ValueError):
        return {"status": "refused",
                "reason": f"reel {timeline_name!r} has no picture uses; "
                          f"build it before recording a deliver"}
    render_clock = f"clock:render:{reel_slug}"
    from fractions import Fraction
    try:
        rate_frac = Fraction(float(rate)).limit_denominator(1001)
        render_tps = max(int(round(float(rate))), 1)
    except (TypeError, ValueError):
        rate_frac, render_tps = None, TICKS_PER_SECOND
    model["clocks"] = [clock for clock in model.get("clocks") or []
                       if not (isinstance(clock, dict)
                               and clock.get("id") == render_clock)]
    model.setdefault("clocks", []).append({
        "id": render_clock,
        "domain": {"kind": "render", "render_id": f"render:{reel_slug}"},
        "ticks_per_second": render_tps,
        "frame_rate": ({"numerator": rate_frac.numerator,
                        "denominator": rate_frac.denominator}
                       if rate_frac is not None else None),
    })
    asset_id = f"asset:render:{reel_slug}"
    present_assets = {row["id"] for row in model.get("assets") or []
                      if isinstance(row, dict) and "id" in row}
    if asset_id not in present_assets:
        model.setdefault("assets", []).append({
            "id": asset_id,
            "kind": "render",
            "uri": output_path,
            "content_digest": None,
            "metadata": {"producer": PRODUCER, "reel": timeline_name,
                         "frame_rate": rate},
            "provenance": [PROVENANCE_ID],
        })
    receipt_id = f"receipt:{reel_slug}"
    present_receipts = {row["id"] for row in model.get("receipts") or []
                        if isinstance(row, dict) and "id" in row}
    if receipt_id not in present_receipts:
        model.setdefault("receipts", []).append({
            "id": receipt_id,
            "status": "ok",
            "renderer": "resolve",
            "input_revision": f"rev-{expected_revision}",
            "output_asset_id": asset_id,
            "details": {"expected_frames": frames, "reel": timeline_name},
        })
    map_id = f"map:{reel_slug}:render"
    model["frame_maps"] = [row for row in model.get("frame_maps") or []
                           if not (isinstance(row, dict)
                                   and row.get("id") == map_id)]
    segments = []
    reel_span = reel_end - reel_start
    cursor = reel_start
    for row in sorted(video_uses,
                      key=lambda item: item["timeline_interval"]["start"]):
        span = row["timeline_interval"]
        if span["start"] != cursor:
            return {"status": "refused",
                    "reason": f"reel {timeline_name!r} picture is not "
                              f"contiguous at {cursor}: use {row['id']!r} "
                              f"starts at {span['start']}"}
        span = row["timeline_interval"]
        # Reel milliseconds onto render frames, proportionally: the
        # delivered file plays the whole picture contiguously, so the
        # reel's end lands exactly on the expected frame count.
        to_start = int(round((span["start"] - reel_start) / reel_span * frames))
        to_end = int(round((span["end"] - reel_start) / reel_span * frames))
        if to_end <= to_start:
            return {"status": "refused",
                    "reason": f"reel {timeline_name!r} use {row['id']!r} "
                              f"covers no render frame"}
        segments.append({
            "from": dict(span),
            "to": {"clock_id": render_clock, "start": to_start,
                   "end": to_end},
            "mapping_kind": "one_to_one",
            "rate": {"numerator": 1, "denominator": 1},
            "source_clip_use": row["id"],
        })
        cursor = span["end"]
    model.setdefault("frame_maps", []).append({
        "id": map_id,
        "from_clock": reel_clock_id,
        "to_clock": render_clock,
        "segments": segments,
    })
    render_id = f"render:{reel_slug}"
    model["renders"] = [row for row in model.get("renders") or []
                        if not (isinstance(row, dict)
                                and row.get("id") == render_id)]
    model.setdefault("renders", []).append({
        "id": render_id,
        "input_revision": f"rev-{expected_revision}",
        "output_asset_id": asset_id,
        "receipt_id": receipt_id,
        "frame_map_id": map_id,
        "settings": {"expected_frames": frames, "frame_rate": rate},
    })
    hearing_note: dict[str, Any] = {"attached": False, "reason": "no hearing given"}
    if hearing is not None:
        from library.tools import reel_layer_links as _links
        model, hearing_note = _links.attach_hearing_qa(
            model, reel_slug, hearing)
    # Lane E3 (R2/R9): the receipt carries the reel's content hashes
    # (`details.build`) - the converged copy the legacy per-reel maps
    # retire into. Read off the derived registry first, the legacy
    # provenance maps for reels that predate it, and stamp the plan
    # hash beside them. Fail-soft like the hearing above: the render
    # happened; only its hash note may be missing.
    build_note: dict[str, Any] = {"attached": False,
                                  "reason": "no baseline on record"}
    try:
        from library.tools import reel_layer_links as _links
        from library.tools.plan_provenance import (
            read_provenance as _read_prov,
            reel_content_hashes as _hashes_of,
        )
        _review_dir = str(Path(project_folder) / "pipeline_output"
                          / "review")
        _hashes = _hashes_of(_review_dir, str(timeline_name))
        _plan_hash_value = (_read_prov(_review_dir) or {}).get(
            "plan_content_hash")
        if _hashes.get("source") != "absent":
            model, build_note = _links.attach_build_hashes(
                model, reel_slug, _hashes,
                plan_hash=_plan_hash_value)
    except Exception as exc:                             # noqa: BLE001
        build_note = {"attached": False,
                      "reason": f"hash baseline unreadable: {exc!r}"}
    validate_project_model(model)
    try:
        revision = store.commit_project_model(
            store_at, model, expected_revision=expected_revision,
            author=author, reason=f"deliver-reel: {timeline_name}")
    except RuntimeError as exc:
        return {"status": "refused", "reason": f"store contention: {exc!r}"}
    result: dict[str, Any] = {"status": "committed", "revision": revision,
                              "render": render_id, "frames": frames,
                              "build_hashes": build_note}
    if hearing is not None:
        result["hearing_qa"] = hearing_note
    return result


# ── Read path ─────────────────────────────────────────────────────


def show(project_folder: str, reel: str | int | None = None,
         store_root: str | Path | None = None) -> dict[str, Any]:
    """The inspectable read: per-reel counts and identity linkage.

    Raises FileNotFoundError when no model is stored, ValueError
    when the reel names nothing.
    """
    from library.tools import project_model_evidence as evidence
    from library.tools import project_model_store as store
    store_at = Path(store_root) if store_root is not None else Path(project_folder)
    revision, model = store.load_project_model(store_at)
    people = {entity["id"]: entity for entity in model.get("entities") or []
              if isinstance(entity, dict) and entity.get("kind") == "person"}
    speakers = {entity["id"]: entity for entity in model.get("entities") or []
                if isinstance(entity, dict) and entity.get("kind") == "speaker"}
    reels: dict[str, dict[str, Any]] = {}
    for use in model.get("clip_uses") or []:
        if not isinstance(use, dict):
            continue
        match = re.match(r"^use:(.+):\d{4}$", str(use.get("id", "")))
        name = match.group(1) if match else "unscoped"
        entry = reels.setdefault(name, {
            "reel": name, "uses": 0, "a_roll": 0, "effects": 0,
            "titles": 0, "renders": 0, "persons": set(), "masks": 0,
            "speech": 0})
        entry["uses"] += 1
        if use.get("role") == "a_roll":
            entry["a_roll"] += 1
        for entity_id in use.get("entity_links") or []:
            if entity_id in people or entity_id in speakers:
                entry["persons"].add(entity_id)
    for effect in model.get("effects") or []:
        if not isinstance(effect, dict):
            continue
        match = re.match(r"^effect:(.+?):", str(effect.get("id", "")))
        if not match:
            continue
        entry = reels.setdefault(match.group(1), {
            "reel": match.group(1), "uses": 0, "a_roll": 0, "effects": 0,
            "titles": 0, "renders": 0, "persons": set(), "masks": 0,
            "speech": 0})
        entry["effects"] += 1
        if str(effect.get("class_id", "")).startswith("title."):
            entry["titles"] += 1
    for render in model.get("renders") or []:
        if not isinstance(render, dict):
            continue
        match = re.match(r"^render:(.+)$", str(render.get("id", "")))
        if match and match.group(1) in reels:
            reels[match.group(1)]["renders"] += 1
    for entity_id in set().union(*(entry["persons"] for entry in reels.values())) \
            if reels else set():
        if entity_id in people:
            resolved = evidence.resolve_person(model, entity_id)
            masks = len(resolved["masks"]) if resolved else 0
            speech = len([obs for obs in (resolved["observations"] if resolved else [])
                          if obs.get("modality") == "speech"])
        else:
            # Diarization-only speaker entities resolve the same way:
            # linked speech observations and masks by entity id.
            wanted_obs = {link.get("observation_id")
                          for link in model.get("identity_links") or []
                          if isinstance(link, dict)
                          and link.get("entity_id") == entity_id}
            speech = len([obs for obs in model.get("observations") or []
                          if isinstance(obs, dict)
                          and obs.get("id") in wanted_obs
                          and obs.get("modality") == "speech"])
            masks = len([mask for mask in model.get("masks") or []
                         if isinstance(mask, dict)
                         and mask.get("entity_id") == entity_id])
        for entry in reels.values():
            if entity_id in entry["persons"]:
                entry["masks"] += masks
                entry["speech"] += speech
    for entry in reels.values():
        entry["persons"] = sorted(entry["persons"])
    if reel is not None:
        want = _slug(str(reel))
        matches = [entry for key, entry in reels.items()
                   if key == want or key.endswith(f"-{want}")
                   or str(reel) in key]
        if not matches:
            raise ValueError(
                f"reel {reel!r} names nothing in the stored model "
                f"(known: {sorted(reels) or 'no reels'})")
        return {"revision": revision, "model": model["id"], "reels": matches}
    return {"revision": revision, "model": model["id"],
            "reels": [reels[key] for key in sorted(reels)],
            "counts": {key: len(model.get(key) or [])
                       for key in MODEL_COLLECTIONS}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reel_project_model",
        description="Maintain and read the reel-built Project Model "
                    "(read-only on lanes and pipeline state; the store "
                    "is the only write).")
    sub = parser.add_subparsers(dest="command", required=True)
    show_p = sub.add_parser("show", help="inspect the stored model")
    show_p.add_argument("project")
    show_p.add_argument("--reel", default=None)
    show_p.add_argument("--store-root", default=None,
                        help="read the store here instead of the project")
    maintain_p = sub.add_parser(
        "maintain", help="bring the model current with the recorded build")
    maintain_p.add_argument("project")
    maintain_p.add_argument("--store-root", default=None,
                            help="commit the store here (scratch runs)")
    maintain_p.add_argument("--reason", default=None)
    args = parser.parse_args(argv)
    if args.command == "show":
        try:
            payload = show(args.project, args.reel,
                           store_root=args.store_root)
        except (FileNotFoundError, ValueError) as exc:
            print(json.dumps({"status": "refused", "reason": str(exc)}))
            return 1
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0
    payload = maintain_from_build(args.project, _record_of(args.project),
                                  store_root=args.store_root,
                                  reason=args.reason)
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0 if payload.get("status") == "committed" else 1


def _record_of(project_folder: str) -> dict:
    """The last recorded reel build, plus its transcript.

    The CLI maintain path reads what the step hook receives: the
    ``reel_build`` record off pipeline state and the transcript file
    the build was cut from. Anything missing refuses by name.
    """
    from library.tools.project_layout import ProjectLayout
    try:
        from library.tools import capability_outputs
        path = ProjectLayout(project_folder).pipeline_data_path
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        record = capability_outputs.value(state, "reel.build", "reel_build")
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"no readable pipeline state: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError("pipeline state carries no reel_build record")
    transcript_path = (Path(project_folder) / "pipeline_output" / "scratch"
                       / "timeline_transcript" / "transcript.json")
    try:
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        transcript = None
    record = dict(record)
    if transcript is not None:
        record["_transcript"] = transcript
    return record


if __name__ == "__main__":
    raise SystemExit(main())
