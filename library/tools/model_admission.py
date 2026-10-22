"""The admission record every optional model carries, and the gate over it.

An OPTIONAL model is one Ren may fetch, load or route to without shipping it
by default. The registry lists eighteen of them. Each one carries an
``admission`` record with four things:

``license``
    The upstream licence statement, verbatim from the licence evidence.
``commercial_use``
    Whether that licence permits commercial use: ``permitted``,
    ``conditional``, ``prohibited`` or ``unresolved``.
``size``
    The bytes a machine downloads for the weights the runtime actually
    loads, with the files that make up the number and the publisher source
    they were measured from.
``required_gain``
    The measurement this model must show before it may be admitted to a
    configuration: the metric, the split, the incumbent it is measured
    against and the threshold it must clear.

The gate is FAIL-CLOSED. A model whose record is missing, incomplete, or
inconsistent with its licence evidence is refused at the point of load,
fetch, package or routing. Nothing here downloads a model and nothing here
decides whether a model is good: the record states what a model must show,
and refuses the ones that have not said.

The threshold in ``required_gain`` is a POLICY floor, not a measurement.
Where a model has a recorded measurement, the record names the incumbent
and its value so the denominator is visible; where nothing has been
measured, the comparison is ``establish_baseline`` and the requirement is
to measure the named metric on the named fixture first. An unmeasured
value is never recorded as a zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The four things an admission record must carry.
REQUIRED_FIELDS = ("license", "commercial_use", "size", "required_gain")

#: How a licence approval status reads as commercial use.
COMMERCIAL_USE_BY_APPROVAL = {
    "approved": "permitted",
    "conditional": "conditional",
    "unresolved": "unresolved",
    "rejected": "prohibited",
}

#: How permissive each commercial use is. A record may withhold the
#: permission the evidence grants; it may never grant permission the
#: evidence does not.
COMMERCIAL_USE_PERMISSIVENESS = {
    "permitted": 0,
    "conditional": 1,
    "unresolved": 2,
    "prohibited": 3,
}

#: How an optional model's commercial use may read.
COMMERCIAL_USE_VALUES = frozenset(COMMERCIAL_USE_BY_APPROVAL.values())

#: The three comparisons a required gain may make.
COMPARISONS = frozenset({
    "no_regression",
    "delta_over_incumbent",
    "establish_baseline",
})

#: Which way round the metric is better.
DIRECTIONS = frozenset({"higher_is_better", "lower_is_better"})

#: What a required gain must name.
GAIN_FIELDS = ("metric", "unit", "split", "direction", "comparison",
               "basis", "fixture", "evidence")


class AdmissionRecordError(ValueError):
    """An admission record is missing, incomplete or inconsistent."""


def record_for(implementation: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the admission record of one implementation, if it has one."""
    record = implementation.get("admission")
    if record is None:
        return None
    if not isinstance(record, Mapping):
        raise AdmissionRecordError(
            f"{implementation.get('id', '<unknown>')}: admission must be an object")
    return dict(record)


def _size_missing(record: Mapping[str, Any]) -> list[str]:
    size = record.get("size")
    if not isinstance(size, Mapping):
        return ["size"]
    missing = []
    if not isinstance(size.get("bytes"), int) or size["bytes"] <= 0:
        missing.append("size.bytes")
    parts = size.get("parts")
    if not isinstance(parts, list) or not parts:
        missing.append("size.parts")
        return missing
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            missing.append(f"size.parts[{index}]")
            continue
        for field in ("source", "method", "files", "bytes"):
            if field not in part:
                missing.append(f"size.parts[{index}].{field}")
        if not isinstance(part.get("bytes"), int) or part.get("bytes", 0) <= 0:
            missing.append(f"size.parts[{index}].bytes")
    return missing


def _gain_missing(record: Mapping[str, Any]) -> list[str]:
    gain = record.get("required_gain")
    if not isinstance(gain, Mapping):
        return ["required_gain"]
    missing = [field for field in GAIN_FIELDS if not gain.get(field)]
    comparison = gain.get("comparison")
    if comparison in {"no_regression", "delta_over_incumbent"}:
        threshold = gain.get("threshold")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            missing.append("required_gain.threshold")
        if not gain.get("incumbent"):
            missing.append("required_gain.incumbent")
    if comparison == "establish_baseline" and gain.get("threshold") is not None:
        missing.append("required_gain.threshold")
    if comparison not in COMPARISONS:
        missing.append("required_gain.comparison")
    if gain.get("direction") not in DIRECTIONS:
        missing.append("required_gain.direction")
    return missing


def missing_fields(implementation: Mapping[str, Any]) -> list[str]:
    """Name every field the admission record of this model still lacks."""
    record = record_for(implementation)
    if record is None:
        return list(REQUIRED_FIELDS)
    missing = []
    if not record.get("license"):
        missing.append("license")
    if record.get("commercial_use") not in COMMERCIAL_USE_VALUES:
        missing.append("commercial_use")
    missing.extend(_size_missing(record))
    missing.extend(_gain_missing(record))
    return missing


def assess(implementation: Mapping[str, Any]) -> dict[str, Any]:
    """Report one implementation's admission record and what it lacks."""
    record = record_for(implementation) or {}
    gain = record.get("required_gain") or {}
    size = record.get("size") or {}
    missing = missing_fields(implementation)
    return {
        "implementation_id": implementation.get("id"),
        "optional": bool(implementation.get("optional_download")),
        "complete": not missing,
        "missing": missing,
        "license": record.get("license"),
        "commercial_use": record.get("commercial_use"),
        "size_bytes": size.get("bytes"),
        "required_gain": {
            "metric": gain.get("metric"),
            "split": gain.get("split"),
            "comparison": gain.get("comparison"),
            "threshold": gain.get("threshold"),
            "incumbent": gain.get("incumbent"),
            "incumbent_value": gain.get("incumbent_value"),
        },
    }


def validate_record(implementation: Mapping[str, Any]) -> None:
    """Refuse a malformed admission record, loudly and by field name."""
    implementation_id = implementation.get("id", "<unknown>")
    record = record_for(implementation)
    if record is None:
        raise AdmissionRecordError(
            f"{implementation_id}: optional model has no admission record")
    missing = missing_fields(implementation)
    if missing:
        raise AdmissionRecordError(
            f"{implementation_id}: admission record is incomplete: {', '.join(missing)}")
    gain = record["required_gain"]
    if gain["comparison"] == "delta_over_incumbent":
        incumbent_value = gain.get("incumbent_value")
        if not isinstance(incumbent_value, (int, float)) or isinstance(incumbent_value, bool):
            raise AdmissionRecordError(
                f"{implementation_id}: a delta over an incumbent needs the "
                f"incumbent's measured value")
        if gain["threshold"] <= 0:
            raise AdmissionRecordError(
                f"{implementation_id}: a required gain over an incumbent must be positive")


def consistency(
    implementation: Mapping[str, Any],
    *,
    approval_status: str | None = None,
    licence_statement: str | None = None,
) -> list[str]:
    """Name every way the record contradicts the licence evidence.

    A record may withhold the permission the evidence grants; it may never
    grant permission the evidence does not, and it may not quote a licence
    the evidence does not carry.
    """
    problems = []
    record = record_for(implementation)
    if record is None:
        return problems
    if approval_status is not None:
        expected = COMMERCIAL_USE_BY_APPROVAL.get(approval_status)
        if expected is None:
            problems.append(f"unknown licence approval status {approval_status!r}")
        elif (COMMERCIAL_USE_PERMISSIVENESS[record["commercial_use"]]
              < COMMERCIAL_USE_PERMISSIVENESS[expected]):
            problems.append(
                f"commercial use {record['commercial_use']!r} claims more than "
                f"licence approval {approval_status!r} allows ({expected!r})")
    if licence_statement is not None and record["license"] != licence_statement:
        problems.append("the recorded licence is not the licence evidence's statement")
    return problems


def consistency_report(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Every implementation whose admission record contradicts its evidence."""
    rows = []
    for item in registry.get("implementations", []):
        evidence = item.get("license_evidence", {})
        problems = consistency(
            item,
            approval_status=evidence.get("approval_status"),
            licence_statement=evidence.get("license"))
        if problems:
            rows.append({"implementation_id": item.get("id"), "problems": problems})
    return {"consistent": not rows, "rows": rows}


def require_record(implementation: Mapping[str, Any], *, action: str) -> dict[str, Any]:
    """Refuse to load, fetch, package or route to a model with no record."""
    record = record_for(implementation)
    if record is None:
        raise AdmissionRecordError(
            f"cannot {action} model implementation "
            f"{implementation.get('id')!r}: no admission record; a model without "
            f"its licence, size and required gain is not admitted")
    missing = missing_fields(implementation)
    if missing:
        raise AdmissionRecordError(
            f"cannot {action} model implementation "
            f"{implementation.get('id')!r}: admission record is missing "
            f"{', '.join(missing)}")
    return record


def coverage(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Share of the registry's optional models that carry a complete record."""
    rows = [assess(item) for item in registry.get("implementations", [])
            if item.get("optional_download")]
    complete = [row for row in rows if row["complete"]]
    total = len(rows)
    return {
        "optional_total": total,
        "complete": len(complete),
        "incomplete": total - len(complete),
        "share": (len(complete) / total) if total else None,
        "rows": rows,
    }


def configurations(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Admitted implementations per configuration, and what personal adds.

    The commercial baseline is the permitted floor: a personal configuration
    may add capabilities, but it may not lose one the commercial
    configuration has.
    """
    admitted = {item["id"] for item in registry.get("implementations", [])
                 if item.get("optional_download")
                 and not missing_fields(item)}
    commercial = {item["id"] for item in registry.get("implementations", [])
                  if item.get("optional_download")
                  and "commercial" in item.get("editions", ())
                  and item["id"] in admitted}
    personal = {item["id"] for item in registry.get("implementations", [])
                if item.get("optional_download")
                and "personal" in item.get("editions", ())
                and item["id"] in admitted}
    lost = sorted(commercial - personal)
    if lost:
        raise AdmissionRecordError(
            f"the personal configuration loses commercial capabilities: {lost}")
    return {
        "commercial": sorted(commercial),
        "personal": sorted(personal),
        "personal_extras": sorted(personal - commercial),
        "commercial_baseline_intact": True,
    }


def main() -> None:
    """Print the admission gate report for the registry."""
    import argparse
    import json

    from library.tools import model_registry

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                        help="print the full report as JSON")
    args = parser.parse_args()
    registry = model_registry.load_registry()
    report = {"coverage": coverage(registry),
              "configurations": configurations(registry)}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    rows = report["coverage"]["rows"]
    print("Model admission gate")
    print(f"  optional models with a complete record: "
          f"{report['coverage']['complete']}/{report['coverage']['optional_total']}")
    for row in rows:
        state = "complete" if row["complete"] else "INCOMPLETE: " + ", ".join(row["missing"])
        print(f"  {row['implementation_id']}\t{row['commercial_use']}\t"
              f"{row['size_bytes']}\t{state}")


if __name__ == "__main__":
    main()
