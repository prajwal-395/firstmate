"""Versioned JSON serialization for the graphics IR.

The scene.json format is the contract between the IR and every rendering
engine. A non-Python renderer (the Rust ren-graphics-fframes runtime)
consumes the same JSON Schema file this module generates, so the two can
never drift apart.

The schema version is the ``version`` field of the Scene model. A scene
with an unknown version REFUSES to deserialize rather than guessing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Scene

SCHEMA_VERSION = 1
SUPPORTED_VERSIONS = (1,)

SCHEMA_FILENAME = "scene.schema.json"


class UnsupportedSceneVersion(ValueError):
    """A scene.json with a version this module does not support."""


def serialize(scene: Scene) -> str:
    """Serialize a scene to a JSON string."""
    return scene.model_dump_json(indent=2)


def deserialize(text: str) -> Scene:
    """Deserialize a JSON string to a scene, refusing unknown versions."""
    data = json.loads(text)
    version = data.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise UnsupportedSceneVersion(
            f"scene.json has version {version!r}, which this module does not "
            f"support. Supported versions: {list(SUPPORTED_VERSIONS)}."
        )
    return Scene.model_validate(data)


def write_scene(scene: Scene, path: str | Path) -> None:
    """Write a scene to a JSON file."""
    Path(path).write_text(serialize(scene), encoding="utf-8")


def read_scene(path: str | Path) -> Scene:
    """Read a scene from a JSON file."""
    return deserialize(Path(path).read_text(encoding="utf-8"))


def generate_schema() -> dict[str, Any]:
    """The JSON Schema for scene.json, generated from the Pydantic model."""
    return Scene.model_json_schema()


def write_schema(path: str | Path) -> None:
    """Write the JSON Schema file for scene.json."""
    schema = generate_schema()
    Path(path).write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8"
    )


def schema_path() -> Path:
    """The path to the schema file shipped beside this module."""
    return Path(__file__).parent / SCHEMA_FILENAME
