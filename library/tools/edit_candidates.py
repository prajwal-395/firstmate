"""Grounded candidate edits with verified predicates (EDIT-013..018).

EDIT-013 extends the existing evidence-to-edit integration instead of
replacing it: this module CONSUMES `evidence_to_edit.ground_cuts` output
(cuts plus their evidence rows) and the other EDIT measurements
(phrase guards, action phases, shot rejects, continuity), and represents
each as a first-class candidate. EDIT-014 makes retrieval results
directly usable by edit-point planning via `candidates_from_retrieval`.
EDIT-015 represents a candidate edit with its supporting evidence.
EDIT-016 requires the candidate's predicates to verify before it counts
as fully grounded. EDIT-017 grades strong candidates apart from
uncertain ones. EDIT-018 plans alternatives per creative policy.

A policy here is a NAMED ordering the caller chooses
(`longest_verified`, `earliest`, `latest`); the default preserves the
incumbent `ground_cuts` behavior (longest surviving span first) and is
documented as such, not as taste. Strength is mechanical: every
predicate verified, coherence ok, no vetoes - otherwise uncertain with
the reasons listed.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence

from library.tools import edit_precision

# Candidate policies: named orderings, caller-chosen. `longest_verified`
# is the incumbent ground_cuts behavior (longest surviving span first).
CANDIDATE_POLICIES = ("longest_verified", "earliest", "latest")


def candidates_from_retrieval(
        hits: Sequence[Mapping[str, Any]],
        *, precision: str = edit_precision.PRECISION_BROWSE
        ) -> list[dict[str, Any]]:
    """Moment skeletons directly usable by edit-point planning (EDIT-014).

    A retrieval `hit` carries `source_id`, `start_seconds`,
    `end_seconds` and its retrieval `rank`. The skeleton keeps the rank
    as retrieval order only: a rank is not a quality score and is never
    presented as one. Hits with empty or inverted ranges are dropped
    with the drop REPORTED in the returned `dropped` list, never
    silently.

    `precision` is the task the moments are wanted for
    (`edit_precision.PRECISIONS`; default `browse`, which places every
    measurable range exactly as before). At `cut` a frame-approximate
    observation is REFUSED - dropped with the reason - so Ren never
    places a frame-approximate observation on the timeline as though
    it were an exact event boundary (F02). A speech hit whose matching
    words carry their own timings is frame-exact and still places; a
    vision window, a chunk, a widened passage or any other row without
    a measured boundary does not.
    """
    edit_precision.requirement_for(precision)
    moments: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for position, hit in enumerate(hits):
        try:
            start = float(hit["start_seconds"])
            end = float(hit["end_seconds"])
            source_id = str(hit["source_id"])
        except (KeyError, TypeError, ValueError):
            dropped.append({"hit": dict(hit),
                            "reason": "missing source_id or range"})
            continue
        if not end > start or not source_id:
            dropped.append({"hit": dict(hit),
                            "reason": "empty range or source"})
            continue
        if precision == edit_precision.PRECISION_CUT:
            verdict = edit_precision.check_boundary_for_cut(
                temporal_precision=edit_precision.classify_hit(
                    hit)["temporal_precision"],
                boundary_status=hit.get("boundary_status"),
                boundary_precision_seconds=hit.get(
                    "boundary_precision_seconds"),
                tolerance_seconds=hit.get("tolerance_seconds"))
            if verdict["status"] == "refused":
                dropped.append({"hit": dict(hit),
                                "reason": "; ".join(verdict["reasons"])})
                continue
        moments.append({"moment_id": f"retrieval-{position}",
                        "source_id": source_id,
                        "start_seconds": start, "end_seconds": end,
                        "retrieval_rank": int(hit.get("rank", position))})
    return [{"moments": moments, "dropped": dropped,
             "precision": precision}]


def build_candidate(
        moment_id: str,
        start_seconds: float,
        end_seconds: float,
        evidence_rows: Sequence[Mapping[str, Any]],
        predicates: Sequence[Mapping[str, Any]],
        coherence: Mapping[str, Any] | None = None,
        vetoes: Sequence[str] = (),
        policy: str = "longest_verified") -> dict[str, Any]:
    """Represent one candidate edit with its supporting evidence (EDIT-015).

    `evidence_rows` are the measurement rows grounding the span (each
    with `evidence_id`); `predicates` are `{predicate, evidence_names[]}`
    declarations verified by `verify_predicates`. `coherence` is a
    `guard_coherent_span` verdict when speech coherence applies, else
    None (non-speech candidates carry no coherence claim). `vetoes` are
    placement vetoes from `cut_placement.refine_cut` and kin.
    """
    if policy not in CANDIDATE_POLICIES:
        raise KeyError(f"unknown candidate policy: {policy!r}")
    evidence_ids = sorted({str(row["evidence_id"]) for row in evidence_rows
                           if row.get("evidence_id")})
    candidate = {
        "candidate_id": f"{moment_id}:{start_seconds}-{end_seconds}",
        "moment_id": moment_id,
        "start_seconds": float(start_seconds),
        "end_seconds": float(end_seconds),
        "evidence_ids": evidence_ids,
        "measurement_count": len(evidence_rows),
        "predicates": verify_predicates(evidence_rows, predicates),
        "coherence": dict(coherence) if coherence else None,
        "vetoes": list(vetoes),
        "policy": policy,
    }
    grade = grade_strength(candidate)
    candidate["strength"] = grade["strength"]
    candidate["strength_reasons"] = grade["reasons"]
    return candidate


def verify_predicates(
        evidence_rows: Sequence[Mapping[str, Any]],
        predicates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Verify each predicate against the candidate's evidence (EDIT-016).

    A predicate names `evidence_names` (measurement/plan names that must
    appear in the rows) and optionally `minimum_rows`. It verifies only
    when every named evidence kind is present in sufficient rows; the
    verdict lists the basis evidence ids. No basis, no verification.
    """
    kinds: dict[str, list[str]] = {}
    for row in evidence_rows:
        kind = str(row.get("plan", row.get("measurement_type",
                                           row.get("kind", "unknown"))))
        kinds.setdefault(kind, [])
        if row.get("evidence_id"):
            kinds[kind].append(str(row["evidence_id"]))
    verified: list[dict[str, Any]] = []
    for predicate in predicates:
        names = [str(name)
                 for name in predicate.get("evidence_names", [])]
        minimum = int(predicate.get("minimum_rows", 1))
        missing = [name for name in names
                   if len(kinds.get(name, [])) < minimum]
        basis = sorted({evidence_id for name in names
                        for evidence_id in kinds.get(name, [])})
        if missing:
            verified.append({"predicate": predicate.get("predicate"),
                             "status": "failed",
                             "missing": missing, "basis": basis})
        else:
            verified.append({"predicate": predicate.get("predicate"),
                             "status": "verified",
                             "missing": [], "basis": basis})
    return verified


def grade_strength(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Grade a candidate strong or uncertain (EDIT-017).

    Strong requires ALL of: every predicate verified, at least one
    evidence id, coherence ok when a coherence verdict is attached, and
    no vetoes. Anything else is uncertain with each failing reason
    listed. Uncertainty is data for `alternative_plans`, not a refusal.
    """
    reasons: list[str] = []
    for verdict in candidate.get("predicates", []):
        if verdict.get("status") != "verified":
            reasons.append(f"predicate failed: {verdict.get('predicate')}")
    if not candidate.get("evidence_ids"):
        reasons.append("no supporting evidence ids")
    coherence = candidate.get("coherence")
    if coherence is not None and coherence.get("status") != "ok":
        reasons.append(f"coherence {coherence.get('status')}: "
                       f"{coherence.get('reason')}")
    for veto in candidate.get("vetoes", []):
        reasons.append(f"vetoed: {veto}")
    if reasons:
        return {"strength": "uncertain", "reasons": reasons}
    return {"strength": "strong", "reasons": []}


def alternative_plans(
        candidates: Sequence[Mapping[str, Any]],
        policies: Sequence[str] = CANDIDATE_POLICIES
        ) -> dict[str, list[str]]:
    """Alternative edit plans, one ordering per creative policy (EDIT-018).

    Each policy ranks the SAME candidates differently; strong candidates
    sort before uncertain ones within every policy, and uncertainty is
    never hidden by rank. Unknown policy names fail loudly.
    """
    plans: dict[str, list[str]] = {}
    for policy in policies:
        if policy == "longest_verified":
            key = lambda candidate: (
                candidate.get("strength") != "strong",
                -(float(candidate["end_seconds"])
                  - float(candidate["start_seconds"])),
                float(candidate["start_seconds"]))
        elif policy == "earliest":
            key = lambda candidate: (
                candidate.get("strength") != "strong",
                float(candidate["start_seconds"]))
        elif policy == "latest":
            key = lambda candidate: (
                candidate.get("strength") != "strong",
                -float(candidate["start_seconds"]))
        else:
            raise KeyError(f"unknown candidate policy: {policy!r}")
        ordered = sorted(candidates, key=key)
        plans[policy] = [str(candidate["candidate_id"])
                         for candidate in ordered]
    return plans
