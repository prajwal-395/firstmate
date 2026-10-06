"""Typed schema for the ResolveFX/OFX effects provider registry.

Each entry records one effect or effect-related capability: its id and
name as Resolve shows it, its category, how it is applied (api where
the scripting API can, otherwise ui), its parameters with type, range
and default where known, whether it is installed (present in the
Fusion registry on this machine), whether it is verified (an
application of it was actually read back), and the resolve_versions
each was observed on.

The installed list is built on the user's machine from
Fusion.GetRegSummary and cached per machine, outside the repo. The
verified list starts empty and is populated only when an application
of the effect has actually been read back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PARAMETER_TYPES = ("str", "int", "float", "bool", "enum")

APPLICATION_METHODS = ("api", "ui")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SchemaError(ValueError):
    """A registry entry does not satisfy the schema."""


@dataclass
class EffectParameter:
    """One parameter of an effect, with its type, range and default."""

    name: str
    type: str
    description: str = ""
    range: Optional[str] = None
    default: Optional[Any] = None
    values: Optional[List[str]] = None

    def __post_init__(self):
        if not self.name:
            raise SchemaError("parameter name must be non-empty")
        if self.type not in PARAMETER_TYPES:
            raise SchemaError(
                f"parameter {self.name!r} has invalid type {self.type!r}; "
                f"must be one of {PARAMETER_TYPES}")
        if self.type == "enum" and not self.values:
            raise SchemaError(
                f"enum parameter {self.name!r} must list its values")


@dataclass
class EffectEntry:
    """One entry in the ResolveFX/OFX effects provider registry."""

    id: str
    name: str
    category: str
    application: str
    description: str = ""
    api_method: Optional[str] = None
    parameters: List[EffectParameter] = field(default_factory=list)
    installed: bool = False
    installed_evidence: Optional[str] = None
    installed_resolve_versions: List[str] = field(default_factory=list)
    verified: bool = False
    evidence: Optional[str] = None
    resolve_versions: List[str] = field(default_factory=list)
    intent_words: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not _ID_RE.match(self.id):
            raise SchemaError(
                f"effect id {self.id!r} must be lowercase snake_case")
        if not self.name:
            raise SchemaError(f"effect {self.id!r} must have a name")
        if not self.category:
            raise SchemaError(f"effect {self.id!r} must have a category")
        if self.application not in APPLICATION_METHODS:
            raise SchemaError(
                f"effect {self.id!r} has invalid application "
                f"{self.application!r}; must be one of {APPLICATION_METHODS}")
        if self.application == "api" and not self.api_method:
            raise SchemaError(
                f"api effect {self.id!r} must name its api_method")
        if self.installed and not self.installed_evidence:
            raise SchemaError(
                f"installed effect {self.id!r} must carry installed_evidence")
        if not self.installed and self.installed_evidence:
            raise SchemaError(
                f"uninstalled effect {self.id!r} must not carry "
                f"installed_evidence")
        if self.verified and not self.evidence:
            raise SchemaError(
                f"verified effect {self.id!r} must carry an evidence ref")
        if not self.verified and self.evidence:
            raise SchemaError(
                f"unverified effect {self.id!r} must not carry an evidence ref")
        if self.verified and not self.resolve_versions:
            raise SchemaError(
                f"verified effect {self.id!r} must list resolve_versions")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EffectEntry":
        params = [
            EffectParameter(**p) for p in data.get("parameters", [])
        ]
        return cls(
            id=data["id"],
            name=data["name"],
            category=data["category"],
            application=data["application"],
            description=data.get("description", ""),
            api_method=data.get("api_method"),
            parameters=params,
            installed=data.get("installed", False),
            installed_evidence=data.get("installed_evidence"),
            installed_resolve_versions=data.get("installed_resolve_versions", []),
            verified=data.get("verified", False),
            evidence=data.get("evidence"),
            resolve_versions=data.get("resolve_versions", []),
            intent_words=data.get("intent_words", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "application": self.application,
            "description": self.description,
            "api_method": self.api_method,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "range": p.range,
                    "default": p.default,
                    "values": p.values,
                }
                for p in self.parameters
            ],
            "installed": self.installed,
            "installed_evidence": self.installed_evidence,
            "installed_resolve_versions": self.installed_resolve_versions,
            "verified": self.verified,
            "evidence": self.evidence,
            "resolve_versions": self.resolve_versions,
            "intent_words": self.intent_words,
        }


def validate_registry(entries: List[EffectEntry]) -> None:
    """Validate a list of entries, checking for duplicate ids."""
    seen = set()
    for entry in entries:
        if entry.id in seen:
            raise SchemaError(f"duplicate effect id {entry.id!r}")
        seen.add(entry.id)
