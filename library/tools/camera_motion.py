"""Interval-level camera motion measurements for retrieval and editing.

The temporal index stores measured flow at 5 Hz. This module provides a
single numeric view over any requested source interval and coarser windows,
without turning absent measurements into zeros or relying on VLM labels.
"""

from __future__ import annotations

from statistics import mean, pstdev


DEFAULT_RESOLUTIONS_SECONDS = (1.0, 5.0, 15.0)


def summarize_camera_motion(temporal_index: dict, start: float, end: float,
                            resolutions=DEFAULT_RESOLUTIONS_SECONDS) -> dict:
    """Summarize measured motion in ``[start, end)`` and time windows.

    Translation and subject energy are normalized to the temporal index's
    proxy-frame scale per sample. Rotation is radians/sample and zoom is
    fractional scale/sample. ``static_score`` is a transparent normalized
    query aid, not a perceptual judgement; missing signals remain ``None``.
    """
    if start < 0 or end <= start:
        raise ValueError("motion interval must satisfy 0 <= start < end")
    decomposition = temporal_index.get("camera_motion_decomposition") or {}
    hz = float(decomposition.get("sample_rate_hz") or 0)
    values = decomposition.get("values") or []
    has_times = any(isinstance(v.get("time"), (int, float)) for v in values)
    selected = [v for v in values
                if isinstance(v.get("time"), (int, float))
                and start <= float(v["time"]) < end]
    if not has_times:
        # Older decompositions omitted per-sample time; flow samples are
        # evenly spaced and begin one sample after the clip start.
        hz = float((temporal_index.get("optical_flow_direction") or {})
                   .get("sample_rate_hz") or 0)
        if hz > 0:
            selected = [{**v, "time": i / hz}
                        for i, v in enumerate(values, start=1)
                        if start <= i / hz < end]

    def avg(key, *, absolute=False):
        measured = [abs(float(v[key])) if absolute else float(v[key])
                    for v in selected
                    if isinstance(v.get(key), (int, float))]
        return round(mean(measured), 6) if measured else None

    tx, ty = avg("translation_x"), avg("translation_y")
    rotation, zoom, subject = (avg("rotation_rad"), avg("zoom_rate"),
                              avg("subject_energy", absolute=True))
    magnitudes = [((float(v["translation_x"]) ** 2
                    + float(v["translation_y"]) ** 2) ** 0.5)
                  for v in selected
                  if isinstance(v.get("translation_x"), (int, float))
                  and isinstance(v.get("translation_y"), (int, float))]
    translation = round(mean(magnitudes), 6) if magnitudes else None
    components = [x for x in (translation,
                              abs(rotation) if rotation is not None else None,
                              abs(zoom) if zoom is not None else None)
                  if x is not None]
    score = round(sum(components), 6) if components else None
    shake = round(pstdev(magnitudes), 6) if len(magnitudes) > 1 else None
    # One search-grid step in the 160-pixel proxy, normalized by the
    # existing 8-pixel flow unit; this is the camera-motion change floor.
    discontinuities = [round(float(selected[i]["time"]), 3)
                       for i in range(1, len(selected))
                       if all(isinstance(selected[j].get(k), (int, float))
                              for j in (i - 1, i)
                              for k in ("translation_x", "translation_y"))
                       and ((float(selected[i]["translation_x"])
                             - float(selected[i - 1]["translation_x"])) ** 2
                            + (float(selected[i]["translation_y"])
                               - float(selected[i - 1]["translation_y"])) ** 2)
                       ** 0.5 >= 0.25]
    windows = []
    for width in resolutions:
        if width <= 0:
            raise ValueError("resolutions must be positive seconds")
        cursor = start
        while cursor < end:
            limit = min(end, cursor + width)
            part = summarize_camera_motion(temporal_index, cursor, limit, ())
            windows.append({"start": round(cursor, 3), "end": round(limit, 3),
                            "resolution_seconds": width,
                            "camera_motion": part["camera_motion"],
                            "static_score": part["static_score"],
                            "discontinuities": part["discontinuities"]})
            cursor = limit
    return {
        "interval": {"start": start, "end": end},
        "sample_count": len(selected),
        "camera_motion": {"translation": translation,
                          "pan": tx, "tilt": ty,
                          "rotation_rad": rotation, "zoom_rate": zoom,
                          "subject_energy": subject,
                          "shake_std_translation_magnitude": shake},
        "discontinuities": discontinuities,
        "static_score": score,
        "multi_resolution": windows,
    }


def matches_motion_constraints(summary: dict, *, max_static_score=None,
                                max_shake=None, max_subject_energy=None) -> bool:
    """Apply explicit numeric movement limits; unknown measurements fail closed."""
    motion = summary["camera_motion"]
    checks = (("static_score", max_static_score),
              ("shake_std_translation_magnitude", max_shake),
              ("subject_energy", max_subject_energy))
    for key, limit in checks:
        if limit is not None and (motion.get(key) if key != "static_score"
                                  else summary.get(key)) is None:
            return False
        value = (motion.get(key) if key != "static_score"
                 else summary.get(key))
        if limit is not None and value > limit:
            return False
    return True
