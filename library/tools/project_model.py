"""Python boundary for Ren's versioned canonical project model contract."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from library.tools import protocol_conformance

SCHEMA_VERSION = 1
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_MODEL_SCHEMA = REPOSITORY_ROOT / "native/ren-protocol/schemas/project-model.schema.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/project_model_e2e"


def validate_project_model(model: dict[str, Any]) -> None:
    """Validate the Rust-generated schema and Python semantic invariants."""
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


def load_project_model(path: str | Path) -> dict[str, Any]:
    """Load JSON and check it directly against the generated JSON Schema."""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
        schema = json.loads(PROJECT_MODEL_SCHEMA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read project model {source}: {exc}") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(document),
                    key=lambda error: list(map(str, error.absolute_path)))
    if errors:
        rendered = "; ".join(
            f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors)
        raise ValueError(rendered)
    return document


def validate_fixture(root: str | Path = FIXTURE_ROOT) -> dict[str, Any]:
    """Validate schema, referenced media, and fixture-specific assertions."""
    fixture = Path(root)
    model = load_project_model(fixture / "expected-project-model.json")
    for asset in model["assets"]:
        if asset["kind"] == "render":
            continue
        media = fixture / asset["uri"]
        if not media.is_file() or media.stat().st_size == 0:
            raise ValueError(f"fixture media is missing or empty: {media}")
        digest = "sha256:" + hashlib.sha256(media.read_bytes()).hexdigest()
        if asset["content_digest"] != digest:
            raise ValueError(f"fixture media digest mismatch: {media}")
    validate_project_model(model)
    people = {entity["id"] for entity in model["entities"]
              if entity["kind"] == "person"}
    for person_id in people:
        linked_observations = {link["observation_id"]
                               for link in model["identity_links"]
                               if link["entity_id"] == person_id}
        modalities = {observation["modality"] for observation in model["observations"]
                      if observation["id"] in linked_observations}
        if not {"face", "speech"} <= modalities:
            raise ValueError(f"{person_id} must link to face and speech observations")
        if not any(mask["entity_id"] == person_id for mask in model["masks"]):
            raise ValueError(f"{person_id} must have an associated mask")
    roles = {use["role"] for use in model["clip_uses"]}
    if not {"a_roll", "b_roll"} <= roles:
        raise ValueError("fixture needs A-roll and B-roll uses")
    trim = next((edit for edit in model["edits"]
                 if edit["operation"] == "clip.trim"), None)
    if not trim or trim["before"]["duration"] <= trim["after"]["duration"]:
        raise ValueError("fixture needs an edit that changes clip length")
    render = model["renders"][0]
    frame_map = next(item for item in model["frame_maps"]
                     if item["id"] == render["frame_map_id"])
    if frame_map["segments"][-1]["to"]["end"] != render["settings"]["expected_frames"]:
        raise ValueError("render frame map must end at the expected frame count")
    return model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "validate-fixture"))
    parser.add_argument("path", nargs="?", help="Project Model JSON for validate")
    args = parser.parse_args()
    try:
        if args.command == "validate-fixture":
            model = validate_fixture()
            print(f"valid Project Model fixture: {model['id']}")
        else:
            if not args.path:
                parser.error("validate requires a JSON path")
            model = load_project_model(args.path)
            print(f"valid Project Model: {model['id']}")
    except ValueError as exc:
        parser.exit(1, f"invalid Project Model: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
