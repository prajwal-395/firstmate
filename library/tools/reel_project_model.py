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
  what was built, never a re-derivation). Measured static Resolve zoom
  and sequence-pixel positions, plus tight overlay placements, travel
  in those reel records and are minted as ``transform.scale`` and
  ``transform.position`` effects.
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
``run_reels.merge_record`` merges ``reel_placements`` and
``reel_overlay_placements`` per reel, so concurrent ``--only-reel``
lanes each keep their rows.

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
from fractions import Fraction
from pathlib import Path
from typing import Any

try:
    from library.tools.project_model import SCHEMA_VERSION, validate_project_model
except ImportError:  # imported as ``tools.*`` from inside library/
    from tools.project_model import SCHEMA_VERSION, validate_project_model

TICKS_PER_SECOND = 120_000
"""120 kHz ticks preserve millisecond evidence and common frame rates.

This shared grid represents 23.976, 24, 29.97 and 59.94 fps frames as
integer ticks, while retaining better-than-millisecond resolution for
float-second measurements.
"""

PROVENANCE_ID = "prov-reel-build"
PRODUCER = "reel_project_model"

MODEL_COLLECTIONS = (
    "assets", "streams", "clocks", "entities", "observations",
    "identity_links", "masks", "object_classes", "tracks", "relations",
    "intents", "clip_uses", "edits", "effects", "renders", "frame_maps",
    "nle_bindings", "receipts", "provenance",
)

REEL_COMMIT_KEYS = (
    "clocks", "clip_uses", "edits", "effects", "frame_maps", "masks",
    "provenance",
)
"""Collections a reel build owns outright, committed as one merge_patch.

On a fingerprint hit the evidence base is byte-identical to the stored
head, so only these small keys can differ: reel rows are namespaced per
reel, reel clocks are minted per build, and the provenance row is
carried verbatim. Committing just these keeps the event log from
growing a full 350 MB model per build; a fresh evidence base still
commits a full replace carrying the new evidence too.
"""

EVIDENCE_FINGERPRINT_FILE = "evidence-fingerprint.json"
"""Sidecar beside the store: fingerprint of the evidence inputs.

Written after each successful ``maintain_from_build``; the next build
reuses the stored evidence base when the fingerprint matches, skipping
the 58 s re-migration. The fingerprint covers the catalog digests and
every lane file's stat (size + mtime), never lane contents.
"""


def evidence_fingerprint(project_folder: str,
                         memory_root: str | Path | None = None) -> str:
    """Cheap fingerprint of the evidence migration inputs.

    Stats only, no lane content reads: the catalog's clip digests plus
    every file under each digest's source-memory directory (name, size,
    mtime). A re-measured lane changes a file's mtime/size, so the
    fingerprint changes and the migration reruns. Identical inputs reuse
    the stored evidence base item-for-item.
    """
    from library.tools import source_memory as _sm
    try:
        catalog = _sm.load_catalog(project_folder)
    except (OSError, ValueError, TypeError):
        catalog = []
    try:
        recorded = _sm.load_recorded_fingerprints(project_folder)
    except (OSError, ValueError, TypeError):
        recorded = {}
    if not isinstance(catalog, list):
        catalog = []
    if not isinstance(recorded, dict):
        recorded = {}
    digest_entries: list[str] = []
    for clip in catalog:
        if not isinstance(clip, dict):
            continue
        clip_id = str(clip.get("clip_id") or "")
        entry = (recorded.get(clip_id) or {}) if isinstance(recorded, dict) else {}
        digest = str(entry.get("content_digest") or "")
        source_file = str(clip.get("source_file") or clip.get("path") or "")
        digest_entries.append(f"{clip_id}\x00{digest}\x00{source_file}")
    digest_entries.sort()
    try:
        root = Path(memory_root).expanduser() if memory_root else _sm.memory_root()
    except (OSError, ValueError, TypeError):
        root = None
    lane_stats: list[str] = []
    if root is not None:
        try:
            digests = sorted({str((recorded.get(str((c or {}).get("clip_id") or "")) or {}).get("content_digest") or "")
                             for c in catalog if isinstance(c, dict)} - {""})
        except (OSError, ValueError, TypeError):
            digests = []
        for digest in digests:
            try:
                lane_dir = _sm.source_dir(digest, root)
            except (OSError, ValueError, TypeError):
                lane_stats.append(f"{digest}:unreadable")
                continue
            try:
                if not lane_dir.is_dir():
                    lane_stats.append(f"{digest}:absent")
                    continue
                for child in sorted(lane_dir.iterdir(), key=lambda p: p.name):
                    try:
                        stat = child.stat()
                        lane_stats.append(
                            f"{digest}/{child.name}:{stat.st_size}:{stat.st_mtime_ns}")
                    except OSError:
                        lane_stats.append(f"{digest}/{child.name}:unreadable")
            except OSError:
                lane_stats.append(f"{digest}:unreadable")
    canonical = "\x00".join([
        "\n".join(digest_entries),
        "\n".join(sorted(lane_stats)),
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_stored_fingerprint(store_at: Path) -> dict[str, Any] | None:
    try:
        return json.loads(
            (Path(store_at) / "project_model" / EVIDENCE_FINGERPRINT_FILE).read_text(
                encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_stored_fingerprint(store_at: Path, fingerprint: str,
                              revision: int, report: dict[str, Any]) -> None:
    path = Path(store_at) / "project_model" / EVIDENCE_FINGERPRINT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "fingerprint": fingerprint,
            "revision": revision,
            "digests": report.get("digests", []),
            "persons": report.get("persons", []),
        }, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return cleaned or "reel"


def _sha16(*parts: Any) -> str:
    canonical = "\x00".join(
        json.dumps(part, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, default=str)
        for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _use_token(asset_id: str, role: str,
               source_span: tuple[int, int], ordinal: int = 0) -> str:
    """The durable id of one use of one asset in one role.

    `library.tools.editorial_identity` owns the identity; this is the
    model's spelling of it.  The model passes its PLAN role, which is
    richer than the media type a live read can compute - the two name
    the same thing at different resolutions, and the model's is the
    authority.
    """
    from library.tools.editorial_identity import use_token_of

    return use_token_of(asset_id, role, source_span, ordinal)


def _ordinal_of(seen: dict[tuple, int], asset_id: str, role: str,
                source_span: tuple[int, int]) -> int:
    """How many times this exact content has already been placed.

    One material can play twice in the same role over the same passage;
    the passage cannot separate those two uses, so their order does.
    """
    return seen.get((asset_id, role, tuple(source_span)), 0)


def _carried_use_id(model: dict[str, Any], asset_id: str, role: str,
                    source_span: tuple[int, int],
                    reel_prefix: str) -> str | None:
    """The id of the clip use this placement CONTINUES, when exactly one.

    A trim changes a clip's passage, so a content-derived id would change
    with it.  The clip use the trim came from is still in the model and
    the trimmed passage OVERLAPS it: that is the same editorial moment
    over a different extent, so its id is carried and its interval
    follows the placement.

    The search is scoped to this reel: another reel's use of the same
    footage is a different editorial moment on a different timeline, and
    carrying its id would move that reel's clip.

    More than one candidate is ambiguous, and minting fresh is the
    honest answer: two candidates mean the model cannot say which one
    this placement continues.
    """
    candidates = []
    for row in model.get("clip_uses") or []:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        if not str(row["id"]).startswith(reel_prefix):
            continue
        if row.get("asset_id") != asset_id or row.get("role") != role:
            continue
        interval = row.get("source_interval")
        if not isinstance(interval, dict):
            continue
        try:
            start = int(interval["start"])
            end = int(interval["end"])
        except (KeyError, TypeError, ValueError):
            continue
        # Half-open overlap, the same shape the model speaks in.
        if start < int(source_span[1]) and int(source_span[0]) < end:
            candidates.append(str(row["id"]))
    if len(candidates) == 1:
        return candidates[0]
    return None


def _refresh_clip_use_extent(model: dict[str, Any], use_id: str,
                             reel_clock: str, reel_span: tuple[int, int],
                             source_clock: str, source_span: tuple[int, int],
                             entity_links: list[str],
                             role: str) -> None:
    """Move a clip use's extent to where the placement put it.

    The same content at a different place on the reel is the same
    editorial moment, moved: its identity is its content, and only its
    extent follows the placement.  A rebuild that moved or trimmed a
    clip must not leave the model describing the old extent.
    """
    for row in model.get("clip_uses") or []:
        if not isinstance(row, dict) or row.get("id") != use_id:
            continue
        row["timeline_interval"] = {"clock_id": reel_clock,
                                    "start": reel_span[0], "end": reel_span[1]}
        row["source_interval"] = {"clock_id": source_clock,
                                  "start": source_span[0], "end": source_span[1]}
        if entity_links:
            row["entity_links"] = list(entity_links)
        if role:
            row["role"] = role
        return


def _finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def sec_to_ticks(start: Any, end: Any,
                 ticks_per_second: int = TICKS_PER_SECOND
                 ) -> tuple[int, int] | None:
    """Float seconds to half-open clock ticks, or None when refused.

    Refuses non-finite bounds and non-positive spans. The default clock
    is 120 kHz, which represents millisecond evidence and common video
    frame rates exactly; frame clocks may request a larger exact base.
    """
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start_f) and math.isfinite(end_f)):
        return None
    if isinstance(ticks_per_second, bool) or ticks_per_second <= 0:
        return None
    start_tick = round(start_f * ticks_per_second)
    end_tick = round(end_f * ticks_per_second)
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


def _frame_rate(fps: Any) -> dict[str, int] | None:
    try:
        value = float(fps)
        if not math.isfinite(value) or value <= 0:
            return None
        standard_rates = (Fraction(24000, 1001), Fraction(30000, 1001),
                          Fraction(60000, 1001))
        rate = next((candidate for candidate in standard_rates
                     if abs(value - float(candidate)) <= 0.001),
                    Fraction(str(value)).limit_denominator(100_000))
        if TICKS_PER_SECOND * rate.denominator % rate.numerator:
            return None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return {"numerator": rate.numerator, "denominator": rate.denominator}


def _ticks_per_second(model: dict[str, Any], clock_id: str) -> int:
    return int(next(clock for clock in model.get("clocks", [])
                    if clock.get("id") == clock_id)["ticks_per_second"])


def _clock_ticks_per_second(frame_rate: dict[str, int] | None) -> int:
    return TICKS_PER_SECOND


def _frame_ticks(model: dict[str, Any], clock_id: str, fps: Any) -> int:
    rate = _frame_rate(fps)
    if rate is None:
        raise ValueError(f"clock {clock_id!r} has no exact frame rate")
    tps = _ticks_per_second(model, clock_id)
    return tps * rate["denominator"] // rate["numerator"]


def _rebase_clock_references(model: dict[str, Any], clock_id: str,
                             multiplier: int) -> None:
    """Keep every stored time in seconds when upgrading a clock's tick rate."""
    def walk(value: Any, inherited: bool = False) -> None:
        if isinstance(value, list):
            for child in value:
                walk(child, inherited)
            return
        if not isinstance(value, dict):
            return
        owns_clock = value.get("clock_id") == clock_id
        interval = value.get("interval")
        has_timed_keys = (isinstance(interval, dict)
                          and interval.get("clock_id") == clock_id)
        active = inherited or owns_clock
        if active:
            for key in ("start", "end", "tick"):
                item = value.get(key)
                if isinstance(item, int) and not isinstance(item, bool):
                    value[key] = item * multiplier
        for key, child in value.items():
            child_is_timed = (active and key in ("samples", "keyframes"))
            child_is_keyed = has_timed_keys and key == "keyframes"
            walk(child, child_is_timed or child_is_keyed)

    for key in ("clip_uses", "observations", "masks", "tracks", "effects",
                "frame_maps", "relations", "intents"):
        walk(model.get(key, []))


def _clock(model: dict[str, Any], clock_id: str, domain: dict[str, Any],
           frame_rate: dict[str, int] | None = None) -> str:
    desired_tps = _clock_ticks_per_second(frame_rate)
    for clock in model.get("clocks") or []:
        if isinstance(clock, dict) and clock.get("id") == clock_id:
            old_tps = int(clock.get("ticks_per_second") or TICKS_PER_SECOND)
            new_tps = math.lcm(old_tps, desired_tps)
            if new_tps != old_tps:
                _rebase_clock_references(model, clock_id, new_tps // old_tps)
                clock["ticks_per_second"] = new_tps
            if clock.get("frame_rate") is None and frame_rate is not None:
                clock["frame_rate"] = frame_rate
            return clock_id
    model.setdefault("clocks", []).append({
        "id": clock_id,
        "domain": domain,
        "ticks_per_second": desired_tps,
        "frame_rate": frame_rate,
    })
    return clock_id


def asset_for_source(model: dict[str, Any], source_file: str,
                     track_type: str = "video",
                     frame_rate: Any = None) -> tuple[str, str] | None:
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
            _clock(model, clock_id, {"kind": "source", "asset_id": asset_id},
                   _frame_rate(frame_rate))
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
    _clock(model, clock_id, {"kind": "source", "asset_id": asset_id},
           _frame_rate(frame_rate))
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
          method: str, confidence: float | None = None,
          status: str = "candidate") -> None:
    link_id = f"link:{_sha16(entity_id, observation_id, method)}"
    for link in model.get("identity_links") or []:
        if isinstance(link, dict) and link.get("id") == link_id:
            return
    # Diarization labels are candidates, never verified: only a
    # measured speech-face link (Unit 3) verifies a person.
    # MU-07 on-screen coverage links are likewise candidates; the
    # confidence is the fraction of the clip use the span covers.
    model.setdefault("identity_links", []).append({
        "id": link_id,
        "entity_id": entity_id,
        "observation_id": observation_id,
        "method": method,
        "status": status,
        "confidence": confidence,
        "provenance_id": PROVENANCE_ID,
    })


def _face_track_owners(model: dict[str, Any]) -> dict[str, set[str]]:
    """Face track to roster-person owners, read off the migrated model.

    M7 ``on_screen`` spans name a face track, not a person; the track's
    owner is whichever person the evidence migration linked (face
    embedding cluster) to a face observation carrying that track.
    A track with no linked face observation has no owner here, never
    a guessed one.
    """
    track_of_obs: dict[str, str] = {}
    for obs in model.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        if obs.get("modality") != "face":
            continue
        payload = obs.get("payload") or {}
        track = payload.get("face_track") if isinstance(payload, dict) else None
        if track:
            track_of_obs[str(obs.get("id"))] = str(track)
    owners: dict[str, set[str]] = {}
    for link in model.get("identity_links") or []:
        if not isinstance(link, dict):
            continue
        track = track_of_obs.get(str(link.get("observation_id")))
        if track is None:
            continue
        entity_id = link.get("entity_id")
        if entity_id:
            owners.setdefault(track, set()).add(str(entity_id))
    return owners


def _on_screen_candidates(model: dict[str, Any], asset_id: str,
                          source_clock: str, start: int, end: int,
                          ) -> list[dict[str, Any]]:
    """Persons whose on-screen presence overlaps a use's source interval.

    Reads M7 ``on_screen`` event-span observations on the use's own
    asset and clock (the join is exact: a span on another asset or
    clock is never evidence about this use). Each candidate carries
    the covered fraction of the use (overlap over use length) as its
    confidence. Ambiguous overlap links every covering person, never
    collapsed to one.

    MU-05 region spans plug in beside M7 here once that lane lands:
    person-owned tracks with ``geometry.kind == "region"`` overlapping
    the interval contribute their entity (they carry no observation
    id, so no evidence row). Until then this branch is inert.
    """
    use_length = end - start
    if use_length <= 0:
        return []
    persons = {entity["id"] for entity in model.get("entities") or []
               if isinstance(entity, dict) and entity.get("kind") == "person"
               and entity.get("id")}
    owners = _face_track_owners(model)
    candidates: list[dict[str, Any]] = []
    for obs in model.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        if obs.get("modality") != "event_span":
            continue
        payload = obs.get("payload") or {}
        if not isinstance(payload, dict) or payload.get("predicate") != "on_screen":
            continue
        if obs.get("asset_id") != asset_id:
            continue
        interval = obs.get("interval") or {}
        if interval.get("clock_id") != source_clock:
            continue
        try:
            obs_start, obs_end = int(interval["start"]), int(interval["end"])
        except (TypeError, ValueError, KeyError):
            continue
        overlap = min(end, obs_end) - max(start, obs_start)
        if overlap <= 0:
            continue
        track = payload.get("face_track")
        for person_id in sorted(owners.get(str(track), set())):
            if person_id not in persons:
                continue
            fraction = round(overlap / use_length, 6)
            candidates.append({
                "entity_id": person_id,
                "observation_id": str(obs.get("id")),
                "confidence": min(max(fraction, 0.0), 1.0),
            })
    for track in model.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        entity_id = track.get("entity_id")
        if entity_id not in persons or track.get("clock_id") != source_clock:
            continue
        samples = track.get("samples") or []
        ticks = [s.get("tick") for s in samples
                 if isinstance(s, dict)
                 and isinstance(s.get("geometry"), dict)
                 and s["geometry"].get("kind") == "region"
                 and isinstance(s.get("tick"), int)]
        if not ticks:
            continue
        overlap = min(end, max(ticks)) - max(start, min(ticks))
        if overlap <= 0:
            continue
        if any(c["entity_id"] == entity_id for c in candidates):
            continue
        candidates.append({
            "entity_id": str(entity_id),
            "observation_id": None,
            "confidence": min(max(round(overlap / use_length, 6), 0.0), 1.0),
        })
    return candidates


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
        span = sec_to_ticks(row.get("source_start"), row.get("source_end"),
                            _ticks_per_second(model, clock_id))
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


def overlap_observations(model: dict[str, Any], transcript: dict | None,
                         speaker: str, source_file: str, asset_id: str,
                         clock_id: str, entity_id: str) -> list[str]:
    """MU-20: one ``overlap`` observation per measured crosstalk span
    this speaker's rows on this source sound in.

    The transcript's ``overlapped_speech`` spans are TIMELINE seconds; a
    row's own binding (``source_start - timeline_start``) carries the
    part of the span the row covers onto the source clock, as
    ``speech_observations`` does for the row itself. A transcript that
    was never measured mints nothing - "unknown" is not "no overlap",
    and a reader of the model sees no observation rather than a false
    negative. Returns the observation ids minted.
    """
    from library.tools import overlapped_speech

    minted: list[str] = []
    spans = overlapped_speech.spans_of(transcript)
    if not spans:
        return minted
    for index, row in enumerate((transcript or {}).get("segments") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("speaker") or "").strip() != str(speaker or "").strip():
            continue
        if str(row.get("source_file") or "") != str(source_file or ""):
            continue
        try:
            row_start = float(row["timeline_start"])
            row_end = float(row["timeline_end"])
            offset = float(row["source_start"]) - row_start
        except (KeyError, TypeError, ValueError):
            continue
        for span in spans:
            if speaker not in span.voices:
                continue
            start, end = max(row_start, span.start), min(row_end, span.end)
            interval = sec_to_ticks(start + offset, end + offset,
                                    _ticks_per_second(model, clock_id))
            if interval is None:
                continue
            obs_id = (f"obs:overlap:"
                      f"{_sha16(speaker, source_file, span.start, span.end, index)}")
            if not any(isinstance(obs, dict) and obs.get("id") == obs_id
                       for obs in model.get("observations") or []):
                model.setdefault("observations", []).append({
                    "id": obs_id,
                    "asset_id": asset_id,
                    "interval": {"clock_id": clock_id,
                                 "start": interval[0], "end": interval[1]},
                    "modality": "overlap",
                    "payload": {
                        "voices": list(span.voices),
                        "speaker_entity_id": entity_id,
                        "timeline_start": round(span.start, 3),
                        "timeline_end": round(span.end, 3),
                        "arm": transcript[overlapped_speech.RECORD_KEY]["arm"],
                    },
                    "confidence": None,
                    "provenance_id": PROVENANCE_ID,
                })
            _link(model, entity_id, obs_id, "diarization_speaker_label")
            minted.append(obs_id)
    return minted


def _reel_clock(model: dict[str, Any], reel_slug: str,
                timeline_name: str, fps: Any = None) -> str:
    return _clock(model, f"clock:reel:{reel_slug}",
                  {"kind": "timeline", "timeline_id": timeline_name},
                  _frame_rate(fps))


def clip_uses_from_placements(model: dict[str, Any], reel_slug: str,
                              timeline_name: str,
                              placements: list[dict[str, Any]],
                              track_roles: dict[tuple[str, int], str],
                              transcript: dict | None = None,
                              fps: float = 24.0) -> dict[str, Any]:
    """One ClipUse per placed piece, plus the reel's assemble edit.

    Roles come from the recorded track plan (``track_roles`` maps
    ``(track_type, track_index)`` to the plan role): A-roll picture
    rows mint ``a_roll``, everything else picture mints ``b_roll``,
    and audio rows use their recorded role (``speech``, ``music`` or
    ``sfx``), defaulting to ``speech`` when the plan has no recognized
    role. A placement with no source span or
    no reel span is refused with its reason, never clamped.
    Returns ``{"use_ids", "evidence_ids", "skipped"}``.
    """
    reel_clock = _reel_clock(model, reel_slug, timeline_name, fps)
    use_ids: list[str] = []
    evidence_ids: list[str] = []
    skipped: list[dict[str, Any]] = []
    use_id_by_index: dict[int, str] = {}
    seen_content: dict[tuple, int] = {}
    total_master = 0.0
    total_reel = 0.0
    for index, place in enumerate(placements):
        if not isinstance(place, dict):
            skipped.append({"index": index, "reason": "not a mapping"})
            continue
        track_type = str(place.get("track_type") or "")
        asset = asset_for_source(
            model, place.get("source_file"), track_type,
            place.get("source_fps") or fps)
        if asset is None:
            skipped.append({"index": index, "reason": "no source file"})
            continue
        asset_id, source_clock = asset
        source = sec_to_ticks(place.get("source_in"), place.get("source_out"),
                              _ticks_per_second(model, source_clock))
        reel_tps = _ticks_per_second(model, reel_clock)
        reel_frame_ticks = _frame_ticks(model, reel_clock, fps)
        if (place.get("record_frame") is not None
                and place.get("record_frames") is not None and fps > 0):
            try:
                record_frame = int(place["record_frame"])
                record_frames = int(place["record_frames"])
                reel_span = (
                    record_frame * reel_frame_ticks,
                    (record_frame + record_frames) * reel_frame_ticks,
                )
                if record_frame < 0 or record_frames <= 0:
                    reel_span = None
            except (TypeError, ValueError, OverflowError):
                reel_span = None
        else:
            reel_span = sec_to_ticks(place.get("record_start"),
                                  float(place.get("record_start", 0.0))
                                  + max(float(place.get("source_out", 0.0))
                                        - float(place.get("source_in", 0.0)), 0.0),
                                  reel_tps)
        if source is None:
            skipped.append({"index": index, "reason": "empty source span"})
            continue
        if reel_span is None:
            skipped.append({"index": index, "reason": "empty reel span"})
            continue
        try:
            track_index = int(place.get("track_index", 0))
        except (TypeError, ValueError):
            track_index = 0
        role = track_roles.get((track_type, track_index), "")
        if track_type == "audio":
            # The recorded track plan is the authority for audio intent.
            # Treating every audio row as dialogue made the model bridge
            # erase the music/SFX roles the FilmCraft adapter already
            # knows how to place on separate stems.
            use_role = role if role in {"speech", "music", "sfx"} else "speech"
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
                evidence_ids.extend(overlap_observations(
                    model, transcript, str(speaker),
                    str(place.get("source_file") or ""),
                    asset_id, source_clock, entity_id))
        if not entity_links:
            # MU-07: no speaker label resolved, so a face-only person
            # would never reach A-roll or a render. Link every person
            # whose M7 on-screen span (or MU-05 region span, once
            # present) overlaps this use's source interval, as
            # candidates with the covered fraction as confidence.
            for candidate in _on_screen_candidates(
                    model, asset_id, source_clock, source[0], source[1]):
                if candidate["entity_id"] not in entity_links:
                    entity_links.append(candidate["entity_id"])
                obs_id = candidate.get("observation_id")
                if obs_id is not None:
                    if obs_id not in evidence_ids:
                        evidence_ids.append(obs_id)
                    _link(model, candidate["entity_id"], obs_id,
                          "on_screen_coverage",
                          confidence=candidate.get("confidence"))
        # The clip use's id is derived from the CONTENT it plays - the
        # asset, the role and the passage - and NOT from the clip's
        # position in the placement list.  A positional id renames every
        # later clip when one is inserted in front of them, and because
        # the minting SKIPS an id it already holds, that rename left the
        # model describing the clip that used to be there: the model
        # said "clip use 0003 is the interview at 0:12" while 0003 was
        # now a different shot.  A content-derived id keeps every other
        # clip's identity when one is inserted, and gives the inserted
        # clip its own.
        #
        # A TRIM changes the passage, so the id this placement CONTINUES
        # is carried when the model still holds it: the same editorial
        # moment over a different extent keeps its identity.
        ordinal = _ordinal_of(seen_content, asset_id, use_role, source)
        seen_content[(asset_id, use_role, source)] = ordinal + 1
        use_id = _carried_use_id(model, asset_id, use_role, source,
                                 f"use:{reel_slug}:")
        if use_id is None or use_id in use_ids:
            use_id = (f"use:{reel_slug}:"
                      f"{_use_token(asset_id, use_role, source, ordinal)}")
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
        else:
            # The same content at a different place on the reel is the
            # same editorial moment, moved.  Its extent follows the
            # placement; its identity does not change.
            _refresh_clip_use_extent(
                model, use_id, reel_clock, reel_span, source_clock, source,
                entity_links, use_role)
        use_ids.append(use_id)
        use_id_by_index[index] = use_id
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
            "skipped": skipped, "use_id_by_index": use_id_by_index}


def static_transforms_from_placements(
        model: dict[str, Any], reel_slug: str, placements: list[dict[str, Any]],
        use_id_by_placement: dict[int, str]) -> dict[str, Any]:
    """Migrate recorded static scale and native-pixel position effects.

    Resolve Pan/Tilt and Zoom are converted by the recorder into native
    sequence pixels and source-pixel scale. ``native_position`` applies
    the shared transform law; ``native_scale`` includes Resolve's
    scale-to-fit base scale, which FilmCraft's source-pixel motion scale
    does not apply automatically.
    """
    minted: list[str] = []
    skipped: list[dict[str, Any]] = []
    for index, place in enumerate(placements):
        if isinstance(place, dict) and place.get("native_transform_refusal"):
            skipped.append({"index": index,
                            "reason": place["native_transform_refusal"]})
            continue
        transform = place.get("transform") if isinstance(place, dict) else None
        if not isinstance(transform, dict):
            continue
        target_id = use_id_by_placement.get(id(place))
        if target_id is None:
            skipped.append({"index": index, "reason": "placement minted no clip use"})
            continue
        use = next((row for row in model.get("clip_uses") or []
                    if isinstance(row, dict) and row.get("id") == target_id), None)
        if use is None:
            skipped.append({"index": index, "reason": "target clip use is not in the model"})
            continue
        scale_x = transform.get("ZoomX")
        scale_y = transform.get("ZoomY")
        if scale_x is not None or scale_y is not None:
            try:
                sx, sy = float(scale_x), float(scale_y)
            except (TypeError, ValueError):
                skipped.append({"index": index, "reason": "Resolve zoom is not numeric"})
            else:
                if not (math.isfinite(sx) and math.isfinite(sy)) or sx <= 0 or sy <= 0:
                    skipped.append({"index": index, "reason": "Resolve zoom must be finite and positive"})
                elif not math.isclose(sx, sy, rel_tol=0.0, abs_tol=1e-6):
                    skipped.append({"index": index, "reason": "non-uniform ZoomX/ZoomY has no uniform native scale"})
                else:
                    native_scale = place.get("native_scale")
                    if native_scale is None:
                        skipped.append({"index": index,
                                        "reason": "Resolve zoom needs measured source and sequence dimensions for the native scaleToFit conversion"})
                    else:
                        try:
                            native_scale = float(native_scale)
                        except (TypeError, ValueError):
                            skipped.append({"index": index,
                                            "reason": "native_scale must be numeric"})
                        else:
                            if (not math.isfinite(native_scale)
                                    or native_scale <= 0):
                                skipped.append({"index": index,
                                                "reason": "native_scale must be finite and positive"})
                            elif not math.isclose(native_scale, 1.0,
                                                 rel_tol=0.0,
                                                 abs_tol=1e-6):
                                minted.append(_mint_static_transform(
                                    model, reel_slug, "transform.scale",
                                    target_id,
                                    dict(use["timeline_interval"]),
                                    {"scale": native_scale}, "scale"))
        native_position = place.get("native_position")
        if native_position is not None:
            if (not isinstance(native_position, (list, tuple))
                    or len(native_position) != 2):
                skipped.append({"index": index, "reason": "native_position must be an [x, y] pair"})
            else:
                try:
                    position = [float(value) for value in native_position]
                except (TypeError, ValueError):
                    skipped.append({"index": index, "reason": "native_position must contain numbers"})
                else:
                    if not all(math.isfinite(value) for value in position):
                        skipped.append({"index": index, "reason": "native_position must be finite"})
                    elif position != [0.0, 0.0]:
                        minted.append(_mint_static_transform(
                            model, reel_slug, "transform.position", target_id,
                            dict(use["timeline_interval"]), {"position": position}, "position"))
        elif any(transform.get(key, 0) not in (0, 0.0, None) for key in ("Pan", "Tilt")):
            skipped.append({"index": index,
                            "reason": "Resolve Pan/Tilt needs measured source and sequence dimensions before conversion to native pixels"})
    return {"minted": minted, "skipped": skipped}


def overlay_uses_from_records(model: dict[str, Any], reel_slug: str,
                              reel_clock: str, records: list[dict[str, Any]],
                              fps: float) -> dict[str, Any]:
    """Migrate the Resolve build's measured overlay placements.

    `place_overlay_segments` records the held Resolve transform, exact reel
    frame span and `native_position` of the tight canvas center. That center
    already includes the tight-canvas offset: native FilmCraft positions its
    source around that center, scales source pixels by the held ZoomX, and
    uses the recorded frame span. Do not rebuild this geometry from the
    header declaration here; the Resolve read-back is the shared placement.
    """
    use_ids: list[str] = []
    effect_ids: list[str] = []
    skipped: list[dict[str, Any]] = []
    overlay_seen: dict[tuple, int] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            skipped.append({"index": index, "reason": "not an overlay placement record"})
            continue
        source_file = str(record.get("overlay_path") or "")
        if not source_file:
            skipped.append({"index": index, "reason": "overlay record names no media file"})
            continue
        try:
            start_frame = int(record["timeline_start_frame"])
            source_frame = int(record["source_start_frame"])
            duration_frames = int(record["duration_frames"])
        except (KeyError, TypeError, ValueError):
            skipped.append({"index": index, "reason": "overlay record lacks exact frame spans"})
            continue
        if start_frame < 0 or source_frame < 0 or duration_frames <= 0 or fps <= 0:
            skipped.append({"index": index, "reason": "overlay frame spans or rate are not positive"})
            continue
        asset = asset_for_source(model, source_file, "video", fps)
        if asset is None:
            skipped.append({"index": index, "reason": "overlay media path is empty"})
            continue
        asset_id, source_clock = asset
        try:
            source_frame_ticks = _frame_ticks(model, source_clock, fps)
            reel_frame_ticks = _frame_ticks(model, reel_clock, fps)
        except ValueError:
            skipped.append({"index": index, "reason": "overlay frame rate is invalid"})
            continue
        source = (source_frame * source_frame_ticks,
                  (source_frame + duration_frames) * source_frame_ticks)
        timeline = (start_frame * reel_frame_ticks,
                    (start_frame + duration_frames) * reel_frame_ticks)
        kind = str(record.get("kind") or "").lower()
        role = ("end_card" if "end_card" in kind or kind == "logo"
                else "post_header" if "post header" in kind or "post_header" in kind
                else "subtitle_overlay" if "caption" in kind or "subtitle" in kind
                else "frame_overlay" if "frame" in kind
                else "motion_graphic")
        # The overlay's id is derived from its CONTENT and its role, for
        # the same reason the clip uses' ids are: a positional id
        # renames every later overlay when one is inserted in front of
        # them.
        ordinal = _ordinal_of(overlay_seen, asset_id, role, source)
        overlay_seen[(asset_id, role, tuple(source))] = ordinal + 1
        use_id = (f"use:{reel_slug}:overlay:"
                  f"{_use_token(asset_id, role, source, ordinal)}")
        if not any(isinstance(row, dict) and row.get("id") == use_id
                   for row in model.get("clip_uses") or []):
            model.setdefault("clip_uses", []).append({
                "id": use_id, "asset_id": asset_id,
                "source_interval": {"clock_id": source_clock,
                                     "start": source[0], "end": source[1]},
                "timeline_interval": {"clock_id": reel_clock,
                                      "start": timeline[0], "end": timeline[1]},
                "entity_links": [], "role": role, "intent_id": None,
            })
        else:
            _refresh_clip_use_extent(
                model, use_id, reel_clock, timeline, source_clock, source,
                [], role)
        use_ids.append(use_id)
        overlay_transform = record.get("transform")
        native_scale = record.get("native_scale")
        if (native_scale is None and isinstance(overlay_transform, dict)):
            # Tight overlays are placed at Resolve Scaling=1; its read-back
            # ZoomX is the native pixel scale and stays the scale authority.
            native_scale = overlay_transform.get("ZoomX")
        transform_place = {
            "transform": overlay_transform,
            "native_position": record.get("native_position"),
            "native_scale": native_scale,
        }
        transform_report = static_transforms_from_placements(
            model, reel_slug, [transform_place],
            {id(transform_place): use_id})
        effect_ids.extend(transform_report["minted"])
        skipped.extend({"index": index, **row}
                       for row in transform_report["skipped"])
    return {"use_ids": use_ids, "effects": effect_ids, "skipped": skipped}


def _mint_static_transform(model: dict[str, Any], reel_slug: str,
                            class_id: str, target_id: str,
                            interval: dict[str, Any], parameters: dict[str, Any],
                            suffix: str) -> str:
    effect_id = (f"effect:{reel_slug}:transform:{_sha16(target_id)}:"
                 f"{suffix}")
    if not any(isinstance(row, dict) and row.get("id") == effect_id
               for row in model.get("effects") or []):
        model.setdefault("effects", []).append({
            "id": effect_id, "class_id": class_id, "target_id": target_id,
            "interval": interval, "parameters": parameters, "keyframes": [],
        })
    return effect_id


def word_gap_ducking_effects(model: dict[str, Any], reel_slug: str,
                             reel_clock: str, placements: list[dict[str, Any]],
                             use_id_by_placement: dict[int, str],
                             audio_mix_spec: dict[str, Any] | None
                             ) -> dict[str, Any]:
    """Carry the measured word-gap mix onto native music uses.

    ``audio_mix_spec`` is on master-spine time. Reel speech placements
    record the master-to-reel offset, and music ClipUses provide the
    destination interval. The effect is minted only where all three
    measurements overlap; no timeline span is guessed.
    """
    spec = audio_mix_spec if isinstance(audio_mix_spec, dict) else {}
    plan = spec.get("music_ducking_plan") or {}
    if not plan.get("enabled"):
        return {"minted": [], "skipped": []}
    duck_db = plan["duck_db"]
    release_ms = plan["release_ms"]
    if (not _finite_number(duck_db) or duck_db <= 0
            or not _finite_number(release_ms) or release_ms <= 0):
        return {"minted": [], "skipped": [{
            "reason": "enabled music ducking needs positive duck_db and release_ms"}]}

    music_uses = [row for row in model.get("clip_uses") or []
                  if isinstance(row, dict) and row.get("role") == "music"]
    if not music_uses:
        return {"minted": [], "skipped": [{
            "reason": "music ducking is enabled but the reel has no music ClipUse"}]}
    tps = _ticks_per_second(model, reel_clock)
    automation = spec["music_automation"]
    words = []
    for row in automation:
        if not row.get("word_intervals"):
            continue
        full_db = row["word_gap_level_db"]
        if not _finite_number(full_db):
            continue
        for interval in row["word_intervals"]:
            words.append((float(interval[0]), float(interval[1]),
                          float(full_db)))

    minted: list[str] = []
    skipped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for place in placements:
        if not isinstance(place, dict) or place.get("track_type") != "audio":
            continue
        speech_id = use_id_by_placement.get(id(place))
        speech_use = next((row for row in model.get("clip_uses") or []
                           if isinstance(row, dict) and row.get("id") == speech_id),
                          None)
        if speech_use is None or speech_use.get("role") != "speech":
            continue
        try:
            master_start = float(place["master_start"])
            master_end = float(place["master_end"])
            reel_start = float(place["record_start"])
            reel_end = reel_start + master_end - master_start
        except (KeyError, TypeError, ValueError):
            skipped.append({"placement": speech_id,
                            "reason": "speech placement lacks a measured master-to-reel span"})
            continue
        for word_start, word_end, full_db in words:
            start = reel_start + max(word_start, master_start) - master_start
            end = reel_start + min(word_end, master_end) - master_start
            if end <= start:
                continue
            for music in music_uses:
                music_interval = music["timeline_interval"]
                music_start = music_interval["start"] / tps
                music_end = music_interval["end"] / tps
                clipped_start = max(start, music_start)
                clipped_end = min(end, music_end, reel_end)
                if clipped_end <= clipped_start:
                    continue
                interval = sec_to_ticks(clipped_start, clipped_end, tps)
                if interval is None:
                    continue
                signature = (music["id"], interval[0], interval[1])
                if signature in seen:
                    continue
                seen.add(signature)
                effect_id = (f"effect:{reel_slug}:duck:{_sha16(*signature)}")
                if not any(isinstance(row, dict) and row.get("id") == effect_id
                           for row in model.get("effects") or []):
                    model.setdefault("effects", []).append({
                        "id": effect_id,
                        "class_id": "audio.ducking",
                        "target_id": music["id"],
                        "interval": {"clock_id": reel_clock,
                                     "start": interval[0], "end": interval[1]},
                        "parameters": {
                            "full_db": full_db,
                            "depth_db": duck_db,
                            "ramp_ticks": round(release_ms * tps / 1000.0),
                        },
                        "keyframes": [],
                    })
                minted.append(effect_id)
    if not minted and not skipped:
        skipped.append({"reason": "declared ducking overlaps no placed music and speech"})
    return {"minted": minted, "skipped": skipped}


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
        if isinstance(copy, str) and copy.strip() or isinstance(copy, dict) and copy:
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

    A reel cut from several sources carries one segment per source
    clock; ``from_clock`` names the first segment's clock only, and
    readers judge a tick against the segments carrying its own clock
    (MU-21: ``frame_map.rs::map_tick`` and the ``resolve_swapback``
    mirror match per segment, so a tick from one source never maps
    against another's).
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
                        masks: list[dict[str, Any]] | None = None,
                        overlay_placements: list[dict[str, Any]] | None = None,
                        fps: float = 24.0,
                        audio_mix_spec: dict[str, Any] | None = None,
                        validate: bool = True
                        ) -> dict[str, Any]:
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
    for track in ((track_plan or {}).get("audio_tracks") or []):
        if isinstance(track, dict) and track.get("role") in {
                "speech", "music", "sfx"}:
            try:
                roles[("audio", int(track.get("index", 0)))] = track["role"]
            except (TypeError, ValueError):
                pass
    reel_clock = _reel_clock(model, reel_slug, reel_name, fps)
    report: dict[str, Any] = {"reel": reel_name, "reel_slug": reel_slug}
    uses = clip_uses_from_placements(model, reel_slug, reel_name,
                                     placements, roles, transcript, fps)
    report["clip_uses"] = len(uses["use_ids"])
    report["clip_uses_skipped"] = uses["skipped"]
    # The mapping comes from the minting itself.  Re-deriving it from
    # the placement INDEX was wrong twice over: the id scheme is the
    # minting's business, and an index is not a placement - a placement
    # the minting skipped (no source span, no reel span) shifted every
    # later placement's index, so the mapping pointed at the wrong clip
    # or at nothing at all.
    use_id_by_placement = {
        id(place): use_id
        for index, place in enumerate(placements)
        if isinstance(place, dict)
        for use_id in [uses["use_id_by_index"].get(index)]
        if use_id
    }
    ducking = word_gap_ducking_effects(
        model, reel_slug, reel_clock, placements, use_id_by_placement,
        audio_mix_spec)
    report["word_gap_ducking_effects"] = ducking["minted"]
    report["word_gap_ducking_skipped"] = ducking["skipped"]
    transforms = static_transforms_from_placements(
        model, reel_slug, placements, use_id_by_placement)
    report["static_transforms"] = transforms["minted"]
    report["static_transforms_skipped"] = transforms["skipped"]
    overlays = overlay_uses_from_records(
        model, reel_slug, reel_clock, list(overlay_placements or []), fps)
    report["overlay_uses"] = overlays["use_ids"]
    report["overlay_transform_effects"] = overlays["effects"]
    report["overlay_transforms_skipped"] = overlays["skipped"]
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
    end_card_use_ids = [
        use_id for use_id in overlays["use_ids"]
        if any(isinstance(use, dict) and use.get("id") == use_id
               and use.get("role") == "end_card"
               for use in model.get("clip_uses") or [])]
    report["frame_map"] = frame_map_for_reel(
        model, reel_slug, reel_clock, uses["use_ids"] + end_card_use_ids)
    if validate:
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
            "audio_mix_spec": capability_outputs.value(
                state, "audio_mix", "audio_mix_spec"),
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
                        reason: str | None = None,
                        audio_mix_spec: dict[str, Any] | None = None
                        ) -> dict[str, Any]:
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
                         motion_graphics_overlay, audio_mix_spec, masks,
                         author, reason)
    except Exception as exc:  # noqa: BLE001 - fail-soft by contract
        return {"status": "refused",
                "reason": f"project model not updated: {exc!r}"}


def _maintain(project_folder: str, store_at: Path, slug: str,
              build_record: dict, transcript: dict | None,
              enhancement_spec: dict | None,
              motion_graphics_overlay: dict | None,
              audio_mix_spec: dict | None,
              masks: list[dict[str, Any]] | None,
              author: str, reason: str | None) -> dict[str, Any]:
    from library.tools import project_model_store as store
    record = build_record if isinstance(build_record, dict) else {}
    placements_by_reel = record.get("reel_placements")
    if not isinstance(placements_by_reel, dict) or not placements_by_reel:
        return {"status": "refused",
                "reason": "build record carries no reel_placements; "
                          "the model cannot grade what was not recorded"}
    if (enhancement_spec is None or motion_graphics_overlay is None
            or audio_mix_spec is None):
        state = _state_outputs(project_folder)
        if enhancement_spec is None:
            enhancement_spec = state.get("enhancement_spec")
        if motion_graphics_overlay is None:
            motion_graphics_overlay = state.get("motion_graphics_overlay")
        if audio_mix_spec is None:
            audio_mix_spec = state.get("audio_mix_spec")
    spec = enhancement_spec if isinstance(enhancement_spec, dict) else {}
    overlay = (motion_graphics_overlay
               if isinstance(motion_graphics_overlay, dict) else {})
    try:
        expected_revision, stored = store.load_project_model(store_at)
    except (FileNotFoundError, ValueError):
        expected_revision, stored = 0, None
    # The evidence base: what the preflight's lanes measured, migrated
    # through Unit 3. A rebuild whose evidence inputs have not changed
    # reuses the stored base item-for-item instead of re-migrating (58 s
    # on the measured build). The fingerprint covers catalog digests and
    # lane file stats, never lane contents; a mismatch re-migrates.
    current_fingerprint: str | None = None
    try:
        current_fingerprint = evidence_fingerprint(project_folder)
    except Exception:  # noqa: BLE001 - fingerprint failure re-migrates
        current_fingerprint = None
    stored_fingerprint = _read_stored_fingerprint(store_at)
    fingerprint_match = (
        stored is not None
        and current_fingerprint is not None
        and isinstance(stored_fingerprint, dict)
        and stored_fingerprint.get("fingerprint") == current_fingerprint
    )
    evidence_report: dict[str, Any] = {}
    if fingerprint_match:
        base = blank_model(f"project-{slug}")
        evidence_note = (
            f"reused stored evidence "
            f"({len(stored_fingerprint.get('digests', []))} digest(s), "
            f"{len(stored_fingerprint.get('persons', []))} person(s), "
            f"fingerprint {current_fingerprint[:12]})")
    else:
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
    if stored is None:
        model = base
    elif fingerprint_match:
        # The evidence base is unchanged (fingerprint match) and the blank
        # base carries no rows, so the merge is a no-op: keep the stored
        # model verbatim instead of filtering 116k rows against an empty set.
        model = stored
        ensure_provenance(model)
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
    overlay_placements_by_reel = record.get("reel_overlay_placements")
    if not isinstance(overlay_placements_by_reel, dict):
        overlay_placements_by_reel = {}
    try:
        reel_fps = float(record.get("reel_fps", 24.0))
    except (TypeError, ValueError):
        reel_fps = 24.0
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
                   if isinstance(mask, dict)] or None,
            overlay_placements=[row for row in
                                overlay_placements_by_reel.get(reel_name, [])
                                if isinstance(row, dict)],
            audio_mix_spec=(audio_mix_spec
                            if isinstance(audio_mix_spec, dict) else None),
            fps=reel_fps, validate=False))
    # One store revision per build. The commit validates the complete
    # merged document once, before writing; per-fragment and immediate
    # duplicate validation each walked every source observation again.
    # When the evidence base is unchanged (fingerprint hit) only the
    # reel-scoped rows differ, so a small merge_patch carries exactly
    # those keys and the 350 MB event log stops growing a full model
    # per build. A fresh evidence base still commits a full replace.
    reels_named = sorted(placements_by_reel)
    write_reason = reason or f"reel.build: {', '.join(reels_named)}"

    def _commit(expected: int, current: dict) -> int:
        if stored is not None and fingerprint_match:
            patch = {key: current.get(key) or []
                     for key in REEL_COMMIT_KEYS}
            return store.append_model_patch(
                store_at, patch, expected_revision=expected,
                author=author, reason=write_reason)
        return store.commit_project_model(
            store_at, current, expected_revision=expected,
            author=author, reason=write_reason)

    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            revision = _commit(expected_revision, model)
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
                           if isinstance(mask, dict)] or None,
                    overlay_placements=[row for row in
                                        overlay_placements_by_reel.get(reel_name, [])
                                        if isinstance(row, dict)],
                    audio_mix_spec=(audio_mix_spec
                                    if isinstance(audio_mix_spec, dict)
                                    else None),
                    fps=reel_fps, validate=False)
            validate_project_model(model)
    else:
        return {"status": "refused",
                "reason": f"store contention, last error: {last_error!r}"}
    if not fingerprint_match and current_fingerprint is not None:
        _write_stored_fingerprint(store_at, current_fingerprint,
                                  revision, evidence_report)
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
            frames = round(float(values["duration"]) * rate)
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
    before_collections = {
        key: {row["id"]: row for row in model.get(key) or []}
        for key in ("clocks", "receipts", "frame_maps", "renders", "provenance")
    }
    delivered_asset_id = f"asset:render:{reel_slug}"
    had_asset = any(row.get("id") == delivered_asset_id
                    for row in model.get("assets") or [])
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
        rate_frac = Fraction(str(float(rate))).limit_denominator(1001)
        render_tps = _clock_ticks_per_second({
            "numerator": rate_frac.numerator,
            "denominator": rate_frac.denominator})
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
    render_frame_ticks = (render_tps * rate_frac.denominator
                          // rate_frac.numerator) if rate_frac else 1
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
        # Reel clock ticks onto render frames, proportionally: the
        # delivered file plays the whole picture contiguously, so the
        # reel's end lands exactly on the expected frame count.
        to_start = round((span["start"] - reel_start) / reel_span * frames)
        to_end = round((span["end"] - reel_start) / reel_span * frames)
        if to_end <= to_start:
            return {"status": "refused",
                    "reason": f"reel {timeline_name!r} use {row['id']!r} "
                              f"covers no render frame"}
        segments.append({
            "from": dict(span),
            "to": {"clock_id": render_clock,
                   "start": to_start * render_frame_ticks,
                   "end": to_end * render_frame_ticks},
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
        )
        from library.tools.plan_provenance import (
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
    changes = []
    for collection in ("clocks", "receipts", "frame_maps", "renders",
                       "provenance"):
        old_rows = before_collections[collection]
        new_rows = {row["id"]: row for row in model.get(collection) or []}
        changes.extend(
            {"collection": collection, "operation": "delete", "id": record_id}
            for record_id in sorted(old_rows.keys() - new_rows.keys()))
        changes.extend(
            {"collection": collection,
             "operation": "update" if record_id in old_rows else "append",
             "id": record_id, "record": row}
            for record_id, row in sorted(new_rows.items())
            if old_rows.get(record_id) != row)
    if not had_asset:
        asset = next(row for row in model.get("assets") or []
                     if row.get("id") == delivered_asset_id)
        changes.append({"collection": "assets", "operation": "append",
                        "id": asset["id"], "record": asset})
    try:
        revision = store.commit_project_model_records(
            store_at, changes, expected_revision=expected_revision,
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
        # The clip use's id is `use:<reel>:<token>`, where the token is
        # derived from the content.  The POSITIONAL scheme
        # (`use:<reel>:0000`) is still read, because a project built
        # before the ids became content-derived still carries those rows.
        match = re.match(r"^use:([^:]+):(?:overlay:)?(?:[0-9a-f]{16}|\d{4})$",
                         str(use.get("id", "")))
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
        raise TypeError("pipeline state carries no reel_build record")
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
