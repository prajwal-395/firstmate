"""Capability-oriented model implementation and edition manifest registry.

The registry extends the planner capability vocabulary without conflating
planner operations with inference providers. License evidence is fail-closed:
missing or unresolved evidence never authorizes commercial use.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ren import edition

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "library" / "model_registry" / "registry.json"
SCHEMA_PATH = ROOT / "library" / "model_registry" / "schema.json"
PURPOSES = {"commercial_client", "personal_noncommercial", "permitted_research"}


class ModelRegistryError(ValueError):
    """The model registry is invalid or an edition cannot be authorized."""


def load_registry(path: str | Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_registry(registry)
    return registry


def validate_registry(registry: dict[str, Any]) -> None:
    """Validate registry cross references and enforce unknown-license closure."""
    capabilities = registry.get("capabilities")
    implementations = registry.get("implementations")
    if registry.get("schema_version") != 1 or not isinstance(capabilities, list) or not isinstance(implementations, list):
        raise ModelRegistryError("expected registry schema version 1 with capability and implementation lists")
    cap_ids = [item.get("id") for item in capabilities]
    impl_ids = [item.get("id") for item in implementations]
    if len(cap_ids) != len(set(cap_ids)) or len(impl_ids) != len(set(impl_ids)):
        raise ModelRegistryError("capability and implementation IDs must be unique")
    known_caps = set(cap_ids)
    components = edition.read_components(ROOT)
    for implementation in implementations:
        if not set(implementation.get("capabilities", ())) <= known_caps:
            raise ModelRegistryError(f"{implementation.get('id')}: references an unknown capability")
        component_id = implementation.get("component_id")
        if component_id not in components:
            raise ModelRegistryError(f"{implementation.get('id')}: unrecorded component {component_id!r}")
        # Existing component policy remains authoritative for packaging and
        # loading; registry edition claims cannot widen it.
        allowed = {"personal"} if components[component_id].edition == edition.PERSONAL_ONLY else {"commercial", "personal"}
        if not set(implementation.get("editions", ())) <= allowed:
            raise ModelRegistryError(f"{implementation.get('id')}: edition claim exceeds component policy")
        evidence = implementation.get("license_evidence", {})
        if evidence.get("approval_status") not in {"approved", "conditional", "unresolved", "rejected"}:
            raise ModelRegistryError(f"{implementation.get('id')}: unknown license status")
        if evidence.get("approval_status") == "unresolved" and "commercial" in implementation.get("editions", ()):
            raise ModelRegistryError(f"{implementation.get('id')}: unresolved license cannot authorize commercial edition")
        if not set(implementation.get("purposes", ())) <= PURPOSES:
            raise ModelRegistryError(f"{implementation.get('id')}: unknown workload purpose")
        if "commercial_client" in implementation.get("purposes", ()) and evidence.get("approval_status") not in {"approved", "conditional"}:
            raise ModelRegistryError(f"{implementation.get('id')}: license does not authorize commercial workload")


def _authorized(implementation: dict[str, Any], edition_name: str, purpose: str) -> bool:
    evidence = implementation["license_evidence"]
    if edition_name not in implementation["editions"] or purpose not in implementation["purposes"]:
        return False
    if evidence["approval_status"] == "unresolved":
        return False
    if purpose == "commercial_client" and evidence["approval_status"] not in {"approved", "conditional"}:
        return False
    return evidence["approval_status"] != "rejected" or purpose == "permitted_research"


def manifest(edition_name: str, purpose: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build derived manifest; Personal is a superset for identical workload."""
    if edition_name not in {"commercial", "personal"}:
        raise ModelRegistryError(f"unknown model edition {edition_name!r}")
    if purpose not in PURPOSES:
        raise ModelRegistryError(f"unknown workload purpose {purpose!r}")
    registry = registry or load_registry()
    validate_registry(registry)
    entries = [item for item in registry["implementations"] if _authorized(item, edition_name, purpose)]
    caps = sorted({cap for item in entries for cap in item["capabilities"]})
    return {"schema_version": 1, "edition": edition_name, "purpose": purpose,
            "capabilities": caps, "implementations": entries}


def edition_manifests(registry: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Return commercial and personal client-work manifests with parity enforced."""
    registry = registry or load_registry()
    commercial = manifest("commercial", "commercial_client", registry)
    personal = manifest("personal", "commercial_client", registry)
    missing = set(commercial["capabilities"]) - set(personal["capabilities"])
    if missing:
        raise ModelRegistryError(f"personal manifest loses commercial capabilities: {sorted(missing)}")
    personal_routes = {item["id"] for item in personal["implementations"]}
    missing_routes = {item["id"] for item in commercial["implementations"]} - personal_routes
    if missing_routes:
        raise ModelRegistryError(f"personal manifest loses commercial routes: {sorted(missing_routes)}")
    return {"commercial": commercial, "personal": personal}


def main() -> None:
    manifests = edition_manifests()
    print(json.dumps(manifests, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
