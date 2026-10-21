"""B-04: the processing status every observation carries.

One enumeration, one place that knows where the status lives. A record
that was never analysed, one whose lane failed and one whose answer is
inconclusive are three different facts, and a reader that cannot tell
them apart reads a gap as an absence (plan B-04).

**Where the status lives.** The Project Model schema gives the status
two homes: ``evidence.processing_status`` on the native evidence
contract, and the free-form ``payload`` for a row that carries no
contract (an observation migrated before the contract existed). This
module owns both, so a caller never has to know which shape it holds:

- :func:`processing_status_of` reads the status from either shape.
- :func:`stamp_processing_status` writes it to the contract when the
  row has one, and to the payload when it does not.

**Where the value comes from.** The source-memory envelope records the
coverage status its producer actually measured
(``complete|sampled|inconclusive|not_yet_analysed|unavailable|failed|unknown``).
:func:`status_from_coverage_status` maps that measured value onto the
model's status vocabulary. Nothing here guesses: a row whose envelope
says ``failed`` is failed, and a row whose envelope says ``complete``
was analysed.

The vocabulary is the model's own
(``analyzed|not_analyzed|unavailable|failed|inconclusive``), the same
one the Rust schema pins for signals and evidence contracts.
"""
from __future__ import annotations

from typing import Any

ANALYZED = "analyzed"
NOT_ANALYZED = "not_analyzed"
UNAVAILABLE = "unavailable"
FAILED = "failed"
INCONCLUSIVE = "inconclusive"

PROCESSING_STATUSES = (ANALYZED, NOT_ANALYZED, UNAVAILABLE, FAILED,
                        INCONCLUSIVE)
"""The model's processing-status vocabulary, in the schema's order."""

#: Measured coverage status -> processing status. The source-memory
#: envelope is the only producer of the left-hand side, and it says
#: what the lane actually did.
COVERAGE_STATUS_TO_PROCESSING = {
    "complete": ANALYZED,
    "sampled": ANALYZED,
    "inferred": ANALYZED,
    "unknown": ANALYZED,
    "not_yet_analysed": NOT_ANALYZED,
    "unavailable": UNAVAILABLE,
    "failed": FAILED,
    "inconclusive": INCONCLUSIVE,
}


def status_from_coverage_status(coverage_status: Any) -> str:
    """The processing status a measured coverage status implies.

    A coverage status the envelope vocabulary does not carry is
    ``analyzed``: the row exists because a producer wrote it, and the
    envelope said nothing about a failure.
    """
    if not isinstance(coverage_status, str):
        return ANALYZED
    return COVERAGE_STATUS_TO_PROCESSING.get(
        coverage_status.strip().lower(), ANALYZED)


def _contract(observation: dict[str, Any]) -> dict[str, Any] | None:
    """The native evidence contract an observation carries, if any.

    Two shapes carry one: the schema's top-level ``evidence`` field and
    the migrated ``payload.evidence_contract``. This module reads both
    so a caller never has to know which shape the row has.
    """
    evidence = observation.get("evidence")
    if isinstance(evidence, dict):
        return evidence
    payload = observation.get("payload")
    if isinstance(payload, dict):
        contract = payload.get("evidence_contract")
        if isinstance(contract, dict):
            return contract
    return None


def processing_status_of(observation: Any) -> str | None:
    """The processing status one observation carries, or ``None``.

    The native contract is read first; a row without one is read from
    its payload. A row that carries neither has said nothing, and this
    returns ``None`` rather than a default: an absent status is not the
    same as an analysed one.
    """
    if not isinstance(observation, dict):
        return None
    contract = _contract(observation)
    if contract is not None:
        status = contract.get("processing_status")
        if isinstance(status, str) and status in PROCESSING_STATUSES:
            return status
    payload = observation.get("payload")
    if isinstance(payload, dict):
        status = payload.get("processing_status")
        if isinstance(status, str) and status in PROCESSING_STATUSES:
            return status
    return None


def stamp_processing_status(observation: dict[str, Any], status: str) -> None:
    """Write one processing status onto an observation, in place.

    The status goes on the native contract when the row carries one and
    into the payload when it does not, so every observation carries
    exactly one status and one reader finds it either way.
    """
    if not isinstance(observation, dict):
        raise TypeError("an observation is a mapping")
    if status not in PROCESSING_STATUSES:
        raise ValueError(
            f"unknown processing status {status!r}; it must be one of "
            f"{PROCESSING_STATUSES}")
    contract = _contract(observation)
    if contract is not None:
        contract["processing_status"] = status
        return
    payload = observation.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("an observation carries no payload to stamp")
    payload["processing_status"] = status


def lane_processing_statuses(model: dict[str, Any]) -> dict[str, set[str]]:
    """Every processing status each provenance lane's rows carry.

    A lane that failed says so here; a lane that never ran has no rows
    and no entry. The reader is the query layer, which must not report
    a failed lane as one that never ran.
    """
    statuses: dict[str, set[str]] = {}
    for row in model.get("observations") or []:
        if not isinstance(row, dict):
            continue
        status = processing_status_of(row)
        if status is None:
            continue
        provenance = str(row.get("provenance_id") or "")
        lane = provenance[len("prov-"):].rpartition("-")[2] if provenance.startswith("prov-") else ""
        if not lane:
            continue
        statuses.setdefault(lane, set()).add(status)
    return statuses
