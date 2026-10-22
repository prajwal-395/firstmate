"""The epistemic census: what the evidence CLAIMS, and how sure it is.

A Project Model used to file every row as ``measurement``/``measured``,
so a detector's 0.98 box and a lens-reported focus distance read the
same way. This module counts what the model says, so the claim can be
checked rather than believed:

* **registry rows** - every registry type with its producer kind, its
  evidence kind and where its uncertainty comes from.
* **the model census** - per measurement type: how many rows, how many
  are measurements and how many are inferences, how many carry an
  uncertainty, and the DEFECT: a model-produced type filed as a
  measurement. That count must be zero.
* **the re-classification** - the same model read through the registry
  as it stands now: what changes, what does not, and whether anything
  outside the epistemic fields moved. A re-classification that changes
  a value is a fidelity regression, and this is where it is caught.
* **the calibration** - the empirical error of an inferred type against
  an adjudicated set, with the row count that error was measured on.
  A type with no reachable adjudicated set is reported as ``unknown``,
  never as a pass.

The census reads a model and writes nothing. The adjudicated set is
read from a path the caller names (``--adjudication``); sealed label
sets stay outside this repository, and only aggregates belong here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from library.tools import evidence_contract

#: The fields a re-classification is allowed to change. Everything else
#: in a contract is the measurement itself, and a change there is a
#: fidelity regression, not an epistemic correction.
EPISTEMIC_FIELDS = ("evidence_type", "certainty", "uncertainty")

#: A box match needs this much overlap, the same rule the object
#: continuity scorer uses.
IOU_THRESHOLD = 0.5

#: The certainty a kind carries, the same table the contract's
#: ``certainty`` property enforces.
_CERTAINTY_FOR_KIND = {
    "measurement": "measured",
    "inference": "inferred",
    "creative_judgment": "interpreted",
    "evaluation": None,
}


class EpistemicCensusError(ValueError):
    """A census that cannot be answered as asked."""


# ── The registry ───────────────────────────────────────────────────


def registry_rows() -> list[dict[str, Any]]:
    """One row per registry type: its kind, its producer and its
    uncertainty source."""
    rows: list[dict[str, Any]] = []
    for name in evidence_contract._REGISTRY:
        entry = evidence_contract.registry_entry(name)
        rows.append({
            "measurement_type": name,
            "kind": entry["kind"].value,
            "producer_kind": entry["producer_kind"],
            "model_produced": entry["producer_kind"] == "model",
            "uncertainty_source": entry.get("uncertainty_source"),
        })
    return rows


def registry_defects() -> list[dict[str, Any]]:
    """Registry rows that break the epistemic rule.

    A model-produced type filed as a measurement is the defect this
    census exists to name: it says an instrument read a value no
    instrument read. An instrument type filed as an inference is a
    second, opposite defect: it says a model read a value a model never
    touched.
    """
    defects: list[dict[str, Any]] = []
    for row in registry_rows():
        if row["model_produced"] and row["kind"] != "inference":
            defects.append({**row,
                            "defect": "model_produced_filed_as_measurement"})
        if not row["model_produced"] and row["kind"] == "inference":
            defects.append({**row, "defect": "instrument_filed_as_inference"})
    return defects


# ── The model census ───────────────────────────────────────────────


def contract_of(observation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The evidence contract of one observation.

    The contract rides in the observation's payload; the flat
    ``evidence`` key is the older shape a reader may still meet.

    This is the ONE dual read: every reader that needs a contract
    reads it here, so the two spellings are never read by two rules.
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


#: The pre-public name, kept so an existing reader keeps working.
_contract_of = contract_of


def model_contracts(model: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every evidence contract a model carries, in observation order."""
    contracts: list[Mapping[str, Any]] = []
    for observation in model.get("observations") or []:
        evidence = _contract_of(observation)
        if isinstance(evidence, dict):
            contracts.append(evidence)
    return contracts


def census_contracts(contracts: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Count what a list of evidence contracts claims, per type."""
    by_type: dict[str, dict[str, Any]] = {}
    defects = {"model_produced_filed_as_measurement": 0,
               "inference_without_uncertainty": 0}
    total = 0
    for evidence in contracts:
        measurement_type = evidence.get("measurement_type")
        if not isinstance(measurement_type, str) or not measurement_type:
            continue
        total += 1
        row = by_type.setdefault(measurement_type, {
            "rows": 0,
            "evidence_type": {},
            "certainty": {},
            "with_uncertainty": 0,
            "unstated_uncertainty": 0,
            "model_produced_filed_as_measurement": 0,
        })
        row["rows"] += 1
        kind = str(evidence.get("evidence_type"))
        row["evidence_type"][kind] = row["evidence_type"].get(kind, 0) + 1
        certainty = str(evidence.get("certainty"))
        row["certainty"][certainty] = row["certainty"].get(certainty, 0) + 1
        if evidence.get("uncertainty") is not None:
            row["with_uncertainty"] += 1
            if float(evidence["uncertainty"]) >= \
                    evidence_contract.UNCERTAINTY_UNSTATED:
                row["unstated_uncertainty"] += 1
        model_produced = evidence_contract.is_model_produced(measurement_type)
        if model_produced and kind == "measurement":
            row["model_produced_filed_as_measurement"] += 1
            defects["model_produced_filed_as_measurement"] += 1
        if kind == "inference" and evidence.get("uncertainty") is None:
            defects["inference_without_uncertainty"] += 1
    model_produced_rows = sum(
        row["rows"] for name, row in by_type.items()
        if evidence_contract.is_model_produced(name))
    return {
        "observations": total,
        "by_type": by_type,
        "defects": defects,
        "reclassified": {
            "rows": model_produced_rows,
            "fraction": (model_produced_rows / total) if total else 0.0,
        },
    }


def census_model(model: Mapping[str, Any]) -> dict[str, Any]:
    """Count what a Project Model's evidence claims, per type."""
    return census_contracts(model_contracts(model))


def load_model(path: str | Path) -> dict[str, Any]:
    """Read a stored Project Model snapshot (or a checkpoint)."""
    document = json.loads(Path(path).read_text())
    model = document.get("model") if isinstance(document, dict) else None
    if not isinstance(model, dict):
        model = document
    return model


# ── The re-classification ──────────────────────────────────────────


def reclassify_contract(contract: Mapping[str, Any],
                       confidence: Any = None) -> dict[str, Any]:
    """One stored contract, re-classified by the registry.

    A model-produced type becomes an inference and carries the
    uncertainty its producer earned: ``1 - score`` where the producer
    recorded its own score, and the unstated maximum where it recorded
    none. The producer's score is read from the payload first and from
    the observation's own confidence second - the migration records
    the same score there. Every other field is carried through
    untouched - a re-classification that changes a measurement is a
    regression, not a correction.
    """
    measurement_type = str(contract.get("measurement_type"))
    entry = evidence_contract.registry_entry(measurement_type)
    kind = entry["kind"].value
    if entry["producer_kind"] == "model":
        uncertainty = evidence_contract.uncertainty_from_payload(
            measurement_type, contract.get("value"))
        if uncertainty >= evidence_contract.UNCERTAINTY_UNSTATED \
                and confidence is not None:
            uncertainty = evidence_contract.uncertainty_from_score(confidence)
    else:
        uncertainty = contract.get("uncertainty")
    certainty = _CERTAINTY_FOR_KIND.get(kind)
    if certainty is None:
        certainty = contract.get("certainty")
    return {
        **contract,
        "evidence_type": kind,
        "certainty": certainty,
        "uncertainty": uncertainty,
    }


def fidelity_violations(before: Mapping[str, Any],
                        after: Mapping[str, Any]) -> list[str]:
    """The fields a re-classification changed that it must not change."""
    violations: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key in EPISTEMIC_FIELDS:
            continue
        if before.get(key) != after.get(key):
            violations.append(key)
    return violations


def reclassify_model(model: Mapping[str, Any]) -> dict[str, Any]:
    """Re-classify every contract a model carries, and check the result.

    The report names what moved: the rows whose epistemic fields
    changed, the rows the contract REFUSES after the change, and any
    field outside the epistemic ones that moved with them.
    """
    reclassified: list[dict[str, Any]] = []
    changed = 0
    refused: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []
    for index, observation in enumerate(model.get("observations") or []):
        contract = _contract_of(observation)
        if not isinstance(contract, dict):
            continue
        after = reclassify_contract(contract, observation.get("confidence"))
        moved = [field for field in EPISTEMIC_FIELDS
                 if contract.get(field) != after.get(field)]
        if moved:
            changed += 1
        outside = fidelity_violations(contract, after)
        if outside:
            violations.append({"observation_index": str(index),
                               "fields": ",".join(outside)})
        refusal = _contract_refusal(after)
        if refusal is not None:
            refused.append({"observation_index": str(index),
                            "reason": refusal})
        reclassified.append(after)
    return {
        "contracts": reclassified,
        "report": {
            "rows": len(reclassified),
            "epistemically_changed": changed,
            "contract_refusals": refused,
            "fidelity_violations": violations,
        },
    }


def _contract_refusal(contract: Mapping[str, Any]) -> str | None:
    """The contract's own verdict on one re-classified contract.

    A re-classified row that the contract refuses is a defect in the
    re-classification, and it is named rather than counted away.
    """
    def enum(name: str, value: Any) -> Any:
        if value is None:
            return None
        return getattr(evidence_contract, name)(value)

    try:
        evidence_contract.EvidenceRecord(
            asset_id="asset", clock_id="clock", start_tick=0, end_tick=1,
            target_entity_id=contract.get("target_entity_id"),
            target_region=contract.get("target_region"),
            evidence_type=enum("EvidenceType", contract.get("evidence_type")),
            measurement_type=str(contract.get("measurement_type")),
            value=contract.get("value"), unit=str(contract.get("unit")),
            method=str(contract.get("method")),
            sample_resolution=contract.get("sample_resolution"),
            coverage=enum("CoverageKind", contract.get("coverage")),
            coverage_samples=int(contract.get("coverage_samples") or 0),
            coverage_sample_rate_hz=float(
                contract.get("coverage_sample_rate_hz") or 0.0),
            provenance_id=str(contract.get("provenance_id") or "prov"),
            uncertainty=contract.get("uncertainty"),
            policy_id=contract.get("policy_id"),
            basis_measurement_ids=tuple(
                contract.get("basis_measurement_ids") or ()),
            basis_rule_ids=tuple(contract.get("basis_rule_ids") or ()),
            contract_version=int(contract.get("contract_version") or 1),
            certainty_state=enum("CertaintyState", contract.get("certainty")),
            processing_status=enum("ProcessingStatus",
                                   contract.get("processing_status")),
            source_reference=contract.get("source_reference"),
            spatial_frame_id=contract.get("spatial_frame_id"),
            coordinate_space_id=contract.get("coordinate_space_id"),
            references=tuple(contract.get("references") or ()),
            coverage_scope=contract.get("coverage_scope"),
            coverage_level=contract.get("coverage_level"),
            producer_version=contract.get("producer_version"),
            freshness=contract.get("freshness"),
            source_stream_id=contract.get("source_stream_id"),
            sampling_method=contract.get("sampling_method"),
            temporal_coverage=tuple(contract.get("temporal_coverage") or ()),
            coverage_status=contract.get("coverage_status"),
            verified_status=contract.get("verified_status"),
            producer=contract.get("producer"))
    except evidence_contract.EvidenceContractError as exc:
        return str(exc)
    return None


# ── The calibration ────────────────────────────────────────────────


def _iou(first: list[float], second: list[float]) -> float:
    ax0, ay0, ax1, ay1 = first
    bx0, by0, bx1, by1 = second
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - intersection
    return (intersection / union) if union > 0 else 0.0


def _box(row: Mapping[str, Any]) -> list[float] | None:
    box = row.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        return [float(value) for value in box]
    except (TypeError, ValueError):
        return None


def adjudicated_errors(adjudication: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The empirical error of each predicted type against an adjudicated set.

    The set names hand-adjudicated ``labels`` and the producer's
    ``predictions``; each prediction names the ``type`` it came from, so
    the error is reported per type and not pooled. A label is matched
    by a same-class prediction over the same frame with an IoU of at
    least :data:`IOU_THRESHOLD`; an unmatched label is a miss and an
    unmatched prediction is a false positive. Both count as error.
    """
    labels = [row for row in (adjudication.get("labels") or [])
              if isinstance(row, Mapping)]
    predictions = [row for row in (adjudication.get("predictions") or [])
                   if isinstance(row, Mapping)]

    def row_for(name: str) -> dict[str, Any]:
        return by_type.setdefault(name, {
            "measurement_type": name,
            "labels": 0,
            "matched": 0,
            "missed": 0,
            "false_positives": 0,
            "predictions": 0,
            "scored_matched": [],
            "scored_missed": [],
        })

    by_type: dict[str, dict[str, Any]] = {}
    for prediction in predictions:
        name = str(prediction.get("type") or "")
        if not name:
            continue
        row = row_for(name)
        row["predictions"] += 1
        box = _box(prediction)
        frame = prediction.get("frame")
        klass = prediction.get("class")
        matched = False
        if box is not None:
            for label in labels:
                if label.get("frame") != frame or label.get("class") != klass:
                    continue
                label_box = _box(label)
                if label_box is None:
                    continue
                if _iou(box, label_box) >= IOU_THRESHOLD:
                    matched = True
                    break
        score = prediction.get("score")
        score_value = (float(score)
                       if isinstance(score, (int, float))
                       and not isinstance(score, bool) else None)
        if matched:
            row["matched"] += 1
            if score_value is not None:
                row["scored_matched"].append(score_value)
        else:
            row["false_positives"] += 1
            if score_value is not None:
                row["scored_missed"].append(score_value)
    for label in labels:
        name = str(label.get("type") or "")
        if not name:
            continue
        row_for(name)["labels"] += 1
    for row in by_type.values():
        # A label is missed when no prediction of its type matched it.
        row["missed"] = max(0, row["labels"] - row["matched"])
        denominator = row["labels"] or row["predictions"]
        row["error_rate"] = (
            (row["missed"] + row["false_positives"]) / denominator
            if denominator else None)
        row["mean_score_matched"] = (
            sum(row["scored_matched"]) / len(row["scored_matched"])
            if row["scored_matched"] else None)
        row["mean_score_missed"] = (
            sum(row["scored_missed"]) / len(row["scored_missed"])
            if row["scored_missed"] else None)
        row.pop("scored_matched")
        row.pop("scored_missed")
    return [by_type[name] for name in sorted(by_type)]


# ── The report ─────────────────────────────────────────────────────


def render_report(census: Mapping[str, Any],
                   calibration: list[Mapping[str, Any]],
                   ) -> str:
    """The census as a table a reader can check."""
    lines: list[str] = []
    lines.append(f"observations: {census['observations']}")
    reclassified = census["reclassified"]
    lines.append(
        f"model-produced rows: {reclassified['rows']} "
        f"({reclassified['fraction']:.1%})")
    defects = census["defects"]
    lines.append(
        f"model-produced rows filed as a measurement: "
        f"{defects['model_produced_filed_as_measurement']}")
    lines.append(
        f"inferences without an uncertainty: "
        f"{defects['inference_without_uncertainty']}")
    lines.append("")
    lines.append("| measurement type | rows | measurement | inference | "
                 "with uncertainty | producer said nothing |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for name in sorted(census["by_type"]):
        row = census["by_type"][name]
        lines.append(
            f"| {name} | {row['rows']} | "
            f"{row['evidence_type'].get('measurement', 0)} | "
            f"{row['evidence_type'].get('inference', 0)} | "
            f"{row['with_uncertainty']} | "
            f"{row.get('unstated_uncertainty', 0)} |")
    lines.append("")
    if calibration:
        lines.append("| type | adjudicated rows | matched | missed | "
                     "false positives | error |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for row in calibration:
            rate = row["error_rate"]
            lines.append(
                f"| {row['measurement_type']} | {row['labels']} | "
                f"{row['matched']} | {row['missed']} | "
                f"{row['false_positives']} | "
                f"{'unknown' if rate is None else f'{rate:.1%}'} |")
    else:
        lines.append("calibration: unknown - no adjudicated set was "
                     "reachable, and an unmeasured error is never a pass")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Census the epistemic vocabulary of a Project Model.")
    parser.add_argument("--model", required=True,
                        help="a stored Project Model snapshot or checkpoint")
    parser.add_argument("--adjudication", default=None,
                        help="an adjudicated label set (sealed, outside "
                             "this repository)")
    args = parser.parse_args(argv)
    model = load_model(args.model)
    before = census_model(model)
    after = reclassify_model(model)
    print("BEFORE")
    print(render_report(before, []))
    print("")
    print("AFTER")
    print(render_report(census_contracts(after["contracts"]), []))
    report = after["report"]
    print("")
    print(f"epistemically changed rows: {report['epistemically_changed']}")
    print(f"contract refusals: {len(report['contract_refusals'])}")
    print(f"fidelity violations: {len(report['fidelity_violations'])}")
    calibration: list[Mapping[str, Any]] = []
    if args.adjudication:
        calibration = adjudicated_errors(
            json.loads(Path(args.adjudication).read_text()))
    if calibration:
        print("")
        print(render_report(before, calibration).split("\n")[-1])
    defects = before["defects"]
    if defects["model_produced_filed_as_measurement"] == 0:
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
