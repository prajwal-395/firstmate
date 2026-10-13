"""Camera, subject, and visual-change evidence over cached M2 frames.

This reader never decodes source footage. It uses the shared M2 thumbnails
and, when present, M3d person masks. A camera/subject split is reported as
unsupported when there is no measured background region to anchor it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

METHOD = "m2-farneback-background-v1"
CHANGE_METHOD = "m2-camera-compensated-luma-difference-v1"
MIN_BACKGROUND_FRACTION = 0.1
BOUNDARY_MIN_GAP_S = 0.5
M3D_CODEC = "runs-rowmajor-v1"
M3F_METHOD = "region-stats-v2-m2"


def _read_m3d(digest: str, root: Path | None) -> tuple[dict | None, list | None]:
    """Read and verify the small M3d record and its content-addressed masks."""
    from library.tools import source_memory

    source_dir = source_memory.source_dir(digest, root)
    record_path = source_dir / source_memory.SLOT_REGIONS
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        ref = record["sidecar"]
        sidecar = (source_dir / ref["file"]).resolve()
        if not sidecar.is_relative_to(source_dir.resolve()):
            return None, None
        raw = gzip.decompress(sidecar.read_bytes())
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            return None, None
        payload = json.loads(raw)
        if (
            record.get("content_digest") != digest
            or payload.get("codec") != M3D_CODEC
            or payload.get("grid") != record["grid"]
        ):
            return None, None
        return record, payload["masks"]
    except (OSError, EOFError, ValueError, KeyError, TypeError):
        return None, None


def _decode_runs(runs: list[int], grid: list[int]) -> np.ndarray:
    """Rasterize the M3d alternating row-major runs onto their declared grid."""
    width, height = (int(value) for value in grid)
    total = width * height
    lengths = np.asarray(runs, dtype=np.int64)
    if width <= 0 or height <= 0 or np.any(lengths < 0) or int(lengths.sum()) != total:
        raise ValueError("M3d mask runs do not cover the declared grid")
    flat = np.zeros(total, dtype=bool)
    offset = 0
    for i, length in enumerate(lengths):
        end = offset + int(length)
        if i % 2:
            flat[offset:end] = True
        offset = end
    return flat.reshape(height, width)


def _read_m3f(
    digest: str, sidecar_sha: str | None, root: Path | None
) -> dict[float, dict]:
    """Read FI-11's M3f output only when it names the active M3d masks."""
    from library.tools import source_memory

    try:
        path = source_memory.source_dir(digest, root) / source_memory.SLOT_REGION_STATS
        record = json.loads(path.read_text(encoding="utf-8"))
        instrument = record["instrument"]
        if (
            record.get("content_digest") != digest
            or instrument.get("method") != M3F_METHOD
            or instrument.get("m3d_sidecar_sha256") != sidecar_sha
        ):
            return {}
        return {
            round(float(sample["t"]), 3): sample
            for sample in record.get("samples") or []
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _gray(path: str) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L"), dtype=np.uint8)


def _boundary_intervals(changes: list[dict[str, Any]]) -> list[dict[str, float]]:
    measured = [
        float(row["novelty"]) for row in changes if row.get("novelty") is not None
    ]
    if len(measured) < 3:
        return []
    median = float(np.median(measured))
    mad = float(np.median(np.abs(np.asarray(measured) - median)))
    threshold = median + max(0.08, 3.0 * mad)
    hits = [
        row
        for row in changes
        if row.get("novelty") is not None and row["novelty"] > threshold
    ]
    boundaries: list[dict[str, float]] = []
    for row in hits:
        t = float(row["end"])
        if boundaries and t - boundaries[-1]["time"] < BOUNDARY_MIN_GAP_S:
            if row["novelty"] > boundaries[-1]["score"]:
                boundaries[-1] = {
                    "time": round(t, 3),
                    "score": round(row["novelty"], 5),
                }
            continue
        boundaries.append({"time": round(t, 3), "score": round(row["novelty"], 5)})
    return boundaries


def analyze_pairs(samples: list[dict[str, Any]], *, cv2_module=None) -> dict:
    """Measure adjacent M2 samples; missing frames break interval coverage.

    Each sample contains `t`, `path`, and optional `instances` plus `masks`
    (track id to boolean raster). Arrays are already on M2's own grid.
    """
    if cv2_module is None:
        import cv2 as cv2_module
    ordered = sorted(samples, key=lambda row: float(row["t"]))
    pairs: list[dict[str, Any]] = []
    attempted = max(0, len(ordered) - 1)
    for prev, cur in pairwise(ordered):
        start, end = float(prev["t"]), float(cur["t"])
        row: dict[str, Any] = {"start": round(start, 3), "end": round(end, 3)}
        if end <= start:
            row.update(
                {
                    "status": "unmeasured",
                    "reason": "M2 pair has a non-positive source interval",
                    "camera": None,
                    "subject_motion": None,
                    "change": None,
                    "novelty": None,
                    "decomposition": {
                        "status": "unsupported",
                        "reason": "pair interval is invalid",
                    },
                }
            )
            pairs.append(row)
            continue
        try:
            a, b = _gray(prev["path"]), _gray(cur["path"])
            if a.shape != b.shape:
                raise ValueError("M2 thumbnail dimensions changed")
            flow = cv2_module.calcOpticalFlowFarneback(
                a, b, None, 0.5, 2, 9, 2, 5, 1.1, 0
            )
            h, w = a.shape
            previous_masks = prev.get("masks")
            current_masks = cur.get("masks")
            decomposition = isinstance(previous_masks, dict) and isinstance(
                current_masks, dict
            )
            bg = np.ones((h, w), dtype=bool)
            tracks = set()
            if decomposition:
                tracks = set(previous_masks) & set(current_masks)
                for mask in list(previous_masks.values()) + list(
                    current_masks.values()
                ):
                    if np.shape(mask) != (h, w):
                        raise ValueError("M3d mask grid differs from M2")
                    bg &= ~np.asarray(mask, dtype=bool)
            bg_fraction = float(bg.mean()) if decomposition else 0.0
            if decomposition and bg_fraction >= MIN_BACKGROUND_FRACTION:
                vectors = flow[bg]
                camera = np.median(vectors, axis=0)
                residual = np.linalg.norm(vectors - camera, axis=1)
                row["camera"] = {
                    "translation_px": [
                        round(float(camera[0]), 3),
                        round(float(camera[1]), 3),
                    ],
                    "residual_p50_px": round(float(np.median(residual)), 3),
                    "background_fraction": round(bg_fraction, 5),
                    "method": METHOD,
                }
                row["subject_motion"] = []
                for track in sorted(tracks):
                    region = np.asarray(previous_masks[track], dtype=bool) & np.asarray(
                        current_masks[track], dtype=bool
                    )
                    if not region.any():
                        continue
                    vectors = flow[region]
                    subject_vector = np.median(vectors, axis=0)
                    row["subject_motion"].append(
                        {
                            "track": str(track),
                            "image_flow_px": [
                                round(float(v), 3) for v in subject_vector
                            ],
                            "relative_to_camera_px": [
                                round(float(v), 3) for v in subject_vector - camera
                            ],
                            "samples": int(region.sum()),
                        }
                    )
                warped = cv2_module.warpAffine(
                    b,
                    np.float32([[1, 0, -camera[0]], [0, 1, -camera[1]]]),
                    (w, h),
                    flags=cv2_module.INTER_LINEAR,
                    borderMode=cv2_module.BORDER_REFLECT,
                )
                delta = np.abs(a.astype(np.float32) - warped.astype(np.float32)) / 255.0
                row["novelty"] = round(float(np.mean(delta)), 5)
                row["decomposition"] = {
                    "status": "measured",
                    "model": "global-translation",
                    "residual_samples": len(residual),
                }
            else:
                row["camera"] = None
                row["subject_motion"] = None
                reason = (
                    "no M3d masks"
                    if not decomposition
                    else "insufficient unmasked background"
                )
                row["decomposition"] = {"status": "unsupported", "reason": reason}
                # Change remains a separate measurement even when motion
                # cannot be attributed to camera or subject.
                row["novelty"] = round(
                    float(
                        np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32)))
                        / 255.0
                    ),
                    5,
                )
            row["change"] = round(float(np.mean(np.linalg.norm(flow, axis=2))), 3)
            region_changes = _region_luma_change(
                prev.get("region_measurements"), cur.get("region_measurements")
            )
            if region_changes is not None:
                row["region_luma_change"] = region_changes
            row["status"] = "measured"
        except (
            OSError,
            ValueError,
            TypeError,
            IndexError,
            AttributeError,
            cv2_module.error,
        ) as exc:  # failed pair is a coverage gap
            row.update(
                {
                    "status": "unmeasured",
                    "reason": str(exc),
                    "camera": None,
                    "subject_motion": None,
                    "change": None,
                    "novelty": None,
                    "decomposition": {
                        "status": "unsupported",
                        "reason": "pair unavailable",
                    },
                }
            )
        pairs.append(row)

    measured = sum(row["status"] == "measured" for row in pairs)
    measured_decomposition = sum(
        row["decomposition"]["status"] == "measured" for row in pairs
    )
    intervals = [
        float(cur["t"]) - float(prev["t"])
        for prev, cur in pairwise(ordered)
        if float(cur["t"]) > float(prev["t"])
    ]
    sample_interval = round(float(np.median(intervals)), 3) if intervals else None
    novelty_rows = [
        {"start": row["start"], "end": row["end"], "novelty": row["novelty"]}
        for row in pairs
    ]
    return {
        "method": METHOD,
        "change_method": CHANGE_METHOD,
        "sample_interval_seconds_p50": sample_interval,
        "pairs": pairs,
        "boundaries": _boundary_intervals(novelty_rows),
        "coverage": {
            "sample_pairs_expected": attempted,
            "sample_pairs_measured": measured,
            "decomposed_pairs": measured_decomposition,
            "basis": "adjacent available M2 thumbnail pairs",
            "temporal_sampling": "sampled; not frame-complete",
            "sample_interval_seconds_p50": sample_interval,
            "kind": (
                "unavailable"
                if attempted == 0
                else "complete"
                if measured == attempted
                else "sampled"
                if measured
                else "unavailable"
            ),
        },
    }


def _region_luma_change(previous: dict | None, current: dict | None) -> dict | None:
    """Compare FI-11's cached luma readings when both M2 times have them."""
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return None
    changes: dict[str, Any] = {}
    for key in ("whole_frame", "outside"):
        before, after = previous.get(key), current.get(key)
        if (
            isinstance(before, dict)
            and isinstance(after, dict)
            and before.get("luma_p50") is not None
            and after.get("luma_p50") is not None
        ):
            changes[key] = round(
                float(after["luma_p50"]) - float(before["luma_p50"]), 2
            )
    before_regions = {
        str(row["track"]): row["inside"]
        for row in previous.get("regions") or []
        if isinstance(row, dict)
        and row.get("track")
        and isinstance(row.get("inside"), dict)
    }
    after_regions = {
        str(row["track"]): row["inside"]
        for row in current.get("regions") or []
        if isinstance(row, dict)
        and row.get("track")
        and isinstance(row.get("inside"), dict)
    }
    for track in sorted(set(before_regions) & set(after_regions)):
        before = before_regions[track].get("luma_p50")
        after = after_regions[track].get("luma_p50")
        if before is not None and after is not None:
            changes[track] = round(float(after) - float(before), 2)
    return (
        {"method": "region-stats-v2-m2", "luma_p50_delta": changes} if changes else None
    )


def _input_identity(
    project_folder: str, clip: dict, recorded: dict | None, root: Path | None
) -> tuple[str | None, dict]:
    """Cheap cache identity; it reads records, never thumbnail pixels."""
    from library.tools import source_memory

    digest, _basis = source_memory.digest_for_clip(project_folder, clip, recorded)
    if digest is None:
        return None, {"source_digest": None}
    m2 = source_memory.read_m2(digest, root)
    source = clip.get("source_file") or clip.get("path") or ""
    if (
        m2 is None
        or not source_memory.m2_frames_present(digest, m2, root)
        or (source and not source_memory.is_fresh(m2, source))
    ):
        return digest, {"source_digest": digest, "m2": None}
    m3d, _masks = _read_m3d(digest, root)
    sidecar_sha = (m3d.get("sidecar") or {}).get("sha256") if m3d else None
    m3f = _read_m3f(digest, sidecar_sha, root) if m3d else {}
    m3f_method = M3F_METHOD if m3f else None
    frames = [
        {"t": round(float(frame["t"]), 3), "file": frame["file"]}
        for frame in m2["frames"]
    ]
    return digest, {
        "source_digest": digest,
        "m2_frames": frames,
        "m3d_sidecar_sha256": sidecar_sha,
        "m3f_method": m3f_method,
    }


def cached_inputs_match(
    existing: dict,
    project_folder: str,
    clip: dict,
    recorded: dict | None = None,
    root: Path | None = None,
) -> bool:
    """Whether the temporal record names the currently available M2/M3 slots."""
    _digest, identity = _input_identity(project_folder, clip, recorded, root)
    return (
        existing.get("method") == METHOD and existing.get("input_identity") == identity
    )


def read_cached_motion(
    project_folder: str,
    clip: dict,
    recorded: dict | None = None,
    root: Path | None = None,
) -> dict:
    """Analyze fresh M2/M3d records, or return explicit unavailable coverage."""
    from library.tools import source_memory

    digest, identity = _input_identity(project_folder, clip, recorded, root)
    unavailable = {
        "method": METHOD,
        "change_method": CHANGE_METHOD,
        "pairs": [],
        "boundaries": [],
        "coverage": {
            "sample_pairs_expected": 0,
            "sample_pairs_measured": 0,
            "decomposed_pairs": 0,
            "kind": "unavailable",
        },
    }
    if digest is None:
        unavailable["unavailable_because"] = "source digest unavailable"
        unavailable["input_identity"] = identity
        return unavailable
    m2 = source_memory.read_m2(digest, root)
    source = clip.get("source_file") or clip.get("path") or ""
    if (
        m2 is None
        or not source_memory.m2_frames_present(digest, m2, root)
        or (source and not source_memory.is_fresh(m2, source))
    ):
        unavailable["unavailable_because"] = "fresh M2 thumbnails unavailable"
        unavailable["input_identity"] = identity
        return unavailable
    m3d, encoded_masks = _read_m3d(digest, root)
    masks_by_time: dict[float, dict[str, np.ndarray]] = {}
    if m3d is not None and encoded_masks is not None:
        try:
            for sample in m3d["samples"]:
                instances = sample["instances"]
                if instances is None:
                    continue
                masks_by_time[round(float(sample["t"]), 3)] = {
                    str(inst["track"]): _decode_runs(
                        encoded_masks[inst["mask"]], m3d["grid"]
                    )
                    for inst in instances
                }
        except (KeyError, ValueError, OSError):
            masks_by_time = {}
    sidecar_sha = (m3d.get("sidecar") or {}).get("sha256") if m3d else None
    region_by_time = _read_m3f(digest, sidecar_sha, root) if sidecar_sha else {}
    frames = []
    for frame in m2["frames"]:
        t = round(float(frame["t"]), 3)
        frames.append(
            {
                "t": t,
                "path": source_memory.frame_abspath(digest, frame, root),
                "masks": masks_by_time.get(t),
                "region_measurements": region_by_time.get(t),
            }
        )
    try:
        result = analyze_pairs(frames)
    except ImportError as exc:
        expected = max(0, len(frames) - 1)
        result = {
            "method": METHOD,
            "change_method": CHANGE_METHOD,
            "sample_interval_seconds_p50": None,
            "pairs": [],
            "boundaries": [],
            "unavailable_because": f"OpenCV unavailable: {exc}",
            "coverage": {
                "sample_pairs_expected": expected,
                "sample_pairs_measured": 0,
                "decomposed_pairs": 0,
                "basis": "adjacent available M2 thumbnail pairs",
                "temporal_sampling": "sampled; not frame-complete",
                "sample_interval_seconds_p50": None,
                "kind": "unavailable",
            },
        }
    result["input_identity"] = identity
    return result
