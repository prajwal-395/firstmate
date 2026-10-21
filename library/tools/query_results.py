"""B-04: the model's home for an answered query.

Plan B-04 asks that a "confidently absent" answer have a home in the
model, and that the home carry the basis it rests on. The Project
Model already has that collection - ``query_results`` - and the
validator already refuses a confident absence without fresh, complete,
versioned coverage. What was missing was a producer: nothing wrote a
row.

This module is the bridge from an answer to a row:

- :func:`row_for_answer` maps an ``evidence_query`` answer onto the
  model's ``QueryResult`` shape (the Rust schema pins it: no extra
  keys, a four-state status, a completeness basis).
- :func:`record_answer` writes the row into a project's model.

**Fail-closed.** The five-state answer vocabulary is wider than the
model's four-state row vocabulary, so the mapping is a narrowing and
the narrowing is lossy on purpose:

- ``absent`` (a measured negative assertion) becomes
  ``confidently_absent`` - but only when every basis row is complete,
  fresh and versioned. When it is not, the row is filed
  ``inconclusive``: a confident absence may never be issued from
  partial coverage.
- ``not_observed`` (a complete lane that asserted nothing either way)
  becomes ``inconclusive``. The model has no row for it, and filing it
  as a confident absence would claim a negative assertion that nobody
  made.
- ``not_analyzed`` becomes ``not_yet_analysed``, ``present`` stays
  ``present`` and ``inconclusive`` stays ``inconclusive``.
"""
from __future__ import annotations

from typing import Any

try:
    from library.tools import evidence_query
except ImportError:  # imported as `tools.*` from inside library/
    from tools import evidence_query

ANSWER_STATE_TO_ROW_STATUS = {
    evidence_query.PRESENT: "present",
    evidence_query.ABSENT: "confidently_absent",
    evidence_query.NOT_OBSERVED: "inconclusive",
    evidence_query.NOT_ANALYZED: "not_yet_analysed",
    evidence_query.INCONCLUSIVE: "inconclusive",
}
"""The five-state answer, narrowed onto the model's four-state row."""

DEFAULT_LEVEL = "l0"
"""The level a basis row claims when neither the plan nor the record
names one. ``l0`` is the weakest claim the vocabulary allows: a
stronger level would be an invention, and the level is metadata about
the analysis, not about the answer."""


def row_for_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """The model's ``query_results`` row for one answered plan.

    The row carries the answer's status, the evidence ids that carried
    it, the lanes that are still missing, and the completeness basis.
    """
    if not isinstance(answer, dict):
        raise TypeError("an answer is a mapping")
    state = answer.get("answer_state")
    if state is None:
        state = evidence_query.PRESENT if answer.get("results") else (
            evidence_query.NOT_ANALYZED
            if answer.get("status") == evidence_query.NOT_YET_ANALYSED
            else evidence_query.INCONCLUSIVE)
    status = ANSWER_STATE_TO_ROW_STATUS.get(state, "inconclusive")
    basis = [_basis_row(row) for row in answer.get("completeness_basis") or []]
    if status == "confidently_absent" and not _absence_is_backed(basis):
        # A confident absence may never come from partial coverage: the
        # model refuses such a row, and so does this writer.
        status = "inconclusive"
    return {
        "status": status,
        "candidate_evidence_ids": sorted(
            {str(evidence_id)
             for evidence_id in answer.get("candidate_evidence_ids") or []}),
        "required_missing_lanes": sorted(
            {str(lane)
             for lane in answer.get("required_missing_lanes") or []}),
        "completeness_basis": basis,
    }


def _absence_is_backed(basis: list[dict[str, Any]]) -> bool:
    """Whether a confident absence rests on fresh, complete, versioned
    coverage - the same bar the Project Model validator applies."""
    if not basis:
        return False
    return all(row.get("coverage") == "complete"
               and row.get("freshness") == "fresh"
               and str(row.get("producer_version") or "").strip()
               for row in basis)


def _basis_row(row: dict[str, Any]) -> dict[str, Any]:
    """One basis row in the model's ``CompletenessBasis`` shape."""
    scope = row.get("scope") or {}
    model_scope: dict[str, Any] = {}
    window = scope.get("window_s")
    if (isinstance(window, list) and len(window) == 2
            and isinstance(window[0], (int, float))
            and isinstance(window[1], (int, float))
            and window[1] > window[0]):
        interval: dict[str, Any] = {
            "start": round(float(window[0]) * 1000),
            "end": round(float(window[1]) * 1000),
        }
        if isinstance(scope.get("clock_id"), str):
            interval["clock_id"] = scope["clock_id"]
        model_scope["interval"] = interval
    if isinstance(scope.get("target_entity_id"), str):
        model_scope["target_entity_id"] = scope["target_entity_id"]
    level = row.get("level")
    return {
        "lane": str(row.get("lane") or ""),
        "coverage": str(row.get("coverage") or "unavailable"),
        "freshness": str(row.get("freshness") or "unknown"),
        "scope": model_scope,
        "producer_version": str(row.get("producer_version") or ""),
        "level": level if level in ("l0", "l1", "l2") else DEFAULT_LEVEL,
    }
