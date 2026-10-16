"""Model registry policy and edition manifest invariants."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from library.tools import model_registry


def test_registry_matches_json_schema_and_projected_manifests():
    import jsonschema

    registry = model_registry.load_registry()
    jsonschema.validate(registry, json.loads(
        model_registry.SCHEMA_PATH.read_text(encoding="utf-8")))
    manifests = model_registry.edition_manifests(registry)
    commercial = manifests["commercial"]
    personal = manifests["personal"]
    assert set(commercial["capabilities"]) <= set(personal["capabilities"])
    assert {row["id"] for row in commercial["implementations"]} <= {
        row["id"] for row in personal["implementations"]}
    assert "audio_flamingo.nvidia_next" not in {
        row["id"] for row in commercial["implementations"]}
    assert "insightface.buffalo_l" not in {
        row["id"] for row in commercial["implementations"]}


def test_research_only_implementations_are_not_client_eligible():
    registry = model_registry.load_registry()
    for implementation in registry["implementations"]:
        if implementation["id"] in {"audio_flamingo.nvidia_next", "insightface.buffalo_l"}:
            assert not model_registry._authorized(
                implementation, "personal", "commercial_client")
            assert model_registry._authorized(
                implementation, "personal", "permitted_research")


def test_unknown_license_status_fails_closed():
    registry = model_registry.load_registry()
    target = copy.deepcopy(registry["implementations"][0])
    target["license_evidence"]["approval_status"] = "unresolved"
    target["editions"] = ["commercial", "personal"]
    target["id"] += ".unknown_license_case"
    registry["implementations"].append(target)
    with pytest.raises(model_registry.ModelRegistryError, match="unresolved license"):
        model_registry.validate_registry(registry)


def test_component_policy_cannot_be_widened_by_model_registry():
    registry = model_registry.load_registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "insightface.buffalo_l")
    target["editions"] = ["commercial", "personal"]
    with pytest.raises(model_registry.ModelRegistryError, match="edition claim exceeds"):
        model_registry.validate_registry(registry)


def test_personal_manifest_derives_same_known_good_client_routes():
    manifests = model_registry.edition_manifests()
    commercial_ids = {row["id"] for row in manifests["commercial"]["implementations"]}
    personal_ids = {row["id"] for row in manifests["personal"]["implementations"]}
    assert commercial_ids == personal_ids
