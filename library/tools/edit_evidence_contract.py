"""Q01: the evidence-grounded editing contract.

The punch list's Q01: "Require every proposed edit to reference source
evidence. Include event identity, source interval, precision, certainty,
and supporting observations. Distinguish what Ren measured from what Ren
inferred. Include alternative candidates when evidence is ambiguous.
Require explicit confidence thresholds for autonomous execution."
Acceptance: "Editorial decisions can be explained and traced to actual
footage."

What was wrong: a proposed edit carries ``evidence_ids`` - bare strings.
The revise path grounds them on ``revision-note:<id>`` (a human's words
about the timeline, not footage), and nothing requires the id to resolve
to an observation, to name the event cut on, to place it on a source
clock, to say how precise the placement is, or to say whether Ren
measured it or inferred it. An approval then compiles, and the request
executes, with the trace ending at a string.

What this is: the reference a proposed edit carries, and the audit that
reads it. An :class:`EvidenceReference` names the event cut on, the
source interval on the universal source reference
(``library/tools/source_reference.py``, PR 3116), the precision of that
interval, the epistemic class of the claim
(``library/tools/claim_provenance.py``, PR 3137: measured, inferred or
interpreted - never defaulted), the uncertainty the producer earned, and
the supporting observations. A portable record (PR 3163) stays the shape
observations travel in; this module never re-stores one, it only cites
observation ids and reads them back from the mapping the caller holds.
The instruction loop (PRs 3145, 3152) stays the account of what ran; the
``gather_evidence`` stage is where this audit belongs, and this module is
the check it runs - it decides nothing and invents no evidence.

The five required pieces, and what each refuses:

    event identity   the event cut on (a claim key the producers
                     declare, e.g. ``speaking_span``). An undeclared
                     kind stays ``unknown`` and is refused, never
                     defaulted to a measurement.
    source interval  an interval (or element) on the source's own
                     clock. An asset alone names the file, not a place
                     in it, and is refused.
    precision        ``tick`` (source ms clock), ``element`` (a frame or
                     sample index) or ``span`` (the event's extent,
                     boundary-approximate). A span places no cut on its
                     own: it proposes, and a person confirms the frame.
    certainty        measured, inferred or interpreted - the class the
                     claim's producer earned, promoted only by a
                     deterministic verifier. A detector's span filed as
                     measured is refused. An inference without an
                     uncertainty is a guess and is refused; a judgement
                     that names nobody who judged it is refused.
    observations     at least one supporting observation id, resolvable
                     in the mapping the audit is given.

Ambiguity is data, not a refusal: when more than one candidate event
overlaps the proposed interval, the reference lists the alternatives it
considered. An ambiguous reference never executes alone - the choice
between candidates belongs on the approval path, where the trace of
what was considered travels with it.

Autonomy is a gate with named bars, never a widening: the verdict is
``autonomous`` only when every reference is complete, nothing is
ambiguous, every inferred claim is at or under the contract's declared
uncertainty bar, no reference is boundary-approximate, no judgement
stands in for evidence, AND the request itself granted autonomy. A gate
that cannot see an approval never grants what an approval guards.

Reach it as ``python3 -m library.tools.edit_evidence_contract --help``;
``audit`` reads a decision document beside an observations file and
reports grounded/ungrounded with denominators.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.tools import claim_provenance, source_reference

#: Version of this contract. Readers refuse records stamped newer.
EDIT_EVIDENCE_CONTRACT_VERSION = 1

#: The temporal precision of a reference. ``tick`` is an interval on
#: the source's millisecond clock; ``element`` is one frame or sample
#: index on that clock; ``span`` is the event's own extent -
#: boundary-approximate, proposing rather than placing.
PRECISION_TICK = "tick"
PRECISION_ELEMENT = "element"
PRECISION_SPAN = "span"

PRECISIONS = (PRECISION_TICK, PRECISION_ELEMENT, PRECISION_SPAN)

#: The source-reference kinds each precision is carried on. A tick or a
#: span is an interval on a clock; an element is one frame or sample.
PRECISION_REFERENCE_KINDS = {
    PRECISION_TICK: (source_reference.INTERVAL,),
    PRECISION_SPAN: (source_reference.INTERVAL,),
    PRECISION_ELEMENT: (source_reference.ELEMENT,),
}

#: The certainties a reference may carry. ``unknown`` is not one of
#: them: it is the absence of a class, and it is refused, never filed.
CERTAINTIES = ("measured", "inferred", "interpreted")

#: The uncertainty bar for autonomous execution, declared by this
#: contract version (v1). An inferred claim less certain than this never
#: executes alone: the bar is explicit and reviewable, a project
#: tightens it through ``thresholds=``, and moving the default is a
#: contract-version bump with a migration, not a quiet edit.
AUTONOMOUS_MAX_UNCERTAINTY = 0.2

AUTONOMOUS = "autonomous"
NEEDS_APPROVAL = "needs_approval"
REFUSED = "refused"

AUTONOMY_VERDICTS = (AUTONOMOUS, NEEDS_APPROVAL, REFUSED)


class EditEvidenceContractError(ValueError):
    """An evidence reference that cannot be what it claims to be."""


# ── The reference ────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceReference:
    """One proposed edit's claim on actual footage.

    ``event_id`` is the identity of the event cut on; ``event_kind`` is
    the claim key its producer declared (``speaking_span``,
    ``scene_boundary``, ...). ``source`` is the universal source
    reference placing it on the source's own clock. ``precision`` says
    what the interval is precise to. ``certainty`` is the class the
    claim earned; ``uncertainty`` is the producer's own uncertainty,
    required exactly when the claim is inferred. ``verification`` is
    the deterministic verifier's record when one promoted the claim;
    ``authorized_by`` names whose judgement an interpreted claim is.
    ``supporting_observation_ids`` are the observations backing it;
    ``alternatives`` are the other candidate events considered when the
    evidence was ambiguous.
    """

    event_id: str
    event_kind: str
    source: dict = field(default_factory=dict)
    precision: str = PRECISION_SPAN
    certainty: str = "inferred"
    uncertainty: float | None = None
    supporting_observation_ids: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    verification: dict | None = None
    authorized_by: str = ""
    version: int = EDIT_EVIDENCE_CONTRACT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "event_id": self.event_id,
            "event_kind": self.event_kind,
            "source": dict(self.source),
            "precision": self.precision,
            "certainty": self.certainty,
            "uncertainty": self.uncertainty,
            "supporting_observation_ids": list(self.supporting_observation_ids),
            "alternatives": list(self.alternatives),
            "verification": (
                None if self.verification is None else dict(self.verification)
            ),
            "authorized_by": self.authorized_by,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceReference:
        """Read a reference back, refusing what the constructor refuses."""
        if not isinstance(data, Mapping):
            raise EditEvidenceContractError("an evidence reference must be a mapping")
        return new_reference(
            event_id=str(data.get("event_id") or ""),
            event_kind=str(data.get("event_kind") or ""),
            source=dict(data.get("source") or {}),
            precision=str(data.get("precision") or PRECISION_SPAN),
            certainty=str(data.get("certainty") or "inferred"),
            uncertainty=data.get("uncertainty"),
            supporting_observation_ids=tuple(
                str(row) for row in (data.get("supporting_observation_ids") or ())
            ),
            alternatives=tuple(str(row) for row in (data.get("alternatives") or ())),
            verification=(
                None if data.get("verification") is None else dict(data["verification"])
            ),
            authorized_by=str(data.get("authorized_by") or ""),
            version=int(data.get("version") or EDIT_EVIDENCE_CONTRACT_VERSION),
        )

    @property
    def filed_certainty(self) -> str:
        """The class this reference is filed under.

        The class its producer earned, promoted only by a deterministic
        verifier. A model's confirmation of a model is recorded and
        does NOT promote: a model agreeing with a model is still a
        model (``claim_provenance.file_class``).
        """
        declared = claim_provenance.declaration(self.event_kind)
        declared_class = (
            declared["class"] if declared else claim_provenance.UNKNOWN_CLASS
        )
        return claim_provenance.file_class(declared_class, self.verification)

    @property
    def is_measured(self) -> bool:
        """Whether the reference rests on a reading, not a description."""
        return self.filed_certainty == "measured"


def new_reference(
    *,
    event_id: str,
    event_kind: str,
    source: Mapping[str, Any],
    precision: str = PRECISION_SPAN,
    certainty: str = "inferred",
    uncertainty: float | None = None,
    supporting_observation_ids: Sequence[str] = (),
    alternatives: Sequence[str] = (),
    verification: Mapping[str, Any] | None = None,
    authorized_by: str = "",
    version: int = EDIT_EVIDENCE_CONTRACT_VERSION,
) -> EvidenceReference:
    """Build a reference, refusing what cannot be what it claims.

    The caller states what only the proposer can know - which event,
    what precision, how uncertain. The contract checks everything
    checkable: the kind is declared, the source parses and names a
    place in the source, the precision rides the right reference kind,
    the certainty is the class the claim earned, and an inference
    carries its uncertainty.
    """
    if version > EDIT_EVIDENCE_CONTRACT_VERSION:
        raise EditEvidenceContractError(
            f"reference stamped v{version}, this reader knows "
            f"v{EDIT_EVIDENCE_CONTRACT_VERSION}"
        )
    if not event_id:
        raise EditEvidenceContractError("an evidence reference names the event cut on")
    declared = claim_provenance.declaration(event_kind)
    if declared is None:
        raise EditEvidenceContractError(
            f"event kind {event_kind!r} is declared by no producer: an "
            "unknown claim is refused, never defaulted"
        )
    if precision not in PRECISIONS:
        raise EditEvidenceContractError(
            f"precision {precision!r} is not one of {list(PRECISIONS)}"
        )
    try:
        parsed = source_reference.parse(source)
    except source_reference.SourceReferenceError as exc:
        raise EditEvidenceContractError(
            f"the source interval is not a source reference: {exc}"
        ) from exc
    if not source_reference.names_source(parsed):
        raise EditEvidenceContractError(
            "the source reference names no place in the source: a file "
            "is not a place in it"
        )
    allowed = PRECISION_REFERENCE_KINDS[precision]
    if parsed.get("kind") not in allowed:
        raise EditEvidenceContractError(
            f"precision {precision!r} rides a reference of kind "
            f"{parsed.get('kind')!r}, not one of {list(allowed)}"
        )
    filed = claim_provenance.file_class(declared["class"], verification)
    if certainty != filed:
        raise EditEvidenceContractError(
            f"certainty {certainty!r} conflicts with the class the claim "
            f"earned ({filed!r}): a {declared['class']} claim filed as "
            f"{certainty} says what nobody measured"
        )
    if certainty not in CERTAINTIES:
        raise EditEvidenceContractError(
            f"certainty {certainty!r} is not one of {list(CERTAINTIES)}: "
            "an unknown claim is refused, never filed"
        )
    if filed == "inferred":
        if (
            uncertainty is None
            or isinstance(uncertainty, bool)
            or not isinstance(uncertainty, (int, float))
            or not 0.0 <= float(uncertainty) <= 1.0
        ):
            raise EditEvidenceContractError(
                f"an inferred {event_kind} without an uncertainty in 0..1 "
                "is a guess, not evidence"
            )
    elif uncertainty is not None:
        raise EditEvidenceContractError(
            f"a {filed} claim carries no numeric uncertainty: the number "
            "would read as measured when nothing measured it"
        )
    if filed == "interpreted" and not authorized_by.strip():
        raise EditEvidenceContractError(
            "an interpreted claim names whose judgement it is: a judgement "
            "that names nobody authorises nothing"
        )
    if not supporting_observation_ids:
        raise EditEvidenceContractError(
            "an evidence reference with no supporting observations points at nothing"
        )
    listed = [str(row) for row in alternatives]
    if event_id in listed:
        raise EditEvidenceContractError("the chosen event is not its own alternative")
    return EvidenceReference(
        event_id=str(event_id),
        event_kind=str(event_kind),
        source=parsed,
        precision=precision,
        certainty=certainty,
        uncertainty=(None if uncertainty is None else float(uncertainty)),
        supporting_observation_ids=tuple(
            str(row) for row in supporting_observation_ids
        ),
        alternatives=tuple(listed),
        verification=(None if verification is None else dict(verification)),
        authorized_by=str(authorized_by),
        version=version,
    )


def reference_from_observation(
    observation: Mapping[str, Any],
    *,
    event_kind: str,
    source: Mapping[str, Any],
    precision: str = PRECISION_SPAN,
    uncertainty: float | None = None,
    verification: Mapping[str, Any] | None = None,
    authorized_by: str = "",
    alternatives: Sequence[str] = (),
) -> EvidenceReference:
    """Ground one stored observation as the reference a proposal cites.

    The observation supplies the identity (its id) and the backing (its
    own id in the supporting set); the caller still states what only
    the proposer can know - which event kind the observation is read as,
    which source interval it is read at, what precision that interval
    carries, and how uncertain the reading is. The convenience is
    spelling, not leniency: every refusal in :func:`new_reference`
    still applies.
    """
    if not isinstance(observation, Mapping) or not observation.get("id"):
        raise EditEvidenceContractError(
            "grounding reads a stored observation, and this names none"
        )
    return new_reference(
        event_id=str(observation["id"]),
        event_kind=event_kind,
        source=source,
        precision=precision,
        certainty=_filed_for(event_kind, verification),
        uncertainty=uncertainty,
        supporting_observation_ids=(str(observation["id"]),),
        alternatives=alternatives,
        verification=verification,
        authorized_by=authorized_by,
    )


def _filed_for(event_kind: str, verification: Mapping[str, Any] | None) -> str:
    declared = claim_provenance.declaration(event_kind)
    declared_class = declared["class"] if declared else claim_provenance.UNKNOWN_CLASS
    return claim_provenance.file_class(declared_class, verification)


# ── The audit ────────────────────────────────────────────────────────


def _ref_interval_ticks(source: Mapping[str, Any]) -> tuple[int, int] | None:
    """The tick span of an interval-kind source, or None."""
    if source.get("kind") != source_reference.INTERVAL:
        return None
    interval = source.get("interval") or {}
    start, end = interval.get("start"), interval.get("end")
    if (
        isinstance(start, int)
        and isinstance(end, int)
        and not isinstance(start, bool)
        and end > start
    ):
        return start, end
    return None


def audit_reference(
    reference: EvidenceReference | Mapping[str, Any],
    observations: Mapping[str, Any] | None = None,
    candidate_events: Mapping[str, Any] | None = None,
) -> list[str]:
    """Every defect in one reference, as problems. Empty is complete.

    ``observations`` maps observation id to the stored observation; a
    supporting id that resolves to nothing is a pointer at nothing.
    ``candidate_events`` maps event id to a ``{"start_tick",
    "end_tick"}`` span on the same clock; a candidate overlapping the
    reference's interval that the reference does not list under
    alternatives is ambiguity the proposal never owned up to.
    """
    if isinstance(reference, Mapping) and not isinstance(reference, EvidenceReference):
        try:
            reference = EvidenceReference.from_dict(reference)
        except EditEvidenceContractError as exc:
            return [str(exc)]
    if not isinstance(reference, EvidenceReference):
        return ["an evidence reference must be a mapping"]
    problems: list[str] = []
    label = reference.event_id or "<no event_id>"
    if reference.version > EDIT_EVIDENCE_CONTRACT_VERSION:
        return [
            (
                f"reference {label}: stamped "
                f"v{reference.version}, this reader knows "
                f"v{EDIT_EVIDENCE_CONTRACT_VERSION}"
            )
        ]
    declared = claim_provenance.declaration(reference.event_kind)
    if declared is None:
        problems.append(
            f"reference {label}: event kind {reference.event_kind!r} is "
            "declared by no producer"
        )
    filed = reference.filed_certainty
    if filed == claim_provenance.UNKNOWN_CLASS:
        problems.append(
            f"reference {label}: event kind {reference.event_kind!r} "
            "carries no class: an unknown claim is refused, never filed"
        )
    elif reference.certainty != filed:
        problems.append(
            f"reference {label}: certainty {reference.certainty!r} "
            f"conflicts with the class the claim earned ({filed!r})"
        )
    if reference.precision not in PRECISIONS:
        problems.append(
            f"reference {label}: precision {reference.precision!r} is not "
            f"one of {list(PRECISIONS)}"
        )
    else:
        allowed = PRECISION_REFERENCE_KINDS[reference.precision]
        if reference.source.get("kind") not in allowed:
            problems.append(
                f"reference {label}: precision {reference.precision!r} "
                f"rides a {reference.source.get('kind')!r} reference"
            )
    try:
        parsed = source_reference.parse(reference.source)
    except source_reference.SourceReferenceError as exc:
        problems.append(f"reference {label}: not a source reference: {exc}")
        parsed = {}
    if parsed and not source_reference.names_source(parsed):
        problems.append(f"reference {label}: the source names no place in the source")
    if not reference.supporting_observation_ids:
        problems.append(f"reference {label}: no supporting observations")
    elif observations is not None:
        for observation_id in reference.supporting_observation_ids:
            if observation_id not in observations:
                problems.append(
                    f"reference {label}: supporting observation "
                    f"{observation_id!r} resolves to nothing stored"
                )
    if reference.event_id in reference.alternatives:
        problems.append(
            f"reference {label}: the chosen event lists itself as an alternative"
        )
    if candidate_events is not None:
        span = _ref_interval_ticks(reference.source)
        if span is not None:
            start, end = span
            unlisted = sorted(
                event_id
                for event_id, candidate in candidate_events.items()
                if event_id != reference.event_id
                and isinstance(candidate, Mapping)
                and _overlaps(start, end, candidate)
                and event_id not in reference.alternatives
            )
            if unlisted:
                problems.append(
                    f"reference {label}: ambiguous evidence - candidate "
                    f"event(s) {unlisted} overlap the proposed interval "
                    "and are not listed as alternatives"
                )
    return problems


def _overlaps(start: int, end: int, candidate: Mapping[str, Any]) -> bool:
    other_start, other_end = candidate.get("start_tick"), candidate.get("end_tick")
    if (
        not isinstance(other_start, int)
        or not isinstance(other_end, int)
        or isinstance(other_start, bool)
    ):
        return False
    return int(other_start) < end and int(other_end) > start


def _decision_evidence_ids(decision: Any) -> list[str]:
    if hasattr(decision, "evidence_ids"):
        return [str(row) for row in (decision.evidence_ids or ())]
    if isinstance(decision, Mapping):
        return [str(row) for row in (decision.get("evidence_ids") or ())]
    raise EditEvidenceContractError("audit_decision needs a decision with evidence_ids")


def _decision_id(decision: Any) -> str:
    if hasattr(decision, "decision_id"):
        return str(decision.decision_id or "")
    if isinstance(decision, Mapping):
        return str(decision.get("decision_id") or "")
    return ""


def audit_decision(
    decision: Any,
    observations: Mapping[str, Any] | None,
    references: Sequence[EvidenceReference | Mapping[str, Any]] = (),
    candidate_events: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """How one proposed edit is grounded, with denominators.

    Every id in the decision's ``evidence_ids`` must be covered by a
    COMPLETE reference whose supporting set carries it; a bare string
    that no reference cites (``revision-note:<id>``, a prose label) is
    reported ungrounded, not read as evidence. The decision is
    ``grounded`` only when it names evidence and every id it names is
    covered by a complete reference.
    """
    evidence_ids = _decision_evidence_ids(decision)
    decision_id = _decision_id(decision) or "<no decision_id>"
    rows: list[dict[str, Any]] = []
    clean_by_support: dict[str, list[str]] = {}
    measured = inferred = interpreted = ambiguous = 0
    for reference in references:
        if isinstance(reference, Mapping) and not isinstance(
            reference, EvidenceReference
        ):
            try:
                reference = EvidenceReference.from_dict(reference)
            except EditEvidenceContractError as exc:
                rows.append(
                    {
                        "event_id": str(
                            reference.get("event_id")
                            if isinstance(reference, Mapping)
                            else ""
                        ),
                        "problems": [str(exc)],
                        "complete": False,
                    }
                )
                continue
        problems = audit_reference(reference, observations, candidate_events)
        complete = not problems
        if reference.alternatives:
            ambiguous += 1
        if complete:
            if reference.filed_certainty == "measured":
                measured += 1
            elif reference.filed_certainty == "inferred":
                inferred += 1
            else:
                interpreted += 1
            for observation_id in reference.supporting_observation_ids:
                clean_by_support.setdefault(observation_id, []).append(
                    reference.event_id
                )
        rows.append(
            {
                "event_id": reference.event_id,
                "event_kind": reference.event_kind,
                "certainty": reference.certainty,
                "complete": complete,
                "problems": problems,
                "alternatives": list(reference.alternatives),
            }
        )
    grounded = sorted(
        {evidence_id for evidence_id in evidence_ids if evidence_id in clean_by_support}
    )
    ungrounded = sorted(set(evidence_ids) - set(grounded))
    return {
        "decision_id": decision_id,
        "evidence_ids": list(evidence_ids),
        "references": rows,
        "grounded_ids": grounded,
        "ungrounded_ids": ungrounded,
        "grounded": bool(evidence_ids) and not ungrounded,
        "measured_references": measured,
        "inferred_references": inferred,
        "interpreted_references": interpreted,
        "ambiguous_references": ambiguous,
    }


def audit_graph(
    decisions: Sequence[Any] | Any,
    observations: Mapping[str, Any] | None,
    references_by_decision: Mapping[
        str, Sequence[EvidenceReference | Mapping[str, Any]]
    ]
    | None = None,
    candidate_events: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The grounding of a set of proposed edits, with denominators.

    ``decisions`` is a sequence of decisions (or a DecisionGraph); the
    report counts proposed edits, edits naming any evidence, and edits
    every one of whose evidence ids a complete reference covers -
    before vs after on the same recorded plans is the Q01 measurement.
    """
    from library.tools import editorial_decisions as _decisions

    if isinstance(decisions, _decisions.DecisionGraph):
        rows = list(decisions.decisions)
    else:
        rows = list(decisions)
    references_by_decision = references_by_decision or {}
    per_decision = [
        audit_decision(
            decision,
            observations,
            references_by_decision.get(_decision_id(decision), ()),
            candidate_events,
        )
        for decision in rows
    ]
    return {
        "format": "ren.edit-evidence-contract-audit/1",
        "contract_version": EDIT_EVIDENCE_CONTRACT_VERSION,
        "proposed_edits": len(per_decision),
        "edits_naming_evidence": sum(1 for row in per_decision if row["evidence_ids"]),
        "grounded_edits": sum(1 for row in per_decision if row["grounded"]),
        "complete_references": sum(
            1
            for row in per_decision
            for reference in row["references"]
            if reference["complete"]
        ),
        "incomplete_references": sum(
            1
            for row in per_decision
            for reference in row["references"]
            if not reference["complete"]
        ),
        "measured_references": sum(row["measured_references"] for row in per_decision),
        "inferred_references": sum(row["inferred_references"] for row in per_decision),
        "interpreted_references": sum(
            row["interpreted_references"] for row in per_decision
        ),
        "ambiguous_references": sum(
            row["ambiguous_references"] for row in per_decision
        ),
        "decisions": per_decision,
    }


# ── The autonomy gate ────────────────────────────────────────────────


def gate_autonomy(
    references: Sequence[EvidenceReference | Mapping[str, Any]],
    *,
    autonomy: str = "needs_approval",
    max_uncertainty: float = AUTONOMOUS_MAX_UNCERTAINTY,
    observations: Mapping[str, Any] | None = None,
    candidate_events: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Whether the grounded proposal may execute without a person.

    The gate never widens what the request granted: anything but an
    explicitly autonomous request caps the verdict at needs_approval,
    and the verdict is autonomous only when every reference is
    complete, every inferred claim is at or under the uncertainty bar,
    nothing is boundary-approximate, nothing is ambiguous, and no
    judgement stands in for evidence. The first failing reason is not
    the only one reported: every reason is listed, so one fix does not
    reveal the next refusal.
    """
    from library.tools import editorial_intent as _intent

    reasons: list[str] = []
    refs: list[EvidenceReference] = []
    if not list(references):
        reasons.append(
            "refused: no evidence references - a proposal "
            "with nothing traceable to footage executes never"
        )
    for reference in references:
        if isinstance(reference, Mapping) and not isinstance(
            reference, EvidenceReference
        ):
            try:
                reference = EvidenceReference.from_dict(reference)
            except EditEvidenceContractError as exc:
                reasons.append(f"refused: {exc}")
                continue
        refs.append(reference)
        problems = audit_reference(reference, observations, candidate_events)
        reasons.extend(f"refused: {problem}" for problem in problems)
    if autonomy not in _intent.AUTONOMY:
        reasons.append(
            f"refused: autonomy {autonomy!r} is not one of {list(_intent.AUTONOMY)}"
        )
    if autonomy != _intent.AUTONOMOUS:
        reasons.append(
            f"needs_approval: the request granted {autonomy!r}, "
            "not autonomous execution"
        )
    for reference in refs:
        label = reference.event_id or "<no event_id>"
        if reference.filed_certainty == "interpreted":
            reasons.append(
                f"needs_approval: {label} is a judgement by "
                f"{reference.authorized_by or 'nobody named'}: a person "
                "confirms a judgement, a machine does not execute one"
            )
        elif (
            reference.filed_certainty == "inferred"
            and reference.uncertainty is not None
            and reference.uncertainty > max_uncertainty
        ):
            reasons.append(
                f"needs_approval: {label} uncertainty "
                f"{reference.uncertainty} is over the autonomous bar "
                f"{max_uncertainty}"
            )
        if reference.alternatives:
            reasons.append(
                f"needs_approval: {label} lists alternatives "
                f"{list(reference.alternatives)}: the choice between "
                "candidates belongs to a person"
            )
        if reference.precision == PRECISION_SPAN:
            reasons.append(
                f"needs_approval: {label} is boundary-approximate "
                f"(precision {PRECISION_SPAN!r}): a person confirms the "
                "frame a cut lands on"
            )
    refused = any(reason.startswith("refused:") for reason in reasons)
    needs_person = any(reason.startswith("needs_approval:") for reason in reasons)
    verdict = REFUSED if refused else NEEDS_APPROVAL if needs_person else AUTONOMOUS
    return {
        "verdict": verdict,
        "reasons": reasons,
        "max_uncertainty": max_uncertainty,
        "references": len(refs),
    }


# ── The explanation ──────────────────────────────────────────────────


def explain(
    decision_id: str,
    references: Sequence[EvidenceReference | Mapping[str, Any]],
    observations: Mapping[str, Any] | None = None,
) -> list[str]:
    """The proposed edit in the footage's own words, one line per claim.

    The acceptance half of Q01: an editorial decision explained as the
    events, intervals and certainties behind it, so a reader can pull
    the frames and check. An incomplete reference explains itself as
    incomplete rather than being skipped.
    """
    lines: list[str] = []
    for reference in references:
        if isinstance(reference, Mapping) and not isinstance(
            reference, EvidenceReference
        ):
            try:
                reference = EvidenceReference.from_dict(reference)
            except EditEvidenceContractError as exc:
                lines.append(f"{decision_id}: no traceable claim ({exc})")
                continue
        source = reference.source
        where = _where(source)
        claim = (
            f"{reference.event_kind} {reference.event_id} "
            f"[{reference.certainty}"
            + (
                f", uncertainty {reference.uncertainty}"
                if reference.certainty == "inferred"
                else ""
            )
            + f", {reference.precision} precision] at {where}"
        )
        if reference.precision == PRECISION_SPAN:
            claim += " (boundary-approximate)"
        problems = audit_reference(reference, observations)
        if problems:
            claim += f" - INCOMPLETE: {'; '.join(problems)}"
        if reference.alternatives:
            claim += f" - alternatives considered: {', '.join(reference.alternatives)}"
        lines.append(f"{decision_id}: {claim}")
    if not lines:
        lines.append(
            f"{decision_id}: no evidence references - nothing traceable to footage"
        )
    return lines


def _where(source: Mapping[str, Any]) -> str:
    kind = source.get("kind")
    if kind == source_reference.INTERVAL:
        interval = source.get("interval") or {}
        start = interval.get("start")
        end = interval.get("end")
        seconds = ""
        if isinstance(start, int) and isinstance(end, int):
            rate = source_reference.SOURCE_TICKS_PER_SECOND
            seconds = f" ({start / rate:.3f}-{end / rate:.3f}s)"
        return (
            f"{source.get('stream_id')} ticks {start}-{end} on "
            f"{interval.get('clock_id')}{seconds}"
        )
    if kind == source_reference.ELEMENT:
        element = source.get("element") or {}
        index = element.get("frame_index", element.get("sample_index"))
        return (
            f"{source.get('stream_id')} {element.get('kind')} "
            f"{index} on {source.get('clock_id')}"
        )
    return f"{kind} {dict(source)}"


# ── The CLI ──────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.edit_evidence_contract",
        description="audit proposed edits against the evidence-grounded "
        "editing contract (Q01)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("audit", help="audit a decision document")
    check.add_argument(
        "decisions",
        help="decision document (JSON: a list of decisions or a DecisionGraph)",
    )
    check.add_argument(
        "--observations", default="", help="stored observations (JSON object by id)"
    )
    check.add_argument(
        "--references",
        default="",
        help="evidence references (JSON object by decision_id)",
    )
    check.add_argument(
        "--candidates", default="", help="candidate events (JSON object by event id)"
    )
    args = parser.parse_args(argv)

    document = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
    if isinstance(document, Mapping) and "decisions" in document:
        from library.tools import editorial_decisions as _decisions

        decisions = _decisions.DecisionGraph.from_dict(document).decisions
    elif isinstance(document, list):
        decisions = document
    else:
        print(
            "the decision document is a list of decisions or a DecisionGraph",
            file=sys.stderr,
        )
        return 2
    observations = (
        json.loads(Path(args.observations).read_text(encoding="utf-8"))
        if args.observations
        else None
    )
    references = (
        json.loads(Path(args.references).read_text(encoding="utf-8"))
        if args.references
        else {}
    )
    candidates = (
        json.loads(Path(args.candidates).read_text(encoding="utf-8"))
        if args.candidates
        else None
    )
    report = audit_graph(decisions, observations, references, candidates)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["grounded_edits"] != report["proposed_edits"] else 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
