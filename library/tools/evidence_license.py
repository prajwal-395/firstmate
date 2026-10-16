"""Model-license provenance on derived evidence (DATA-053).

The model registry lane owns which implementations may run under which
edition and workload purpose; this module is the evidence side of that
boundary. It stamps the registry's verdict onto evidence rows and
refuses reuse that the verdict does not authorize, fail-closed: a row
with no license context, an unknown implementation, or a purpose the
license does not cover is refused, never passed through.

Provenance rows already carry ``producer`` and ``producer_version``;
``license_context`` is the additive record beside them naming the
registry implementation, its approval status, the checkpoint revision
the registry recorded, and the workload purpose the evidence was
generated under. Reuse under a different purpose re-checks; it never
inherits the old stamp.
"""
from __future__ import annotations

from typing import Any

try:
    from library.tools import model_registry
except ImportError:  # imported as `tools.*` from inside library/
    from tools import model_registry

LICENSE_CONTEXT_KEY = "license_context"
"""Additive evidence key; schema rows ignore it, readers enforce it."""


def implementation_license(implementation_id: str,
                           registry: dict[str, Any] | None = None
                           ) -> dict[str, Any]:
    """Read the registry verdict for one implementation.

    Returns the approval status, authorized editions and purposes, the
    component id, and the checkpoint identity the registry recorded.
    Unknown ids raise: an unrecorded implementation authorizes nothing.
    """
    registry = registry or model_registry.load_registry()
    model_registry.validate_registry(registry)
    for implementation in registry.get("implementations", []):
        if implementation.get("id") == implementation_id:
            evidence = implementation.get("license_evidence", {})
            checkpoint = {key: implementation.get(key) for key in
                          ("checkpoint", "weights", "revision", "provider",
                           "conversion", "runtime")
                          if implementation.get(key) is not None}
            return {"implementation_id": implementation_id,
                    "approval_status": evidence.get("approval_status"),
                    "editions": list(implementation.get("editions", [])),
                    "purposes": list(implementation.get("purposes", [])),
                    "component_id": implementation.get("component_id"),
                    "checkpoint": checkpoint}
    raise KeyError(f"unrecorded model implementation {implementation_id!r}")


def authorized_for(implementation_id: str, edition: str, purpose: str,
                   registry: dict[str, Any] | None = None) -> bool:
    """Whether the registry authorizes an implementation for an
    edition and workload purpose, via the public manifest route."""
    manifest = model_registry.manifest(edition, purpose, registry)
    return any(item.get("id") == implementation_id
               for item in manifest.get("implementations", []))


def stamp_license(rows: list[dict[str, Any]], implementation_id: str,
                  purpose: str, registry: dict[str, Any] | None = None
                  ) -> list[dict[str, Any]]:
    """Copy rows with the registry verdict stamped beside provenance.

    Rows are copied, never mutated, so a purpose-specific stamp cannot
    leak into the canonical record. Stamping records the verdict; it
    does not grant reuse, which ``reuse_allowed`` decides per purpose.
    """
    verdict = implementation_license(implementation_id, registry)
    if purpose not in model_registry.PURPOSES:
        raise ValueError(f"unknown workload purpose {purpose!r}")
    stamped = []
    for row in rows:
        copy = dict(row)
        copy[LICENSE_CONTEXT_KEY] = {
            "implementation_id": implementation_id,
            "approval_status": verdict["approval_status"],
            "checkpoint": verdict["checkpoint"],
            "generated_under_purpose": purpose,
        }
        stamped.append(copy)
    return stamped


def reuse_allowed(rows: list[dict[str, Any]], purpose: str, *,
                  edition: str = "commercial",
                  registry: dict[str, Any] | None = None
                  ) -> dict[str, Any]:
    """Fail-closed reuse check for evidence crossing a purpose boundary.

    Every row must carry a license context whose implementation the
    registry authorizes for the requested edition and purpose. Rows
    without one, with unknown implementations, or outside the verdict
    are refused with the reason named per row. Reuse under a stricter
    purpose than generation re-checks from the registry; the old stamp
    is evidence, not permission.
    """
    if purpose not in model_registry.PURPOSES:
        raise ValueError(f"unknown workload purpose {purpose!r}")
    registry = registry or model_registry.load_registry()
    model_registry.validate_registry(registry)
    allowed, refused = [], []
    for row in rows:
        context = row.get(LICENSE_CONTEXT_KEY)
        row_id = row.get("id", "<unidentified>")
        if not isinstance(context, dict):
            refused.append({"id": row_id,
                            "reason": "no license context recorded"})
            continue
        implementation_id = context.get("implementation_id")
        try:
            verdict = implementation_license(implementation_id, registry)
        except KeyError:
            refused.append({"id": row_id, "reason": (
                f"unrecorded model implementation {implementation_id!r}")})
            continue
        if verdict["approval_status"] not in ("approved", "conditional"):
            if not (verdict["approval_status"] == "rejected"
                    and purpose == "permitted_research"):
                refused.append({"id": row_id, "reason": (
                    f"license status {verdict['approval_status']!r} does not "
                    f"authorize {purpose}")})
                continue
        if not authorized_for(implementation_id, edition, purpose, registry):
            refused.append({"id": row_id, "reason": (
                f"{implementation_id!r} is not authorized for "
                f"{edition}/{purpose}")})
            continue
        allowed.append(row)
    return {"allowed": allowed, "refused": refused,
            "verdict": "reuse_refused" if refused else "reuse_permitted"}
