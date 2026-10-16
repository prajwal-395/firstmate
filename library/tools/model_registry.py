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
ROUTING_MODES = {"quality_first", "balanced", "fastest_acceptable"}


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
        primary = evidence.get("primary_evidence")
        if primary is not None:
            status_map = {
                "approved": "approved", "conditional": "conditional",
                "research-only": "rejected", "quarantine": "unresolved",
                "unresolved": "unresolved",
            }
            if primary.get("status") not in status_map:
                raise ModelRegistryError(
                    f"{implementation.get('id')}: unknown primary evidence status")
            if evidence.get("approval_status") != status_map[primary["status"]]:
                raise ModelRegistryError(
                    f"{implementation.get('id')}: approval status conflicts with primary evidence")
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
    if evidence["approval_status"] == "rejected":
        return purpose == "permitted_research"
    return True


def manifest(edition_name: str, purpose: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build derived manifest; Personal is a superset for identical workload."""
    if edition_name not in {"commercial", "personal"}:
        raise ModelRegistryError(f"unknown model edition {edition_name!r}")
    if purpose not in PURPOSES:
        raise ModelRegistryError(f"unknown workload purpose {purpose!r}")
    if registry is None:
        root = Path(__import__("os").environ.get("REN_MODEL_REGISTRY_ROOT", ROOT))
        registry = load_registry(root / "library" / "model_registry" / "registry.json")
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


def route(
    capability: str,
    *,
    purpose: str,
    mode: str,
    quality_floor: float = 0.0,
    override: str | None = None,
    hardware: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select a licensed implementation and return a durable decision record.

    Scores are qualification measurements supplied in registry metadata; this
    router does not infer model quality. Missing scores/resources cannot meet a
    nonzero floor or an explicit hardware constraint.
    """
    if mode not in ROUTING_MODES:
        raise ModelRegistryError(f"unknown routing mode {mode!r}")
    if purpose not in PURPOSES:
        raise ModelRegistryError(f"unknown workload purpose {purpose!r}")
    if not 0.0 <= quality_floor <= 1.0:
        raise ModelRegistryError("quality_floor must be between 0 and 1")
    registry = registry or load_registry()
    validate_registry(registry)
    hardware = hardware or {}
    edition_name = "commercial" if purpose == "commercial_client" else "personal"
    rejected: list[dict[str, str]] = []
    feasible: list[dict[str, Any]] = []
    for item in registry["implementations"]:
        if capability not in item["capabilities"]:
            continue
        reason = _ineligible_reason(item, edition_name, purpose, hardware, quality_floor)
        if reason:
            rejected.append({"implementation_id": item["id"], "reason": reason})
            continue
        feasible.append(item)
    if override is not None:
        selected = next((item for item in feasible if item["id"] == override), None)
        if selected is None:
            raise ModelRegistryError(
                f"override {override!r} is unavailable for {capability!r}: "
                f"{_reason_for_id(rejected, override)}")
        rationale = "explicit override among eligible implementations"
    elif not feasible:
        raise ModelRegistryError(
            f"no eligible implementation for {capability!r}; rejected="
            f"{json.dumps(rejected, sort_keys=True)}")
    else:
        selected = _rank(feasible, mode)[0]
        rationale = f"selected by {mode} among license- and resource-eligible routes"
    alternatives = [item for item in _rank(feasible, mode) if item["id"] != selected["id"]]
    decision = {
        "schema_version": 1,
        "capability": capability,
        "purpose": purpose,
        "mode": mode,
        "selected_implementation": selected["id"],
        "alternatives": [item["id"] for item in alternatives],
        "rejected": rejected,
        "resource_estimates": selected.get("resources", {}),
        "qualification": selected.get("qualification", "unknown"),
        "rationale": rationale,
        "fallback_chain": [selected["id"], *[item["id"] for item in alternatives]],
    }
    return decision


def _ineligible_reason(
    implementation: dict[str, Any], edition_name: str, purpose: str,
    hardware: dict[str, Any], quality_floor: float,
) -> str | None:
    evidence = implementation["license_evidence"]
    if purpose not in implementation["purposes"]:
        return f"workload purpose {purpose!r} is not authorized"
    if edition_name not in implementation["editions"]:
        return f"implementation is not included in {edition_name} edition"
    if evidence["approval_status"] in {"unresolved", "rejected"}:
        return f"license status is {evidence['approval_status']}"
    if purpose == "commercial_client" and evidence["approval_status"] not in {"approved", "conditional"}:
        return "license evidence does not authorize client work"
    if implementation.get("qualification") != "qualified":
        return f"qualification is {implementation.get('qualification', 'unknown')}"
    metrics = implementation.get("qualification_metrics", {})
    if metrics.get("quality") is None or metrics["quality"] < quality_floor:
        return f"quality {metrics.get('quality')!r} is below required floor {quality_floor}"
    resources = implementation.get("resources", {})
    for resource in ("memory_gb", "runtime_seconds"):
        ceiling_key = f"max_{resource}"
        if ceiling_key in hardware and (resources.get(resource) is None or resources[resource] > hardware[ceiling_key]):
            return f"resource {resource} exceeds available budget"
    if hardware.get("accelerators"):
        required = set(implementation.get("hardware", {}).get("accelerators", ()))
        if required and not required.intersection(hardware["accelerators"]):
            return "no required accelerator is available"
    return None


def _rank(implementations: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    def values(item: dict[str, Any]) -> tuple[float, float, float]:
        quality = item.get("qualification_metrics", {}).get("quality") or 0.0
        runtime = item.get("resources", {}).get("runtime_seconds")
        memory = item.get("resources", {}).get("memory_gb")
        return quality, -(runtime if runtime is not None else float("inf")), -(memory if memory is not None else float("inf"))

    if mode == "quality_first":
        key = lambda item: (*values(item), item["id"])
    elif mode == "fastest_acceptable":
        key = lambda item: (values(item)[1], values(item)[0], values(item)[2], item["id"])
    else:
        key = lambda item: (sum(values(item)), values(item)[0], item["id"])
    return sorted(implementations, key=key, reverse=True)


def _reason_for_id(rejected: list[dict[str, str]], implementation_id: str) -> str:
    return next((item["reason"] for item in rejected
                 if item["implementation_id"] == implementation_id), "not registered for this capability")


def require_implementation(
    implementation_id: str,
    *,
    action: str,
    purpose: str,
    edition_name: str | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Authorize a fetch/load/package action against current license evidence."""
    registry = registry or load_registry()
    validate_registry(registry)
    edition_name = edition_name or (
        "commercial" if purpose == "commercial_client" else "personal")
    item = next((row for row in registry["implementations"]
                 if row["id"] == implementation_id), None)
    if item is None:
        raise ModelRegistryError(f"unknown model implementation {implementation_id!r}")
    if not _authorized(item, edition_name, purpose):
        raise ModelRegistryError(
            f"cannot {action} model implementation {implementation_id}: "
            f"license/purpose {purpose!r} is not authorized "
            f"(status={item['license_evidence']['approval_status']})")
    return item


def installed_capability_report(
    installed_components: set[str],
    *,
    purpose: str,
    edition_name: str,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Report every implementation as available, missing, blocked, or unqualified."""
    registry = registry or load_registry()
    validate_registry(registry)
    rows = []
    for item in registry["implementations"]:
        evidence = item["license_evidence"]
        if not _authorized(item, edition_name, purpose):
            status = "blocked"
            reason = f"license or workload purpose disallows {purpose}"
        elif item["qualification"] != "qualified":
            status = "unqualified"
            reason = f"qualification is {item['qualification']}"
        elif item["component_id"] not in installed_components:
            status = "missing"
            reason = "component is not installed"
        else:
            status = "available"
            reason = "qualified component is installed and authorized"
        rows.append({"implementation_id": item["id"],
                     "component_id": item["component_id"],
                     "capabilities": item["capabilities"],
                     "status": status, "reason": reason,
                     "license_status": evidence["approval_status"]})
    return {"schema_version": 1, "edition": edition_name,
            "purpose": purpose, "implementations": rows}


def notices(registry: dict[str, Any] | None = None) -> str:
    """Generate a concise attribution and license-obligation inventory."""
    registry = registry or load_registry()
    validate_registry(registry)
    lines = ["Ren model and checkpoint notices", "", "Generated from registry.json.",
             "Conditional and unresolved entries require review before distribution.", ""]
    for item in registry["implementations"]:
        evidence = item["license_evidence"]
        if evidence["approval_status"] in {"unresolved", "rejected"}:
            continue
        lines.extend((f"## {item['label']}",
                      f"- Capability: {', '.join(item['capabilities'])}",
                      f"- License: {evidence['license'] or 'UNRESOLVED'}",
                      f"- Approval: {evidence['approval_status']}",
                      f"- Source: {evidence['upstream_url'] or 'not recorded'}"))
        lines.extend(f"- Obligation: {obligation}"
                     for obligation in evidence["obligations"])
        lines.append("")
    lines.append("Restricted or unresolved implementations are excluded from this notice and commercial manifest.")
    return "\n".join(lines)


def authorize_component(component_id: str, *, action: str, purpose: str) -> None:
    """Apply model authorization before a model component is fetched or loaded."""
    registry = load_registry()
    candidates = [row for row in registry["implementations"]
                  if row["component_id"] == component_id]
    if not candidates:
        raise ModelRegistryError(f"no model implementation registered for {component_id}")
    edition_name = "commercial" if purpose == "commercial_client" else "personal"
    failures = []
    for candidate in candidates:
        try:
            require_implementation(candidate["id"], action=action, purpose=purpose,
                                   edition_name=edition_name, registry=registry)
            return
        except ModelRegistryError as exc:
            failures.append(str(exc))
    raise ModelRegistryError("; ".join(failures))


LICENSE_ARTIFACT_MAP = {
    "audio_flamingo_next": ("audio_flamingo.nvidia_next",),
    "clip": ("clip.openai_vit_large_patch14",),
    "deepfilternet": ("deepfilternet.v0_5_6",),
    "gemma4": ("gemma.vision_4_12b_it_4bit",),
    "insightface_buffalo_l": ("insightface.buffalo_l",),
    "minilm": ("minilm.all_minilm_l6_v2",),
    "panns_weights": ("panns.cnn14_decision_max",),
}


def ingest_license_records(
    registry: dict[str, Any] | None = None,
    records_dir: str | Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Apply primary-source evidence records to explicitly mapped implementations.

    Records are passed in, never located by product code: the caller
    supplies the directory holding the license lane's record files, or
    a directory holding a previously snapshotted set. There is no
    default records location.
    """
    if records_dir is None:
        raise ModelRegistryError(
            "ingest_license_records requires records_dir: license records "
            "are passed in, never discovered from a hardcoded location")
    registry = registry or load_registry()
    by_id = {row["id"]: row for row in registry["implementations"]}
    unmapped = []
    for path in sorted(Path(records_dir).glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        targets = LICENSE_ARTIFACT_MAP.get(record["artifact_id"])
        if targets is None:
            unmapped.append(record["artifact_id"])
            continue
        status = {"approved": "approved", "conditional": "conditional",
                  "research-only": "rejected", "quarantine": "unresolved",
                  "unresolved": "unresolved"}.get(record["status"])
        if status is None:
            raise ModelRegistryError(
                f"{path.name}: unsupported license evidence status {record['status']!r}")
        for implementation_id in targets:
            implementation = by_id[implementation_id]
            implementation["license_evidence"].update({
                "upstream_url": record["upstream_url"],
                "exact_version": record["exact_revision"],
                "retrieved_at": record["retrieved_at"].split("T", 1)[0],
                "license": record["license"],
                "obligations": [record["attribution"], record["redistribution"],
                                record["conditions"]],
                "approval_status": status,
                "evidence_source": ", ".join(record["evidence_sources"]),
                "primary_evidence": record,
            })
            implementation["checkpoint"]["version"] = record["exact_revision"]
            implementation["license_evidence"]["approval_status"] = status
            if status in {"unresolved", "rejected"}:
                implementation["purposes"] = [
                    purpose for purpose in implementation["purposes"]
                    if purpose != "commercial_client"]
    validate_registry(registry)
    return registry, unmapped


def unmapped_license_records(
    records_dir: str | Path | None = None,
) -> list[str]:
    """Return evidence artifact IDs without an explicit registry mapping."""
    if records_dir is None:
        raise ModelRegistryError(
            "unmapped_license_records requires records_dir: license records "
            "are passed in, never discovered from a hardcoded location")
    known = set(LICENSE_ARTIFACT_MAP)
    found = []
    for path in sorted(Path(records_dir).glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["artifact_id"] not in known:
            found.append(record["artifact_id"])
    return found


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("manifests", "notices", "installed-report"))
    parser.add_argument("--edition", choices=("commercial", "personal"), default="personal")
    parser.add_argument("--purpose", choices=sorted(PURPOSES), default="commercial_client")
    parser.add_argument("--installed-component", action="append", default=[])
    parser.add_argument("--include-unmapped", action="store_true")
    args = parser.parse_args()
    if args.command == "manifests":
        output = edition_manifests()
    elif args.command == "notices":
        print(notices(), end="")
        return
    else:
        output = installed_capability_report(
            set(args.installed_component), purpose=args.purpose,
            edition_name=args.edition)
    if args.include_unmapped:
        payload = output if isinstance(output, dict) else {"output": output}
        payload["unmapped_license_artifacts"] = unmapped_license_records()
        output = payload
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
