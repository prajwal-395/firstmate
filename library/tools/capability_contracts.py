"""Capability-owned input and evidence declarations.

The first migration slice covers preflight analysis.  The compatibility
projection deliberately returns the existing executable Requirement objects:
their checks and refusal text remain the single implementation during
the ledger transition.

Evidence envelope
-----------------
Each declaration names the evidence that satisfies it: where it was
recorded (``provenance``), which clock its timestamps use
(``source_clock``), how certain the measurement is (``certainty``), which
capability produced it and where that producer's version is recorded
(``producer`` / ``producer_version``), and the Project Model revision it
stands on when one exists (``revision``).  CAP-04 decides the
revision envelope; until then ``revision`` names the slot without
claiming authority for it.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CERTAINTY_LEVELS = ("observed", "measured", "inferred", "interpreted")
"""The only vocabulary a declaration may use for how sure a measurement is."""


@dataclass(frozen=True)
class EvidenceSchema:
    """Envelope promised for evidence produced to satisfy a requirement."""

    provenance: str
    source_clock: str | None
    certainty: tuple[str, ...]
    producer: str
    producer_version: str
    revision: str | None


@dataclass(frozen=True)
class RequiresSpec:
    """A capability input, named independently of its legacy DAG node."""

    requirement_id: str
    evidence: EvidenceSchema


# The version slot for pipeline evidence: the step ledger records each
# completion under its capability id beside the code-identity fingerprint
# the run measured, so "which code made this" is read off the ledger
# rather than stamped into the declaration.
_PIPELINE_VERSION = "code_identity_fingerprint_in_step_ledger"
# The revision slot until CAP-04 wires it: the Project Model revision the
# evidence stands on, when the write went through the model store.
_PM_REVISION = "project_model_revision_when_available"

_SCAN_RECORD = EvidenceSchema(
    provenance="scan_recording_or_verified_external_input",
    source_clock=None,
    certainty=("observed",),
    producer="footage.scan",
    producer_version=_PIPELINE_VERSION,
    revision=_PM_REVISION,
)

_CATALOG_RECORD = EvidenceSchema(
    provenance="catalog_recording",
    source_clock=None,
    certainty=("measured", "inferred"),
    producer="footage.catalog",
    producer_version=_PIPELINE_VERSION,
    revision=_PM_REVISION,
)

_TEMPORAL_RECORD = EvidenceSchema(
    provenance="temporal_index_recording",
    source_clock="source_seconds",
    certainty=("measured",),
    producer="temporal.index",
    producer_version=_PIPELINE_VERSION,
    revision=_PM_REVISION,
)

_MACHINE_PROBE = EvidenceSchema(
    provenance="machine_probe_at_check_time",
    source_clock=None,
    certainty=("measured",),
    producer="machine_environment",
    producer_version="probe_result_not_versioned",
    revision=None,
)

# Keep the order identical to the existing node-keyed projection.  These are
# authored capability contracts; the adapter below only resolves execution.
ANALYSIS_REQUIRES: Mapping[str, tuple[RequiresSpec, ...]] = {
    "footage.scan": (),
    "footage.catalog": (RequiresSpec("state.catalog.raw_footage_files", _SCAN_RECORD),),
    "semantics.analyse": (
        RequiresSpec("state.semantic_analysis.raw_footage_files", _SCAN_RECORD),
    ),
    "temporal.index": (
        RequiresSpec("state.temporal_index.raw_footage_files", _SCAN_RECORD),
        RequiresSpec("state.temporal_index.clip_catalog", _CATALOG_RECORD),
    ),
    "prosody.analyse": (
        RequiresSpec("state.prosody_analysis.raw_footage_files", _SCAN_RECORD),
        RequiresSpec("state.prosody_analysis.temporal_event_indices", _TEMPORAL_RECORD),
        RequiresSpec("env.parselmouth", _MACHINE_PROBE),
        RequiresSpec("prosody.speech_regions", EvidenceSchema(
            provenance="temporal_index_recording", source_clock="source_seconds",
            certainty=("measured",), producer="temporal.index",
            producer_version=_PIPELINE_VERSION,
            revision=_PM_REVISION)),
    ),
    "ocr.extract": (
        RequiresSpec("state.ocr_extraction.raw_footage_files", _SCAN_RECORD),
    ),
    "objects.segment": (
        RequiresSpec("state.object_segmentation.clip_catalog", _CATALOG_RECORD),
    ),
}


def requirements_for(capability_id: str) -> tuple[RequiresSpec, ...] | None:
    """Return a declaration, or ``None`` when this capability is unmigrated."""
    return ANALYSIS_REQUIRES.get(capability_id)


def requirement_ids_for(capability_id: str) -> tuple[str, ...] | None:
    """The requirement names a migrated capability refuses without, by id.

    ``None`` when unmigrated; ``()`` when migrated with no inputs (scan).
    This is the DAG-free half of the contract: names only, no checker
    objects, no node lookup.
    """
    declared = ANALYSIS_REQUIRES.get(capability_id)
    if declared is None:
        return None
    return tuple(item.requirement_id for item in declared)


def project_to_legacy(declarations: tuple[RequiresSpec, ...]) -> tuple:
    """Resolve declarations to the existing requirement checker objects."""
    from library.tools import requirements

    by_name = {requirement.name: requirement
               for requirement in requirements.all_requirements()}
    projected = []
    for declaration in declarations:
        try:
            projected.append(by_name[declaration.requirement_id])
        except KeyError as exc:
            raise ValueError(
                f"capability requirement {declaration.requirement_id!r} "
                "has no compatibility requirement") from exc
    return tuple(projected)


def provides_for(produces: tuple[str, ...]) -> frozenset:
    """Requirement names satisfied by producing state keys, without the DAG.

    Any requirement whose ``produced_keys`` names one of the keys is
    provided, regardless of which node records it.  The node restriction
    stays in the compatibility projection (``dag_adapter``); ordering
    analysis capabilities needs only the key agreement, which is what
    makes the composer independent of node identity.
    """
    from library.tools import requirements

    produced = set(produces or ())
    out: set[str] = set()
    for requirement in requirements.all_requirements():
        for _node, key in requirement.produced_keys:
            if key in produced:
                out.add(requirement.name)
                break
    return frozenset(out)


def compose_ordered(capability_ids: Sequence[str],
                    rank: Mapping[str, int] | None = None) -> tuple:
    """Order migrated capabilities so each follows what it requires.

    Reads only capability-owned declarations (``requirement_ids_for``),
    each operation's declared ``produces`` keys, and the requirement
    registry's ``produced_keys``.  No DAG import, no node lookup.  Every
    id must be migrated; unmigrated ids raise ``KeyError`` so the caller
    falls back to the legacy composer rather than misordering silently.
    Ties keep ``rank`` order (roster order for analysis), then input order.
    """
    from library.tools import operations

    ids = tuple(capability_ids)
    missing = [cid for cid in ids if requirement_ids_for(cid) is None]
    if missing:
        raise KeyError(
            f"capability contracts declare no requirements for {missing!r}")
    needs = {cid: set(requirement_ids_for(cid) or ()) for cid in ids}
    provides = {cid: set(provides_for(operations.get(cid).produces))
                for cid in ids}
    after: dict[str, set] = {
        cid: {other for other in ids if other != cid
              and bool(provides[other] & needs[cid])}
        for cid in ids}
    if rank is None:
        rank = {cid: i for i, cid in enumerate(ids)}
    ordered: list[str] = []
    while len(ordered) < len(ids):
        ready = sorted((cid for cid in ids if cid not in ordered
                        and after[cid] <= set(ordered)),
                       key=lambda c: (rank.get(c, len(rank)), ids.index(c)))
        if not ready:
            raise ValueError(
                "the capability selection has a requirement cycle: "
                f"left unordered: {sorted(set(ids) - set(ordered))}")
        ordered.append(ready[0])
    return tuple(ordered)
