"""The matrix a live qualification run walks.

AXI-026 asks that each candidate primitive runs against varied
disposable timeline configurations, and AXI-030 asks that the run
compare the state before and after the operation. This module is the
bookkeeping of that walk: which primitive runs against which
configuration, what the measured states add up to, and what the
resulting evidence report says.

The RUN is the live lane's: it builds a configuration, runs the
operation through its production route, and measures the state before
and after. This module takes what the run measured and turns it into
the evidence the registry imports.

Two rules shape it:

* the coverage is MEASURED, never asserted - a dimension value enters
  the coverage because a run measured it, and a configuration that did
  not run leaves its dimensions out of the claim;
* a run that changed something it was not asked to change does not
  become a coverage - the report says so, and the operation is not
  qualified by it.

`library/tools/axi_qualification_configurations.py` owns the shapes and
`library/tools/axi_qualification_snapshot.py` owns the comparison;
this module joins them.

`tests/unit/resolve/test_axi_qualification_matrix.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from library.tools import axi_qualification
from library.tools import axi_qualification_configurations as configurations
from library.tools import axi_qualification_dimensions as dimensions
from library.tools import axi_qualification_snapshot as snapshots


@dataclass(frozen=True)
class Candidate:
    """One operation, the backend that executes it, and where it runs.

    `target` says how the run picks the item the operation names:
    "first" for the first item on the first populated row, "second" for
    the one after it, and "none" for an operation that creates its own
    item or names no item at all.
    """

    operation: str
    backend: str
    configurations: tuple[str, ...]
    target: str = "first"

    def runs_against(self, configuration_id: str) -> bool:
        return configuration_id in self.configurations


#: The primitives a qualification run walks, and the configurations each
#: one is proven in. A primitive that is not listed for a configuration
#: was NOT run there, and the coverage says so.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("clip.delete", "edit_patch",
              ("populated_1080p24", "linked_av_1080p24", "still_1080p24",
               "graded_1080p24", "transformed_1080p24", "retimed_1080p24",
               "transition_1080p24", "fusion_1080p24"),
              target="first"),
    Candidate("clip.set_property", "edit_patch",
              ("populated_1080p24", "linked_av_1080p24",
               "graded_1080p24", "transformed_1080p24", "retimed_1080p24",
               "transition_1080p24", "fusion_1080p24"),
              target="first"),
    Candidate("clip.insert", "edit_patch",
              ("empty_1080p24", "populated_1080p24",
               "linked_av_1080p24", "still_1080p24",
               "caption_cards_1080p24"),
              target="none"),
    Candidate("clip.trim", "edit_patch",
              ("populated_1080p24", "linked_av_1080p24",
               "graded_1080p24", "transformed_1080p24", "retimed_1080p24"),
              target="first"),
    Candidate("clip.move", "edit_patch",
              ("populated_1080p24", "linked_av_1080p24"),
              target="first"),
    Candidate("track.add", "edit_patch",
              ("empty_1080p24", "populated_1080p24"),
              target="none"),
    Candidate("broll.insert", "resolve_axi",
              ("linked_av_1080p24", "populated_1080p24"),
              target="none"),
    Candidate("clip.remove", "resolve_axi",
              ("populated_1080p24", "linked_av_1080p24"),
              target="first"),
    Candidate("caption.style", "resolve_axi",
              ("caption_cards_1080p24",),
              target="first"),
)


def plan(candidates: Sequence[Candidate] = CANDIDATES) -> tuple[tuple, ...]:
    """Every (candidate, configuration) pair the run walks."""
    pairs = []
    for candidate in candidates:
        for configuration_id in candidate.configurations:
            configuration = configurations.configuration_by_id(configuration_id)
            pairs.append((candidate, configuration))
    return tuple(pairs)


def measured_coverage(results: Sequence[Mapping]) -> dict:
    """The dimension values the runs MEASURED, by dimension.

    A dimension value enters the coverage because a run measured it. A
    run that did not measure a dimension - because it could not read the
    state - contributes nothing to it.
    """
    covered: dict[str, list[str]] = {}
    configuration_ids: list[str] = []
    for result in results or ():
        state = ((result.get("qualification_state") or {})
                 .get("state") or {})
        for field in dimensions.DIMENSIONS:
            value = state.get(field)
            if not isinstance(value, str) or not value:
                continue
            values = covered.setdefault(field, [])
            if value not in values:
                values.append(value)
        configuration_id = str(result.get("configuration") or "")
        if configuration_id and configuration_id not in configuration_ids:
            configuration_ids.append(configuration_id)
    return {"dimensions": covered, "configurations": configuration_ids}


def preserved(results: Sequence[Mapping]) -> tuple[bool, list[dict]]:
    """Whether every run changed nothing it was not asked to change.

    A run that made an unintended modification is a failed
    qualification, whatever its receipt says.
    """
    unintended: list[dict] = []
    for result in results or ():
        verdict = result.get("verdict") or {}
        for entry in verdict.get("unintended_modifications") or ():
            unintended.append({**entry,
                               "configuration": result.get("configuration")})
    return not unintended, unintended


def build_report(*, operation_id: str, backend: str, results: Sequence[Mapping],
                 evidence: str, environment: Mapping,
                 semantic_boundary: Mapping,
                 project_conditions: Sequence[str],
                 supported_media_types: Sequence[str],
                 expected_state: Mapping, actual_state: Mapping,
                 preservation_checks: Sequence[Mapping],
                 preservation_limits: Sequence[str],
                 visual_verification: Sequence[str],
                 audio_verification: Sequence[str],
                 recovery_status: str) -> dict:
    """The evidence report the registry imports.

    The report is REFUSED by the importer when the coverage does not
    name every dimension, so a run that measured nothing cannot be
    reported as a qualification.
    """
    coverage = measured_coverage(results)
    ok, unintended = preserved(results)
    if not ok:
        raise ValueError(
            f"{operation_id} on {backend} changed what it was not asked to "
            f"change: {unintended}")
    missing = [field for field in dimensions.DIMENSIONS
               if field not in coverage["dimensions"]]
    if missing:
        raise ValueError(
            f"{operation_id} on {backend} measured no value for: "
            + ", ".join(missing))
    return {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": operation_id,
        "backend": backend,
        "result": "passed",
        "evidence": evidence,
        "resolve_version": str(environment.get("resolve_version") or ""),
        "environment": dict(environment),
        "semantic_boundary": dict(semantic_boundary),
        "coverage": {
            "dimensions": coverage["dimensions"],
            "configurations": coverage["configurations"],
            "uncovered": configurations.uncovered_values(
                coverage["dimensions"]),
            "source": "measured",
        },
        "project_conditions": list(project_conditions),
        "supported_media_types": list(supported_media_types),
        "expected_state": dict(expected_state),
        "actual_state": dict(actual_state),
        "preservation_checks": [dict(check) for check in preservation_checks],
        "preservation_limits": list(preservation_limits),
        "visual_verification": list(visual_verification),
        "audio_verification": list(audio_verification),
        "recovery_status": recovery_status,
    }


def verdict(before: Mapping, after: Mapping,
            operations: Sequence[Mapping]) -> dict:
    """The comparison of a run's two snapshots."""
    return snapshots.compare(before, after, operations)
