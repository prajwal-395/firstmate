"""Measured shot hierarchy and evidence-linked cinematographic profiles.

This module deliberately uses only measurements already available in the
temporal index. It does not treat VLM prose as a boundary measurement.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

METHOD = "scene-cinematography-measurements-v1"


def segment_and_profile(
    duration: float,
    scene_boundaries: list[dict[str, Any]],
    motion: dict[str, Any],
    camera_motion: dict[str, Any],
    face_presence: dict[str, Any],
    color_curves: dict[str, Any],
    scene_intervals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create half-open shot/sub-shot/sequence spans and measured profiles.

    Boundaries start with ffmpeg's deterministic scene-change measurements.
    A sub-shot is introduced only when an existing measured camera-motion
    class changes inside a shot. Each attribute states the measurement IDs
    that support it; unavailable evidence stays ``unknown``.
    """
    if duration <= 0:
        raise ValueError("duration must be positive")
    cuts = _valid_times(
        [item["time"] for item in scene_boundaries if item.get("type") != "start"],
        duration,
    )
    shot_edges = [0.0, *cuts, duration]
    shots = []
    subshots = []
    for index, (start, end) in enumerate(zip(shot_edges, shot_edges[1:])):
        shot_id = f"shot-{index + 1:04d}"
        profile = _profile(start, end, motion, camera_motion,
                           face_presence, color_curves)
        shots.append({"id": shot_id, "start": start, "end": end,
                      "certainty": "measured",
                      "boundary_basis_ids": _cut_basis(start, scene_boundaries),
                      "attributes": profile})
        # Camera mode shifts define sub-shot intervals, but only when both
        # sides have actual sampled measurements. Empty/unknown runs do not
        # become invented cuts.
        internal = _camera_mode_changes(start, end, camera_motion)
        sub_edges = [start, *internal, end]
        for sub_index, (sub_start, sub_end) in enumerate(zip(sub_edges, sub_edges[1:])):
            subshots.append({
                "id": f"{shot_id}-sub-{sub_index + 1:02d}",
                "parent_shot_id": shot_id,
                "start": sub_start,
                "end": sub_end,
                "certainty": "inferred",
                "boundary_basis_ids": (
                    _camera_boundary_basis(sub_start, camera_motion)
                    if sub_start > start else []
                ),
                "attributes": _profile(sub_start, sub_end, motion, camera_motion,
                                        face_presence, color_curves),
            })
    measured_scenes = []
    for index, scene in enumerate(scene_intervals or []):
        start, end = float(scene["start"]), float(scene["end"])
        if start < 0 or end > duration or start >= end:
            continue
        measured_scenes.append({
            "id": f"scene-{index + 1:04d}", "start": start, "end": end,
            "certainty": "interpreted",
            "description": scene.get("description"),
            "location": scene.get("location"), "type": scene.get("type"),
            "boundary_basis_ids": scene.get("boundary_basis_ids")
                or [f"m4_scene:semantic:{index}"],
            "attributes": _profile(start, end, motion, camera_motion,
                                   face_presence, color_curves),
        })
    sequences = _sequences_from_scenes(measured_scenes)
    return {
        "method": METHOD,
        "status": "measured",
        "duration": duration,
        "shots": shots,
        "scenes": measured_scenes,
        "subshots": subshots,
        "sequences": sequences,
        "sequence_analysis": {
            "status": "interpreted" if sequences else "not_analyzed",
            "reason": None if sequences else
                "M4 scenes lack matching location/type evidence for grouping",
        },
        "measurement_catalog": _measurement_catalog(
            scene_boundaries, motion, camera_motion, face_presence, color_curves,
            scene_intervals or []),
    }


def _valid_times(values: list[float], duration: float) -> list[float]:
    return sorted({round(float(value), 3) for value in values
                   if 0 < float(value) < duration})


def _samples(data: dict[str, Any], value_key: str) -> list[tuple[float, Any]]:
    values = data.get(value_key) or data.get("values") or []
    times = data.get("times") or data.get("sample_times")
    rate = data.get("sample_rate_hz") or data.get("sample_rate")
    if times is None and rate:
        times = [i / float(rate) for i in range(len(values))]
    if times is None:
        return []
    return [(float(t), value) for t, value in zip(times, values)]


def _measurement_id(lane: str, data: dict[str, Any], index: int) -> str:
    return f"{lane}:{data.get('method', 'legacy')}:{index}"


def _profile(start: float, end: float, motion: dict[str, Any],
             camera: dict[str, Any], face: dict[str, Any],
             color: dict[str, Any]) -> dict[str, Any]:
    sampled = {
        "motion": (motion, "values"),
        "camera_motion": (camera, "dominant_motion"),
        "face_width": (face, "face_width"),
        "brightness": (color, "brightness_values"),
        "hue": (color, "hue_values"),
    }
    selected: dict[str, list[tuple[int, Any]]] = {}
    for lane, (data, key) in sampled.items():
        selected[lane] = [(i, value) for i, (t, value) in enumerate(_samples(data, key))
                          if start <= t < end and value is not None]
    motion_values = [(i, float(v)) for i, v in selected["motion"] if _number(v)]
    face_values = [(i, float(v)) for i, v in selected["face_width"] if _number(v)]
    brightness = [(i, float(v)) for i, v in selected["brightness"] if _number(v)]
    hue = [(i, float(v)) for i, v in selected["hue"] if _number(v)]
    modes = [(i, _movement(v)) for i, v in selected["camera_motion"]]
    modes = [(i, mode) for i, mode in modes if mode != "unknown"]

    def attr(name: str, value: Any, lane: str, indices: list[int],
             certainty: str) -> dict[str, Any]:
        data = sampled[lane][0]
        return {"value": value,
                "certainty": certainty if indices else None,
                "status": "analyzed" if indices else "not_analyzed",
                "evidence_ids": [_measurement_id(lane, data, i) for i in indices]}

    framing = "unknown"
    if face_values:
        mean_face = sum(value for _, value in face_values) / len(face_values)
        framing = "close" if mean_face >= .45 else "medium" if mean_face >= .20 else "wide"
    movement = Counter(mode for _, mode in modes).most_common(1)[0][0] if modes else "unknown"
    lighting = "unknown"
    if brightness:
        mean_brightness = sum(value for _, value in brightness) / len(brightness)
        lighting = "low" if mean_brightness < .30 else "high" if mean_brightness > .70 else "balanced"
    color_character = "unknown"
    if hue:
        mean_hue = sum(value for _, value in hue) / len(hue) % 360
        color_character = "warm" if mean_hue < 70 or mean_hue >= 300 else "cool" if 170 <= mean_hue < 270 else "green"
    return {
        "framing": attr("framing", framing, "face_width",
                        [i for i, _ in face_values], "interpreted"),
        "camera_movement": attr("camera_movement", movement, "camera_motion",
                                [i for i, _ in modes], "inferred"),
        "motion_magnitude": attr("motion_magnitude",
                                 round(sum(v for _, v in motion_values) / len(motion_values), 4)
                                 if motion_values else None,
                                 "motion", [i for i, _ in motion_values], "measured"),
        "lighting_level": attr("lighting_level", lighting, "brightness",
                               [i for i, _ in brightness], "interpreted"),
        "color_character": attr("color_character", color_character, "hue",
                                [i for i, _ in hue], "interpreted"),
    }


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _movement(value: Any) -> str:
    if isinstance(value, str):
        return value if value in {"static", "pan_left", "pan_right", "tilt_up",
                                 "tilt_down", "handheld", "mixed"} else "unknown"
    if not isinstance(value, dict):
        return "unknown"
    x = float(value.get("translation_x", 0.0))
    y = float(value.get("translation_y", 0.0))
    residual = float(value.get("residual", 0.0))
    if max(abs(x), abs(y), residual) < 0.01:
        return "static"
    if residual > max(abs(x), abs(y)):
        return "handheld"
    if abs(x) >= abs(y):
        return "pan_right" if x > 0 else "pan_left"
    return "tilt_down" if y > 0 else "tilt_up"


def _cut_basis(time: float, boundaries: list[dict[str, Any]]) -> list[str]:
    found = [i for i, item in enumerate(boundaries)
             if item.get("type") != "start"
             and abs(float(item.get("time", -1)) - time) <= .001]
    return [f"scene_boundary:ffmpeg:{i}" for i in found]


def _camera_mode_changes(start: float, end: float, camera: dict[str, Any]) -> list[float]:
    samples = _samples(camera, "values")
    changes = []
    previous = None
    for time, mode in samples:
        mode = _movement(mode)
        if start <= time < end and mode != "unknown":
            if previous and previous[1] != mode:
                if time > start:
                    changes.append(round(time, 3))
            previous = (time, mode)
    return changes


def _camera_boundary_basis(time: float, camera: dict[str, Any]) -> list[str]:
    samples = _samples(camera, "values")
    matching = [i for i, (t, _) in enumerate(samples) if abs(t - time) <= .001]
    return [_measurement_id("camera_motion", camera, i) for i in matching]


def _sequences_from_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group adjoining measured scenes only when M4 location and type agree."""
    sequences: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    for scene in scenes:
        key = (scene["location"], scene["type"])
        if not any(key):
            if group:
                sequences.append(_sequence_from_scenes(group, len(sequences) + 1))
                group = []
            continue
        prior = (group[-1]["location"], group[-1]["type"]) if group else None
        if group and (key != prior or group[-1]["end"] != scene["start"]):
            sequences.append(_sequence_from_scenes(group, len(sequences) + 1))
            group = []
        group.append(scene)
    if group:
        sequences.append(_sequence_from_scenes(group, len(sequences) + 1))
    return sequences


def _sequence_from_scenes(scenes: list[dict[str, Any]], index: int) -> dict[str, Any]:
    ids = sorted({evidence_id for scene in scenes
                  for evidence_id in scene.get("boundary_basis_ids", [])} | {
                  evidence_id for scene in scenes
                  for attribute in scene["attributes"].values()
                  for evidence_id in attribute["evidence_ids"]})
    return {"id": f"sequence-{index:04d}", "start": scenes[0]["start"],
            "end": scenes[-1]["end"],
            "certainty": "interpreted",
            "scene_ids": [scene["id"] for scene in scenes],
            "grouping_basis": "adjacent_m4_scenes_same_location_and_type",
            "basis_measurement_ids": ids}


def _measurement_catalog(boundaries: list[dict[str, Any]], motion: dict[str, Any],
                          camera: dict[str, Any], face: dict[str, Any],
                          color: dict[str, Any],
                          scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog = []
    for lane_name, data, value_key in zip(
        ("motion", "camera_motion", "face_width", "brightness", "hue"),
        (motion, camera, face, color, color),
        ("values", "values", "face_width", "brightness_values", "hue_values"),
    ):
        for index, (time, value) in enumerate(_samples(data, value_key)):
            catalog.append({"id": _measurement_id(lane_name, data, index),
                            "lane": lane_name, "time": time,
                            "value": value, "unit": {
                                "motion": "per-clip-normalized-frame-difference",
                                "camera_motion": "normalized-image-width-per-sample",
                                "face_width": "normalized-image-width",
                                "brightness": "normalized-luminance",
                                "hue": "degrees"}[lane_name],
                            "method": data.get("method", "legacy")})
    for index, boundary in enumerate(boundaries):
        catalog.append({"id": f"scene_boundary:ffmpeg:{index}", "lane": "scene_boundary",
                        "time": boundary["time"], "value": boundary.get("score"),
                        "unit": "ffmpeg-scene-score-0-to-1",
                        "method": "ffmpeg-scene-score"})
    for index, scene in enumerate(scenes):
        catalog.append({"id": f"m4_scene:semantic:{index}", "lane": "m4_scene",
                        "time": float(scene["start"]),
                        "value": {"end": float(scene["end"]),
                                  "location": scene.get("location"),
                                  "type": scene.get("type"),
                                  "description": scene.get("description")},
                        "unit": "source-interval-and-semantic-labels",
                        "method": "m4-scene-profile"})
    return sorted(catalog, key=lambda item: (item["lane"], item["time"], item["id"]))
