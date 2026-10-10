"""Python boundary for Ren's versioned canonical project model contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from library.tools import protocol_conformance

SCHEMA_VERSION = 1


def read_project_model(path: Path) -> dict[str, Any]:
    """Read a model document, refusing unknown versions and schema drift."""
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read project model {path}: {exc}") from exc
    validate_project_model(model)
    return model


def write_project_model(path: Path, model: dict[str, Any]) -> None:
    """Validate then write the shared Rust/Python JSON representation."""
    validate_project_model(model)
    path.write_text(json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_project_model(model: dict[str, Any]) -> None:
    version = model.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported project model schema_version {version!r}; expected {SCHEMA_VERSION}")
    protocol_conformance.validate(model, "project-model")
    for clock in model["clocks"]:
        if clock["ticks_per_second"] <= 0 or (clock["frame_rate"] and clock["frame_rate"]["denominator"] <= 0):
            raise ValueError("clock rates must have positive denominators and ticks_per_second")
    confidence_values = [item["confidence"] for key in ("observations", "identity_links", "relations") for item in model[key]]
    confidence_values.extend(sample["confidence"] for track in model["tracks"] for sample in track["samples"])
    if any(value is not None and not 0 <= value <= 1 for value in confidence_values):
        raise ValueError("confidence must be between 0 and 1")
    for collection in ("observations", "masks", "relations", "effects"):
        for item in model[collection]:
            interval = item["interval"]
            if interval["start"] >= interval["end"]:
                raise ValueError(f"{collection} intervals must be non-empty and half-open (start < end)")
    for use in model["clip_uses"]:
        for interval in (use["source_interval"], use["timeline_interval"]):
            if interval["start"] >= interval["end"]:
                raise ValueError("clip use intervals must be non-empty and half-open (start < end)")
    for frame_map in model["frame_maps"]:
        for segment in frame_map["segments"]:
            if segment["from"]["start"] >= segment["from"]["end"] or segment["to"]["start"] >= segment["to"]["end"]:
                raise ValueError("frame map intervals must be non-empty and half-open (start < end)")
