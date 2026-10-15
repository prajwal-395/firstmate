"""Compile process goals into capability dependency plans.

Dependency truth comes from capability contracts. A process supplies the
goal set and default policy; legacy DAG nodes are used only at the runner
boundary while state and review services still use node identities.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from library.tools.capabilities import CapabilitySpec


@dataclass(frozen=True)
class CapabilityPlan:
    goals: tuple[str, ...]
    ordered: tuple[str, ...]
    omitted: tuple[str, ...]


class PlanError(ValueError):
    """A requested capability plan cannot be satisfied."""


def compile_plan(specs: Iterable[CapabilitySpec], goals: Iterable[str], *,
                 excluded: Iterable[str] = (),
                 satisfied: Iterable[str] = (),
                 default_off: Iterable[str] = ()) -> CapabilityPlan:
    """Resolve goals using typed capability reads and writes.

    `satisfied` names capability outputs already present in the project.
    `excluded` and `default_off` are process policy, deliberately kept out
    of the dependency declarations.
    """
    specs = tuple(specs)
    by_id = {spec.id: spec for spec in specs}
    goal_ids = tuple(dict.fromkeys(goals))
    unknown = sorted(set(goal_ids) - set(by_id))
    if unknown:
        raise PlanError(f"unknown capability goal(s): {unknown}")
    excluded_set = set(excluded) | set(default_off)
    satisfied_set = set(satisfied)
    unknown_policy = sorted((excluded_set | satisfied_set) - set(by_id))
    if unknown_policy:
        raise PlanError(
            f"unknown capability policy id(s): {unknown_policy}")

    # Capability state keys are the typed links between outputs and inputs.
    producer_map: dict[str, set[str]] = {}
    for spec in specs:
        for key in spec.produces:
            producer_map.setdefault(key, set()).add(spec.id)
    edges: dict[str, set[str]] = {spec.id: set() for spec in specs}
    for spec in specs:
        for key in spec.consumes:
            edges[spec.id].update(producer_map.get(key, ()))
        edges[spec.id].discard(spec.id)

    needed: set[str] = set()
    pending = list(reversed(goal_ids))
    while pending:
        current = pending.pop()
        if current in needed or current in satisfied_set:
            continue
        if current in excluded_set:
            raise PlanError(f"goal dependency {current!r} is excluded")
        needed.add(current)
        for parent in sorted(edges[current], reverse=True):
            if parent not in satisfied_set:
                pending.append(parent)

    ordered: list[str] = []
    remaining = set(needed)
    while remaining:
        ready = [spec.id for spec in specs
                 if spec.id in remaining and not (edges[spec.id] & remaining)]
        if not ready:
            raise PlanError("capability dependency cycle: "
                            f"{sorted(remaining)}")
        current = ready[0]
        ordered.append(current)
        remaining.remove(current)

    excluded_by_policy = [spec.id for spec in specs
                          if spec.id in excluded_set and spec.id not in needed]
    omitted = tuple(excluded_by_policy + [spec.id for spec in specs
                    if spec.id not in needed and spec.id not in excluded_set])
    return CapabilityPlan(goal_ids, tuple(ordered), omitted)


def legacy_steps(plan: CapabilityPlan,
                 specs: Iterable[CapabilitySpec]) -> tuple[str, ...]:
    """Project a plan to legacy runner keys for compatibility services."""
    by_id = {spec.id: spec for spec in specs}
    return tuple(by_id[capability_id].legacy.node_id
                 for capability_id in plan.ordered
                 if by_id[capability_id].legacy is not None)
