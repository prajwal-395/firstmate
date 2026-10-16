"""Project Model adapter for optional step 1.07 OCR/UI artifacts.

The per-clip OCR JSON stays the spatial artifact authority. The Project Model
stores typed entities, appearance intervals, sampled geometry and verified
content references in the same pattern used by M3d region masks.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

from library.tools import evidence_contract, project_model_masks
from library.tools.project_layout import Area, ProjectLayout

PRODUCER = "library.steps.step_1_07_ocr_extraction"
OCR_TRACK_MASK_KIND = project_model_masks.OCR_POLYGON_TRACK_KIND
TICKS_PER_SECOND = 1000


def _intervals(row: dict[str, Any], sample_times: list[float], sample_fps: float) -> list[tuple[float, float]]:
    intervals = row.get("appearance_intervals") or row.get("appearances") or []
    result = []
    for interval in intervals:
        try:
            if isinstance(interval, dict):
                start = float(interval["start_seconds"])
                end = float(interval["end_seconds"])
            else:
                start, end = float(interval[0]), float(interval[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            continue
        if not isinstance(interval, dict):
            # Legacy pair format stores the last sampled point inclusively.
            # Extend it by one declared sample period, not to the next scene.
            end += 1.0 / sample_fps if sample_fps > 0 else 0.001
        result.append((start, end))
    if not result and sample_times:
        period = 1.0 / sample_fps if sample_fps > 0 else 0.001
        result = [(min(sample_times), max(sample_times) + period)]
    return [(start, end) for start, end in result if end > start]


def _add_track(builder, *, digest: str, asset_id: str, clock_id: str,
               clip_id: str, artifact_path: str, artifact_sha256: str,
               track: dict[str, Any], category: str, index: int,
               sample_fps: float, sample_resolution: list[int]) -> bool:
    samples = track.get("samples")
    if not isinstance(samples, list) or not samples:
        return False
    normalized = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        try:
            timestamp = float(sample["timestamp"])
            box = [float(v) for v in sample["bbox_normalized"]]
            confidence = (None if sample.get("confidence") is None
                          else float(sample["confidence"]))
            frame_index = sample["frame_index"]
        except (KeyError, TypeError, ValueError):
            continue
        if (not math.isfinite(timestamp) or timestamp < 0 or len(box) != 4
                or not all(math.isfinite(v) for v in box)
                or box[0] < 0 or box[1] < 0 or box[2] <= 0 or box[3] <= 0
                or box[0] + box[2] > 1 or box[1] + box[3] > 1
                or type(frame_index) is not int or frame_index < 0
                or (confidence is not None and not 0 <= confidence <= 1)):
            continue
        tick = round(timestamp * TICKS_PER_SECOND)
        polygon = sample.get("polygon_normalized") or []
        normalized.append({"tick": tick, "timestamp": timestamp,
                           "bbox": box, "polygon": polygon,
                           "confidence": confidence,
                           "frame_index": frame_index,
                           "visual_state": sample.get("visual_state")})
    normalized.sort(key=lambda row: row["tick"])
    if not normalized or len({row["tick"] for row in normalized}) != len(normalized):
        return False
    sample_times = [row["timestamp"] for row in normalized]
    intervals = _intervals(track, sample_times, sample_fps)
    if not intervals:
        return False
    prefix = "text" if category == "text" else "graphic"
    track_key = str(track.get("track_id") or f"{prefix}-{index:04d}")
    suffix = hashlib.sha256(clip_id.encode("utf-8")).hexdigest()[:10]
    entity_id = f"entity-{prefix}-{digest}-{suffix}-{track_key}"
    track_id = f"track-{prefix}-{digest}-{suffix}-{track_key}"
    provenance_id = builder.provenance(digest, "ocr_extraction", PRODUCER)
    if not any(row["id"] == entity_id for row in builder.model["entities"]):
        label = track.get("text") if category == "text" else str(track.get("kind", "graphic candidate"))
        builder.model["entities"].append({
            "id": entity_id, "kind": "other" if category == "text" else "object",
            "label": label, "object_class_id": None,
            # Lane 3: an OCR/UI track holds for its source asset.
            "identity_scope": "asset",
        })
    observation_ids = []
    observation_scopes = []
    for appearance_index, (start, end) in enumerate(intervals):
        scope_start = math.floor(start * TICKS_PER_SECOND)
        scope_end = math.ceil(end * TICKS_PER_SECOND)
        scoped_samples = [row for row in normalized
                          if scope_start <= row["tick"] < scope_end]
        if not scoped_samples:
            continue
        payload = {
            "track_id": track_key,
            "category": category,
            "text": track.get("text") if category == "text" else None,
            "language": track.get("language") if category == "text" else None,
            "alternatives": track.get("alternatives", []) if category == "text" else [],
            "appearance_index": appearance_index,
            "sample_count": len(scoped_samples),
            "artifact_ref": {"path": artifact_path,
                             "sha256": artifact_sha256,
                             "track_id": track_key},
        }
        observation_confidence = (
            sum(row["confidence"] for row in scoped_samples
                if row["confidence"] is not None)
            / sum(row["confidence"] is not None for row in scoped_samples)
            if any(row["confidence"] is not None for row in scoped_samples)
            else None)
        references = tuple({
            "kind": "source_frame", "stream_id": f"stream-{digest}",
            "frame_index": int(row["frame_index"]),
        } for row in scoped_samples if row["frame_index"] is not None)
        native = evidence_contract.new_measurement(
            asset_id=asset_id, clock_id=clock_id,
            start_tick=scope_start, end_tick=scope_end,
            measurement_type=("image.ocr_text_track" if category == "text"
                              else "image.graphic_region_track"),
            value=payload, method="easyocr-ui-contours-v2",
            provenance_id=provenance_id,
            sample_resolution=sample_resolution,
            target_entity_id=entity_id,
            target_region="text" if category == "text" else "graphic_candidate",
            coverage=evidence_contract.CoverageKind.SAMPLED,
            coverage_samples=len(scoped_samples),
            coverage_sample_rate_hz=sample_fps,
        )
        native = replace(native, references=references)
        observation_id = builder.observation(
            digest, "ocr_extraction", asset_id, clock_id, start, end,
            "visual_text_track" if category == "text" else "visual_graphic_candidate",
            payload, observation_confidence, PRODUCER, evidence=native,
        )
        if observation_id:
            observation = next(row for row in builder.model["observations"]
                               if row["id"] == observation_id)
            observation["certainty"] = "measured"
            if observation_confidence is not None:
                observation["confidence_detail"] = {
                    "value": observation_confidence, "kind": "raw_score",
                    "producer": "easyocr" if category == "text" else "opencv-contours",
                }
            observation_ids.append(observation_id)
            observation_scopes.append((scope_start, scope_end, observation_id))
    if not observation_ids:
        return False
    builder.model["tracks"].append({
        "id": track_id, "entity_id": entity_id, "clock_id": clock_id,
        # Lane 3: the OCR tracker's own name, distinct from the entity.
        "local_track_id": track_key,
        "kind": "ocr_text" if category == "text" else str(track.get("kind", "graphic_candidate")),
        "coverage": "analyzed", "continuity": "sparse OCR/UI samples",
        "provenance_id": provenance_id,
        "samples": [{
            "tick": row["tick"],
            "interval": {"clock_id": clock_id, "start": row["tick"],
                         "end": row["tick"] + 1},
            "geometry": {"kind": "text_box" if category == "text" else "graphic_box",
                         "bbox": row["bbox"], "polygon": row["polygon"],
                         "artifact_sample_index": sample_index,
                         "visual_state": (row.get("visual_state")
                                          if category == "graphic" else None)},
            "coordinate_space": "source-normalised-tl",
            "confidence": row["confidence"],
            "evidence_ids": [observation_id for start, end, observation_id
                             in observation_scopes
                             if start <= row["tick"] < end],
        } for sample_index, row in enumerate(normalized)],
    })
    start, end = intervals[0][0], intervals[-1][1]
    ticks = (math.floor(start * TICKS_PER_SECOND), math.ceil(end * TICKS_PER_SECOND))
    builder.model["masks"].append({
        "id": f"mask-{prefix}-{digest}-{suffix}-{track_key}",
        "entity_id": entity_id,
        "interval": {"clock_id": clock_id, "start": ticks[0], "end": ticks[1]},
        "representation": {
            "kind": OCR_TRACK_MASK_KIND,
            "content_digest": digest,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha256,
            "track_id": track_key,
            "sample_ticks": [row["tick"] for row in normalized],
            "sample_indices": list(range(len(normalized))),
            "coordinate_space": "source-normalised-tl",
        },
        "source_model": PRODUCER,
        "provenance_id": provenance_id,
    })
    return True


def migrate_ocr_artifact(builder, project_folder: str | Path, clip_id: str,
                         digest: str) -> int:
    """Add one current 1.07 artifact to an in-memory Project Model.

    Legacy artifacts are not reinterpreted as tracks: step 1.07 must be
    rerun to produce the geometry, alternatives and UI-candidate schema.
    """
    layout = ProjectLayout(str(project_folder))
    artifact = layout.read_dir(Area.OCR) / clip_id / "ocr_result.json"
    try:
        raw = artifact.read_bytes()
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(doc, dict):
        builder.notes.append(f"{clip_id}: OCR artifact is not an object; skipped")
        return 0
    if doc.get("schema_version") != 2:
        builder.notes.append(f"{clip_id}: OCR artifact predates temporal-track schema; rerun optional step 1.07")
        return 0
    try:
        resolution = [int(v) for v in doc["resolution"]]
        sample_fps = float(doc["sample_fps"])
    except (KeyError, TypeError, ValueError):
        builder.notes.append(f"{clip_id}: OCR artifact lacks usable sampling metadata; skipped")
        return 0
    if (len(resolution) != 2 or min(resolution) <= 0
            or not math.isfinite(sample_fps) or sample_fps <= 0):
        builder.notes.append(f"{clip_id}: OCR artifact has invalid sampling metadata; skipped")
        return 0
    assets = [row for row in builder.model["assets"] if row["id"] == f"asset-{digest}"]
    clocks = [row for row in builder.model["clocks"] if row["id"] == f"clock-{digest}"]
    if not assets or not clocks:
        builder.notes.append(f"{clip_id}: OCR has no migrated source asset clock; model tracks refused")
        return 0
    artifact_path = artifact.relative_to(Path(project_folder)).as_posix()
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    added = 0
    for index, row in enumerate(doc.get("detections") or [], 1):
        if isinstance(row, dict):
            added += _add_track(
                builder, digest=digest, asset_id=assets[0]["id"],
                clock_id=clocks[0]["id"], clip_id=clip_id,
                artifact_path=artifact_path, artifact_sha256=artifact_sha256,
                track=row, category="text", index=index,
                sample_fps=sample_fps,
                sample_resolution=resolution,
            )
    for index, row in enumerate(doc.get("graphics") or [], 1):
        if isinstance(row, dict):
            added += _add_track(
                builder, digest=digest, asset_id=assets[0]["id"],
                clock_id=clocks[0]["id"], clip_id=clip_id,
                artifact_path=artifact_path, artifact_sha256=artifact_sha256,
                track=row, category="graphic", index=index,
                sample_fps=sample_fps,
                sample_resolution=resolution,
            )
    return added
