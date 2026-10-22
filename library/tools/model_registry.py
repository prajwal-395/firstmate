"""Capability-oriented model implementation and edition manifest registry.

The registry extends the planner capability vocabulary without conflating
planner operations with inference providers. License evidence is fail-closed:
missing or unresolved evidence never authorizes commercial use.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from library.tools import model_admission
from ren import edition

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "library" / "model_registry" / "registry.json"
SCHEMA_PATH = ROOT / "library" / "model_registry" / "schema.json"
EVIDENCE_SNAPSHOT_PATH = ROOT / "library" / "model_registry" / "license_evidence.json"
USER_LICENSES_ENV = "REN_MODEL_LICENSES"
LICENSE_RECORDS_DIR_ENV = "REN_LICENSE_RECORDS_DIR"
PURPOSES = {"commercial_client", "personal_noncommercial", "permitted_research"}
ROUTING_MODES = {"quality_first", "balanced", "fastest_acceptable"}


class ModelRegistryError(ValueError):
    """The model registry is invalid or an edition cannot be authorized."""


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        import os

        root = Path(os.environ.get("REN_MODEL_REGISTRY_ROOT", ROOT))
        path = root / "library" / "model_registry" / "registry.json"
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
        runtime_env = implementation.get("runtime_env")
        if runtime_env is not None:
            if not runtime_env.get("name"):
                raise ModelRegistryError(f"{implementation.get('id')}: runtime environment must name its environment")
            if runtime_env.get("isolated") and not runtime_env.get("requirements"):
                raise ModelRegistryError(
                    f"{implementation.get('id')}: isolated runtime environment must record its requirements file")
        user_license = implementation.get("user_license")
        if user_license is not None:
            if not user_license.get("license_name") or not user_license.get("entitlement_env"):
                raise ModelRegistryError(
                    f"{implementation.get('id')}: user license slot must name its license and entitlement variable")
        # An optional model is admitted only with a complete record: its
        # licence, its commercial use, the size of the weights it downloads
        # and the measured gain it must show. A record that is malformed is
        # refused here; a record that is MISSING is refused by the gate, at
        # the point the model would be loaded.
        if implementation.get("admission") is not None:
            try:
                model_admission.validate_record(implementation)
            except model_admission.AdmissionRecordError as exc:
                raise ModelRegistryError(str(exc)) from exc


def _authorized(
    implementation: dict[str, Any],
    edition_name: str,
    purpose: str,
    user_licenses: dict[str, dict[str, str]] | None = None,
) -> bool:
    evidence = implementation["license_evidence"]
    if edition_name not in implementation["editions"] or purpose not in implementation["purposes"]:
        return False
    if implementation.get("user_license") is not None:
        if entitlement_authorizes(implementation, user_licenses) is None:
            return False
        if evidence["approval_status"] == "unresolved":
            return False
        if evidence["approval_status"] == "rejected":
            return purpose == "permitted_research"
    elif evidence["approval_status"] == "unresolved":
        return False
    if purpose == "commercial_client" and evidence["approval_status"] not in {"approved", "conditional"}:
        return False
    if evidence["approval_status"] == "rejected":
        return purpose == "permitted_research"
    return True


def user_entitlements(
    source: str | dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """Read user-supplied license entitlements; malformed entries refuse.

    The default source is the REN_MODEL_LICENSES environment variable, a
    JSON object mapping implementation IDs to {"license_name", "key"}
    records. Unknown implementation IDs refuse rather than being ignored.
    """
    import os

    if source is None:
        raw = os.environ.get(USER_LICENSES_ENV, "")
        if not raw.strip():
            return {}
        source = raw
    if isinstance(source, dict):
        data = source
    else:
        try:
            data = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ModelRegistryError(f"{USER_LICENSES_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelRegistryError(f"{USER_LICENSES_ENV} must map implementation IDs to entitlement records")
    entitlements: dict[str, dict[str, str]] = {}
    for implementation_id, entry in data.items():
        if not isinstance(entry, dict) or not entry.get("license_name") or not entry.get("key"):
            raise ModelRegistryError(
                f"entitlement for {implementation_id!r} must name license_name and a non-empty key")
        entitlements[implementation_id] = {"license_name": entry["license_name"], "key": entry["key"]}
    if registry is not None:
        known = {row["id"] for row in registry.get("implementations", [])}
        unknown = sorted(set(entitlements) - known)
        if unknown:
            raise ModelRegistryError(f"entitlements name unregistered implementations: {unknown}")
    return entitlements


def entitlement_authorizes(
    implementation: dict[str, Any],
    user_licenses: dict[str, dict[str, str]] | None,
) -> str | None:
    """Return the license name when a user entitlement matches the slot."""
    slot = implementation.get("user_license")
    if not slot:
        return None
    entry = (user_licenses or {}).get(implementation["id"])
    if not entry or entry.get("license_name") != slot["license_name"] or not entry.get("key"):
        return None
    return slot["license_name"]


def isolated_runtime_envs(registry: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Map each isolated runtime environment to the implementations needing it."""
    registry = registry or load_registry()
    envs: dict[str, list[str]] = {}
    for item in registry["implementations"]:
        env = item.get("runtime_env") or {}
        if env.get("isolated"):
            envs.setdefault(env["name"], []).append(item["id"])
    return envs


def manifest(edition_name: str, purpose: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build derived manifest; Personal is a superset for identical workload.

    A manifest is an eligibility list (license status, edition, workload
    purpose). Measured qualification gates SELECTION in ``route``, never
    eligibility here: nothing is qualified before the benchmark lanes run,
    and an eligibility list that requires qualification is empty.
    """
    if edition_name not in {"commercial", "personal"}:
        raise ModelRegistryError(f"unknown model edition {edition_name!r}")
    if purpose not in PURPOSES:
        raise ModelRegistryError(f"unknown workload purpose {purpose!r}")
    if registry is None:
        registry = load_registry()
    validate_registry(registry)
    entries = [item for item in registry["implementations"]
               if _authorized(item, edition_name, purpose)]
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
    user_licenses: dict[str, dict[str, str]] | None = None,
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
    entitlements = user_entitlements(None, registry) if user_licenses is None else user_licenses
    hardware = hardware or {}
    edition_name = "commercial" if purpose == "commercial_client" else "personal"
    rejected: list[dict[str, str]] = []
    feasible: list[dict[str, Any]] = []
    for item in registry["implementations"]:
        if capability not in item["capabilities"]:
            continue
        reason = _ineligible_reason(item, edition_name, purpose, hardware, quality_floor, entitlements)
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
        "runtime_env": selected.get("runtime_env") or {"name": "primary", "isolated": False},
        "user_license_used": entitlement_authorizes(selected, entitlements),
    }
    return decision


def _ineligible_reason(
    implementation: dict[str, Any], edition_name: str, purpose: str,
    hardware: dict[str, Any], quality_floor: float,
    user_licenses: dict[str, dict[str, str]] | None = None,
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
    slot = implementation.get("user_license")
    if slot is not None and entitlement_authorizes(implementation, user_licenses) is None:
        return f"a user-supplied {slot['license_name']} license is required"
    missing = model_admission.missing_fields(implementation)
    if missing:
        return f"admission record is missing {', '.join(missing)}"
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
    user_licenses: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Authorize a fetch/load/package action against current license evidence."""
    registry = registry or load_registry()
    validate_registry(registry)
    entitlements = user_entitlements(None, registry) if user_licenses is None else user_licenses
    edition_name = edition_name or (
        "commercial" if purpose == "commercial_client" else "personal")
    item = next((row for row in registry["implementations"]
                 if row["id"] == implementation_id), None)
    if item is None:
        raise ModelRegistryError(f"unknown model implementation {implementation_id!r}")
    if not _authorized(item, edition_name, purpose, entitlements):
        raise ModelRegistryError(
            f"cannot {action} model implementation {implementation_id!r}: "
            f"license/purpose {purpose!r} is not authorized "
            f"(status={item['license_evidence']['approval_status']})")
    try:
        model_admission.require_record(item, action=action)
    except model_admission.AdmissionRecordError as exc:
        raise ModelRegistryError(str(exc)) from exc
    return item


def installed_capability_report(
    installed_components: set[str],
    *,
    purpose: str,
    edition_name: str,
    registry: dict[str, Any] | None = None,
    user_licenses: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Report every implementation as available, missing, blocked, or unqualified."""
    registry = registry or load_registry()
    validate_registry(registry)
    entitlements = user_entitlements(None, registry) if user_licenses is None else user_licenses
    rows = []
    for item in registry["implementations"]:
        evidence = item["license_evidence"]
        runtime_env = item.get("runtime_env") or {"name": "primary", "isolated": False}
        slot = item.get("user_license")
        if not _authorized(item, edition_name, purpose, entitlements):
            status = "blocked"
            if slot is not None and entitlement_authorizes(item, entitlements) is None:
                reason = f"requires a user-supplied {slot['license_name']} license"
            else:
                reason = f"license or workload purpose disallows {purpose}"
        elif item["component_id"] not in installed_components:
            status = "missing"
            reason = "component is not installed"
        elif item["qualification"] != "qualified":
            status = "unqualified"
            reason = f"qualification is {item['qualification']}"
        else:
            status = "available"
            reason = "qualified component is installed and authorized"
        rows.append({"implementation_id": item["id"],
                     "component_id": item["component_id"],
                     "capabilities": item["capabilities"],
                     "status": status, "reason": reason,
                     "license_status": evidence["approval_status"],
                     "runtime_env": runtime_env["name"],
                     "user_license": slot["license_name"] if slot else None})
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


def authorize_component(
    component_id: str, *, action: str, purpose: str,
    user_licenses: dict[str, dict[str, str]] | None = None,
) -> None:
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
                                   edition_name=edition_name, registry=registry,
                                   user_licenses=user_licenses)
            return
        except ModelRegistryError as exc:
            failures.append(str(exc))
    raise ModelRegistryError("; ".join(failures))


LICENSE_ARTIFACT_MAP = {
    "audio_flamingo_next": ("audio_flamingo.nvidia_next",),
    "ast_audio_labels": ("ast.audioset",),
    "depth_v2_small": ("depth_anything_v2.small",),
    "florence2": ("florence2.base",),
    "owlv2": ("owlv2.base_patch16",),
    "grounding_dino": ("grounding_dino.tiny",),
    "sam21": ("sam2.1_hiera_small",),
    "clip": ("clip.openai_vit_large_patch14",),
    "deepfilternet": ("deepfilternet.v0_5_6",),
    "gemma4": ("gemma.vision_4_12b_it_4bit",),
    "insightface_buffalo_l": ("insightface.buffalo_l",),
    "minilm": ("minilm.all_minilm_l6_v2",),
    "panns_weights": ("panns.cnn14_decision_max",),
    "sortformer_nvidia_v21": ("sortformer.offline_coreml",),
    "sortformer_coreml_6bit": ("sortformer.offline_coreml",),
}


def _snapshot_records() -> list[dict[str, Any]]:
    """Read the versioned in-repo license-evidence snapshot.

    The snapshot is the only evidence source product code may read: raw
    license records live with the vep-mu-license lane outside the repo and
    are snapshotted in at ingest time, never referenced by path.
    """
    snapshot = json.loads(EVIDENCE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return snapshot["records"]


def ingest_license_records(
    registry: dict[str, Any] | None = None,
    records_dir: str | Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Apply primary-source evidence records to explicitly mapped implementations.

    With no explicit directory the versioned in-repo snapshot is applied.
    Pass ``records_dir`` (or set ``REN_LICENSE_RECORDS_DIR``) only when
    re-ingesting fresh lane records into a new snapshot.
    """
    import os

    if records_dir is None:
        records_dir = os.environ.get(LICENSE_RECORDS_DIR_ENV)
    if records_dir is None:
        records = _snapshot_records()
        labels = [record["artifact_id"] for record in records]
    else:
        paths = sorted(Path(records_dir).glob("*.json"))
        records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        labels = [path.name for path in paths]
    registry = registry or load_registry()
    by_id = {row["id"]: row for row in registry["implementations"]}
    unmapped = []
    for label, record in zip(labels, records):
        targets = LICENSE_ARTIFACT_MAP.get(record["artifact_id"])
        if targets is None:
            unmapped.append(record["artifact_id"])
            continue
        status = {"approved": "approved", "conditional": "conditional",
                  "research-only": "rejected", "quarantine": "unresolved",
                  "unresolved": "unresolved"}.get(record["status"])
        if status is None:
            raise ModelRegistryError(
                f"{label}: unsupported license evidence status {record['status']!r}")
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
            # The admission record states the licence and the commercial use
            # of the model it admits. New evidence moves both, or the record
            # would contradict the evidence it claims to summarise.
            admission = implementation.get("admission")
            if admission is not None:
                admission["license"] = record["license"]
                admission["commercial_use"] = model_admission.COMMERCIAL_USE_BY_APPROVAL[status]
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
    known = set(LICENSE_ARTIFACT_MAP)
    if records_dir is None:
        records = _snapshot_records()
    else:
        records = [json.loads(path.read_text(encoding="utf-8"))
                   for path in sorted(Path(records_dir).glob("*.json"))]
    found = [record["artifact_id"] for record in records
             if record["artifact_id"] not in known]
    return found


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("manifests", "notices", "installed-report", "runtime-envs"))
    parser.add_argument("--edition", choices=("commercial", "personal"), default="personal")
    parser.add_argument("--purpose", choices=sorted(PURPOSES), default="commercial_client")
    parser.add_argument("--installed-component", action="append", default=[])
    parser.add_argument("--include-unmapped", action="store_true")
    args = parser.parse_args()
    if args.command == "manifests":
        output = edition_manifests()
    elif args.command == "runtime-envs":
        output = {"isolated_runtime_envs": isolated_runtime_envs()}
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
