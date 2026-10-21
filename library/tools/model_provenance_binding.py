"""Bind a lane's measurement to the licence record the reuse gate reads.

**The gap this closes.** A lane measures with a model and records WHAT
the model was (``sound_event_pipeline.METHOD`` names the PANNs
checkpoint that produced the events), and the registry records what that
model's licence permits. Nothing joined the two, so the row a reader
holds carried no licence record, and the cross-project reuse gate - which
fails closed on a row with no record - refused every model-derived row,
including the ones the engine had measured with a commercially reusable
model.

**The record lives in the project that holds the evidence.** The
Project Model store already has a place for generation records
(``model_evidence_provenance.jsonl``); a record written there says which
implementation produced which evidence, under which workload purpose,
and carries the registry's own verdict. This module writes that record
and stamps the rows it names.

**A row a record names carries the record.** The migration reads the
project's generation records and stamps the rows they name, so a model
rebuilt from the same measurements lands on the same rows. The stamp is
the record's own verdict, copied - the registry stays the authority.

**The lane names the model; the registry says what it may do.** The
implementation id comes from the lane's own module, never from a guess
about which model made a row. A lane that does not declare one is not
bound: an unnamed model authorises nothing.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

try:
    from library.tools import (
        model_evidence_provenance,
        model_registry,
        project_model_store,
    )
except ImportError:  # imported as `tools.*` from inside library/
    from tools import model_evidence_provenance, model_registry, project_model_store

LANES: dict[str, dict[str, str]] = {
    "m5": {
        "implementation": "library.tools.analysis.sound_event_pipeline:IMPLEMENTATION_ID",
        "modality": "sound_event",
    },
}
"""Which registry implementation each lane's measurement came from.

The lane's own module declares it, so the answer to "which model made
this row" is the model's own declaration and not a mapping somebody
guessed. A lane that declares none is not in this table, and a lane that
declares a model the registry does not know binds nothing.

The modality is the lane's OWN measurement. A row that was derived from
a measured row (a cross-modal report resting on a sound event) is not
the lane's measurement, and it does not carry the model that made it: it
carries a rule that read it. Such a row is left without a record, and
the reuse gate refuses it, which is the safe answer for a row whose
producer chain is not recorded.

Only a lane whose MEASUREMENT names its model is here. The M5 slot
records the method that produced its events, so the model is a fact the
measurement carries; a lane whose slot names no model (M4 records one
only on a window the acquisition path measured) is not bound, because
naming its model would be a guess about a run this engine did not make.
"""


class ProvenanceBindingError(ValueError):
    """A binding the evidence side cannot make as asked."""


def lane_implementation_id(lane: str) -> str:
    """The registry implementation the named lane measures with.

    Raises when the lane declares no implementation: an unnamed model
    authorises nothing, and a guessed one would be worse.
    """
    entry = LANES.get(lane)
    if not entry:
        raise ProvenanceBindingError(
            f"no lane declares a model implementation for {lane!r}")
    module_name, _, attribute = entry["implementation"].partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ProvenanceBindingError(
            f"lane {lane!r} names {module_name!r}, which cannot be "
            f"imported: {exc}") from exc
    implementation_id = getattr(module, attribute, None)
    if not isinstance(implementation_id, str) or not implementation_id:
        raise ProvenanceBindingError(
            f"lane {lane!r} declares no model implementation")
    return implementation_id


def project_identity(project: str | Path,
                     model: dict[str, Any] | None = None) -> tuple[str, str]:
    """The project's sharing identity, and where it lives.

    A project has two names: the Project Model's own id and the stable
    id its cross-project policy grants by. The grant is the second one,
    so a row that names its producer names the id the grant is made in -
    a row stamped with the model store's id would read as another
    project's evidence to the share that grants by the policy's id.
    """
    root = Path(project).expanduser().resolve()
    project_id = None
    try:
        from library.tools.project_graph.cross_project import _policy
        policy = _policy(root)
    except (OSError, ValueError, TypeError):
        policy = None
    if policy:
        project_id = policy.get("project_id")
    if not project_id:
        project_id = (model or {}).get("id") or root.name
    return project_id, str(root)


def read_generation_records(project: str | Path) -> list[dict[str, Any]]:
    """The generation records the project holds."""
    return model_evidence_provenance.read_generation_records(project)


def records_by_evidence(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index generation records by the evidence id they name.

    A record may name many rows, and the same record is copied onto
    each of them, so identical records are collapsed first. Two records
    that DIFFER and both name one row are refused: no licence record
    governs that row.
    """
    index: dict[str, dict[str, Any]] = {}
    for record in model_evidence_provenance.unique_records(records):
        for evidence_id in record.get("evidence_ids") or []:
            if not isinstance(evidence_id, str) or not evidence_id:
                continue
            if evidence_id in index:
                raise ProvenanceBindingError(
                    f"evidence {evidence_id!r} has more than one generation "
                    f"record, so no licence record governs it")
            index[evidence_id] = record
    return index


def stamp_row(row: dict[str, Any], record: dict[str, Any], *,
              producer_project_id: str,
              producer_project_root: str) -> dict[str, Any]:
    """Copy a row with the record's licence verdict carried on it.

    The row is copied, never mutated. The record names the model and the
    licence the registry ruled on; the producer names the project that
    holds the evidence, which is what a cross-project share checks.
    """
    if not isinstance(row, dict):
        raise ProvenanceBindingError("an observation row is needed")
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise ProvenanceBindingError("an observation row needs a payload")
    stamped = dict(row)
    payload = dict(payload)
    payload["model_provenance"] = record
    payload["model_provenance_source"] = {
        "project_id": producer_project_id,
        "project_root": producer_project_root,
    }
    payload = model_evidence_provenance.stamp_observation_license(
        payload, record)
    stamped["payload"] = payload
    return stamped


def stamp_rows(rows: list[dict[str, Any]], records: list[dict[str, Any]], *,
               producer_project_id: str,
               producer_project_root: str) -> list[dict[str, Any]]:
    """Stamp the rows a generation record names, and leave the rest alone.

    A row no record names keeps its own payload: it is not wrong without
    a licence record, and a record that does not name it does not govern
    it.
    """
    index = records_by_evidence(records)
    stamped = []
    for row in rows:
        record = index.get(row.get("id"))
        if record is None:
            stamped.append(row)
            continue
        stamped.append(stamp_row(
            row, record, producer_project_id=producer_project_id,
            producer_project_root=producer_project_root))
    return stamped


def lane_rows(model: dict[str, Any], digest: str, lane: str) -> list[dict[str, Any]]:
    """The rows one lane measured for one piece of footage.

    A lane's rows share the lane's provenance id, which is derived from
    the content digest, so the selection is by identity and not by
    position. The lane's own modality is the measured row; a row that
    merely rests on it is not the lane's measurement.
    """
    entry = LANES.get(lane) or {}
    modality = entry.get("modality")
    provenance_id = f"prov-{digest}-{lane}"
    return [row for row in model.get("observations", [])
            if isinstance(row, dict)
            and row.get("provenance_id") == provenance_id
            and (modality is None or row.get("modality") == modality)]


def bind_lane_measurement(project: str | Path, digest: str, lane: str, *,
                          workload_purpose: str = "commercial_client",
                          registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record the licence of the model a lane measured with, and stamp its rows.

    The lane's own declaration names the implementation; the registry
    says what that implementation may be reused for. The record is
    written into the project's own generation-record store, and the rows
    the record names carry it, so the reuse gate has something to decide
    on instead of refusing every row for want of a record.
    """
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise ProvenanceBindingError("the project root must be a directory")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ProvenanceBindingError("a content digest is needed")
    implementation_id = lane_implementation_id(lane)
    reg = registry if registry is not None else model_registry.load_registry()
    revision, model = project_model_store.load_project_model(root)
    rows = lane_rows(model, digest, lane)
    if not rows:
        raise ProvenanceBindingError(
            f"the project holds no {lane!r} rows for {digest[:12]}, so there "
            f"is nothing to bind")
    evidence_ids = [row["id"] for row in rows]
    existing = model_evidence_provenance.read_generation_records(root)
    already = [record for record in existing
               if set(evidence_ids) <= set(record.get("evidence_ids") or [])]
    if already:
        record = already[0]
        created = False
    else:
        record = model_evidence_provenance.record_for_generation(
            evidence_ids, implementation_id, workload_purpose, registry=reg)
        model_evidence_provenance.append_generation_record(root, record)
        created = True
    project_id, project_root = project_identity(root, model)
    stamped = stamp_rows(
        rows, [record], producer_project_id=project_id,
        producer_project_root=project_root)
    merged = project_model_store.merge_evidence_records(
        root, observations=stamped, expected_revision=revision,
        author="ren", reason=(
            f"bind the licence record of {implementation_id} to the {lane} "
            f"rows of {digest[:12]}"))
    return {
        "project": str(root),
        "project_id": project_id,
        "digest": digest,
        "lane": lane,
        "implementation_id": implementation_id,
        "evidence_ids": evidence_ids,
        "record_created": created,
        "stamped_rows": len([row for row in stamped
                             if row.get("payload", {}).get("model_provenance")]),
        "revision": merged,
        "workload_purpose": workload_purpose,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="model_provenance_binding",
        description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bind = sub.add_parser(
        "bind",
        help="record the licence of the model a lane measured with")
    bind.add_argument("project")
    bind.add_argument("--digest", required=True,
                      help="the content digest the lane measured")
    bind.add_argument("--lane", default="m5",
                      help="the lane whose measurement is bound")
    bind.add_argument("--purpose", default="commercial_client",
                      choices=("commercial_client", "personal_noncommercial",
                               "permitted_research"))
    args = parser.parse_args(argv)
    if args.command == "bind":
        report = bind_lane_measurement(
            args.project, args.digest, args.lane,
            workload_purpose=args.purpose)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
