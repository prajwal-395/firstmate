"""FI-22: explicit, versioned rules for subjective footage evaluation.

Policies contain authored requirements and preferences. Every rule names its
origin and every evaluation cites both the rule and the measured observations
it used. No thresholds or default policy are supplied by this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from library.tools import evidence_contract as evidence


class EvaluationPolicyError(ValueError):
    """A policy or requested evaluation is incomplete or invalid."""


@dataclass(frozen=True)
class PolicyRule:
    id: str
    kind: str  # requirement | preference
    measurement_type: str
    operator: str
    expected: Any
    origin: str
    origin_ref: str

    def __post_init__(self) -> None:
        if (not isinstance(self.id, str) or not self.id
                or not isinstance(self.measurement_type, str)
                or not self.measurement_type):
            raise EvaluationPolicyError("policy rule needs an id and measurement type")
        if not isinstance(self.kind, str) or self.kind not in {"requirement", "preference"}:
            raise EvaluationPolicyError(f"unknown policy rule kind {self.kind!r}")
        if not isinstance(self.operator, str) or self.operator not in {"eq", "ne", "lt", "lte", "gt", "gte"}:
            raise EvaluationPolicyError(f"unsupported policy operator {self.operator!r}")
        if (not isinstance(self.origin, str) or not self.origin
                or not isinstance(self.origin_ref, str) or not self.origin_ref):
            raise EvaluationPolicyError("policy rule must name its origin and reference")


@dataclass(frozen=True)
class EvaluationPolicy:
    id: str
    version: int
    project_id: str
    requirements: tuple[PolicyRule, ...] = ()
    preferences: tuple[PolicyRule, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.id, str) or not self.id
                or not isinstance(self.project_id, str) or not self.project_id
                or not isinstance(self.version, int) or isinstance(self.version, bool)
                or self.version < 1):
            raise EvaluationPolicyError("policy needs an id, positive version and project")
        if any(rule.kind != "requirement" for rule in self.requirements):
            raise EvaluationPolicyError("requirements must contain requirement rules")
        if any(rule.kind != "preference" for rule in self.preferences):
            raise EvaluationPolicyError("preferences must contain preference rules")
        ids = [rule.id for rule in (*self.requirements, *self.preferences)]
        if len(ids) != len(set(ids)):
            raise EvaluationPolicyError("policy rule ids must be unique")

    @property
    def ref(self) -> str:
        return f"{self.id}@v{self.version}"

    def to_dict(self) -> dict[str, Any]:
        def rows(rules: tuple[PolicyRule, ...]) -> list[dict[str, Any]]:
            return [rule.__dict__.copy() for rule in rules]

        return {
            "format": "evaluation_policy/1",
            "id": self.id,
            "version": self.version,
            "project_id": self.project_id,
            "requirements": rows(self.requirements),
            "preferences": rows(self.preferences),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvaluationPolicy:
        if value.get("format") != "evaluation_policy/1":
            raise EvaluationPolicyError("unsupported evaluation policy format")

        def rules(key: str) -> tuple[PolicyRule, ...]:
            try:
                return tuple(PolicyRule(**row) for row in value[key])
            except (KeyError, TypeError) as exc:
                raise EvaluationPolicyError(
                    f"policy {key} must be a list of complete rules") from exc

        return cls(
            id=value.get("id"), version=value.get("version"),
            project_id=value.get("project_id"),
            requirements=rules("requirements"), preferences=rules("preferences"))


_COMPARATORS = {
    "eq": lambda left, right: left == right,
    "ne": lambda left, right: left != right,
    "lt": lambda left, right: left < right,
    "lte": lambda left, right: left <= right,
    "gt": lambda left, right: left > right,
    "gte": lambda left, right: left >= right,
}


def evaluate(policy: EvaluationPolicy | None, *, asset_id: str, clock_id: str,
             project_id: str, start_tick: int, end_tick: int,
             measurements: Mapping[str, evidence.EvidenceRecord],
             provenance_id: str) -> list[evidence.EvidenceRecord]:
    """Evaluate declared rules only; absent policy or evidence refuses loudly."""
    if isinstance(policy, Mapping):
        policy = EvaluationPolicy.from_dict(policy)
    if policy is None:
        raise EvaluationPolicyError("evaluation refused: no project policy was supplied")
    if policy.project_id != project_id:
        raise EvaluationPolicyError(
            f"evaluation refused: policy {policy.ref!r} is scoped to "
            f"{policy.project_id!r}, not {project_id!r}")
    results = []
    for rule in (*policy.requirements, *policy.preferences):
        matching = [
            (measurement_id, record)
            for measurement_id, record in measurements.items()
            if record.measurement_type == rule.measurement_type
        ]
        if not matching:
            raise EvaluationPolicyError(
                f"evaluation refused: rule {rule.id!r} requires measured "
                f"evidence {rule.measurement_type!r}, but none was supplied")
        for measurement_id, record in matching:
            if not measurement_id:
                raise EvaluationPolicyError("measurement evidence must have a stable id")
            if record.evidence_type is not evidence.EvidenceType.MEASUREMENT:
                raise EvaluationPolicyError(
                    f"rule {rule.id!r} requires measured evidence; "
                    f"{measurement_id!r} is {record.evidence_type.value}")
            if record.asset_id != asset_id or record.clock_id != clock_id:
                raise EvaluationPolicyError(
                    f"rule {rule.id!r} measurement {measurement_id!r} "
                    "belongs to a different asset or clock")
        measurement_ids = tuple(row[0] for row in matching)
        try:
            passed = all(_COMPARATORS[rule.operator](row.value, rule.expected)
                         for _, row in matching)
        except (TypeError, KeyError) as exc:
            raise EvaluationPolicyError(
                f"rule {rule.id!r} cannot compare the supplied measurement") from exc
        results.append(evidence.new_evaluation(
            asset_id=asset_id, clock_id=clock_id,
            start_tick=start_tick, end_tick=end_tick,
            measurement_type="eval.policy_rule_pass", value=passed,
            method="evaluation_policy/1", provenance_id=provenance_id,
            policy_id=policy.ref, basis_measurement_ids=measurement_ids,
            basis_rule_ids=(rule.id,)))
    return results


def load_policy(project_model: Mapping[str, Any], policy_ref: str) -> EvaluationPolicy:
    """Read one exact policy version from a Project Model; never select a default."""
    matches = [
        row for row in project_model.get("evaluation_policies", [])
        if f"{row.get('id')}@v{row.get('version')}" == policy_ref
    ]
    if len(matches) != 1:
        raise EvaluationPolicyError(
            f"policy {policy_ref!r} is missing or ambiguous in the Project Model")
    policy = EvaluationPolicy.from_dict(matches[0])
    if policy.project_id != project_model.get("id"):
        raise EvaluationPolicyError("policy belongs to a different project")
    return policy
